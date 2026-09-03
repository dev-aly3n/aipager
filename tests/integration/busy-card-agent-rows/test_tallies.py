"""spec.md requirement 3 ("Tallies/status line"): the bottom status
line's per-tool tallies exclude agent-attributed tools (they show up on
their own row instead); a parent's own agent-launching tool call (e.g.
a "Task:" row) still tallies normally under its own name.
"""

from __future__ import annotations

import time

from aipager.bot.animation import build_stream_card
from aipager.state import Status, TrackedSession

MARK = "\U0001f916"


def _sess():
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic()
    return sess


def test_tally_never_shows_a_bot_marker_segment_for_a_live_agent_row():
    sess = _sess()
    sess.tool_history = [(f"{MARK} sweeper", False), ("Read: /a", True)]
    sess.active_subagents["s1"] = {
        "type": "sweeper", "started_at": time.monotonic(), "history_idx": 0,
    }
    card = build_stream_card(sess, "Working")
    assert f"{MARK} ×" not in card


def test_tally_never_shows_a_bot_marker_segment_for_a_settled_agent_row():
    sess = _sess()
    sess.tool_history = [
        (f"{MARK} sweeper · 4 tool calls · 9s", True),
        (f"{MARK} auditor · 1 tool call · 2s", True),
    ]
    card = build_stream_card(sess, "Working")
    assert f"{MARK} ×" not in card


def test_tally_excludes_attributed_tool_but_counts_unattributed_ones():
    """A mixed turn: one agent's tool calls are folded into its own row
    (no per-name tally contribution), while the parent's OWN unattributed
    Bash calls still tally under "Bash ×N"."""
    sess = _sess()
    sess.tool_history = [
        (f"{MARK} sweeper · 5 tool calls · 9s", True),
        ("Bash: ls", True),
        ("Bash: pwd", True),
    ]
    card = build_stream_card(sess, "Working")
    assert "Bash ×2" in card
    assert f"{MARK} ×" not in card


def test_tally_still_counts_a_parents_own_task_launching_tool_call():
    """A "Task: <description>" row is a completely different summary
    shape (no 🤖 prefix) — the parent's own Task/Agent-launching call
    keeps tallying normally, untouched by the exclusion guard."""
    sess = _sess()
    sess.tool_history = [("Task: run the audit subagent", True)]
    card = build_stream_card(sess, "Working")
    assert "Task ×1" in card


def test_tally_counts_multiple_task_calls_alongside_excluded_agent_rows():
    sess = _sess()
    sess.tool_history = [
        ("Task: audit", True),
        ("Task: explore", True),
        (f"{MARK} explore · 3 tool calls · 4s", True),
    ]
    card = build_stream_card(sess, "Working")
    assert "Task ×2" in card
    assert f"{MARK} ×" not in card
