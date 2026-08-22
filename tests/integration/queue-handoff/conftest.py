"""Shared fixtures for the queue-handoff black-box integration tests.

Independent of the Developer's own adapted unit tests
(``tests/test_hold_prompt_during_open_dialog.py``,
``tests/test_bot_session_ops.py``, ``tests/test_bot_notify.py``,
``tests/test_telegram_bot_clearqueue.py``) — this suite drives only the
surface documented in ``entrypoints.md``: ``TelegramBot`` handler
methods, with the dtach layer mocked at the boundary, asserting on what
reached the mocked pty, what reaction was set, and what
``TrackedSession``/``StopOutcome`` state resulted.

Note: this directory's name (``queue-handoff``) is not a valid Python
identifier, so test modules here cannot ``from .conftest import ...`` —
pytest still auto-discovers this file as a conftest regardless. Shared
constants (``CHAT_ID = -1001``, session name ``"claude-x"``) are
therefore redefined locally in each test module, matching the
convention already used by ``tests/test_hold_prompt_during_open_dialog.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def wired(mk_bot, monkeypatch):
    """A bot with a live-looking session named ``claude-x``; pty writes
    captured, not performed; reactions/messages captured via a blanket
    AsyncMock Telegram client. ``_react`` is stubbed out — tests that
    care about the actual reaction emoji use ``wired_reactions``
    instead, which leaves ``_react`` real and only mocks the Telegram
    API boundary.

    Returns ``(bot, sess, injected, keys)``.
    """
    bot = mk_bot()
    bot._app.bot = AsyncMock()

    injected: list[str] = []
    keys: list[str] = []

    async def _send_text_and_enter(name, body):
        injected.append(body)
        return True

    async def _send_keys(name, key):
        keys.append(key)
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        _send_text_and_enter)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)

    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()

    sess = bot.registry.get_or_create("claude-x")
    sess.label = "x"
    bot.registry.last_active_session = "claude-x"

    return bot, sess, injected, keys


@pytest.fixture
def wired_reactions(wired):
    """Same wiring as ``wired``, but ``_react`` is real — reactions
    surface through the mocked ``set_message_reaction`` boundary, per
    entrypoints.md's documented observable ("Reactions, via mocked
    bot._app.bot.set_message_reaction")."""
    bot, sess, injected, keys = wired
    # `wired` stubbed `_react` on the instance; deleting the instance
    # attribute exposes the class's real bound method again.
    del bot._react
    return bot, sess, injected, keys
