"""SC-7: with KillMode=control-group, a foreground daemon, or launchd, no
restart is scheduled; the message says why and gives the exact command.
(design.md Success criteria #7; entrypoints.md "aipager outcomes")."""

from __future__ import annotations

import os
import types

import pytest

from aipager import self_update
from aipager.state import Status


def _ap(bot, h, run_async):
    return run_async(h.run_job(bot, "aipager"))


@pytest.fixture
def launchd(world, monkeypatch):
    world.set_under_unit(False)
    monkeypatch.setattr(self_update, "platform", types.SimpleNamespace(
        system=lambda: "Darwin", machine=lambda: "arm64",
        mac_ver=lambda: ("14.0", ("", "", ""), "arm64")))
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.aipager.daemon")
    return world


@pytest.fixture(params=["foreground", "not_main_pid", "control_group", "unreadable", "launchd"])
def manual(request, world, monkeypatch):
    kind = request.param
    if kind == "foreground":
        world.set_under_unit(False)
    elif kind == "not_main_pid":
        world.main_pid = os.getpid() + 1
    elif kind == "control_group":
        world.killmodes = ["control-group"]
    elif kind == "unreadable":
        world.killmodes = ["\x00garbage"]
    elif kind == "launchd":
        request.getfixturevalue("launchd")
    return kind


def test_manual_mode_schedules_no_restart(world, manual, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert world.schedule_calls() == []


def test_manual_mode_writes_no_marker(world, manual, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_manual_mode_still_installs(world, manual, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert len(world.upgrade_calls()) == 1


def test_manual_mode_does_not_wait_on_busy_sessions(world, manual, personal_bot, h, run_async):
    """design.md: with no automatic restart there is nothing to wait for."""
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)
    _ap(personal_bot, h, run_async)
    assert len(world.upgrade_calls()) == 1


def test_manual_mode_releases_the_lock(world, manual, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert h.lock_is_free()


def test_foreground_says_no_service_unit(world, personal_bot, h, run_async):
    world.set_under_unit(False)
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "no service unit" in h.all_text(personal_bot, msg)


def test_unit_installed_but_not_main_pid_is_foreground(world, personal_bot, h, run_async):
    world.main_pid = os.getpid() + 1
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "no service unit" in h.all_text(personal_bot, msg)


def test_launchd_gives_the_kickstart_command(launchd, personal_bot, h, run_async):
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert f"launchctl kickstart -k gui/{os.getuid()}/com.aipager.daemon" in \
        h.all_text(personal_bot, msg)


def test_control_group_killmode_names_killmode(world, personal_bot, h, run_async):
    world.killmodes = ["control-group"]
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "KillMode" in h.all_text(personal_bot, msg)


def test_control_group_killmode_says_run_service_install(world, personal_bot, h, run_async):
    world.killmodes = ["control-group"]
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "aipager service install" in h.all_text(personal_bot, msg)


def test_control_group_killmode_lists_sessions_at_risk(world, personal_bot, h, run_async):
    world.killmodes = ["control-group"]
    h.add_session(personal_bot.registry, "precious")
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "precious" in h.all_text(personal_bot, msg)


def test_killmode_none_is_automatic(world, personal_bot, h, run_async):
    """Boundary: the other safe KillMode value."""
    world.killmodes = ["none"]
    _ap(personal_bot, h, run_async)
    assert len(world.schedule_calls()) == 1


@pytest.mark.parametrize("killmode", ["mixed", "control-group"])
def test_unsafe_killmodes_are_not_automatic(world, personal_bot, h, run_async, killmode):
    world.killmodes = [killmode]
    _ap(personal_bot, h, run_async)
    assert world.schedule_calls() == []


def test_status_reports_manual_reason_for_control_group(world, personal_bot, h, run_async):
    world.killmodes = ["control-group"]
    status = run_async(personal_bot.updates.status(force=True))
    assert status["restart"]["automatic"] is False and status["restart"]["mode"] == "systemd"


def test_status_reports_foreground_mode(world, personal_bot, h, run_async):
    world.set_under_unit(False)
    status = run_async(personal_bot.updates.status(force=True))
    assert status["restart"]["mode"] == "foreground"


def test_status_reports_launchd_mode(launchd, personal_bot, h, run_async):
    status = run_async(personal_bot.updates.status(force=True))
    assert status["restart"]["mode"] == "launchd"
