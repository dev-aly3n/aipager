"""A background agent's prompt that opens after Claude's own turn ended
(the session IDLE, the job's waiting card kept above the interim answer).

Bug A: on main it went silently onto that old card, out of view, and the
agent waited for hours. It comes as a new message with Allow and Deny.
Bug B: once it closed, the session went BUSY although Claude's turn was
over, so the next Telegram message was taken for a queued one (no card,
no queued line, a phantom turn at the next Stop). It goes back to IDLE.
"""

from __future__ import annotations

import json
import logging
import time

import pytest

from aipager import i18n
from aipager.bot.callbacks import PROMPT_OPENING
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, GROUP, OTHER, TRIGGER, Rig, answers

CMD = "sed -i s/x/y/ heartbeat"
INTERIM = "The developer runs in the background."


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    rig = Rig(mk_bot, monkeypatch, run_async)

    async def _post(method, payload, **kw):
        """The rich-message family, recorded beside the plain sends (so no
        call reaches the real-call guard)."""
        if not method.startswith("send"):
            return {"ok": True, "result": True}
        msg_id = next(rig.ids)
        rig.sent.append({"chat_id": payload.get("chat_id"),
                         "text": json.dumps(payload, ensure_ascii=False),
                         "msg_id": msg_id, "rich": method,
                         "reply_to_message_id": payload.get("reply_to_message_id")})
        return {"ok": True, "result": {"message_id": msg_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return rig


def _bash(command):
    return {"command": command, "description": command}


def _after_stop(rig, *agents):
    for agent in agents or (BG,):
        rig.launch(agent)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message=INTERIM)
    sess = rig.sess
    assert sess.status == Status.IDLE
    assert sess.job_background_open()
    assert sess.busy_msg_id == CARD


def _interim_msg(rig):
    [msg] = [m for m in rig.sent if INTERIM in (m["text"] or "")]
    return msg


def _allow_deny(rig, msg):
    return [b.split()[-1] for b in rig.buttons(msg)]


def _card_buttons(rig):
    """Edits of the card that gave it a keyboard."""
    return [e for e in rig.card_edits if e[0] == CARD and e[2] is not None]


def _prompt_msgs(rig):
    return [m for m in rig.prompt_messages() if "Allow" in " ".join(rig.buttons(m))]


def _agent_pre(rig, agent, command, tool_use_id):
    rig.hook("PreToolUse", tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-developer")


def _agent_post(rig, agent, command, tool_use_id):
    rig.hook("PostToolUse", tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-developer",
             tool_response={"stdout": "ok"})


def _notification(rig, agent=BG):
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission", agent_id=agent,
             agent_type="ship-developer")


def _field_close(rig):
    """The field's order: the agent's call, its ask, the hook's timeout,
    the answer in the terminal (the tool ran)."""
    _agent_pre(rig, BG, CMD, "toolu_bg1")
    rig.agent_bash(BG, CMD, request_id="req-1")
    rig.timeout("req-1")
    _agent_post(rig, BG, CMD, "toolu_bg1")


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name.startswith("aipager")]


# -- Bug A: the prompt is a new message ------------------------------------

def test_agent_prompt_after_the_interim_stop_is_a_new_message_with_buttons(rig, caplog):
    caplog.set_level(logging.INFO)
    _after_stop(rig)
    interim = _interim_msg(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    sess = rig.sess
    [msg] = _prompt_msgs(rig)
    assert msg["chat_id"] == GROUP
    assert _allow_deny(rig, msg) == ["Allow", "Deny"]
    assert CMD in msg["text"]
    assert msg["msg_id"] > interim["msg_id"]
    assert msg["reply_to_message_id"] == TRIGGER
    assert _card_buttons(rig) == []
    assert sess.pending_permission is None
    assert sess.pending_prompt_msg["msg_id"] == msg["msg_id"]
    assert sess.status == Status.INTERACTIVE and sess.wait_from_idle
    assert any("agent's prompt after the turn ended - sent as its own message" in m
               for m in _messages(caplog))


@pytest.mark.parametrize("edit_result", [False, None], ids=["refused", "gone"])
def test_new_message_even_when_the_card_edit_fails(rig, edit_result):
    _after_stop(rig)

    async def _edit(mid, text, **kwargs):
        rig.card_edits.append((mid, text, kwargs.get("reply_markup")))
        return edit_result

    rig.bot._edit_busy_raw.side_effect = _edit
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    assert CMD in msg["text"]
    assert rig.sess.pending_prompt_msg["msg_id"] == msg["msg_id"]


def test_notification_after_the_timeout_draws_no_second_prompt(rig, caplog):
    caplog.set_level(logging.INFO)
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    rig.timeout("req-1")
    _notification(rig)
    assert len(_prompt_msgs(rig)) == 1
    assert _card_buttons(rig) == []
    assert any("prompt already open" in m for m in _messages(caplog))


def test_tap_before_the_deadline_answers_through_the_hook(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.tap(msg["msg_id"], "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [
        ("req-1", "allow")]
    assert rig.keys == []


def test_tap_right_after_the_timeout_types_nothing_yet(rig):
    """The agent's dialog is drawn only once the hook is gone, and a fresh
    dialog ignores a key for a moment: the tap is refused, buttons kept."""
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.timeout("req-1")
    q = rig.tap(msg["msg_id"], "allow")
    assert answers(q) == [i18n.get("en").text(PROMPT_OPENING)]
    assert rig.keys == []
    assert rig.decisions == []
    assert len(rig.sess.open_prompts()) == 1


def test_tap_after_the_timeout_types_the_answer(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.timeout("req-1")
    [perm] = rig.sess.open_prompts()
    perm["tool_info"]["order"]["timed_out_at"] = time.monotonic() - 4.01
    rig.tap(msg["msg_id"], "allow")
    assert rig.keys
    assert rig.decisions == []


def test_answered_in_the_terminal_settles_it(rig):
    _after_stop(rig)
    _field_close(rig)
    sess = rig.sess
    assert sess.open_prompts() == []
    [msg] = _prompt_msgs(rig)
    q = rig.tap(msg["msg_id"], "allow")
    assert rig.keys == [] and rig.decisions == []
    assert answers(q)


def test_two_agents_asking_after_the_stop_each_get_their_own_message(rig):
    _after_stop(rig, BG, OTHER)
    rig.agent_bash(BG, CMD, request_id="req-bg")
    rig.agent_bash(OTHER, "make", request_id="req-other")
    msgs = _prompt_msgs(rig)
    assert len(msgs) == 2
    assert len({m["msg_id"] for m in msgs}) == 2
    assert _card_buttons(rig) == []
    by_cmd = {("make" if "make" in m["text"] else CMD): m["msg_id"] for m in msgs}
    rig.tap(by_cmd["make"], "allow")
    rig.tap(by_cmd[CMD], "deny")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [
        ("req-other", "allow"), ("req-bg", "deny")]
    assert rig.sess.status == Status.IDLE


def test_the_waiting_card_stays_the_jobs_card(rig):
    _after_stop(rig)
    _field_close(rig)
    sess = rig.sess
    assert sess.busy_msg_id == CARD
    assert _card_buttons(rig) == []
    assert sess.job_background_open()


def test_an_agents_question_after_the_stop_is_its_own_message(rig):
    _after_stop(rig)
    n = len(rig.sent)
    rig.question(agent=BG)
    new = [m for m in rig.sent[n:] if m.get("reply_markup") is not None]
    assert len(new) == 1
    labels = " ".join(rig.buttons(new[0]))
    assert "Only the card" in labels
    assert _card_buttons(rig) == []


def test_control_agent_prompt_in_a_running_turn_sits_on_the_card(rig):
    rig.launch(BG)
    rig.agent_bash(BG, CMD, request_id="req-1")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE and not sess.wait_from_idle
    assert _card_buttons(rig)
    assert sess.pending_permission is not None
    assert _prompt_msgs(rig) == []


# -- Bug B: back to IDLE, not BUSY -----------------------------------------

def test_prompt_from_idle_closed_by_its_tool_leaves_it_idle(rig, caplog):
    caplog.set_level(logging.INFO)
    _after_stop(rig)
    # Minutes passed since the Stop (hours in the field): the IDLE is not
    # the debounced re-IDLE, so its transition is logged.
    rig.sess.last_idle_at = 0.0
    mark = len(caplog.records)
    _field_close(rig)
    sess = rig.sess
    assert sess.status == Status.IDLE
    assert sess.job_background_open()
    later = [r.getMessage() for r in caplog.records[mark:]]
    assert any("INTERACTIVE → IDLE" in m for m in later)
    assert not any("→ BUSY" in m for m in later)


def test_prompt_from_idle_closed_by_the_agents_next_call_leaves_it_idle(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/a"},
             tool_use_id="toolu_r", agent_id=BG, agent_type="ship-developer")
    assert rig.sess.open_prompts() == []
    assert rig.sess.status == Status.IDLE


def test_last_tap_on_a_prompt_from_idle_leaves_it_idle(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.tap(msg["msg_id"], "allow")
    assert rig.sess.open_prompts() == []
    assert rig.sess.status == Status.IDLE


def _message_after_the_close(rig, caplog, msg_id=77, *, check=True):
    """The field's Bug B: a Telegram message picked up after the close,
    then its UserPromptSubmit. Returns the sends made during the
    UserPromptSubmit. *check*: assert it was taken as a new turn's."""
    rig.hook("queue_pickup", consumed=[{"msg_id": msg_id, "chat_id": GROUP,
                                        "raw_text": "hello"}], expired=[])
    sess = rig.sess
    if check:
        assert msg_id not in [t.get("msg_id") for t in sess.queued_targets]
        assert not any("queued while busy" in m for m in _messages(caplog))
        assert sess.trigger_msg_id == msg_id
    n = len(rig.sent)
    rig.hook("UserPromptSubmit", prompt="[via Telegram @bob]\nhello")
    assert sess.status == Status.BUSY
    return rig.sent[n:]


def test_message_after_the_close_gets_its_card_at_once(rig, caplog):
    caplog.set_level(logging.INFO)
    _after_stop(rig)
    _field_close(rig)
    during = _message_after_the_close(rig, caplog)
    assert any(m.get("reply_to_message_id") == 77 for m in during)
    n = len(rig.sent)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Answer to hello.")
    at_stop = rig.sent[n:]
    assert not any("taken inside the open job" in m for m in _messages(caplog))
    answer = [m for m in at_stop if "Answer to hello." in (m["text"] or "")]
    assert answer and answer[0].get("reply_to_message_id") == 77
    assert not any(m.get("reply_to_message_id") == 77
                   and "Answer to hello." not in (m["text"] or "")
                   and m.get("reply_markup") is not None
                   and "Stop" in " ".join(rig.buttons(m))
                   for m in at_stop)


def _injected(rig, monkeypatch):
    injected = []

    async def _inject(sess, text, *a, **k):
        injected.append(text)
        return True

    monkeypatch.setattr(rig.bot, "_inject_prompt", _inject)
    return injected


@pytest.mark.parametrize("close", ["tap", "terminal"])
def test_held_message_goes_out_when_a_prompt_from_idle_closes(
        rig, run_async, monkeypatch, close):
    _after_stop(rig)
    _agent_pre(rig, BG, CMD, "toolu_bg1")
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    injected = _injected(rig, monkeypatch)
    sess = rig.sess
    sess.queue_prompt("also check the docs", 41)
    hold = {"entered_at": sess.interactive_entered_at,
            "told": set(), "notices": []}
    sess.dialog_hold = hold
    if close == "tap":
        rig.tap(msg["msg_id"], "allow")
        assert sess.status == Status.IDLE
    else:
        rig.timeout("req-1")
        _agent_post(rig, BG, CMD, "toolu_bg1")
    assert hold["closed"] == "answered"
    run_async(rig.bot.notify(sess, "dialog_hold_settle", {}))
    assert injected == ["also check the docs"]
    if close == "terminal":
        # Claude sat at its input: the held message went at once, as a new
        # turn with its card under it.
        assert sess.status == Status.BUSY and sess.trigger_msg_id == 41


def test_terminal_prompt_during_a_prompt_from_idle_starts_a_turn(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    sess = rig.sess
    seq = sess.turn_seq
    seen = {}
    real = rig.recv.notify_fn

    async def _notify(s, event, context):
        if event == "user_prompt_submit":
            seen["status"] = s.status
            seen["open"] = len(s.open_prompts())
        await real(s, event, context)

    rig.recv.notify_fn = _notify
    rig.hook("UserPromptSubmit", prompt="and update the changelog")
    assert seen == {"status": Status.BUSY, "open": 1}
    assert sess.turn_seq == seq + 1
    assert sess.status == Status.INTERACTIVE
    assert [p["tool_info"].get("agent_id") for p in sess.open_prompts()] == [BG]
    assert not sess.wait_from_idle and not sess.resumed_after_stop
    # A turn of Claude's runs now: the last answer makes the session work.
    rig.tap(msg["msg_id"], "allow")
    assert sess.status == Status.BUSY


def test_control_claudes_tool_call_during_the_wait_makes_the_close_busy(rig):
    _after_stop(rig)
    _agent_pre(rig, BG, CMD, "toolu_bg1")
    rig.agent_bash(BG, CMD, request_id="req-1")
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"},
             tool_use_id="toolu_r")
    assert not rig.sess.wait_from_idle
    _agent_post(rig, BG, CMD, "toolu_bg1")
    assert rig.sess.open_prompts() == []
    assert rig.sess.status == Status.BUSY


def test_control_message_behind_claudes_own_turn_is_still_queued(rig, caplog):
    caplog.set_level(logging.INFO)
    _after_stop(rig)
    _field_close(rig)
    _message_after_the_close(rig, caplog, check=False)
    sess = rig.sess
    try:
        rig.hook("queue_pickup", consumed=[{"msg_id": 78, "chat_id": GROUP,
                                            "raw_text": "and this"}], expired=[])
        targets = {t.get("msg_id"): t for t in sess.queued_targets}
        assert 78 in targets
        assert targets[78].get("line_armed")
        assert any("queued while busy: [78]" in m for m in _messages(caplog))
    finally:
        for timer in list(getattr(sess, "queued_line_timers", {}).values()):
            timer.cancel()


# -- Claude's own prompt while aipager shows IDLE (rev-iter1-002) ------------
# A Stop hook carrying the turn on, or a false idle: Claude's question comes
# at PreToolUse, before the parent PreToolUse that would make it BUSY. Its
# turn runs, so the close goes BUSY; the prompt is still its own message
# when the job's card is kept above the answer.

def _new_prompt_msg(rig, n):
    [msg] = [m for m in rig.sent[n:] if m.get("reply_markup") is not None]
    return msg


def test_claudes_own_question_while_idle_is_its_own_message_and_closes_busy(rig, caplog):
    caplog.set_level(logging.INFO)
    _after_stop(rig)
    n = len(rig.sent)
    rig.question()
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert not sess.wait_from_idle
    msg = _new_prompt_msg(rig, n)
    assert "Only the card" in " ".join(rig.buttons(msg))
    assert _card_buttons(rig) == []
    assert any("Claude's prompt while idle - sent as its own message" in m
               for m in _messages(caplog))
    rig.tap(msg["msg_id"], "opt0")
    assert sess.open_prompts() == []
    assert sess.status == Status.BUSY


def test_claudes_own_permission_request_while_idle_closes_busy(rig):
    _after_stop(rig)
    n = len(rig.sent)
    rig.claude_bash("make build")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert not sess.wait_from_idle
    msg = _new_prompt_msg(rig, n)
    assert _card_buttons(rig) == []
    rig.tap(msg["msg_id"], "allow")
    assert sess.open_prompts() == []
    assert sess.status == Status.BUSY


def test_claudes_own_question_during_an_agents_wait_from_idle_closes_busy(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    sess = rig.sess
    assert sess.wait_from_idle
    [agent_msg] = _prompt_msgs(rig)
    n = len(rig.sent)
    rig.question()
    assert not sess.wait_from_idle
    question_msg = _new_prompt_msg(rig, n)
    rig.tap(agent_msg["msg_id"], "allow")
    assert sess.status == Status.INTERACTIVE
    rig.tap(question_msg["msg_id"], "opt0")
    assert sess.open_prompts() == []
    assert sess.status == Status.BUSY


def test_notification_naming_no_agent_still_counts_as_a_wait_after_the_turn(rig, tmp_path):
    """Claude Code's Notification never names the agent (2.1.326), and the
    tool read from the main transcript is Claude's last one: that proves
    nothing about whose dialog it is."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "id": "toolu_x",
         "input": {"command": "ls", "description": "ls"}}]}}) + "\n")
    _after_stop(rig)
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission",
             transcript_path=str(transcript))
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    [perm] = sess.open_prompts()
    assert isinstance(perm["tool_info"], dict)
    assert not perm["tool_info"].get("agent_id")
    assert sess.wait_from_idle


# -- A prompt naming no agent while none runs (tester-iter2-001) -------------
# The Notification never names the agent, so an unnamed prompt after the
# Stop may be an agent's only while one runs. With none running, only
# Claude's own turn can have opened it: its close goes BUSY, as on main.

def _plain_stop(rig):
    rig.hook("Stop", stop_hook_active=False, last_assistant_message=INTERIM)
    sess = rig.sess
    assert sess.status == Status.IDLE
    assert not (sess.active_subagents or sess.bg_agents)


def test_unnamed_notification_with_no_agent_running_is_claudes_own(rig, caplog):
    caplog.set_level(logging.INFO)
    _plain_stop(rig)
    n = len(rig.sent)
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    [perm] = sess.open_prompts()
    assert isinstance(perm["tool_info"], dict)
    assert not perm["tool_info"].get("agent_id")
    assert not sess.wait_from_idle
    assert not sess.wait_after_turn()
    # Still shown, with its buttons, as its own message.
    msg = _new_prompt_msg(rig, n)
    assert "Allow" in " ".join(rig.buttons(msg))
    assert _card_buttons(rig) == []
    rig.tap(msg["msg_id"], "allow")
    assert sess.open_prompts() == []
    assert sess.status == Status.BUSY


def test_prompt_with_no_tool_known_and_no_agent_running_is_claudes_own(rig):
    _plain_stop(rig)
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert not sess.wait_from_idle
    assert not sess.unnamed_prompt_after_turn()


def test_prompt_with_no_tool_known_while_an_agent_runs_stays_after_the_turn(rig):
    _after_stop(rig)
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission")
    sess = rig.sess
    assert sess.status == Status.INTERACTIVE
    assert sess.wait_from_idle
    assert sess.unnamed_prompt_after_turn()


def test_unnamed_prompt_while_an_agent_outside_the_job_runs_stays_after_the_turn(rig):
    """An agent of an earlier turn still runs (the session's running set,
    not the job's table): the unnamed prompt may be its."""
    _plain_stop(rig)
    sess = rig.sess
    assert sess.bg_agent_started("old-agent", "Explore", time.monotonic())
    assert not sess.active_subagents
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert sess.status == Status.INTERACTIVE
    assert sess.wait_from_idle


def test_unnamed_prompt_while_an_agent_only_in_the_job_table_runs_stays_after_the_turn(rig):
    """An agent the job's table knows but the session's running set does
    not (a start that named no type): the unnamed prompt may be its."""
    _plain_stop(rig)
    sess = rig.sess
    sess.add_subagent("job-only", {"type": "general-purpose",
                                   "started_at": time.monotonic()})
    assert sess.active_subagents
    assert not sess.bg_agents
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission to use Bash")
    assert sess.status == Status.INTERACTIVE
    assert sess.wait_from_idle
