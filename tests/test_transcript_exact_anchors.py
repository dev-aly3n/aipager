"""Transcript-exact sentence anchors ("transcript-exact-sentence-anchors").

The MessageDisplay hook delivers a short sentence only at message END —
after every tool_use block of that message has streamed and, with
sequential tool execution, after all but the last has run. A four-tool
message therefore ends seconds after its first PreToolUse. The old
clock-based batch expiry (`_expire_tool_batch`, 1.5 s from the FIRST row)
gave up first, moved the floor past the rows, and the sentence landed
below (or in the middle of) the tools it introduced; the last sentence
before the answer anchored past the end and was hidden as "the answer".

Live reproduction: session `mimo`, 2026-09-04 20:29–20:30 UTC (transcript
message `Q5bz…`: text, Agent, three Bash rows; the card put the sentence
after the second row). Fix: the transcript is the ground truth — its lines
for a message (text + tool_use, one `message.id`) flush together, so they
place each sentence exactly; the clock never moves the floor while a
transcript is readable.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import aipager.bot.animation as anim
from aipager import preferences as prefs
from aipager.bot.animation import (
    _ROW_SEP,
    _sync_anchors_from_transcript,
    build_stream_card_ex,
)
from aipager.state import TOOL_HISTORY_CAP, Status, TrackedSession


# ── helpers ──────────────────────────────────────────────────────────────────

def _sess(tmp_path, label="dev", *, busy_msg_id=0):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = "dm"
    s.scope_chat_id = 12345
    s.busy_started_at = time.monotonic() - 10
    s.busy_msg_id = busy_msg_id
    p = tmp_path / "t.jsonl"
    p.write_bytes(b"")
    s.stream_transcript_path = str(p)
    s.stream_offset = 0
    s.stream_hook_live = True
    return s


def _append_round(sess, message_id, text, tools):
    """Append one flushed assistant round the way Claude Code writes it:
    one line per content block, every line carrying the same
    ``message.id`` — the text block first, then one tool_use per tool."""
    lines = []
    if text:
        lines.append({"type": "assistant", "message": {
            "id": message_id,
            "content": [{"type": "text", "text": text}],
        }})
    for i, name in enumerate(tools):
        lines.append({"type": "assistant", "message": {
            "id": message_id,
            "content": [{"type": "tool_use", "id": f"{message_id}-{i}",
                         "name": name, "input": {}}],
        }})
    with open(sess.stream_transcript_path, "ab") as fh:
        for line in lines:
            fh.write((json.dumps(line) + "\n").encode("utf-8"))


def _text(bot, run_async, sess, delta, msg_id):
    run_async(bot.notify(sess, "assistant_text", {
        "delta": delta, "message_id": msg_id, "index": 0, "final": True,
    }))


def _tool(bot, run_async, sess, summary):
    run_async(bot.notify(sess, "tool_use", {
        "tool_name": summary.split(":", 1)[0], "tool_summary": summary,
    }))


def _age_batch(sess):
    """The message's end came long after its first PreToolUse."""
    sess.stream_batch_since = time.monotonic() - anim._BATCH_HOLD_SECS - 0.1


def _rows(card: str) -> list[str]:
    body, _, _status = card.rpartition(_ROW_SEP)
    return body.split(_ROW_SEP) if body else []


def _at(rows: list[str], needle: str) -> int:
    hits = [i for i, r in enumerate(rows) if needle in r]
    assert hits, f"{needle!r} not in {rows}"
    return hits[0]


# ── the live defect, reproduced ──────────────────────────────────────────────

def test_multi_tool_message_sentence_anchors_above_its_first_row(
    mk_bot, run_async, tmp_path,
):
    """The `Q5bz` shape from the mimo transcript: text, Agent, then three
    Bash rows, the sentence arriving at message end — seconds after the
    first row. It must sit above the Agent row, not after the second."""
    bot = mk_bot()
    sess = _sess(tmp_path)
    _tool(bot, run_async, sess, "Agent")
    run_async(bot.notify(sess, "subagent_start", {
        "agent_type": "general-purpose", "agent_id": "a1",
    }))
    _tool(bot, run_async, sess, "Bash: Count Python files")
    _tool(bot, run_async, sess, "Bash: Count total Python lines")
    _tool(bot, run_async, sess, "Bash: List ten biggest files")
    _age_batch(sess)
    _text(bot, run_async, sess, "Starting with a look at the tree.", "Q5bz")

    assert sess.stream_commentary == [(0, "Starting with a look at the tree.")]
    rows = _rows(build_stream_card_ex(sess, "Working")[0])
    assert rows[0] == "> Starting with a look at the tree."
    assert _at(rows, "Starting with a look") < _at(rows, "`Agent`")
    assert _at(rows, "`Agent`") < _at(rows, "Bash: Count Python files")


def test_late_sentence_before_the_answer_is_not_lost(mk_bot, run_async, tmp_path):
    """`hEHn` from the same transcript: the git-stage sentence arrived after
    its two rows with the batch long expired, anchored past the end, and
    was hidden as the answer. It is commentary and must be on the card."""
    bot = mk_bot()
    sess = _sess(tmp_path)
    _tool(bot, run_async, sess, "Bash: Show last 10 commits")
    _tool(bot, run_async, sess, "Bash: Show branch")
    _age_batch(sess)
    _text(bot, run_async, sess, "All false positives — git history now.", "hEHn")

    assert sess.stream_commentary == [(0, "All false positives — git history now.")]
    live = _rows(build_stream_card_ex(sess, "Working")[0])
    assert live[0] == "> All false positives — git history now."
    final = _rows(build_stream_card_ex(sess, "Done", final=True)[0])
    assert final[0] == "> All false positives — git history now."


# ── the transcript as ground truth ───────────────────────────────────────────

def test_silent_round_settles_from_the_transcript_before_the_next_sentence(
    mk_bot, run_async, tmp_path,
):
    """A message that called tools without saying anything: its flushed
    round moves the floor past its rows, so the NEXT sentence does not
    claim them — the job the clock used to do, now done exactly."""
    bot = mk_bot()
    sess = _sess(tmp_path)
    _tool(bot, run_async, sess, "Read: a.py")
    _tool(bot, run_async, sess, "Read: b.py")
    _append_round(sess, "S", None, ["Read", "Read"])
    _tool(bot, run_async, sess, "Bash: grep")
    _text(bot, run_async, sess, "Now grepping.", "M")

    assert sess.stream_commentary == [(2, "Now grepping.")]
    rows = _rows(build_stream_card_ex(sess, "Working")[0])
    assert _at(rows, "Read: b.py") < _at(rows, "Now grepping.")
    assert _at(rows, "Now grepping.") < _at(rows, "Bash: grep")


def test_transcript_round_corrects_a_provisional_anchor(tmp_path):
    """A block that landed below its rows (the pre-fix placement) is moved
    to the message's first row once the round is readable; the cursor and
    floor advance, the batch is settled, and the card is marked dirty."""
    sess = _sess(tmp_path)
    for s in ("Bash: one", "Bash: two", "Bash: three"):
        sess.record_tool(s, True)
    sess.stream_commentary = [(3, "Doing three things.")]
    sess.stream_block_index = {"M": 0}
    sess.stream_anchor_floor = 3
    sess.stream_dirty = False
    _append_round(sess, "M", "Doing three things.", ["Bash", "Bash", "Bash"])

    assert _sync_anchors_from_transcript(sess) is True
    assert sess.stream_commentary == [(0, "Doing three things.")]
    assert sess.stream_exact_anchor == {"M": 0}
    assert sess.stream_tool_cursor == 3
    assert sess.stream_anchor_floor == 3
    assert sess.stream_batch_since is None


def test_sync_reports_no_change_when_the_anchor_was_already_right(tmp_path):
    sess = _sess(tmp_path)
    sess.record_tool("Bash: one", True)
    sess.stream_commentary = [(0, "One thing.")]
    sess.stream_block_index = {"M": 0}
    _append_round(sess, "M", "One thing.", ["Bash"])
    assert _sync_anchors_from_transcript(sess) is False
    assert sess.stream_commentary == [(0, "One thing.")]
    assert sess.stream_tool_cursor == 1


def test_exact_anchor_learned_before_the_hook_text_arrives(
    mk_bot, run_async, tmp_path,
):
    """The round can flush before the hook hands over the sentence (the
    last tool was quick). The next message's first row then lands before
    the sentence does — and the floor would put the sentence below it.
    The exact anchor learned from the transcript wins."""
    bot = mk_bot()
    sess = _sess(tmp_path)
    _tool(bot, run_async, sess, "Bash: one")
    _tool(bot, run_async, sess, "Bash: two")
    _append_round(sess, "M", "Both at once.", ["Bash", "Bash"])
    _tool(bot, run_async, sess, "Bash: next")
    _text(bot, run_async, sess, "Both at once.", "M")

    assert sess.stream_commentary == [(0, "Both at once.")]
    assert sess.stream_block_index == {"M": 0}


def test_agent_rows_are_stepped_over_when_matching_a_round(tmp_path):
    """SubagentStart appends a 🤖 row after the Agent row; the transcript
    has no block for it. The walk must step over it, like the fallback's
    cursor always has, so the following message anchors on ITS row."""
    sess = _sess(tmp_path)
    sess.record_tool("Agent", True)
    sess.record_tool("\U0001f916 general-purpose", True)
    sess.record_tool("Bash: after", True)
    sess.stream_commentary = [(3, "Kicking off an agent.")]
    sess.stream_block_index = {"A": 0}
    _append_round(sess, "A", "Kicking off an agent.", ["Agent"])

    assert _sync_anchors_from_transcript(sess) is True
    assert sess.stream_commentary == [(0, "Kicking off an agent.")]
    # The 🤖 row is the Agent block's own: the walk steps over it, so the
    # NEXT sentence lands below it rather than between the Agent and its
    # agent row.
    assert sess.stream_tool_cursor == 2
    assert sess.stream_anchor_floor == 2

    sess.stream_commentary.append((3, "Then a bash."))
    sess.stream_block_index["B"] = 1
    _append_round(sess, "B", "Then a bash.", ["Bash"])
    assert _sync_anchors_from_transcript(sess) is True
    assert sess.stream_commentary == [(0, "Kicking off an agent."), (2, "Then a bash.")]
    assert sess.stream_tool_cursor == 3


def test_an_unmatched_tool_line_leaves_the_cursor_alone(tmp_path):
    """The transcript cannot legitimately run ahead of PreToolUse; a tool
    line with no row is skipped rather than walking the cursor off the
    rows that ARE there."""
    sess = _sess(tmp_path)
    sess.record_tool("Bash: real", True)
    _append_round(sess, "M", None, ["Grep", "Bash"])
    _sync_anchors_from_transcript(sess)
    assert sess.stream_tool_cursor == 1
    assert sess.stream_anchor_floor == 1


def test_partial_settlement_keeps_the_open_batch(tmp_path):
    """Rows beyond the flushed round belong to the next message; their
    batch stays open (no hold flicker, no premature settlement)."""
    sess = _sess(tmp_path)
    sess.record_tool("Bash: one", True)
    since = sess.stream_batch_since
    _append_round(sess, "S", None, ["Bash"])
    sess.record_tool("Bash: two", False)
    _sync_anchors_from_transcript(sess)
    assert sess.stream_anchor_floor == 1
    assert sess.stream_batch_since == since


def test_sync_is_inert_without_a_readable_transcript(tmp_path):
    sess = _sess(tmp_path)
    sess.record_tool("Bash: one", True)
    sess.stream_transcript_path = ""
    assert _sync_anchors_from_transcript(sess) is False
    sess.stream_transcript_path = str(tmp_path / "missing.jsonl")
    assert _sync_anchors_from_transcript(sess) is False
    assert sess.stream_anchor_floor == 0


def test_sync_is_inert_while_the_hook_is_not_live(tmp_path):
    """The transcript fallback owns the cursor and offset then; the exact
    scan must not consume the lines it is about to read for TEXT."""
    sess = _sess(tmp_path)
    sess.stream_hook_live = False
    sess.record_tool("Bash: one", True)
    _append_round(sess, "M", "Hello.", ["Bash"])
    assert _sync_anchors_from_transcript(sess) is False
    assert sess.stream_offset == 0


# ── the clock is now only a fallback ─────────────────────────────────────────

def test_expiry_defers_to_the_transcript_when_one_is_readable(tmp_path):
    sess = _sess(tmp_path)
    sess.record_tool("Bash: a", True)
    _age_batch(sess)
    anim._expire_tool_batch(sess)
    assert sess.stream_anchor_floor == 0
    assert sess.stream_batch_since is not None


def test_expiry_still_settles_a_batch_without_a_transcript(tmp_path):
    sess = _sess(tmp_path)
    sess.stream_transcript_path = ""
    sess.record_tool("Bash: quiet", True)
    _age_batch(sess)
    anim._expire_tool_batch(sess)
    assert sess.stream_anchor_floor == 1
    assert sess.stream_batch_since is None


def test_expiry_still_settles_a_batch_for_the_transcript_fallback(tmp_path):
    sess = _sess(tmp_path)
    sess.stream_hook_live = False
    sess.record_tool("Bash: quiet", True)
    _age_batch(sess)
    anim._expire_tool_batch(sess)
    assert sess.stream_anchor_floor == 1


def test_rows_still_draw_after_the_hold_while_the_batch_stays_open(
    mk_bot, run_async, tmp_path,
):
    """Live card: a multi-tool message's rows appear after the hold even
    though its batch stays open until the sentence lands."""
    bot = mk_bot()
    sess = _sess(tmp_path)
    _tool(bot, run_async, sess, "Bash: slow")
    assert _rows(build_stream_card_ex(sess, "Working")[0]) == []
    _age_batch(sess)
    assert _rows(build_stream_card_ex(sess, "Working")[0]) == ["⏳ `Bash: slow`"]


# ── bookkeeping ──────────────────────────────────────────────────────────────

def test_cap_trim_shifts_exact_anchors():
    sess = TrackedSession(name="claude-x", label="x", status=Status.BUSY)
    sess.stream_exact_anchor = {"M": 5, "N": 1}
    for i in range(TOOL_HISTORY_CAP + 3):
        sess.record_tool(f"Bash: {i}", True)
    assert sess.stream_exact_anchor == {"M": 2, "N": 0}


def test_new_turn_seed_clears_the_per_turn_maps(mk_bot, run_async, tmp_path):
    bot = mk_bot()
    bot.send_busy = AsyncMock(return_value=0)
    bot._animate_busy = AsyncMock()  # no live animator on a closed loop
    bot._app.bot.send_chat_action = AsyncMock()
    sess = _sess(tmp_path)
    sess.stream_block_index = {"M": 0}
    sess.stream_exact_anchor = {"M": 0}
    run_async(bot._send_busy_and_animate(sess))
    assert sess.stream_block_index == {}
    assert sess.stream_exact_anchor == {}


@pytest.fixture
def rich_calls(monkeypatch):
    calls = []

    async def _fake_post(method, payload, **_kw):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 999}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return calls


def test_finished_card_is_exact_even_without_a_last_tick(
    mk_bot, run_async, tmp_path, rich_calls,
):
    """The last round flushes right before Stop; no tick may run between.
    The finish path syncs before rendering the finished card."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess(tmp_path, busy_msg_id=42)
    sess.status = Status.IDLE  # the idle path runs after the transition
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    sess.record_tool("Bash: one", True)
    sess.record_tool("Bash: two", True)
    sess.stream_commentary = [(2, "Two quick checks.")]
    sess.stream_block_index = {"M": 0}
    _append_round(sess, "M", "Two quick checks.", ["Bash", "Bash"])

    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    edits = [p for m, p in rich_calls if m == "editMessageText"]
    assert edits, rich_calls
    rows = _rows(edits[0]["rich_message"]["markdown"])
    assert rows[0] == "> Two quick checks."
    assert sess.stream_block_index == {}
    assert sess.stream_exact_anchor == {}


def test_a_text_only_message_never_borrows_the_next_rows(tmp_path):
    """A message with text and no matched tool line (the answer, or one
    whose tool lines all went unmatched) must not take the NEXT message's
    first row as its anchor."""
    sess = _sess(tmp_path)
    sess.record_tool("Bash: theirs", True)
    sess.stream_commentary = [(1, "Just a remark.")]
    sess.stream_block_index = {"T": 0}
    _append_round(sess, "T", "Just a remark.", [])
    _append_round(sess, "N", None, ["Bash"])

    assert _sync_anchors_from_transcript(sess) is False
    assert "T" not in sess.stream_exact_anchor
    assert sess.stream_commentary == [(1, "Just a remark.")]
    assert sess.stream_tool_cursor == 1


def test_a_tick_corrects_an_anchor_and_marks_the_card_dirty(
    mk_bot, run_async, tmp_path,
):
    bot = mk_bot()
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._app.bot.send_chat_action = AsyncMock()
    sess = _sess(tmp_path, busy_msg_id=42)
    sess.record_tool("Bash: one", True)
    sess.record_tool("Bash: two", True)
    sess.stream_commentary = [(2, "Two checks.")]
    sess.stream_block_index = {"M": 0}
    sess.stream_dirty = False
    _append_round(sess, "M", "Two checks.", ["Bash", "Bash"])

    run_async(bot._animate_tick(sess, "Working", False))

    assert sess.stream_commentary == [(0, "Two checks.")]
    assert sess.stream_dirty is True
    bot._edit_busy_rich.assert_awaited()
