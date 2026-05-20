"""Additional handler tests covering /stop, /kill (no-arg), /new (errors),
and the _restart_daemon branches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


from aipager.state import Status, TrackedSession


# ===== /stop ============================================================

def test_handle_stop_no_active_session(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/stop")
    run_async(bot._handle_stop_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "No active session" in text


def test_handle_stop_session_not_busy(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    update = mk_update("/stop")
    run_async(bot._handle_stop_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "not busy" in text


def test_handle_stop_busy_invokes_stop(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    bot._stop_session = AsyncMock()
    update = mk_update("/stop")
    run_async(bot._handle_stop_cmd(update, MagicMock()))
    bot._stop_session.assert_awaited_once()


# ===== /kill (no arg) ===================================================

def test_handle_kill_no_arg_no_sessions(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/kill")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "No sessions to kill" in text


def test_handle_kill_no_arg_lists_sessions(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    update = mk_update("/kill")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    kb = update.message.reply_text.await_args.kwargs.get("reply_markup")
    assert kb is not None
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "claude-jim:kill" in cb


def test_handle_kill_unknown_label(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/kill nonexistent")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown" in text


# ===== /new error paths =================================================

def test_handle_new_no_name_shows_usage(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/new")
    run_async(bot._handle_new_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Usage" in text


def test_handle_new_empty_after_bang_warns(mk_bot, mk_update, run_async):
    """`/new !` → name is empty after stripping `!`."""
    bot = mk_bot()
    update = mk_update("/new !")
    run_async(bot._handle_new_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "empty" in text.lower()


def test_handle_new_launch_failure(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    update = mk_update("/new newsess")
    update.message.reply_text = AsyncMock(return_value=status_msg)
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(False, "dtach unavailable")))
    run_async(bot._handle_new_cmd(update, MagicMock()))
    # Status message edited with the error
    text = status_msg.edit_text.await_args.args[0]
    assert "dtach unavailable" in text


# ===== _send_new_conflict_prompt =======================================

def test_send_new_conflict_prompt_alive_session(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    update = mk_update("")
    run_async(bot._send_new_conflict_prompt(
        update=update, existing=sess, prompt="", skip_perms=False,
    ))
    text = update.message.reply_text.await_args.args[0]
    assert "already running" in text
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "claude-jim:new_resume" in cb
    assert "claude-jim:new_replace" in cb


def test_send_new_conflict_prompt_gone_with_preview(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID"
    sess.last_assistant_preview = "what I did"
    update = mk_update("")
    run_async(bot._send_new_conflict_prompt(
        update=update, existing=sess, prompt="go", skip_perms=False,
    ))
    text = update.message.reply_text.await_args.args[0]
    assert "previously used" in text
    assert "what I did" in text


# ===== _handle_clearqueue ===============================================

def test_handle_clearqueue_no_active(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "No active session" in text


def test_handle_clearqueue_unknown_session(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot.registry.last_active_session = "claude-vanished"
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "not found" in text


def test_handle_clearqueue_empty(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Nothing to clear" in text


def test_handle_clearqueue_drops_entries(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.queue_prompt("a", 1)
    sess.queue_prompt("b", 2)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    assert sess.pending_queue == []


# ===== _handle_message reply-target paths ==============================

def test_handle_message_reply_to_session_by_last_msg_id(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_msg_id = 300
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("text")
    # reply_to.message_id matches sess.last_msg_id
    update.message.reply_to_message = MagicMock(
        message_id=300, text="(old)", caption=None,
    )
    run_async(bot._handle_message(update, MagicMock()))
    assert sess.status == Status.BUSY


def test_handle_message_reply_to_guessed_from_text(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("text")
    # Unknown message_id; falls back to guessing from reply text
    update.message.reply_to_message = MagicMock(
        message_id=999999, text="⚙️ jim · Working", caption=None,
    )
    run_async(bot._handle_message(update, MagicMock()))
    assert sess.status == Status.BUSY


# ===== _send_template / _send_command corners ==========================

def test_send_template_no_active_warns(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("Continue")
    run_async(bot._send_template(update, "Continue"))
    text = update.message.reply_text.await_args.args[0]
    assert "No active session" in text


def test_send_template_dead_session(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update = mk_update("Continue")
    run_async(bot._send_template(update, "Continue"))
    text = update.message.reply_text.await_args.args[0]
    assert "not found" in text


def test_send_template_send_failure(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update = mk_update("Continue")
    run_async(bot._send_template(update, "Continue"))
    text = update.message.reply_text.await_args.args[0]
    assert "Failed to send" in text


def test_send_command_clear_during_busy_refused(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    text = update.message.reply_text.await_args.args[0]
    assert "Can't clear" in text


def test_send_command_model_change_acks(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._react = AsyncMock()
    update = mk_update("/model opus")
    run_async(bot._send_command(update, "/model opus"))
    text = update.message.reply_text.await_args.args[0]
    assert "opus" in text


def test_send_command_send_failure(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update = mk_update("/compact")
    run_async(bot._send_command(update, "/compact"))
    text = update.message.reply_text.await_args.args[0]
    assert "Failed to send" in text


# ===== _direct_send corners ============================================

def test_direct_send_dead_session(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update = mk_update("")
    run_async(bot._direct_send(update, "jim", "do thing"))
    text = update.message.reply_text.await_args.args[0]
    assert "not alive" in text


def test_direct_send_auto_discovers_unregistered_session(mk_bot, mk_update, run_async, monkeypatch):
    """If the label isn't in the registry but the socket exists, create
    the registry entry and send."""
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("")
    run_async(bot._direct_send(update, "discovered", "hello"))
    assert bot.registry.get("claude-discovered") is not None


def test_direct_send_send_failure(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update = mk_update("")
    run_async(bot._direct_send(update, "jim", "fail"))
    text = update.message.reply_text.await_args.args[0]
    assert "Failed to send" in text
