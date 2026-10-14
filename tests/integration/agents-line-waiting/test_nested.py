"""Two levels of nesting: P launches C, C launches G (design.md success
criterion 4: "Two-level nesting is counted right at every step").

Each parent pauses while its own agent runs; G stops; C resumes and
finishes; P resumes and finishes. The answer's block and the running set
are read at every step.
"""

from __future__ import annotations

import pytest

from tests import agents_line_waiting_bb as h

P, C, G = h.P, h.C, h.G
P_TYPE, C_TYPE, G_TYPE = h.P_TYPE, h.C_TYPE, h.G_TYPE


@pytest.fixture
def tree(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.agent_call(G, G_TYPE, by=C)
    rig.stop(C, C_TYPE)                 # C waits for G
    rig.stop(P, P_TYPE)                 # P waits for C (itself waiting)
    rig.phantom()
    return rig


# ---- both parents paused, G working -----------------------------------------------

def test_the_middle_agent_waits(tree):
    assert tree.sess.agent_is_waiting(C) is True


def test_the_top_agent_waits_on_a_waiting_child(tree):
    assert tree.sess.agent_is_waiting(P) is True


def test_all_three_are_running(tree):
    assert sorted(tree.sess.bg_agents) == sorted([P, C, G])


def test_all_three_are_in_the_job(tree):
    assert sorted(tree.sess.active_subagents) == sorted([P, C, G])


def test_each_parent_lists_only_its_own_child(tree):
    assert (tree.sess.agent_children_running(P),
            tree.sess.agent_children_running(C)) == ([C], [G])


def test_the_answer_counts_three(tree):
    assert h.header(tree.answer()) == "⏳ 3 agents working"


@pytest.mark.parametrize("agent_type", [P_TYPE, C_TYPE])
def test_the_answer_shows_both_parents_waiting(tree, agent_type):
    """Each waits for its own agent (a waiting agent's row since the
    agents tree, 2026-10-14)."""
    assert h.is_waiting_row(h.row_of(tree.answer(), agent_type))


def test_the_answer_shows_the_grandchild_working(tree):
    assert h.is_working_row(h.row_of(tree.answer(), G_TYPE))


# ---- G stops ------------------------------------------------------------------------

@pytest.fixture
def g_done(tree):
    tree.answer()
    tree.sleep(10)
    tree.stop(G, G_TYPE)
    return tree


def test_g_leaves(g_done):
    assert G not in g_done.sess.bg_agents


def test_c_stays_after_its_child_ends(g_done):
    assert C in g_done.sess.bg_agents


def test_p_still_waits_while_c_is_on_the_line(g_done):
    assert g_done.sess.agent_is_waiting(P) is True


def test_the_block_shows_g_done_and_two_working(g_done):
    g_done.tick(31)
    block = g_done.last_block()
    assert [h.is_done_row(h.row_of(block, t)) for t in (P_TYPE, C_TYPE, G_TYPE)] == \
        [False, False, True]


# ---- C resumes and finishes ----------------------------------------------------------

@pytest.fixture
def c_done(g_done):
    g_done.start(C, C_TYPE)
    g_done.tool(C)
    g_done.stop(C, C_TYPE)
    return g_done


def test_c_resumed_without_a_second_row(g_done):
    g_done.start(C, C_TYPE)
    assert len(g_done.robot_rows(C_TYPE)) == 1


def test_c_finishes_with_no_child_running(c_done):
    assert C not in c_done.sess.bg_agents


def test_p_still_on_the_line_after_c_finished(c_done):
    assert P in c_done.sess.bg_agents


def test_one_agent_still_counts_in_the_job(c_done):
    assert list(c_done.sess.active_subagents) == [P]


def test_the_job_is_still_open(c_done):
    c_done.sess.status = h.Status.IDLE
    assert c_done.sess.job_background_open() is True


# ---- P resumes and finishes ------------------------------------------------------------

@pytest.fixture
def all_done(c_done):
    c_done.start(P, P_TYPE)
    c_done.tool(P)
    c_done.stop(P, P_TYPE)
    return c_done


def test_nothing_runs_at_the_end(all_done):
    assert all_done.sess.bg_agents == {}


def test_the_block_settles_to_three_done(all_done):
    all_done.tick(6)
    all_done.tick(31)
    assert all_done.last_block().startswith("✅ 3 agents done (")


def test_each_card_row_appears_once(all_done):
    assert [len(all_done.robot_rows(t)) for t in (P_TYPE, C_TYPE, G_TYPE)] == [1, 1, 1]


# ---- the out-of-order variant: P pauses before C does ---------------------------------

def test_p_pausing_first_still_waits(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)                 # P pauses while C still works
    rig.agent_call(G, G_TYPE, by=C)
    rig.stop(C, C_TYPE)                 # then C pauses for G
    rig.stop(G, G_TYPE)
    assert (rig.sess.agent_is_waiting(P), rig.sess.agent_is_waiting(C)) == (True, True)
