"""The permission-mode switch card (2026-09-30). The operator sent /perms,
got "Switch to Auto?", tapped Yes, and nothing happened; a later Yes
relaunched the session in Ask. Two causes:

- the 10 s after a relaunch, meant only to hush the old process's late
  SessionEnd, also refused any new switch, in silence;
- the tap spent the card's record first and read a missing one as Ask.

A switch is now refused only while a relaunch is really running, and says
so; a card whose record is gone or belongs to a newer card does nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.bot.session_ops import RestartOutcome
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 12345
# A name no live session on the machine can have; the relaunch core's look
# for its dtach socket is answered below, never from /tmp.
NAME = "claude-zzpermcard__t1"


@pytest.fixture
def bot(mk_bot, monkeypatch):
    b = mk_bot(SessionRegistry())
    b._maybe_update_bot_name = AsyncMock()
    b._update_bot_commands = AsyncMock()
    b._session_system_prompt = lambda *a, **k: ""
    b._is_admin = MagicMock(return_value=True)
    b._is_admin_user = MagicMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)
    monkeypatch.setattr("aipager.bot.session_ops._PERMS_POLL_INTERVAL", 0.0)
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)
    b.launches = launch
    return b


@pytest.fixture
def sess(bot):
    s = TrackedSession(name=NAME, label="jim", status=Status.IDLE)
    s.scope_chat_id = CHAT
    s.skip_perms = True
    bot.registry._sessions[s.name] = s
    bot.registry.last_active_session = s.name
    return s


_ids = iter(range(5000, 6000))


@pytest.fixture
def cmd(bot, mk_update, run_async):
    """Send a command; its reply gets a real message id, as Telegram's does."""
    def _cmd(text):
        update = mk_update(text, chat_id=CHAT)
        update.effective_chat.type = "private"
        replies = []

        async def _reply(*a, **k):
            sent = MagicMock(message_id=next(_ids))
            sent.edit_text = AsyncMock()
            replies.append((a, k, sent))
            return sent
        update.message.reply_text = AsyncMock(side_effect=_reply)
        run_async(bot._handle_mode_cmd(update, MagicMock()))
        return replies
    return _cmd


@pytest.fixture
def tap(bot, run_async):
    def _tap(sent, kwargs, text):
        data = next(b.callback_data for row in kwargs["reply_markup"].inline_keyboard
                    for b in row if b.text == text)
        query = MagicMock(data=data)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.from_user = MagicMock(id=1)
        query.message = MagicMock(message_id=sent.message_id)
        query.message.reply_text = AsyncMock()
        update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
        update.effective_chat = MagicMock(id=CHAT, type="private")
        run_async(bot._handle_callback(update, MagicMock()))
        return query
    return _tap


def _confirm(replies):
    """The "Switch to Auto?" card among a command's replies."""
    a, k, sent = next(r for r in replies if "reply_markup" in r[1])
    assert a[0].startswith("⚙️ Switch <b>jim</b> to 🤖 Auto mode?"), a[0]
    return sent, k


def _edits(query):
    return [(c.args[0] if c.args else c.kwargs.get("text")) for c in query.edit_message_text.await_args_list]


def _toasts(query):
    return [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]


def test_the_operators_sequence_switches_to_auto(bot, sess, cmd, tap):
    """Auto → Ask, then at once /mode auto and Yes: it used to do nothing
    (the post-relaunch window), and the next Yes relaunched in Ask."""
    cmd("/mode ask")
    assert sess.skip_perms is False
    assert sess.is_restarting(), "the post-relaunch quiet window is still open"

    sent, kwargs = _confirm(cmd("/mode auto"))
    query = tap(sent, kwargs, "✅ Yes, switch")

    assert sess.skip_perms is True
    assert [c.kwargs["skip_perms"] for c in bot.launches.await_args_list] == [False, True]
    # One message, changed in place: switching, then the card for the new
    # mode with the opposite switch (operator, 2026-10-01).
    assert _edits(query) == ["⚙️ Switching <b>jim</b> to 🤖 Auto mode…",
                             "✍️ <b>jim</b> is 🤖 Auto."]
    kb = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert [b.text for row in kb.inline_keyboard for b in row] == ["💬 Switch to Ask"]


def test_a_second_yes_on_a_used_card_never_switches_to_ask(bot, sess, cmd, tap):
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    tap(sent, kwargs, "✅ Yes, switch")
    assert sess.skip_perms is True
    bot.launches.reset_mock()

    query = tap(sent, kwargs, "✅ Yes, switch")      # the same card again

    bot.launches.assert_not_awaited()
    assert sess.skip_perms is True
    assert _toasts(query) == ["This card is out of date - nothing changed"]
    assert _edits(query) == [
        "⚠️ That card was out of date, so nothing changed.\n\n✍️ <b>jim</b> is 🤖 Auto."]


def test_a_card_with_no_record_does_nothing(bot, sess, cmd, tap):
    """A daemon restart forgets every card's record."""
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    bot._perms_pending.clear()

    query = tap(sent, kwargs, "✅ Yes, switch")

    bot.launches.assert_not_awaited()
    assert sess.skip_perms is False
    assert "out of date" in _toasts(query)[0]


def test_an_older_card_neither_uses_nor_clears_the_newer_one(bot, sess, cmd, tap):
    sess.skip_perms = False
    old_sent, old_kw = _confirm(cmd("/mode auto"))
    new_sent, new_kw = _confirm(cmd("/mode auto"))

    query = tap(old_sent, old_kw, "✅ Yes, switch")
    bot.launches.assert_not_awaited()
    assert "out of date" in _toasts(query)[0]
    tap(old_sent, old_kw, "↩️ Cancel")

    tap(new_sent, new_kw, "✅ Yes, switch")
    assert [c.kwargs["skip_perms"] for c in bot.launches.await_args_list] == [True]


def test_not_now_on_an_older_busy_card_keeps_the_newer_one(bot, sess, cmd, tap):
    sess.status = Status.BUSY

    def _busy_card(replies):
        a, k, sent = next(r for r in replies if "reply_markup" in r[1])
        assert a[0].startswith("⚙️ <b>jim</b> is busy."), a[0]
        return sent, k
    old_sent, old_kw = _busy_card(cmd("/mode ask"))
    new_sent, new_kw = _busy_card(cmd("/mode ask"))

    tap(old_sent, old_kw, "⏳ Not now")
    tap(new_sent, new_kw, "🛑 Stop task & switch")

    assert [c.kwargs["skip_perms"] for c in bot.launches.await_args_list] == [False]


def test_a_tap_without_a_message_id_is_not_this_cards(bot, sess, cmd, run_async):
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    data = next(b.callback_data for row in kwargs["reply_markup"].inline_keyboard
                for b in row if b.text == "✅ Yes, switch")
    query = MagicMock(data=data)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock(id=1)
    query.message = MagicMock(message_id=None)
    update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=CHAT, type="private")

    run_async(bot._handle_callback(update, MagicMock()))

    bot.launches.assert_not_awaited()
    assert "out of date" in _toasts(query)[0]
    assert _edits(query) == [
        "⚠️ That card was out of date, so nothing changed.\n\n✍️ <b>jim</b> is 💬 Ask."]


@pytest.mark.parametrize("failure", ["still_stopping", "launch_failed"])
def test_a_failed_relaunch_keeps_an_earlier_relaunchs_quiet_window(
        bot, sess, run_async, monkeypatch, failure):
    """A relaunch inside another's 10 s window that then fails must not
    close that window: the first process's late SessionEnd is still due."""
    run_async(bot._kill_and_relaunch_core(sess, target_skip_perms=False,
                                          interrupt_first=False))
    window = sess.restarting_until
    assert sess.is_restarting()
    if failure == "still_stopping":
        monkeypatch.setattr("pathlib.Path.is_socket", lambda self: True)
    else:
        monkeypatch.setattr("aipager.dtach.inject.launch_session",
                            AsyncMock(return_value=(False, "boom")))

    outcome = run_async(bot._kill_and_relaunch_core(sess, target_skip_perms=True,
                                                    interrupt_first=False))

    assert outcome.reason == failure
    assert sess.restarting_until == window
    assert sess.is_restarting()


def test_while_a_relaunch_runs_the_card_waits_and_then_works(bot, sess, cmd, tap):
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    sess.relaunch_in_flight = True

    query = tap(sent, kwargs, "✅ Yes, switch")

    bot.launches.assert_not_awaited()
    assert _toasts(query) == ["jim is restarting right now - tap again in a moment"]
    query.edit_message_text.assert_not_awaited()     # the card and its button stay

    sess.relaunch_in_flight = False
    tap(sent, kwargs, "✅ Yes, switch")
    assert sess.skip_perms is True


def test_a_switch_refused_by_the_core_says_why(bot, sess, cmd, tap):
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    bot._perms_switch_core = AsyncMock(return_value=RestartOutcome(
        ok=False, reason="already_restarting", label="jim"))

    query = tap(sent, kwargs, "✅ Yes, switch")

    assert _edits(query) == [
        "⚙️ Switching <b>jim</b> to 🤖 Auto mode…",
        "⚠️ <b>jim</b> is restarting right now - mode not changed. Try again in a moment."
        "\n\n✍️ <b>jim</b> is 💬 Ask."]


def test_switching_to_ask_while_a_relaunch_runs_says_so(bot, sess, cmd):
    """The Auto→Ask switch has no card: its "Switching…" message is edited."""
    sess.relaunch_in_flight = True

    replies = cmd("/mode ask")

    _a, _k, status = replies[0]
    bot.launches.assert_not_awaited()
    assert status.edit_text.await_args.args[0] == (
        "⚠️ <b>jim</b> is restarting right now - mode not changed. Try again in a moment."
        "\n\n✍️ <b>jim</b> is 🤖 Auto.")


def test_a_switch_on_a_session_that_is_not_running_says_so(bot, sess, cmd, tap):
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    bot._perms_switch_core = AsyncMock(return_value=RestartOutcome(
        ok=False, reason="not_live", label="jim"))

    query = tap(sent, kwargs, "✅ Yes, switch")

    assert _edits(query) == ["⚙️ Switching <b>jim</b> to 🤖 Auto mode…",
                             "⚠️ <b>jim</b> is not running - mode not changed."
                             "\n\n✍️ <b>jim</b> is 💬 Ask."]


def test_the_in_flight_flag_clears_even_when_the_relaunch_fails(bot, sess, monkeypatch, run_async):
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        run_async(bot._kill_and_relaunch_core(sess, target_skip_perms=False,
                                              interrupt_first=False))
    assert sess.relaunch_in_flight is False


def test_stop_and_switch_on_a_busy_session_says_why_it_did_not(bot, sess, cmd, tap):
    sess.status = Status.BUSY
    replies = cmd("/mode ask")
    a, kwargs, sent = next(r for r in replies if "reply_markup" in r[1])
    assert a[0].startswith("⚙️ <b>jim</b> is busy.")
    bot._perms_switch_core = AsyncMock(return_value=RestartOutcome(
        ok=False, reason="not_live", label="jim"))

    query = tap(sent, kwargs, "🛑 Stop task & switch")

    assert _edits(query) == ["⚙️ Switching <b>jim</b> to 💬 Ask mode…",
                             "⚠️ <b>jim</b> is not running - mode not changed."
                             "\n\n✍️ <b>jim</b> is 🤖 Auto."]
    assert NAME not in bot._perms_pending      # a switch was attempted: spent


def test_the_mode_cards_switch_then_yes_switches_to_auto(bot, sess, cmd, tap, run_async):
    """P4's /mode card: 🤖 Switch to Auto turns the card itself into the
    confirm, whose Yes switches, right after an earlier switch too; the
    same message ends as the card for Auto (operator, 2026-10-01)."""
    cmd("/mode ask")                               # Auto → Ask, window open
    replies = cmd("/mode")
    _a, kwargs, card = replies[0]

    query = tap(card, kwargs, "🤖 Switch to Auto")

    query.message.reply_text.assert_not_awaited()      # no second message
    confirm = query.edit_message_text.await_args
    assert confirm.args[0].startswith("⚙️ Switch <b>jim</b> to 🤖 Auto mode?")
    query = tap(card, confirm.kwargs, "✅ Yes, switch")
    assert sess.skip_perms is True
    assert _edits(query)[-1] == "✍️ <b>jim</b> is 🤖 Auto."
    query.message.reply_text.assert_not_awaited()


# ---- one message that changes in place (operator, 2026-10-01) -------------------

def _resolve(bot, data):
    return session_parity.resolve_short_cb(bot, CHAT, *data.split(":", 1))


def _buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def test_the_cards_switch_ends_as_the_card_for_the_new_mode(bot, sess, cmd, tap):
    """Auto → Ask from the card: the same message says switching (no
    buttons left to press), then shows Ask with a fresh Switch to Auto."""
    _a, kwargs, card = cmd("/mode")[0]

    query = tap(card, kwargs, "💬 Switch to Ask")

    query.message.reply_text.assert_not_awaited()
    calls = query.edit_message_text.await_args_list
    assert [c.args[0] for c in calls] == ["⚙️ Switching <b>jim</b> to 💬 Ask mode…",
                                         "✍️ <b>jim</b> is 💬 Ask."]
    assert calls[0].kwargs.get("reply_markup") is None
    [(text, data)] = _buttons(calls[1].kwargs["reply_markup"])
    assert text == "🤖 Switch to Auto"
    # The button carries the session's turn key as it is now.
    assert _resolve(bot, data) == (sess.name, f"modeauto{sess.turn_key}")
    assert sess.skip_perms is False


def test_cancel_returns_the_card_for_the_current_mode(bot, sess, cmd, tap):
    sess.skip_perms = False
    _a, kwargs, card = cmd("/mode")[0]
    tap(card, kwargs, "🤖 Switch to Auto")

    query = tap(card, {"reply_markup": bot._build_perms_confirm_keyboard(sess)}, "↩️ Cancel")

    bot.launches.assert_not_awaited()
    assert _edits(query) == ["✍️ <b>jim</b> is 💬 Ask."]
    assert _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])[0][0] == (
        "🤖 Switch to Auto")
    assert NAME not in bot._perms_pending


def test_stop_and_switch_works_on_a_card_older_than_the_busy_card(bot, sess, cmd, tap):
    """The busy prompt is the /mode card edited in place, so it can be older
    than the turn's busy card (which moves down the chat). Its record's turn
    decides, not the message ids: a message-id check would refuse it."""
    sess.status = Status.BUSY
    _a, kwargs, card = cmd("/mode")[0]
    sess.busy_msg_id = card.message_id + 1000           # the busy card moved below it
    tap(card, kwargs, "💬 Switch to Ask")
    assert bot._perms_pending[NAME].get("turn") == sess.turn_key

    query = tap(card, {"reply_markup": bot._build_perms_busy_keyboard(sess)},
                "🛑 Stop task & switch")

    assert [c.kwargs["skip_perms"] for c in bot.launches.await_args_list] == [False]
    assert _edits(query)[-1] == "✍️ <b>jim</b> is 💬 Ask."


def test_stop_and_switch_after_a_new_turn_changes_nothing(bot, sess, cmd, tap):
    sess.status = Status.BUSY
    _a, kwargs, card = cmd("/mode")[0]
    tap(card, kwargs, "💬 Switch to Ask")
    bot.registry.transition(sess.name, Status.IDLE)
    bot.registry.transition(sess.name, Status.BUSY)      # a new turn since

    query = tap(card, {"reply_markup": bot._build_perms_busy_keyboard(sess)},
                "🛑 Stop task & switch")

    bot.launches.assert_not_awaited()
    assert sess.skip_perms is True
    assert _edits(query) == [
        "⚠️ <b>jim</b> moved on to new work, so nothing changed.\n\n✍️ <b>jim</b> is 🤖 Auto."]


def test_yes_after_a_new_turn_changes_nothing(bot, sess, cmd, tap):
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    bot.registry.transition(sess.name, Status.BUSY)      # a new turn since
    bot.registry.transition(sess.name, Status.IDLE)

    query = tap(sent, kwargs, "✅ Yes, switch")

    bot.launches.assert_not_awaited()
    assert _edits(query)[0].startswith("⚠️ <b>jim</b> moved on to new work")


def test_not_now_returns_the_card(bot, sess, cmd, tap):
    sess.status = Status.BUSY
    _a, kwargs, card = cmd("/mode")[0]
    tap(card, kwargs, "💬 Switch to Ask")

    query = tap(card, {"reply_markup": bot._build_perms_busy_keyboard(sess)}, "⏳ Not now")

    bot.launches.assert_not_awaited()
    assert _edits(query) == ["✍️ <b>jim</b> is 🤖 Auto."]


def test_the_typed_command_replies_once_and_ends_in_the_card(bot, sess, cmd):
    replies = cmd("/mode ask")

    assert len(replies) == 1
    status = replies[0][2]
    last = status.edit_text.await_args
    assert last.args[0] == "✍️ <b>jim</b> is 💬 Ask."
    assert _buttons(last.kwargs["reply_markup"])[0][0] == "🤖 Switch to Auto"


def test_the_pickers_switch_changes_the_picker_in_place(bot, sess, cmd, tap):
    """`/mode ask` with no target and two sessions: the picker's button
    switches, and the picker itself becomes the result."""
    other = TrackedSession(name="claude-zzpermcard__t2", label="kim", status=Status.IDLE)
    other.scope_chat_id = CHAT
    bot.registry._sessions[other.name] = other
    bot.registry.last_active_session = ""
    _a, kwargs, picker = cmd("/mode ask")[0]

    query = tap(picker, kwargs, "jim")

    query.message.reply_text.assert_not_awaited()
    assert _edits(query)[-1] == "<b>jim</b> is 💬 Ask."
    assert sess.skip_perms is False


def _tap_with(bot, run_async, card, markup, text, *, edit_effect=None):
    data = next(b.callback_data for row in markup.inline_keyboard for b in row
                if b.text == text)
    query = MagicMock(data=data)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock(side_effect=edit_effect)
    query.from_user = MagicMock(id=1)
    query.message = MagicMock(message_id=card.message_id)
    replies = []

    async def _reply(*a, **k):
        sent = MagicMock(message_id=next(_ids))
        sent.edit_text = AsyncMock()
        replies.append((a, k, sent))
        return sent
    query.message.reply_text = AsyncMock(side_effect=_reply)
    update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=CHAT, type="private")
    run_async(bot._handle_callback(update, MagicMock()))
    return query, replies


def test_a_second_tap_on_the_same_switch_sends_nothing_new(bot, sess, cmd, tap, run_async):
    """Review-1 (001): the second tap re-renders the same confirm; Telegram
    answers "message is not modified", which is not a reason to reply."""
    sess.skip_perms = False
    _a, kwargs, card = cmd("/mode")[0]
    tap(card, kwargs, "🤖 Switch to Auto")

    from telegram.error import BadRequest
    query, replies = _tap_with(
        bot, run_async, card, kwargs["reply_markup"], "🤖 Switch to Auto",
        edit_effect=BadRequest("Message is not modified: specified new message "
                               "content and reply markup are exactly the same"))

    assert replies == []
    assert bot._perms_pending[NAME]["msg_id"] == card.message_id
    tap(card, {"reply_markup": bot._build_perms_confirm_keyboard(sess)}, "✅ Yes, switch")
    assert sess.skip_perms is True


def test_when_the_card_cannot_be_edited_one_reply_carries_every_step(
        bot, sess, cmd, run_async):
    """Review-1 (002): a failed edit falls back to one reply, and the later
    steps edit that reply, never the card again."""
    _a, kwargs, card = cmd("/mode")[0]

    query, replies = _tap_with(
        bot, run_async, card, kwargs["reply_markup"], "💬 Switch to Ask",
        edit_effect=RuntimeError("message to edit not found"))

    assert query.edit_message_text.await_count == 1          # tried once, then not again
    assert len(replies) == 1
    (a, _k, status) = replies[0]
    assert a[0] == "⚙️ Switching <b>jim</b> to 💬 Ask mode…"
    assert status.edit_text.await_args.args[0] == "✍️ <b>jim</b> is 💬 Ask."
    assert sess.skip_perms is False


def test_a_card_tap_on_a_starting_session_is_a_toast(bot, sess, cmd, tap):
    """Review-1 (003)."""
    _a, kwargs, card = cmd("/mode")[0]
    sess.status = Status.UNKNOWN

    query = tap(card, kwargs, "💬 Switch to Ask")

    bot.launches.assert_not_awaited()
    assert _toasts(query) == ["jim is still starting - try again in a moment"]
    query.edit_message_text.assert_not_awaited()
    query.message.reply_text.assert_not_awaited()


def test_a_card_tap_in_a_muted_chat_leaves_no_record(bot, sess, cmd, run_async):
    """Review-2 (001): a card the user cannot see changing must not hold a
    record a later tap could act on. Control: the same tap unmuted does."""
    from aipager.bot.flood import MUTE
    sess.skip_perms = False
    _a, kwargs, card = cmd("/mode")[0]

    def tap_in_chat():
        data = next(b.callback_data for row in kwargs["reply_markup"].inline_keyboard
                    for b in row if b.text == "🤖 Switch to Auto")
        query = MagicMock(data=data)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.from_user = MagicMock(id=1)
        query.message = MagicMock(message_id=card.message_id)
        query.message.chat = MagicMock(id=CHAT)
        query.message.reply_text = AsyncMock()
        update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
        update.effective_chat = MagicMock(id=CHAT, type="private")
        run_async(bot._handle_callback(update, MagicMock()))
        return query

    MUTE.mute(CHAT, 60)
    query = tap_in_chat()
    query.edit_message_text.assert_not_awaited()
    query.message.reply_text.assert_not_awaited()
    assert NAME not in bot._perms_pending

    MUTE.clear()
    tap_in_chat()
    assert bot._perms_pending[NAME]["msg_id"] == card.message_id


def test_when_yes_cannot_edit_the_card_one_message_in_this_chat_carries_the_rest(
        bot, sess, cmd, tap, run_async):
    """Review-2 (002): the fallback goes to the tapped chat, once, and the
    result edits it; it used to post each step to the main chat."""
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    posted = MagicMock(message_id=next(_ids))
    posted.edit_text = AsyncMock()
    bot._app.bot.send_message = AsyncMock(return_value=posted)

    query, _replies = _tap_with(bot, run_async, sent, kwargs["reply_markup"],
                                "✅ Yes, switch",
                                edit_effect=RuntimeError("message to edit not found"))

    assert query.edit_message_text.await_count == 1
    assert bot._app.bot.send_message.await_count == 1
    assert bot._app.bot.send_message.await_args.kwargs["chat_id"] == CHAT
    assert posted.edit_text.await_args.args[0] == "✍️ <b>jim</b> is 🤖 Auto."
    assert sess.skip_perms is True


def test_a_second_yes_during_the_switch_leaves_the_card_alone(bot, sess, cmd, run_async,
                                                               monkeypatch):
    """Review-3 (001): fired while the first Yes is relaunching, the second
    must not paint "out of date" over the card's "Switching…"."""
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    seen = {}

    async def _launch(*a, **k):
        # The second tap, handled while the first is inside the relaunch.
        data = next(b.callback_data for row in kwargs["reply_markup"].inline_keyboard
                    for b in row if b.text == "✅ Yes, switch")
        query = MagicMock(data=data)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.from_user = MagicMock(id=1)
        query.message = MagicMock(message_id=sent.message_id)
        query.message.reply_text = AsyncMock()
        update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
        update.effective_chat = MagicMock(id=CHAT, type="private")
        await bot._handle_callback(update, MagicMock())
        seen["toasts"] = _toasts(query)
        seen["edits"] = query.edit_message_text.await_count
        seen["replies"] = query.message.reply_text.await_count
        return True, ""
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _launch)

    query, _r = _tap_with(bot, run_async, sent, kwargs["reply_markup"], "✅ Yes, switch")

    assert seen == {"toasts": ["jim is restarting right now - tap again in a moment"],
                    "edits": 0, "replies": 0}
    assert _edits(query)[-1] == "✍️ <b>jim</b> is 🤖 Auto."


def test_yes_on_a_card_that_already_shows_the_step_sends_nothing(bot, sess, cmd, run_async):
    """Review-3 nit: "message is not modified" on the Yes path is not a
    reason to post a new message."""
    from telegram.error import BadRequest
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    bot._app.bot.send_message = AsyncMock()

    _tap_with(bot, run_async, sent, kwargs["reply_markup"], "✅ Yes, switch",
              edit_effect=BadRequest("Message is not modified"))

    bot._app.bot.send_message.assert_not_awaited()
    assert sess.skip_perms is True
