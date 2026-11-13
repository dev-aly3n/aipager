"""Clear conversation: the one ``/clear`` path (the Commands keyboard's
Clear, the ⋮ menu's confirm and the Mini App's), the ⋮ menu row and its
confirm card, their gates, and what stays out of Telegram's ``/`` menu.

Nothing here reaches Claude: typing is the ``inject.send_text_and_enter``
seam (or ``bot._inject_prompt``), liveness ``inject.is_alive``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import reactions
from aipager.bot.transport import PROMPT_DIALOG, PROMPT_REFUSED
from aipager.state import Status, TrackedSession


# ---- helpers -------------------------------------------------------------

def _live(bot, *, name="claude-dev", label="dev", status=Status.IDLE,
          target=True) -> TrackedSession:
    sess = TrackedSession(name=name, label=label, status=status)
    bot.registry._sessions[name] = sess
    if target:
        bot.registry.last_active_session = name
    return sess


@pytest.fixture
def typed(monkeypatch):
    """What reaches Claude: (session name, text) per typed message, with the
    session alive."""
    calls: list[tuple[str, str]] = []

    async def _send(name, text, *a, **kw):
        calls.append((name, text))
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", _send)
    return calls


def _core(bot, sess, run_async, **kw):
    kw.setdefault("driver_user_id", 12345)
    kw.setdefault("chat_id", None)
    return run_async(bot._clear_session_core(sess, **kw))


# ---- the shared core -----------------------------------------------------

def test_core_types_clear_once_into_an_idle_session(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    outcome = _core(bot, sess, run_async)
    assert (outcome.ok, outcome.reason, outcome.label) == (True, "sent", "dev")
    assert typed == [("claude-dev", "/clear")]


def test_core_gone_is_not_live(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot, status=Status.GONE)
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "not_live"
    assert typed == []


def test_core_dead_socket_is_missing(mk_bot, run_async, typed, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    bot = mk_bot()
    sess = _live(bot)
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "missing"
    assert typed == []


def test_core_busy_refused(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot, status=Status.BUSY)
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "busy"
    assert typed == []


def test_core_busy_refused_even_with_a_hold(mk_bot, run_async, typed):
    """The keyboard's hold would queue it; a busy session refuses first."""
    bot = mk_bot()
    sess = _live(bot, status=Status.BUSY)
    hold = AsyncMock(return_value=True)
    outcome = _core(bot, sess, run_async, hold=hold)
    assert outcome.reason == "busy"
    hold.assert_not_awaited()
    assert typed == []


def test_core_without_hold_refuses_open_dialog(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot, status=Status.INTERACTIVE)
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "prompt_open"
    assert typed == []
    assert sess.pending_queue == []


def test_core_without_hold_refuses_background_agents(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    sess.job_continuation_active = True
    assert sess.job_background_open()
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "waiting"
    assert typed == []


def test_core_without_hold_refuses_model_hold(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    sess.model_hold = True
    assert sess.queue_prompt("hello", 7)
    assert sess.model_hold_active()
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "waiting"
    assert typed == []


def test_core_without_hold_refuses_other_senders_note(mk_bot, run_async, typed,
                                                      monkeypatch):
    monkeypatch.setattr("aipager.policy_snapshot.outstanding_sender_keys",
                        lambda name, **kw: {(0, 4242)})
    bot = mk_bot()
    sess = _live(bot)
    outcome = _core(bot, sess, run_async, driver_user_id=12345)
    assert outcome.reason == "waiting"
    assert typed == []


def test_core_without_hold_takes_the_senders_own_note(mk_bot, run_async, typed,
                                                      monkeypatch):
    """Control for the test above: the sender's own outstanding note does
    not hold their clear."""
    monkeypatch.setattr("aipager.policy_snapshot.outstanding_sender_keys",
                        lambda name, **kw: {(0, 12345)})
    bot = mk_bot()
    sess = _live(bot)
    outcome = _core(bot, sess, run_async, driver_user_id=12345)
    assert outcome.reason == "sent"


def test_core_parked_refused(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    sess.parked_job = "job-1"
    outcome = _core(bot, sess, run_async)
    assert outcome.reason == "parked"
    assert typed == []


def test_core_parked_after_typing_failed_is_parked(mk_bot, run_async, monkeypatch):
    """Parked between the checks and the typing: said as parked, not as
    a send failure."""
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)

    async def _inject(s, text, **kw):
        s.parked_job = "job-1"
        return False

    bot._inject_prompt = _inject
    assert _core(bot, sess, run_async).reason == "parked"


def test_core_maps_refused_role_to_needs_admin(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(return_value=PROMPT_REFUSED)
    assert _core(bot, sess, run_async).reason == "needs_admin"


def test_core_dialog_met_while_typing_is_prompt_open(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(return_value=PROMPT_DIALOG)
    assert _core(bot, sess, run_async).reason == "prompt_open"


def test_core_typing_failed_is_send_failed(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(return_value=False)
    assert _core(bot, sess, run_async).reason == "send_failed"


def test_core_passes_the_sender_and_chat_to_the_typing(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(return_value=True)
    _core(bot, sess, run_async, driver_user_id=77, chat_id=-100, msg_id=5)
    bot._inject_prompt.assert_awaited_once_with(
        sess, "/clear", msg_id=5, chat_id=-100, driver_user_id=77)


# ---- Commands -> Clear (the keyboard), unchanged to the person ------------

def test_keyboard_clear_types_clear_and_says_where(mk_bot, mk_update, run_async, typed):
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    bot._react = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert typed == [("claude-jim", "/clear")]
    assert update.message.reply_text.await_args.args[0] == "🧹 /clear sent to jim"
    bot._react.assert_awaited_once_with(update, reactions.ACK)


def test_keyboard_clear_busy_refused(mk_bot, mk_update, run_async, typed):
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim", status=Status.BUSY)
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert typed == []
    assert update.message.reply_text.await_args.args[0] == (
        "⚠️ Can't clear while session is busy")


def test_keyboard_clear_dead_socket(mk_bot, mk_update, run_async, typed, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert typed == []
    assert update.message.reply_text.await_args.args[0] == (
        "⚠️ Session 'claude-jim' not found")


def test_keyboard_clear_with_dialog_open_is_held_not_typed(
        mk_bot, mk_update, run_async, typed):
    bot = mk_bot()
    sess = _live(bot, name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot._react = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert typed == []
    assert [entry[0] for entry in sess.pending_queue] == ["/clear"]
    bot._react.assert_awaited_with(update, reactions.HANDED_OFF)


def test_keyboard_clear_meeting_dialog_is_held(mk_bot, mk_update, run_async, monkeypatch):
    """A dialog that opens while it is typed: held for it, as before."""
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot, name="claude-jim", label="jim")
    bot._inject_prompt = AsyncMock(return_value=PROMPT_DIALOG)
    bot._hold_met_dialog = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    bot._hold_met_dialog.assert_awaited_once_with(update, sess, "/clear")


def test_keyboard_clear_refused_role_needs_admin(mk_bot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    bot._inject_prompt = AsyncMock(return_value=PROMPT_REFUSED)
    bot._react = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert update.message.reply_text.await_args.args[0] == "That command needs an admin."


def test_keyboard_clear_typing_failed(mk_bot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    bot._inject_prompt = AsyncMock(return_value=False)
    update = mk_update("/clear")
    run_async(bot._send_command(update, "/clear"))
    assert update.message.reply_text.await_args.args[0] == "❌ Failed to send to [jim]"


def test_keyboard_other_command_keeps_its_glyph(mk_bot, mk_update, run_async, typed):
    bot = mk_bot()
    _live(bot, name="claude-jim", label="jim")
    bot._react = AsyncMock()
    update = mk_update("/cost")
    run_async(bot._send_command(update, "/cost"))
    assert update.message.reply_text.await_args.args[0] == "↪️ /cost sent to jim"


# ---- a typed "/clear" stays what it was ------------------------------------

def test_typed_clear_types_nothing_into_a_live_session(mk_bot, mk_update, run_async,
                                                      monkeypatch):
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(side_effect=lambda name: name == "claude-x1"))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    bot = mk_bot()
    _live(bot, name="claude-x1", label="x1")
    bot._react = AsyncMock()
    update = mk_update("/clear")
    run_async(bot._handle_message(update, MagicMock()))
    sent.assert_not_awaited()
    assert update.message.reply_text.await_args.args[0] == "⚠️ Unknown session: clear"


# ---- never in Telegram's "/" menu; never a session name -------------------

@pytest.mark.parametrize("group", [False, True], ids=["private", "group"])
@pytest.mark.parametrize("labels", [set(), {"x1", "frontend"}], ids=["none", "two"])
def test_no_bot_command_clears_a_conversation(labels, group, monkeypatch):
    """One tap on a `/` menu entry must never wipe a conversation: no
    `clear` (nor `clearqueue`), no `reset`, under any name."""
    from aipager.bot.lifecycle import LifecycleMixin

    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    names = [c.command for c in LifecycleMixin._command_list(labels, group=group)]
    assert names, "the menu itself must not be empty"
    assert not [n for n in names if "clear" in n or n == "reset"], names


@pytest.mark.parametrize("name", ["clear", "reset", "Clear", "RESET"])
def test_clear_and_reset_are_reserved_session_names(name):
    from aipager.dtach.inject import _RESERVED
    from aipager.miniapp.launch import validate_session_name

    assert {"clear", "reset"} <= _RESERVED
    clean, err = validate_session_name(name)
    assert clean == ""
    assert err


def test_a_name_that_only_starts_with_clear_is_still_free():
    from aipager.miniapp.launch import validate_session_name

    assert validate_session_name("clearview") == ("clearview", "")


# ---- the ⋮ menu row and its confirm (personal mode, a private chat) -------

DM_CHAT = 5
CONFIRM_TEXT = (
    "🧹 Clear the conversation in <b>dev</b>?\n"
    "Claude starts a new conversation and forgets this one.\n\n"
    "The old conversation stays saved on the computer, but it can't be resumed from here.")


def _query(data="", *, user_id=12345, message_id=42):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user_id
    query.from_user.username = None
    return query


def _cb_update(chat_id, user_id):
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def _dm_session(bot, status=Status.IDLE, chat=DM_CHAT) -> TrackedSession:
    sess = _live(bot, status=status)
    sess.scope_chat_id = chat
    return sess


def _press(bot, run_async, sess, verb, *, chat=DM_CHAT, user_id=12345):
    from aipager.bot import session_parity

    query = _query(user_id=user_id)
    handled = run_async(session_parity.handle_callback(
        bot, _cb_update(chat, user_id), query, sess.name, verb))
    return handled, query


def _buttons(query):
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    return [btn for row in markup.inline_keyboard for btn in row]


def _toast_list(query):
    return [c.args[0] if c.args else None for c in query.answer.await_args_list]


def _read_only_bot(mk_bot):
    from aipager.team import Role, Team
    from aipager.team import User as TeamUser

    return mk_bot(team=Team(group_id=0, users={
        999: TeamUser(id=999, label="ro", role=Role.READ_ONLY),
        12345: TeamUser(id=12345, label="me", role=Role.ADMIN)}))


def test_menu_live_session_has_clear_after_end(mk_bot, run_async):
    bot = mk_bot()
    sess = _dm_session(bot)
    _, query = _press(bot, run_async, sess, "menu")
    buttons = _buttons(query)
    assert [b.text for b in buttons] == [
        "🔄 Restart", "⏹ End session", "🧹 Clear conversation", "✏️ Rename",
        "👤 Preferences", "📝 Diff", "✖️ Close"]
    assert buttons[2].callback_data.endswith(":clear")


def test_menu_gone_session_has_no_clear(mk_bot, run_async):
    bot = mk_bot()
    sess = _dm_session(bot, status=Status.GONE)
    sess.claude_session_id = "abc"
    _, query = _press(bot, run_async, sess, "menu")
    buttons = _buttons(query)
    assert [b.text for b in buttons][0] == "▶️ Resume"
    assert not [b for b in buttons if b.callback_data.endswith(":clear")]
    assert "🧹 Clear conversation" not in [b.text for b in buttons]


def test_clear_tap_draws_the_confirm(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _dm_session(bot)
    sess.turn_key = 7
    handled, query = _press(bot, run_async, sess, "clear")
    assert handled is True
    assert query.edit_message_text.await_args.args[0] == CONFIRM_TEXT
    assert "Resume" not in query.edit_message_text.await_args.args[0]
    buttons = _buttons(query)
    assert [b.text for b in buttons] == ["🧹 Clear", "Cancel"]
    assert buttons[0].callback_data.endswith(":clearok7")
    assert buttons[1].callback_data.endswith(":clear-cancel")
    assert typed == []


def test_clear_tap_refused_for_read_only(mk_bot, run_async, typed):
    bot = _read_only_bot(mk_bot)
    sess = _dm_session(bot)
    _, query = _press(bot, run_async, sess, "clear", user_id=999)
    assert _toast_list(query) == ["You can't clear this session's conversation."]
    query.edit_message_text.assert_not_awaited()


def test_clear_tap_from_another_chat_refused(mk_bot, run_async):
    bot = mk_bot()
    sess = _dm_session(bot, chat=6)
    _, query = _press(bot, run_async, sess, "clear")
    assert _toast_list(query) == ["That session isn't running here."]
    query.edit_message_text.assert_not_awaited()


def test_clear_tap_on_ended_session_draws_nothing(mk_bot, run_async):
    bot = mk_bot()
    sess = _dm_session(bot, status=Status.GONE)
    _, query = _press(bot, run_async, sess, "clear")
    assert _toast_list(query) == ["That session has ended."]
    # The menu again (the gone layout), never the confirm.
    assert query.edit_message_text.await_args.args[0].startswith("⋮ <b>dev</b>")
    assert not [b for b in _buttons(query) if "clearok" in b.callback_data]


def test_clear_tap_on_busy_session_draws_nothing(mk_bot, run_async):
    bot = mk_bot()
    sess = _dm_session(bot, status=Status.BUSY)
    _, query = _press(bot, run_async, sess, "clear")
    assert _toast_list(query) == ["⚠️ Can't clear while session is busy"]
    query.edit_message_text.assert_not_awaited()


def test_clear_tap_refused_when_role_may_not_send_clear(mk_bot, run_async):
    bot = mk_bot()
    sess = _dm_session(bot)
    bot._command_needs_admin = MagicMock(return_value=True)
    _, query = _press(bot, run_async, sess, "clear")
    query.answer.assert_awaited_once_with("That command needs an admin.", show_alert=True)
    query.edit_message_text.assert_not_awaited()
    assert bot._command_needs_admin.call_args.args[:2] == ("/clear", 12345)


def test_clear_confirm_types_clear_once_and_says_so(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _dm_session(bot)
    _, query = _press(bot, run_async, sess, f"clearok{sess.turn_key}")
    assert typed == [("claude-dev", "/clear")]
    query.edit_message_text.assert_awaited_once()
    assert query.edit_message_text.await_args.args[0] == "🧹 /clear sent to dev"
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert _toast_list(query) == [None]


def test_clear_confirm_refused_for_read_only(mk_bot, run_async, typed):
    bot = _read_only_bot(mk_bot)
    sess = _dm_session(bot)
    _, query = _press(bot, run_async, sess, f"clearok{sess.turn_key}", user_id=999)
    assert _toast_list(query) == ["You can't clear this session's conversation."]
    assert typed == []
    query.edit_message_text.assert_not_awaited()


def test_clear_confirm_from_another_chat_types_nothing(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _dm_session(bot, chat=6)
    _, query = _press(bot, run_async, sess, f"clearok{sess.turn_key}")
    assert _toast_list(query) == ["That session isn't running here."]
    assert typed == []


def test_clear_confirm_during_resume_types_nothing(mk_bot, run_async, typed):
    import time

    bot = mk_bot()
    sess = _dm_session(bot)
    sess.resuming_until = time.monotonic() + 60
    _, query = _press(bot, run_async, sess, f"clearok{sess.turn_key}")
    assert _toast_list(query) == ["dev is being resumed - try again in a moment"]
    assert typed == []
    query.edit_message_text.assert_not_awaited()


def test_clear_confirm_after_session_ended_types_nothing(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _dm_session(bot)
    turn = sess.turn_key
    sess.status = Status.GONE
    _, query = _press(bot, run_async, sess, f"clearok{turn}")
    assert _toast_list(query) == ["That session has ended."]
    assert typed == []
    assert query.edit_message_text.await_args.args[0].startswith("⋮ <b>dev</b>")


def test_clear_confirm_after_new_turn_types_nothing(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _dm_session(bot)
    turn = sess.turn_key
    bot.registry.transition(sess.name, Status.BUSY)
    bot.registry.transition(sess.name, Status.IDLE)
    assert sess.turn_key != turn
    _, query = _press(bot, run_async, sess, f"clearok{turn}")
    assert _toast_list(query) == [
        "dev changed since this was shown - tap 🧹 Clear conversation again to clear it."]
    assert typed == []
    assert query.edit_message_text.await_args.args[0].startswith("⋮ <b>dev</b>")


def test_clear_confirm_after_already_cleared_types_nothing(mk_bot, run_async, typed):
    """A /clear passes the session through GONE (its SessionEnd), which
    moves the turn on: a second tap on the same confirm clears nothing."""
    bot = mk_bot()
    sess = _dm_session(bot)
    turn = sess.turn_key
    _press(bot, run_async, sess, f"clearok{turn}")
    assert typed == [("claude-dev", "/clear")]
    bot.registry.transition(sess.name, Status.GONE)
    bot.registry.transition(sess.name, Status.IDLE)
    _, query = _press(bot, run_async, sess, f"clearok{turn}")
    assert _toast_list(query) == [
        "dev changed since this was shown - tap 🧹 Clear conversation again to clear it."]
    assert typed == [("claude-dev", "/clear")]


def test_clear_confirm_on_busy_session_types_nothing(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _dm_session(bot)
    turn = sess.turn_key
    sess.status = Status.BUSY       # the same turn: no new turn key
    _, query = _press(bot, run_async, sess, f"clearok{turn}")
    assert _toast_list(query) == ["⚠️ Can't clear while session is busy"]
    assert typed == []
    assert query.edit_message_text.await_args.args[0].startswith("⋮ <b>dev</b>")


@pytest.mark.parametrize("setup, toast", [
    ("dialog", "Answer the open prompt first, then clear the conversation."),
    ("agents", "Claude is still finishing its work - clear the conversation when it's done."),
    ("parked", "Bring dev back from Claude Code's background first."),
], ids=["dialog", "agents", "parked"])
def test_clear_confirm_refusals_keep_the_card(mk_bot, run_async, typed, setup, toast):
    bot = mk_bot()
    sess = _dm_session(bot)
    turn = sess.turn_key
    if setup == "dialog":
        sess.prompt_wait_carry = True
    elif setup == "agents":
        sess.job_continuation_active = True
    else:
        sess.parked_job = "job-1"
    _, query = _press(bot, run_async, sess, f"clearok{turn}")
    assert _toast_list(query) == [toast]
    assert typed == []
    query.edit_message_text.assert_not_awaited()


def test_clear_confirm_refused_role_is_an_alert(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _dm_session(bot)
    bot._inject_prompt = AsyncMock(return_value=PROMPT_REFUSED)
    _, query = _press(bot, run_async, sess, f"clearok{sess.turn_key}")
    query.answer.assert_awaited_once_with("That command needs an admin.", show_alert=True)
    query.edit_message_text.assert_not_awaited()


def test_clear_confirm_socket_gone(mk_bot, run_async, typed, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    bot = mk_bot()
    sess = _dm_session(bot)
    _, query = _press(bot, run_async, sess, f"clearok{sess.turn_key}")
    assert _toast_list(query) == ["dev was not found"]
    assert typed == []
    assert query.edit_message_text.await_args.args[0].startswith("⋮ <b>dev</b>")


def test_clear_confirm_typing_failed(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _dm_session(bot)
    bot._inject_prompt = AsyncMock(return_value=False)
    _, query = _press(bot, run_async, sess, f"clearok{sess.turn_key}")
    assert _toast_list(query) == ["Couldn't clear dev - try again."]
    query.edit_message_text.assert_not_awaited()


def test_clear_cancel(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _dm_session(bot)
    _, query = _press(bot, run_async, sess, "clear-cancel")
    assert query.edit_message_text.await_args.args[0] == "Clear cancelled for [<b>dev</b>]."
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert typed == []


@pytest.mark.parametrize("verb", ["clearok", "clearokX", "clearok7a", "clearok-1"])
def test_clearok_without_digits_is_not_handled(mk_bot, run_async, typed, verb):
    bot = mk_bot()
    sess = _dm_session(bot)
    handled, query = _press(bot, run_async, sess, verb)
    assert handled is False
    assert typed == []
    query.answer.assert_not_awaited()


@pytest.mark.parametrize("verb", ["clearok", "clearokX", "clearok7a"])
def test_clearok_without_digits_is_not_claimed_for_an_unknown_session(mk_bot, run_async, verb):
    """Not a Clear confirm at all: left to the other dispatchers (False),
    never answered "Session not found" here."""
    from aipager.bot import session_parity

    bot = mk_bot()
    query = _query()
    handled = run_async(session_parity.handle_callback(
        bot, _cb_update(DM_CHAT, 12345), query, "claude-nope", verb))
    assert handled is False
    query.answer.assert_not_awaited()


def test_clearok_with_digits_is_claimed_for_an_unknown_session(mk_bot, run_async):
    """Control for the test above: a real confirm verb is this module's."""
    from aipager.bot import session_parity

    bot = mk_bot()
    query = _query()
    handled = run_async(session_parity.handle_callback(
        bot, _cb_update(DM_CHAT, 12345), query, "claude-nope", "clearok7"))
    assert handled is True
    assert _toast_list(query) == ["Session not found"]


# ---- gates and card ownership in a group -----------------------------------

from tests.test_group_attribution_senders import (  # noqa: E402
    ALICE, BOB, G, X1, _cb, _edit_texts, _registry, _run_tap, _scopes, _sess, _toasts,
)

READER = 66


def _wire_group(bot, r, monkeypatch):
    from aipager.dtach import inject
    from aipager.policy import load_policy

    bot.policy = load_policy()
    monkeypatch.setattr(inject, "is_alive",
                        AsyncMock(side_effect=lambda name: name in r._sessions))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot.refresh_pinned = AsyncMock()
    return bot


@pytest.fixture
def cbot(mk_bot, monkeypatch):
    """Group G in scope mode (alice owner, bob user, a read_only reader)."""
    from dataclasses import replace

    from aipager.scope import Member

    scopes = _scopes()
    scopes[0] = replace(scopes[0], members=scopes[0].members + (
        Member(id=READER, label="reader", role="read_only"),))
    r = _registry()
    return _wire_group(mk_bot(r, scopes=scopes), r, monkeypatch)


def _clear_card(bot, run_async, user=ALICE, message_id=900):
    q = _run_tap(bot, run_async, _cb(bot, G, X1, "clear"), user, message_id=message_id)
    assert _edit_texts(q)[0].startswith("🧹 Clear the conversation in <b>x1</b>?")
    return message_id


def test_clear_tap_in_group_claims_the_card(cbot, run_async):
    card = _clear_card(cbot, run_async)
    rec = cbot.registry.card_owners[(G, card)]
    assert (rec["user_id"], rec["kind"], rec["command"]) == (ALICE, "clear", "")


def test_other_members_tap_on_clear_confirm_is_refused(cbot, run_async):
    card = _clear_card(cbot, run_async)
    turn = _sess(cbot, X1).turn_key
    q = _run_tap(cbot, run_async, _cb(cbot, G, X1, f"clearok{turn}"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card."]
    q.edit_message_text.assert_not_awaited()
    cbot._inject_prompt.assert_not_awaited()
    # alice's own tap clears it, and names her.
    q = _run_tap(cbot, run_async, _cb(cbot, G, X1, f"clearok{turn}"), ALICE, message_id=card)
    cbot._inject_prompt.assert_awaited_once()
    assert cbot._inject_prompt.await_args.args[1] == "/clear"
    assert _edit_texts(q) == ["🧹 /clear sent to x1 by @alice"]
    assert (G, card) not in cbot.registry.card_owners


def test_other_members_cancel_on_clear_confirm_is_refused(cbot, run_async):
    card = _clear_card(cbot, run_async)
    q = _run_tap(cbot, run_async, _cb(cbot, G, X1, "clear-cancel"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card."]
    q.edit_message_text.assert_not_awaited()
    assert (G, card) in cbot.registry.card_owners


def test_clear_verbs_need_prompt():
    from aipager.bot import tap_gate

    for verb in ("clear", "clear-cancel", "clearok0", "clearok12"):
        assert tap_gate.required_capability(X1, verb) is tap_gate.PROMPT, verb
    assert tap_gate.EXACT_CAPS["clear"] is tap_gate.PROMPT
    assert tap_gate.EXACT_CAPS["clear-cancel"] is tap_gate.PROMPT
    assert ("clearok", tap_gate.PROMPT) in tap_gate.NUMBERED_CAPS


@pytest.mark.parametrize("verb", ["clear", "clearok", "clear-cancel"])
def test_read_only_tap_on_clear_is_refused_by_the_gate(cbot, run_async, verb):
    if verb == "clearok":
        verb = f"clearok{_sess(cbot, X1).turn_key}"
    q = _run_tap(cbot, run_async, _cb(cbot, G, X1, verb), READER, message_id=901)
    assert _toasts(q) == ["Your role can't do that here."]
    q.edit_message_text.assert_not_awaited()
    cbot._inject_prompt.assert_not_awaited()


def test_user_role_may_not_clear_without_clear_on_the_keyboard(cbot, run_async):
    cbot._command_map = {}
    q = _run_tap(cbot, run_async, _cb(cbot, G, X1, "clear"), BOB, message_id=902)
    q.answer.assert_awaited_once_with("That command needs an admin.", show_alert=True)
    q.edit_message_text.assert_not_awaited()
    # An owner still may.
    _clear_card(cbot, run_async, user=ALICE, message_id=903)


def test_clear_card_owner_survives_registry_reload(cbot, run_async):
    from aipager.state import SessionRegistry

    card = _clear_card(cbot, run_async)
    cbot.registry.save()
    fresh = SessionRegistry()
    fresh.load()
    assert fresh.card_owners[(G, card)]["kind"] == "clear"


# ---- a confirm clears once per turn ---------------------------------------

def test_core_second_confirm_clear_on_the_same_turn_is_refused(mk_bot, run_async, typed):
    """Its turn moves on only with Claude Code's SessionEnd for the clear:
    a second confirm before that must not type /clear again."""
    bot = mk_bot()
    sess = _live(bot)
    assert _core(bot, sess, run_async).reason == "sent"
    again = _core(bot, sess, run_async)
    assert (again.ok, again.reason) == (False, "already_cleared")
    assert typed == [("claude-dev", "/clear")]


def test_core_two_confirms_at_once_type_clear_once(mk_bot, run_async, typed):
    import asyncio
    bot = mk_bot()
    sess = _live(bot)

    async def both():
        return await asyncio.gather(
            bot._clear_session_core(sess, driver_user_id=1, chat_id=None),
            bot._clear_session_core(sess, driver_user_id=2, chat_id=None))

    reasons = sorted(o.reason for o in run_async(both()))
    assert reasons == ["already_cleared", "sent"]
    assert typed == [("claude-dev", "/clear")]


def test_core_a_confirm_clear_that_typed_nothing_can_be_retried(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot = mk_bot()
    sess = _live(bot)
    bot._inject_prompt = AsyncMock(side_effect=[False, True])
    assert _core(bot, sess, run_async).reason == "send_failed"
    assert _core(bot, sess, run_async).reason == "sent"


def test_core_a_new_turn_may_be_cleared_again(mk_bot, run_async, typed):
    bot = mk_bot()
    sess = _live(bot)
    assert _core(bot, sess, run_async).reason == "sent"
    sess.turn_key += 1          # SessionEnd(clear) moved the turn on
    assert _core(bot, sess, run_async).reason == "sent"
    assert typed == [("claude-dev", "/clear"), ("claude-dev", "/clear")]


def test_core_the_keyboards_clear_is_not_limited_per_turn(mk_bot, run_async, typed):
    """The Commands keyboard's Clear (with its hold) keeps today's
    behaviour: each tap types /clear."""
    bot = mk_bot()
    sess = _live(bot)
    hold = AsyncMock(return_value=False)
    assert _core(bot, sess, run_async, hold=hold).reason == "sent"
    assert _core(bot, sess, run_async, hold=hold).reason == "sent"
    assert len(typed) == 2
