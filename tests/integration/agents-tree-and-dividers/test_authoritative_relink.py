"""The PostToolUse link is authoritative (entrypoints.md "Amendments
(orchestrator, after review-1)", "Authoritative re-link"; design.md
success criteria 6 and 7).

A launcher match linked C to the listed R1. Then Q's PostToolUse names
C, and Q's chain reaches nothing the answer lists:
- C sent with the answer becomes a top row;
- C that joined late leaves the block with the late rows under it, and
  their ids leave the record;
- a sent row under a removed late row moves to its nearest remaining
  listed ancestor, or to the top.
The same parent confirmed again, or a PostToolUse with no earlier link,
leaves the row where it is.

Driven by hook datagrams; the block is read from the answer's last edit,
the record from ``sess.agents_lines``."""

from __future__ import annotations

import pytest

from tests import agents_line_waiting_bb as h

R1 = "a505c081ca3926ed5"    # pipeline-runner, the session's own, listed
R2 = "a7700000000000bb2"    # r2-runner, the session's own
Q = "a77e10f00d1c2b3a9"     # q-runner, the session's own, started after the send
C = "ac1d54c85d78f59ea"     # ship-tester, R1's by the launcher match
G = "ab77e10f00d1c2b3a"     # Explore
D = "ad0000000000000d4"     # ship-reviewer
R1_TYPE, R2_TYPE, Q_TYPE = "pipeline-runner", "r2-runner", "q-runner"
T, G_TYPE, D_TYPE = "ship-tester", "Explore", "ship-reviewer"
HINT = " › "


def _main(rig, agent, agent_type, desc="run it"):
    rig.call(agent_type, desc)
    rig.start(agent, agent_type)
    rig.link(agent, by="")


def _q(rig):
    """Q: a top agent of the session's own, started after the send, so
    its chain reaches nothing the earlier answer lists."""
    _main(rig, Q, Q_TYPE, "another runner")


def _ids(rig):
    return [i for rec in rig.sess.agents_lines for i in rec.get("ids", ())]


def _tree(rig, shapes):
    return shapes.tree(rig.last_block())


# ==== C was in the block at send ===============================================================

@pytest.fixture
def sent_under_r1(rig, shapes):
    _main(rig, R1, R1_TYPE, "run the pipeline")
    rig.launch(C, T, "helper work", by=R1)
    assert shapes.tree(rig.answer()) == [(1, R1_TYPE), (2, T)]
    return rig


def test_a_sent_child_relinked_to_an_unlisted_q_becomes_a_top_row(sent_under_r1, shapes):
    _q(sent_under_r1)
    sent_under_r1.link(C, by=Q)
    sent_under_r1.tick(31)
    assert _tree(sent_under_r1, shapes) == [(1, R1_TYPE), (1, T)]


def test_a_sent_child_relinked_to_an_unknown_q_becomes_a_top_row(sent_under_r1, shapes):
    """Q never started (no SubagentStart seen): its chain reaches nothing
    listed either."""
    sent_under_r1.link(C, by=Q)
    sent_under_r1.tick(31)
    assert _tree(sent_under_r1, shapes) == [(1, R1_TYPE), (1, T)]


def test_a_sent_child_relinked_stays_in_the_record(sent_under_r1):
    _q(sent_under_r1)
    sent_under_r1.link(C, by=Q)
    sent_under_r1.tick(31)
    assert C in _ids(sent_under_r1)


def test_a_sent_child_relinked_keeps_the_count(sent_under_r1):
    _q(sent_under_r1)
    sent_under_r1.link(C, by=Q)
    sent_under_r1.tick(31)
    assert h.header(sent_under_r1.last_block()) == "⏳ 2 agents working"


def test_a_sent_childs_late_child_follows_it_to_the_top(sent_under_r1, shapes):
    sent_under_r1.launch(G, G_TYPE, "look around", by=C)
    sent_under_r1.tick(31)
    _q(sent_under_r1)
    sent_under_r1.link(C, by=Q)
    sent_under_r1.tick(31)
    assert _tree(sent_under_r1, shapes) == [(1, R1_TYPE), (1, T), (2, G_TYPE)]


def test_a_sent_child_confirmed_by_the_same_parent_stays(sent_under_r1, shapes):
    sent_under_r1.link(C, by=R1)
    sent_under_r1.tick(31)
    assert _tree(sent_under_r1, shapes) == [(1, R1_TYPE), (2, T)]


def test_a_sent_child_relinked_to_a_listed_top_moves_under_it(rig, shapes):
    """Control: Q's chain DOES reach a listed agent (R2): C moves under R2."""
    _main(rig, R1, R1_TYPE, "run the pipeline")
    _main(rig, R2, R2_TYPE, "second runner")
    rig.launch(C, T, "helper work", by=R1)
    rig.answer()
    rig.link(C, by=R2)
    rig.tick(31)
    assert _tree(rig, shapes) == [(1, R1_TYPE), (1, R2_TYPE), (2, T)]


# ==== C joined the block late =====================================================================

@pytest.fixture
def late_under_r1(rig, shapes):
    _main(rig, R1, R1_TYPE, "run the pipeline")
    rig.answer()
    rig.launch(C, T, "helper work", by=R1)
    rig.tick(31)
    assert shapes.tree(rig.last_block()) == [(1, R1_TYPE), (2, T)]
    return rig


def test_a_late_child_relinked_to_an_unlisted_q_leaves_the_block(late_under_r1, shapes):
    _q(late_under_r1)
    late_under_r1.link(C, by=Q)
    late_under_r1.tick(31)
    assert _tree(late_under_r1, shapes) == [(1, R1_TYPE)]


def test_a_late_child_relinked_leaves_the_record(late_under_r1):
    _q(late_under_r1)
    late_under_r1.link(C, by=Q)
    late_under_r1.tick(31)
    assert C not in _ids(late_under_r1)


def test_a_late_child_relinked_leaves_the_count(late_under_r1):
    _q(late_under_r1)
    late_under_r1.link(C, by=Q)
    late_under_r1.tick(31)
    assert h.header(late_under_r1.last_block()) == "⏳ 1 agent working"


def test_a_late_child_relinked_to_an_unknown_q_leaves_the_block(late_under_r1, shapes):
    late_under_r1.link(C, by=Q)
    late_under_r1.tick(31)
    assert _tree(late_under_r1, shapes) == [(1, R1_TYPE)]


def test_a_late_child_confirmed_by_the_same_parent_stays(late_under_r1, shapes):
    late_under_r1.link(C, by=R1)
    late_under_r1.tick(31)
    assert _tree(late_under_r1, shapes) == [(1, R1_TYPE), (2, T)]


def test_a_late_child_confirmed_again_is_recorded_once(late_under_r1):
    late_under_r1.link(C, by=R1)
    late_under_r1.tick(31)
    assert _ids(late_under_r1).count(C) == 1


def test_a_late_child_relinked_to_a_listed_top_moves_under_it(rig, shapes):
    _main(rig, R1, R1_TYPE, "run the pipeline")
    _main(rig, R2, R2_TYPE, "second runner")
    rig.answer()
    rig.launch(C, T, "helper work", by=R1)
    rig.tick(31)
    rig.link(C, by=R2)
    rig.tick(31)
    assert _tree(rig, shapes) == [(1, R1_TYPE), (1, R2_TYPE), (2, T)]


# ---- a late row with late rows under it ----------------------------------------------------------

@pytest.fixture
def late_family(late_under_r1, shapes):
    """C (late, R1's) starts G; G starts D: all three joined late."""
    late_under_r1.launch(G, G_TYPE, "look around", by=C)
    late_under_r1.launch(D, D_TYPE, "review it", by=G)
    late_under_r1.tick(31)
    assert shapes.tree(late_under_r1.last_block()) == [
        (1, R1_TYPE), (2, T), (2, f"{T}{HINT}{G_TYPE}"), (2, f"{G_TYPE}{HINT}{D_TYPE}")]
    _q(late_under_r1)
    late_under_r1.link(C, by=Q)
    late_under_r1.tick(31)
    return late_under_r1


def test_a_relinked_late_row_takes_its_late_rows_out(late_family, shapes):
    assert _tree(late_family, shapes) == [(1, R1_TYPE)]


@pytest.mark.parametrize("agent", [C, G, D], ids=["c", "grandchild", "great-grandchild"])
def test_a_relinked_late_rows_ids_leave_the_record(late_family, agent):
    assert agent not in _ids(late_family)


def test_a_relinked_late_family_leaves_the_count(late_family):
    assert h.header(late_family.last_block()) == "⏳ 1 agent working"


def test_a_late_family_confirmed_by_the_same_parent_stays(late_under_r1, shapes):
    late_under_r1.launch(G, G_TYPE, "look around", by=C)
    late_under_r1.tick(31)
    late_under_r1.link(C, by=R1)
    late_under_r1.tick(31)
    assert _tree(late_under_r1, shapes) == [(1, R1_TYPE), (2, T), (2, f"{T}{HINT}{G_TYPE}")]


# ---- a sent row under a removed late row -----------------------------------------------------------

@pytest.fixture
def sent_under_late(rig, shapes):
    """G ran at send with no link (mixed launchers): a top row. Later C
    joins late under R1, and C's PostToolUse names G: G now sits under
    the late C."""
    _main(rig, R1, R1_TYPE, "run the pipeline")
    rig.call(G_TYPE, "main look")
    rig.call(G_TYPE, "helper look", by=R1)
    rig.start(G, G_TYPE)
    assert shapes.tree(rig.answer()) == [(1, R1_TYPE), (1, G_TYPE)]
    rig.launch(C, T, "helper work", by=R1)
    rig.link(G, by=C)
    rig.tick(31)
    assert shapes.tree(rig.last_block()) == [
        (1, R1_TYPE), (2, T), (2, f"{T}{HINT}{G_TYPE}")]
    return rig


def test_a_sent_row_under_a_removed_late_row_moves_to_the_top(sent_under_late, shapes):
    _q(sent_under_late)
    sent_under_late.link(C, by=Q)
    sent_under_late.tick(31)
    assert _tree(sent_under_late, shapes) == [(1, R1_TYPE), (1, G_TYPE)]


def test_a_sent_row_under_a_removed_late_row_stays_in_the_record(sent_under_late):
    _q(sent_under_late)
    sent_under_late.link(C, by=Q)
    sent_under_late.tick(31)
    assert G in _ids(sent_under_late)


def test_the_removed_late_row_leaves_the_record_but_the_sent_row_stays(sent_under_late):
    _q(sent_under_late)
    sent_under_late.link(C, by=Q)
    sent_under_late.tick(31)
    assert (C in _ids(sent_under_late), G in _ids(sent_under_late)) == (False, True)


def test_a_sent_row_two_late_rows_down_moves_to_the_top(rig, shapes):
    """G (sent) sits under D, which sits under C; both late. C's re-link
    removes C and D; G goes to the top."""
    _main(rig, R1, R1_TYPE, "run the pipeline")
    rig.call(G_TYPE, "main look")
    rig.call(G_TYPE, "helper look", by=R1)
    rig.start(G, G_TYPE)
    rig.answer()
    rig.launch(C, T, "helper work", by=R1)
    rig.launch(D, D_TYPE, "review it", by=C)
    rig.link(G, by=D)
    rig.tick(31)
    _q(rig)
    rig.link(C, by=Q)
    rig.tick(31)
    assert _tree(rig, shapes) == [(1, R1_TYPE), (1, G_TYPE)]


def test_a_sent_row_under_a_relinked_late_row_leaves_its_old_branch(rig, shapes):
    """G (sent) sits under the late D; D sits under the sent C, under R1.
    D's re-link to the unlisted Q removes D. G's chain now runs G, D, Q
    and reaches nothing listed, so G goes to the top (not under C, D's
    old parent)."""
    _main(rig, R1, R1_TYPE, "run the pipeline")
    rig.launch(C, T, "helper work", by=R1)
    rig.call(G_TYPE, "main look")
    rig.call(G_TYPE, "helper look", by=R1)
    rig.start(G, G_TYPE)
    rig.answer()
    rig.launch(D, D_TYPE, "review it", by=C)
    rig.link(G, by=D)
    rig.tick(31)
    _q(rig)
    rig.link(D, by=Q)
    rig.tick(31)
    assert _tree(rig, shapes) == [(1, R1_TYPE), (2, T), (1, G_TYPE)]


# ==== a PostToolUse with no earlier link ==============================================================

@pytest.fixture
def sent_unlinked(rig, shapes):
    """C started with no pending call (no link) and ran at send: a top row."""
    _main(rig, R1, R1_TYPE, "run the pipeline")
    rig.start(C, T)
    assert shapes.tree(rig.answer()) == [(1, R1_TYPE), (1, T)]
    return rig


def test_an_unlinked_sent_row_named_by_an_unlisted_q_stays_top(sent_unlinked, shapes):
    _q(sent_unlinked)
    sent_unlinked.link(C, by=Q)
    sent_unlinked.tick(31)
    assert _tree(sent_unlinked, shapes) == [(1, R1_TYPE), (1, T)]


def test_an_unlinked_sent_row_named_by_the_main_session_stays_top(sent_unlinked, shapes):
    sent_unlinked.link(C, by="")
    sent_unlinked.tick(31)
    assert _tree(sent_unlinked, shapes) == [(1, R1_TYPE), (1, T)]


def test_an_unlinked_sent_row_named_by_an_unlisted_q_stays_recorded(sent_unlinked):
    _q(sent_unlinked)
    sent_unlinked.link(C, by=Q)
    sent_unlinked.tick(31)
    assert C in _ids(sent_unlinked)


def test_an_unlinked_late_agent_named_by_an_unlisted_q_stays_out(rig, shapes):
    _main(rig, R1, R1_TYPE, "run the pipeline")
    rig.answer()
    rig.start(C, T)
    _q(rig)
    rig.link(C, by=Q)
    rig.tick(31)
    assert _tree(rig, shapes) == [(1, R1_TYPE)]


def test_an_unlinked_late_agent_named_by_an_unlisted_q_is_not_recorded(rig):
    _main(rig, R1, R1_TYPE, "run the pipeline")
    rig.answer()
    rig.start(C, T)
    _q(rig)
    rig.link(C, by=Q)
    rig.tick(31)
    assert C not in _ids(rig)
