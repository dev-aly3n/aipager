"""Black box: the restart job (``UpdateManager.start_restart``), its busy
gate and every exit (design.md section 5 and its success criteria).

The daemon's stop is BBEnv's recorder attached through
``self_restart.attach``: a stop request records the card text at that
moment, so "final edit before the request" is observable."""

from __future__ import annotations

import os
import time

import pytest

from aipager import self_update
from aipager.state import Status

from bb_helpers import BBEnv, run_scenario


@pytest.fixture
def bb(monkeypatch, tmp_path, mk_bot):
    return BBEnv(monkeypatch, tmp_path, mk_bot)

RESTART_NOW_BTN = "_:up:rs"


def _ready(bb):
    bb.launch_as_daemon()
    bb.attach_stop()


def _go(bb, body):
    return run_scenario(bb, body)


async def _start_and_finish(bb):
    res = await bb.start_restart()
    assert res.ok, res
    await bb.finish()
    return res


def _lock_free() -> bool:
    lock = self_update.UpdateLock()
    free = lock.try_acquire()
    if free:
        lock.release()
    return free


# ----- idle: straight to the stop --------------------------------------------------------

def test_idle_restart_requests_one_stop(bb):
    _ready(bb)
    bb.add_session()
    _go(bb, lambda: _start_and_finish(bb))
    assert len(bb.stops) == 1


def test_idle_restart_job_kind_is_restart(bb):
    _ready(bb)
    res = _go(bb, lambda: _start_and_finish(bb))
    assert res.job["kind"] == "restart"


def test_idle_restart_phase_restart_scheduled(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.manager.snapshot()["phase"] == "restart_scheduled"


def test_final_card_says_restarting_before_the_stop(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    assert "restarting" in bb.stops[0]


def test_final_card_names_both_versions_before_the_stop(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    assert "1.0.1" in bb.stops[0] and "1.1.0" in bb.stops[0]


def test_first_card_text_is_the_check(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.texts()[0].startswith("⏳ Checking the installed aipager")


def test_marker_written_with_from_and_to(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    m = self_update.read_and_clear_marker() or {}
    assert (m.get("from"), m.get("to")) == ("1.0.1", "1.1.0")


def test_marker_names_the_card_message(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    m = self_update.read_and_clear_marker() or {}
    assert bb.message.message_id in json_values(m)


def json_values(d):
    out = []
    stack = [d]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
        else:
            out.append(x)
    return out


def test_restart_scheduled_keeps_the_manager_busy(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.manager.busy is True


def test_restart_scheduled_refuses_a_second_restart(bb):
    _ready(bb)

    async def body():
        await _start_and_finish(bb)
        return await bb.start_restart()
    res = _go(bb, body)
    assert (res.ok, res.error) == (False, "update_in_progress")


def test_in_place_restart_schedules_nothing_with_systemd(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.schedule_calls() == []


def test_probe_happens_before_the_stop(bb):
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    assert len(bb.restart_probe_calls()) >= 1


# ----- the probe's exits --------------------------------------------------------------------

def test_broken_install_fails(bb):
    _ready(bb)
    bb.restart_probe = (None, False)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.manager.snapshot()["phase"] == "failed"


def test_broken_install_requests_no_stop(bb):
    _ready(bb)
    bb.restart_probe = (None, False)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.stops == []


def test_broken_install_releases_the_lock(bb):
    _ready(bb)
    bb.restart_probe = (None, False)

    async def body():
        await _start_and_finish(bb)
        return _lock_free()
    assert _go(bb, body) is True


def test_broken_install_card_says_still_runs(bb):
    _ready(bb)
    bb.restart_probe = (None, False)
    _go(bb, lambda: _start_and_finish(bb))
    assert "does not load" in bb.last_text() and "1.0.1" in bb.last_text()


def test_same_version_is_done_nothing_to_restart(bb):
    _ready(bb)
    bb.restart_probe = ("1.0.1", True)
    _go(bb, lambda: _start_and_finish(bb))
    assert "nothing to restart" in bb.last_text()


def test_same_version_phase_done(bb):
    _ready(bb)
    bb.restart_probe = ("1.0.1", True)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.manager.snapshot()["phase"] == "done"


def test_same_version_requests_no_stop(bb):
    _ready(bb)
    bb.restart_probe = ("1.0.1", True)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.stops == []


def test_older_install_requests_no_stop(bb):
    """Disk older than running: not newer, so nothing to restart into."""
    _ready(bb)
    bb.restart_probe = ("1.0.0", True)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.stops == []


def test_same_version_releases_the_lock(bb):
    _ready(bb)
    bb.restart_probe = ("1.0.1", True)

    async def body():
        await _start_and_finish(bb)
        return _lock_free()
    assert _go(bb, body) is True


# ----- request refused (no daemon stop attached) -------------------------------------------

def test_request_refused_fails_the_job(bb):
    bb.launch_as_daemon()            # nothing attached: request() returns False
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.manager.snapshot()["phase"] == "failed"


def test_request_refused_clears_the_marker(bb):
    bb.launch_as_daemon()
    _go(bb, lambda: _start_and_finish(bb))
    assert self_update.read_and_clear_marker() is None


def test_request_refused_releases_the_lock(bb):
    bb.launch_as_daemon()

    async def body():
        await _start_and_finish(bb)
        return _lock_free()
    assert _go(bb, body) is True


def test_request_refused_card_says_could_not_restart(bb):
    bb.launch_as_daemon()
    _go(bb, lambda: _start_and_finish(bb))
    assert "could not restart itself" in bb.last_text()


# ----- plans that cannot restart -------------------------------------------------------------

def test_mainpid_mismatch_ends_done_with_steps(bb):
    _ready(bb)
    bb.set_under_unit(True)
    bb.main_pid = os.getpid() + 1
    _go(bb, lambda: _start_and_finish(bb))
    assert "aipager start" in bb.last_text()


def test_mainpid_mismatch_requests_no_stop(bb):
    _ready(bb)
    bb.set_under_unit(True)
    bb.main_pid = os.getpid() + 1
    _go(bb, lambda: _start_and_finish(bb))
    assert (bb.stops, bb.schedule_calls()) == ([], [])


def test_systemd_automatic_restart_job_schedules_through_systemd_run(bb):
    _ready(bb)
    bb.set_under_unit(True)
    _go(bb, lambda: _start_and_finish(bb))
    assert len(bb.schedule_calls()) == 1


def test_systemd_automatic_restart_job_requests_no_in_place_stop(bb):
    _ready(bb)
    bb.set_under_unit(True)
    _go(bb, lambda: _start_and_finish(bb))
    assert bb.stops == []


def test_systemd_killmode_unsafe_schedules_nothing(bb):
    _ready(bb)
    bb.set_under_unit(True)
    bb.killmode = "control-group"
    _go(bb, lambda: _start_and_finish(bb))
    assert (bb.schedule_calls(), bb.stops) == ([], [])


def test_systemd_killmode_unsafe_shows_systemctl_command(bb):
    _ready(bb)
    bb.set_under_unit(True)
    bb.killmode = "control-group"
    _go(bb, lambda: _start_and_finish(bb))
    assert "systemctl --user restart aipager" in bb.last_text()


def test_no_exec_target_ends_with_terminal_steps(bb, tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "executable", str(tmp_path / "gone" / "python3"))
    _ready(bb)
    _go(bb, lambda: _start_and_finish(bb))
    assert "Ctrl+C" in bb.last_text() and bb.stops == []


# ----- the busy gate ---------------------------------------------------------------------------

BLOCKERS = {
    "busy": dict(status=Status.BUSY),
    "interactive": dict(status=Status.INTERACTIVE),
    "background_job": dict(job_continuation_active=True),
    "busy_card": dict(busy_msg_id=4242),
    "tool_in_flight": "tool",
    "pending_permission": dict(pending_permission={"tool": "Bash"}),
    "held_answer": "held",
}


def _block(bb, kind):
    spec = BLOCKERS[kind]
    if spec == "tool":
        return bb.add_session(pending_tool_started_at=time.monotonic())
    if spec == "held":
        from aipager.bot import held
        held.HELD.hold(chat_id=bb.chat_id, session="claude-web", label="web",
                       rich_text="answer", plain_text="answer")
        return bb.add_session()
    return bb.add_session(**spec)


@pytest.mark.parametrize("kind", sorted(BLOCKERS))
def test_each_blocker_keeps_the_job_waiting(bb, kind):
    _ready(bb)
    _block(bb, kind)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        await bb.drain(10)
        phase = bb.manager.snapshot()["phase"]
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        return phase
    assert _go(bb, body) == "waiting_for_idle"


@pytest.mark.parametrize("kind", sorted(BLOCKERS))
def test_each_blocker_prevents_the_stop(bb, kind):
    _ready(bb)
    _block(bb, kind)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        await bb.drain(10)
        stops = list(bb.stops)
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        return stops
    assert _go(bb, body) == []


def test_waiting_card_offers_restart_now_and_cancel(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        await bb.until(lambda: any(d and d.startswith("_:up:now:")
                                   for d in bb.last_markup_data()))
        data = bb.last_markup_data()
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        return data
    data = _go(bb, body)
    assert any(d.startswith("_:up:stop:") for d in data)


def test_blocker_clearing_lets_the_restart_proceed(bb):
    _ready(bb)
    sess = bb.add_session(status=Status.BUSY)

    async def body():
        await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        sess.status = Status.IDLE
        await bb.finish()
    _go(bb, body)
    assert len(bb.stops) == 1


def test_restart_now_override_skips_the_wait(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        assert bb.manager.control("restart-now", res.job["id"]).ok
        await bb.finish()
    _go(bb, body)
    assert len(bb.stops) == 1


def test_cancel_ends_cancelled(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
    _go(bb, body)
    assert bb.manager.snapshot()["phase"] == "cancelled"


def test_cancel_requests_no_stop(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
    _go(bb, body)
    assert bb.stops == []


def test_cancel_card_says_still_runs(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
    _go(bb, body)
    assert "Restart cancelled" in bb.last_text() and "1.0.1" in bb.last_text()


def test_cancel_releases_the_lock(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        return _lock_free()
    assert _go(bb, body) is True


def test_cancel_leaves_no_marker(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
    _go(bb, body)
    assert self_update.read_and_clear_marker() is None


def test_gate_timeout_phase(bb, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "gate_timeout")
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
    _go(bb, body)
    assert bb.stops == []


def test_gate_timeout_offers_now_wait_and_cancel(bb, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "gate_timeout")
        await bb.until(lambda: any(d and d.startswith("_:up:wait:")
                                   for d in bb.last_markup_data()))
        data = bb.last_markup_data()
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        return {d.rsplit(":", 1)[0] for d in data if d}
    assert {"_:up:now", "_:up:wait", "_:up:stop"} <= _go(bb, body)


def test_gate_timeout_unanswered_auto_cancels(bb, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(self_update, "GATE_DECISION_MAX_SECONDS", 0.1)
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        await bb.start_restart()
        await bb.finish()
    _go(bb, body)
    assert (bb.manager.snapshot()["phase"], bb.stops) == ("cancelled", [])


def test_gate_timeout_restart_now_proceeds(bb, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "gate_timeout")
        bb.manager.control("restart-now", res.job["id"])
        await bb.finish()
    _go(bb, body)
    assert len(bb.stops) == 1


def test_second_probe_counts_install_broken_while_waiting(bb):
    """R4: the probe is repeated after the gate; a broken install found
    there ends the job without a stop."""
    _ready(bb)
    sess = bb.add_session(status=Status.BUSY)

    async def body():
        await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        bb.restart_probe = (None, False)
        sess.status = Status.IDLE
        await bb.finish()
    _go(bb, body)
    assert (bb.manager.snapshot()["phase"], bb.stops) == ("failed", [])


def test_shutdown_during_gate_requests_no_stop(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        await bb.manager.shutdown()
        await bb.finish()
    _go(bb, body)
    assert bb.stops == []


def test_shutdown_during_gate_leaves_the_marker_for_the_next_start(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        await bb.manager.shutdown()
        await bb.finish()
    _go(bb, body)
    m = self_update.read_and_clear_marker() or {}
    assert (m.get("from"), m.get("to")) == ("1.0.1", "1.1.0")


# ----- single flight ---------------------------------------------------------------------------

def test_start_restart_refused_while_shutting_down(bb):
    _ready(bb)

    async def body():
        await bb.manager.shutdown()
        return await bb.start_restart()
    res = _go(bb, body)
    assert (res.ok, res.error) == (False, "shutting_down")


def test_start_restart_refused_while_lock_held_elsewhere(bb):
    _ready(bb)
    other = self_update.UpdateLock()
    assert other.try_acquire()
    try:
        res = _go(bb, bb.start_restart)
    finally:
        other.release()
    assert (res.ok, res.error) == (False, "update_in_progress")


def test_start_restart_lock_held_elsewhere_starts_no_probe(bb):
    _ready(bb)
    other = self_update.UpdateLock()
    assert other.try_acquire()
    try:
        _go(bb, bb.start_restart)
    finally:
        other.release()
    assert bb.restart_probe_calls() == []


def test_two_concurrent_starts_make_one_job(bb):
    import asyncio
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        a, b = await asyncio.gather(bb.start_restart(), bb.start_restart())
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        if bb.manager.snapshot():
            bb.manager.control("cancel", bb.manager.snapshot()["id"])
        await bb.finish()
        return a, b
    a, b = _go(bb, body)
    assert sorted([a.ok, b.ok]) == [False, True]


def test_start_restart_refused_while_another_restart_job_waits(bb):
    _ready(bb)
    bb.add_session(status=Status.BUSY)

    async def body():
        await bb.manager.check()
        first = await bb.manager.start_restart(chat_id=bb.chat_id, user_id=None,
                                               origin="chat", status_message=bb.message)
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        second = await bb.start_restart()
        bb.manager.control("cancel", first.job["id"])
        await bb.finish()
        return second
    res = _go(bb, body)
    assert (res.ok, res.error) == (False, "update_in_progress")
