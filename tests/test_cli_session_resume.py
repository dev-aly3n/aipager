"""``aipager session <name> --resume <id>`` and ``aipager resume <name>``
(R3).

Before the command line resumes a conversation it follows where the
conversation moved (``continued-in``) and rewrites the id, saying so; a
conversation held by an idle Claude Code background job has the job
stopped first (``claude stop <job>``), saying so; a busy one is refused
with exit 1 and the exact ``claude stop`` and ``claude attach`` commands,
and nothing is started. All three spellings of the flag count.

Claude's files are a fake config folder under tmp_path; ``claude stop``
and ``claude agents --json`` run through ``claude_jobs._subprocess_run``,
the dtach spawn through the launcher's own ``subprocess.run`` attribute
(the existing launcher tests' seam), set to fail so nothing attaches.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aipager import claude_jobs
from aipager.cli import resume as cli_resume
from aipager.dtach import launcher

JOB = "ab12cd34"
X = "aaaaaaaa-0000-4000-8000-000000000001"
Y = "ab12cd34-0000-4000-8000-000000000002"
HOLDER = 4200


class Cli:
    def __init__(self, tmp_path, monkeypatch):
        self._mtimes = 0
        self.cfg = tmp_path / "claude-config"
        (self.cfg / "sessions").mkdir(parents=True)
        self.projects = self.cfg / "projects" / "-home-alice-app"
        self.projects.mkdir(parents=True)
        monkeypatch.setattr(claude_jobs, "config_dir", lambda: self.cfg)
        self.alive: set[int] = set()
        monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid in self.alive)
        monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: None)
        self.claude_calls: list[list[str]] = []
        self.stop_rc = 0
        self.stop_ends_job = True
        monkeypatch.setattr(claude_jobs, "_subprocess_run", self._run)
        self.sleeps = 0
        monkeypatch.setattr(claude_jobs, "_sync_sleep", self._sleep)
        # the dtach side of `aipager session`: a spawn that fails, recorded
        self.spawns: list[list[str]] = []
        monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/fake/dtach")
        monkeypatch.setattr(launcher, "_dtach_works", lambda p: (True, ""))
        monkeypatch.setattr(launcher, "_socket_alive", lambda s: False)
        monkeypatch.setattr(launcher.subprocess, "run", self._spawn)

    def reg(self, pid, **fields):
        path = self.cfg / "sessions" / f"{pid}.json"
        path.write_text(json.dumps({"pid": pid, **fields}))
        self._mtimes += 1      # the reader caches by (mtime, size)
        ns = 1_700_000_000_000_000_000 + self._mtimes * 1_000_000
        os.utime(path, ns=(ns, ns))

    def transcript(self, sid, *records):
        (self.projects / f"{sid}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records))

    def moved(self, sid, to):
        self.transcript(sid, {"type": "user"},
                        {"type": "continued-in", "continuedInSessionId": to})

    def holder(self, status="idle", sid=Y):
        self.reg(HOLDER, kind="bg", jobId=JOB, sessionId=sid, status=status)
        self.alive.add(HOLDER)

    def _run(self, argv, **kw):
        args = list(argv[1:])
        self.claude_calls.append(args)
        if args[:1] == ["stop"] and self.stop_rc == 0 and self.stop_ends_job:
            (self.cfg / "sessions" / f"{HOLDER}.json").unlink(missing_ok=True)
            self.alive.discard(HOLDER)
        rc = self.stop_rc if args[:1] == ["stop"] else 1
        return SimpleNamespace(returncode=rc, stdout=b"", stderr=b"")

    def _sleep(self, _seconds):
        self.sleeps += 1

    def _spawn(self, argv, **kw):
        self.spawns.append(list(argv))
        return SimpleNamespace(returncode=1, stderr="spawn refused in tests\n", stdout="")


@pytest.fixture
def cli(tmp_path, monkeypatch):
    return Cli(tmp_path, monkeypatch)


def flat(text: str) -> str:
    """The terminal wraps long lines: compare words, not line breaks."""
    return " ".join(text.split())


# ---- the flag ------------------------------------------------------------------

@pytest.mark.parametrize("args, found", [
    (["--resume", X], (1, "", X)),
    ([f"--resume={X}"], (0, "--resume=", X)),
    (["--model", "opus", "-r", X], (3, "", X)),
    (["--resume"], None),                     # Claude Code's own picker
    (["--resume", "--model", "opus"], None),
    (["--", "--resume", X], None),
    (["--model", "opus"], None),
])
def test_the_three_spellings(args, found):
    assert launcher._resume_arg(args) == found


# ---- T3.16 through `aipager session` -------------------------------------------

FORMS = {
    "space": (["--resume", X], ["--resume", Y]),
    "equals": ([f"--resume={X}"], [f"--resume={Y}"]),
    "short": (["-r", X], ["-r", Y]),
}


@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_moved_conversation_is_resumed_where_it_went(cli, capsys, form):
    asked, rewritten = FORMS[form]
    cli.moved(X, Y)
    cli.transcript(Y, {"type": "user"})
    args = list(asked)
    launcher.launch("dev", args)
    assert args == asked, "the caller's list is left alone"
    spawned = cli.spawns[-1]
    assert spawned[-len(rewritten):] == rewritten
    assert X not in " ".join(spawned)
    out = flat(capsys.readouterr().out)
    assert f"The conversation moved to {Y}, so this session continues there." in out


@pytest.mark.parametrize("form", sorted(FORMS))
def test_the_rewrite_is_a_copy(cli, form):
    asked, rewritten = FORMS[form]
    cli.moved(X, Y)
    cli.transcript(Y, {"type": "user"})
    args = list(asked)
    out, rc = launcher._prepare_resume_args(args)
    assert (out, rc) == (rewritten, 0)
    assert args == asked and out is not args


def test_an_idle_holder_is_stopped_and_the_spawn_goes_on(cli, capsys):
    cli.transcript(Y, {"type": "user"})
    cli.holder("idle")
    launcher.launch("dev", ["--resume", Y])
    assert cli.claude_calls == [["stop", JOB]]
    assert len(cli.spawns) == 1 and cli.spawns[0][-2:] == ["--resume", Y]
    out = flat(capsys.readouterr().out)
    assert (f"Stopped Claude Code's background job {JOB}, which held the conversation, "
            "to resume it here.") in out


def test_a_busy_holder_refuses_and_starts_nothing(cli, capsys):
    cli.transcript(Y, {"type": "user"})
    cli.holder("busy")
    rc = launcher.launch("dev", [f"--resume={Y}"])
    assert rc == 1
    assert cli.spawns == [] and cli.claude_calls == []
    err = flat(capsys.readouterr().err)
    assert f"claude stop {JOB}" in err and f"claude attach {JOB}" in err


def test_a_failed_stop_refuses_and_starts_nothing(cli, capsys):
    cli.transcript(Y, {"type": "user"})
    cli.holder("idle")
    cli.stop_rc = 1
    assert launcher.launch("dev", ["-r", Y]) == 1
    assert cli.spawns == []
    err = flat(capsys.readouterr().err)
    assert f"Could not stop Claude Code's background job {JOB}" in err
    assert "exit code 1" in err


def test_a_job_that_will_not_go_refuses(cli, capsys):
    cli.transcript(Y, {"type": "user"})
    cli.holder("idle")
    cli.stop_ends_job = False
    assert launcher.launch("dev", ["--resume", Y]) == 1
    assert cli.sleeps == int(claude_jobs.HOLDER_WAIT / claude_jobs.HOLDER_POLL)
    assert cli.spawns == []
    assert "the background job is still running" in flat(capsys.readouterr().err)


def test_nothing_in_the_way_spawns_as_before(cli, capsys):
    cli.transcript(X, {"type": "user"})
    launcher.launch("dev", ["--resume", X])
    assert cli.claude_calls == []
    assert cli.spawns[0][-2:] == ["--resume", X]
    assert "moved" not in flat(capsys.readouterr().out)


def test_without_a_resume_nothing_is_looked_at(cli, monkeypatch):
    monkeypatch.setattr(claude_jobs, "prepare_resume_sync",
                        lambda *_a: pytest.fail("looked at Claude's files"))
    launcher.launch("dev", ["--model", "opus"])
    assert len(cli.spawns) == 1


def test_a_reattach_stops_nothing(cli, monkeypatch, tmp_path):
    """The session is already running: the attach resumes nothing, so no
    job is stopped for it."""
    cli.transcript(Y, {"type": "user"})
    cli.holder("idle")
    prefix = tmp_path / "dtach-"
    (tmp_path / "dtach-dev.sock").write_text("")     # a plain file standing in
    monkeypatch.setattr("aipager.instance.dtach_sock_prefix", lambda: str(prefix))
    monkeypatch.setattr(launcher, "_socket_alive", lambda s: True)
    monkeypatch.setattr(launcher, "_attach_following", lambda *a, **k: 5.0)
    assert launcher.launch("dev", ["--resume", Y]) == 0
    assert cli.claude_calls == [] and cli.spawns == []


# ---- T3.16 through `aipager resume` --------------------------------------------

@pytest.fixture
def gone(monkeypatch):
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [{
        "name": "claude-jim", "claude_session_id": X, "cwd": "/home/alice/app",
        "gone_at": 100.0, "perm_mode": "auto"}])
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)
    return launch


def test_resume_follows_the_moved_conversation(cli, gone, capsys):
    cli.moved(X, Y)
    cli.transcript(Y, {"type": "user"})
    assert cli_resume._resume_one("jim") == 0
    assert gone.await_args.kwargs["resume_id"] == Y
    assert f"The conversation moved to {Y}" in flat(capsys.readouterr().out)


def test_resume_stops_an_idle_holder_first(cli, gone):
    cli.transcript(X, {"type": "user"})
    cli.holder("idle", sid=X)
    assert cli_resume._resume_one("jim") == 0
    assert cli.claude_calls == [["stop", JOB]]
    assert gone.await_args.kwargs["resume_id"] == X


def test_resume_refuses_a_busy_holder(cli, gone, capsys):
    cli.transcript(X, {"type": "user"})
    cli.holder("busy", sid=X)
    assert cli_resume._resume_one("jim") == 1
    gone.assert_not_awaited()
    err = flat(capsys.readouterr().err)
    assert f"claude stop {JOB}" in err and f"claude attach {JOB}" in err


def test_resume_with_unreadable_files_is_as_before(cli, gone):
    (cli.cfg / "sessions").rmdir()
    assert cli_resume._resume_one("jim") == 0
    assert gone.await_args.kwargs["resume_id"] == X
    assert cli.claude_calls == []
