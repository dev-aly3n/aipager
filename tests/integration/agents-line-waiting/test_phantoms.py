"""Phantom SubagentStop events (empty type, unknown id, 0.0 s) change
nothing at any step of the waiting sequence (design.md success criterion
5; CLAUDE.md: tolerated by design)."""

from __future__ import annotations

import copy

import pytest

from tests import agents_line_waiting_bb as h

P, C, P_TYPE, C_TYPE = h.P, h.C, h.P_TYPE, h.C_TYPE


def _snapshot(r):
    s = r.sess
    return (copy.deepcopy(s.bg_agents), sorted(s.active_subagents),
            list(s.tool_history), s.agent_is_waiting(P))


def _launch(r):
    r.agent_call(P, P_TYPE)


def _link(r):
    _launch(r)
    r.agent_call(C, C_TYPE, by=P)


def _paused(r):
    _link(r)
    r.stop(P, P_TYPE)


def _child_done(r):
    _paused(r)
    r.stop(C, C_TYPE)


def _resumed(r):
    _child_done(r)
    r.start(P, P_TYPE)


STEPS = {"launched": _launch, "linked": _link, "paused": _paused,
         "child-done": _child_done, "resumed": _resumed}


@pytest.mark.parametrize("step", list(STEPS))
def test_a_phantom_changes_no_table_and_no_row(rig, step):
    STEPS[step](rig)
    before = _snapshot(rig)
    rig.phantom(3)
    assert _snapshot(rig) == before


@pytest.mark.parametrize("step", ["paused", "child-done"])
def test_a_phantom_changes_no_block(rig, step):
    STEPS[step](rig)
    rig.answer()
    rig.tick(31)
    before = rig.last_block()
    rig.phantom(3)
    rig.tick(31)
    assert rig.last_block() == before


def test_a_phantom_with_the_parents_id_and_no_type_does_not_end_the_wait(rig):
    """Error guess: an empty-type stop that happens to carry P's id."""
    _paused(rig)
    rig.hook("SubagentStop", agent_id=P, agent_type="")
    assert rig.sess.agent_is_waiting(P) is True


def test_a_phantom_does_not_keep_a_finished_parent(rig):
    _child_done(rig)
    rig.start(P, P_TYPE)
    rig.phantom()
    rig.stop(P, P_TYPE)
    assert P not in rig.sess.bg_agents


def test_a_phantom_start_with_no_type_adds_nothing(rig):
    _paused(rig)
    before = _snapshot(rig)
    rig.hook("SubagentStart", agent_id="", agent_type="")
    assert _snapshot(rig) == before
