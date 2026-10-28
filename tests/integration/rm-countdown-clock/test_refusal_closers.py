"""The hook closers after Claude Code refused its risky-removal prompt by
itself (roadmap 8.128; design.md criteria 15 and 16): Claude goes on with
its next tool call, or its turn ends, and no hook ever said the prompt
closed. Once the refusal window is open, those closers close it as
refused by Claude Code; before it, exactly as before."""

from __future__ import annotations

import pytest

from aipager import i18n, rm_countdown
from aipager.bot import rm_refusal
from aipager.state import Status
from tests.rm_clock_rig import RM, rm_prompt

EN = i18n.get("en")
REFUSED_ROW = f"🔑 Bash: {RM} → Refused by Claude Code"
IN_WINDOW = 120.0 - rm_countdown.WINDOW_LEAD_SECONDS + 1.0
BEFORE_WINDOW = 120.0 - rm_countdown.WINDOW_LEAD_SECONDS - 1.0


def next_tool_call(r):
    r.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"},
           tool_use_id="toolu_next")


def rows(r):
    return [s for s, _d in r.sess.tool_history]


def refused_rows(r):
    return [s for s in rows(r) if s.endswith("Refused by Claude Code")]


# -- criterion 15: the next tool call ---------------------------------------------------

def test_the_next_tool_call_after_the_window_closes_it_as_refused(rig, tx, clock, audits):
    rm_prompt(rig, tx)
    clock.at(IN_WINDOW)
    next_tool_call(rig)
    assert rig.sess.open_prompts() == []
    assert rig.sess.status == Status.BUSY
    assert refused_rows(rig) == [REFUSED_ROW]
    assert ("failed" in [d for s, d in rig.sess.tool_history if s == REFUSED_ROW])
    assert [(a["action"], a["denied"], a["via"]) for a in audits] == [
        ("Refused by Claude Code", True, "claude_code_timeout")]


def test_the_next_tool_call_edits_the_separate_message(cardless, tx, clock):
    rm_prompt(cardless, tx)
    [msg] = cardless.prompt_messages()
    clock.at(IN_WINDOW)
    next_tool_call(cardless)
    [call] = cardless.bot._app.bot.edit_message_text.await_args_list
    assert call.kwargs["message_id"] == msg["msg_id"]
    assert call.kwargs["text"].endswith(EN.html(rm_refusal.RM_REFUSED))
    assert call.kwargs["reply_markup"] is None
    assert cardless.sess.prompt_refused(msg["msg_id"])


def test_the_next_tool_call_before_the_window_closes_it_as_before(rig, tx, clock, audits):
    rm_prompt(rig, tx)
    clock.at(BEFORE_WINDOW)
    next_tool_call(rig)
    assert rig.sess.open_prompts() == []
    assert refused_rows(rig) == []
    assert audits == []
    assert rig.bot._app.bot.edit_message_text.await_args_list == []


def test_a_prompt_with_no_clock_closes_as_before(rig, tx, clock, audits):
    rig.claude_bash(RM, mode="default", suggestions=[], transcript_path=str(tx))
    clock.at(IN_WINDOW + 600)
    next_tool_call(rig)
    assert rig.sess.open_prompts() == []
    assert refused_rows(rig) == [] and audits == []


def test_an_agents_tool_call_leaves_claudes_prompt_open(rig, tx, clock, audits):
    from tests.two_prompts_rig import BG
    rig.launch(BG)
    rm_prompt(rig, tx)
    clock.at(IN_WINDOW)
    rig.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"},
             tool_use_id="toolu_agent", agent_id=BG, agent_type="ship-reviewer")
    assert len(rig.sess.open_prompts()) == 1
    assert audits == []


# -- criterion 16: the turn's end ---------------------------------------------------------

def finished_texts(r) -> list[str]:
    return r.frames + [t for _m, t, _k in r.card_edits] + [m["text"] or "" for m in r.sent]


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_the_turns_end_after_the_window_closes_it_as_refused(rig, tx, clock, audits, event):
    rm_prompt(rig, tx)
    clock.at(IN_WINDOW)
    rig.hook(event)
    assert rig.sess.status == Status.IDLE
    assert refused_rows(rig) == [REFUSED_ROW]
    assert [a["action"] for a in audits] == ["Refused by Claude Code"]


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_the_refused_row_is_on_the_finished_card(rig, tx, clock, event):
    rm_prompt(rig, tx)
    clock.at(IN_WINDOW)
    rig.frames.clear()
    rig.card_edits.clear()
    rig.hook(event)
    assert any("Refused by Claude Code" in t for t in finished_texts(rig))


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_the_turns_end_before_the_window_closes_it_as_before(rig, tx, clock, audits, event):
    rm_prompt(rig, tx)
    clock.at(BEFORE_WINDOW)
    rig.hook(event)
    assert refused_rows(rig) == [] and audits == []


def test_the_turns_end_edits_the_separate_message(cardless, tx, clock):
    rm_prompt(cardless, tx)
    [msg] = cardless.prompt_messages()
    clock.at(IN_WINDOW)
    cardless.hook("Stop")
    calls = [c for c in cardless.bot._app.bot.edit_message_text.await_args_list
             if c.kwargs.get("message_id") == msg["msg_id"]]
    assert len(calls) == 1
    assert calls[0].kwargs["reply_markup"] is None


def test_an_idle_notification_while_waiting_closes_nothing(rig, tx, clock, audits):
    rm_prompt(rig, tx)
    clock.at(IN_WINDOW)
    rig.hook("Notification", notification_type="idle_prompt",
             message="Claude is waiting for your input")
    assert len(rig.sess.open_prompts()) == 1
    assert audits == []
