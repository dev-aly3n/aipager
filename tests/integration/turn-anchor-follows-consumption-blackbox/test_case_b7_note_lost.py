"""Contract case table row B7: "daemon restarted between send and
absorption (note lost)" — "no detection fires (the note simply isn't
found); the answer replies to M1 — same degraded behaviour as today,
asserted as a 'no regression' test."

Simulated the same way R7's already-swept-note test is: the note is
gone from the notes directory by the time the absorption line is read
(here because a restart lost it rather than because the sweep fired —
observably identical, per entrypoints.md's "assert a note file's
presence/absence directly with list_outstanding_notes").
"""

from __future__ import annotations

from aipager import policy_snapshot as ps
from aipager import preferences as prefs
from aipager.state import Status

M1 = 1101
M2 = 1102


def test_note_lost_produces_no_detection_and_no_reanchor(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))

    # The note is simply gone (as if a daemon restart lost the
    # in-memory bookkeeping that would otherwise still have it on disk
    # — observably: the note file just isn't there when the matcher
    # looks).
    lost = [n for n in ps.list_outstanding_notes("claude-x") if n["msg_id"] == M2]
    ps.delete_notes("claude-x", lost)

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()
    assert sess.busy_msg_id == c1
    assert sess.trigger_msg_id == M1


def test_note_lost_the_answer_still_replies_to_m1_degraded_but_not_broken(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    lost = [n for n in ps.list_outstanding_notes("claude-x") if n["msg_id"] == M2]
    ps.delete_notes("claude-x", lost)

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    tick(bot, run_async, sess)
    rich_calls.clear()

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "answer"}))

    send_rich_payloads = [p for m, p in rich_calls if m == "sendRichMessage"]
    assert send_rich_payloads
    assert send_rich_payloads[-1].get("reply_to_message_id") == M1, (
        "with the note lost, the answer must fall back to the turn's "
        f"own message (degraded, not broken): {send_rich_payloads[-1]}")
