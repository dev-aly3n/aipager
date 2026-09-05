"""Case table row B1 (design.md): a message sent while BUSY gets folded
into the turn already running ("absorbed mid-turn") instead of starting
its own turn. The live card must re-anchor from M1 to M2 the moment the
absorption is detected — before the turn even finishes.

MUST FAIL ON TODAY'S CODE (pre-feature): nothing reads the transcript's
``queue-operation`` lines today, so no delete/re-send ever happens; the
live card and the eventual answer stay under M1.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager.policy_snapshot import delete_notes, list_outstanding_notes
from aipager.state import Status

CHAT_ID = -2002


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def test_b1_absorbed_mid_turn_reanchors_live_card_and_answer(
    wired, mk_update, run_async, rich_calls, append_queue_op,
):
    bot, sess, injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    # M1 starts the turn from IDLE.
    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))
    assert injected == ["hello?"]
    assert sess.trigger_msg_id == 1
    c1 = sess.busy_msg_id
    assert c1 and c1 > 0
    assert sess.busy_card_trigger == 1
    first_send = bot._app.bot.send_message.await_args_list[0]
    assert first_send.kwargs["reply_to_message_id"] == 1

    # M2 arrives while BUSY — injected immediately (queue-handoff), but
    # R1 says the reply target must NOT move at send time.
    run_async(bot._handle_message(_update(mk_update, "no it was a test",
                                          message_id=2), MagicMock()))
    assert sess.status == Status.BUSY
    assert injected == ["hello?", "no it was a test"]
    assert sess.trigger_msg_id == 1, "R1: a send while BUSY must not move the target"

    # Both M1 and M2 wrote a per-message note (design.md "queue handoff"
    # writes one for every inbound message, including the one that
    # started the turn). In real life M1's OWN UserPromptSubmit hook
    # (it started a genuine turn — Claude Code fires one for it) matches
    # and deletes M1's note via `_match_and_promote` before M2 is ever
    # absorbed; simulate that pre-condition directly (this feature does
    # not touch that pre-existing pick-up path) so the prefix-run match
    # below isn't blocked by M1's own still-outstanding note sitting
    # ahead of M2's in oldest-first order.
    notes = list_outstanding_notes(sess.name)
    assert len(notes) == 2
    m1_note = next(n for n in notes if n.get("msg_id") == 1)
    m2_note = next(n for n in notes if n.get("msg_id") == 2)
    delete_notes(sess.name, [m1_note])
    body = m2_note["body"]

    # Claude Code folds M2 into the turn already running — the exact
    # transcript signal for that (design.md's signal table).
    append_queue_op(sess, "remove", "absorbed_mid_turn", body)

    # Detection runs wherever the transcript is already read mid-turn —
    # the assistant_text notify path (R4).
    run_async(bot.notify(sess, "assistant_text", {
        "delta": "continuing on the same turn", "message_id": "m-abs",
    }))

    assert sess.trigger_msg_id == 2, "R2: consumption moves the target"
    assert sess.busy_card_trigger == 2, "R3: the live card re-anchors"
    assert sess.busy_msg_id and sess.busy_msg_id != c1, (
        "R3: a NEW card must replace the stale one"
    )
    c2 = sess.busy_msg_id

    bot._app.bot.delete_message.assert_any_await(
        chat_id=CHAT_ID, message_id=c1,
    )
    reanchor_send = bot._app.bot.send_message.await_args_list[-1]
    assert reanchor_send.kwargs["reply_to_message_id"] == 2
    assert reanchor_send.kwargs.get("disable_notification") is True

    reaction_targets = [
        call.args[1] for call in bot._app.bot.set_message_reaction.await_args_list
        if call.args[2] == "👍"
    ]
    assert 2 in reaction_targets, "M2 gets a 👍 once consumed"

    # The turn finishes — the answer must follow M2, not M1.
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "it was indeed a test", "raw_md": "it was indeed a test",
    }))

    methods = [m for m, _p in rich_calls]
    assert "sendRichMessage" in methods
    answer_payload = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert answer_payload["reply_to_message_id"] == 2, (
        "the answer must reply to M2, the message Claude actually "
        f"consumed for this turn — rich_calls={rich_calls}"
    )
    assert sess.busy_msg_id != c2 or sess.busy_msg_id is None
