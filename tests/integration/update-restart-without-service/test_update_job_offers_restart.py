"""An aipager update on a daemon no service manager restarts ends with
Restart now (or the exact steps); systemd's automatic restart is as it
was."""

from __future__ import annotations

import os

import pytest

from aipager import self_restart, self_update


def _update(env, run):
    async def scenario():
        res = await env.start_update()
        assert res.ok, res
        await env.finish()
    run(scenario)


@pytest.fixture(params=["foreground", "launchd", "docker"])
def no_unit(request, env, monkeypatch):
    if request.param == "launchd":
        monkeypatch.setattr(self_update, "_system", lambda: "Darwin")
        monkeypatch.setenv("XPC_SERVICE_NAME", "com.aipager.daemon")
    elif request.param == "docker":
        env.dockerenv.write_text("")
    return request.param


def test_the_finished_update_offers_restart_now(env, run, no_unit):
    _update(env, run)
    snap = env.manager.snapshot()
    assert snap["phase"] == "done" and snap["can_restart"] is True
    assert env.last_text() == ("✅ <b>aipager</b> 1.0.1 → 1.1.0 installed.\n"
                               "Tap Restart now to run it (aipager waits until no turn is "
                               "running).")
    assert env.last_markup_data() == ["_:up:rs"]


def test_the_update_itself_restarts_nothing(env, run, no_unit):
    _update(env, run)
    assert env.stops == [] and not self_restart.pending()
    assert env.schedule_calls() == []
    assert not self_update.UPDATE_MARKER_PATH.exists()
    assert self_update.UpdateLock().try_acquire()


def test_the_mini_app_learns_it_is_waiting(env, run):
    _update(env, run)
    assert env.manager.waiting_info() == {"version": "1.1.0", "running": "1.0.1",
                                          "source": "installed"}


def test_a_unit_that_does_not_run_this_daemon_gets_the_terminal_steps(env, run):
    env.set_under_unit(True)
    env.main_pid = os.getpid() + 7
    _update(env, run)
    snap = env.manager.snapshot()
    assert "can_restart" not in snap
    assert env.last_text() == (
        "✅ <b>aipager</b> 1.0.1 → 1.1.0 installed.\n"
        "To run 1.1.0, restart aipager yourself:\n"
        "In the terminal or tmux window where aipager runs, press <code>Ctrl+C</code>, then "
        "run <code>aipager start</code>.")
    assert env.last_markup_data() == []


def test_docker_without_an_interpreter_gets_the_docker_steps(env, run, monkeypatch):
    env.dockerenv.write_text("")
    monkeypatch.setenv("HOSTNAME", "0123456789ab")
    monkeypatch.setattr(self_restart, "exec_target", lambda: None)
    _update(env, run)
    text = env.last_text()
    assert "On the Docker host, run <code>docker restart 0123456789ab</code>." in text
    assert "brings back the aipager version in that image" in text
    assert env.last_markup_data() == []


def test_systemd_automatic_is_untouched(env, run):
    env.set_under_unit(True)
    _update(env, run)
    snap = env.manager.snapshot()
    assert snap["phase"] == "restart_scheduled" and "can_restart" not in snap
    assert len(env.schedule_calls()) == 1
    assert env.last_text() == "⏳ <b>aipager</b> 1.0.1 → 1.1.0 installed, restarting…"
    assert env.stops == []
