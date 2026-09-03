"""spec.md requirement 4 ("Shedding"): an active agent row is never
collapsed by phase-1 "▸ N tool calls" folding while the agent is still
running — driven end-to-end through ``build_stream_card_ex`` with an
oversized ``tool_history`` to force byte-pressure truncation, exactly
the scenario a long-running turn with many parent tool calls produces
in production.
"""

from __future__ import annotations

import time

from aipager.bot.animation import build_stream_card_ex
from aipager.state import Status, TrackedSession

MARK = "\U0001f916"


def _oversized_sess(*, agent_position="middle"):
    """A session whose tool_history is large enough to force the fitter
    to truncate, with one still-live agent row placed among the fat
    parent rows.

    For "middle", the OLDER run is deliberately kept short (2 rows) and
    the NEWER run large (250 rows): collapsing the small older run alone
    does not free enough bytes to fit the budget, forcing the fitter to
    also decide what to do with the agent row's own section — the
    precise condition needed to distinguish "the agent row is
    structurally protected" from "the agent row just happened to survive
    because an earlier collapse already sufficed".
    """
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 600

    agent_row = (f"{MARK} crawler", False)

    if agent_position == "middle":
        old_rows = [(f"Bash: old-{i} " + ("z" * 120), True) for i in range(2)]
        new_rows = [(f"Bash: new-{i} " + ("z" * 120), True) for i in range(250)]
        history = old_rows + [agent_row] + new_rows
        idx = len(old_rows)
    elif agent_position == "newest":
        fat_rows = [
            (f"Bash: step-{i} " + ("z" * 120), True) for i in range(250)
        ]
        history = fat_rows + [agent_row]
        idx = len(fat_rows)
    else:
        raise ValueError(agent_position)

    sess.tool_history = history
    sess.active_subagents["c1"] = {
        "type": "crawler", "started_at": time.monotonic() - 8,
        "history_idx": idx, "activity": "Bash: find . -name '*.py'",
    }
    return sess


def test_active_agent_row_text_survives_forced_truncation_in_the_middle():
    sess = _oversized_sess(agent_position="middle")
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    assert f"{MARK} crawler · Bash: find . -name '*.py' · 8s" in card


def test_active_agent_row_text_survives_forced_truncation_as_newest():
    sess = _oversized_sess(agent_position="newest")
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    assert f"{MARK} crawler · Bash: find . -name '*.py' · 8s" in card


def test_active_agent_row_is_never_replaced_by_a_single_tool_call_placeholder():
    """The specific phase-1 collapse text ("▸ 1 tool call" / "▸ _1 tool
    call_") that a lone ordinary "run" section of one row would fold
    into must never appear for the still-active agent's own row."""
    sess = _oversized_sess(agent_position="middle")
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    assert "▸ _1 tool call_" not in card
    assert "▸ 1 tool call" not in card


def test_once_settled_the_row_is_an_ordinary_row_and_may_be_folded_like_any_other():
    """Sanity/contrast: after SubagentStop, the row is ordinary — this
    test does not assert it MUST fold (phase-1's collapse condition is
    about run length, not agent-ness once settled), only that settling
    does not crash the fitter under the same byte pressure and the card
    still renders successfully."""
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 600
    fat_rows = [
        (f"Bash: step-{i} " + ("z" * 120), True) for i in range(250)
    ]
    settled_row = (f"{MARK} crawler · 40 tool calls · 8s", True)
    sess.tool_history = fat_rows[:125] + [settled_row] + fat_rows[125:]
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    assert isinstance(card, str) and len(card) > 0
