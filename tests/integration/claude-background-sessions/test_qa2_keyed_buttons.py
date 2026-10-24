"""QA iteration 2: the bring-back buttons are keyed to the job they were
shown for.

Contract (iteration-2 brief): the "Bring it back now" and "Stop and bring
back" buttons carry the job id (``bgback<job8>``, ``bgforce<job8>``); a
button from an earlier park answers "x is not in Claude Code's
background." and does nothing. design.md: ``bg.not_parked`` (toast)
"{session} is not in Claude Code's background."

Partitions for the button's key against the session now:
- the current park's job (control: it acts);
- an earlier park's job, while parked on another job (the stale case);
- any job, while the session is no longer parked;
- malformed: 7 characters, 9 characters, upper case, no key at all on a
  destructive ``bgforce``.
Buttons: ``bgback`` (idle job) and ``bgforce`` (busy job).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity


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
NOT_PARKED = "frontend is not in Claude Code's background."


def _second_park(stack, *, job_status="idle"):
    """The terminal user brought the conversation back and pressed ``←``
    again: the client now parks on another job."""
    qa.park_and_tick(stack, job=JOB2, js=JS2, worker=WORKER2, job_status=job_status)
    assert stack.sess.parked_job == JOB2, "precondition: parked on the second job"


def _toasts(q) -> list[str]:
    out = []
    for c in q.answer.await_args_list:
        text = c.args[0] if c.args else c.kwargs.get("text")
        if text:
            out.append(text)
    return out


def _edited(q) -> list[str]:
    return [c.args[0] if c.args else c.kwargs.get("text", "")
            for c in q.edit_message_text.await_args_list]


def _raw_tap(stack, action):
    """A tap with exactly *action* (``Stack.tap`` adds the current job to a
    bare verb and insists the tap is handled)."""
    q = MagicMock()
    q.from_user = MagicMock(id=12345)
    q.message = MagicMock(chat=MagicMock(id=stack.ids.CHAT), chat_id=stack.ids.CHAT,
                          message_id=77)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock(callback_query=q, effective_chat=MagicMock(id=stack.ids.CHAT),
                  effective_user=MagicMock(id=12345))
    stack.run(session_parity.handle_callback(stack.bot, u, q, stack.ids.NAME, action))
    return q


def _untouched(stack):
    return (qa.stops(stack), stack.kinds()) == ([], [])


VERBS = [pytest.param("bgback", "idle", id="bgback"),
         pytest.param("bgforce", "busy", id="bgforce")]


# ---- control: the current park's button acts ----------------------------------------

@pytest.mark.parametrize("verb, job_status", VERBS)
def test_the_current_parks_button_acts(stack, verb, job_status):
    qa.park_and_tick(stack, job_status=job_status if verb == "bgforce" else "idle")
    stack.tap(verb + stack.ids.JOB)
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]


def test_the_second_parks_notice_carries_its_own_job(stack):
    qa.park_and_tick(stack)
    _second_park(stack)
    calls = qa.calls_with_text(stack, "moved to Claude Code's background")
    assert qa.buttons(calls[-1]) == ["bgback" + JOB2]


def test_the_second_parks_question_carries_its_own_job(stack, mk_update):
    qa.park_and_tick(stack)
    _second_park(stack, job_status="busy")
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    calls = qa.calls_with_text(stack, "is still working in Claude Code's background")
    assert [qa.buttons(c) for c in calls] == [["bgforce" + JOB2, "bgleave"]]


# ---- an earlier park's button --------------------------------------------------------

@pytest.mark.parametrize("verb, job_status", VERBS)
def test_an_earlier_parks_button_does_nothing(stack, verb, job_status):
    qa.park_and_tick(stack)
    _second_park(stack, job_status=job_status)
    stack.tap(verb + stack.ids.JOB)
    assert _untouched(stack)


@pytest.mark.parametrize("verb, job_status", VERBS)
def test_an_earlier_parks_button_keeps_the_new_park(stack, verb, job_status):
    qa.park_and_tick(stack)
    _second_park(stack, job_status=job_status)
    stack.tap(verb + stack.ids.JOB)
    assert stack.sess.parked_job == JOB2


@pytest.mark.parametrize("verb, job_status", VERBS)
def test_an_earlier_parks_button_says_not_in_the_background(stack, verb, job_status):
    qa.park_and_tick(stack)
    _second_park(stack, job_status=job_status)
    q = stack.tap(verb + stack.ids.JOB)
    assert NOT_PARKED in _toasts(q) + _edited(q)


# ---- the session is no longer parked -------------------------------------------------

def _unparked(stack):
    qa.park_and_tick(stack)
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle")
    stack.tick()
    assert not stack.sess.parked_job, "precondition: the park ended"


@pytest.mark.parametrize("verb, job_status", VERBS)
def test_a_button_after_the_park_ended_does_nothing(stack, verb, job_status):
    _unparked(stack)
    qa.job_status(stack, job_status)
    stack.tap(verb + stack.ids.JOB)
    assert _untouched(stack)


@pytest.mark.parametrize("verb, job_status", VERBS)
def test_a_button_after_the_park_ended_says_not_in_the_background(stack, verb, job_status):
    _unparked(stack)
    q = stack.tap(verb + stack.ids.JOB)
    assert NOT_PARKED in _toasts(q) + _edited(q)


# ---- malformed keys ------------------------------------------------------------------

MALFORMED = [
    pytest.param(lambda j: j[:7], id="seven_chars"),
    pytest.param(lambda j: j + "9", id="nine_chars"),
    pytest.param(lambda j: j.upper(), id="upper_case"),
]


@pytest.mark.parametrize("key", MALFORMED)
@pytest.mark.parametrize("verb, job_status", VERBS)
def test_a_malformed_key_does_nothing(stack, verb, job_status, key):
    qa.park_and_tick(stack, job_status=job_status)
    _raw_tap(stack, verb + key(stack.ids.JOB))
    assert _untouched(stack)


def test_an_unkeyed_stop_and_bring_back_never_force_stops_a_parked_job(stack):
    """A ``bgforce`` without a job (from before the buttons were keyed)
    must not stop whatever job the session is parked on now."""
    qa.park_and_tick(stack, job_status="busy")
    _raw_tap(stack, "bgforce")
    assert qa.stops(stack) == []
