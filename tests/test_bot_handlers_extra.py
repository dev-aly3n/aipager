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
    """The picker's buttons carry the short indexed form, not
    `{name}:kill` — no `{name}:<verb>` form can fit Telegram's 64-byte
    callback_data cap (design.md), so what must hold is the DESTINATION
    (which session a button reaches), not the literal encoded string."""
    from aipager.bot import session_parity

    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    update = mk_update("/kill")  # default chat_id=-1001
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    kb = update.message.reply_text.await_args.kwargs.get("reply_markup")
    assert kb is not None
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    dests = []
    for cb in cbs:
        _sentinel, kind, idx, verb = cb.split(":", 3)
        assert (_sentinel, kind) == ("_", "sx"), f"unexpected callback form: {cb!r}"
        resolved = session_parity._resolve_pref_index(bot, -1001, idx)
        dests.append((resolved.name if resolved is not None else None, verb))
    assert (sess.name, "kill") in dests


def test_handle_kill_unknown_label(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/kill nonexistent")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown" in text


# ===== /new error paths =================================================

def test_handle_new_no_name_starts_the_wizard(mk_bot, mk_update, run_async):
    """`/new` with no arguments used to print usage text. It now opens the
    interactive wizard — that is the whole point of the second entry
    point, so this test was inverted deliberately rather than deleted.

    `/new !name` and `/new name` are unaffected; the tests below still
    pin them.
    """
    bot = mk_bot()
    update = mk_update("/new")
    run_async(bot._handle_new_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Usage" not in text, "still printing the old usage text"
    # The wizard's first step asks what to call the session and offers a
    # way out. Asserted on intent, not on exact wording — copy is allowed
    # to improve without breaking this.
    assert "new session" in text.lower()
    assert "called" in text.lower() or "name" in text.lower()
    kb = update.message.reply_text.await_args.kwargs.get("reply_markup")
    assert kb is not None, "the wizard must offer buttons (at least Cancel)"


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
    """The Resume/Replace/Cancel buttons carry the short indexed form,
    not `{name}:<verb>` — no `{name}:<verb>` form can fit Telegram's
    64-byte callback_data cap (design.md), so what must hold is the
    DESTINATION (which session a button reaches), not the literal
    encoded string."""
    from aipager.bot import session_parity

    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess
    update = mk_update("")  # default chat_id=-1001
    run_async(bot._send_new_conflict_prompt(
        update=update, existing=sess, prompt="", skip_perms=False,
    ))
    text = update.message.reply_text.await_args.args[0]
    assert "already running" in text
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    dests = []
    for cb in cbs:
        _sentinel, kind, idx, verb = cb.split(":", 3)
        assert (_sentinel, kind) == ("_", "sx"), f"unexpected callback form: {cb!r}"
        resolved = session_parity._resolve_pref_index(bot, -1001, idx)
        dests.append((resolved.name if resolved is not None else None, verb))
    assert (sess.name, "new_resume") in dests
    assert (sess.name, "new_replace") in dests


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


# ===== /settings =========================================================

def test_handle_settings_cmd_opens_root_menu(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/settings", chat_id=555)
    run_async(bot._handle_settings_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert "Settings" in args[0]
    assert kwargs.get("parse_mode") == "HTML"
    assert kwargs.get("reply_markup") is not None


def test_handle_settings_cmd_available_to_read_only(mk_bot, mk_update, run_async):
    """Viewing /settings is never gated — even a read-only team member
    can open the menu (only value-set taps are gated, in callbacks.py)."""
    from aipager.team import Role, Rules, Team, User as TeamUser
    bot = mk_bot(team=Team(
        group_id=-100,
        users={7: TeamUser(id=7, label="ro", role=Role.READ_ONLY)},
        rules=Rules(deny_tools=[]),
    ))
    update = mk_update("/settings", user_id=7, chat_id=-100)
    run_async(bot._handle_settings_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
