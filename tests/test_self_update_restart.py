"""The restart gate, the restart plan, scheduling and the marker (8.36).

The plan reads ``/proc/self/cgroup`` (conftest points it at a tmp file
reading ``0::/``), asks ``systemctl --user show`` through the faked seam,
and — only when KillMode is unsafe — reads the unit's ``cgroup.procs`` and
each pid's ``cmdline`` under tmp roots. Nothing here touches the host's
real systemd.
"""

from __future__ import annotations

import os
import stat
import time

import pytest

from aipager import install_source, self_update
from aipager.self_update import (
    CommandResult,
    RestartPlan,
    read_and_clear_marker,
    restart_blockers,
    restart_plan,
    schedule_restart,
    write_marker,
)
from aipager.state import SessionRegistry, Status, TrackedSession

UNIT_CGROUP = "/user.slice/user-1000.slice/user@1000.service/app.slice/aipager.service"


def _registry(*sessions: TrackedSession) -> SessionRegistry:
    reg = SessionRegistry()
    for s in sessions:
        reg._sessions[s.name] = s
    return reg


def _sess(name="claude-dev", label="dev", status=Status.IDLE, **kw) -> TrackedSession:
    s = TrackedSession(name=name, label=label, status=status)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# ----- the gate -----------------------------------------------------------------

@pytest.mark.parametrize("clause", [
    "busy", "interactive", "background_job", "busy_card", "tool_in_flight",
    "pending_permission", "held_answers",
])
def test_gate_blocks_on(clause):
    now = time.monotonic()
    held = 0
    if clause == "busy":
        sess = _sess(status=Status.BUSY)
    elif clause == "interactive":
        sess = _sess(status=Status.INTERACTIVE)
    elif clause == "background_job":
        sess = _sess(job_continuation_active=True)
    elif clause == "busy_card":
        sess = _sess()
        sess.busy_msg_id = 4242
    elif clause == "tool_in_flight":
        sess = _sess(pending_tool_started_at=now - 1)
    elif clause == "pending_permission":
        sess = _sess(pending_permission={"tool": "Bash"})
    else:
        sess = _sess()
        held = 2
    reasons = restart_blockers(_registry(sess), now=now, held_count=held)
    assert reasons, f"{clause} did not close the gate"
    if clause == "held_answers":
        assert reasons == ["2 held answers not yet delivered"]
    else:
        assert reasons[0].startswith("dev: ")


def test_gate_open_for_idle_unknown_and_gone_sessions():
    reg = _registry(
        _sess(),
        _sess("claude-a", "a", Status.UNKNOWN),
        _sess("claude-b", "b", Status.GONE, pending_permission={"x": 1}),
    )
    # A persisted busy_msg_id on an UNKNOWN session (just after a daemon
    # restart) does not block either.
    reg.get("claude-a").busy_msg_id = 7
    assert restart_blockers(reg, held_count=0) == []


def test_gate_reads_the_real_held_buffer():
    from aipager.bot import held

    held.HELD.hold(chat_id=1, session="claude-dev", label="dev",
                   rich_text="answer", plain_text="answer")
    assert restart_blockers(_registry(), held_count=None) == [
        "1 held answer not yet delivered"]


def test_gate_ignores_a_placeholder_card_id():
    sess = _sess()
    sess.busy_msg_id = 0
    assert restart_blockers(_registry(sess), held_count=0) == []


# ----- the plan -------------------------------------------------------------------

def _under_unit(tmp_path, monkeypatch):
    cg = tmp_path / "cgroup"
    cg.write_text(f"0::{UNIT_CGROUP}\n")
    monkeypatch.setattr(self_update, "_PROC_SELF_CGROUP", str(cg))
    monkeypatch.setattr(self_update, "_system", lambda: "Linux")
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/usr/bin/{n}")


def _fake_show(monkeypatch, *, main_pid=None, killmode="process", rc=0, calls=None):
    pid = os.getpid() if main_pid is None else main_pid

    def _run(argv, *, timeout, env=None, capture=True):
        if calls is not None:
            calls.append(list(argv))
        assert argv[:3] == ["/usr/bin/systemctl", "--user", "show"], argv
        out = f"MainPID={pid}\nKillMode={killmode}\nControlGroup={UNIT_CGROUP}\n"
        return CommandResult(rc, out if rc == 0 else "Failed to connect", False, None)
    monkeypatch.setattr(self_update, "_run_command", _run)


def test_foreground_by_default():
    plan = restart_plan(_registry())
    assert plan.mode == "foreground"
    assert plan.automatic is False
    assert "no service unit" in plan.reason


@pytest.mark.parametrize("killmode", ["process", "none"])
def test_systemd_plan_is_automatic_with_safe_killmode(killmode, tmp_path, monkeypatch):
    _under_unit(tmp_path, monkeypatch)
    calls: list = []
    _fake_show(monkeypatch, killmode=killmode, calls=calls)
    plan = restart_plan(_registry())
    assert (plan.mode, plan.automatic) == ("systemd", True)
    assert len(calls) == 1
    assert calls[0][-1] == "aipager.service"


def _fake_cgroup_with_sessions(tmp_path, monkeypatch, names):
    cg_root = tmp_path / "cgroupfs"
    unit_dir = cg_root / UNIT_CGROUP.lstrip("/")
    unit_dir.mkdir(parents=True)
    proc_root = tmp_path / "proc"
    pids = []
    for i, name in enumerate(names, start=100):
        (proc_root / str(i)).mkdir(parents=True)
        sock = f"/tmp/claude-dtach-{name}.sock"
        (proc_root / str(i) / "cmdline").write_bytes(
            b"\0".join([b"dtach", b"-n", sock.encode(), b"-Ez", b"claude"]) + b"\0")
        pids.append(str(i))
    (proc_root / "99").mkdir()
    (proc_root / "99" / "cmdline").write_bytes(b"/usr/bin/python3\0aipager\0start\0")
    unit_dir.joinpath("cgroup.procs").write_text("\n".join(["99", *pids]) + "\n")
    monkeypatch.setattr(self_update, "_CGROUP_ROOT", str(cg_root))
    monkeypatch.setattr(self_update, "_PROC_ROOT", str(proc_root))


def test_control_group_killmode_refuses_restart_and_lists_sessions(tmp_path, monkeypatch):
    _under_unit(tmp_path, monkeypatch)
    _fake_show(monkeypatch, killmode="control-group")
    _fake_cgroup_with_sessions(tmp_path, monkeypatch, ["dev", "web"])
    reg = _registry(_sess("claude-dev", "dev"), _sess("claude-web", "Web App"),
                    _sess("claude-outside", "outside"))
    plan = restart_plan(reg)
    assert plan.mode == "systemd"
    assert plan.automatic is False
    assert "KillMode" in plan.reason
    assert "aipager service install" in plan.reason
    # Only the sessions whose dtach master is IN the unit's cgroup.
    assert plan.at_risk == ["Web App", "dev"]
    assert schedule_restart(plan) == (False, "this daemon cannot restart itself automatically")


def test_at_risk_falls_back_to_every_live_session_when_unreadable(tmp_path, monkeypatch):
    _under_unit(tmp_path, monkeypatch)
    _fake_show(monkeypatch, killmode="control-group")
    monkeypatch.setattr(self_update, "_CGROUP_ROOT", str(tmp_path / "missing"))
    plan = restart_plan(_registry(_sess("claude-dev", "dev")))
    assert plan.at_risk == ["may include: dev"]


def test_unreadable_killmode_fails_closed(tmp_path, monkeypatch):
    _under_unit(tmp_path, monkeypatch)
    _fake_show(monkeypatch, rc=1)
    plan = restart_plan(_registry())
    assert plan.mode == "systemd"
    assert plan.automatic is False
    assert "could not be read" in plan.reason
    _fake_show(monkeypatch, killmode="")
    assert restart_plan(_registry()).automatic is False


def test_unit_installed_but_not_main_pid_is_foreground(tmp_path, monkeypatch):
    _under_unit(tmp_path, monkeypatch)
    _fake_show(monkeypatch, main_pid=os.getpid() + 12345)
    plan = restart_plan(_registry())
    assert (plan.mode, plan.automatic) == ("foreground", False)


def test_launchd_gets_a_kickstart_command(monkeypatch):
    monkeypatch.setattr(self_update, "_system", lambda: "Darwin")
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.aipager.daemon")
    plan = restart_plan(_registry())
    assert plan.mode == "launchd"
    assert plan.automatic is False
    assert plan.manual_command == f"launchctl kickstart -k gui/{os.getuid()}/com.aipager.daemon"


def test_plan_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("x")
    monkeypatch.setattr(self_update, "_system", _boom)
    assert restart_plan(None).automatic is False


# ----- scheduling -------------------------------------------------------------------

def test_schedule_restart_argv_is_detached_single_restart(monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/usr/bin/{n}")
    seen: list = []

    def _run(argv, *, timeout, env=None, capture=True):
        seen.append((list(argv), timeout, env))
        return CommandResult(0, "", False, None)
    monkeypatch.setattr(self_update, "_run_command", _run)
    ok, unit = schedule_restart(RestartPlan("systemd", True))
    assert ok is True
    (argv, timeout, env), = seen
    assert argv[0] == "/usr/bin/systemd-run" and os.path.isabs(argv[0])
    assert argv[1:3] == ["--user", "--on-active=5s"]
    assert argv[3] == f"--unit={unit}" and unit.startswith("aipager-update-restart-")
    assert argv[4:6] == ["--collect", "--quiet"]
    assert argv[6:] == ["/usr/bin/systemctl", "--user", "restart", "aipager.service"]
    assert argv.count("restart") == 1 and "start" not in argv
    assert timeout == self_update.SCHEDULE_TIMEOUT_SECONDS
    assert "CLAUDE_TG_BOT_TOKEN" not in env


def test_schedule_restart_reports_failure(monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(self_update, "_run_command",
                        lambda argv, **k: CommandResult(1, "Unknown option --collect",
                                                        False, None))
    ok, detail = schedule_restart(RestartPlan("systemd", True))
    assert ok is False and "--collect" in detail


def test_schedule_restart_needs_systemd_run(monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: None)
    assert schedule_restart(RestartPlan("systemd", True))[0] is False


# ----- the marker --------------------------------------------------------------------

def test_marker_round_trip_is_private_and_send_once():
    write_marker({"from": "0.7.13", "to": "0.7.14", "chat_id": 1})
    path = self_update.UPDATE_MARKER_PATH
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_and_clear_marker() == {"from": "0.7.13", "to": "0.7.14", "chat_id": 1}
    assert not path.exists()
    assert read_and_clear_marker() is None


def test_corrupt_marker_is_cleared():
    path = self_update.UPDATE_MARKER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert read_and_clear_marker() is None
    assert not path.exists()


# ----- the CLI's restart instruction ------------------------------------------------

def test_cli_instruction_asks_for_service_install_when_killmode_unsafe(monkeypatch):
    from aipager import service

    service.LINUX_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    service.LINUX_UNIT_PATH.write_text("[Unit]\n")
    monkeypatch.setattr(self_update, "_system", lambda: "Linux")
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(self_update, "_run_command",
                        lambda argv, **k: CommandResult(0, "KillMode=control-group",
                                                        False, None))
    lines = self_update.cli_restart_instruction()
    assert "aipager service install" in lines[0]
    assert "systemctl --user restart aipager.service" in lines[-1]


def test_cli_instruction_without_a_unit_says_restart_yourself(monkeypatch):
    monkeypatch.setattr(self_update, "_system", lambda: "Linux")
    assert self_update.cli_restart_instruction() == [
        "restart your `aipager start` to run the new version"]


def test_two_restarts_in_the_same_second_get_distinct_units(monkeypatch):
    """A voice restart and an update restart in one second must not collide
    on the transient unit name (the second schedule would fail)."""
    from types import SimpleNamespace

    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(self_update, "time",
                        SimpleNamespace(time=lambda: 1_700_000_000.0,
                                        monotonic=lambda: 0.0))
    monkeypatch.setattr(self_update, "_run_command",
                        lambda argv, **k: CommandResult(0, "", False, None))
    ok1, unit1 = schedule_restart(RestartPlan("systemd", True))
    ok2, unit2 = schedule_restart(RestartPlan("systemd", True))
    assert ok1 and ok2
    assert unit1 != unit2
    assert unit1.startswith("aipager-update-restart-1700000000-")


def test_cancel_scheduled_restart_stops_the_timer_by_absolute_path(monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: f"/usr/bin/{n}")
    seen: list = []

    def _run(argv, *, timeout, env=None, capture=True):
        seen.append((list(argv), timeout))
        return CommandResult(0, "", False, None)
    monkeypatch.setattr(self_update, "_run_command", _run)
    assert self_update.cancel_scheduled_restart("aipager-update-restart-1-2-3") is True
    assert seen == [(["/usr/bin/systemctl", "--user", "stop",
                      "aipager-update-restart-1-2-3.timer"],
                     self_update.SYSTEMCTL_TIMEOUT_SECONDS)]
    # Never a unit it did not create, and never without a name.
    assert self_update.cancel_scheduled_restart("aipager.service") is False
    assert self_update.cancel_scheduled_restart(None) is False
    assert len(seen) == 1


def test_missing_main_pid_fails_closed_to_manual(tmp_path, monkeypatch):
    """systemctl printing no MainPID must not count as "this is the main
    PID": fail closed, like an unreadable KillMode."""
    _under_unit(tmp_path, monkeypatch)

    def _run(argv, *, timeout, env=None, capture=True):
        return CommandResult(0, f"KillMode=process\nControlGroup={UNIT_CGROUP}\n",
                             False, None)
    monkeypatch.setattr(self_update, "_run_command", _run)
    plan = restart_plan(_registry())
    assert plan.automatic is False
    assert plan.mode == "foreground"
