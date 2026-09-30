"""Integration tests: SC10, SC11 - the /new reply (the Ready card).

Since the 2026-09-30 redesign every /new ends in the Ready card: the mode,
model and folder the session got, where the next message goes, and a
one-tap switch to the other mode (it replaced the "/perms" nudge).

SC10: an Ask session (the chat's default set to Ask) shows 💬 Ask, the real
      folder, "Default model" when none was chosen, and a Switch to Auto
      button.
SC11: /new !ben shows 🤖 Auto and a Switch to Ask button.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager import preferences
from aipager.state import SessionRegistry


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(text, *, user_id=12345, chat_id=0):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = 999
    update.message.reply_text = AsyncMock()
    update.message.reply_to_message = None
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update


def _make_bot(*, registry=None):
    from aipager.bot import TelegramBot
    if registry is None:
        registry = SessionRegistry()
    bot = TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot._app.bot.edit_message_text = AsyncMock()
    bot.team = None
    bot.scopes = None
    return bot


async def _launch_ok(*a, **kw):
    return True, ""


def _ready(text_cmd, *, ask_default=False):
    if ask_default:
        preferences.set_new_session_default(0, "mode", "ask")
    bot = _make_bot()
    update = _make_update(text_cmd)
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))
    bot._app.bot.edit_message_text.assert_awaited_once()
    kw = bot._app.bot.edit_message_text.await_args.kwargs
    buttons = [b.text for row in kw["reply_markup"].inline_keyboard for b in row]
    return kw["text"], buttons


# --------------------------------------------------------------------------- #
# SC10 - an Ask session                                                       #
# --------------------------------------------------------------------------- #

def test_sc10_new_ask_reply_says_ask():
    text, _ = _ready("/new ben", ask_default=True)
    assert "💬 Ask" in text, text


def test_sc10_new_ask_reply_offers_auto_instead_of_a_perms_nudge():
    text, buttons = _ready("/new ben", ask_default=True)
    assert "🤖 Switch to Auto" in buttons, buttons
    assert "/perms" not in text


def test_sc10_new_ask_reply_contains_cwd():
    from aipager.dtach import inject
    text, _ = _ready("/new ben", ask_default=True)
    assert inject._PROJECT_DIR[-20:] in text, text


def test_sc10_new_ask_model_reads_default_when_unknown():
    text, _ = _ready("/new ben", ask_default=True)
    assert "🧠 Default model" in text, text


def test_sc10_the_reply_says_where_the_next_message_goes():
    text, _ = _ready("/new ben", ask_default=True)
    assert "✍️ Just send a message, it goes to ben." in text, text


# --------------------------------------------------------------------------- #
# SC11 - an Auto session                                                      #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("cmd", ["/new !ben", "/new ben"])
def test_sc11_new_auto_reply_says_auto(cmd):
    text, buttons = _ready(cmd)
    assert "🤖 Auto" in text, text
    assert "💬" not in text, text
    assert "💬 Switch to Ask" in buttons, buttons
