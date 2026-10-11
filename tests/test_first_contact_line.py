"""The first-contact line (multilingual design 7.1): its words, in the
chat's language and naming it. Since phase 5 it is its own message, sent
once ever by the language launch (tests/test_language_launch.py), and no
longer part of the /start home screen (G47, G48)."""

from __future__ import annotations

import pytest

from aipager import i18n
from aipager.bot import language_menu
from aipager.i18n import persist

DM = 555


@pytest.mark.parametrize("code, name", [("ru", "Русский"), ("fa", "فارسی"), ("en", "English")])
def test_the_line_names_the_language_and_how_to_change_it(code, name):
    line = language_menu.first_contact_text(i18n.get(code))
    assert name in line and "/language" in line and line.lstrip("\u200f").startswith("🌐")


def test_the_home_screen_never_carries_it(mk_bot, run_async, mk_update):
    from unittest.mock import AsyncMock
    persist.ensure_launch_marker(people=(), prior_run=False)     # a new chat
    bot = mk_bot()
    bot._send_keyboard = AsyncMock()
    bot._app.bot.send_message = AsyncMock()
    update = mk_update("/start", chat_id=DM, user_id=DM)
    update.effective_user.language_code = "ru"
    run_async(bot._send_home(update))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text.startswith("👋")
    assert language_menu.first_contact_text(i18n.get("ru")) not in text
