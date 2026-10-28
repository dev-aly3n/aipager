"""The turn's end keeps a "Refused by Claude Code" row ❌ (roadmap 8.128,
spec requirement 4: the row shows it refused, never ✅). Every other row
is marked done there as before, an ordinary failed tool row included:
only the refused row is kept."""

from __future__ import annotations

import pytest

from aipager import rm_countdown
from aipager.bot import card_rows
from tests.rm_clock_rig import RM, rm_prompt

REFUSED_ROW = f"🔑 Bash: {RM} → Refused by Claude Code"
IN_WINDOW = 120.0 - rm_countdown.WINDOW_LEAD_SECONDS + 1.0


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_the_refused_row_stays_failed_at_the_turns_end(rig, tx, clock, event):
    rm_prompt(rig, tx)
    rig.sess.record_tool("Bash: false", "failed")
    clock.at(IN_WINDOW)
    rig.hook(event)
    marks = dict(rig.sess.tool_history)
    assert marks[REFUSED_ROW] == "failed"
    # Today's turn end, unchanged: an ordinary failed row is marked done.
    assert marks["Bash: false"] is True


def test_the_finished_card_draws_the_refused_row_with_a_cross(rig, tx, clock):
    rm_prompt(rig, tx)
    clock.at(IN_WINDOW)
    rig.frames.clear()
    rig.card_edits.clear()
    rig.hook("Stop")
    texts = rig.frames + [t for _m, t, _k in rig.card_edits]
    rows = [line for t in texts for line in t.splitlines()
            if "Refused by Claude Code" in line]
    assert rows and all("✅" not in r for r in rows)


@pytest.mark.parametrize("summary, want", [
    (REFUSED_ROW, True),
    ("❓ Which database? → Refused by Claude Code", True),
    (f"🔑 Bash: {RM} → Denied", False),
    (f"🔑 Bash: {RM} → Allowed", False),
    ("Bash: echo Refused by Claude Code", False),
    ("shell: make - failed (exit 2)", False),
    ("", False),
    (None, False),
    (3, False),
], ids=["refused", "question", "denied", "allowed", "text-only", "shell", "empty",
        "none", "int"])
def test_which_rows_are_refused_by_claude(summary, want):
    assert card_rows.refused_by_claude(summary) is want
