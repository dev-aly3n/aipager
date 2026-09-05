"""Contract case table row B3: "M2, M3 while BUSY, both absorbed (same
batch or in turn)" — "re-anchored once per batch, ends under M3" /
"exactly ONE delete_message/send_message re-anchor pair fires (not
two); busy_card_trigger ends at M3; both M2 and M3 got a thumbs-up
set_message_reaction call."

Also the boundary-value counterpart to B1 (n=1 consumed note) — B3 is
n=2 consumed in the SAME detection batch.
"""

from __future__ import annotations

M1 = 701
M2 = 702
M3 = 703


def test_two_absorptions_in_one_batch_reanchor_exactly_once(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1,
                            ("second", M2), ("third", M3))

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    append_queue_op(sess, "remove", "absorbed_mid_turn", "third")
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_awaited_once()
    bot._app.bot.send_message.assert_awaited_once()


def test_batch_absorption_ends_anchored_under_the_last_message(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1,
                            ("second", M2), ("third", M3))

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    append_queue_op(sess, "remove", "absorbed_mid_turn", "third")
    tick(bot, run_async, sess)

    assert sess.trigger_msg_id == M3, "must anchor to the LAST consumed message"
    assert sess.busy_card_trigger == M3

    send_kwargs = bot._app.bot.send_message.await_args.kwargs
    assert send_kwargs.get("reply_to_message_id") == M3


def test_batch_absorption_reacts_thumbs_up_on_both_messages(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1,
                            ("second", M2), ("third", M3))
    bot._app.bot.set_message_reaction.reset_mock()

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    append_queue_op(sess, "remove", "absorbed_mid_turn", "third")
    tick(bot, run_async, sess)

    calls = bot._app.bot.set_message_reaction.await_args_list
    thumbs = {c.args[1] for c in calls if c.args[2] == "\U0001f44d"}
    assert thumbs == {M2, M3}, f"expected both M2 and M3 to get \U0001f44d: {thumbs}"


def test_batch_absorption_routes_messages_to_the_session(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    """entrypoints.md: "each msg_id becomes routable" — track_message per
    consumed note."""
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1,
                            ("second", M2), ("third", M3))

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    append_queue_op(sess, "remove", "absorbed_mid_turn", "third")
    tick(bot, run_async, sess)

    assert bot.registry.get_session_by_msg(M2, sess.scope_chat_id) is sess
    assert bot.registry.get_session_by_msg(M3, sess.scope_chat_id) is sess
