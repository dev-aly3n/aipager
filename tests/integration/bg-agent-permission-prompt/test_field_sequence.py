"""The field sequences (a session's log, Claude Code 2.1.325): a
background agent's Bash prompt after Claude's Stop, its hook's timeout,
the Notification that follows, the answer; then a Telegram message."""

from __future__ import annotations

import logging

from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, GROUP, OTHER, TRIGGER

CMD = "sed -i s/x/y/ heartbeat"
INTERIM = "The developer runs in the background."


def _bash(command):
    return {"command": command, "description": command}


def _stop(rig, *agents):
    for agent in agents:
        rig.launch(agent, agent_type="ship-developer")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message=INTERIM)
    assert rig.sess.status == Status.IDLE
    assert rig.sess.job_background_open()


def _pre(rig, agent, tool_use_id, command=CMD):
    rig.hook("PreToolUse", tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-developer")


def _post(rig, agent, tool_use_id, command=CMD):
    rig.hook("PostToolUse", tool_name="Bash", tool_input=_bash(command),
             tool_use_id=tool_use_id, agent_id=agent, agent_type="ship-developer",
             tool_response={"stdout": ""})


def _prompts(rig):
    return [m for m in rig.prompt_messages()
            if "Allow" in " ".join(rig.buttons(m))]


def _card_keyboards(rig):
    return [e for e in rig.card_edits if e[0] == CARD and e[2] is not None]


def test_field_sequence_prompt_reaches_the_chat_and_the_tap_reaches_the_pty(rig):
    _stop(rig, BG)
    _pre(rig, BG, "toolu_bg1")
    rig.agent_bash(BG, CMD, request_id="req-1")
    rig.timeout("req-1")
    rig.elapse()  # the field's Notification came about 6 s after the timeout
    rig.hook("Notification", notification_type="permission_prompt",
             message="Claude needs your permission", agent_id=BG,
             agent_type="ship-developer")
    [msg] = _prompts(rig)
    assert msg["chat_id"] == GROUP and msg["reply_to_message_id"] == TRIGGER
    assert CMD in msg["text"]
    assert _card_keyboards(rig) == []
    rig.tap(msg["msg_id"], "allow")
    assert rig.keys and rig.decisions == []
    _post(rig, BG, "toolu_bg1")
    sess = rig.sess
    assert sess.status == Status.IDLE
    assert sess.job_background_open()
    assert sess.busy_msg_id == CARD


def test_field_sequence_message_after_the_terminal_answer_gets_its_card(rig, caplog):
    caplog.set_level(logging.INFO)
    _stop(rig, BG)
    _pre(rig, BG, "toolu_bg1")
    rig.agent_bash(BG, CMD, request_id="req-1")
    rig.timeout("req-1")
    _post(rig, BG, "toolu_bg1")
    sess = rig.sess
    assert sess.status == Status.IDLE
    rig.hook("queue_pickup", consumed=[{"msg_id": 77, "chat_id": GROUP,
                                        "raw_text": "hello"}], expired=[])
    assert 77 not in [t.get("msg_id") for t in sess.queued_targets]
    assert sess.trigger_msg_id == 77
    n = len(rig.sent)
    rig.hook("UserPromptSubmit", prompt="[via Telegram @bob]\nhello")
    assert sess.status == Status.BUSY
    assert any(m.get("reply_to_message_id") == 77 for m in rig.sent[n:])
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Answer to hello.")
    logs = [r.getMessage() for r in caplog.records]
    assert not any("queued while busy" in m for m in logs)
    assert not any("taken inside the open job" in m for m in logs)
    [answer] = [m for m in rig.sent if "Answer to hello." in (m["text"] or "")]
    assert answer["reply_to_message_id"] == 77


def test_waiting_card_up_then_two_agents_ask(rig):
    _stop(rig, BG, OTHER)
    rig.agent_bash(BG, CMD, request_id="req-bg")
    rig.agent_bash(OTHER, "make", request_id="req-other")
    msgs = _prompts(rig)
    assert len(msgs) == 2
    assert _card_keyboards(rig) == []
    for m in msgs:
        rig.tap(m["msg_id"], "allow")
    assert sorted(r["request_id"] for r, _d in rig.decisions) == ["req-bg", "req-other"]
    assert rig.sess.open_prompts() == []
    assert rig.sess.status == Status.IDLE
    assert rig.sess.busy_msg_id == CARD
