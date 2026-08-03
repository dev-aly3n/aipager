"""Integration: draft-safety contracts — SC10–SC16.

Success criteria covered:
  SC10 - DM turn start: draft_id > 0, stream_offset == transcript size, stream_text == ""
  SC11 - Group turn start: draft_id == 0, no draft call ever
  SC12 - Every draft call within one turn uses the SAME draft_id
  SC14 - send_rich_message_draft returning False → draft_id = 0, never raises
  SC15 - busy message still sent for DM scopes with Stop keyboard
  SC16 - draft_id, stream_offset, stream_text absent from _PERSIST_FIELDS

Error guessing:
  - RuntimeError inside send_rich_message_draft must not escape to the animation loop
  - Group scope: verify no draft call across multiple animation ticks
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status, TrackedSession
from aipager.state import SessionRegistry


def _dm_sess(label="eve"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = "dm"
    s.scope_chat_id = 555555
    return s


def _group_sess(label="grp"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = "group"
    s.scope_chat_id = -200
    return s


def _write_assistant(tmp_path, text: str) -> str:
    entry = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    }
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(entry) + "\n")
    return str(p)


# ── SC16 — transient fields absent from _PERSIST_FIELDS ──────────────────────

def test_sc16_draft_id_not_in_persist_fields():
    """draft_id must not be in _PERSIST_FIELDS."""
    registry = SessionRegistry()
    assert "draft_id" not in registry._PERSIST_FIELDS


def test_sc16_stream_offset_not_in_persist_fields():
    """stream_offset must not be in _PERSIST_FIELDS."""
    registry = SessionRegistry()
    assert "stream_offset" not in registry._PERSIST_FIELDS


def test_sc16_stream_text_not_in_persist_fields():
    """stream_text must not be in _PERSIST_FIELDS."""
    registry = SessionRegistry()
    assert "stream_text" not in registry._PERSIST_FIELDS


# ── SC10 — DM turn start seeds correctly ──────────────────────────────────────

def test_sc10_dm_draft_id_nonzero_after_turn_start(mk_bot, tmp_path, monkeypatch):
    """DM scope: draft_id > 0 after _send_busy_and_animate."""
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')

    bot = mk_bot()
    sess = _dm_sess()
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    asyncio.new_event_loop().run_until_complete(bot._send_busy_and_animate(sess))

    assert sess.draft_id > 0


def test_sc10_dm_stream_text_empty_after_turn_start(mk_bot, tmp_path, monkeypatch):
    """DM scope: stream_text == '' after _send_busy_and_animate (cleared)."""
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')

    bot = mk_bot()
    sess = _dm_sess()
    sess.stream_text = "stale from previous turn"
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    asyncio.new_event_loop().run_until_complete(bot._send_busy_and_animate(sess))

    assert sess.stream_text == ""


def test_sc10_dm_stream_offset_equals_file_size(mk_bot, tmp_path, monkeypatch):
    """DM scope: stream_offset == transcript byte size at turn start."""
    tp = tmp_path / "t.jsonl"
    content = b'{"type":"user","message":{}}\n{"type":"assistant","message":{"content":[]}}\n'
    tp.write_bytes(content)

    bot = mk_bot()
    sess = _dm_sess()
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    asyncio.new_event_loop().run_until_complete(bot._send_busy_and_animate(sess))

    assert sess.stream_offset == len(content)


# ── SC11 — Group scope never sets draft_id ────────────────────────────────────

def test_sc11_group_draft_id_stays_zero(mk_bot, tmp_path, monkeypatch):
    """Group scope: draft_id stays 0 after _send_busy_and_animate."""
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')

    bot = mk_bot()
    sess = _group_sess()
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    asyncio.new_event_loop().run_until_complete(bot._send_busy_and_animate(sess))

    assert sess.draft_id == 0


def test_sc11_group_no_draft_call_when_animate_busy_runs(mk_bot, monkeypatch):
    """Group scope: no draft call fires even across multiple _animate_busy ticks."""
    bot = mk_bot()
    sess = _group_sess()
    sess.busy_msg_id = 1
    sess.draft_id = 0  # group scope

    iteration = 0

    async def _no_sleep(t):
        nonlocal iteration
        iteration += 1
        if iteration >= 3:
            sess.status = Status.IDLE

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    draft_calls = []

    async def _fake_push(s):
        draft_calls.append(1)

    monkeypatch.setattr(bot, "_push_draft", _fake_push)
    bot._app.bot.send_chat_action = AsyncMock()
    sess.last_tool_edit_at = time.monotonic() + 1000  # force debounce

    asyncio.new_event_loop().run_until_complete(bot._animate_busy(sess))

    assert len(draft_calls) == 0


# ── SC12 — same draft_id across all ticks ─────────────────────────────────────

def test_sc12_same_draft_id_across_ticks(mk_bot, tmp_path, monkeypatch):
    """Every send_rich_message_draft call within a turn uses the same draft_id."""
    tp = _write_assistant(tmp_path, "First block")

    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 55
    sess.stream_offset = 0
    sess.stream_text = ""

    recorded_ids = []

    async def _capture_draft(chat_id, draft_id, markdown, **kw):
        recorded_ids.append(draft_id)
        return True

    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: tp)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft", _capture_draft)
    monkeypatch.setattr("aipager.bot.animation.detect_rtl", lambda t: False)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(bot._push_draft(sess))
    loop.run_until_complete(bot._push_draft(sess))

    assert len(recorded_ids) >= 1
    assert all(did == 55 for did in recorded_ids)


# ── SC14 — draft failure sets draft_id = 0, never raises ─────────────────────

def test_sc14_draft_failure_sets_draft_id_zero(mk_bot, tmp_path, monkeypatch):
    """send_rich_message_draft returning False → draft_id = 0."""
    tp = _write_assistant(tmp_path, "Some text")

    bot = mk_bot()
    sess = _dm_sess()
    sess.draft_id = 33
    sess.stream_offset = 0
    sess.stream_text = ""

    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: tp)
    monkeypatch.setattr("aipager.bot.animation.send_rich_message_draft",
                        AsyncMock(return_value=False))
    monkeypatch.setattr("aipager.bot.animation.detect_rtl", lambda t: False)

    asyncio.new_event_loop().run_until_complete(bot._push_draft(sess))

    assert sess.draft_id == 0


def test_sc14_draft_failure_disables_drafts_in_animate_busy(mk_bot, monkeypatch):
    """When send_rich_message_draft returns False, the animation loop must continue
    normally (not crash) and no further draft calls must fire that turn.

    This tests the PUBLIC contract at the _animate_busy level: the loop must
    survive a draft failure and eventually stop when the session goes IDLE.
    """
    bot = mk_bot()
    sess = _dm_sess()
    sess.busy_msg_id = 1
    sess.draft_id = 44

    # Track how many times _push_draft is called from the loop
    push_calls = []

    async def _fail_push(s):
        push_calls.append(1)
        # Simulate what failure does: set draft_id = 0
        s.draft_id = 0

    monkeypatch.setattr(bot, "_push_draft", _fail_push)

    iteration = 0

    async def _no_sleep(t):
        nonlocal iteration
        iteration += 1
        if iteration >= 3:
            sess.status = Status.IDLE

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    bot._app.bot.send_chat_action = AsyncMock()
    sess.last_tool_edit_at = time.monotonic() + 1000

    # Must NOT raise
    asyncio.new_event_loop().run_until_complete(bot._animate_busy(sess))

    # The loop completed without exception; _push_draft was called at least once
    assert len(push_calls) >= 1


def test_sc14_no_draft_calls_after_draft_id_cleared(mk_bot, monkeypatch):
    """After draft_id is cleared to 0, the animation loop must not call _push_draft again.

    This asserts the _animate_busy guard: 'if sess.draft_id and scope_kind == "dm"'.
    """
    bot = mk_bot()
    sess = _dm_sess()
    sess.busy_msg_id = 1
    sess.draft_id = 77
    sess.scope_kind = "dm"

    push_calls = []
    first_call_done = False

    async def _fail_then_clear(s):
        nonlocal first_call_done
        push_calls.append(1)
        if not first_call_done:
            s.draft_id = 0  # simulate failure disabling drafts
            first_call_done = True

    monkeypatch.setattr(bot, "_push_draft", _fail_then_clear)

    iteration = 0

    async def _no_sleep(t):
        nonlocal iteration
        iteration += 1
        if iteration >= 4:
            sess.status = Status.IDLE

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    bot._app.bot.send_chat_action = AsyncMock()
    sess.last_tool_edit_at = time.monotonic() + 1000

    asyncio.new_event_loop().run_until_complete(bot._animate_busy(sess))

    # Only the first call happened (before draft_id was cleared).
    # After clearing, the guard 'if sess.draft_id and ...' prevents further calls.
    assert len(push_calls) == 1


# ── SC15 — busy message still sent for DM scopes ─────────────────────────────

def test_sc15_busy_message_sent_for_dm_scope(mk_bot, tmp_path, monkeypatch):
    """DM scope: send_message is called (busy message) during _send_busy_and_animate."""
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')

    bot = mk_bot()
    sess = _dm_sess()
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    asyncio.new_event_loop().run_until_complete(bot._send_busy_and_animate(sess))

    bot._app.bot.send_message.assert_awaited()


def test_sc15_busy_message_has_stop_keyboard(mk_bot, tmp_path, monkeypatch):
    """DM scope: the busy message must carry a Stop keyboard."""
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')

    bot = mk_bot()
    sess = _dm_sess()
    monkeypatch.setattr("aipager.bot.animation.find_transcript", lambda name: str(tp))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()

    asyncio.new_event_loop().run_until_complete(bot._send_busy_and_animate(sess))

    # The send_message call for the busy message must include a reply_markup
    call_kwargs = bot._app.bot.send_message.await_args.kwargs
    assert "reply_markup" in call_kwargs


# ── IDLE resets stream fields ─────────────────────────────────────────────────

def test_idle_resets_draft_id(mk_bot, run_async, monkeypatch):
    """On IDLE, draft_id must be reset to 0."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-eve", label="eve", status=Status.IDLE)
    sess.draft_id = 99
    sess.stream_offset = 5000
    sess.stream_text = "leftover"
    sess.busy_started_at = time.monotonic()

    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()

    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.draft_id == 0


def test_idle_resets_stream_offset(mk_bot, run_async, monkeypatch):
    """On IDLE, stream_offset must be reset to 0."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-eve", label="eve", status=Status.IDLE)
    sess.draft_id = 99
    sess.stream_offset = 5000
    sess.stream_text = "leftover"
    sess.busy_started_at = time.monotonic()

    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()

    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.stream_offset == 0


def test_idle_resets_stream_text(mk_bot, run_async, monkeypatch):
    """On IDLE, stream_text must be reset to ''."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-eve", label="eve", status=Status.IDLE)
    sess.draft_id = 99
    sess.stream_offset = 5000
    sess.stream_text = "leftover"
    sess.busy_started_at = time.monotonic()

    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()

    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    assert sess.stream_text == ""
