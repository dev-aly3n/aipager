"""Case table row B8 (design.md): the delete of the old (now stale) card
raises. The new card must still be sent under the correct target, and
`busy_msg_id` must reflect it — the delete failure is swallowed, never
propagated, and the old message is simply left behind."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager.policy_snapshot import delete_notes, list_outstanding_notes

CHAT_ID = -2002


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def test_b8_old_card_delete_raises_new_card_still_sent(
    wired, mk_update, run_async, rich_calls, append_queue_op,
):
    bot, sess, injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))
    c1 = sess.busy_msg_id
    run_async(bot._handle_message(_update(mk_update, "no it was a test",
                                          message_id=2), MagicMock()))

    notes = list_outstanding_notes(sess.name)
    m1_note = next(n for n in notes if n.get("msg_id") == 1)
    m2_note = next(n for n in notes if n.get("msg_id") == 2)
    delete_notes(sess.name, [m1_note])
    append_queue_op(sess, "remove", "absorbed_mid_turn", m2_note["body"])

    bot._app.bot.delete_message.side_effect = RuntimeError("Telegram: message to delete not found")

    # Must not raise.
    run_async(bot.notify(sess, "assistant_text", {
        "delta": "continuing", "message_id": "m-abs",
    }))

    assert sess.trigger_msg_id == 2
    assert sess.busy_card_trigger == 2
    assert sess.busy_msg_id and sess.busy_msg_id != c1, (
        "the new card must still exist even though the old one's delete raised"
    )
    bot._app.bot.delete_message.assert_awaited()  # the delete WAS attempted
    reanchor_send = next(
        c for c in bot._app.bot.send_message.await_args_list
        if c.kwargs.get("reply_to_message_id") == 2
    )
    assert reanchor_send.kwargs.get("disable_notification") is True
