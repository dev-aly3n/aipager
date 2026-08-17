"""Mini App session detail-page write actions — Stop/Kill/Resume/Delete on
``/api/sessions/{label}[/stop|/kill|/resume]`` (design.md: "Mini App
session controls").

Mirrors ``test_miniapp_session_preferences_api.py``'s exact fixture
pattern (``_Policy``/``_Role`` stand-ins implementing both
``bypass_safety`` and ``can_prompt``, ``_init_data``/``_hdr`` HMAC
signing, ``ADMIN_ID``/``DEVELOPER_ID``/``READONLY_ID``/``OUTSIDER_ID``/
``FOREIGN_MEMBER_ID``, aiohttp ``TestClient``/``TestServer``).

HARD RULE (design.md, this feature's own top risk): every test that
reaches ``inject.kill_session``, ``inject.launch_session``,
``inject.send_keys`` or ``inject.is_alive`` monkeypatches it — no test
here may kill a real dtach session, launch a real claude process, or
touch a real dtach socket. Each test below that exercises a code path
reaching one of those four names says so in its own setup.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.miniapp.sessions import NO_TRANSCRIPT_REASON
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

SCOPE_CHAT_ID = -100
FOREIGN_SCOPE_CHAT_ID = -200

ADMIN_ID = 555         # bypass_safety AND can_prompt
DEVELOPER_ID = 777     # can_prompt, NOT bypass_safety
READONLY_ID = 888      # neither bypass_safety NOR can_prompt
OUTSIDER_ID = 999      # member of no scope at all
FOREIGN_MEMBER_ID = 321  # a real member, but of the OTHER scope


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id, *, bot_token=BOT_TOKEN, auth_date=None):
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


class _Role:
    def __init__(self, *, bypass_safety=False, can_prompt=True):
        self.bypass_safety = bypass_safety
        self.can_prompt = can_prompt


class _Policy:
    """Minimal stand-in for the real policy, implementing BOTH
    `bypass_safety` and `can_prompt` (test-double note, design.md
    Risks) — `_can_prompt_user` reads `Role.can_prompt` via
    `_role_can_prompt`, so a stand-in with only `bypass_safety` would
    AttributeError, not silently pass for the wrong reason.

    - "admin"      -> bypass_safety=True,  can_prompt=True
    - "developer"  -> bypass_safety=False, can_prompt=True
    - "read_only"  -> bypass_safety=False, can_prompt=False
    """

    _ROLES = {
        "admin": _Role(bypass_safety=True, can_prompt=True),
        "developer": _Role(bypass_safety=False, can_prompt=True),
        "read_only": _Role(bypass_safety=False, can_prompt=False),
    }

    def get_role(self, name):
        return self._ROLES.get(name)


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=SCOPE_CHAT_ID, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=DEVELOPER_ID, label="bob", role="developer"),
            Member(id=READONLY_ID, label="cleo", role="read_only"),
        ),
    )
    foreign_scope = Scope(
        chat_id=FOREIGN_SCOPE_CHAT_ID, kind="group", label="other-team",
        members=(Member(id=FOREIGN_MEMBER_ID, label="zed", role="admin"),),
    )
    bot = mk_bot(registry, scopes=[scope, foreign_scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    # Both scheduled by the kill/resume cores via asyncio.create_task —
    # explicit AsyncMocks so the tasks resolve cleanly instead of a
    # MagicMock's auto-attributes chasing real bot._app.bot.* calls.
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, registry, port=8767)


def _mk_session(
    server, label, *, scope_chat_id=SCOPE_CHAT_ID, status=Status.IDLE,
    claude_session_id="",
):
    sess = server.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.status = status
    sess.claude_session_id = claude_session_id
    if status == Status.GONE:
        sess.gone_at = time.monotonic()
    return sess


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


# ===== Stop =================================================================

def test_stop_busy_session_returns_200_idles_it_and_mirrors_once(
    server, run_async, monkeypatch,
):
    """Exercises inject.send_keys — monkeypatched, never real."""
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        monkeypatch.setattr(
            "aipager.dtach.inject.send_keys", AsyncMock(return_value=True),
        )
        monkeypatch.setattr(
            "aipager.bot.session_ops.asyncio.sleep", AsyncMock(),
        )
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            body = await resp.json()
            assert body == {"status": "stopped", "label": "dev", "dropped": 0}

            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["status"] == "idle"

            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_stop_idle_session_returns_409_not_busy_and_sends_no_mirror(
    server, run_async,
):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            body = await resp.json()
            assert body == {
                "error": "not_busy",
                "detail": "This session isn't busy right now — nothing to stop.",
            }
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_stop_readonly_member_gets_403(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_stop_outsider_gets_403(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(OUTSIDER_ID),
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_stop_rate_limited(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        monkeypatch.setattr(
            "aipager.dtach.inject.send_keys", AsyncMock(return_value=True),
        )
        monkeypatch.setattr(
            "aipager.bot.session_ops.asyncio.sleep", AsyncMock(),
        )
        client = await _client_for(server)
        try:
            seen_429 = False
            for _ in range(35):
                resp = await client.post(
                    "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "stop route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== Kill ==================================================================

def test_kill_idle_session_returns_200_removes_from_registry_and_mirrors(
    server, run_async, monkeypatch,
):
    """Exercises inject.kill_session — monkeypatched, never real."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        monkeypatch.setattr(
            "aipager.dtach.inject.kill_session", AsyncMock(return_value=True),
        )
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {"status": "killed", "label": "dev"}
            assert server.registry.get("claude-dev") is None

            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert check.status == 404

            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_kill_still_running_returns_409_and_session_survives(
    server, run_async, monkeypatch,
):
    """Exercises inject.kill_session and inject.is_alive — both
    monkeypatched, never real."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        monkeypatch.setattr(
            "aipager.dtach.inject.kill_session", AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "aipager.dtach.inject.is_alive", AsyncMock(return_value=True),
        )
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert await resp.json() == {
                "error": "still_running",
                "detail": "Could not kill — the process is still running. Try again.",
            }
            assert server.registry.get("claude-dev") is not None
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_kill_race_gone_already_returns_404_matching_route_level_shape(
    server, run_async, monkeypatch,
):
    """The post-lookup race (dtach socket already gone) — deliberately
    the SAME minimal 404 body as an unknown label (design.md Risks)."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        monkeypatch.setattr(
            "aipager.dtach.inject.kill_session", AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "aipager.dtach.inject.is_alive", AsyncMock(return_value=False),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


def test_kill_readonly_member_gets_403(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_kill_rate_limited(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        monkeypatch.setattr(
            "aipager.dtach.inject.kill_session", AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            "aipager.dtach.inject.is_alive", AsyncMock(return_value=True),
        )
        client = await _client_for(server)
        try:
            seen_429 = False
            for _ in range(35):
                resp = await client.post(
                    "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "kill route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== Resume ================================================================

def test_resume_gone_with_transcript_returns_200_and_mirrors(
    server, run_async, monkeypatch,
):
    """Exercises inject.launch_session — monkeypatched, never real."""
    async def _run():
        _mk_session(
            server, "dev", status=Status.GONE, claude_session_id="UUID-1",
        )
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(True, "")),
        )
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(DEVELOPER_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {"status": "resumed", "label": "dev"}

            sess = server.registry.get("claude-dev")
            assert sess.status != Status.GONE
            # design.md Unknown 2: attributed directly from the
            # authenticated caller, no Update needed.
            assert sess.last_driver_user_id == DEVELOPER_ID

            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_resume_no_transcript_returns_409_with_shared_reason_string(
    server, run_async,
):
    """The 409 body's `detail` must be the EXACT same string
    `session_actions()` already put in the pre-check reason — one
    constant, not two independently-worded copies that could drift."""
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == "no_transcript"
            assert body["detail"] == NO_TRANSCRIPT_REASON

            detail = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID),
            )
            assert (await detail.json())["actions"]["resume"]["reason"] \
                == NO_TRANSCRIPT_REASON
        finally:
            await client.close()
    run_async(_run())


def test_resume_not_gone_returns_409(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_gone"
        finally:
            await client.close()
    run_async(_run())


def test_resume_launch_failed_returns_400_and_restores_transcript_id(
    server, run_async, monkeypatch,
):
    """Exercises inject.launch_session — monkeypatched, never real."""
    async def _run():
        _mk_session(
            server, "dev", status=Status.GONE, claude_session_id="UUID-1",
        )
        monkeypatch.setattr(
            "aipager.dtach.inject.launch_session",
            AsyncMock(return_value=(False, "dtach broken")),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 400
            body = await resp.json()
            assert body == {"error": "launch_failed", "detail": "dtach broken"}
            assert server.registry.get("claude-dev").claude_session_id == "UUID-1"
            assert server.registry.get("claude-dev").status == Status.GONE
        finally:
            await client.close()
    run_async(_run())


def test_resume_readonly_member_gets_403(server, run_async):
    async def _run():
        _mk_session(
            server, "dev", status=Status.GONE, claude_session_id="UUID-1",
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_resume_rate_limited(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="")
        client = await _client_for(server)
        try:
            seen_429 = False
            for _ in range(35):
                resp = await client.post(
                    "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "resume route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== Delete ================================================================

def test_delete_gone_session_returns_200_and_removes_it(server, run_async):
    """No dtach interaction at all — Delete only touches the registry
    (design.md: "a registry forget-only, not a process wipe")."""
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {"status": "deleted", "label": "dev"}
            assert server.registry.get("claude-dev") is None

            listing = await client.get("/api/sessions", headers=_hdr(ADMIN_ID))
            labels = [row["label"] for row in (await listing.json())["sessions"]]
            assert "dev" not in labels

            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_delete_calls_mark_dirty_explicitly(server, run_async):
    """registry.remove() does NOT call mark_dirty() itself (state.py) —
    the route must call it explicitly, or a page refresh/save would
    never observe the deletion. Reset `_dirty` right before the request
    so THIS call is the only thing that can flip it back to True."""
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            server.registry._dirty = False
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert server.registry._dirty is True
        finally:
            await client.close()
    run_async(_run())


def test_delete_live_session_returns_409_conflict_and_session_survives(
    server, run_async,
):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert await resp.json() == {
                "error": "conflict",
                "detail": "Session is still running. Use Kill to stop it first.",
            }
            assert server.registry.get("claude-dev") is not None
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_delete_readonly_member_gets_403(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_delete_rate_limited(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(35):
                _mk_session(server, f"dev{i}", status=Status.GONE)
                resp = await client.delete(
                    f"/api/sessions/dev{i}", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "delete route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== cross-scope isolation: byte-identical 404s across all four routes ===

@pytest.mark.parametrize("label", ["ghost-does-not-exist", "other"])
@pytest.mark.parametrize("method,path_suffix", [
    ("post", "/stop"), ("post", "/kill"), ("post", "/resume"), ("delete", ""),
])
def test_foreign_or_unknown_label_404s_identically_on_every_route(
    server, run_async, label, method, path_suffix,
):
    async def _run():
        # "other" genuinely exists — but in the FOREIGN scope.
        _mk_session(server, "other", scope_chat_id=FOREIGN_SCOPE_CHAT_ID)
        client = await _client_for(server)
        try:
            resp = await getattr(client, method)(
                f"/api/sessions/{label}{path_suffix}", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


def test_404_matches_the_existing_detail_route_404_byte_for_byte(
    server, run_async,
):
    async def _run():
        client = await _client_for(server)
        try:
            detail_resp = await client.get(
                "/api/sessions/ghost", headers=_hdr(ADMIN_ID),
            )
            stop_resp = await client.post(
                "/api/sessions/ghost/stop", headers=_hdr(ADMIN_ID),
            )
            assert detail_resp.status == stop_resp.status == 404
            assert await detail_resp.json() == await stop_resp.json()
        finally:
            await client.close()
    run_async(_run())
