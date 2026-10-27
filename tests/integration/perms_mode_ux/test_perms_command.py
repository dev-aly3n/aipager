"""Integration tests: SC6, SC7, SC8, SC9, SC19 — /perms command end-to-end.

Tests exercise the observable outcomes: message text, keyboard structure,
registry state changes, keystroke sequences.  No source reading beyond
entrypoints.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


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
# SC6 — IDLE Ask → Full access: confirm keyboard shown, on confirm registry updated  #
# --------------------------------------------------------------------------- #

def test_sc6_perms_idle_ask_to_auto_sends_confirm_keyboard():
    """SC6a: /perms on IDLE Ask session sends a reply with confirm keyboard
    containing [Yes, switch] and [Cancel] buttons."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)

    update = _make_update("/perms full")
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
    """SC6b: After tapping [Yes, switch] (into Full access, a restart), the
    registry must be in Full access and the edited message must say so."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.perm_mode = "ask"
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_confirm")

    async def mock_launch(short_name, *, mode, resume_id=None, cwd=None, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls, \
         patch("aipager.dtach.inject.kill_session", new_callable=AsyncMock):
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    # Registry must be updated
    assert sess.perm_mode == "full", (
        "Registry must be in Full access after perms_confirm"
    )
    # Message must be edited
    # The card changes in place: "Switching…" (buttons gone), then the
    # /mode card for the new mode with the opposite switch (2026-10-01).
    edits = query.edit_message_text.await_args_list
    assert [c.args[0] for c in edits] == [
        "⚙️ Switching <b>ben</b> to 🔓 Full access mode (the session restarts)…",
        "<b>ben</b> is in 🔓 Full access mode."]
    kb = edits[-1].kwargs["reply_markup"]
    assert [b.text for row in kb.inline_keyboard for b in row] == [
        "💬 Switch to Ask mode", "🤖 Switch to Auto mode"]


def test_sc6_perms_confirm_clears_pending():
    """SC6c: After perms_confirm, pending state for the session is removed."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_confirm")

    async def mock_launch(short_name, *, mode, **kw):
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
# SC7 — IDLE Full access → Ask: no confirmation, immediate switch                     #
# --------------------------------------------------------------------------- #

def test_sc7_perms_idle_auto_to_ask_no_confirm_keyboard():
    """SC7: /perms on IDLE Auto session must NOT send a confirm keyboard.
    It should execute the switch directly (or send success message)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.perm_mode = "full"  # currently Auto
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)
    bot._do_perms_switch_via_fn = AsyncMock()

    update = _make_update("/perms ask")
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
    """SC7: Full access→Ask switch must target Ask."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.perm_mode = "full"
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)
    bot._do_perms_switch_via_fn = AsyncMock()

    update = _make_update("/perms ask")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    call_args = bot._do_perms_switch_via_fn.await_args
    # Second positional arg is the target mode
    assert call_args[0][1] == "ask", (
        "Full access→Ask switch must pass the target mode 'ask'"
    )


def test_sc7_perms_confirm_result_shows_ask_mode():
    """SC7: After a completed Ask switch, message must mention 💬 or 'Ask mode'."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.IDLE)
    sess.perm_mode = "full"  # currently Auto
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "ask",
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_confirm")

    async def mock_launch(short_name, *, mode, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("aipager.bot.callbacks.Path") as mock_path_cls, \
         patch("aipager.dtach.inject.kill_session", new_callable=AsyncMock):
        mock_path_cls.return_value.is_socket.return_value = False
        _run(bot._handle_callback(update, MagicMock()))

    text = query.edit_message_text.await_args[0][0]
    assert "💬" in text or "Ask" in text, (
        f"Message must mention Ask mode after Auto→Ask switch; got: {text}"
    )
    assert sess.perm_mode == "ask", "Registry must be updated to False"


# --------------------------------------------------------------------------- #
# SC8 — BUSY: shows Stop & switch / Not now keyboard                           #
# --------------------------------------------------------------------------- #

def test_sc8_perms_busy_sends_busy_keyboard():
    """SC8a: /perms on a BUSY session sends a keyboard with [Stop & switch]
    and [Not now] buttons."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)

    update = _make_update("/perms full")
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
    and NOT change the mode."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_wait")
    _run(bot._handle_callback(update, MagicMock()))

    # Not now: back to the /mode card for the mode it is in (2026-10-01).
    query.edit_message_text.assert_awaited_once()
    msg = query.edit_message_text.await_args[0][0]
    assert msg == "<b>ben</b> is in 💬 Ask mode.", msg
    # The mode must be unchanged
    assert sess.perm_mode == "ask", (
        "Not now must not change the mode"
    )
    # Pending state cleared
    assert "claude-ben" not in bot._perms_pending


def test_sc8_perms_not_now_message_contains_retry_hint():
    """SC8c: [Not now] cancellation message must contain a hint to try /perms
    again, referencing 'perms' or 'idle'."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }

    update, query = _make_query("claude-ben:perms_wait")
    _run(bot._handle_callback(update, MagicMock()))

    # The way to retry is the card's own switch.
    kb = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert [b.text for row in kb.inline_keyboard for b in row] == [
        "🤖 Switch to Auto mode", "🔓 Switch to Full access mode"]


# --------------------------------------------------------------------------- #
# SC9 — BUSY: [Stop & switch] interrupts, kills, then relaunches            #
# --------------------------------------------------------------------------- #

def _dead_on_kill(monkeypatch, events=None):
    """The busy session's terminal (real ``inject.send_keys`` into a fake
    dtach), a kill that ends it, and no real waits."""
    from aipager.dtach import inject
    events = [] if events is None else events

    async def _run_dtach(args, stdin=b"", timeout=5):
        events.append(("write", bytes(stdin)))
        return True, ""

    async def _kill(name):
        events.append(("kill", name))
        return True

    async def _no_sleep(_secs):
        pass

    monkeypatch.setattr(inject, "_run", _run_dtach)
    monkeypatch.setattr(inject, "kill_session", _kill)
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    monkeypatch.setattr("aipager.bot.session_ops.Path", MagicMock(
        return_value=MagicMock(is_socket=MagicMock(return_value=False))))
    return events


def test_sc9_stop_switch_interrupts_with_escape(monkeypatch):
    """SC9a: Tapping [Stop task & switch] interrupts the turn with two
    Escapes (no text typed: "C-c" used to land in the input box), then
    kills the session before relaunching (roadmap 8.137)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.perm_mode = "ask"
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/alice"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }
    events = _dead_on_kill(monkeypatch)

    async def mock_launch(short_name, *, mode, **kw):
        events.append(("launch", mode))
        return True, ""

    update, query = _make_query("claude-ben:perms_stop_switch")
    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch):
        _run(bot._handle_callback(update, MagicMock()))

    assert events == [("write", b"\x1b"), ("write", b"\x1b"),
                      ("kill", "claude-ben"), ("launch", "full")], events


def test_sc9_stop_switch_relaunches_with_the_target_mode(monkeypatch):
    """SC9b: After the interrupt, the kill and the socket gone, session relaunches with the
    target mode (Full access)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.perm_mode = "ask"
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }

    launch_calls = []

    _dead_on_kill(monkeypatch)

    async def mock_launch(short_name, *, mode, resume_id=None, cwd=None, **kw):
        launch_calls.append({"mode": mode})
        return True, ""

    update, query = _make_query("claude-ben:perms_stop_switch")
    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch):
        _run(bot._handle_callback(update, MagicMock()))

    assert len(launch_calls) > 0, "Session must be relaunched"
    assert launch_calls[-1]["mode"] == "full", (
        f"Relaunch must use Full access; got {launch_calls}"
    )


def test_sc9_stop_switch_edits_message_to_new_mode(monkeypatch):
    """SC9c: After [Stop & switch] completes, the message must be edited to
    show the new mode (Full access in this case)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.BUSY)
    sess.perm_mode = "ask"
    sess.claude_session_id = "uuid-001"
    sess.cwd = "/home/aly"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }

    _dead_on_kill(monkeypatch)

    async def mock_launch(short_name, *, mode, **kw):
        return True, ""

    update, query = _make_query("claude-ben:perms_stop_switch")
    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch):
        _run(bot._handle_callback(update, MagicMock()))

    text = query.edit_message_text.await_args[0][0]
    assert "🔓 Full access mode" in text, (
        f"After switch to Full access, message must say so; got: {text}"
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
    sess.perm_mode = "ask"
    sess.claude_session_id = original_session_id
    sess.cwd = "/home/aly/project"
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }

    launch_kwargs_captured = {}

    async def mock_launch(short_name, *, mode, resume_id=None, cwd=None, **kw):
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
    sess.perm_mode = "ask"
    sess.claude_session_id = "some-uuid"
    sess.cwd = original_cwd
    bot.registry._sessions["claude-ben"] = sess
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
        "msg_id": 42,
        "label": "ben",
    }

    launch_kwargs_captured = {}

    async def mock_launch(short_name, *, mode, resume_id=None, cwd=None, **kw):
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
    assert msg == "No live sessions here. Start one with /new.", (
        f"Must send helpful error when no active session; got: {msg}"
    )


def test_perms_cancel_edits_and_clears_pending():
    """Tapping Cancel must edit to cancellation text and clear pending state."""
    bot = _make_bot()
    bot._perms_pending["claude-ben"] = {
        "target_mode": "full",
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


# --------------------------------------------------------------------------- #
# UNKNOWN status: /perms must reply "still initializing" and NOT switch mode  #
# (new guard added in iter 2 — rev-iter1-004)                                 #
# --------------------------------------------------------------------------- #

def test_perms_unknown_status_sends_initializing_message():
    """/perms on a session with Status.UNKNOWN must reply with a message
    indicating the session is still initializing (not switch modes)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.UNKNOWN)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)

    update = _make_update("/perms full")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert "initializing" in msg.lower() or "initializ" in msg.lower(), (
        f"Must mention initializing; got: {msg}"
    )


def test_perms_unknown_status_does_not_switch_mode():
    """/perms on a session with Status.UNKNOWN must NOT change the mode."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.UNKNOWN)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-ben"] = sess
    bot.registry.last_active_session = "claude-ben"
    bot._is_admin = MagicMock(return_value=True)

    update = _make_update("/perms")
    _run(bot._handle_perms_cmd(update, MagicMock()))

    assert sess.perm_mode == "ask", (
        "UNKNOWN-status /perms must not change the mode; "
        f"got {sess.perm_mode}"
    )
    # Pending dict must not be populated either
    assert "claude-ben" not in bot._perms_pending, (
        "UNKNOWN-status /perms must not enqueue a pending switch"
    )
