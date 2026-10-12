"""Black box: ``aipager.self_restart`` (entrypoints.md "Exported functions")
and ``self_update.probe_restart_target``: the exec target, the request /
cancel contract, ``after_shutdown`` (lock released before exec, same
argv/env/cwd, fds not inherited, exec failure -> respawn waiter, respawn
failure -> rc 1 with the steps logged) and the waiter command.

Every exec/popen/kill goes to BBEnv's recorders; nothing here can replace,
respawn or signal pytest."""

from __future__ import annotations

import pytest

import logging
import os
import signal
import sys

from aipager import self_restart, self_update

from bb_helpers import BBEnv, LAUNCH_MARK, waiter_ok


@pytest.fixture
def bb(monkeypatch, tmp_path, mk_bot):
    return BBEnv(monkeypatch, tmp_path, mk_bot)


def _target(bb, **kw):
    bb.launch_as_daemon(**kw)
    t = self_restart.exec_target()
    assert t is not None
    return t


def _request(bb, **kw):
    t = _target(bb, **kw)
    bb.attach_stop()
    assert self_restart.request(t, frm="1.0.1", to="1.1.0", steps="terminal") is True
    return t


def _release(bb):
    return lambda: bb.order.append("release_lock")


# ----- exec_target ------------------------------------------------------------------

def test_exec_target_executable_is_this_interpreter(bb):
    assert _target(bb).executable == sys.executable


def test_exec_target_module_launch_keeps_the_module_argv(bb):
    assert _target(bb).argv == [sys.executable, "-m", "aipager", "start"]


def test_exec_target_existing_script_keeps_the_launch_argv(bb, tmp_path):
    script = tmp_path / "bin" / "aipager"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    assert _target(bb, script=str(script)).argv == [sys.executable, str(script), "start"]


def test_exec_target_missing_script_falls_back_to_dash_m_aipager(bb, tmp_path):
    script = tmp_path / "bin" / "aipager-gone"
    assert _target(bb, script=str(script)).argv == [sys.executable, "-m", "aipager", "start"]


def test_exec_target_env_is_the_launch_env(bb):
    assert _target(bb).env.get(LAUNCH_MARK) == "bb-launch-env"


def test_exec_target_env_is_a_snapshot_taken_at_launch(bb, monkeypatch):
    t0 = _target(bb)
    monkeypatch.setenv(LAUNCH_MARK, "changed-after-launch")
    assert self_restart.exec_target().env.get(LAUNCH_MARK) == t0.env.get(LAUNCH_MARK)


def test_exec_target_cwd_is_the_launch_folder(bb, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    assert _target(bb, cwd=work).cwd == str(work)


def test_exec_target_none_when_interpreter_is_gone(bb, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "gone" / "python3"))
    bb.launch_as_daemon()
    assert self_restart.exec_target() is None


# ----- request / cancel ---------------------------------------------------------------

def test_request_calls_the_attached_stop(bb):
    _request(bb)
    assert bb.order == ["stop"]


def test_request_without_attached_stop_returns_false(bb):
    t = _target(bb)
    assert self_restart.request(t, frm="1.0.1", to="1.1.0", steps="terminal") is False


def test_request_while_one_is_pending_returns_false(bb):
    t = _request(bb)
    assert self_restart.request(t, frm="1.0.1", to="1.1.0", steps="terminal") is False


def test_request_after_a_stop_signal_returns_false(bb):
    t = _target(bb)
    bb.attach_stop()
    self_restart.cancel("signal")
    assert self_restart.request(t, frm="1.0.1", to="1.1.0", steps="terminal") is False


def test_request_after_detach_returns_false(bb):
    t = _target(bb)
    bb.attach_stop()
    self_restart.detach()
    assert self_restart.request(t, frm="1.0.1", to="1.1.0", steps="terminal") is False


def test_stop_daemon_uses_the_attached_stop(bb):
    bb.attach_stop()
    self_restart.stop_daemon()
    assert (bb.order, bb.kills) == (["stop"], [])


def test_stop_daemon_without_attached_stop_sigterms_itself(bb):
    self_restart.stop_daemon()
    assert bb.kills == [(os.getpid(), signal.SIGTERM)]


# ----- after_shutdown -------------------------------------------------------------------

def test_after_shutdown_nothing_pending_returns_0(bb):
    assert self_restart.after_shutdown(release_lock=_release(bb)) == 0


def test_after_shutdown_nothing_pending_never_execs(bb):
    self_restart.after_shutdown(release_lock=_release(bb))
    assert bb.execs == []


def test_signal_after_request_returns_0(bb):
    _request(bb)
    self_restart.cancel("signal")
    assert self_restart.after_shutdown(release_lock=_release(bb)) == 0


def test_signal_after_request_never_execs_nor_respawns(bb):
    _request(bb)
    self_restart.cancel("signal")
    self_restart.after_shutdown(release_lock=_release(bb))
    assert (bb.execs, bb.popens) == ([], [])


def test_signal_after_request_logs_the_cancel(bb, caplog):
    _request(bb)
    with caplog.at_level(logging.DEBUG):
        self_restart.cancel("signal")
    assert "restart.cancelled reason=signal" in caplog.text


def test_after_shutdown_execs_this_interpreter(bb):
    _request(bb)
    self_restart.after_shutdown(release_lock=_release(bb))
    assert bb.execs[0][0] == sys.executable


def test_after_shutdown_execs_the_launch_argv(bb):
    _request(bb)
    self_restart.after_shutdown(release_lock=_release(bb))
    assert bb.execs[0][1] == [sys.executable, "-m", "aipager", "start"]


def test_after_shutdown_execs_with_the_launch_env(bb):
    t = _request(bb)
    self_restart.after_shutdown(release_lock=_release(bb))
    assert bb.execs[0][2] == t.env


def test_after_shutdown_releases_the_lock_before_exec(bb):
    _request(bb)
    self_restart.after_shutdown(release_lock=_release(bb))
    assert bb.order == ["stop", "release_lock", "exec"]


def test_after_shutdown_marks_open_fds_non_inheritable_before_exec(bb):
    _request(bb)
    r, w = os.pipe()
    seen = {}
    try:
        os.set_inheritable(r, True)
        os.set_inheritable(w, True)

        def _exec(exe, argv, envp):
            seen["r"] = os.get_inheritable(r)
            seen["w"] = os.get_inheritable(w)
        bb.mp.setattr(self_restart, "_execve", _exec)
        self_restart.after_shutdown(release_lock=_release(bb))
    finally:
        os.close(r)
        os.close(w)
    assert seen == {"r": False, "w": False}


def test_after_shutdown_keeps_stdio_inheritable(bb):
    """fds 0/1/2 are the terminal the restarted daemon keeps."""
    _request(bb)
    seen = {}

    def _exec(exe, argv, envp):
        for fd in (0, 1, 2):
            try:
                seen[fd] = os.get_inheritable(fd)
            except OSError:
                seen[fd] = None
    bb.mp.setattr(self_restart, "_execve", _exec)
    before = {}
    for fd in (0, 1, 2):
        try:
            before[fd] = os.get_inheritable(fd)
        except OSError:
            before[fd] = None
    self_restart.after_shutdown(release_lock=_release(bb))
    assert seen == before


def test_after_shutdown_logs_restart_exec(bb, caplog):
    _request(bb)
    with caplog.at_level(logging.INFO):
        self_restart.after_shutdown(release_lock=_release(bb))
    assert "restart.exec" in caplog.text


def test_after_shutdown_never_logs_env_values(bb, caplog, monkeypatch):
    monkeypatch.setenv("CLAUDE_TG_BOT_TOKEN", "999999:bb-never-log-this-token")
    _request(bb)
    with caplog.at_level(logging.DEBUG):
        self_restart.after_shutdown(release_lock=_release(bb))
    assert "bb-never-log-this-token" not in caplog.text


def test_exec_failure_spawns_the_waiter(bb):
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    self_restart.after_shutdown(release_lock=_release(bb))
    assert len(bb.popens) == 1


def test_exec_failure_with_waiter_returns_0(bb):
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    assert self_restart.after_shutdown(release_lock=_release(bb)) == 0


def test_exec_failure_waiter_waits_for_this_pid(bb):
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    self_restart.after_shutdown(release_lock=_release(bb))
    assert f"kill -0 {os.getpid()}" in " ".join(map(str, bb.popens[0][0][0]))


def test_exec_failure_waiter_runs_the_launch_argv(bb):
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    self_restart.after_shutdown(release_lock=_release(bb))
    cmd = " ".join(map(str, bb.popens[0][0][0]))
    assert f"exec {sys.executable} -m aipager start" in cmd


def test_exec_failure_logs_failed_then_respawn(bb, caplog):
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    with caplog.at_level(logging.INFO):
        self_restart.after_shutdown(release_lock=_release(bb))
    text = caplog.text
    assert 0 <= text.find("restart.exec.failed") < text.find("restart.respawn.started")


def test_exec_failure_respawn_log_goes_to_instance_dir(bb, tmp_path, monkeypatch, caplog):
    inst = tmp_path / "inst"
    inst.mkdir()
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(inst))
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    with caplog.at_level(logging.INFO):
        self_restart.after_shutdown(release_lock=_release(bb))
    assert str(inst / "aipager.log") in caplog.text


def test_executable_gone_after_request_starts_nothing_and_exits_1(bb, tmp_path,
                                                                    monkeypatch):
    """Step 2: the executable is re-checked; gone -> no exec and no waiter
    (it would run the same missing file), exit 1 with the steps logged."""
    fake = tmp_path / "py"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(fake))
    _request(bb)
    fake.unlink()
    rc = self_restart.after_shutdown(release_lock=_release(bb))
    assert (rc, bb.execs, len(bb.popens)) == (1, [], 0)


def test_waiter_spawn_failure_returns_1(bb):
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    bb.popen_raises = OSError(12, "Cannot allocate memory")
    assert self_restart.after_shutdown(release_lock=_release(bb)) == 1


def test_waiter_spawn_failure_logs_manual_steps_at_error(bb, caplog):
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    bb.popen_raises = OSError(12, "Cannot allocate memory")
    with caplog.at_level(logging.INFO):
        self_restart.after_shutdown(release_lock=_release(bb))
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("restart.failed" in m and "aipager start" in m for m in errors), errors


def test_waiter_dying_at_once_returns_1(bb):
    _request(bb)
    bb.exec_raises = OSError(8, "Exec format error")
    bb.popen_poll = 127
    assert self_restart.after_shutdown(release_lock=_release(bb)) == 1


# ----- spawn_waiter -----------------------------------------------------------------------

def _spawn(bb, tmp_path):
    t = _target(bb)
    return t, self_restart.spawn_waiter(t, tmp_path / "aipager.log")


def test_spawn_waiter_ok(bb, tmp_path):
    _t, res = _spawn(bb, tmp_path)
    assert waiter_ok(res) is True


def test_spawn_waiter_returns_the_documented_ok_error_pair(bb, tmp_path):
    """entrypoints.md: ``spawn_waiter(target, log_path) -> (ok, error)``."""
    _t, res = _spawn(bb, tmp_path)
    ok, error = res
    assert (ok, error) == (True, None)


def test_spawn_waiter_runs_sh(bb, tmp_path):
    _spawn(bb, tmp_path)
    argv = list(bb.popens[0][0][0])
    assert argv[0].endswith("sh") and argv[1] == "-c"


def test_spawn_waiter_starts_a_new_session(bb, tmp_path):
    _spawn(bb, tmp_path)
    assert bb.popens[0][1].get("start_new_session") is True


def test_spawn_waiter_passes_the_launch_env(bb, tmp_path):
    t, _ = _spawn(bb, tmp_path)
    assert bb.popens[0][1].get("env") == t.env


def test_spawn_waiter_passes_the_launch_cwd(bb, tmp_path):
    t, _ = _spawn(bb, tmp_path)
    assert bb.popens[0][1].get("cwd") == t.cwd


def test_spawn_waiter_output_goes_to_the_log(bb, tmp_path):
    _spawn(bb, tmp_path)
    assert bb.popens[0][1].get("_stdout_path") == str(tmp_path / "aipager.log")


def test_spawn_waiter_unopenable_log_is_not_ok(bb, tmp_path):
    t = _target(bb)
    res = self_restart.spawn_waiter(t, tmp_path / "no" / "such" / "dir" / "aipager.log")
    assert waiter_ok(res) is False


def test_spawn_waiter_popen_oserror_is_not_ok(bb, tmp_path):
    bb.popen_raises = OSError(12, "Cannot allocate memory")
    _t, res = _spawn(bb, tmp_path)
    assert waiter_ok(res) is False


def test_spawn_waiter_quotes_paths_with_spaces(bb, tmp_path):
    """The waiter's ``sh -c`` command quotes the argv (shlex-joined)."""
    script = tmp_path / "my dir" / "aipager"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    t = _target(bb, script=str(script))
    self_restart.spawn_waiter(t, tmp_path / "aipager.log")
    cmd = bb.popens[0][0][0][2]
    assert f"'{script}'" in cmd


# ----- probe_restart_target ---------------------------------------------------------------

def test_probe_restart_target_reports_the_version(bb):
    t = _target(bb)
    bb.restart_probe = ("1.2.0", True)
    assert self_update.probe_restart_target(t)[:2] == ("1.2.0", True)


def test_probe_restart_target_runs_the_exec_interpreter_without_isolation(bb):
    t = _target(bb)
    self_update.probe_restart_target(t)
    argv = bb.restart_probe_calls()[0]
    assert argv[0] == sys.executable and "-I" not in argv


def test_probe_restart_target_never_passes_secrets(bb, monkeypatch):
    monkeypatch.setenv("CLAUDE_TG_BOT_TOKEN", "999999:bb-secret-value")
    t = _target(bb)
    self_update.probe_restart_target(t)
    assert "bb-secret-value" not in " ".join(bb.probe_envs[0].values())


def test_probe_restart_target_keeps_pythonpath(bb, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/home/alice/checkout")
    t = _target(bb)
    self_update.probe_restart_target(t)
    assert bb.probe_envs[0].get("PYTHONPATH") == "/home/alice/checkout"


def test_probe_restart_target_import_error_is_not_importable(bb):
    t = _target(bb)
    bb.restart_probe = (None, False)
    assert self_update.probe_restart_target(t)[1] is False


def test_probe_restart_target_timeout_is_not_importable(bb, monkeypatch):
    t = _target(bb)

    def _timeout(argv, *, timeout, env=None, capture=True):
        return self_update.CommandResult(None, "", True, f"timed out after {timeout}s")
    monkeypatch.setattr(self_update, "_run_command", _timeout)
    v, ok, err = self_update.probe_restart_target(t)
    assert (ok, err is not None) == (False, True)
