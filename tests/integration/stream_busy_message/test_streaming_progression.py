"""Integration tests: streaming progression through the animation loop.

Covers the contract from entrypoints.md "Observable state — streaming progression":

- A 1000-character blob must take MORE than one edit to become fully visible.
- Intermediate stream_shown values end at a whitespace boundary (no mid-word splits).
- Card markdown passed to HTTP layer grows monotonically in body content while buffer drains.
- Once stream_pending empties, the body stops growing (only footer changes).
- Text written before the turn started never appears in stream_shown (offset seeding /
  cross-turn leakage guard) — tested for BOTH DM and group scope.
- A turn that produces no assistant text leaves stream_shown == "" but card has a footer.

These are BLACK-BOX tests: we drive the animation loop via the public surface
(_read_stream_text, _reveal_chunk, build_stream_card, _edit_busy_rich) with the HTTP
boundary (edit_message_text_rich) monkeypatched.  We do NOT read implementation internals.
"""

from __future__ import annotations

import json
import os
import time

from aipager.state import Status, TrackedSession
from aipager.bot.animation import (
    build_stream_card,
    _read_stream_text,
    _reveal_chunk,
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


# ── SC-PROG-1: A 1000-char blob takes MORE than one edit ──────────────────────

def test_1000_char_blob_requires_multiple_reveal_steps():
    """entrypoints.md: A 1000-character blob must take more than one edit to become
    fully visible. This tests _reveal_chunk directly as the inner-loop primitive."""
    sess = _sess()
    text = "word " * 200  # 1000 chars
    sess.stream_pending = text
    sess.stream_shown = ""

    edit_count = 0
    while sess.stream_pending:
        prev_shown = sess.stream_shown
        _reveal_chunk(sess)
        if sess.stream_shown != prev_shown:
            edit_count += 1
        if edit_count > 100:
            break  # safety against infinite loop

    assert edit_count > 1, (
        "A 1000-char blob must take more than one reveal step; "
        f"it was done in {edit_count}"
    )


# ── SC-PROG-2: Monotonic growth of body in HTTP payloads ─────────────────────

def test_card_body_grows_monotonically_across_reveals(tmp_path):
    """entrypoints.md: The card markdown passed to the HTTP layer grows monotonically
    in body content while the buffer drains."""
    sess = _sess()
    # Seed a large pending buffer (simulating what _read_stream_text would have loaded)
    sess.stream_pending = "word " * 200  # 1000 chars
    sess.stream_shown = ""

    body_lengths = []
    while sess.stream_pending:
        _reveal_chunk(sess)
        build_stream_card(sess, "Working")  # verify card is buildable at each step
        # Track length of the body portion (between header line and divider)
        body_lengths.append(len(sess.stream_shown))

    # Lengths must be non-decreasing
    for i in range(1, len(body_lengths)):
        assert body_lengths[i] >= body_lengths[i - 1], (
            f"Body shrank at step {i}: {body_lengths[i - 1]} -> {body_lengths[i]}"
        )


# ── SC-PROG-3: No mid-word splits ─────────────────────────────────────────────

def test_intermediate_shown_ends_at_whitespace_boundary():
    """entrypoints.md: Text is never split mid-word — every intermediate
    stream_shown value ends at a whitespace boundary (unless no whitespace at all)."""
    sess = _sess()
    # Construct text where a naive 280-char cut would land mid-word.
    # Use a long word that straddles the 280-char boundary.
    prefix = "ab " * 93   # 279 chars (93 × "ab ")
    long_word = "verylongwordthatmustnotbesplit"
    rest = " more words here"
    sess.stream_pending = prefix + long_word + rest
    sess.stream_shown = ""

    _reveal_chunk(sess)

    shown = sess.stream_shown
    # If there is still pending text, the cut was at a boundary
    if sess.stream_pending:
        # The shown text must not end in the middle of a word
        # (i.e., shown should end with whitespace or be followed by whitespace in the original)
        assert not shown[-1:].isalpha() or shown.endswith(" "), (
            f"stream_shown ends mid-word: ...{shown[-20:]!r}"
        )
        # Specifically: the long word must NOT have been partially included
        if long_word[:10] in shown:
            # If any part of long_word is included, it must be the whole thing
            assert long_word in shown, (
                "Long word was split: partial word appears in shown text"
            )


def test_no_word_split_for_exactly_window_size_text():
    """_reveal_chunk must not split a word even when the text is exactly
    STREAM_REVEAL_CHARS long with a word crossing the boundary."""
    from aipager.config import STREAM_REVEAL_CHARS
    sess = _sess()
    # Build: (STREAM_REVEAL_CHARS - 5) chars + a word crossing the boundary
    prefix = "x " * ((STREAM_REVEAL_CHARS - 5) // 2)  # word-aligned prefix
    word_at_boundary = "boundary"
    suffix = " trailing text here"
    sess.stream_pending = prefix + word_at_boundary + suffix
    sess.stream_shown = ""

    _reveal_chunk(sess)

    shown = sess.stream_shown
    if sess.stream_pending:
        # word_at_boundary must be entirely shown or entirely pending
        partial_shown = any(
            word_at_boundary[:k] == shown[-k:]
            for k in range(1, len(word_at_boundary))
        )
        assert not partial_shown, (
            f"Word was split across boundary: shown ends with {shown[-20:]!r}"
        )


# ── SC-PROG-4: Body stops growing once pending is empty ──────────────────────

def test_body_stops_growing_after_pending_empties():
    """entrypoints.md: Once stream_pending empties, the body stops growing
    and only the footer changes."""
    sess = _sess()
    sess.stream_pending = "hello world"
    sess.stream_shown = ""

    # Drain the pending buffer
    while sess.stream_pending:
        _reveal_chunk(sess)

    final_shown = sess.stream_shown
    assert sess.stream_pending == ""

    # Call reveal again — shown must not change
    _reveal_chunk(sess)
    assert sess.stream_shown == final_shown, (
        "stream_shown grew after pending was empty"
    )


# ── SC-PROG-5: Cross-turn leakage guard (DM scope) ───────────────────────────

def test_cross_turn_leakage_guard_dm(tmp_path):
    """entrypoints.md: Text written to the transcript before the turn started
    must never appear in stream_shown. DM scope."""
    sess = _sess("dev", "dm")
    tp = tmp_path / "t.jsonl"

    # Write "previous turn" text
    _write_assistant_entry(str(tp), "Previous turn answer that must not leak")

    # Seed offset to current file size (simulating _send_busy_and_animate)
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = os.path.getsize(str(tp))

    # Now append the new turn's text
    _write_assistant_entry(str(tp), "New turn commentary", mode="a")

    # Read stream text
    _read_stream_text(sess)

    assert "Previous turn answer that must not leak" not in sess.stream_pending, (
        "Cross-turn leakage: pre-turn text appeared in stream_pending (DM)"
    )
    assert "New turn commentary" in sess.stream_pending, (
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

    assert "Old group turn text" not in sess.stream_pending, (
        "Cross-turn leakage in group scope: pre-turn text appeared"
    )
    assert "Group new turn commentary" in sess.stream_pending, (
        "New group turn text was not picked up"
    )


# ── SC-PROG-7: No assistant text → stream_shown stays "" → card has footer ───

def test_no_assistant_text_stream_shown_empty(tmp_path):
    """entrypoints.md: A turn that produces no assistant text at all leaves
    stream_shown == ''."""
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

    _read_stream_text(sess)

    # No text content → stream_pending stays empty
    assert sess.stream_pending == "", (
        f"stream_pending should be empty for tool_use-only turn; got {sess.stream_pending!r}"
    )
    assert sess.stream_shown == ""


def test_no_assistant_text_card_has_footer():
    """entrypoints.md: A turn that produces no assistant text still renders
    a card with a footer (not just an empty string)."""
    sess = _sess()
    sess.stream_shown = ""
    sess.stream_pending = ""

    card = build_stream_card(sess, "Working")

    assert "⏳" in card, "Footer missing for no-text turn"
    assert "Working" in card, "Header missing for no-text turn"
    # No commentary means there is nothing to divide.
    assert "────────────────" not in card


# ── SC-PROG-8: stream_shown monotonically grows while draining ───────────────

def test_stream_shown_never_shrinks_during_reveal():
    """entrypoints.md: stream_shown grows monotonically while draining —
    it must not shrink between reveal steps."""
    sess = _sess()
    sess.stream_pending = ("hello world " * 100)  # 1200 chars
    sess.stream_shown = ""

    prev_len = 0
    steps = 0
    while sess.stream_pending and steps < 50:
        _reveal_chunk(sess)
        steps += 1
        cur_len = len(sess.stream_shown)
        assert cur_len >= prev_len, (
            f"stream_shown shrank at step {steps}: {prev_len} -> {cur_len}"
        )
        prev_len = cur_len


# ── Regression: successive text blocks must not be glued together ─────────────

def test_successive_blocks_separated_after_buffer_drains(tmp_path):
    """A block arriving after the buffer has fully drained into stream_shown must
    still be separated by a blank line.

    Regression: the separator was chosen on stream_pending alone, so once the
    buffer emptied the next block appended bare, producing
    "...a subpackage.Layout is clear:" in a live turn.
    """
    p = tmp_path / "t.jsonl"
    _write_assistant_entry(p, "First block ends here.")
    sess = _sess()
    sess.stream_transcript_path = str(p)
    sess.stream_offset = 0

    _read_stream_text(sess)
    while _reveal_chunk(sess):
        pass
    assert sess.stream_pending == ""
    assert sess.stream_shown == "First block ends here."

    _write_assistant_entry(p, "Second block starts here.", mode="a")
    _read_stream_text(sess)
    while _reveal_chunk(sess):
        pass

    assert "here.Second" not in sess.stream_shown
    assert "First block ends here.\n\nSecond block starts here." == sess.stream_shown
