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
