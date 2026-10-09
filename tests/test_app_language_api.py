"""The Mini App's "App language" (the multilingual design, 7.2): the
viewer's own language for aipager's text. GET /api/preferences carries
it; PUT /api/app-language sets it. Authenticated like every route, but
never admin-gated (it is personal), written only to the viewer's own
private-chat entry, never to the chat being looked at, never posted into
a chat; 400 for anything but Automatic or a language a person may pick,
429 past the write budget."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences
from aipager.i18n import locales
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
GROUP, ADMIN_ID, MEMBER_ID, OUTSIDER_ID = -100, 555, 777, 999
CONFIRM = ("✅ aipager now speaks English here. Your sessions and Claude's answers "
           "are unchanged.")
HINT = "Only aipager's own text. Claude's answers don't change."


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _init_data(user_id, language_code=None):
    user = {"id": user_id, "first_name": "Test"}
    if language_code:
        user["language_code"] = language_code
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id, language_code=None):
    return {"X-Telegram-Init-Data": _init_data(user_id, language_code)}


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN_ID, label="ada", role="admin"),
        Member(id=MEMBER_ID, label="bob", role="user")))
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    bot._app.bot.username = "aipager_test_bot"
    bot._app.bot.send_message = AsyncMock()
    return MiniAppServer(bot, registry, port=8765)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _call(srv, method, path, headers, body=None):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    try:
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["data"] = body if isinstance(body, str) else json.dumps(body)
        resp = await client.request(method, path, **kwargs)
        try:
            data = await resp.json()
        except Exception:
            data = None
        return resp.status, data
    finally:
        await client.close()


def test_get_carries_the_app_language(server):
    status, data = _run(_call(server, "GET", "/api/preferences", _hdr(MEMBER_ID)))
    assert status == 200
    al = data["app_language"]
    assert al == {
        "title": "App language", "hint": HINT, "value": "auto", "effective": "en",
        # Phase 2: Russian and Persian are offered (no beta mark here).
        "options": [{"value": "auto", "label": "Automatic (your Telegram language)"},
                    {"value": "en", "label": "English"}, {"value": "ru", "label": "Русский"},
                    {"value": "fa", "label": "فارسی"}]}


def test_a_member_who_is_not_an_admin_may_set_it(server):
    status, data = _run(_call(server, "PUT", "/api/app-language", _hdr(MEMBER_ID),
                              {"value": "en"}))
    assert status == 200
    assert data == {"value": "en", "effective": "en", "confirmation": CONFIRM}
    assert preferences.get_ui_language(MEMBER_ID) == "en"
    # The viewer's own entry, never the chat being looked at.
    assert preferences.get_ui_language(GROUP) == ""
    # Never posted into a chat.
    server.bot._app.bot.send_message.assert_not_awaited()
    status, data = _run(_call(server, "GET", "/api/preferences", _hdr(MEMBER_ID)))
    assert data["app_language"]["value"] == "en"
    status, data = _run(_call(server, "PUT", "/api/app-language", _hdr(MEMBER_ID),
                              {"value": "auto"}))
    assert status == 200 and data["value"] == "auto"
    assert preferences.get_ui_language(MEMBER_ID) == ""


@pytest.mark.parametrize("body", [
    {"value": "de"}, {"value": "qps"}, {"value": "qps-rtl"}, {"value": "xx"},
    {"value": ""}, {"value": 5}, {}, ["en"], "not json"])
def test_anything_else_is_a_bad_request(server, body, monkeypatch):
    # Refused by the route itself, before the store's own check.
    asked = []
    monkeypatch.setattr(preferences, "set_ui_language", lambda *a: asked.append(a))
    status, data = _run(_call(server, "PUT", "/api/app-language", _hdr(MEMBER_ID), body))
    assert (status, data) == (400, {"error": "bad_request"})
    assert asked == [] and preferences.get_ui_language(MEMBER_ID) == ""


@pytest.mark.parametrize("who, want", [("none", 401), ("junk", 401), ("outsider", 403)])
def test_unauthenticated_or_outsiders_are_refused(server, who, want):
    # Signed here, not at collection: initData goes stale in minutes.
    headers = {"none": {}, "junk": {"X-Telegram-Init-Data": "x"},
               "outsider": _hdr(OUTSIDER_ID)}[who]
    status, _data = _run(_call(server, "PUT", "/api/app-language", headers, {"value": "en"}))
    assert status == want
    assert preferences._ensure_loaded() == {}


def test_the_write_budget_applies(server, monkeypatch):
    monkeypatch.setattr(server, "_allow_write", lambda user_id: False)
    status, data = _run(_call(server, "PUT", "/api/app-language", _hdr(MEMBER_ID),
                              {"value": "en"}))
    assert (status, data) == (429, {"error": "too_many_requests"})
    assert preferences._ensure_loaded() == {}


def test_the_viewer_s_telegram_language_is_the_automatic_one(server, monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"fa"}))
    status, data = _run(_call(server, "GET", "/api/preferences", _hdr(MEMBER_ID, "fa")))
    assert data["app_language"]["effective"] == "fa"
    status, data = _run(_call(server, "PUT", "/api/app-language", _hdr(MEMBER_ID, "fa"),
                              {"value": "en"}))
    assert data["effective"] == "en"
