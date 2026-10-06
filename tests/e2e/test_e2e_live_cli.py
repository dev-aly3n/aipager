"""Live e2e for the installed CLI: ``aipager doctor --json`` and
``aipager setup`` against the operator's real install, without changing it.

Opt-in like ``test_e2e_live_daemon.py``: marked ``e2e`` and skipped unless
``AIPAGER_E2E_LIVE=1`` is set, a daemon is running (doctor's daemon check
needs one) and a chat is configured. Runs the ``aipager`` on ``PATH`` (the
installed CLI, what the operator and a coding agent run), in a subprocess
that inherits ``PYTEST_CURRENT_TEST``, so the real-home write guard
(roadmap 8.97) refuses any write under the real home it might attempt.

What each test proves, and what it sends:

- ``test_doctor_json``: ``doctor --json`` prints one JSON object, ``ok``
  true, one row per check with the keys this checkout's ``doctor.CHECKS``
  define, and no bot token. Calls getMe/getChat; posts nothing.
- ``test_setup_dry_run_changes_nothing``: ``setup --dry-run --json`` with
  the real token (read by this process from ``config.BOT_TOKEN`` into a
  0600 file in the test's tmp dir, removed after, never printed) and the
  real chat id: exit 0, status ``dry_run``, no change to ``aipager.yaml``
  planned, and the real ``aipager.yaml`` and ``~/.claude/settings.json``
  byte-identical (sha256) before and after. Calls getMe; posts nothing.
- ``test_setup_chat_not_started``: exit 5 (``chat_not_started``) for a
  chat id that never started the bot. Setup only reaches its test
  message on a real (not dry) run, so this runs with ``HOME`` pointed at
  an empty temp folder: a fresh install whose files would land there, and
  the refused test message comes before any write. Asserts nothing was
  written there and the real files are unchanged. Calls getMe and one
  sendMessage that Telegram refuses; posts nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.e2e import harness

if os.environ.get("AIPAGER_E2E_LIVE") != "1":
    pytest.skip("set AIPAGER_E2E_LIVE=1 to run the installed CLI against the real install",
                allow_module_level=True)
if not harness.daemon_running():
    pytest.skip("no aipager daemon running — doctor --json needs the live install",
                allow_module_level=True)

from aipager import config  # noqa: E402
from aipager.state import _default_scope  # noqa: E402

_SCOPE = _default_scope()
if _SCOPE is None:
    pytest.skip("no chat configured", allow_module_level=True)
CHAT_ID = int(_SCOPE[0])

#: The operator's real files (the password database's home, never $HOME,
#: which tests may redirect).
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
REAL_FILES = (_REAL_HOME / ".config" / "aipager" / "aipager.yaml",
              _REAL_HOME / ".claude" / "settings.json")
#: A user id that has certainly never pressed Start in the operator's bot
#: (beyond the ids Telegram has handed out).
NEVER_STARTED_CHAT = 999_999_999_999
_YAML_CHANGES = {"migrated_v1", "bot_token", "owner_dm", "role"}


def _aipager() -> str:
    exe = shutil.which("aipager")
    if exe is None:
        pytest.skip("no aipager on PATH")
    return exe


def _hashes() -> dict[str, str | None]:
    out = {}
    for p in REAL_FILES:
        try:
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            out[str(p)] = None
    return out


def _token() -> str:
    token = config.BOT_TOKEN
    if not token:
        pytest.skip("no bot token configured")
    return token


def _no_token_in(token: str, *texts: str) -> None:
    """Fail (without showing the text) if the token or its secret half
    appears in any of ``texts``."""
    secret = token.partition(":")[2]
    for t in texts:
        if token in t or (secret and secret in t):
            pytest.fail("the CLI printed the bot token", pytrace=False)


@pytest.fixture
def token_file(tmp_path):
    """The real bot token in a 0600 file inside this test's tmp dir,
    removed afterwards (pytest keeps old tmp dirs). A run killed outright
    (SIGKILL, OOM) leaves it there, 0600 inside pytest's 0700 temp root."""
    token = _token()  # may skip: before any file exists
    path = tmp_path / "bot-token"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (token + "\n").encode())
    finally:
        os.close(fd)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _run(argv: list[str], *, env: dict | None = None, timeout: int = 180):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                       env=env if env is not None else dict(os.environ))
    return p.returncode, p.stdout, p.stderr


def _doc(stdout: str) -> dict:
    try:
        doc = json.loads(stdout)
    except json.JSONDecodeError:
        pytest.fail(f"no JSON on stdout ({len(stdout)} chars)", pytrace=False)
    assert isinstance(doc, dict)
    return doc


def _summary(doc: dict, *keys: str) -> dict:
    """The fields worth showing on a failure (no free text that could
    carry anything secret beyond what the CLI already scrubs)."""
    return {k: doc.get(k) for k in keys}


def test_doctor_json():
    from aipager import doctor
    rc, out, err = _run([_aipager(), "doctor", "--json"])
    _no_token_in(_token(), out, err)
    doc = _doc(out)
    checks = doc.get("checks") or []
    rows = {c.get("key"): c.get("status") for c in checks}
    failing = {k: s for k, s in rows.items() if s != "ok"}
    assert doc.get("command") == "doctor"
    assert [c.get("key") for c in checks] == [doctor._check_key(f) for f in doctor.CHECKS], rows
    assert all(s in ("ok", "warn", "fail") for s in rows.values()), rows
    assert doc.get("ok") is True and rc == 0, f"rc={rc}, not ok: {failing}"


def test_setup_dry_run_changes_nothing(token_file):
    before = _hashes()
    rc, out, err = _run([_aipager(), "setup", "--token-file", str(token_file),
                         "--chat-id", str(CHAT_ID), "--dry-run", "--json"])
    after = _hashes()
    _no_token_in(_token(), out, err)
    assert before == after, "setup --dry-run changed a real file"
    doc = _doc(out)
    shown = _summary(doc, "status", "ok", "exit_code", "error", "changed", "test_message",
                     "settings_json", "chat_id", "role")
    assert rc == 0 and doc.get("ok") is True and doc.get("status") == "dry_run", shown
    assert doc.get("dry_run") is True and doc.get("test_message") == "skipped_dry_run", shown
    assert doc.get("chat_id") == CHAT_ID, shown
    assert not _YAML_CHANGES & set(doc.get("changed") or []), (
        f"the real install is not what setup would write for this token and chat: {shown}")


def test_setup_chat_not_started(token_file, tmp_path):
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE_TG_") and k != "CLAUDE_CONFIG_DIR"}
    env["HOME"] = str(empty_home)
    before = _hashes()
    rc, out, err = _run([_aipager(), "setup", "--token-file", str(token_file),
                         "--chat-id", str(NEVER_STARTED_CHAT), "--json"], env=env)
    after = _hashes()
    _no_token_in(_token(), out, err)
    assert before == after, "setup changed a real file"
    written = sorted(str(p.relative_to(empty_home)) for p in empty_home.rglob("*")
                     if p.is_file())
    assert not [w for w in written if w.startswith((".config/aipager", ".claude/settings"))], \
        f"setup wrote files although it refused: {written}"
    doc = _doc(out)
    shown = _summary(doc, "status", "exit_code", "error", "changed", "test_message", "deps")
    if doc.get("error") == "deps_missing":
        pytest.skip(f"setup cannot find its dependencies with an empty HOME: {shown}")
    assert rc == 5 and doc.get("error") == "chat_not_started", shown
    assert doc.get("test_message") == "failed" and doc.get("changed") == [], shown
