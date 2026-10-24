"""Claude's exit code, recorded by the launch shell (background-jobs step 4).

Both launch paths run claude under a shell that removes the previous exit
record, runs claude, and then writes ``{"code", "start", "end"}`` to the
session's exit file (``bg_jobs.exit_path``, redirected under tmp_path by
the conftest). The daemon reads it when the session's socket vanishes.

T4.2: the terminal's ``aipager session`` spawns ``sh -c SCRIPT sh <exit>
env ...`` and a user argument stays one argv element. T4.3: the wrapper,
run with ``/bin/sh`` on a stand-in command (never claude, never dtach),
records the code and a signal death. T4.7 and the reader: only our own,
regular, fresh, well-formed record counts.

The bash side of the daemon's launch (T4.1) is in
``tests/test_dtach_inject_launch.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

from aipager import claude_jobs, claude_launch
from aipager.dtach import bg_jobs
from aipager.dtach import launcher as dtach_launcher

SH = shutil.which("sh") or "/bin/sh"
#: The real runner, kept before any test replaces the launcher's
#: (``dtach_launcher.subprocess`` is the global module).
_RUN = subprocess.run


def _stand_in(tmp_path, body: str):
    """An executable shell script standing in for claude."""
    path = tmp_path / "stand-in"
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


def _record_path(name: str):
    path = bg_jobs.exit_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _run_wrapper(record, *command, env=None):
    """``sh -c SCRIPT sh <record> <command...>``, as the CLI spawns it."""
    return _RUN(
        [SH, "-c", claude_launch.exit_wrapper_script(), "sh", str(record), *command],
        capture_output=True, timeout=30, env=env)


# ---- the fragments ------------------------------------------------------------

def test_the_fragments_name_the_variable_they_are_given():
    prefix, suffix = claude_launch.exit_record("rec")
    assert prefix.startswith('rm -f -- "$rec";')
    assert "s=$(date +%s)" in prefix
    assert suffix.startswith("rc=$?;")
    assert 'mv -f -- "$rec.tmp" "$rec"' in suffix
    assert suffix.endswith("exit $rc")


def test_the_wrapper_keeps_the_command_positional():
    script = claude_launch.exit_wrapper_script()
    assert script.startswith("p=$1; shift; ")
    assert '"$@";' in script
    assert script.endswith("exit $rc")


# ---- T4.3: the wrapper run with /bin/sh ------------------------------------------

def test_a_stand_in_that_exits_3_is_recorded_as_3(tmp_path):
    record = _record_path("claude-dev")
    before = int(time.time())
    done = _run_wrapper(record, str(_stand_in(tmp_path, "exit 3")))
    after = int(time.time())
    assert done.returncode == 3  # the shell ends with claude's status
    data = json.loads(record.read_text())
    assert data["code"] == 3
    assert before <= data["start"] <= data["end"] <= after


def test_a_stand_in_killed_by_sigkill_is_recorded_as_137(tmp_path):
    record = _record_path("claude-dev")
    done = _run_wrapper(record, str(_stand_in(tmp_path, "kill -9 $$")))
    assert done.returncode == 137
    assert json.loads(record.read_text())["code"] == 137


def test_a_clean_exit_is_recorded_as_0(tmp_path):
    record = _record_path("claude-dev")
    done = _run_wrapper(record, str(_stand_in(tmp_path, "exit 0")))
    assert done.returncode == 0
    assert json.loads(record.read_text())["code"] == 0


def test_the_earlier_launchs_record_is_gone_before_the_command_runs(tmp_path):
    """The prefix's ``rm``: a stand-in that finds the old record exits 9."""
    record = _record_path("claude-dev")
    record.write_text('{"code": 0, "start": 1, "end": 1}')
    stand_in = _stand_in(tmp_path, '[ -e "$1" ] && exit 9\nexit 4')
    done = _run_wrapper(record, str(stand_in), str(record))
    assert done.returncode == 4
    assert json.loads(record.read_text())["code"] == 4


def test_the_record_is_ours_alone(tmp_path):
    record = _record_path("claude-dev")
    _run_wrapper(record, str(_stand_in(tmp_path, "exit 1")))
    assert record.stat().st_mode & 0o777 == 0o600
    assert not record.with_name(record.name + ".tmp").exists()


def test_the_wrapped_command_gets_its_arguments_unchanged(tmp_path):
    out = tmp_path / "argv"
    stand_in = _stand_in(tmp_path, 'printf "%s\\0" "$@" > "$OUT"')
    marker = tmp_path / "injected"
    odd = f"a; touch {marker}; $(touch {marker}) `touch {marker}` \"q\" 'r'"
    done = _run_wrapper(_record_path("claude-dev"), str(stand_in), odd, "two",
                        env={**os.environ, "OUT": str(out)})
    assert done.returncode == 0
    assert out.read_bytes().split(b"\0")[:-1] == [odd.encode(), b"two"]
    assert not marker.exists()


# ---- T4.2: the terminal's spawn -------------------------------------------------

def _cli_argv(monkeypatch, claude_bin, claude_args):
    from aipager import claude_resolve
    from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
    monkeypatch.setattr(dtach_launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(dtach_launcher, "_dtach_works", lambda p: (True, ""))
    monkeypatch.setattr(dtach_launcher, "_socket_alive", lambda s: False)
    resolved = ResolvedClaude(chosen=ClaudeInstall(path="/opt/claude",
                                                   realpath="/opt/claude",
                                                   version="2.1.302"))
    monkeypatch.setattr(claude_resolve, "try_resolve_claude_binary",
                        lambda **kw: resolved)
    seen = {}

    class _Refused:
        returncode = 1
        stderr = "stop here\n"
        stdout = ""

    def _run(argv, *a, **k):
        seen["argv"] = argv
        return _Refused()
    monkeypatch.setattr(dtach_launcher.subprocess, "run", _run)
    assert dtach_launcher.launch("dev", claude_args, claude_bin=claude_bin) == 1
    return seen["argv"]


ODD_ARG = "x; touch /nonexistent/never $(id) `id`"


def test_the_terminal_spawn_runs_claude_under_the_exit_wrapper(monkeypatch):
    argv = _cli_argv(monkeypatch, "/opt/claude", ["--name", ODD_ARG])
    assert argv[:4] == ["/fake/dtach", "-n", argv[2], "-Ez"]
    assert argv[4:8] == ["sh", "-c", claude_launch.exit_wrapper_script(), "sh"]
    assert argv[8] == str(bg_jobs.exit_path("claude-dev"))
    assert argv[9] == "env"
    assert argv[10] == "CLAUDE_DTACH_SESSION=claude-dev"
    assert argv.index("/opt/claude") > 9
    # The user's argument is one argv element, as typed, after claude.
    assert argv.count(ODD_ARG) == 1
    assert argv.index(ODD_ARG) > argv.index("/opt/claude")
    assert all(ODD_ARG not in a for a in argv if a != ODD_ARG)


def test_the_spawn_passes_the_rewritten_resume_id(monkeypatch):
    """The args after ``_prepare_resume_args`` (the conversation moved)."""
    monkeypatch.setattr(dtach_launcher, "prepare_resume",
                        lambda asked: ("moved-id", 0))
    argv = _cli_argv(monkeypatch, "/opt/claude", ["--resume", "old-id"])
    assert argv[argv.index("--resume") + 1] == "moved-id"
    assert "old-id" not in argv
    assert argv[4:6] == ["sh", "-c"]


def test_the_terminal_spawn_tail_runs_and_records(monkeypatch, tmp_path):
    """The spawn's command after dtach, run for real on a stand-in claude:
    the odd argument arrives whole and the exit code is recorded."""
    out = tmp_path / "argv"
    stand_in = _stand_in(tmp_path, 'printf "%s\\0" "$@" > "$OUT"\nexit 2')
    marker = tmp_path / "injected"
    odd = f"a; touch {marker}; $(touch {marker})"
    argv = _cli_argv(monkeypatch, str(stand_in), ["--name", odd])
    record = bg_jobs.exit_path("claude-dev")
    record.parent.mkdir(parents=True, exist_ok=True)
    tail = [SH, *argv[5:]]
    done = _RUN(tail, capture_output=True, timeout=30,
                          env={**os.environ, "OUT": str(out)})
    assert done.returncode == 2
    args = out.read_bytes().split(b"\0")[:-1]
    assert args[-2:] == [b"--name", odd.encode()]
    assert not marker.exists()
    assert json.loads(record.read_text())["code"] == 2


# ---- the reader, T4.7 ---------------------------------------------------------------

def _write_record(name="claude-dev", **fields):
    path = _record_path(name)
    path.write_text(json.dumps(fields))
    return path


def test_a_fresh_record_of_ours_is_read():
    now = time.time()
    _write_record(code=1, start=int(now) - 3, end=int(now) - 2)
    assert claude_jobs.read_exit("claude-dev", now) == {
        "code": 1, "start": int(now) - 3, "end": int(now) - 2}


def test_no_record_reads_as_none():
    assert claude_jobs.read_exit("claude-dev", time.time()) is None


def test_a_symlinked_record_is_ignored(tmp_path):
    now = int(time.time())
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"code": 1, "start": now, "end": now}))
    link = _record_path("claude-dev")
    link.symlink_to(real)
    assert claude_jobs.read_exit("claude-dev", now) is None


def test_another_users_record_is_ignored(monkeypatch):
    now = int(time.time())
    _write_record(code=1, start=now, end=now)
    monkeypatch.setattr(bg_jobs, "_getuid", lambda: os.getuid() + 1)
    assert claude_jobs.read_exit("claude-dev", now) is None


@pytest.mark.parametrize("age,fresh", [(110, True), (130, False), (-130, False)],
                         ids=["110s-old", "130s-old", "130s-ahead"])
def test_only_a_fresh_record_counts(age, fresh):
    now = int(time.time())
    _write_record(code=1, start=now - age - 1, end=now - age)
    assert (claude_jobs.read_exit("claude-dev", now) is not None) is fresh


@pytest.mark.parametrize("fields", [
    {"code": "1", "start": 0, "end": 0},
    {"code": True, "start": 0, "end": 0},
    {"code": 1, "start": 0},
    {"code": 256, "start": 0, "end": 0},
    {"code": -1, "start": 0, "end": 0},
    {"code": 1, "start": 5, "end": 4},
], ids=["code-text", "code-bool", "no-end", "code-256", "code-negative", "end-before-start"])
def test_a_malformed_record_is_ignored(fields):
    now = int(time.time())
    shifted = {k: (v + now if k in ("start", "end") else v) for k, v in fields.items()}
    _write_record(**shifted)
    assert claude_jobs.read_exit("claude-dev", now) is None


@pytest.mark.parametrize("code,name", [(137, "SIGKILL"), (143, "SIGTERM"), (129, "SIGHUP"),
                                       (128, ""), (1, ""), (250, "")],
                         ids=["137", "143", "129", "128", "1", "250"])
def test_the_signal_behind_a_code(code, name):
    assert claude_jobs.exit_signal(code) == name


@pytest.mark.parametrize("record,start_failure", [
    ({"code": 1, "start": 100, "end": 110}, True),
    ({"code": 1, "start": 100, "end": 111}, False),
    ({"code": 0, "start": 100, "end": 101}, False),
    ({"code": 137, "start": 100, "end": 101}, False),
    (None, False),
], ids=["1-at-10s", "1-at-11s", "0", "signal", "none"])
def test_what_stopped_right_after_starting_means(record, start_failure):
    assert claude_jobs.is_start_failure(record) is start_failure
