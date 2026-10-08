"""The Mini App's "Report a problem" row (roadmap 8.112 step 3):
``can_report`` in GET /api/preferences and POST /api/report/preview,
which only asks the daemon to post the preview card in the owner's
private chat. Owner only, rate limited."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.bot import report_flow
from aipager.miniapp.server import SCOPE_HEADER, MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry
from tests.report_ui_harness import GROUP, OWNER, FakeTg
from tests.test_miniapp_preferences_api import BOT_TOKEN, _init_data

ADMIN = 555


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _scopes(with_owner_dm=True):
    group = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="aly", role="owner"),
        Member(id=ADMIN, label="ada", role="admin")))
    dm = Scope(chat_id=OWNER, kind="dm", label="aly DM",
               members=(Member(id=OWNER, label="aly", role="owner"),))
    return [dm, group] if with_owner_dm else [group]


def _server(mk_bot, *, with_owner_dm=True):
    registry = SessionRegistry()
    bot = mk_bot(registry, scopes=_scopes(with_owner_dm))
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    tg = FakeTg()
    tg.username = "aipager_test_bot"
    bot._app.bot = tg
    return MiniAppServer(bot, registry, port=8765), bot, tg


def _hdr(user_id, scope=None):
    h = {"X-Telegram-Init-Data": _init_data(user_id)}
    if scope is not None:
        h[SCOPE_HEADER] = str(scope)
    return h


def _call(run_async, srv, method, path, headers):
    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, json={})
            return resp.status, await resp.json()
        finally:
            await client.close()
    return run_async(_run())


def test_can_report_only_owner_private(mk_bot, run_async):
    srv, _bot, _tg = _server(mk_bot)
    assert _call(run_async, srv, "GET", "/api/preferences", _hdr(OWNER))[1]["can_report"] is True
    assert _call(run_async, srv, "GET", "/api/preferences",
                 _hdr(OWNER, GROUP))[1]["can_report"] is False
    assert _call(run_async, srv, "GET", "/api/preferences", _hdr(ADMIN))[1]["can_report"] is False


def test_preview_opens_the_card_in_the_dm(mk_bot, run_async):
    srv, bot, tg = _server(mk_bot)
    status, body = _call(run_async, srv, "POST", "/api/report/preview", _hdr(OWNER, GROUP))
    assert (status, body) == (200, {"opened": True})
    assert [m.chat_id for m in tg.sent] == [OWNER]
    (kept,) = bot._report_cards.values()
    assert kept.report["trigger"] == "manual" and kept.chat_id == OWNER


def test_403_non_owner(mk_bot, run_async):
    srv, bot, tg = _server(mk_bot)
    status, body = _call(run_async, srv, "POST", "/api/report/preview", _hdr(ADMIN))
    assert (status, body) == (403, {"error": "forbidden"})
    assert tg.sent == [] and bot._report_cards == {}


def test_no_owner_409(mk_bot, run_async):
    srv, _bot, tg = _server(mk_bot, with_owner_dm=False)
    status, body = _call(run_async, srv, "POST", "/api/report/preview", _hdr(OWNER))
    assert (status, body) == (409, {"error": "no_owner"})
    assert tg.sent == []


def test_429(mk_bot, run_async):
    srv, _bot, tg = _server(mk_bot)
    srv._write_hits[OWNER] = [time.monotonic()] * 100
    status, body = _call(run_async, srv, "POST", "/api/report/preview", _hdr(OWNER))
    assert (status, body) == (429, {"error": "too_many_requests"})
    assert tg.sent == []


def test_503_when_the_card_did_not_go_out(mk_bot, run_async):
    from aipager.bot.transport import MUTED
    srv, _bot, tg = _server(mk_bot)
    tg.send_result = MUTED
    status, body = _call(run_async, srv, "POST", "/api/report/preview", _hdr(OWNER))
    assert (status, body) == (503, {"error": "not_sent"})


def test_the_route_closes_the_owners_note_capture(mk_bot, run_async):
    srv, bot, tg = _server(mk_bot)
    run_async(report_flow.open_preview(bot, trigger="manual"))
    (kept,) = bot._report_cards.values()
    kept.state = "note"
    bot._report_note_pending = {"chat_id": OWNER, "user_id": OWNER, "msg_id": kept.msg_id,
                                "last_active": time.monotonic()}
    status, _body = _call(run_async, srv, "POST", "/api/report/preview", _hdr(OWNER))
    assert status == 200
    assert bot._report_note_pending is None and kept.state == "open"
