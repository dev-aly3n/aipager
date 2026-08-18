"""Black-box tests for design.md success criterion 16, entrypoints.md's
HTTP routes table:

  GET  /api/sessions/{label}: `actions.compact.available` is `false` with
       a non-null `reason` whenever `context_pct <= 0` on a `busy` or
       `idle` session (the only two statuses that ever offer `compact`);
       `true` otherwise.
  POST /api/sessions/{label}/compact: behavior UNCHANGED -- still returns
       200 and queues/sends regardless of `context_pct`. The GET-side
       gate is a UI affordance only, never server-side enforcement.

Fixture plumbing (HMAC-signed initData, aiohttp TestClient/TestServer
against `MiniAppServer._build_app()`) is self-contained in this file
rather than importing another ship's `tests/integration/.../conftest.py`,
to keep this batch independently auditable -- it mirrors the pattern
already established in `tests/integration/miniapp_session_controls/
conftest.py` and `tests/integration/miniapp_session_menu_actions/
test_session_menu_actions_api.py` (existing files, read for framework
conventions only; no assertion here was copied from either).

Every test that could reach a real dtach seam
(`send_keys`/`send_text_and_enter`/`is_alive`/`launch_session`/
`kill_session`) monkeypatches it explicitly -- this file never touches a
real dtach socket.
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
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
SCOPE_CHAT_ID = -100
ADMIN_ID = 555


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id, *, bot_token=BOT_TOKEN):
    fields = {
        "auth_date": str(int(time.time())),
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
    _ROLES = {"admin": _Role(bypass_safety=True, can_prompt=True)}

    def get_role(self, name):
        return self._ROLES.get(name)


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=SCOPE_CHAT_ID, kind="group", label="team",
        members=(Member(id=ADMIN_ID, label="ada", role="admin"),),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, registry, port=8768)


def _mk_session(server, label, *, status, last_token_pct):
    sess = server.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = SCOPE_CHAT_ID
    sess.status = status
    sess.last_token_pct = last_token_pct
    sess.claude_session_id = "uuid-1"
    sess.cwd = "/tmp/proj"
    return sess


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


# ===== GET .../actions.compact ============================================

@pytest.mark.parametrize("status", [Status.BUSY, Status.IDLE])
def test_compact_unavailable_when_context_pct_is_zero(server, run_async, status):
    async def _run():
        _mk_session(server, "dev", status=status, last_token_pct=0)
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["actions"]["compact"]["available"] is False
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("status", [Status.BUSY, Status.IDLE])
def test_compact_unavailable_reason_is_non_null(server, run_async, status):
    async def _run():
        _mk_session(server, "dev", status=status, last_token_pct=0)
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            reason = body["actions"]["compact"]["reason"]
            assert reason is not None
            assert len(reason) > 0
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("status", [Status.BUSY, Status.IDLE])
def test_compact_available_when_context_pct_positive(server, run_async, status):
    async def _run():
        _mk_session(server, "dev", status=status, last_token_pct=5)
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["actions"]["compact"] == {"available": True, "reason": None}
        finally:
            await client.close()
    run_async(_run())


def test_compact_unavailable_at_boundary_one_pct_is_available(server, run_async):
    """Boundary-value check: context_pct == 1 (just above the <=0 cutoff)
    must be available."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, last_token_pct=1)
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["actions"]["compact"]["available"] is True
        finally:
            await client.close()
    run_async(_run())


# ===== POST .../compact: unaffected by context_pct =========================

def test_post_compact_still_200_at_zero_pct_idle(server, run_async, monkeypatch):
    monkeypatch.setattr(
        "aipager.dtach.inject.send_keys", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.send_text_and_enter", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=True),
    )

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, last_token_pct=0)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "sent"
        finally:
            await client.close()
    run_async(_run())


def test_post_compact_still_200_at_zero_pct_busy(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, last_token_pct=0)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())
