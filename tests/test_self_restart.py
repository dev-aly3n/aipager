"""aipager.self_restart: the exec target, the request, and what happens
after the shutdown (exec, respawn fallback, manual steps). Every exec,
spawn and kill goes to a fake; the conftest refusers catch any other."""

from __future__ import annotations

import logging
import os
import signal
import sys

import pytest

from aipager import instance, self_restart

PY = sys.executable


def _target(**kw) -> self_restart.ExecTarget:
    base = {"executable": PY, "argv": [PY, "-m", "aipager", "start"],
            "env": {"HOME": "/home/alice", "X": "1"}, "cwd": "/home/alice"}
    base.update(kw)
    return self_restart.ExecTarget(**base)


# ---- the launch argv ---------------------------------------------------------------

@pytest.mark.parametrize("argv, expected", [
    ([PY, "-m", "aipager", "start"], (True, None)),
    ([PY, "-c", "from x import y; y()", "start"], (True, None)),
    ([PY, "-I", "-m", "aipager", "start"], (True, None)),
    ([PY, "-Im", "aipager", "start"], (True, None)),
    ([PY, "/usr/bin/aipager", "start"], (True, 1)),
    ([PY, "-u", "-W", "ignore", "/usr/bin/aipager", "start"], (True, 4)),
    ([PY, "-Wignore", "/usr/bin/aipager", "start"], (True, 2)),
    ([PY, "-X", "dev", "--", "/usr/bin/aipager", "start"], (True, 4)),
    ([PY, "--check-hash-based-pycs", "never", "/usr/bin/aipager"], (True, 3)),
    ([PY, "-"], (False, None)),
    ([PY], (False, None)),
])
def test_script_of_reads_the_interpreter_options(argv, expected):
    assert self_restart._script_of(argv) == expected


def test_capture_launch_snapshots_argv_env_and_cwd(monkeypatch):
    monkeypatch.setenv("AIPAGER_TEST_LAUNCH", "before")
    self_restart.capture_launch()
    monkeypatch.setenv("AIPAGER_TEST_LAUNCH", "after")
    target = self_restart.exec_target()
    assert target is not None
    assert target.env["AIPAGER_TEST_LAUNCH"] == "before"
    assert target.cwd == os.getcwd()
    assert target.executable == PY


def test_exec_target_keeps_an_existing_script(tmp_path):
    script = tmp_path / "aipager"
    script.write_text("#!/bin/sh\n")
    self_restart._launch = {"argv": [PY, str(script), "start"], "env": {"A": "1"},
                            "cwd": str(tmp_path)}
    target = self_restart.exec_target()
    assert target.argv == [PY, str(script), "start"]
    assert target.env == {"A": "1"} and target.cwd == str(tmp_path)


def test_exec_target_resolves_a_relative_script_against_the_launch_folder(tmp_path):
    (tmp_path / "run-aipager").write_text("")
    self_restart._launch = {"argv": [PY, "run-aipager", "start"], "env": {},
                            "cwd": str(tmp_path)}
    assert self_restart.exec_target().argv == [PY, "run-aipager", "start"]


def test_a_missing_launch_script_falls_back_to_the_module_form(tmp_path):
    self_restart._launch = {"argv": [PY, str(tmp_path / "gone" / "aipager"), "start"],
                            "env": {}, "cwd": str(tmp_path)}
    assert self_restart.exec_target().argv == [PY, "-m", "aipager", "start"]


def test_the_module_form_is_kept_as_launched(tmp_path):
    self_restart._launch = {"argv": ["python3", "-m", "aipager", "start"], "env": {},
                            "cwd": str(tmp_path)}
    assert self_restart.exec_target().argv == ["python3", "-m", "aipager", "start"]


def test_no_exec_target_without_an_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr(self_restart, "_interpreter", lambda: str(tmp_path / "python"))
    assert self_restart.exec_target() is None
    monkeypatch.setattr(self_restart, "_interpreter", lambda: "python3")   # not absolute
    assert self_restart.exec_target() is None
    noexec = tmp_path / "python-noexec"
    noexec.write_text("")
    noexec.chmod(0o644)
    monkeypatch.setattr(self_restart, "_interpreter", lambda: str(noexec))
    assert self_restart.exec_target() is None


# ---- the request ---------------------------------------------------------------------

def test_request_without_a_daemon_attached_stops_nothing():
    assert self_restart.request(_target(), frm="1.0", to="1.1", steps="terminal") is False
    assert not self_restart.pending()


def test_request_stops_the_daemon_once():
    stops = []
    self_restart.attach(lambda: stops.append(1))
    assert self_restart.request(_target(), frm="1.0", to="1.1", steps="terminal")
    assert stops == [1] and self_restart.pending()
    # A second request while one is pending is refused.
    assert not self_restart.request(_target(), frm="1.0", to="1.1", steps="terminal")
    assert stops == [1]


def test_a_stop_signal_cancels_the_request_and_refuses_new_ones():
    self_restart.attach(lambda: None)
    self_restart.request(_target(), frm="1.0", to="1.1", steps="terminal")
    self_restart.cancel("signal")
    assert not self_restart.pending()
    assert not self_restart.request(_target(), frm="1.0", to="1.1", steps="terminal")


def test_a_raising_stop_drops_the_request():
    def boom():
        raise RuntimeError("x")
    self_restart.attach(boom)
    assert not self_restart.request(_target(), frm="1.0", to="1.1", steps="terminal")
    assert not self_restart.pending()


def test_detach_refuses_later_requests():
    self_restart.attach(lambda: None)
    self_restart.detach()
    assert not self_restart.request(_target(), frm="1.0", to="1.1", steps="terminal")


def test_stop_daemon_uses_the_attached_stop(monkeypatch):
    kills = []
    monkeypatch.setattr(self_restart, "_kill", lambda pid, sig: kills.append((pid, sig)))
    stops = []
    self_restart.attach(lambda: stops.append(1))
    self_restart.stop_daemon()
    assert stops == [1] and kills == []


def test_stop_daemon_without_a_daemon_sends_sigterm_to_itself(monkeypatch):
    kills = []
    monkeypatch.setattr(self_restart, "_kill", lambda pid, sig: kills.append((pid, sig)))
    self_restart.stop_daemon()
    assert kills == [(os.getpid(), signal.SIGTERM)]


# ---- the respawn ----------------------------------------------------------------------

class _Proc:
    def __init__(self, rc=None, pid=4242):
        self.returncode = rc
        self.pid = pid

    def poll(self):
        return self.returncode


@pytest.fixture
def no_settle(monkeypatch):
    monkeypatch.setattr(self_restart, "WAITER_SETTLE_SECONDS", 0)


def test_spawn_waiter_runs_the_launch_argv_after_this_pid(monkeypatch, tmp_path, no_settle):
    calls = []

    def fake_popen(argv, **kw):
        calls.append((argv, kw))
        return _Proc()
    monkeypatch.setattr(self_restart, "_popen", fake_popen)
    target = _target(argv=["/usr/bin/python3", "/opt/my tools/aipager", "start"],
                     cwd=str(tmp_path))
    res = self_restart.spawn_waiter(target, tmp_path / "aipager.log")
    assert res.ok and res.pid == 4242
    ok, error = res
    assert ok is True and error is None
    (argv, kw), = calls
    assert argv[:2] == ["/bin/sh", "-c"]
    assert argv[2] == (f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.2; done; "
                       f"exec {PY} '/opt/my tools/aipager' start")
    assert kw["env"] == target.env and kw["cwd"] == str(tmp_path)
    assert kw["start_new_session"] is True and kw["close_fds"] is True
    assert (tmp_path / "aipager.log").exists()


def test_spawn_waiter_reports_a_waiter_that_died(monkeypatch, tmp_path, no_settle):
    monkeypatch.setattr(self_restart, "_popen", lambda argv, **kw: _Proc(rc=127))
    res = self_restart.spawn_waiter(_target(cwd=str(tmp_path)), tmp_path / "log")
    assert not res.ok and res.stage == "exited"


def test_spawn_waiter_reports_a_spawn_failure(monkeypatch, tmp_path, no_settle):
    def fail(argv, **kw):
        raise OSError("no sh")
    monkeypatch.setattr(self_restart, "_popen", fail)
    res = self_restart.spawn_waiter(_target(cwd=str(tmp_path)), tmp_path / "log")
    assert not res.ok and res.stage == "spawn" and "no sh" in res.error


def test_spawn_waiter_reports_an_unopenable_log(tmp_path, no_settle):
    res = self_restart.spawn_waiter(_target(), tmp_path / "missing" / "dir" / "log")
    assert not res.ok and res.stage == "open"


# ---- after the shutdown ---------------------------------------------------------------

def _pending(target=None, steps="terminal"):
    self_restart.attach(lambda: None)
    assert self_restart.request(target or _target(), frm="1.0.1", to="1.1.0", steps=steps)


def test_nothing_pending_exits_zero_without_exec(monkeypatch):
    released = []
    assert self_restart.after_shutdown(lambda: released.append(1)) == 0
    assert released == []


def test_a_cancelled_request_exits_zero_without_exec():
    _pending()
    self_restart.cancel("signal")
    assert self_restart.after_shutdown(lambda: None) == 0


def test_exec_gets_the_target_after_the_lock_is_released(monkeypatch, tmp_path):
    order = []
    monkeypatch.setattr(self_restart, "_execve",
                        lambda exe, argv, env: order.append(("exec", exe, argv, env)))
    target = _target(cwd=str(tmp_path))
    _pending(target)
    rc = self_restart.after_shutdown(lambda: order.append(("release",)))
    assert rc == 0
    assert order == [("release",), ("exec", PY, target.argv, target.env)]


def test_fds_above_stdio_are_close_on_exec_at_the_exec(monkeypatch, tmp_path):
    r, w = os.pipe()
    try:
        os.set_inheritable(r, True)
        seen = {}
        monkeypatch.setattr(self_restart, "_execve",
                            lambda exe, argv, env: seen.update(inh=os.get_inheritable(r)))
        _pending()
        self_restart.after_shutdown(lambda: None)
        assert seen == {"inh": False}
    finally:
        os.close(r)
        os.close(w)


def test_exec_failure_hands_over_to_the_respawn(monkeypatch, tmp_path, no_settle,
                                                caplog):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))

    def fail(exe, argv, env):
        raise OSError("Exec format error")
    monkeypatch.setattr(self_restart, "_execve", fail)
    spawned = []
    monkeypatch.setattr(self_restart, "_popen",
                        lambda argv, **kw: spawned.append(argv) or _Proc(pid=777))
    target = _target(argv=[PY, "-m", "aipager", "start"], cwd=str(tmp_path))
    _pending(target)
    with caplog.at_level(logging.INFO, logger="aipager.self_restart"):
        rc = self_restart.after_shutdown(lambda: None)
    assert rc == 0
    assert len(spawned) == 1
    assert spawned[0][2].endswith(f"exec {PY} -m aipager start")
    assert f"kill -0 {os.getpid()}" in spawned[0][2]
    assert "restart.exec.failed error=Exec format error" in caplog.text
    assert (f"restart.respawn.started pid=777 log={instance.self_restart_log_path()}"
            in caplog.text)


def test_a_gone_interpreter_skips_the_exec_and_respawns(monkeypatch, tmp_path, no_settle):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    execs, spawned = [], []
    monkeypatch.setattr(self_restart, "_execve", lambda *a: execs.append(a))
    monkeypatch.setattr(self_restart, "_popen",
                        lambda argv, **kw: spawned.append(argv) or _Proc())
    _pending(_target(executable=str(tmp_path / "gone-python"), cwd=str(tmp_path)))
    assert self_restart.after_shutdown(lambda: None) == 0
    assert execs == [] and len(spawned) == 1


@pytest.mark.parametrize("steps, command", [
    ("terminal", "aipager start"),
    ("systemd", "systemctl --user restart aipager.service"),
    ("launchd", "launchctl kickstart -k gui/"),
    ("docker", "docker restart <container>"),
])
def test_nothing_starts_exits_one_with_the_steps_logged(monkeypatch, tmp_path, no_settle,
                                                        caplog, steps, command):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))

    def fail_exec(exe, argv, env):
        raise OSError("boom")

    def fail_spawn(argv, **kw):
        raise OSError("no sh")
    monkeypatch.setattr(self_restart, "_execve", fail_exec)
    monkeypatch.setattr(self_restart, "_popen", fail_spawn)
    _pending(_target(cwd=str(tmp_path)), steps=steps)
    with caplog.at_level(logging.ERROR, logger="aipager.self_restart"):
        rc = self_restart.after_shutdown(lambda: None)
    assert rc == 1
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(m.startswith("restart.failed:") and command in m for m in errors), errors
