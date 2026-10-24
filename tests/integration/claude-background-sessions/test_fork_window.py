"""The moment after ``←`` (review rev-iter1-003, live check 2): a
just-forked job's registry entry reads unknown, then idle, then busy when
a turn moved with it. A message in that moment must never stop the job
unasked.

Through the wired stack (``conftest.Stack``), with the park as fresh as
the tick that noticed it.
"""

from __future__ import annotations

from aipager import claude_jobs
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


def _fresh_park(stack, **kw):
    qa.park(stack, **kw)
    stack.tick()
    assert stack.sess.parked_job == stack.ids.JOB, "precondition: parked"


def _waits(stack):
    return [d[1] for d in stack.dtach if d[0] == "sleep"
            and d[1] not in (claude_jobs.HOLDER_POLL, 0.2)]


def test_a_message_right_after_an_idle_park_waits_then_brings_it_back(stack, mk_update):
    _fresh_park(stack)
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    waits = _waits(stack)
    assert len(waits) == 1 and 0 < waits[0] <= claude_jobs.PARK_SETTLE_SECONDS
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]
    assert qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION) == 0, "not asked"
    assert stack.sess.parked_job == ""


def test_a_message_while_the_moved_turn_runs_asks(stack, mk_update):
    """``←`` mid-turn: the job's entry may read idle for a moment, but the
    session's card is still open (BUSY)."""
    stack.registry.transition(stack.ids.NAME, Status.BUSY)
    qa.park(stack)                       # the job's entry: "idle"
    stack.registry.transition(stack.ids.NAME, Status.BUSY)
    stack.tick()
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    assert qa.stops(stack) == [] and "kill" not in stack.kinds()
    assert qa.count(qa.sent_texts(stack), qa.BUSY_QUESTION) == 1
    assert stack.sess.parked_job == stack.ids.JOB
    assert _waits(stack) == [], "nothing to wait for: a turn runs"


def test_the_button_right_after_the_park_asks(stack):
    """An unforced tap in the moment: the question, never a stop."""
    _fresh_park(stack)
    q = stack.tap("bgback")
    assert qa.stops(stack) == []
    texts = [c.args[0] if c.args else c.kwargs.get("text", "")
             for c in q.edit_message_text.await_args_list]
    assert qa.BUSY_QUESTION in texts
