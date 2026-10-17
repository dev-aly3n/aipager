"""Every prompt that arrives while another is open is shown in Telegram
with its own buttons (design.md success criteria 1 and 9; entrypoints.md
Events): the first on the busy card, each later one as its own message
replying to the card. Duplicates, the ``permission_prompt`` fallback, the
question's own PermissionRequest and a helper agent's question are not
shown again."""

from __future__ import annotations

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, FG, OTHER, QUESTION

OPTIONS = [o["label"] for o in QUESTION["questions"][0]["options"]]


def card_buttons(rig) -> list[str]:
    """The buttons of the card's latest edit that carried a keyboard."""
    for mid, _text, markup in reversed(rig.card_edits):
        if mid == CARD and markup is not None:
            return [b.text for row in markup.inline_keyboard for b in row]
    return []


def extra_messages(rig) -> list[dict]:
    return [m for m in rig.prompt_messages() if m.get("reply_to_message_id") == CARD]


def has_allow_deny(buttons) -> bool:
    joined = " ".join(buttons)
    return "Allow" in joined and "Deny" in joined


# -- agent's permission, then Claude's question -------------------------------------

def _agent_first(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.question()


def test_agent_first_card_shows_agent_allow_deny(rig):
    _agent_first(rig)
    assert has_allow_deny(card_buttons(rig))


def test_agent_first_question_is_its_own_message(rig):
    _agent_first(rig)
    assert len(extra_messages(rig)) == 1


def test_agent_first_question_message_has_its_options(rig):
    _agent_first(rig)
    assert rig.buttons(extra_messages(rig)[0]) == OPTIONS


def test_agent_first_two_prompts_open(rig):
    _agent_first(rig)
    assert len(rig.sess.open_prompts()) == 2


def test_agent_first_open_prompts_in_arrival_order(rig):
    _agent_first(rig)
    owners = [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()]
    assert owners == [BG, None]


def test_agent_first_session_interactive(rig):
    _agent_first(rig)
    assert rig.sess.status == Status.INTERACTIVE


# -- Claude's question, then an agent's permission -------------------------------------

def _claude_first(rig):
    rig.launch(BG)
    rig.question()
    rig.agent_bash(BG, "make", request_id="req-bg")


def test_claude_first_card_shows_question_options(rig):
    _claude_first(rig)
    assert all(o in card_buttons(rig) for o in OPTIONS)


def test_claude_first_agent_prompt_is_its_own_message(rig):
    _claude_first(rig)
    assert len(extra_messages(rig)) == 1


def test_claude_first_agent_message_has_allow_deny(rig):
    _claude_first(rig)
    assert has_allow_deny(rig.buttons(extra_messages(rig)[0]))


def test_claude_first_open_prompts_in_arrival_order(rig):
    _claude_first(rig)
    owners = [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()]
    assert owners == [None, BG]


# -- two of Claude's own parallel permissions ---------------------------------------------

def _two_claude(rig):
    rig.claude_bash("rm -rf build/", tool_use_id="toolu_p1")
    rig.claude_bash("rm -rf dist/", tool_use_id="toolu_p2")


def test_two_claude_permissions_both_open(rig):
    _two_claude(rig)
    assert len(rig.sess.open_prompts()) == 2


def test_two_claude_permissions_second_has_its_own_buttons(rig):
    _two_claude(rig)
    assert has_allow_deny(rig.buttons(extra_messages(rig)[0]))


def test_second_claude_permission_message_names_its_command(rig):
    _two_claude(rig)
    assert "dist" in extra_messages(rig)[0]["text"]


# -- more than two (boundary: n = 3) ---------------------------------------------------------

def test_third_prompt_is_also_shown(rig):
    rig.launch(BG)
    rig.launch(FG, background=False, agent_type="ship-tester")
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(FG, "pytest", request_id="req-fg")
    rig.question()
    assert len(extra_messages(rig)) == 2


def test_three_prompts_open(rig):
    rig.launch(BG)
    rig.launch(FG, background=False, agent_type="ship-tester")
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(FG, "pytest", request_id="req-fg")
    rig.question()
    assert len(rig.sess.open_prompts()) == 3


# -- what is not a new prompt ----------------------------------------------------------------

def test_identical_permission_repeat_not_shown_twice(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(BG, "make", request_id="req-bg")
    assert len(rig.sess.open_prompts()) == 1


def test_identical_permission_repeat_sends_no_message(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(BG, "make", request_id="req-bg")
    assert extra_messages(rig) == []


def test_same_tool_different_input_is_a_second_prompt(rig):
    """Two parallel calls of one tool by one agent differ by input."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-1")
    rig.agent_bash(BG, "make test", request_id="req-2")
    assert len(rig.sess.open_prompts()) == 2


def test_same_input_different_agents_are_two_prompts(rig):
    rig.launch(BG)
    rig.launch(OTHER, agent_type="ship-tester")
    rig.agent_bash(BG, "make", request_id="req-1")
    rig.agent_bash(OTHER, "make", request_id="req-2")
    assert len(rig.sess.open_prompts()) == 2


def test_notification_fallback_not_a_second_prompt(rig):
    rig.claude_bash()
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert len(rig.sess.open_prompts()) == 1


def test_notification_fallback_sends_no_message_while_open(rig):
    rig.claude_bash()
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert extra_messages(rig) == []


def test_question_permission_request_not_shown_twice(rig):
    """aipager's PermissionRequest hook has matcher "*", so the question
    Claude already asked at PreToolUse also arrives as a PermissionRequest."""
    rig.question()
    rig.hook("PermissionRequest", tool_name="AskUserQuestion", tool_input=QUESTION,
             tool_use_id="toolu_q1", aipager_reply_addr="/nonexistent/reply-q.sock",
             aipager_request_id="req-q")
    assert len(rig.sess.open_prompts()) == 1


def test_question_permission_request_behind_agent_not_shown(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.question()
    rig.hook("PermissionRequest", tool_name="AskUserQuestion", tool_input=QUESTION,
             tool_use_id="toolu_q1", aipager_reply_addr="/nonexistent/reply-q.sock",
             aipager_request_id="req-q")
    assert len(extra_messages(rig)) == 1


def test_helper_question_while_prompt_open_not_shown(rig):
    """Claude Code 2.1.285+ forks a prompt-suggestion helper; its question
    (unknown agent, session not BUSY) is never the user's."""
    rig.claude_bash()
    rig.question(agent="f00dfeedf00dfeed1", tool_use_id="toolu_helper")
    assert len(rig.sess.open_prompts()) == 1


def test_helper_question_while_prompt_open_sends_nothing(rig):
    rig.claude_bash()
    rig.question(agent="f00dfeedf00dfeed1", tool_use_id="toolu_helper")
    assert extra_messages(rig) == []


def test_known_agent_question_while_prompt_open_is_shown(rig):
    """Partition: a question from an agent aipager knows is the user's."""
    rig.launch(BG)
    rig.claude_bash()
    rig.question(agent=BG, tool_use_id="toolu_agent_q")
    assert len(rig.sess.open_prompts()) == 2


def test_notification_fallback_with_nothing_open_is_shown(rig):
    """Control for the two tests above: the same datagram does raise a
    prompt when nothing is open, so their "not shown" is not vacuous."""
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert rig.sess.status == Status.INTERACTIVE
