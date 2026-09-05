"""Case table row B7 (design.md): the daemon restarted (or otherwise
lost) the note between send and absorption. No detection fires — the
note simply isn't found — and the answer replies to M1: the SAME
degraded behaviour as today. A "no regression" pin, not a new feature."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager.policy_snapshot import delete_notes, list_outstanding_notes
from aipager.state import Status

CHAT_ID = -2002


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def test_b7_note_lost_before_absorption_degrades_to_m1_like_today(
    wired, mk_update, run_async, rich_calls, append_queue_op,
):
    bot, sess, injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))
    c1 = sess.busy_msg_id
    sess.stream_hook_live = True
    run_async(bot._handle_message(_update(mk_update, "no it was a test",
                                          message_id=2), MagicMock()))
    assert sess.trigger_msg_id == 1

    # Both notes are lost — the daemon "restarted" between send and
    # absorption (or the notes dir was otherwise wiped). Neither M1's
    # nor M2's note is on disk by the time the absorption line appears.
    notes = list_outstanding_notes(sess.name)
    delete_notes(sess.name, notes)
    assert list_outstanding_notes(sess.name) == []

    append_queue_op(sess, "remove", "absorbed_mid_turn", "no it was a test")

    run_async(bot.notify(sess, "assistant_text", {
        "delta": "continuing", "message_id": "m-lost",
    }))

    # No detection at all — the target never moves.
    assert sess.trigger_msg_id == 1
    assert sess.busy_card_trigger == 1
    assert sess.busy_msg_id == c1
    bot._app.bot.delete_message.assert_not_awaited()

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "degraded answer", "raw_md": "degraded answer",
    }))
    answer_payload = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert answer_payload["reply_to_message_id"] == 1
