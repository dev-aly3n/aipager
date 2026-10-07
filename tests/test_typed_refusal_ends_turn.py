"""Roadmap 8.99 (2): a Deny typed into Claude Code's dialog ends the turn.

With the PermissionRequest hook no longer waiting, Deny types Downs onto
the dialog's last row ("No, and tell Claude what to do differently
(esc)") and Enter. Claude Code 2.1.291 then ends the turn like an
interrupt: it writes ``[Request interrupted by user for tool use]`` and
fires no PostToolUse and no Stop. The session used to stay BUSY ("tool
still in flight") until the 900 s cap. Pinned here:

- :func:`aipager.transcript.interrupted_since` reads that marker, and
  only one written since the answer;
- the watch ends the turn like /stop (card "Denied", tool-in-flight and
  turn-open cleared, IDLE, held messages released) on the marker, or for
  a refusal after the bounded grace; it stands down whenever Claude
  carried on; a typed Allow is ended only by the marker;
- end to end through the tap handler: a typed Deny settles the card and
  the session is IDLE within the watch, nowhere near the cap.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot
from aipager import transcript as tr
from aipager.bot import session_ops
from aipager.dtach import hook_reply, inject
from aipager.state import TOOL_INFLIGHT_MAX_SECONDS, Status

NAME = "claude-dev"
MARKER = "[Request interrupted by user for tool use]"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path, *entries) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _tool_use(t):
    return {"type": "assistant", "timestamp": _iso(t), "message": {
        "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "Write", "input": {}}]}}


def _rejected(t):
    return {"type": "user", "timestamp": _iso(t), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True,
         "content": "The user doesn't want to proceed with this tool use."}]}}


def _marker(t, text=MARKER):
    return {"type": "user", "timestamp": _iso(t),
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


@pytest.fixture(autouse=True)
def _no_leftover_watches():
    session_ops._KEYSTROKE_WATCHES.clear()
    yield
    session_ops._KEYSTROKE_WATCHES.clear()


# ---- the transcript signal ----------------------------------------------------

def test_the_marker_written_after_the_answer_reads_as_interrupted(tmp_path):
    p = tmp_path / "t.jsonl"
    t0 = time.time()
    _write(p, _tool_use(t0 - 5), _rejected(t0 + 0.1), _marker(t0 + 0.1),
           {"type": "last-prompt", "lastPrompt": "x"})        # sidecar, skipped
    assert tr.interrupted_since(str(p), t0) is True


def test_a_marker_from_before_the_answer_does_not(tmp_path):
    p = tmp_path / "t.jsonl"
    t0 = time.time()
    _write(p, _marker(t0 - 30))
    assert tr.interrupted_since(str(p), t0) is False


@pytest.mark.parametrize("tail", ["tool_use", "rejected_result", "answer", "prompt",
                                  "answer_quoting_the_marker"])
def test_anything_else_at_the_tail_does_not(tmp_path, tail):
    p = tmp_path / "t.jsonl"
    t0 = time.time()
    entry = {
        "tool_use": _tool_use(t0 + 1),
        "rejected_result": _rejected(t0 + 1),
        "answer": {"type": "assistant", "timestamp": _iso(t0 + 1), "message": {
            "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "done"}]}},
        "prompt": _marker(t0 + 1, text="please go on"),
        # Claude's own text naming the marker is not the marker.
        "answer_quoting_the_marker": {
            "type": "assistant", "timestamp": _iso(t0 + 1), "message": {
                "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": f"You wrote {MARKER}"}]}},
    }[tail]
    _write(p, _marker(t0 - 30), entry)
    assert tr.interrupted_since(str(p), t0) is False


def test_a_marker_without_a_timestamp_counts_by_the_files_time(tmp_path):
    p = tmp_path / "t.jsonl"
    t0 = time.time()
    _write(p, {"type": "user", "message": {"role": "user", "content": MARKER}})
    assert tr.interrupted_since(str(p), t0 - 5) is True
    os.utime(p, (t0 - 60, t0 - 60))
    assert tr.interrupted_since(str(p), t0) is False


def test_no_transcript_is_never_interrupted(tmp_path):
    assert tr.interrupted_since("", time.time()) is False
    assert tr.interrupted_since(str(tmp_path / "missing.jsonl"), time.time()) is False


# ---- the watch ------------------------------------------------------------------

@pytest.fixture
def fast_watch(monkeypatch):
    monkeypatch.setattr(session_ops, "KEYSTROKE_ANSWER_WATCH_SECONDS", 0.4)
    monkeypatch.setattr(session_ops, "KEYSTROKE_ANSWER_POLL_SECONDS", 0.02)


@pytest.fixture
def world(mk_bot, tmp_path, fast_watch):
    bot = mk_bot()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    bot._drain_next_queued = AsyncMock()
    sess = bot.registry.get_or_create(NAME)
    sess.label = "dev"
    sess.status = Status.BUSY
    sess.busy_msg_id = 50
    sess.busy_started_at = time.monotonic() - 5
    sess.pending_tool_started_at = time.monotonic() - 3
    sess.parent_tool_started_at = sess.pending_tool_started_at
    sess.transcript_path = str(tmp_path / "t.jsonl")
    _write(sess.transcript_path, _tool_use(time.time() - 3))
    policy_snapshot.mark_turn_open(NAME)
    return bot, sess


def _card_texts(bot):
    return [c.args[1] for c in bot._edit_busy_raw.await_args_list]


def test_the_marker_ends_the_turn_like_a_stop(world, run_async):
    bot, sess = world
    sess.pending_permission = {"tool_summary": "stale"}
    t0 = time.time()
    _write(sess.transcript_path, _rejected(t0 + 0.05), _marker(t0 + 0.05))
    run_async(bot._keystroke_answer_watch(sess, refusal=True, by="@bob",
                                          answered_at=t0))
    assert sess.status is Status.IDLE
    assert _card_texts(bot) == ["🚫 <b>dev</b> · Denied by @bob"]
    assert sess.busy_msg_id is None
    assert sess.pending_tool_started_at is None and sess.parent_tool_started_at is None
    assert sess.work_in_flight(time.monotonic()) is False
    assert policy_snapshot.turn_is_open(NAME) is False
    assert sess.user_stopped_at > 0                   # a late Stop is not owed
    assert sess.pending_permission is None
    assert sess.closed_turn_seq == sess.turn_seq      # no card for it from here
    bot._drain_next_queued.assert_awaited_once_with(sess)


def test_a_refusal_with_no_marker_ends_after_the_bounded_grace(world, run_async):
    bot, sess = world
    started = time.monotonic()
    run_async(bot._keystroke_answer_watch(sess, refusal=True, by="",
                                          answered_at=time.time()))
    assert sess.status is Status.IDLE
    assert time.monotonic() - started < 5             # the grace, not the cap
    assert session_ops.KEYSTROKE_ANSWER_WATCH_SECONDS < TOOL_INFLIGHT_MAX_SECONDS
    assert _card_texts(bot) == ["🚫 <b>dev</b> · Denied"]
    # Not seen to be interrupted: a Stop that still comes delivers its answer.
    assert sess.user_stopped_at == 0.0


def test_a_typed_allow_with_no_marker_is_left_alone(world, run_async):
    bot, sess = world
    run_async(bot._keystroke_answer_watch(sess, refusal=False, by="",
                                          answered_at=time.time()))
    assert sess.status is Status.BUSY
    assert sess.pending_tool_started_at is not None
    assert _card_texts(bot) == []
    assert policy_snapshot.turn_is_open(NAME) is True


def test_a_typed_allow_that_was_interrupted_reads_as_stopped(world, run_async):
    bot, sess = world
    t0 = time.time()
    _write(sess.transcript_path, _marker(t0 + 0.05))
    run_async(bot._keystroke_answer_watch(sess, refusal=False, by="@bob",
                                          answered_at=t0))
    assert sess.status is Status.IDLE
    assert _card_texts(bot) == ["⚠️ <b>dev</b> · Stopped by @bob"]


@pytest.mark.parametrize("change", ["new_tool", "tool_done", "stop",
                                    "new_permission_prompt", "new_turn",
                                    "session_replaced"])
def test_the_watch_stands_down_when_claude_carried_on(world, run_async, change):
    bot, sess = world

    async def _run():
        task = asyncio.ensure_future(bot._keystroke_answer_watch(
            sess, refusal=True, by="", answered_at=time.time()))
        await asyncio.sleep(0.05)
        if change == "new_tool":
            sess.pending_tool_started_at = time.monotonic()     # a new PreToolUse
        elif change == "tool_done":
            sess.pending_tool_started_at = None                 # its PostToolUse
        elif change == "stop":
            sess.status = Status.IDLE
        elif change == "new_permission_prompt":
            sess.status = Status.INTERACTIVE
        elif change == "session_replaced":
            bot.registry._sessions[NAME] = type(sess)(name=NAME, label="dev",
                                                      status=Status.BUSY)
        else:
            sess.turn_seq += 1
        await task

    run_async(_run())
    assert _card_texts(bot) == []
    assert policy_snapshot.turn_is_open(NAME) is True
    bot._drain_next_queued.assert_not_awaited()


def test_with_a_background_job_open_only_the_tool_is_cleared(world, run_async):
    bot, sess = world
    sess.active_subagents["a1"] = {"type": "x", "started_at": time.monotonic(),
                                   "last_seen": time.monotonic()}
    assert sess.job_background_open()
    t0 = time.time()
    _write(sess.transcript_path, _marker(t0 + 0.05))
    run_async(bot._keystroke_answer_watch(sess, refusal=True, by="", answered_at=t0))
    assert sess.pending_tool_started_at is None
    assert sess.status is Status.BUSY
    assert _card_texts(bot) == []


# ---- end to end through the tap handler --------------------------------------------

def _query(verb, msg_id=50):
    q = MagicMock()
    q.data = f"{NAME}:{verb}"
    q.from_user = MagicMock(id=12345)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=-100)
    q.message.chat_id = -100
    q.message.message_id = msg_id
    q.message.text = ""
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    return u


@pytest.mark.parametrize("hook", [False, True], ids=["typed", "hook"])
def test_a_typed_deny_on_the_card_ends_the_turn_and_a_hook_deny_does_not(
        world, run_async, monkeypatch, hook):
    bot, sess = world
    sess.status = Status.INTERACTIVE
    sess.pending_permission = {
        "tool_summary": "Write: x.txt", "tool_info": {"name": "Write"},
        "hook_reply": {"addr": "/nonexistent/x.sock", "request_id": "r"} if hook else None,
        "wait_started_at": time.monotonic()}
    keys = []

    async def _keys(name, key, *a, **k):
        keys.append(key)
        if key == "Enter":
            # Claude Code 2.1.291 at the dialog's last row: the turn ends
            # like an interrupt, no PostToolUse, no Stop.
            _write(sess.transcript_path, _rejected(time.time()), _marker(time.time()))
        return True

    monkeypatch.setattr(inject, "send_keys", _keys)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(hook_reply, "send_decision",
                        lambda reply, decision: bool(reply))
    read_at = []

    async def _queue(s):
        read_at.append(len(keys))
        return []

    bot._claude_queue_before_typed_refusal = _queue

    async def _run():
        await bot._handle_callback(_query("deny"), MagicMock())
        watch = session_ops._KEYSTROKE_WATCHES.get(NAME)
        if watch is not None:
            await watch

    run_async(_run())
    if hook:
        assert keys == [] and read_at == []
        assert NAME not in session_ops._KEYSTROKE_WATCHES
        assert sess.status is Status.BUSY             # Claude goes on; Stop ends it
    else:
        assert keys == ["Down"] * 5 + ["Enter"]
        assert read_at == [0]                         # Claude's queue, before any key
        assert sess.status is Status.IDLE
        assert _card_texts(bot)[-1] == "🚫 <b>dev</b> · Denied"
        assert sess.pending_tool_started_at is None
        assert policy_snapshot.turn_is_open(NAME) is False


def test_the_end_changes_nothing_when_claude_carried_on_meanwhile(world, run_async):
    """Re-checked under the card lock: a hook handled while the watch
    waited for it (a new PreToolUse) means the turn goes on."""
    bot, sess = world
    ended = run_async(bot._end_turn_ended_at_dialog(
        sess, refused=True, by="", confirmed=True, carried_on=lambda: True))
    assert ended is False
    assert sess.status is Status.BUSY and sess.pending_tool_started_at is not None
    assert _card_texts(bot) == []
    assert policy_snapshot.turn_is_open(NAME) is True



def test_the_marker_is_found_at_the_end_of_a_long_transcript(tmp_path):
    """Only the file's end is read (it is polled on the event loop)."""
    p = tmp_path / "t.jsonl"
    t0 = time.time()
    filler = {"type": "user", "timestamp": _iso(t0 - 60),
              "message": {"role": "user", "content": "x" * 5000}}
    _write(p, *([filler] * 200), _marker(t0 + 0.1))  # ~1 MB
    assert p.stat().st_size > 2 * tr.TAIL_READ_BYTES
    assert tr.turn_tail_since(str(p), t0) == "marker"
    _write(p, _tool_use(t0 + 0.2))
    assert tr.turn_tail_since(str(p), t0) == "other"


@pytest.mark.parametrize("entry", ["rejected_result", "new_prompt"])
def test_at_the_grace_a_transcript_that_moved_on_stands_the_refusal_down(
        world, run_async, entry):
    """Something written since the answer that is not the marker: the
    rejected tool's result with no marker after it (a release where the
    last row no longer ends the turn), or a new prompt."""
    bot, sess = world
    t0 = time.time()
    _write(sess.transcript_path, _rejected(t0 + 0.05) if entry == "rejected_result"
           else _marker(t0 + 0.05, text="next thing please"))
    run_async(bot._keystroke_answer_watch(sess, refusal=True, by="", answered_at=t0))
    assert sess.status is Status.BUSY
    assert _card_texts(bot) == []


def test_with_claudes_queue_holding_messages_only_the_marker_ends_it(
        world, run_async, monkeypatch):
    bot, sess = world
    held = [{"msg_id": 7, "chat_id": -100, "text": "queued"}]
    run_async(bot._keystroke_answer_watch(sess, refusal=True, by="",
                                          answered_at=time.time(), held=held))
    assert sess.status is Status.BUSY                 # Claude may run it next

    wiped = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "discard_queued_input", wiped)
    bot._mark_not_delivered = AsyncMock()
    bot._discard_queued_targets = MagicMock()
    t0 = time.time()
    _write(sess.transcript_path, _marker(t0 + 0.05))
    run_async(bot._keystroke_answer_watch(sess, refusal=True, by="",
                                          answered_at=t0, held=held))
    assert sess.status is Status.IDLE
    # Pulled into the input box by the interrupt, as by /stop's Escape.
    wiped.assert_awaited_once_with(NAME)
    bot._mark_not_delivered.assert_awaited_once_with(sess, held)
    bot._discard_queued_targets.assert_called_once_with(sess)


def test_with_nothing_queued_the_input_box_is_not_touched(world, run_async, monkeypatch):
    bot, sess = world
    wiped = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "discard_queued_input", wiped)
    t0 = time.time()
    _write(sess.transcript_path, _marker(t0 + 0.05))
    run_async(bot._keystroke_answer_watch(sess, refusal=True, by="", answered_at=t0))
    assert sess.status is Status.IDLE
    wiped.assert_not_awaited()


def test_a_newer_watch_replaces_the_older_one(world, run_async):
    bot, sess = world

    async def _run():
        bot._watch_keystroke_answer(sess, refusal=False, by="", answered_at=time.time())
        first = session_ops._KEYSTROKE_WATCHES[NAME]
        bot._watch_keystroke_answer(sess, refusal=False, by="", answered_at=time.time())
        second = session_ops._KEYSTROKE_WATCHES[NAME]
        await asyncio.sleep(0)
        assert first is not second
        assert first.cancelled()
        await second

    run_async(_run())


def test_a_typed_deny_on_a_degraded_question_is_not_a_refusal(
        world, run_async, monkeypatch):
    """The "AskUserQuestion (loading...)" prompt has Allow/Deny, but its
    Downs land on the question's own rows: only the marker may end it."""
    bot, sess = world
    sess.status = Status.INTERACTIVE
    sess.pending_permission = {
        "tool_summary": "AskUserQuestion (loading…)",
        "tool_info": {"name": "AskUserQuestion"}, "wait_started_at": time.monotonic()}
    monkeypatch.setattr(inject, "send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))

    async def _run():
        await bot._handle_callback(_query("deny"), MagicMock())
        await session_ops._KEYSTROKE_WATCHES[NAME]

    run_async(_run())
    assert sess.status is Status.BUSY
    assert _card_texts(bot)[-1] != "🚫 <b>dev</b> · Denied"
