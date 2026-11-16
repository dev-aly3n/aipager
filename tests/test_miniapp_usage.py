"""The Mini App's Usage page (roadmap 8.131): ``GET /api/usage`` behind the
/usage rule, the same rendered view, and the home button's hint."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences, usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.statusline_file import Window

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
OWNER, ADMIN, USER = 111, 555, 777
GROUP = -100


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _hdr(user_id, scope=None):
    user = {"id": user_id, "first_name": "T"}
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    headers = {"X-Telegram-Init-Data": urlencode(fields)}
    if scope is not None:
        headers["X-Aipager-Scope"] = str(scope)
    return headers


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[
        Scope(chat_id=OWNER, kind="dm", label="owner", members=(
            Member(id=OWNER, label="alice", role="owner"),)),
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=ADMIN, label="dave", role="admin"),
            Member(id=USER, label="bob", role="user"))),
    ])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    b._app.bot.username = "aipager_test_bot"
    return b


def _get(bot, run_async, path, headers):
    srv = MiniAppServer(bot, bot.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            r = await client.get(path, headers=headers)
            try:
                body = await r.json()
            except Exception:
                body = None
            return r.status, body
        finally:
            await client.close()
    return run_async(scenario())


def test_no_auth_is_401(bot, run_async):
    status, _ = _get(bot, run_async, "/api/usage", {})
    assert status == 401


def test_a_member_who_is_not_an_admin_is_403(bot, run_async):
    status, body = _get(bot, run_async, "/api/usage", _hdr(USER, GROUP))
    assert (status, body) == (403, {"error": "forbidden"})


def test_an_admin_gets_the_view(bot, run_async):
    usage_store.note_plan("frontend", {"seven_day": Window(83, int(time.time()) + 3 * 86400)},
                          SessionLogin("saved", "", "/home/alice/.claude"))
    status, body = _get(bot, run_async, "/api/usage", _hdr(ADMIN, GROUP))
    assert status == 200
    (login,) = body["plan"]["logins"]
    (window,) = login["windows"]
    assert window["line"] == "This week (all models): 83% used"
    assert window["state"] == "ok" and window["pct"] == 83
    assert body["tokens"]["title"] == "Tokens in the chats you manage"
    assert body["tokens"]["empty"] == "No tokens recorded yet."
    assert len(body["tokens"]["days"]) == 7
    assert "ui" not in body
    text = json.dumps(body)
    assert "$" not in text and "/home/alice" not in text and "cost" not in text


def test_the_page_is_private_so_a_group_view_keeps_the_plan(bot, run_async):
    status, body = _get(bot, run_async, "/api/usage", _hdr(OWNER, GROUP))
    assert status == 200
    assert body["plan"]["shown"] is True and body["tokens"]["title"] == "Tokens"


def test_the_view_follows_the_viewers_language(bot, run_async):
    preferences.set_ui_language(OWNER, "ru")
    status, body = _get(bot, run_async, "/api/usage", _hdr(OWNER, OWNER))
    assert status == 200
    assert body["ui"]["code"] == "ru"


@pytest.mark.parametrize("user,expected", [(OWNER, True), (ADMIN, True), (USER, False)])
def test_sessions_carry_the_usage_hint(bot, run_async, user, expected):
    status, body = _get(bot, run_async, "/api/sessions", _hdr(user, GROUP))
    assert status == 200
    assert body["can_usage"] is expected
