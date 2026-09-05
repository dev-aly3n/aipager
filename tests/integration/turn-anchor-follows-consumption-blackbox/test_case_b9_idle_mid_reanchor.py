"""Contract case table row B9: "session goes IDLE while the re-anchor is
mid-send" — "fresh card is finalised as Done immediately (never left
'Working'), no duplicate answer."

Best-effort black-box approximation: the true race (a live re-anchor's
own send in flight, cancelled mid-way by the finish path's
``_stop_animation``) is an internal scheduling detail this suite cannot
reach directly (entrypoints.md does not expose ``_reanchor_busy_card``
or ``_stop_animation``). What IS observable and asserted here is the
CONTRACT's own stated outcome: firing the live absorption detection and
the turn's own finish (Stop/idle_prompt) back to back — the narrowest
gap this suite can create between them — must still settle into exactly
one final answer and no card left showing a non-final ("Working")
state.
"""

from __future__ import annotations

import asyncio

from aipager import preferences as prefs
from aipager.state import Status

M1 = 1601
M2 = 1602


def test_absorption_immediately_followed_by_finish_settles_on_one_final_answer(
    wire_transport, rich_calls, mid_turn, append_queue_op, run_async,
):
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")

    async def _scenario():
        live_task = asyncio.create_task(bot.notify(sess, "assistant_text", {
            "delta": "...", "message_id": "tick-1", "index": 0, "final": False,
        }))
        await asyncio.sleep(0)  # give the live re-anchor a chance to start
        sess.status = Status.IDLE
        await bot.notify(sess, "idle_prompt", {"summary": "it was a test"})
        await live_task

    run_async(_scenario())

    send_rich_payloads = [p for m, p in rich_calls if m == "sendRichMessage"]
    assert len(send_rich_payloads) == 1, (
        f"exactly one answer must be sent, never a duplicate: {rich_calls}")
    assert send_rich_payloads[-1].get("reply_to_message_id") == M2

    edit_payloads = [p for m, p in rich_calls if m == "editMessageText"]
    if edit_payloads:
        last_markdown = edit_payloads[-1]["rich_message"]["markdown"]
        assert "Working" not in last_markdown, (
            f"the card must never settle showing a non-final state: "
            f"{last_markdown!r}")
