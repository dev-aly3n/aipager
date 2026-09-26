"""A scheduled restart keeps the update busy and the lock owned (8.36).

Review rev-iter1-001/002: after "restart scheduled" the job used to count
as finished, so a second update could start (and release the lock early),
and the restart watchdog — the only thing that gives up on a restart that
never happened — had no test.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import AsyncMock, MagicMock

from aipager import self_update
from aipager.bot import update_flow
from aipager.self_update import UpdateLock


def _lock_free() -> bool:
    lock = UpdateLock()
    free = lock.try_acquire()
    lock.release()
    return free


async def _scheduled(env):
    res = await env.start("aipager")
    assert res.ok
    await env.finish()
    assert env.manager.snapshot()["phase"] == "restart_scheduled"
    return res.job["id"]


def test_start_refused_while_restart_pending_and_lock_stays_held(env, run):
    async def scenario():
        first = await _scheduled(env)
        assert env.manager.busy is True
        for kind in ("claude", "aipager", "both"):
            res = await env.start(kind)
            assert (res.ok, res.error) == (False, "update_in_progress"), kind
            assert res.job["id"] == first
        # Nothing new ran, and the flock is still this job's.
        assert [c for c in env.calls if c[-1] == "update"] == []
        assert env.manager._lock.held is True
        assert env.manager._lock_owner == first
        assert _lock_free() is False
    run(scenario)
    assert len(env.upgrade_calls()) == 1


def test_update_cmd_while_restart_pending_says_restarting(env, run):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = "/update"
    update.message.chat = MagicMock()
    update.message.chat.id = env.chat_id
    update.message.reply_text = AsyncMock(return_value=env.message)
    update.effective_user = MagicMock()
    update.effective_user.id = env.chat_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = env.chat_id

    async def scenario():
        await _scheduled(env)
        await update_flow.handle_update_cmd(env.bot, update, MagicMock())
    run(scenario)
    replies = [c.args[0] for c in update.message.reply_text.await_args_list]
    assert len(replies) == 1
    assert "about to restart" in replies[0]
    assert not any("Check for updates" in r for r in replies)
    assert update.message.reply_text.await_args.kwargs.get("reply_markup") is None


# ----- the watchdog ---------------------------------------------------------------

def _fast_watchdog(env):
    env.monkeypatch.setattr(update_flow, "RESTART_WATCHDOG_SECONDS", 0.05)
    env.monkeypatch.setattr(self_update, "RESTART_DELAY_SECONDS", 0)


def test_watchdog_gives_up_stops_timer_releases_lock_clears_marker(env, run):
    _fast_watchdog(env)
    seen: list = []
    real_cancel = self_update.cancel_scheduled_restart

    def _cancel(unit):
        # The timer must be stopped while the lock is still held: once the
        # lock is free a later job may start, and a late timer would
        # restart the daemon in the middle of it.
        seen.append((unit, _lock_free(), self_update.UPDATE_MARKER_PATH.exists()))
        return real_cancel(unit)
    env.monkeypatch.setattr(self_update, "cancel_scheduled_restart", _cancel)

    async def scenario():
        await _scheduled(env)
        # Found by prefix: roadmap 8.45 put AccuracySec before --unit.
        unit = next(a for a in env.schedule_calls()[0]
                    if a.startswith("--unit=")).removeprefix("--unit=")
        await env.until(lambda: env.manager.snapshot()["phase"] == "failed")
        assert _lock_free() is True
        assert env.manager.busy is False
        return unit
    unit = run(scenario)
    assert seen == [(unit, False, True)]
    assert env.timer_stop_calls() == [
        ["/abs/bin/systemctl", "--user", "stop", f"{unit}.timer"]]
    assert not self_update.UPDATE_MARKER_PATH.exists()
    text = env.last_text()
    assert "The scheduled restart did not happen" in text
    assert "systemctl --user restart aipager.service" in text


def test_watchdog_releases_even_if_the_timer_stop_fails(env, run):
    _fast_watchdog(env)
    env.timer_stop_rc = 1

    async def scenario():
        await _scheduled(env)
        await env.until(lambda: env.manager.snapshot()["phase"] == "failed")
        assert _lock_free() is True
    run(scenario)
    assert len(env.timer_stop_calls()) == 1


def test_update_can_start_again_after_the_watchdog(env, run):
    _fast_watchdog(env)

    async def scenario():
        await _scheduled(env)
        await env.until(lambda: env.manager.snapshot()["phase"] == "failed")
        res = await env.start("claude")
        assert res.ok
        await env.finish()
    run(scenario)


def test_stale_watchdog_leaves_a_later_jobs_lock_and_marker_alone(env, run):
    _fast_watchdog(env)
    env.upgrade_release = threading.Event()

    async def scenario():
        # Job A schedules a restart; its watchdog gives up.
        env.upgrade_release.set()
        await _scheduled(env)
        job_a = env.manager._job
        await env.until(lambda: job_a.phase == "failed")
        stops_after_a = len(env.timer_stop_calls())
        # Job B now owns the lock (blocked inside its installer) and a
        # marker exists that is B's business, not A's.
        env.upgrade_release.clear()
        res_b = await env.start("aipager")
        assert res_b.ok
        await env.until(lambda: len(env.upgrade_calls()) == 2)
        self_update.write_marker({"job_id": res_b.job["id"], "from": "x", "to": "y"})
        # A's watchdog runs again late (e.g. a stray task): it must not
        # touch B's lock, B's marker, the timer or B's status.
        job_a.phase = "restart_scheduled"
        edits_before = len(env.edits)
        await env.manager._restart_watchdog(job_a)
        assert _lock_free() is False
        assert env.manager._lock_owner == res_b.job["id"]
        assert json.loads(self_update.UPDATE_MARKER_PATH.read_text())["job_id"] \
            == res_b.job["id"]
        assert len(env.timer_stop_calls()) == stops_after_a
        assert all("did not happen" not in e["text"] for e in env.edits[edits_before:])
        env.upgrade_release.set()
        await env.finish()
    run(scenario)


def test_a_finished_jobs_release_cannot_free_a_later_jobs_lock(env, run):
    env.upgrade_release = threading.Event()

    async def scenario():
        res = await env.start("aipager")
        assert res.ok
        await env.until(lambda: env.upgrade_calls())
        stale = update_flow._Job(id=res.job["id"] - 1, kind="claude", chat_id=env.chat_id,
                                 user_id=None, origin="chat", started_at=0.0)
        env.manager._release_lock(stale)
        assert env.manager._lock.held is True
        assert _lock_free() is False
        env.upgrade_release.set()
        await env.finish()
    run(scenario)
