"""Test item 2.5 — `animate_lock` serializes concurrent
`_send_busy_and_animate` callers so two coroutines can't both observe
``busy_msg_id is None`` and both send.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from aipager import telegram_bot as tb
from aipager.state import SessionRegistry, Status, TrackedSession


def _make_bot(registry):
    bot = tb.TelegramBot(registry)
    fake_bot = MagicMock()
    fake_bot.send_chat_action = AsyncMock()
    bot._app = MagicMock()
    bot._app.bot = fake_bot
    return bot


def test_animate_lock_serializes_concurrent_callers(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    registry._sessions["claude-jim"] = sess
    bot = _make_bot(registry)

    send_count = {"n": 0}

    async def _send_busy_returning_msg(sess_arg):
        # Simulate work that yields to the event loop, allowing a competing
        # coroutine to race in if no lock is held.
        await asyncio.sleep(0.01)
        send_count["n"] += 1
        return 1000 + send_count["n"]

    monkeypatch.setattr(bot, "send_busy", _send_busy_returning_msg)
    # _start_animation must set sess.animate_task to a live (non-done) task,
    # otherwise the "stale busy state" check at the top of
    # _send_busy_and_animate clears busy_msg_id and the second coroutine
    # proceeds — defeating the lock test. The production flow always
    # starts a real animation task.
    def _fake_start(s):
        async def _sleep_forever():
            await asyncio.sleep(3600)
        s.animate_task = asyncio.create_task(_sleep_forever())
    monkeypatch.setattr(bot, "_start_animation", _fake_start)
    monkeypatch.setattr(bot, "_stop_animation", lambda s: None)

    async def _both():
        await asyncio.gather(
            bot._send_busy_and_animate(sess),
            bot._send_busy_and_animate(sess),
        )
        # Clean up the fake long-running animate_task to avoid
        # "Task was destroyed but pending" warnings.
        if sess.animate_task and not sess.animate_task.done():
            sess.animate_task.cancel()
            try:
                await sess.animate_task
            except asyncio.CancelledError:
                pass

    run_async(_both())
    # Only ONE send_busy call survived the race
    assert send_count["n"] == 1
    assert sess.busy_msg_id == 1001
