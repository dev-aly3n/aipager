"""Contract case table row B8: "delete of the old card fails" — "assert
the new send_message under the new target still happened and
busy_msg_id reflects it — the delete failure is swallowed, not
propagated."

Error-guessing: a plausible real-world failure (the old card was
already manually deleted by the user, or Telegram briefly 404s) must
never surface as an exception up through the notify() event or block
the re-anchor's own send.
"""

from __future__ import annotations

from telegram.error import BadRequest

M1 = 1201
M2 = 1202


def test_delete_failure_does_not_block_the_new_card_send(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    bot._app.bot.delete_message.side_effect = BadRequest("message to delete not found")

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    tick(bot, run_async, sess)  # must not raise

    bot._app.bot.send_message.assert_awaited_once()
    send_kwargs = bot._app.bot.send_message.await_args.kwargs
    assert send_kwargs.get("reply_to_message_id") == M2
    assert send_kwargs.get("disable_notification") is True


def test_delete_failure_still_updates_busy_msg_id_to_the_new_card(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    bot._app.bot.delete_message.side_effect = BadRequest("message to delete not found")

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    tick(bot, run_async, sess)

    assert sess.busy_msg_id != c1, "busy_msg_id must point at the NEW card"
    assert sess.busy_card_trigger == M2
    assert sess.trigger_msg_id == M2


def test_delete_failure_is_attempted_exactly_once_not_retried(
    wire_transport, rich_calls, mid_turn, append_queue_op, tick, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    bot._app.bot.delete_message.side_effect = BadRequest("gone already")

    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_awaited_once()
    assert bot._app.bot.delete_message.await_args.kwargs.get("message_id") == c1
