"""Shared fixtures for the black-box HTTP test suite covering the four
Mini App session-control routes (Stop/Kill/Resume/Delete) plus the
`actions` extension to `GET /api/sessions/{label}` -- design.md's
"Success criteria" section, tested strictly against entrypoints.md's
documented wire contract.

Fixture shape follows tests/test_miniapp_session_preferences_api.py
(named as this batch's reference by the orchestrator): HMAC-signed
`initData`, `_Policy`/`_Role` stand-ins implementing both `bypass_safety`
and `can_prompt` (this route's gate is `_can_prompt_user`, not
`_is_admin_user` -- design.md's Authorization section), member ids at
every relevant permission level, aiohttp `TestClient`/`TestServer`
against `MiniAppServer._build_app()`.

Every test that reaches `aipager.dtach.inject.{send_keys,kill_session,
is_alive,launch_session}` MUST monkeypatch it explicitly -- this suite
never touches a real dtach socket, launches a real claude process, or
hits the real Telegram API (autouse `_block_real_telegram_http` in the
root conftest already refuses any real Telegram HTTP call).
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
FOREIGN_SCOPE_CHAT_ID = -200

ADMIN_ID = 555          # bypass_safety AND can_prompt
DEVELOPER_ID = 777      # can_prompt, NOT bypass_safety -- proves the
                         # gate is _can_prompt_user, not _is_admin_user
READONLY_ID = 888       # neither bypass_safety NOR can_prompt
OUTSIDER_ID = 999999    # a member of no scope at all
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
    """Minimal stand-in for the real policy, implementing both
    `bypass_safety` and `can_prompt` (design.md Risks note, echoed in
    the reference fixture file): `_can_prompt_user` reads `can_prompt`
    via `_role_can_prompt`, so a half-built double would AttributeError
    loudly rather than quietly pass for the wrong reason."""

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
    # Background "refresh bot commands / name" fire-and-forget tasks that
    # a successful Kill/Resume schedules via asyncio.create_task --
    # mocked so a stray unawaited real call against the MagicMock
    # `_app.bot` never happens.
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, registry, port=8766)


def _mk_session(server, label, *, scope_chat_id=SCOPE_CHAT_ID,
                 status=Status.IDLE, claude_session_id="", cwd="/tmp/proj",
                 name=None):
    # `name` lets a test build two DIFFERENT registry entries that
    # happen to share the same `label` (the wire-visible name) across
    # two different scopes -- the registry key must stay unique even
    # though the human-facing label collides.
    sess = server.registry.get_or_create(name or f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.status = status
    sess.claude_session_id = claude_session_id
    sess.cwd = cwd
    return sess


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client
