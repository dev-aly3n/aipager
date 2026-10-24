"""QA iteration 2: a fresh ``←`` is seen while the monitor is mid-scan.

Contract (iteration-2 brief, design.md "Risks: race between ← and
detection"): the message gate and the receiver's link check see a fresh
``←`` even while the monitor is in the middle of a scan. Success
criteria 3 and 4: parked "on the job's first datagram", and no byte is
written to a parked session's dtach.

How a scan is held open: a second session (``backend``) parks on its own
job in the same tick, so the scan calls the bot's ``notify`` with
``bg_parked`` for it. Inside that call (the scan has not finished), the
``←`` of ``frontend`` lands in Claude's registry and a Telegram message or
the job's first datagram arrives.

Partitions:
- what arrives mid-scan: a Telegram message / the job's first linked
  datagram;
- where the other session sits in the scan: before or after ``frontend``
  (stable ids ``other_first`` / ``other_last``, by registry insertion);
- control: the same arrival outside a scan;
- right after a bring-back (the relaunched claude parks again on a new
  job while the bring-back's restart quiet window still runs).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


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

OTHER_JOB = "cd34ef56"
OTHER_JS = "cd34ef56-0000-4000-8000-000000000005"
OTHER_X = "bbbbbbbb-0000-4000-8000-000000000004"
OTHER_CLIENT, OTHER_WORKER = 4500, 4600
OTHER_CHAT = -2002


def _other_session(stack, *, first: bool):
    """A second live session that parks on its own job at the next tick.
    With ``first`` it is put ahead of ``frontend`` in the registry."""
    name = "claude-backend"
    if first:
        # Re-create frontend after the other one, keeping its object.
        sess = stack.registry._sessions.pop(stack.ids.NAME)
        other = stack.registry.get_or_create(name)
        stack.registry._sessions[stack.ids.NAME] = sess
    else:
        other = stack.registry.get_or_create(name)
    other.label = "backend"
    other.scope_chat_id = OTHER_CHAT          # another chat: frontend's stays unambiguous
    other.cwd = str(stack.cc.work)
    other.claude_session_id = OTHER_X
    other.transcript_path = str(stack.claude.transcript(OTHER_X, {"type": "user"}))
    stack.live.add(name)
    stack.claude.reg(OTHER_CLIENT, kind="interactive", sessionId=OTHER_X, status="idle",
                     parkedJobId=OTHER_JOB)
    stack.claude.reg(OTHER_WORKER, kind="bg", jobId=OTHER_JOB, sessionId=OTHER_JS,
                     status="idle")
    stack.claude.append(OTHER_X, {"type": "continued-in", "continuedInSessionId": OTHER_JS})
    stack.claude.transcript(OTHER_JS, {"type": "user"})
    return other


def _mid_scan(stack, monkeypatch, action):
    """Run one tick; while it notifies the bot about the other session's
    park, put frontend's ``←`` in Claude's registry and run *action*."""
    inner = stack.bot.notify
    seen = {"ran": False}

    async def notify(sess, event, context=None):
        if event == "bg_parked" and sess.name == "claude-backend" and not seen["ran"]:
            seen["ran"] = True
            qa.park(stack)
            seen["result"] = await action()
        return await inner(sess, event, context)

    monkeypatch.setattr(stack.bot, "notify", notify)
    stack.tick()
    assert seen["ran"], "precondition: the scan notified about the other park"
    return seen.get("result")


def _message(stack, mk_update, text="carry on", msg_id=41):
    async def act():
        update = mk_update(text, message_id=msg_id, user_id=12345, chat_id=stack.ids.CHAT)
        ctx = MagicMock()
        ctx.bot = MagicMock(id=87654321)
        await stack.bot._handle_message(update, ctx)
        return list(stack.typed)
    return act


def _tp(stack, sid):
    return str(stack.claude.projects / f"{sid}.jsonl")


def _first_datagram(stack):
    async def act():
        await stack.receiver._on_datagram(json.dumps({
            "session": stack.ids.NAME, "hook_event_name": "UserPromptSubmit",
            "bg_job": stack.ids.JOB, "session_id": stack.ids.JS, "prompt": "go on",
            "transcript_path": _tp(stack, stack.ids.JS), "n": 7001}).encode())
        return (stack.sess.parked_job, stack.sess.claude_session_id)
    return act


ORDER = [pytest.param(True, id="other_first"), pytest.param(False, id="other_last")]


@pytest.mark.parametrize("first", ORDER)
def test_a_message_mid_scan_is_never_typed(stack, mk_update, monkeypatch, first):
    _other_session(stack, first=first)
    typed = _mid_scan(stack, monkeypatch, _message(stack, mk_update))
    assert typed == []


@pytest.mark.parametrize("first", ORDER)
def test_a_message_mid_scan_is_held(stack, mk_update, monkeypatch, first):
    _other_session(stack, first=first)
    _mid_scan(stack, monkeypatch, _message(stack, mk_update))
    assert [q[0] for q in stack.sess.pending_queue] == ["carry on"]


# qa finding tester-iter2-001 (fixed): a scan that reaches frontend after the
# other session must not judge it from the registry read at the start of the
# tick, which is older than the park the gate (or the receiver) just saw.
ORDER_STALE = [pytest.param(True, id="other_first"), pytest.param(False, id="other_last")]


@pytest.mark.parametrize("first", ORDER_STALE)
def test_a_park_seen_mid_scan_is_not_undone_by_that_scan(stack, mk_update, monkeypatch,
                                                         first):
    _other_session(stack, first=first)
    _mid_scan(stack, monkeypatch, _message(stack, mk_update))
    assert stack.sess.parked_job == stack.ids.JOB


@pytest.mark.parametrize("first", ORDER_STALE)
def test_the_link_of_a_park_seen_mid_scan_outlives_that_scan(stack, monkeypatch, first):
    _other_session(stack, first=first)
    _mid_scan(stack, monkeypatch, _first_datagram(stack))
    assert (qa.link(stack.ids.JOB) or {}).get("session") == stack.ids.NAME


@pytest.mark.parametrize("first", ORDER_STALE)
def test_the_message_held_mid_scan_is_never_typed_into_the_parked_client(
        stack, mk_update, monkeypatch, first):
    """After the scan, the bring-back the gate started runs. While Claude's
    registry still says the client is parked, nothing may be typed."""
    _other_session(stack, first=first)
    _mid_scan(stack, monkeypatch, _message(stack, mk_update))
    stack.run_spawned()
    assert stack.typed == []


@pytest.mark.parametrize("first", ORDER)
def test_a_second_message_after_that_scan_is_held_too(stack, mk_update, monkeypatch, first):
    _other_session(stack, first=first)
    _mid_scan(stack, monkeypatch, _message(stack, mk_update))
    stack.message(mk_update, "and this", msg_id=42)
    assert stack.typed == []


@pytest.mark.parametrize("first", ORDER)
def test_the_jobs_first_datagram_mid_scan_is_linked(stack, monkeypatch, first):
    _other_session(stack, first=first)
    parked, _sid = _mid_scan(stack, monkeypatch, _first_datagram(stack))
    assert parked == stack.ids.JOB


@pytest.mark.parametrize("first", ORDER)
def test_the_jobs_first_datagram_mid_scan_is_not_dropped(stack, monkeypatch, first):
    """A linked datagram adopts the job's conversation (D4); a dropped one
    changes nothing."""
    _other_session(stack, first=first)
    _parked, sid = _mid_scan(stack, monkeypatch, _first_datagram(stack))
    assert sid == stack.ids.JS


def test_control_a_message_outside_a_scan_is_held(stack, mk_update):
    qa.park(stack)
    stack.message(mk_update, "carry on", msg_id=41)
    assert (stack.typed, [q[0] for q in stack.sess.pending_queue]) == ([], ["carry on"])


def test_control_the_first_datagram_outside_a_scan_is_linked(stack):
    qa.park(stack)
    stack.hook(hook_event_name="UserPromptSubmit", bg_job=stack.ids.JOB,
               session_id=stack.ids.JS, prompt="go on",
               transcript_path=_tp(stack, stack.ids.JS))
    assert (stack.sess.parked_job, stack.sess.claude_session_id) == (
        stack.ids.JOB, stack.ids.JS)




# ---- a fresh park right after a bring-back -------------------------------------------

JOB2 = "ef90ab12"
JS2 = "ef90ab12-0000-4000-8000-000000000007"
WORKER2 = 4201

QUIET_WINDOW = (
    "qa finding tester-iter2-002: for about 10 s after a bring-back (its restart "
    "quiet window) the parked check answers False, so a fresh park of the "
    "relaunched claude is not seen by the message gate and the message is typed "
    "into the parked client (S1, Fact 9)")


def _brought_back_then_parked_again(stack, mk_update):
    qa.park_and_tick(stack)
    stack.message(mk_update, "one", msg_id=41)
    stack.run_spawned()
    qa.relaunched_start(stack)
    qa.own_turns(stack, 1)
    assert [t.rsplit("\n", 1)[-1] for t in stack.typed] == ["one"], "precondition"
    stack.claude.reg(stack.ids.RELAUNCHED, kind="interactive", sessionId=stack.ids.JS,
                     status="idle", parkedJobId=JOB2)
    stack.claude.reg(WORKER2, kind="bg", jobId=JOB2, sessionId=JS2, status="idle")


@pytest.mark.xfail(strict=True, reason=QUIET_WINDOW)
def test_a_fresh_park_right_after_a_bring_back_is_seen_by_the_gate(stack, mk_update):
    _brought_back_then_parked_again(stack, mk_update)
    stack.message(mk_update, "two", msg_id=42)
    assert [t.rsplit("\n", 1)[-1] for t in stack.typed] == ["one"]


@pytest.mark.xfail(strict=True, reason=QUIET_WINDOW)
def test_a_fresh_park_right_after_a_bring_back_is_noticed_by_the_tick(stack, mk_update):
    _brought_back_then_parked_again(stack, mk_update)
    stack.tick()
    assert stack.sess.parked_job == JOB2


def test_control_a_fresh_park_after_the_quiet_window_is_seen_by_the_gate(stack, mk_update):
    _brought_back_then_parked_again(stack, mk_update)
    stack.sess.restarting_until = 0               # the window is over
    stack.message(mk_update, "two", msg_id=42)
    assert [t.rsplit("\n", 1)[-1] for t in stack.typed] == ["one"]
