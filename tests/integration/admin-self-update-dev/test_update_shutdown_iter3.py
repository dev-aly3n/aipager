"""Shutdown, iteration 3: once the daemon has begun to stop, nothing new
starts and no restart is ever scheduled (review rev-iter2-001/002/003,
test-report tester-iter2-003).

Each test drives one window in which the shutdown can begin, and names the
guard that closes it. The shutdown is triggered from the event loop while a
worker thread waits for ``self_update.shutting_down()``: the same
interleaving the daemon sees, with no sleeps patched.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from aipager import claude_resolve, install_source, self_update
from aipager.bot import update_flow
from aipager.self_update import UpdateLock
from aipager.state import Status

R = self_update.CommandResult


def _lock_free() -> bool:
    lock = UpdateLock()
    free = lock.try_acquire()
    lock.release()
    return free


def _wait_for_shutdown(timeout: float = 5.0) -> bool:
    """In a worker thread: block until the daemon's shutdown has begun."""
    deadline = time.monotonic() + timeout
    while not self_update.shutting_down():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True


def _marker() -> dict:
    return json.loads(self_update.UPDATE_MARKER_PATH.read_text())


def _all_texts(env) -> str:
    return "\n".join(e["text"] for e in env.edits)


def _wrap_run(env, match, before):
    """Run ``before(argv)`` in the seam thread for argv matching ``match``,
    then answer as the env's fake would."""
    original = env._run

    def _seam(argv, *, timeout, env=None, capture=True):
        argv_s = [str(a) for a in argv]
        if match(argv_s):
            before(argv_s)
        return original(argv, timeout=timeout, env=env, capture=capture)
    env.monkeypatch.setattr(self_update, "_run_command", _seam)


# ---- rev-iter2-002: start() refuses once shutdown has begun -------------------

def test_start_is_refused_once_shutdown_has_begun(env, run):
    async def scenario():
        await env.manager.shutdown()
        return await env.start("claude")
    res = run(scenario)
    assert res.ok is False
    assert res.error == "shutting_down"
    assert env.manager.snapshot() is None
    assert env.calls == []


# ---- rev-iter2-001: never a restart once shutdown has begun -------------------

def test_a_probe_killed_by_the_shutdown_reports_the_interruption(env, run):
    """The shutdown kills the post-install probe: the job must say the
    install may be partial, not that the new version failed to import."""
    entered, release = threading.Event(), threading.Event()
    killed: list = []

    def _probe_blocks(argv):
        entered.set()
        release.wait(10)

    _wrap_run(env, lambda a: "-I" in a, _probe_blocks)
    env.probe_ok = False     # what a killed probe looks like: no version line

    def _terminate():
        killed.append(1)
        release.set()
        return 1
    env.monkeypatch.setattr(self_update, "terminate_running_commands", _terminate)
    env.monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.2)

    async def scenario():
        await env.start("aipager")
        await env.until(entered.is_set)
        await env.manager.shutdown()
    try:
        run(scenario)
    finally:
        release.set()
    assert killed == [1]
    assert env.manager.snapshot()["phase"] == "cancelled"
    text = env.last_text()
    assert "shut down while the update was installing" in text
    assert "failed to import" not in text
    assert _marker()["interrupted"] == "upgrading"
    assert env.schedule_calls() == []


def test_a_probe_refused_by_the_shutdown_falls_back_to_the_version_on_disk(env, run):
    """The shutdown begins just before the probe spawns: the seam refuses
    it, and the job reads the installed version in-process instead of
    calling the refusal an import failure."""
    entered = threading.Event()
    real_probe = self_update.probe_installed_version

    def _probe(python):
        entered.set()
        assert _wait_for_shutdown()
        return real_probe(python)
    env.monkeypatch.setattr(self_update, "probe_installed_version", _probe)
    env.monkeypatch.setattr(self_update, "installed_version", lambda: "0.7.14")

    async def scenario():
        await env.start("aipager")
        await env.until(entered.is_set)
        await env.manager.shutdown()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "done"
    assert "nothing was restarted" in env.last_text()
    assert "failed to import" not in _all_texts(env)
    assert _marker()["to"] == "0.7.14"
    assert env.schedule_calls() == []
    assert _lock_free() is True


def test_a_probe_that_passes_during_shutdown_skips_the_gate(env, run):
    """Installed and proven while the daemon is stopping: go straight to
    the A→B marker, never into the restart gate."""
    sess = env.add_session(status=Status.IDLE)
    entered = threading.Event()
    after_install: list = []

    def _probe(argv):
        # A turn starts meanwhile: a gate entered now would wait on it.
        sess.status = Status.BUSY
        after_install.append(len(env.edits))
        entered.set()
        assert _wait_for_shutdown()
    _wrap_run(env, lambda a: "-I" in a, _probe)
    env.monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.5)

    async def scenario():
        await env.start("aipager")
        await env.until(entered.is_set)
        await env.manager.shutdown()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "done"
    later = "\n".join(e["text"] for e in env.edits[after_install[0]:])
    assert "waiting for every session to go idle" not in later
    assert _marker()["to"] == "0.7.14"
    assert env.schedule_calls() == []


def test_shutdown_during_the_plan_reread_never_calls_schedule_restart(env, run):
    """The shutdown begins while the plan is re-read after the gate: the
    job must not even try to schedule (let alone rely on the seam's
    refusal)."""
    shows: list = []
    entered = threading.Event()

    def _show(argv):
        shows.append(argv)
        if len(shows) == 2:          # the re-read right before scheduling
            entered.set()
            assert _wait_for_shutdown()
    _wrap_run(env, lambda a: a[1:3] == ["--user", "show"], _show)
    scheduled: list = []
    real_schedule = self_update.schedule_restart

    def _schedule(plan):
        scheduled.append(plan)
        return real_schedule(plan)
    env.monkeypatch.setattr(self_update, "schedule_restart", _schedule)

    async def scenario():
        await env.start("aipager")
        await env.until(entered.is_set)
        await env.manager.shutdown()
    run(scenario)
    assert scheduled == []
    assert env.manager.snapshot()["phase"] == "done"
    assert _marker()["to"] == "0.7.14"


def test_shutdown_while_the_restart_is_being_scheduled_stops_the_timer(env, run):
    """The shutdown begins while systemd-run runs: the timer it made is
    stopped at once, so it cannot bring a stopped daemon back 5 s later,
    and the next start still announces the update."""
    entered = threading.Event()

    def _hook(argv):
        entered.set()
        assert _wait_for_shutdown()
    env.schedule_hook = _hook

    async def scenario():
        await env.start("aipager")
        await env.until(entered.is_set)
        await env.manager.shutdown()
    run(scenario)
    stops = env.timer_stop_calls()
    assert len(stops) == 1
    assert stops[0][-1].startswith("aipager-update-restart-")
    assert stops[0][-1].endswith(".timer")
    assert env.manager.snapshot()["phase"] == "done"
    assert "nothing was restarted" in env.last_text()
    assert _marker()["to"] == "0.7.14"
    assert _lock_free() is True


def test_shutdown_in_the_post_install_gate_leaves_the_update_marker(env, run):
    """B is on disk and the job waits for idle when the daemon stops: the
    next start runs B, so the normal A→B marker must be there."""
    sess = env.add_session(status=Status.IDLE)

    def _hook():
        sess.status = Status.BUSY     # a turn starts during the install
    env.upgrade_hook = _hook

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls()
                        and env.manager.snapshot()["phase"] == "waiting_for_idle")
        await env.manager.shutdown()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "done"
    assert "nothing was restarted" in env.last_text()
    marker = _marker()
    assert "interrupted" not in marker and marker["to"] == "0.7.14"
    assert env.schedule_calls() == []
    assert _lock_free() is True


# ---- rev-iter2-003: no new spawn once shutdown has begun ------------------------

def test_both_does_not_begin_the_aipager_half_after_the_shutdown(env, run):
    """``Both``: Claude Code finishes inside the grace; the aipager half
    (PyPI lookup, installer, restart) does not begin."""
    entered = threading.Event()
    refreshed: list = []
    env.monkeypatch.setattr(claude_resolve, "refresh_claude_binary",
                            lambda: refreshed.append(1))

    def _update(argv):
        entered.set()
        assert _wait_for_shutdown()
    _wrap_run(env, lambda a: a[1:] == ["update"], _update)

    async def scenario():
        await env.start("both")
        await env.until(entered.is_set)
        env.fetches.clear()
        await env.manager.shutdown()
    run(scenario)
    assert self_update.PYPI_URL not in env.fetches
    assert env.upgrade_calls() == []
    assert env.manager.snapshot()["phase"] == "cancelled"
    text = env.last_text()
    # The version probe and the resolver's re-probe are not started now.
    assert "update finished; aipager is shutting down" in text
    assert refreshed == []
    assert [env.claude_path, "--version"] not in env.calls[
        env.calls.index([env.claude_path, "update"]):]


def test_a_claude_update_refused_by_the_shutdown_reports_the_shutdown(env, run):
    entered = threading.Event()
    real = self_update.run_claude_update
    resolved: list = []
    real_resolve = claude_resolve.try_resolve_claude_binary

    def _resolve(*a, **k):
        resolved.append(1)
        return real_resolve(*a, **k)

    def _run_claude_update():
        entered.set()
        assert _wait_for_shutdown()
        env.monkeypatch.setattr(claude_resolve, "try_resolve_claude_binary", _resolve)
        return real()
    env.monkeypatch.setattr(self_update, "run_claude_update", _run_claude_update)

    async def scenario():
        await env.start("claude")
        await env.until(entered.is_set)
        await env.manager.shutdown()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert "shut down before the update finished" in env.last_text()
    assert "not started" not in _all_texts(env)
    # Not even the resolver's own probes run once the shutdown began.
    assert resolved == []
    assert [c for c in env.calls if c[1:] == ["update"]] == []


def test_an_installer_refused_by_the_shutdown_reports_the_shutdown(env, run):
    entered = threading.Event()
    real_env = install_source.upgrade_env

    def _upgrade_env(source):
        entered.set()
        assert _wait_for_shutdown()
        return real_env(source)
    env.monkeypatch.setattr(install_source, "upgrade_env", _upgrade_env)

    async def scenario():
        await env.start("aipager")
        await env.until(entered.is_set)
        await env.manager.shutdown()
    run(scenario)
    assert env.upgrade_calls() == []
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert "shut down before the update finished" in env.last_text()
    assert "upgrade failed" not in _all_texts(env)
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_shutdown_fits_one_overall_deadline(env, run):
    """An installer that will not die must not hold the stop: grace, kill
    and cancel all share SHUTDOWN_DEADLINE_SECONDS."""
    env.upgrade_release = threading.Event()
    unkillable = threading.Event()

    def _terminate():
        unkillable.wait(10)          # the kill itself hangs
        return 1
    env.monkeypatch.setattr(self_update, "terminate_running_commands", _terminate)
    env.monkeypatch.setattr(update_flow, "SHUTDOWN_DEADLINE_SECONDS", 0.6)
    env.monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 5.0)
    took: list = []

    async def scenario():
        try:
            await env.start("aipager")
            await env.until(lambda: env.upgrade_calls())
            t0 = time.monotonic()
            await asyncio.wait_for(env.manager.shutdown(), 20)
            took.append(time.monotonic() - t0)
        finally:
            unkillable.set()
            env.upgrade_release.set()
    run(scenario)
    assert took and took[0] < 1.5, took


def test_daemon_bounds_the_updates_shutdown_and_goes_on(run, env, monkeypatch):
    """Whatever the manager does, the daemon's registry save and bot stop
    still run well before systemd's TimeoutStopSec."""
    from aipager.cli import daemon

    monkeypatch.setattr(update_flow, "SHUTDOWN_DEADLINE_SECONDS", 0.1)
    monkeypatch.setattr(daemon, "_UPDATES_SHUTDOWN_SLACK_SECONDS", 0.1)

    class _Hangs:
        async def shutdown(self):
            await asyncio.Event().wait()

    class _Bot:
        updates = _Hangs()

    async def scenario():
        t0 = time.monotonic()
        await asyncio.wait_for(daemon._shutdown_updates(_Bot()), 3)
        return time.monotonic() - t0
    assert run(scenario) < 1.0


# ---- tester-iter2-003: the shutdown sees a spawn before its child exists ---------

def test_the_seam_counts_a_call_before_its_child_exists(env):
    entered, release = threading.Event(), threading.Event()

    def _slow_spawn(argv, *, timeout, env=None, capture=True):
        entered.set()                 # inside the seam, no child registered
        release.wait(10)
        return R(0, "", False, None)
    env.monkeypatch.setattr(self_update, "_run_command", _slow_spawn)
    t = threading.Thread(target=lambda: self_update.run_command(["/abs/bin/x"], timeout=5))
    t.start()
    try:
        assert entered.wait(5)
        with self_update._LIVE_LOCK:
            assert not self_update._LIVE_CHILDREN
        assert self_update.running_command_count() == 1
    finally:
        release.set()
        t.join(5)
    assert self_update.running_command_count() == 0


def test_shutdown_kills_an_installer_whose_child_is_not_registered_yet(env, run):
    env.upgrade_release = threading.Event()
    killed: list = []

    def _terminate():
        killed.append(1)
        env.upgrade_rc = -15
        env.upgrade_release.set()
        return 0
    env.monkeypatch.setattr(self_update, "terminate_running_commands", _terminate)
    env.monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.2)

    async def scenario():
        try:
            await env.start("aipager")
            await env.until(lambda: env.upgrade_calls())
            await env.manager.shutdown()
        finally:
            env.upgrade_release.set()
    run(scenario)
    assert killed == [1]
    assert _marker()["interrupted"] == "upgrading"
    assert "shut down while the update was installing" in env.last_text()
