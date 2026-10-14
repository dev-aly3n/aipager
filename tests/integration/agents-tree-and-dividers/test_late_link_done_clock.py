"""A child whose link arrives after it stopped joins as done, its clock
ending at its stop, not at the link (entrypoints.md "Amendments
(orchestrator, after review-1)", "Late-linked done clock"; docs: the done
clock is how long that agent ran).

The virtual loop's clock is read around the start and the stop, so the
expected run time is measured, not assumed."""

from __future__ import annotations

import re

import pytest

R1 = "a505c081ca3926ed5"
C = "ac1d54c85d78f59ea"
R1_TYPE, T = "pipeline-runner", "ship-tester"
ROBOT = "\U0001f916"
RAN = 42.0
GAP = 120.0
DONE = re.compile(rf"- ✓ {ROBOT} \*\*{T}\*\* · done \((\d+):(\d\d)\)")


def _clock_seconds(block: str) -> int:
    rows = [x for x in block.split("\n") if f"**{T}**" in x]
    assert len(rows) == 1, block
    m = DONE.fullmatch(rows[0])
    assert m, rows[0]
    return int(m.group(1)) * 60 + int(m.group(2))


def _r1(rig):
    rig.call(R1_TYPE, "run the pipeline")
    rig.start(R1, R1_TYPE)
    rig.link(R1, by="")


def _ambiguous_start(rig):
    """The main session and R1 both call ship-tester: C starts unlinked."""
    rig.call(T, "main work")
    rig.call(T, "helper work", by=R1)
    rig.start(C, T)


def _completed():
    return {"status": "completed", "agentId": C,
            "content": [{"type": "text", "text": "done"}]}


def _run_then_link(rig, gap: float, t0: float | None = None) -> float:
    """C runs RAN s, stops, and R1's link comes *gap* s later; returns the
    measured run time (from *t0*, C's start, default now)."""
    t0 = rig.now() if t0 is None else t0
    rig.sleep(RAN)
    rig.stop(C, T)
    ran = rig.now() - t0
    if gap:
        rig.sleep(gap)
    rig.link(C, by=R1, response=_completed())
    rig.tick(31)
    return ran


@pytest.mark.parametrize("gap", [GAP, 0.0], ids=["two-minutes-later", "at-once"])
def test_a_late_linked_done_row_clocks_to_its_stop(rig, gap):
    _r1(rig)
    rig.answer()
    _ambiguous_start(rig)
    ran = _run_then_link(rig, gap)
    assert int(ran) <= _clock_seconds(rig.last_block()) <= int(ran) + 1


def test_a_late_linked_done_row_does_not_clock_to_the_link(rig):
    _r1(rig)
    rig.answer()
    _ambiguous_start(rig)
    _run_then_link(rig, GAP)
    assert _clock_seconds(rig.last_block()) < GAP


def test_a_sent_unlinked_row_linked_after_its_stop_clocks_to_its_stop(rig):
    """Boundary: C ran at send (a top row), stops, and its link comes two
    minutes later; it moves under R1 with its own run time."""
    _r1(rig)
    _ambiguous_start(rig)
    t0 = rig.now()
    rig.answer()
    ran = _run_then_link(rig, GAP, t0)
    rig.tick(31)
    assert int(ran) <= _clock_seconds(rig.last_block()) <= int(ran) + 1
