"""Black-box: Claude's OWN prompt (no agent_id) that opens while aipager
shows the session IDLE, beside the agent's prompt from IDLE that design.md
targets. Written from design.md, entrypoints.md and the iteration-2 brief
(review-1 rev-iter1-002) only.

Promise under test:
- a main-loop question or permission raised while aipager shows IDLE (a
  Stop hook that blocks the Stop, a false idle) is still shown as its own
  visible message with its buttons;
- when it closes, Claude's turn is still running, so the session goes
  back to BUSY (not IDLE), and a Telegram message picked up after the
  close is queued with its queued line;
- an agent's prompt from IDLE still closes to IDLE;
- mixed: an agent's prompt from IDLE, then Claude's own prompt during that
  wait: once everything closes Claude's turn runs, so BUSY;
- Notification-only prompts (no PermissionRequest datagram).
"""

from __future__ import annotations

import logging

import pytest

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, GROUP

REQ_BG = "req-bg-1"
REQ_MAIN = "req-main-1"
CMD_BG = "touch marker-a"
CMD_MAIN = "rm -rf build/"
MAIN_TOOL = "toolu_p1"
Q_TOOL = "toolu_q1"
FIRST = 77


# -- helpers ----------------------------------------------------------------

def _bash(command):
    return {"command": command, "description": command}


def _stop(rig, ctx):
    """*ctx* "plain": Claude's Stop with no agent left (no job open);
    "job": a background agent still runs, so the job waits on it."""
    if ctx == "job":
        rig.launch(BG)
    rig.hook("Stop", stop_hook_active=False,
             last_assistant_message="Done for now.")


def _new_prompts(rig, before):
    return [m for m in rig.sent[before:] if m.get("reply_markup") is not None]


def _raise_main(rig, kind):
    """Claude's own prompt (no agent_id). Returns the new prompt messages."""
    before = len(rig.sent)
    if kind == "question":
        rig.question(tool_use_id=Q_TOOL)
    else:
        # No PreToolUse first: that is Claude's visible activity and would
        # move the session to BUSY before the prompt (a running turn). The
        # case here is the PermissionRequest landing while aipager shows
        # IDLE (its PreToolUse datagram lost, or a false idle).
        rig.claude_bash(CMD_MAIN, tool_use_id=MAIN_TOOL, request_id=REQ_MAIN)
    return _new_prompts(rig, before)


def _raise_bg(rig):
    before = len(rig.sent)
    rig.hook("PreToolUse", tool_name="Bash", tool_input=_bash(CMD_BG),
             tool_use_id="toolu_bg1", agent_id=BG, agent_type="ship-reviewer")
    rig.agent_bash(BG, CMD_BG, request_id=REQ_BG)
    return _new_prompts(rig, before)


def _only(msgs):
    assert len(msgs) == 1, msgs
    return msgs[0]["msg_id"]


def _main_post(rig, kind, event="PostToolUse"):
    if kind == "question":
        rig.hook(event, tool_name="AskUserQuestion", tool_input={},
                 tool_use_id=Q_TOOL,
                 **({"tool_response": {"answers": {}}} if event == "PostToolUse"
                    else {"error": "refused"}))
    else:
        extra = ({"tool_response": {"stdout": "ok"}} if event == "PostToolUse"
                 else {"error": "The user doesn't want to proceed with this tool use."})
        rig.hook(event, tool_name="Bash", tool_input=_bash(CMD_MAIN),
                 tool_use_id=MAIN_TOOL, **extra)


def _bg_post(rig):
    rig.hook("PostToolUse", tool_name="Bash", tool_input=_bash(CMD_BG),
             tool_use_id="toolu_bg1", agent_id=BG, agent_type="ship-reviewer",
             tool_response={"stdout": "ok"})


def _close_main(rig, kind, how, msg):
    if how == "tap":
        rig.tap(msg, "opt0" if kind == "question" else "allow")
    elif how == "tap-deny":
        rig.tap(msg, "deny")
    elif how == "keys-after-timeout":
        rig.timeout(REQ_MAIN)
        rig.tap(msg, "allow")
    elif how == "terminal":
        if kind == "permission":
            rig.timeout(REQ_MAIN)
        _main_post(rig, kind)
    elif how == "terminal-refused":
        rig.timeout(REQ_MAIN)
        _main_post(rig, kind, event="PostToolUseFailure")
    else:
        raise AssertionError(how)


# (kind, how) pairs that exist: a question has no Deny and no hook deadline.
CLOSES = [("question", "tap"), ("question", "terminal"),
          ("permission", "tap"), ("permission", "tap-deny"),
          ("permission", "keys-after-timeout"), ("permission", "terminal"),
          ("permission", "terminal-refused")]
CTXS = ["plain", "job"]


def _main_closed(rig, ctx, kind, how):
    _stop(rig, ctx)
    msg = _only(_raise_main(rig, kind))
    _close_main(rig, kind, how, msg)
    return msg


def _pickup(rig, msg_id, text="hello"):
    rig.hook("queue_pickup", consumed=[{"msg_id": msg_id, "chat_id": GROUP,
                                        "raw_text": text}], expired=[])


def _queued_ids(sess):
    return [t["msg_id"] if isinstance(t, dict) else getattr(t, "msg_id", t)
            for t in (sess.queued_targets or [])]


def _armed(sess, msg_id):
    for t in sess.queued_targets or []:
        tid = t["msg_id"] if isinstance(t, dict) else getattr(t, "msg_id", None)
        if tid == msg_id:
            return t["line_armed"] if isinstance(t, dict) else t.line_armed
    return None


def _cancel_timers(sess):
    timers = getattr(sess, "queued_line_timers", None) or {}
    for t in (timers.values() if isinstance(timers, dict) else timers):
        try:
            t.cancel()
        except Exception:
            pass


def _card_edits_with_buttons(rig):
    return [e for e in rig.card_edits if e[0] == CARD and e[2] is not None]


# -- precondition -----------------------------------------------------------

@pytest.mark.parametrize("ctx", CTXS)
def test_precondition_the_stop_leaves_the_session_idle(rig, ctx):
    _stop(rig, ctx)
    assert rig.sess.status == Status.IDLE


# -- Claude's own prompt from IDLE is still its own visible message ----------

@pytest.mark.parametrize("ctx", CTXS)
@pytest.mark.parametrize("kind", ["question", "permission"])
def test_main_prompt_from_idle_is_one_new_message(rig, ctx, kind):
    _stop(rig, ctx)
    assert len(_raise_main(rig, kind)) == 1


@pytest.mark.parametrize("ctx", CTXS)
@pytest.mark.parametrize("kind", ["question", "permission"])
def test_main_prompt_from_idle_goes_to_the_sessions_chat(rig, ctx, kind):
    _stop(rig, ctx)
    msgs = _raise_main(rig, kind)
    assert [m["chat_id"] for m in msgs] == [GROUP]


@pytest.mark.parametrize("ctx", CTXS)
def test_main_question_from_idle_carries_its_options(rig, ctx):
    _stop(rig, ctx)
    msgs = _raise_main(rig, "question")
    assert rig.buttons(msgs[0])[:2] == ["Only the card", "The card and the bar"]


@pytest.mark.parametrize("ctx", CTXS)
def test_main_permission_from_idle_has_allow_and_deny(rig, ctx):
    _stop(rig, ctx)
    names = rig.buttons(_raise_main(rig, "permission")[0])
    assert any("Allow" in n for n in names) and any("Deny" in n for n in names), names


def test_main_permission_from_idle_names_the_command(rig):
    _stop(rig, "job")
    assert CMD_MAIN in _raise_main(rig, "permission")[0]["text"]


@pytest.mark.parametrize("kind", ["question", "permission"])
def test_main_prompt_from_idle_puts_no_buttons_on_the_waiting_card(rig, kind):
    _stop(rig, "job")
    _raise_main(rig, kind)
    assert _card_edits_with_buttons(rig) == []


@pytest.mark.parametrize("ctx", CTXS)
@pytest.mark.parametrize("kind", ["question", "permission"])
def test_main_prompt_from_idle_makes_the_session_interactive(rig, ctx, kind):
    _stop(rig, ctx)
    _raise_main(rig, kind)
    assert rig.sess.status == Status.INTERACTIVE


def test_main_permission_from_idle_tap_answers_through_the_hook(rig):
    _stop(rig, "plain")
    msg = _only(_raise_main(rig, "permission"))
    rig.tap(msg, "allow")
    assert len(rig.decisions) == 1


def test_main_permission_from_idle_notification_draws_no_second_prompt(rig):
    """The Notification after the hook gave up is the same dialog."""
    _stop(rig, "plain")
    _raise_main(rig, "permission")
    rig.timeout(REQ_MAIN)
    before = len(rig.sent)
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert _new_prompts(rig, before) == []


# -- ... and when it closes Claude's turn still runs: BUSY -------------------

@pytest.mark.parametrize("ctx", CTXS)
@pytest.mark.parametrize("kind,how", CLOSES)
def test_main_prompt_from_idle_closes_to_busy(rig, ctx, kind, how):
    _main_closed(rig, ctx, kind, how)
    assert rig.sess.status == Status.BUSY


@pytest.mark.parametrize("ctx", CTXS)
@pytest.mark.parametrize("kind,how", CLOSES)
def test_main_prompt_from_idle_close_leaves_no_prompt_open(rig, ctx, kind, how):
    _main_closed(rig, ctx, kind, how)
    assert rig.sess.open_prompts() == []


@pytest.mark.parametrize("kind,how", [("question", "tap"), ("permission", "terminal")])
def test_main_prompt_from_idle_close_logs_interactive_to_busy(rig, kind, how, caplog):
    _stop(rig, "plain")
    msg = _only(_raise_main(rig, kind))
    with caplog.at_level(logging.INFO):
        _close_main(rig, kind, how, msg)
    assert "INTERACTIVE → BUSY" in caplog.text


@pytest.mark.parametrize("kind,how", [("question", "tap"), ("permission", "terminal")])
def test_main_prompt_from_idle_close_logs_no_move_to_idle(rig, kind, how, caplog):
    _stop(rig, "plain")
    rig.sess.last_idle_at = 0.0  # minutes passed since the Stop: no debounce
    msg = _only(_raise_main(rig, kind))
    with caplog.at_level(logging.INFO):
        _close_main(rig, kind, how, msg)
    assert "→ IDLE" not in caplog.text


@pytest.mark.parametrize("kind,how", [("question", "tap"), ("permission", "tap"),
                                      ("permission", "terminal")])
def test_main_prompt_from_idle_with_a_job_keeps_the_job_open(rig, kind, how):
    _main_closed(rig, "job", kind, how)
    assert rig.sess.job_background_open()


# -- a Telegram message after the close is queued, with its line -------------

@pytest.mark.parametrize("ctx", CTXS)
@pytest.mark.parametrize("kind,how", [("question", "tap"), ("question", "terminal"),
                                      ("permission", "tap"), ("permission", "terminal")])
def test_message_after_the_main_close_is_queued(rig, ctx, kind, how):
    _main_closed(rig, ctx, kind, how)
    _pickup(rig, FIRST)
    try:
        assert FIRST in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)


@pytest.mark.parametrize("kind,how", [("question", "tap"), ("permission", "terminal")])
def test_message_after_the_main_close_has_its_line_armed(rig, kind, how):
    _main_closed(rig, "plain", kind, how)
    _pickup(rig, FIRST)
    try:
        assert _armed(rig.sess, FIRST)
    finally:
        _cancel_timers(rig.sess)


@pytest.mark.parametrize("kind,how", [("question", "tap"), ("permission", "terminal")])
def test_message_after_the_main_close_logs_queued_while_busy(rig, kind, how, caplog):
    _main_closed(rig, "plain", kind, how)
    try:
        with caplog.at_level(logging.INFO):
            _pickup(rig, FIRST)
        assert f"queued while busy: [{FIRST}]" in caplog.text
    finally:
        _cancel_timers(rig.sess)


@pytest.mark.parametrize("kind,how", [("question", "tap"), ("permission", "terminal")])
def test_message_after_the_main_close_gets_no_card_of_its_own(rig, kind, how):
    """Queued behind Claude's running turn: no new card replies to it at
    pickup (that is what the queued line is for)."""
    _main_closed(rig, "plain", kind, how)
    before = len(rig.sent)
    _pickup(rig, FIRST)
    try:
        cards = [m for m in rig.sent[before:]
                 if m.get("reply_to_message_id") == FIRST and not m.get("rich")
                 and m.get("reply_markup") is not None
                 and "queued" not in str(m.get("text", "")).lower()]
        assert cards == []
    finally:
        _cancel_timers(rig.sess)


# -- control: an agent's question from IDLE still closes to IDLE --------------

def _agent_question(rig):
    rig.launch(BG)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Done for now.")
    before = len(rig.sent)
    rig.question(agent=BG, tool_use_id="toolu_bgq")
    return _only(_new_prompts(rig, before))


def test_control_agents_question_from_idle_tap_closes_to_idle(rig):
    msg = _agent_question(rig)
    rig.tap(msg, "opt0")
    assert rig.sess.status == Status.IDLE


def test_control_agents_question_from_idle_terminal_closes_to_idle(rig):
    _agent_question(rig)
    rig.hook("PostToolUse", tool_name="AskUserQuestion", tool_input={},
             tool_use_id="toolu_bgq", agent_id=BG, agent_type="ship-reviewer",
             tool_response={"answers": {}})
    assert rig.sess.status == Status.IDLE


def test_control_agents_permission_from_idle_message_after_close_not_queued(rig):
    _stop(rig, "job")
    msg = _only(_raise_bg(rig))
    rig.tap(msg, "allow")
    _pickup(rig, FIRST)
    try:
        assert FIRST not in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)


# -- mixed: the agent asks from IDLE, then Claude's own prompt arrives -------

def _mixed(rig, kind):
    _stop(rig, "job")
    bg_msg = _only(_raise_bg(rig))
    main_msgs = _raise_main(rig, kind)
    return bg_msg, main_msgs


@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_claudes_prompt_is_its_own_new_message(rig, kind):
    bg_msg, main_msgs = _mixed(rig, kind)
    assert len(main_msgs) == 1 and main_msgs[0]["msg_id"] != bg_msg


@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_both_prompts_are_open(rig, kind):
    _mixed(rig, kind)
    assert len(rig.sess.open_prompts()) == 2


@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_no_card_edit_carries_buttons(rig, kind):
    _mixed(rig, kind)
    assert _card_edits_with_buttons(rig) == []


@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_claudes_closed_first_stays_interactive(rig, kind):
    _bg_msg, main_msgs = _mixed(rig, kind)
    _close_main(rig, kind, "tap", main_msgs[0]["msg_id"])
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_agents_closed_first_stays_interactive(rig, kind):
    bg_msg, _main_msgs = _mixed(rig, kind)
    rig.tap(bg_msg, "allow")
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("bg_close", ["tap", "terminal"])
@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_claudes_first_then_agents_ends_busy(rig, kind, bg_close):
    bg_msg, main_msgs = _mixed(rig, kind)
    _close_main(rig, kind, "tap", main_msgs[0]["msg_id"])
    if bg_close == "tap":
        rig.tap(bg_msg, "allow")
    else:
        rig.timeout(REQ_BG)
        _bg_post(rig)
    assert rig.sess.status == Status.BUSY


@pytest.mark.parametrize("main_close", ["tap", "terminal"])
@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_agents_first_then_claudes_ends_busy(rig, kind, main_close):
    bg_msg, main_msgs = _mixed(rig, kind)
    rig.tap(bg_msg, "allow")
    _close_main(rig, kind, main_close, main_msgs[0]["msg_id"])
    assert rig.sess.status == Status.BUSY


@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_message_after_both_closed_is_queued(rig, kind):
    bg_msg, main_msgs = _mixed(rig, kind)
    rig.tap(bg_msg, "allow")
    _close_main(rig, kind, "tap", main_msgs[0]["msg_id"])
    _pickup(rig, FIRST)
    try:
        assert FIRST in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)


@pytest.mark.parametrize("kind", ["question", "permission"])
def test_mixed_each_tap_answers_its_own_prompt(rig, kind):
    """The agent's tap sends its own request's decision, never Claude's."""
    bg_msg, _main_msgs = _mixed(rig, kind)
    rig.tap(bg_msg, "allow")
    ids = [r.get("aipager_request_id") or r.get("request_id") for r, _d in rig.decisions]
    assert ids == [REQ_BG], rig.decisions


def test_mixed_reverse_claudes_question_then_agent_asks_ends_busy(rig):
    """Claude's own question from IDLE first, then the agent asks; both
    answered: Claude's turn runs, so BUSY."""
    _stop(rig, "job")
    q_msg = _only(_raise_main(rig, "question"))
    bg_msgs = _raise_bg(rig)
    rig.tap(q_msg, "opt0")
    rig.tap(bg_msgs[0]["msg_id"], "allow")
    assert rig.sess.status == Status.BUSY


def test_mixed_reverse_agents_prompt_is_its_own_new_message(rig):
    _stop(rig, "job")
    q_msg = _only(_raise_main(rig, "question"))
    bg_msgs = _raise_bg(rig)
    assert len(bg_msgs) == 1 and bg_msgs[0]["msg_id"] != q_msg


# -- Notification-only prompts (no PermissionRequest datagram) ---------------

def _notification(rig, agent=None, message="Claude needs your permission"):
    before = len(rig.sent)
    fields = {"notification_type": "permission_prompt", "message": message}
    if agent:
        fields["agent_id"] = agent
    rig.hook("Notification", **fields)
    return _new_prompts(rig, before)


def test_agent_notification_only_after_stop_is_one_new_message(rig):
    _stop(rig, "job")
    assert len(_notification(rig, BG)) == 1


def test_agent_notification_only_after_stop_puts_no_buttons_on_the_card(rig):
    _stop(rig, "job")
    _notification(rig, BG)
    assert _card_edits_with_buttons(rig) == []


def test_agent_notification_only_after_stop_makes_it_interactive(rig):
    _stop(rig, "job")
    _notification(rig, BG)
    assert rig.sess.status == Status.INTERACTIVE


def test_agent_notification_only_tap_types_keys(rig):
    """No hook reply channel: the answer can only be typed."""
    _stop(rig, "job")
    msg = _only(_notification(rig, BG))
    rig.tap(msg, "allow")
    assert rig.keys != []


def test_agent_notification_only_tap_closes_to_idle(rig):
    _stop(rig, "job")
    msg = _only(_notification(rig, BG))
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.IDLE


@pytest.mark.xfail(strict=True, reason=(
    "pre-existing on main 591966a4 and outside design.md: a prompt known "
    "only from the agent's Notification is not closed by the agent's "
    "PostToolUse (it stays INTERACTIVE until the agent's next call); "
    "see test-report-2.json tester-iter2-002"))
def test_agent_notification_only_terminal_answer_closes_to_idle(rig):
    _stop(rig, "job")
    rig.hook("PreToolUse", tool_name="Bash", tool_input=_bash(CMD_BG),
             tool_use_id="toolu_bg1", agent_id=BG, agent_type="ship-reviewer")
    _notification(rig, BG)
    _bg_post(rig)
    assert rig.sess.status == Status.IDLE


def test_agent_notification_only_closed_by_the_agents_next_call_is_idle(rig):
    _stop(rig, "job")
    _notification(rig, BG)
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
             tool_use_id="toolu_bg2", agent_id=BG, agent_type="ship-reviewer")
    assert rig.sess.status == Status.IDLE


def test_agent_notification_only_message_after_close_is_not_queued(rig):
    _stop(rig, "job")
    msg = _only(_notification(rig, BG))
    rig.tap(msg, "allow")
    _pickup(rig, FIRST)
    try:
        assert FIRST not in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)


@pytest.mark.parametrize("ctx", CTXS)
def test_unnamed_notification_only_after_stop_is_one_new_message(rig, ctx):
    _stop(rig, ctx)
    assert len(_notification(rig, None, "Claude needs your permission to use Bash")) == 1


@pytest.mark.parametrize("ctx", CTXS)
def test_unnamed_notification_only_after_stop_makes_it_interactive(rig, ctx):
    _stop(rig, ctx)
    _notification(rig, None, "Claude needs your permission to use Bash")
    assert rig.sess.status == Status.INTERACTIVE


def test_unnamed_notification_only_puts_no_buttons_on_the_waiting_card(rig):
    _stop(rig, "job")
    _notification(rig, None, "Claude needs your permission to use Bash")
    assert _card_edits_with_buttons(rig) == []


def test_unnamed_notification_only_with_no_agents_closes_to_busy(rig):
    """No agent exists, so only Claude's own turn can own the dialog."""
    _stop(rig, "plain")
    msg = _only(_notification(rig, None, "Claude needs your permission to use Bash"))
    rig.tap(msg, "allow")
    assert rig.sess.status == Status.BUSY


def test_unnamed_notification_only_then_claudes_tool_call_is_busy(rig):
    """Claude's own visible activity after the dialog: its turn runs."""
    _stop(rig, "plain")
    _notification(rig, None, "Claude needs your permission to use Bash")
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
             tool_use_id="toolu_main2")
    rig.hook("PostToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
             tool_use_id="toolu_main2", tool_response={"ok": True})
    assert rig.sess.status != Status.IDLE


def test_unnamed_notification_only_with_no_agents_message_after_close_is_queued(rig):
    """Claude's turn runs on after the close, so a message picked up then
    waits behind it with its queued line."""
    _stop(rig, "plain")
    msg = _only(_notification(rig, None, "Claude needs your permission to use Bash"))
    rig.tap(msg, "allow")
    _pickup(rig, FIRST)
    try:
        assert FIRST in _queued_ids(rig.sess)
    finally:
        _cancel_timers(rig.sess)
