"""No automatic restart without a safe systemd unit (8.36); without a
unit, Restart now (in place) or the exact steps for the setup."""

from __future__ import annotations

import os

from aipager import self_update


def _run_aipager(env, run):
    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)


def test_foreground_daemon_offers_restart_now_and_no_restart(env, run):
    env.set_under_unit(False)
    _run_aipager(env, run)
    text = env.last_text()
    assert "0.7.13 → 0.7.14 installed" in text
    assert "Tap Restart now to run it" in text
    assert env.last_markup_data() == ["_:up:rs"]
    assert env.manager.snapshot()["can_restart"] is True
    assert env.schedule_calls() == []
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_launchd_offers_restart_now_and_no_restart(env, run, monkeypatch):
    env.set_under_unit(False)
    monkeypatch.setattr(self_update, "_system", lambda: "Darwin")
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.aipager.daemon")
    _run_aipager(env, run)
    assert "Tap Restart now to run it" in env.last_text()
    assert env.last_markup_data() == ["_:up:rs"]
    assert env.schedule_calls() == []


def test_launchd_without_an_interpreter_gets_the_kickstart_steps(env, run, monkeypatch):
    from aipager import self_restart
    monkeypatch.setattr(self_restart, "exec_target", lambda: None)
    env.set_under_unit(False)
    monkeypatch.setattr(self_update, "_system", lambda: "Darwin")
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.aipager.daemon")
    _run_aipager(env, run)
    text = env.last_text()
    assert "To run 0.7.14, restart aipager yourself:" in text
    assert (f"On the Mac, run <code>launchctl kickstart -k gui/{os.getuid()}/"
            "com.aipager.daemon</code>." in text)
    assert env.last_markup_data() == []
    assert env.schedule_calls() == []


def test_unit_installed_but_not_main_pid_gets_the_terminal_steps(env, run):
    env.main_pid = os.getpid() + 4242
    _run_aipager(env, run)
    text = env.last_text()
    assert ("In the terminal or tmux window where aipager runs, press <code>Ctrl+C</code>, "
            "then run <code>aipager start</code>." in text)
    assert env.last_markup_data() == []
    assert "can_restart" not in env.manager.snapshot()
    assert env.schedule_calls() == []


def test_control_group_killmode_refuses_restart_and_lists_sessions(env, run):
    env.killmode = "control-group"
    env.add_session("claude-dev", "dev")
    _run_aipager(env, run)
    text = env.last_text()
    assert "0.7.13 → 0.7.14 installed" in text
    assert "KillMode" in text and "aipager service install" in text
    assert "Sessions a restart would kill: may include: dev" in text
    assert env.schedule_calls() == []
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_killmode_reread_before_scheduling(env, run):
    # Safe when the job starts, changed to unsafe by the time it restarts.
    env.killmodes = ["process", "control-group"]
    _run_aipager(env, run)
    assert env.schedule_calls() == []
    assert "KillMode" in env.last_text()
    show_calls = [c for c in env.calls if c[1:3] == ["--user", "show"]]
    assert len(show_calls) == 2
