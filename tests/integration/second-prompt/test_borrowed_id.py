"""An agent's PermissionRequest that carries no tool_use_id of its own
(fixes-2, tester-iter1-001/-002): it adopts the id of the agent's ONE
unfinished call of the same tool with the same whole input, so that
call's PostToolUse / PostToolUseFailure closes the prompt. When the match
is not unique (two parallel identical calls), the prompt stays open
rather than guessing which call ended; another tool's, another input's or
another agent's call never closes it.

Every scenario keeps Claude's own question open beside the agent's
prompt, so ``owners()`` shows exactly which prompt a hook closed.
"""

from __future__ import annotations

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, OTHER

REQ = "req-bg"


def owners(rig):
    return [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()]


def bash(command):
    return {"command": command, "description": command}


def call(rig, agent, tool_use_id, command="make", event="PreToolUse", tool="Bash"):
    extra = {}
    if event == "PostToolUse":
        extra = {"tool_response": {"stdout": "ok"}}
    elif event == "PostToolUseFailure":
        extra = {"error": "The user doesn't want to proceed with this tool use."}
    tool_input = bash(command) if tool == "Bash" else {"file_path": "/src/a.py"}
    rig.hook(event, tool_name=tool, tool_input=tool_input, tool_use_id=tool_use_id,
             agent_id=agent, agent_type="ship-reviewer", **extra)


def ask(rig, agent=BG, command="make"):
    """The agent's PermissionRequest, no tool_use_id of its own."""
    rig.hook("PermissionRequest", tool_name="Bash", tool_input=bash(command),
             agent_id=agent, agent_type="ship-reviewer",
             aipager_reply_addr=f"/nonexistent/reply-{agent}.sock",
             aipager_request_id=REQ)


def start(rig):
    rig.launch(BG)
    rig.question()


# -- one matching unfinished call: its end closes the prompt ------------------------------

def test_single_call_end_closes_the_agent_prompt(rig):
    start(rig)
    call(rig, BG, "toolu_a")
    ask(rig)
    call(rig, BG, "toolu_a", event="PostToolUse")
    assert owners(rig) == [None]


def test_single_call_failure_closes_the_agent_prompt(rig):
    start(rig)
    call(rig, BG, "toolu_a")
    ask(rig)
    call(rig, BG, "toolu_a", event="PostToolUseFailure")
    assert owners(rig) == [None]


def test_single_call_end_with_lone_prompt_leaves_waiting(rig):
    """Boundary: the agent's prompt was the only one: nothing is open."""
    rig.launch(BG)
    call(rig, BG, "toolu_a")
    ask(rig)
    call(rig, BG, "toolu_a", event="PostToolUse")
    assert rig.sess.status != Status.INTERACTIVE


# -- two parallel identical calls: no guess -----------------------------------------------------

def test_parallel_identical_calls_one_end_keeps_the_prompt_open(rig):
    start(rig)
    call(rig, BG, "toolu_a")
    call(rig, BG, "toolu_b")
    ask(rig)
    call(rig, BG, "toolu_a", event="PostToolUse")
    assert owners(rig) == [None, BG]


def test_parallel_identical_calls_other_end_keeps_the_prompt_open(rig):
    start(rig)
    call(rig, BG, "toolu_a")
    call(rig, BG, "toolu_b")
    ask(rig)
    call(rig, BG, "toolu_b", event="PostToolUse")
    assert owners(rig) == [None, BG]


def test_parallel_identical_calls_failure_keeps_the_prompt_open(rig):
    start(rig)
    call(rig, BG, "toolu_a")
    call(rig, BG, "toolu_b")
    ask(rig)
    call(rig, BG, "toolu_a", event="PostToolUseFailure")
    assert owners(rig) == [None, BG]


def test_parallel_identical_prompt_still_answered_by_hook(rig):
    """Left open, it is still live: Allow reaches its parked hook."""
    start(rig)
    call(rig, BG, "toolu_a")
    call(rig, BG, "toolu_b")
    ask(rig)
    call(rig, BG, "toolu_a", event="PostToolUse")
    agent_msg = [m["msg_id"] for m in rig.prompt_messages()
                 if m.get("reply_to_message_id") == CARD][0]
    rig.tap(agent_msg, "allow")
    assert [r["request_id"] for r, _d in rig.decisions] == [REQ]


def test_parallel_identical_prompt_closed_by_the_agents_stop(rig):
    start(rig)
    call(rig, BG, "toolu_a")
    call(rig, BG, "toolu_b")
    ask(rig)
    call(rig, BG, "toolu_a", event="PostToolUse")
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-reviewer")
    assert owners(rig) == [None]


def test_earlier_identical_call_ended_before_the_request_leaves_one_match(rig):
    """Boundary: two identical calls, one already finished when the
    request arrives: the one unfinished call is the match."""
    start(rig)
    call(rig, BG, "toolu_a")
    call(rig, BG, "toolu_b")
    call(rig, BG, "toolu_a", event="PostToolUse")
    ask(rig)
    call(rig, BG, "toolu_b", event="PostToolUse")
    assert owners(rig) == [None]


# -- parallel calls with other inputs: told apart by the input ------------------------------------

def test_parallel_other_input_end_keeps_the_prompt_open(rig):
    start(rig)
    call(rig, BG, "toolu_a", "make")
    call(rig, BG, "toolu_b", "pytest")
    ask(rig, command="pytest")
    call(rig, BG, "toolu_a", "make", event="PostToolUse")
    assert owners(rig) == [None, BG]


def test_parallel_asking_call_end_closes_the_prompt(rig):
    start(rig)
    call(rig, BG, "toolu_a", "make")
    call(rig, BG, "toolu_b", "pytest")
    ask(rig, command="pytest")
    call(rig, BG, "toolu_b", "pytest", event="PostToolUse")
    assert owners(rig) == [None]


def test_other_tool_call_end_keeps_the_prompt_open(rig):
    start(rig)
    call(rig, BG, "toolu_r", tool="Read")
    call(rig, BG, "toolu_a")
    ask(rig)
    call(rig, BG, "toolu_r", tool="Read", event="PostToolUse")
    assert owners(rig) == [None, BG]


# -- another agent's identical call is never borrowed ---------------------------------------------

def test_other_agents_identical_call_end_keeps_the_prompt_open(rig):
    rig.launch(OTHER, agent_type="ship-tester")
    start(rig)
    call(rig, OTHER, "toolu_x")
    call(rig, BG, "toolu_a")
    ask(rig)
    call(rig, OTHER, "toolu_x", event="PostToolUse")
    assert owners(rig) == [None, BG]


def test_request_with_no_unfinished_call_is_not_closed_by_another_agent(rig):
    """Partition: the asking agent has no call on record at all; only
    another agent made the identical call."""
    rig.launch(OTHER, agent_type="ship-tester")
    start(rig)
    call(rig, OTHER, "toolu_x")
    ask(rig)
    call(rig, OTHER, "toolu_x", event="PostToolUse")
    assert owners(rig) == [None, BG]
