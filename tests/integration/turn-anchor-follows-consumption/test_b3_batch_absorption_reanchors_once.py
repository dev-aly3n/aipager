"""Case table row B3 (design.md): two messages (M2, M3) absorbed in the
same detection batch re-anchor exactly ONCE — ending under the LAST one
— never once per queue-operation line."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager.policy_snapshot import delete_notes, list_outstanding_notes
from aipager.state import Status

CHAT_ID = -2002


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def test_b3_two_absorptions_in_one_batch_reanchor_once_to_the_last(
    wired, mk_update, run_async, rich_calls, append_queue_op,
):
    bot, sess, injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))
    c1 = sess.busy_msg_id

    run_async(bot._handle_message(_update(mk_update, "part two", message_id=2),
                                  MagicMock()))
    run_async(bot._handle_message(_update(mk_update, "part three", message_id=3),
                                  MagicMock()))
    assert sess.trigger_msg_id == 1, "R1: neither send while BUSY moves the target"

    notes = list_outstanding_notes(sess.name)
    m1_note = next(n for n in notes if n.get("msg_id") == 1)
    m2_note = next(n for n in notes if n.get("msg_id") == 2)
    m3_note = next(n for n in notes if n.get("msg_id") == 3)
    delete_notes(sess.name, [m1_note])  # M1's own pick-up, see B1's test

    # Both M2 and M3 are absorbed together, in ONE batch — the same
    # transcript read (one assistant_text tick) sees both lines.
    append_queue_op(sess, "remove", "absorbed_mid_turn", m2_note["body"])
    append_queue_op(sess, "remove", "absorbed_mid_turn", m3_note["body"])

    run_async(bot.notify(sess, "assistant_text", {
        "delta": "continuing", "message_id": "m-batch",
    }))

    assert sess.trigger_msg_id == 3, "ends under the LAST consumed message"
    assert sess.busy_card_trigger == 3

    delete_calls = bot._app.bot.delete_message.await_args_list
    assert len(delete_calls) == 1, (
        f"expected exactly one re-anchor delete, got {delete_calls}"
    )
    assert delete_calls[0].kwargs["message_id"] == c1

    reanchor_sends = [
        c for c in bot._app.bot.send_message.await_args_list
        if c.kwargs.get("reply_to_message_id") == 3
    ]
    assert len(reanchor_sends) == 1, (
        f"expected exactly one re-anchor send, got "
        f"{bot._app.bot.send_message.await_args_list}"
    )

    reaction_targets = [
        call.args[1] for call in bot._app.bot.set_message_reaction.await_args_list
        if call.args[2] == "👍"
    ]
    assert 2 in reaction_targets and 3 in reaction_targets, (
        f"both M2 and M3 must get a 👍 — got {reaction_targets}"
    )

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "final", "raw_md": "final",
    }))
    answer_payload = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert answer_payload["reply_to_message_id"] == 3
