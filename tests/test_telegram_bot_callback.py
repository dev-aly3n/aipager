"""Test items 2.7 (eager `query.answer()`) and 2.6 (cancel animation
tasks during `bot.stop()`).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from telegram.error import BadRequest

from aipager.bot import TelegramBot
from aipager.state import SessionRegistry, TrackedSession


# ----- 2.7 — _safe_answer swallows BadRequest -----

def test_safe_answer_swallows_query_too_old(run_async):
    query = MagicMock()
    query.answer = AsyncMock(
        side_effect=BadRequest("Bad Request: query is too old"),
    )
    # Should not raise
    run_async(TelegramBot._safe_answer(query, "irrelevant"))


def test_safe_answer_swallows_already_answered(run_async):
    query = MagicMock()
    query.answer = AsyncMock(
        side_effect=BadRequest("Bad Request: query_id_invalid"),
    )
    run_async(TelegramBot._safe_answer(query))


def test_safe_answer_passes_text_through_when_ok(run_async):
    query = MagicMock()
    query.answer = AsyncMock()
    run_async(TelegramBot._safe_answer(query, "Killing jim..."))
    query.answer.assert_awaited_once_with("Killing jim...")


# ----- 2.6 — bot.stop() cancels animation tasks -----

def test_bot_stop_cancels_animate_tasks(run_async):
    registry = SessionRegistry()

    async def _outer():
        # Build two sessions with live animate_tasks
        async def _idle():
            await asyncio.sleep(3600)

        sess1 = TrackedSession(name="claude-jim", label="jim")
        sess1.animate_task = asyncio.create_task(_idle())
        sess2 = TrackedSession(name="claude-john", label="john")
        sess2.animate_task = asyncio.create_task(_idle())
        registry._sessions[sess1.name] = sess1
        registry._sessions[sess2.name] = sess2

        bot = TelegramBot(registry)
        # Mock _app and its updater/stop methods.
        bot._app = MagicMock()
        bot._app.updater.stop = AsyncMock()
        bot._app.stop = AsyncMock()
        bot._app.shutdown = AsyncMock()

        await bot.stop()
        # Both animate tasks should be cancelled and done.
        assert sess1.animate_task.cancelled() or sess1.animate_task.done()
        assert sess2.animate_task.cancelled() or sess2.animate_task.done()

    run_async(_outer())


def test_bot_stop_safe_when_no_animate_tasks(run_async):
    """No sessions have active animate_task — stop should still work."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.animate_task = None
    registry._sessions[sess.name] = sess

    async def _outer():
        bot = TelegramBot(registry)
        bot._app = MagicMock()
        bot._app.updater.stop = AsyncMock()
        bot._app.stop = AsyncMock()
        bot._app.shutdown = AsyncMock()
        await bot.stop()
        bot._app.shutdown.assert_awaited_once()

    run_async(_outer())
