"""Integration tests: IDLE state reset.

Covers the contract from entrypoints.md "Observable state — IDLE":

After the IDLE notify path:
  stream_commentary is [];
  stream_last_rendered is "";
  stream_dirty is False;
  stream_offset is 0;
  stream_transcript_path is "".

These are BLACK-BOX tests.  We call bot.notify() with event="idle_prompt" and assert
on the session fields after the call.  The HTTP transport (send_rich_message) is mocked.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status, TrackedSession


# ── Helper ─────────────────────────────────────────────────────────────────────

def _sess_with_streaming_state(tmp_path, scope_kind="dm"):
    """Return a session that looks like it was mid-turn (all stream fields dirty)."""
    s = TrackedSession(name="claude-idletest", label="idletest", status=Status.IDLE)
    s.scope_kind = scope_kind
    s.scope_chat_id = 12345 if scope_kind == "dm" else -100111222333
    s.busy_started_at = time.monotonic() - 30
    # Populate stream fields as if a turn just finished
    s.stream_commentary = [(0, "opening line"), (2, "text shown in the card")]
    s.stream_dirty = True
    s.stream_last_rendered = "last card markdown"
    s.stream_offset = 4096
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    s.stream_transcript_path = str(tp)
    return s


# ── SC-IDLE-1: stream_commentary cleared on IDLE ─────────────────────────────

def test_idle_clears_stream_commentary(mk_bot, run_async, tmp_path, monkeypatch):
    """entrypoints.md: After IDLE, stream_commentary must be []."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))

    sess = _sess_with_streaming_state(tmp_path)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.stream_commentary == [], (
        f"stream_commentary not cleared on IDLE; got {sess.stream_commentary!r}"
    )


# ── SC-IDLE-3: stream_last_rendered cleared on IDLE ──────────────────────────

def test_idle_clears_stream_last_rendered(mk_bot, run_async, tmp_path, monkeypatch):
    """entrypoints.md: After IDLE, stream_last_rendered must be ''."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))

    sess = _sess_with_streaming_state(tmp_path)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.stream_last_rendered == "", (
        f"stream_last_rendered not cleared on IDLE; got {sess.stream_last_rendered!r}"
    )


# ── SC-IDLE-4: stream_dirty cleared on IDLE ──────────────────────────────────

def test_idle_clears_stream_dirty(mk_bot, run_async, tmp_path, monkeypatch):
    """entrypoints.md: After IDLE, stream_dirty must be False."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))

    sess = _sess_with_streaming_state(tmp_path)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.stream_dirty is False, (
        f"stream_dirty not cleared on IDLE; got {sess.stream_dirty!r}"
    )


# ── SC-IDLE-5: stream_offset reset to 0 on IDLE ──────────────────────────────

def test_idle_resets_stream_offset_to_zero(mk_bot, run_async, tmp_path, monkeypatch):
    """entrypoints.md: After IDLE, stream_offset must be 0."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))

    sess = _sess_with_streaming_state(tmp_path)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.stream_offset == 0, (
        f"stream_offset not reset to 0 on IDLE; got {sess.stream_offset!r}"
    )


# ── SC-IDLE-6: stream_transcript_path cleared on IDLE ───────────────────────

def test_idle_clears_stream_transcript_path(mk_bot, run_async, tmp_path, monkeypatch):
    """entrypoints.md: After IDLE, stream_transcript_path must be ''."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))

    sess = _sess_with_streaming_state(tmp_path)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.stream_transcript_path == "", (
        f"stream_transcript_path not cleared on IDLE; got {sess.stream_transcript_path!r}"
    )


# ── SC-IDLE-7: stream_hook_live SURVIVES the reset ──────────────────────────

def test_idle_keeps_stream_hook_live(mk_bot, run_async, tmp_path, monkeypatch):
    """The hook latch is session-scoped, not per-turn.

    It records that this session's Claude Code sends MessageDisplay, which is
    a capability and does not come and go between turns. Clearing it here
    would re-enable the transcript fallback for the next turn and print every
    sentence twice — the invariant the whole no-duplication design rests on.
    """
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))

    sess = _sess_with_streaming_state(tmp_path)
    sess.stream_hook_live = True
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.stream_hook_live is True, (
        "stream_hook_live was cleared on IDLE; the transcript fallback would "
        "come back and duplicate every commentary block next turn"
    )
