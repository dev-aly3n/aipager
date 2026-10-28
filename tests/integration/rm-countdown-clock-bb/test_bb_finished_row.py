"""The refused row keeps its cross on the finished card, whichever closer
recorded it and however the turn ends (spec requirement 4: "the busy
card's history row shows it refused (not ✅)"; design.md criteria 12 and
16), while the other rows of the same turn keep their tick (the control
that the finished card did not simply turn every row into a cross)."""

from __future__ import annotations

import pytest

from aipager import preferences
from tests.rm_clock_bb import REFUSE_AT, VERB_EN, catalog
from tests.two_prompts_rig import GROUP

AFTER = REFUSE_AT + 1.0
READ_PATH = "/home/alice/notes.txt"


def refused_by_monitor(cenv):
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(AFTER)


def refused_by_next_tool(cenv):
    cenv.rm()
    cenv.wall.t = AFTER
    cenv.hook("PreToolUse", tool_name="Read", tool_input={"file_path": READ_PATH},
              tool_use_id="toolu_read")


def a_read_that_ran(cenv):
    cenv.hook("PreToolUse", tool_name="Read", tool_input={"file_path": READ_PATH},
              tool_use_id="toolu_read")
    cenv.hook("PostToolUse", tool_name="Read", tool_input={"file_path": READ_PATH},
              tool_use_id="toolu_read", tool_response={"type": "text"})


def finished(cenv, event):
    cenv.hook(event)
    return cenv.rig.frames[-1] if cenv.rig.frames else ""


def lines_with(text, needle):
    return [line for line in text.splitlines() if needle in line]


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
@pytest.mark.parametrize("close", [refused_by_monitor, refused_by_next_tool],
                         ids=["monitor", "next-tool"])
def test_refused_row_is_not_a_tick_on_the_finished_card(cenv, event, close):
    close(cenv)
    rows = lines_with(finished(cenv, event), VERB_EN)
    assert rows and not any("✅" in r for r in rows)


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_a_tool_that_ran_keeps_its_tick_beside_the_refused_row(cenv, event):
    refused_by_monitor(cenv)
    a_read_that_ran(cenv)
    rows = lines_with(finished(cenv, event), "notes.txt")
    assert rows and all("✅" in r for r in rows)


def test_refused_row_stays_failed_in_the_history_after_stop(cenv):
    refused_by_monitor(cenv)
    cenv.hook("Stop")
    assert [done for _row, done in cenv.refused_rows()] == ["failed"]


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_fa_refused_row_by_the_stop_closer_is_not_a_tick(cenv, event):
    preferences.set_ui_language(GROUP, "fa")
    cenv.rm()
    cenv.wall.t = AFTER
    rows = lines_with(finished(cenv, event), catalog("fa")["ans.verb.refused_by_claude"])
    assert rows and not any("✅" in r for r in rows)
