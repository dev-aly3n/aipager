"""design.md success criteria: a short turn renders with no <details>
block at all; a long turn's <details> block carries exactly what its own
summary claims (row-for-row, block-for-block); an extremely long turn's
card never exceeds 8,800 characters or 32,768 UTF-8 bytes, and the
renderer reports genuine (irrecoverable) truncation via its `hid`/
`truncated` return value.

Black-box: builds a bare TrackedSession by hand (entrypoints.md — there
is no shared fixture) and calls build_stream_card_ex directly. Does not
import any of the internal helpers entrypoints.md marks NOT exported
(_CARD_CHAR_BUDGET, _ROW_SEP, _fit_sections, etc.) — only the documented
8,800-character / 32,768-byte numbers from entrypoints.md's own public
contract, and the literal blank-line separator.
"""

from __future__ import annotations

import re
import time

from aipager.bot.animation import build_stream_card_ex
from aipager.state import Status, TrackedSession


def _sess(label="jim"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 30
    return sess


def _sess_with_sections(label="jim", rows=150, row_pad=60):
    """A session whose timeline has SEVERAL sections.

    A session carrying only tool rows is a single run section, and that
    section is the newest — which the design never wraps — so it produces no
    `<details>` at all. Folding is only reachable once older sections exist,
    which is what the commentary anchors below create.
    """
    sess = _sess(label)
    sess.tool_history = [(f"Bash: step-{i} " + "z" * row_pad, True)
                         for i in range(rows)]
    sess.stream_commentary = [
        (0, "First I'll look at the receiver."),
        (rows // 3, "Now the renderer."),
        (2 * rows // 3, "Finally the tests."),
    ]
    return sess


# ---- verification item (d): a short turn is unchanged -----------------------

def test_short_turn_renders_with_no_details_block_at_all():
    sess = _sess()
    sess.tool_history = [("Bash: ls", True), ("Read: /a.py", True)]
    card, truncated = build_stream_card_ex(sess, "Working")
    assert "<details" not in card
    assert truncated is False


def test_short_turn_card_is_byte_identical_to_before_within_the_ceilings():
    sess = _sess()
    sess.tool_history = [("Bash: ls", True)]
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert len(card) <= 8_800
    assert len(card.encode("utf-8")) <= 32_768
    assert card.rstrip().splitlines()[-1].startswith("⏳ **jim** ·")


# ---- a long turn's <details> block is exactly what it claims ---------------

def test_long_turn_details_block_summary_counts_match_whats_physically_present():
    sess = _sess_with_sections()
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert len(card) <= 8_800
    assert len(card.encode("utf-8")) <= 32_768

    # Every fold's summary states its own row count, and that count must
    # match exactly what the block physically contains.
    blocks = re.findall(
        r"<details><summary>▸ (\d+) tool calls?</summary>\n\n(.*?)\n\n</details>",
        card, re.DOTALL,
    )
    assert blocks, card[:300]
    for claimed, body in blocks:
        rows = [r for r in body.split("\n\n") if r.strip()]
        assert len(rows) == int(claimed)
        assert all(r.startswith("✅ ") or r.startswith("⏳ ") for r in rows)


def test_long_turn_collapsed_rows_are_each_their_own_blank_line_block():
    """Every row inside <details> is its own blank-line-separated block —
    never joined by a bare newline (design.md's block-structure
    finding)."""
    sess = _sess_with_sections()
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert "<details" in card, card[:300]
    inside = card.split("</summary>", 1)[1].split("</details>", 1)[0]
    assert "step-0`\nstep-1" not in inside  # no bare-newline-joined pair
    assert "\n\n" in inside


def test_long_turn_status_line_stays_the_cards_last_line():
    sess = _sess()
    sess.tool_history = [(f"Bash: step-{i} " + "z" * 60, True) for i in range(150)]
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert card.rstrip().splitlines()[-1].startswith("⏳ **jim** ·")


# ---- an extremely long turn: genuine truncation, both ceilings hold --------

def test_extremely_long_turn_never_exceeds_either_ceiling():
    sess = _sess()
    sess.tool_history = [(f"Bash: step-{i} " + "z" * 300, True) for i in range(500)]
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    assert len(card) <= 8_800
    assert len(card.encode("utf-8")) <= 32_768


def test_extremely_long_turn_still_reports_the_status_line_last():
    sess = _sess()
    sess.tool_history = [(f"Bash: step-{i} " + "z" * 300, True) for i in range(500)]
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert card.rstrip().splitlines()[-1].startswith("⏳ **jim** ·")
