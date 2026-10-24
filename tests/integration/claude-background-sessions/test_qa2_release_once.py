"""QA iteration 2: held messages after a bring-back go out exactly once and
in order.

Contract (iteration-2 brief, design.md step 3 "On success ... the first own
SessionStart of the relaunched claude emits bg_release ... The monitor sends
the same event as a backstop 10 s after the bring-back"):
- after a bring-back, held messages are released exactly once and in order,
  also when the relaunched claude's SessionStart arrives before the
  bring-back finishes;
- the 10 s backstop never releases a second time;
- a failed launch leaves nothing pending.

The release drains one held message; the rest go at turn ends (design.md),
so "exactly once" means: one message typed at the release, never a second
one before that turn ends.

Partitions:
- the relaunched claude's SessionStart: inside the launch (before the
  bring-back ends) / after it / never (backstop only) / twice;
- the backstop: just before its time (boundary, nothing) / just after it /
  after a SessionStart already released;
- held messages: one / three (order);
- the launch: succeeds / reports failure / raises.

Time for the backstop is moved through the session's bring-back stamp
``bg_released_at`` (the developer's own tests do the same; no clock is
patched).
"""

from __future__ import annotations

import json
import time

import pytest

from aipager import claude_jobs
from aipager.dtach import inject


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


def _bodies(stack):
    return [t.rsplit("\n", 1)[-1] for t in stack.typed]


def _hold(stack, mk_update, *texts):
    for i, text in enumerate(texts):
        stack.message(mk_update, text, msg_id=41 + i)
    assert stack.typed == [], "precondition: held while parked"


def _start_inside_the_launch(stack, monkeypatch, *, times=1):
    """The relaunched claude's own SessionStart reaches the receiver before
    ``launch_session`` returns. Returns what was typed at that moment."""
    ids = stack.ids
    launch = inject.launch_session
    seen = {}

    async def launch_and_start(short_name, **kw):
        ok = await launch(short_name, **kw)
        stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=ids.JS)
        for k in range(times):
            await stack.receiver._on_datagram(json.dumps({
                "hook_event_name": "SessionStart", "source": "resume",
                "session": ids.NAME, "session_id": ids.JS,
                "claude_pid": ids.RELAUNCHED, "n": 9100 + k}).encode())
        seen["typed_inside"] = list(stack.typed)
        return ok

    monkeypatch.setattr(inject, "launch_session", launch_and_start)
    return seen


def _backstop(stack, *, past: float):
    """The monitor's tick, *past* seconds after the backstop's time (negative:
    before it)."""
    stack.sess.bg_released_at = time.monotonic() - (claude_jobs.RELEASE_BACKSTOP + past)
    stack.tick()


# ---- the SessionStart comes inside the launch ----------------------------------------

def test_inside_nothing_is_typed_while_the_bring_back_runs(stack, mk_update, monkeypatch):
    qa.park_and_tick(stack)
    seen = _start_inside_the_launch(stack, monkeypatch)
    _hold(stack, mk_update, "one")
    stack.run_spawned()
    assert seen["typed_inside"] == []


def test_inside_the_message_goes_out_as_the_bring_back_ends(stack, mk_update, monkeypatch):
    qa.park_and_tick(stack)
    _start_inside_the_launch(stack, monkeypatch)
    _hold(stack, mk_update, "one")
    stack.run_spawned()
    assert _bodies(stack) == ["one"]


def test_inside_then_the_backstop_adds_nothing(stack, mk_update, monkeypatch):
    qa.park_and_tick(stack)
    _start_inside_the_launch(stack, monkeypatch)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    _backstop(stack, past=1)
    assert _bodies(stack) == ["one"]


def test_two_session_starts_inside_release_once(stack, mk_update, monkeypatch):
    qa.park_and_tick(stack)
    _start_inside_the_launch(stack, monkeypatch, times=2)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    assert _bodies(stack) == ["one"]


def test_inside_then_a_later_session_start_adds_nothing(stack, mk_update, monkeypatch):
    qa.park_and_tick(stack)
    _start_inside_the_launch(stack, monkeypatch)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    qa.relaunched_start(stack)
    assert _bodies(stack) == ["one"]


# ---- the SessionStart comes after the bring-back -------------------------------------

def test_after_nothing_is_typed_before_the_new_claude_starts(stack, mk_update):
    qa.park_and_tick(stack)
    _hold(stack, mk_update, "one")
    stack.run_spawned()
    assert stack.typed == []


def test_after_the_session_start_releases_one(stack, mk_update):
    qa.park_and_tick(stack)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    qa.relaunched_start(stack)
    assert _bodies(stack) == ["one"]


def test_after_a_second_session_start_adds_nothing(stack, mk_update):
    qa.park_and_tick(stack)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    qa.relaunched_start(stack)
    qa.relaunched_start(stack)
    assert _bodies(stack) == ["one"]


def test_after_the_session_start_the_backstop_adds_nothing(stack, mk_update):
    qa.park_and_tick(stack)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    qa.relaunched_start(stack)
    _backstop(stack, past=1)
    assert _bodies(stack) == ["one"]


# ---- no SessionStart: the backstop ---------------------------------------------------

def test_the_backstop_just_before_its_time_releases_nothing(stack, mk_update):
    qa.park_and_tick(stack)
    _hold(stack, mk_update, "one")
    stack.run_spawned()
    _backstop(stack, past=-1)
    assert stack.typed == []


def test_the_backstop_just_after_its_time_releases_one(stack, mk_update):
    qa.park_and_tick(stack)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    _backstop(stack, past=1)
    assert _bodies(stack) == ["one"]


def test_the_backstop_twice_releases_once(stack, mk_update):
    qa.park_and_tick(stack)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    _backstop(stack, past=1)
    _backstop(stack, past=5)
    assert _bodies(stack) == ["one"]


def test_a_session_start_after_the_backstop_adds_nothing(stack, mk_update):
    qa.park_and_tick(stack)
    _hold(stack, mk_update, "one", "two")
    stack.run_spawned()
    _backstop(stack, past=1)
    qa.relaunched_start(stack)
    assert _bodies(stack) == ["one"]


# ---- order ---------------------------------------------------------------------------

@pytest.mark.parametrize("inside", [pytest.param(True, id="start_inside"),
                                    pytest.param(False, id="start_after")])
def test_three_held_messages_go_out_in_order(stack, mk_update, monkeypatch, inside):
    qa.park_and_tick(stack)
    if inside:
        _start_inside_the_launch(stack, monkeypatch)
    _hold(stack, mk_update, "one", "two", "three")
    stack.run_spawned()
    if not inside:
        qa.relaunched_start(stack)
    qa.own_turns(stack, 3)
    assert _bodies(stack) == ["one", "two", "three"]


def test_each_held_message_goes_out_once(stack, mk_update, monkeypatch):
    qa.park_and_tick(stack)
    _start_inside_the_launch(stack, monkeypatch)
    _hold(stack, mk_update, "one", "two", "three")
    stack.run_spawned()
    qa.own_turns(stack, 3)
    _backstop(stack, past=1)
    qa.relaunched_start(stack)
    qa.own_turns(stack, 5)
    assert sorted(_bodies(stack)) == ["one", "three", "two"]


# ---- a failed launch leaves nothing pending ------------------------------------------

def _failing_launch(stack, monkeypatch, how):
    async def launch(short_name, **kw):
        stack.dtach.append(("launch", kw))
        if how == "raises":
            raise OSError("dtach could not start")
        return False, "dtach could not start"
    monkeypatch.setattr(inject, "launch_session", launch)


FAIL = [pytest.param("reports", id="launch_reports_failure"),
        pytest.param("raises", id="launch_raises")]


def _bring_back_that_fails(stack, mk_update, monkeypatch, how):
    qa.park_and_tick(stack)
    _failing_launch(stack, monkeypatch, how)
    _hold(stack, mk_update, "one")
    try:
        stack.run_spawned()
    except OSError:
        pass                              # a raised launch may surface; not the point here
    assert "launch" in stack.kinds(), "precondition: the launch was tried"


@pytest.mark.parametrize("how", FAIL)
def test_a_failed_launch_types_nothing(stack, mk_update, monkeypatch, how):
    _bring_back_that_fails(stack, mk_update, monkeypatch, how)
    assert stack.typed == []


@pytest.mark.parametrize("how", FAIL)
def test_after_a_failed_launch_a_later_session_start_releases_nothing(
        stack, mk_update, monkeypatch, how):
    """A later own SessionStart (a manual resume, say) is not the bring-back's."""
    _bring_back_that_fails(stack, mk_update, monkeypatch, how)
    qa.relaunched_start(stack)
    assert stack.typed == []


@pytest.mark.parametrize("how", FAIL)
def test_after_a_failed_launch_the_backstop_releases_nothing(stack, mk_update, monkeypatch,
                                                            how):
    _bring_back_that_fails(stack, mk_update, monkeypatch, how)
    _backstop(stack, past=1)
    assert stack.typed == []
