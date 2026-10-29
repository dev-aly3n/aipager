"""The card row of a Skill or Agent call names what ran.

Claude Code 2.1.311's Skill tool input is ``{"skill": <name>, "args":
<optional text>}`` and its Agent tool (legacy name ``Task``) takes
``description``, ``prompt`` and ``subagent_type``. The row shows the skill's
name and the agent's short description and type, never ``args`` or
``prompt`` (they can hold the person's own text), with any direction
control shown as a visible marker like every other summary.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.bot.animation import _find_tool_row
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, TrackedSession

RLO = "\u202e"
RLO_SHOWN = "⟨U+202E⟩"


# ---- Skill ----------------------------------------------------------------

@pytest.mark.parametrize("skill", ["design", "plugin:skill", "my-skill_2"])
def test_skill_row_names_the_skill(skill):
    assert hr._summarize_tool("Skill", {"skill": skill}) == f"Skill: {skill}"


def test_skill_row_never_shows_args():
    out = hr._summarize_tool("Skill", {"skill": "design",
                                       "args": "my secret pricing notes"})
    assert out == "Skill: design"
    assert "secret" not in out


@pytest.mark.parametrize("inp", [
    {}, {"skill": ""}, {"skill": "   "}, {"skill": None}, {"skill": 3},
    {"skill": ["design"]}, {"args": "only args"},
], ids=["missing", "empty", "blank", "none", "int", "list", "args-only"])
def test_skill_row_without_a_name_is_bare(inp):
    assert hr._summarize_tool("Skill", inp) == "Skill"


def test_skill_row_cuts_a_long_name_to_80():
    out = hr._summarize_tool("Skill", {"skill": "s" * 300})
    assert out == "Skill: " + "s" * 80


def test_skill_row_is_one_line():
    out = hr._summarize_tool("Skill", {"skill": "two\nlines\t here"})
    assert out == "Skill: two lines here"


def test_skill_row_reveals_a_direction_control():
    out = hr._summarize_tool("Skill", {"skill": f"de{RLO}sign"})
    assert out == f"Skill: de{RLO_SHOWN}sign"
    assert RLO not in out


# ---- Agent / Task ---------------------------------------------------------

@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_agent_row_shows_description_and_type(tool):
    out = hr._summarize_tool(tool, {"description": "Review the diff",
                                    "subagent_type": "ship-reviewer",
                                    "prompt": "private words"})
    assert out == f"{tool}: Review the diff (ship-reviewer)"


@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_agent_row_description_only(tool):
    assert hr._summarize_tool(tool, {"description": "Find callers"}) \
        == f"{tool}: Find callers"


@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_agent_row_type_only(tool):
    assert hr._summarize_tool(tool, {"subagent_type": "Explore"}) \
        == f"{tool}: Explore"


@pytest.mark.parametrize("tool", ["Agent", "Task"])
@pytest.mark.parametrize("inp", [
    {}, {"description": "", "subagent_type": "  "},
    {"description": None, "subagent_type": 5},
], ids=["missing", "blank", "not-strings"])
def test_agent_row_without_either_is_bare(tool, inp):
    assert hr._summarize_tool(tool, inp) == tool


@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_agent_row_never_shows_the_prompt(tool):
    out = hr._summarize_tool(tool, {"prompt": "the person's private text"})
    assert out == tool
    out = hr._summarize_tool(tool, {"prompt": "private", "description": "x"})
    assert "private" not in out


def test_agent_row_cuts_long_parts():
    out = hr._summarize_tool("Agent", {"description": "d" * 300,
                                       "subagent_type": "t" * 300})
    assert out == "Agent: " + "d" * 80 + " (" + "t" * 40 + ")"


def test_agent_row_is_one_line():
    out = hr._summarize_tool("Agent", {"description": "a\nb",
                                       "subagent_type": "x\ty"})
    assert out == "Agent: a b (x y)"


def test_agent_row_reveals_direction_controls():
    out = hr._summarize_tool("Agent", {"description": f"a{RLO}b",
                                       "subagent_type": f"c{RLO}d"})
    assert out == f"Agent: a{RLO_SHOWN}b (c{RLO_SHOWN}d)"


@pytest.mark.parametrize("tool", ["Skill", "Agent", "Task"])
@pytest.mark.parametrize("inp", ["not a dict", None, ["skill"], 7],
                         ids=["str", "none", "list", "int"])
def test_a_malformed_input_gives_the_bare_name(tool, inp):
    assert hr._summarize_tool(tool, inp) == tool


# ---- fall-through ---------------------------------------------------------

@pytest.mark.parametrize("tool", [
    "UnknownTool", "ExitPlanMode", "mcp__server__do_thing",
])
def test_other_tools_keep_the_bare_name(tool):
    assert hr._summarize_tool(tool, {"skill": "design", "description": "x",
                                     "subagent_type": "y"}) == tool


# ---- through the receiver and onto the card --------------------------------

def _pre_tool_use(tool_name, tool_input):
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    return recv, notify_fn, json.dumps({
        "hook_event_name": "PreToolUse", "session": "claude-alice",
        "tool_name": tool_name, "tool_input": tool_input}).encode()


def test_pre_tool_use_skill_sends_the_named_row(run_async):
    recv, notify_fn, payload = _pre_tool_use(
        "Skill", {"skill": "design", "args": "pricing for alice"})
    run_async(recv._on_datagram(payload))
    _, event, ctx = notify_fn.await_args.args
    assert event == "tool_use"
    assert ctx["tool_summary"] == "Skill: design"


def test_named_rows_still_match_their_transcript_block():
    """The transcript anchor finds a row by "<tool>" or "<tool>:", so the
    new summary shapes keep matching their tool_use blocks."""
    sess = TrackedSession(name="claude-alice", label="alice")
    skill = hr._summarize_tool("Skill", {"skill": "design"})
    agent = hr._summarize_tool("Agent", {"description": "Review",
                                         "subagent_type": "ship-reviewer"})
    sess.tool_history = [("Bash: ls", True), (skill, True), (agent, False)]
    assert _find_tool_row(sess, "Skill", 0) == 1
    assert _find_tool_row(sess, "Agent", 0) == 2
