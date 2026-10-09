"""The first-contact line (multilingual design 7.1) is built and wired
into the /start home screen, but off while Telegram-language detection is
(G47); when on, it shows once per private chat per run and never after a
choice (G48)."""

from __future__ import annotations

import dataclasses

import pytest

from aipager import i18n, preferences
from aipager.bot import language_menu
from aipager.i18n import locales

DM, OTHER, GROUP = 555, 556, -1001


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(language_menu, "_FIRST_CONTACT_SHOWN", set())
    for code in ("fa", "ru"):
        monkeypatch.setitem(locales.LOCALES, code,
                            dataclasses.replace(locales.LOCALES[code], enabled=True))


def test_no_line_while_detection_is_off():
    assert locales.AUTO_DETECT is False
    assert language_menu.first_contact_line(i18n.get("ru"), DM) == ""
    assert language_menu.first_contact_line(i18n.get("fa"), DM) == ""


def test_once_and_never_after_a_choice(monkeypatch):
    monkeypatch.setattr(locales, "AUTO_DETECT", True)
    ru = i18n.get("ru")
    line = language_menu.first_contact_line(ru, DM)
    assert "Русский" in line and "/language" in line
    assert language_menu.first_contact_line(ru, DM) == ""          # once
    preferences.set_ui_language(OTHER, "ru")
    assert language_menu.first_contact_line(ru, OTHER) == ""       # chose already
    assert language_menu.first_contact_line(ru, GROUP) == ""       # private only
    assert language_menu.first_contact_line(i18n.get("en"), 557) == ""


def test_the_home_screen_carries_it_only_when_on(mk_bot, run_async, mk_update, monkeypatch):
    from unittest.mock import AsyncMock
    bot = mk_bot()
    bot._send_keyboard = AsyncMock()
    bot._app.bot.send_message = AsyncMock()

    def home(language_code):
        update = mk_update("/start", chat_id=DM, user_id=DM)
        update.effective_user.language_code = language_code
        run_async(bot._send_home(update))
        return bot._app.bot.send_message.await_args.args[1]

    assert home("ru").startswith("👋 <b>aipager</b>")
    monkeypatch.setattr(locales, "AUTO_DETECT", True)
    text = home("ru")
    assert text.startswith(i18n.get("ru").html(language_menu.FIRST_CONTACT,
                                               language="Русский") + "\n\n👋")
    assert home("ru").startswith("👋")                              # once per run
