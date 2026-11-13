"""Iteration 2 gaps of the ⋮ Clear confirm (design.md success criteria 3
and 9, entrypoints.md confirm table):

- "another sender's message pending" gives the waiting answer. The
  precondition is built only through public paths: a first person's
  message goes through ``bot._handle_message`` (which leaves its own
  outstanding note as a side effect of typing it), and a second person
  taps the confirm. The same person's confirm is the control.
- A fast double tap on one confirm with NO SessionEnd in between (the
  session stays IDLE and alive): the spec says an already-cleared confirm
  must never act.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.state import Status

CHAT = 0
WRITER = 12345      # the person whose message is still on its way
TAPPER = 1          # someone else tapping the confirm
WAITING = "Claude is still finishing its work - clear the conversation when it's done."


def _query(data, *, user_id, message_id=42):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.message.reply_text = AsyncMock()
    query.from_user = MagicMock()
    query.from_user.id = user_id
    return query


def _cb_update(user_id, chat_id=CHAT):
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def _tap(run_async, bot, verb, *, user, name="claude-dev"):
    query = _query(f"{name}:{verb}", user_id=user)
    run_async(session_parity.handle_callback(bot, _cb_update(user), query, name, verb))
    return query


def _toasts(query):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in query.answer.await_args_list]


def _confirm_verb(run_async, bot, user):
    query = _tap(run_async, bot, "clear", user=user)
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    verbs = [b.callback_data.rsplit(":", 1)[1] for row in markup.inline_keyboard for b in row]
    return next(v for v in verbs if v.startswith("clearok"))


def _cleared(dm):
    return [c for c in dm.typed.await_args_list if c.args[1:2] == ("/clear",)]


@pytest.fixture
def pending(dm, mk_update, run_async, tmp_path):
    """``dev`` with WRITER's message typed into it and not yet picked up."""
    bot = dm.bot
    bot._app.bot = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot.registry.last_active_session = dm.sess.name
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"")
    dm.sess.transcript_path = str(transcript)
    run_async(bot._handle_message(
        mk_update("hello from the writer", message_id=5, user_id=WRITER), MagicMock()))
    # Typing it starts a turn; the turn ends while the writer's note is
    # still outstanding (nothing confirmed its pick-up).
    bot.registry.transition(dm.sess.name, Status.IDLE)
    return dm


def test_precondition_the_session_is_idle_again(pending):
    assert pending.sess.status == Status.IDLE


def test_precondition_the_writers_message_was_typed(pending):
    """Positive control: the note exists because the message really went in."""
    assert [c.args[1] for c in pending.typed.await_args_list] == ["hello from the writer"]


def test_confirm_by_another_person_while_a_message_is_on_its_way_answers_waiting(
        run_async, pending):
    verb = _confirm_verb(run_async, pending.bot, TAPPER)
    query = _tap(run_async, pending.bot, verb, user=TAPPER)
    assert _toasts(query) == [WAITING]


def test_confirm_by_another_person_while_a_message_is_on_its_way_types_nothing(
        run_async, pending):
    verb = _confirm_verb(run_async, pending.bot, TAPPER)
    _tap(run_async, pending.bot, verb, user=TAPPER)
    assert _cleared(pending) == []


def test_confirm_by_another_person_while_a_message_is_on_its_way_keeps_the_card(
        run_async, pending):
    verb = _confirm_verb(run_async, pending.bot, TAPPER)
    query = _tap(run_async, pending.bot, verb, user=TAPPER)
    query.edit_message_text.assert_not_awaited()


def test_confirm_by_the_writer_themself_still_clears(run_async, pending):
    """Control: only ANOTHER person's message makes the confirm wait."""
    verb = _confirm_verb(run_async, pending.bot, WRITER)
    _tap(run_async, pending.bot, verb, user=WRITER)
    assert len(_cleared(pending)) == 1


# ---- fast double tap, no SessionEnd in between --------------------------------

@pytest.mark.xfail(strict=True,
                   reason="review rev-iter1-001: double tap before SessionEnd types /clear twice")
def test_fast_double_tap_without_session_end_types_clear_once(run_async, dm):
    """Error guessing: two taps on the same confirm land before Claude Code's
    SessionEnd(clear) arrives. The session stays IDLE and alive throughout."""
    verb = _confirm_verb(run_async, dm.bot, TAPPER)
    _tap(run_async, dm.bot, verb, user=TAPPER)
    _tap(run_async, dm.bot, verb, user=TAPPER)
    assert len(_cleared(dm)) == 1
