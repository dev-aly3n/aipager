"""Acting on a session from the Mini App closes the person's open /new Name
card in chat, as acting on one from chat does (review-3, 2026-09-30).

Otherwise: open /new in chat, start or stop a session from the app, come
back and type a message for it, and the card takes that message as the
name of a new Auto session. Looking (a GET, preferences) keeps the card.
Real routes over aiohttp's test server, the Mini App tests' own signing.
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
CHAT = -100
OWNER = 555
OTHER = 777


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _hdr(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Test"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


class _Role:
    bypass_safety = True
    can_prompt = True


class _Policy:
    def get_role(self, name):
        return _Role()


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(chat_id=CHAT, kind="group", label="team", members=(
        Member(id=OWNER, label="ada", role="admin"),
        Member(id=OTHER, label="bob", role="admin"),))
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    bot._app.bot.edit_message_text = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    sess = registry.get_or_create("claude-dev")
    sess.label, sess.scope_chat_id, sess.status = "dev", CHAT, Status.IDLE
    bot._new_wizard_pending = {CHAT: {"step": "name", "user_id": OWNER,
                                      "msg_id": 900, "last_active": time.monotonic()}}
    return MiniAppServer(bot, registry, port=8769)


def _call(run_async, srv, method, path, user, **kw):
    async def _go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=_hdr(user), **kw)
            return resp.status
        finally:
            await client.close()
    return run_async(_go())


def _open(srv):
    return CHAT in srv.bot._new_wizard_pending


def test_an_action_on_a_session_closes_the_card(server, run_async):
    server.registry.get("claude-dev").queue_prompt("later", 1)
    status = _call(run_async, server, "POST", "/api/sessions/dev/clearqueue", OWNER)

    assert status < 400
    assert not _open(server)


def test_starting_a_session_from_the_app_closes_the_card(server, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    status = _call(run_async, server, "POST", "/api/sessions", OWNER,
                   json={"name": "x2"})

    assert status < 500
    assert not _open(server)


def test_looking_at_a_session_keeps_the_card(server, run_async):
    _call(run_async, server, "GET", "/api/sessions/dev", OWNER)
    _call(run_async, server, "GET", "/api/sessions/dev/preferences", OWNER)

    assert _open(server)


def test_changing_a_sessions_preferences_keeps_the_card(server, run_async):
    _call(run_async, server, "PUT", "/api/sessions/dev/preferences/layout", OWNER,
          json={"value": "card"})

    assert _open(server)


def test_someone_elses_action_keeps_the_card(server, run_async):
    server.registry.get("claude-dev").queue_prompt("later", 1)
    status = _call(run_async, server, "POST", "/api/sessions/dev/clearqueue", OTHER)

    assert status < 400
    assert _open(server)


def test_a_refused_action_keeps_the_card(server, run_async):
    """Review-4: the close waits for the action to go through. A request
    the route refuses (here: nothing is queued, so there is nothing to
    clear) changes nothing, the card included."""
    status = _call(run_async, server, "POST", "/api/sessions/dev/clearqueue", OWNER)

    assert status >= 400
    assert _open(server)


def test_the_close_key_is_a_typed_request_key(server, run_async):
    """Review-5 nit: a plain str request key makes aiohttp warn
    (NotAppKeyWarning, once per key per process, so the journal shows
    it). aiohttp has RequestKey since 3.10; the pinned one does."""
    from aiohttp import web

    from aipager.miniapp import server as server_module

    assert isinstance(server_module._close_card_key(), web.RequestKey)
    server.registry.get("claude-dev").queue_prompt("later", 1)
    _call(run_async, server, "POST", "/api/sessions/dev/clearqueue", OWNER)
    assert not _open(server)
