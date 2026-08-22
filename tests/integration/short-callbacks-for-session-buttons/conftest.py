"""Shared black-box fixtures for the short-callbacks-for-session-buttons
suite.

Black-box only: every helper here calls the PUBLIC surface listed in
entrypoints.md — ``TelegramBot._build_*`` keyboard builders (explicitly
listed as "Exported functions"), ``TelegramBot._handle_callback`` (the
dispatcher the whole feature is about; exercised the same way the real
``CallbackQueryHandler`` registration reaches it, and the same way this
repo's OTHER test suites already exercise it — see
tests/test_telegram_bot_callback.py and
tests/integration/sync-commands-with-mini-app/conftest.py on ``main``),
and ``session_parity.session_cb`` / ``resolve_short_cb`` (both
exported). Nothing here imports or asserts on
``keyboards.KeyboardMixin._make_cb`` or any of the "NOT exported"
internals entrypoints.md lists.

Root ``tests/conftest.py`` already autouse-blocks real Telegram HTTP,
the real credential probe, real dtach spawning, and redirects every
real-home path to ``tmp_path`` — nothing here needs to repeat that.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


def upgrade_bot_api_mocks(bot):
    """Several surfaces this feature touches (the /new conflict prompt,
    the /kill confirm step, /perms) edit a message via
    ``bot._app.bot.edit_message_text`` / ``send_message`` directly,
    cross-turn, rather than only replying on ``update.message``. The
    shared root ``mk_bot`` fixture only upgrades ``send_message`` to an
    ``AsyncMock``; awaiting a bare ``MagicMock`` raises a ``TypeError``
    that a broad ``try/except`` in the handler can swallow, silently
    hiding a real assertion target. Upgrade every Bot-API method this
    suite touches up front."""
    api = bot._app.bot
    for name in (
        "send_message", "edit_message_text", "edit_message_reply_markup",
        "delete_message", "send_document", "answer_callback_query",
        "set_my_commands",
    ):
        setattr(api, name, AsyncMock())
    return bot


@pytest.fixture
def scb_bot(mk_bot):
    """A TelegramBot ready for this suite's black-box calls."""
    def _mk(**kw):
        bot = mk_bot(**kw)
        return upgrade_bot_api_mocks(bot)
    return _mk


def make_message_update(text, *, user_id=12345, chat_id=-100,
                          chat_type="group", message_id=999):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = message_id
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    update.message.reply_to_message = None
    update.message.quote = None
    update.effective_message = update.message
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    return update


def make_callback_update(callback_data, *, user_id=12345, chat_id=-100,
                           chat_type="private", message_id=42, text=""):
    """Build an ``Update`` carrying a ``CallbackQuery`` with the given
    ``callback_data`` — the black-box way to simulate "a button, already
    sitting in the chat, gets tapped" for EITHER grammar form
    (entrypoints.md's short ``_:sx:<idx>:<verb>`` or long
    ``<session_name>:<verb>``): the tester builds the raw string
    directly rather than relying on any renderer."""
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = text
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.message.chat.type = chat_type
    query.message.reply_text = AsyncMock()
    query.message.edit_text = AsyncMock()
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = query.message.chat
    update.effective_message = query.message
    return update, query


def callback_data_in(markup):
    """Every ``callback_data`` string present in an InlineKeyboardMarkup,
    row-major. ``[]`` for ``None``."""
    if markup is None:
        return []
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data is not None
    ]


def make_session(name, label, *, status=Status.IDLE, scope_chat_id=-100,
                  **kw):
    return TrackedSession(name=name, label=label, status=status,
                           scope_chat_id=scope_chat_id, **kw)


class _Helpers:
    upgrade_bot_api_mocks = staticmethod(upgrade_bot_api_mocks)
    make_message_update = staticmethod(make_message_update)
    make_callback_update = staticmethod(make_callback_update)
    callback_data_in = staticmethod(callback_data_in)
    make_session = staticmethod(make_session)


@pytest.fixture
def helpers():
    """This directory's name has hyphens, so it isn't a valid Python
    package for a relative import — bundle every helper above through
    one fixture, matching the convention this repo's other hyphenated
    ``tests/integration/*`` suites already use."""
    return _Helpers()
