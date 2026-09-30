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
    assert _edits(query)[-1] == "🤖 <b>jim</b> is now in Auto mode."


def test_a_second_yes_on_a_used_card_never_switches_to_ask(bot, sess, cmd, tap):
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    tap(sent, kwargs, "✅ Yes, switch")
    assert sess.skip_perms is True
    bot.launches.reset_mock()

    query = tap(sent, kwargs, "✅ Yes, switch")      # the same card again

    bot.launches.assert_not_awaited()
    assert sess.skip_perms is True
    assert _toasts(query) == ["This card is out of date - send /mode again"]
    assert _edits(query) == ["⚠️ This card is out of date - send /mode again."]


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
    assert _edits(query) == ["⚠️ This card is out of date - send /mode again."]


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
        "⚠️ <b>jim</b> is restarting right now - mode not changed. Send /mode again in a moment."]


def test_switching_to_ask_while_a_relaunch_runs_says_so(bot, sess, cmd):
    """The Auto→Ask switch has no card: its "Switching…" message is edited."""
    sess.relaunch_in_flight = True

    replies = cmd("/mode ask")

    _a, _k, status = replies[0]
    bot.launches.assert_not_awaited()
    assert status.edit_text.await_args.args[0] == (
        "⚠️ <b>jim</b> is restarting right now - mode not changed. Send /mode again in a moment.")


def test_a_switch_on_a_session_that_is_not_running_says_so(bot, sess, cmd, tap):
    sess.skip_perms = False
    sent, kwargs = _confirm(cmd("/mode auto"))
    bot._perms_switch_core = AsyncMock(return_value=RestartOutcome(
        ok=False, reason="not_live", label="jim"))

    query = tap(sent, kwargs, "✅ Yes, switch")

    assert _edits(query) == ["⚠️ <b>jim</b> is not running - mode not changed."]


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

    assert _edits(query) == ["⚠️ <b>jim</b> is not running - mode not changed."]
    assert NAME not in bot._perms_pending      # a switch was attempted: spent


def test_the_mode_cards_switch_then_yes_switches_to_auto(bot, sess, cmd, tap, run_async):
    """P4's /mode card: 🤖 Switch to Auto replies with the confirm card,
    whose Yes switches, right after an earlier switch too."""
    cmd("/mode ask")                               # Auto → Ask, window open
    replies = cmd("/mode")
    _a, kwargs, card = replies[0]
    confirms = []

    async def _reply(*a, **k):
        sent = MagicMock(message_id=next(_ids))
        confirms.append((a, k, sent))
        return sent
    data = kwargs["reply_markup"].inline_keyboard[0][0].callback_data
    assert session_parity.resolve_short_cb(bot, CHAT, *data.split(":", 1))[1].startswith("modeauto")
    query = MagicMock(data=data)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock(id=1)
    query.message = MagicMock(message_id=card.message_id)
    query.message.reply_text = AsyncMock(side_effect=_reply)
    update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=CHAT, type="private")
    run_async(bot._handle_callback(update, MagicMock()))

    sent, k = _confirm(confirms)
    tap(sent, k, "✅ Yes, switch")
    assert sess.skip_perms is True
