"""The in-place restart through the real ``aipager start`` path
(design.md section 4): the whole clean shutdown runs first, then the
daemon lock is released, every fd above 2 is close-on-exec, and the same
interpreter is exec'd with the launch argv and environment. A real stop
signal cancels it; an exec failure hands over to the respawn; nothing
starting at all exits 1 with the steps logged. Every component is a fake,
and the exec and the respawn only record."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import shlex
import signal
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import instance, self_restart
from aipager.cli import daemon


class World:
    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.tmp = tmp_path
        self.order: list[str] = []
        self.execs: list[tuple] = []
        self.spawns: list[list[str]] = []
        self.lock_path = tmp_path / "daemon.lock"
        self.at_exec: dict = {}
        self.signal_after_request = False
        self.launch_argv: list[str] | None = None
        self.exec_error: OSError | None = None
        self.spawn_error: OSError | None = None
        self._install()

    def rec(self, name):
        def _record(*a, **k):
            self.order.append(name)
        return _record

    def _install(self):
        mp = self.mp
        from aipager import preflight, telegram_endpoint
        from aipager import migrate
        from aipager.i18n import persist
        from aipager.report import capture, runtime
        mp.setattr(instance, "start_check", lambda: [])
        mp.setattr(telegram_endpoint, "check", lambda: [])
        mp.setattr(preflight, "require_config", lambda: None)
        mp.setattr(capture, "install", lambda: None)

        def late_env_change():
            # After the launch snapshot: must not reach the exec.
            mp.setenv("AIPAGER_TEST_CHANGED_LATE", "1")
            return False
        mp.setattr(migrate, "migrate_to_v2", late_env_change)
        mp.setattr(migrate, "upgrade_to_v3", lambda: False)
        mp.setattr(migrate, "retire_v1", lambda: False)
        mp.setattr(daemon, "_check_existing_daemon", lambda: None)
        mp.setattr(daemon, "_acquire_daemon_lock", self._acquire)
        mp.setattr(daemon, "_telegram_preflight", lambda: "fake_bot")
        mp.setattr(persist, "note_prior_run", lambda: False)
        mp.setattr(runtime, "daemon_starting", lambda **k: None)
        mp.setattr(runtime, "daemon_stopped",
                   lambda **k: self.order.append(f"daemon_stopped clean={k.get('clean')}"))
        real_release = daemon._release_daemon_lock

        def release():
            self.order.append("release_lock")
            real_release()
        mp.setattr(daemon, "_release_daemon_lock", release)
        mp.setattr(instance, "self_restart_log_path", lambda: str(self.tmp / "aipager.log"))
        mp.setattr(self_restart, "WAITER_SETTLE_SECONDS", 0)
        mp.setattr(self_restart, "_execve", self._execve)
        mp.setattr(self_restart, "_popen", self._popen)
        real_capture = self_restart.capture_launch

        def capture_launch():
            real_capture()
            if self.launch_argv is not None:
                self_restart._launch["argv"] = list(self.launch_argv)
        mp.setattr(self_restart, "capture_launch", capture_launch)
        mp.setenv("AIPAGER_TEST_AT_LAUNCH", "yes")

        # The components (as tests/test_cli_daemon_run.py fakes them).
        mp.setattr("aipager.config.BOT_TOKEN", "tok")
        mp.setattr("aipager.config.CHAT_ID", "12345")
        mp.setattr("aipager.config.OBSERVER_BOTS", [])
        mp.setattr("aipager.config.MINIAPP_ENABLED", False)
        bot = MagicMock()
        for name in ("start", "notify", "recover_sessions", "reload_team",
                     "_update_bot_commands", "publish_miniapp_button"):
            setattr(bot, name, AsyncMock())
        bot.stop = AsyncMock(side_effect=self.rec("bot.stop"))
        bot.updates.shutdown = AsyncMock(side_effect=self.rec("updates.shutdown"))
        bot.observers = None
        hook_receiver = MagicMock()
        hook_receiver.start = AsyncMock()
        hook_receiver.stop = MagicMock(side_effect=self.rec("hook_receiver.stop"))
        monitor = MagicMock()
        monitor.start = AsyncMock()
        monitor.stop = MagicMock(side_effect=self.rec("session_monitor.stop"))
        registry = MagicMock()
        registry.save = MagicMock(side_effect=self.rec("registry.save"))
        self.hook_receiver = hook_receiver
        self.bot = bot
        mp.setattr("aipager.bot.TelegramBot", lambda r: bot)
        mp.setattr("aipager.dtach.hook_receiver.HookReceiver", lambda r, n: hook_receiver)
        mp.setattr("aipager.session_monitor.SessionMonitor", lambda r, n: monitor)
        mp.setattr("aipager.state.SessionRegistry", lambda: registry)
        mp.setattr("aipager.claude_bootstrap.bootstrap_claude_settings", lambda: None)

        async def no_marker(bot, registry):
            return None
        mp.setattr("aipager.bot.update_flow.deliver_update_marker", no_marker)

        def stop_event():
            ev = asyncio.Event()
            asyncio.get_running_loop().call_soon(self._restart_now)
            return ev
        mp.setattr(daemon, "_new_stop_event", stop_event)

    # ---- fakes ---------------------------------------------------------------

    def _acquire(self):
        fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        daemon._daemon_lock_fd = fd

    def _restart_now(self):
        """What a Restart now job does last: ask the daemon to stop."""
        self.order.append("request")
        assert self_restart.request(self_restart.exec_target(), frm="1.0.1", to="1.1.0",
                                    steps="terminal")
        if self.signal_after_request:
            # The operator's Ctrl+C or docker stop: the loop's own handler.
            handle = asyncio.get_running_loop()._signal_handlers[signal.SIGTERM]
            handle._run()

    def _lock_is_free(self) -> bool:
        fd = os.open(str(self.lock_path), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    def _execve(self, exe, argv, env):
        self.order.append("exec")
        self.execs.append((exe, list(argv), dict(env)))
        self.at_exec["lock_free"] = self._lock_is_free()
        self.at_exec["inheritable"] = os.get_inheritable(self.probe_fd)
        if self.exec_error is not None:
            raise self.exec_error

    def _popen(self, argv, **kw):
        self.spawns.append(list(argv))
        if self.spawn_error is not None:
            raise self.spawn_error
        return MagicMock(pid=4321, poll=lambda: None, returncode=None)

    def start(self) -> int:
        r, w = os.pipe()
        os.set_inheritable(r, True)
        self.probe_fd = r
        try:
            return daemon._cmd_start(MagicMock())
        finally:
            os.close(r)
            os.close(w)
            if daemon._daemon_lock_fd is not None:
                os.close(daemon._daemon_lock_fd)
                daemon._daemon_lock_fd = None


@pytest.fixture
def world(monkeypatch, tmp_path):
    return World(monkeypatch, tmp_path)


SHUTDOWN = ["request", "updates.shutdown", "registry.save", "session_monitor.stop",
            "hook_receiver.stop", "bot.stop", "registry.save", "daemon_stopped clean=True"]


def test_the_whole_shutdown_runs_before_the_exec(world):
    assert world.start() == 0
    assert world.order == SHUTDOWN + ["release_lock", "exec"]


def test_the_exec_is_the_same_interpreter_argv_and_launch_environment(world):
    world.start()
    (exe, argv, env), = world.execs
    assert exe == sys.executable
    assert argv == list(sys.orig_argv)
    assert env["AIPAGER_TEST_AT_LAUNCH"] == "yes"
    assert "AIPAGER_TEST_CHANGED_LATE" not in env


def test_the_daemon_lock_is_free_and_fds_close_on_exec_at_the_exec(world):
    world.start()
    assert world.at_exec == {"lock_free": True, "inheritable": False}
    assert daemon._daemon_lock_fd is None


def test_a_missing_launch_script_execs_the_module_form(world):
    world.launch_argv = [sys.executable, "/nonexistent/aipager-test/bin/aipager", "start"]
    world.start()
    assert world.execs[0][1] == [sys.executable, "-m", "aipager", "start"]


def test_a_stop_signal_after_the_request_cancels_the_exec(world):
    world.signal_after_request = True
    assert world.start() == 0
    assert world.execs == [] and world.spawns == []
    assert world.order == SHUTDOWN


def test_nothing_pending_is_a_plain_exit(world, monkeypatch):
    monkeypatch.setattr(world, "_restart_now", lambda: None)

    def stop_event():
        ev = asyncio.Event()
        ev.set()
        return ev
    monkeypatch.setattr(daemon, "_new_stop_event", stop_event)
    assert world.start() == 0
    assert world.execs == [] and "release_lock" not in world.order


def test_an_exec_failure_hands_over_to_the_respawn(world, caplog):
    world.exec_error = OSError(8, "Exec format error")
    with caplog.at_level(logging.INFO, logger="aipager.self_restart"):
        assert world.start() == 0
    (argv,) = world.spawns
    assert argv[:2] == ["/bin/sh", "-c"]
    assert f"while kill -0 {os.getpid()} " in argv[2]
    # The waiter runs the ORIGINAL launch argv once this PID is gone.
    assert argv[2].endswith("; exec " + " ".join(
        shlex.quote(a) for a in [sys.executable, *sys.orig_argv[1:]]))
    assert f"restart.respawn.started pid=4321 log={world.tmp / 'aipager.log'}" in caplog.text


def test_nothing_starting_exits_one_with_the_steps_logged(world, caplog):
    world.exec_error = OSError(8, "Exec format error")
    world.spawn_error = OSError(2, "No such file")
    with caplog.at_level(logging.ERROR, logger="aipager.self_restart"):
        assert world.start() == 1
    assert any(r.getMessage().startswith("restart.failed:")
               and "aipager start" in r.getMessage() for r in caplog.records)


def test_a_crash_never_reaches_the_exec(world, monkeypatch):
    world.bot.stop.side_effect = RuntimeError("bot stop crashed")
    from aipager.report import runtime
    monkeypatch.setattr(runtime, "daemon_stopped", lambda **k: None)
    with pytest.raises(RuntimeError):
        world.start()
    assert world.execs == []
