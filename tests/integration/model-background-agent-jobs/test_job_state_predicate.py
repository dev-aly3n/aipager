"""design.md: ``TrackedSession.job_background_open() -> bool`` — the one
predicate every job-aware site consults — and
``SessionRegistry.transition(..., preserve_job_state=...)``'s guarded
``busy_started_wall`` stamp, the mechanism the anchoring success
criterion depends on.

Equivalence partitioning over the (status, active_subagents) grid for
the predicate, and over the (prior_status, preserve_job_state) grid for
the stamp-skip.
"""

from __future__ import annotations

import time

import pytest

from aipager.state import SessionRegistry, Status, TrackedSession


def _sess(status, agents):
    s = TrackedSession(name="claude-hiva", label="hiva", status=status)
    s.active_subagents = agents
    return s


# ---- job_background_open(): four status/agents quadrants -----------------

@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY])
def test_open_true_when_status_idle_or_busy_and_agents_present(status):
    sess = _sess(status, {"a1": {"type": "Explore"}})
    assert sess.job_background_open() is True


@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY])
def test_open_false_when_status_idle_or_busy_but_no_agents(status):
    sess = _sess(status, {})
    assert sess.job_background_open() is False


@pytest.mark.parametrize("status", [Status.INTERACTIVE, Status.GONE, Status.UNKNOWN])
def test_open_false_for_every_other_status_even_with_agents_present(status):
    """The predicate is `status in (IDLE, BUSY) and bool(active_subagents)`
    per design.md — INTERACTIVE/GONE/UNKNOWN must never read as an open
    job no matter what active_subagents holds (e.g. a session that went
    GONE mid-job must not keep reporting an open job)."""
    sess = _sess(status, {"a1": {"type": "Explore"}})
    assert sess.job_background_open() is False


def test_open_becomes_false_the_instant_the_dict_empties():
    """Boundary: the predicate is live, not cached — popping the last
    agent flips it within the same status."""
    sess = _sess(Status.IDLE, {"a1": {"type": "Explore"}})
    assert sess.job_background_open() is True
    sess.active_subagents.pop("a1")
    assert sess.job_background_open() is False


# ---- transition(..., preserve_job_state=...): busy_started_wall stamp ----

def _seeded_registry(name, status, wall):
    registry = SessionRegistry()
    sess = TrackedSession(name=name, label="hiva", status=status)
    sess.busy_started_wall = wall
    registry._sessions[name] = sess
    return registry, sess


SENTINEL_WALL = 555_000.0


@pytest.mark.parametrize("preserve", [True, False])
def test_from_interactive_the_wall_stamp_is_always_skipped(preserve):
    """entrypoints.md: 'Assert busy_started_wall is NOT updated when
    preserve_job_state=True and the prior status was not INTERACTIVE' —
    the converse this pins is that FROM Interactive, the pre-existing
    permission-answer skip already applies regardless of the new flag's
    value, i.e. the new parameter must not double-apply or override the
    existing skip in either direction."""
    registry, sess = _seeded_registry("claude-hiva", Status.INTERACTIVE, SENTINEL_WALL)
    out = registry.transition("claude-hiva", Status.BUSY,
                              preserve_job_state=preserve)
    assert out is not None  # a real transition happened (status did change)
    assert sess.busy_started_wall == SENTINEL_WALL


def test_from_idle_preserve_true_skips_the_stamp():
    registry, sess = _seeded_registry("claude-hiva", Status.IDLE, SENTINEL_WALL)
    registry.transition("claude-hiva", Status.BUSY, preserve_job_state=True)
    assert sess.busy_started_wall == SENTINEL_WALL


def test_from_idle_preserve_false_or_omitted_restamps():
    """The genuine-new-turn case: a real prompt re-entering BUSY from IDLE
    (no preserve_job_state) MUST move the anchor — this is the control
    proving the skip above is actually gated by the flag/status, not a
    permanent no-op."""
    registry, sess = _seeded_registry("claude-hiva", Status.IDLE, SENTINEL_WALL)
    before = time.time()
    registry.transition("claude-hiva", Status.BUSY)  # preserve_job_state omitted
    assert sess.busy_started_wall != SENTINEL_WALL
    assert sess.busy_started_wall >= before


def test_from_busy_same_state_is_a_pure_noop_regardless_of_preserve():
    """Boundary: BUSY->BUSY can never reach the stamp branch at all (the
    same-state early return fires first) — pin this so a future change
    can't accidentally make preserve_job_state reach through a same-state
    call."""
    registry, sess = _seeded_registry("claude-hiva", Status.BUSY, SENTINEL_WALL)
    out = registry.transition("claude-hiva", Status.BUSY, preserve_job_state=True)
    assert out is None
    assert sess.busy_started_wall == SENTINEL_WALL
