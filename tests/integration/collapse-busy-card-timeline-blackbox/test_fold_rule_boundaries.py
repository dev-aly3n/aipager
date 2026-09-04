"""Equivalence partitioning + boundary-value analysis against design.md's
numbered "Rules" (1-9), driven only through the exported
``build_stream_card_ex``/``build_stream_card`` and the documented
``TrackedSession`` fields (entrypoints.md).

Distinct from the Developer's own
``tests/integration/collapse-busy-card-timeline/`` suite: this file
targets the RULE BOUNDARIES themselves (exactly 2 vs exactly 3 rows/tools,
an unmatched/aged-out settled-agent slot, drop-oldest-first protecting
agent + newest sections, determinism) rather than end-to-end reference
shapes.
"""

from __future__ import annotations

import time

from aipager.bot.animation import build_stream_card, build_stream_card_ex
from aipager.state import Status, TrackedSession

MARK = "\U0001f916"  # 🤖


def _sess(label="jim"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 20
    return sess


# ---- Rule 4: fewer than _FOLD_MIN_ROWS (3) never folds ----------------------

def _sess_older_section_of_size(n):
    sess = _sess()
    total = n + 5
    sess.tool_history = [(f"Bash: step-{i}", True) for i in range(total)]
    # anchor at 0 makes rows [0, n) the OLDER section; anchor at n makes
    # the rest the newest section (never wrapped regardless of size).
    sess.stream_commentary = [(0, "Older phase."), (n, "Newest phase.")]
    return sess


def test_older_section_of_exactly_two_rows_never_folds():
    sess = _sess_older_section_of_size(2)
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert "<details" not in card


def test_older_section_of_exactly_three_rows_does_fold():
    sess = _sess_older_section_of_size(3)
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert "<details><summary>▸ 3 tool call" in card


# ---- Rule 3: the newest run section is never wrapped, regardless of size ----

def test_single_huge_run_with_no_commentary_never_wraps():
    """A session carrying only tool_history (no commentary) is a single
    section that is also the newest — never wrapped, even when huge
    enough to force the raw chop backstop."""
    sess = _sess()
    sess.tool_history = [
        (f"Bash: step-{i} " + "z" * 300, True) for i in range(500)
    ]
    card, truncated = build_stream_card_ex(sess, "Working")
    assert "<details" not in card
    assert truncated is True  # budget pressure forced the raw chop instead


def test_newest_run_after_commentary_stays_unwrapped_even_with_many_rows():
    """With commentary present, the run AFTER the last commentary anchor
    is the newest section — never wrapped, however many rows it has,
    while an EARLIER section with the same row count does fold."""
    sess = _sess()
    older = [(f"Bash: old-{i}", True) for i in range(6)]
    newest = [(f"Bash: new-{i}", True) for i in range(6)]
    sess.tool_history = older + newest
    sess.stream_commentary = [(0, "Older phase."), (6, "Newest phase.")]
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert card.count("<details") == 1  # only the older section folds
    # every newest-section row is visible plainly, outside any block
    fold_span = card[card.index("<details"): card.index("</details>") + len("</details>")]
    for i in range(6):
        row = f"Bash: new-{i}"
        assert row in card
        assert row not in fold_span


# ---- Rule 1: commentary never folds, at any age -----------------------------

def test_old_commentary_text_never_appears_inside_a_details_span():
    sess = _sess()
    rows = 40
    sess.tool_history = [(f"Bash: step-{i}", True) for i in range(rows)]
    sess.stream_commentary = [
        (0, "ANCIENT_COMMENTARY_MARKER_ONE"),
        (10, "OLD_COMMENTARY_MARKER_TWO"),
        (20, "RECENT_COMMENTARY_MARKER_THREE"),
        (30, "NEWEST_COMMENTARY_MARKER_FOUR"),
    ]
    card, _truncated = build_stream_card_ex(sess, "Working")
    import re
    for span in re.findall(r"<details>.*?</details>", card, re.DOTALL):
        assert "ANCIENT_COMMENTARY_MARKER_ONE" not in span
        assert "OLD_COMMENTARY_MARKER_TWO" not in span
        assert "RECENT_COMMENTARY_MARKER_THREE" not in span
        assert "NEWEST_COMMENTARY_MARKER_FOUR" not in span
    # and all four markers are still present SOMEWHERE, visible
    for marker in [
        "ANCIENT_COMMENTARY_MARKER_ONE", "OLD_COMMENTARY_MARKER_TWO",
        "RECENT_COMMENTARY_MARKER_THREE", "NEWEST_COMMENTARY_MARKER_FOUR",
    ]:
        assert marker in card


def test_commentary_never_folds_even_under_severe_budget_pressure():
    """Same as above but with fat rows that force budget shedding — an
    older section may be DROPPED whole (rule 7) but if its commentary
    marker survives at all, it must never be inside a block."""
    sess = _sess()
    sess.tool_history = [
        (f"Bash: step-{i} " + "y" * 250, True) for i in range(200)
    ]
    sess.stream_commentary = [
        (0, "SURVIVING_OR_DROPPED_MARKER_A"),
        (60, "SURVIVING_OR_DROPPED_MARKER_B"),
        (120, "SURVIVING_OR_DROPPED_MARKER_C"),
        (180, "NEWEST_MARKER_D"),
    ]
    card, _truncated = build_stream_card_ex(sess, "Working")
    import re
    for span in re.findall(r"<details>.*?</details>", card, re.DOTALL):
        assert "MARKER" not in span
    # the newest commentary (never dropped, never the newest-run backstop
    # target) must always survive.
    assert "NEWEST_MARKER_D" in card


# ---- Rule 5 / 6 boundary: 2 vs 3 tool calls gates the nested fold ----------

def test_live_agent_with_two_tools_shows_them_plainly_not_folded():
    sess = _sess()
    sess.tool_history = [(f"{MARK} explorer", False)]
    sess.active_subagents["a1"] = {
        "type": "explorer", "activity": "Bash: probing",
        "started_at": time.monotonic() - 5, "history_idx": 0,
        "tools": ["Grep: a", "Grep: b"],
    }
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert "<details" not in card
    assert "Grep: a" in card and "Grep: b" in card


def test_live_agent_with_three_tools_gets_its_own_nested_fold():
    sess = _sess()
    sess.tool_history = [(f"{MARK} explorer", False)]
    sess.active_subagents["a1"] = {
        "type": "explorer", "activity": "Bash: probing",
        "started_at": time.monotonic() - 5, "history_idx": 0,
        "tools": ["Grep: a", "Grep: b", "Grep: c"],
    }
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert "<details><summary>▸ 3 tool call" in card


def test_live_agent_row_itself_is_never_wrapped_regardless_of_tool_count():
    sess = _sess()
    sess.tool_history = [(f"{MARK} explorer", False)]
    sess.active_subagents["a1"] = {
        "type": "explorer", "activity": "Bash: probing",
        "started_at": time.monotonic() - 5, "history_idx": 0,
        "tools": [f"Grep: {i}" for i in range(9)],
    }
    card, _truncated = build_stream_card_ex(sess, "Working")
    # the agent's OWN row text precedes any <details> tag, never inside one
    row_pos = card.index(f"{MARK} explorer")
    first_details = card.find("<details")
    assert first_details == -1 or row_pos < first_details


def test_settled_agent_with_two_tools_shows_them_plainly_not_folded():
    sess = _sess()
    sess.tool_history = [(f"{MARK} archivist", True)]
    sess.finished_subagents.append({
        "type": "archivist", "started_at": 0.0, "elapsed": 12.0,
        "tool_count": 2, "tools": ["Read: x", "Read: y"], "history_idx": 0,
    })
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert "<details" not in card
    assert "Read: x" in card and "Read: y" in card


def test_settled_agent_with_three_tools_gets_its_own_nested_fold():
    sess = _sess()
    sess.tool_history = [(f"{MARK} archivist", True)]
    sess.finished_subagents.append({
        "type": "archivist", "started_at": 0.0, "elapsed": 12.0,
        "tool_count": 3, "tools": ["Read: x", "Read: y", "Read: z"],
        "history_idx": 0,
    })
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert "<details><summary>▸ 3 tool call" in card


def test_settled_agent_nested_fold_count_reflects_the_tools_list_not_tool_count():
    """The fold's own summary counts what is physically present — an
    inflated/mismatched tool_count field must not leak into the summary
    number (design.md: 'ONE number, the count of rows physically
    present')."""
    sess = _sess()
    sess.tool_history = [(f"{MARK} archivist", True)]
    sess.finished_subagents.append({
        "type": "archivist", "started_at": 0.0, "elapsed": 12.0,
        "tool_count": 99, "tools": ["A", "B", "C", "D"], "history_idx": 0,
    })
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert "<details><summary>▸ 4 tool call" in card
    assert "99 tool call" not in card


def test_settled_agent_slot_with_no_matching_finished_entry_renders_plainly():
    """An entry that aged out past FINISHED_SUBAGENTS_CAP is simply gone
    from finished_subagents — the row it would have annotated must still
    render (as an ordinary completed row) without crashing or fabricating
    a fold from nothing."""
    sess = _sess()
    sess.tool_history = [(f"{MARK} archivist", True)]
    # finished_subagents deliberately left empty: simulates the eviction.
    card, truncated = build_stream_card_ex(sess, "Working")
    assert "<details" not in card
    assert f"{MARK} archivist" in card
    assert truncated is False


# ---- Rule 7: whole-section drop protects agent + newest sections ----------

def test_budget_pressure_drops_oldest_commentary_section_before_touching_agent():
    sess = _sess()
    fat = "q" * 400
    sess.tool_history = [(f"Bash: old-{i} {fat}", True) for i in range(40)]
    sess.stream_commentary = [(0, "OLDEST_SECTION_MARKER " + "w" * 500)]
    live_idx = len(sess.tool_history)
    sess.tool_history.append((f"{MARK} explorer", False))
    sess.active_subagents["a1"] = {
        "type": "explorer", "activity": "Bash: probing",
        "started_at": time.monotonic() - 5, "history_idx": live_idx,
        "tools": ["Grep: a", "Grep: b", "Grep: c"],
    }
    card, truncated = build_stream_card_ex(sess, "Working")
    assert len(card) <= 8_800
    assert len(card.encode("utf-8")) <= 32_768
    # the live agent row must survive — agent sections are never dropped.
    assert f"{MARK} explorer" in card
    if truncated:
        assert "OLDEST_SECTION_MARKER" not in card


def test_budget_pressure_never_drops_the_physically_last_section():
    sess = _sess()
    fat = "q" * 400
    sess.tool_history = [(f"Bash: step-{i} {fat}", True) for i in range(60)]
    sess.stream_commentary = [(i * 10, f"Phase {i} " + "w" * 300) for i in range(6)]
    card, _truncated = build_stream_card_ex(sess, "Working")
    assert len(card) <= 8_800
    assert len(card.encode("utf-8")) <= 32_768
    assert "Bash: step-59" in card  # the very last row is never the drop target


# ---- Rule 9 / determinism: pure, no I/O, identical for identical input ----

def test_build_stream_card_ex_is_deterministic_across_repeated_calls():
    sess = _sess()
    sess.tool_history = [(f"Bash: step-{i}", True) for i in range(30)]
    sess.stream_commentary = [(0, "Phase one."), (15, "Phase two.")]
    first, first_trunc = build_stream_card_ex(sess, "Working")
    second, second_trunc = build_stream_card_ex(sess, "Working")
    assert first == second
    assert first_trunc == second_trunc


def test_build_stream_card_thin_wrapper_matches_the_ex_variants_markdown():
    sess = _sess()
    sess.tool_history = [(f"Bash: step-{i}", True) for i in range(30)]
    sess.stream_commentary = [(0, "Phase one."), (15, "Phase two.")]
    ex_card, _truncated = build_stream_card_ex(sess, "Working")
    plain_card = build_stream_card(sess, "Working")
    assert ex_card == plain_card
