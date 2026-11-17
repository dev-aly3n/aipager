"""Black-box: a background agent's prompt after Claude's own turn ended
(Bug A). Written from design.md's success criteria and entrypoints.md's
observable side effects only.

Setup for every case: Claude launches a background agent and its Stop
ends the turn while the agent still runs (status IDLE, the job open, the
waiting card kept). Then the agent asks. The prompt must be a NEW message
in the session's chat with Allow and Deny that answers the agent's dialog;
the waiting card never carries the buttons.
"""

from __future__ import annotations

import logging

import pytest

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, GROUP, OTHER, TRIGGER

REQ = "req-bg-1"
REQ2 = "req-other-1"
CMD = "touch marker-a"


def _bash(command):
    return {"command": command, "description": command}


def _after_stop(rig, *agents):
    for agent in agents or (BG,):
        rig.launch(agent)
    rig.hook("Stop", stop_hook_active=False,
             last_assistant_message="The agent runs in the background.")
    return len(rig.sent)


def _ask(rig, agent=BG, command=CMD, request_id=REQ, **kw):
    before = len(rig.sent)
    rig.agent_bash(agent, command, request_id=request_id, **kw)
    return [m for m in rig.sent[before:] if m.get("reply_markup") is not None]


def _the_prompt(rig, agent=BG, command=CMD, request_id=REQ, **kw):
    new = _ask(rig, agent, command, request_id, **kw)
    assert len(new) == 1, new
    return new[0]


def _card_edits_with_buttons(rig):
    return [e for e in rig.card_edits if e[0] == CARD and e[2] is not None]


def _notification(rig, agent=BG, message="Claude needs your permission"):
    rig.hook("Notification", notification_type="permission_prompt",
             message=message, agent_id=agent)


def _agent_pre(rig, agent, command, tool_use_id):
    rig.hook("PreToolUse", tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-reviewer")


def _agent_post(rig, agent, command, tool_use_id):
    rig.hook("PostToolUse", tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-reviewer",
             tool_response={"stdout": "ok"})


# -- the precondition the rest relies on -------------------------------------

def test_setup_stop_with_a_background_agent_leaves_idle(rig):
    _after_stop(rig)
    assert rig.sess.status == Status.IDLE


def test_setup_stop_with_a_background_agent_keeps_the_job_open(rig):
    _after_stop(rig)
    assert rig.sess.job_background_open()


# -- a single agent's Bash prompt (valid partition) --------------------------

def test_prompt_after_stop_sends_exactly_one_new_message(rig):
    _after_stop(rig)
    assert len(_ask(rig)) == 1


def test_prompt_after_stop_goes_to_the_sessions_chat(rig):
    _after_stop(rig)
    assert _the_prompt(rig)["chat_id"] == GROUP


def test_prompt_after_stop_has_allow_and_deny(rig):
    _after_stop(rig)
    names = rig.buttons(_the_prompt(rig))
    assert {"Allow", "Deny"} <= {n.strip() for n in names} or (
        any("Allow" in n for n in names) and any("Deny" in n for n in names)), names


def test_prompt_after_stop_names_the_command(rig):
    _after_stop(rig)
    assert CMD in _the_prompt(rig)["text"]


def test_prompt_after_stop_replies_to_the_jobs_trigger(rig):
    _after_stop(rig)
    assert _the_prompt(rig)["reply_to_message_id"] == TRIGGER


def test_prompt_after_stop_lands_below_the_interim_answer(rig):
    interim_count = _after_stop(rig)
    interim = rig.sent[interim_count - 1]
    assert _the_prompt(rig)["msg_id"] > interim["msg_id"]


def test_prompt_after_stop_does_not_put_buttons_on_the_card(rig):
    _after_stop(rig)
    _ask(rig)
    assert _card_edits_with_buttons(rig) == []


def test_prompt_after_stop_keeps_the_card_id(rig):
    _after_stop(rig)
    _ask(rig)
    assert rig.sess.busy_msg_id == CARD


def test_prompt_after_stop_is_not_the_cards_pending_permission(rig):
    _after_stop(rig)
    _ask(rig)
    assert rig.sess.pending_permission is None


def test_prompt_after_stop_records_its_message_as_the_pending_prompt(rig):
    _after_stop(rig)
    msg = _the_prompt(rig)
    assert rig.sess.pending_prompt_msg["msg_id"] == msg["msg_id"]


def test_prompt_after_stop_makes_the_session_interactive(rig):
    _after_stop(rig)
    _ask(rig)
    assert rig.sess.status == Status.INTERACTIVE


def test_prompt_after_stop_logs_the_own_message_line(rig, caplog):
    _after_stop(rig)
    with caplog.at_level(logging.INFO):
        _ask(rig)
    assert "agent's prompt after the turn ended - sent as its own message" in caplog.text


# -- the waiting card's edit fails (error guessing) --------------------------

@pytest.mark.parametrize("result", [False, None], ids=["false", "none"])
def test_new_message_even_when_the_card_edit_fails(rig, result):
    async def _fail(*a, **k):
        return result

    rig.bot._edit_busy_raw = _fail
    _after_stop(rig)
    assert len(_ask(rig)) == 1


# -- no card at all: deleted after the Stop, or never there (boundary) -------

def test_prompt_after_stop_with_the_card_deleted_is_a_new_message(rig):
    _after_stop(rig)
    rig.sess.busy_msg_id = None
    assert len(_ask(rig)) == 1


def test_prompt_after_stop_with_the_card_deleted_has_buttons(rig):
    _after_stop(rig)
    rig.sess.busy_msg_id = None
    names = " ".join(rig.buttons(_the_prompt(rig)))
    assert "Allow" in names and "Deny" in names


# -- no reply channel on the request (equivalence: hook=False) ---------------

def test_prompt_after_stop_without_a_reply_channel_is_a_new_message(rig):
    _after_stop(rig)
    assert len(_ask(rig, hook=False)) == 1


def test_tap_on_a_prompt_without_a_reply_channel_types_keys(rig):
    _after_stop(rig)
    msg = _the_prompt(rig, hook=False)
    rig.tap(msg["msg_id"], "allow")
    assert rig.keys != []


# -- the Notification after the hook's deadline -----------------------------

def test_notification_after_the_timeout_draws_no_second_prompt(rig):
    _after_stop(rig)
    _ask(rig)
    rig.timeout(REQ)
    before = len(rig.prompt_messages())
    _notification(rig)
    assert len(rig.prompt_messages()) == before


def test_notification_after_the_timeout_logs_already_open(rig, caplog):
    _after_stop(rig)
    _ask(rig)
    rig.timeout(REQ)
    with caplog.at_level(logging.INFO):
        _notification(rig)
    assert "prompt already open" in caplog.text


def test_notification_naming_the_tool_draws_no_second_prompt(rig):
    _after_stop(rig)
    _ask(rig)
    rig.timeout(REQ)
    before = len(rig.prompt_messages())
    _notification(rig, message="Claude needs your permission to use Bash")
    assert len(rig.prompt_messages()) == before


def test_notification_before_the_timeout_draws_no_second_prompt(rig):
    _after_stop(rig)
    _ask(rig)
    before = len(rig.prompt_messages())
    _notification(rig)
    assert len(rig.prompt_messages()) == before


def test_notification_leaves_the_card_without_buttons(rig):
    _after_stop(rig)
    _ask(rig)
    rig.timeout(REQ)
    _notification(rig)
    assert _card_edits_with_buttons(rig) == []


# -- taps: before / after the deadline, allow / deny -------------------------

@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_tap_before_the_deadline_answers_through_the_hook(rig, verb):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.tap(msg["msg_id"], verb)
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, verb)]


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_tap_before_the_deadline_types_no_keys(rig, verb):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.tap(msg["msg_id"], verb)
    assert rig.keys == []


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_tap_after_the_timeout_types_the_answer(rig, verb):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.timeout(REQ)
    rig.tap(msg["msg_id"], verb)
    assert rig.keys != []


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_tap_after_the_timeout_sends_no_hook_decision(rig, verb):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.timeout(REQ)
    rig.tap(msg["msg_id"], verb)
    assert rig.decisions == []


def test_allow_and_deny_type_different_keys_after_the_timeout(mk_bot, monkeypatch,
                                                               run_async):
    from tests.two_prompts_rig import Rig
    typed = {}
    for verb in ("allow", "deny"):
        rig = Rig(mk_bot, monkeypatch, run_async)
        monkeypatch.setattr("aipager.bot.rich_message._post", _ok_post)
        _after_stop(rig)
        msg = _the_prompt(rig)
        rig.timeout(REQ)
        rig.tap(msg["msg_id"], verb)
        typed[verb] = list(rig.keys)
    assert typed["allow"] != typed["deny"]


async def _ok_post(method, payload, **kw):
    return {"ok": True, "result": {"message_id": 9000} if method.startswith("send") else True}


def test_second_tap_on_the_same_message_sends_no_second_decision(rig):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.tap(msg["msg_id"], "allow")
    rig.tap(msg["msg_id"], "allow")
    assert len(rig.decisions) == 1


def test_second_tap_on_the_same_message_types_nothing(rig):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.tap(msg["msg_id"], "allow")
    rig.tap(msg["msg_id"], "deny")
    assert rig.keys == []


# -- answered in the terminal ------------------------------------------------

def _answered_in_terminal(rig):
    _after_stop(rig)
    _agent_pre(rig, BG, CMD, "toolu_bg1")
    msg = _the_prompt(rig)
    rig.timeout(REQ)
    _agent_post(rig, BG, CMD, "toolu_bg1")
    return msg


def test_answered_in_the_terminal_closes_the_prompt(rig):
    _answered_in_terminal(rig)
    assert rig.sess.open_prompts() == []


def test_tap_after_the_terminal_answer_types_nothing(rig):
    msg = _answered_in_terminal(rig)
    rig.tap(msg["msg_id"], "allow")
    assert rig.keys == []


def test_tap_after_the_terminal_answer_sends_no_decision(rig):
    msg = _answered_in_terminal(rig)
    rig.tap(msg["msg_id"], "allow")
    assert rig.decisions == []


def test_tap_after_the_terminal_answer_is_refused_with_a_toast(rig):
    msg = _answered_in_terminal(rig)
    q = rig.tap(msg["msg_id"], "allow")
    assert q.answer.await_args_list, "the tap got no answer at all"


# -- two agents asking after the Stop ---------------------------------------

def _two_asks(rig):
    _after_stop(rig, BG, OTHER)
    first = _the_prompt(rig, BG, "make a", REQ)
    second = _the_prompt(rig, OTHER, "make b", REQ2)
    return first, second


def test_two_agents_each_get_their_own_message(rig):
    first, second = _two_asks(rig)
    assert first["msg_id"] != second["msg_id"]


def test_two_agents_both_prompts_are_open(rig):
    _two_asks(rig)
    assert len(rig.sess.open_prompts()) == 2


def test_two_agents_second_message_names_its_command(rig):
    _first, second = _two_asks(rig)
    assert "make b" in second["text"]


def test_two_agents_no_card_edit_carries_buttons(rig):
    _two_asks(rig)
    assert _card_edits_with_buttons(rig) == []


@pytest.mark.parametrize("order", ["first-then-second", "second-then-first"])
def test_two_agents_each_tap_answers_its_own_request(rig, order):
    first, second = _two_asks(rig)
    taps = [first, second] if order == "first-then-second" else [second, first]
    for msg in taps:
        rig.tap(msg["msg_id"], "allow")
    expected = [REQ, REQ2] if order == "first-then-second" else [REQ2, REQ]
    assert [r["request_id"] for r, _d in rig.decisions] == expected


def test_two_agents_one_answered_in_terminal_keeps_the_other_open(rig):
    _after_stop(rig, BG, OTHER)
    _agent_pre(rig, BG, "make a", "toolu_a")
    _the_prompt(rig, BG, "make a", REQ)
    _the_prompt(rig, OTHER, "make b", REQ2)
    _agent_post(rig, BG, "make a", "toolu_a")
    assert [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()] == [OTHER]


def test_two_agents_one_answered_in_terminal_stays_interactive(rig):
    _after_stop(rig, BG, OTHER)
    _agent_pre(rig, BG, "make a", "toolu_a")
    _the_prompt(rig, BG, "make a", REQ)
    _the_prompt(rig, OTHER, "make b", REQ2)
    _agent_post(rig, BG, "make a", "toolu_a")
    assert rig.sess.status == Status.INTERACTIVE


def test_two_agents_one_in_terminal_then_tap_the_other_answers_it(rig):
    _after_stop(rig, BG, OTHER)
    _agent_pre(rig, BG, "make a", "toolu_a")
    _the_prompt(rig, BG, "make a", REQ)
    second = _the_prompt(rig, OTHER, "make b", REQ2)
    _agent_post(rig, BG, "make a", "toolu_a")
    rig.tap(second["msg_id"], "allow")
    assert [r["request_id"] for r, _d in rig.decisions] == [REQ2]


# -- the waiting card across the prompt and its close ------------------------

def test_the_waiting_card_keeps_its_id_after_the_close(rig):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.tap(msg["msg_id"], "allow")
    assert rig.sess.busy_msg_id == CARD


def test_the_job_stays_open_after_the_close(rig):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.tap(msg["msg_id"], "allow")
    assert rig.sess.job_background_open()


def test_a_second_prompt_after_the_first_closed_is_again_a_new_message(rig):
    _after_stop(rig)
    msg = _the_prompt(rig)
    rig.tap(msg["msg_id"], "allow")
    again = _ask(rig, BG, "touch marker-b", "req-bg-2")
    assert len(again) == 1 and again[0]["msg_id"] != msg["msg_id"]


# -- an agent's question after the Stop -------------------------------------

def test_an_agents_question_after_the_stop_is_a_new_message(rig):
    _after_stop(rig)
    before = len(rig.sent)
    rig.question(agent=BG)
    new = [m for m in rig.sent[before:] if m.get("reply_markup") is not None]
    assert len(new) == 1


def test_an_agents_question_after_the_stop_carries_its_options(rig):
    _after_stop(rig)
    before = len(rig.sent)
    rig.question(agent=BG)
    new = [m for m in rig.sent[before:] if m.get("reply_markup") is not None]
    names = " ".join(rig.buttons(new[0]))
    assert "Only the card" in names and "The card and the bar" in names


def test_an_agents_question_after_the_stop_leaves_the_card_without_buttons(rig):
    _after_stop(rig)
    rig.question(agent=BG)
    assert _card_edits_with_buttons(rig) == []


# -- controls: Claude's own turn still running -------------------------------

def test_control_agent_prompt_in_a_running_turn_sits_on_the_card(rig):
    rig.launch(BG)
    rig.agent_bash(BG, CMD, request_id=REQ)
    assert len(_card_edits_with_buttons(rig)) >= 1


def test_control_agent_prompt_in_a_running_turn_sends_no_new_message(rig):
    rig.launch(BG)
    assert _ask(rig) == []


def test_control_agent_prompt_in_a_running_turn_sets_pending_permission(rig):
    rig.launch(BG)
    _ask(rig)
    assert rig.sess.pending_permission is not None
