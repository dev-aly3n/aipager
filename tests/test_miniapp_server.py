"""Tests for aipager.miniapp.server.MiniAppServer.

Route behavior is exercised via aiohttp's own test client (no real TCP
bind needed for those); a small dedicated test proves the *lifecycle*
binds a real loopback socket and that the host passed to
`web.TCPSite` is hardcoded to 127.0.0.1 — never 0.0.0.0 or a LAN IP.
"""

import hashlib
import hmac
import json
import logging
import socket
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer, MiniAppUnavailable
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    """The server reads the real bot token from aipager.config at
    request time — point it at this test module's signing key so
    _init_data()'s signatures verify against what the handler checks."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields: dict, bot_token: str) -> str:
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(fields.items()) if k != "hash"
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id: int, *, bot_token: str = BOT_TOKEN, auth_date=None) -> str:
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


@pytest.fixture
def scoped_server(mk_bot):
    """A MiniAppServer wired to a scope-mode bot with one member (id=555)
    in scope chat_id=-100, plus one BUSY session in that scope."""
    registry = SessionRegistry()
    scope = Scope(
        chat_id=-100, kind="group", label="team",
        members=(Member(id=555, label="ada", role="developer"),),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "aipager_test_bot"

    sess = registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = -100
    sess.status = Status.BUSY
    sess.model_name = "Opus 4.6"
    sess.last_token_pct = 42
    sess.last_cost_usd = 1.2345
    sess.last_hook_at = time.monotonic() - 5

    server = MiniAppServer(bot, registry, port=8765)
    return server


async def _client_for(server: MiniAppServer):
    app = server._build_app()
    test_server = TestServer(app)
    client = TestClient(test_server)
    await client.start_server()
    return client


def test_index_returns_200_html_no_auth(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            resp = await client.get("/")
            assert resp.status == 200
            assert "text/html" in resp.headers["Content-Type"]
            body = await resp.text()
            assert "<html" in body.lower()
        finally:
            await client.close()
    run_async(_run())


def test_status_missing_header_returns_401(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            resp = await client.get("/api/status")
            assert resp.status == 401
            body = await resp.json()
            assert "error" in body
        finally:
            await client.close()
    run_async(_run())


def test_status_wrong_token_signature_returns_401(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            bad = _init_data(555, bot_token="000000:wrong-token-signature-abc")
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": bad},
            )
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


def test_status_stale_auth_date_returns_401(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            stale = _init_data(555, auth_date=int(time.time()) - 3600)
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": stale},
            )
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


def test_status_unauthorized_user_returns_403(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            stranger = _init_data(999999)  # valid signature, not a scope member
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": stranger},
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_status_authorized_member_returns_scoped_sessions(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            good = _init_data(555)
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": good},
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["daemon"]["bot_username"] == "aipager_test_bot"
            assert isinstance(body["daemon"]["uptime_seconds"], int)
            assert len(body["sessions"]) == 1
            sess = body["sessions"][0]
            assert sess["label"] == "dev"
            assert sess["status"] == "busy"
            assert sess["model"] == "Opus 4.6"
            assert sess["context_pct"] == 42
            assert sess["cost_usd"] == pytest.approx(1.2345)
            assert sess["last_active_seconds_ago"] is not None
        finally:
            await client.close()
    run_async(_run())


def test_status_scopes_sessions_to_authenticated_users_scope(mk_bot, run_async):
    """A member of scope A must never see scope B's sessions."""
    registry = SessionRegistry()
    scope_a = Scope(chat_id=-1, kind="group", label="a",
                    members=(Member(id=1, label="alice", role="developer"),))
    scope_b = Scope(chat_id=-2, kind="group", label="b",
                    members=(Member(id=2, label="bob", role="developer"),))
    bot = mk_bot(registry, scopes=[scope_a, scope_b])
    bot._app.bot.username = "bot"

    sess_a = registry.get_or_create("claude-a")
    sess_a.label = "a-session"
    sess_a.scope_chat_id = -1
    sess_b = registry.get_or_create("claude-b")
    sess_b.label = "b-session"
    sess_b.scope_chat_id = -2

    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            alice = _init_data(1)
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": alice},
            )
            body = await resp.json()
            labels = {s["label"] for s in body["sessions"]}
            assert labels == {"a-session"}
        finally:
            await client.close()
    run_async(_run())


def test_no_route_accepts_post(scoped_server, run_async):
    """Stage 1 is strictly read-only — no route registers POST/PUT/DELETE."""
    async def _run():
        client = await _client_for(scoped_server)
        try:
            resp = await client.post("/api/status")
            assert resp.status in (404, 405)
        finally:
            await client.close()
    run_async(_run())


def test_start_raises_unavailable_when_aiohttp_missing(mk_bot, run_async, monkeypatch):
    """Simulate the extra not being installed — must raise
    MiniAppUnavailable with install instructions, not crash the daemon."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "aiohttp" or name.startswith("aiohttp."):
            raise ImportError("simulated: aiohttp not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    registry = SessionRegistry()
    bot = mk_bot(registry)
    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        with pytest.raises(MiniAppUnavailable) as exc_info:
            await server.start()
        assert "aipager[miniapp]" in str(exc_info.value)
    run_async(_run())


def test_binds_loopback_host_only(mk_bot, run_async, monkeypatch):
    """The host passed to aiohttp's TCPSite must always be 127.0.0.1 —
    never 0.0.0.0 or any other interface."""
    import aiohttp.web as web

    seen_hosts = []
    real_tcp_site = web.TCPSite

    class _SpyTCPSite(real_tcp_site):
        def __init__(self, runner, host=None, port=None, **kwargs):
            seen_hosts.append(host)
            super().__init__(runner, host, port, **kwargs)

    monkeypatch.setattr(web, "TCPSite", _SpyTCPSite)

    registry = SessionRegistry()
    bot = mk_bot(registry)
    server = MiniAppServer(bot, registry, port=0)  # port=0 → OS picks an ephemeral port

    async def _run():
        await server.start()
        try:
            assert seen_hosts == ["127.0.0.1"]
            assert "0.0.0.0" not in seen_hosts
        finally:
            await server.stop()
    run_async(_run())


def test_real_bind_accepts_loopback_connection(mk_bot, run_async):
    """End-to-end lifecycle: start() really binds 127.0.0.1, a real
    socket connect to that port succeeds, stop() releases it."""
    registry = SessionRegistry()
    bot = mk_bot(registry)
    server = MiniAppServer(bot, registry, port=0)

    async def _run():
        await server.start()
        try:
            actual_port = (
                server._runner.addresses[0][1]
                if server._runner.addresses else None
            )
            assert actual_port is not None
            with socket.create_connection(("127.0.0.1", actual_port), timeout=2) as s:
                assert s.getpeername()[0] == "127.0.0.1"
        finally:
            await server.stop()
    run_async(_run())


def test_no_log_line_contains_init_data_or_token(scoped_server, run_async, caplog):
    """Grep every emitted log record across a full rejected + accepted
    request cycle — the raw initData string and the bot token must never
    appear, at any level (design.md non-negotiable)."""
    async def _run():
        client = await _client_for(scoped_server)
        try:
            with caplog.at_level(logging.DEBUG):
                await client.get("/api/status")  # missing header (401)
                bad = _init_data(555, bot_token="wrong:token-value-xyz")
                await client.get(
                    "/api/status", headers={"X-Telegram-Init-Data": bad},
                )
                good = _init_data(555)
                await client.get(
                    "/api/status", headers={"X-Telegram-Init-Data": good},
                )
                await client.get("/")
        finally:
            await client.close()

        for record in caplog.records:
            message = record.getMessage()
            assert BOT_TOKEN not in message
            assert good not in message
            assert bad not in message
    run_async(_run())
