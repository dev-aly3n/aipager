"""A daemon restart with open prompts (design.md success criterion 8,
entrypoints.md "State file"): with exactly one open, ``open_prompt`` is
saved as in 8.102; with two or more, none is, every one of their message
ids is in ``closed_prompt_msgs``, and after the restart all their buttons
answer "already answered" and type nothing."""

from __future__ import annotations

import json

import pytest

from aipager import state
from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.state import SessionRegistry
from tests.two_prompts_rig import BG, CARD, FG, S, Rig, answers


def extra_ids(rig):
    return [m["msg_id"] for m in rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


def saved(rig) -> dict:
    rig.bot.registry.save()
    return json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][S]


def restarted(rig, mk_bot, monkeypatch, run_async) -> Rig:
    rig.bot.registry.save()
    again = SessionRegistry()
    again.load()
    return Rig(mk_bot, monkeypatch, run_async, registry=again)


def agent_and_question(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.question()
    return CARD, extra_ids(rig)[0]


def two_claude(rig):
    rig.claude_bash("rm -rf build/", tool_use_id="toolu_p1", hook=False)
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", hook=False)
    return CARD, extra_ids(rig)[0]


def claude_question_and_permission(rig):
    rig.question()
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2", hook=False)
    return CARD, extra_ids(rig)[0]


SETUPS = {"agent+question": agent_and_question, "two-claude": two_claude,
          "question+claude-permission": claude_question_and_permission}


# -- two or more open: nothing saved -------------------------------------------------------

@pytest.mark.parametrize("setup", SETUPS)
def test_two_open_no_open_prompt_saved(rig, setup):
    SETUPS[setup](rig)
    assert "open_prompt" not in saved(rig)


@pytest.mark.parametrize("setup", SETUPS)
def test_two_open_card_in_closed_prompt_msgs(rig, setup):
    SETUPS[setup](rig)
    assert CARD in json.loads(json.dumps(saved(rig).get("closed_prompt_msgs", [])))


@pytest.mark.parametrize("setup", SETUPS)
def test_two_open_extra_in_closed_prompt_msgs(rig, setup):
    _card, extra = SETUPS[setup](rig)
    assert extra in json.loads(json.dumps(saved(rig).get("closed_prompt_msgs", [])))


def test_three_open_no_open_prompt_saved(rig):
    rig.launch(BG)
    rig.launch(FG, background=False, agent_type="ship-tester")
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(FG, "pytest", request_id="req-fg")
    rig.question()
    assert "open_prompt" not in saved(rig)


@pytest.mark.parametrize("setup", SETUPS)
def test_after_restart_card_button_already_answered(rig, mk_bot, monkeypatch, run_async, setup):
    card, _extra = SETUPS[setup](rig)
    after = restarted(rig, mk_bot, monkeypatch, run_async)
    assert answers(after.tap(card, "allow")) == [str(ALREADY_ANSWERED)]


@pytest.mark.parametrize("setup", SETUPS)
def test_after_restart_extra_button_already_answered(rig, mk_bot, monkeypatch, run_async, setup):
    _card, extra = SETUPS[setup](rig)
    after = restarted(rig, mk_bot, monkeypatch, run_async)
    verb = "allow" if setup == "two-claude" or setup.endswith("permission") else "opt0"
    assert answers(after.tap(extra, verb)) == [str(ALREADY_ANSWERED)]


@pytest.mark.parametrize("setup", SETUPS)
def test_after_restart_taps_type_nothing(rig, mk_bot, monkeypatch, run_async, setup):
    card, extra = SETUPS[setup](rig)
    after = restarted(rig, mk_bot, monkeypatch, run_async)
    for msg, verb in ((card, "allow"), (card, "deny"), (card, "opt0"),
                      (extra, "allow"), (extra, "opt1")):
        after.tap(msg, verb)
    assert after.keys == []


@pytest.mark.parametrize("setup", SETUPS)
def test_after_restart_taps_send_no_decision(rig, mk_bot, monkeypatch, run_async, setup):
    card, extra = SETUPS[setup](rig)
    after = restarted(rig, mk_bot, monkeypatch, run_async)
    for msg in (card, extra):
        after.tap(msg, "allow")
    assert after.decisions == []


# -- exactly one open: 8.102 unchanged ----------------------------------------------------------

def test_one_claude_question_open_is_saved(rig):
    rig.question()
    assert "open_prompt" in saved(rig)


def test_one_claude_permission_open_is_saved(rig):
    rig.claude_bash(hook=False)
    assert "open_prompt" in saved(rig)


def test_two_then_one_left_is_saved(rig):
    """Boundary 2 -> 1: Claude's question answered, its permission left."""
    card, _extra = claude_question_and_permission(rig)
    rig.tap(card, "opt0")
    assert "open_prompt" in saved(rig)


def test_one_left_after_restart_is_restored(rig, mk_bot, monkeypatch, run_async):
    card, _extra = claude_question_and_permission(rig)
    rig.tap(card, "opt0")
    rig.bot.registry.save()
    again = SessionRegistry()
    again.load()
    assert again.get(S).restored_open_prompt is not None


def test_lone_agent_prompt_not_saved(rig):
    """8.102: a subagent's prompt is never saved (its answer goes to the
    subagent's own transcript)."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    assert "open_prompt" not in saved(rig)
