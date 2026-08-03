"""Integration: cross-turn leakage — success criterion 13.

SC13: given a transcript whose previous turn already contains assistant text,
a newly-started turn that has written nothing yet produces no draft call.

This is the regression test for the false-idle-recovery class of bug:
stream_offset is seeded to the transcript's byte size at turn start, so
previous text is never streamed as the current turn's output.

The test exercises the full integration path:
  TrackedSession (state) → _send_busy_and_animate (animation) → _push_draft
  → read_turn_text (transcript) → send_rich_message_draft (rich_message)

We assert at the BEHAVIOURAL level: after seeding stream_offset to the file's
current size, a call to _push_draft with no new assistant text appended must
not invoke send_rich_message_draft.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status, TrackedSession


def _dm_sess(label="dan"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = "dm"
    s.scope_chat_id = 444444
    return s


def _write_prev_turn(tmp_path, text: str) -> tuple[str, int]:
    """Write one complete previous-turn assistant entry, return (path, size)."""
    entry = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    }
    p = tmp_path / "transcript.jsonl"
    content = (json.dumps(entry) + "\n").encode("utf-8")
    p.write_bytes(content)
    return str(p), len(content)


# ── SC13 core regression ──────────────────────────────────────────────────────

def test_sc13_no_draft_when_new_turn_has_written_nothing(mk_bot, tmp_path, monkeypatch):
    """New turn start with offset == file size → no draft call."""
    tp, prev_size = _write_prev_turn(tmp_path, "Previous turn answer")

    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 17
    sess.stream_text = ""
    sess.stream_offset = prev_size  # seeded to file size at turn start
    sess.stream_transcript_path = tp  # pinned at seed time

    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)

    asyncio.new_event_loop().run_until_complete(bot._push_draft(sess))

    send_draft_mock.assert_not_awaited()


def test_sc13_previous_turn_text_not_in_draft(mk_bot, tmp_path, monkeypatch):
    """Even if send_draft were somehow called, previous-turn text must not appear.

    This tests a second layer of protection: the stream_text starts empty,
    so even if read_turn_text returned empty text, the guard on stream_text
    prevents a spurious draft.
    """
    tp, prev_size = _write_prev_turn(tmp_path, "MUST NOT APPEAR IN DRAFT")

    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 18
    sess.stream_text = ""  # no accumulated text yet
    sess.stream_offset = prev_size
    sess.stream_transcript_path = tp  # pinned at seed time

    captured_markdown = []

    async def _capture(chat_id, draft_id, markdown, **kw):
        captured_markdown.append(markdown)
        return True

    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", _capture)

    asyncio.new_event_loop().run_until_complete(bot._push_draft(sess))

    # If any draft was sent, it must not contain the previous turn's text
    for md in captured_markdown:
        assert "MUST NOT APPEAR IN DRAFT" not in md


def test_sc13_new_text_after_offset_is_streamed(mk_bot, tmp_path, monkeypatch):
    """After offset is seeded, NEW assistant text appended after that point IS streamed."""
    prev_entry = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "Old turn"}],
            "stop_reason": "end_turn",
        },
    }
    p = tmp_path / "transcript.jsonl"
    prev_bytes = (json.dumps(prev_entry) + "\n").encode("utf-8")
    p.write_bytes(prev_bytes)
    prev_size = len(prev_bytes)

    # New turn begins — offset seeded here
    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 19
    sess.stream_text = ""
    sess.stream_offset = prev_size  # seeded at turn start

    # Simulate Claude writing a new block during the current turn
    new_entry = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "New turn answer"}],
            "stop_reason": "end_turn",
        },
    }
    with open(str(p), "ab") as f:
        f.write((json.dumps(new_entry) + "\n").encode("utf-8"))

    sess.stream_transcript_path = str(p)  # pinned at seed time
    send_draft_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", send_draft_mock)
    monkeypatch.setattr("aipager.bot.animation.detect_rtl", lambda t: False)

    asyncio.new_event_loop().run_until_complete(bot._push_draft(sess))

    # The new turn's text must have been sent
    send_draft_mock.assert_awaited_once()
    call = send_draft_mock.await_args
    assert "New turn answer" in call.args[2]


def test_sc13_stream_offset_seeded_to_file_size_at_turn_start(
    mk_bot, run_async, tmp_path, monkeypatch
):
    """_send_busy_and_animate seeds stream_offset to the transcript's byte size."""
    prev_text = "Previous turn existing content\n" * 10
    tp = tmp_path / "t.jsonl"
    tp.write_text(prev_text)
    expected_size = tp.stat().st_size

    bot = mk_bot()
    sess = _dm_sess()
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    run_async(bot._send_busy_and_animate(sess))

    assert sess.stream_offset == expected_size


def test_sc13_group_scope_never_seeds_draft_id(mk_bot, run_async, tmp_path, monkeypatch):
    """Group scope: draft_id stays 0 even with a large existing transcript."""
    # Write a transcript full of previous assistant text
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"assistant","message":{"content":[{"type":"text","text":"old"}]}}\n' * 20)

    bot = mk_bot()
    sess = TrackedSession(name="claude-team", label="team", status=Status.BUSY)
    sess.scope_kind = "group"
    sess.scope_chat_id = -100

    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    run_async(bot._send_busy_and_animate(sess))

    assert sess.draft_id == 0
