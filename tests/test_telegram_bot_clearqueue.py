"""Tests for `/clearqueue` (item 3.3) and `/kill` confirmation flow
(item 3.2)."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager.state import SessionRegistry, Status, TrackedSession


# ----- /clearqueue -----

def test_clearqueue_no_active_session(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    bot = mk_bot(registry)
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    assert "No active session" in update.message.reply_text.await_args.args[0]


def test_clearqueue_empty_queue(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = mk_bot(registry)
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args.args[0]
    assert "Nothing to clear" in msg
    assert "jim" in msg


def test_clearqueue_drops_entries(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("a", 100)
    sess.queue_prompt("b", 101)
    sess.queue_prompt("c", 102)
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = mk_bot(registry)
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    assert sess.pending_queue == []
    msg = update.message.reply_text.await_args.args[0]
    assert "Cleared 3" in msg
    assert "messages" in msg  # plural


def test_clearqueue_singular_message(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("only one", 100)
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = mk_bot(registry)
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Cleared 1 queued message " in msg  # singular, no trailing "s"


# ----- /kill confirmation flow -----

def test_kill_with_label_shows_confirmation(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/kill jim")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
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


def test_kill_unknown_session_friendly_error(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    # No sessions registered
    bot = mk_bot(registry)
    update = mk_update("/kill ghost")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Unknown" in msg or "gone" in msg


def test_kill_already_gone_session_friendly_error(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/kill jim")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Unknown" in msg or "gone" in msg
