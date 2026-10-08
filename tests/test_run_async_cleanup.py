"""Roadmap 8.116: ``run_async`` shuts its loops down when the test ends.

A task a test left running on one of ``run_async``'s loops used to stay
pending on an abandoned loop until the garbage collector reached it, and
asyncio then logged "Task was destroyed but it is pending!" into whichever
test was running at that moment (one such test counted every WARNING and
failed for it). The fixture now cancels what is left and closes each loop,
the way ``asyncio.run`` ends, and only after the test: a test may still
look at a task between its calls.

The last two tests run in file order on purpose: the first leaves a task
running, the second checks the fixture's teardown cancelled it.
"""

from __future__ import annotations

import asyncio

from tests.conftest import LoopRunner


async def _leave_a_task_running():
    return asyncio.ensure_future(asyncio.sleep(3600))


def test_close_cancels_what_is_left_and_closes_every_loop():
    runner = LoopRunner()
    first = runner(_leave_a_task_running())
    second = runner(_leave_a_task_running())
    assert not first.done() and not second.done()    # a test may still look
    loops = list(runner.loops)
    runner.close()
    assert first.cancelled() and second.cancelled()
    assert all(loop.is_closed() for loop in loops)
    assert runner.loops == []


def test_close_waits_for_a_task_that_cleans_up_after_cancel():
    cleaned = []

    async def _cleans_up():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0)          # its own async cleanup runs
            cleaned.append(True)
            raise

    async def _start():
        return asyncio.ensure_future(_cleans_up())

    runner = LoopRunner()
    task = runner(_start())
    runner.close()
    assert cleaned == [True] and task.cancelled()


def test_a_finished_run_leaves_nothing_to_cancel():
    runner = LoopRunner()

    async def _value():
        return 7

    assert runner(_value()) == 7
    runner.close()
    assert runner.loops == []


_LEFT_RUNNING: list = []


def test_a_test_may_leave_a_task_running(run_async):
    task = run_async(_leave_a_task_running())
    _LEFT_RUNNING.append(task)
    assert not task.done()


def test_the_fixture_cancelled_it_when_that_test_ended():
    (task,) = _LEFT_RUNNING
    assert task.cancelled(), "run_async's teardown must cancel what a test left running"
    assert task.get_loop().is_closed()
