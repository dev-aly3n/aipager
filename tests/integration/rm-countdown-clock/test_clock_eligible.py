"""Which PermissionRequest carries the risky-removal clock (roadmap 8.128;
design.md criteria 9 and 10): Claude's own (no agent), Bash, in Full
access or Auto, with no permission suggestions, for a command that runs a
removal. Each condition flipped alone gives no clock, and every prompt
with a clock is a countdown candidate for the order guard."""

from __future__ import annotations

import pytest

from aipager import prompt_stack, rm_countdown
from tests.two_prompts_rig import Rig

ELIGIBLE = {"tool_name": "Bash", "mode": "bypassPermissions", "suggestions": [],
            "agent_id": None, "tool_input": {"command": "rm -rf /home/alice/app"}}


def eligible(**changes):
    return rm_countdown.clock_eligible(**{**ELIGIBLE, **changes})


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_the_eligible_prompt_has_a_clock(mode):
    assert eligible(mode=mode) is True


@pytest.mark.parametrize("agent_id", ["a1b2c3d4e5f60718a", 1, True],
                         ids=["agent", "int", "true"])
def test_an_agents_prompt_has_no_clock(agent_id):
    assert eligible(agent_id=agent_id) is False


@pytest.mark.parametrize("agent_id", [None, ""], ids=["none", "empty"])
def test_no_agent_id_is_claudes_own(agent_id):
    assert eligible(agent_id=agent_id) is True


@pytest.mark.parametrize("tool_name", ["Write", "Edit", "bash", "", None],
                         ids=["write", "edit", "lower", "empty", "none"])
def test_another_tool_has_no_clock(tool_name):
    assert eligible(tool_name=tool_name) is False


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", "dontAsk", "", None,
                                  "?", "BypassPermissions", ["auto"]],
                         ids=["default", "accept-edits", "plan", "dont-ask", "empty",
                              "none", "unknown", "case", "list"])
def test_another_mode_has_no_clock(mode):
    assert eligible(mode=mode) is False


@pytest.mark.parametrize("suggestions", [None, [{"type": "addRules"}], {}, "", 0],
                         ids=["missing", "a-rule", "dict", "str", "zero"])
def test_suggestions_not_an_empty_list_give_no_clock(suggestions):
    assert eligible(suggestions=suggestions) is False


@pytest.mark.parametrize("tool_input", [{"command": "ls"}, {"command": None}, {}, None,
                                        "rm -rf x", {"command": ["rm", "x"]}],
                         ids=["ls", "none-command", "empty", "none", "str", "list"])
def test_no_removal_command_gives_no_clock(tool_input):
    assert eligible(tool_input=tool_input) is False


class _Raising(dict):
    def get(self, *a, **k):  # noqa: ARG002 - shape of dict.get
        raise RuntimeError("boom")


def test_an_input_that_raises_gives_no_clock():
    assert eligible(tool_input=_Raising(command="rm x")) is False


# -- a clock-carrying prompt is a countdown candidate (criterion 10) -----------------

@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
@pytest.mark.parametrize("command", ["rm -rf build/", "sudo rmdir /srv/x",
                                     "find . -exec rm {} +", "sh -c 'rm -rf \"$D\"'"],
                         ids=["rm", "sudo-rmdir", "find-exec", "sh-c"])
def test_every_clock_prompt_is_a_candidate(rig, mode, command):
    rig.claude_bash(command, mode=mode, suggestions=[])
    perm = rig.sess.open_prompts()[0]
    assert rm_countdown.clock_of(perm) is not None
    assert prompt_stack.countdown_candidate(perm["tool_info"]["order"]) is True
