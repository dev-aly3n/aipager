"""QA iteration 3: a wall clock that went backwards never makes a park count
as settled.

Contract (iteration-3 brief): a wall clock that went backwards never makes
a park count as settled (older than 3 s). Iteration 2: an unforced
bring-back asks (never stops the job) while the park is younger than 3 s;
the message gate's automatic bring-back waits out the window once and then
decides.

A clock that stepped back after the park reads as a ``parked_since`` (a
documented persisted wall stamp) later than now.

Partitions for how far the clock stepped back: 0.5 s / 60 s / 1 h (a park
"in the future"), with the settled park (60 s old) as the control.
Entries: the core, the "Bring it back now" button, Restart (unforced); the
core forced (still brings it back: force is never blocked by age); the
message gate (error guessing: its one wait stays within the 3 s window, it
never sleeps for the size of the step).
"""

from __future__ import annotations

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

BACK = [pytest.param(0.5, id="back_0_5s"), pytest.param(60.0, id="back_60s"),
        pytest.param(3600.0, id="back_1h")]


def _parked(stack, *, back):
    """Parked on an idle job; the wall clock then stepped back *back*
    seconds (a negative *back* is a park that long ago)."""
    qa.park_and_tick(stack)
    stack.sess.parked_since = time.time() + back
    assert stack.sess.parked_job == stack.ids.JOB, "precondition: parked"


def _touched(stack):
    return (qa.stops(stack), [k for k in stack.kinds() if k in ("kill", "launch")])


def _waits(stack):
    return [d[1] for d in stack.dtach if d[0] == "sleep"
            and d[1] not in (claude_jobs.HOLDER_POLL, 0.2)]


# ---- control ----------------------------------------------------------------------------

def test_control_a_settled_park_is_brought_back(stack):
    _parked(stack, back=-60)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.ok, qa.stops(stack)) == (True, [["stop", stack.ids.JOB]])


# ---- unforced: asks ---------------------------------------------------------------------

@pytest.mark.parametrize("back", BACK)
def test_the_core_asks(stack, back):
    _parked(stack, back=back)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")


@pytest.mark.parametrize("back", BACK)
def test_the_core_stops_nothing(stack, back):
    _parked(stack, back=back)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert _touched(stack) == ([], [])


@pytest.mark.parametrize("back", BACK)
def test_the_button_stops_nothing(stack, back):
    _parked(stack, back=back)
    stack.tap("bgback")
    assert _touched(stack) == ([], [])


@pytest.mark.parametrize("back", BACK)
def test_restart_asks(stack, back):
    _parked(stack, back=back)
    out = stack.run(stack.bot._restart_session_core(stack.sess))
    assert (out.reason, _touched(stack)) == ("bg_busy", ([], []))


@pytest.mark.parametrize("back", BACK)
def test_the_park_is_kept(stack, back):
    _parked(stack, back=back)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert stack.sess.parked_job == stack.ids.JOB


# ---- forced: age never blocks it --------------------------------------------------------

@pytest.mark.parametrize("back", BACK)
def test_forced_it_is_brought_back(stack, back):
    _parked(stack, back=back)
    out = stack.run(stack.bot._bring_back_core(stack.sess, force=True))
    assert (out.ok, qa.stops(stack)) == (True, [["stop", stack.ids.JOB]])


# ---- the message gate -------------------------------------------------------------------

@pytest.mark.parametrize("back", BACK)
def test_the_gate_holds_the_message(stack, mk_update, back):
    _parked(stack, back=back)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert (stack.typed, [q[0] for q in stack.sess.pending_queue]) == ([], ["carry on"])


@pytest.mark.parametrize("back", BACK)
def test_the_gates_wait_stays_within_the_window(stack, mk_update, back):
    """Error guessing: the gate waits out a young park once; a clock step of
    an hour must not turn that into an hour's sleep."""
    _parked(stack, back=back)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert all(0 < w <= claude_jobs.PARK_SETTLE_SECONDS for w in _waits(stack)), _waits(stack)
