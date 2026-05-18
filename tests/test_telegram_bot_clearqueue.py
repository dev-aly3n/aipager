"""Tests for `/clearqueue` (item 3.3) and `/kill` confirmation flow
(item 3.2)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from aipager import telegram_bot as tb
from aipager.state import SessionRegistry, Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _mk_bot(registry):
    bot = tb.TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    return bot


def _mk_update(text):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


# ----- /clearqueue -----

def test_clearqueue_no_active_session(monkeypatch):
    registry = SessionRegistry()
    bot = _mk_bot(registry)
    update = _mk_update("/clearqueue")
    _run(bot._handle_clearqueue_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    assert "No active session" in update.message.reply_text.await_args.args[0]


def test_clearqueue_empty_queue(monkeypatch):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = _mk_bot(registry)
    update = _mk_update("/clearqueue")
    _run(bot._handle_clearqueue_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args.args[0]
    assert "Nothing to clear" in msg
    assert "jim" in msg


def test_clearqueue_drops_entries(monkeypatch):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("a", 100)
    sess.queue_prompt("b", 101)
    sess.queue_prompt("c", 102)
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = _mk_bot(registry)
    update = _mk_update("/clearqueue")
    _run(bot._handle_clearqueue_cmd(update, MagicMock()))
    assert sess.pending_queue == []
    msg = update.message.reply_text.await_args.args[0]
    assert "Cleared 3" in msg
    assert "messages" in msg  # plural


def test_clearqueue_singular_message():
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("only one", 100)
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = _mk_bot(registry)
    update = _mk_update("/clearqueue")
    _run(bot._handle_clearqueue_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Cleared 1 queued message " in msg  # singular, no trailing "s"


# ----- /kill confirmation flow -----

def test_kill_with_label_shows_confirmation(monkeypatch):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    bot = _mk_bot(registry)
    update = _mk_update("/kill jim")
    _run(bot._handle_kill_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    text = call.args[0] if call.args else call.kwargs.get("text", "")
    # Should ask for confirmation, not kill immediately
    assert "Kill session" in text
    keyboard = call.kwargs.get("reply_markup")
    assert keyboard is not None
    # Inspect the inline keyboard buttons
    buttons = keyboard.inline_keyboard[0]
    cb_data = [b.callback_data for b in buttons]
    assert "claude-jim:kill-confirm" in cb_data
    assert "claude-jim:kill-cancel" in cb_data


def test_kill_unknown_session_friendly_error(monkeypatch):
    registry = SessionRegistry()
    # No sessions registered
    bot = _mk_bot(registry)
    update = _mk_update("/kill ghost")
    _run(bot._handle_kill_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Unknown" in msg or "gone" in msg


def test_kill_already_gone_session_friendly_error():
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    registry._sessions["claude-jim"] = sess
    bot = _mk_bot(registry)
    update = _mk_update("/kill jim")
    _run(bot._handle_kill_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Unknown" in msg or "gone" in msg
