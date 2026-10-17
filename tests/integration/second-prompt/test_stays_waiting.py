"""The session stays INTERACTIVE until the last prompt closes (design.md
success criterion 5): across an agent's tool call, Claude's own tool
call, a continuation, a terminal prompt and Claude's Stop while a
background agent's prompt is open. Each close path closes only what it
proves closed; phantom SubagentStops close nothing."""

from __future__ import annotations

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, OTHER

REQ = "req-bg"


def extra_ids(rig):
    return [m["msg_id"] for m in rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


def owners(rig):
    return [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()]


def both_open(rig, order="agent-first"):
    rig.launch(BG)
    if order == "agent-first":
        rig.agent_bash(BG, "make", request_id=REQ)
        rig.question()
    else:
        rig.question()
        rig.agent_bash(BG, "make", request_id=REQ)


def agent_tool(rig, agent, name="Read", event="PreToolUse", **extra):
    rig.hook(event, tool_name=name, tool_input={"file_path": "/src/a.py"},
             tool_use_id=f"toolu_{agent}_{name}", agent_id=agent,
             agent_type="ship-reviewer", **extra)


def claude_tool(rig, name="Read", event="PreToolUse", tool_use_id="toolu_c_read", **extra):
    rig.hook(event, tool_name=name, tool_input={"file_path": "/src/a.py"},
             tool_use_id=tool_use_id, **extra)


# -- a subagent's tool call while Claude's question is open -------------------------------

def test_running_agent_tool_call_keeps_lone_question_interactive(rig):
    """The original 8.120 symptom: one prompt open (Claude's question), an
    agent that owns nothing works on; the session must not go BUSY."""
    rig.launch(BG)
    rig.question()
    agent_tool(rig, BG)
    assert rig.sess.status == Status.INTERACTIVE


def test_running_agent_tool_call_keeps_lone_question_open(rig):
    rig.launch(BG)
    rig.question()
    agent_tool(rig, BG)
    assert owners(rig) == [None]


def test_unknown_agent_tool_call_keeps_question_interactive(rig):
    rig.question()
    agent_tool(rig, OTHER)
    assert rig.sess.status == Status.INTERACTIVE


def test_owner_tool_call_closes_only_its_own_prompt(rig):
    both_open(rig, "claude-first")
    agent_tool(rig, BG)
    assert owners(rig) == [None]


def test_owner_tool_call_with_question_open_stays_interactive(rig):
    both_open(rig, "agent-first")
    agent_tool(rig, BG)
    assert rig.sess.status == Status.INTERACTIVE


def test_other_agent_tool_call_closes_nothing(rig):
    rig.launch(OTHER, agent_type="ship-tester")  # before: a launch is Claude's own tool call
    both_open(rig)
    agent_tool(rig, OTHER)
    assert len(rig.sess.open_prompts()) == 2


def test_owner_tool_call_with_nothing_else_open_goes_busy(rig):
    """Boundary: the last prompt closes, the session works again."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ)
    agent_tool(rig, BG)
    assert rig.sess.status == Status.BUSY


# -- Claude's own tool call while an agent's prompt is open ------------------------------

def test_claude_tool_call_keeps_agent_prompt_open(rig):
    both_open(rig)
    claude_tool(rig)
    assert owners(rig) == [BG]


def test_claude_tool_call_with_agent_prompt_stays_interactive(rig):
    both_open(rig)
    claude_tool(rig)
    assert rig.sess.status == Status.INTERACTIVE


def test_claude_tool_call_with_only_its_prompt_goes_busy(rig):
    rig.question()
    claude_tool(rig)
    assert rig.sess.status == Status.BUSY


def test_agent_prompt_answerable_after_claude_tool_call(rig):
    both_open(rig)
    claude_tool(rig)
    rig.tap(CARD, "allow")
    assert [r["request_id"] for r, _d in rig.decisions] == [REQ]


# -- PostToolUse / PostToolUseFailure -------------------------------------------------------

def test_question_post_tool_use_closes_only_the_question(rig):
    both_open(rig)
    claude_tool(rig, name="AskUserQuestion", event="PostToolUse", tool_use_id="toolu_q1",
                tool_response={})
    assert owners(rig) == [BG]


BASH = {"command": "make", "description": "make"}


def agent_bash_seen(rig, *, own_id=False):
    """Claude Code's order: the agent's PreToolUse, then its
    PermissionRequest (with or without its own tool_use_id)."""
    rig.launch(BG)
    rig.question()
    rig.hook("PreToolUse", tool_name="Bash", tool_input=BASH, tool_use_id="toolu_bg_bash",
             agent_id=BG, agent_type="ship-reviewer")
    fields = {"tool_use_id": "toolu_bg_bash"} if own_id else {}
    rig.hook("PermissionRequest", tool_name="Bash", tool_input=BASH, agent_id=BG,
             agent_type="ship-reviewer", aipager_reply_addr="/nonexistent/reply-bg.sock",
             aipager_request_id=REQ, **fields)


def agent_bash_done(rig, event="PostToolUse"):
    extra = ({"tool_response": {"stdout": "ok"}} if event == "PostToolUse"
             else {"error": "The user doesn't want to proceed with this tool use."})
    rig.hook(event, tool_name="Bash", tool_input=BASH, tool_use_id="toolu_bg_bash",
             agent_id=BG, agent_type="ship-reviewer", **extra)


def test_agent_pretooluse_before_its_request_keeps_question_open(rig):
    agent_bash_seen(rig)
    assert owners(rig) == [None, BG]


def test_agent_post_tool_use_closes_the_extra_only(rig):
    agent_bash_seen(rig)
    agent_bash_done(rig)
    assert owners(rig) == [None]


def test_agent_post_tool_use_with_own_id_closes_the_extra_only(rig):
    agent_bash_seen(rig, own_id=True)
    agent_bash_done(rig)
    assert owners(rig) == [None]


def test_agent_post_tool_use_failure_closes_its_prompt(rig):
    agent_bash_seen(rig)
    agent_bash_done(rig, "PostToolUseFailure")
    assert owners(rig) == [None]


def test_agent_post_tool_use_keeps_session_interactive(rig):
    agent_bash_seen(rig)
    agent_bash_done(rig)
    assert rig.sess.status == Status.INTERACTIVE


# -- SubagentStop ------------------------------------------------------------------------------

def test_subagent_stop_closes_its_prompts_only(rig):
    both_open(rig)
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    assert owners(rig) == [None]


def test_subagent_stop_keeps_session_interactive(rig):
    both_open(rig)
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    assert rig.sess.status == Status.INTERACTIVE


def test_phantom_subagent_stop_closes_nothing(rig):
    """Empty type, unknown id: these arrive constantly and are tolerated."""
    both_open(rig)
    rig.hook("SubagentStop", agent_id="0000000000000000f", agent_type="")
    assert len(rig.sess.open_prompts()) == 2


def test_subagent_stop_without_id_closes_nothing(rig):
    both_open(rig)
    rig.hook("SubagentStop", agent_type="")
    assert len(rig.sess.open_prompts()) == 2


def test_other_agent_stop_closes_nothing(rig):
    rig.launch(OTHER, agent_type="ship-tester")
    both_open(rig)
    rig.hook("SubagentStop", agent_id=OTHER, agent_type="ship-tester")
    assert len(rig.sess.open_prompts()) == 2


# -- continuation and terminal prompt -----------------------------------------------------------

def test_continuation_stays_interactive_with_agent_prompt(rig):
    both_open(rig)
    rig.hook("UserPromptSubmit",
             prompt=(f"<task-notification>\n<task-id>{OTHER}</task-id>\n"
                     "Background agent finished."))
    assert rig.sess.status == Status.INTERACTIVE


def test_terminal_prompt_keeps_agent_prompt_open(rig):
    """A prompt typed at the terminal while an agent's dialog waits ends
    Claude's own prompts but not the agent's."""
    both_open(rig)
    rig.hook("UserPromptSubmit", prompt="carry on with the docs")
    assert owners(rig) == [BG]


def test_terminal_prompt_with_agent_prompt_stays_interactive(rig):
    both_open(rig)
    rig.hook("UserPromptSubmit", prompt="carry on with the docs")
    assert rig.sess.status == Status.INTERACTIVE


# -- Claude's Stop while a background agent's prompt is open --------------------------------------

def stop(rig, event="Stop"):
    fields = {"stop_hook_active": False, "last_assistant_message": "Waiting on the agent."}
    if event == "StopFailure":
        fields = {"error": "rate_limit"}
    rig.hook(event, **fields)


def test_stop_with_background_prompt_returns_to_interactive(rig):
    both_open(rig)
    rig.tap(extra_ids(rig)[0], "opt0")  # Claude's question answered
    stop(rig)
    assert rig.sess.status == Status.INTERACTIVE


def test_stop_with_background_prompt_dialog_is_open(rig):
    both_open(rig)
    rig.tap(extra_ids(rig)[0], "opt0")
    stop(rig)
    assert rig.sess.dialog_is_open() is True


def test_stop_with_background_prompt_keeps_it_open(rig):
    both_open(rig)
    rig.tap(extra_ids(rig)[0], "opt0")
    stop(rig)
    assert owners(rig) == [BG]


def test_stop_with_background_prompt_still_answered_by_hook(rig):
    both_open(rig)
    rig.tap(extra_ids(rig)[0], "opt0")
    stop(rig)
    # The card's prompt survives the turn's end as its own message.
    rig.tap(rig.prompt_messages()[-1]["msg_id"], "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, "allow")]


def test_stop_with_both_open_closes_claudes_question(rig):
    """Claude's Stop proves its own question closed."""
    both_open(rig)
    stop(rig)
    assert owners(rig) == [BG]


def test_stop_failure_with_background_prompt_returns_to_interactive(rig):
    both_open(rig)
    stop(rig, "StopFailure")
    assert rig.sess.status == Status.INTERACTIVE


def test_stop_with_dead_agent_prompt_goes_idle(rig):
    """Boundary: the agent stopped, so its prompt is gone; nothing waits."""
    both_open(rig)
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    rig.tap(extra_ids(rig)[0], "opt0")  # agent-first: the question is its own message
    stop(rig)
    assert rig.sess.status == Status.IDLE


def test_stop_with_nothing_open_goes_idle(rig):
    rig.question()
    rig.tap(CARD, "opt0")
    stop(rig)
    assert rig.sess.status == Status.IDLE
