"""QA iteration 2: an unforced bring-back asks, and never stops the job,
while work may still run.

Contract (iteration-2 brief; S5; design.md success criterion 6 "never
kills anything ... while the job is busy unless forced"):
- an unforced bring-back asks (never stops the job) when the session is
  BUSY or INTERACTIVE or has work in flight, or while the park is younger
  than 3 s;
- for the automatic bring-back on a message, the young-park case waits out
  the window once and then decides.

Partitions:
- the session: IDLE (control) / BUSY / INTERACTIVE / IDLE with work in
  flight (a background agent's tool call still running after the turn's
  Stop) / IDLE with the job's own entry busy (``job_busy``);
- the park's age: 0 s / 2.9 s / 3.1 s / 60 s (boundaries of the 3 s window),
  against a frozen wall clock (``session_ops._now_wall``, see
  ``_frozen_wall``): on the real clock a slow runner aged a 2.9 s park past
  the window between the setup and the check;
- the entry: the core, the "Bring it back now" button, Restart, the Mini
  App's Bring it back (all unforced) / forced (Stop and bring back);
- for the message gate's automatic bring-back: a young park whose job
  stays idle / turns busy during the wait / whose moved turn shows up
  during the wait.

The job's own registry entry reads "idle" in every case but ``job_busy``:
the point is that aipager's own view, or the park's youth, decides.
"""

from __future__ import annotations

import time

import pytest

from aipager import claude_jobs
from aipager.bot import session_ops
from aipager.state import Status


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

#: The frozen wall clock: years before the real time, so a park-age reader
#: that still used ``time.time()`` would see every park here as settled.
WALL = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _frozen_wall(monkeypatch):
    monkeypatch.setattr(session_ops, "_now_wall", lambda: WALL)


def _tp(stack):
    return str(stack.claude.projects / f"{stack.ids.JS}.jsonl")


def _work_in_flight(stack):
    """The moved turn ended (Stop) but its background agent still runs a
    tool: IDLE, with work in flight."""
    h = dict(bg_job=stack.ids.JOB, session_id=stack.ids.JS, transcript_path=_tp(stack))
    stack.registry.transition(stack.ids.NAME, Status.BUSY)
    stack.hook(hook_event_name="UserPromptSubmit", prompt="go", **h)
    stack.hook(hook_event_name="SubagentStart", agent_id="ag1",
               agent_type="general-purpose", **h)
    stack.claude.append(stack.ids.JS, {"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "text", "text": "started"}]}})
    stack.hook(hook_event_name="Stop", last_assistant_message="started", **h)
    stack.hook(hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="tu9",
               agent_id="ag1", tool_input={"command": "make"}, **h)


def _state(stack, state):
    """Put the parked session in *state* (the job's entry stays idle)."""
    if state == "busy":
        stack.registry.transition(stack.ids.NAME, Status.BUSY)
    elif state == "interactive":
        stack.registry.transition(stack.ids.NAME, Status.INTERACTIVE)
    elif state == "job_busy":
        # The session is IDLE; only the job's own entry says it works (since
        # iteration 2, qa.park leaves the session BUSY for a busy job, so
        # the iteration-1 busy-job tests no longer tell the two apart).
        qa.job_status(stack, "busy")
        assert stack.sess.status == Status.IDLE, "precondition: idle"
    elif state == "work_in_flight":
        _work_in_flight(stack)
        assert stack.sess.status == Status.IDLE, "precondition: the turn ended"
        assert stack.sess.work_in_flight(time.monotonic()), "precondition: work in flight"
    else:
        assert stack.sess.status == Status.IDLE, "precondition: idle"
    stack.sess.parked_since = WALL - 60


def _parked(stack, *, age=60.0, state="idle"):
    qa.park_and_tick(stack)
    _state(stack, state)
    stack.sess.parked_since = WALL - age
    assert stack.sess.parked_job == stack.ids.JOB, "precondition: parked"


def _touched(stack):
    return (qa.stops(stack), [k for k in stack.kinds() if k in ("kill", "launch")])


STATES = ["busy", "interactive", "work_in_flight", "job_busy"]
UNSETTLED = [pytest.param(0.0, id="age_0s"), pytest.param(2.9, id="age_2_9s")]
SETTLED = [pytest.param(3.1, id="age_3_1s"), pytest.param(60.0, id="age_60s")]


# ---- the core, unforced --------------------------------------------------------------

@pytest.mark.parametrize("state", STATES)
def test_core_asks_while_work_may_run(stack, state):
    _parked(stack, state=state)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")


@pytest.mark.parametrize("state", STATES)
def test_core_stops_nothing_while_work_may_run(stack, state):
    _parked(stack, state=state)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert _touched(stack) == ([], [])


@pytest.mark.parametrize("state", STATES)
def test_core_keeps_the_park_while_work_may_run(stack, state):
    _parked(stack, state=state)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert stack.sess.parked_job == stack.ids.JOB


@pytest.mark.parametrize("age", UNSETTLED)
def test_core_asks_on_a_young_park(stack, age):
    _parked(stack, age=age)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.reason, _touched(stack)) == ("bg_busy", ([], []))


@pytest.mark.parametrize("age", SETTLED)
def test_core_brings_back_a_settled_idle_park(stack, age):
    _parked(stack, age=age)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.ok, qa.stops(stack)) == (True, [["stop", stack.ids.JOB]])


# ---- forced never asks ---------------------------------------------------------------

@pytest.mark.parametrize("state", STATES)
def test_forced_brings_back_while_work_may_run(stack, state):
    _parked(stack, state=state)
    out = stack.run(stack.bot._bring_back_core(stack.sess, force=True))
    assert (out.ok, qa.stops(stack)) == (True, [["stop", stack.ids.JOB]])


@pytest.mark.parametrize("age", UNSETTLED)
def test_forced_brings_back_a_young_park(stack, age):
    _parked(stack, age=age)
    out = stack.run(stack.bot._bring_back_core(stack.sess, force=True))
    assert (out.ok, qa.stops(stack)) == (True, [["stop", stack.ids.JOB]])


def test_forced_stops_before_it_kills_on_a_young_busy_park(stack, monkeypatch):
    _parked(stack, age=0.5, state="busy")
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        stack.dtach.append(("run", list(argv[1:2])))
        return await inner(*argv, **kw)
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)
    stack.run(stack.bot._bring_back_core(stack.sess, force=True))
    order = [d[0] if d[0] != "run" else d[1][0] for d in stack.dtach
             if d[0] in ("run", "kill", "launch")]
    assert [o for o in order if o in ("stop", "kill", "launch")] == ["stop", "kill", "launch"]


# ---- the other unforced entries ------------------------------------------------------

@pytest.mark.parametrize("state", STATES)
def test_the_button_asks_while_work_may_run(stack, state):
    _parked(stack, state=state)
    stack.tap("bgback")
    assert _touched(stack) == ([], [])


@pytest.mark.parametrize("age", UNSETTLED)
def test_the_button_asks_on_a_young_park(stack, age):
    _parked(stack, age=age)
    q = stack.tap("bgback")
    edited = [c.args[0] if c.args else c.kwargs.get("text", "")
              for c in q.edit_message_text.await_args_list]
    assert (qa.BUSY_QUESTION in edited + qa.sent_texts(stack), _touched(stack)) == (
        True, ([], []))


@pytest.mark.parametrize("age", SETTLED)
def test_the_button_brings_back_a_settled_park(stack, age):
    _parked(stack, age=age)
    stack.tap("bgback")
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]


@pytest.mark.parametrize("state", STATES)
def test_restart_asks_while_work_may_run(stack, state):
    _parked(stack, state=state)
    out = stack.run(stack.bot._restart_session_core(stack.sess))
    assert (out.reason, _touched(stack)) == ("bg_busy", ([], []))


@pytest.mark.parametrize("age", UNSETTLED)
def test_restart_asks_on_a_young_park(stack, age):
    _parked(stack, age=age)
    out = stack.run(stack.bot._restart_session_core(stack.sess))
    assert (out.reason, _touched(stack)) == ("bg_busy", ([], []))


@pytest.mark.parametrize("age", SETTLED)
def test_restart_brings_back_a_settled_park(stack, age):
    _parked(stack, age=age)
    out = stack.run(stack.bot._restart_session_core(stack.sess))
    assert (out.ok, qa.stops(stack)) == (True, [["stop", stack.ids.JOB]])


@pytest.mark.parametrize("state", STATES)
def test_a_tap_on_stop_and_bring_back_still_forces(stack, state):
    _parked(stack, state=state)
    stack.tap("bgforce")
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]


# ---- the message gate's automatic bring-back -----------------------------------------

@pytest.fixture
def stop_marks(stack, monkeypatch):
    """``claude stop`` leaves a mark in the dtach log, so waits before it are
    told from the holder poll after it."""
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        if list(argv[1:2]) == ["stop"]:
            stack.dtach.append(("kill-marker-stop", None))
        return await inner(*argv, **kw)
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)
    return stack


def _before_stop(stack):
    out = []
    for d in stack.dtach:
        if d[0] == "kill-marker-stop":
            break
        if d[0] == "sleep":
            out.append(d[1])
    return out


@pytest.mark.parametrize("age", [pytest.param(1.0, id="age_1s_waits_2s"),
                                 pytest.param(2.9, id="age_2_9s_waits_0_1s")])
def test_the_gate_waits_out_a_young_park_once(stop_marks, mk_update, age):
    stack = stop_marks
    _parked(stack, age=age)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    waits = _before_stop(stack)
    assert waits == pytest.approx([claude_jobs.PARK_SETTLE_SECONDS - age], abs=1e-6), waits


@pytest.mark.parametrize("age", [pytest.param(1.0, id="age_1s"),
                                 pytest.param(2.9, id="age_2_9s")])
def test_the_gate_then_brings_back_an_idle_job_without_asking(stack, mk_update, age):
    _parked(stack, age=age)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (qa.stops(stack), qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION)) == (
        [["stop", stack.ids.JOB]], 0)


@pytest.mark.parametrize("age", SETTLED)
def test_the_gate_does_not_wait_on_a_settled_park(stop_marks, mk_update, age):
    stack = stop_marks
    _parked(stack, age=age)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (_before_stop(stack), qa.stops(stack)) == ([], [["stop", stack.ids.JOB]])


def _during_the_wait(stack, monkeypatch, change):
    """Run *change* (a coroutine function) right after the bring-back's
    first wait."""
    inner = session_ops._sleep
    done = []

    async def sleep(seconds):
        await inner(seconds)
        if not done:
            done.append(seconds)
            await change()
    monkeypatch.setattr(session_ops, "_sleep", sleep)


def test_a_job_that_turns_busy_during_the_wait_is_asked_about(stack, mk_update, monkeypatch):
    _parked(stack, age=1.0)

    async def busy():
        qa.job_status(stack, "busy")
    _during_the_wait(stack, monkeypatch, busy)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (_touched(stack), qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION)) == (
        ([], []), 1)


def test_a_moved_turn_that_shows_up_during_the_wait_is_asked_about(stack, mk_update,
                                                                  monkeypatch):
    """The job's worker continues the moved turn while the gate waits: its
    linked UserPromptSubmit makes the session BUSY; the job's own entry
    still reads idle."""
    import json
    _parked(stack, age=1.0)

    async def moved_turn():
        await stack.receiver._on_datagram(json.dumps({
            "session": stack.ids.NAME, "hook_event_name": "UserPromptSubmit",
            "bg_job": stack.ids.JOB, "session_id": stack.ids.JS, "prompt": "go on",
            "transcript_path": _tp(stack), "n": 8001}).encode())
        assert stack.sess.status == Status.BUSY, "precondition: the moved turn runs"
    _during_the_wait(stack, monkeypatch, moved_turn)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (_touched(stack), qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION)) == (
        ([], []), 1)


@pytest.mark.parametrize("state", ["busy", "interactive"])
def test_the_gate_asks_at_once_while_work_runs(stop_marks, mk_update, state):
    stack = stop_marks
    _parked(stack, state=state)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (_before_stop(stack), _touched(stack),
            qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION)) == ([], ([], []), 1)


def test_the_gate_asks_while_work_is_in_flight(stack, mk_update):
    _parked(stack, state="work_in_flight")
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (_touched(stack), qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION)) == (
        ([], []), 1)


def test_the_gate_asks_about_a_busy_job_of_an_idle_session(stack, mk_update):
    _parked(stack, state="job_busy")
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (_touched(stack), qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION)) == (
        ([], []), 1)
