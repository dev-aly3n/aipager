"""Daemon shutdown in the middle of an update (review rev-iter1-003).

The installer runs in a worker thread in its own session. Cancelling the
job cannot stop it, and under KillMode=process it would outlive the daemon
and keep writing the venv while the next daemon imports it. Shutdown gives
it a short grace, then kills its process group, says so in the status
message and leaves an "interrupted" marker for the next daemon to post.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading

from aipager import self_update
from aipager.bot import update_flow
from aipager.self_update import UpdateLock
from aipager.state import Status


def _lock_free() -> bool:
    lock = UpdateLock()
    free = lock.try_acquire()
    lock.release()
    return free


class _StandIn:
    """What the real seam registers while it waits on an installer."""
    pid = 424242
    args = ["/abs/bin/pipx", "upgrade", "aipager"]


def _live_installer(env, *, finishes_after: float | None = None):
    """The fake installer registers a stand-in child with the real seam's
    live-children set and blocks until it is killed (or finishes)."""
    env.upgrade_release = threading.Event()
    killed: list = []
    child = _StandIn()

    def _hook():
        with self_update._LIVE_LOCK:
            self_update._LIVE_CHILDREN.add(child)
        if finishes_after is not None:
            threading.Timer(finishes_after, env.upgrade_release.set).start()

    def _kill_group(proc):
        killed.append(proc)
        with self_update._LIVE_LOCK:
            self_update._LIVE_CHILDREN.discard(proc)
        env.upgrade_rc = -15
        env.upgrade_release.set()

    env.upgrade_hook = _hook
    env.monkeypatch.setattr(self_update, "_kill_group", _kill_group)
    env.monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.3)
    return child, killed


def test_shutdown_mid_install_kills_the_installer_and_announces(env, run):
    child, killed = _live_installer(env)

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls())
        await env.manager.shutdown()
        assert env.manager._job_task.done()
        assert _lock_free() is True
    try:
        run(scenario)
    finally:
        self_update._LIVE_CHILDREN.discard(child)
    assert killed == [child]
    assert env.manager.snapshot()["phase"] == "cancelled"
    text = env.last_text()
    assert "shut down while the update was installing" in text
    assert "may be partial" in text
    assert "pipx install --force aipager" in text
    marker = json.loads(self_update.UPDATE_MARKER_PATH.read_text())
    assert marker["interrupted"] == "upgrading"
    assert marker["chat_id"] == env.chat_id
    assert marker["source_kind"] == "pipx"
    assert env.schedule_calls() == []


def test_shutdown_lets_an_installer_that_finishes_in_the_grace_finish(env, run):
    child, killed = _live_installer(env, finishes_after=0.02)
    env.monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 3.0)

    def _hook_done():
        # The seam drops the child once the installer has exited.
        with self_update._LIVE_LOCK:
            self_update._LIVE_CHILDREN.discard(child)
    original = env.upgrade_hook

    def _hook():
        original()
        env.upgrade_release.wait(5)
        _hook_done()
    env.upgrade_hook = _hook

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls())
        await env.manager.shutdown()
    try:
        run(scenario)
    finally:
        self_update._LIVE_CHILDREN.discard(child)
    assert killed == []
    # It went on and scheduled the restart; that marker is a normal one.
    assert env.manager.snapshot()["phase"] == "restart_scheduled"
    marker = json.loads(self_update.UPDATE_MARKER_PATH.read_text())
    assert "interrupted" not in marker and marker["to"] == "0.7.14"


def test_shutdown_during_the_gate_wait_cancels_cleanly(env, run):
    env.add_session(status=Status.BUSY)
    killed: list = []
    env.monkeypatch.setattr(self_update, "_kill_group", lambda p: killed.append(p))

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        await env.manager.shutdown()
        assert _lock_free() is True
    run(scenario)
    assert killed == []
    assert env.upgrade_calls() == []
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert "shut down before the update finished" in env.last_text()
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_shutdown_while_restart_pending_keeps_the_marker(env, run):
    env.monkeypatch.setattr(update_flow, "RESTART_WATCHDOG_SECONDS", 0.2)
    env.monkeypatch.setattr(self_update, "RESTART_DELAY_SECONDS", 0)

    async def scenario():
        await env.start("aipager")
        await env.finish()
        await env.manager.shutdown()
        # The watchdog was cancelled, not fired: it must not give up on
        # the very restart that is shutting us down.
        await asyncio.sleep(0.3)
    run(scenario)
    marker = json.loads(self_update.UPDATE_MARKER_PATH.read_text())
    assert marker["to"] == "0.7.14"
    assert env.timer_stop_calls() == []
    assert env.manager.snapshot()["phase"] == "restart_scheduled"


def test_shutdown_with_no_job_is_a_no_op(env, run):
    async def scenario():
        await env.manager.shutdown()
    run(scenario)
    assert env.calls == []


def test_daemon_shuts_updates_down_before_the_bot_stops():
    from aipager.cli import daemon

    src = inspect.getsource(daemon._run_daemon)
    assert "await _shutdown_updates(bot)" in src
    assert src.index("await _shutdown_updates(bot)") < src.index("await bot.stop()")
    helper = inspect.getsource(daemon._shutdown_updates)
    assert "await shutdown()" in helper


def test_daemon_shutdown_helper_never_raises(env, run):
    from aipager.cli import daemon

    class _Boom:
        async def shutdown(self):
            raise RuntimeError("x")

    class _Bot:
        updates = _Boom()

    async def scenario():
        await daemon._shutdown_updates(_Bot())
        await daemon._shutdown_updates(object())
    run(scenario)


def test_shutdown_mid_claude_update_kills_it_and_says_run_it_again(env, run):
    release = threading.Event()
    killed: list = []
    child = _StandIn()

    def _claude_update():
        with self_update._LIVE_LOCK:
            self_update._LIVE_CHILDREN.add(child)
        release.wait(10)
        return self_update.ClaudeUpdateResult("2.1.281", "2.1.281", -15, False, "",
                                              None, env.claude_path)

    def _kill_group(proc):
        killed.append(proc)
        with self_update._LIVE_LOCK:
            self_update._LIVE_CHILDREN.discard(proc)
        release.set()

    env.monkeypatch.setattr(self_update, "run_claude_update", _claude_update)
    env.monkeypatch.setattr(self_update, "_kill_group", _kill_group)
    env.monkeypatch.setattr(update_flow, "SHUTDOWN_GRACE_SECONDS", 0.3)

    async def scenario():
        await env.start("claude")
        await env.until(lambda: env.manager.snapshot()["phase"] == "claude_updating"
                        and self_update.running_command_count() == 1)
        await env.manager.shutdown()
    try:
        run(scenario)
    finally:
        self_update._LIVE_CHILDREN.discard(child)
    assert killed == [child]
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert "run <code>claude update</code> again" in env.last_text()
    marker = json.loads(self_update.UPDATE_MARKER_PATH.read_text())
    assert marker["interrupted"] == "claude_updating"
