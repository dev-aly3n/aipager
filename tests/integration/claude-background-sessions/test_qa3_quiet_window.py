"""QA iteration 3: a new ``←`` right after a bring-back or a restart, and the
old client's leftover registry entry.

Contract (iteration-3 brief):
- a new ``←`` right after a bring-back or restart (inside its quiet window)
  is seen as a park once the relaunch itself has finished;
- the old client's leftover registry entry never counts as a park when that
  client is dead, a zombie, or its pid was reused by another process.

A SIGKILLed claude leaves its ``<cfg>/sessions/<pid>.json`` behind; after a
bring-back the session's conversation is the job's (``JS``), whose first 8
characters are the old job's id, the very id the leftover entry still names
in ``parkedJobId``.

Partitions:
- what came before the new ``←``: a bring-back / a plain Restart of a
  session that was not parked;
- the relaunched claude's own SessionStart: seen / not seen yet;
- who notices the new park: the message gate / the monitor's tick;
- the old client's leftover entry (still saying ``parkedJobId`` = the old
  job): dead / a zombie (a real unreaped child of this test) / pid reused
  (alive, another ``procStart``);
- control: the relaunched claude's own ``←`` is still seen with the
  leftover there.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from aipager import claude_jobs


def _kit():
    import importlib.util
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()

JOB2 = "ef90ab12"
JS2 = "ef90ab12-0000-4000-8000-000000000007"
WORKER2 = 4201


def _bodies(stack):
    return [t.rsplit("\n", 1)[-1] for t in stack.typed]


def _new_park(stack, *, conv):
    """``←`` on the relaunched claude (pid RELAUNCHED), whose conversation
    is *conv*, onto JOB2."""
    ids = stack.ids
    stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=conv, status="idle",
                     parkedJobId=JOB2)
    stack.claude.reg(WORKER2, kind="bg", jobId=JOB2, sessionId=JS2, status="idle")
    stack.claude.stop_ends[JOB2] = WORKER2
    stack.claude.append(conv, {"type": "continued-in", "continuedInSessionId": JS2})
    stack.claude.transcript(JS2, {"type": "user"}, {"type": "assistant"})


def _start(stack, conv):
    ids = stack.ids
    stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=conv, status="idle")
    stack.hook(hook_event_name="SessionStart", source="resume", session_id=conv,
               claude_pid=ids.RELAUNCHED)


# ---- after a bring-back -----------------------------------------------------------------

def _brought_back(stack):
    qa.park_and_tick(stack)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.ok, stack.sess.parked_job, stack.sess.relaunch_in_flight) == (
        True, "", False), "precondition: brought back, the relaunch finished"


START = [pytest.param(True, id="start_seen"), pytest.param(False, id="start_not_seen")]


@pytest.mark.parametrize("seen", START)
def test_after_a_bring_back_a_new_park_is_seen_by_the_gate(stack, mk_update, seen):
    _brought_back(stack)
    if seen:
        _start(stack, stack.ids.JS)
    _new_park(stack, conv=stack.ids.JS)
    stack.message(mk_update, "two", msg_id=42)
    assert (stack.typed, stack.sess.parked_job) == ([], JOB2)


@pytest.mark.parametrize("seen", START)
def test_after_a_bring_back_a_new_park_is_seen_by_the_tick(stack, seen):
    _brought_back(stack)
    if seen:
        _start(stack, stack.ids.JS)
    _new_park(stack, conv=stack.ids.JS)
    stack.tick()
    assert stack.sess.parked_job == JOB2


@pytest.mark.parametrize("seen", START)
def test_after_a_bring_back_the_new_park_writes_its_link(stack, seen):
    _brought_back(stack)
    if seen:
        _start(stack, stack.ids.JS)
    _new_park(stack, conv=stack.ids.JS)
    stack.tick()
    assert (qa.link(JOB2) or {}).get("session") == stack.ids.NAME


# ---- after a plain Restart --------------------------------------------------------------

def _restarted(stack):
    from aipager.state import Status
    stack.registry.transition(stack.ids.NAME, Status.IDLE)
    out = stack.run(stack.bot._restart_session_core(stack.sess))
    assert (out.ok, stack.sess.relaunch_in_flight, len(stack.launches())) == (
        True, False, 1), f"precondition: restarted ({out})"


@pytest.mark.parametrize("seen", START)
def test_after_a_restart_a_new_park_is_seen_by_the_gate(stack, mk_update, seen):
    _restarted(stack)
    if seen:
        _start(stack, stack.ids.X)
    _new_park(stack, conv=stack.ids.X)
    stack.message(mk_update, "two", msg_id=42)
    assert (stack.typed, stack.sess.parked_job) == ([], JOB2)


@pytest.mark.parametrize("seen", START)
def test_after_a_restart_a_new_park_is_seen_by_the_tick(stack, seen):
    _restarted(stack)
    if seen:
        _start(stack, stack.ids.X)
    _new_park(stack, conv=stack.ids.X)
    stack.tick()
    assert stack.sess.parked_job == JOB2


def test_control_after_a_restart_no_new_park_types_the_message(stack, mk_update):
    _restarted(stack)
    _start(stack, stack.ids.X)
    stack.message(mk_update, "two", msg_id=42)
    assert _bodies(stack) == ["two"]


# ---- the old client's leftover entry ----------------------------------------------------

@pytest.fixture
def zombie_pid():
    """A real child of this test that exited and is not reaped yet."""
    if not sys.platform.startswith("linux"):
        pytest.skip("/proc only")
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                stat = Path(f"/proc/{child.pid}/stat").read_text()
            except OSError:
                stat = ""
            if stat.rsplit(")", 1)[-1].split()[:1] == ["Z"]:
                break
            assert time.monotonic() < deadline, "the child never became a zombie"
            time.sleep(0.01)
        yield child.pid
    finally:
        child.wait()


@pytest.fixture
def wired(request):
    """The stack, plus the real pid check for one pid (the zombie); every
    other pid answers from the fake's own set."""
    real_alive = claude_jobs._pid_alive
    stack = request.getfixturevalue("stack")
    stack.real_alive = real_alive
    return stack


def _leftover(stack, monkeypatch, how, zombie=None):
    """After the bring-back, the old client's file is back as a leftover
    that still names the old job. Returns its pid."""
    ids = stack.ids
    pid = ids.CLIENT
    fields = dict(kind="interactive", sessionId=ids.X, status="idle", parkedJobId=ids.JOB)
    if how == "dead":
        stack.claude.reg(pid, **fields)
        stack.claude.alive.discard(pid)
    elif how == "zombie":
        pid = zombie
        stack.claude.reg(pid, **fields)
        stack.claude.alive.discard(pid)
        fake = stack.claude.alive
        real = stack.real_alive
        monkeypatch.setattr(claude_jobs, "_pid_alive",
                            lambda p: real(p) if p == zombie else p in fake)
    elif how == "reused":
        stack.claude.reg(pid, procStart="111", **fields)
        monkeypatch.setattr(claude_jobs, "_proc_start",
                            lambda p: 222 if p == ids.CLIENT else None)
    return pid


HOW = [pytest.param("dead", id="dead"), pytest.param("zombie", id="zombie"),
       pytest.param("reused", id="pid_reused")]


@pytest.mark.parametrize("seen", START)
@pytest.mark.parametrize("how", HOW)
def test_a_leftover_entry_is_not_a_park_for_the_tick(wired, monkeypatch, zombie_pid, how,
                                                      seen):
    stack = wired
    _brought_back(stack)
    if seen:
        _start(stack, stack.ids.JS)
    _leftover(stack, monkeypatch, how, zombie_pid)
    stack.tick()
    assert stack.sess.parked_job == ""


@pytest.mark.parametrize("seen", START)
@pytest.mark.parametrize("how", HOW)
def test_a_leftover_entry_writes_no_link(wired, monkeypatch, zombie_pid, how, seen):
    stack = wired
    _brought_back(stack)
    if seen:
        _start(stack, stack.ids.JS)
    _leftover(stack, monkeypatch, how, zombie_pid)
    stack.tick()
    assert qa.link(stack.ids.JOB) is None


@pytest.mark.parametrize("how", HOW)
def test_a_leftover_entry_does_not_hold_a_message(wired, mk_update, monkeypatch,
                                                  zombie_pid, how):
    """The relaunched claude started and its first turn (the release) is
    over; the next message is typed, not held."""
    stack = wired
    _brought_back(stack)
    _start(stack, stack.ids.JS)
    _leftover(stack, monkeypatch, how, zombie_pid)
    stack.message(mk_update, "next", msg_id=45)
    assert _bodies(stack) == ["next"]


@pytest.mark.parametrize("how", HOW)
def test_a_leftover_entry_sends_no_second_park_notice(wired, monkeypatch, zombie_pid, how):
    stack = wired
    _brought_back(stack)
    _leftover(stack, monkeypatch, how, zombie_pid)
    stack.tick()
    assert qa.count(qa.sent_texts(stack), qa.PARK_NOTICE) == 1


@pytest.mark.parametrize("how", HOW)
def test_control_with_a_leftover_the_relaunched_claudes_own_park_is_seen(
        wired, monkeypatch, zombie_pid, how):
    stack = wired
    _brought_back(stack)
    _leftover(stack, monkeypatch, how, zombie_pid)
    _new_park(stack, conv=stack.ids.JS)
    stack.tick()
    assert stack.sess.parked_job == JOB2
