"""QA iteration 3: a keyed "Stop and bring back" on a session that ended.

Contract (iteration-3 brief):
- a keyed ``bgforce<job8>`` on a session that has ended force-stops the
  conversation's holder only when the holder's job id starts with that key;
- another holder is handled unforced: a busy one asks, an idle one is
  stopped and the session resumed;
- a bare ``bgforce`` behaves as before (an ended session's resume, forced).

The session parked on ``ab12cd34``, its busy question was shown, then the
parked client died and the session ended (its dtach socket went away).
The conversation is still held by a background job: the button's own job,
or another one (the conversation moved on).

Partitions for the holder's job id against the key ``ab12cd34``:
- equal (``ab12cd34``);
- longer, starting with the key (``ab12cd34ff``, boundary: "starts with");
- sharing a shorter prefix (``ab12cd99``, 6 characters; ``ab12cd3x``,
  7 characters: just inside the key but not all of it);
- unrelated (``ef90ab12``).
Crossed with the holder busy / idle, and the bare verb as a control.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
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

KEY = "ab12cd34"
SAME = [pytest.param("ab12cd34", id="equal"),
        pytest.param("ab12cd34ff", id="longer_starts_with_key")]
OTHER = [pytest.param("ab12cd99", id="shares_6_chars"),
         pytest.param("ab12cd3x", id="shares_7_chars"),
         pytest.param("ef90ab12", id="unrelated")]


def _ended(stack, *, holder_job, status):
    """Parked on KEY (busy, so the question was shown), then ended; the
    conversation ``JS`` is held by *holder_job*."""
    ids = stack.ids
    qa.park_and_tick(stack, job_status="busy")
    assert stack.ids.JOB == KEY
    stack.claude.reg(ids.WORKER, kind="bg", jobId=holder_job, sessionId=ids.JS,
                     status=status)
    stack.claude.stop_ends[holder_job] = ids.WORKER
    stack.claude.unreg(ids.CLIENT)
    qa.vanish(stack)
    assert stack.sess.status == Status.GONE, "precondition: ended"


def _tap(stack, action):
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
    stack.run_spawned()
    return q


def _shown(stack, q) -> list[str]:
    edited = [c.args[0] if c.args else c.kwargs.get("text", "")
              for c in q.edit_message_text.await_args_list]
    answered = [c.args[0] if c.args else c.kwargs.get("text", "")
                for c in q.answer.await_args_list]
    return qa.sent_texts(stack) + edited + [a or "" for a in answered]


def _resumed(stack):
    return [lc.get("resume_id") for lc in stack.launches()]


# ---- the holder is the key's job --------------------------------------------------------

@pytest.mark.parametrize("job", SAME)
def test_the_keys_busy_job_is_force_stopped(stack, job):
    _ended(stack, holder_job=job, status="busy")
    _tap(stack, "bgforce" + KEY)
    assert qa.stops(stack) == [["stop", job]]


@pytest.mark.parametrize("job", SAME)
def test_the_keys_busy_job_is_stopped_then_the_session_resumed(stack, job):
    _ended(stack, holder_job=job, status="busy")
    _tap(stack, "bgforce" + KEY)
    assert _resumed(stack) == [stack.ids.JS]


# ---- another holder, busy: asks ---------------------------------------------------------

@pytest.mark.parametrize("job", OTHER)
def test_another_busy_holder_is_not_stopped(stack, job):
    _ended(stack, holder_job=job, status="busy")
    _tap(stack, "bgforce" + KEY)
    assert qa.stops(stack) == []


@pytest.mark.parametrize("job", OTHER)
def test_another_busy_holder_launches_nothing(stack, job):
    _ended(stack, holder_job=job, status="busy")
    _tap(stack, "bgforce" + KEY)
    assert stack.launches() == []


@pytest.mark.parametrize("job", OTHER)
def test_another_busy_holder_asks(stack, job):
    _ended(stack, holder_job=job, status="busy")
    q = _tap(stack, "bgforce" + KEY)
    assert any("still working in Claude Code's background" in t for t in _shown(stack, q))


# ---- another holder, idle: stopped and resumed ------------------------------------------

@pytest.mark.parametrize("job", OTHER)
def test_another_idle_holder_is_stopped(stack, job):
    _ended(stack, holder_job=job, status="idle")
    _tap(stack, "bgforce" + KEY)
    assert qa.stops(stack) == [["stop", job]]


@pytest.mark.parametrize("job", OTHER)
def test_another_idle_holder_then_the_session_is_resumed(stack, job):
    _ended(stack, holder_job=job, status="idle")
    _tap(stack, "bgforce" + KEY)
    assert _resumed(stack) == [stack.ids.JS]


# ---- the bare verb: as before -----------------------------------------------------------

@pytest.mark.parametrize("job", [pytest.param("ab12cd34", id="the_parks_job"),
                                 pytest.param("ef90ab12", id="another_job")])
def test_a_bare_bgforce_on_an_ended_session_force_stops_the_holder(stack, job):
    _ended(stack, holder_job=job, status="busy")
    _tap(stack, "bgforce")
    assert qa.stops(stack) == [["stop", job]]


def test_a_bare_bgforce_on_an_ended_session_resumes_it(stack):
    _ended(stack, holder_job="ef90ab12", status="busy")
    _tap(stack, "bgforce")
    assert _resumed(stack) == [stack.ids.JS]


# ---- control: a keyed bgback on an ended session never forces ---------------------------

@pytest.mark.parametrize("job", SAME)
def test_a_keyed_bring_it_back_on_an_ended_session_asks_for_a_busy_holder(stack, job):
    _ended(stack, holder_job=job, status="busy")
    _tap(stack, "bgback" + KEY)
    assert (qa.stops(stack), stack.launches()) == ([], [])
