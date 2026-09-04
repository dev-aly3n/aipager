"""spec.md requirement 4 ("Shedding"): an active agent row is never
folded or dropped while the agent is still running — driven end-to-end
through ``build_stream_card_ex`` with an oversized ``tool_history``/
``stream_commentary`` to force genuine truncation, exactly the scenario
a long-running turn with many parent tool calls produces in production.

Updated for "collapse-busy-card-timeline": the single-global-budget
phase machinery this file originally targeted is retired in favor of
per-section folding (design.md) — "phase2-only" is now "step-b-only",
and the specific retired placeholder text this file used to check for
is gone, replaced by checking the row never lands inside a `<details>`
block at all.
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

    commentary = None
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
    elif agent_position == "step-b-only":
        # Only ONE "run" section exists in the whole timeline (the
        # trailing "Bash: newest" row, 1 row, so it never folds anyway) —
        # there is nothing foldable at all, so ALL shedding pressure
        # lands on Step B's whole-section drop walk. That walk must pass
        # THROUGH the agent's own section (kind == "agent-run") without
        # dropping it, and keep going to shed the prose sections flanking
        # it — the specific gap research.md gotcha ~53 names (today's
        # Step B has no agent-run exclusion of its own unless it is
        # added explicitly, distinct from the newest-run/newest-prose
        # kind filters).
        history = [agent_row, ("Bash: newest", True)]
        idx = 0
        # PROSE-A alone is deliberately too small to free enough room on
        # its own — Step B must continue PAST the agent's section (skip,
        # not stop) and also drop PROSE-B (chronologically AFTER the
        # agent) to fit, proving the loop didn't halt at the agent.
        commentary = [
            (0, "PROSE-A " + "a" * 500),
            (1, "PROSE-B " + "b" * 9000),
            (2, "PROSE-C " + "c" * 60),
        ]
    else:
        raise ValueError(agent_position)

    sess.tool_history = history
    sess.active_subagents["c1"] = {
        "type": "crawler", "started_at": time.monotonic() - 8,
        "history_idx": idx, "activity": "Bash: find . -name '*.py'",
    }
    if commentary is not None:
        sess.stream_commentary = commentary
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


def test_active_agent_row_is_never_folded_behind_a_details_block():
    """A lone-row run would normally be too short to fold anyway (rule 4:
    under 3 rows never folds) — but the still-active agent's own row must
    never be wrapped in a <details> block regardless of row count."""
    sess = _oversized_sess(agent_position="middle")
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    if "<details>" in card:
        assert f"{MARK} crawler" not in card.split("</details>", 1)[0]


def test_active_agent_row_survives_when_only_step_b_pressure_reaches_it():
    """Extends this file's own documented gap: neither "middle" nor
    "newest" ever forces whole-section dropping. "step-b-only" is sized
    so there is nothing foldable at all, and the fitter must reach Step
    B's own walk to decide the agent's section's neighbors — the exact
    untested path research.md gotcha ~53 named."""
    sess = _oversized_sess(agent_position="step-b-only")
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    assert f"{MARK} crawler · Bash: find . -name '*.py' · 8s" in card


def test_active_agent_row_is_never_inside_the_details_block_under_step_b_pressure():
    sess = _oversized_sess(agent_position="step-b-only")
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    if "</details>" in card:
        inside_details = card.split("</details>", 1)[0]
        assert f"{MARK} crawler" not in inside_details


def test_step_b_continues_past_the_agent_row_to_shed_what_comes_after_it():
    """The specific `continue`-not-`break` fix: a prose section
    positioned AFTER the agent's own section (PROSE-B) must still be
    reachable and dropped — PROSE-A alone is too small to free enough
    room, so this only passes if Step B kept walking past the agent's
    index rather than stopping dead there. PROSE-C (the newest prose)
    stays protected and visible throughout."""
    sess = _oversized_sess(agent_position="step-b-only")
    card, truncated = build_stream_card_ex(sess, "Working")
    assert truncated is True
    assert "PROSE-A" not in card
    assert "PROSE-B" not in card  # only reachable by continuing past the agent
    assert "PROSE-C" in card
    assert f"{MARK} crawler" in card  # the agent itself is untouched throughout


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
