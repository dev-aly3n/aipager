"""Tests for design.md Decision 8: `_send_busy_and_animate`'s stale-reset
decision is keyed on the live message stack's TOP KIND, not raw task
liveness — this is the actual fix for "one stuck compacting card
suppresses every later busy card on a session forever" (the reported
live bug, reproduced here at Status.IDLE with the compacting card's
animate task still alive).

The two ORIGINAL guards (live busy top blocks; dead busy top task
stale-resets) already have dedicated coverage in test_bot_animation.py
(test_send_busy_and_animate_skips_when_already_busy /
test_send_busy_and_animate_clears_stale_state) and are unchanged by this
feature — this file adds only the new `compacting`-top branch.
"""

from __future__ import annotations

import time
import asyncio
from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status, TrackedSession


def test_compacting_top_with_live_task_still_gets_a_fresh_busy_card(mk_bot, run_async):
    """The critical case: the compacting card's animate task is ALIVE
    (not dead), yet a fresh busy card must still be sent — proving the
    fix is a genuinely new branch keyed on kind, not merely a repeat of
    the dead-task stale-reset."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.push_compacting(msg_id=3218, now=0.0, deadline_seconds=None)

    loop = asyncio.new_event_loop()

    async def _long():
        await asyncio.sleep(100)

    sess.animate_task = loop.create_task(_long())
    assert not sess.animate_task.done()  # sanity: genuinely alive

    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    run_async(bot._send_busy_and_animate(sess))

    assert sess.busy_msg_id == 555
    assert sess.stack_top_kind() == "busy"
    bot._app.bot.send_message.assert_awaited_once()
    bot._start_animation.assert_called_once()
    loop.close()


def test_compacting_top_with_dead_task_also_gets_a_fresh_busy_card(mk_bot, run_async):
    """Decision 8's "regardless of task liveness" — the dead-task case
    self-heals identically to the live-task case above."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.push_compacting(msg_id=3218, now=0.0, deadline_seconds=None)

    loop = asyncio.new_event_loop()

    async def _done():
        return None

    sess.animate_task = loop.create_task(_done())
    loop.run_until_complete(sess.animate_task)
    assert sess.animate_task.done()  # sanity: genuinely dead

    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=556))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    run_async(bot._send_busy_and_animate(sess))

    assert sess.busy_msg_id == 556
    assert sess.stack_top_kind() == "busy"
    loop.close()


def test_compacting_top_reclaim_does_not_disturb_a_busy_layer_underneath_kind(
    mk_bot, run_async,
):
    """The reclaimed compacting entry is popped, not the busy layer that
    may still be beneath it — after the fresh card is sent, exactly one
    "busy" entry remains (the new one), not two."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_msg_id = 42  # stale busy layer from a previous, unrelated cycle
    sess.push_compacting(msg_id=42, now=0.0, deadline_seconds=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    run_async(bot._send_busy_and_animate(sess))

    assert sess.busy_msg_id == 99
    assert len(sess._live_stack) == 1
    assert sess._live_stack[0].kind == "busy"


# ===== the tick ceiling: the guard that stops a leaked loop OOMing the box ===

def test_compact_animation_stops_at_the_tick_ceiling(mk_bot, run_async, monkeypatch):
    """_animate_compact must terminate on its own even when nothing ever
    pops the stack and every edit keeps succeeding.

    This is the guard that matters when `asyncio.sleep` has been
    neutralised process-wide — which ~50 sites across 14 test files in
    this suite do, by patching the SHARED asyncio module object. Without
    a clock-independent ceiling, a leaked animation task spins as fast as
    the loop allows and grows an AsyncMock's `mock_calls` until the
    machine OOMs (observed: 13.9 GB, killed an unrelated 8 GiB VM).

    The fake edit is a tripwire, not just a spy: if the ceiling is
    removed, this test fails FAST with a clear error instead of hanging
    or re-triggering the OOM it exists to prevent.
    """
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    sess.push_compacting(42, time.monotonic(), deadline_seconds=None)

    # Scoped patches — never asyncio.sleep itself.
    monkeypatch.setattr("aipager.bot.animation.COMPACT_ANIMATE_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("aipager.bot.animation.COMPACT_ANIMATE_MAX_TICKS", 3)

    edits = []

    async def _edit(msg_id, text, **k):
        edits.append(text)
        if len(edits) > 40:
            raise RuntimeError(
                "compact animation ran unbounded — the tick ceiling is gone"
            )
        return True  # always succeeds: nothing else can end this loop

    monkeypatch.setattr(bot, "_edit_busy_raw", _edit)
    run_async(bot._animate_compact(sess))

    assert len(edits) == 3, f"expected the ceiling to stop it at 3, got {len(edits)}"
    assert all("Compacting" in t for t in edits)
