"""`aipager update` over the shared core (8.36)."""

from __future__ import annotations

from aipager import self_update, service, updater
from aipager.install_source import InstallSource
from aipager.self_update import UpdateLock


def test_cli_update_uses_absolute_installer(env, capsys):
    assert updater.cmd_update() == 0
    upgrade = env.upgrade_calls()
    assert upgrade == [["/abs/bin/pipx", "upgrade", "aipager"]]
    out = capsys.readouterr().out
    assert "0.7.13 → 0.7.14" in out
    # The CLI restarts nothing.
    assert env.schedule_calls() == []


def test_cli_update_refuses_editable_install(env, capsys):
    env.source = InstallSource(kind="editable", prefix="/src", python="/src/python",
                               reason="this is an editable (development) install")
    assert updater.cmd_update() == 1
    assert "editable" in capsys.readouterr().err
    assert env.calls == []


def test_cli_update_prints_killmode_fix_when_unsafe(env, capsys):
    service.LINUX_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    service.LINUX_UNIT_PATH.write_text("[Unit]\n")
    env.killmode = "control-group"
    assert updater.cmd_update() == 0
    out = capsys.readouterr().out
    assert "aipager service install" in out
    assert "systemctl --user restart aipager.service" in out
    assert out.index("aipager service install") < out.index("systemctl --user restart")


def test_cli_update_refused_while_daemon_job_holds_lock(env, capsys):
    held = UpdateLock()
    assert held.try_acquire()
    try:
        assert updater.cmd_update() == 1
    finally:
        held.release()
    assert "already running" in capsys.readouterr().err
    assert env.calls == []


def test_cli_update_timeout_exits_1(env, capsys):
    env.upgrade_timeout = True
    assert updater.cmd_update() == 1
    assert "timed out" in capsys.readouterr().err


def test_cli_update_passes_the_timeout_and_live_output(env, monkeypatch):
    seen = []
    original = env._run

    def _spy(argv, *, timeout, env=None, capture=True):
        if argv[0] == "/abs/bin/pipx":
            seen.append((timeout, capture))
        return original(argv, timeout=timeout, env=env, capture=capture)
    monkeypatch.setattr(self_update, "_run_command", _spy)
    updater.cmd_update()
    assert seen == [(self_update.UPGRADE_TIMEOUT_SECONDS, False)]


def test_cli_update_already_current(env, capsys):
    env.probe_version = "0.7.13"
    assert updater.cmd_update() == 0
    assert "already at 0.7.13" in capsys.readouterr().out
