"""Contract case table row B2: "M1; M2 while BUSY; queued to next turn"
— MUST FAIL on today's code (per the contract's own case table).

"turn 1's answer reply_to_message_id == M1 (today it's M2, because
trigger_msg_id was already overwritten at send time); turn 2's card is
sent exactly once with reply_to_message_id == M2 (no correcting second
send)."

Turn 2's start is driven through a real ``HookReceiver`` replaying
``queue_pickup`` THEN ``UserPromptSubmit`` — R6's own fix (queue_pickup
emitted before the UserPromptSubmit forward) is what makes the daemon
see the correct target already in place by the time it sends turn 2's
card, so replaying them in that (corrected) order and asserting a
SINGLE send is the daemon-side half of R6's contract.
"""

from __future__ import annotations

import json

from aipager import preferences as prefs
from aipager.state import Status

M1 = 1401
M2 = 1402


def test_turn_one_answer_replies_to_m1_when_m2_was_never_absorbed(
    wire_transport, rich_calls, mid_turn, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    # Turn 1 ends without ever absorbing M2 — no queue-operation line for
    # it at all; it will be picked up as its OWN turn later.
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "turn one done"}))

    send_rich_payloads = [p for m, p in rich_calls if m == "sendRichMessage"]
    assert send_rich_payloads, f"no answer sent: {rich_calls}"
    assert send_rich_payloads[-1].get("reply_to_message_id") == M1, (
        f"turn 1's answer must reply to M1, not the message queued for "
        f"the NEXT turn: {send_rich_payloads[-1]}")


def test_turn_two_card_is_sent_exactly_once_under_m2(
    wire_transport, rich_calls, mid_turn, hook_receiver, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "turn one done"}))

    recv = hook_receiver(bot)
    bot._app.bot.send_message.reset_mock()
    bot._app.bot.delete_message.reset_mock()

    # Claude Code dequeues M2, then submits it as turn 2's own prompt —
    # replayed in R6's corrected order (queue_pickup before the
    # UserPromptSubmit forward).
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "queue_pickup", "session": "claude-x",
        "consumed": [{"msg_id": M2, "chat_id": sess.scope_chat_id,
                      "raw_text": "second"}],
        "expired": [],
    }).encode()))
    assert sess.trigger_msg_id == M2

    turn2_tp = tp.parent / "turn2.jsonl"
    turn2_tp.write_bytes(b"")
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "UserPromptSubmit", "session": "claude-x",
        "prompt": "second", "transcript_path": str(turn2_tp),
    }).encode()))

    assert sess.status == Status.BUSY
    bot._app.bot.send_message.assert_awaited_once(), (
        "turn 2's card must be sent exactly once — no correcting second "
        "send")
    send_kwargs = bot._app.bot.send_message.await_args.kwargs
    assert send_kwargs.get("reply_to_message_id") == M2
    bot._app.bot.delete_message.assert_not_awaited(), (
        "a correctly-targeted first send has nothing to re-anchor away from")
    assert sess.busy_card_trigger == M2
