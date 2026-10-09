"""The upgrade offer card (the multilingual design, 7.4): built, tested,
and switched off until the other languages ship. When on, it goes only
to a private chat with nothing stored whose Telegram language aipager
speaks (not English), at the lowest priority, never while muted."""

from __future__ import annotations

from unittest.mock import MagicMock

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


def test_the_offer_sends_nothing_while_disabled(bot, run_async, sends):
    assert language_offer.OFFER_ENABLED is False
    assert run_async(language_offer.maybe_offer(bot, DM, "fa")) is False
    assert sends == []
    bot._app.bot.send_message.assert_not_awaited()


def test_enabled_offer_is_ornament_and_mute_aware(bot, run_async, sends, monkeypatch):
    monkeypatch.setattr(language_offer, "OFFER_ENABLED", True)
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
    monkeypatch.setattr(language_offer, "OFFER_ENABLED", True)
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
    (DM, "_:set:lang_offer:fa"), (DM, "_:set:lang_offer:qps"), (GROUP, "_:set:lang_offer:en"),
    (DM, "_:set:lang_offer:en:x")])
def test_an_offer_tap_that_does_not_fit_writes_nothing(bot, run_async, chat, data):
    q = tap(bot, run_async, data, chat=chat, user=ALY)
    assert toast(q)[0] in ("Invalid value", "Invalid callback")
    assert q.edit_message_text.await_count == 0
    assert preferences._ensure_loaded() == {}


def test_the_offer_has_no_caller_yet():
    """Phase 5 wires it in; until then nothing in aipager sends it."""
    from pathlib import Path
    root = Path(language_offer.__file__).resolve().parents[1]
    callers = [p.name for p in root.rglob("*.py")
               if p.name != "language_offer.py"
               and "maybe_offer" in p.read_text(encoding="utf-8")
               and "language_offer" in p.read_text(encoding="utf-8")]
    assert callers == [], callers
