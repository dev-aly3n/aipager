"""A tool-less turn finishing in a chat in MINIMAL MODE (roadmap 8.32 R1).

The vm3 chat that produced the bare Done cards was in minimal mode — held
to 0.05 calls/s. Driven here through the daemon's REAL limiter: the rich
side runs the real ``_post`` onto an ``httpx.MockTransport``, the PTB side
the real ``process_request``, so what is asserted is what reached the
wire, not what the finish path attempted.

What must hold: the answer goes out, opening with the stats line; no card
edit leaves a "✅ · Done" card behind; and the card's delete — card
housekeeping, an ORNAMENT — is refused by minimal mode rather than taking
one of the chat's scarce tokens.
"""

from __future__ import annotations

import asyncio
import time

from aipager import config
from aipager import preferences as prefs
from aipager.bot import notify as notify_mod
from aipager.state import Status, TrackedSession

CHAT = 123456
ANSWER = "Nothing new from that last notice."


def _set_rate(limiter, clock, rate):
    limiter.restore([{
        "chat_id": CHAT, "rate": rate, "rate_earned_at": clock.wall,
        "backoff": 1.0, "last_429_at": None, "ban_stamps": [],
    }])


def test_a_toolless_turn_in_minimal_mode_sends_the_answer_and_spends_nothing_on_the_card(
    mk_bot, gated_bot, run_async, limiter, anim_clock, rich_http,
):
    bot = mk_bot()
    bot._app.bot = gated_bot

    async def _noop(*_a, **_kw):
        return None

    bot._maybe_update_bot_name = _noop
    _set_rate(limiter, anim_clock, config.FLOOD_MIN_RATE)
    assert limiter.minimal_mode(CHAT)

    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    sess.busy_msg_id = 10
    sess.busy_card_trigger = 7
    sess.trigger_msg_id = 7
    sess.busy_started_at = time.monotonic() - 5
    prefs.set_preference(CHAT, "layout", "card")
    before = limiter.snapshot()["chats"][0]["ornaments_suspended"]

    async def _scenario():
        await bot.notify(sess, "idle_prompt", {"summary": ANSWER})
        for _ in range(5):
            pending = [t for t in notify_mod._BACKGROUND_TASKS if not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)

    run_async(asyncio.wait_for(_scenario(), timeout=5))

    assert rich_http.endpoints() == ["sendRichMessage"]
    md = rich_http.requests[0][2]["rich_message"]["markdown"]
    assert md.startswith("💬 **jim** · Finished (")
    assert md.endswith(ANSWER)
    # No card edit and no delete reached Telegram: the ornament delete was
    # refused by minimal mode, counted as such.
    assert "deleteMessage" not in gated_bot.endpoints()
    assert "editMessageText" not in gated_bot.endpoints()
    after = limiter.snapshot()["chats"][0]["ornaments_suspended"]
    assert after == before + 1
    assert sess.busy_msg_id is None
