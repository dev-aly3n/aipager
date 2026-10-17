"""After Claude's Stop carried an agent's prompt over (fixes-2,
rev-iter1-005): Claude's turn is over, so the last answer leaves the
session IDLE, not BUSY, and the messages held behind the prompt are then
released. If Claude is working again (a continuation) when the last
prompt closes, the session is BUSY as usual. While a prompt is still
open, held messages stay held.

Held messages enter through the real message handler; their release is
observed at the PTY boundary (``inject.send_text_and_enter``), after the
session monitor's next tick.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.session_monitor import SessionMonitor
from aipager.state import Status
from tests.two_prompts_rig import BG, BOB, CARD, GROUP, OTHER, S

REQ = "req-bg"


def extra_ids(rig):
    return [m["msg_id"] for m in rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


def stop(rig):
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Waiting on the agent.")


def carried(rig, *, agents=(BG,)):
    """Claude-first: Claude's question on the card, each agent's prompt
    its own message; the question answered, then Claude's Stop. Returns
    the agents' message ids in order."""
    for i, agent in enumerate(agents):
        rig.launch(agent, agent_type=f"ship-{i}")
    rig.question()
    for agent in agents:
        rig.agent_bash(agent, "make", request_id=f"req-{agent}")
    msgs = extra_ids(rig)
    rig.tap(CARD, "opt0")
    stop(rig)
    return msgs


@pytest.fixture
def injected(monkeypatch):
    bodies: list[str] = []
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(side_effect=lambda n, body: bodies.append(body) or True))
    return bodies


@pytest.fixture
def say(rig, mk_update, run_async):
    def go(text, msg_id=77):
        update = mk_update(text, message_id=msg_id, user_id=BOB, chat_id=GROUP)
        ctx = MagicMock()
        ctx.bot = MagicMock(id=999)
        run_async(rig.bot._handle_message(update, ctx))
    return go


@pytest.fixture
def tick(rig, monkeypatch, run_async):
    async def _alive():
        return [S]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _alive)
    monitor = SessionMonitor(rig.bot.registry, rig.bot.notify)
    return lambda: run_async(monitor._scan())


def sent_on(bodies, text):
    return [b for b in bodies if b.endswith(text)]


# -- the last answer after a carried Stop ------------------------------------------------------

def test_carried_wait_is_interactive(rig):
    """Precondition."""
    carried(rig)
    assert rig.sess.status == Status.INTERACTIVE


def test_last_answer_by_tap_leaves_the_session_idle(rig):
    (agent_msg,) = carried(rig)
    rig.tap(agent_msg, "allow")
    assert rig.sess.status == Status.IDLE


def test_last_answer_by_deny_tap_leaves_the_session_idle(rig):
    (agent_msg,) = carried(rig)
    rig.tap(agent_msg, "deny")
    assert rig.sess.status == Status.IDLE


def test_last_prompt_closed_by_the_agents_tool_call_leaves_idle(rig):
    """Answered at the terminal: the agent's next tool call proves it."""
    carried(rig)
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/src/a.py"},
             tool_use_id="toolu_bg_read", agent_id=BG, agent_type="ship-0")
    assert rig.sess.status == Status.IDLE


def test_last_prompt_closed_by_the_agents_stop_leaves_idle(rig):
    carried(rig)
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-0")
    assert rig.sess.status == Status.IDLE


def test_last_answer_leaves_nothing_open(rig):
    (agent_msg,) = carried(rig)
    rig.tap(agent_msg, "allow")
    assert rig.sess.dialog_is_open() is False


def test_first_of_two_carried_answers_keeps_waiting(rig):
    """Boundary: n = 2 carried prompts; the first answer is not the last."""
    first, _second = carried(rig, agents=(BG, OTHER))
    rig.tap(first, "allow")
    assert rig.sess.status == Status.INTERACTIVE


def test_second_of_two_carried_answers_leaves_idle(rig):
    first, second = carried(rig, agents=(BG, OTHER))
    rig.tap(first, "allow")
    rig.tap(second, "allow")
    assert rig.sess.status == Status.IDLE


def test_continuation_then_last_answer_is_busy(rig):
    """Partition: Claude woke again (task-notification), so the last
    answer finds Claude working."""
    (agent_msg,) = carried(rig)
    rig.hook("UserPromptSubmit",
             prompt=(f"<task-notification>\n<task-id>{OTHER}</task-id>\n"
                     "Background agent finished."))
    rig.tap(agent_msg, "allow")
    assert rig.sess.status == Status.BUSY


def test_claude_tool_call_then_last_answer_is_busy(rig):
    (agent_msg,) = carried(rig)
    rig.hook("UserPromptSubmit",
             prompt=(f"<task-notification>\n<task-id>{OTHER}</task-id>\n"
                     "Background agent finished."))
    rig.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/src/a.py"},
             tool_use_id="toolu_c_read2")
    rig.tap(agent_msg, "allow")
    assert rig.sess.status == Status.BUSY


# -- held messages --------------------------------------------------------------------------------

def test_message_while_carried_prompt_open_is_not_typed(rig, injected, say, tick):
    carried(rig)
    say("please also update the docs")
    tick()
    assert injected == []


def test_message_held_after_one_of_two_answers(rig, injected, say, tick):
    first, _second = carried(rig, agents=(BG, OTHER))
    say("please also update the docs")
    rig.tap(first, "allow")
    tick()
    assert injected == []


def test_held_message_released_after_the_last_answer(rig, injected, say, tick):
    (agent_msg,) = carried(rig)
    say("please also update the docs")
    rig.tap(agent_msg, "allow")
    tick()
    assert len(sent_on(injected, "please also update the docs")) == 1


def test_held_message_released_after_the_agent_closed_it(rig, injected, say, tick):
    carried(rig)
    say("please also update the docs")
    rig.hook("SubagentStop", agent_id=BG, agent_type="ship-0")
    tick()
    assert len(sent_on(injected, "please also update the docs")) == 1


def test_two_held_messages_released_first_first(rig, injected, say, tick):
    """The first held message goes out first (the second may follow it,
    or wait behind the turn the first one starts)."""
    (agent_msg,) = carried(rig)
    say("first note", msg_id=77)
    say("second note", msg_id=78)
    rig.tap(agent_msg, "allow")
    tick()
    text = "\n".join(injected)
    assert "first note" in text and (
        "second note" not in text or text.index("first note") < text.index("second note"))
