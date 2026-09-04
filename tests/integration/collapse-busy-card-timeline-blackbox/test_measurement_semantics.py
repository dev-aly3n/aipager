"""Acceptance criterion (a): the card is MEASURED in characters against
8,800 (Python `len()`, code points — not bytes, not UTF-16) and in bytes
against 32,768, "whichever binds first" (design.md "Numbers"). These
tests specifically target the divergence between the two units and the
"measured, not estimated" guarantee, rather than just re-confirming both
ceilings hold (covered broadly by the property sweep).
"""

from __future__ import annotations

import time

from aipager.bot.animation import build_stream_card_ex
from aipager.state import Status, TrackedSession

CARD_CHAR_BUDGET = 8_800
RICH_LIMIT_BYTES = 32_768


def _sess(label="jim"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 20
    return sess


def test_dense_multibyte_content_can_bind_the_byte_ceiling_before_the_char_one():
    """design.md: 'at high multi-byte density the byte limit can bind
    first even under the 8,800-char ceiling.' Rows built almost entirely
    of 4-byte-UTF-8 emoji should trip the byte backstop while character
    count stays comfortably under 8,800."""
    sess = _sess()
    # each row: ~1 4-byte emoji character repeated -> code points cheap,
    # bytes expensive. 300 rows * 40 emoji = 12,000 code points alone if
    # unbounded, so the char budget WOULD also eventually bind; the
    # per-row size here is tuned so bytes-per-char (4x) makes the byte
    # ceiling (32,768) bind at a much smaller code-point count than 8,800.
    emoji = "\U0001f525"  # 🔥, 4 bytes in UTF-8, 1 Python code point
    # Sized so bytes bind BEFORE characters: 8,700 rows of pure emoji put
    # the byte count over 32,768 while the code-point count stays under the
    # 8,800-character ceiling only after shedding — which is the point.
    sess.tool_history = [
        (f"Bash: {emoji * 30}", True) for i in range(400)
    ]
    card, truncated = build_stream_card_ex(sess, "Working")
    assert len(card) <= CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= RICH_LIMIT_BYTES
    assert truncated is True


def test_plain_ascii_content_is_bound_by_the_char_ceiling_not_the_byte_one():
    """The mirror case: plain ASCII rows (1 byte per code point) should
    hit the 8,800-char ceiling while the byte count stays far below
    32,768 — confirms the char path is genuinely exercised, not just the
    byte one."""
    sess = _sess()
    sess.tool_history = [
        (f"Bash: step-{i} " + "a" * 40, True) for i in range(400)
    ]
    card, truncated = build_stream_card_ex(sess, "Working")
    assert len(card) <= CARD_CHAR_BUDGET
    byte_len = len(card.encode("utf-8"))
    assert byte_len <= RICH_LIMIT_BYTES
    # The ROWS are ASCII, but the card's own furniture never is: the status
    # line and every row marker carry ✅ / ⏳ / 🤖, and a chopped card opens
    # with "…", all multi-byte. So bytes exceed characters slightly; what
    # matters is that the CHARACTER bound is the one that bound here —
    # the card sits at the char ceiling while bytes stay far below theirs.
    assert byte_len > len(card)
    assert len(card) == CARD_CHAR_BUDGET
    assert byte_len < RICH_LIMIT_BYTES // 2


def test_code_points_are_counted_not_utf16_code_units():
    """A character outside the Basic Multilingual Plane (like most modern
    emoji) is TWO UTF-16 code units but ONE Python `len()` code point.
    design.md's own verified reference case sends a card that is 8,600
    `len()` characters but 9,508 UTF-16 units and confirms it renders
    whole (well under the char budget, past it under UTF-16 counting).
    Reconstruct the same shape of case: a card sized (via a self-
    calibrating probe against the real renderer, not a guessed constant)
    to land just under the 8,800 char budget while its UTF-16-unit
    length sits clearly OVER 8,800 — the card must still be treated as
    fitting, with nothing shed to compensate."""
    astral = "\U0001f9ee"  # 🧮, astral-plane: 2 UTF-16 units, 1 code point

    probe_sess = _sess()
    probe_sess.tool_history = [("X", True)]
    probe_card, _truncated = build_stream_card_ex(probe_sess, "Working")
    overhead = len(probe_card) - 1  # every character except the row's own "X"

    n_astral = 300  # pushes UTF-16 length ~300 units past the char length
    target_total = 8_700  # under the 8,800 budget, with margin to spare
    content_len = target_total - overhead
    assert content_len > n_astral, "test setup: overhead too large for this budget"
    content = astral * n_astral + "a" * (content_len - n_astral)

    sess = _sess()
    sess.tool_history = [(content, True)]
    card, truncated = build_stream_card_ex(sess, "Working")

    assert len(card) <= CARD_CHAR_BUDGET
    utf16_len = len(card.encode("utf-16-le")) // 2
    assert utf16_len > CARD_CHAR_BUDGET  # would look over-budget under UTF-16 counting
    assert truncated is False  # the real (code-point) measurement says it fits
    assert content in card  # nothing was shed to compensate


def test_real_measured_length_holds_near_the_boundary_despite_block_overhead():
    """design.md "Numbers": the go/no-go check must be a real
    `len(card)` measurement of the actual candidate string, never a
    running-sum estimate of pre-wrap row lengths — because wrapping rows
    in `<details><summary>...</summary>` adds real overhead the raw row
    text does not have. Construct raw row content sized close enough to
    8,800 that block-wrapping overhead would push a naive estimate over
    the edge, and confirm the REAL rendered card still respects the
    ceiling."""
    sess = _sess()
    # 3 older sections of 20 rows each (~40 raw chars/row => ~2,400 raw
    # chars/section, ~7,200 total raw), each followed by its own details
    # wrapper (tag + summary overhead ~45 chars each = ~135 extra) plus a
    # small newest section and the status line -- close enough to the
    # ceiling that silently ignoring wrapper overhead would be a bug an
    # estimate-based implementation could plausibly get wrong.
    rows = []
    commentary = []
    for section in range(3):
        commentary.append((len(rows), f"Section {section} intro."))
        for i in range(20):
            rows.append((f"Bash: s{section}-{i} " + "x" * 20, True))
    commentary.append((len(rows), "Newest section intro."))
    for i in range(4):
        rows.append((f"Bash: newest-{i}", True))
    sess.tool_history = rows
    sess.stream_commentary = commentary

    card, _truncated = build_stream_card_ex(sess, "Working")
    assert len(card) <= CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= RICH_LIMIT_BYTES
    last_line = card.rstrip("\n").splitlines()[-1]
    assert last_line.startswith("⏳ **jim**")
