"""Multilingual phase 3b: the Mini App's update check answers in the
viewer's own language, while the job it starts posts in its chat's."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from tests.i18n_surfaces import update_status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
GROUP, ADMIN = -100, 555


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _hdr(user_id, language_code=None):
    user = {"id": user_id, "first_name": "T"}
    if language_code:
        user["language_code"] = language_code
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),))])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))

    async def _check():
        return update_status()
    b.updates.check = _check
    return b


def _call(bot, run_async, fn):
    srv = MiniAppServer(bot, bot.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            return await fn(client)
        finally:
            await client.close()
    return run_async(scenario())


def _check(bot, run_async, language_code=None):
    async def fn(c):
        r = await c.post("/api/update/check", headers=_hdr(ADMIN, language_code))
        return r.status, await r.json()
    return _call(bot, run_async, fn)


def test_check_lines_follow_the_viewer(bot, run_async):
    preferences.set_ui_language(ADMIN, "fa")
    preferences.set_ui_language(GROUP, "ru")
    status, body = _check(bot, run_async, "en")
    assert status == 200
    check = body["check"]
    assert check["lines"][0] == "\u200faipager \u20681.0.1\u2069 ← \u20681.0.2\u2069\u200f"
    assert check["offer"] == {"kind": "both", "label": "\u200fآپدیت هر دو\u200f"}
    assert any("؀" <= ch <= "ۿ" for ch in check["restart"])


def test_a_telegram_language_alone_stays_english(bot, run_async):
    """Detection is off (decision D1): a Russian app with nothing stored
    reads English."""
    status, body = _check(bot, run_async, "ru")
    assert status == 200
    assert body["check"]["lines"] == ["aipager 1.0.1 → 1.0.2",
                                      "Claude Code 2.1.300 → 2.1.310"]
    assert body["check"]["offer"]["label"] == "Update both"


def test_the_started_job_posts_in_the_chats_language(bot, run_async):
    preferences.set_ui_language(ADMIN, "fa")
    preferences.set_ui_language(GROUP, "ru")
    mgr = bot.updates
    mgr._checked = (time.monotonic(), update_status(ap_newer=False, latest="1.0.1"))
    mgr._lock = MagicMock(try_acquire=MagicMock(return_value=True))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9))

    async def _run_job(job):
        await mgr._report(job)
    mgr._run_job = _run_job

    async def fn(c):
        r = await c.post("/api/update/start", json={"kind": "claude"},
                         headers=_hdr(ADMIN, "en"))
        await mgr._job_task
        return r.status
    assert _call(bot, run_async, fn) == 202
    call = bot._app.bot.send_message.await_args
    assert call.args[0] == GROUP
    assert call.args[1] == "⏳ Начинаю обновление…"
