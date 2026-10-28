"""Taps on two open prompts when one may be Claude Code's risky-rm ask
(roadmap 8.128; design.md criteria 9, 10, 11, 12, 13 and 15), through the
real hook receiver, notify path and tap handler (``tests/two_prompts_rig``).

The harm on main: a background agent's prompt waited past aipager's hook
wait, Claude's own rm prompt (with its countdown) opened on top of it, and
Allow on the agent's card typed Enter into the rm dialog. aipager leaves
the countdown on, so every session now refuses every typed answer in such
an overlap."""

from __future__ import annotations

import pytest

import tests.two_prompts_rig as rigmod
from aipager import prompt_stack
from aipager.bot.callbacks import PROMPT_BEHIND, PROMPT_ORDER_UNKNOWN
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, Rig, answers

UNKNOWN = str(PROMPT_ORDER_UNKNOWN)
BEHIND = str(PROMPT_BEHIND)
REQ_BG = "req-bg"
REQ_RM = "req-rm"
MODES = ["bypassPermissions", "auto"]


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)


def extra_ids(rig) -> list[int]:
    return [m["msg_id"] for m in rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


def decided(rig):
    return [(r["request_id"], d["behavior"]) for r, d in rig.decisions]


def overlap(rig, mode, *, rm_hook_waits=False):
    """A background agent's prompt whose hook gave up, then Claude's own rm
    prompt in *mode*. Returns ``(agent's surface, rm's surface)``."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ_BG)
    rig.timeout(REQ_BG)
    rig.claude_bash("rm -rf /home/alice", request_id=REQ_RM, mode=mode)
    if not rm_hook_waits:
        rig.timeout(REQ_RM)
    return CARD, extra_ids(rig)[0]


# -- criterion 9: typed taps are refused --------------------------------

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_agents_typed_answer_is_refused_beside_the_rm(rig, mode, verb):
    agent, _rm = overlap(rig, mode)
    q = rig.tap(agent, verb)
    assert (answers(q), rig.keys, rig.decisions) == ([UNKNOWN], [], [])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_rms_typed_answer_is_refused(rig, mode, verb):
    _agent, rm = overlap(rig, mode)
    q = rig.tap(rm, verb)
    assert (answers(q), rig.keys, rig.decisions) == ([UNKNOWN], [], [])


@pytest.mark.parametrize("mode", MODES)
def test_refused_taps_keep_both_prompts_open(rig, mode):
    agent, rm = overlap(rig, mode)
    rig.tap(agent, "allow")
    rig.tap(rm, "deny")
    assert len(rig.sess.open_prompts()) == 2
    assert rig.sess.status == Status.INTERACTIVE


def test_the_agents_answer_is_typed_once_the_rm_is_answered_in_the_terminal(rig):
    """The rm's tool runs (answered at the terminal): the agent alone is
    open again and its tap types."""
    agent, _rm = overlap(rig, "bypassPermissions")
    rig.hook("PreToolUse", tool_name="Bash",
             tool_input={"command": "rm -rf /home/alice",
                         "description": "rm -rf /home/alice"},
             tool_use_id="toolu_p1")
    q = rig.tap(agent, "allow")
    assert UNKNOWN not in answers(q)
    assert rig.keys != []


def test_the_rm_beside_a_parked_agent_is_refused(rig):
    """Literal reading: an agent whose hook still waits counts too."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ_BG)
    rig.claude_bash("rm -rf /home/alice", request_id=REQ_RM, mode="auto")
    rig.timeout(REQ_RM)
    q = rig.tap(extra_ids(rig)[0], "allow")
    assert (answers(q), rig.keys) == ([UNKNOWN], [])


@pytest.mark.parametrize("mode", MODES)
def test_a_question_beside_the_rm_is_refused(rig, mode):
    rig.question(mode=mode)
    rig.claude_bash("rm -rf /home/alice", request_id=REQ_RM, mode=mode)
    rig.timeout(REQ_RM)
    q = rig.tap(CARD, "opt0")
    assert (answers(q), rig.keys) == ([UNKNOWN], [])


# -- criterion 10: an answer through a waiting hook is unaffected ----------------------------

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_rm_answered_through_its_waiting_hook(rig, mode, verb):
    _agent, rm = overlap(rig, mode, rm_hook_waits=True)
    rig.tap(rm, verb)
    assert (decided(rig), rig.keys) == ([(REQ_RM, verb)], [])


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_parked_agent_answered_through_its_hook_beside_the_rm(rig, verb):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ_BG)
    rig.claude_bash("rm -rf /home/alice", request_id=REQ_RM, mode="bypassPermissions")
    rig.timeout(REQ_RM)
    rig.tap(CARD, verb)
    assert (decided(rig), rig.keys) == ([(REQ_BG, verb)], [])


# -- criterion 12: non-candidates keep today's verdicts --------------------------------------

@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", None],
                         ids=["default", "accept-edits", "plan", "missing"])
def test_non_candidate_mode_keeps_arrival_order(rig, mode):
    agent, rm = overlap(rig, mode)
    rig.tap(rm, "allow")
    assert rig.keys == []
    rig.tap(agent, "allow")
    assert rig.keys != []


def test_agents_bypass_bash_beside_question_keeps_arrival_order(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id=REQ_BG, mode="bypassPermissions")
    rig.timeout(REQ_BG)
    rig.question(mode="bypassPermissions")
    q = rig.tap(extra_ids(rig)[0], "opt0")
    assert answers(q) == [BEHIND]
    rig.tap(CARD, "allow")
    assert rig.keys != []


# -- criterion 13: the Notification fallback and a restored prompt ---------------------------

def notified_then_agent(rig, **notification_fields):
    rig.launch(BG)
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash", **notification_fields)
    rig.agent_bash(BG, "make", request_id=REQ_BG)
    rig.timeout(REQ_BG)
    return CARD, extra_ids(rig)[0]


def test_notification_prompt_without_a_mode_is_a_candidate(rig):
    notified, _agent = notified_then_agent(rig)
    assert rig.sess.open_prompts()[0]["tool_info"]["order"]["mode"] == \
        prompt_stack.MODE_UNKNOWN
    q = rig.tap(notified, "allow")
    assert (answers(q), rig.keys) == ([UNKNOWN], [])


@pytest.mark.parametrize("mode", ["", 0, None], ids=["empty", "not-a-str", "null"])
def test_notification_prompt_with_an_empty_mode_is_a_candidate(rig, mode):
    """An empty or non-str mode on the datagram is no mode either: recorded
    as unknown, not as "" (which no rule would count as a candidate)."""
    notified, _agent = notified_then_agent(rig, permission_mode=mode)
    assert rig.sess.open_prompts()[0]["tool_info"]["order"]["mode"] == \
        prompt_stack.MODE_UNKNOWN
    q = rig.tap(notified, "allow")
    assert (answers(q), rig.keys) == ([UNKNOWN], [])


def test_notification_prompt_with_a_default_mode_keeps_arrival_order(rig):
    """Control: the same datagram with its mode is no candidate."""
    notified, _agent = notified_then_agent(rig, permission_mode="default")
    rig.tap(notified, "allow")
    assert rig.keys != []


def drop_orders(rig):
    for perm in rig.sess.open_prompts():
        perm["tool_info"].pop("order", None)


def test_restored_bash_prompt_is_a_candidate(rig):
    rig.claude_bash("make", request_id="req-p1", mode="default")
    rig.timeout("req-p1")
    rig.hook("PermissionRequest", tool_name="Edit",
             tool_input={"file_path": "/home/alice/a.py", "old_string": "a",
                         "new_string": "b"},
             tool_use_id="toolu_e1")
    drop_orders(rig)
    q = rig.tap(extra_ids(rig)[0], "allow")
    assert (answers(q), rig.keys) == ([UNKNOWN], [])


def test_restored_prompt_of_unknown_tool_is_a_candidate(rig):
    rig.claude_bash("make", request_id="req-p1", mode="default")
    rig.timeout("req-p1")
    rig.hook("PermissionRequest", tool_name="Edit",
             tool_input={"file_path": "/home/alice/a.py", "old_string": "a",
                         "new_string": "b"},
             tool_use_id="toolu_e1")
    drop_orders(rig)
    first = rig.sess.open_prompts()[0]
    first["tool_info"].pop("name", None)
    q = rig.tap(extra_ids(rig)[0], "allow")
    assert (answers(q), rig.keys) == ([UNKNOWN], [])


def test_restored_non_bash_prompts_keep_arrival_order(rig):
    for n, path in enumerate(("/home/alice/a.py", "/home/alice/b.py")):
        rig.hook("PermissionRequest", tool_name="Edit",
                 tool_input={"file_path": path, "old_string": "a", "new_string": "b"},
                 tool_use_id=f"toolu_e{n}")
    drop_orders(rig)
    q = rig.tap(extra_ids(rig)[0], "allow")
    assert (answers(q), rig.keys) == ([BEHIND], [])


# -- the order facts the hook receiver writes ------------------------------------------------

def test_permission_request_records_tool_and_mode(rig):
    rig.claude_bash("rm -rf /home/alice", request_id=REQ_RM, mode="auto")
    order = rig.sess.open_prompts()[0]["tool_info"]["order"]
    assert (order["tool"], order["mode"]) == ("Bash", "auto")


def test_permission_request_without_a_mode_records_empty(rig):
    rig.claude_bash("rm -rf /home/alice", request_id=REQ_RM)
    order = rig.sess.open_prompts()[0]["tool_info"]["order"]
    assert (order["tool"], order["mode"]) == ("Bash", "")


def test_question_records_its_mode(rig):
    rig.question(mode="bypassPermissions")
    order = rig.sess.open_prompts()[0]["tool_info"]["order"]
    assert (order["tool"], order["mode"]) == ("AskUserQuestion", "bypassPermissions")


# -- criterion 15: a role's auto-deny waits, never loops, then types -------------------------

#: The rig group's user (``two_prompts_rig.scopes``): a user's turn has
#: Bash refused by the role's rules, typed as Escape when no hook waits.
USER_ID = rigmod.scopes()[0].members[1].id


@pytest.fixture
def drig(mk_bot, monkeypatch, run_async):
    r = Rig(mk_bot, monkeypatch, run_async)
    r.sess.last_driver_user_id = USER_ID
    return r


def auto_deny_beside_rm(r):
    """A background agent's question whose hook still waits (its dialog is
    not in the terminal yet), then Claude's rm in Full access with no hook,
    refused by the user's rules: before 8.128 its Escape was typed at once,
    the rm being certainly on top."""
    r.launch(BG)
    r.question(agent=BG, tool_use_id="toolu_agent_q")
    r.hook("PermissionRequest", tool_name="AskUserQuestion", tool_input=rigmod.QUESTION,
           tool_use_id="toolu_agent_q", agent_id=BG, agent_type="ship-reviewer",
           aipager_reply_addr="/nonexistent/reply-q.sock", aipager_request_id="req-q")
    r.claude_bash("rm -rf /home/alice", hook=False, mode="bypassPermissions")


def close_agents_question(r):
    r.hook("PostToolUse", tool_name="AskUserQuestion", tool_input={},
           tool_use_id="toolu_agent_q", tool_response={}, agent_id=BG,
           agent_type="ship-reviewer")


def test_auto_deny_beside_the_rm_types_nothing(drig):
    auto_deny_beside_rm(drig)
    assert drig.keys == []
    assert [p["tool_info"]["name"] for p in drig.sess.open_prompts()] == [
        "AskUserQuestion", "Bash"]
    assert drig.sess.status == Status.INTERACTIVE


def test_auto_deny_types_its_escape_once_the_other_prompt_closes(drig):
    auto_deny_beside_rm(drig)
    close_agents_question(drig)
    assert drig.keys == ["Escape"]
    assert drig.sess.open_prompts() == []


def test_auto_deny_retried_on_a_hook_timeout_still_types_nothing(drig):
    """Another wake-up of the waiting refusals (a hook gave up) is no
    loop and types nothing while the overlap lasts."""
    auto_deny_beside_rm(drig)
    drig.timeout("req-q")
    assert drig.keys == []
    close_agents_question(drig)
    assert drig.keys == ["Escape"]
