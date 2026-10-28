"""A question or permission prompt stays on Telegram while a background
agent works (live 2026-10-15, 11:57).

Claude asked a question while a background review agent it had launched
kept making tool calls. Each of those calls moved the session from
INTERACTIVE back to BUSY ("its tool running means the prompt it waited on
was answered in the terminal", true only for that agent's own prompt), and
the busy card's next frame drew over the question within a second. The
question was only ever answerable in the terminal.

Now an agent's tool call closes only its own prompt, and no busy frame is
drawn over a prompt the card holds.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import Status
from tests import answer_status_bb as bb

BG, OTHER = "a1b2c3d4e5f60718a", "a9f8e7d6c5b4a3921"
QUESTION = {"questions": [{
    "question": "Which text should become small footer text?", "header": "Footer scope",
    "multiSelect": False,
    "options": [{"label": "Only under the dividers", "description": "a"},
                {"label": "Whole busy card too", "description": "b"}]}]}
LS = {"command": "ls", "description": "List files"}


@pytest.fixture
def rig(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = bb.make_sess(bot, label="boss")
    sess.status = Status.BUSY
    recv = hr.HookReceiver(bot.registry, bot.notify)
    prompts, frames = [], []
    bot._edit_busy_raw = AsyncMock(
        side_effect=lambda mid, text, **k: prompts.append(k.get("reply_markup")) or True)

    async def _rich(chat_id, msg_id, text, **kwargs):
        frames.append(text)
        return True

    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich", _rich)
    # Every hook-driven card edit is due: the guard, not the cadence, must
    # be what keeps a frame off the prompt.
    bot._card_edit_due = lambda *a, **k: True
    n = [0]

    def hook(event, **fields):
        n[0] += 1
        recv._recent_fingerprints.clear()
        payload = dict({"session": sess.name, "hook_event_name": event,
                        "transcript_path": "", "_seq": n[0]}, **fields)
        run_async(recv._on_datagram(json.dumps(payload).encode()))

    def agent_tool(agent, tool_use_id="toolu_bg1"):
        own = {"agent_id": agent, "agent_type": "ship-reviewer"}
        hook("PreToolUse", tool_name="Bash", tool_input=LS, tool_use_id=tool_use_id, **own)
        hook("PostToolUse", tool_name="Bash", tool_input=LS, tool_use_id=tool_use_id,
             tool_response={"stdout": ""}, **own)

    hook("SubagentStart", agent_id=BG, agent_type="ship-reviewer")
    hook("SubagentStart", agent_id=OTHER, agent_type="ship-tester")
    return bot, sess, hook, agent_tool, prompts, frames


def _held(sess, frames):
    """The prompt is still open and on the card: INTERACTIVE, its record
    kept, no busy frame drawn since, no animation ticking over it."""
    assert sess.status == Status.INTERACTIVE
    assert sess.pending_permission is not None
    assert frames == []
    assert not sess.animation_running()


def test_claudes_question_stays_while_a_background_agent_works(rig):
    """The live sequence: Claude asks, a background agent keeps working."""
    _bot, sess, hook, agent_tool, prompts, frames = rig
    hook("PreToolUse", tool_name="AskUserQuestion", tool_input=QUESTION,
         tool_use_id="toolu_q1")
    assert sess.status == Status.INTERACTIVE
    assert prompts and prompts[-1] is not None          # the question's buttons
    frames.clear()

    agent_tool(BG)
    agent_tool(BG, "toolu_bg2")

    _held(sess, frames)
    assert sess.pending_permission["tool_info"]["name"] == "AskUserQuestion"


def test_the_question_answered_lets_the_card_work_again(rig):
    _bot, sess, hook, agent_tool, _prompts, frames = rig
    hook("PreToolUse", tool_name="AskUserQuestion", tool_input=QUESTION,
         tool_use_id="toolu_q1")
    agent_tool(BG)
    # Answered (in the terminal): Claude's own call ends.
    hook("PostToolUse", tool_name="AskUserQuestion", tool_input=QUESTION,
         tool_use_id="toolu_q1", tool_response={"answers": {}})
    assert sess.status == Status.BUSY
    frames.clear()
    agent_tool(BG, "toolu_bg3")
    assert frames, "no frame once the question was answered"


def test_claudes_permission_prompt_stays_while_a_background_agent_works(rig):
    _bot, sess, hook, agent_tool, _prompts, frames = rig
    hook("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build/"},
         tool_use_id="toolu_p1")
    assert sess.status == Status.INTERACTIVE
    frames.clear()

    agent_tool(BG)

    _held(sess, frames)


def test_another_agents_prompt_stays_and_its_own_tool_closes_it(rig):
    _bot, sess, hook, agent_tool, _prompts, frames = rig
    hook("PermissionRequest", tool_name="Bash", tool_input={"command": "make"},
         tool_use_id="toolu_p2", agent_id=OTHER, agent_type="ship-tester")
    assert sess.status == Status.INTERACTIVE
    frames.clear()

    agent_tool(BG)                       # a different agent: the prompt stays
    _held(sess, frames)

    # The asking agent working again means its prompt was answered in the
    # terminal (8.58): the session is busy again.
    hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a.py"},
         tool_use_id="toolu_o1", agent_id=OTHER, agent_type="ship-tester")
    assert sess.status == Status.BUSY


def test_an_agents_own_question_closes_on_its_next_tool(rig):
    _bot, sess, hook, _agent_tool, _prompts, _frames = rig
    hook("PreToolUse", tool_name="AskUserQuestion", tool_input=QUESTION,
         tool_use_id="toolu_q2", agent_id=BG, agent_type="ship-reviewer")
    assert sess.status == Status.INTERACTIVE
    hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a.py"},
         tool_use_id="toolu_b9", agent_id=BG, agent_type="ship-reviewer")
    assert sess.status == Status.BUSY


# ---- whose prompt is open ------------------------------------------------------------

class _Sess:
    def __init__(self, perm=None, separate=None):
        self.pending_permission = perm
        self.pending_prompt_msg = {"perm": separate} if separate is not None else None
        self.interactive_entered_at = 0.0


@pytest.mark.parametrize("sess, owner", [
    (_Sess(), None),
    (_Sess({"tool_info": {"name": "Bash"}}), ""),
    (_Sess({"tool_info": {"name": "Bash", "agent_id": BG}}), BG),
    (_Sess({"tool_info": None}), None),
    (_Sess(separate={"tool_info": {"name": "AskUserQuestion"}}), ""),
    (_Sess(separate={"tool_info": {"name": "Bash", "agent_id": OTHER}}), OTHER),
    (_Sess({"tool_info": {"name": "Bash", "agent_id": BG}},
           separate={"tool_info": {"name": "Bash"}}), BG),
], ids=["none", "claudes", "an-agents", "unknown-tool", "separate-claudes",
        "separate-an-agents", "inline-first"])
def test_the_open_prompts_owner(sess, owner):
    assert hr._open_prompt_owner(sess) == owner


def test_with_no_prompt_on_record_an_agents_tool_still_ends_the_wait(rig):
    """Owner unknown: as before this fix (the prompt it waited on was
    answered in the terminal)."""
    bot, sess, hook, agent_tool, _prompts, _frames = rig
    bot.registry.transition(sess.name, Status.INTERACTIVE)
    sess.pending_permission = None
    sess.pending_prompt_msg = None
    agent_tool(BG)
    assert sess.status == Status.BUSY


def test_the_notification_fallback_records_whose_prompt_it_is(rig):
    _bot, sess, hook, agent_tool, _prompts, frames = rig
    hook("Notification", notification_type="permission_prompt",
         message="Claude needs your permission to use Bash",
         agent_id=OTHER, agent_type="ship-tester")
    assert sess.status == Status.INTERACTIVE
    assert sess.pending_permission["tool_info"]["agent_id"] == OTHER
    frames.clear()
    agent_tool(BG)
    _held(sess, frames)


# ---- no busy frame over an open prompt ------------------------------------------------

def test_no_busy_frame_is_drawn_over_an_inline_prompt(rig, run_async):
    bot, sess, hook, _agent_tool, _prompts, frames = rig
    hook("PreToolUse", tool_name="AskUserQuestion", tool_input=QUESTION,
         tool_use_id="toolu_q1")
    frames.clear()
    assert run_async(bot._edit_busy_rich(sess, "Working")) is False
    assert frames == []
    # The settled card still goes out (a turn that ends at the dialog).
    assert run_async(bot._edit_busy_rich(sess, "Done", final=True)) is True
    assert len(frames) == 1


def test_a_prompt_in_its_own_message_leaves_the_card_free(rig, run_async):
    bot, sess, _hook, _agent_tool, _prompts, frames = rig
    bot.registry.transition(sess.name, Status.INTERACTIVE)
    sess.pending_permission = None
    sess.pending_prompt_msg = {"perm": {"tool_info": {"name": "Bash"}}}
    sess.record_tool("Bash: ls", False)        # something new to draw
    frames.clear()
    assert run_async(bot._edit_busy_rich(sess, "Working")) is True
    assert len(frames) == 1


def test_an_agent_finishing_does_not_close_claudes_question(rig):
    """Its <task-notification> continuation may arrive while the question
    is open: the question stays, the job is marked as continuing."""
    _bot, sess, hook, _agent_tool, _prompts, frames = rig
    hook("PreToolUse", tool_name="AskUserQuestion", tool_input=QUESTION,
         tool_use_id="toolu_q1")
    frames.clear()
    hook("UserPromptSubmit", prompt="<task-notification>\n<task-id>" + BG
         + "</task-id>\n<status>completed</status>\n</task-notification>")
    _held(sess, frames)
    assert sess.job_continuation_active


def test_a_continuation_with_no_prompt_open_works_again(rig):
    bot, sess, hook, _agent_tool, _prompts, _frames = rig
    bot.registry.transition(sess.name, Status.INTERACTIVE)
    sess.pending_permission = None
    sess.pending_prompt_msg = None
    hook("UserPromptSubmit", prompt="<task-notification>\n<task-id>" + BG
         + "</task-id>\n<status>completed</status>\n</task-notification>")
    assert sess.status == Status.BUSY


# ---- a prompt whose agent has ended -------------------------------------------------

def _agents_prompt(hook, agent=OTHER):
    hook("PermissionRequest", tool_name="Bash", tool_input={"command": "make"},
         tool_use_id="toolu_p3", agent_id=agent, agent_type="ship-tester")


def test_the_asking_agent_stopping_ends_its_prompt(rig):
    _bot, sess, hook, agent_tool, _prompts, _frames = rig
    _agents_prompt(hook)
    assert sess.status == Status.INTERACTIVE
    hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")   # another agent
    assert sess.status == Status.INTERACTIVE
    hook("SubagentStop", agent_id=OTHER, agent_type="ship-tester")  # the asking one
    assert sess.status == Status.BUSY
    # Its buttons on the card type nothing now.
    assert sess.prompt_known_closed(sess.busy_msg_id)


@pytest.mark.parametrize("later", [Status.IDLE, Status.GONE], ids=["idle", "gone"])
def test_the_asking_agents_late_stop_wakes_nothing(rig, later):
    """Its prompt's record outlives the wait: a stop that comes once the
    session has moved on (an idle job, a session gone) leaves it there."""
    bot, sess, hook, _agent_tool, _prompts, _frames = rig
    _agents_prompt(hook)
    bot.registry.transition(sess.name, later, preserve_job_state=True)
    hook("SubagentStop", agent_id=OTHER, agent_type="ship-tester")
    assert sess.status == later


def test_the_asking_agents_late_refusal_wakes_nothing(rig):
    """Claude's turn ended while the agent's dialog was open; the agent's
    refused tool then fails: the idle job stays idle."""
    bot, sess, hook, _agent_tool, _prompts, _frames = rig
    _agents_prompt(hook)
    bot.registry.transition(sess.name, Status.IDLE, preserve_job_state=True)
    hook("PostToolUseFailure", tool_name="Bash", tool_input={"command": "make"},
         tool_use_id="toolu_p3", agent_id=OTHER, agent_type="ship-tester",
         error="The user doesn't want to proceed with this tool use.")
    assert sess.status == Status.IDLE


def test_the_asking_agents_refused_tool_ends_its_prompt(rig):
    """Refused in the terminal: its tool fails, and the agent goes on."""
    _bot, sess, hook, _agent_tool, _prompts, _frames = rig
    _agents_prompt(hook)
    hook("PostToolUseFailure", tool_name="Bash", tool_input={"command": "make"},
         tool_use_id="toolu_p3", agent_id=OTHER, agent_type="ship-tester",
         error="The user doesn't want to proceed with this tool use.")
    assert sess.status == Status.BUSY


def test_claudes_interrupted_tool_is_left_to_the_turns_end(rig):
    """An Escape while Claude's own allowed call ran ends its turn
    (dialog_ended_in_terminal): the interrupted failure alone does not
    move the session."""
    _bot, sess, hook, _agent_tool, _prompts, _frames = rig
    hook("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build/"},
         tool_use_id="toolu_p1")
    hook("PostToolUseFailure", tool_name="Bash", tool_input={"command": "rm -rf build/"},
         tool_use_id="toolu_p1", error="Interrupted by user", is_interrupt=True)
    assert sess.status == Status.INTERACTIVE


def test_claudes_failed_tool_works_again(rig):
    """Its call ran and failed (roadmap 8.151): the session works again."""
    _bot, sess, hook, _agent_tool, _prompts, _frames = rig
    hook("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build/"},
         tool_use_id="toolu_p1")
    hook("PostToolUseFailure", tool_name="Bash", tool_input={"command": "rm -rf build/"},
         tool_use_id="toolu_p1", error="Exit code 1")
    assert sess.status == Status.BUSY


def _continuation(hook):
    hook("UserPromptSubmit", prompt="<task-notification>\n<task-id>" + OTHER
         + "</task-id>\n<status>completed</status>\n</task-notification>")


def test_a_finished_agents_prompt_does_not_hold_a_continuation(rig):
    _bot, sess, hook, _agent_tool, _prompts, _frames = rig
    _agents_prompt(hook)
    sess.active_subagents.pop(OTHER, None)        # it ended with no stop seen
    _continuation(hook)
    assert sess.status == Status.BUSY


def test_a_running_agents_prompt_holds_a_continuation(rig):
    _bot, sess, hook, _agent_tool, _prompts, frames = rig
    _agents_prompt(hook)
    frames.clear()
    _continuation(hook)
    _held(sess, frames)


def test_a_continuation_after_a_turn_starts_one_however_old_the_prompt_record(rig):
    """An idle job with a separate-message prompt record from this wait:
    the continuation is a turn, not a prompt still open."""
    bot, sess, hook, _agent_tool, _prompts, _frames = rig
    sess.job_interim_seen = True
    bot.registry.transition(sess.name, Status.IDLE, preserve_job_state=True)
    sess.pending_permission = None
    sess.pending_prompt_msg = {"perm": {"tool_info": {"name": "Bash"},
                                        "wait_started_at": sess.interactive_entered_at}}
    assert hr._open_prompt_owner(sess) == ""
    _continuation(hook)
    assert sess.status == Status.BUSY


def test_a_separate_prompt_from_an_earlier_wait_has_no_owner():
    sess = _Sess(separate={"tool_info": {"name": "Bash"}, "wait_started_at": 5.0})
    sess.interactive_entered_at = 9.0
    assert hr._open_prompt_owner(sess) is None
