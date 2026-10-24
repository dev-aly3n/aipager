"""QA iteration 3: the release backstop waits for IDLE, and a failed
bring-back keeps an earlier pending release.

Contract (iteration-3 brief):
- the 10 s release backstop releases held messages only while the session
  is IDLE; while it is BUSY they wait and go out once it is IDLE, still
  exactly once and in order;
- a bring-back that fails leaves an earlier release that was still pending
  pending.

BUSY without the relaunched claude's SessionStart reaching aipager (a
daemon restart in between, say): someone types at the terminal, and the
relaunched claude's own UserPromptSubmit opens a turn.

Time for the backstop is moved through the bring-back stamp
``bg_released_at`` (as in ``test_qa2_release_once.py``; finding
tester-iter2-004: no clock seam).

Partitions:
- the session when the backstop is due: BUSY just past it (1 s) / BUSY long
  past it (1 h) / IDLE (control);
- after BUSY: the turn ends, then a tick;
- held messages: two / three (order);
- the later bring-back: the stop exits 1 / times out / the launch reports
  failure; and what follows: the park ends at the terminal, then the
  backstop.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from aipager import claude_jobs
from aipager.dtach import inject
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

JOB2 = "ef90ab12"
JS2 = "ef90ab12-0000-4000-8000-000000000007"
WORKER2 = 4201


def _bodies(stack):
    return [t.rsplit("\n", 1)[-1] for t in stack.typed]


def _tp(stack):
    return str(stack.claude.projects / f"{stack.ids.JS}.jsonl")


def _brought_back_holding(stack, mk_update, *texts):
    """Held *texts*, the gate's bring-back done, no SessionStart seen."""
    qa.park_and_tick(stack)
    for i, text in enumerate(texts):
        stack.message(mk_update, text, msg_id=41 + i)
    stack.run_spawned()
    assert (stack.typed, stack.sess.parked_job, stack.sess.bg_release_pending) == (
        [], "", True), "precondition: back, release pending"


def _terminal_turn(stack):
    stack.claude.reg(stack.ids.RELAUNCHED, kind="interactive", sessionId=stack.ids.JS)
    stack.hook(hook_event_name="UserPromptSubmit", prompt="typed at the terminal",
               session_id=stack.ids.JS, transcript_path=_tp(stack),
               claude_pid=stack.ids.RELAUNCHED)
    assert stack.sess.status == Status.BUSY, "precondition: BUSY"


def _backstop(stack, *, past):
    stack.sess.bg_released_at = time.monotonic() - (claude_jobs.RELEASE_BACKSTOP + past)
    stack.tick()


def _turn_ends(stack):
    """The terminal's turn ends: the session is IDLE (and the first held
    message may go out right then, opening its own turn)."""
    qa.own_stop(stack)


PAST = [pytest.param(1, id="1s_past"), pytest.param(3600, id="1h_past")]


# ---- control: IDLE ----------------------------------------------------------------------

def test_control_idle_the_backstop_releases_one(stack, mk_update):
    _brought_back_holding(stack, mk_update, "one", "two")
    _backstop(stack, past=1)
    assert _bodies(stack) == ["one"]


# ---- BUSY: nothing goes out -------------------------------------------------------------

@pytest.mark.parametrize("past", PAST)
def test_busy_the_backstop_releases_nothing(stack, mk_update, past):
    _brought_back_holding(stack, mk_update, "one", "two")
    _terminal_turn(stack)
    _backstop(stack, past=past)
    assert stack.typed == []


@pytest.mark.parametrize("past", PAST)
def test_busy_the_held_messages_stay_held(stack, mk_update, past):
    _brought_back_holding(stack, mk_update, "one", "two")
    _terminal_turn(stack)
    _backstop(stack, past=past)
    assert [q[0] for q in stack.sess.pending_queue] == ["one", "two"]


@pytest.mark.parametrize("past", PAST)
def test_busy_several_ticks_release_nothing(stack, mk_update, past):
    _brought_back_holding(stack, mk_update, "one", "two")
    _terminal_turn(stack)
    for _ in range(3):
        _backstop(stack, past=past)
    assert stack.typed == []


# ---- then IDLE: out once, in order ------------------------------------------------------

@pytest.mark.parametrize("past", PAST)
def test_once_idle_the_first_goes_out(stack, mk_update, past):
    _brought_back_holding(stack, mk_update, "one", "two")
    _terminal_turn(stack)
    _backstop(stack, past=past)
    _turn_ends(stack)
    _backstop(stack, past=past)
    assert _bodies(stack) == ["one"]


@pytest.mark.parametrize("past", PAST)
def test_once_idle_the_second_waits_for_the_firsts_turn(stack, mk_update, past):
    _brought_back_holding(stack, mk_update, "one", "two")
    _terminal_turn(stack)
    _backstop(stack, past=past)
    _turn_ends(stack)
    _backstop(stack, past=past)
    assert [q[0] for q in stack.sess.pending_queue] == ["two"]


@pytest.mark.parametrize("past", PAST)
def test_once_idle_more_ticks_add_nothing(stack, mk_update, past):
    _brought_back_holding(stack, mk_update, "one", "two")
    _terminal_turn(stack)
    _backstop(stack, past=past)
    _turn_ends(stack)
    for _ in range(3):
        _backstop(stack, past=past)
    assert _bodies(stack) == ["one"]


LATE_START = [pytest.param("resume", id="resume"), pytest.param("clear", id="clear")]


LATE_START_RELEASES = (
    "qa finding tester-iter3-001: when the backstop waited out a BUSY turn "
    "and the first held message went out at that turn's end, the release "
    "stays armed (bg_release_pending is still True), so a later own "
    "SessionStart of the same claude (/clear at the terminal) sends the "
    "second held message into the first one's running turn")


@pytest.mark.xfail(strict=True, reason=LATE_START_RELEASES)
@pytest.mark.parametrize("source", LATE_START)
@pytest.mark.parametrize("past", PAST)
def test_once_idle_a_late_session_start_adds_nothing(stack, mk_update, past, source):
    """The first held message went out when the terminal's turn ended, and
    its own turn now runs. A SessionStart of the relaunched claude after
    that (``/clear`` typed at the terminal, say) must not send the second
    one into the running turn."""
    _brought_back_holding(stack, mk_update, "one", "two")
    _terminal_turn(stack)
    _backstop(stack, past=past)
    _turn_ends(stack)
    _backstop(stack, past=past)
    assert _bodies(stack) == ["one"], "precondition: the first went out"
    stack.hook(hook_event_name="SessionStart", source=source, session_id=stack.ids.JS,
               claude_pid=stack.ids.RELAUNCHED)
    assert _bodies(stack) == ["one"]


def test_once_idle_three_go_out_in_order_and_once(stack, mk_update):
    _brought_back_holding(stack, mk_update, "one", "two", "three")
    _terminal_turn(stack)
    _backstop(stack, past=1)
    _turn_ends(stack)
    _backstop(stack, past=1)
    qa.own_turns(stack, 3)
    _backstop(stack, past=1)
    assert _bodies(stack) == ["one", "two", "three"]


# ---- a failed bring-back keeps an earlier pending release -------------------------------

def _parked_again(stack):
    """The relaunched claude (no SessionStart seen yet) is parked on JOB2
    by a new ``←``; the earlier release is still pending."""
    ids = stack.ids
    stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=ids.JS, status="idle",
                     parkedJobId=JOB2)
    stack.claude.reg(WORKER2, kind="bg", jobId=JOB2, sessionId=JS2, status="idle")
    stack.claude.stop_ends[JOB2] = WORKER2
    stack.claude.append(ids.JS, {"type": "continued-in", "continuedInSessionId": JS2})
    stack.claude.transcript(JS2, {"type": "user"}, {"type": "assistant"})
    stack.tick()
    assert stack.sess.parked_job == JOB2, "precondition: parked again"
    stack.sess.parked_since = time.time() - 60


def _fails(stack, monkeypatch, how):
    if how == "launch":
        async def launch(short_name, **kw):
            stack.dtach.append(("launch", kw))
            return False, "dtach could not start"
        monkeypatch.setattr(inject, "launch_session", launch)
        return
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        if list(argv[1:2]) != ["stop"]:
            return await inner(*argv, **kw)
        stack.claude.runs.append(list(argv[1:]))

        class Proc:
            returncode = None

            async def communicate(self, _stdin=None):
                if how == "timeout":
                    raise asyncio.TimeoutError
                self.returncode = 1
                return b"", b""

            def kill(self):
                pass

            async def wait(self):
                return self.returncode
        return Proc()
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)


STOP_FAILS = [pytest.param("rc", id="stop_exit_1"), pytest.param("timeout", id="stop_timeout")]
ALL_FAILS = STOP_FAILS + [pytest.param("launch", id="launch_reports_failure")]


def _second_bring_back_fails(stack, mk_update, monkeypatch, how):
    _brought_back_holding(stack, mk_update, "one", "two")
    _parked_again(stack)
    _fails(stack, monkeypatch, how)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert out.ok is False, f"precondition: the second bring-back failed ({out})"


@pytest.mark.parametrize("how", ALL_FAILS)
def test_a_failed_bring_back_leaves_the_earlier_release_pending(stack, mk_update,
                                                                monkeypatch, how):
    _second_bring_back_fails(stack, mk_update, monkeypatch, how)
    assert stack.sess.bg_release_pending is True


@pytest.mark.parametrize("how", STOP_FAILS)
def test_while_parked_again_the_pending_release_types_nothing(stack, mk_update,
                                                              monkeypatch, how):
    _second_bring_back_fails(stack, mk_update, monkeypatch, how)
    _backstop(stack, past=1)
    assert stack.typed == []


@pytest.mark.parametrize("how", STOP_FAILS)
def test_once_the_park_ends_the_pending_release_goes_out(stack, mk_update, monkeypatch,
                                                         how):
    """The person at the terminal brings the conversation back; the release
    that was pending all along sends the first held message."""
    _second_bring_back_fails(stack, mk_update, monkeypatch, how)
    stack.claude.reg(stack.ids.RELAUNCHED, kind="interactive", sessionId=stack.ids.JS,
                     status="idle")
    stack.tick()
    assert stack.sess.parked_job == "", "precondition: the park ended"
    _backstop(stack, past=1)
    assert _bodies(stack) == ["one"]
