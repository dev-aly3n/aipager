"""Tests for perms-mode callback actions and allow_always keystroke injection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

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
        query.message.chat = MagicMock()
        query.message.chat.id = -100
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        update.effective_chat = MagicMock()
        update.effective_chat.id = -100
        return update, query
    return _mk


# ---- allow_always: Down + Down + Enter keystroke sequence ------------------

def test_allow_always_sends_down_down_enter(mk_bot, mk_query, run_async):
    """allow_always action must inject Down, Down, Enter with 0.1s delays."""
    from aipager.state import Status, TrackedSession
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-dev"] = sess
    update, query = mk_query("claude-dev:allow_always")

    key_calls = []

    async def mock_send_keys(session_name, key):
        key_calls.append(key)
        return True

    async def mock_is_alive(name):
        return True

    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive):
        run_async(bot._handle_callback(update, MagicMock()))

    assert key_calls == ["Down", "Down", "Enter"], (
        f"Expected ['Down', 'Down', 'Enter'], got {key_calls}"
    )


# ---- perms_confirm: executes mode switch -----------------------------------

def test_perms_confirm_calls_do_perms_switch(mk_bot, mk_query, run_async):
    """Tapping 'Yes, switch' confirms the perms switch."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE)
    sess.skip_perms = False
    bot.registry._sessions["claude-dev"] = sess
    # Set up pending state
    bot._perms_pending["claude-dev"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "dev",
    }
    bot._do_perms_switch_via_fn = AsyncMock()
    update, query = mk_query("claude-dev:perms_confirm")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_perms_switch_via_fn.assert_awaited_once()
    # Pending state should be cleared
    assert "claude-dev" not in bot._perms_pending


# ---- perms_cancel: edits message to Cancelled ------------------------------

def test_perms_cancel_edits_to_cancelled(mk_bot, mk_query, run_async):
    """Tapping Cancel edits message to cancelled notice."""
    bot = mk_bot()
    bot._perms_pending["claude-dev"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "dev",
    }
    update, query = mk_query("claude-dev:perms_cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    assert "Cancelled" in msg
    assert "claude-dev" not in bot._perms_pending


# ---- perms_wait: cancels and shows retry hint ------------------------------

def test_perms_wait_edits_to_retry_hint(mk_bot, mk_query, run_async):
    """Tapping 'Not now' edits message to a try-again hint."""
    bot = mk_bot()
    bot._perms_pending["claude-dev"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "dev",
    }
    update, query = mk_query("claude-dev:perms_wait")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    # Should contain hint to retry later
    assert "perms" in msg.lower() or "Cancelled" in msg
    assert "claude-dev" not in bot._perms_pending


# ---- resume_mode_ask: calls _do_resume with skip_perms_override=False ------

def test_resume_mode_ask_calls_do_resume_false(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._resume_mode_pending["claude-dev"] = "dev"
    bot._do_resume = AsyncMock()
    update, query = mk_query("claude-dev:resume_mode_ask")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()
    kwargs = bot._do_resume.await_args[1]
    assert kwargs["skip_perms_override"] is False
    assert kwargs["label"] == "dev"
    assert "claude-dev" not in bot._resume_mode_pending


# ---- resume_mode_auto: calls _do_resume with skip_perms_override=True ------

def test_resume_mode_auto_calls_do_resume_true(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._resume_mode_pending["claude-dev"] = "dev"
    bot._do_resume = AsyncMock()
    update, query = mk_query("claude-dev:resume_mode_auto")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()
    kwargs = bot._do_resume.await_args[1]
    assert kwargs["skip_perms_override"] is True
    assert kwargs["label"] == "dev"
    assert "claude-dev" not in bot._resume_mode_pending


# ---- resume_mode_cancel: edits to Cancelled --------------------------------

def test_resume_mode_cancel_edits_to_cancelled(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._resume_mode_pending["claude-dev"] = "dev"
    update, query = mk_query("claude-dev:resume_mode_cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    assert "Cancelled" in msg
    assert "claude-dev" not in bot._resume_mode_pending


# ---- perms_stop_switch: sends Ctrl-C then relaunches -----------------------

def test_perms_stop_switch_sends_ctrl_c(mk_bot, mk_query, run_async):
    """Tapping 'Stop task & switch' sends Ctrl-C and then relaunches."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.BUSY)
    sess.skip_perms = False
    sess.claude_session_id = "some-uuid"
    sess.cwd = "/home/user/project"
    bot.registry._sessions["claude-dev"] = sess
    bot._perms_pending["claude-dev"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "dev",
    }

    ctrl_c_calls = []
    launch_calls = []

    async def mock_send_keys(session_name, key):
        ctrl_c_calls.append(key)
        return True

    async def mock_launch_session(short_name, *, skip_perms=False, **kw):
        launch_calls.append({"short_name": short_name, "skip_perms": skip_perms})
        return True, ""

    update, query = mk_query("claude-dev:perms_stop_switch")

    from pathlib import Path

    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.launch_session", side_effect=mock_launch_session), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls:
        # Make the socket appear gone immediately
        mock_path_cls.return_value.is_socket.return_value = False
        run_async(bot._handle_callback(update, MagicMock()))

    # Ctrl-C should have been sent
    assert "C-c" in ctrl_c_calls
    # Launch should have been called with skip_perms=True
    assert any(c["skip_perms"] is True for c in launch_calls), launch_calls
    # Session should be updated
    assert sess.skip_perms is True
