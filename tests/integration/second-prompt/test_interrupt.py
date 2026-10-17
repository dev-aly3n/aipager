"""A dialog answered "No" / Escape at the terminal while an agent's prompt
is open (fixes-2, rev-iter1-001). Claude Code writes the interrupt marker
to the transcript and fires no hook; the session monitor reads it.

The interrupt ends Claude's turn. Only a prompt whose owner survives the
interrupt keeps the session waiting: a running agent launched in the
background, or a foreground agent that moved to the background mid-run.
A foreground agent's or an unknown agent's prompt ends with the turn, so
the session never strands INTERACTIVE.

The transcript is a real file under ``tmp_path`` (the filesystem
boundary); no clock is patched.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.session_monitor import SessionMonitor
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, FG, OTHER, S, answers

MARKER = "[Request interrupted by user for tool use]"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def interrupt(rig, monkeypatch, run_async, tmp_path):
    """Write the terminal's "No" on Claude's question to the transcript
    and run one monitor scan."""
    async def _alive():
        return [S]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _alive)
    monitor = SessionMonitor(rig.bot.registry, rig.bot.notify)

    def go():
        path = tmp_path / "t.jsonl"
        now = time.time()
        lines = [
            {"type": "assistant", "timestamp": _iso(now - 3), "message": {
                "role": "assistant", "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "toolu_q1",
                             "name": "AskUserQuestion", "input": {}}]}},
            {"type": "user", "timestamp": _iso(now), "message": {
                "role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "toolu_q1", "is_error": True,
                    "content": "The user doesn't want to proceed with this tool use."}]}},
            {"type": "user", "timestamp": _iso(now), "message": {
                "role": "user", "content": [{"type": "text", "text": MARKER}]}},
        ]
        path.write_text("".join(json.dumps(x) + "\n" for x in lines))
        rig.sess.transcript_path = str(path)
        run_async(monitor._scan())

    return go


def owners(rig):
    return [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()]


def open_with(rig, kind, order="agent-first"):
    """Claude's question plus a prompt of an agent of launch *kind*."""
    agent = {"background": BG, "foreground": FG, "unknown": OTHER,
             "went-async": FG}[kind]
    if kind == "background":
        rig.launch(BG)
    elif kind == "foreground":
        rig.launch(FG, background=False)
    elif kind == "went-async":
        rig.launch(FG, background=False)
        rig.hook("PostToolUse", tool_name="Agent",
                 tool_input={"subagent_type": "ship-reviewer", "description": "Review",
                             "prompt": "Review it"},
                 tool_use_id=f"toolu_launch_{FG}",
                 tool_response={"isAsync": True, "status": "async_launched",
                                "agentId": FG})
    if order == "agent-first":
        rig.agent_bash(agent, "make", request_id="req-a")
        rig.question()
    else:
        rig.question()
        rig.agent_bash(agent, "make", request_id="req-a")
    return agent


# -- a surviving owner keeps the session waiting ------------------------------------------

@pytest.mark.parametrize("order", ["agent-first", "claude-first"])
def test_background_agent_prompt_keeps_waiting_after_interrupt(rig, interrupt, order):
    open_with(rig, "background", order)
    interrupt()
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("order", ["agent-first", "claude-first"])
def test_background_agent_prompt_survives_the_interrupt(rig, interrupt, order):
    open_with(rig, "background", order)
    interrupt()
    assert owners(rig) == [BG]


def test_background_agent_prompt_answerable_by_hook_after_interrupt(rig, interrupt):
    open_with(rig, "background")
    interrupt()
    rig.tap(CARD, "allow")
    assert [r["request_id"] for r, _d in rig.decisions] == ["req-a"]


def test_went_async_agent_prompt_keeps_waiting_after_interrupt(rig, interrupt):
    open_with(rig, "went-async")
    interrupt()
    assert owners(rig) == [FG]


# -- a foreground or unknown owner ends with the turn ----------------------------------------

@pytest.mark.parametrize("order", ["agent-first", "claude-first"])
@pytest.mark.parametrize("kind", ["foreground", "unknown"])
def test_non_surviving_agent_prompt_ends_the_turn(rig, interrupt, kind, order):
    open_with(rig, kind, order)
    interrupt()
    assert rig.sess.status == Status.IDLE


@pytest.mark.parametrize("kind", ["foreground", "unknown"])
def test_non_surviving_agent_prompt_closes(rig, interrupt, kind):
    open_with(rig, kind)
    interrupt()
    assert rig.sess.open_prompts() == []


@pytest.mark.parametrize("kind", ["foreground", "unknown"])
def test_non_surviving_agent_no_dialog_open(rig, interrupt, kind):
    open_with(rig, kind)
    interrupt()
    assert rig.sess.dialog_is_open() is False


def test_foreground_agent_button_after_interrupt_already_answered(rig, interrupt):
    open_with(rig, "foreground")
    interrupt()
    q = rig.tap(CARD, "allow")
    assert answers(q) == [str(ALREADY_ANSWERED)]


def test_foreground_agent_button_after_interrupt_sends_nothing(rig, interrupt):
    open_with(rig, "foreground")
    interrupt()
    rig.tap(CARD, "allow")
    assert (rig.keys, rig.decisions) == ([], [])


# -- controls ----------------------------------------------------------------------------------

def test_lone_question_interrupt_ends_the_turn(rig, interrupt):
    rig.question()
    interrupt()
    assert rig.sess.status == Status.IDLE


def test_background_agent_owning_nothing_does_not_keep_waiting(rig, interrupt):
    """A background agent runs but owns no prompt: nothing to wait on."""
    rig.launch(BG)
    rig.question()
    interrupt()
    assert rig.sess.dialog_is_open() is False


def test_mixed_owners_keep_only_the_background_one(rig, interrupt):
    """A background agent's and a foreground agent's prompts open with
    Claude's question: the interrupt leaves only the background one."""
    rig.launch(BG)
    rig.launch(FG, background=False)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(FG, "pytest", request_id="req-fg")
    rig.question()
    interrupt()
    assert owners(rig) == [BG]




def test_mixed_owners_dead_foreground_tap_types_nothing(rig, interrupt):
    """Error guessing: the foreground agent died with the interrupt, its
    parked hook with it (the send fails), and the background agent's
    dialog is now the one in the terminal (its hook gave up). A tap on the
    dead agent's Allow must not type into the background agent's dialog."""
    rig.launch(BG)
    rig.launch(FG, background=False)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(FG, "pytest", request_id="req-fg")
    rig.question()
    rig.timeout("req-bg")
    interrupt()
    fg_msg = [m["msg_id"] for m in rig.prompt_messages()
              if "pytest" in (m.get("text") or "")][0]
    rig.hook_ok = False
    rig.tap(fg_msg, "allow")
    assert rig.keys == []
