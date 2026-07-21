"""Tests for _handle_perms_cmd in CommandHandlersMixin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
    assert "No active session" in msg


def test_perms_gone_session_replies_error(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.GONE)
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    update = mk_update("/perms")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert "No active session" in msg


# ---- IDLE Ask→Auto: requires admin + shows confirm keyboard ----------------

def test_perms_idle_ask_to_auto_non_admin_denied(mk_bot, mk_update, run_async):
    """Non-admin trying to switch to Auto mode gets an error."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE)
    sess.skip_perms = False  # currently Ask
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    # Make _is_admin return False
    bot._is_admin = MagicMock(return_value=False)
    update = mk_update("/perms")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert "requires admin role" in msg


def test_perms_idle_ask_to_auto_admin_shows_confirm_keyboard(mk_bot, mk_update, run_async):
    """Admin switching Ask→Auto gets a confirmation keyboard."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE)
    sess.skip_perms = False  # currently Ask
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    bot._is_admin = MagicMock(return_value=True)
    update = mk_update("/perms")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    # Keyboard must be present
    call_kwargs = update.message.reply_text.await_args[1]
    assert "reply_markup" in call_kwargs
    # Pending state should be stored
    assert "claude-dev" in bot._perms_pending
    assert bot._perms_pending["claude-dev"]["target_skip_perms"] is True


# ---- IDLE Auto→Ask: executes directly (no confirmation) --------------------

def test_perms_idle_auto_to_ask_executes_directly(mk_bot, mk_update, run_async):
    """Auto→Ask switch doesn't require confirmation."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE)
    sess.skip_perms = True  # currently Auto
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    bot._is_admin = MagicMock(return_value=True)
    bot._do_perms_switch_via_fn = AsyncMock()
    update = mk_update("/perms")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    # Should call _do_perms_switch_via_fn with target_skip_perms=False
    bot._do_perms_switch_via_fn.assert_awaited_once()
    call_args = bot._do_perms_switch_via_fn.await_args
    assert call_args[0][1] is False  # target_skip_perms=False


# ---- BUSY: shows Stop & switch / Not now keyboard --------------------------

def test_perms_busy_shows_busy_keyboard(mk_bot, mk_update, run_async):
    """BUSY session gets Stop task & switch / Not now keyboard."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.BUSY)
    sess.skip_perms = False  # Ask → switching to Auto
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    bot._is_admin = MagicMock(return_value=True)
    update = mk_update("/perms")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    call_kwargs = update.message.reply_text.await_args[1]
    assert "reply_markup" in call_kwargs
    # Pending state stored
    assert "claude-dev" in bot._perms_pending


def test_perms_busy_to_ask_non_admin_ok(mk_bot, mk_update, run_async):
    """Switching BUSY Auto→Ask should be allowed even for non-admins (going to safer mode)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.BUSY)
    sess.skip_perms = True  # Auto → switching to Ask
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    bot._is_admin = MagicMock(return_value=False)
    update = mk_update("/perms")
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    # Should NOT be denied (target is Ask, not Auto)
    call_text = update.message.reply_text.await_args[0][0]
    assert "requires admin" not in call_text
