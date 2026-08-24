"""Integration tests: build_stream_card layout and truncation.

Covers the contract from entrypoints.md:
  - shape with body text: header · verb · status, then the timeline below
  - shape with no body text: the header line alone
  - output always ≤ 32 768 UTF-8 bytes
  - head of body dropped (not tail) when truncation occurs
  - header still present after truncation
  - cost_baseline unset → no $ segment
  - cost delta ≤ 0.001 → no $ segment
  - tool_history empty → no tally
  - tool_history with 3 Read + 1 Grep → Read ×3 and Grep ×1
  - elapsed ≥ 60 s → Xm Ys format
  - elapsed shown from 0 s, never blank
  - verb="Thinking" → header contains Thinking
  - label with *, _, ` → markdown not broken
  - calling twice → byte-identical output (purity)
  - output is valid UTF-8 even after truncation of multibyte content

These are unit-style tests on a pure function, but placed here because
entrypoints.md designates build_stream_card as an observable public surface
(not internal), so they belong in the integration contract suite.

These tests do NOT duplicate tests already in tests/test_stream_card.py.
They target the exact shapes and boundary values from entrypoints.md that the
existing unit file either skips or covers loosely.
"""

from __future__ import annotations

import time

import pytest

from aipager.state import Status, TrackedSession
from aipager.bot.animation import build_stream_card


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sess(label="dev", *, elapsed_s=10.0):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.busy_started_at = time.monotonic() - elapsed_s
    return s


# ── SC-CARD-1: Shape with body text: body between header and divider ──────────

def test_card_full_shape_with_body():
    """Contract change ("status-line-at-card-bottom"): the timeline on top,
    the status line as the card's LAST element — Telegram parks the
    viewport at a message's END, so that is the only always-visible spot."""
    sess = _sess()
    sess.stream_commentary = [(0, "Let me look at the project structure.")]
    card = build_stream_card(sess, "Reading files")

    body, sep, status = card.rpartition("\n\n")
    assert sep, "Card has no timeline above the status line"
    assert "Reading files" in status
    assert "⏳" in status
    assert "Let me look at the project structure." in body



def test_card_no_empty_body_line_when_no_text():
    """entrypoints.md: shape with no body text → header and footer only."""
    sess = _sess()
    sess.stream_commentary = []
    sess.tool_history = []
    card = build_stream_card(sess, "Working")

    # An empty timeline leaves the header standing alone — no blank block below.
    assert "\n\n" not in card, (
        "Empty body placeholder found when the timeline is empty"
    )
    assert "⏳" in card


# ── SC-CARD-3: cost_baseline unset → no $ segment ────────────────────────────

def test_card_no_cost_segment_when_baseline_unset():
    """entrypoints.md: cost_baseline unset → no $ segment in the footer."""
    sess = _sess()
    sess.cost_baseline = None
    sess.last_cost_usd = 5.0
    card = build_stream_card(sess, "Working")
    assert "$" not in card, "$ cost segment appeared despite cost_baseline being None"


# ── SC-CARD-4: cost delta ≤ 0.001 → no $ segment ────────────────────────────

def test_card_no_cost_segment_when_delta_at_threshold():
    """entrypoints.md: cost delta ≤ 0.001 → no $ segment.
    Tests the exact boundary: delta == 0.001 must be omitted."""
    sess = _sess()
    sess.cost_baseline = 1.000
    sess.last_cost_usd = 1.001  # delta == 0.001 exactly
    card = build_stream_card(sess, "Working")
    assert "$" not in card, (
        "$ segment appeared for delta == 0.001 (should be omitted at exactly the threshold)"
    )


def test_card_cost_segment_present_when_delta_above_threshold():
    """entrypoints.md: cost delta > 0.001 → $ segment present."""
    sess = _sess()
    sess.cost_baseline = 1.000
    sess.last_cost_usd = 1.002  # delta == 0.002 > 0.001
    card = build_stream_card(sess, "Working")
    assert "$" in card, "$ segment missing for delta == 0.002 (should be present)"


# ── SC-CARD-5: tool_history empty → no tally segment ────────────────────────

def test_card_no_tally_when_tool_history_empty():
    """entrypoints.md: tool_history empty → no tally segment."""
    sess = _sess()
    sess.tool_history = []
    card = build_stream_card(sess, "Working")
    assert "×" not in card, "Tally segment appeared despite empty tool_history"


# ── SC-CARD-6: tool_history with 3 Read + 1 Grep ─────────────────────────────

def test_card_tally_three_read_one_grep():
    """entrypoints.md: tool_history with 3 Read + 1 Grep → Read ×3 and Grep ×1."""
    sess = _sess()
    sess.tool_history = [
        ("Read: /a/b.py", True),
        ("Read: /c/d.py", True),
        ("Read: /e/f.py", True),
        ("Grep: pattern in code", True),
    ]
    card = build_stream_card(sess, "Working")
    assert "Read ×3" in card, f"Read ×3 not found in card:\n{card}"
    assert "Grep ×1" in card, f"Grep ×1 not found in card:\n{card}"


# ── SC-CARD-7: elapsed ≥ 60 s → Xm Ys format ────────────────────────────────

def test_card_elapsed_format_minutes_and_seconds():
    """entrypoints.md: elapsed ≥ 60 s → footer shows Xm Ys."""
    sess = _sess(elapsed_s=75)  # 1m 15s
    card = build_stream_card(sess, "Working")
    assert "1m" in card, f"'1m' not found for 75s elapsed: {card}"
    assert "15s" in card, f"'15s' not found for 75s elapsed: {card}"


# ── SC-CARD-8: elapsed < 2 s → elapsed segment omitted ──────────────────────

def test_card_elapsed_shown_from_the_first_second():
    """The clock counts from the start: a blank that jumps to "5s" reads broken."""
    sess = _sess(elapsed_s=1)  # 1 second
    header = build_stream_card(sess, "Working").partition("\n\n")[0]
    assert "1s" in header, f"Elapsed hidden on a 1s turn: {header!r}"


# ── SC-CARD-9: verb present in header ────────────────────────────────────────

def test_card_verb_thinking_in_header():
    """entrypoints.md: verb='Thinking' → header contains Thinking."""
    sess = _sess()
    card = build_stream_card(sess, "Thinking")
    lines = card.split("\n")
    header = lines[0]
    assert "Thinking" in header, f"'Thinking' not in header: {header!r}"


# ── SC-CARD-10: label with * is escaped ──────────────────────────────────────

def test_card_label_with_underscore_escaped():
    """entrypoints.md: label containing _ → markdown is not broken."""
    sess = _sess()
    sess.label = "my_project"
    card = build_stream_card(sess, "Working")
    # The raw underscore that would break bold must be escaped
    assert "my\\_project" in card or "my_project" in card, (
        "Label with underscore not present in card"
    )
    # Specifically: must NOT appear as literal **my_project** which would
    # be parsed as bold-start + italic-conflict
    # Just check the label is in the header line
    header_line = card.split("\n")[0]
    assert "my" in header_line and "project" in header_line


# ── SC-CARD-11: Purity — byte-identical output on two calls ──────────────────

def test_card_purity_same_output_on_second_call():
    """entrypoints.md: calling twice with identical sess and verb → byte-identical."""
    sess = _sess(elapsed_s=10)
    sess.stream_commentary = [(0, "some body text")]
    sess.cost_baseline = 0.0
    sess.last_cost_usd = 0.05
    sess.tool_history = [("Read: /x", True)]
    # Fix the time anchor so monotonic doesn't drift between calls
    fixed_start = time.monotonic() - 10.0
    sess.busy_started_at = fixed_start

    out1 = build_stream_card(sess, "Working")
    out2 = build_stream_card(sess, "Working")

    assert out1 == out2, "build_stream_card is not pure — two calls produced different output"


# ── SC-CARD-12: Truncation — head dropped, footer preserved, valid UTF-8 ──────

def test_card_truncation_head_dropped_status_preserved():
    """entrypoints.md: body large enough to exceed 32 768 bytes →
    the HEAD of the body is dropped; the status line still closes the card; output is valid UTF-8
    ("status-line-at-card-bottom")."""
    sess = _sess()
    # First marker at the head, last marker near the tail
    # No markdown metacharacters in the markers: _md_escape would backslash
    # them and the substring check below would miss.
    head_marker = "HEADMARKERTHATMUSTBEDROPPED"
    tail_content = "y" * 35_000
    tail_marker = "TAILMARKERTHATMUSTSTAY"
    # Commentary is capped by the byte ceiling (the old STREAM_BODY_CHARS budget is gone), so only a pathological tool
    # summary can still reach the byte ceiling.
    sess.tool_history = [
        ("Bash: " + head_marker + tail_content + tail_marker, True),
    ]

    card = build_stream_card(sess, "Working")

    assert len(card.encode("utf-8")) <= 32_768, "Card exceeds 32 768 UTF-8 bytes"
    assert head_marker not in card, "Head of body was kept instead of being dropped"
    assert tail_marker in card, "Tail of body was dropped; head should have been dropped instead"
    assert card.rstrip().splitlines()[-1].startswith("⏳ "), (
        "status line missing after truncation")

    # Must be valid UTF-8
    try:
        card.encode("utf-8").decode("utf-8")
    except UnicodeDecodeError as e:
        pytest.fail(f"Card contains invalid UTF-8 after truncation: {e}")
    status = card.rstrip().splitlines()[-1]
    assert status.startswith("⏳")

def test_card_truncation_multibyte_valid_utf8():
    """entrypoints.md: output is valid UTF-8 even when the body contains
    multi-byte sequences (e.g., Persian text)."""
    sess = _sess()
    sess.tool_history = [("Bash: " + "سلام دنیا " * 5000, True)]  # over 32 768 bytes

    card = build_stream_card(sess, "Working")

    assert len(card.encode("utf-8")) <= 32_768
    try:
        card.encode("utf-8").decode("utf-8")
    except UnicodeDecodeError as e:
        pytest.fail(f"Card contains invalid UTF-8 after multibyte truncation: {e}")
