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

from aipager.bot import animation, notify


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
