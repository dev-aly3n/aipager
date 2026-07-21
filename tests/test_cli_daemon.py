"""Tests for aipager.cli.daemon — daemon-startup helpers.

Covers ``_check_existing_daemon`` (socket existence + liveness probe)
and ``_telegram_preflight`` (token + chat-id verification). The actual
``_run_daemon`` is not tested here — it boots a real asyncio event
loop and is tested via the end-to-end smoke check.
"""

from __future__ import annotations

import fcntl
import json
import os
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock

import pytest

from aipager.cli import daemon


# ---- _check_existing_daemon ---------------------------------------------

def test_check_existing_daemon_no_socket_returns(monkeypatch):
    monkeypatch.setattr("aipager.cli.daemon.Path.exists", lambda self: False)
    # Should return None without raising
    daemon._check_existing_daemon()


def test_check_existing_daemon_stale_socket_returns(monkeypatch):
    """ConnectionRefusedError on sendto → stale socket, return cleanly."""
    monkeypatch.setattr("aipager.cli.daemon.Path.exists", lambda self: True)

    fake_sock = MagicMock()
    fake_sock.sendto.side_effect = ConnectionRefusedError("stale")
    monkeypatch.setattr("aipager.cli.daemon.socket.socket", lambda *a, **k: fake_sock)

    # Should return without raising or exiting
    daemon._check_existing_daemon()
    fake_sock.close.assert_called_once()


def test_check_existing_daemon_other_oserror_warns(monkeypatch, capsys):
    monkeypatch.setattr("aipager.cli.daemon.Path.exists", lambda self: True)
    fake_sock = MagicMock()
    fake_sock.sendto.side_effect = OSError("EPERM")
    monkeypatch.setattr("aipager.cli.daemon.socket.socket", lambda *a, **k: fake_sock)
    # Should warn but not exit
    daemon._check_existing_daemon()


def test_check_existing_daemon_live_responder_exits_1(monkeypatch):
    """A daemon answering on the socket → friendly error + sys.exit(1)."""
    monkeypatch.setattr("aipager.cli.daemon.Path.exists", lambda self: True)
    fake_sock = MagicMock()
    fake_sock.sendto.return_value = None  # success
    monkeypatch.setattr("aipager.cli.daemon.socket.socket", lambda *a, **k: fake_sock)

    with pytest.raises(SystemExit) as exc:
        daemon._check_existing_daemon()
    assert exc.value.code == 1


# ---- _telegram_preflight -------------------------------------------------

def _stub_urlopen(monkeypatch, responses):
    """Make urlopen return successive responses (json dicts) for each call."""
    calls = iter(responses)

    class _FakeResponse:
        def __init__(self, body):
            self._body = json.dumps(body).encode()
            self.status = 200

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def _fake_urlopen(url, timeout=15):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return _FakeResponse(r)

    monkeypatch.setattr("aipager.cli.daemon.urllib.request.urlopen", _fake_urlopen)
    # The _call wrapper uses json.load on the response; provide a body
    # that survives a json.load call.
    def _fake_json_load(fp):
        return json.loads(fp.read())
    monkeypatch.setattr("aipager.cli.daemon.json.load", _fake_json_load)


def test_telegram_preflight_happy_path(monkeypatch):
    _stub_urlopen(monkeypatch, [
        {"ok": True, "result": {"username": "bot_username"}},  # getMe
        {"ok": True, "result": {"id": 12345}},  # getChat
    ])
    username = daemon._telegram_preflight()
    assert username == "bot_username"


def test_telegram_preflight_invalid_token_exits_2(monkeypatch):
    """HTTP 401 from getMe → exit 2."""
    err = urllib.error.HTTPError(
        url="https://api.telegram.org",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=BytesIO(b'{"description": "Unauthorized"}'),
    )
    _stub_urlopen(monkeypatch, [err])
    with pytest.raises(SystemExit) as exc:
        daemon._telegram_preflight()
    assert exc.value.code == 2


def test_telegram_preflight_server_error_exits_1(monkeypatch):
    err = urllib.error.HTTPError(
        url="https://api.telegram.org",
        code=503,
        msg="Bad Gateway",
        hdrs=None,
        fp=BytesIO(b'{"description": "Bad Gateway"}'),
    )
    _stub_urlopen(monkeypatch, [err])
    with pytest.raises(SystemExit) as exc:
        daemon._telegram_preflight()
    assert exc.value.code == 1


def test_telegram_preflight_network_error_exits_1(monkeypatch):
    err = urllib.error.URLError("DNS failure")
    _stub_urlopen(monkeypatch, [err])
    with pytest.raises(SystemExit) as exc:
        daemon._telegram_preflight()
    assert exc.value.code == 1


def test_telegram_preflight_chat_not_found_exits_2(monkeypatch):
    """getChat returns 'chat not found' → exit 2 with DM instructions."""
    chat_err = urllib.error.HTTPError(
        url="https://api.telegram.org",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=BytesIO(b'{"description": "Bad Request: chat not found"}'),
    )
    _stub_urlopen(monkeypatch, [
        {"ok": True, "result": {"username": "bot_username"}},  # getMe ok
        chat_err,  # getChat fails
    ])
    with pytest.raises(SystemExit) as exc:
        daemon._telegram_preflight()
    assert exc.value.code == 2


def test_telegram_preflight_chat_other_failure_warns_but_returns(monkeypatch, caplog):
    """A transient getChat failure (not 'chat not found') is non-fatal."""
    chat_err = urllib.error.HTTPError(
        url="https://api.telegram.org",
        code=500,
        msg="Server Error",
        hdrs=None,
        fp=BytesIO(b'{"description": "Internal Server Error"}'),
    )
    _stub_urlopen(monkeypatch, [
        {"ok": True, "result": {"username": "bot_username"}},
        chat_err,
    ])
    # Should NOT exit — getChat is best-effort
    username = daemon._telegram_preflight()
    assert username == "bot_username"


# ---- _acquire_daemon_lock -----------------------------------------------


def _patch_home(monkeypatch, tmp_path):
    """Route Path.home() to tmp_path so lockfile lands in the sandbox."""
    monkeypatch.setattr("aipager.cli.daemon.Path.home",
                        classmethod(lambda cls: tmp_path))


def _release_lock():
    """Reset the module-level lock fd (used between tests)."""
    fd = daemon._daemon_lock_fd
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    daemon._daemon_lock_fd = None


def test_acquire_daemon_lock_succeeds_on_first_call(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    try:
        daemon._acquire_daemon_lock()
        lock_path = tmp_path / ".local" / "share" / "aipager" / "daemon.lock"
        assert lock_path.exists()
        # PID recorded in the file
        assert lock_path.read_text().strip() == str(os.getpid())
        # Module-level fd is set (so lock won't get GC'd)
        assert daemon._daemon_lock_fd is not None
    finally:
        _release_lock()


def test_acquire_daemon_lock_second_call_exits(tmp_path, monkeypatch):
    """A second acquisition attempt while another holder has the lock
    must call sys.exit(1) — the whole point of the guard."""
    _patch_home(monkeypatch, tmp_path)
    # First holder: open the file directly + flock it (simulates a
    # separately-running daemon holding the lock).
    lock_path = tmp_path / ".local" / "share" / "aipager" / "daemon.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    other_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(other_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.write(other_fd, b"99999\n")
    try:
        with pytest.raises(SystemExit) as exc:
            daemon._acquire_daemon_lock()
        assert exc.value.code == 1
        # Module-level fd should NOT have been set (we failed)
        assert daemon._daemon_lock_fd is None
    finally:
        try:
            os.close(other_fd)
        except OSError:
            pass
        _release_lock()


def test_acquire_daemon_lock_after_release_succeeds(tmp_path, monkeypatch):
    """After the previous holder releases (via close), a new acquire
    succeeds — proves the lock is process-associated, not permanent."""
    _patch_home(monkeypatch, tmp_path)
    try:
        daemon._acquire_daemon_lock()
        # Release: close the fd (simulates prior daemon exit) and
        # clear the module-level ref so a fresh call can succeed.
        _release_lock()
        # Second acquire should succeed cleanly (no SystemExit).
        daemon._acquire_daemon_lock()
        assert daemon._daemon_lock_fd is not None
    finally:
        _release_lock()


def test_acquire_daemon_lock_creates_missing_parent_dir(tmp_path, monkeypatch):
    """Fresh install: ~/.local/share/aipager doesn't exist yet.
    Helper must create it, not crash."""
    _patch_home(monkeypatch, tmp_path)
    parent = tmp_path / ".local" / "share" / "aipager"
    assert not parent.exists()
    try:
        daemon._acquire_daemon_lock()
        assert parent.is_dir()
        assert (parent / "daemon.lock").exists()
    finally:
        _release_lock()


def test_acquire_daemon_lock_pid_written_correctly(tmp_path, monkeypatch):
    """Re-verifies PID contents are exactly current pid — humans read
    this to identify the running daemon in the friendly error."""
    _patch_home(monkeypatch, tmp_path)
    try:
        daemon._acquire_daemon_lock()
        lock_path = tmp_path / ".local" / "share" / "aipager" / "daemon.lock"
        assert int(lock_path.read_text().strip()) == os.getpid()
    finally:
        _release_lock()
