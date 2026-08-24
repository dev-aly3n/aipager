"""Integration tests: streaming progression through the animation loop.

Covers the contract from entrypoints.md "Observable state — streaming progression",
restated for the chronological timeline card:

- A commentary block appears whole the moment it is read (no progressive reveal).
- Blocks render in the order they arrived, interleaved with the tool rows that
  ran between them.
- The card body never shrinks a block back out of view while a turn runs.
- Text written before the turn started never appears (offset seeding /
  cross-turn leakage guard) — tested for BOTH DM and group scope.
- A turn that produces no assistant text leaves stream_commentary == [] but the
  card still renders its header.

These are BLACK-BOX tests: we drive the loop via the public surface
(_read_stream_text, build_stream_card) and never read implementation internals.
"""

from __future__ import annotations

import json
import os
import time

from aipager.state import Status, TrackedSession
from aipager.bot.animation import (
    build_stream_card,
    _read_stream_text,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sess(label="dev", scope_kind="dm"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = scope_kind
    s.scope_chat_id = 12345 if scope_kind == "dm" else -100987654321
    s.busy_started_at = time.monotonic() - 5  # 5 s elapsed
    s.busy_msg_id = 42
    return s


def _write_assistant_entry(path, text: str, mode="w"):
    entry = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    }
    with open(path, mode) as f:
        f.write(json.dumps(entry) + "\n")


def _run_tool(path, sess, name: str, summary: str, done=True):
    """A tool call as it really lands: the hook appends the row, the transcript
    grows its tool_use block afterwards."""
    sess.tool_history.append((summary, done))
    entry = {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": "t", "name": name, "input": {}}],
            "stop_reason": "tool_use",
        },
    }
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _rows(card: str) -> list[str]:
    """The timeline rows, in order, without the header line."""
    # Contract change ("status-line-at-card-bottom"): the status line is
    # the card's LAST element, so strip it from the END.
    body, _, _status = card.rpartition("\n\n")
    return body.split("\n\n") if body else []


# ── SC-PROG-1: A block appears whole on the first read ───────────────────────

def test_block_appears_whole_on_first_read(tmp_path):
    """No progressive drip: one read makes the entire block visible."""
    p = tmp_path / "t.jsonl"
    text = "word " * 200  # 1000 chars — would once have taken several reveals
    _write_assistant_entry(p, text)
    sess = _sess()
    sess.stream_transcript_path = str(p)
    sess.stream_offset = 0

    assert _read_stream_text(sess) is True
    assert sess.stream_commentary == [(0, text.strip())]


# ── SC-PROG-2: Chronological interleaving through the real read path ─────────

def test_commentary_interleaves_with_tools_in_arrival_order(tmp_path):
    """Claude speaks, runs tools, speaks again — the card must preserve that order."""
    p = tmp_path / "t.jsonl"
    sess = _sess()
    sess.stream_transcript_path = str(p)
    sess.stream_offset = 0

    _write_assistant_entry(p, "Starting with the overall shape.")
    _read_stream_text(sess)

    _run_tool(p, sess, "Bash", "Bash: cloc aipager/")
    _run_tool(p, sess, "Glob", "Glob: x.py")

    _write_assistant_entry(p, "Layout is clear: five zones.", mode="a")
    _read_stream_text(sess)

    _run_tool(p, sess, "Read", "Read: animation.py", done=False)

    assert _rows(build_stream_card(sess, "Working")) == [
        "> Starting with the overall shape.",
        "✅ `Bash: cloc aipager/`",
        "✅ `Glob: x.py`",
        "> Layout is clear: five zones.",
        "⏳ `Read: animation.py`",
    ]


# ── SC-PROG-3: Body never loses a block mid-turn ─────────────────────────────

def test_body_does_not_shrink_across_reads(tmp_path):
    """Successive reads only append; earlier rows stay put while under the cap."""
    p = tmp_path / "t.jsonl"
    sess = _sess()
    sess.stream_transcript_path = str(p)
    sess.stream_offset = 0

    seen: list[int] = []
    for i in range(5):
        _write_assistant_entry(p, f"block {i}", mode="a")
        _read_stream_text(sess)
        seen.append(len(_rows(build_stream_card(sess, "Working"))))

    assert seen == [1, 2, 3, 4, 5]
    rows = _rows(build_stream_card(sess, "Working"))
    assert rows == [f"> block {i}" for i in range(5)]


# ── SC-PROG-4: Reading again with nothing new leaves the card unchanged ──────

def test_repeat_read_with_no_new_text_is_a_no_op(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_assistant_entry(p, "hello world")
    sess = _sess()
    sess.stream_transcript_path = str(p)
    sess.stream_offset = 0

    _read_stream_text(sess)
    before = build_stream_card(sess, "Working")

    assert _read_stream_text(sess) is False
    assert build_stream_card(sess, "Working") == before


# ── SC-PROG-5: Cross-turn leakage guard (DM scope) ───────────────────────────

def test_cross_turn_leakage_guard_dm(tmp_path):
    """entrypoints.md: Text written to the transcript before the turn started
    must never appear in the card. DM scope."""
    sess = _sess("dev", "dm")
    tp = tmp_path / "t.jsonl"

    # Write "previous turn" text
    _write_assistant_entry(str(tp), "Previous turn answer that must not leak")

    # Seed offset to current file size (simulating _send_busy_and_animate)
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = os.path.getsize(str(tp))

    # Now append the new turn's text
    _write_assistant_entry(str(tp), "New turn commentary", mode="a")

    _read_stream_text(sess)

    card = build_stream_card(sess, "Working")
    assert "Previous turn answer that must not leak" not in card, (
        "Cross-turn leakage: pre-turn text appeared in the card (DM)"
    )
    assert "New turn commentary" in card, (
        "New turn's text was not picked up (DM)"
    )


# ── SC-PROG-6: Cross-turn leakage guard (group scope) ────────────────────────

def test_cross_turn_leakage_guard_group(tmp_path):
    """entrypoints.md: Cross-turn leakage guard must work for group scope too.
    entrypoints.md states 'Applies identically to DM and group scopes; there
    must be no scope_kind branch left.'"""
    sess = _sess("team", "group")
    tp = tmp_path / "g.jsonl"

    # Write "previous turn" text
    _write_assistant_entry(str(tp), "Old group turn text — must not show")

    sess.stream_transcript_path = str(tp)
    sess.stream_offset = os.path.getsize(str(tp))

    # Append new turn text
    _write_assistant_entry(str(tp), "Group new turn commentary", mode="a")

    _read_stream_text(sess)

    card = build_stream_card(sess, "Working")
    assert "Old group turn text" not in card, (
        "Cross-turn leakage in group scope: pre-turn text appeared"
    )
    assert "Group new turn commentary" in card, (
        "New group turn text was not picked up"
    )


# ── SC-PROG-7: No assistant text → empty commentary → card still has a footer ─

def test_no_assistant_text_leaves_commentary_empty(tmp_path):
    """entrypoints.md: A turn that produces no assistant text at all leaves
    stream_commentary == []."""
    sess = _sess()
    tp = tmp_path / "empty.jsonl"
    # Write only a tool_use entry (no text content)
    entry = {"type": "assistant", "message": {
        "content": [{"type": "tool_use", "id": "tu1", "name": "Bash",
                      "input": {"command": "ls"}}],
        "stop_reason": "tool_use",
    }}
    tp.write_text(json.dumps(entry) + "\n")
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0

    assert _read_stream_text(sess) is False
    assert sess.stream_commentary == []


def test_no_assistant_text_card_still_has_a_header():
    """entrypoints.md: A turn that produces no assistant text still renders
    a card with its status line (not just an empty string)."""
    sess = _sess()
    sess.stream_commentary = []
    sess.tool_history = []

    card = build_stream_card(sess, "Working")

    assert "⏳" in card, "Status missing for no-text turn"
    assert "Working" in card, "Header missing for no-text turn"
    # An empty timeline leaves the header standing alone.
    assert _rows(card) == []


def test_tools_only_turn_still_renders_timeline():
    """The pre-stream shape — tool rows and nothing else — must still work."""
    sess = _sess()
    sess.tool_history = [("Read: a.py", True), ("Bash: ls", False)]
    assert _rows(build_stream_card(sess, "Working")) == [
        "✅ `Read: a.py`",
        "⏳ `Bash: ls`",
    ]


# ── Regression: successive text blocks must not be glued together ─────────────

def test_successive_blocks_are_separate_rows(tmp_path):
    """A block arriving after an earlier one must render as its own row.

    Regression: the old buffer appended blocks bare once it had drained,
    producing "...a subpackage.Layout is clear:" in a live turn.
    """
    p = tmp_path / "t.jsonl"
    _write_assistant_entry(p, "First block ends here.")
    sess = _sess()
    sess.stream_transcript_path = str(p)
    sess.stream_offset = 0

    _read_stream_text(sess)
    _write_assistant_entry(p, "Second block starts here.", mode="a")
    _read_stream_text(sess)

    card = build_stream_card(sess, "Working")
    assert "here.Second" not in card
    assert _rows(card) == [
        "> First block ends here.",
        "> Second block starts here.",
    ]


# ── Regression: prose must anchor BEFORE the tools it introduces ─────────────

def test_opening_prose_anchors_before_the_tools_it_introduces(
    mk_bot, run_async, tmp_path,
):
    """Claude narrates, then fires tools in the SAME assistant entry.

    Live regression (2026-08-04): Claude Code fires PreToolUse *before* it
    flushes the assistant entry, so by the time the prose is readable both
    tool rows already exist. Anchoring on the live tool count rendered a
    turn's opening line below the tools it was introducing. The anchor has
    to come from the transcript's own order instead.
    """
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0  # suppress the card edit; we only assert on state

    p = tmp_path / "t.jsonl"
    sess.stream_transcript_path = str(p)
    sess.stream_offset = 0

    # Both tool hooks fire off that one entry, back to back — and land before
    # anything of it is on disk.
    run_async(bot.notify(sess, "tool_use", {"tool_summary": "WebSearch: father"}))
    run_async(bot.notify(sess, "tool_use", {"tool_summary": "Bash: ls"}))

    entry = {"type": "assistant", "message": {
        "content": [
            {"type": "text", "text": "Three things — starting both lookups."},
            {"type": "tool_use", "id": "t1", "name": "WebSearch", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}},
        ],
        "stop_reason": "tool_use",
    }}
    p.write_text(json.dumps(entry) + "\n")
    _read_stream_text(sess)

    assert sess.stream_commentary == [(0, "Three things — starting both lookups.")], (
        "Opening prose was not anchored ahead of the tools it introduced"
    )
    assert _rows(build_stream_card(sess, "Working")) == [
        "> Three things — starting both lookups.",
        "⏳ `WebSearch: father`",
        "⏳ `Bash: ls`",
    ]
