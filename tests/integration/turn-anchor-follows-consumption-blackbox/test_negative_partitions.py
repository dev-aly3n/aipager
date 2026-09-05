"""Negative equivalence partitions over the transcript's queue-operation
vocabulary (design.md's signal table / R4 / R7) and the transcript's
own partial-write rules — every one of these must produce NO
observable change, per entrypoints.md: "Only remove/absorbed_mid_turn
and remove/delivered_to_agent are ever expected to change observable
state (a re-anchor); every other operation/reason combination... must
produce NO observable change."
"""

from __future__ import annotations

import pytest

from aipager import policy_snapshot as ps

M1 = 901
M2 = 902


def _unchanged(bot, sess, c1):
    bot._app.bot.delete_message.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()
    assert sess.busy_msg_id == c1
    assert sess.trigger_msg_id == M1
    assert sess.busy_card_trigger == M1


# ---- equivalence partitioning over `operation`/`reason` ------------------

@pytest.mark.parametrize("operation,reason", [
    ("enqueue", None),
    ("dequeue", None),
    ("remove", None),          # bare remove: discarded
    ("popAll", None),
    ("remove", "delivered_to_agent_typo"),  # not one of the two exact reasons
])
def test_non_absorbing_operations_never_reanchor(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
    operation, reason,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))

    append_queue_op(sess, operation, reason, "second")
    tick(bot, run_async, sess)

    _unchanged(bot, sess, c1)
    # And the note must still be there — nothing consumed it.
    assert any(n["msg_id"] == M2 for n in ps.list_outstanding_notes("claude-x")), (
        "an ignored operation must not remove the outstanding note")


# ---- content that matches no outstanding note -----------------------------

def test_absorption_line_content_matching_no_note_is_a_noop(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))

    append_queue_op(sess, "remove", "absorbed_mid_turn",
                    "this text was never sent by anyone")
    tick(bot, run_async, sess)

    _unchanged(bot, sess, c1)
    assert any(n["msg_id"] == M2 for n in ps.list_outstanding_notes("claude-x"))


# ---- R7: a note already swept before the absorption line is seen ---------

def test_absorption_of_an_already_swept_note_is_ignored(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    """R7: "The Stop sweep stays the safety net... a stale absorption
    line seen later (note already gone) is ignored." Simulated by
    removing M2's note out of band (as the 5s sweep would) before the
    absorption line is ever read."""
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))

    swept = [n for n in ps.list_outstanding_notes("claude-x") if n["msg_id"] == M2]
    assert swept, "setup assumption broke: no note to sweep"
    ps.delete_notes("claude-x", swept)

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()
    assert sess.busy_msg_id == c1
    assert sess.trigger_msg_id == M1


# ---- partial (mid-flush) line: no trailing newline ------------------------

def test_partial_trailing_line_without_newline_is_not_consumed_yet(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    """Boundary: Claude Code's transcript is written lazily (CLAUDE.md);
    a line still being flushed (no trailing \\n) must not be parsed as
    a complete queue-operation."""
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second", newline=False)
    tick(bot, run_async, sess)

    _unchanged(bot, sess, c1)
    assert any(n["msg_id"] == M2 for n in ps.list_outstanding_notes("claude-x"))


def test_completing_the_partial_line_then_consumes_it(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    """Same setup, but the missing newline arrives on a later flush —
    the line must now be picked up."""
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second", newline=False)
    tick(bot, run_async, sess)
    _unchanged(bot, sess, c1)  # still nothing, per the previous test

    with open(sess.stream_transcript_path, "ab") as fh:
        fh.write(b"\n")  # the flush completes the line
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_awaited_once()
    bot._app.bot.send_message.assert_awaited_once()
    assert sess.trigger_msg_id == M2


# ---- the same absorption line "seen twice" (idempotency) ------------------

def test_a_quiescent_transcript_produces_no_extra_effects_on_a_second_tick(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    """Whether or not a re-scan could ever technically re-present the
    same bytes, the outcome must be identical either way: the note was
    already deleted by the first successful match, so a second look at
    the same content can find nothing left to consume. Two consecutive
    ticks over an unchanged transcript must settle into exactly one
    reanchor, never two."""
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_awaited_once()
    bot._app.bot.send_message.assert_awaited_once()
    busy_msg_id_after_first_tick = sess.busy_msg_id
    trigger_after_first_tick = sess.trigger_msg_id
    busy_card_trigger_after_first_tick = sess.busy_card_trigger

    bot._app.bot.delete_message.reset_mock()
    bot._app.bot.send_message.reset_mock()

    tick(bot, run_async, sess)  # nothing new appended — a quiescent re-scan

    bot._app.bot.delete_message.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()
    assert sess.busy_msg_id == busy_msg_id_after_first_tick
    assert sess.trigger_msg_id == trigger_after_first_tick
    assert sess.busy_card_trigger == busy_card_trigger_after_first_tick
