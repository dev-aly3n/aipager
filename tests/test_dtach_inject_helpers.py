"""Extra coverage for aipager.dtach.inject — the helpers other than
``launch_session`` (already covered in test_dtach_inject_launch.py)."""

from __future__ import annotations

import asyncio
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

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append((pid, sig)))

    try:
        ok = run_async(inject.kill_session("claude-jim"))
        assert ok is True
        assert killed == [(12345, 15), (67890, 15)]  # SIGTERM=15
    finally:
        srv.close()


def test_kill_session_handles_fuser_failure(tmp_path, monkeypatch, run_async):
    sock_path = tmp_path / "claude-dtach-jim.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    monkeypatch.setattr(inject, "_sock_path", lambda s: str(sock_path))

    async def _fake_exec(*args, **kwargs):
        raise OSError("fuser binary missing")

    monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", _fake_exec)

    try:
        # Should still return True (socket file deletion is a separate path)
        ok = run_async(inject.kill_session("claude-jim"))
        assert ok is True
    finally:
        srv.close()


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
