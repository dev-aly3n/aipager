"""A task a test leaves pending in a shared background-task set must not
reach the next test (tests/conftest.py ``_forget_tasks_a_test_left_running``).

The two rows run in file order: the first leaves a pending task behind on a
loop it abandons; the second finds nothing pending, then drains the sets the
way the finish-path tests do. Without the fixture the second finds the tasks
still pending (and a drain would fail with "The future belongs to a
different loop": the 2026-09-29 full-suite failure, 69 rows). Run alone,
the second row passes trivially.
"""

from __future__ import annotations

import asyncio
import weakref

from aipager.bot import animation, notify, session_ops


def test_a_test_leaves_a_pending_task_behind():
    loop = asyncio.new_event_loop()

    async def _leave():
        for tasks in (notify._BACKGROUND_TASKS, animation._CARD_TASKS):
            task = asyncio.create_task(asyncio.sleep(3600))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        await asyncio.sleep(0)

    loop.run_until_complete(_leave())
    pending = [t for t in (*notify._BACKGROUND_TASKS, *animation._CARD_TASKS)
               if not t.done()]
    assert len(pending) >= 2, "precondition: the tasks are left pending"


def test_the_next_test_finds_nothing_left_pending():
    left = [t for t in (*notify._BACKGROUND_TASKS, *animation._CARD_TASKS)
            if not t.done()]
    assert left == []

    async def _drain():   # what the finish-path tests do
        await asyncio.gather(*left, return_exceptions=True)

    asyncio.new_event_loop().run_until_complete(_drain())


# ---- keystroke watches (CI 2026-11-14, py3.13) ----------------------------
# session_ops._KEYSTROKE_WATCHES keeps a watch, and through it the test's
# bot, until a watch for the same session replaces it; a later test then ran
# the old objects' finalizers on its own loop (a teardown's select failed
# with EBADF). The fixture empties it and collects what it held, so the
# leftovers end with their own test.

_FINALIZED: list[str] = []


class _Bot:
    """Stands for the test's bot a watch keeps alive. It refers to itself,
    as a real bot does through its handlers, so only the cycle collector
    frees it."""

    def __init__(self):
        self.me = self


def test_a_test_leaves_a_keystroke_watch_behind():
    loop = asyncio.new_event_loop()
    bot = _Bot()
    weakref.finalize(bot, _FINALIZED.append, "bot")

    async def _watch(b):
        await asyncio.sleep(3600)

    async def _leave(b):
        session_ops._KEYSTROKE_WATCHES["claude-x1"] = asyncio.create_task(_watch(b))
        await asyncio.sleep(0)

    loop.run_until_complete(_leave(bot))
    loop.close()
    del bot
    assert "claude-x1" in session_ops._KEYSTROKE_WATCHES, "precondition: the watch is left"
    assert _FINALIZED == [], "precondition: the watch keeps the bot alive"


def test_the_next_test_finds_no_watch_and_the_old_bot_finalized():
    assert session_ops._KEYSTROKE_WATCHES == {}
    assert _FINALIZED == ["bot"]
