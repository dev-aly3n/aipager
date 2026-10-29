"""design-6 success criterion: a kill pressed at the terminal (no aipager
Stop) ends the job as Finished and leaves no "waiting" state.

Claude Code fires no SubagentStop for a killed agent (probe G1). Two
signals clear the agents, each on its own:
- the transcript's ``system`` line ``subtype: "agents_killed"`` written
  after the Stop that left the agents running (S2);
- a Stop whose ``background_tasks`` no longer lists agents it listed
  before (S3), with the transcript line not flushed.

Boundaries and error guessing: an ``agents_killed`` line older than the
last Stop is not this kill; a Stop that still lists one of two agents
leaves that one; an agent Claude Code never listed is left to its own
SubagentStop; a late SubagentStop of a killed agent changes nothing.
"""

from __future__ import annotations

import importlib
import time

import pytest

kit = importlib.import_module("tests.integration.stop-kill-agents-blackbox.conftest")
ANSWER = "Noted, both helpers are stopped."


def _scenario(h, *, line: str | None, listed_at_end=(), late_subagent_stop=False,
              list_first=True):
    """*line*: "fresh" (after the Stop that left the agents running),
    "old" (before the session's previous job finished) or None."""
    seen = {}

    async def go():
        # An earlier job of the session, finished: its end is the bound an
        # ``agents_killed`` line must not predate.
        await h.prompt("tidy the readme")
        await h.turn_stop("Tidied.", agents=())
        t0 = time.time()
        await h.prompt()
        await h.launch(kit.A1)
        await h.launch(kit.A2)
        await h.turn_stop(agents=(kit.A1, kit.A2), tasks=list_first)
        seen["before"] = h.sess.shown_state()
        if line == "fresh":
            now = time.time() + 1.0
            h.append(*kit.queue_lines(now, kit.WAKE_TWO),
                     kit.agents_killed_line(now),
                     kit.wake_user_line(now, kit.WAKE_TWO))
        elif line == "old":
            h.append(kit.agents_killed_line(t0 - 60.0))
        await h.hook("UserPromptSubmit", prompt=kit.WAKE_TWO)
        seen["at_wake"] = {
            "agents": sorted(h.sess.active_subagents),
            "continuation": h.sess.job_continuation_active,
            "origin": h.sess.last_prompt_origin,
        }
        await h.turn_stop(ANSWER, agents=listed_at_end)
        seen["after"] = (h.sess.shown_state(), h.sess.job_background_open())
        seen["edits"] = len(h.edits)
        seen["sent"] = len(h.sent)
        if late_subagent_stop:
            await h.hook("SubagentStop", agent_id=kit.A1,
                         agent_type="general-purpose")
            seen["late"] = (h.sess.shown_state(), len(h.edits), len(h.sent))
    h.run(go())
    return seen


SIGNALS = pytest.mark.parametrize("line", ["fresh", None],
                                  ids=["agents_killed-line", "stop-tasks-only"])


def test_precondition_the_session_waits(dm):
    assert _scenario(dm, line="fresh")["before"] == ("tasks", 2)


# ---- S2: the transcript line ---------------------------------------------------------

def test_the_agents_killed_line_clears_the_agents_at_the_wake(dm):
    assert _scenario(dm, line="fresh")["at_wake"]["agents"] == []


def test_the_agents_killed_line_makes_the_wake_the_jobs_continuation(dm):
    assert _scenario(dm, line="fresh")["at_wake"]["continuation"] is True


def test_the_wake_after_a_terminal_kill_is_not_a_terminal_typed_turn(dm):
    assert _scenario(dm, line="fresh")["at_wake"]["origin"] != "terminal"


def test_an_agents_killed_line_from_before_the_previous_job_ended_clears_nothing(dm):
    assert _scenario(dm, line="old")["at_wake"]["agents"] == sorted([kit.A1, kit.A2])


# ---- both signals: nothing stays waiting, the card ends Finished -----------------------

@SIGNALS
def test_nothing_stays_waiting(dm, line):
    assert _scenario(dm, line=line)["after"] == (("idle", 0), False)


@SIGNALS
def test_no_agent_left_in_either_table(dm, line):
    _scenario(dm, line=line)
    assert (dm.sess.active_subagents, dm.sess.bg_agents) == ({}, {})


@SIGNALS
def test_the_card_ends_finished(dm, line):
    """The job's one true finish: the card's last frame is its final one
    (no Stop button), and not a working or waiting frame."""
    _scenario(dm, line=line)
    last_text, last_markup = [(t, m) for i, t, m in dm.edits if i == kit.CARD][-1]
    assert (last_markup, "Working" in last_text, "waiting for" in last_text) == (
        None, False, False)


@SIGNALS
def test_the_card_does_not_say_an_agent_was_lost(dm, line):
    _scenario(dm, line=line)
    assert not any("lost" in t for t in dm.card_texts())


@SIGNALS
def test_claudes_reply_is_delivered_once(dm, line):
    _scenario(dm, line=line)
    assert sum(ANSWER in m["text"] for m in dm.sent) == 1


@SIGNALS
def test_the_bar_shows_no_agents(dm, line):
    import re
    _scenario(dm, line=line)
    bar = re.sub(r"</?b>", "", dm.bot._render_pinned(kit.DM, [dm.sess])[0])
    assert bar.split("\n")[0] == "💤 x1 (idle)"


@SIGNALS
def test_a_terminal_kill_writes_nothing(dm, line):
    _scenario(dm, line=line)
    assert dm.term.writes() == []


# ---- S3 boundaries --------------------------------------------------------------------

def test_a_stop_still_listing_one_agent_keeps_that_one(dm):
    seen = _scenario(dm, line=None, listed_at_end=(kit.A2,))
    assert seen["after"][0] == ("tasks", 1)


def test_an_agent_never_listed_is_kept(dm):
    """No earlier Stop listed the agents (no ``background_tasks`` at all):
    a Stop with ``[]`` does not end them."""
    seen = _scenario(dm, line=None, list_first=False)
    assert seen["after"][0] == ("tasks", 2)


# ---- a late SubagentStop of a killed agent -------------------------------------------

@SIGNALS
def test_a_late_subagent_stop_keeps_the_session_idle(dm, line):
    seen = _scenario(dm, line=line, late_subagent_stop=True)
    assert seen["late"][0] == ("idle", 0)


@SIGNALS
def test_a_late_subagent_stop_adds_no_card_row_or_message(dm, line):
    seen = _scenario(dm, line=line, late_subagent_stop=True)
    assert seen["late"][1:] == (seen["edits"], seen["sent"])


def test_a_late_subagent_stop_after_aipagers_stop_adds_nothing(dm):
    seen = {}

    async def go():
        await dm.waiting()
        await dm.stop("card")
        seen["n"] = (len(dm.edits), len(dm.sent))
        await dm.hook("SubagentStop", agent_id=kit.A1, agent_type="general-purpose")
        seen["late"] = (len(dm.edits), len(dm.sent), dm.sess.shown_state())
    dm.run(go())
    assert seen["late"] == (*seen["n"], ("idle", 0))
