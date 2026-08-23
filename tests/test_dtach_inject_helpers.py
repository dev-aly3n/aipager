"""Extra coverage for aipager.dtach.inject — the helpers other than
``launch_session`` (already covered in test_dtach_inject_launch.py)."""

from __future__ import annotations

import asyncio
import signal as _signal
import socket as _socket

import pytest

from aipager.dtach import inject


# ---- _resolve_dtach ------------------------------------------------------

def test_resolve_dtach_prefers_dtach_bin(monkeypatch):
    fake_path = "/opt/dtach-bin/dtach"

    class _FakeDtachBin:
        @staticmethod
        def path():
            return fake_path

    import sys
    monkeypatch.setitem(sys.modules, "dtach_bin", _FakeDtachBin)
    assert inject._resolve_dtach() == fake_path


def test_resolve_dtach_falls_back_to_path(monkeypatch):
    import sys
    # Force the dtach_bin import to fail
    monkeypatch.setitem(sys.modules, "dtach_bin", None)
    monkeypatch.setattr(inject.shutil, "which", lambda name: "/usr/bin/dtach")
    assert inject._resolve_dtach() == "/usr/bin/dtach"


def test_resolve_dtach_returns_dtach_literal_when_no_install(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "dtach_bin", None)
    monkeypatch.setattr(inject.shutil, "which", lambda name: None)
    assert inject._resolve_dtach() == "dtach"


# ---- _sock_path ----------------------------------------------------------

@pytest.mark.parametrize("session,expected", [
    ("claude-dev", "/tmp/claude-dtach-dev.sock"),
    ("dev", "/tmp/claude-dtach-dev.sock"),
    ("claude-claude-funny", "/tmp/claude-dtach-claude-funny.sock"),
    ("a", "/tmp/claude-dtach-a.sock"),
])
def test_sock_path(session, expected):
    assert inject._sock_path(session) == expected


# ---- _run ---------------------------------------------------------------

def test_run_success(monkeypatch, run_async):
    async def _fake_exec(*args, **kwargs):
        from unittest.mock import AsyncMock
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"hello", b""))
        return proc

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)
    ok, out = run_async(inject._run(["echo"]))
    assert ok is True
    assert out == "hello"


def test_run_nonzero_exit_returns_false(monkeypatch, run_async):
    async def _fake_exec(*args, **kwargs):
        from unittest.mock import AsyncMock
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"err"))
        return proc

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)
    ok, out = run_async(inject._run(["false"]))
    assert ok is False
    assert out == ""


def test_run_timeout(monkeypatch, run_async):
    async def _fake_exec(*args, **kwargs):
        from unittest.mock import AsyncMock
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        return proc

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)
    ok, out = run_async(inject._run(["sleep", "999"], timeout=0.01))
    assert ok is False


def test_run_file_not_found(monkeypatch, run_async):
    async def _fake_exec(*args, **kwargs):
        raise FileNotFoundError("dtach gone")

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)
    ok, out = run_async(inject._run(["nope"]))
    assert ok is False


# ---- send_keys ----------------------------------------------------------

def test_send_keys_translates_logical_names(monkeypatch, run_async):
    captured = {}

    async def _fake_run(args, stdin=b"", timeout=5):
        captured["stdin"] = stdin
        captured["args"] = args
        return True, ""

    monkeypatch.setattr(inject, "_run", _fake_run)
    assert run_async(inject.send_keys("claude-jim", "Enter")) is True
    assert captured["stdin"] == b"\r"
    assert "-p" in captured["args"]


def test_send_keys_passes_raw_text(monkeypatch, run_async):
    captured = {}

    async def _fake_run(args, stdin=b"", timeout=5):
        captured["stdin"] = stdin
        return True, ""

    monkeypatch.setattr(inject, "_run", _fake_run)
    run_async(inject.send_keys("claude-jim", "hello"))
    assert captured["stdin"] == b"hello"


def test_send_keys_returns_false_on_dtach_failure(monkeypatch, run_async):
    async def _fake_run(*a, **kw):
        return False, ""

    monkeypatch.setattr(inject, "_run", _fake_run)
    assert run_async(inject.send_keys("claude-jim", "x")) is False


# ---- send_text_and_enter -------------------------------------------------

def test_send_text_and_enter_sends_text_then_cr(monkeypatch, run_async):
    sent = []

    async def _fake_run(args, stdin=b"", timeout=5):
        sent.append(stdin)
        return True, ""

    monkeypatch.setattr(inject, "_run", _fake_run)
    # Skip the sleep
    async def _no_sleep(_):
        pass
    monkeypatch.setattr(inject.asyncio, "sleep", _no_sleep)

    assert run_async(inject.send_text_and_enter("claude-jim", "hi")) is True
    assert sent == [b"hi", b"\r"]


def test_send_text_and_enter_aborts_on_text_failure(monkeypatch, run_async):
    calls = []

    async def _fake_run(args, stdin=b"", timeout=5):
        calls.append(stdin)
        return False, ""  # first call fails

    monkeypatch.setattr(inject, "_run", _fake_run)
    assert run_async(inject.send_text_and_enter("claude-jim", "hi")) is False
    # Should NOT have tried to send the trailing \r
    assert calls == [b"hi"]


# ---- is_alive -----------------------------------------------------------

def test_is_alive_true_for_real_socket(tmp_path, run_async):
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    try:
        # is_alive uses /tmp/claude-dtach-<name>.sock — we need to redirect
        # via SOCK_PREFIX. Easier: just monkeypatch _sock_path.
        # Simpler: pretend the socket lives where _sock_path says it does.
        # Use a session name and create the expected file at /tmp.
        # NOTE: actually is_alive uses Path(...).is_socket() which we can
        # verify by stubbing _sock_path.
        from unittest.mock import patch
        with patch.object(inject, "_sock_path", return_value=str(sock_path)):
            assert run_async(inject.is_alive("claude-jim")) is True
    finally:
        srv.close()


def test_is_alive_false_when_missing(run_async, monkeypatch):
    monkeypatch.setattr(inject, "_sock_path",
                        lambda s: "/tmp/aipager-test-nope.sock")
    assert run_async(inject.is_alive("claude-x")) is False


# ---- kill_session -------------------------------------------------------

def test_kill_session_returns_false_when_no_socket(monkeypatch, run_async):
    monkeypatch.setattr(inject, "_sock_path",
                        lambda s: "/tmp/aipager-test-nope.sock")
    assert run_async(inject.kill_session("claude-x")) is False


def test_kill_session_sigterms_fuser_pids(tmp_path, monkeypatch, run_async):
    sock_path = tmp_path / "claude-dtach-jim.sock"
    # Make a real Unix socket so is_socket() returns True
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))

    killed = []

    async def _fake_exec(*args, **kwargs):
        from unittest.mock import AsyncMock
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"12345 67890\n", b""))
        return proc

    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [])
    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)

    # Model a process that actually dies: SIGTERM is recorded, and the
    # liveness probe (signal 0) reports it gone from then on. kill_session
    # waits for exactly that before it returns.
    dead = set()

    def _fake_kill(pid, sig):
        if sig == 0:
            if pid in dead:
                raise ProcessLookupError(pid)
            return
        killed.append((pid, sig))
        dead.add(pid)

    monkeypatch.setattr("os.kill", _fake_kill)

    try:
        ok = run_async(inject.kill_session("claude-jim"))
        assert ok is True
        assert killed == [(12345, 15), (67890, 15)]  # SIGTERM=15
    finally:
        srv.close()


def test_kill_session_fails_and_keeps_socket_when_fuser_missing(
    tmp_path, monkeypatch, run_async,
):
    """No PID found → False, and the socket must survive.

    The socket is aipager's only handle on the session: unlinking it
    after a failed kill strands a live claude that the monitor then
    reports as gone.
    """
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))
    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [])

    async def _fake_exec(*args, **kwargs):
        raise OSError("fuser binary missing")

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)

    try:
        assert run_async(inject.kill_session("claude-jim")) is False
        assert sock_path.is_socket()
    finally:
        srv.close()


def test_kill_session_fails_when_no_pids_found(tmp_path, monkeypatch, run_async):
    """fuser succeeding but returning nothing is still a failed kill."""
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))
    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [])

    async def _fake_exec(*args, **kwargs):
        from unittest.mock import AsyncMock
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"\n", b""))
        return proc

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)

    try:
        assert run_async(inject.kill_session("claude-jim")) is False
        assert sock_path.is_socket()
    finally:
        srv.close()


def test_kill_session_fails_when_sigterm_raises(tmp_path, monkeypatch, run_async):
    """A PID that cannot be signalled (already gone) is not a kill."""
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))
    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [12345])

    def _boom(pid, sig):
        raise ProcessLookupError("no such process")

    monkeypatch.setattr("os.kill", _boom)

    try:
        assert run_async(inject.kill_session("claude-jim")) is False
        assert sock_path.is_socket()
    finally:
        srv.close()


def test_kill_session_uses_proc_scan_without_fuser(tmp_path, monkeypatch, run_async):
    """The /proc scan alone suffices — fuser is never consulted."""
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))
    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [4242])

    killed = []
    dead = set()

    def _fake_kill(pid, sig):
        if sig == 0:
            if pid in dead:
                raise ProcessLookupError(pid)
            return
        killed.append((pid, sig))
        dead.add(pid)

    monkeypatch.setattr("os.kill", _fake_kill)

    async def _fail(*args, **kwargs):
        raise AssertionError("fuser must not run when /proc found a PID")

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fail)

    try:
        assert run_async(inject.kill_session("claude-jim")) is True
        assert killed == [(4242, 15)]  # SIGTERM
        assert not sock_path.exists()
    finally:
        srv.close()


def test_proc_socket_pids_ignores_non_dtach_processes(tmp_path, monkeypatch):
    """A process merely mentioning the socket path must not be signalled."""
    sock = "/tmp/claude-dtach-jim.sock"
    proc_dir = tmp_path / "proc"
    for pid, argv in {
        "10": [b"/usr/local/bin/dtach", b"-n", sock.encode(), b"-Ez"],
        "11": [b"/bin/grep", sock.encode(), b"/var/log/syslog"],
        "12": [b"/usr/bin/claude", b"--resume", b"abc"],
    }.items():
        d = proc_dir / pid
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"\0".join(argv) + b"\0")
    (proc_dir / "self").mkdir()  # non-numeric entry must be skipped

    real_path = inject.Path
    monkeypatch.setattr(
        inject, "Path",
        lambda arg, *a, **kw: real_path(proc_dir) if arg == "/proc"
        else real_path(arg, *a, **kw),
    )
    assert inject._proc_socket_pids(sock) == [10]


# ---- list_sessions ------------------------------------------------------

def test_list_sessions_finds_socket_files(tmp_path, monkeypatch, run_async):
    # Bind a real socket so is_socket() is True
    sock = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock))
    # Also create a regular file with the same naming pattern (should be ignored)
    (tmp_path / "claude-dtach-not-a-socket.sock").touch()

    # Patch glob target. Capture the real glob first to avoid recursion
    # when our replacement calls it on tmp_path.
    _real_glob = inject.Path.glob
    monkeypatch.setattr(inject.Path, "glob",
                        lambda self, pat: list(_real_glob(tmp_path, pat)))
    try:
        result = run_async(inject.list_sessions())
        assert "claude-jim" in result
        # The regular file is filtered out
        assert "claude-not-a-socket" not in result
    finally:
        srv.close()


# ---- kill_session waits for the process to actually exit -----------------

def test_kill_session_waits_before_unlinking(tmp_path, monkeypatch, run_async):
    """Live regression (2026-08-15): kill_session unlinked and returned while
    the process was still dying. `/perms` relaunched into the same socket
    path microseconds later, and the corpse removed the NEW session's socket
    on its way out — the restarted session vanished two seconds after being
    reported alive."""
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))
    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [4242])
    monkeypatch.setattr(inject, "_KILL_POLL_INTERVAL", 0.001)

    # Stays alive for the first few liveness probes, then exits.
    probes = {"n": 0}
    unlinked_while_alive = []

    def _fake_kill(pid, sig):
        if sig == 0:
            probes["n"] += 1
            if probes["n"] > 3:
                raise ProcessLookupError(pid)
            # Still running — the socket must NOT have been removed yet.
            unlinked_while_alive.append(not sock_path.exists())
            return
        return

    monkeypatch.setattr("os.kill", _fake_kill)

    try:
        assert run_async(inject.kill_session("claude-jim")) is True
        assert probes["n"] > 3, "did not wait for the process to exit"
        assert not any(unlinked_while_alive), (
            "socket was unlinked while the process was still alive"
        )
        assert not sock_path.exists()
    finally:
        srv.close()


def test_kill_session_escalates_to_sigkill(tmp_path, monkeypatch, run_async):
    """A process ignoring SIGTERM must not hang the caller forever."""
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))
    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [4242])
    monkeypatch.setattr(inject, "_KILL_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(inject, "_KILL_TIMEOUT", 0.01)

    sent = []
    dead = {"yes": False}

    def _fake_kill(pid, sig):
        if sig == 0:
            if dead["yes"]:
                raise ProcessLookupError(pid)
            return
        sent.append(sig)
        if sig == _signal.SIGKILL:
            dead["yes"] = True   # SIGKILL cannot be ignored

    monkeypatch.setattr("os.kill", _fake_kill)

    try:
        assert run_async(inject.kill_session("claude-jim")) is True
        assert _signal.SIGTERM in sent and _signal.SIGKILL in sent
    finally:
        srv.close()


def test_kill_session_gives_up_on_an_unkillable_process(
    tmp_path, monkeypatch, run_async,
):
    """Never hang: a pid that survives SIGKILL still returns."""
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))
    monkeypatch.setattr(inject, "_proc_socket_pids", lambda sock: [4242])
    monkeypatch.setattr(inject, "_KILL_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(inject, "_KILL_TIMEOUT", 0.01)
    monkeypatch.setattr("os.kill", lambda pid, sig: None)  # never dies

    try:
        assert run_async(inject.kill_session("claude-jim")) is True
    finally:
        srv.close()


# ---- resume only when there is a conversation to resume ------------------

def test_conversation_exists_finds_a_transcript(tmp_path, monkeypatch):
    projects = tmp_path / ".claude" / "projects" / "-home-aly"
    projects.mkdir(parents=True)
    (projects / "abc-123.jsonl").write_text("{}\n")
    monkeypatch.setattr(inject.Path, "home", staticmethod(lambda: tmp_path))
    assert inject._conversation_exists("abc-123") is True


def test_conversation_exists_false_for_an_unknown_id(tmp_path, monkeypatch):
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setattr(inject.Path, "home", staticmethod(lambda: tmp_path))
    assert inject._conversation_exists("nope-999") is False
    assert inject._conversation_exists("") is False


def test_launch_drops_resume_when_the_conversation_is_missing(
    tmp_path, monkeypatch, run_async,
):
    """Live regression (2026-08-15): `/perms` on a freshly-created session
    relaunched it with `--resume <id>`, but a session that has taken no turns
    has no conversation on disk. Claude Code exits 1 with "No conversation
    found with session ID", so switching mode killed the session outright."""
    captured = {}

    async def _fake_exec(*args, **kwargs):
        from unittest.mock import AsyncMock
        captured["argv"] = args
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    monkeypatch.setattr(inject, "_conversation_exists", lambda sid: False)
    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(tmp_path))
    run_async(inject.launch_session("jim", resume_id="ghost-id",
                                    cwd=str(tmp_path)))
    joined = " ".join(str(a) for a in captured.get("argv", ()))
    assert "--resume" not in joined, "resumed a conversation that does not exist"


def test_launch_keeps_resume_when_the_conversation_exists(
    tmp_path, monkeypatch, run_async,
):
    captured = {}

    async def _fake_exec(*args, **kwargs):
        from unittest.mock import AsyncMock
        captured["argv"] = args
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    monkeypatch.setattr(inject, "_conversation_exists", lambda sid: True)
    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(tmp_path))
    run_async(inject.launch_session("jim", resume_id="real-id",
                                    cwd=str(tmp_path)))
    joined = " ".join(str(a) for a in captured.get("argv", ()))
    assert "--resume" in joined and "real-id" in joined


# ---- discard_queued_input / KEYS["KillLine"] (design.md "queue handoff") --

def test_kill_line_key_is_ctrl_u():
    assert inject.KEYS["KillLine"] == "\x15"


def test_discard_queued_input_sends_escape_then_kill_line(monkeypatch, run_async):
    keys = []

    async def _fake_send_keys(session, k):
        keys.append(k)
        return True

    monkeypatch.setattr(inject, "send_keys", _fake_send_keys)
    assert run_async(inject.discard_queued_input("claude-jim")) is True
    assert keys == ["Escape", "KillLine"]


def test_discard_queued_input_returns_false_if_kill_line_send_fails(monkeypatch, run_async):
    async def _fake_send_keys(session, k):
        return k != "KillLine"

    monkeypatch.setattr(inject, "send_keys", _fake_send_keys)
    assert run_async(inject.discard_queued_input("claude-jim")) is False


def test_discard_queued_input_sends_to_the_named_session(monkeypatch, run_async):
    seen_sessions = []

    async def _fake_send_keys(session, k):
        seen_sessions.append(session)
        return True

    monkeypatch.setattr(inject, "send_keys", _fake_send_keys)
    run_async(inject.discard_queued_input("claude-target"))
    assert seen_sessions == ["claude-target", "claude-target"]
