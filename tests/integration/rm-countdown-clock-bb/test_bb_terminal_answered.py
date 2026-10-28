"""A prompt answered in the terminal is never closed as refused (spec
requirement 4: "a clear different wording if the result shows it was
answered in the terminal instead: reuse the existing terminal-answer
path"; design.md decision 3: "A terminal Escape/No writes the interrupt
marker ... so it wins"; criterion 12-16 "no Allowed" and its converse,
no "Refused by Claude Code" for a person's answer).

Error guessing, from review-1 rev-iter1-001 and rev-iter1-002:

- Yes in the terminal, the rm then FAILS (rmdir on a full folder,
  permission denied). Claude Code fires PostToolUseFailure, and the
  transcript holds the matching tool_use plus an ``is_error`` result: the
  same shape as Claude Code's own refusal. Claude Code's own expiry fires
  no PostToolUse/PostToolUseFailure (live-main Q3), so the hook proves a
  person answered. Through the monitor tick, the deadline timer, the next
  PreToolUse, Stop and StopFailure: no refused text, row or audit.
- Yes in the terminal and the rm succeeds (PostToolUse): the same.
- No/Escape in the terminal: the interrupt marker plus an ``is_error``
  result. Through the tick (marker seen at a later tick) and through the
  timer seam alone (no tick after the marker): no refused records.

Each negative case has a control that differs only in the hook or the
marker and does close as refused, so the negatives are not vacuous."""

from __future__ import annotations

import asyncio
import json

import pytest

from aipager import session_monitor
from tests.rm_clock_bb import REFUSE_AT, REFUSED_EN, RM, T0, WINDOW_AT
from tests.two_prompts_rig import S, answers

AFTER = REFUSE_AT + 1.0
BEFORE = WINDOW_AT - 5.0
MARKER = "[Request interrupted by user for tool use]"
TUID = "toolu_p1"
FAIL_ERROR = "Exit code 1\nrmdir: failed to remove 'build/': Directory not empty"
TOAST_REFUSED_CLOSED = ("Claude Code already refused this by itself, so the question "
                        "is no longer open.")


# -- drivers ---------------------------------------------------------------------------------

def failure_payload(command=RM, tuid=TUID):
    return {"tool_name": "Bash", "tool_input": {"command": command, "description": command},
            "tool_use_id": tuid, "error": FAIL_ERROR, "is_interrupt": False}


def ok_payload(command=RM, tuid=TUID):
    return {"tool_name": "Bash", "tool_input": {"command": command, "description": command},
            "tool_use_id": tuid,
            "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}


POST = {"PostToolUseFailure": failure_payload, "PostToolUse": ok_payload}


def terminal_yes(env, event="PostToolUseFailure", at=None, *, is_error=True):
    """Answered Yes in the terminal: the Post hook, then the lazy transcript."""
    if at is not None:
        env.wall.t = at
    env.hook(event, **POST[event]())
    env.call()
    env.result(is_error=is_error)


def marker(env):
    with env.path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": MARKER}]}}) + "\n")


def refused_anything(env):
    return (env.refused_audit(), env.refused_rows(), env.refused_text_edits())


NOTHING = ([], [], [])


def next_tool(env, at):
    env.wall.t = at
    env.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/home/alice/a"},
             tool_use_id="toolu_next")


def stop(env, at, event="Stop"):
    env.wall.t = at
    env.hook(event)


CLOSERS = {"tick": lambda env, at: env.scan(at), "pretooluse": next_tool, "stop": stop,
           "stopfailure": lambda env, at: stop(env, at, "StopFailure")}


class Timer:
    """The ``_rm_due_sleep`` seam: while "asleep" it runs *during* (the
    world moving on: hooks arriving, the transcript flushing), then moves
    the wall clock by the wait, as a real deadline passing."""

    def __init__(self, wall, during=None):
        self.wall, self.during, self.waits = wall, during, []

    async def __call__(self, seconds):
        self.waits.append(seconds)
        if self.during is not None:
            await self.during()
        self.wall.t += seconds


async def datagram(env, event, **fields):
    """A hook datagram delivered from inside the running loop (the timer's
    sleep), the same way ``Rig.hook`` delivers it from outside."""
    env.rig.recv._recent_fingerprints.clear()
    payload = dict({"session": S, "hook_event_name": event,
                    "transcript_path": str(env.path), "_seq": 10_000 + len(env.rig.sent)},
                   **fields)
    await env.rig.recv._on_datagram(json.dumps(payload).encode())


def arm_timer(env, monkeypatch, during):
    """Arm the deadline timer with a tick right after arrival (nothing in
    the transcript yet), and let it fire with no further tick."""
    timer = Timer(env.wall, during)
    monkeypatch.setattr(session_monitor, "_rm_due_sleep", timer)
    env.wall.t = T0 + 1.0

    async def go():
        await env.monitor._scan()
        for _ in range(100):
            await asyncio.sleep(0)
    env.run_async(go())
    return timer


# -- controls: the same transcript with no terminal signal is refused ------------------------

@pytest.mark.parametrize("closer", CLOSERS)
def test_control_no_terminal_signal_closes_as_refused(env, closer):
    env.rm()
    env.call()
    env.result(is_error=True)
    CLOSERS[closer](env, AFTER)
    assert len(env.refused_audit()) == 1


# -- Yes in the terminal, the rm failed (PostToolUseFailure) -----------------------------------

@pytest.mark.parametrize("closer", CLOSERS)
@pytest.mark.parametrize("hook", [True, False], ids=["hook-parked", "typed"])
def test_terminal_yes_then_failure_is_never_refused(env, closer, hook):
    env.rm(hook=hook)
    terminal_yes(env, at=T0 + 20.0)
    CLOSERS[closer](env, AFTER)
    assert refused_anything(env) == NOTHING


@pytest.mark.parametrize("closer", CLOSERS)
def test_terminal_yes_then_failure_says_allowed_nowhere_and_refused_nowhere(env, closer):
    env.rm()
    terminal_yes(env, at=T0 + 20.0)
    CLOSERS[closer](env, AFTER)
    blob = repr(env.audit()) + repr(env.rig.sess.tool_history) + repr(env.text_edits())
    assert "Refused by Claude Code" not in blob


@pytest.mark.parametrize("closer", CLOSERS)
def test_terminal_yes_then_failure_on_the_inline_card_is_never_refused(cenv, closer):
    cenv.rm()
    terminal_yes(cenv, at=T0 + 20.0)
    CLOSERS[closer](cenv, AFTER)
    assert refused_anything(cenv) == NOTHING


def test_terminal_yes_then_failure_inside_the_window_is_never_refused(env):
    """Boundary: the failure arrives after the window already opened."""
    env.rm()
    env.scan(WINDOW_AT + 1.0)
    terminal_yes(env, at=WINDOW_AT + 2.0)
    env.scan(AFTER)
    assert refused_anything(env) == NOTHING


def test_terminal_yes_then_failure_many_ticks_then_stop_is_never_refused(env):
    env.rm()
    terminal_yes(env, at=T0 + 20.0)
    for at in (BEFORE, WINDOW_AT, REFUSE_AT, AFTER, AFTER + 60.0):
        env.scan(at)
    stop(env, AFTER + 61.0)
    assert refused_anything(env) == NOTHING


def test_terminal_yes_then_failure_tap_types_nothing(env):
    env.rm(hook=False)
    terminal_yes(env, at=T0 + 20.0)
    env.scan(AFTER)
    env.rig.tap(env.separate_ids()[0], "allow")
    assert env.rig.keys == []


def test_terminal_yes_then_failure_tap_does_not_say_refused(env):
    env.rm(hook=False)
    terminal_yes(env, at=T0 + 20.0)
    env.scan(AFTER)
    q = env.rig.tap(env.separate_ids()[0], "allow")
    assert TOAST_REFUSED_CLOSED not in answers(q)


def test_terminal_yes_then_failure_through_the_timer_is_never_refused(env, monkeypatch):
    async def during():
        await datagram(env, "PostToolUseFailure", **failure_payload())
        env.call()
        env.result(is_error=True)
    arm_timer(env, monkeypatch, during)
    assert refused_anything(env) == NOTHING


def test_control_timer_with_the_error_and_no_hook_closes_as_refused(env, monkeypatch):
    env.rm()

    async def during():
        env.call()
        env.result(is_error=True)
    timer = arm_timer(env, monkeypatch, during)
    assert (len(timer.waits), len(env.refused_audit())) == (1, 1)


def test_another_calls_failure_does_not_spare_the_refused_rm(env):
    """Error guessing the other way: a PostToolUseFailure for another call
    (other id, other command) is no answer to the rm, so Claude Code's own
    refusal of the rm still closes as refused."""
    env.rm()
    env.wall.t = T0 + 20.0
    env.hook("PostToolUseFailure", **failure_payload("make test", "toolu_other"))
    env.call()
    env.result(is_error=True)
    env.scan(AFTER)
    assert len(env.refused_audit()) == 1


# -- Yes in the terminal, the rm ran fine (PostToolUse) ------------------------------------------

@pytest.mark.parametrize("closer", CLOSERS)
def test_terminal_yes_then_success_is_never_refused(env, closer):
    env.rm()
    terminal_yes(env, "PostToolUse", at=T0 + 20.0, is_error=False)
    CLOSERS[closer](env, AFTER)
    assert refused_anything(env) == NOTHING


@pytest.mark.parametrize("closer", CLOSERS)
def test_terminal_yes_then_success_closes_the_prompt(env, closer):
    env.rm()
    terminal_yes(env, "PostToolUse", at=T0 + 20.0, is_error=False)
    CLOSERS[closer](env, AFTER)
    assert env.rig.sess.open_prompts() == []


# -- No / Escape in the terminal: the interrupt marker wins ----------------------------------------

def test_marker_seen_at_a_later_tick_wins(env):
    env.rm(hook=False)
    env.scan(WINDOW_AT + 1.0)
    env.call()
    env.result(is_error=True)
    marker(env)
    env.scan(AFTER)
    assert refused_anything(env) == NOTHING


def test_marker_before_the_window_then_ticks_in_it_wins(env):
    env.rm(hook=False)
    env.call()
    env.result(is_error=True)
    marker(env)
    env.scan(BEFORE)
    env.scan(AFTER)
    assert refused_anything(env) == NOTHING


@pytest.mark.parametrize("hook", [True, False], ids=["hook-parked", "typed"])
def test_marker_through_the_timer_alone_wins(env, monkeypatch, hook):
    """rev-iter1-002: the timer runs its check with no tick after the
    marker, so it must look for the interrupt itself."""
    env.rm(hook=hook)

    async def during():
        env.call()
        env.result(is_error=True)
        marker(env)
    timer = arm_timer(env, monkeypatch, during)
    assert (len(timer.waits), refused_anything(env)) == (1, NOTHING)


def test_marker_through_the_timer_says_refused_nowhere(env, monkeypatch):
    env.rm(hook=False)

    async def during():
        env.call()
        env.result(is_error=True)
        marker(env)
    arm_timer(env, monkeypatch, during)
    assert not any(REFUSED_EN in (k.get("text") or "") for k in env.text_edits())


def test_marker_through_the_timer_then_a_tap_does_not_say_refused(env, monkeypatch):
    env.rm(hook=False)

    async def during():
        env.call()
        env.result(is_error=True)
        marker(env)
    arm_timer(env, monkeypatch, during)
    q = env.rig.tap(env.separate_ids()[0], "allow")
    assert (TOAST_REFUSED_CLOSED in answers(q), env.rig.keys) == (False, [])
