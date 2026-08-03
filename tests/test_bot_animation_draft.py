"""Tests for the streaming draft additions to AnimationMixin.

Covers:
- _send_busy_and_animate seeds draft_id / stream_offset / stream_text correctly
  for DM and group scopes.
- _push_draft reads from transcript, calls send_rich_message_draft, disables
  drafts on failure.
- _animate_busy calls _push_draft on every iteration including the debounce path.
- No cross-turn leakage: a new DM turn that has written nothing yet produces
  no draft call.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def run_async():
    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)
    return _run


def _dm_sess(label="jim"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = "dm"
    s.scope_chat_id = 123456
    return s


def _group_sess(label="team"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = "group"
    s.scope_chat_id = -100
    return s


# ── _send_busy_and_animate: DM seed ─────────────────────────────────────────

def test_send_busy_and_animate_dm_seeds_draft_id(mk_bot, run_async, tmp_path, monkeypatch):
    """DM scope: draft_id > 0 after _send_busy_and_animate."""
    bot = mk_bot()
    sess = _dm_sess()
    # Create a transcript file so stream_offset gets the file size.
    tp = tmp_path / "turn.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')
    monkeypatch.setattr("aipager.bot.animation.find_transcript",
                        lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.draft_id > 0
    assert sess.stream_offset == tp.stat().st_size
    assert sess.stream_text == ""


def test_send_busy_and_animate_dm_no_transcript_draft_id_still_set(
    mk_bot, run_async, monkeypatch
):
    """DM scope: draft_id is still assigned even when transcript is not found."""
    bot = mk_bot()
    sess = _dm_sess()
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.draft_id > 0
    assert sess.stream_offset == 0


def test_send_busy_and_animate_group_no_draft(mk_bot, run_async, monkeypatch, tmp_path):
    """Group scope: draft_id stays 0."""
    bot = mk_bot()
    sess = _group_sess()
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user"}\n')
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.draft_id == 0


def test_send_busy_and_animate_stream_text_reset(mk_bot, run_async, monkeypatch):
    """stream_text is cleared at the start of each turn."""
    bot = mk_bot()
    sess = _dm_sess()
    sess.stream_text = "leftover from previous turn"
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.stream_text == ""


# ── _push_draft ──────────────────────────────────────────────────────────────

def _write_assistant(tmp_path, text: str) -> str:
    entry = {"type": "assistant", "message": {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }}
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(entry) + "\n")
    return str(p)


def test_push_draft_calls_send_draft(mk_bot, run_async, tmp_path, monkeypatch):
    """_push_draft sends the accumulated text via send_rich_message_draft."""
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 7
    sess.stream_offset = 0
    sess.stream_text = ""
    tp = _write_assistant(tmp_path, "Hello Claude")
    sess.stream_transcript_path = tp  # pinned at seed time
    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)
    monkeypatch.setattr("aipager.bot.animation.detect_rtl", lambda t: False)
    run_async(bot._push_draft(sess))
    send_draft_mock.assert_awaited_once()
    call = send_draft_mock.await_args
    assert call.args[1] == 7  # draft_id
    assert "Hello Claude" in call.args[2]


def test_push_draft_uses_same_draft_id_across_ticks(mk_bot, run_async, tmp_path, monkeypatch):
    """Every draft call within a turn uses the same draft_id."""
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 42
    sess.stream_offset = 0
    sess.stream_text = ""
    tp = _write_assistant(tmp_path, "Partial answer")
    sess.stream_transcript_path = tp  # pinned at seed time
    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)
    monkeypatch.setattr("aipager.bot.animation.detect_rtl", lambda t: False)
    run_async(bot._push_draft(sess))
    run_async(bot._push_draft(sess))
    # Both calls use the same draft_id.
    for call in send_draft_mock.await_args_list:
        assert call.args[1] == 42


def test_push_draft_failure_disables_drafts(mk_bot, run_async, tmp_path, monkeypatch):
    """send_rich_message_draft returning False sets draft_id = 0."""
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 5
    sess.stream_offset = 0
    sess.stream_text = ""
    tp = _write_assistant(tmp_path, "Some text")
    sess.stream_transcript_path = tp  # pinned at seed time
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft",
                        AsyncMock(return_value=False))
    monkeypatch.setattr("aipager.bot.animation.detect_rtl", lambda t: False)
    run_async(bot._push_draft(sess))
    assert sess.draft_id == 0


def test_push_draft_no_transcript_skips(mk_bot, run_async, monkeypatch):
    """If stream_transcript_path is empty (no transcript found at seed time),
    _push_draft returns without calling send_draft."""
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 3
    sess.stream_offset = 0
    sess.stream_transcript_path = ""  # no path was pinned at seed time
    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)
    run_async(bot._push_draft(sess))
    send_draft_mock.assert_not_awaited()


def test_push_draft_empty_text_skips_send(mk_bot, run_async, tmp_path, monkeypatch):
    """If no assistant text has been written yet, no draft call fires."""
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 3
    # Write only a user entry — no assistant text.
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
    sess.stream_offset = 0
    sess.stream_transcript_path = str(p)  # pinned at seed time
    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)
    run_async(bot._push_draft(sess))
    send_draft_mock.assert_not_awaited()


def test_push_draft_no_cross_turn_leakage(mk_bot, run_async, tmp_path, monkeypatch):
    """A new turn whose offset is seeded to the file's current size produces
    no draft call — the previous turn's text must never stream as this turn's.

    This is the unit-level regression test for success criterion 13.
    """
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 9
    sess.stream_text = ""
    # Write the previous turn's text.
    tp = _write_assistant(tmp_path, "Previous turn answer")
    prev_size = os.path.getsize(tp)
    # Seed offset to the file size at turn start (as _send_busy_and_animate does).
    sess.stream_offset = prev_size
    sess.stream_transcript_path = tp  # pinned at seed time
    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)
    run_async(bot._push_draft(sess))
    # No draft fired because this turn has written nothing yet.
    send_draft_mock.assert_not_awaited()


# ── _animate_busy: draft integration ─────────────────────────────────────────

def test_animate_busy_calls_push_draft_before_debounce(mk_bot, run_async, monkeypatch):
    """_animate_busy calls _push_draft even when the busy-message edit is debounced."""
    bot = mk_bot()
    sess = _dm_sess()
    sess.busy_msg_id = 1
    sess.draft_id = 7
    iteration = 0

    async def _no_sleep(t):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            sess.status = Status.IDLE  # stop the loop

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)

    push_calls = []

    async def _fake_push(s):
        push_calls.append(1)

    monkeypatch.setattr(bot, "_push_draft", _fake_push)
    # Force debounce by making last_tool_edit_at very recent.
    sess.last_tool_edit_at = time.monotonic() + 1000
    bot._app.bot.send_chat_action = AsyncMock()
    run_async(bot._animate_busy(sess))
    assert len(push_calls) >= 1


def test_animate_busy_group_no_push_draft(mk_bot, run_async, monkeypatch):
    """Group scope: _push_draft is never called (draft_id == 0)."""
    bot = mk_bot()
    sess = _group_sess()
    sess.busy_msg_id = 1
    sess.draft_id = 0  # group scopes never set draft_id
    iteration = 0

    async def _no_sleep(t):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            sess.status = Status.IDLE

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    push_calls = []

    async def _fake_push(s):
        push_calls.append(1)

    monkeypatch.setattr(bot, "_push_draft", _fake_push)
    sess.last_tool_edit_at = time.monotonic() + 1000
    bot._app.bot.send_chat_action = AsyncMock()
    run_async(bot._animate_busy(sess))
    # _push_draft never called for group scope
    assert len(push_calls) == 0


# ── notify IDLE: stream fields reset ─────────────────────────────────────────

def test_idle_resets_stream_fields(mk_bot, run_async, monkeypatch):
    """On IDLE, draft_id / stream_offset / stream_text / stream_transcript_path are all reset."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.draft_id = 99
    sess.stream_offset = 5000
    sess.stream_text = "partial answer"
    sess.stream_transcript_path = "/some/path/turn.jsonl"
    sess.busy_started_at = time.monotonic()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    assert sess.draft_id == 0
    assert sess.stream_offset == 0
    assert sess.stream_text == ""
    assert sess.stream_transcript_path == ""


# ── Cross-session leak fix tests ─────────────────────────────────────────────

def test_seed_prefers_transcript_path_over_find_transcript(
    mk_bot, run_async, tmp_path, monkeypatch
):
    """Seeding uses sess.transcript_path when set; find_transcript is NOT called."""
    bot = mk_bot()
    sess = _dm_sess()

    # The authoritative transcript from the hook payload.
    hook_tp = tmp_path / "hook_transcript.jsonl"
    hook_tp.write_text('{"type":"user","message":{}}\n')
    sess.transcript_path = str(hook_tp)

    # A different file that find_transcript would return (wrong session).
    other_tp = tmp_path / "other_session.jsonl"
    other_tp.write_text('{"type":"assistant","message":{}}\n' * 5)

    find_calls: list[str] = []

    def _mock_find(name: str) -> str:
        find_calls.append(name)
        return str(other_tp)

    monkeypatch.setattr("aipager.bot.animation.find_transcript", _mock_find)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))

    # find_transcript must NOT have been called — hook path takes precedence.
    assert find_calls == []
    # Pinned path is the hook transcript, not the other session's file.
    assert sess.stream_transcript_path == str(hook_tp)
    assert sess.stream_offset == hook_tp.stat().st_size


def test_seed_falls_back_to_find_transcript_when_transcript_path_empty(
    mk_bot, run_async, tmp_path, monkeypatch
):
    """When sess.transcript_path is empty, find_transcript is used as fallback."""
    bot = mk_bot()
    sess = _dm_sess()
    sess.transcript_path = ""  # not yet stamped by hook

    fallback_tp = tmp_path / "fallback.jsonl"
    fallback_tp.write_text('{"type":"user","message":{}}\n')

    monkeypatch.setattr("aipager.bot.animation.find_transcript",
                        lambda name: str(fallback_tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))

    assert sess.stream_transcript_path == str(fallback_tp)
    assert sess.stream_offset == fallback_tp.stat().st_size


def test_push_draft_uses_pinned_path_not_find_transcript(
    mk_bot, run_async, tmp_path, monkeypatch
):
    """_push_draft reads from stream_transcript_path, not from find_transcript.

    Regression test for failure mode 2: seeding pins fileA, then find_transcript
    would return fileB mid-turn.  Without the fix this emits a mid-file slice of
    fileB; with the fix _push_draft ignores find_transcript entirely.
    """
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 11

    # Pin the correct transcript at seed time.
    pinned_tp = _write_assistant(tmp_path, "correct turn text")
    sess.stream_offset = 0
    sess.stream_transcript_path = pinned_tp

    # find_transcript would return a DIFFERENT file (the wrong session).
    wrong_tp = tmp_path / "wrong_session.jsonl"
    wrong_entry = {"type": "assistant", "message": {
        "content": [{"type": "text", "text": "WRONG SESSION CONTENT"}],
        "stop_reason": "end_turn",
    }}
    wrong_tp.write_text(json.dumps(wrong_entry) + "\n")
    find_calls: list[str] = []

    def _mock_find(name: str) -> str:
        find_calls.append(name)
        return str(wrong_tp)

    monkeypatch.setattr("aipager.bot.animation.find_transcript", _mock_find)
    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)
    monkeypatch.setattr("aipager.bot.animation.detect_rtl", lambda t: False)
    run_async(bot._push_draft(sess))

    # find_transcript was never consulted mid-turn.
    assert find_calls == []
    # The draft was sent with correct content, not the wrong session's content.
    send_draft_mock.assert_awaited_once()
    sent_content = send_draft_mock.await_args.args[2]
    assert "correct turn text" in sent_content
    assert "WRONG SESSION CONTENT" not in sent_content


def test_push_draft_no_stream_when_no_path_pinned(mk_bot, run_async, monkeypatch):
    """_push_draft does not stream when stream_transcript_path is empty.

    This covers the case where no transcript was found at seed time.
    Failing closed (no draft) is always correct — never stream from an
    unknown file.
    """
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 3
    sess.stream_transcript_path = ""  # no path was pinned

    # find_transcript would have a file to offer, but must not be called.
    find_calls: list[str] = []

    def _mock_find(name: str) -> str:
        find_calls.append(name)
        return "/some/other/file.jsonl"

    monkeypatch.setattr("aipager.bot.animation.find_transcript", _mock_find)
    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)
    run_async(bot._push_draft(sess))

    assert find_calls == []
    send_draft_mock.assert_not_awaited()
