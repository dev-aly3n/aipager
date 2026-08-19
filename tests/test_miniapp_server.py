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
    sess.cwd = "/home/dev/myproject"
    sess.busy_started_at = time.monotonic() - 12
    sess.tool_history = [("Read foo.py", True), ("Bash: pytest", False)]
    sess.stream_commentary = [(0, "Let me check that file")]

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


# ===== Personal mode (rev-iter1-003 / orchestrator F1) ==================
#
# Every other test in this file uses scope mode (mk_bot(..., scopes=[...])).
# Personal mode (team=None, scopes=None) has no allow-list at all, so
# unlike scope/team mode a valid signature alone would otherwise be
# treated as "the operator" -- which is exactly the widened blast radius
# the review flagged: a stranger who gets a signed initData (e.g. via
# /app, see tests/test_bot_app_command.py's matching guard) would get
# the operator's dashboard. MiniAppServer._resolve_scope_chat_id must
# additionally require user_id == the operator's own CHAT_ID.

def test_status_personal_mode_operator_returns_200(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    registry = SessionRegistry()
    bot = mk_bot(registry)  # team=None, scopes=None -> personal mode
    bot._app.bot.username = "solo_bot"
    sess = registry.get_or_create("claude-solo")
    sess.label = "solo"
    sess.scope_chat_id = 555
    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            good = _init_data(555)  # the operator
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": good},
            )
            assert resp.status == 200
            body = await resp.json()
            assert {s["label"] for s in body["sessions"]} == {"solo"}
        finally:
            await client.close()
    run_async(_run())


def test_status_personal_mode_non_operator_returns_403(mk_bot, run_async, monkeypatch):
    """The defect this guards against: a stranger with a validly-signed
    initData for their OWN Telegram user id must not receive the
    operator's session list just because personal mode has no
    allow-list to check membership against. Fails if the guard in
    MiniAppServer._resolve_scope_chat_id is removed (verified by hand:
    removing it makes this assert 403 == 200 and fail)."""
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    registry = SessionRegistry()
    bot = mk_bot(registry)
    bot._app.bot.username = "solo_bot"
    sess = registry.get_or_create("claude-solo")
    sess.label = "solo"
    sess.scope_chat_id = 555
    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            stranger = _init_data(999999)  # valid signature, not the operator
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": stranger},
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_status_personal_mode_unconfigured_chat_id_fails_closed(
    mk_bot, run_async, monkeypatch,
):
    """No operator identity resolvable (e.g. a fresh/malformed install)
    must deny, never fall open to 'any signed request is the operator'."""
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    registry = SessionRegistry()
    bot = mk_bot(registry)
    bot._app.bot.username = "solo_bot"
    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            any_user = _init_data(555)
            resp = await client.get(
                "/api/status", headers={"X-Telegram-Init-Data": any_user},
            )
            assert resp.status == 403
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


# ===== Stage 2: /api/sessions (grid) =====================================

def test_sessions_returns_scoped_grid_with_project_and_no_cwd(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            good = _init_data(555)
            resp = await client.get(
                "/api/sessions", headers={"X-Telegram-Init-Data": good},
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["daemon"]["bot_username"] == "aipager_test_bot"
            assert len(body["sessions"]) == 1
            row = body["sessions"][0]
            assert row["label"] == "dev"
            assert row["status"] == "busy"
            assert row["waiting_kind"] is None
            assert row["project"] == "myproject"
            assert "cwd" not in row
            assert "waiting_summary" not in row
        finally:
            await client.close()
    run_async(_run())


def test_sessions_never_invokes_git(scoped_server, run_async, monkeypatch):
    """The polled grid endpoint must stay fast regardless of pending
    diffs — it must never spawn git at all. Monkeypatching
    create_subprocess_exec to raise proves it: the request must still
    succeed 200."""
    async def _boom(*args, **kwargs):
        raise AssertionError("GET /api/sessions must never invoke git")
    monkeypatch.setattr("asyncio.create_subprocess_exec", _boom)

    async def _run():
        client = await _client_for(scoped_server)
        try:
            good = _init_data(555)
            resp = await client.get(
                "/api/sessions", headers={"X-Telegram-Init-Data": good},
            )
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_sessions_hides_gone_and_hidden_from_status(mk_bot, run_async):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=-100, kind="group", label="team",
        members=(Member(id=555, label="ada", role="developer"),),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "aipager_test_bot"

    live = registry.get_or_create("claude-live")
    live.label = "live"
    live.scope_chat_id = -100
    live.status = Status.IDLE

    hidden_gone = registry.get_or_create("claude-gone")
    hidden_gone.label = "gone-hidden"
    hidden_gone.scope_chat_id = -100
    hidden_gone.status = Status.GONE
    hidden_gone.hidden_from_status = True

    visible_gone = registry.get_or_create("claude-gone2")
    visible_gone.label = "gone-visible"
    visible_gone.scope_chat_id = -100
    visible_gone.status = Status.GONE

    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            good = _init_data(555)
            resp = await client.get(
                "/api/sessions", headers={"X-Telegram-Init-Data": good},
            )
            body = await resp.json()
            labels = {s["label"] for s in body["sessions"]}
            assert labels == {"live", "gone-visible"}
        finally:
            await client.close()
    run_async(_run())


# ===== Stage 2: /api/sessions/{label} (drill-down) ========================

def test_session_detail_returns_full_payload_for_owned_session(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            good = _init_data(555)
            resp = await client.get(
                "/api/sessions/dev", headers={"X-Telegram-Init-Data": good},
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["label"] == "dev"
            assert body["status"] == "busy"
            assert body["cwd"] == "/home/dev/myproject"
            assert body["busy_elapsed_seconds"] is not None
            # timeline is complete: 2 tool rows + 1 commentary row, nothing capped
            assert len(body["timeline"]) == 3
        finally:
            await client.close()
    run_async(_run())


def test_session_detail_unknown_label_returns_404(scoped_server, run_async):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            good = _init_data(555)
            resp = await client.get(
                "/api/sessions/does-not-exist",
                headers={"X-Telegram-Init-Data": good},
            )
            assert resp.status == 404
            body = await resp.json()
            assert body == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


def test_session_detail_cross_scope_label_identical_to_unknown(mk_bot, run_async):
    """A label that exists but belongs to a DIFFERENT scope must produce
    a response byte-for-byte identical to a genuinely unknown label —
    this is the headline requirement this stage introduces (see
    design.md's cross-scope-resolution section)."""
    registry = SessionRegistry()
    scope_a = Scope(chat_id=-1, kind="group", label="a",
                    members=(Member(id=1, label="alice", role="developer"),))
    scope_b = Scope(chat_id=-2, kind="group", label="b",
                    members=(Member(id=2, label="bob", role="developer"),))
    bot = mk_bot(registry, scopes=[scope_a, scope_b])
    bot._app.bot.username = "bot"

    sess_b = registry.get_or_create("claude-b")
    sess_b.label = "shared-label"
    sess_b.scope_chat_id = -2

    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            alice = _init_data(1)
            resp_other_scope = await client.get(
                "/api/sessions/shared-label",
                headers={"X-Telegram-Init-Data": alice},
            )
            resp_unknown = await client.get(
                "/api/sessions/totally-unknown-label",
                headers={"X-Telegram-Init-Data": alice},
            )
            assert resp_other_scope.status == resp_unknown.status == 404
            body_other_scope = await resp_other_scope.json()
            body_unknown = await resp_unknown.json()
            assert body_other_scope == body_unknown == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


def test_session_detail_diff_cross_scope_label_identical_to_unknown(mk_bot, run_async):
    """Same identical-404 guarantee, for the /diff route."""
    registry = SessionRegistry()
    scope_a = Scope(chat_id=-1, kind="group", label="a",
                    members=(Member(id=1, label="alice", role="developer"),))
    scope_b = Scope(chat_id=-2, kind="group", label="b",
                    members=(Member(id=2, label="bob", role="developer"),))
    bot = mk_bot(registry, scopes=[scope_a, scope_b])
    bot._app.bot.username = "bot"

    sess_b = registry.get_or_create("claude-b")
    sess_b.label = "shared-label"
    sess_b.scope_chat_id = -2

    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            alice = _init_data(1)
            resp_other_scope = await client.get(
                "/api/sessions/shared-label/diff",
                headers={"X-Telegram-Init-Data": alice},
            )
            resp_unknown = await client.get(
                "/api/sessions/totally-unknown-label/diff",
                headers={"X-Telegram-Init-Data": alice},
            )
            assert resp_other_scope.status == resp_unknown.status == 404
            body_other_scope = await resp_other_scope.json()
            body_unknown = await resp_unknown.json()
            assert body_other_scope == body_unknown == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


# ===== Stage 2: /api/sessions/{label}/diff =================================

def test_session_diff_wired_against_collect_diff(scoped_server, run_async, monkeypatch):
    async def _fake_collect_diff(cwd):
        assert cwd == "/home/dev/myproject"
        return {"available": True, "files": [], "files_truncated": False}
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff", _fake_collect_diff,
    )

    async def _run():
        client = await _client_for(scoped_server)
        try:
            good = _init_data(555)
            resp = await client.get(
                "/api/sessions/dev/diff", headers={"X-Telegram-Init-Data": good},
            )
            assert resp.status == 200
            body = await resp.json()
            assert body == {"available": True, "files": [], "files_truncated": False}
        finally:
            await client.close()
    run_async(_run())


def test_session_diff_never_receives_a_client_supplied_path(scoped_server, run_async, monkeypatch):
    """collect_diff must only ever be called with the session's own
    server-stamped cwd — never anything derived from the request."""
    seen = {}
    async def _fake_collect_diff(cwd):
        seen["cwd"] = cwd
        return {"available": False, "reason": "not_a_git_repo"}
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff", _fake_collect_diff,
    )

    async def _run():
        client = await _client_for(scoped_server)
        try:
            good = _init_data(555)
            await client.get(
                "/api/sessions/dev/diff?cwd=/etc/passwd",
                headers={"X-Telegram-Init-Data": good},
            )
        finally:
            await client.close()
    run_async(_run())
    assert seen["cwd"] == "/home/dev/myproject"


# ===== Stage 2: auth gate applies to every new route, before any registry read

@pytest.mark.parametrize("path", [
    "/api/sessions", "/api/sessions/dev", "/api/sessions/dev/diff",
])
def test_new_routes_reject_missing_header_401(scoped_server, run_async, path):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            resp = await client.get(path)
            assert resp.status == 401
            body = await resp.json()
            assert body == {"error": "unauthorized"}
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("path", [
    "/api/sessions", "/api/sessions/dev", "/api/sessions/dev/diff",
])
def test_new_routes_reject_forged_signature_401(scoped_server, run_async, path):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            forged = _init_data(555, bot_token="000000:forged-token-abcdefghijklmno")
            resp = await client.get(path, headers={"X-Telegram-Init-Data": forged})
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("path", [
    "/api/sessions", "/api/sessions/dev", "/api/sessions/dev/diff",
])
def test_new_routes_reject_stale_init_data_401(scoped_server, run_async, path):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            stale = _init_data(555, auth_date=int(time.time()) - 3600)
            resp = await client.get(path, headers={"X-Telegram-Init-Data": stale})
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("path", [
    "/api/sessions", "/api/sessions/dev", "/api/sessions/dev/diff",
])
def test_new_routes_reject_non_member_403(scoped_server, run_async, path):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            stranger = _init_data(999999)  # valid signature, not a scope member
            resp = await client.get(path, headers={"X-Telegram-Init-Data": stranger})
            assert resp.status == 403
            body = await resp.json()
            assert body == {"error": "forbidden"}
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("path", [
    "/api/sessions", "/api/sessions/dev", "/api/sessions/dev/diff",
])
def test_new_routes_reject_missing_header_before_any_registry_read(scoped_server, run_async, path, monkeypatch):
    """Auth must be checked BEFORE the registry is ever touched — proven
    by making any registry read raise and confirming a header-less
    request still cleanly 401s instead of blowing up."""
    def _boom(*args, **kwargs):
        raise AssertionError("registry must not be read before auth succeeds")
    monkeypatch.setattr(scoped_server.registry, "find_by_label", _boom)
    monkeypatch.setattr(scoped_server.registry, "all_sessions", _boom)

    async def _run():
        client = await _client_for(scoped_server)
        try:
            resp = await client.get(path)
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("path", [
    "/api/sessions", "/api/sessions/dev", "/api/sessions/dev/diff",
])
def test_new_routes_reject_post_put_delete_patch(scoped_server, run_async, path):
    async def _run():
        client = await _client_for(scoped_server)
        try:
            for method in ("post", "put", "delete", "patch"):
                if method == "post" and path == "/api/sessions":
                    # Batch 5 added POST /api/sessions as the session-create
                    # route. Every other verb/path combination stays refused.
                    continue
                if method == "delete" and path == "/api/sessions/dev":
                    # The session-controls batch added DELETE on this exact
                    # path (the Delete action) — a real, auth-gated route
                    # now, so a header-less request 401s (see the dedicated
                    # session-controls test file) rather than 404/405ing at
                    # the router. Every other verb/path combination here
                    # stays refused.
                    continue
                resp = await getattr(client, method)(path)
                assert resp.status in (404, 405), f"{method.upper()} {path} -> {resp.status}"
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
        # No longer names the old extra: aiohttp is a base dependency,
        # so its absence means a broken install, not a missing option.
        msg = str(exc_info.value)
        assert "aiohttp" in msg and "incomplete" in msg
        assert "reinstall" in msg.lower() or "install" in msg.lower()
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
