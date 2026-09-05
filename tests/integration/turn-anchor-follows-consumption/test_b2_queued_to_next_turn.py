"""Case table row B2 (design.md): a message sent while BUSY is NOT
absorbed — Claude finishes the current turn first, answers, and only
THEN picks the second message up as a fresh turn.

Today the daemon overwrites ``trigger_msg_id`` at SEND time, so turn 1's
answer is threaded to M2 (the wrong message) instead of M1 — the card
for turn 2 happens to land on M2 anyway, purely because the target was
already moved, so that half looks right by coincidence.

MUST FAIL ON TODAY'S CODE (pre-feature): turn 1's answer replies to M2,
not M1.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager.state import Status

CHAT_ID = -2002


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def test_b2_turn1_answers_m1_turn2_card_sent_once_under_m2(
    wired, mk_update, run_async, rich_calls,
):
    bot, sess, injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    # M1 starts turn 1 from IDLE.
    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))
    assert sess.trigger_msg_id == 1

    # M2 arrives while turn 1 is still BUSY — R1 says the target stays put.
    run_async(bot._handle_message(_update(mk_update, "no it was a test",
                                          message_id=2), MagicMock()))
    assert sess.status == Status.BUSY
    assert sess.trigger_msg_id == 1, "R1: a send while BUSY must not move the target"

    # Turn 1 finishes — no absorption ever happened, so the answer must
    # still go under M1.
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "turn one's answer", "raw_md": "turn one's answer",
    }))

    turn1_answer = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert turn1_answer["reply_to_message_id"] == 1, (
        "turn 1's answer must reply to M1 — the message that started "
        f"IT, not whatever was sent afterward. rich_calls={rich_calls}"
    )
    assert sess.busy_msg_id is None
    assert sess.trigger_msg_id is None  # reply cycle complete

    rich_calls.clear()
    bot._app.bot.send_message.reset_mock()

    # Claude now picks M2 up as turn 2's own prompt — the hook's
    # queue_pickup signal (R2), which the daemon always applies
    # unconditionally (this path is exercised at the notify() level;
    # the hook-side emission ORDER itself is R6, covered separately).
    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [{"msg_id": 2, "chat_id": CHAT_ID, "raw_text": "no it was a test"}],
        "expired": [],
    }))
    assert sess.trigger_msg_id == 2

    # Turn 2 actually starts (the forwarded UserPromptSubmit, processed
    # AFTER queue_pickup per R6) — exactly one busy card, targeting M2.
    bot.registry.transition(sess.name, Status.BUSY)
    run_async(bot.notify(sess, "user_prompt_submit", {}))

    busy_sends = [
        c for c in bot._app.bot.send_message.await_args_list
        if c.kwargs.get("reply_markup") is not None
    ]
    assert len(busy_sends) == 1, (
        "turn 2's card must be sent exactly once — no correcting second "
        f"send. calls={bot._app.bot.send_message.await_args_list}"
    )
    assert busy_sends[0].kwargs["reply_to_message_id"] == 2
    assert sess.busy_card_trigger == 2

    # Turn 2 finishes — its own answer follows M2.
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "turn two's answer", "raw_md": "turn two's answer",
    }))
    turn2_answer = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert turn2_answer["reply_to_message_id"] == 2
