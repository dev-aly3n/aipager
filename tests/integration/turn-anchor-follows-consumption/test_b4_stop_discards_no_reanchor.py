"""Case table row B4 (design.md): a message sent while BUSY, then the
turn is stopped (Escape / /stop) before it is ever consumed. The note
is dropped, not consumed — no re-anchor, no 👍, and the stop path's own
message replies to M1 (the target R1 never moved off)."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager.policy_snapshot import list_outstanding_notes
from aipager.state import Status

CHAT_ID = -2002


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def test_b4_stop_drops_the_note_without_reanchoring(
    wired, mk_update, run_async,
):
    bot, sess, injected = wired

    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))
    c1 = sess.busy_msg_id

    run_async(bot._handle_message(_update(mk_update, "no it was a test",
                                          message_id=2), MagicMock()))
    assert sess.trigger_msg_id == 1

    assert len(list_outstanding_notes(sess.name)) == 2  # M1's own + M2's

    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok

    # The note is gone — dropped, not consumed.
    assert list_outstanding_notes(sess.name) == []
    # No re-anchor: delete_message was never called with a NEW card
    # id — only the stop path's own edit (_edit_busy_raw) touched the
    # ORIGINAL card, never a send_message re-anchor.
    reanchor_sends = [
        c for c in bot._app.bot.send_message.await_args_list
        if c.kwargs.get("disable_notification") is True
    ]
    assert reanchor_sends == []
    bot._app.bot.delete_message.assert_not_awaited()

    # M2 never got a 👍 — it was discarded, not consumed.
    reaction_targets = [
        call.args[1] for call in bot._app.bot.set_message_reaction.await_args_list
        if call.args[2] == "👍"
    ]
    assert 2 not in reaction_targets

    assert sess.trigger_msg_id is None
    assert sess.busy_card_trigger is None
    assert sess.busy_msg_id is None
    assert sess.status == Status.IDLE
    assert c1 and c1 > 0  # sanity: a card really had been live
