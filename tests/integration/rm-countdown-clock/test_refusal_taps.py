"""Taps on Claude Code's risky-removal prompt around its refusal (roadmap
8.128; design.md criteria 17, 18 and 19). After Claude Code refused it by
itself, a tap types nothing and says the question is no longer open. In
the refusal window before aipager knows, Allow and Deny type nothing,
send no hook answer and record nothing (Claude Code ignores a late hook
answer). A role's auto-deny waiting on it types no Escape then."""

from __future__ import annotations

import pytest

from aipager import rm_countdown
from aipager.bot.callbacks import PROMPT_REFUSED_CLOSED, RM_REFUSAL_DUE
from aipager.bot.dashboard import ALREADY_ANSWERED, current_prompt_token
from tests.rm_clock_rig import REQ, rm_prompt, scan
from tests.two_prompts_rig import BOB, CARD, GROUP, QUESTION, Rig, answers

IN_WINDOW = 120.0 - rm_countdown.WINDOW_LEAD_SECONDS + 1.0
BEFORE_WINDOW = 120.0 - rm_countdown.WINDOW_LEAD_SECONDS - 1.0
REFUSED = str(PROMPT_REFUSED_CLOSED)
DUE = str(RM_REFUSAL_DUE)
VERBS = ["allow", "allow_always", "deny"]


def refuse(r, monitor, tx, clock):
    tx.refused()
    clock.at(IN_WINDOW)
    scan(r, monitor)
    assert r.sess.open_prompts() == []


# -- criterion 17: a tap after the refused close ----------------------------------------

@pytest.mark.parametrize("verb", VERBS)
def test_a_tap_on_the_refused_card_types_nothing(rig, monitor, tx, clock, verb):
    rm_prompt(rig, tx)
    refuse(rig, monitor, tx, clock)
    q = rig.tap(CARD, verb)
    assert (answers(q), rig.keys, rig.decisions) == ([REFUSED], [], [])


@pytest.mark.parametrize("verb", VERBS)
def test_a_tap_on_the_refused_message_types_nothing(cardless, monitor_cl, tx, clock, verb):
    rm_prompt(cardless, tx)
    [msg] = cardless.prompt_messages()
    refuse(cardless, monitor_cl, tx, clock)
    q = cardless.tap(msg["msg_id"], verb)
    assert (answers(q), cardless.keys, cardless.decisions) == ([REFUSED], [], [])


def test_a_tap_on_a_bar_copy_of_the_refused_message(cardless, monitor_cl, tx, clock):
    rm_prompt(cardless, tx)
    token = cardless.sess.pending_prompt_msg["prompt_token"]
    cardless.bot.register_prompt_surface(GROUP, 777, cardless.sess, token)
    refuse(cardless, monitor_cl, tx, clock)
    q = cardless.tap(777, "allow")
    assert (answers(q), cardless.keys) == ([REFUSED], [])


def test_a_tap_on_a_bar_copy_of_the_refused_card_prompt(rig, monitor, tx, clock):
    rm_prompt(rig, tx)
    token = current_prompt_token(rig.sess, create=True)
    rig.bot.register_prompt_surface(GROUP, 778, rig.sess, token)
    refuse(rig, monitor, tx, clock)
    q = rig.tap(778, "deny")
    assert (answers(q), rig.keys) == ([REFUSED], [])


def test_a_tap_on_a_prompt_answered_in_the_chat_still_says_answered(rig, tx, clock):
    rm_prompt(rig, tx)
    rig.tap(CARD, "allow")
    q = rig.tap(CARD, "allow")
    assert answers(q) == [str(ALREADY_ANSWERED)]


def test_a_tap_after_the_next_tool_call_closed_it_as_refused(rig, tx, clock):
    rm_prompt(rig, tx)
    clock.at(IN_WINDOW)
    rig.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"},
             tool_use_id="toolu_next")
    q = rig.tap(CARD, "allow")
    assert (answers(q), rig.keys) == ([REFUSED], [])


def test_a_new_prompt_on_the_card_answers_again(rig, monitor, tx, clock):
    """The card shows another prompt later: its buttons answer it."""
    rm_prompt(rig, tx)
    refuse(rig, monitor, tx, clock)
    rig.claude_bash("make", tool_use_id="toolu_make", mode="default", hook=False)
    rig.tap(CARD, "allow")
    assert rig.keys == ["Enter"]


# -- criterion 18: a tap in the refusal window, before the close -----------------------------

@pytest.mark.parametrize("verb", VERBS)
def test_a_typed_tap_in_the_window_types_nothing(rig, tx, clock, audits, verb):
    rm_prompt(rig, tx)
    rig.timeout(REQ)
    clock.at(IN_WINDOW)
    rows = list(rig.sess.tool_history)
    q = rig.tap(CARD, verb)
    assert (answers(q), rig.keys, rig.decisions, audits) == ([DUE], [], [], [])
    assert rig.sess.tool_history == rows
    assert len(rig.sess.open_prompts()) == 1


@pytest.mark.parametrize("verb", VERBS)
def test_a_hook_answer_in_the_window_is_not_sent(rig, tx, clock, audits, verb):
    rm_prompt(rig, tx)
    clock.at(IN_WINDOW)
    q = rig.tap(CARD, verb)
    assert (answers(q), rig.keys, rig.decisions, audits) == ([DUE], [], [], [])


def test_a_tap_in_the_window_keeps_the_buttons(cardless, tx, clock):
    rm_prompt(cardless, tx)
    [msg] = cardless.prompt_messages()
    clock.at(IN_WINDOW)
    q = cardless.tap(msg["msg_id"], "allow")
    assert answers(q) == [DUE]
    q.edit_message_reply_markup.assert_not_awaited()
    q.edit_message_text.assert_not_awaited()


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_a_typed_tap_before_the_window_works(rig, tx, clock, verb):
    rm_prompt(rig, tx)
    rig.timeout(REQ)
    clock.at(BEFORE_WINDOW)
    rig.tap(CARD, verb)
    assert rig.keys == (["Enter"] if verb == "allow" else ["Escape"])


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_a_hook_answer_before_the_window_works(rig, tx, clock, verb):
    rm_prompt(rig, tx)
    clock.at(BEFORE_WINDOW)
    rig.tap(CARD, verb)
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, verb)]


def test_a_prompt_with_no_clock_is_answered_any_time(rig, tx, clock):
    rig.claude_bash("rm -rf /home/alice/app", mode="default", suggestions=[],
                    request_id=REQ, transcript_path=str(tx))
    clock.at(IN_WINDOW + 3600)
    rig.tap(CARD, "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, "allow")]


# -- criterion 19: a role's auto-deny on it ---------------------------------------------------

@pytest.fixture
def drig(mk_bot, monkeypatch, run_async, clock, audits):
    """A user's turn: the rig group's rules refuse its Bash, typed as Escape
    when no hook waits (``tests/two_prompts_rig.scopes``)."""
    r = Rig(mk_bot, monkeypatch, run_async)
    r.sess.last_driver_user_id = BOB
    return r


def question_then_refused_rm(r, tx):
    """Claude's question is open; its risky rm then opens, refused by the
    user's rules with no hook: the Escape waits until the rm is alone."""
    r.question()
    rm_prompt(r, tx, hook=False)
    assert r.keys == []


def close_the_question(r):
    r.hook("PostToolUse", tool_name="AskUserQuestion", tool_input=QUESTION,
           tool_use_id="toolu_q1", tool_response={"answers": {}})


def test_an_auto_deny_in_the_window_types_no_escape(drig, tx, clock):
    question_then_refused_rm(drig, tx)
    clock.at(IN_WINDOW)
    close_the_question(drig)
    assert drig.keys == []


def test_an_auto_deny_before_the_window_types_its_escape(drig, tx, clock):
    question_then_refused_rm(drig, tx)
    clock.at(BEFORE_WINDOW)
    close_the_question(drig)
    assert drig.keys == ["Escape"]
