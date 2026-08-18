"""Black-box tests for design.md success criteria 2 and 3: `TelegramBot
.notify(sess, "compacting", {...})`'s send-vs-edit decision, per
entrypoints.md's `TelegramBot.notify` contract and the `mk_bot` fixture
(`tests/conftest.py`).

Criterion 2: a session with an existing busy card gets that SAME message
edited (no new `send_message` call), and `busy_msg_id` keeps returning
the same id.

Criterion 3: a session with no live card gets exactly one new message
sent, and `busy_msg_id` returns its id.

Telegram I/O is mocked at `bot._app.bot.{send_message,edit_message_text}`
(the `mk_bot` fixture's own boundary) -- no real network call, matching
the project's established `test_bot_notify.py` convention for this exact
event.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status, TrackedSession


def _sess(label="jim", *, status=Status.BUSY, busy_msg_id=None):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    s.scope_kind = "dm"
    s.scope_chat_id = 12345
    return s


# ===== Criterion 2: existing busy card -> edit in place ===================

def test_compacting_with_existing_busy_card_edits_same_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.edit_message_text.assert_awaited_once()
    call = bot._app.bot.edit_message_text.await_args
    assert call.kwargs.get("message_id") == 42


def test_compacting_with_existing_busy_card_sends_no_new_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.send_message.assert_not_awaited()


def test_compacting_with_existing_busy_card_keeps_same_busy_msg_id(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    assert sess.busy_msg_id == 42


def test_compacting_with_existing_busy_card_pushes_compacting_kind(mk_bot, run_async):
    """The compatibility scalar (busy_msg_id) is unchanged, but the stack
    underneath must now report the compacting layer as the top kind --
    the whole point of the feature."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    assert sess.stack_top_kind() == "compacting"


# ===== Criterion 3: no live card -> send exactly one new message ==========

def test_compacting_with_no_busy_card_sends_exactly_one_new_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.send_message.assert_awaited_once()


def test_compacting_with_no_busy_card_busy_msg_id_becomes_new_id(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    assert sess.busy_msg_id == 555


def test_compacting_with_no_busy_card_pushes_compacting_kind(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    assert sess.stack_top_kind() == "compacting"


def test_compacting_with_no_busy_card_does_not_edit_anything(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.edit_message_text.assert_not_awaited()
