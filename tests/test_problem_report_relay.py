"""Problem reports, step 1b2 (roadmap 8.112): errors in the hook and the
command line reach the daemon's report store as ONE datagram of shapes.

The hook is the security gate (PreToolUse) and sees the tool's input, so
the error path must not change any answer, must not slow the happy path,
and must carry nothing from the payload or the error's text. The daemon's
socket is writable by any local process, so it checks every datagram
whole.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import _test_guard, errors
from aipager.dtach import enforce, notify_hook
from aipager.dtach import hook_receiver as hr
from aipager.report import fingerprint as fp
from aipager.report import relay, store
from aipager.state import SessionRegistry
from tests.test_problem_report_privacy import (
    CHAT, CWD, LABEL, PROMPT, TOKEN, USERNAME, assert_no_leak,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def short_root():
    """A short folder (AF_UNIX paths must stay below 108 bytes)."""
    root = tempfile.mkdtemp(prefix="aprp-")
    yield Path(root)
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def daemon_socket(short_root):
    """A bound datagram socket standing in for the daemon's."""
    path = short_root / "aipager.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(path))
    sock.settimeout(2)
    yield sock, path
    sock.close()


def _received(sock) -> list[bytes]:
    out = []
    sock.settimeout(0.3)
    while True:
        try:
            out.append(sock.recv(65536))
        except (socket.timeout, BlockingIOError):
            return out


def _ours(make):
    namespace = {"__name__": "aipager.dtach.enforce", "make": make, "token": TOKEN,
                 "cwd": CWD}
    # A frame the daemon accepts names real code: ``decide`` is defined in
    # enforce.py, and line 3 lies inside it.
    exec(compile("def decide():\n    command = token + cwd\n    raise make()\n",
                 str(fp.PACKAGE_DIR / "dtach" / "enforce.py"), "exec"), namespace)
    try:
        namespace["decide"]()
    except BaseException as exc:  # noqa: BLE001 - the exception is the input
        return exc
    raise AssertionError


def _hostile_error():
    exc = _ours(lambda: ValueError(f"rm -rf {CWD} {TOKEN} chat {CHAT} {PROMPT}"))
    exc.__notes__ = [f"{USERNAME} {LABEL}"]
    return exc


# ---- the datagram ---------------------------------------------------------------

def test_the_datagram_carries_shapes_only():
    payload = relay.error_datagram(_hostile_error(), where="hook", event="PreToolUse",
                                   tool=f"mcp__{USERNAME}__{LABEL}", denied=True)
    assert_no_leak(payload.decode())
    message = json.loads(payload)
    assert set(message) == {"type", "where", "event", "tool", "denied", "env", "facts"}
    assert (message["tool"], message["denied"], message["env"]) == ("mcp", True, None)
    assert message["facts"]["type"] == "builtins.ValueError"
    assert message["facts"]["frames"][0]["file"] == "aipager/dtach/enforce.py"


def test_denied_is_only_ever_a_true_bool():
    for denied, sent in ((True, True), (False, False), (1, False), ("yes", False)):
        message = json.loads(relay.error_datagram(_hostile_error(), where="hook", denied=denied))
        assert message["denied"] is sent


def test_an_environment_error_sends_its_counter_and_no_facts():
    message = json.loads(relay.error_datagram(
        _ours(lambda: OSError(28, f"No space {CWD}")), where="hook", event="Stop"))
    assert message["env"] == "env_disk_full" and "facts" not in message


@pytest.mark.parametrize(("tool", "kind"), [("Bash", "Bash"), ("mcp__srv__x", "mcp"),
                                            (f"{USERNAME}Tool", "other"), (None, None),
                                            ("", None), (["Bash"], None)])
def test_a_tool_is_named_by_kind(tool, kind):
    assert relay.tool_kind(tool) == kind


@pytest.mark.parametrize("event", [f"{USERNAME} x", "Pre7770001234", "x" * 40, 5, ["PreToolUse"]])
def test_an_odd_event_name_is_left_out(event):
    message = json.loads(relay.error_datagram(_hostile_error(), where="hook", event=event))
    assert message["event"] is None


def test_building_the_datagram_never_raises(monkeypatch):
    monkeypatch.setattr(fp, "describe_exception", lambda exc: 1 / 0)
    assert relay.error_datagram(_hostile_error(), where="hook") is None


def test_the_daemon_reads_back_exactly_what_was_sent():
    payload = relay.error_datagram(_hostile_error(), where="hook", event="PreToolUse",
                                   tool="Bash", denied=True)
    message = json.loads(payload)
    assert relay.read_error_datagram(message) == message
    env = json.loads(relay.error_datagram(OSError(28, "x"), where="cli"))
    assert relay.read_error_datagram(env) == env


def _good() -> dict:
    return json.loads(relay.error_datagram(_hostile_error(), where="hook", event="PreToolUse",
                                           tool="Bash", denied=False))


@pytest.mark.parametrize("change", [
    {"session": LABEL}, {"type": "report_errors"}, {"where": "daemon"}, {"where": CWD},
    {"where": "statusline"},
    {"denied": 1}, {"denied": "yes"}, {"event": USERNAME}, {"event": "A" * 33},
    {"tool": LABEL}, {"tool": "Bash "}, {"env": "env_cnry"}, {"env": "tg_5xx"},
    {"event": "Cnryuserqx"},
    {"env": ["env_network"]},
    {"facts": {}}, {"facts": None}, {"facts": "x"}])
def test_a_hostile_datagram_is_dropped_whole(change):
    message = dict(_good(), **change)
    assert relay.read_error_datagram(message) is None


@pytest.mark.parametrize("facts_change", [
    {"fingerprint": f"ap1-{CHAT}"}, {"type": "customer_billing.Error"}, {"type": None},
    {"frames": [{"file": f"{CWD}/x.py", "line": 1, "fn": "f"}]},
    {"frames": [{"file": "aipager/state.py", "line": 1, "fn": "f", "msg": PROMPT}]},
    {"external": [LABEL]}, {"errno": TOKEN}, {"cause_types": [USERNAME]},
    {"message": PROMPT}])
def test_hostile_facts_drop_the_datagram(facts_change):
    good = _good()
    message = dict(good, facts=dict(good["facts"], **facts_change))
    assert relay.read_error_datagram(message) is None


@pytest.mark.parametrize("missing", ["type", "cause_types", "errno", "frames", "external"])
def test_facts_missing_a_key_drop_the_datagram(missing):
    good = _good()
    facts = {k: v for k, v in good["facts"].items() if k != missing}
    assert relay.read_error_datagram(dict(good, facts=facts)) is None


def test_missing_keys_or_both_env_and_facts_are_dropped():
    good = _good()
    for key in good:
        assert relay.read_error_datagram({k: v for k, v in good.items() if k != key}) is None
    assert relay.read_error_datagram(dict(good, env="env_network")) is None
    for value in (None, [], "x", 5):
        assert relay.read_error_datagram(value) is None


# ---- the store ------------------------------------------------------------------

@pytest.mark.parametrize(("where", "event", "tool", "denied", "trigger", "counter"), [
    ("hook", "PreToolUse", "Bash", False, "hook_error", "hook_error"),
    ("hook", "PreToolUse", "Bash", True, "fail_closed", "hook_fail_closed"),
    ("cli", None, None, False, "crash", None)])
def test_a_relayed_error_is_recorded_as_a_bug_of_its_kind(where, event, tool, denied, trigger,
                                                         counter):
    message = json.loads(relay.error_datagram(_hostile_error(), where=where, event=event,
                                              tool=tool, denied=denied))
    store.record_relayed(relay.read_error_datagram(message))
    [entry] = store.errors()
    assert (entry["where"], entry["trigger"], entry["tier"]) == (where, trigger, "bug")
    assert (entry["event"], entry["tool"]) == (event, tool)
    assert store.counters_24h() == ({counter: 1} if counter else {})


@pytest.mark.parametrize("change", [
    {"event": "PreToolUse"}, {"tool": "Bash"}, {"denied": True}])
def test_a_cli_datagram_with_hook_fields_is_dropped(change):
    message = dict(json.loads(relay.error_datagram(_hostile_error(), where="cli")), **change)
    assert relay.read_error_datagram(message) is None


@pytest.mark.parametrize("value", [[], {}, ["env_network"], {"env_network": 1}])
@pytest.mark.parametrize("key", ["env", "where", "event", "tool", "denied", "type"])
def test_an_unhashable_field_is_dropped_without_raising(key, value):
    """The receiver reads a datagram in a task: a raise there is logged,
    and a logged error is recorded as a bug a stranger made up."""
    message = json.loads(relay.error_datagram(OSError(28, "x"), where="hook"))
    assert relay.read_error_datagram(dict(message, **{key: value})) is None


def test_reading_a_datagram_never_raises(monkeypatch):
    from aipager.report import schema

    def _boom(*args):
        raise ValueError("x")

    monkeypatch.setattr(schema, "validate_part", _boom)
    assert relay.read_error_datagram(_good()) is None


def test_a_relayed_environment_error_is_only_counted():
    message = json.loads(relay.error_datagram(OSError(28, "x"), where="hook", denied=True))
    store.record_relayed(relay.read_error_datagram(message))
    assert store.errors() == []
    assert store.counters_24h() == {"hook_fail_closed": 1, "env_disk_full": 1}


def test_recording_a_relayed_error_never_raises():
    assert store.record_relayed({"where": "hook"}) is None
    assert store.record_relayed(None) is None


# ---- the daemon's receiver --------------------------------------------------------

@pytest.fixture
def receiver():
    return hr.HookReceiver(SessionRegistry(), AsyncMock())


def _deliver(recv, data: bytes):
    asyncio.run(recv._on_datagram(data))


def test_the_receiver_records_a_report_error_without_a_session(receiver):
    _deliver(receiver, relay.error_datagram(_hostile_error(), where="hook", event="PreToolUse",
                                            tool="Bash", denied=True))
    assert store.errors()[0]["trigger"] == "fail_closed"
    assert receiver.registry._sessions == {}          # no session made up for it
    assert_no_leak(json.dumps(store.errors()))


@pytest.mark.parametrize("data", [
    b"[]", b"5", b'"x"', b"null", b"[" * 100_000 + b"]" * 100_000, b"\xff",
    json.dumps({"type": "report_error", "where": "hook", "session": LABEL}).encode(),
    json.dumps({"type": "report_error", "where": "hook", "event": None, "tool": None,
                "denied": False, "env": None, "facts": {}, "note": PROMPT}).encode(),
    json.dumps({"type": "report_error", "where": "hook", "event": None, "tool": None,
                "denied": False, "env": "tg_5xx"}).encode()])
def test_the_receiver_drops_what_is_not_ours_and_never_raises(receiver, data):
    _deliver(receiver, data)
    assert store.errors() == [] and store.counters_24h() == {}
    assert receiver.registry._sessions == {}


@pytest.mark.parametrize(("hook", "where", "file"), [
    ("aipager-hook", "hook", "aipager/dtach/notify_hook.py"),
    ("aipager-statusline", "statusline", "aipager/dtach/statusline_notify.py")])
def test_a_cap_hit_is_a_bug_and_counted(receiver, hook, where, file):
    _deliver(receiver, json.dumps({"type": "hook_memory_cap_hit", "session": "claude-cap-x",
                                   "hook": hook, "tool": f"mcp__{USERNAME}"}).encode())
    [entry] = store.errors()
    assert (entry["where"], entry["trigger"], entry["tier"]) == (where, "hook_cap", "bug")
    assert entry["frames"][0]["file"] == file
    assert store.counters_24h()["hook_cap_hit"] == 1
    assert_no_leak(json.dumps(store.errors()))


def test_a_cap_hit_from_an_unknown_hook_is_not_recorded(receiver):
    _deliver(receiver, json.dumps({"type": "hook_memory_cap_hit", "session": "claude-cap-x",
                                   "hook": LABEL}).encode())
    assert store.errors() == [] and store.counters_24h() == {}


# ---- the hook: answers unchanged, reports after them -------------------------------

def _run_hook(monkeypatch, payload: dict, sock_path) -> str:
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    notify_hook._ERROR_CONTEXT.update(event=None, tool=None)
    notify_hook._run("claude-relay-test", [b"", False])
    return out.getvalue()


def _pre_tool_use(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "session_id": "s", "cwd": CWD}


def test_a_decision_that_fails_still_denies_then_reports(monkeypatch, daemon_socket):
    sock, path = daemon_socket

    def _boom(data):
        raise _hostile_error()

    monkeypatch.setattr(enforce, "_decide", _boom)
    monkeypatch.setattr(enforce, "read_snapshot", lambda session: None)
    out = _run_hook(monkeypatch, _pre_tool_use(f"cat {CWD}/{TOKEN}"), path)
    assert json.loads(out.strip().splitlines()[0])["hookSpecificOutput"][
        "permissionDecision"] == "deny"
    reports = [m for m in map(json.loads, _received(sock)) if m.get("type") == "report_error"]
    assert len(reports) == 1 and reports[0]["denied"] is True
    assert (reports[0]["event"], reports[0]["tool"]) == ("PreToolUse", "Bash")
    for message in reports:
        assert_no_leak(json.dumps(message))


def test_an_owner_bypass_is_reported_as_not_denied(monkeypatch, daemon_socket):
    sock, path = daemon_socket

    def _boom(data):
        raise ValueError("x")

    monkeypatch.setattr(enforce, "_decide", _boom)
    monkeypatch.setattr(enforce, "read_snapshot", lambda session: {"bypass_safety": True})
    out = _run_hook(monkeypatch, _pre_tool_use("ls"), path)
    assert "deny" not in out
    [report] = [m for m in map(json.loads, _received(sock)) if m.get("type") == "report_error"]
    assert report["denied"] is False


def test_a_decision_that_works_reports_nothing(monkeypatch, daemon_socket):
    sock, path = daemon_socket
    monkeypatch.setattr(enforce, "_decide", lambda data: None)
    _run_hook(monkeypatch, _pre_tool_use("ls"), path)
    assert [m for m in map(json.loads, _received(sock)) if m.get("type") == "report_error"] == []
    assert enforce.take_decide_error() is None


def test_the_decide_error_is_taken_once():
    enforce._decide_error = ValueError("x")
    assert isinstance(enforce.take_decide_error(), ValueError)
    assert enforce.take_decide_error() is None


def test_an_earlier_decide_error_is_never_reported_for_a_decision_that_worked(
        monkeypatch, daemon_socket):
    sock, path = daemon_socket
    monkeypatch.setattr(enforce, "_decide_error", ValueError("an earlier call's"))
    monkeypatch.setattr(enforce, "_decide", lambda data: None)
    out = _run_hook(monkeypatch, _pre_tool_use("ls"), path)
    assert "deny" not in out
    assert [m for m in map(json.loads, _received(sock)) if m.get("type") == "report_error"] == []


def test_an_unavailable_enforcer_still_denies_then_reports(monkeypatch, daemon_socket):
    sock, path = daemon_socket
    import builtins
    real_import = builtins.__import__

    def _no_enforce(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "aipager.dtach" and fromlist and "enforce" in fromlist:
            raise ImportError("enforce is broken")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _no_enforce)
    out = _run_hook(monkeypatch, _pre_tool_use("ls"), path)
    monkeypatch.setattr(builtins, "__import__", real_import)
    assert '"permissionDecision": "deny"' in out
    [report] = [m for m in map(json.loads, _received(sock)) if m.get("type") == "report_error"]
    assert report["denied"] is True and report["facts"]["type"] == "builtins.ImportError"


def _hook_process(instance: Path, payload: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AIPAGER"))}
    env.update(PYTHONPATH=str(REPO), AIPAGER_INSTANCE_DIR=str(instance),
               CLAUDE_DTACH_SESSION="claude-relay-8112", HOME=str(instance / "home"))
    return subprocess.run([sys.executable, "-c",
                           "from aipager.dtach.notify_hook import main; main()"],
                          input=payload, capture_output=True, text=True, env=env, timeout=60)


def test_an_unreadable_pre_tool_use_is_denied_and_reported(short_root):
    """A payload the hook cannot decode (8.107) is denied as before, and the
    daemon hears of it: shape only, nothing of the payload."""
    receiver_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver_sock.bind(str(short_root / "aipager.sock"))
    try:
        payload = '{"hook_event_name": "PreToolUse", "tool_input": ' + "[" * 100_000 \
            + json.dumps(f"{TOKEN} {CWD}") + "]" * 100_000 + "}"
        proc = _hook_process(short_root, payload)
        assert '"permissionDecision": "deny"' in proc.stdout
        reports = [m for m in map(json.loads, _received(receiver_sock))
                   if m.get("type") == "report_error"]
    finally:
        receiver_sock.close()
    assert len(reports) == 1 and reports[0]["denied"] is True
    assert reports[0]["event"] == "PreToolUse"
    assert relay.read_error_datagram(reports[0]) is not None
    assert_no_leak(json.dumps(reports))


def _patched_hook_process(instance: Path, payload: str, patch: str
                          ) -> subprocess.CompletedProcess:
    """The real hook's ``main()`` in a child, after *patch* (code run with
    the hook module as ``h``) breaks one of its parts."""
    script = ("from aipager.dtach import notify_hook as h\n"
              f"{patch}\n"
              "h.main()\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AIPAGER"))}
    env.update(PYTHONPATH=str(REPO), AIPAGER_INSTANCE_DIR=str(instance),
               CLAUDE_DTACH_SESSION="claude-relay-8112", HOME=str(instance / "home"))
    return subprocess.run([sys.executable, "-c", script], input=payload, capture_output=True,
                          text=True, env=env, timeout=60)


def _reports_from_child(short_root, payload: str, patch: str):
    receiver_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver_sock.bind(str(short_root / "aipager.sock"))
    try:
        proc = _patched_hook_process(short_root, payload, patch)
        reports = [m for m in map(json.loads, _received(receiver_sock))
                   if m.get("type") == "report_error"]
    finally:
        receiver_sock.close()
    return proc, reports


def test_a_hook_crash_on_another_event_keeps_its_exit_and_is_reported(short_root):
    """Row 10: a crash outside PreToolUse still exits 1 with no answer
    (Claude Code shows a hook error and never blocks), and the daemon now
    hears of it."""
    patch = ("def _boom(session):\n"
             f"    raise ValueError({TOKEN!r} + {CWD!r})\n"
             "h._read_statusline_tokens = _boom\n")
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                          "tool_input": {"command": f"cat {CWD}/{TOKEN}"}, "session_id": "s"})
    proc, reports = _reports_from_child(short_root, payload, patch)
    assert proc.returncode == 1 and proc.stdout == ""
    assert len(reports) == 1
    assert (reports[0]["event"], reports[0]["tool"], reports[0]["denied"]) == (
        "PostToolUse", "Bash", False)
    assert relay.read_error_datagram(reports[0]) is not None
    assert_no_leak(json.dumps(reports))


def test_a_deny_that_cannot_be_written_still_exits_2_and_is_reported(short_root):
    patch = ("def _broken(session, error):\n"
             "    raise OSError('stdout is gone')\n"
             "h._deny_unhandled_pre_tool_use = _broken\n")
    payload = '{"hook_event_name": "PreToolUse", "tool_input": ' + "[" * 100_000 + "]" * 100_000 \
        + "}"
    proc, reports = _reports_from_child(short_root, payload, patch)
    assert proc.returncode == 2
    assert len(reports) == 1 and reports[0]["denied"] is True


def test_a_deny_exit_survives_a_report_that_raises(short_root):
    """The report runs before the exit 2; even a report that escapes its
    own guard cannot turn that deny into another exit."""
    patch = ("def _broken(session, error):\n"
             "    raise OSError('stdout is gone')\n"
             "h._deny_unhandled_pre_tool_use = _broken\n"
             "def _report(error, *, denied):\n"
             "    raise KeyboardInterrupt\n"
             "h._report_error = _report\n")
    payload = '{"hook_event_name": "PreToolUse", "tool_input": ' + "[" * 100_000 + "]" * 100_000 \
        + "}"
    proc, _reports = _reports_from_child(short_root, payload, patch)
    assert proc.returncode == 2


_REPORT_RAISES = ("def _report(error, *, denied):\n"
                  "    raise MemoryError\n"
                  "h._report_error = _report\n")
_UNREADABLE_PRE_TOOL_USE = ('{"hook_event_name": "PreToolUse", "tool_input": '
                            + "[" * 100_000 + "]" * 100_000 + "}")


def _decided_deny(proc) -> bool:
    return proc.returncode == 0 and '"permissionDecision": "deny"' in proc.stdout


def test_a_report_that_raises_after_a_printed_deny_keeps_the_deny(short_root):
    """rev 1b2-001: a deny printed by main() exits 0 (Claude Code reads the
    answer only then) even when the report after it raises; an exit 1
    would be a non-blocking error, and the tool would run."""
    proc, _reports = _reports_from_child(short_root, _UNREADABLE_PRE_TOOL_USE, _REPORT_RAISES)
    assert _decided_deny(proc), (proc.returncode, proc.stdout[-200:], proc.stderr[-300:])


def test_a_report_that_raises_after_a_failed_decision_keeps_the_deny(short_root):
    patch = _REPORT_RAISES + (
        "from aipager.dtach import enforce\n"
        "def _boom(data):\n"
        "    raise ValueError('x')\n"
        "enforce._decide = _boom\n"
        "enforce.read_snapshot = lambda session: None\n")
    payload = json.dumps(_pre_tool_use("ls"))
    proc, _reports = _reports_from_child(short_root, payload, patch)
    assert _decided_deny(proc), (proc.returncode, proc.stdout[-200:], proc.stderr[-300:])


def test_a_report_that_raises_after_an_owner_bypass_keeps_the_allow(short_root):
    patch = _REPORT_RAISES + (
        "from aipager.dtach import enforce\n"
        "enforce.read_snapshot = lambda session: {'bypass_safety': True}\n")
    proc, _reports = _reports_from_child(short_root, _UNREADABLE_PRE_TOOL_USE, patch)
    assert proc.returncode == 0 and "deny" not in proc.stdout


def test_a_report_that_raises_after_an_unavailable_enforcer_keeps_the_deny(short_root):
    patch = _REPORT_RAISES + (
        "import builtins\n"
        "_real = builtins.__import__\n"
        "def _no_enforce(name, globals=None, locals=None, fromlist=(), level=0):\n"
        "    if name == 'aipager.dtach' and fromlist and 'enforce' in fromlist:\n"
        "        raise ImportError('enforce is broken')\n"
        "    return _real(name, globals, locals, fromlist, level)\n"
        "builtins.__import__ = _no_enforce\n")
    proc, _reports = _reports_from_child(short_root, json.dumps(_pre_tool_use("ls")), patch)
    assert _decided_deny(proc), (proc.returncode, proc.stdout[-200:], proc.stderr[-300:])


def test_a_report_that_raises_after_another_crash_keeps_that_crash(short_root):
    patch = _REPORT_RAISES + ("def _boom(session):\n"
                              "    raise ValueError('the real one')\n"
                              "h._read_statusline_tokens = _boom\n")
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                          "tool_input": {"command": "ls"}, "session_id": "s"})
    proc, _reports = _reports_from_child(short_root, payload, patch)
    assert proc.returncode == 1 and proc.stdout == ""
    assert "ValueError: the real one" in proc.stderr and "MemoryError" not in proc.stderr


def test_the_report_itself_swallows_even_a_memory_error(monkeypatch, short_root):
    from aipager.report import relay as relay_module
    monkeypatch.setattr(relay_module, "error_datagram", lambda *a, **k: (_ for _ in ()).throw(
        MemoryError()))
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(short_root / "x.sock"))
    notify_hook._report_error(ValueError("x"), denied=True)


def test_the_happy_path_never_loads_the_report_modules(short_root):
    script = ("import atexit, sys\n"
              "atexit.register(lambda: sys.stderr.write('LOADED' if any("
              "m.startswith('aipager.report') for m in sys.modules) else 'CLEAN'))\n"
              "from aipager.dtach.notify_hook import main\nmain()\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "AIPAGER"))}
    env.update(PYTHONPATH=str(REPO), AIPAGER_INSTANCE_DIR=str(short_root),
               CLAUDE_DTACH_SESSION="claude-relay-8112", HOME=str(short_root / "home"))
    for payload in (_pre_tool_use("ls"), {"hook_event_name": "Stop", "session_id": "s"}):
        proc = subprocess.run([sys.executable, "-c", script], input=json.dumps(payload),
                              capture_output=True, text=True, env=env, timeout=60)
        assert proc.stderr.endswith("CLEAN"), proc.stderr[-300:]


def test_the_send_never_raises_or_waits(monkeypatch, short_root):
    # No daemon at all.
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(short_root / "missing.sock"))
    notify_hook._report_error(ValueError("x"), denied=False)
    # A daemon that reads nothing: fill its queue, then report.
    path = short_root / "full.sock"
    full = _full_socket(path)  # (socket, senders)
    try:
        monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(path))
        assert _returns_at_once(lambda: notify_hook._report_error(_hostile_error(), denied=True))
    finally:
        _close_full(full)


# ---- the command line -------------------------------------------------------------

def test_a_cli_crash_reaches_a_listening_daemon(monkeypatch, daemon_socket, capsys):
    sock, path = daemon_socket
    monkeypatch.setattr(errors, "_report_socket_path", lambda: str(path))
    original = sys.excepthook
    try:
        errors.install_excepthook()
        exc = _hostile_error()
        sys.excepthook(type(exc), exc, exc.__traceback__)
    finally:
        sys.excepthook = original
    assert "unexpected error" in capsys.readouterr().err
    [message] = [json.loads(m) for m in _received(sock)]
    assert message["where"] == "cli" and relay.read_error_datagram(message) is not None
    assert_no_leak(json.dumps(message))


def test_a_cli_crash_with_no_daemon_writes_nothing(monkeypatch, short_root):
    target = short_root / "aipager.sock"
    monkeypatch.setattr(errors, "_report_socket_path", lambda: str(target))
    errors._report_crash(_hostile_error())
    assert list(short_root.iterdir()) == []


def test_an_unexplained_os_error_in_a_command_is_reported(monkeypatch, daemon_socket, capsys):
    """Row 24's other path: an OSError a command handler could not explain
    shows the bug link, so it is a crash too."""
    import errno as errno_mod
    sock, path = daemon_socket
    monkeypatch.setattr(errors, "_report_socket_path", lambda: str(path))

    @errors.with_friendly_errors
    def command():
        raise OSError(errno_mod.EIO, f"I/O error {CWD}/{TOKEN}")

    with pytest.raises(SystemExit):
        command()
    assert "OSError" in capsys.readouterr().err       # the friendly error is unchanged
    [message] = [json.loads(m) for m in _received(sock)]
    assert message["where"] == "cli" and message["facts"]["type"] == "builtins.OSError"
    assert relay.read_error_datagram(message) is not None
    assert_no_leak(json.dumps(message))


def test_an_explained_os_error_in_a_command_is_not_reported(monkeypatch, daemon_socket):
    import errno as errno_mod
    sock, path = daemon_socket
    monkeypatch.setattr(errors, "_report_socket_path", lambda: str(path))

    @errors.with_friendly_errors
    def command():
        raise FileNotFoundError(errno_mod.ENOENT, "missing", f"{CWD}/x")

    with pytest.raises(SystemExit):
        command()
    assert _received(sock) == []


def test_an_interrupt_is_no_crash(monkeypatch, daemon_socket):
    sock, path = daemon_socket
    monkeypatch.setattr(errors, "_report_socket_path", lambda: str(path))
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None)
    original = sys.excepthook
    try:
        errors.install_excepthook()
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    finally:
        sys.excepthook = original
    assert _received(sock) == []


# ---- never the live daemon from a test ----------------------------------------------

def test_a_test_can_never_send_to_the_live_daemon(real_home_refusals_expected):
    for live in _test_guard._live_control_sockets():
        with pytest.raises(_test_guard.LiveSocketSendError):
            _test_guard.check_send(live)
    _test_guard.check_send("/tmp/pytest-of-x/some.sock")


def test_conftest_points_every_sender_at_a_tmp_path():
    assert notify_hook.SOCKET_PATH not in _test_guard._live_control_sockets()
    assert errors._report_socket_path() not in _test_guard._live_control_sockets()


def test_the_daemon_names_a_relayed_error_as_it_would_its_own():
    """The fingerprint is computed from the checked type and frames, and is
    the one the error would have in the daemon itself."""
    exc = _hostile_error()
    message = relay.read_error_datagram(json.loads(relay.error_datagram(exc, where="hook")))
    assert "fingerprint" not in message["facts"]
    store.record_relayed(message)
    assert store.errors()[0]["fingerprint"] == fp.fingerprint(exc)


@pytest.mark.parametrize(("file", "module"), [
    ("aipager/dtach/enforce.py", "aipager.dtach.enforce"), ("aipager/bot/__init__.py", "aipager.bot"),
    ("aipager/__init__.py", "aipager")])
def test_a_file_names_its_module(file, module):
    assert fp.module_of(file) == module


def _deep(depth: int):
    namespace = {"__name__": "aipager.state"}
    exec(compile("def down(n):\n    return down(n - 1) if n else 1 / 0\n",
                 str(fp.PACKAGE_DIR / "state.py"), "exec"), namespace)
    try:
        namespace["down"](depth)
    except ZeroDivisionError as exc:
        return exc
    raise AssertionError


def test_frames_past_the_eighth_do_not_change_the_fingerprint():
    a, b = _deep(9), _deep(10)
    assert len(fp.aipager_frames(a)) != len(fp.aipager_frames(b))
    assert fp.fingerprint(a) == fp.fingerprint(b)


# ---- the hook's main(): its own errors -------------------------------------------

def _main(monkeypatch, raw: str, sock_path):
    """main() in-process, without its address-space cap (never on pytest)."""
    import resource
    monkeypatch.setattr(resource, "setrlimit", lambda *a: None)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    notify_hook._ERROR_CONTEXT.update(event=None, tool=None)
    return out


def test_a_crash_on_another_event_is_reported_and_still_raised(monkeypatch, daemon_socket):
    sock, path = daemon_socket
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-relay-test")

    def _boom(*a, **k):
        raise RuntimeError(f"stop failed {TOKEN}")

    monkeypatch.setattr(notify_hook, "_end_turn", _boom)
    _main(monkeypatch, json.dumps({"hook_event_name": "Stop", "session_id": "s"}), path)
    with pytest.raises(RuntimeError):
        notify_hook.main()
    [report] = [m for m in map(json.loads, _received(sock)) if m.get("type") == "report_error"]
    assert (report["event"], report["denied"]) == ("Stop", False)
    assert_no_leak(json.dumps(report))


def test_an_unreadable_pre_tool_use_of_an_owner_is_reported_as_not_denied(monkeypatch,
                                                                           daemon_socket):
    sock, path = daemon_socket
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-relay-test")
    monkeypatch.setattr(enforce, "read_snapshot", lambda session: {"bypass_safety": True})
    out = _main(monkeypatch, '{"hook_event_name": "PreToolUse", "tool_input": [' + CWD, path)
    notify_hook.main()
    assert "deny" not in out.getvalue()
    [report] = [m for m in map(json.loads, _received(sock)) if m.get("type") == "report_error"]
    assert (report["event"], report["denied"]) == ("PreToolUse", False)


# ---- the senders refuse the live daemon under pytest, and only then ---------------

@pytest.fixture
def fake_live(monkeypatch, daemon_socket):
    """The bound test socket plays the live daemon's for check_send."""
    sock, path = daemon_socket
    monkeypatch.setattr(_test_guard, "_live_control_sockets", lambda: {str(path)})
    return sock, path


def test_the_hook_never_sends_to_the_live_daemon_from_a_test(monkeypatch, fake_live,
                                                              real_home_refusals_expected):
    sock, path = fake_live
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(path))
    notify_hook._report_error(_hostile_error(), denied=True)
    assert _received(sock) == []
    assert any("live daemon socket" in r for r in _test_guard.refusals)


def test_the_cli_never_sends_to_the_live_daemon_from_a_test(monkeypatch, fake_live,
                                                             real_home_refusals_expected):
    sock, path = fake_live
    monkeypatch.setattr(errors, "_report_socket_path", lambda: str(path))
    errors._report_crash(_hostile_error())
    assert _received(sock) == []
    assert any("live daemon socket" in r for r in _test_guard.refusals)


def test_outside_pytest_the_live_daemon_is_reachable(monkeypatch):
    monkeypatch.setattr(_test_guard, "under_pytest", lambda: False)
    for live in _test_guard._live_control_sockets():
        _test_guard.check_send(live)


def test_the_live_sockets_are_the_daemon_defaults():
    assert _test_guard._live_control_sockets() == {
        "/tmp/aipager.sock", f"/run/user/{os.getuid()}/aipager.sock"}


def _full_socket(path: Path):
    """A bound socket whose receive queue is full: senders are added until
    a fresh one cannot queue a single datagram (one sender alone only
    fills its own send buffer, and a new socket could still send)."""
    full = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    full.bind(str(path))
    senders = []
    while True:
        sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sender.setblocking(False)
        senders.append(sender)
        sent = 0
        try:
            while True:
                sender.sendto(b"x", str(path))
                sent += 1
        except BlockingIOError:
            pass
        if sent == 0:
            break
    return full, senders


def _close_full(full_and_senders) -> None:
    full, senders = full_and_senders
    full.close()
    for sender in senders:
        sender.close()


def _returns_at_once(call) -> bool:
    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(timeout=2)
    return not worker.is_alive()


def test_the_cli_send_never_waits_on_a_full_daemon(monkeypatch, short_root):
    path = short_root / "full.sock"
    full = _full_socket(path)  # (socket, senders)
    try:
        monkeypatch.setattr(errors, "_report_socket_path", lambda: str(path))
        assert _returns_at_once(lambda: errors._report_crash(_hostile_error()))
    finally:
        _close_full(full)


def test_the_cli_report_never_raises(monkeypatch):
    def _broken():
        raise OSError("no runtime dir")

    monkeypatch.setattr(errors, "_report_socket_path", _broken)
    errors._report_crash(_hostile_error())


# ---- a relayed error names real code only ------------------------------------------

def _with_facts(**changes) -> dict:
    good = _good()
    return dict(good, facts=dict(good["facts"], **changes))


def _frame(fn: str, line: int = 3, file: str = "aipager/dtach/enforce.py") -> dict:
    return {"file": file, "line": line, "fn": fn}


@pytest.mark.parametrize("fn", ["decide", "fail_closed", "<lambda>", "<module>",
                                "decide.<locals>.<lambda>", "decide.<locals>.<genexpr>",
                                "decide.<locals>.<lambda>.<locals>.<genexpr>"])
def test_a_frame_naming_real_code_is_kept(fn):
    assert relay.read_error_datagram(_with_facts(frames=[_frame(fn)])) is not None


def test_an_anonymous_frame_in_a_class_body_is_kept():
    """3.11+ names a lambda or comprehension in a class body after the
    class, with no ``<locals>``."""
    frame = _frame("HookReceiver.<listcomp>", line=10, file="aipager/dtach/hook_receiver.py")
    assert relay.read_error_datagram(_with_facts(frames=[frame])) is not None
    forged = _frame("CnryBox.<listcomp>", line=10, file="aipager/dtach/hook_receiver.py")
    assert relay.read_error_datagram(_with_facts(frames=[forged])) is None


@pytest.mark.parametrize("frame", [
    _frame("cnryuserQX7"), _frame("decide2"), _frame("nope.<locals>.<lambda>"),
    _frame("decide.<locals>.helper"), _frame("decide", line=10**6),
    _frame("save_if_dirty", file="aipager/dtach/enforce.py")])
def test_a_frame_naming_code_that_is_not_there_drops_the_datagram(frame):
    assert relay.read_error_datagram(_with_facts(frames=[frame])) is None


def test_a_method_is_named_by_its_class():
    assert relay.read_error_datagram(_with_facts(frames=[_frame(
        "HookReceiver._on_datagram", line=10, file="aipager/dtach/hook_receiver.py")])) is not None
    assert relay.read_error_datagram(_with_facts(frames=[_frame(
        "HookReceiver.cnry", line=10, file="aipager/dtach/hook_receiver.py")])) is None


@pytest.mark.parametrize(("name", "real"), [
    ("builtins.ValueError", True), ("telegram.error.BadRequest", True), ("<other>", True),
    ("aipager._test_guard.RealHomeWriteError", True),
    ("builtins.cnryuser", False), ("builtins.object", False), ("telegram.error.Cnry777", False),
    ("aipager.dtach.enforce.decide", False), ("aipager.dtach.enforce.NotAClass", False),
    ("aipager.cnryuser.Error", False)])
def test_an_exception_type_must_be_a_real_class(name, real):
    assert (relay.read_error_datagram(_with_facts(type=name)) is not None) is real
    assert (relay.read_error_datagram(_with_facts(cause_types=[name])) is not None) is real


@pytest.mark.parametrize(("field", "value"), [("event", "<invalid>"), ("tool", "<invalid>")])
def test_the_invalid_sentinel_is_no_value(field, value):
    assert relay.read_error_datagram(dict(_good(), **{field: value})) is None
    sent = json.loads(relay.error_datagram(_hostile_error(), where="hook", event=value,
                                           tool=value))
    assert sent["event"] is None and sent["tool"] in (None, "other")


def test_the_event_names_are_the_events_aipager_hooks():
    from aipager import claude_bootstrap
    from aipager.report import schema
    assert set(schema.HOOK_EVENTS) == set(claude_bootstrap._HOOK_EVENTS)


def test_an_errno_must_be_a_real_errno_name():
    assert relay.read_error_datagram(_with_facts(errno="ENOSPC")) is not None
    assert relay.read_error_datagram(_with_facts(errno="ECNRYUSER")) is None


def test_a_cap_datagram_with_an_odd_hook_name_never_raises(receiver):
    for hook in ([], {"a": 1}, 5, None):
        _deliver(receiver, json.dumps({"type": "hook_memory_cap_hit", "session": "claude-cap-x",
                                       "hook": hook}).encode())
    assert store.errors() == [] and store.counters_24h() == {}


def _relayed(fn: str) -> dict:
    return relay.read_error_datagram(_with_facts(frames=[_frame(fn)]))


def test_new_relayed_fingerprints_are_capped_an_hour():
    names = ["decide", "fail_closed", "take_decide_error", "deny_decision_json", "read_snapshot"]
    names += sorted(relay._source("aipager/dtach/enforce.py")[0] - set(names))[:20]
    now = 1_791_500_000
    for i, fn in enumerate(names[:store.RELAY_NEW_PER_HOUR + 3]):
        store.record_relayed(_relayed(fn), now=now + i)
    assert len(store.errors(limit=50)) == store.RELAY_NEW_PER_HOUR
    # One already known still counts, and an hour later new ones are taken.
    store.record_relayed(_relayed(names[0]), now=now + 60)
    assert store.records()[-1]["entry"]["count"] == 2
    store.record_relayed(_relayed(names[store.RELAY_NEW_PER_HOUR + 5]), now=now + 3700)
    assert len(store.errors(limit=50)) == store.RELAY_NEW_PER_HOUR + 1


def test_a_nested_function_is_named_with_its_locals():
    real = _frame("install_excepthook.<locals>._hook", line=140, file="aipager/errors.py")
    flat = _frame("install_excepthook._hook", line=140, file="aipager/errors.py")
    assert relay.read_error_datagram(_with_facts(frames=[real])) is not None
    assert relay.read_error_datagram(_with_facts(frames=[flat])) is None
