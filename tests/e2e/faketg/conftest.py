"""Fixtures for the fake-Telegram group-mode scenarios (opt-in).

Off unless ``AIPAGER_E2E_FAKETG=1``. ``AIPAGER_E2E_FAKETG_CLAUDE`` picks
the Claude side: ``real`` (default; needs a ``claude`` binary and
``CLAUDE_CODE_OAUTH_TOKEN`` in the environment, as run_e2e.sh provides)
or ``standin`` (no credential: ``tests/e2e/fake_telegram/standin_claude.py``
plays Claude, for plumbing runs).

Each module gets ONE isolated daemon (``faketg``) against its own fake
Bot API, torn down at module end even when a test fails; ``fresh`` ends
that daemon's sessions between tests. A session-wide autouse fixture
records the operator's real files and daemon first and asserts at the
end that nothing of theirs changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
from pathlib import Path

import pytest

from tests.e2e.fake_telegram import instance as fti

_SKIP_OPT_IN = "fake-Telegram scenarios are opt-in: set AIPAGER_E2E_FAKETG=1"
SKIP_NO_CREDENTIAL = ("no Claude credential; set AIPAGER_E2E_FAKETG_CLAUDE=standin "
                      "for the plumbing run")
_FAKE_ID_MARKERS = (f"__g{abs(fti.GROUP_ID)}", f"__g{abs(fti.SUPERGROUP_ID)}",
                    *(f"__d{uid}" for uid in fti.MEMBERS))


def claude_mode() -> str:
    return (os.environ.get("AIPAGER_E2E_FAKETG_CLAUDE") or "real").strip().lower()


def why_skip() -> str | None:
    if os.environ.get("AIPAGER_E2E_FAKETG") != "1":
        return _SKIP_OPT_IN
    mode = claude_mode()
    if mode not in ("real", "standin"):
        return f"AIPAGER_E2E_FAKETG_CLAUDE must be real or standin, not {mode!r}"
    if mode == "real":
        from tests.e2e import harness
        if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or harness.claude_bin() is None:
            return SKIP_NO_CREDENTIAL
    return None


def pytest_collection_modifyitems(config, items):
    here = str(Path(__file__).resolve().parent) + os.sep
    reason = why_skip()
    for item in items:
        if str(Path(str(item.fspath)).resolve()).startswith(here):
            item.add_marker(pytest.mark.fake_telegram)
            if reason:
                item.add_marker(pytest.mark.skip(reason=reason))


# ---------------------------------------------------------------------------
# The operator's real install: recorded first, checked last.
# ---------------------------------------------------------------------------

def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _fingerprint(path: Path) -> tuple[str, int] | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
    except OSError:
        return None


def _real_daemon_pids() -> list[int]:
    from aipager.wizard.daemon_io import _is_daemon_argv
    out = []
    for pid in fti._own_pids():
        argv = [a.decode(errors="replace")
                for a in fti._read(f"/proc/{pid}/cmdline").split(b"\0") if a]
        if argv and _is_daemon_argv(argv) and \
                b"AIPAGER_INSTANCE_DIR=" not in fti._read(f"/proc/{pid}/environ"):
            out.append(pid)
    return out


def _tmp_claude_names() -> set[str]:
    return {p.name for p in Path("/tmp").glob("claude-*")}


#: The harness's session labels: ``ft`` and a number (``ft0`` ... ``ft14``,
#: ``ft8r`` after the rename). A real label like ``ftp-sync`` is not one.
_HARNESS_LABEL_RE = re.compile(r"ft\d")


def harness_names_in(names) -> list[str]:
    return sorted(n for n in names
                  if any(m in n for m in _FAKE_ID_MARKERS)
                  or _HARNESS_LABEL_RE.match(n.removeprefix("claude-")))


def _real_files() -> list[Path]:
    """Fingerprinted by sha256 AND mtime. The real policy floor is
    rewritten whenever the operator's daemon starts or reloads, so a
    reload during the run (``aipager config``, a SIGUSR1, a restart)
    fails the session check: it errs on the side of a false alarm."""
    home = _real_home()
    return [home / ".config" / "aipager" / "aipager.yaml",
            home / ".claude" / "settings.json",
            Path(f"/tmp/claude-policy-.floor-{os.getuid()}.json")]


#: What the session fixture recorded first; read by mid-run checks.
RECORDED: dict = {}


def check_real_install() -> None:
    """The operator's files and daemons as recorded at session start
    (callable mid-run; the session fixture calls it at the end too)."""
    before = RECORDED["files"]
    after = {str(p): _fingerprint(p) for p in _real_files()}
    changed = [p for p in before if before[p] != after[p]]
    assert not changed, f"the operator's real files changed: {changed}"
    dead = [p for p in RECORDED["pids"] if not fti._alive(p)]
    assert not dead, f"a real daemon died during the run: {dead}"


@pytest.fixture(scope="session", autouse=True)
def _real_install_untouched():
    home = _real_home()
    RECORDED["files"] = {str(p): _fingerprint(p) for p in _real_files()}
    RECORDED["pids"] = _real_daemon_pids()
    tmp_before = _tmp_claude_names()
    yield
    check_real_install()
    registry = home / ".claude" / "aipager-sessions.json"
    try:
        names = json.loads(registry.read_text()).get("sessions", {})
    except (OSError, ValueError):
        names = {}
    assert not harness_names_in(names), harness_names_in(names)
    new_tmp = _tmp_claude_names() - tmp_before
    assert not harness_names_in(new_tmp), harness_names_in(new_tmp)


# ---------------------------------------------------------------------------
# One isolated daemon per module.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def faketg():
    inst = fti.TestInstance(claude_mode=claude_mode())
    try:
        inst.build()
        inst.start()
        yield inst
    finally:
        root = inst.root
        inst.stop()
        if root is not None:
            assert not root.exists(), f"instance root left behind: {root}"
            assert not fti.pids_referencing(root / "i")


@pytest.fixture
def fresh(faketg):
    """The module's daemon with no live session; ends this test's sessions
    afterwards and checks the daemon called nothing the fake lacks."""
    if faketg.live_sockets():
        faketg.kill_sessions()
    yield faketg
    assert faketg.fake.unknown_methods == [], faketg.fake.unknown_methods
    if faketg.live_sockets():
        faketg.kill_sessions()
