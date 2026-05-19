"""Tests for aipager.cli.daemon — daemon-startup helpers.

Covers ``_check_existing_daemon`` (socket existence + liveness probe)
and ``_telegram_preflight`` (token + chat-id verification). The actual
``_run_daemon`` is not tested here — it boots a real asyncio event
loop and is tested via the end-to-end smoke check.
"""

from __future__ import annotations

import json
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
