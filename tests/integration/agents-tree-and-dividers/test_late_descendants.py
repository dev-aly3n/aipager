"""Agents a listed agent starts after the answer went out join the block
under their parent, live (design.md success criteria 3, 4, 6, 7;
entrypoints.md "Late descendants").

Driven by hook datagrams on a virtual loop; the block is read from what
Telegram was sent (the answer's last edit)."""

from __future__ import annotations

import re

import pytest

from tests import agents_line_waiting_bb as h

R1 = "a505c081ca3926ed5"
T = "ac1d54c85d78f59ea"
G = "ab77e10f00d1c2b3a"
M = "a99000000000000aa"
R1_TYPE, T_TYPE, G_TYPE = "pipeline-runner", "ship-tester", "Explore"
ROBOT = "\U0001f916"


@pytest.fixture
def sent(rig):
    """R1 (the session's own, background) works; the answer goes out."""
    rig.call(R1_TYPE, "run the pipeline")
    rig.start(R1, R1_TYPE)
    rig.link(R1, by="")
    rig.answer()
    return rig


def _tree(shapes, block):
    return shapes.tree(block)


# ---- the block at send ----------------------------------------------------------------

def test_at_send_the_block_holds_r1_alone(sent, shapes):
    assert _tree(shapes, sent.last_block()) == [(1, R1_TYPE)]


# ---- a helper started after the send ------------------------------------------------------

@pytest.fixture
def helper(sent):
    sent.launch(T, T_TYPE, "Write and run criteria tests", by=R1)
    sent.tick(31)
    return sent


def test_a_late_helper_appears_under_its_parent(helper, shapes):
    assert _tree(shapes, helper.last_block()) == [(1, R1_TYPE), (2, T_TYPE)]


def test_a_late_helper_row_shows_its_description(helper):
    assert f"- {ROBOT} **{T_TYPE}** \u00b7 Write and run criteria tests" in \
        helper.last_block().split("\n")


def test_a_late_helper_is_counted_in_the_header(helper):
    assert h.header(helper.last_block()) == "\u23f3 2 agents working"


def test_a_late_helper_settles_done_under_its_parent(helper, shapes):
    helper.stop(T, T_TYPE)
    helper.tick(6)
    helper.tick(31)
    block = helper.last_block()
    assert _tree(shapes, block) == [(1, R1_TYPE), (2, T_TYPE)]


def test_a_late_helper_settles_with_a_done_row_and_clock(helper):
    helper.stop(T, T_TYPE)
    helper.tick(6)
    helper.tick(31)
    lines = [x for x in helper.last_block().split("\n") if T_TYPE in x]
    assert len(lines) == 1 and re.fullmatch(
        rf"- \u2713 {ROBOT} \*\*{T_TYPE}\*\* \u00b7 done \(\d+:\d\d\)", lines[0])


def test_a_grandchild_appears_under_r1_with_the_hint(helper, shapes):
    helper.launch(G, G_TYPE, "look around", by=T)
    helper.tick(31)
    assert _tree(shapes, helper.last_block()) == [
        (1, R1_TYPE), (2, T_TYPE), (2, f"{T_TYPE} \u203a {G_TYPE}")]


def test_a_grandchild_counts_in_the_header(helper):
    helper.launch(G, G_TYPE, "look around", by=T)
    helper.tick(31)
    assert h.header(helper.last_block()) == "\u23f3 3 agents working"


def test_a_grandchild_keeps_its_place_after_its_parent_stops(helper, shapes):
    helper.launch(G, G_TYPE, "look around", by=T)
    helper.tick(31)
    helper.stop(T, T_TYPE)
    helper.tick(6)
    helper.tick(31)
    assert _tree(shapes, helper.last_block()) == [
        (1, R1_TYPE), (2, T_TYPE), (2, f"{T_TYPE} \u203a {G_TYPE}")]


def test_a_late_great_grandchild_appears_too(helper, shapes):
    helper.launch(G, G_TYPE, "look around", by=T)
    helper.launch(M, "ship-reviewer", "review", by=G)
    helper.tick(31)
    assert _tree(shapes, helper.last_block())[-1] == (2, f"{G_TYPE} \u203a ship-reviewer")


# ---- the main session's later agents never join --------------------------------------------

def test_a_later_main_agent_never_joins_the_earlier_block(sent, shapes):
    sent.call("ship-reviewer", "review it")
    sent.start(M, "ship-reviewer")
    sent.link(M, by="")
    sent.tick(31)
    assert "ship-reviewer" not in [n for _, n in _tree(shapes, sent.last_block())]


def test_a_later_main_agent_does_not_change_the_count(sent):
    sent.call("ship-reviewer", "review it")
    sent.start(M, "ship-reviewer")
    sent.link(M, by="")
    sent.tick(31)
    assert h.header(sent.last_block()) == "\u23f3 1 agent working"


# ---- an ambiguous launcher ------------------------------------------------------------------

@pytest.fixture
def ambiguous(sent):
    """R1 and the main session both call ship-tester; T starts: no link."""
    sent.call(T_TYPE, "main work")
    sent.call(T_TYPE, "helper work", by=R1)
    sent.start(T, T_TYPE)
    sent.tick(31)
    return sent


def test_an_ambiguous_child_stays_out_of_the_block(ambiguous, shapes):
    assert _tree(shapes, ambiguous.last_block()) == [(1, R1_TYPE)]


def test_an_ambiguous_child_joins_under_r1_at_its_postooluse(ambiguous, shapes):
    ambiguous.link(T, by=R1)
    ambiguous.tick(31)
    assert _tree(shapes, ambiguous.last_block()) == [(1, R1_TYPE), (2, T_TYPE)]


def test_an_ambiguous_child_never_shows_main_works_description(ambiguous):
    ambiguous.link(T, by=R1)
    ambiguous.tick(31)
    assert "main work" not in ambiguous.last_block()


# ---- a running-at-send child whose link arrives later moves under its parent -------------------

def test_a_top_level_row_moves_under_its_parent_on_the_link(rig, shapes):
    rig.call(R1_TYPE, "run the pipeline")
    rig.start(R1, R1_TYPE)
    rig.link(R1, by="")
    rig.call(T_TYPE, "main work")
    rig.call(T_TYPE, "helper work", by=R1)
    rig.start(T, T_TYPE)
    first = shapes.tree(rig.answer())
    rig.link(T, by=R1)
    rig.tick(31)
    assert (first, shapes.tree(rig.last_block())) == (
        [(1, R1_TYPE), (1, T_TYPE)], [(1, R1_TYPE), (2, T_TYPE)])


# ---- a link that arrives after the child stopped ----------------------------------------------

def test_a_link_after_the_child_stopped_adds_a_done_row(sent, shapes):
    """A foreground helper of R1: its exact link (R1's PostToolUse) only
    comes when it ends, and here no launcher match was possible."""
    sent.call(T_TYPE, "main work")
    sent.call(T_TYPE, "helper work", by=R1)
    sent.start(T, T_TYPE)
    sent.stop(T, T_TYPE)
    sent.link(T, by=R1, response={"status": "completed", "agentId": T,
                                  "content": [{"type": "text", "text": "done"}]})
    sent.tick(31)
    rows = [x for x in sent.last_block().split("\n") if T_TYPE in x]
    assert len(rows) == 1 and rows[0].startswith(f"- \u2713 {ROBOT} **{T_TYPE}**")


# ---- waiting rows live ------------------------------------------------------------------------

def test_a_parent_waiting_on_its_helper_says_so(rig):
    rig.agent_call(R1, R1_TYPE)
    rig.agent_call(T, T_TYPE, by=R1)
    rig.stop(R1, R1_TYPE)
    block = rig.answer()
    assert h.row_of(block, R1_TYPE) == \
        f"\u2016 {ROBOT} **{R1_TYPE}** \u00b7 waiting for its own agents"


def test_a_parent_waiting_after_a_tool_never_shows_the_tool(rig):
    rig.agent_call(R1, R1_TYPE)
    rig.tool(R1, "Bash", "pytest -q", post=False)
    rig.agent_call(T, T_TYPE, by=R1)
    rig.stop(R1, R1_TYPE)
    assert "pytest" not in h.row_of(rig.answer(), R1_TYPE)


def test_a_parent_starting_to_wait_after_the_send_turns_waiting(rig):
    rig.agent_call(R1, R1_TYPE)
    rig.agent_call(T, T_TYPE, by=R1)
    rig.answer()
    rig.stop(R1, R1_TYPE)
    rig.tick(31)
    assert h.is_waiting_row(h.row_of(rig.last_block(), R1_TYPE))


def test_the_waiting_parents_helper_is_nested(rig, shapes):
    rig.agent_call(R1, R1_TYPE)
    rig.agent_call(T, T_TYPE, by=R1)
    rig.stop(R1, R1_TYPE)
    assert shapes.tree(rig.answer()) == [(1, R1_TYPE), (2, T_TYPE)]


# ---- every live edit stays a valid tree ---------------------------------------------------------

def test_every_edit_keeps_the_divider_before_the_block(helper):
    from tests import answer_status_bb as bb
    from aipager.bot.divider import DIVIDER_SEP
    edits = bb.answer_edits(helper.wire)
    assert edits and all(DIVIDER_SEP in bb.md(e) for e in edits)


# ---- two open answers ---------------------------------------------------------------------------

SECOND = "Here is the second answer, after the agents woke the session."
R2 = "a7700000000000bb2"


def test_a_helper_of_an_agent_listed_in_two_answers_joins_both(sent, shapes):
    sent.answer(SECOND)
    sent.launch(T, T_TYPE, "Write and run criteria tests", by=R1)
    sent.tick(31)
    assert (shapes.tree(sent.last_block()), shapes.tree(sent.last_block(SECOND))) == (
        [(1, R1_TYPE), (2, T_TYPE)], [(1, R1_TYPE), (2, T_TYPE)])


def test_a_helper_of_a_later_top_joins_only_the_later_answer(sent, shapes):
    sent.call("r2-runner", "second runner")
    sent.start(R2, "r2-runner")
    sent.link(R2, by="")
    sent.answer(SECOND)
    sent.launch(T, T_TYPE, "Write and run criteria tests", by=R2)
    sent.tick(31)
    assert (T_TYPE in sent.last_block(), T_TYPE in sent.last_block(SECOND)) == (False, True)
