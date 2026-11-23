"""A Telegram tap on a permission prompt after its hook gave up
(design.md "Success criteria", entrypoints.md "Telegram surface").

A background or unknown-launch agent's prompt: in the gap (4.0 s after
the hook's timeout, or 2.0 s after Claude Code's record said the
permission dialog is waiting) a tap types nothing, keeps the buttons and
answers ``toast.prompt_opening``. Claude's own and a foreground agent's
prompt type at once. A tap while the hook waits answers through it.
"""

from __future__ import annotations

import logging
import time

import pytest

from aipager import prompt_gap
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, FG, OTHER, answers

OPENING = "Claude Code is still opening this prompt in the terminal, so tap it again in a moment."
BEHIND = "Claude Code is showing another prompt first, so answer that one before this one."
ORDER_UNKNOWN = ("aipager can't tell which prompt Claude Code is showing first, "
                 "so answer this one in the terminal.")
EXPIRED = "this prompt has expired"
CMD = "sed -i s/x/y/ heartbeat"
INTERIM = "The developer runs in the background."
SUGGEST = [{"type": "addRules", "behavior": "allow", "destination": "localSettings",
            "rules": [{"toolName": "Bash", "ruleContent": "sed:*"}]}]
VERBS = ["allow", "deny", "allow_always"]
CONTEXTS = ["in-turn", "after-stop"]


# -- helpers ----------------------------------------------------------------------------

def ask(rig, agent, req="req-1", *, hook=True, cmd=CMD):
    fields = {"tool_name": "Bash", "tool_input": {"command": cmd, "description": cmd},
              "agent_id": agent, "agent_type": "ship-reviewer",
              "permission_suggestions": SUGGEST}
    if hook:
        fields.update(aipager_reply_addr=f"/nonexistent/reply-{agent}.sock",
                      aipager_request_id=req)
    rig.hook("PermissionRequest", **fields)


def prompt_msg_id(rig, context, cmd=CMD):
    if context == "in-turn":
        return CARD
    msgs = [m for m in rig.prompt_messages() if cmd in (m["text"] or "")]
    return msgs[-1]["msg_id"]


def open_agent_prompt(rig, context="in-turn", *, launch="background", agent=BG,
                      hook=True, req="req-1"):
    """Open a lone agent prompt; return its message id."""
    if launch == "background":
        rig.launch(agent)
    elif launch == "foreground":
        rig.launch(agent, background=False)
    if context == "after-stop":
        rig.hook("Stop", stop_hook_active=False, last_assistant_message=INTERIM)
        assert rig.sess.status == Status.IDLE
    ask(rig, agent, req, hook=hook)
    return prompt_msg_id(rig, context)


def the_order(rig, agent=BG):
    perm = next(p for p in rig.sess.open_prompts()
                if p["tool_info"].get("agent_id") == agent)
    return perm["tool_info"]["order"]


def timed_out_ago(rig, seconds, agent=BG):
    the_order(rig, agent)["timed_out_at"] = time.monotonic() - seconds


def gap_tap(rig, verb="allow", context="in-turn", *, launch="background", agent=BG):
    """The lone agent prompt, its hook's timeout, then a tap at once."""
    msg = open_agent_prompt(rig, context, launch=launch, agent=agent)
    rig.timeout("req-1")
    return rig.tap(msg, verb)


def give_record(monkeypatch, status_at, status="waiting", waiting_for="permission prompt"):
    async def _record(sess):
        return {"status": status, "waitingFor": waiting_for, "statusAt": status_at}

    monkeypatch.setattr(prompt_gap, "_record", _record)


# -- the gap: refused, nothing typed, buttons kept ----------------------------------------

@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("verb", VERBS)
def test_tap_in_gap_answers_opening_toast(rig, verb, context):
    q = gap_tap(rig, verb, context)
    assert answers(q) == [OPENING]


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("verb", VERBS)
def test_tap_in_gap_types_nothing(rig, verb, context):
    gap_tap(rig, verb, context)
    assert rig.keys == []


@pytest.mark.parametrize("verb", VERBS)
def test_tap_in_gap_sends_no_hook_decision(rig, verb):
    gap_tap(rig, verb)
    assert rig.decisions == []


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("verb", VERBS)
def test_tap_in_gap_keeps_the_prompt_open(rig, verb, context):
    gap_tap(rig, verb, context)
    assert len(rig.sess.open_prompts()) == 1


@pytest.mark.parametrize("verb", VERBS)
def test_tap_in_gap_keeps_the_session_waiting(rig, verb):
    gap_tap(rig, verb)
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("verb", VERBS)
def test_tap_in_gap_edits_no_buttons_away(rig, verb):
    q = gap_tap(rig, verb, "after-stop")
    assert (q.edit_message_reply_markup.await_count, q.edit_message_text.await_count) == (0, 0)


@pytest.mark.parametrize("verb", VERBS)
def test_tap_in_gap_posts_nothing_under_the_prompt(rig, verb):
    msg = open_agent_prompt(rig, "after-stop")
    rig.timeout("req-1")
    n = len(rig.sent)
    rig.tap(msg, verb)
    assert rig.sent[n:] == []


def test_tap_in_gap_is_not_order_unknown(rig):
    q = gap_tap(rig)
    assert ORDER_UNKNOWN not in answers(q)


def test_tap_in_gap_logs_the_refusal(rig, caplog):
    caplog.set_level(logging.INFO)
    gap_tap(rig)
    assert any("tap refused: its dialog is still opening in the terminal" in r.getMessage()
               for r in caplog.records)


def test_second_tap_in_gap_is_refused_too(rig):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    rig.tap(msg, "allow")
    q = rig.tap(msg, "allow")
    assert (answers(q), rig.keys) == ([OPENING], [])


@pytest.mark.parametrize("verb", VERBS)
def test_unknown_launch_agent_tap_in_gap_is_refused(rig, verb):
    """An agent aipager saw no launch for (a teammate, an unmatched call)."""
    msg = open_agent_prompt(rig, launch=None, agent=OTHER)
    assert the_order(rig, OTHER)["launch"] == "unknown"
    rig.timeout("req-1")
    q = rig.tap(msg, verb)
    assert (answers(q), rig.keys) == ([OPENING], [])


# -- boundary: timed_out_at + 4.0 ---------------------------------------------------------

@pytest.mark.parametrize("ago", [0.0, 2.0, 3.8], ids=["0s", "2s", "3_8s"])
def test_tap_inside_grace_types_nothing(rig, ago):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    timed_out_ago(rig, ago)
    rig.tap(msg, "allow")
    assert rig.keys == []


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("verb", VERBS)
def test_tap_after_grace_types_the_answer(rig, verb, context):
    msg = open_agent_prompt(rig, context)
    rig.timeout("req-1")
    timed_out_ago(rig, 4.01)
    rig.tap(msg, verb)
    assert rig.keys


@pytest.mark.parametrize("verb", VERBS)
def test_tap_after_grace_types_what_a_typed_answer_types(rig_factory, verb):
    """Oracle: the keys of the same tap on Claude's own prompt after its
    hook's timeout (never gated). Each rig records the keys typed after
    it was built, so the oracle rig comes first."""
    other = rig_factory()
    other.hook("PermissionRequest", tool_name="Bash",
               tool_input={"command": CMD, "description": CMD}, tool_use_id="toolu_p1",
               permission_suggestions=SUGGEST,
               aipager_reply_addr="/nonexistent/reply-claude.sock",
               aipager_request_id="req-p")
    other.timeout("req-p")
    other.tap(CARD, verb)
    expected = list(other.keys)
    rig = rig_factory()
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    timed_out_ago(rig, 4.01)
    rig.tap(msg, verb)
    assert rig.keys == expected


def test_tap_after_grace_closes_the_prompt(rig):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    timed_out_ago(rig, 4.01)
    rig.tap(msg, "allow")
    assert rig.sess.open_prompts() == []


def test_tap_after_grace_is_not_refused(rig):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    timed_out_ago(rig, 4.01)
    q = rig.tap(msg, "allow")
    assert OPENING not in answers(q)


def test_refused_then_after_grace_answers(rig):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    rig.tap(msg, "allow")
    rig.elapse(4.01)
    rig.tap(msg, "allow")
    assert rig.keys and rig.sess.open_prompts() == []


# -- the hook's socket gone before the timeout datagram ------------------------------------

@pytest.mark.parametrize("verb", VERBS)
def test_tap_with_hook_gone_before_datagram_is_refused(rig, verb):
    msg = open_agent_prompt(rig)
    rig.hook_ok = False
    q = rig.tap(msg, verb)
    assert (answers(q), rig.keys) == ([OPENING], [])


def test_hook_gone_just_inside_parked_window_is_refused(rig):
    msg = open_agent_prompt(rig)
    rig.hook_ok = False
    the_order(rig)["arrived"] = time.monotonic() - 29.5
    rig.tap(msg, "allow")
    assert rig.keys == []


def test_hook_gone_and_datagram_lost_types_after_parked_window(rig):
    msg = open_agent_prompt(rig)
    rig.hook_ok = False
    the_order(rig)["arrived"] = time.monotonic() - 30.05
    rig.tap(msg, "allow")
    assert rig.keys


def test_hook_gone_then_datagram_starts_grace_from_timeout(rig):
    msg = open_agent_prompt(rig)
    rig.hook_ok = False
    rig.tap(msg, "allow")
    rig.timeout("req-1")
    timed_out_ago(rig, 4.01)
    rig.tap(msg, "allow")
    assert rig.keys


# -- a prompt with no reply channel at all (gone = arrived) ----------------------------------

def test_no_hook_prompt_just_arrived_is_refused(rig):
    msg = open_agent_prompt(rig, hook=False)
    q = rig.tap(msg, "allow")
    assert (answers(q), rig.keys) == ([OPENING], [])


def test_no_hook_prompt_after_grace_types(rig):
    msg = open_agent_prompt(rig, hook=False)
    the_order(rig)["arrived"] = time.monotonic() - 4.01
    rig.tap(msg, "allow")
    assert rig.keys


# -- record evidence: statusAt + 2.0 -----------------------------------------------------------

def test_tap_after_record_waiting_and_settle_types(rig, monkeypatch):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    timed_out_ago(rig, 2.4)
    give_record(monkeypatch, time.time() - 2.1)
    rig.tap(msg, "allow")
    assert rig.keys


def test_tap_after_record_waiting_but_before_settle_is_refused(rig, monkeypatch):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    timed_out_ago(rig, 2.4)
    give_record(monkeypatch, time.time() - 1.5)
    q = rig.tap(msg, "allow")
    assert (answers(q), rig.keys) == ([OPENING], [])


@pytest.mark.parametrize("status,waiting_for,offset", [
    ("busy", "permission prompt", 2.1),
    ("waiting", "dialog open", 2.1),
    ("waiting", "permission prompt", 3.4),
], ids=["not-waiting", "other-dialog", "before-timeout-slack"])
def test_record_that_proves_nothing_keeps_refusing(rig, monkeypatch, status, waiting_for,
                                                   offset):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    timed_out_ago(rig, 2.4)
    give_record(monkeypatch, time.time() - offset, status, waiting_for)
    rig.tap(msg, "allow")
    assert rig.keys == []


def test_record_read_error_keeps_refusing(rig, monkeypatch):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    timed_out_ago(rig, 2.4)

    async def _boom(sess):
        raise OSError("unreadable")

    monkeypatch.setattr(prompt_gap, "_record", _boom)
    q = rig.tap(msg, "allow")
    assert (answers(q), rig.keys) == ([OPENING], [])


def test_prompt_closed_during_record_read_is_expired(rig, monkeypatch):
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")

    async def _closing(sess):
        # The agent's next call arrives while aipager reads the record:
        # its prompt was answered in the terminal.
        rig.recv._recent_fingerprints.clear()
        import json
        await rig.recv._on_datagram(json.dumps({
            "session": sess.name, "hook_event_name": "PreToolUse",
            "transcript_path": "", "_seq": 999, "tool_name": "Read",
            "tool_input": {"file_path": "/a"}, "tool_use_id": "toolu_r",
            "agent_id": BG, "agent_type": "ship-reviewer"}).encode())
        return None

    monkeypatch.setattr(prompt_gap, "_record", _closing)
    q = rig.tap(msg, "allow")
    assert (answers(q), rig.keys) == ([EXPIRED], [])


# -- unchanged: own, foreground, restored, hook decision ---------------------------------------

@pytest.mark.parametrize("verb", VERBS)
def test_claude_own_prompt_after_timeout_types_at_once(rig, verb):
    rig.hook("PermissionRequest", tool_name="Bash",
             tool_input={"command": CMD, "description": CMD}, tool_use_id="toolu_p1",
             permission_suggestions=SUGGEST,
             aipager_reply_addr="/nonexistent/reply-claude.sock",
             aipager_request_id="req-p")
    rig.timeout("req-p")
    rig.tap(CARD, verb)
    assert rig.keys


@pytest.mark.parametrize("verb", VERBS)
def test_foreground_agent_prompt_after_timeout_types_at_once(rig, verb):
    msg = open_agent_prompt(rig, launch="foreground", agent=FG)
    assert the_order(rig, FG)["launch"] == "foreground"
    rig.timeout("req-1")
    rig.tap(msg, verb)
    assert rig.keys


def test_claude_own_prompt_with_hook_gone_types_at_once(rig):
    rig.claude_bash(CMD, request_id="req-p")
    rig.hook_ok = False
    rig.tap(CARD, "allow")
    assert rig.keys


def test_prompt_without_order_types_at_once(rig):
    """A restored prompt carries no order: it counts as Claude's own."""
    msg = open_agent_prompt(rig)
    rig.timeout("req-1")
    [perm] = rig.sess.open_prompts()
    perm["tool_info"].pop("order", None)
    rig.tap(msg, "allow")
    assert rig.keys


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_tap_while_hook_waits_answers_through_the_hook(rig, verb, context):
    msg = open_agent_prompt(rig, context)
    rig.tap(msg, verb)
    assert [d["behavior"] for _r, d in rig.decisions] == [verb]


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_tap_while_hook_waits_types_nothing(rig, verb):
    msg = open_agent_prompt(rig)
    rig.tap(msg, verb)
    assert rig.keys == []


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_tap_while_hook_waits_is_not_refused(rig, verb):
    msg = open_agent_prompt(rig)
    q = rig.tap(msg, verb)
    assert OPENING not in answers(q)


# -- a background agent's question -------------------------------------------------------------

def test_background_agent_question_option_in_gap_is_refused(rig):
    rig.launch(BG)
    rig.question(agent=BG)
    q = rig.tap(CARD, "opt0")
    assert (answers(q), rig.keys) == ([OPENING], [])


def test_background_agent_question_option_after_grace_types(rig):
    rig.launch(BG)
    rig.question(agent=BG)
    rig.elapse(4.01)
    rig.tap(CARD, "opt0")
    assert rig.keys


def test_claude_own_question_option_types_at_once(rig):
    rig.question()
    rig.tap(CARD, "opt0")
    assert rig.keys


# -- several prompts: BEHIND and ORDER_UNKNOWN keep their meaning ---------------------------------

def _two_with_own_first(rig):
    rig.launch(BG)
    rig.claude_bash("make first", request_id="req-p")
    ask(rig, BG, "req-1")
    rig.timeout("req-1")
    [agent_msg] = [m for m in rig.prompt_messages() if CMD in (m["text"] or "")]
    return agent_msg["msg_id"]


def test_agent_behind_claudes_prompt_in_gap_says_behind(rig):
    msg = _two_with_own_first(rig)
    q = rig.tap(msg, "allow")
    assert answers(q) == [BEHIND]


def test_agent_behind_claudes_prompt_in_gap_types_nothing(rig):
    msg = _two_with_own_first(rig)
    rig.tap(msg, "allow")
    assert rig.keys == []


def test_claudes_question_beside_overlapping_agent_says_order_unknown(rig):
    rig.launch(BG)
    ask(rig, BG, hook=False)
    rig.question()
    [qmsg] = [m for m in rig.prompt_messages() if "Only the card" in " ".join(rig.buttons(m))]
    q = rig.tap(qmsg["msg_id"], "opt0")
    assert answers(q) == [ORDER_UNKNOWN]
