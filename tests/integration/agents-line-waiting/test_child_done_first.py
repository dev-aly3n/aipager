"""A parent whose own agents all finished before it stops is done at once
(design.md success criterion 3), and today's settle is unchanged."""

from __future__ import annotations

import pytest

from tests import agents_line_waiting_bb as h

P, C, P_TYPE, C_TYPE = h.P, h.C, h.P_TYPE, h.C_TYPE


@pytest.fixture
def both_running(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.answer()
    rig.sleep(15)
    return rig


@pytest.fixture
def done_in_order(both_running):
    both_running.stop(C, C_TYPE)
    both_running.sleep(5)
    both_running.stop(P, P_TYPE)
    return both_running


def test_the_parent_leaves_the_running_set_at_once(done_in_order):
    assert P not in done_in_order.sess.bg_agents


def test_the_parent_is_not_waiting(done_in_order):
    assert done_in_order.sess.agent_is_waiting(P) is False


def test_the_parent_leaves_the_job_at_once(done_in_order):
    assert done_in_order.sess.active_subagents == {}


def test_the_parents_card_row_is_settled(done_in_order):
    assert [d for _s, d in done_in_order.robot_rows(P_TYPE)] == [True]


def test_the_block_settles_past_five_seconds(done_in_order):
    done_in_order.tick(5.5)
    done_in_order.tick(31)
    assert done_in_order.last_block().startswith("✅ 2 agents done (")


def test_the_pin_count_drops_to_zero(done_in_order):
    assert len(done_in_order.sess.bg_agents) == 0


def test_with_no_child_at_all_the_stop_finishes(rig):
    rig.agent_call(P, P_TYPE)
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents


def test_a_parent_with_one_child_done_and_one_running_waits(rig):
    """Boundary: one of two children still running is enough to wait."""
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.agent_call(h.G, h.G_TYPE, by=P)
    rig.stop(C, C_TYPE)
    rig.stop(P, P_TYPE)
    assert rig.sess.agent_children_running(P) == [h.G]


def test_the_last_of_two_children_ending_first_makes_the_stop_a_finish(rig):
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.agent_call(h.G, h.G_TYPE, by=P)
    rig.stop(C, C_TYPE)
    rig.stop(h.G, h.G_TYPE)
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents
