"""design.md: "That helper must strip every block (re.sub with DOTALL,
all matches), keeping each summary as a bare line — pinned by a test
with 2+ blocks." entrypoints.md: "a card with 2+ blocks is the
regression case to test here."

Distinct from the Developer's own
``tests/integration/collapse-busy-card-timeline/test_fallback_degrade.py``:
this test uses THREE independent blocks with three DIFFERENT row counts
(so the summaries are individually distinguishable, not just repeats of
the same number) and asserts every one of the three summary lines
survives, in chronological order, with no raw markup left anywhere —
error-guessing the case where a strip implementation only handles the
first match or gets block ordering wrong.
"""

from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock

from aipager.bot.animation import build_stream_card_ex
from aipager.bot.rich_message import RichMessageFallbackRequired
from aipager.state import Status, TrackedSession


def _sess(label="jim"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    return sess


def _sess_with_three_distinct_blocks():
    sess = _sess()
    rows = []
    commentary = []
    # three older sections with DIFFERENT row counts (3, 4, 5) so their
    # <details> summaries are individually distinguishable.
    for n in (3, 4, 5):
        commentary.append((len(rows), f"Phase of {n} rows."))
        for i in range(n):
            rows.append((f"Bash: n{n}-{i}", True))
    commentary.append((len(rows), "Newest phase."))
    rows.append(("Bash: newest", True))
    sess.tool_history = rows
    sess.stream_commentary = commentary
    return sess


def test_rich_markdown_has_exactly_three_blocks_before_fallback():
    """Sanity precondition: confirms the fixture actually produces 3
    distinct blocks with 3, 4, 5 as their summary counts, in that
    chronological order, before testing the degrade path against it."""
    sess = _sess_with_three_distinct_blocks()
    card, _truncated = build_stream_card_ex(sess, "Working")
    counts = re.findall(r"<details><summary>▸ (\d+) tool calls?</summary>", card)
    assert counts == ["3", "4", "5"], card


def test_fallback_strips_all_three_blocks_and_keeps_every_summary_line(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _sess_with_three_distinct_blocks()

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageFallbackRequired("transport changed")),
    )
    bot._app.bot.edit_message_text = AsyncMock()

    result = run_async(bot._edit_busy_rich(sess, "Working"))

    assert result is True
    text = bot._app.bot.edit_message_text.await_args.args[0]
    assert "<details" not in text
    assert "</details>" not in text
    assert "<summary" not in text
    assert "</summary>" not in text
    for n in ("3", "4", "5"):
        assert f"▸ {n} tool call" in text


def test_fallback_stripped_summaries_stay_in_chronological_order(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _sess_with_three_distinct_blocks()

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageFallbackRequired("transport changed")),
    )
    bot._app.bot.edit_message_text = AsyncMock()

    run_async(bot._edit_busy_rich(sess, "Working"))

    text = bot._app.bot.edit_message_text.await_args.args[0]
    positions = [text.index(f"▸ {n} tool call") for n in ("3", "4", "5")]
    assert positions == sorted(positions), text
