"""Black box: ``self_update.restart_plan()`` per row of design.md's table
(section 2) and the extended ``to_dict()``."""

from __future__ import annotations

import os
import sys

import pytest

from aipager import self_update

from bb_helpers import BBEnv


@pytest.fixture
def bb(monkeypatch, tmp_path, mk_bot):
    return BBEnv(monkeypatch, tmp_path, mk_bot)

OLD_KEYS = {"mode", "automatic", "reason", "manual_command"}


def _plan(bb, *, launch=True):
    if launch:
        bb.launch_as_daemon()
    return self_update.restart_plan(bb.registry)


# ----- systemd ------------------------------------------------------------------

def test_systemd_automatic_mode(bb):
    bb.set_under_unit(True)
    assert _plan(bb).mode == "systemd"


def test_systemd_automatic_is_automatic(bb):
    bb.set_under_unit(True)
    assert _plan(bb).automatic is True


def test_systemd_automatic_is_not_in_place(bb):
    bb.set_under_unit(True)
    assert _plan(bb).in_place is False


def test_systemd_automatic_steps_are_systemd(bb):
    bb.set_under_unit(True)
    assert _plan(bb).steps == "systemd"


def test_systemd_to_dict_old_keys_keep_their_values(bb):
    bb.set_under_unit(True)
    d = _plan(bb).to_dict()
    assert {k: d[k] for k in OLD_KEYS} == {
        "mode": "systemd", "automatic": True, "reason": None,
        "manual_command": "systemctl --user restart aipager.service"}


def test_to_dict_has_exactly_the_documented_keys(bb):
    d = _plan(bb).to_dict()
    assert set(d) == OLD_KEYS | {"in_place", "steps", "container"}


def test_systemd_killmode_unsafe_is_not_automatic(bb):
    bb.set_under_unit(True)
    bb.killmode = "control-group"
    assert _plan(bb).automatic is False


def test_systemd_killmode_unsafe_stays_systemd_and_not_in_place(bb):
    bb.set_under_unit(True)
    bb.killmode = "control-group"
    p = _plan(bb)
    assert (p.mode, p.in_place, p.steps) == ("systemd", False, "systemd")


# ----- fail closed: under the unit cgroup but MainPID is someone else ---------------

def test_mainpid_mismatch_is_foreground_and_not_in_place(bb):
    bb.set_under_unit(True)
    bb.main_pid = os.getpid() + 1
    p = _plan(bb)
    assert (p.mode, p.automatic, p.in_place) == ("foreground", False, False)


def test_mainpid_mismatch_steps_terminal(bb):
    bb.set_under_unit(True)
    bb.main_pid = os.getpid() + 1
    assert _plan(bb).steps == "terminal"


# ----- plain foreground -------------------------------------------------------------

def test_foreground_with_exec_target_is_in_place(bb):
    p = _plan(bb)
    assert (p.mode, p.automatic, p.in_place, p.steps) == ("foreground", False, True,
                                                          "terminal")


def test_foreground_without_launch_capture_cannot_restart_in_place_unless_target(bb):
    """No launch captured: in_place mirrors whether exec_target() exists."""
    from aipager import self_restart
    p = _plan(bb, launch=False)
    assert p.in_place is (self_restart.exec_target() is not None)


def test_foreground_interpreter_gone_is_not_in_place(bb, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "gone" / "python3"))
    p = _plan(bb)
    assert (p.mode, p.in_place) == ("foreground", False)


def test_foreground_interpreter_not_executable_is_not_in_place(bb, tmp_path, monkeypatch):
    fake = tmp_path / "python3"
    fake.write_text("")
    fake.chmod(0o644)
    monkeypatch.setattr(sys, "executable", str(fake))
    assert _plan(bb).in_place is False


def test_detection_raising_falls_back_to_foreground_not_in_place(bb, monkeypatch):
    def boom():
        raise RuntimeError("platform probe broke")
    monkeypatch.setattr(self_update, "_system", boom)
    p = _plan(bb)
    assert (p.mode, p.automatic, p.in_place) == ("foreground", False, False)


def test_restart_plan_never_raises_with_unreadable_cgroup(bb, monkeypatch, tmp_path):
    monkeypatch.setattr(self_update, "_PROC_SELF_CGROUP", str(tmp_path / "no" / "cgroup"))
    assert _plan(bb).mode in ("foreground", "systemd", "docker", "launchd")


# ----- containers -------------------------------------------------------------------

def test_dockerenv_gives_docker_mode(bb):
    bb.dockerenv.write_text("")
    assert _plan(bb).mode == "docker"


def test_containerenv_gives_docker_mode(bb):
    bb.containerenv.write_text("")
    assert _plan(bb).mode == "docker"


def test_docker_is_in_place_with_exec_target(bb):
    bb.dockerenv.write_text("")
    p = _plan(bb)
    assert (p.automatic, p.in_place, p.steps) == (False, True, "docker")


def test_docker_without_exec_target_is_not_in_place(bb, tmp_path, monkeypatch):
    bb.dockerenv.write_text("")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "gone" / "python3"))
    p = _plan(bb)
    assert (p.mode, p.in_place, p.steps) == ("docker", False, "docker")


@pytest.mark.parametrize("hostname,expected", [
    ("0123456789ab", "0123456789ab"),          # exactly 12 lowercase hex
    ("0123456789a", None),                     # 11: just outside
    ("0123456789abc", None),                   # 13: just outside
    ("0123456789AB", None),                    # uppercase is not Docker's id
    ("0123456789ag", None),                    # a non-hex digit
    ("web-1", None),                           # a named container
    ("", None),
])
def test_docker_container_id_from_hostname(bb, monkeypatch, hostname, expected):
    bb.dockerenv.write_text("")
    monkeypatch.setenv("HOSTNAME", hostname)
    assert _plan(bb).container == expected


def test_docker_container_none_without_hostname(bb, monkeypatch):
    bb.dockerenv.write_text("")
    monkeypatch.delenv("HOSTNAME", raising=False)
    assert _plan(bb).container is None


def test_no_container_marker_means_no_container_field(bb, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "0123456789ab")
    assert _plan(bb).container is None


def test_systemd_wins_over_container_marker(bb):
    """The container check comes after the systemd check."""
    bb.set_under_unit(True)
    bb.dockerenv.write_text("")
    assert _plan(bb).mode == "systemd"


# ----- launchd ----------------------------------------------------------------------

def _darwin(bb, monkeypatch, xpc="com.aipager.daemon"):
    monkeypatch.setattr(self_update, "_system", lambda: "Darwin")
    if xpc is None:
        monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    else:
        monkeypatch.setenv("XPC_SERVICE_NAME", xpc)


def test_launchd_mode(bb, monkeypatch):
    _darwin(bb, monkeypatch)
    p = _plan(bb)
    assert (p.mode, p.automatic) == ("launchd", False)


def test_launchd_is_in_place(bb, monkeypatch):
    _darwin(bb, monkeypatch)
    assert _plan(bb).in_place is True


def test_launchd_steps_launchd(bb, monkeypatch):
    _darwin(bb, monkeypatch)
    assert _plan(bb).steps == "launchd"


def test_launchd_keeps_kickstart_manual_command(bb, monkeypatch):
    _darwin(bb, monkeypatch)
    cmd = _plan(bb).manual_command or ""
    assert cmd.startswith("launchctl kickstart -k gui/") and cmd.endswith("/com.aipager.daemon")


def test_darwin_other_service_is_foreground(bb, monkeypatch):
    _darwin(bb, monkeypatch, xpc="com.example.other")
    p = _plan(bb)
    assert (p.mode, p.steps) == ("foreground", "terminal")


def test_darwin_terminal_without_xpc_is_foreground(bb, monkeypatch):
    _darwin(bb, monkeypatch, xpc=None)
    assert _plan(bb).mode == "foreground"
