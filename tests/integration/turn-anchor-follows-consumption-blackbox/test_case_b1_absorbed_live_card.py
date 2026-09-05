"""Contract case table row B1: "M1; M2 while BUSY; absorbed" — MUST FAIL
on today's code (per the contract's own case table and spec.md's
"defect" section).

"assert a delete_message(message_id=C1) AND a
send_message(reply_to_message_id=M2, disable_notification=True) happen;
busy_msg_id is the new id; busy_card_trigger == M2; the eventual answer
send/edit also carries M2."
"""

from __future__ import annotations

from aipager import preferences as prefs

M1 = 601
M2 = 602


def _mid_turn(bot, live_turn, send_update, send_text, run_async):
    """M1 starts a real turn (live card C1); M2 arrives while BUSY —
    R1 must leave trigger_msg_id at M1 and write M2's note."""
    sess, tp = live_turn(bot, "hello?", M1)
    c1 = sess.busy_msg_id
    bot._app.bot.send_message.reset_mock()
    bot._app.bot.delete_message.reset_mock()
    bot._app.bot.set_message_reaction.reset_mock()

    run_async(send_text(bot, send_update("no it was a test", M2)))
    assert sess.trigger_msg_id == M1, "setup assumption broke (R1 regressed already?)"
    bot._app.bot.send_message.reset_mock()
    bot._app.bot.delete_message.reset_mock()
    return sess, tp, c1


def test_absorption_deletes_the_old_card_and_sends_a_new_one_under_m2(
    wire_transport, rich_calls, live_turn, append_queue_op, tick,
    send_update, send_text, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = _mid_turn(bot, live_turn, send_update, send_text, run_async)

    append_queue_op(sess, "remove", "absorbed_mid_turn",
                    "[via Telegram · @owner]\nno it was a test")
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_awaited_once()
    delete_kwargs = bot._app.bot.delete_message.await_args.kwargs
    assert delete_kwargs.get("message_id") == c1, (
        f"the OLD card was not the one deleted: {delete_kwargs}")

    bot._app.bot.send_message.assert_awaited_once()
    send_kwargs = bot._app.bot.send_message.await_args.kwargs
    assert send_kwargs.get("reply_to_message_id") == M2
    assert send_kwargs.get("disable_notification") is True


def test_absorption_updates_busy_msg_id_and_busy_card_trigger(
    wire_transport, rich_calls, live_turn, append_queue_op, tick,
    send_update, send_text, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = _mid_turn(bot, live_turn, send_update, send_text, run_async)

    append_queue_op(sess, "remove", "absorbed_mid_turn",
                    "[via Telegram · @owner]\nno it was a test")
    tick(bot, run_async, sess)

    assert sess.busy_msg_id != c1, "busy_msg_id must now be the new card"
    assert sess.busy_msg_id is not None
    assert sess.busy_card_trigger == M2
    assert sess.trigger_msg_id == M2


def test_absorbed_message_gets_a_thumbs_up_reaction(
    wire_transport, rich_calls, live_turn, append_queue_op, tick,
    send_update, send_text, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = _mid_turn(bot, live_turn, send_update, send_text, run_async)
    bot._app.bot.set_message_reaction.reset_mock()

    append_queue_op(sess, "remove", "absorbed_mid_turn",
                    "[via Telegram · @owner]\nno it was a test")
    tick(bot, run_async, sess)

    calls = bot._app.bot.set_message_reaction.await_args_list
    assert any(c.args[1] == M2 and c.args[2] == "\U0001f44d" for c in calls), (
        f"M2 never got a \U0001f44d for being consumed: {calls}")


def test_the_eventual_answer_after_absorption_also_targets_m2(
    wire_transport, rich_calls, live_turn, append_queue_op, tick,
    send_update, send_text, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = _mid_turn(bot, live_turn, send_update, send_text, run_async)
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    append_queue_op(sess, "remove", "absorbed_mid_turn",
                    "[via Telegram · @owner]\nno it was a test")
    tick(bot, run_async, sess)
    rich_calls.clear()

    from aipager.state import Status
    sess.status = Status.IDLE  # the daemon transitions to IDLE at Stop
    run_async(bot.notify(sess, "idle_prompt", {"summary": "it was indeed a test"}))

    send_rich_payloads = [p for m, p in rich_calls if m == "sendRichMessage"]
    assert send_rich_payloads, f"no answer was sent at all: {rich_calls}"
    assert send_rich_payloads[-1].get("reply_to_message_id") == M2, (
        f"the answer after a mid-turn absorption did not reply to M2: "
        f"{send_rich_payloads[-1]}")
