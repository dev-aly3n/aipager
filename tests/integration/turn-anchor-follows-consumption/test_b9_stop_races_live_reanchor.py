"""Case table row B9 (design.md): the session goes IDLE (Stop fires)
while a LIVE re-anchor (``final=False``, from a tick) is still mid-send.
``_stop_animation`` cancels the ticking task; the finish path's own
``final=True`` re-anchor call always runs strictly after that, and must
get ``sess.animate_lock`` cleanly once the cancelled call's ``async
with`` unwinds. The card that ends up live must be the ``final=True``
one — never left showing "Working" — with no exception escaping either
call.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from aipager.state import Status, TrackedSession


def _sess(busy_msg_id):
    s = TrackedSession(name="claude-x", label="x", status=Status.IDLE)
    s.scope_chat_id = -2002
    s.busy_msg_id = busy_msg_id
    s.busy_card_trigger = 1
    s.trigger_msg_id = 2
    return s


def test_b9_final_reanchor_wins_after_live_one_is_cancelled_mid_send(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    sess = _sess(busy_msg_id=555)

    entered_send = asyncio.Event()
    gate = asyncio.Event()  # never set — the live call blocks here until cancelled
    sends = []

    async def _post(method, payload, **_kw):
        # The move is one sendRichMessage carrying the whole card.
        if method != "sendRichMessage":
            return {"ok": True, "result": {"message_id": 999}}
        sends.append(payload)
        if len(sends) == 1:
            entered_send.set()
            await gate.wait()
        result = {"message_id": 9000 + len(sends)}
        if "reply_markup" in payload:
            result["reply_markup"] = payload["reply_markup"]
        return {"ok": True, "result": result}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    bot._app.bot.delete_message = AsyncMock()

    async def scenario():
        live_task = asyncio.create_task(
            bot._reanchor_busy_card(sess, 2, final=False),
        )
        sess.animate_task = live_task
        await entered_send.wait()
        assert sess.animate_lock.locked(), (
            "the live call must be holding the lock while mid-send"
        )
        # The old card must still be intact — the live call never got
        # far enough to touch it.
        assert sess.busy_msg_id == 555

        # Stop fires: cancel whatever is ticking.
        bot._stop_animation(sess)
        try:
            await live_task
        except asyncio.CancelledError:
            pass
        assert not sess.animate_lock.locked(), (
            "cancellation must release the lock, not leave it stuck"
        )
        assert sess.busy_msg_id == 555, (
            "the cancelled live call must never have mutated busy_msg_id"
        )

        # The finish path's OWN final=True call always runs strictly
        # after _stop_animation — it must complete cleanly.
        await bot._reanchor_busy_card(sess, 2, final=True)

    run_async(scenario())

    assert len(sends) == 2, "exactly one cancelled attempt, one that completed"
    assert sess.busy_msg_id == 9002, "the FINAL card is the one left live"
    bot._app.bot.delete_message.assert_awaited_once_with(
        chat_id=-2002, message_id=555,
        # The move's delete of the OLD card is INSTANT (operator,
        # 2026-09-29): two cards on screen for seconds is not cosmetic.
        rate_limit_args={"class": "instant"},
    )
    assert "reply_markup" not in sends[-1], (
        "the settled card must carry no Stop button — never left "
        f"showing 'Working': {sends[-1]}"
    )
