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
