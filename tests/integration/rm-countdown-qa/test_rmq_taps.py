"""Taps on real cards (design.md success criteria 9-13), through the shared
two-prompts rig: the real hook receiver, notify path and tap handler.

Observed only from outside: the keys typed (``rig.keys``), the hook
decisions (``rig.decisions``), the pop-up of each tap (compared with the
``PROMPT_ORDER_UNKNOWN`` / ``PROMPT_BEHIND`` messages, never with
hand-written text) and ``TrackedSession.keys_verdict``.

``rig`` is a session as a hook creates it; aipager leaves the countdown
on in every session."""

from __future__ import annotations

import time

import pytest

from aipager.bot.callbacks import PROMPT_BEHIND, PROMPT_ORDER_UNKNOWN
from tests.two_prompts_rig import BG, CARD, FG, answers

CANDIDATE_MODES = ["bypassPermissions", "auto"]
UNKNOWN = str(PROMPT_ORDER_UNKNOWN)
BEHIND = str(PROMPT_BEHIND)


def toast(q) -> str:
    return " ".join(answers(q))


def extra_ids(r) -> list[int]:
    return [m["msg_id"] for m in r.prompt_messages() if m.get("reply_to_message_id") == CARD]


def decided(r):
    return [(rq["request_id"], d["behavior"]) for rq, d in r.decisions]


def verdicts(r) -> list[str]:
    now = time.time()
    return [r.sess.keys_verdict(p, now) for p in r.sess.open_prompts()]


# -- the scenes ---------------------------------------------------------------------------

def agent_gave_up_then_rm(r, mode, *, rm_hook_gives_up=True):
    """The live harm: a background agent's Bash whose hook gave up (its
    dialog is in the terminal), then Claude's own rm in Full access/Auto.
    Returns (agent's message, rm's message)."""
    r.launch(BG)
    r.agent_bash(BG, "make", request_id="req-bg", mode=mode)
    r.timeout("req-bg")
    r.claude_bash("rm -rf /home/alice/app", request_id="req-rm", mode=mode)
    if rm_hook_gives_up:
        r.timeout("req-rm")
    return CARD, extra_ids(r)[0]


def parked_agent_then_rm(r, mode):
    """A background agent's prompt still parked on its hook (certainly not in
    the terminal), then Claude's rm whose hook gave up."""
    r.launch(BG)
    r.agent_bash(BG, "make", request_id="req-bg", mode=mode)
    r.claude_bash("rm -rf /home/alice/app", request_id="req-rm", mode=mode)
    r.timeout("req-rm")
    return CARD, extra_ids(r)[0]


def fg_agent_then_rm(r, mode):
    r.launch(FG, background=False)
    r.agent_bash(FG, "make", hook=False, mode=mode)
    r.claude_bash("rm -rf /home/alice/app", hook=False, mode=mode)
    return CARD, extra_ids(r)[0]


def question_then_rm(r, mode):
    r.question(mode=mode)
    r.claude_bash("rm -rf /home/alice/app", hook=False, mode=mode)
    return CARD, extra_ids(r)[0]


def rm_then_question(r, mode):
    r.claude_bash("rm -rf /home/alice/app", hook=False, mode=mode)
    r.question(mode=mode)
    return extra_ids(r)[0], CARD


#: scene -> the verb a tap on its OLDER prompt uses
SCENES = {
    "agent-gave-up": agent_gave_up_then_rm,
    "fg-agent": fg_agent_then_rm,
    "question-first": question_then_rm,
}
OLDER_VERB = {"agent-gave-up": "allow", "fg-agent": "allow", "question-first": "opt0"}


# -- criterion 9: every typed tap is refused ----------------------------------------

@pytest.mark.parametrize("mode", CANDIDATE_MODES)
@pytest.mark.parametrize("scene", SCENES)
def test_every_verdict_is_unknown(rig, scene, mode):
    SCENES[scene](rig, mode)
    assert verdicts(rig) == ["unknown", "unknown"]


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
@pytest.mark.parametrize("scene", SCENES)
def test_tap_on_the_older_prompt_types_nothing(rig, scene, mode):
    older, _rm = SCENES[scene](rig, mode)
    rig.tap(older, OLDER_VERB[scene])
    assert rig.keys == []


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
@pytest.mark.parametrize("scene", SCENES)
def test_tap_on_the_older_prompt_says_it_cannot_tell(rig, scene, mode):
    older, _rm = SCENES[scene](rig, mode)
    q = rig.tap(older, OLDER_VERB[scene])
    assert toast(q) == UNKNOWN


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
@pytest.mark.parametrize("scene", SCENES)
def test_tap_on_the_rm_types_nothing(rig, scene, mode):
    _older, rm = SCENES[scene](rig, mode)
    rig.tap(rm, "allow")
    assert rig.keys == []


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
@pytest.mark.parametrize("scene", SCENES)
def test_tap_on_the_rm_says_it_cannot_tell(rig, scene, mode):
    _older, rm = SCENES[scene](rig, mode)
    q = rig.tap(rm, "allow")
    assert toast(q) == UNKNOWN


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_agent_card_tap_never_types_into_the_rm(rig, mode, verb):
    """The live harm, both verbs: nothing reaches the terminal."""
    agent, _rm = agent_gave_up_then_rm(rig, mode)
    rig.tap(agent, verb)
    assert rig.keys == []


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_refused_taps_leave_both_prompts_open(rig, mode):
    agent, rm = agent_gave_up_then_rm(rig, mode)
    rig.tap(agent, "allow")
    rig.tap(rm, "deny")
    assert len(rig.sess.open_prompts()) == 2


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_refused_tap_keeps_its_buttons(rig, mode):
    agent, _rm = agent_gave_up_then_rm(rig, mode)
    q = rig.tap(agent, "allow")
    q.edit_message_reply_markup.assert_not_awaited()


def test_rm_first_then_question_refuses_the_question(rig):
    """The candidate is the older one: the later question is refused too."""
    _rm, q_msg = rm_then_question(rig, "bypassPermissions")
    q = rig.tap(q_msg, "opt0")
    assert (rig.keys, toast(q)) == ([], UNKNOWN)


def test_rm_first_then_question_refuses_the_rm(rig):
    rm, _q = rm_then_question(rig, "auto")
    q = rig.tap(rm, "allow")
    assert (rig.keys, toast(q)) == ([], UNKNOWN)


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_rm_beside_a_parked_background_agent_is_refused(rig, mode):
    """The other dialog is certainly absent (its hook waits); the literal
    reading still refuses the rm's typed answer."""
    _agent, rm = parked_agent_then_rm(rig, mode)
    q = rig.tap(rm, "allow")
    assert (rig.keys, toast(q)) == ([], UNKNOWN)


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_once_the_other_closes_the_rm_is_answered_by_keys(rig, mode):
    """Boundary: the overlap ends when the agent's prompt is answered through
    its hook; the rm, now alone, is typed."""
    agent, rm = parked_agent_then_rm(rig, mode)
    rig.tap(agent, "allow")
    rig.tap(rm, "allow")
    assert rig.keys != []


# -- criterion 10: answers through a waiting hook are unaffected --------------------------------

@pytest.mark.parametrize("mode", CANDIDATE_MODES)
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_rm_with_its_hook_waiting_is_answered_through_the_hook(rig, mode, verb):
    _agent, rm = agent_gave_up_then_rm(rig, mode, rm_hook_gives_up=False)
    rig.tap(rm, verb)
    assert decided(rig) == [("req-rm", verb)]


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_rm_with_its_hook_waiting_types_nothing(rig, mode):
    _agent, rm = agent_gave_up_then_rm(rig, mode, rm_hook_gives_up=False)
    rig.tap(rm, "allow")
    assert rig.keys == []


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_parked_agent_beside_the_rm_is_answered_through_its_hook(rig, mode, verb):
    agent, _rm = parked_agent_then_rm(rig, mode)
    rig.tap(agent, verb)
    assert decided(rig) == [("req-bg", verb)]


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_hook_answer_closes_the_rm(rig, mode):
    _agent, rm = agent_gave_up_then_rm(rig, mode, rm_hook_gives_up=False)
    rig.tap(rm, "allow")
    assert len(rig.sess.open_prompts()) == 1


# -- criterion 12: non-candidates keep today ----------------------------------------

NON_CANDIDATE_MODES = ["default", "acceptEdits", "plan", None]
NC_IDS = ["default", "acceptEdits", "plan", "missing"]


@pytest.mark.parametrize("mode", NON_CANDIDATE_MODES, ids=NC_IDS)
def test_non_candidate_mode_agent_allow_still_types_enter(rig, mode):
    agent, _bash = agent_gave_up_then_rm(rig, mode)
    rig.tap(agent, "allow")
    assert rig.keys == ["Enter"]


@pytest.mark.parametrize("mode", NON_CANDIDATE_MODES, ids=NC_IDS)
def test_non_candidate_mode_later_bash_still_says_behind(rig, mode):
    _agent, bash = agent_gave_up_then_rm(rig, mode)
    q = rig.tap(bash, "allow")
    assert toast(q) == BEHIND


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_non_bash_tool_in_a_candidate_mode_keeps_today(rig, mode):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg", mode=mode)
    rig.timeout("req-bg")
    rig.hook("PermissionRequest", tool_name="Edit",
             tool_input={"file_path": "/home/alice/app/a.py", "old_string": "a",
                         "new_string": "b"},
             tool_use_id="toolu_e1", permission_mode=mode)
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_an_agents_bash_in_a_candidate_mode_keeps_today(rig, mode):
    """Claude's question first, then a background agent's Bash in bypass/auto
    whose hook gave up (it enters under the question): the question is on
    top and its answer types."""
    rig.launch(BG)
    rig.question()
    rig.agent_bash(BG, "rm -rf /home/alice/app", request_id="req-bg", mode=mode)
    rig.timeout("req-bg")
    rig.tap(CARD, "opt0")
    assert rig.keys != []


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_an_agents_bash_in_a_candidate_mode_still_says_behind(rig, mode):
    rig.launch(BG)
    rig.question()
    rig.agent_bash(BG, "rm -rf /home/alice/app", request_id="req-bg", mode=mode)
    rig.timeout("req-bg")
    q = rig.tap(extra_ids(rig)[0], "allow")
    assert toast(q) == BEHIND


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_a_lone_candidate_is_typed(rig, mode):
    rig.claude_bash("rm -rf /home/alice/app", hook=False, mode=mode)
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_a_lone_candidate_whose_hook_gave_up_is_typed(rig, mode):
    rig.claude_bash("rm -rf /home/alice/app", request_id="req-rm", mode=mode)
    rig.timeout("req-rm")
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]


# -- criterion 13: Notification fallback and restored prompts -------------------------------------

def notification_bash(r, **fields):
    r.hook("Notification", notification_type="permission_prompt",
           message="Claude needs your permission to use Bash", **fields)


def test_notification_fallback_without_mode_is_refused_beside_a_question(rig):
    notification_bash(rig)
    rig.question()
    assert verdicts(rig) == ["unknown", "unknown"]


def test_notification_fallback_without_mode_refuses_its_typed_tap(rig):
    notification_bash(rig)
    rig.question()
    q = rig.tap(CARD, "allow")
    assert (rig.keys, toast(q)) == ([], UNKNOWN)


@pytest.mark.parametrize("mode", CANDIDATE_MODES)
def test_notification_fallback_in_a_candidate_mode_is_refused(rig, mode):
    notification_bash(rig, permission_mode=mode)
    rig.question()
    assert verdicts(rig) == ["unknown", "unknown"]


def test_notification_fallback_in_default_mode_keeps_today(rig):
    """Control: the fallback with a reported non-candidate mode is not a
    candidate; the Bash came first, so it is on top."""
    notification_bash(rig, permission_mode="default")
    rig.question()
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]


def test_notification_fallback_alone_is_typed(rig):
    notification_bash(rig)
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]


def _drop_order(perm):
    """What a prompt restored after a daemon restart looks like: no order."""
    perm.get("tool_info", {}).pop("order", None)


def _restored_then_question(r, tool="Bash", mode="default"):
    if tool == "Bash":
        r.claude_bash("rm -rf /home/alice/app", hook=False, mode=mode)
    else:
        r.hook("PermissionRequest", tool_name=tool,
               tool_input={"file_path": "/home/alice/app/a.py", "old_string": "a",
                           "new_string": "b"},
               tool_use_id="toolu_e1", permission_mode=mode)
    _drop_order(r.sess.pending_permission)
    r.question()


def test_restored_bash_is_a_candidate(rig):
    """Its mode cannot be proven, even when it was asked in Ask mode."""
    _restored_then_question(rig, mode="default")
    assert verdicts(rig) == ["unknown", "unknown"]


def test_restored_bash_refuses_the_later_question(rig):
    _restored_then_question(rig, mode="default")
    q = rig.tap(extra_ids(rig)[0], "opt0")
    assert (rig.keys, toast(q)) == ([], UNKNOWN)


def test_restored_prompt_of_unknown_tool_is_a_candidate(rig):
    _restored_then_question(rig, mode="default")
    rig.sess.pending_permission["tool_info"].pop("name", None)
    assert verdicts(rig) == ["unknown", "unknown"]


def test_restored_non_bash_keeps_today(rig):
    """A restored Edit is not a candidate: it arrived first, so it is on
    top and the later question is behind it, as before 8.128."""
    _restored_then_question(rig, tool="Edit", mode="bypassPermissions")
    assert verdicts(rig) == ["yes", "behind"]


def test_lone_restored_bash_is_typed(rig):
    rig.claude_bash("rm -rf /home/alice/app", hook=False, mode="bypassPermissions")
    _drop_order(rig.sess.pending_permission)
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]
