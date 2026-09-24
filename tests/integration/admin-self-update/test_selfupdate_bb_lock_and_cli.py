"""SC-9: a second update while one runs (Telegram, Mini App or CLI) is
refused. SC-10: `aipager update` works with uv/pipx only in ~/.local/bin
and an empty PATH, and never hangs past its timeout.
(design.md Success criteria #9, #10; entrypoints.md "CLI commands",
`UpdateLock`, `cmd_update`)."""

from __future__ import annotations

import os
import stat
from unittest.mock import MagicMock

import pytest

from aipager import install_source, self_update, updater
from aipager.bot import update_flow
from aipager.state import Status


@pytest.fixture
def held_lock():
    lock = self_update.UpdateLock()
    assert lock.try_acquire()
    yield lock
    lock.release()


# ---- the lock itself -------------------------------------------------------

def test_second_lock_in_same_process_is_refused(held_lock):
    other = self_update.UpdateLock()
    assert other.try_acquire() is False


def test_lock_is_free_after_release():
    lock = self_update.UpdateLock()
    lock.try_acquire()
    lock.release()
    assert self_update.UpdateLock().try_acquire() is True


def test_lock_reports_held(held_lock):
    assert held_lock.held is True


# ---- daemon vs daemon --------------------------------------------------------

def _busy_job(bot, h):
    h.add_session(bot.registry, "w", status=Status.BUSY)
    return bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                             origin="chat", status_message=h.status_message(h.DM))


def test_second_update_refused_while_first_runs(world, personal_bot, h, run_async):
    async def go():
        await _busy_job(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        return await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 701))
    res = run_async(go())
    assert (res.ok, res.error) == (False, "update_in_progress")


def test_second_update_runs_nothing(world, personal_bot, h, run_async):
    async def go():
        await _busy_job(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 701))
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert world.claude_update_calls() == []


def test_manager_reports_busy_while_job_runs(world, personal_bot, h, run_async):
    async def go():
        await _busy_job(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        return personal_bot.updates.busy
    assert run_async(go()) is True


def test_update_command_during_job_says_already_running(world, personal_bot, mk_update, h, run_async):
    upd = mk_update("/update", user_id=h.OPERATOR, chat_id=h.DM)
    upd.message.chat = MagicMock()
    upd.message.chat.id = h.DM
    reply = h.status_message(h.DM, 950)
    upd.message.reply_text.return_value = reply
    upd.effective_message = upd.message
    upd.effective_chat.type = "private"

    async def go():
        await _busy_job(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await update_flow.handle_update_cmd(personal_bot, upd, MagicMock())
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert "An update is already running" in "\n".join(
        h.texts_of(upd.message, reply, personal_bot._app.bot))


def test_daemon_update_refused_while_cli_holds_lock(world, personal_bot, h, run_async, held_lock):
    res = run_async(personal_bot.updates.start(
        "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
        status_message=h.status_message(h.DM)))
    assert (res.ok, res.error) == (False, "update_in_progress")


def test_daemon_runs_nothing_while_cli_holds_lock(world, personal_bot, h, run_async, held_lock):
    async def go():
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM))
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert world.upgrade_calls() == [] and world.claude_update_calls() == []


def test_failed_job_releases_lock(world, personal_bot, h, run_async):
    world.upgrade_rc = 1
    run_async(h.run_job(personal_bot, "aipager"))
    assert h.lock_is_free()


def test_new_job_allowed_after_previous_finished(world, personal_bot, h, run_async):
    async def go():
        await h.run_job(personal_bot, "claude")
        return await personal_bot.updates.start(
            "claude", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM, 702))
    assert run_async(go()).ok is True


def test_exception_in_step_releases_lock(world, personal_bot, h, run_async, monkeypatch):
    """Error guessing: an unexpected exception escapes the spawn seam
    while the installer runs."""
    real = world._run_command

    def boom(argv, *, timeout, env=None, capture=True):
        if world.is_upgrade([str(a) for a in argv]):
            raise OSError("exec format error")
        return real(argv, timeout=timeout, env=env, capture=capture)
    monkeypatch.setattr(self_update, "_run_command", boom)

    async def go():
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM))
        await h.wait_for(lambda: (personal_bot.updates.snapshot() or {}).get("phase")
                         in h.TERMINAL, 2)
        await h.wait_for(lambda: False, 0.1)
    run_async(go())
    assert h.lock_is_free()


def test_exception_in_step_schedules_no_restart(world, personal_bot, h, run_async, monkeypatch):
    real = world._run_command

    def boom(argv, *, timeout, env=None, capture=True):
        if world.is_upgrade([str(a) for a in argv]):
            raise OSError("exec format error")
        return real(argv, timeout=timeout, env=env, capture=capture)
    monkeypatch.setattr(self_update, "_run_command", boom)

    async def go():
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM))
        await h.wait_for(lambda: False, 0.4)
    run_async(go())
    assert world.schedule_calls() == []


# ---- CLI -------------------------------------------------------------------

def test_cli_refused_while_daemon_job_holds_lock(world, personal_bot, h, run_async):
    async def go():
        await _busy_job(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        return updater.cmd_update()
    assert run_async(go()) == 1


def test_cli_update_uses_absolute_installer(world):
    updater.cmd_update()
    assert world.upgrade_calls()[0]["argv"] == ["/abs/tools/pipx", "upgrade", "aipager"]


def test_cli_update_returns_zero_on_success(world):
    assert updater.cmd_update() == 0


def test_cli_update_uses_upgrade_timeout(world):
    updater.cmd_update()
    assert world.upgrade_calls()[0]["timeout"] == self_update.UPGRADE_TIMEOUT_SECONDS


def test_cli_update_never_restarts(world):
    updater.cmd_update()
    assert world.schedule_calls() == [] and not any(
        "restart" in a for a in world.argvs())


def test_cli_update_writes_no_marker(world):
    updater.cmd_update()
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_cli_update_releases_lock(world):
    updater.cmd_update()
    assert self_update.UpdateLock().try_acquire() is True


def test_cli_update_prints_a_to_b(world, capsys, h):
    updater.cmd_update()
    assert f"{h.RUNNING} → {h.LATEST}" in capsys.readouterr().out


def test_cli_already_current_returns_zero(world):
    world.probe = (world.running, True, None)
    assert updater.cmd_update() == 0


def test_cli_upgrade_failure_returns_one(world):
    world.upgrade_rc = 1
    assert updater.cmd_update() == 1


def test_cli_upgrade_timeout_returns_one(world):
    world.upgrade_timed_out = True
    assert updater.cmd_update() == 1


def test_cli_refuses_editable_install(world):
    world.origin = "editable"
    assert updater.cmd_update() == 1


def test_cli_editable_install_runs_nothing(world):
    world.origin = "editable"
    updater.cmd_update()
    assert world.upgrade_calls() == []


def test_cli_tool_not_found_returns_one(world):
    world.tool_missing = True
    assert updater.cmd_update() == 1


def _installed_unit():
    from aipager import service
    path = service.LINUX_UNIT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Service]\nExecStart=/x/aipager start\n")


def test_cli_prints_systemctl_restart_when_unit_installed(world, capsys):
    _installed_unit()
    updater.cmd_update()
    assert "systemctl --user restart aipager.service" in capsys.readouterr().out


def test_cli_prints_killmode_fix_when_unsafe(world, capsys):
    _installed_unit()
    world.killmodes = ["control-group"]
    updater.cmd_update()
    out = capsys.readouterr()
    assert "aipager service install" in out.out + out.err


@pytest.mark.parametrize("tool,kind", [("uv", "uv"), ("pipx", "pipx")])
def test_cli_finds_installer_only_in_local_bin_with_empty_path(world, monkeypatch, tmp_path, tool, kind):
    """Fact 1: the installer lives only in ~/.local/bin and PATH is empty
    (systemd unit / non-interactive shell). Real resolve_tool, fake home."""
    home = tmp_path / "fakehome"
    bindir = home / ".local" / "bin"
    bindir.mkdir(parents=True)
    exe = bindir / tool
    exe.write_text("#!/bin/sh\nexit 99\n")        # never executed: _run_command is faked
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(install_source, "resolve_tool", REAL_RESOLVE_TOOL)
    world.source_kind = kind
    updater.cmd_update()
    assert world.upgrade_calls()[0]["argv"][0] == str(exe)


REAL_RESOLVE_TOOL = install_source.resolve_tool


def test_augmented_path_contains_local_bin_with_empty_path(monkeypatch, tmp_path):
    home = tmp_path / "fakehome"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    assert str(home / ".local" / "bin") in install_source.augmented_path().split(os.pathsep)
