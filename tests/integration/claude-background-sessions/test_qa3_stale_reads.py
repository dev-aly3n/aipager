"""QA iteration 3: a registry read never undoes a newer park or bring-back.

Contract (iteration-3 brief):
- a ``←`` seen while a monitor scan is underway is never undone by that
  scan's older registry read, in either scan order (iteration 2 pins the
  gate and the receiver; this file adds the bring-back side);
- a bring-back's end is never undone (re-parked) by a read older than it;
- a real unpark seen by a newer read still happens;
- before a held message goes out after the gate found the session not
  parked, the gate looks for a park again; parked again means the message
  stays held.

How a scan is held open (as in ``test_qa2_fresh_park_mid_scan.py``): a
second session (``backend``) parks on its own job in the same tick, so the
scan calls the bot's ``notify`` with ``bg_parked`` for it. Inside that call
the scan has read Claude's registry but has not finished.

Partitions:
- the scan order: ``backend`` before / after ``frontend`` (``other_first``,
  ``other_last``);
- what the bring-back's kill leaves of the old client's registry file:
  removed (a clean exit) / left with a dead pid (SIGKILL leaves the file) /
  left with a pid that still answers (the process is still on its way
  out): only the read's age can tell the last one apart;
- the read that sees an unpark: right after the park (a newer read) / a
  scan that saw the park mid-way, then the next scan;
- after the gate's bring-back finds the session not parked: no park again
  (control: the held message goes out) / parked again on a new job, no
  tick in between (the message stays held).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

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

OTHER_JOB = "cd34ef56"
OTHER_JS = "cd34ef56-0000-4000-8000-000000000005"
OTHER_X = "bbbbbbbb-0000-4000-8000-000000000004"
OTHER_CLIENT, OTHER_WORKER = 4500, 4600
OTHER_CHAT = -2002
JOB2 = "ef90ab12"
JS2 = "ef90ab12-0000-4000-8000-000000000007"
WORKER2 = 4201

ORDER = [pytest.param(True, id="other_first"), pytest.param(False, id="other_last")]
LEFTOVER = [pytest.param("removed", id="file_removed"),
            pytest.param("left_dead", id="file_left_pid_dead"),
            pytest.param("left_alive", id="file_left_pid_answers")]


def _other_session(stack, *, first: bool):
    """A second live session that parks on its own job at the next tick."""
    name = "claude-backend"
    if first:
        sess = stack.registry._sessions.pop(stack.ids.NAME)
        other = stack.registry.get_or_create(name)
        stack.registry._sessions[stack.ids.NAME] = sess
    else:
        other = stack.registry.get_or_create(name)
    other.label = "backend"
    other.scope_chat_id = OTHER_CHAT
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


def _kill_leaves(stack, monkeypatch, how):
    """What the bring-back's kill of the parked client leaves behind."""
    ids = stack.ids

    async def kill(name):
        stack.dtach.append(("kill", name))
        if how == "removed":
            stack.claude.unreg(ids.CLIENT)
        elif how == "left_dead":
            stack.claude.alive.discard(ids.CLIENT)
        return True
    monkeypatch.setattr(inject, "kill_session", kill)


def _mid_scan(stack, monkeypatch, action):
    """One tick; while it notifies about backend's park, run *action*."""
    inner = stack.bot.notify
    seen = {"ran": False}

    async def notify(sess, event, context=None):
        if event == "bg_parked" and sess.name == "claude-backend" and not seen["ran"]:
            seen["ran"] = True
            seen["result"] = await action()
        return await inner(sess, event, context)

    monkeypatch.setattr(stack.bot, "notify", notify)
    stack.tick()
    assert seen["ran"], "precondition: the scan notified about the other park"
    return seen.get("result")


def _bring_back_mid_scan(stack, monkeypatch, *, first, how):
    """frontend is parked (settled, idle job); a scan starts; while it is
    held open, frontend is brought back. Returns the bring-back's outcome
    and frontend's park right after it (before the scan goes on)."""
    qa.park_and_tick(stack)
    _other_session(stack, first=first)
    _kill_leaves(stack, monkeypatch, how)

    async def act():
        out = await stack.bot._bring_back_core(stack.sess)
        return out, stack.sess.parked_job
    out, parked_after = _mid_scan(stack, monkeypatch, act)
    assert (out.ok, parked_after) == (True, ""), f"precondition: brought back ({out})"


# ---- a bring-back's end is never undone by an older read -------------------------------

@pytest.mark.parametrize("how", LEFTOVER)
@pytest.mark.parametrize("first", ORDER)
def test_a_bring_back_mid_scan_is_not_re_parked_by_that_scan(stack, monkeypatch, first,
                                                              how):
    _bring_back_mid_scan(stack, monkeypatch, first=first, how=how)
    assert stack.sess.parked_job == ""


@pytest.mark.parametrize("how", LEFTOVER)
@pytest.mark.parametrize("first", ORDER)
def test_a_bring_back_mid_scan_leaves_no_link_after_that_scan(stack, monkeypatch, first,
                                                              how):
    _bring_back_mid_scan(stack, monkeypatch, first=first, how=how)
    assert qa.link(stack.ids.JOB) is None


@pytest.mark.parametrize("how", LEFTOVER)
@pytest.mark.parametrize("first", ORDER)
def test_a_bring_back_mid_scan_keeps_the_resumed_conversation(stack, monkeypatch, first,
                                                              how):
    _bring_back_mid_scan(stack, monkeypatch, first=first, how=how)
    assert stack.sess.claude_session_id == stack.ids.JS


@pytest.mark.parametrize("how", ["removed", "left_dead"])
@pytest.mark.parametrize("first", ORDER)
def test_after_that_scan_a_message_is_not_held_as_parked(stack, mk_update, monkeypatch,
                                                         first, how):
    """The relaunched claude started and ran nothing yet; the message is a
    normal prompt, not a held one (no held notice, no question)."""
    _bring_back_mid_scan(stack, monkeypatch, first=first, how=how)
    qa.relaunched_start(stack)
    u = stack.message(mk_update, "next", msg_id=45)
    texts = qa.all_texts(stack, u)
    assert (qa.count(texts, qa.HELD_NOTICE), qa.count(texts, qa.BUSY_QUESTION),
            stack.sess.parked_job) == (0, 0, "")


# ---- a real unpark seen by a newer read still happens ----------------------------------

def _terminal_brings_it_back(stack):
    """The person at the terminal brought the conversation back into the
    client: its entry no longer carries ``parkedJobId``."""
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle")


def test_an_unpark_read_right_after_a_fresh_park_ends_it(stack, mk_update):
    """The gate parks from a fresh read; the very next read sees the
    unpark."""
    qa.park(stack)
    stack.message(mk_update, "carry on", msg_id=41)
    assert stack.sess.parked_job == stack.ids.JOB, "precondition: the gate parked it"
    _terminal_brings_it_back(stack)
    stack.tick()
    assert stack.sess.parked_job == ""


def test_an_unpark_read_right_after_a_fresh_park_removes_the_link(stack, mk_update):
    qa.park(stack)
    stack.message(mk_update, "carry on", msg_id=41)
    assert qa.link(stack.ids.JOB) is not None, "precondition: linked"
    _terminal_brings_it_back(stack)
    stack.tick()
    assert qa.link(stack.ids.JOB) is None


def _park_mid_scan_then_unpark(stack, mk_update, monkeypatch, first):
    _other_session(stack, first=first)

    async def act():
        qa.park(stack)
        update = mk_update("carry on", message_id=41, user_id=12345, chat_id=stack.ids.CHAT)
        ctx = MagicMock()
        ctx.bot = MagicMock(id=87654321)
        await stack.bot._handle_message(update, ctx)
    _mid_scan(stack, monkeypatch, act)
    assert stack.sess.parked_job == stack.ids.JOB, "precondition: the park survived the scan"
    _terminal_brings_it_back(stack)
    stack.tick()


@pytest.mark.parametrize("first", ORDER)
def test_a_park_kept_through_a_scan_is_ended_by_the_next_scan(stack, mk_update,
                                                              monkeypatch, first):
    _park_mid_scan_then_unpark(stack, mk_update, monkeypatch, first)
    assert stack.sess.parked_job == ""


@pytest.mark.parametrize("first", ORDER)
def test_a_park_kept_through_a_scan_loses_its_link_at_the_next_scan(stack, mk_update,
                                                                    monkeypatch, first):
    _park_mid_scan_then_unpark(stack, mk_update, monkeypatch, first)
    assert qa.link(stack.ids.JOB) is None


def test_an_unpark_after_a_wall_clock_step_back_still_ends_the_park(stack):
    """Error guessing: the wall clock stepped back 30 s after the park
    (an NTP correction). A newer read that shows the unpark still ends it;
    the park must not stick until the clock catches up."""
    qa.park_and_tick(stack)
    stack.sess.parked_since = time.time() + 30
    _terminal_brings_it_back(stack)
    stack.tick()
    assert stack.sess.parked_job == ""


# ---- the gate looks again before a held message goes out ------------------------------

def _held_then_unparked(stack, mk_update):
    """A held message whose bring-back has not run yet; then the terminal
    brings the conversation back and a tick ends the park."""
    qa.park_and_tick(stack)
    stack.message(mk_update, "carry on", msg_id=41)
    assert stack.spawned, "precondition: the gate started a bring-back"
    _terminal_brings_it_back(stack)
    stack.tick()
    assert stack.sess.parked_job == "", "precondition: the park ended"


def _parked_again(stack):
    """``←`` again on the client, on a new job; no tick has seen it."""
    ids = stack.ids
    stack.claude.reg(ids.CLIENT, kind="interactive", sessionId=ids.X, status="idle",
                     parkedJobId=JOB2)
    stack.claude.reg(WORKER2, kind="bg", jobId=JOB2, sessionId=JS2, status="idle")
    stack.claude.stop_ends[JOB2] = WORKER2
    stack.claude.append(ids.X, {"type": "continued-in", "continuedInSessionId": JS2})
    stack.claude.transcript(JS2, {"type": "user"}, {"type": "assistant"})


def _bodies(stack):
    return [t.rsplit("\n", 1)[-1] for t in stack.typed]


def test_control_not_parked_again_the_held_message_goes_out(stack, mk_update):
    _held_then_unparked(stack, mk_update)
    stack.run_spawned()
    assert _bodies(stack) == ["carry on"]


def test_parked_again_the_held_message_is_not_typed(stack, mk_update):
    _held_then_unparked(stack, mk_update)
    _parked_again(stack)
    stack.run_spawned()
    assert stack.typed == []


def test_parked_again_the_held_message_stays_held(stack, mk_update):
    _held_then_unparked(stack, mk_update)
    _parked_again(stack)
    stack.run_spawned()
    assert [q[0] for q in stack.sess.pending_queue] == ["carry on"]


def test_parked_again_the_new_park_is_seen(stack, mk_update):
    _held_then_unparked(stack, mk_update)
    _parked_again(stack)
    stack.run_spawned()
    # Parked on the new job, or brought back from it (stopped first).
    assert stack.sess.parked_job == JOB2 or qa.stops(stack) == [["stop", JOB2]]
