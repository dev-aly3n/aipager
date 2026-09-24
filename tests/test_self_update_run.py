"""The subprocess seam's contract (roadmap 8.36).

These tests restore the REAL ``_run_command`` (captured at import, before
conftest's refuser replaces it) and run only ``sys.executable -c`` snippets
under tmp — never an installer, claude or systemd. That is the one
exception the self-update guard allows, and it is explicit here.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from aipager import self_update
from aipager.self_update import _run_command as REAL_RUN_COMMAND


@pytest.fixture
def real_seam(monkeypatch):
    monkeypatch.setattr(self_update, "_run_command", REAL_RUN_COMMAND)
    monkeypatch.setattr(self_update, "KILL_GRACE_SECONDS", 2)
    return REAL_RUN_COMMAND


def _gone(pid: int) -> bool:
    """True once ``pid`` no longer exists (or is only a zombie)."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return True
    return state in ("Z", "X")


def test_run_command_timeout_kills_group(real_seam, tmp_path):
    # The child starts a grandchild in its own process group and prints its
    # pid; a timeout must take BOTH down — killing only the direct child
    # would leave an installer's worker running on the operator's box.
    script = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    res = self_update.run_command([sys.executable, "-c", script], timeout=1.5)
    assert res.timed_out is True
    assert res.returncode is None
    assert time.monotonic() - started < 20
    grandchild = int(res.output_tail.split()[0])
    deadline = time.monotonic() + 5
    while not _gone(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    if not _gone(grandchild):
        os.kill(grandchild, 9)  # never leave it behind, then fail
        pytest.fail("the grandchild outlived the timeout — the group was not killed")


def test_run_command_never_raises_on_missing_binary(real_seam, tmp_path):
    res = self_update.run_command([str(tmp_path / "no-such-binary")], timeout=5)
    assert res.returncode is None
    assert res.error and "FileNotFoundError" in res.error
    assert res.ok is False


def test_run_command_reports_exit_code_and_tail(real_seam):
    res = self_update.run_command(
        [sys.executable, "-c", "import sys; print('x' * 2000); sys.exit(3)"],
        timeout=10)
    assert res.returncode == 3
    assert len(res.output_tail) == self_update.OUTPUT_TAIL_CHARS


def test_output_tail_redacts_bot_token(real_seam, monkeypatch):
    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-SECRETSECRETSECRET")
    script = (
        f"print('GET https://api.telegram.org/bot{token}/getMe')\n"
        f"print('bare {token}')\n"
        "print('oauth sk-ant-oat01-SECRETSECRETSECRET')\n"
    )
    res = self_update.run_command([sys.executable, "-c", script], timeout=10)
    assert res.returncode == 0
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ" not in res.output_tail
    assert "SECRETSECRET" not in res.output_tail
    assert "<redacted>" in res.output_tail


def test_spawned_child_env_has_no_secrets(real_seam, monkeypatch):
    for key in ("CLAUDE_TG_BOT_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_SOME_OTHER_TOKEN"):
        monkeypatch.setenv(key, "x")
    script = ("import os\n"
              "print(sorted(k for k in os.environ if k.startswith("
              "('CLAUDE_TG_', 'ANTHROPIC_', 'CLAUDE_CODE_')) and ("
              "'TOKEN' in k or 'KEY' in k)))")
    res = self_update.run_command([sys.executable, "-c", script], timeout=10,
                                  env=self_update._scrub_env())
    assert res.output_tail.strip() == "[]"


def test_run_command_argv_is_never_a_shell_string(real_seam, tmp_path):
    marker = tmp_path / "pwned"
    # A shell would run the second command; an argv list passes it as data.
    res = self_update.run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", f"; touch {marker}"],
        timeout=10)
    assert res.returncode == 0
    assert not marker.exists()
