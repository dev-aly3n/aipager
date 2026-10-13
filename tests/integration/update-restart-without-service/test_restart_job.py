"""The Restart now job (design.md section 5): verify, wait for idle
sessions like every restart, verify again, write the marker, and only then
ask the daemon to stop and exec (or schedule systemd's restart)."""

from __future__ import annotations

import asyncio
import json

import pytest

from aipager import self_restart, self_update
from aipager.bot import held as held_mod
from aipager.state import Status


def _marker():
    path = self_update.UPDATE_MARKER_PATH
    return json.loads(path.read_text()) if path.exists() else None


def _restart(env, run):
    async def scenario():
        res = await env.start_restart()
        assert res.ok, res
        await env.finish()
    run(scenario)


# ---- idle sessions: straight through ------------------------------------------------

def test_idle_restart_edits_the_card_before_asking_the_daemon_to_stop(env, run):
    env.add_session()
    _restart(env, run)
    assert env.all_texts()[0] == "⏳ Checking the installed aipager…"
    assert env.stops == ["⏳ <b>aipager</b> 1.0.1 → 1.1.0 installed, restarting…"]
    assert env.manager.snapshot()["phase"] == "restart_scheduled"
    assert env.manager.busy and env.manager.restart_pending
    assert self_restart.pending()


def test_the_marker_names_the_versions_the_chat_and_the_card(env, run):
    env.add_session(name="claude-dev", label="dev")
    _restart(env, run)
    marker = _marker()
    assert (marker["from"], marker["to"], marker["chat_id"]) == ("1.0.1", "1.1.0", env.chat_id)
    assert marker["message"] == {"chat_id": env.chat_id, "message_id": 4711, "prefix": ""}
    assert marker["sessions"] == [{"name": "claude-dev", "label": "dev"}]


def test_the_request_execs_the_launch_argv_without_scheduling_anything(env, run):
    _restart(env, run)
    req = self_restart.take()
    assert req.target.argv[1:] == ["-m", "aipager", "start"]
    assert (req.frm, req.to, req.steps) == ("1.0.1", "1.1.0", "terminal")
    assert env.schedule_calls() == []


def test_the_install_is_verified_twice_with_no_secret_passed_on(env, run):
    _restart(env, run)
    assert len(env.restart_probe_calls()) == 2
    assert all("CLAUDE_TG_BOT_TOKEN" not in e for e in env.probe_envs)
    assert all(e.get("HOME") == "/home/alice" for e in env.probe_envs)


def test_the_lock_stays_held_until_the_process_is_replaced(env, run):
    async def scenario():
        await env.start_restart()
        await env.finish()
        return self_update.UpdateLock().try_acquire()
    assert run(scenario) is False


# ---- the busy rule (one rule for every restart) ---------------------------------------

BLOCKERS = {
    "busy": dict(status=Status.BUSY),
    "question": dict(status=Status.INTERACTIVE),
    "busy card": dict(busy_msg_id=55),
    "permission": dict(pending_permission=object()),
}


@pytest.mark.parametrize("kind", sorted(BLOCKERS))
def test_a_blocker_keeps_the_restart_waiting_until_it_clears(env, run, kind):
    sess = env.add_session(**BLOCKERS[kind])

    async def scenario():
        await env.start_restart()
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        await asyncio.sleep(0.1)
        assert env.stops == [] and not self_restart.pending()
        assert env.last_markup_data()[0].startswith("_:up:now:")
        env.registry._sessions.pop(sess.name)
        await env.finish()
    run(scenario)
    assert len(env.stops) == 1


def test_held_answers_keep_the_restart_waiting(env, run, monkeypatch):
    monkeypatch.setattr(held_mod.HELD, "count", lambda *a, **k: 2)

    async def scenario():
        await env.start_restart()
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        await asyncio.sleep(0.1)
        assert env.stops == []
        monkeypatch.setattr(held_mod.HELD, "count", lambda *a, **k: 0)
        await env.finish()
    run(scenario)
    assert len(env.stops) == 1


def test_restart_now_overrides_the_wait(env, run):
    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start_restart()
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        assert env.manager.control("restart-now", res.job["id"]).ok
        await env.finish()
    run(scenario)
    assert len(env.stops) == 1


def test_cancel_while_waiting_restarts_nothing(env, run):
    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start_restart()
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        assert env.manager.control("cancel", res.job["id"]).ok
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert env.last_text() == "↩️ Restart cancelled. aipager still runs 1.0.1."
    assert env.stops == [] and _marker() is None
    assert self_update.UpdateLock().try_acquire()


def test_a_long_wait_offers_wait_restart_now_and_cancel(env, run, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0)
    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start_restart()
        await env.until(lambda: env.manager.snapshot()["phase"] == "gate_timeout")
        await env.until(lambda: len(env.last_markup_data()) == 3)
        data = env.last_markup_data()
        env.manager.control("cancel", res.job["id"])
        await env.finish()
        return data
    data = run(scenario)
    assert [d.split(":")[2] for d in data] == ["wait", "now", "stop"]
    assert env.stops == []


# ---- nothing to restart, or nothing that loads -------------------------------------------

def test_a_broken_install_fails_without_stopping(env, run):
    env.restart_probe = (None, False)
    _restart(env, run)
    assert env.manager.snapshot()["phase"] == "failed"
    assert "does not load" in env.last_text() and "still runs 1.0.1" in env.last_text()
    assert env.stops == [] and _marker() is None
    assert self_update.UpdateLock().try_acquire()


def test_the_same_version_has_nothing_to_restart(env, run):
    env.restart_probe = ("1.0.1", True)
    _restart(env, run)
    assert env.manager.snapshot()["phase"] == "done"
    assert env.last_text() == ("✅ aipager 1.0.1 is already the installed version, so there "
                               "is nothing to restart.")
    assert env.stops == []
    assert self_update.UpdateLock().try_acquire()


def test_a_broken_install_found_by_the_second_check_stops_nothing(env, run):
    env.restart_probes = [("1.1.0", True), (None, False)]
    _restart(env, run)
    assert env.manager.snapshot()["phase"] == "failed"
    assert env.stops == [] and _marker() is None


def test_a_daemon_that_cannot_stop_says_so_and_frees_everything(env, run):
    self_restart.detach()        # no daemon main loop to stop
    _restart(env, run)
    snap = env.manager.snapshot()
    assert snap["phase"] == "failed"
    assert env.last_text().startswith("⚠️ aipager could not restart itself here.\n"
                                      "To run 1.1.0, restart aipager yourself:")
    assert _marker() is None
    assert not env.manager.busy
    assert self_update.UpdateLock().try_acquire()


# ---- systemd -------------------------------------------------------------------------------

def test_systemd_schedules_its_restart_and_never_stops_in_process(env, run):
    env.set_under_unit(True)
    _restart(env, run)
    assert len(env.schedule_calls()) == 1
    assert env.manager.snapshot()["phase"] == "restart_scheduled"
    assert env.stops == [] and not self_restart.pending()
    assert _marker()["to"] == "1.1.0"


def test_systemd_with_an_unsafe_killmode_restarts_nothing(env, run):
    env.set_under_unit(True)
    env.killmode = "control-group"
    _restart(env, run)
    assert env.manager.snapshot()["phase"] == "done"
    assert "Restart needed: KillMode=control-group" in env.last_text()
    assert env.schedule_calls() == [] and env.stops == [] and _marker() is None


def test_no_interpreter_to_exec_gives_the_steps(env, run, monkeypatch):
    monkeypatch.setattr(self_restart, "exec_target", lambda: None)
    _restart(env, run)
    assert env.manager.snapshot()["phase"] == "done"
    assert "press <code>Ctrl+C</code>, then run <code>aipager start</code>" in env.last_text()
    assert env.stops == []


# ---- a shutdown in the middle ------------------------------------------------------------------

def test_a_shutdown_during_the_wait_leaves_the_marker_for_the_next_start(env, run):
    env.add_session(status=Status.BUSY)

    async def scenario():
        await env.start_restart()
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        await env.manager.shutdown()
    run(scenario)
    assert env.stops == [] and not self_restart.pending()
    marker = _marker()
    assert (marker["from"], marker["to"]) == ("1.0.1", "1.1.0")
    assert "the next start runs 1.1.0" in env.last_text()


def test_a_second_check_refused_by_a_shutdown_leaves_the_marker(env, run, monkeypatch):
    calls = []
    real = self_update.probe_restart_target

    def probe(target):
        calls.append(1)
        if len(calls) == 2:
            # The daemon starts stopping as the second check begins: the
            # seam refuses to start it.
            env.manager._shutting_down = True
            self_update.begin_shutdown()
        return real(target)
    monkeypatch.setattr(self_update, "probe_restart_target", probe)

    async def scenario():
        await env.start_restart()
        await env.finish()
    run(scenario)
    assert len(calls) == 2 and len(env.restart_probe_calls()) == 1
    assert env.stops == []
    assert (_marker()["from"], _marker()["to"]) == ("1.0.1", "1.1.0")
    assert "the next start runs 1.1.0" in env.last_text()


def test_a_first_check_refused_by_a_shutdown_writes_no_marker(env, run):
    async def scenario():
        await env.start_restart()
        env.manager._shutting_down = True
        self_update.begin_shutdown()
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert env.stops == [] and _marker() is None
