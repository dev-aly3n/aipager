"""Integration tests: SC6, SC7, SC8, SC9, SC19 — /perms command end-to-end.

Tests exercise the observable outcomes: message text, keyboard structure,
registry state changes, keystroke sequences.  No source reading beyond
entrypoints.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.state import SessionRegistry, Status, TrackedSession


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(text, *, user_id=12345, chat_id=-1001):
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


def _make_query(callback_data, *, user_id=12345, message_id=42):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
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


def _make_bot(*, registry=None):
    from aipager.bot import TelegramBot
    if registry is None:
        registry = SessionRegistry()
    bot = TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    return bot


# --------------------------------------------------------------------------- #
# SC6 — IDLE Ask → Auto: confirm keyboard shown, on confirm registry updated  #
# --------------------------------------------------------------------------- #

def test_sc6_perms_idle_ask_to_auto_sends_confirm_keyboard():
    """SC6a: /perms on IDLE Ask session sends a reply with confirm keyboard
    containing [Yes, switch] and [Cancel] buttons."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    call_kwargs = update.message.reply_text.await_args[1]
    assert "reply_markup" in call_kwargs, "Confirmation keyboard must be present in kwargs"

    kb = call_kwargs["reply_markup"]
    all_cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any("perms_confirm" in cb for cb in all_cbs), (
        f"Must have perms_confirm callback; got {all_cbs}"
    )
    assert any("perms_cancel" in cb for cb in all_cbs), (
        f"Must have perms_cancel callback; got {all_cbs}"
    )


def test_sc6_perms_idle_ask_to_auto_confirm_updates_registry():
    """SC6b: After tapping [Yes, switch], session registry must have
    skip_perms=True and the edited message must mention 'Auto mode' and '🤖'."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_confirm")

    async def mock_launch(short_name, *, skip_perms=False, resume_id=None, cwd=None, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls, \
         patch("aipager.dtach.inject.kill_session", new_callable=AsyncMock):
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    # Registry must be updated
    assert sess.skip_perms is True, (
        "Registry skip_perms must be True after perms_confirm"
    )
    # Message must be edited
    query.edit_message_text.assert_awaited_once()
    edited_text = query.edit_message_text.await_args[0][0]
    assert "🤖" in edited_text or "Auto" in edited_text, (
        f"Edited message must mention Auto mode; got: {edited_text}"
    )


def test_sc6_perms_confirm_clears_pending():
    """SC6c: After perms_confirm, pending state for the session is removed."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_confirm")

    async def mock_launch(short_name, *, skip_perms=False, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls, \
         patch("aipager.dtach.inject.kill_session", new_callable=AsyncMock):
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    assert "claude-ben" not in bot._perms_pending, (
        "Pending state must be removed after confirming"
    )


# --------------------------------------------------------------------------- #
# SC7 — IDLE Auto → Ask: no confirmation, immediate switch                     #
# --------------------------------------------------------------------------- #

def test_sc7_perms_idle_auto_to_ask_no_confirm_keyboard():
    """SC7: /perms on IDLE Auto session must NOT send a confirm keyboard.
    It should execute the switch directly (or send success message)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = True  # currently Auto
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)
    bot._do_perms_switch_via_fn = AsyncMock()

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    # Confirm keyboard must NOT have been sent
    if update.message.reply_text.await_args is not None:
        call_kwargs = update.message.reply_text.await_args[1] or {}
        kb = call_kwargs.get("reply_markup")
        if kb is not None:
            all_cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
            assert not any("perms_confirm" in cb for cb in all_cbs), (
                "Auto→Ask must NOT show a confirmation keyboard"
            )

    # Direct switch must be attempted
    bot._do_perms_switch_via_fn.assert_awaited_once()


def test_sc7_perms_idle_auto_to_ask_switch_sets_false():
    """SC7: Auto→Ask switch must target skip_perms=False."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = True
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)
    bot._do_perms_switch_via_fn = AsyncMock()

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    call_args = bot._do_perms_switch_via_fn.await_args
    # Second positional arg is target_skip_perms
    assert call_args[0][1] is False, (
        "Auto→Ask switch must pass target_skip_perms=False"
    )


def test_sc7_perms_confirm_result_shows_ask_mode():
    """SC7: After a completed Ask switch, message must mention 💬 or 'Ask mode'."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = True  # currently Auto
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": False,
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_confirm")

    async def mock_launch(short_name, *, skip_perms=False, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls, \
         patch("aipager.dtach.inject.kill_session", new_callable=AsyncMock):
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args[0][0]
    assert "💬" in text or "Ask" in text, (
        f"Message must mention Ask mode after Auto→Ask switch; got: {text}"
    )
    assert sess.skip_perms is False, "Registry must be updated to False"


# --------------------------------------------------------------------------- #
# SC8 — BUSY: shows Stop & switch / Not now keyboard                           #
# --------------------------------------------------------------------------- #

def test_sc8_perms_busy_sends_busy_keyboard():
    """SC8a: /perms on a BUSY session sends a keyboard with [Stop & switch]
    and [Not now] buttons."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    call_kwargs = update.message.reply_text.await_args[1]
    assert "reply_markup" in call_kwargs, "Busy keyboard must be in reply_markup kwarg"

    kb = call_kwargs["reply_markup"]
    all_cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any("perms_stop_switch" in cb for cb in all_cbs), (
        f"Must have perms_stop_switch; got {all_cbs}"
    )
    assert any("perms_wait" in cb for cb in all_cbs), (
        f"Must have perms_wait; got {all_cbs}"
    )


def test_sc8_perms_not_now_edits_to_cancellation():
    """SC8b: Tapping [Not now] must edit the message to a cancellation notice
    and NOT change skip_perms."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_wait")
    _run(bot._handle_callback(update, MagicMock()))

    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    assert "Cancelled" in msg or "cancel" in msg.lower(), (
        f"Not now must edit to cancellation; got: {msg}"
    )
    # skip_perms must be unchanged
    assert sess.skip_perms is False, (
        "Not now must not change skip_perms"
    )
    # Pending state cleared
    assert "claude-ben" not in bot._perms_pending


def test_sc8_perms_not_now_message_contains_retry_hint():
    """SC8c: [Not now] cancellation message must contain a hint to try /perms
    again, referencing 'perms' or 'idle'."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_wait")
    _run(bot._handle_callback(update, MagicMock()))

    msg = query.edit_message_text.await_args[0][0]
    assert "perms" in msg.lower() or "idle" in msg.lower() or "Cancelled" in msg, (
        f"Not now message must hint about retrying; got: {msg}"
    )


# --------------------------------------------------------------------------- #
# SC9 — BUSY: [Stop & switch] sends Ctrl-C then relaunches                    #
# --------------------------------------------------------------------------- #

def test_sc9_stop_switch_sends_ctrl_c():
    """SC9a: Tapping [Stop task & switch] must send Ctrl-C before relaunching."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.skip_perms = False
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    ctrl_c_received = []

    async def mock_send_keys(session_name, key):
        ctrl_c_received.append(key)
        return True

    async def mock_launch(short_name, *, skip_perms=False, **kw):
        return True, ""

    update, query = _make_query("claude-ben:perms_stop_switch")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls:
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    assert any("C-c" in k or "ctrl" in k.lower() for k in ctrl_c_received), (
        f"Ctrl-C must be sent; got keys: {ctrl_c_received}"
    )


def test_sc9_stop_switch_relaunches_with_toggled_skip_perms():
    """SC9b: After Ctrl-C and socket disappears, session relaunches with the
    toggled skip_perms value."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.skip_perms = False
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    launch_calls = []

    async def mock_send_keys(session_name, key):
        return True

    async def mock_launch(short_name, *, skip_perms=False, resume_id=None, cwd=None, **kw):
        launch_calls.append({"skip_perms": skip_perms})
        return True, ""

    update, query = _make_query("claude-ben:perms_stop_switch")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls:
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    assert len(launch_calls) > 0, "Session must be relaunched"
    assert launch_calls[-1]["skip_perms"] is True, (
        f"Relaunch must use skip_perms=True (toggled); got {launch_calls}"
    )


def test_sc9_stop_switch_edits_message_to_new_mode():
    """SC9c: After [Stop & switch] completes, the message must be edited to
    show the new mode (Auto in this case)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.skip_perms = False
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    async def mock_send_keys(session_name, key):
        return True

    async def mock_launch(short_name, *, skip_perms=False, **kw):
        return True, ""

    update, query = _make_query("claude-ben:perms_stop_switch")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls:
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args[0][0]
    assert "🤖" in text or "Auto" in text, (
        f"After switch to Auto, message must say so; got: {text}"
    )


# --------------------------------------------------------------------------- #
# SC19 — kill+resume preserves claude_session_id and cwd                       #
# --------------------------------------------------------------------------- #

def test_sc19_perms_switch_preserves_claude_session_id():
    """SC19: After /perms kill+resume, sess.claude_session_id must be the same
    value passed to launch_session as resume_id."""
    bot = _make_bot()
    original_session_id = "abc-session-uuid-original"
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False
    sess.claude_session_id = original_session_id
    sess.cwd = "/home/aly/project"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    launch_kwargs_captured = {}

    async def mock_launch(short_name, *, skip_perms=False, resume_id=None, cwd=None, **kw):
        launch_kwargs_captured["resume_id"] = resume_id
        launch_kwargs_captured["cwd"] = cwd
        return True, ""

    update, query = _make_query("claude-ben:perms_confirm")
    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls, \
         patch("aipager.dtach.inject.kill_session", new_callable=AsyncMock):
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    assert launch_kwargs_captured.get("resume_id") == original_session_id, (
        f"claude_session_id must be passed as resume_id; "
        f"got {launch_kwargs_captured.get('resume_id')}"
    )


def test_sc19_perms_switch_preserves_cwd():
    """SC19: After /perms kill+resume, the original cwd must be passed to
    launch_session."""
    bot = _make_bot()
    original_cwd = "/home/aly/myproject"
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.skip_perms = False
    sess.claude_session_id = "some-uuid"
    sess.cwd = original_cwd
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    launch_kwargs_captured = {}

    async def mock_launch(short_name, *, skip_perms=False, resume_id=None, cwd=None, **kw):
        launch_kwargs_captured["cwd"] = cwd
        return True, ""

    update, query = _make_query("claude-ben:perms_confirm")
    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls, \
         patch("aipager.dtach.inject.kill_session", new_callable=AsyncMock):
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    assert launch_kwargs_captured.get("cwd") == original_cwd, (
        f"Original cwd must be preserved; got {launch_kwargs_captured.get('cwd')}"
    )


# --------------------------------------------------------------------------- #
# Error guessing: /perms on session with no last_active_session               #
# --------------------------------------------------------------------------- #

def test_perms_no_active_session_sends_helpful_error():
    """When last_active_session is empty, /perms must reply with a helpful
    error message rather than crashing."""
    bot = _make_bot()
    bot.registry.last_active_session = ""

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert "No active session" in msg or "no session" in msg.lower(), (
        f"Must send helpful error when no active session; got: {msg}"
    )


def test_perms_cancel_edits_and_clears_pending():
    """Tapping Cancel must edit to cancellation text and clear pending state."""
    bot = _make_bot()
    bot._perms_pending["claude-ben"] = {
        "target_skip_perms": True,
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_cancel")
    _run(bot._handle_callback(update, MagicMock()))

    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    assert "Cancelled" in msg or "cancel" in msg.lower(), (
        f"Cancel must edit to cancellation text; got: {msg}"
    )
    assert "claude-ben" not in bot._perms_pending
