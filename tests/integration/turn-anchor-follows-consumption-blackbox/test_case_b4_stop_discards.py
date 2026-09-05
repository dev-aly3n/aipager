"""Contract case table row B4: "M2 while BUSY, then /stop" — "notes
dropped, no re-anchor" / "no delete_message/re-anchor send_message call
at all; the stop-path's own message replies to M1; the note for M2 is
gone from the notes dir (dropped, not consumed — no thumbs-up reaction
on M2)."

R4's own vocabulary table says a discard shows up in the transcript as
``remove`` with NO ``reason`` (Escape/``/stop``/``/clearqueue``) or
``popAll`` — this suite drives only the surfaces entrypoints.md lists,
so the discard mechanism itself (`/stop`'s own note cleanup, out of
this feature's scope per spec.md) is exercised through the transcript
line the contract says represents it, and the finish path is driven
directly through `bot.notify(sess, "idle_prompt", ...)` (spec.md's
"Out: ... the mixed-sender hold, the job model's timing" — the discard
UI itself isn't in scope; R4's *filter* on the transcript line is).
"""

from __future__ import annotations

from aipager import preferences as prefs
from aipager.state import Status

M1 = 801
M2 = 802


def test_bare_remove_triggers_no_reanchor(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))

    append_queue_op(sess, "remove", None, "second")  # discarded, no reason
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()
    assert sess.trigger_msg_id == M1
    assert sess.busy_msg_id == c1


def test_bare_remove_gives_no_thumbs_up(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    bot._app.bot.set_message_reaction.reset_mock()

    append_queue_op(sess, "remove", None, "second")
    tick(bot, run_async, sess)

    calls = bot._app.bot.set_message_reaction.await_args_list
    assert not any(c.args[2] == "\U0001f44d" for c in calls), (
        f"a discarded (bare remove) message must never get \U0001f44d: {calls}")


def test_after_a_discard_the_finish_path_still_answers_m1(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    append_queue_op(sess, "remove", None, "second")
    tick(bot, run_async, sess)
    rich_calls.clear()

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    send_rich_payloads = [p for m, p in rich_calls if m == "sendRichMessage"]
    assert send_rich_payloads, f"no answer sent: {rich_calls}"
    assert send_rich_payloads[-1].get("reply_to_message_id") == M1, (
        "a discarded message must never move the reply target away "
        f"from the turn's own message: {send_rich_payloads[-1]}")
