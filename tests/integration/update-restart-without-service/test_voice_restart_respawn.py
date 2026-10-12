"""The voice extra's "Restart daemon now" without a service unit: the
respawn now lives in aipager.self_restart, runs the ORIGINAL launch argv
(so ``aipager config`` still finds the daemon) and stops the daemon
through its own clean stop instead of a SIGTERM to itself."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

from aipager import instance, self_restart


def _query(env):
    q = MagicMock()
    q.message = env._mk_message(env.chat_id)
    q.edit_message_text = AsyncMock()
    return q


def _wire(env, monkeypatch, *, poll=None, error=None):
    spawned = []

    def popen(argv, **kw):
        spawned.append((argv, kw))
        if error is not None:
            raise error
        return MagicMock(pid=77, poll=lambda: poll, returncode=poll)
    monkeypatch.setattr(self_restart, "_popen", popen)
    monkeypatch.setattr(self_restart, "WAITER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(instance, "self_restart_log_path",
                        lambda: str(env.tmp / "aipager.log"))
    self_restart._launch["argv"] = [sys.executable, "/usr/local/bin/aipager", "start"]
    (env.tmp / "launch").mkdir()
    return spawned


def test_the_respawn_runs_the_launch_argv_and_stops_cleanly(env, run, monkeypatch):
    spawned = _wire(env, monkeypatch)
    script = env.tmp / "launch" / "aipager"
    script.write_text("")
    self_restart._launch["argv"] = [sys.executable, str(script), "start"]
    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    run(scenario)
    ((argv, kw),) = spawned
    assert argv[2].endswith(f"exec {sys.executable} {script} start")
    assert f"kill -0 {os.getpid()}" in argv[2]
    assert kw["env"]["HOME"] == "/home/alice"
    assert len(env.stops) == 1                 # the attached stop, never a kill
    assert "send your voice message again" in q.edit_message_text.await_args.args[0]


def test_a_waiter_that_dies_at_once_stops_nothing(env, run, monkeypatch):
    _wire(env, monkeypatch, poll=1)
    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    run(scenario)
    assert env.stops == []
    assert "Replacement daemon failed to start" in q.edit_message_text.await_args.args[0]


def test_a_spawn_failure_stops_nothing(env, run, monkeypatch):
    _wire(env, monkeypatch, error=OSError("no sh"))
    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    run(scenario)
    assert env.stops == []
    assert "Couldn't spawn replacement: no sh" in q.edit_message_text.await_args.args[0]


def test_no_interpreter_stops_nothing(env, run, monkeypatch):
    spawned = _wire(env, monkeypatch)
    monkeypatch.setattr(self_restart, "exec_target", lambda: None)
    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    run(scenario)
    assert spawned == [] and env.stops == []
    assert "Couldn't spawn replacement" in q.edit_message_text.await_args.args[0]
