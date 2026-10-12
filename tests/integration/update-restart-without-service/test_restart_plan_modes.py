"""restart_plan per setup (design.md section 2 table): who restarts the
daemon, whether it can restart in place, and which manual steps fit."""

from __future__ import annotations

import os

import pytest

from aipager import self_restart, self_update


def plan(env):
    return self_update.restart_plan(env.registry)


def test_foreground_restarts_in_place(env):
    p = plan(env)
    assert (p.mode, p.automatic, p.in_place, p.steps, p.container) == (
        "foreground", False, True, "terminal", None)


def test_foreground_without_an_interpreter_shows_steps(env, monkeypatch):
    monkeypatch.setattr(self_restart, "exec_target", lambda: None)
    p = plan(env)
    assert (p.mode, p.in_place, p.steps) == ("foreground", False, "terminal")


def test_systemd_automatic_never_restarts_in_place(env):
    env.set_under_unit(True)
    p = plan(env)
    assert (p.mode, p.automatic, p.in_place, p.steps) == ("systemd", True, False, "systemd")


def test_systemd_old_keys_and_values_are_unchanged(env):
    env.set_under_unit(True)
    d = plan(env).to_dict()
    assert {k: d[k] for k in ("mode", "automatic", "reason", "manual_command")} == {
        "mode": "systemd", "automatic": True, "reason": None,
        "manual_command": "systemctl --user restart aipager.service"}
    assert (d["in_place"], d["steps"], d["container"]) == (False, "systemd", None)


@pytest.mark.parametrize("killmode", ["control-group", "mixed"])
def test_unsafe_killmode_is_manual_and_never_in_place(env, killmode):
    env.set_under_unit(True)
    env.killmode = killmode
    p = plan(env)
    assert (p.mode, p.automatic, p.in_place, p.steps) == ("systemd", False, False, "systemd")
    assert "KillMode" in p.reason


def test_unit_whose_main_pid_is_not_this_daemon_fails_closed(env):
    # Not systemd's daemon, and no exec either: systemd may restart the
    # unit's own daemon too.
    env.set_under_unit(True)
    env.main_pid = os.getpid() + 1
    p = plan(env)
    assert (p.mode, p.automatic, p.in_place, p.steps) == (
        "foreground", False, False, "terminal")
    assert p.reason == "no service unit is running this daemon; restart it yourself"


@pytest.mark.parametrize("marker", ["dockerenv", "containerenv"])
def test_a_container_restarts_in_place_with_docker_steps(env, monkeypatch, marker):
    getattr(env, marker).write_text("")
    monkeypatch.setenv("HOSTNAME", "0123456789ab")
    p = plan(env)
    assert (p.mode, p.automatic, p.in_place, p.steps, p.container) == (
        "docker", False, True, "docker", "0123456789ab")


@pytest.mark.parametrize("hostname", ["aipager", "0123456789AB", "0123456789abc", ""])
def test_a_container_hostname_that_is_not_a_short_id_is_not_used(env, monkeypatch,
                                                                 hostname):
    env.dockerenv.write_text("")
    monkeypatch.setenv("HOSTNAME", hostname)
    assert plan(env).container is None


def test_a_container_without_an_interpreter_shows_steps(env, monkeypatch):
    env.dockerenv.write_text("")
    monkeypatch.setattr(self_restart, "exec_target", lambda: None)
    p = plan(env)
    assert (p.mode, p.in_place, p.steps) == ("docker", False, "docker")


def test_systemd_inside_a_container_is_still_systemd(env):
    env.dockerenv.write_text("")
    env.set_under_unit(True)
    assert plan(env).mode == "systemd"


def test_launchd_restarts_in_place_with_kickstart_as_its_steps(env, monkeypatch):
    monkeypatch.setattr(self_update, "_system", lambda: "Darwin")
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.aipager.daemon")
    p = plan(env)
    assert (p.mode, p.automatic, p.in_place, p.steps) == ("launchd", False, True, "launchd")
    assert p.manual_command == f"launchctl kickstart -k gui/{os.getuid()}/com.aipager.daemon"


def test_a_failed_detection_never_restarts_in_place(env, monkeypatch):
    def boom():
        raise RuntimeError("no platform")
    monkeypatch.setattr(self_update, "_system", boom)
    p = plan(env)
    assert (p.mode, p.automatic, p.in_place, p.steps) == (
        "foreground", False, False, "terminal")
