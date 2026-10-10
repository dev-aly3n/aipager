"""The upgrade offer card (the multilingual design, 7.4). It goes only to
a private chat with nothing stored whose Telegram language aipager speaks
(not English), at the lowest priority, never while muted. Its caller and
its once-only record: tests/test_language_launch.py."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import language_offer, report_offer
from aipager.i18n import locales
from tests.test_language_setting import ALY, DM, GROUP, edited, tap, toast
from tests.test_language_setting import bot as _bot_fixture  # noqa: F401

bot = _bot_fixture

OFFER_EN = ("🌐 aipager now speaks English. This changes only aipager's own messages and "
            "buttons here and in the Mini App, not your Claude sessions: Claude still "
            "answers in the language you write to it.")


@pytest.fixture
def sends(monkeypatch):
    calls = []

    async def _send(bot, chat_id, text, **kwargs):
        calls.append((chat_id, text, kwargs))
        return MagicMock(message_id=5)

    monkeypatch.setattr("aipager.bot.transport.send_text", _send)
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"fa"}))
    return calls


def test_the_offer_is_ornament_and_mute_aware(bot, run_async, sends, monkeypatch):
    assert run_async(language_offer.maybe_offer(bot, DM, "fa-IR")) is True
    (chat, text, kwargs), = sends
    assert chat == DM and "⟦" not in text
    assert kwargs["rate_limit_args"]["class"] == "ornament"
    assert kwargs["rate_limit_args"]["kind"] == "skip"
    assert kwargs["disable_notification"] is True
    sends.clear()
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: False)     # muted
    assert run_async(language_offer.maybe_offer(bot, DM, "fa")) is False
    assert sends == []


@pytest.mark.parametrize("chat, code, stored", [
    (DM, "en", ""), (DM, "ja", ""), (DM, None, ""), (GROUP, "fa", ""), (DM, "fa", "en")])
def test_the_offer_goes_only_where_it_fits(bot, run_async, sends, monkeypatch, chat, code,
                                           stored):
    if stored:
        preferences.set_ui_language(chat, stored)
    assert run_async(language_offer.maybe_offer(bot, chat, code)) is False
    assert sends == []


def test_the_card_names_the_language_in_its_own_words():
    text, kb = language_offer.render_offer(i18n.get("en"), "en")
    assert text == OFFER_EN
    (row,) = kb.inline_keyboard
    assert [(b.text, b.callback_data) for b in row] == [
        ("English", "_:set:lang_offer:en"), ("Keep English", "_:set:lang_offer:keep")]
    text, kb = language_offer.render_offer(i18n.get("en"), "fa")
    assert "aipager now speaks فارسی." in text
    assert kb.inline_keyboard[0][0].text == "فارسی"
    assert all(len(b.callback_data.encode()) <= 64 for b in kb.inline_keyboard[0])


def test_keep_english_sets_the_dm_and_confirms(bot, run_async):
    q = tap(bot, run_async, "_:set:lang_offer:keep", chat=DM, user=ALY)
    assert preferences.get_ui_language(DM) == "en"
    text, rows = edited(q)
    assert text == ("✅ aipager now speaks English here. Your sessions and Claude's answers "
                    "are unchanged.") and rows == []


@pytest.mark.parametrize("chat, data", [
    (DM, "_:set:lang_offer:de"), (DM, "_:set:lang_offer:ja"), (DM, "_:set:lang_offer:qps"), (GROUP, "_:set:lang_offer:en"),
    (DM, "_:set:lang_offer:en:x")])
def test_an_offer_tap_that_does_not_fit_writes_nothing(bot, run_async, chat, data,
                                                       monkeypatch):
    # German is enabled (beta) since translation run 2; it stands here for a
    # known language that is switched off.
    monkeypatch.setitem(locales.LOCALES, "de", dataclasses.replace(
        locales.LOCALES["de"], enabled=False))
    q = tap(bot, run_async, data, chat=chat, user=ALY)
    assert toast(q)[0] in ("Invalid value", "Invalid callback")
    assert q.edit_message_text.await_count == 0
    assert preferences._ensure_loaded() == {}


def test_the_offer_has_exactly_one_caller():
    """Phase 5 wires it in: the language launch (after an update and at
    start) is the only sender."""
    from pathlib import Path
    root = Path(language_offer.__file__).resolve().parents[1]
    callers = sorted(p.name for p in root.rglob("*.py")
                     if p.name != "language_offer.py"
                     and "language_offer.maybe_offer(" in p.read_text(encoding="utf-8"))
    assert callers == ["language_launch.py"], callers


# ---- a tap is an explicit choice (review rev-iter1-002) --------------------------------

def test_a_language_tap_refreshes_menu_keyboard_and_button(bot, run_async):
    """Like a choice in /language: the `/` menu, ONE reply keyboard and the
    menu button follow at once, so the chat is never half English."""
    q = tap(bot, run_async, "_:set:lang_offer:fa", chat=DM, user=ALY)
    assert preferences.get_ui_language(DM) == "fa"
    assert "✅" in edited(q)[0]
    bot._send_keyboard.assert_awaited_once_with(level="main", chat_id=DM)
    assert bot._app.bot.set_my_commands.await_count >= 1


def test_a_language_tap_gives_the_menu_button_a_link_in_that_language(bot, run_async):
    """The menu button's link carries the language (``?lang=``), so the
    Mini App's first frame is already in it (2026-10-10 flash fix)."""
    bot._miniapp_url = "https://tunnel.example/"
    tap(bot, run_async, "_:set:lang_offer:fa", chat=DM, user=ALY)
    [call] = bot._app.bot.set_chat_menu_button.await_args_list
    assert call.kwargs["chat_id"] == DM
    assert call.kwargs["menu_button"].web_app.url == "https://tunnel.example/?lang=fa"


def test_the_tap_calls_the_language_change_once_with_the_keyboard(bot, run_async):
    bot._on_language_changed = AsyncMock()
    tap(bot, run_async, "_:set:lang_offer:fa", chat=DM, user=ALY)
    bot._on_language_changed.assert_awaited_once_with(DM)


def test_keep_english_on_an_english_chat_sends_no_keyboard(bot, run_async):
    bot._on_language_changed = AsyncMock()
    tap(bot, run_async, "_:set:lang_offer:keep", chat=DM, user=ALY)
    assert preferences.get_ui_language(DM) == "en"
    bot._on_language_changed.assert_not_awaited()
    bot._send_keyboard.assert_not_awaited()
