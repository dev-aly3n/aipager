"""Unit tests for the streaming card and buffer helpers.

Covers build_stream_card (pure function), _read_stream_text, and the
_edit_busy_rich method on AnimationMixin.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession
from aipager.bot.animation import (
    build_stream_card,
    build_stream_card_ex,
    _CARD_CHAR_BUDGET,
    _build_details_block,
    _fit_sections,
    _read_stream_text,
    _ROW_SEP,
    _strip_details_tags,
    _tool_fold_summary,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def run_async():
    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)
    return _run


def _sess(label="dev", scope="dm"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = scope
    s.scope_chat_id = 12345 if scope == "dm" else -100
    s.busy_started_at = time.monotonic() - 10  # 10s ago
    return s


# ── build_stream_card: layout ─────────────────────────────────────────────────

def test_card_header_contains_verb():
    sess = _sess()
    card = build_stream_card(sess, "Thinking")
    assert "Thinking" in card


def test_card_header_contains_label():
    sess = _sess("myproj")
    card = build_stream_card(sess, "Working")
    assert "myproj" in card


def test_card_with_no_body_is_the_header_alone():
    sess = _sess()
    sess.stream_commentary = []
    sess.tool_history = []
    card = build_stream_card(sess, "Working")
    # No trailing blank lines standing in for an empty body.
    assert "\n\n" not in card
    assert "⏳" in card


def test_card_with_body_contains_body():
    sess = _sess()
    sess.stream_commentary = [(0, "Here is some prose.")]
    card = build_stream_card(sess, "Working")
    assert "Here is some prose." in card


def test_card_body_appears_above_the_status_line():
    """Contract change ("status-line-at-card-bottom"): the status line is
    the card's LAST element — Telegram parks the viewport at a message's
    END, so a top header scrolls away exactly when a long turn needs it."""
    sess = _sess()
    sess.stream_commentary = [(0, "body text")]
    card = build_stream_card(sess, "Working")
    body, _, status = card.rpartition("\n\n")
    assert "⏳" in status
    assert "body text" in body


def test_card_status_rides_on_the_last_line():
    """Elapsed, cost and tally sit on the card's LAST line
    ("status-line-at-card-bottom") — not in a header above the timeline."""
    sess = _sess()
    sess.busy_started_at = time.monotonic() - 7
    sess.cost_baseline = 0.0
    sess.last_cost_usd = 0.25
    sess.tool_history = [("Bash: ls", True)]
    sess.stream_commentary = [(0, "body text")]
    status = build_stream_card(sess, "Working").rpartition("\n\n")[2]
    assert status == "⏳ **dev** · Working · 7s · $0.25 · Bash ×1"


# ── build_stream_card: status-line segments ─────────────────────────────────


def test_card_elapsed_shown_when_ge_2s():
    sess = _sess()
    sess.busy_started_at = time.monotonic() - 5
    card = build_stream_card(sess, "Working")
    assert "5s" in card


def test_card_elapsed_shown_from_the_first_second():
    """The clock counts from 0s. It used to appear blank until 2s, then jump."""
    sess = _sess()
    sess.busy_started_at = time.monotonic() - 1
    assert "1s" in build_stream_card(sess, "Working")
    sess.busy_started_at = time.monotonic()
    assert "0s" in build_stream_card(sess, "Working")


def test_card_elapsed_format_minutes():
    sess = _sess()
    sess.busy_started_at = time.monotonic() - 125
    card = build_stream_card(sess, "Working")
    assert "2m" in card


def test_card_cost_shown_when_delta_above_threshold():
    sess = _sess()
    sess.cost_baseline = 1.0
    sess.last_cost_usd = 1.05
    card = build_stream_card(sess, "Working")
    assert "$0.05" in card


def test_card_cost_omitted_when_baseline_unset():
    sess = _sess()
    sess.cost_baseline = None
    sess.last_cost_usd = 1.0
    card = build_stream_card(sess, "Working")
    assert "$" not in card


def test_card_cost_omitted_when_delta_le_threshold():
    sess = _sess()
    sess.cost_baseline = 1.0
    sess.last_cost_usd = 1.001  # delta == 0.001 → omit
    card = build_stream_card(sess, "Working")
    assert "$" not in card


def test_card_tool_tally_shown():
    sess = _sess()
    sess.tool_history = [
        ("Read: /a/b.py", True),
        ("Read: /c/d.py", True),
        ("Read: /e/f.py", True),
        ("Grep: pattern in aipager", True),
    ]
    card = build_stream_card(sess, "Working")
    assert "Read ×3" in card
    assert "Grep ×1" in card


def test_card_tool_tally_names_have_no_trailing_colon():
    """Summaries arrive as "Read: /path" — the colon must not leak into the tally."""
    sess = _sess()
    sess.tool_history = [("Bash: run the tests", True)]
    card = build_stream_card(sess, "Working")
    assert "Bash ×1" in card
    assert "Bash: ×1" not in card


def test_card_tool_tally_handles_subagent_summaries():
    """"agent activity rows on the busy card" (requirement 3) changes this
    from the old behavior: agent rows (live or settled) never contribute a
    "🤖 ×N" tally segment — their tool calls are counted on the agent's own
    row, not the parent's tally. Companion of
    test_status_line_tally_excludes_settled_agent_rows below."""
    sess = _sess()
    sess.tool_history = [("\U0001f916 general-purpose", False),
                         ("\U0001f916 Explore", True)]
    card = build_stream_card(sess, "Working")
    assert "\U0001f916 ×" not in card


def test_card_tool_tally_omitted_when_empty():
    sess = _sess()
    sess.tool_history = []
    card = build_stream_card(sess, "Working")
    # Header exists but carries no tally segment (format is "Name ×N")
    assert "×" not in card.partition("\n\n")[0]


# ── build_stream_card: label escaping ────────────────────────────────────────

def test_card_label_asterisk_escaped():
    sess = _sess()
    sess.label = "my*project"
    card = build_stream_card(sess, "Working")
    # The raw asterisk must not appear unescaped in the header
    # (it would break the **label** bold formatting)
    assert "my\\*project" in card


def test_card_label_backtick_escaped():
    sess = _sess()
    sess.label = "my`project"
    card = build_stream_card(sess, "Working")
    assert "my\\`project" in card


# ── build_stream_card: purity ─────────────────────────────────────────────────

def test_card_is_pure_identical_output():
    sess = _sess()
    sess.stream_commentary = [(0, "some text")]
    sess.busy_started_at = 1000.0  # fixed monotonic
    sess.cost_baseline = 0.0
    sess.last_cost_usd = 0.0
    # Call twice — output must be byte-identical
    out1 = build_stream_card(sess, "Thinking")
    out2 = build_stream_card(sess, "Thinking")
    assert out1 == out2


def test_card_does_not_mutate_sess():
    sess = _sess()
    sess.stream_commentary = [(0, "text")]
    sess.tool_history = [("Read: /a.py", True)]
    commentary_before = list(sess.stream_commentary)
    history_before = list(sess.tool_history)
    build_stream_card(sess, "Working")
    assert sess.stream_commentary == commentary_before
    assert sess.tool_history == history_before


# ── build_stream_card: truncation ────────────────────────────────────────────

def test_card_truncation_output_within_limit():
    sess = _sess()
    # One pathological tool summary is the only way past the byte ceiling now
    # that commentary is capped by the byte ceiling (the old STREAM_BODY_CHARS budget is gone).
    sess.tool_history = [("Bash: " + "x" * 40_000, True)]
    card = build_stream_card(sess, "Working")
    assert len(card.encode("utf-8")) <= 32_768


def test_card_truncation_status_line_preserved():
    """Even when one giant row forces the byte backstop, the status line
    is appended after truncation and stays the last line
    ("status-line-at-card-bottom")."""
    sess = _sess()
    sess.tool_history = [("Bash: " + "y" * 40_000, True)]
    card = build_stream_card(sess, "Working")
    assert len(card.encode("utf-8")) <= 32768
    status = card.rstrip().splitlines()[-1]
    assert status.startswith("⏳ **dev** · Working ·")
    assert status.endswith("Bash ×1")


def test_card_truncation_head_dropped():
    sess = _sess()
    # The head of the body is "FIRST" and the tail is "LAST"
    sess.tool_history = [("Bash: FIRST " + "middle " * 5000 + "LAST", True)]
    card = build_stream_card(sess, "Working")
    # The head should have been dropped
    assert "FIRST" not in card
    assert "LAST" in card


def test_card_truncation_valid_utf8():
    sess = _sess()
    # Persian text repeated to exceed the limit
    sess.tool_history = [("Bash: " + "سلام دنیا " * 4000, True)]
    card = build_stream_card(sess, "Working")
    assert len(card.encode("utf-8")) <= 32_768
    card.encode("utf-8")  # must not raise


# ── _read_stream_text ─────────────────────────────────────────────────────────

def _write_assistant(tmp_path, text: str) -> str:
    entry = {"type": "assistant", "message": {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }}
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(entry) + "\n")
    return str(p)


def test_read_stream_text_returns_true_when_new_text(tmp_path):
    sess = _sess()
    tp = _write_assistant(tmp_path, "new commentary")
    sess.stream_transcript_path = tp
    sess.stream_offset = 0
    result = _read_stream_text(sess)
    assert result is True
    assert sess.stream_commentary == [(0, "new commentary")]


def test_read_stream_text_returns_false_when_no_path():
    sess = _sess()
    sess.stream_transcript_path = ""
    result = _read_stream_text(sess)
    assert result is False


def test_read_stream_text_returns_false_when_no_new_text(tmp_path):
    tp = _write_assistant(tmp_path, "old text")
    sess = _sess()
    sess.stream_transcript_path = tp
    sess.stream_offset = os.path.getsize(tp)  # seeded to file size
    result = _read_stream_text(sess)
    assert result is False
    assert sess.stream_commentary == []


def test_read_stream_text_anchors_each_block_separately(tmp_path):
    """Blocks read at different tool counts must anchor where they arrived."""
    tp = _write_assistant(tmp_path, "first block")
    sess = _sess()
    sess.stream_transcript_path = tp
    sess.stream_offset = 0
    _read_stream_text(sess)
    # A tool runs, then Claude speaks again.
    sess.tool_history.append(("Read: /a.py", True))
    entry2 = {"type": "assistant", "message": {
        "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
            {"type": "text", "text": "second block"},
        ],
        "stop_reason": "end_turn",
    }}
    with open(tp, "a") as f:
        f.write(json.dumps(entry2) + "\n")
    _read_stream_text(sess)
    assert sess.stream_commentary == [(0, "first block"), (1, "second block")]


def test_read_stream_text_splits_multi_block_read(tmp_path):
    """read_turn_text joins blocks with a blank line — they become separate rows."""
    entry = {"type": "assistant", "message": {
        "content": [
            {"type": "text", "text": "alpha"},
            {"type": "text", "text": "beta"},
        ],
        "stop_reason": "end_turn",
    }}
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(entry) + "\n")
    sess = _sess()
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0
    _read_stream_text(sess)
    assert sess.stream_commentary == [(0, "alpha"), (0, "beta")]


def test_read_stream_text_advances_offset(tmp_path):
    tp = _write_assistant(tmp_path, "content")
    sess = _sess()
    sess.stream_transcript_path = tp
    sess.stream_offset = 0
    _read_stream_text(sess)
    assert sess.stream_offset > 0


# ── build_stream_card: chronological interleaving ────────────────────────────

def _body(card: str) -> list[str]:
    """The timeline rows, in order, without the status line.

    Contract change ("status-line-at-card-bottom"): the status line is the
    card's LAST element, not its first — a top header scrolled out of
    sight exactly when a turn grew long enough to need it.
    """
    body, _, _status = card.rpartition("\n\n")
    return body.split("\n\n") if body else []


def test_timeline_interleaves_commentary_between_tools():
    sess = _sess()
    sess.tool_history = [("Bash: cloc aipager/", True), ("Glob: x.py", True)]
    sess.stream_commentary = [(0, "Starting with the shape."), (2, "Layout is clear.")]
    rows = _body(build_stream_card(sess, "Working"))
    assert rows == [
        "> Starting with the shape.",
        "✅ `Bash: cloc aipager/`",
        "✅ `Glob: x.py`",
        "> Layout is clear.",
    ]


def test_timeline_commentary_mid_run_lands_between_rows():
    sess = _sess()
    sess.tool_history = [("Bash: a", True), ("Bash: b", True), ("Bash: c", False)]
    sess.stream_commentary = [(1, "after the first tool")]
    rows = _body(build_stream_card(sess, "Working"))
    assert rows[1] == "> after the first tool"
    assert rows[0] == "✅ `Bash: a`"
    assert rows[2] == "✅ `Bash: b`"


def test_timeline_rows_separated_by_blank_line():
    """Telegram collapses a lone newline — rows need a blank line between them."""
    sess = _sess()
    sess.tool_history = [("Bash: a", True), ("Bash: b", True)]
    card = build_stream_card(sess, "Working")
    assert "✅ `Bash: a`\n\n✅ `Bash: b`" in card


def test_timeline_failed_and_running_markers():
    sess = _sess()
    sess.tool_history = [("Bash: a", "failed"), ("Read: b.py", False)]
    rows = _body(build_stream_card(sess, "Working"))
    assert rows == ["❌ `Bash: a`", "⏳ `Read: b.py`"]


def test_timeline_tool_summary_goes_in_a_code_span():
    """A glob like **/*.py must not reformat everything after it. Inside a code
    span it is literal, so it needs no backslashes either."""
    sess = _sess()
    sess.tool_history = [("Glob: **/*.py", True)]
    rows = _body(build_stream_card(sess, "Working"))
    assert rows == ["✅ `Glob: **/*.py`"]


def test_timeline_tool_summary_with_backticks_keeps_its_span_intact():
    """A summary of its own backticks must not break out of the code span.

    The fence grows past the longest run inside, and the padding spaces are
    symmetric — CommonMark strips one from each side, so a summary ending in a
    backtick survives verbatim.
    """
    sess = _sess()
    sess.tool_history = [("Bash: echo `date`", True)]
    rows = _body(build_stream_card(sess, "Working"))
    assert rows == ["✅ `` Bash: echo `date` ``"]


def test_timeline_commentary_left_unescaped():
    """Claude's prose is meant to render its own markdown."""
    sess = _sess()
    sess.stream_commentary = [(0, "the **bot/** package")]
    rows = _body(build_stream_card(sess, "Working"))
    assert rows == ["> the **bot/** package"]


def test_timeline_collapses_old_tools_into_count():
    # Contract change ("layered-card-shedding"): fit-driven, not window-
    # or budget-driven; commentary outlives tool rows.
    # The old fixed 15-tool visible window is gone: 25 short rows fit the
    # byte ceiling, so all render and no counter appears.
    sess = _sess()
    sess.tool_history = [(f"Bash: t-{i}", True) for i in range(25)]
    rows = _body(build_stream_card(sess, "Working"))
    assert not any("earlier tool" in r for r in rows)
    assert any("t-0" in r for r in rows) and any("t-24" in r for r in rows)


def test_timeline_hidden_commentary_reanchors_to_window_top():
    """Commentary anchored to a scrolled-off tool must still show, not vanish."""
    sess = _sess()
    sess.tool_history = [(f"Read: f{i}.py", True) for i in range(20)]
    sess.stream_commentary = [(1, "said this very early")]
    rows = _body(build_stream_card(sess, "Working"))
    assert "> said this very early" in rows
    # It lands at the top of the visible window, right after the earlier-tools note.
    assert rows.index("> said this very early") == 1


def test_timeline_anchor_past_history_end_renders_last():
    """A block read before its tool row was appended must not be dropped."""
    sess = _sess()
    sess.tool_history = [("Bash: a", True)]
    sess.stream_commentary = [(9, "trailing")]
    rows = _body(build_stream_card(sess, "Working"))
    assert rows == ["✅ `Bash: a`", "> trailing"]


def test_timeline_subagent_elapsed_suffix_preserved():
    """"agent activity rows on the busy card" changes the live row's shape
    from the old bare "summary (elapsed)" suffix to "type · activity or
    'starting' · elapsed" — the elapsed time is still there, just as part
    of the new three-segment row."""
    sess = _sess()
    sess.tool_history = [("\U0001f916 Explore", False)]
    sess.active_subagents = {
        "abc": {"type": "Explore", "history_idx": 0,
                "started_at": time.monotonic() - 5},
    }
    rows = _body(build_stream_card(sess, "Working"))
    assert rows == ["⏳ `\U0001f916 Explore · starting · 5s`"]


def test_timeline_commentary_budget_drops_oldest_blocks():
    # Contract change ("layered-card-shedding"): fit-driven, not window-
    # or budget-driven; commentary outlives tool rows.
    # The old per-render commentary character budget is gone: many blocks
    # that fit the byte ceiling ALL render.
    sess = _sess()
    sess.tool_history = [(f"Bash: t-{i}", True) for i in range(6)]
    sess.stream_commentary = [(i, f"BLOCK-{i} " + "w" * 300) for i in range(6)]
    card = build_stream_card(sess, "Working")
    for i in range(6):
        assert f"BLOCK-{i}" in card


def test_timeline_newest_block_always_survives():
    # Contract change ("layered-card-shedding"): fit-driven, not window-
    # or budget-driven; commentary outlives tool rows.
    # Even in phase 2 (sections folding into the hidden marker), the
    # newest commentary block always survives.
    sess = _sess()
    n = 50
    sess.tool_history = [(f"Bash: t-{i}", True) for i in range(n)]
    sess.stream_commentary = [(i, f"BLOCK-{i:02d} " + "v" * 1500) for i in range(n)]
    card = build_stream_card(sess, "Working")
    assert len(card.encode("utf-8")) <= 32768
    assert f"BLOCK-{n-1:02d}" in card


def test_edit_busy_rich_skips_post_when_markdown_identical(mk_bot, run_async, monkeypatch):
    """Dedupe: identical consecutive renders produce only one HTTP call."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5

    post_calls = []

    async def _fake_post(method, payload):
        post_calls.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    # Force stream_last_rendered to match what build_stream_card will produce
    sess.stream_last_rendered = build_stream_card(sess, "Working")
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert len(post_calls) == 0  # no POST because content identical


def test_edit_busy_rich_posts_when_content_changed(mk_bot, run_async, monkeypatch):
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""  # nothing rendered yet

    post_calls = []

    async def _fake_post(method, payload):
        post_calls.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert len(post_calls) == 1


def test_edit_busy_rich_reply_markup_on_every_call(mk_bot, run_async, monkeypatch):
    """Stop button must ride along on every edit."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert len(payloads) == 1
    assert "reply_markup" in payloads[0]


def test_edit_busy_rich_blocked_returns_none(mk_bot, run_async, monkeypatch):
    from aipager.bot.rich_message import RichMessageBlocked
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageBlocked("blocked")),
    )
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is None


def test_edit_busy_rich_gone_clears_busy_msg_id(mk_bot, run_async, monkeypatch):
    from aipager.bot.rich_message import RichMessageGone
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageGone("gone")),
    )
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is None
    assert sess.busy_msg_id == 0


def test_edit_busy_rich_fallback_required_degrades_to_plain_text_edit(
    mk_bot, run_async, monkeypatch,
):
    """Defensive arm (research.md gotcha ~53/54): edit_message_text_rich
    structurally cannot raise RichMessageFallbackRequired today — this
    pins the LETTER of the requirement with a monkeypatch-raise, proving
    the degrade path rather than requiring the unreachable path to fire
    for real. The plain-text edit must carry every fold's own summary
    text but no raw <details>/<summary> markup, and no parse_mode — the
    regression case here is TWO independent folded sections (an older
    run each side of a commentary block), pinning that _strip_details_tags
    strips ALL blocks, not just the first."""
    from aipager.bot.rich_message import RichMessageFallbackRequired
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    # Two older runs (>= 3 rows each), each foldable, either side of a
    # commentary block — and a newest run (split off by a second
    # commentary block) so BOTH older ones actually fold.
    sess.tool_history = (
        [(f"Bash: old-{i}", True) for i in range(4)]
        + [(f"Bash: mid-{i}", True) for i in range(4)]
        + [("Bash: newest", True)]
    )
    sess.stream_commentary = [
        (4, "Between the two runs."),
        (8, "Right before the newest call."),
    ]

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageFallbackRequired("changed transport")),
    )
    bot._app.bot.edit_message_text = AsyncMock()
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is True
    bot._app.bot.edit_message_text.assert_awaited_once()
    call = bot._app.bot.edit_message_text.await_args
    text = call.args[0]
    assert "<details" not in text and "<summary" not in text and "</details>" not in text
    assert text.count("▸ 4 tool calls") == 2  # both summaries survive, stripped
    assert "Between the two runs." in text
    assert "Right before the newest call." in text
    assert call.kwargs.get("parse_mode") is None


def test_edit_busy_rich_fallback_required_returns_false_on_its_own_failure(
    mk_bot, run_async, monkeypatch,
):
    """If even the plain-text degrade edit fails, the tick reports a
    transient failure (False), same as any other retry-next-tick case."""
    from aipager.bot.rich_message import RichMessageFallbackRequired
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageFallbackRequired("changed transport")),
    )
    bot._app.bot.edit_message_text = AsyncMock(side_effect=RuntimeError("network"))
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is False


def test_edit_busy_rich_transient_failure_returns_false(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(return_value=None),  # transient
    )
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is False


def test_edit_busy_rich_success_updates_last_edit_at(mk_bot, run_async, monkeypatch):
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    sess.last_tool_edit_at = 0.0

    monkeypatch.setattr(rm_mod, "_post", AsyncMock(
        return_value={"ok": True, "result": {}},
    ))
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert sess.last_tool_edit_at > 0.0


def test_edit_busy_rich_success_clears_stream_dirty(mk_bot, run_async, monkeypatch):
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    sess.stream_dirty = True

    monkeypatch.setattr(rm_mod, "_post", AsyncMock(
        return_value={"ok": True, "result": {}},
    ))
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert sess.stream_dirty is False


def test_edit_busy_rich_rtl_body_passes_is_rtl_true(mk_bot, run_async, monkeypatch):
    """RTL body text → is_rtl=True in the payload."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_commentary = [(0, "سلام دنیا " * 20)]  # Persian text
    sess.stream_last_rendered = ""

    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert len(payloads) == 1
    assert payloads[0]["rich_message"]["is_rtl"] is True


# ── _send_busy_and_animate: streaming field seeding ──────────────────────────

def test_send_busy_seeds_stream_fields_dm(mk_bot, run_async, tmp_path):
    """DM scope: stream fields seeded correctly after _send_busy_and_animate."""
    bot = mk_bot()
    sess = _sess("dev", "dm")
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')
    sess.transcript_path = str(tp)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.stream_transcript_path == str(tp)
    assert sess.stream_offset == tp.stat().st_size
    assert sess.stream_commentary == []
    assert sess.stream_dirty is False
    assert sess.stream_last_rendered == ""


def test_send_busy_seeds_stream_fields_group(mk_bot, run_async, tmp_path):
    """Group scope: stream fields seeded the same way (no DM guard)."""
    bot = mk_bot()
    sess = _sess("team", "group")
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')
    sess.transcript_path = str(tp)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.stream_transcript_path == str(tp)
    assert sess.stream_offset == tp.stat().st_size
    assert sess.stream_commentary == []


def test_send_busy_no_draft_id_attribute(mk_bot, run_async, tmp_path):
    """After _send_busy_and_animate, sess must not have draft_id."""
    bot = mk_bot()
    sess = _sess("dev", "dm")
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')
    sess.transcript_path = str(tp)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert not hasattr(sess, "draft_id")


def test_send_busy_offset_seeded_to_file_size_prevents_cross_turn_leak(
    mk_bot, run_async, tmp_path
):
    """Offset seeded to file size → previous turn's text never re-read."""
    bot = mk_bot()
    sess = _sess("dev", "dm")
    entry = {"type": "assistant", "message": {
        "content": [{"type": "text", "text": "Previous turn answer"}],
        "stop_reason": "end_turn",
    }}
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(entry) + "\n")
    sess.transcript_path = str(tp)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    # Now simulate _read_stream_text — should find nothing new
    result = _read_stream_text(sess)
    assert result is False
    assert sess.stream_commentary == []


# ── _animate_compact regression guard ────────────────────────────────────────

def test_animate_compact_never_calls_edit_message_text_rich(
    mk_bot, run_async, monkeypatch
):
    """_animate_compact must use _edit_busy_raw, not edit_message_text_rich."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 5

    rich_calls = []

    async def _fake_rich_post(method, payload):
        rich_calls.append(method)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_rich_post)

    iteration = 0

    async def _no_sleep(t):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            sess.busy_msg_id = None

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    # _edit_busy_raw uses PTB bot, not raw HTTP; we mock it to avoid network
    bot._app.bot.edit_message_text = AsyncMock()

    run_async(bot._animate_compact(sess))

    # No call to the raw HTTP layer (editMessageText with rich_message)
    assert all("editMessageText" not in c for c in rich_calls)


def test_card_body_capped_to_recent_window():
    # Contract change ("collapse-busy-card-timeline"): fit-driven, not
    # window-driven. One unbroken run is trivially the newest run — never
    # wrapped (rule 3) — so an over-budget card like this one goes
    # straight to the raw chop, keeping the tail (design.md Risks:
    # "Retiring the newest run's partial shedding... goes from fully
    # visible straight to the raw chop. Accepted per rule 3").
    sess = _sess()
    sess.tool_history = [(f"Bash: {'u' * 120}-{i}", True) for i in range(400)]
    card = build_stream_card(sess, "Working")
    assert len(card) <= _CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= 32768
    assert "<details" not in card  # the newest run is never wrapped
    assert "-399`" in card  # newest row survives, tail-kept


def test_card_short_body_not_truncated():
    sess = _sess()
    sess.stream_commentary = [(0, "short commentary")]
    card = build_stream_card(sess, "Working")
    assert "…" not in card
    assert "short commentary" in card


def test_concurrent_edits_are_serialised(run_async, monkeypatch):
    """Two edits started concurrently must not overlap on the wire.

    Regression: the POST is a suspension point, so a hook-driven edit could
    start while the animation loop's edit was in flight. Telegram rejected the
    first with 400 "canceled by new edit message request".
    """
    import aipager.bot.animation as anim

    sess = _sess()
    sess.busy_msg_id = 7
    in_flight = 0
    overlaps = []

    async def _fake_edit(chat_id, message_id, markdown, **kw):
        nonlocal in_flight
        in_flight += 1
        if in_flight > 1:
            overlaps.append(in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"message_id": message_id}

    monkeypatch.setattr(anim, "edit_message_text_rich", _fake_edit)

    bot = MagicMock()
    bot._build_stop_keyboard = MagicMock(
        return_value=MagicMock(to_dict=MagicMock(return_value={})),
    )
    bot._edit_busy_rich = anim.AnimationMixin._edit_busy_rich.__get__(bot)

    async def _drive():
        # Distinct bodies so the dedupe cannot mask the race.
        async def _one(text):
            sess.stream_commentary = [(0, text)]
            return await bot._edit_busy_rich(sess, "Working")
        return await asyncio.gather(_one("first body"), _one("second body"))

    run_async(_drive())
    assert overlaps == [], f"Concurrent edits overlapped on the wire: {overlaps}"


# ── The finished card (final=True) ────────────────────────────────────────────

def _rows(card: str) -> list[str]:
    """The timeline rows, in order, without the status line.

    Contract change ("status-line-at-card-bottom"): the status line is the
    card's LAST element, so it is stripped from the end, not the front.
    """
    body, _, _status = card.rpartition("\n\n")
    return body.split("\n\n") if body else []


def test_final_card_keeps_every_tool_row():
    """No 15-row collapse: the finished card is the turn's record."""
    sess = _sess()
    sess.tool_history = [(f"Read: f{i}.py", True) for i in range(40)]
    card = build_stream_card(sess, "Done", final=True)
    assert "earlier tool" not in card
    assert len(_rows(card)) == 40


def test_live_card_still_folds_old_tool_rows():
    # Contract change ("collapse-busy-card-timeline"): fit-driven, per
    # section — the OLDER (non-newest) run folds behind its own
    # <details> block while the NEWEST run's rows stay fully visible.
    sess = _sess()
    n = 20
    sess.tool_history = [(f"Bash: l-{i}", True) for i in range(n)]
    sess.stream_commentary = [(n // 2, "MIDWAY-PROSE")]
    sess.stream_hook_live = True
    card = build_stream_card(sess, "Working")
    assert len(card) <= _CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= 32768
    assert "<details><summary>▸ 10 tool calls</summary>" in card
    assert "Bash: l-19" in card and "l-19" not in card.split("</details>", 1)[0]
    assert "MIDWAY-PROSE" in card
    assert f"-{n-1}" in card  # the newest row is visible


def test_final_card_keeps_every_commentary_block():
    """No 600-char budget: all prose survives on the finished card."""
    sess = _sess()
    sess.stream_commentary = [(0, "block %d %s" % (i, "x" * 200)) for i in range(6)]
    card = build_stream_card(sess, "Done", final=True)
    for i in range(6):
        assert f"block {i} " in card


def test_live_card_still_drops_oldest_commentary():
    # Contract change ("collapse-busy-card-timeline"): commentary never
    # folds (rule 1) — under pressure, Step B drops whole oldest
    # commentary (and single-row run) sections outright, never a fold.
    sess = _sess()
    n = 60
    sess.tool_history = [(f"Bash: t-{i}", True) for i in range(n)]
    sess.stream_commentary = [(i, f"BLOCK-{i:02d} " + "k" * 1200) for i in range(n)]
    card = build_stream_card(sess, "Working")
    assert len(card) <= _CARD_CHAR_BUDGET
    assert "BLOCK-00" not in card          # oldest genuinely dropped
    assert f"BLOCK-{n-1:02d}" in card      # newest survives


def test_final_card_footer_is_settled_not_hourglass():
    sess = _sess()
    sess.tool_history = [("Read: a.py", True)]
    card = build_stream_card(sess, "Done", final=True)
    footer = card.rsplit("\n\n", 1)[-1]
    assert footer.startswith("✅")


def test_final_card_folds_the_older_run_keeping_commentary_visible():
    # Contract change ("collapse-busy-card-timeline"): the FINAL card
    # uses the same per-section policy — an older, non-newest run folds
    # behind its own block; commentary stays fully visible, never folded.
    sess = _sess()
    n = 20
    sess.tool_history = [(f"Bash: f-{i}", True) for i in range(n)]
    sess.stream_commentary = [(0, "OPENING-PROSE"), (n // 2, "MID-PROSE")]
    card = build_stream_card(sess, "Done", final=True)
    assert len(card) <= _CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= 32768
    assert "OPENING-PROSE" in card and "MID-PROSE" in card
    assert "<details><summary>▸ 10 tool calls</summary>" in card


def test_final_card_shedding_is_a_no_op_when_it_already_fits():
    sess = _sess()
    sess.tool_history = [("Read: a.py", True), ("Bash: ls", True)]
    sess.stream_commentary = [(1, "midway")]
    assert _rows(build_stream_card(sess, "Done", final=True)) == [
        "✅ `Read: a.py`",
        "> midway",
        "✅ `Bash: ls`",
    ]


def test_final_card_survives_commentary_alone_over_the_ceiling():
    """Nothing left to shed — the byte-level backstop still holds the ceiling."""
    sess = _sess()
    sess.stream_commentary = [(0, "z" * 40_000)]
    card = build_stream_card(sess, "Done", final=True)
    assert len(card.encode("utf-8")) <= 32_768


def test_final_edit_omits_reply_markup(mk_bot, run_async, monkeypatch):
    """A finished turn must not keep a live Stop button."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Done", final=True))
    assert len(payloads) == 1
    assert "reply_markup" not in payloads[0]


def test_final_edit_bypasses_the_dedupe(mk_bot, run_async, monkeypatch):
    """Even byte-identical, the final edit must go out — it clears the button."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.stream_last_rendered = build_stream_card(sess, "Done", final=True)
    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Done", final=True))
    assert len(payloads) == 1


def test_folded_run_sits_between_the_prose_that_flanks_it():
    """Contract change ("collapse-busy-card-timeline"): a folded run's
    <details> block stays exactly where that run happened in the
    timeline — never moved to the top of the card or gathered elsewhere.
    An older, non-newest run (>= 3 rows) folds between the two prose
    blocks that flank it, in chronological place."""
    sess = _sess()
    sess.tool_history = (
        [(f"Bash: mid-{i}", True) for i in range(4)]
        + [("Bash: newest", True)]
    )
    sess.stream_commentary = [(0, "OPENING-PROSE"), (4, "MIDDLE-PROSE")]
    card = build_stream_card(sess, "Working")
    i_open = card.index("OPENING-PROSE")
    i_details = card.index("<details>")
    i_close = card.index("</details>")
    i_mid = card.index("MIDDLE-PROSE")
    assert i_open < i_details < i_close < i_mid


def _write_entry(path, content: list[dict], mode="a") -> None:
    entry = {"type": "assistant",
             "message": {"content": content, "stop_reason": "tool_use"}}
    with open(path, mode) as f:
        f.write(json.dumps(entry) + "\n")


def test_prose_anchors_ahead_of_rows_already_recorded(tmp_path):
    """The regression: PreToolUse lands the row before the prose is readable.

    Both tool rows exist by the time the transcript entry is flushed, so an
    anchor taken from len(tool_history) would push the opening line below the
    tools it introduced.
    """
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    sess = _sess()
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0
    sess.tool_history = [("Bash: one", True), ("Grep: two", False)]

    _write_entry(tp, [
        {"type": "text", "text": "opening line"},
        {"type": "tool_use", "id": "a", "name": "Bash", "input": {}},
        {"type": "tool_use", "id": "b", "name": "Grep", "input": {}},
    ])
    assert _read_stream_text(sess) is True
    assert sess.stream_commentary == [(0, "opening line")]


def test_prose_between_two_tools_anchors_between_them(tmp_path):
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    sess = _sess()
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0
    sess.tool_history = [("Bash: one", True), ("Grep: two", False)]

    _write_entry(tp, [{"type": "tool_use", "id": "a", "name": "Bash", "input": {}}])
    _write_entry(tp, [
        {"type": "text", "text": "now grepping"},
        {"type": "tool_use", "id": "b", "name": "Grep", "input": {}},
    ])
    _read_stream_text(sess)
    assert sess.stream_commentary == [(1, "now grepping")]


def test_subagent_row_is_stepped_over(tmp_path):
    """A Task adds a 🤖 row on top of its own; it belongs to no tool_use block."""
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    sess = _sess()
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0
    sess.tool_history = [("Task: audit", True), ("🤖 explorer", True),
                         ("Bash: after", False)]

    _write_entry(tp, [{"type": "tool_use", "id": "a", "name": "Task", "input": {}}])
    _write_entry(tp, [
        {"type": "text", "text": "agent done, running the check"},
        {"type": "tool_use", "id": "b", "name": "Bash", "input": {}},
    ])
    _read_stream_text(sess)
    assert sess.stream_commentary == [(2, "agent done, running the check")]


def test_subagent_row_settled_still_stepped_over(tmp_path):
    """Orchestrator amendment 4: the settled row's new frozen text ("🤖
    <type> · N tool calls · elapsed") keeps the _SUBAGENT_MARK prefix, so
    _advance_tool_cursor steps over it exactly like the old short "🤖
    <type>" shape did — no code change needed, this pins it."""
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    sess = _sess()
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0
    sess.tool_history = [
        ("Task: audit", True),
        ("\U0001f916 explorer · 3 tool calls · 5s", True),
        ("Bash: after", False),
    ]

    _write_entry(tp, [{"type": "tool_use", "id": "a", "name": "Task", "input": {}}])
    _write_entry(tp, [
        {"type": "text", "text": "agent done, running the check"},
        {"type": "tool_use", "id": "b", "name": "Bash", "input": {}},
    ])
    _read_stream_text(sess)
    assert sess.stream_commentary == [(2, "agent done, running the check")]


def test_unrecorded_tool_leaves_the_cursor_put(tmp_path):
    """No matching row yet — the cursor must not run off the end of the history."""
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    sess = _sess()
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0
    sess.tool_history = [("Bash: one", True)]

    _write_entry(tp, [
        {"type": "tool_use", "id": "a", "name": "WebFetch", "input": {}},
        {"type": "text", "text": "still early"},
    ])
    _read_stream_text(sess)
    assert sess.stream_commentary == [(0, "still early")]
    assert sess.stream_tool_cursor == 0



# ── Commentary streamed from the MessageDisplay hook ─────────────────────────

def _md(bot, run_async, sess, delta, msg_id="m1", final=False):
    run_async(bot.notify(sess, "assistant_text", {
        "delta": delta, "message_id": msg_id, "index": 0, "final": final,
    }))


def test_hook_chunks_of_one_message_grow_a_single_block(mk_bot, run_async):
    """A message is one block that grows, not a row per chunk."""
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0  # suppress the edit; assert on state only
    _md(bot, run_async, sess, "First step — ")
    _md(bot, run_async, sess, "listing the directories.")
    assert sess.stream_commentary == [(0, "First step — listing the directories.")]


def test_a_new_message_id_starts_a_new_block(mk_bot, run_async):
    """A tool recorded between two blocks belongs to the SECOND message.

    A short preamble only reaches the hook once its message is complete, so a
    row that lands after one block and before the next was introduced by the
    next one — both blocks therefore sit above it.
    """
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    _md(bot, run_async, sess, "first", msg_id="m1")
    sess.record_tool("Bash: ls", False)
    _md(bot, run_async, sess, "second", msg_id="m2")
    assert sess.stream_commentary == [(0, "first"), (0, "second")]


def test_prose_anchors_above_the_batch_of_tools_it_introduced(mk_bot, run_async):
    """The live regression: three parallel tools land before their preamble.

    Reported 2026-08-04 — the quote rendered below all three Bash rows it
    was introducing, because the anchor was the tool count at arrival.
    """
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    for summary in ("Bash: list dirs", "Bash: count lines", "Bash: git log"):
        sess.record_tool(summary, True)
    _md(bot, run_async, sess, "I'll start with three parallel lookups.", msg_id="m1")

    assert _body(build_stream_card(sess, "Working")) == [
        "> I'll start with three parallel lookups.",
        "✅ `Bash: list dirs`",
        "✅ `Bash: count lines`",
        "✅ `Bash: git log`",
    ]


def test_each_message_keeps_its_own_batch(mk_bot, run_async):
    """Two preambles, each with its own tools, stay in their own groups."""
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    sess.record_tool("Bash: one", True)
    _md(bot, run_async, sess, "first step", msg_id="m1")
    sess.record_tool("Bash: two", True)
    _md(bot, run_async, sess, "second step", msg_id="m2")

    assert _body(build_stream_card(sess, "Working")) == [
        "> first step",
        "✅ `Bash: one`",
        "> second step",
        "✅ `Bash: two`",
    ]


def test_a_batch_waits_for_the_sentence_that_introduces_it(mk_bot, run_async):
    """Live regression (2026-08-07): the row was drawn first and the quote
    jumped in above it. Measured, the prose is 20-515 ms behind, so the batch
    waits rather than showing rows in the wrong order."""
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    # Latched by the session's first streamed chunk; holding is pointless
    # before the hook has proved itself, or the fallback path would stall.
    sess.stream_hook_live = True
    sess.record_tool("Bash: ls", False)
    assert _body(build_stream_card(sess, "Working")) == []

    _md(bot, run_async, sess, "Listing the directories now.", msg_id="m1")
    assert _body(build_stream_card(sess, "Working")) == [
        "> Listing the directories now.",
        "⏳ `Bash: ls`",
    ]


def test_hook_latches_off_the_transcript_fallback(mk_bot, run_async, tmp_path):
    """Both sources delivering the same prose would print every line twice."""
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0

    _md(bot, run_async, sess, "Listing the directories now.", msg_id="m1")
    assert sess.stream_hook_live is True

    # The transcript catches up minutes later with the very same sentence.
    _write_entry(tp, [{"type": "text", "text": "Listing the directories now."}])
    assert _read_stream_text(sess) is False
    assert sess.stream_commentary == [(0, "Listing the directories now.")]


def test_transcript_fallback_still_works_without_the_hook(tmp_path):
    """An older Claude Code never sends MessageDisplay — keep reading the file."""
    sess = _sess()
    tp = tmp_path / "t.jsonl"
    tp.write_text("")
    sess.stream_transcript_path = str(tp)
    sess.stream_offset = 0
    _write_entry(tp, [{"type": "text", "text": "from the transcript"}])
    assert _read_stream_text(sess) is True
    assert sess.stream_commentary == [(0, "from the transcript")]


# ── The final answer must not be quoted into the card ────────────────────────

def test_the_answer_block_is_not_rendered(mk_bot, run_async):
    """The hook streams the answer too; it belongs in the answer message."""
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    sess.record_tool("Bash: ls", True)
    _md(bot, run_async, sess, "Step 1 — listing.", msg_id="m1")
    _md(bot, run_async, sess, "Here is the full answer.", msg_id="m2")

    assert _body(build_stream_card(sess, "Working")) == [
        "> Step 1 — listing.",
        "✅ `Bash: ls`",
    ]


def test_the_answer_does_not_evict_real_commentary(mk_bot, run_async):
    """Live regression (2026-08-07): mid-turn the card lost both quotes and
    showed a dump of the answer, then the quotes came back on the finished
    card. The answer is long, so it ate the whole commentary budget."""
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    for summary in ("Bash: one", "Bash: two", "Bash: three"):
        sess.record_tool(summary, True)
    _md(bot, run_async, sess, "Re-running step 1 fresh.", msg_id="m1")
    sess.record_tool("Bash: grep", True)
    _md(bot, run_async, sess, "Now step 2 — reading and grepping.", msg_id="m2")
    _md(bot, run_async, sess, "ANSWER " * 400, msg_id="m3")  # 2800 chars

    rows = _body(build_stream_card(sess, "Working"))
    assert rows[0] == "> Re-running step 1 fresh."
    assert "> Now step 2 — reading and grepping." in rows
    assert not any("ANSWER" in r for r in rows)


def test_the_answer_is_absent_from_the_finished_card_too(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    sess.record_tool("Bash: ls", True)
    _md(bot, run_async, sess, "Step 1 — listing.", msg_id="m1")
    _md(bot, run_async, sess, "Here is the full answer.", msg_id="m2")

    assert _body(build_stream_card(sess, "Done", final=True)) == [
        "> Step 1 — listing.",
        "✅ `Bash: ls`",
    ]


def test_a_block_appears_once_its_tools_fire(mk_bot, run_async):
    """A long message streams before its tools; it must not be lost, only
    deferred until they land and the batch settles."""
    import aipager.bot.animation as anim
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    _md(bot, run_async, sess, "About to grep for that symbol.", msg_id="m1")
    assert _body(build_stream_card(sess, "Working")) == []

    sess.record_tool("Bash: grep", False)
    sess.stream_batch_since = time.monotonic() - anim._BATCH_HOLD_SECS - 0.1
    anim._expire_tool_batch(sess)
    assert _body(build_stream_card(sess, "Working")) == [
        "> About to grep for that symbol.",
        "⏳ `Bash: grep`",
    ]


def test_a_silent_batch_settles_so_later_prose_lands_below_it(mk_bot, run_async):
    """A message that called tools without saying anything must not have the
    NEXT message's sentence claim its rows."""
    import aipager.bot.animation as anim
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    sess.record_tool("Bash: quiet", True)
    sess.stream_batch_since = time.monotonic() - anim._BATCH_HOLD_SECS - 0.1
    anim._expire_tool_batch(sess)
    assert sess.stream_anchor_floor == 1

    sess.record_tool("Bash: loud", True)
    _md(bot, run_async, sess, "Now the second step.", msg_id="m2")
    assert _body(build_stream_card(sess, "Working")) == [
        "✅ `Bash: quiet`",
        "> Now the second step.",
        "✅ `Bash: loud`",
    ]


def test_a_turn_with_no_tools_quotes_nothing(mk_bot, run_async):
    """Claude just answers — the card carries its header and nothing else."""
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    _md(bot, run_async, sess, "Short answer, no tools needed.", msg_id="m1")
    assert _body(build_stream_card(sess, "Done", final=True)) == []


def test_the_fallback_still_renders_an_anchor_past_the_end(tmp_path):
    """Without the hook the rule does not apply: there an anchor past the end
    means the prose was read before its tool row landed."""
    sess = _sess()
    sess.tool_history = [("Bash: a", True)]
    sess.stream_commentary = [(9, "trailing")]
    assert sess.stream_hook_live is False
    assert _body(build_stream_card(sess, "Working")) == [
        "✅ `Bash: a`",
        "> trailing",
    ]


# ── Prose that flushes before its own tools (long streaming message) ─────────

def test_a_stale_batch_does_not_pull_the_next_block_up(mk_bot, run_async):
    """Found in review (2026-08-07): a message that flushed its prose BEFORE
    calling its tool left the floor behind, so the next block — usually the
    final answer — anchored above that tool and was rendered."""
    import aipager.bot.animation as anim
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    sess.stream_hook_live = True

    _md(bot, run_async, sess, "Let me check that file.", msg_id="m1")
    sess.record_tool("Read: X", True)
    # The answer takes seconds to write, so its batch has long gone stale.
    sess.stream_batch_since = time.monotonic() - anim._BATCH_HOLD_SECS - 0.1
    _md(bot, run_async, sess, "The file looks fine.", msg_id="m2")

    assert _body(build_stream_card(sess, "Done", final=True)) == [
        "> Let me check that file.",
        "✅ `Read: X`",
    ]


def test_two_tool_introducing_messages_then_an_answer(mk_bot, run_async):
    """The shape the reviewer asked for: chained batches plus a final answer."""
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0
    sess.stream_hook_live = True

    sess.record_tool("Bash: one", True)
    sess.record_tool("Bash: two", True)
    _md(bot, run_async, sess, "First step — two lookups.", msg_id="m1")
    sess.record_tool("Read: f.py", True)
    _md(bot, run_async, sess, "Second step — reading the file.", msg_id="m2")
    _md(bot, run_async, sess, "Here is the final answer text.", msg_id="m3")

    assert _body(build_stream_card(sess, "Done", final=True)) == [
        "> First step — two lookups.",
        "✅ `Bash: one`",
        "✅ `Bash: two`",
        "> Second step — reading the file.",
        "✅ `Read: f.py`",
    ]


def test_trimming_the_history_shifts_the_card_anchors():
    """tool_history is trimmed from the front at TOOL_HISTORY_CAP; anchors
    index into it, so they must move with it or prose drifts upward.
    (Contract change "layered-card-shedding": no "earlier tools" counter —
    the shifted prose simply renders first, above the visible rows.)"""
    from aipager.state import TOOL_HISTORY_CAP
    sess = _sess()
    sess.stream_commentary = [(0, "opening line")]
    sess.stream_anchor_floor = 0
    sess.stream_tool_cursor = 0
    for i in range(TOOL_HISTORY_CAP + 5):
        sess.record_tool(f"Bash: {i}", True)

    assert len(sess.tool_history) == TOOL_HISTORY_CAP
    assert sess.stream_anchor_floor >= 0
    assert sess.stream_commentary[0][0] == 0
    rows = _body(build_stream_card(sess, "Working"))
    assert rows[0] == "> opening line"


def _header(card: str) -> str:
    """The status line — now the card's LAST element
    ("status-line-at-card-bottom")."""
    return card.rsplit("\n\n", 1)[-1]


def test_waiting_header_single_agent_no_types_over_three():
    sess = _sess("hiva")
    sess.busy_started_at = time.monotonic() - 260  # 4m 20s
    sess.active_subagents["a1"] = {"type": "Explore"}
    header = _header(build_stream_card(sess, "Working", waiting=True))
    assert header == (
        "🔄 **hiva** · 1 agent (Explore) still working · 4m 20s"
    )


def test_waiting_header_plural_agents():
    sess = _sess("hiva")
    sess.active_subagents["a1"] = {"type": "Explore"}
    sess.active_subagents["a2"] = {"type": "Plan"}
    header = _header(build_stream_card(sess, "Working", waiting=True))
    assert "2 agents" in header


def test_waiting_header_types_sorted_and_comma_joined():
    sess = _sess("hiva")
    sess.active_subagents["a1"] = {"type": "Zebra"}
    sess.active_subagents["a2"] = {"type": "Alpha"}
    header = _header(build_stream_card(sess, "Working", waiting=True))
    assert "(Alpha, Zebra)" in header


def test_waiting_header_omits_types_over_three_distinct():
    sess = _sess("hiva")
    for i, t in enumerate(["A", "B", "C", "D"]):
        sess.active_subagents[f"a{i}"] = {"type": t}
    header = _header(build_stream_card(sess, "Working", waiting=True))
    assert "(" not in header  # no parenthetical type list
    assert "4 agents" in header


def test_waiting_status_omits_elapsed_when_busy_started_at_falsy():
    sess = _sess("hiva")
    sess.busy_started_at = 0.0
    sess.active_subagents["a1"] = {"type": "Explore"}
    status = _header(build_stream_card(sess, "Working", waiting=True))
    assert status == "🔄 **hiva** · 1 agent (Explore) still working"


def test_waiting_body_is_the_same_timeline_with_a_waiting_status_line():
    """The waiting frame swaps ONLY the status line; the timeline above it
    is byte-identical to an ordinary busy render."""
    sess = _sess("hiva")
    sess.active_subagents["a1"] = {"type": "Explore"}
    sess.tool_history = [("Bash: ls", True)]
    waiting_card = build_stream_card(sess, "Working", waiting=True)
    normal_card = build_stream_card(sess, "Whatever", waiting=False)
    status = waiting_card.splitlines()[-1]
    assert status.startswith("🔄")
    assert "still working" in status
    assert "1 agent (Explore)" in status
    assert _body(waiting_card) == _body(normal_card)


def test_waiting_status_line_survives_truncation():
    """The status line is appended after the fitter and after the byte
    backstop, so it stays the last line even when the timeline is far
    over the ceiling."""
    sess = _sess("hiva")
    sess.active_subagents["a1"] = {"type": "Explore"}
    sess.stream_commentary = [(0, "x" * 8000)]
    sess.tool_history = [(f"Bash: cmd-{i}", True) for i in range(400)]
    card = build_stream_card(sess, "Working", waiting=True)
    assert len(card.encode("utf-8")) <= 32768
    status = card.splitlines()[-1]
    assert status.startswith("🔄")
    assert "still working" in status


def test_no_footer_on_normal_and_final_frames():
    """The footer belongs to the waiting frame only."""
    sess = _sess("hiva")
    sess.active_subagents["a1"] = {"type": "Explore"}
    sess.tool_history = [("Bash: ls", True)]
    for kwargs in ({"waiting": False}, {"final": True, "waiting": True}):
        card = build_stream_card(sess, "Working", **kwargs)
        assert "still working" not in card


def test_waiting_ignored_when_final_also_true():
    """final wins over waiting — a settled card is never shown as still
    waiting."""
    sess = _sess("hiva")
    sess.active_subagents["a1"] = {"type": "Explore"}
    card = build_stream_card(sess, "Done", final=True, waiting=True)
    header = _header(card)
    assert "waiting on background work" not in header
    assert "✅" in header


def test_waiting_status_during_grace_says_finishing_up():
    """Review rev-iter1-004: with the agent table empty (the continuation
    grace window — an ordinary part of every job's lifecycle) the status
    line must not say "0 agents still working"."""
    sess = _sess("hiva")
    sess.job_grace_until = 10**12  # far future; table empty
    sess.tool_history = [("Bash: ls", True)]
    card = build_stream_card(sess, "Working", waiting=True)
    status = card.splitlines()[-1]
    assert status.startswith("🔄")
    assert "finishing up" in status
    assert "0 agent" not in card


# ---- per-section folding ("collapse-busy-card-timeline") -------------------


def _many_tools_sess(n_tools=30, commentary=None):
    sess = _sess("hiva")
    sess.tool_history = [(f"Bash: step-{i}", True) for i in range(n_tools)]
    sess.stream_hook_live = True
    sess.stream_commentary = commentary or []
    return sess


def test_all_tools_visible_when_it_is_a_single_newest_run():
    """30 tool rows with nothing to split them stay ONE section, which is
    trivially the newest run — rule 3 says it is never wrapped, however
    many rows it has."""
    sess = _many_tools_sess(30)
    card = build_stream_card(sess, "Working")
    assert "<details" not in card
    assert "step-0" in card and "step-29" in card


def test_short_run_under_fold_min_rows_never_folds():
    """Rule 4: fewer than _FOLD_MIN_ROWS (3) never folds, even when the
    run is older, not the newest."""
    sess = _sess()
    sess.tool_history = [("Bash: a", True), ("Bash: b", True)]
    sess.stream_commentary = [(2, "Split point.")]
    sess.tool_history.append(("Bash: newest", True))
    card = build_stream_card(sess, "Working")
    assert "<details" not in card
    assert "Bash: a" in card and "Bash: b" in card


def test_older_run_with_three_or_more_rows_folds_behind_its_own_block():
    """Rule 2/4: an older, non-newest run with >= 3 rows folds behind its
    own <details> block, summary counting exactly its own rows. The row
    text itself is still PRESENT in the markdown (verbatim, recoverable
    via the tap) — it is Telegram's client rendering, not the markdown
    string, that hides it until tapped — so it must sit strictly inside
    the block's own span, not outside it."""
    sess = _sess()
    sess.tool_history = [(f"Bash: old-{i}", True) for i in range(3)]
    sess.stream_commentary = [(3, "Split point.")]
    sess.tool_history.append(("Bash: newest", True))
    card = build_stream_card(sess, "Working")
    assert "<details><summary>▸ 3 tool calls</summary>" in card
    inside = card.split("<details>", 1)[1].split("</details>", 1)[0]
    assert "Bash: old-0" in inside
    outside = card.split("</details>", 1)[1]
    assert "Bash: old-0" not in outside


def test_newest_run_never_wraps_regardless_of_row_count():
    """Rule 3: the newest run section is never wrapped, however many
    rows it has — retiring the old code's partial-shedding of the
    newest run (design.md Risks: "that case now goes from fully visible
    straight to the raw chop")."""
    sess = _sess()
    sess.tool_history = [(f"Bash: t-{i}", True) for i in range(50)]
    card = build_stream_card(sess, "Working")
    assert "<details" not in card
    assert "Bash: t-0" in card and "Bash: t-49" in card


def test_commentary_never_folds_at_any_age():
    """Rule 1: commentary is never itself wrapped in a <details> block —
    it can only ever be genuinely DROPPED whole by Step B, never folded."""
    sess = _sess()
    sess.tool_history = [(f"Bash: old-{i}", True) for i in range(3)]
    sess.stream_commentary = [(0, "OLD-NARRATIVE")]
    # A second split so the "old" run is not trivially the newest run —
    # otherwise rule 3 alone (never wraps) would explain the absence of
    # a fold, not rule 1.
    sess.stream_commentary.append((3, "Split point."))
    sess.tool_history.append(("Bash: newest", True))
    card = build_stream_card(sess, "Working")
    assert "<details" in card  # the run DID fold
    assert "OLD-NARRATIVE" in card
    inside = card.split("<details>", 1)[1].split("</details>", 1)[0]
    assert "OLD-NARRATIVE" not in inside


def test_multiple_folded_sections_each_get_their_own_accurate_summary():
    """Several independent blocks per card is normal (rule 2) — each
    summary's count matches ONLY that section's own rows, row-for-row."""
    sess = _sess()
    sess.tool_history = (
        [(f"Bash: a-{i}", True) for i in range(3)]
        + [(f"Bash: b-{i}", True) for i in range(5)]
        + [("Bash: newest", True)]
    )
    sess.stream_commentary = [
        (3, "Between A and B."),
        (8, "Between B and newest."),
    ]
    card = build_stream_card(sess, "Working")
    assert card.count("<details>") == 2
    assert "▸ 3 tool calls" in card
    assert "▸ 5 tool calls" in card
    first_block = card.split("</details>", 1)[0]
    assert first_block.count("✅ `Bash: a-") == 3
    assert "Bash: b-" not in first_block  # the first block is A's alone


def test_layered_render_is_deterministic():
    sess = _many_tools_sess(80, [(40, "SAME-PROSE " + "q" * 300)])
    a = build_stream_card(sess, "Working")
    b = build_stream_card(sess, "Working")
    assert a == b


def test_newest_run_survives_when_the_protected_floor_still_overflows():
    """Review rev-iter1-001, carried into design.md rule 7: a trailing
    prose section AFTER the newest run must not make that run eligible
    for whole-section removal. Every row here dwarfs the 8,800-character
    budget many times over, so even the protected floor overflows and the
    raw chop fires — the newest run's own last row is what the chop's
    tail-keep preserves."""
    sess = _sess()
    tools = [(f"Bash: old-{i}", True) for i in range(10)]
    tools += [(f"Bash: {'r' * 300}-{i}", True) for i in range(90)]
    tools.append(("Bash: " + "z" * 33000 + "NEWEST-ROW-END", True))
    sess.tool_history = tools
    sess.stream_commentary = [
        (0, "EARLY-BIG " + "e" * 34000),
        (10, "MID-BIG " + "f" * 33000),
        (len(tools), "tiny trailing note"),
    ]
    card = build_stream_card(sess, "Working")
    assert len(card) <= _CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= 32768
    assert "NEWEST-ROW-END" in card
    assert "tiny trailing note" in card


# ---- <details> block construction, summary text, and stripping -------------

def test_row_separator_survives_inside_a_details_block():
    """Live-verified (design.md "Verified" #1): rows separated by a blank
    line render one per line inside a block, once expanded — offline
    proxy: the constructed markdown contains two _ROW_SEP-separated rows
    strictly between <details>...<summary>...</summary> and </details> —
    never a bare newline, which would let Telegram silently merge them."""
    db = _build_details_block("SUMMARY", ["row-one", "row-two"])
    assert db.startswith("<details><summary>SUMMARY</summary>")
    assert db.endswith("</details>")
    body = db.split("</summary>", 1)[1].rsplit("</details>", 1)[0]
    assert f"row-one{_ROW_SEP}row-two" in body
    assert "row-one\nrow-two" not in body  # not a bare-newline join


def test_build_details_block_never_emits_an_open_attribute():
    db = _build_details_block("▸ 3 tool calls", ["r1", "r2", "r3"])
    assert "open" not in db.split(">", 1)[0]


def test_tool_fold_summary_pluralizes():
    assert _tool_fold_summary(0) == "▸ 0 tool calls"
    assert _tool_fold_summary(1) == "▸ 1 tool call"
    assert _tool_fold_summary(2) == "▸ 2 tool calls"


def test_strip_details_tags_strips_every_block_not_just_the_first():
    """design.md: the multi-block regression case for _strip_details_tags
    — a card can carry several independent folds; every one of them must
    lose its tags, not only the first `re.sub` match would find."""
    block1 = _build_details_block("▸ 3 tool calls", ["r1", "r2", "r3"])
    block2 = _build_details_block("▸ 5 tool calls", ["r4", "r5", "r6", "r7", "r8"])
    markdown = f"{block1}{_ROW_SEP}mid prose{_ROW_SEP}{block2}{_ROW_SEP}status"
    stripped = _strip_details_tags(markdown)
    assert "<details" not in stripped
    assert "</details>" not in stripped
    assert "<summary" not in stripped
    assert stripped.count("▸ 3 tool calls") == 1
    assert stripped.count("▸ 5 tool calls") == 1
    assert "mid prose" in stripped
    assert "r1" not in stripped and "r8" not in stripped


def test_strip_details_tags_is_a_no_op_without_a_details_block():
    markdown = "plain body" + _ROW_SEP + "status"
    assert _strip_details_tags(markdown) == markdown


# ---- direct Step A / Step B coverage against _fit_sections -----------------

def test_fit_sections_folds_an_eligible_older_run_and_reports_not_dropped():
    sections = [
        ("run", [f"✅ `Bash: old-{i}`" for i in range(3)], None),
        ("run", ["✅ `Bash: newest`"], None),
    ]
    body, dropped = _fit_sections(sections, 0, 0)
    assert dropped is False
    assert "<details><summary>▸ 3 tool calls</summary>" in body
    assert "Bash: newest" in body


def test_fit_sections_step_b_drops_whole_oldest_sections_under_pressure():
    prose_a = ("prose", ["> " + "A" * 2000], None)
    prose_b = ("prose", ["> " + "B" * 2000], None)
    run = ("run", ["✅ `Bash: only`"], None)
    sections = [prose_a, prose_b, run]
    body, dropped = _fit_sections(
        sections, _CARD_CHAR_BUDGET - 300, 32_768 - 300,
    )
    assert dropped is True
    assert "AAAA" not in body
    assert "BBBB" in body    # newest prose — protected
    assert "Bash: only" in body


def test_fit_sections_never_drops_a_live_agent_section():
    """Step B protection, mutation target: an agent-run section must
    never be removed by the whole-section backstop, regardless of where
    it sits relative to sections that DO get dropped."""
    prose_a = ("prose", ["> " + "A" * 2000], None)
    agent = ("agent-run", ["⏳ `\U0001f916 explore · Bash: ls · 5s`"], [])
    prose_c = ("prose", ["> " + "C" * 60], None)
    run_newest = ("run", ["✅ `Bash: newest`"], None)
    sections = [prose_a, agent, prose_c, run_newest]
    body, dropped = _fit_sections(
        sections, _CARD_CHAR_BUDGET - 300, 32_768 - 300,
    )
    assert dropped is True
    assert "explore" in body
    assert "AAAA" not in body
    assert "CCCC" in body
    assert "newest" in body


def test_fit_sections_never_drops_a_settled_agent_section():
    prose_a = ("prose", ["> " + "A" * 2000], None)
    settled = ("agent-settled", ["✅ `\U0001f916 explore · 5 tool calls · 5s`"], [])
    prose_c = ("prose", ["> " + "C" * 60], None)
    run_newest = ("run", ["✅ `Bash: newest`"], None)
    sections = [prose_a, settled, prose_c, run_newest]
    body, dropped = _fit_sections(
        sections, _CARD_CHAR_BUDGET - 300, 32_768 - 300,
    )
    assert dropped is True
    assert "explore" in body
    assert "AAAA" not in body


def test_fit_sections_agent_skip_does_not_stop_shedding_sections_after_it():
    """Skip (do not stop at) an agent section — a still-live agent can sit
    anywhere in the timeline, and skipping past it must not stop Step B
    from still shedding newer sections if the budget demands it."""
    prose_a = ("prose", ["> " + "A" * 2000], None)
    agent = ("agent-run", ["⏳ `\U0001f916 explore · Bash: ls · 5s`"], [])
    prose_b = ("prose", ["> " + "B" * 2000], None)
    prose_c = ("prose", ["> " + "C" * 60], None)
    run_newest = ("run", ["✅ `Bash: newest`"], None)
    sections = [prose_a, agent, prose_b, prose_c, run_newest]
    body, dropped = _fit_sections(
        sections, _CARD_CHAR_BUDGET - 400, 32_768 - 400,
    )
    assert dropped is True
    assert "explore" in body
    assert "AAAA" not in body
    assert "BBBB" not in body  # dropped too — Step B kept going past the agent
    assert "CCCC" in body


# ---- last_card_truncated semantics ("collapse-busy-card-timeline") ---------

def test_last_card_truncated_false_when_only_folding_happened():
    """Folding is presentation, not loss — even with several folds in the
    card, last_card_truncated stays False as long as nothing had to be
    genuinely dropped."""
    sess = _sess()
    sess.tool_history = (
        [(f"Bash: a-{i}", True) for i in range(3)]
        + [(f"Bash: b-{i}", True) for i in range(4)]
        + [("Bash: newest", True)]
    )
    sess.stream_commentary = [(3, "split")]
    card, truncated = build_stream_card_ex(sess, "Working")
    assert "<details" in card  # folds really happened
    assert truncated is False


def test_last_card_truncated_true_when_a_whole_section_is_dropped():
    """Once even the fully-folded card exceeds budget, Step B drops whole
    oldest sections (commentary included) — THAT sets last_card_truncated
    True, never mere folding."""
    sess = _sess()
    n = 300
    sess.tool_history = [(f"Bash: old-{i} " + "z" * 40, True) for i in range(n)]
    sess.stream_commentary = [
        (0, "OLD-NARRATIVE " + "e" * 2000), (n, "NEWEST-NARRATIVE"),
    ]
    sess.tool_history.append(("Bash: newest", True))
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    assert "OLD-NARRATIVE" not in card  # whole section dropped, commentary included
    assert "NEWEST-NARRATIVE" in card


# ---- char budget vs. the byte ceiling ("collapse-busy-card-timeline") ------

def test_card_char_budget_binds_before_byte_ceiling_for_ascii_content():
    """For plain ASCII content, 1 char == 1 byte, so the tighter
    8,800-character budget binds well before the 32,768-byte ceiling ever
    could — extends the test_card_truncation_valid_utf8 pattern to the
    new dual budget."""
    sess = _sess()
    sess.tool_history = [(f"Bash: t-{i} " + "x" * 300, True) for i in range(60)]
    card = build_stream_card(sess, "Working")
    assert len(card) <= _CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) < 32_768  # nowhere near the byte ceiling


def test_card_char_byte_divergence_persian_text_stays_under_both_ceilings():
    """Persian text is multi-byte per character — the char budget and the
    byte ceiling must BOTH still hold even when they diverge sharply, and
    the char-level chop (Python string slicing, not raw bytes) must never
    produce invalid UTF-8."""
    sess = _sess()
    sess.tool_history = [("Bash: " + "سلام دنیا " * 2000, True)]
    card = build_stream_card(sess, "Working")
    assert len(card) <= _CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= 32_768
    card.encode("utf-8")  # must not raise


def test_step_b_never_stops_early_on_a_char_only_fit_when_bytes_still_overflow():
    """Mutation target: Step B's own go/no-go check (``_fits``) must
    verify BOTH real, measured bounds before accepting a candidate as
    final — a char-only check would stop as soon as chars fit even
    though bytes are still over. Dense 4-byte-per-character emoji content
    sized so char-count alone comfortably fits under 8,800 once the older
    section is dropped, while the surviving emoji-heavy section's own
    bytes alone still exceed 32,768 — a scenario a char-only ``_fits``
    would wrongly accept."""
    sess = _sess()
    sess.tool_history = [("Bash: only", True)]
    sess.stream_commentary = [
        (0, "OLDER " + "a" * 500),
        (1, "NEWEST " + "\U0001f600" * 8500),
    ]
    card, dropped = build_stream_card_ex(sess, "Working")
    assert dropped is True
    assert len(card) <= _CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= 32_768


# ---- broad property sweep: both budgets + status-line-last, always -------
#
# hypothesis is not a project dependency (checked: not present in
# pyproject.toml or anywhere under tests/), so this hand-rolls a
# parametrized sweep with Python's own random, fixed seeds for
# reproducibility, rather than adding a new dependency for one test.

MARK_EMOJI = "\U0001f916"
_SWEEP_EMOJI_POOL = "\U0001f600\U0001f680\U0001f4a5\U0001f9e0\U0001f30d"


def _sweep_random_text(rng, max_len, *, emoji=False):
    n = rng.randint(0, max_len)
    if emoji:
        return "".join(rng.choice(_SWEEP_EMOJI_POOL) for _ in range(n))
    alphabet = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJ0123456789"
    return "".join(rng.choice(alphabet) for _ in range(n))


def _sweep_random_session(rng):
    """A varied TrackedSession: random prose length/density (including
    emoji-heavy, multi-byte text), random run-section row counts (some
    under _FOLD_MIN_ROWS, some well over), a random number of sections,
    and randomly-placed live and settled agent sections (with random
    tool-call counts, some under and some over the nested-fold
    threshold) — with and without enough total content to force any
    shedding at all."""
    sess = TrackedSession(name="claude-x", label="x", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - rng.uniform(0, 3600)
    for _ in range(rng.randint(0, 10)):
        kind = rng.choice(["run", "run", "run", "prose", "agent_live", "agent_settled"])
        if rng.random() < 0.5:
            text = _sweep_random_text(
                rng, rng.choice([0, 20, 200, 2000, 5000]), emoji=rng.random() < 0.2,
            )
            if text:
                sess.stream_commentary.append((len(sess.tool_history), text))
        if kind == "run":
            for _j in range(rng.randint(0, 8)):
                summary = f"Bash: {_sweep_random_text(rng, rng.choice([5, 50, 300]))}"
                sess.tool_history.append((summary, True))
        elif kind == "agent_live":
            idx = len(sess.tool_history)
            agent_type = rng.choice(["explore", "review", "crawler"])
            sess.tool_history.append((f"{MARK_EMOJI} {agent_type}", False))
            n_tools = rng.randint(0, 8)
            sess.active_subagents[f"a{idx}"] = {
                "type": agent_type,
                "started_at": time.monotonic() - rng.uniform(0, 600),
                "history_idx": idx,
                "activity": _sweep_random_text(rng, 40),
                "tools": [f"Bash: {_sweep_random_text(rng, 30)}" for _ in range(n_tools)],
            }
        elif kind == "agent_settled":
            idx = len(sess.tool_history)
            agent_type = rng.choice(["explore", "review", "crawler"])
            n_tools = rng.randint(0, 8)
            plural = "" if n_tools == 1 else "s"
            summary = f"{MARK_EMOJI} {agent_type} · {n_tools} tool call{plural} · 5s"
            sess.tool_history.append((summary, True))
            sess.finished_subagents.append({
                "type": agent_type, "started_at": 0.0, "elapsed": 5.0,
                "tool_count": n_tools,
                "tools": [f"Bash: {_sweep_random_text(rng, 30)}" for _ in range(n_tools)],
                "history_idx": idx,
            })
    return sess


@pytest.mark.parametrize("seed", [20260904, 777])
def test_property_sweep_every_generated_shape_holds_both_budgets_and_status_last(seed):
    """The actual guarantee: not a handful of fixed hand-picked cases, but
    many varied shapes (300 per seed), each independently checked against
    the REAL assembled string for both ceilings and for the status line
    sitting last on whichever frame it was rendered as."""
    rng = random.Random(seed)
    for _ in range(300):
        waiting = rng.random() < 0.1
        final = rng.random() < 0.1 and not waiting
        sess = _sweep_random_session(rng)
        card, _dropped = build_stream_card_ex(
            sess, "Working", final=final, waiting=waiting,
        )
        assert len(card) <= _CARD_CHAR_BUDGET
        assert len(card.encode("utf-8")) <= 32_768
        last_line = card.rstrip("\n").splitlines()[-1] if card else ""
        mark = "✅" if final else ("\U0001f504" if waiting else "⏳")
        assert last_line.startswith(mark), (seed, card[-120:])


# ---- status line at the bottom ("status-line-at-card-bottom") --------------

def test_status_line_is_last_on_every_frame():
    """Busy, waiting and final frames all end with their status line —
    the one position Telegram keeps on screen."""
    sess = _sess("omni")
    sess.busy_started_at = time.monotonic() - 12
    sess.tool_history = [("Bash: ls", True)]
    sess.stream_commentary = [(0, "opening prose")]
    busy = build_stream_card(sess, "Working")
    final = build_stream_card(sess, "Done", final=True)
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": 1.0}
    waiting = build_stream_card(sess, "Working", waiting=True)
    assert busy.splitlines()[-1].startswith("⏳ **omni** ·")
    assert final.splitlines()[-1].startswith("✅ **omni** ·")
    assert waiting.splitlines()[-1].startswith("🔄 **omni** ·")
    # ...and nothing status-shaped at the top: the story leads.
    for card in (busy, final, waiting):
        assert card.startswith("> opening prose")


def test_status_line_tally_excludes_settled_agent_rows():
    """"agent activity rows on the busy card" requirement 3: neither a
    live NOR a settled agent row ever contributes a "🤖 ×N" tally
    segment. Mutation target: remove the startswith(_SUBAGENT_MARK):
    continue guard in _status_line's tally loop."""
    sess = _sess()
    sess.tool_history = [
        ("\U0001f916 explore", False),
        ("\U0001f916 review · 3 tool calls · 5s", True),
        ("Bash: ls", True),
    ]
    card = build_stream_card(sess, "Working")
    assert "\U0001f916 ×" not in card
    assert "Bash ×1" in card


def test_status_line_tally_still_counts_parent_task_tool_call():
    """Sanity: the parent's own Task/Agent-launching tool call is a
    completely different summary shape ("Task: <description>", no 🤖
    prefix) and keeps tallying normally — the guard only excludes rows
    that actually start with the agent marker."""
    sess = _sess()
    sess.tool_history = [("Task: audit the repo", True)]
    card = build_stream_card(sess, "Working")
    assert "Task ×1" in card


def test_status_line_survives_every_shedding_shape():
    """Several folded sections, whole-section Step B drops, and the raw
    char backstop all leave the status line as the card's last line."""
    sess = _sess("omni")
    sess.busy_started_at = time.monotonic() - 90
    # Folds survive: six small older runs, split by short prose, none of
    # them the newest — each gets its own <details> block.
    sess.tool_history = []
    for i in range(6):
        sess.tool_history += [(f"Bash: r{i}-{j}", True) for j in range(5)]
    sess.stream_commentary = [(k * 5, f"PROSE-{k}") for k in range(6)]
    folded = build_stream_card(sess, "Working")
    # Step B: prose so heavy that even with every eligible run folded, the
    # oldest whole prose section must be dropped to fit.
    sess.stream_commentary = [(k * 5, f"BIG-{k:02d} " + "q" * 1500) for k in range(6)]
    dropped_whole = build_stream_card(sess, "Working")
    # Raw char backstop: one row that alone blows the ceiling — nothing to
    # fold or drop, so it chops straight away.
    sess.tool_history = [("Bash: " + "z" * 60000, True)]
    sess.stream_commentary = []
    backstop = build_stream_card(sess, "Working")
    for card in (folded, dropped_whole, backstop):
        assert len(card) <= _CARD_CHAR_BUDGET
        assert len(card.encode("utf-8")) <= 32768
        assert card.splitlines()[-1].startswith("⏳ **omni** ·")
    assert "<details" in folded and "tool call" in folded
    assert "BIG-00" not in dropped_whole and "BIG-05" in dropped_whole
    assert backstop.startswith("…")


def test_reserve_keeps_the_card_clean_across_the_ceiling_boundary():
    """The fitter's reserves are sized from the status line itself, so a
    card crossing either ceiling sheds a whole section or chops cleanly,
    rather than the status line itself ever getting clipped
    ("status-line-at-card-bottom").

    Geometry: a single unbroken run (no commentary splits it, so it is
    trivially the newest run — never wrapped, rule 3) grown past the
    char budget, with MANY distinct tool names (a fat status line, so an
    unreserved status would be what tips the card over). Sweeps a range
    straddling the ceiling to catch an off-by-one in the reserve math.
    """
    for n in range(810, 875, 7):
        sess = _sess("omni")
        sess.busy_started_at = time.monotonic() - 12
        sess.tool_history = [
            (f"Tool{i % 40}: {'k' * 20}-{i}", True) for i in range(n)
        ]
        card = build_stream_card(sess, "Working")
        assert len(card) <= _CARD_CHAR_BUDGET, n
        assert len(card.encode("utf-8")) <= 32768, n
        assert card.splitlines()[-1].startswith("⏳ **omni** ·"), n


def test_newest_content_and_agent_row_survive_extreme_pressure():
    """Review findings rev-iter1-001 and rev-iter1-006, pinned with the
    reviewer's own reproduction.

    001: an unauthorized "second wave" dropped EVERY non-agent section —
    the newest prose and the newest run included — whenever an agent
    section was present and the protected floor still overflowed, leaving
    a card whose status line counted 150 tool calls above a body with
    none. Design rule 7 forbids dropping the newest run or newest prose.

    006: removing that wave exposed the opposite loss — the tail-keeping
    chop deleted the agent row outright, because an agent sits
    chronologically before the newest content. The chop now keeps agent
    rows (never their nested folds) and cuts only what follows.
    """
    sess = _sess("probe")
    sess.busy_started_at = time.monotonic() - 30
    sess.stream_commentary.append((0, "OLD-NARRATIVE " + "o" * 2000))
    for i in range(5):
        sess.record_tool(f"Bash: old-{i} " + "x" * 200, True)
    idx = sess.record_tool("\U0001f916 agent starting", False)
    sess.active_subagents["a1"] = {
        "type": "worker", "activity": "digging",
        "started_at": time.monotonic() - 20, "history_idx": idx,
        "tools": [f"Read: /path/to/file_{i}.py" for i in range(40)],
    }
    sess.stream_commentary.append(
        (len(sess.tool_history), "Newest prose sentence. " * 60))
    for i in range(150):
        sess.record_tool(f"Grep: some_pattern_{i} in some/really/long/path", True)

    card, dropped = build_stream_card_ex(sess, "Working")

    assert dropped is True
    assert "Newest prose sentence" in card          # rev-iter1-001
    assert card.count("some_pattern_") > 0          # rev-iter1-001
    assert "worker" in card                          # rev-iter1-006
    assert "OLD-NARRATIVE" not in card               # oldest still yields first
    assert len(card) <= 8_800
    assert card.rstrip().splitlines()[-1].startswith("\u23f3 **probe** \u00b7")


def test_chop_never_leaves_unbalanced_details_markup_around_an_agent_row():
    """Review rev-iter2-001: the agent-preserving chop used to slice the
    ORIGINAL body, which still carried the agent's own section, so the kept
    row appeared twice and the cut could land inside that section's
    `<details>` span — emitting a dangling `</details>` with no opening tag
    (reproduced at 162 newest rows). The chop now excludes agent sections
    from the text it slices. Swept across the band where the overage is
    smaller than the agent section itself, which is where it bit."""
    for nrows in range(150, 175):
        sess = _sess("probe")
        sess.busy_started_at = time.monotonic() - 20
        sess.stream_commentary.append((0, "OLD " + "o" * 400))
        for i in range(5):
            sess.record_tool(f"Bash: old-{i} " + "x" * 120, True)
        idx = sess.record_tool("\U0001f916 agent starting", False)
        sess.active_subagents["a1"] = {
            "type": "worker", "activity": "digging",
            "started_at": time.monotonic() - 20, "history_idx": idx,
            "tools": [f"Read: /path/to/file_{i}.py" for i in range(6)],
        }
        sess.stream_commentary.append(
            (len(sess.tool_history), "Short final answer sentence."))
        for i in range(nrows):
            sess.record_tool(f"Grep: some_pattern_{i} in some/really/long/path", True)

        card, _dropped = build_stream_card_ex(sess, "Working")
        assert card.count("<details>") == card.count("</details>"), nrows
        assert card.count("\U0001f916 worker") <= 1, nrows  # never duplicated
        assert len(card) <= 8_800, nrows


def test_no_dangling_details_tag_across_a_family_of_pressured_shapes():
    """Review rev-iter3-001: Step B's walk used to BREAK at the first
    protected section, so later droppable sections were never considered
    and the raw chop landed inside one of their folds — leaving a
    `</details>` with no opening tag, which the plain-text degrade path
    cannot strip either (its regex needs a matched pair).

    The walk now steps past protected sections, and every chop result is
    swept for an unmatched close tag. Verified across a family of shapes
    rather than one: the previous code failed at (5 old rows, 60-char
    padding, 160/162/164 newest rows) among others.
    """
    for n_old in (5, 10):
        for pad in (60, 120):
            for n_new in (150, 160, 162, 164, 180):
                sess = _sess("probe")
                sess.busy_started_at = time.monotonic() - 20
                sess.stream_commentary.append((0, "ONLY-PROSE " + "o" * 200))
                for i in range(n_old):
                    sess.record_tool(f"Bash: A-{i} " + "x" * pad, True)
                idx = sess.record_tool("\U0001f916 agent starting", False)
                sess.active_subagents["a1"] = {
                    "type": "worker", "activity": "digging",
                    "started_at": time.monotonic() - 20, "history_idx": idx,
                    "tools": [f"Read: /f{i}.py" for i in range(6)],
                }
                for i in range(n_new):
                    sess.record_tool(
                        f"Grep: some_pattern_{i} in some/really/long/path", True)

                card, _dropped = build_stream_card_ex(sess, "Working")
                shape = (n_old, pad, n_new)
                assert card.count("<details>") == card.count("</details>"), shape
                assert "</details>" not in card.split("<details>")[0], shape
                assert len(card) <= 8_800, shape
                assert card.rstrip().splitlines()[-1].startswith("⏳ "), shape
