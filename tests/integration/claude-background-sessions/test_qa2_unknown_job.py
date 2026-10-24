"""QA iteration 2: a job whose state cannot be known counts as unknown
(ask), never as gone.

Contract (iteration-2 brief; S5; design.md step 3.2 "An unknown state
(registry and agents --json both failed) counts as busy for a non-forced
call"; B4 "a job that is gone means no stop"): when the job listing
(``claude agents --json``) fails and Claude's registry may be incomplete,
the job counts as unknown (ask), not gone.

Why it matters: "gone" skips ``claude stop`` and kills the parked client
while the job may still hold the conversation.

In every case here ``claude agents --json`` fails (the stack's fake
``claude`` answers it with exit code 1).

Partitions for the job's own registry file:
- absent, with the rest of the registry read in full: gone (control: no
  stop, the client is killed and the conversation resumed);
- cut short mid-write (broken JSON) / empty / unreadable (mode 000): the
  registry may be incomplete, so unknown;
- present but behind many newer files (600 stale entries of dead jobs):
  whatever is read, the client is never killed before a stop.
Each unknown case: unforced (asks, touches nothing), forced (stops first),
and the message gate (asks the sender).
"""

from __future__ import annotations

import os
import time

import pytest

from aipager import claude_jobs


def _kit():
    import importlib.util
    import sys
    from pathlib import Path
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()


@pytest.fixture
def ordered(stack, monkeypatch):
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        stack.dtach.append(("run", list(argv[1:])))
        return await inner(*argv, **kw)
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)
    return stack


def _worker_file(stack):
    return stack.claude.sessions / f"{stack.ids.WORKER}.json"


def _bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def _spoil(stack, how):
    """The job's own registry file after the park was noticed."""
    path = _worker_file(stack)
    if how == "cut_short":
        path.write_text('{"pid": %d, "kind": "bg", "jobId": "ab1' % stack.ids.WORKER)
    elif how == "empty":
        path.write_text("")
    elif how == "unreadable":
        path.chmod(0)
    _bump(path)


UNKNOWN = ["cut_short", "empty", "unreadable"]


@pytest.fixture
def restore_modes(stack):
    yield
    path = _worker_file(stack)
    if path.exists():
        path.chmod(0o600)


def _steps(stack):
    return [d[0] if d[0] != "run" else d[1][0] for d in stack.dtach
            if d[0] in ("run", "kill", "launch") and (d[0] != "run" or d[1][:1] == ["stop"])]


# ---- control: a complete registry without the job's file means gone -----------------

def test_control_a_job_gone_from_a_complete_registry_is_not_stopped(stack):
    qa.park_and_tick(stack)
    stack.claude.unreg(stack.ids.WORKER)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.ok, qa.stops(stack), [lc.get("resume_id") for lc in stack.launches()]) == (
        True, [], [stack.ids.JS])


# ---- the job's file cannot be read: unknown ------------------------------------------

@pytest.mark.parametrize("how", UNKNOWN)
def test_an_unreadable_job_entry_is_asked_about(stack, restore_modes, how):
    qa.park_and_tick(stack)
    _spoil(stack, how)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")


@pytest.mark.parametrize("how", UNKNOWN)
def test_an_unreadable_job_entry_kills_nothing(stack, restore_modes, how):
    qa.park_and_tick(stack)
    _spoil(stack, how)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert (qa.stops(stack), stack.kinds()) == ([], [])


@pytest.mark.parametrize("how", UNKNOWN)
def test_a_forced_bring_back_stops_an_unknown_job_before_the_kill(ordered, restore_modes,
                                                                 how):
    stack = ordered
    qa.park_and_tick(stack)
    _spoil(stack, how)
    stack.run(stack.bot._bring_back_core(stack.sess, force=True))
    assert _steps(stack)[:2] == ["stop", "kill"]


@pytest.mark.parametrize("how", UNKNOWN)
def test_the_gate_asks_about_an_unknown_job(stack, mk_update, restore_modes, how):
    qa.park_and_tick(stack)
    _spoil(stack, how)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (stack.kinds(), qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION)) == ([], 1)


@pytest.mark.parametrize("how", UNKNOWN)
def test_the_button_asks_about_an_unknown_job(stack, restore_modes, how):
    qa.park_and_tick(stack)
    _spoil(stack, how)
    stack.tap("bgback")
    assert (qa.stops(stack), stack.kinds()) == ([], [])


# ---- many newer registry files -------------------------------------------------------

def _crowd(stack, n=600):
    """*n* stale entries of dead background jobs, all newer than the job's."""
    base = time.time_ns()
    for i in range(n):
        pid = 20000 + i
        path = stack.claude.sessions / f"{pid}.json"
        qa_job = f"zz{i:06d}"
        path.write_text('{"pid": %d, "kind": "bg", "jobId": "%s", "sessionId": '
                        '"%s-0000-4000-8000-000000000000", "status": "idle"}'
                        % (pid, qa_job, qa_job))
        os.utime(path, ns=(base + i, base + i))


def test_a_job_behind_many_newer_files_is_never_killed_before_a_stop(ordered):
    stack = ordered
    qa.park_and_tick(stack)
    _crowd(stack)
    stack.run(stack.bot._bring_back_core(stack.sess))
    steps = _steps(stack)
    assert steps == [] or steps[0] == "stop", steps


def test_a_forced_bring_back_behind_many_newer_files_stops_the_job(ordered):
    stack = ordered
    qa.park_and_tick(stack)
    _crowd(stack)
    stack.run(stack.bot._bring_back_core(stack.sess, force=True))
    assert _steps(stack)[:2] == ["stop", "kill"]
