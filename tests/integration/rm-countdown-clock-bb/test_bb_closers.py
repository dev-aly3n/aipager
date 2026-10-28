"""The existing closers pick the refused wording once the refusal window
opened (design.md success criteria 15 and 16): the next parent PreToolUse,
Stop and StopFailure. Before the window they close as today, with no
refused record. No transcript is needed for these closers.

Also the id pairing (design.md decision 2): Claude Code 2.1.311 sends the
PermissionRequest without ``tool_use_id``; the parent PreToolUse before it
carries the id, and the monitor's close keys on it."""

from __future__ import annotations

import pytest

from aipager.state import Status
from tests.rm_clock_bb import REFUSE_AT, REFUSED_EN, RM, T0, VERB_EN, WINDOW_AT
from tests.two_prompts_rig import BG

BEFORE = WINDOW_AT - 0.5
AFTER = REFUSE_AT + 1.0
EDGE = WINDOW_AT


def next_tool(env, at):
    env.wall.t = at
    env.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/home/alice/a"},
             tool_use_id="toolu_next")


def stop(env, at, event="Stop"):
    env.wall.t = at
    env.hook(event)


CLOSERS = {"pretooluse": next_tool, "stop": stop,
           "stopfailure": lambda env, at: stop(env, at, "StopFailure")}


# -- after the window opened: refused ---------------------------------------------------------

@pytest.mark.parametrize("closer", CLOSERS)
@pytest.mark.parametrize("at", [AFTER, EDGE], ids=["after-deadline", "window-edge"])
def test_closer_after_the_window_records_the_refusal(env, closer, at):
    env.rm()
    CLOSERS[closer](env, at)
    assert [(a["action"], a["denied"]) for a in env.refused_audit()] == [
        ("Refused by Claude Code", True)]


@pytest.mark.parametrize("closer", CLOSERS)
def test_closer_after_the_window_edits_the_message_refused(env, closer):
    env.rm()
    original = env.rig.prompt_messages()[0]["text"]
    CLOSERS[closer](env, AFTER)
    assert [k["text"] for k in env.refused_text_edits()] == [original + "\n\n" + REFUSED_EN]


@pytest.mark.parametrize("closer", CLOSERS)
def test_closer_after_the_window_drops_the_keyboard(env, closer):
    env.rm()
    CLOSERS[closer](env, AFTER)
    assert [k.get("reply_markup", "absent") for k in env.refused_text_edits()] == [None]


@pytest.mark.parametrize("closer", CLOSERS)
def test_closer_after_the_window_adds_the_refused_row(env, closer):
    env.rm()
    CLOSERS[closer](env, AFTER)
    assert [row[0] for row in env.refused_rows()] == [f"🔑 Bash: {RM} → {VERB_EN}"]


@pytest.mark.parametrize("closer", CLOSERS)
def test_closer_after_the_window_says_allowed_nowhere(env, closer):
    env.rm()
    CLOSERS[closer](env, AFTER)
    blob = repr(env.audit()) + repr(env.rig.sess.tool_history) + repr(env.text_edits())
    assert "Allowed" not in blob


@pytest.mark.parametrize("closer", CLOSERS)
def test_closer_after_the_window_closes_the_prompt(env, closer):
    env.rm()
    CLOSERS[closer](env, AFTER)
    assert env.rig.sess.open_prompts() == []


def test_pretooluse_closer_types_nothing(env):
    env.rm(hook=False)
    next_tool(env, AFTER)
    assert env.rig.keys == []


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_stop_after_the_window_puts_the_row_on_the_finished_card(cenv, event):
    cenv.rm()
    stop(cenv, AFTER, event)
    assert any(VERB_EN in f for f in cenv.rig.frames)


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_stop_after_the_window_finished_card_row_is_not_a_tick(cenv, event):
    """Spec requirement 4: the history row shows it refused (not ✅)."""
    cenv.rm()
    stop(cenv, AFTER, event)
    rows = [line for f in cenv.rig.frames for line in f.splitlines() if VERB_EN in line]
    assert rows and not any("✅" in r for r in rows)


def test_stop_after_the_window_ends_idle(env):
    env.rm()
    stop(env, AFTER)
    assert env.rig.sess.status == Status.IDLE


# -- before the window: today's close ----------------------------------------------------------

@pytest.mark.parametrize("closer", CLOSERS)
def test_closer_before_the_window_records_no_refusal(env, closer):
    env.rm()
    CLOSERS[closer](env, BEFORE)
    assert (env.refused_audit(), env.refused_rows(), env.refused_text_edits()) == ([], [], [])


@pytest.mark.parametrize("closer", CLOSERS)
def test_closer_before_the_window_still_closes(env, closer):
    env.rm()
    CLOSERS[closer](env, BEFORE)
    assert env.rig.sess.open_prompts() == []


@pytest.mark.parametrize("closer", CLOSERS)
def test_closer_right_after_arrival_records_no_refusal(env, closer):
    env.rm()
    CLOSERS[closer](env, T0 + 1.0)
    assert env.refused_audit() == []


# -- non-clock prompts never take the refused wording ---------------------------------------------

@pytest.mark.parametrize("closer", CLOSERS)
@pytest.mark.parametrize("mode", ["default", None])
def test_a_non_clock_prompt_closer_never_says_refused(env, closer, mode):
    env.rm(mode=mode)
    CLOSERS[closer](env, AFTER + 600.0)
    assert env.refused_audit() == []


@pytest.mark.parametrize("closer", ["pretooluse", "stop"])
def test_a_non_removal_bash_prompt_closer_never_says_refused(env, closer):
    env.rm("ls -la")
    CLOSERS[closer](env, AFTER + 600.0)
    assert env.refused_audit() == []


def test_an_agents_prompt_is_not_closed_as_refused_by_claudes_next_tool(cenv):
    cenv.rig.launch(BG)
    cenv.hook("PermissionRequest", tool_name="Bash",
              tool_input={"command": RM, "description": RM}, agent_id=BG,
              agent_type="ship-reviewer", permission_mode="bypassPermissions",
              permission_suggestions=[],
              aipager_reply_addr="/nonexistent/reply-bg.sock", aipager_request_id="req-bg")
    next_tool(cenv, AFTER + 600.0)
    assert cenv.refused_audit() == []


# -- answered from the chat, then a closer -------------------------------------------------------

@pytest.mark.parametrize("closer", CLOSERS)
def test_a_chat_answer_then_a_late_closer_records_no_refusal(env, closer):
    env.rm()
    env.rig.tap(env.separate_ids()[0], "allow")
    CLOSERS[closer](env, AFTER)
    assert env.refused_audit() == []


# -- pairing with no payload id (2.1.311) -----------------------------------------------------------

def test_a_paired_id_lets_the_monitor_close_it(env):
    env.hook("PreToolUse", tool_name="Bash", tool_input={"command": RM, "description": RM},
             tool_use_id="toolu_paired")
    env.rm(tool_use_id=None, request_id="req-np")
    env.refused_transcript(tuid="toolu_paired")
    env.scan(AFTER)
    assert len(env.refused_audit()) == 1


def test_a_paired_id_ignores_another_calls_error(env):
    env.hook("PreToolUse", tool_name="Bash", tool_input={"command": RM, "description": RM},
             tool_use_id="toolu_paired")
    env.rm(tool_use_id=None, request_id="req-np")
    env.refused_transcript(tuid="toolu_unpaired")
    env.scan(AFTER)
    assert env.refused_audit() == []
