"""The hook and the status line start lean, and still work.

Claude Code starts ``aipager-hook`` on every hook event (before and after
every tool call among them, and waits for it) and ``aipager-statusline``
on every status refresh. Both used to spend ~55 ms of a ~74 ms start
importing what they never use: ``aipager/__init__.py`` read the installed
version through importlib.metadata, and ``aipager/dtach/__init__.py``
imported ``inject`` (and asyncio) for seven re-exports. Both are now read
on first use.

The rest of the suite runs in one process that has imported everything
already, so it cannot see a module the hook forgot to import. These tests
run the real hook and status line in fresh interpreters.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# aipager itself is imported inside each test, never here: a broken lazy
# lookup in a package ``__init__`` must fail these tests, not stop the
# file from being collected.

REPO = Path(__file__).resolve().parents[1]
SESSION = "claude-lean-hook"
EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "Notification", "Stop", "StopFailure", "SubagentStart", "SubagentStop",
    "PreCompact", "PostCompact", "MessageDisplay", "PreModelSwitch",
)
#: Never imported by the hook or the status line at start.
HEAVY = ("asyncio", "importlib.metadata", "aipager.dtach.inject")


def _env(root: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AIPAGER"))}
    env.update(PYTHONPATH=str(REPO), AIPAGER_INSTANCE_DIR=str(root),
               CLAUDE_DTACH_SESSION=SESSION, HOME=str(root / "home"),
               AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS="0.3",
               # The hook catches most errors and only logs them: with
               # its debug log on stderr, a caught one is seen too.
               AIPAGER_DEBUG="1")
    return env


#: What a module the hook forgot to import shows as, caught or not (a
#: caught error is logged as its message only).
MISSING_IMPORT = ("NameError", "AttributeError", "ImportError", "ModuleNotFoundError",
                  "has no attribute", "No module named", "is not defined",
                  "cannot import name")


def _clean(stderr: str) -> bool:
    return "Traceback" not in stderr and not any(e in stderr for e in MISSING_IMPORT)


@pytest.fixture
def root():
    """A short folder (AF_UNIX paths must stay below 108 bytes) holding the
    instance's runtime files and a throwaway HOME."""
    path = Path(tempfile.mkdtemp(prefix="ahs-"))
    (path / "home").mkdir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def daemon(root):
    """A bound datagram socket standing in for the daemon's."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(root / "aipager.sock"))
    yield sock
    sock.close()


def _received(sock) -> list[dict]:
    out = []
    sock.settimeout(0.3)
    while True:
        try:
            out.append(json.loads(sock.recv(4_000_000)))
        except (socket.timeout, BlockingIOError):
            return out


def _run(root: Path, module: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", f"from aipager.dtach.{module} import main; main()"],
        input=json.dumps(payload), capture_output=True, text=True,
        env=_env(root), cwd=str(root), timeout=60)


# ---- what a fresh start imports --------------------------------------------------------

@pytest.mark.parametrize("module", ["notify_hook", "statusline_notify"])
def test_a_fresh_start_loads_nothing_heavy(root, module):
    # A name the packages do not have (a probe, a typo) loads nothing either.
    script = ("import json, sys; before = set(sys.modules); "
              f"assert not set({HEAVY!r}) & before, 'loaded before the test: ' + repr(set({HEAVY!r}) & before); "
              f"import aipager, aipager.dtach, aipager.dtach.{module}; "
              "hasattr(aipager, '__wrapped__'); hasattr(aipager.dtach, '__wrapped__'); "
              "hasattr(aipager.dtach, 'no_such_name'); "
              "print(json.dumps(sorted(set(sys.modules) - before)))")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env=_env(root), cwd=str(root), timeout=60)
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout)
    assert f"aipager.dtach.{module}" in loaded          # not vacuous
    assert [m for m in HEAVY if m in loaded] == []


def test_every_way_of_importing_the_packages_still_works(root):
    # A package ``__getattr__`` answering for a name it does not own would
    # shadow a submodule (``from aipager import dtach``) or recurse forever
    # importing one (``from aipager.dtach import inject``).
    script = ("from aipager import dtach, state; "
              "from aipager.dtach import inject, notify_hook, statusline_notify, enforce; "
              "import aipager.dtach.hook_receiver; "
              "assert dtach.__name__ == 'aipager.dtach' and inject.__name__ == 'aipager.dtach.inject'; "
              "print('ok')")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env=_env(root), cwd=str(root), timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip() == "ok", proc.stderr[-600:]


# ---- the real hook and status line, end to end ------------------------------------------

def _payload(event: str) -> dict:
    data = {"hook_event_name": event, "session_id": "s-lean", "cwd": "/tmp",
            "transcript_path": "/nonexistent/t.jsonl"}
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"):
        data.update(tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="toolu_1")
    if event in ("SubagentStart", "SubagentStop"):
        data.update(agent_id="a1", agent_type="Explore")
    if event == "UserPromptSubmit":
        data["prompt"] = "hello"
    return data


@pytest.mark.parametrize("event", EVENTS)
def test_the_hook_reaches_the_daemon_for_every_event(root, daemon, event):
    proc = _run(root, "notify_hook", _payload(event))
    assert proc.returncode == 0, proc.stderr
    assert _clean(proc.stderr), proc.stderr
    got = _received(daemon)
    if event == "PreModelSwitch":
        # Answered by the hook itself, from a local file; nothing to tell.
        assert got == []
        return
    assert got, f"no datagram for {event}"
    assert got[0]["hook_event_name"] == event
    assert all(g["session"] == SESSION for g in got), got
    if event == "PreToolUse":
        # No policy for this session: the safety check denies, as always.
        decision = json.loads(proc.stdout.strip().splitlines()[0])
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_status_line_reaches_the_daemon_and_writes_its_file(root, daemon):
    proc = _run(root, "statusline_notify", {
        "session_id": "s-lean", "model": {"display_name": "Opus"},
        "context_window": {"used_percentage": 3}, "cost": {"total_cost_usd": 0}})
    assert proc.returncode == 0, proc.stderr
    assert _clean(proc.stderr) and "Opus" in proc.stdout, proc.stderr
    got = _received(daemon)
    assert [(g.get("type"), g.get("session")) for g in got] == [("statusline", SESSION)]
    assert (root / f"claude-status-{SESSION}.json").exists()


# ---- the lazy names, in process -------------------------------------------------------------

def test_the_version_reads_as_before():
    import aipager
    from importlib.metadata import PackageNotFoundError, version
    try:
        expected = version("aipager")
    except PackageNotFoundError:
        expected = "0.0.0+unknown"
    from aipager import __version__
    assert __version__ == aipager.__version__ == getattr(aipager, "__version__", None) == expected


def test_the_version_is_read_once(monkeypatch):
    import importlib.metadata as metadata

    import aipager
    monkeypatch.delattr(aipager, "__version__", raising=False)   # as if never read
    calls = []
    monkeypatch.setattr(metadata, "version", lambda name: calls.append(name) or "1.2.3")
    assert aipager.__version__ == "1.2.3" and aipager.__version__ == "1.2.3"
    assert calls == ["aipager"]


def test_an_uninstalled_package_reads_the_fallback(monkeypatch):
    import importlib.metadata as metadata

    import aipager

    def _missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.delattr(aipager, "__version__", raising=False)
    monkeypatch.setattr(metadata, "version", _missing)
    assert aipager.__version__ == "0.0.0+unknown"


@pytest.mark.parametrize("form", ["dotted", "object"])
def test_a_test_can_still_pin_the_version(monkeypatch, form):
    import aipager
    real = aipager.__version__
    with monkeypatch.context() as m:
        if form == "dotted":
            m.setattr("aipager.__version__", "9.9.9")
        else:
            m.setattr(aipager, "__version__", "9.9.9")
        from aipager import __version__
        assert __version__ == "9.9.9"
    assert aipager.__version__ == real


def test_the_dtach_names_are_inject_s_own():
    from aipager import dtach
    from aipager.dtach import inject
    names = {}
    exec("from aipager.dtach import *", names)
    exported = sorted(n for n in names if not n.startswith("__"))
    assert exported == sorted(dtach.__all__) and len(exported) == 7
    for name in exported:
        assert names[name] is getattr(inject, name) is getattr(dtach, name)


def test_an_unknown_name_still_fails():
    import aipager
    from aipager import dtach
    with pytest.raises(AttributeError):
        aipager.no_such_name
    with pytest.raises(AttributeError):
        dtach.no_such_name
    with pytest.raises(ImportError):
        exec("from aipager.dtach import no_such_name", {})
