"""The turn files Claude sends may come from (``TrackedSession.files_turn_*``)
and the call ids already copied (``files_sent_ids``): opened only by prompt
evidence, never by a tool call's hook, closed when the turn and its job
end, persisted (review 2026-10-18, rev-iter2-001)."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

from aipager import state as state_mod
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status, TrackedSession

S = "claude-frontend"


def test_a_prompt_opens_the_turn_once_and_keeps_its_start():
    sess = TrackedSession(name=S, label="frontend", status=Status.IDLE)
    sess.transcript_path = "/t/a.jsonl"
    assert sess.files_turn_opened(100.0) is True
    assert (sess.files_turn_open, sess.files_turn_wall, sess.files_transcript) == (
        True, 100.0, "/t/a.jsonl")
    # A message absorbed by the running turn, or a job's continuation:
    # the start and the transcript stay.
    sess.transcript_path = "/t/b.jsonl"
    assert sess.files_turn_opened(200.0) is False
    assert (sess.files_turn_wall, sess.files_transcript) == (100.0, "/t/a.jsonl")


def test_the_first_transcript_learned_is_taken_when_none_was_known():
    sess = TrackedSession(name=S, label="frontend", status=Status.IDLE)
    sess.files_turn_opened(100.0)
    assert sess.files_transcript == ""
    sess.transcript_path = "/t/a.jsonl"
    sess.files_turn_opened(150.0)
    assert (sess.files_turn_wall, sess.files_transcript) == (100.0, "/t/a.jsonl")


def test_going_idle_closes_the_turn_unless_a_job_runs(tmp_state_file):
    reg = SessionRegistry()
    reg.transition(S, Status.BUSY)
    sess = reg.get(S)
    sess.files_turn_opened(time.time())
    sess.active_subagents["a1"] = {"started_at": time.monotonic()}
    reg.transition(S, Status.IDLE)            # an interim Stop
    assert sess.files_turn_open
    sess.active_subagents.clear()
    reg.transition(S, Status.BUSY)
    reg.transition(S, Status.IDLE)            # the job's last Stop
    assert not sess.files_turn_open
    sess.files_turn_opened(time.time())
    reg.transition(S, Status.GONE)
    assert not sess.files_turn_open


def test_a_call_id_is_used_once_and_the_list_is_bounded(monkeypatch):
    sess = TrackedSession(name=S, label="frontend", status=Status.BUSY)
    assert sess.files_call_used("toolu_1") is True
    assert sess.files_call_used("toolu_1") is False
    assert sess.files_call_used("") is False
    assert sess.files_call_used(None) is False
    monkeypatch.setattr(state_mod, "MAX_FILES_SENT_IDS", 3)
    for i in range(2, 6):
        assert sess.files_call_used(f"toolu_{i}")
    assert sess.files_sent_ids == ["toolu_3", "toolu_4", "toolu_5"]


def test_the_turn_and_the_used_ids_survive_a_restart(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition(S, Status.BUSY)
    sess = r1.get(S)
    sess.transcript_path = "/t/a.jsonl"
    wall = time.time() - 5
    sess.files_turn_opened(wall)
    sess.files_call_used("toolu_1")
    r1.save()
    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get(S)
    assert (s2.files_turn_open, s2.files_turn_wall, s2.files_transcript,
            s2.files_sent_ids) == (True, wall, "/t/a.jsonl", ["toolu_1"])
    assert s2.files_call_used("toolu_1") is False


def test_unusable_saved_values_restore_as_no_turn(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition(S, Status.BUSY)
    r1.save()
    data = json.loads(tmp_state_file.read_text())
    entry = data["sessions"][S]
    entry.update(files_turn_open=True, files_turn_wall="soon",
                 files_transcript=7, files_sent_ids=["ok", 3, "", None])
    tmp_state_file.write_text(json.dumps(data))
    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get(S)
    assert (s2.files_turn_open, s2.files_turn_wall, s2.files_transcript,
            s2.files_sent_ids) == (False, 0.0, "", ["ok"])
    # A start in the future is not one either.
    entry.update(files_turn_open=True, files_turn_wall=time.time() + 3600)
    tmp_state_file.write_text(json.dumps(data))
    r3 = SessionRegistry()
    r3.load()
    assert r3.get(S).files_turn_open is False
    # Only a real true opens it, even with a usable start.
    entry.update(files_turn_open="yes", files_turn_wall=time.time() - 5)
    tmp_state_file.write_text(json.dumps(data))
    r4 = SessionRegistry()
    r4.load()
    assert r4.get(S).files_turn_open is False


# ---- what opens it: prompt evidence only ------------------------------------------

def _receiver():
    calls: list = []

    async def notify_fn(sess, kind, payload):
        calls.append(kind)
    reg = SessionRegistry()
    return hr.HookReceiver(reg, notify_fn), reg


def _dg(recv, **kw):
    base = {"session": S, "session_id": "sid", "cwd": "/w",
            "transcript_path": "/t/a.jsonl"}
    asyncio.run(recv._on_datagram(json.dumps({**base, **kw}).encode()))


def test_a_user_prompt_submit_opens_the_turn(tmp_state_file):
    recv, reg = _receiver()
    reg.transition(S, Status.IDLE)
    before = time.time()
    _dg(recv, hook_event_name="UserPromptSubmit", prompt="send me the chart")
    sess = reg.get(S)
    assert sess.files_turn_open
    assert sess.files_turn_wall >= before
    assert sess.files_transcript == "/t/a.jsonl"


def test_a_tool_call_alone_never_opens_the_turn(tmp_state_file):
    """A PreToolUse for an idle session moves it to BUSY by itself (a lost
    UserPromptSubmit), so a forged one would too: it opens nothing."""
    recv, reg = _receiver()
    reg.transition(S, Status.IDLE)
    _dg(recv, hook_event_name="PreToolUse", tool_name="SendUserFile",
        tool_use_id="toolu_x", tool_input={"files": ["/w/a.png"]})
    sess = reg.get(S)
    assert sess.status is Status.BUSY
    assert not sess.files_turn_open


def test_a_subagents_prompt_never_opens_the_turn(tmp_state_file):
    recv, reg = _receiver()
    reg.transition(S, Status.IDLE)
    _dg(recv, hook_event_name="UserPromptSubmit", prompt="x", agent_id="a1")
    assert not reg.get(S).files_turn_open


def test_a_typed_prompt_opens_the_turn(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name=S, label="frontend", status=Status.IDLE)
    bot.registry._sessions[S] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    before = time.time()
    run_async(bot._inject_prompt(sess, "send me the chart"))
    assert sess.files_turn_open and sess.files_turn_wall >= before


def test_a_prompt_that_was_not_typed_opens_nothing(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name=S, label="frontend", status=Status.IDLE)
    bot.registry._sessions[S] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    run_async(bot._inject_prompt(sess, "send me the chart"))
    assert not sess.files_turn_open


def test_a_later_prompt_of_the_turn_names_the_transcript_again(tmp_state_file):
    """A turn opened before Claude's transcript was the one it writes (a
    /clear typed from Telegram): the next prompt's transcript is the one
    looked in, and the turn's start stays."""
    recv, reg = _receiver()
    reg.transition(S, Status.IDLE)
    _dg(recv, hook_event_name="UserPromptSubmit", prompt="one")
    sess = reg.get(S)
    wall = sess.files_turn_wall
    _dg(recv, hook_event_name="UserPromptSubmit", prompt="two",
        transcript_path="/t/b.jsonl")
    assert (sess.files_turn_wall, sess.files_transcript) == (wall, "/t/b.jsonl")


def test_a_new_conversation_closes_the_turn(tmp_state_file):
    recv, reg = _receiver()
    reg.transition(S, Status.IDLE)
    _dg(recv, hook_event_name="UserPromptSubmit", prompt="/clear")
    sess = reg.get(S)
    _dg(recv, hook_event_name="SessionStart", source="compact")
    assert sess.files_turn_open
    _dg(recv, hook_event_name="SessionStart", source="clear",
        transcript_path="/t/new.jsonl")
    assert not sess.files_turn_open


# ---- a turn Claude Code starts with no prompt hook (review rev-iter3-001) -------

def test_going_idle_keeps_when_the_turn_ended(tmp_state_file):
    reg = SessionRegistry()
    reg.transition(S, Status.BUSY)
    sess = reg.get(S)
    sess.transcript_path = "/t/a.jsonl"
    sess.files_turn_opened(time.time())
    before = time.time()
    reg.transition(S, Status.IDLE)
    assert not sess.files_turn_open
    assert before <= sess.files_turn_closed_wall <= time.time()
    # Gone: no later turn of that conversation can resume it.
    reg.transition(S, Status.GONE)
    assert sess.files_turn_closed_wall == 0.0


def test_a_new_conversation_forgets_when_the_last_turn_ended(tmp_state_file):
    recv, reg = _receiver()
    reg.transition(S, Status.IDLE)
    _dg(recv, hook_event_name="UserPromptSubmit", prompt="one")
    _dg(recv, hook_event_name="Stop")
    sess = reg.get(S)
    assert sess.files_turn_closed_wall > 0
    _dg(recv, hook_event_name="SessionStart", source="clear",
        transcript_path="/t/new.jsonl")
    assert sess.files_turn_closed_wall == 0.0


def test_a_turn_resumes_in_the_transcript_noted_for_it():
    sess = TrackedSession(name=S, label="frontend", status=Status.BUSY)
    sess.transcript_path = "/t/a.jsonl"
    sess.files_turn_opened(100.0)
    sess.files_turn_closed(150.0)
    # A datagram named another transcript since: the noted one stays.
    sess.transcript_path = "/t/other.jsonl"
    assert sess.files_turn_resumed(150.0) is True
    assert (sess.files_turn_open, sess.files_turn_wall, sess.files_transcript) == (
        True, 150.0, "/t/a.jsonl")
    # Already open: nothing moves.
    assert sess.files_turn_resumed(170.0) is False
    assert sess.files_turn_wall == 150.0


def test_a_turn_with_no_transcript_or_start_does_not_resume():
    sess = TrackedSession(name=S, label="frontend", status=Status.BUSY)
    assert sess.files_turn_resumed(150.0) is False
    sess.files_transcript = "/t/a.jsonl"
    assert sess.files_turn_resumed(0.0) is False
    assert not sess.files_turn_open


def test_a_stop_on_an_idle_session_moves_the_last_end(tmp_state_file):
    """A turn Claude Code ran with no hook and no tool call ends with a Stop
    on an already idle session: that end is the one a later start must
    follow."""
    recv, reg = _receiver()
    reg.transition(S, Status.IDLE)
    _dg(recv, hook_event_name="UserPromptSubmit", prompt="one")
    _dg(recv, hook_event_name="Stop")
    sess = reg.get(S)
    first = sess.files_turn_closed_wall
    time.sleep(0.01)
    # (Another answer: an identical datagram is dropped as a duplicate.)
    _dg(recv, hook_event_name="Stop", last_assistant_message="again")
    assert sess.files_turn_closed_wall > first


def test_a_stop_while_the_turn_is_open_or_never_ended_moves_nothing():
    sess = TrackedSession(name=S, label="frontend", status=Status.IDLE)
    sess.files_turn_stopped(500.0)          # no turn of it ended yet
    assert sess.files_turn_closed_wall == 0.0
    sess.files_turn_closed(100.0)
    sess.files_transcript = "/t/a.jsonl"
    sess.files_turn_resumed(100.0)
    sess.files_turn_stopped(500.0)          # an interim Stop of a job
    assert sess.files_turn_closed_wall == 100.0


def test_when_the_turn_ended_survives_a_restart(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition(S, Status.BUSY)
    sess = r1.get(S)
    sess.files_turn_opened(time.time() - 9)
    r1.transition(S, Status.IDLE)
    ended = sess.files_turn_closed_wall
    r1.save()
    r2 = SessionRegistry()
    r2.load()
    assert r2.get(S).files_turn_closed_wall == ended
    data = json.loads(tmp_state_file.read_text())
    for bad in ("soon", time.time() + 3600, -1, None):
        data["sessions"][S]["files_turn_closed_wall"] = bad
        tmp_state_file.write_text(json.dumps(data))
        r3 = SessionRegistry()
        r3.load()
        assert r3.get(S).files_turn_closed_wall == 0.0, bad


def test_a_queued_message_the_daemon_typed_resumes_the_turn(mk_bot):
    """Claude Code pops a message the daemon typed while the last turn ran
    as its next prompt, with no hook (_take_next_prompt)."""
    bot = mk_bot()
    sess = TrackedSession(name=S, label="frontend", status=Status.BUSY)
    bot.registry._sessions[S] = sess
    sess.transcript_path = "/t/a.jsonl"
    sess.files_turn_opened(time.time() - 30)
    bot.registry.transition(S, Status.IDLE)
    assert not sess.files_turn_open
    sess.queued_targets.append({"msg_id": 77, "chat_id": 1, "raw_text": "also"})
    before = time.time()
    assert bot._take_next_prompt(sess) is not None
    assert sess.files_turn_open and sess.files_turn_wall >= before
    assert sess.files_transcript == "/t/a.jsonl"


def test_no_queued_message_resumes_nothing(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name=S, label="frontend", status=Status.BUSY)
    bot.registry._sessions[S] = sess
    sess.transcript_path = "/t/a.jsonl"
    sess.files_turn_opened(time.time() - 30)
    bot.registry.transition(S, Status.IDLE)
    assert bot._take_next_prompt(sess) is None
    assert not sess.files_turn_open

