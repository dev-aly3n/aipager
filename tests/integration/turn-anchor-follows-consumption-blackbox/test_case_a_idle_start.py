"""Contract case table row A: "M1 from IDLE."

design.md / the contract: "one send_message with reply_to_message_id=M1;
busy_card_trigger == trigger_msg_id == M1 after send." This is the
baseline every other row is a deviation from — send and consumption are
the same event for the message that starts a turn from IDLE (the
invariant's own stated exception), so no re-anchor should ever be
needed here.
"""

from __future__ import annotations

from aipager.state import Status

NAME = "claude-x"


def test_message_from_idle_sends_one_card_reply_to_that_message(
    wire_transport, rich_calls, install_session, send_update, send_text,
    run_async,
):
    bot, injected = wire_transport
    install_session(bot, status=Status.IDLE, busy_msg_id=None,
                    trigger_msg_id=None, busy_card_trigger=None)

    run_async(send_text(bot, send_update("hello?", 501)))

    bot._app.bot.send_message.assert_awaited_once()
    call = bot._app.bot.send_message.await_args
    assert call.kwargs.get("reply_to_message_id") == 501, (
        f"the busy card did not reply to the message that started the "
        f"turn: {call}")


def test_message_from_idle_sets_busy_card_trigger_and_trigger_msg_id_to_m1(
    wire_transport, rich_calls, install_session, send_update, send_text,
    run_async,
):
    bot, injected = wire_transport
    sess = install_session(bot, status=Status.IDLE, busy_msg_id=None,
                           trigger_msg_id=None, busy_card_trigger=None)

    run_async(send_text(bot, send_update("hello?", 501)))

    assert sess.trigger_msg_id == 501
    assert sess.busy_card_trigger == 501, (
        "send_busy must record which message the freshly-sent card is "
        "anchored to")


def test_message_from_idle_never_deletes_anything(
    wire_transport, rich_calls, install_session, send_update, send_text,
    run_async,
):
    """Boundary: the very first card of a turn has nothing to re-anchor
    away from — a delete here would be a spurious extra call."""
    bot, injected = wire_transport
    install_session(bot, status=Status.IDLE, busy_msg_id=None,
                    trigger_msg_id=None, busy_card_trigger=None)

    run_async(send_text(bot, send_update("hello?", 501)))

    bot._app.bot.delete_message.assert_not_awaited()
