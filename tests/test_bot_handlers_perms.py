"""Tests for _handle_perms_cmd in CommandHandlersMixin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def mk_query():
    """Build a mocked Telegram CallbackQuery."""
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        return update, query
    return _mk


# ---- No active session ------------------------------------------------------

def test_perms_no_active_session_replies_error(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot.registry.last_active_session = ""
    update = mk_update("/perms")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert msg == "No live sessions here. Start one with /new."


def test_perms_gone_session_replies_error(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.GONE)
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    update = mk_update("/perms")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert msg == "No live sessions here. Start one with /new."


# ---- Into Full access: requires admin + shows confirm keyboard ------------

def _dev(bot, mode, status=Status.IDLE):
    sess = TrackedSession(name="claude-dev", label="dev", status=status)
    sess.perm_mode = mode
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    return sess


@pytest.mark.parametrize("current", ["ask", "auto"])
def test_perms_full_non_admin_denied(mk_bot, mk_update, run_async, current):
    """Non-admin trying to switch to Full access gets an error."""
    bot = mk_bot()
    sess = _dev(bot, current)
    # May not use Full access here
    bot._may_use_full_access = MagicMock(return_value=False)
    update = mk_update("/perms full")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert msg == "Full access mode needs an admin."
    assert sess.perm_mode == current and "claude-dev" not in bot._perms_pending


def test_perms_full_admin_shows_confirm_keyboard(mk_bot, mk_update, run_async):
    """Admin switching to Full access gets a confirmation that names the
    restart and the flag."""
    bot = mk_bot()
    _dev(bot, "ask")
    bot._is_admin = MagicMock(return_value=True)
    update = mk_update("/perms full")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert "restarts the session" in text
    assert "<code>--dangerously-skip-permissions</code>" in text
    call_kwargs = update.message.reply_text.await_args[1]
    assert "reply_markup" in call_kwargs
    assert bot._perms_pending["claude-dev"]["target_mode"] == "full"


# ---- Out of Full access: executes directly (no confirmation) ---------------

@pytest.mark.parametrize("target", ["ask", "auto"])
def test_perms_out_of_full_access_executes_directly(mk_bot, mk_update, run_async, target):
    """Leaving Full access doesn't require confirmation (nor an admin)."""
    bot = mk_bot()
    _dev(bot, "full")
    bot._is_admin = MagicMock(return_value=False)
    bot._do_perms_switch_via_fn = AsyncMock()
    update = mk_update(f"/perms {target}")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    bot._do_perms_switch_via_fn.assert_awaited_once()
    assert bot._do_perms_switch_via_fn.await_args[0][1] == target
    assert "(the session restarts)" in update.message.reply_text.await_args[0][0]


# ---- Ask <-> Auto: in place, for anyone, no card in between ---------------

@pytest.mark.parametrize("current, target, presses", [
    ("ask", "auto", 3), ("auto", "ask", 1)], ids=["ask-to-auto", "auto-to-ask"])
@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY], ids=["idle", "busy"])
def test_perms_ask_auto_switches_in_place(mk_bot, mk_update, run_async, monkeypatch,
                                          current, target, presses, status):
    bot = mk_bot()
    sess = _dev(bot, current, status)
    bot._is_admin = MagicMock(return_value=False)
    bot._kill_and_relaunch_core = AsyncMock(side_effect=AssertionError("no restart"))
    press = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", press)
    reply = MagicMock(message_id=901)
    update = mk_update(f"/perms {target}", chat_id=12345)
    update.message.reply_text = AsyncMock(return_value=reply)
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    press.assert_awaited_once()
    assert press.await_args.args == ("claude-dev", presses)
    assert "claude-dev" not in bot._perms_pending          # no busy/confirm card
    text = update.message.reply_text.await_args[0][0]
    assert "without a restart" in text
    assert "Waiting for Claude Code to confirm the switch." in text
    assert sess.perm_mode == target
    assert sess.mode_switch.reply == (12345, 901, "mode_card")


def test_perms_ask_auto_refused_while_a_prompt_is_open(mk_bot, mk_update, run_async,
                                                       monkeypatch):
    bot = mk_bot()
    sess = _dev(bot, "ask", Status.INTERACTIVE)
    press = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", press)
    update = mk_update("/perms auto")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    press.assert_not_awaited()
    assert sess.perm_mode == "ask" and sess.mode_switch is None
    assert update.message.reply_text.await_args[0][0] == (
        "⚠️ <b>dev</b> is waiting on a question or permission prompt. Answer it first, "
        "then switch the mode.")


# ---- BUSY: shows Stop & switch / Not now keyboard (restarts only) ----------

def test_perms_busy_to_full_shows_busy_keyboard(mk_bot, mk_update, run_async):
    """A switch into Full access on a BUSY session gets Stop task & switch
    / Not now."""
    bot = mk_bot()
    _dev(bot, "auto", Status.BUSY)
    bot._is_admin = MagicMock(return_value=True)
    update = mk_update("/perms full")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    assert "The session restarts." in update.message.reply_text.await_args[0][0]
    call_kwargs = update.message.reply_text.await_args[1]
    assert "reply_markup" in call_kwargs
    assert bot._perms_pending["claude-dev"]["target_mode"] == "full"


def test_perms_busy_out_of_full_access_non_admin_ok(mk_bot, mk_update, run_async):
    """Leaving Full access on a BUSY session is allowed for non-admins."""
    bot = mk_bot()
    _dev(bot, "full", Status.BUSY)
    bot._is_admin = MagicMock(return_value=False)
    update = mk_update("/perms ask")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    call_text = update.message.reply_text.await_args[0][0]
    assert "needs an admin" not in call_text
    assert bot._perms_pending["claude-dev"]["target_mode"] == "ask"


# ---- bare /perms (= /mode, 2026-09-30): the mode and the other two ---------

@pytest.mark.parametrize("mode, label, buttons", [
    ("ask", "💬 Ask mode", ["🤖 Switch to Auto mode", "🔓 Switch to Full access mode"]),
    ("auto", "🤖 Auto mode", ["💬 Switch to Ask mode", "🔓 Switch to Full access mode"]),
    ("full", "🔓 Full access mode", ["💬 Switch to Ask mode", "🤖 Switch to Auto mode"]),
], ids=["ask", "auto", "full"])
def test_bare_perms_shows_the_mode_and_switches_nothing(mk_bot, mk_update, run_async,
                                                       mode, label, buttons):
    """It used to flip the mode blind; now it says what the mode is and
    offers the other two."""
    bot = mk_bot()
    _dev(bot, mode)
    bot._do_perms_switch_via_fn = AsyncMock()
    # A DM: in a group (mk_update's default -1001) the card marks no
    # target, since each member has their own (8.90).
    update = mk_update("/perms", chat_id=12345)

    run_async(bot._handle_perms_cmd(update, MagicMock()))

    bot._do_perms_switch_via_fn.assert_not_awaited()
    text = update.message.reply_text.await_args[0][0]
    kb = update.message.reply_text.await_args[1]["reply_markup"]
    assert text == f"✍️ <b>dev</b> is in {label}."
    assert [b.text for row in kb.inline_keyboard for b in row] == buttons

