"""Multilingual phase 3b: the Mini App's report page gets its areas and its
result line in the viewer's own language; the report itself and the
``retry`` flag are unchanged."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aipager import preferences
from aipager.bot import report_drafts
from tests.report_ui_harness import OWNER
from tests.test_miniapp_preferences_api import BOT_TOKEN
from tests.test_report_miniapp import _hdr, _post, _server


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)

NOTIFY = {"frames": [{"file": "aipager/bot/notify.py"}]}


def _draft(state="open", result=None):
    return SimpleNamespace(id="d1", report={"errors": [NOTIFY]}, preview=b"{}",
                           state=state, result=result)


def test_draft_areas_follow_the_viewer(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(OWNER, "fa")
    srv, _bot, _tg = _server(mk_bot)

    async def _open(bot, user_id):
        return _draft()
    monkeypatch.setattr(report_drafts, "open_draft", _open)
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER))
    assert status == 200
    assert body["areas"] == ["\u200fتحویل پیام‌ها"]
    assert body["preview"] == "{}"


def test_draft_areas_in_english_by_default(mk_bot, run_async, monkeypatch):
    srv, _bot, _tg = _server(mk_bot)

    async def _open(bot, user_id):
        return _draft()
    monkeypatch.setattr(report_drafts, "open_draft", _open)
    assert _post(run_async, srv, "/api/report/draft", _hdr(OWNER))[1]["areas"] == [
        "message delivery"]


def test_send_line_follows_the_viewer(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(OWNER, "ru")
    srv, _bot, _tg = _server(mk_bot)
    draft = _draft()
    monkeypatch.setattr(report_drafts, "lookup", lambda bot, uid, did: draft)
    monkeypatch.setattr(report_drafts, "check_note", lambda d, note: ("ok", "", {"v": 1}))

    async def _send(bot, d, candidate):
        return report_drafts.reply_for("rate_limited", None)
    monkeypatch.setattr(report_drafts, "send_draft", _send)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER),
                         {"draft": "d1", "note": ""})
    assert status == 200
    assert body["line"] == "Сейчас не удалось отправить. Ничего не потеряно; попробуй позже."
    assert body["retry"] is True and body["outcome"] == "rate_limited"


def test_a_repeat_send_line_follows_the_viewer(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(OWNER, "ru")
    srv, _bot, _tg = _server(mk_bot)
    draft = _draft(state="done", result=("sent", "ap1-x"))
    monkeypatch.setattr(report_drafts, "lookup", lambda bot, uid, did: draft)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER),
                         {"draft": "d1", "note": ""})
    assert status == 200
    assert body["line"] == ("Отправлено. Номер ap1-x (укажи его, если откроешь issue "
                            "на GitHub).")
    assert body["repeat"] is True and body["retry"] is False
