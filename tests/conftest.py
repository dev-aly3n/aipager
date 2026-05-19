"""Shared pytest fixtures."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def tmp_state_file(tmp_path, monkeypatch):
    """Redirect SESSION_STATE_FILE so tests never touch the real one."""
    target = tmp_path / "sessions.json"
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", target)
    return target


@pytest.fixture
def run_async():
    """Run a coroutine to completion in a fresh event loop.

    Tests use this instead of `asyncio.run(...)` so that a single
    test file can interleave sync setup with `await` calls without
    inheriting an event loop from another fixture.
    """
    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)
    return _run


@pytest.fixture
def mk_bot():
    """Build a TelegramBot with mocked `_app` and `team=None` by default.

    `team=None` defeats the team-mode authz gate so handlers can be
    exercised directly without seeding an allow-list. Pass
    ``team=<Team>`` to test the team-mode path.
    """
    from aipager.bot import TelegramBot
    from aipager.state import SessionRegistry

    def _mk(registry=None, *, team=None):
        if registry is None:
            registry = SessionRegistry()
        bot = TelegramBot(registry)
        bot._app = MagicMock()
        bot._app.bot = MagicMock()
        bot._app.bot.send_message = AsyncMock()
        bot.team = team
        return bot
    return _mk


@pytest.fixture
def mk_update():
    """Build a mocked Telegram Update with sensible defaults.

    `text` becomes ``update.message.text``. `user_id` / `chat_id`
    populate ``effective_user`` / ``effective_chat`` so handlers that
    re-derive identity (team auth, mark_driver) work without extra
    wiring.
    """
    def _mk(text, *, message_id=999, user_id=12345, chat_id=-1001):
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = text
        update.message.message_id = message_id
        update.message.reply_text = AsyncMock()
        # Default to no reply target; tests that want one can set this
        # explicitly. Without this default, the auto-generated MagicMock
        # has non-string `.text` / `.caption` attributes that break any
        # handler that runs regex against them.
        update.message.reply_to_message = None
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        return update
    return _mk
