"""The busy card's direction: the chat's language decides, so a Persian
card with only tool rows reads right to left (G65). Since 2026-10-17
(operator) Claude's own words no longer turn it: the card is aipager's
own, and Claude's sentences on it keep their own direction inside it."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

import pytest

from aipager import preferences
from aipager.bot import animation
from aipager.i18n import locales
from aipager.state import Status, TrackedSession

CHAT = 4242


@pytest.fixture(autouse=True)
def fa_ru(monkeypatch):
    for code in ("fa", "ru"):
        monkeypatch.setitem(locales.LOCALES, code,
                            dataclasses.replace(locales.LOCALES[code], enabled=True))


def _sess(commentary=()):
    s = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    s.scope_chat_id, s.scope_kind = CHAT, "dm"
    s.busy_msg_id = 77
    s.tool_history = [("Read: /a", True)]
    s.stream_commentary = [(0, t) for t in commentary]
    return s


def _edited_rtl(mk_bot, run_async, monkeypatch, sess) -> bool:
    edit = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(animation, "edit_message_text_rich", edit)
    bot = mk_bot()
    bot.registry._sessions[sess.name] = sess
    run_async(bot._edit_busy_rich(sess, "Working"))
    return edit.await_args.kwargs["is_rtl"]


def test_a_persian_card_without_commentary_is_rtl(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(CHAT, "fa")
    assert _edited_rtl(mk_bot, run_async, monkeypatch, _sess()) is True
    preferences.set_ui_language(CHAT, "ru")
    assert _edited_rtl(mk_bot, run_async, monkeypatch, _sess()) is False
    preferences.set_ui_language(CHAT, "")
    assert _edited_rtl(mk_bot, run_async, monkeypatch, _sess()) is False


def test_commentary_no_longer_decides(mk_bot, run_async, monkeypatch):
    # Changed 2026-10-17 (was test_commentary_still_decides: English
    # commentary made a Persian chat's card False, Persian an English
    # chat's True).
    preferences.set_ui_language(CHAT, "fa")
    assert _edited_rtl(mk_bot, run_async, monkeypatch,
                       _sess(["Reading the file now."])) is True
    preferences.set_ui_language(CHAT, "")
    assert _edited_rtl(mk_bot, run_async, monkeypatch,
                       _sess(["فایل را می‌خوانم."])) is False


def test_the_moved_copy_follows_the_same_rule(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(CHAT, "fa")
    sent = AsyncMock(return_value={"message_id": 9})
    monkeypatch.setattr(animation, "send_rich_message", sent)
    bot = mk_bot()
    sess = _sess()
    bot.registry._sessions[sess.name] = sess
    run_async(bot._send_card_copy(sess, 5, verb="Working", final=False, waiting=False))
    assert sent.await_args.kwargs["is_rtl"] is True
