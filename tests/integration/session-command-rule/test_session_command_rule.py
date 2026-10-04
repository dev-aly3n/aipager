"""P4 of the command redesign (2026-09-30): one rule for the commands that
act on a session. `/<cmd> name` acts on that session of this chat; a bare
`/<cmd>` acts on the one session it can mean, else shows a picker of only
the sessions it makes sense for (this chat's target first, marked ✍️, and
a Cancel); the picker and the typed name lead to the same card.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.bot.session_ops import StopOutcome
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 12345
OTHER_CHAT = 999


def _sess(bot, label, status=Status.IDLE, *, chat=CHAT, **fields):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.scope_chat_id = chat
    for k, v in fields.items():
        setattr(s, k, v)
    bot.registry._sessions[s.name] = s
    return s


@pytest.fixture
def bot(mk_bot, monkeypatch):
    b = mk_bot(SessionRegistry())
    b._read_status_file = MagicMock(return_value=None)
    b._maybe_update_bot_name = AsyncMock()
    b._update_bot_commands = AsyncMock()
    b._react = AsyncMock()

    async def _alive(name):
        sess = b.registry.get(name)
        return sess is not None and sess.status != Status.GONE
    monkeypatch.setattr("aipager.dtach.inject.is_alive", _alive)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", AsyncMock(return_value=[]))
    return b


@pytest.fixture
def cmd(bot, mk_update, run_async):
    """Send a command in CHAT; return (first reply text, its keyboard)."""
    handlers = {
        "stop": bot._handle_stop_cmd, "kill": bot._handle_kill_cmd,
        "mode": bot._handle_mode_cmd, "perms": bot._handle_perms_cmd,
        "restart": lambda u, c: session_parity.handle_restart_cmd(bot, u, c),
        "rename": lambda u, c: session_parity.handle_rename_cmd(bot, u, c),
        "delete": lambda u, c: session_parity.handle_delete_cmd(bot, u, c),
        "diff": lambda u, c: session_parity.handle_diff_cmd(bot, u, c),
    }

    def _cmd(text, *, chat=CHAT):
        update = mk_update(text, chat_id=chat)
        update.effective_chat.type = "group"
        run_async(handlers[text.split()[0][1:]](update, MagicMock()))
        call = update.message.reply_text.await_args
        if call is None:
            return None, None
        return call.args[0], call.kwargs.get("reply_markup")
    return _cmd


@pytest.fixture
def tap(bot, run_async):
    def _tap(data, *, user=1, chat=CHAT):
        query = MagicMock(data=data)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.from_user = MagicMock(id=user)
        query.message = MagicMock(message_id=4242)
        query.message.reply_text = AsyncMock()
        update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
        update.effective_chat = MagicMock(id=chat, type="group")
        run_async(bot._handle_callback(update, MagicMock()))
        return query
    return _tap


def _edited(query):
    call = query.edit_message_text.await_args
    assert call is not None, "the tapped message was not edited"
    return (call.args[0] if call.args else call.kwargs["text"]), call.kwargs.get("reply_markup")


def _toast(query):
    texts = [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]
    return texts[-1] if texts else None


def _resolve(bot, cb, chat=CHAT):
    return session_parity.resolve_short_cb(bot, chat, *cb.split(":", 1))


def _buttons(bot, kb):
    """[(text, (session, verb) or the raw data), ...] row-major."""
    out = []
    for row in kb.inline_keyboard:
        for b in row:
            data = b.callback_data
            out.append((b.text, _resolve(bot, data) if data.startswith("_:sx:") else data))
    return out


def _cb(bot, kb, text):
    return next(b.callback_data for row in kb.inline_keyboard for b in row if b.text == text)


def _stopped(sess, dropped=0):
    return StopOutcome(ok=True, label=sess.label, dropped=dropped)


# What a stub returns where the test expects no stop at all: a real
# outcome, so a guard that fails to refuse shows as a failed assertion.
_ANY_STOP = StopOutcome(ok=True, label="any")


# ---- /stop -------------------------------------------------------------------

def test_stop_stops_the_one_working_session_even_when_it_is_not_the_target(bot, cmd):
    target = _sess(bot, "a1")
    busy = _sess(bot, "b1", Status.BUSY)
    bot.registry.last_active_session = target.name
    bot._stop_session_core = AsyncMock(return_value=_stopped(busy))

    text, _kb = cmd("/stop")

    bot._stop_session_core.assert_awaited_once_with(busy, by="")
    assert text == "⏹ Stopped <b>b1</b>"


def test_stop_counts_a_session_that_needs_you_as_working(bot, cmd):
    wait = _sess(bot, "w1", Status.INTERACTIVE)
    bot._stop_session_core = AsyncMock(return_value=_stopped(wait))

    cmd("/stop")

    bot._stop_session_core.assert_awaited_once_with(wait, by="")


def test_stop_with_nothing_working_says_so(bot, cmd):
    target = _sess(bot, "a1")
    _sess(bot, "u1", Status.UNKNOWN)
    bot.registry.last_active_session = target.name
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)

    text, kb = cmd("/stop")

    assert (text, kb) == ("Nothing is running.", None)
    bot._stop_session_core.assert_not_awaited()


def test_stop_never_reaches_another_chats_working_session(bot, cmd):
    _sess(bot, "a1")
    _sess(bot, "theirs", Status.BUSY, chat=OTHER_CHAT)
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)

    assert cmd("/stop")[0] == "Nothing is running."
    assert cmd("/stop theirs")[0] == "⚠️ No session named <b>theirs</b> here."
    bot._stop_session_core.assert_not_awaited()


def test_stop_with_several_working_offers_a_picker_target_first(bot, cmd):
    b1 = _sess(bot, "b1", Status.BUSY)
    b2 = _sess(bot, "b2", Status.BUSY)
    c0 = _sess(bot, "c0", Status.INTERACTIVE)
    _sess(bot, "idle1")
    _sess(bot, "theirs", Status.BUSY, chat=OTHER_CHAT)
    bot.registry.last_active_session = b2.name
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)

    text, kb = cmd("/stop")

    assert text == "Which one to stop?"
    assert _buttons(bot, kb) == [
        ("✍️ b2", (b2.name, f"pstop{b2.turn_key}")),
        ("⏹ b1", (b1.name, f"pstop{b1.turn_key}")),
        ("⏹ c0", (c0.name, f"pstop{c0.turn_key}")),
        ("✖️ Cancel", "_:pick:cancel"),
    ]
    bot._stop_session_core.assert_not_awaited()


def test_stop_by_name(bot, cmd):
    _sess(bot, "b0", Status.BUSY)
    b1 = _sess(bot, "b1", Status.BUSY)
    bot._stop_session_core = AsyncMock(return_value=_stopped(b1, dropped=2))

    text, _kb = cmd("/stop b1")

    bot._stop_session_core.assert_awaited_once_with(b1, by="")
    assert text == "⏹ Stopped <b>b1</b> (2 queued messages discarded)"


def test_stop_by_name_of_a_session_that_is_not_working(bot, cmd):
    _sess(bot, "a1")
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)

    assert cmd("/stop a1")[0] == "<b>a1</b> is not working."
    assert cmd("/stop nope")[0] == "⚠️ No session named <b>nope</b> here."
    bot._stop_session_core.assert_not_awaited()


def test_the_stop_picker_stops_and_becomes_the_result(bot, cmd, tap):
    b1 = _sess(bot, "b1", Status.BUSY)
    _sess(bot, "b2", Status.BUSY)
    _text, kb = cmd("/stop")
    bot._stop_session_core = AsyncMock(return_value=_stopped(b1, dropped=1))

    query = tap(_cb(bot, kb, "⏹ b1"))

    bot._stop_session_core.assert_awaited_once_with(b1, by="")
    assert _toast(query) == "⏹ Stopped b1 (1 queued message discarded)"
    assert _edited(query) == ("⏹ Stopped <b>b1</b> (1 queued message discarded)", None)


def test_the_stop_picker_leaves_the_picker_when_nothing_was_stopped(bot, cmd, tap):
    b1 = _sess(bot, "b1", Status.BUSY)
    _sess(bot, "b2", Status.BUSY)
    _text, kb = cmd("/stop")
    bot._stop_session_core = AsyncMock(return_value=StopOutcome(ok=False, label="b1"))

    query = tap(_cb(bot, kb, "⏹ b1"))

    bot._stop_session_core.assert_awaited_once_with(b1, by="")
    assert _toast(query) == "b1 is not working"
    query.edit_message_text.assert_not_awaited()


def test_the_stop_picker_refuses_a_turn_that_moved_on(bot, cmd, tap):
    b1 = _sess(bot, "b1", Status.BUSY)
    _sess(bot, "b2", Status.BUSY)
    _text, kb = cmd("/stop")
    bot.registry.transition(b1.name, Status.IDLE)
    bot.registry.transition(b1.name, Status.BUSY)     # new work since the picker
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)

    query = tap(_cb(bot, kb, "⏹ b1"))

    bot._stop_session_core.assert_not_awaited()
    assert _toast(query) == "b1 moved on to new work - send /stop again"
    query.edit_message_text.assert_not_awaited()      # the picker stays


def test_the_stop_picker_survives_a_card_move_in_the_same_turn(bot, cmd, tap):
    """The busy card moving after the picker was sent gives it a newer
    message id; the turn is what counts, not the ids."""
    b1 = _sess(bot, "b1", Status.BUSY)
    _sess(bot, "b2", Status.BUSY)
    _text, kb = cmd("/stop")
    b1.busy_msg_id = 99999
    bot._stop_session_core = AsyncMock(return_value=_stopped(b1))

    tap(_cb(bot, kb, "⏹ b1"))

    bot._stop_session_core.assert_awaited_once_with(b1, by="")


def test_the_stop_picker_refuses_a_re_created_session(bot, cmd, tap):
    old = _sess(bot, "b1", Status.BUSY)
    _sess(bot, "b2", Status.BUSY)
    _text, kb = cmd("/stop")
    del bot.registry._sessions[old.name]
    new = _sess(bot, "b1", Status.BUSY)               # same name, new session
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)

    query = tap(_cb(bot, kb, "⏹ b1"))

    assert new is not old
    bot._stop_session_core.assert_not_awaited()
    assert "moved on" in _toast(query)


def test_the_stop_picker_needs_the_right_to_prompt(bot, cmd, tap):
    _sess(bot, "b1", Status.BUSY)
    _sess(bot, "b2", Status.BUSY)
    _text, kb = cmd("/stop")
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)
    bot._can_prompt_user = MagicMock(return_value=False)

    query = tap(_cb(bot, kb, "⏹ b1"))

    bot._stop_session_core.assert_not_awaited()
    assert _toast(query) == "You can't stop this session."


def test_the_stop_picker_refuses_another_chats_session(bot, tap):
    theirs = _sess(bot, "theirs", Status.BUSY, chat=OTHER_CHAT)
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)

    query = tap(f"{theirs.name}:pstop{theirs.turn_key}")

    bot._stop_session_core.assert_not_awaited()
    assert _toast(query) == "That session isn't running here."


def test_a_picker_cancel_says_so(bot, cmd, tap):
    _sess(bot, "b1", Status.BUSY)
    _sess(bot, "b2", Status.BUSY)
    _text, kb = cmd("/stop")

    query = tap(_cb(bot, kb, "✖️ Cancel"))

    assert _edited(query)[0] == "Cancelled."


# ---- /mode (and /perms) --------------------------------------------------------

@pytest.mark.parametrize("auto, text, button, verb", [
    (True, "✍️ <b>x1</b> is 🤖 Auto.", "💬 Switch to Ask", "modeask"),
    (False, "✍️ <b>x1</b> is 💬 Ask.", "🤖 Switch to Auto", "modeauto"),
])
def test_mode_shows_the_mode_and_the_one_switch(bot, cmd, auto, text, button, verb):
    sess = _sess(bot, "x1", skip_perms=auto)
    bot.registry.last_active_session = sess.name
    bot._perms_flow = AsyncMock()

    got, kb = cmd("/mode")

    assert got == text
    assert _buttons(bot, kb) == [(button, (sess.name, f"{verb}{sess.turn_key}"))]
    bot._perms_flow.assert_not_awaited()          # it no longer flips blind


def test_perms_is_the_same_command(bot, cmd):
    sess = _sess(bot, "x1", skip_perms=True)
    bot.registry.last_active_session = sess.name

    assert cmd("/perms") == cmd("/mode")


def test_mode_by_name_and_from_the_picker_show_the_same_card(bot, cmd, tap):
    x1 = _sess(bot, "x1", skip_perms=True)
    x2 = _sess(bot, "x2")
    _sess(bot, "gone1", Status.GONE)
    _sess(bot, "theirs", chat=OTHER_CHAT)

    text, kb = cmd("/mode")          # no target, two live sessions: which one?
    assert text == "Which session?"
    assert _buttons(bot, kb) == [("x1", (x1.name, "mode_show")),
                                 ("x2", (x2.name, "mode_show")),
                                 ("✖️ Cancel", "_:pick:cancel")]

    picked_text, picked_kb = _edited(tap(_cb(bot, kb, "x2")))
    typed_text, typed_kb = cmd("/mode x2")
    assert picked_text == typed_text == "<b>x2</b> is 💬 Ask."
    assert _buttons(bot, picked_kb) == _buttons(bot, typed_kb) == [
        ("🤖 Switch to Auto", (x2.name, f"modeauto{x2.turn_key}"))]


@pytest.mark.parametrize("text, question, verb, target", [
    ("/mode ask", "Which session to switch to 💬 Ask?", "modeask", False),
    ("/mode auto", "Which session to switch to 🤖 Auto?", "modeauto", True),
])
def test_mode_named_with_no_target_picks_then_switches_in_one_tap(
        bot, cmd, tap, text, question, verb, target):
    x1 = _sess(bot, "x1", skip_perms=True)
    x2 = _sess(bot, "x2")
    bot._perms_flow = AsyncMock()

    got, kb = cmd(text)

    assert got == question
    assert _buttons(bot, kb) == [("x1", (x1.name, f"{verb}{x1.turn_key}")),
                                 ("x2", (x2.name, f"{verb}{x2.turn_key}")),
                                 ("✖️ Cancel", "_:pick:cancel")]
    bot._perms_flow.assert_not_awaited()
    tap(_cb(bot, kb, "x1" if not target else "x2"))
    sess, want, _msg = bot._perms_flow.await_args.args
    assert (sess, want) == ((x2 if target else x1), target)


def test_mode_with_one_live_session_and_no_target_shows_it(bot, cmd):
    _sess(bot, "x1")
    _sess(bot, "gone1", Status.GONE)

    assert cmd("/mode")[0] == "<b>x1</b> is 💬 Ask."


def test_mode_with_no_live_session(bot, cmd):
    _sess(bot, "gone1", Status.GONE)
    _sess(bot, "theirs", chat=OTHER_CHAT)

    assert cmd("/mode")[0] == "No live sessions here. Start one with /new."
    assert cmd("/mode theirs")[0] == "⚠️ No live session named <b>theirs</b> here."
    assert cmd("/mode theirs ask")[0] == "⚠️ No live session named <b>theirs</b> here."


@pytest.mark.parametrize("text, who, target", [
    ("/mode ask", "x1", False),
    ("/mode x2 auto", "x2", True),
    ("/mode auto x2", "x2", True),
    ("/perms x2 auto", "x2", True),
])
def test_mode_switches_straight_to_the_mode_named(bot, cmd, text, who, target):
    x1 = _sess(bot, "x1", skip_perms=True)
    _sess(bot, "x2")
    bot.registry.last_active_session = x1.name
    bot._perms_flow = AsyncMock()

    cmd(text)

    sess, want, _msg = bot._perms_flow.await_args.args
    assert (sess.label, want) == (who, target)


def test_mode_already_in_the_mode_named(bot, cmd):
    x1 = _sess(bot, "x1")
    bot.registry.last_active_session = x1.name
    bot._perms_flow = AsyncMock()

    assert cmd("/mode ask")[0] == "<b>x1</b> is already in 💬 Ask."
    bot._perms_flow.assert_not_awaited()


def test_only_going_to_auto_needs_an_admin(bot, cmd):
    x1 = _sess(bot, "x1")
    x2 = _sess(bot, "x2", skip_perms=True)
    bot._is_admin = MagicMock(return_value=False)
    bot._do_perms_switch_via_fn = AsyncMock()

    assert cmd("/mode x1 auto")[0] == "Auto mode needs an admin."
    assert x1.skip_perms is False

    assert cmd("/mode x2 ask")[0] == "⚙️ Switching <b>x2</b> to 💬 Ask mode…"
    sess, target, _edit = bot._do_perms_switch_via_fn.await_args.args
    assert (sess, target) == (x2, False)


def test_mode_on_a_busy_session_offers_stop_and_switch(bot, cmd):
    _sess(bot, "b1", Status.BUSY, skip_perms=True)

    text, kb = cmd("/mode b1 ask")

    assert text == "⚙️ <b>b1</b> is busy.\nSwitch to Ask mode?"
    assert [b.text for row in kb.inline_keyboard for b in row] == ["🛑 Stop task & switch", "⏳ Not now"]


def test_the_mode_card_switch_runs_the_same_flow(bot, cmd, tap):
    x1 = _sess(bot, "x1", skip_perms=True)
    bot.registry.last_active_session = x1.name
    _text, kb = cmd("/mode")
    bot._perms_flow = AsyncMock()

    tap(_cb(bot, kb, "💬 Switch to Ask"))

    sess, want, _msg = bot._perms_flow.await_args.args
    assert (sess, want) == (x1, False)


def test_the_mode_card_refuses_a_turn_that_moved_on(bot, cmd, tap):
    x1 = _sess(bot, "x1", Status.BUSY, skip_perms=True)
    bot.registry.last_active_session = x1.name
    _text, kb = cmd("/mode")
    bot.registry.transition(x1.name, Status.IDLE)
    bot.registry.transition(x1.name, Status.BUSY)
    bot._perms_flow = AsyncMock()

    query = tap(_cb(bot, kb, "💬 Switch to Ask"))

    bot._perms_flow.assert_not_awaited()
    assert _toast(query) == "x1 moved on to new work - tap the switch again if you still want it"
    # A fresh card, for this turn, to tap if they do.
    text, fresh = _edited(query)
    assert text == "✍️ <b>x1</b> is 🤖 Auto."
    assert _buttons(bot, fresh) == [("💬 Switch to Ask", (x1.name, f"modeask{x1.turn_key}"))]


def test_the_mode_card_survives_a_card_move_in_the_same_turn(bot, cmd, tap):
    x1 = _sess(bot, "x1", Status.BUSY, skip_perms=True)
    bot.registry.last_active_session = x1.name
    _text, kb = cmd("/mode")
    x1.busy_msg_id = 99999
    bot._perms_flow = AsyncMock()

    tap(_cb(bot, kb, "💬 Switch to Ask"))

    bot._perms_flow.assert_awaited_once()


def test_the_mode_card_says_already_in_after_a_switch_elsewhere(bot, cmd, tap):
    x1 = _sess(bot, "x1", skip_perms=True)
    bot.registry.last_active_session = x1.name
    _text, kb = cmd("/mode")
    # Switched from another card: that relaunches the session, which ends
    # and comes back (two new turn keys) in the other mode.
    bot.registry.transition(x1.name, Status.GONE)
    x1.skip_perms = False
    bot.registry.transition(x1.name, Status.IDLE)
    bot._perms_flow = AsyncMock()

    query = tap(_cb(bot, kb, "💬 Switch to Ask"))

    bot._perms_flow.assert_not_awaited()
    assert _toast(query) == "x1 is already in 💬 Ask."
    assert _edited(query)[0] == "✍️ <b>x1</b> is 💬 Ask."


@pytest.mark.parametrize("setup, toast", [
    ("no_prompt", "You can't change this session."),
    ("other_chat", "That session isn't running here."),
    ("gone", "That session has ended."),
])
def test_the_mode_card_refusals(bot, tap, setup, toast):
    x1 = _sess(bot, "x1", Status.GONE if setup == "gone" else Status.IDLE,
               chat=OTHER_CHAT if setup == "other_chat" else CHAT, skip_perms=True)
    if setup == "no_prompt":
        bot._can_prompt_user = MagicMock(return_value=False)
    bot._perms_flow = AsyncMock()

    query = tap(f"{x1.name}:modeask{x1.turn_key}")

    bot._perms_flow.assert_not_awaited()
    assert _toast(query) == toast


def test_a_non_admin_tapping_switch_to_auto_is_refused_in_words(bot, cmd, tap):
    x1 = _sess(bot, "x1")
    bot.registry.last_active_session = x1.name
    _text, kb = cmd("/mode")
    bot._is_admin_user = MagicMock(return_value=False)

    query = tap(_cb(bot, kb, "🤖 Switch to Auto"))

    # A toast; the card stays as it is, and nothing new is sent.
    assert _toast(query) == "Auto mode needs an admin."
    query.edit_message_text.assert_not_awaited()
    query.message.reply_text.assert_not_awaited()
    assert x1.skip_perms is False


# ---- /kill ---------------------------------------------------------------------

END_X1 = "⏹ End <b>x1</b>? Claude stops and the session closes."


def test_kill_by_name_asks_to_end_it(bot, cmd):
    x1 = _sess(bot, "x1")
    _sess(bot, "x2")
    bot._kill_session_core = AsyncMock()

    text, kb = cmd("/kill x1")

    assert text == END_X1
    assert _buttons(bot, kb) == [("⏹ End", (x1.name, f"endok{x1.turn_key}")),
                                 ("Cancel", (x1.name, "kill-cancel"))]
    bot._kill_session_core.assert_not_awaited()


def test_kill_with_one_live_session_goes_to_its_confirm(bot, cmd):
    _sess(bot, "x1")
    _sess(bot, "gone1", Status.GONE)

    assert cmd("/kill")[0] == END_X1


def test_the_kill_picker_lists_only_this_chats_live_sessions(bot, cmd, tap):
    x1 = _sess(bot, "x1")
    x2 = _sess(bot, "x2", Status.BUSY)
    _sess(bot, "gone1", Status.GONE)
    _sess(bot, "theirs", chat=OTHER_CHAT)
    bot.registry.last_active_session = x2.name

    text, kb = cmd("/kill")

    assert text == "Which session to end?"
    assert _buttons(bot, kb) == [("✍️ x2", (x2.name, "end")), ("⏹ x1", (x1.name, "end")),
                                 ("✖️ Cancel", "_:pick:cancel")]
    # The picker leads to the same confirm the typed name does.
    picked = _edited(tap(_cb(bot, kb, "⏹ x1")))
    typed = cmd("/kill x1")
    assert picked[0] == typed[0] == END_X1
    assert _buttons(bot, picked[1]) == _buttons(bot, typed[1])


def test_kill_with_nothing_live(bot, cmd):
    _sess(bot, "gone1", Status.GONE)
    _sess(bot, "theirs", chat=OTHER_CHAT)

    assert cmd("/kill")[0] == "No sessions to end."
    assert cmd("/kill theirs")[0] == "⚠️ No live session named <b>theirs</b> here."
    assert cmd("/kill gone1")[0] == "⚠️ No live session named <b>gone1</b> here."


def test_end_ends_it_and_says_so_above_what_remains(bot, cmd, tap):
    x1 = _sess(bot, "x1")
    _sess(bot, "x2")
    _text, kb = cmd("/kill x1")

    async def _kill(name, label):
        bot.registry._sessions[name].status = Status.GONE
        return MagicMock(result="killed")
    bot._kill_session_core = AsyncMock(side_effect=_kill)

    query = tap(_cb(bot, kb, "⏹ End"))

    bot._kill_session_core.assert_awaited_once_with(x1.name, "x1")
    assert _toast(query) == "⏹ Ended x1"
    text = _edited(query)[0]
    assert text.startswith("⏹ Ended <b>x1</b>\n\n📊 <b>Sessions (1)</b>"), text


def test_a_failed_end_has_no_ended_line(bot, cmd, tap):
    _sess(bot, "x1")
    _text, kb = cmd("/kill x1")
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result="still_running"))

    query = tap(_cb(bot, kb, "⏹ End"))

    assert _toast(query) == "x1 did not stop - try again"
    assert _edited(query)[0].startswith("📊")


def test_the_end_confirm_from_kill_checks_the_turn(bot, cmd, tap):
    x1 = _sess(bot, "x1", Status.BUSY)
    _text, kb = cmd("/kill x1")
    x1.busy_msg_id = 99999                      # the card moved: same turn, still fine
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result="killed"))
    tap(_cb(bot, kb, "⏹ End"))
    bot._kill_session_core.assert_awaited_once()

    x1.status = Status.BUSY
    _text, kb = cmd("/kill x1")
    bot.registry.transition(x1.name, Status.IDLE)
    bot.registry.transition(x1.name, Status.BUSY)   # new work since
    bot._kill_session_core.reset_mock()
    query = tap(_cb(bot, kb, "⏹ End"))
    bot._kill_session_core.assert_not_awaited()
    assert "moved on" in _toast(query)


def test_the_end_confirm_refuses_a_re_created_session(bot, cmd, tap):
    old = _sess(bot, "x1")
    _text, kb = cmd("/kill x1")
    del bot.registry._sessions[old.name]
    _sess(bot, "x1")
    bot._kill_session_core = AsyncMock()

    tap(_cb(bot, kb, "⏹ End"))

    bot._kill_session_core.assert_not_awaited()


def test_cancelling_end_says_nothing_ended(bot, cmd, tap):
    _sess(bot, "x1")
    _text, kb = cmd("/kill x1")
    bot._kill_session_core = AsyncMock()

    query = tap(_cb(bot, kb, "Cancel"))

    assert _edited(query)[0] == "↩️ Cancelled. Nothing was ended."
    bot._kill_session_core.assert_not_awaited()


# ---- /restart --------------------------------------------------------------------

def test_restart_one_live_session_goes_to_its_confirm(bot, cmd):
    x1 = _sess(bot, "x1")
    _sess(bot, "gone1", Status.GONE)

    text, kb = cmd("/restart")

    assert cmd("/restart x1")[0] == text
    assert [v for _t, v in _buttons(bot, kb)] == [
        (x1.name, f"restartok{x1.turn_key}"), (x1.name, "restart-cancel")]


def test_restart_picker_and_typed_name_reach_the_same_confirm(bot, cmd, tap):
    x1 = _sess(bot, "x1")
    x2 = _sess(bot, "x2")
    _sess(bot, "gone1", Status.GONE)
    _sess(bot, "theirs", chat=OTHER_CHAT)
    bot.registry.last_active_session = x2.name

    text, kb = cmd("/restart")

    assert text == "Which session to restart?"
    assert _buttons(bot, kb) == [("✍️ x2", (x2.name, "restart")),
                                 ("🔄 x1", (x1.name, "restart")),
                                 ("✖️ Cancel", "_:pick:cancel")]
    picked = _edited(tap(_cb(bot, kb, "🔄 x1")))
    typed = cmd("/restart x1")
    assert picked[0] == typed[0]
    assert _buttons(bot, picked[1]) == _buttons(bot, typed[1])


def test_restart_none_and_unknown(bot, cmd):
    _sess(bot, "gone1", Status.GONE)
    _sess(bot, "theirs", chat=OTHER_CHAT)

    assert cmd("/restart")[0] == "No live sessions to restart."
    assert cmd("/restart theirs")[0] == "⚠️ No live session named <b>theirs</b> here."


def _restart_ok(bot, cmd):
    _text, kb = cmd("/restart x1")
    return next(b.callback_data for row in kb.inline_keyboard for b in row
                if _resolve(bot, b.callback_data)[1].startswith("restartok"))


def test_the_restart_confirm_checks_the_turn(bot, cmd, tap):
    x1 = _sess(bot, "x1", Status.BUSY)
    bot._restart_session_core = AsyncMock(return_value=MagicMock(ok=True, label="x1"))

    cb = _restart_ok(bot, cmd)
    x1.busy_msg_id = 99999                     # the card moved: same turn
    tap(cb)
    bot._restart_session_core.assert_awaited_once_with(x1)

    cb = _restart_ok(bot, cmd)
    bot.registry.transition(x1.name, Status.IDLE)
    bot.registry.transition(x1.name, Status.BUSY)   # new work since
    bot._restart_session_core.reset_mock()
    query = tap(cb)
    bot._restart_session_core.assert_not_awaited()
    assert _toast(query) == "x1 moved on to new work - tap 🔄 Restart again to restart it"


def test_the_restart_confirm_refuses_a_re_created_session(bot, cmd, tap):
    old = _sess(bot, "x1")
    cb = _restart_ok(bot, cmd)
    del bot.registry._sessions[old.name]
    _sess(bot, "x1")
    bot._restart_session_core = AsyncMock(return_value=MagicMock(ok=True, label="x1"))

    tap(cb)

    bot._restart_session_core.assert_not_awaited()


@pytest.mark.parametrize("setup, toast", [
    ("no_prompt", "You can't restart this session."),
    ("other_chat", "That session isn't running here."),
])
def test_the_restart_confirm_refusals(bot, tap, setup, toast):
    x1 = _sess(bot, "x1", chat=OTHER_CHAT if setup == "other_chat" else CHAT)
    if setup == "no_prompt":
        bot._can_prompt_user = MagicMock(return_value=False)
    bot._restart_session_core = AsyncMock(return_value=MagicMock(ok=True, label="x1"))

    query = tap(f"{x1.name}:restartok{x1.turn_key}")

    bot._restart_session_core.assert_not_awaited()
    assert _toast(query) == toast


# ---- /rename, /delete, /diff ------------------------------------------------------

def test_rename_one_session_asks_for_the_name_and_the_picker_does_too(bot, cmd, tap):
    x1 = _sess(bot, "x1")
    one = cmd("/rename")
    assert one[0] == "✏️ New name for [<b>x1</b>]? Send it as a message."

    x2 = _sess(bot, "x2", Status.GONE)          # an ended one can be renamed too
    _sess(bot, "theirs", chat=OTHER_CHAT)
    bot.registry.last_active_session = x1.name
    text, kb = cmd("/rename")
    assert text == "Which session to rename?"
    assert _buttons(bot, kb) == [("✍️ x1", (x1.name, "rename")),
                                 ("✏️ x2", (x2.name, "rename")),
                                 ("✖️ Cancel", "_:pick:cancel")]
    picked = _edited(tap(_cb(bot, kb, "✍️ x1")))
    assert picked[0] == one[0]
    assert _buttons(bot, picked[1]) == _buttons(bot, one[1])


def test_rename_none_and_unknown(bot, cmd):
    _sess(bot, "theirs", chat=OTHER_CHAT)

    assert cmd("/rename")[0] == "No sessions to rename."
    assert cmd("/rename theirs x9")[0] == "⚠️ No session named <b>theirs</b> here."


def test_delete_one_ended_session_goes_to_its_confirm_and_the_picker_does_too(bot, cmd, tap):
    g1 = _sess(bot, "g1", Status.GONE)
    _sess(bot, "x1")
    one = cmd("/delete")
    assert one[0].startswith("🗑️ Remove [<b>g1</b>]")
    assert [v for _t, v in _buttons(bot, one[1])] == [
        (g1.name, "delete-confirm"), (g1.name, "delete-cancel")]

    g0 = _sess(bot, "g0", Status.GONE)
    _sess(bot, "theirs", Status.GONE, chat=OTHER_CHAT)
    text, kb = cmd("/delete")
    assert text == "Which ended session to remove?"
    assert _buttons(bot, kb) == [("🗑️ g0", (g0.name, "delete")),
                                 ("🗑️ g1", (g1.name, "delete")),
                                 ("✖️ Cancel", "_:pick:cancel")]
    picked = _edited(tap(_cb(bot, kb, "🗑️ g1")))
    assert picked[0] == one[0]
    assert _buttons(bot, picked[1]) == _buttons(bot, one[1])


def test_delete_none_unknown_and_still_running(bot, cmd):
    _sess(bot, "x1")
    _sess(bot, "theirs", Status.GONE, chat=OTHER_CHAT)

    assert cmd("/delete")[0] == "No ended sessions to delete."
    assert cmd("/delete theirs")[0] == "⚠️ No session named <b>theirs</b> here."
    assert cmd("/delete x1")[0] == "⚠️ [<b>x1</b>] is still running. End it first with /kill."


def test_diff_goes_to_the_target_the_one_live_session_or_a_picker(bot, cmd, tap, monkeypatch):
    run_diff = AsyncMock()
    monkeypatch.setattr(session_parity, "_run_diff", run_diff)
    x1 = _sess(bot, "x1")
    _sess(bot, "gone1", Status.GONE)

    cmd("/diff")                                     # one live session
    assert run_diff.await_args.args[1] is x1

    x2 = _sess(bot, "x2")
    _sess(bot, "theirs", chat=OTHER_CHAT)
    run_diff.reset_mock()
    text, kb = cmd("/diff")                          # two, no target: which?
    run_diff.assert_not_awaited()
    assert text == "Which session's diff?"
    assert _buttons(bot, kb) == [("📝 x1", (x1.name, "diff")), ("📝 x2", (x2.name, "diff")),
                                 ("✖️ Cancel", "_:pick:cancel")]
    tap(_cb(bot, kb, "📝 x2"))
    assert run_diff.await_args.args[1] is x2

    bot.registry.last_active_session = x1.name
    run_diff.reset_mock()
    cmd("/diff")                                     # the target, no question
    assert run_diff.await_args.args[1] is x1


def test_diff_none_and_unknown(bot, cmd):
    _sess(bot, "theirs", chat=OTHER_CHAT)

    assert cmd("/diff")[0] == "No live sessions to diff."
    assert cmd("/diff theirs")[0] == "⚠️ No session named <b>theirs</b> here."


def test_no_em_dashes_in_what_these_commands_say(bot, cmd):
    _sess(bot, "x1", skip_perms=True)
    _sess(bot, "x2", Status.BUSY)
    _sess(bot, "g1", Status.GONE)
    for text in ("/stop", "/stop x1", "/stop nope", "/mode", "/mode x1", "/kill",
                 "/kill x1", "/kill nope", "/restart", "/restart nope", "/rename",
                 "/delete x1", "/delete nope", "/diff nope"):
        got, kb = cmd(text)
        labels = [b.text for row in (kb.inline_keyboard if kb else []) for b in row]
        assert "—" not in (got or "") and not any("—" in t for t in labels), text


# ---- review-1 of P4 --------------------------------------------------------------

def _waiting_on_an_agent(bot, label):
    """IDLE, with a background agent of its job still running: the turn's
    Stop hook fired, the card still shows it working (CLAUDE.md)."""
    sess = _sess(bot, label, Status.IDLE, active_subagents={"a1": {}})
    assert sess.job_background_open()
    return sess


def test_stop_counts_a_job_still_running_a_background_agent(bot, cmd):
    """rev-iter1-001: /stop said "Nothing is running." under a card that
    still said working."""
    _sess(bot, "a1")
    job = _waiting_on_an_agent(bot, "j1")
    bot._stop_session_core = AsyncMock(return_value=_stopped(job))

    cmd("/stop")
    bot._stop_session_core.assert_awaited_once_with(job, by="")

    bot._stop_session_core.reset_mock()
    assert cmd("/stop j1")[0] == "⏹ Stopped <b>j1</b>"
    bot._stop_session_core.assert_awaited_once_with(job, by="")


def test_status_offers_stop_for_a_job_still_running_a_background_agent(bot, mk_update, run_async):
    job = _waiting_on_an_agent(bot, "j1")
    _sess(bot, "a1")
    update = mk_update("/status", chat_id=CHAT)
    update.effective_chat.type = "group"
    run_async(bot._handle_status(update, MagicMock()))
    kb = update.message.reply_text.await_args.kwargs["reply_markup"]

    assert ("⏹ Stop", (job.name, f"ststop{job.turn_key}")) in _buttons(bot, kb)
    assert [t for t, _v in _buttons(bot, kb)].count("⏹ Stop") == 1   # not for idle a1


@pytest.mark.parametrize("verb", ["endok", "restartok", "modeask", "pstop", "ststop"])
def test_a_button_from_before_an_end_and_resume_is_refused(bot, tap, verb):
    """rev-iter1-002: the same session, ended and resumed, kept its turn key,
    so an End from before it ended ended the resumed one."""
    x1 = _sess(bot, "x1", Status.BUSY if "stop" in verb else Status.IDLE, skip_perms=True)
    cb = session_parity.session_cb(bot, CHAT, x1, f"{verb}{x1.turn_key}")
    bot.registry.transition(x1.name, Status.GONE)
    bot.registry.transition(x1.name, Status.IDLE)          # resumed, same object
    if "stop" in verb:
        x1.status = Status.BUSY                             # and working again
    for core in ("_kill_session_core", "_restart_session_core", "_stop_session_core"):
        setattr(bot, core, AsyncMock(return_value=MagicMock(ok=True, label="x1",
                                                            result="killed", dropped=0)))
    bot._perms_flow = AsyncMock()

    query = tap(cb)

    for core in ("_kill_session_core", "_restart_session_core", "_stop_session_core"):
        getattr(bot, core).assert_not_awaited()
    bot._perms_flow.assert_not_awaited()
    assert "moved on" in _toast(query)


def test_the_turn_key_changes_when_a_session_ends_and_when_it_comes_back(bot):
    x1 = _sess(bot, "x1")
    before = x1.turn_key
    bot.registry.transition(x1.name, Status.GONE)
    ended = x1.turn_key
    bot.registry.transition(x1.name, Status.IDLE)
    assert len({before, ended, x1.turn_key}) == 3


def test_an_admin_switching_to_auto_by_button_gets_the_confirm(bot, cmd, tap):
    """rev-iter1-004: only the refusal was pinned."""
    x1 = _sess(bot, "x1")
    bot.registry.last_active_session = x1.name
    _text, kb = cmd("/mode")
    bot._is_admin_user = MagicMock(return_value=True)

    query = tap(_cb(bot, kb, "🤖 Switch to Auto"))

    # The card itself becomes the confirm (2026-10-01).
    assert _edited(query)[0].startswith("⚙️ Switch <b>x1</b> to 🤖 Auto mode?")
    query.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize("text", ["/mode x1 x2", "/mode x1 ask more", "/mode ask auto x1"])
def test_mode_says_what_it_takes_rather_than_drop_words(bot, cmd, text):
    _sess(bot, "x1", skip_perms=True)
    _sess(bot, "x2")
    bot._perms_flow = AsyncMock()

    assert cmd(text)[0] == ("⚠️ /mode takes a session name and ask or auto, for example "
                            "/mode x1 or /mode x1 ask.")
    bot._perms_flow.assert_not_awaited()


def test_a_leading_slash_on_the_name_works_for_every_session_command(bot, cmd, monkeypatch):
    run_diff = AsyncMock()
    monkeypatch.setattr(session_parity, "_run_diff", run_diff)
    x1 = _sess(bot, "x1")
    g1 = _sess(bot, "g1", Status.GONE)

    assert cmd("/restart /x1")[0] == cmd("/restart x1")[0]
    assert cmd("/delete /g1")[0] == cmd("/delete g1")[0]
    assert cmd("/kill /x1")[0] == END_X1
    assert cmd("/mode /x1")[0] == "<b>x1</b> is 💬 Ask."
    cmd("/diff /g1")
    assert [c.args[1] for c in run_diff.await_args_list] == [g1]
    bot._rename_session_core = AsyncMock(return_value=MagicMock(
        changed=True, previous_label="x1", new_label="x9"))
    cmd("/rename /x1 x9")
    assert [c.args[0] for c in bot._rename_session_core.await_args_list] == [x1]


@pytest.mark.parametrize("setup, toast", [
    ("other_chat", "That session isn't running here."),
    ("gone", "That session has ended."),
])
def test_the_mode_picker_refuses_a_session_it_cannot_show(bot, tap, setup, toast):
    x1 = _sess(bot, "x1", Status.GONE if setup == "gone" else Status.IDLE,
               chat=OTHER_CHAT if setup == "other_chat" else CHAT)

    query = tap(f"{x1.name}:mode_show")

    query.edit_message_text.assert_not_awaited()
    assert _toast(query) == toast


def test_the_stale_end_and_restart_confirms_redraw_the_menu_to_tap_again(bot, cmd, tap):
    x1 = _sess(bot, "x1", Status.BUSY)
    _text, kb = cmd("/kill x1")
    bot.registry.transition(x1.name, Status.IDLE)
    bot.registry.transition(x1.name, Status.BUSY)
    bot._kill_session_core = AsyncMock()

    query = tap(_cb(bot, kb, "⏹ End"))

    assert _toast(query) == "x1 moved on to new work - tap ⏹ End session again to end it"
    assert "⏹ End session" in [b.text for row in _edited(query)[1].inline_keyboard for b in row]

    cb = _restart_ok(bot, cmd)
    bot.registry.transition(x1.name, Status.IDLE)
    bot.registry.transition(x1.name, Status.BUSY)
    bot._restart_session_core = AsyncMock()

    query = tap(cb)

    bot._restart_session_core.assert_not_awaited()
    assert _toast(query) == "x1 moved on to new work - tap 🔄 Restart again to restart it"
    menu = _edited(query)[1]
    restart = next(b.callback_data for row in menu.inline_keyboard for b in row
                   if b.text == "🔄 Restart")
    assert _resolve(bot, restart) == (x1.name, "restart")   # a fresh way to the confirm


@pytest.mark.parametrize("verb", ["pstop", "ststop"])
def test_a_stop_for_a_session_that_ended_since_says_it_ended(bot, tap, verb):
    b1 = _sess(bot, "b1", Status.BUSY)
    cb = session_parity.session_cb(bot, CHAT, b1, f"{verb}{b1.turn_key}")
    bot.registry.transition(b1.name, Status.GONE)
    bot._stop_session_core = AsyncMock(return_value=_ANY_STOP)

    query = tap(cb)

    bot._stop_session_core.assert_not_awaited()
    assert _toast(query) == "That session has ended."
