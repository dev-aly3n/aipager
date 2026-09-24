"""The voice extra's "Restart daemon now" goes through the detached helper."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def spawned(monkeypatch):
    """The foreground branch spawns `sh -c … aipager start` and SIGTERMs
    this very process. None of these tests may reach it: record instead."""
    calls: list = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: calls.append(("popen", a)))
    monkeypatch.setattr(os, "kill", lambda *a: calls.append(("kill", a)))
    yield calls
    assert calls == [], f"the foreground respawn path ran: {calls!r}"


def _query(env):
    q = MagicMock()
    q.message = env._mk_message(env.chat_id)
    q.edit_message_text = AsyncMock()
    return q


def test_voice_restart_schedules_detached_restart(env, run):
    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    run(scenario)
    assert len(env.schedule_calls()) == 1
    assert env.schedule_calls()[0][-3:] == ["--user", "restart", "aipager.service"]
    assert "Restarting in 5 s" in q.edit_message_text.await_args.args[0]


def test_voice_restart_refuses_on_control_group_killmode(env, run):
    env.killmode = "control-group"
    env.add_session("claude-dev", "dev")
    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    run(scenario)
    assert env.schedule_calls() == []
    text = q.edit_message_text.await_args.args[0]
    assert "Not restarting" in text and "KillMode" in text
    assert "aipager service install" in text


def test_voice_restart_reports_schedule_failure(env, run):
    env.schedule_rc = 1
    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    run(scenario)
    assert "Couldn't schedule the restart" in q.edit_message_text.await_args.args[0]


# ----- never during an update (review rev-iter1-004) ---------------------------------

def test_voice_restart_refuses_while_an_update_job_runs(env, run):
    import threading

    env.upgrade_release = threading.Event()
    q = _query(env)

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls())
        await env.bot._restart_daemon(q)
        env.upgrade_release.set()
        await env.finish()
    run(scenario)
    # The only schedule is the update's own, after its installer finished.
    assert len(env.schedule_calls()) == 1
    texts = [c.args[0] for c in q.edit_message_text.await_args_list]
    assert texts and "Not restarting: an aipager update" in texts[0]


def test_voice_restart_refuses_while_an_update_restart_is_pending(env, run):
    q = _query(env)

    async def scenario():
        await env.start("aipager")
        await env.finish()
        assert env.manager.snapshot()["phase"] == "restart_scheduled"
        await env.bot._restart_daemon(q)
    run(scenario)
    assert len(env.schedule_calls()) == 1
    assert "Not restarting: an aipager update" in q.edit_message_text.await_args.args[0]


def test_voice_restart_refuses_while_the_cli_holds_the_update_lock(env, run):
    from aipager.self_update import UpdateLock

    cli = UpdateLock()
    assert cli.try_acquire()
    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    try:
        run(scenario)
    finally:
        cli.release()
    assert env.schedule_calls() == []
    assert "update lock is held" in q.edit_message_text.await_args.args[0]


def test_voice_restart_leaves_the_update_lock_free(env, run):
    from aipager.self_update import UpdateLock

    q = _query(env)

    async def scenario():
        await env.bot._restart_daemon(q)
    run(scenario)
    probe = UpdateLock()
    assert probe.try_acquire() is True
    probe.release()
