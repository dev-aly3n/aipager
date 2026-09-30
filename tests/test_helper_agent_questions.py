"""A question from a Claude Code helper agent is not shown as Claude asking
(live 2026-09-30, test2): after the turn ended, a forked helper's
AskUserQuestion ("What would you like to do next?") reached the hook
receiver with an ``agent_id``. aipager showed it as buttons; the tap typed
Enter into an idle prompt and nothing came back. Claude's own questions,
and a known agent's question, are unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status

NAME = "claude-helperq"
QUESTION = {"questions": [{
    "question": "What would you like to do next?",
    "options": [{"label": "Nothing more"}, {"label": "Expand bazaar text"}]}]}


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    return registry, hr.HookReceiver(registry, notify_fn), notify_fn


def _session(registry, status):
    sess = registry.get_or_create(NAME)
    sess.label = "helperq"
    registry.transition(NAME, status)
    return sess


def _ask(recv, run_async, **extra):
    payload = {"hook_event_name": "PreToolUse", "session": NAME,
               "tool_name": "AskUserQuestion", "tool_input": QUESTION,
               **extra}
    run_async(recv._on_datagram(json.dumps(payload).encode()))


def test_a_helper_agents_question_while_idle_is_not_shown(receiver, run_async,
                                                          caplog):
    registry, recv, notify_fn = receiver
    _session(registry, Status.IDLE)

    with caplog.at_level(logging.INFO, logger="aipager.dtach.hook_receiver"):
        _ask(recv, run_async, agent_id="acbc82d02083a19c6")

    assert registry.get(NAME).status == Status.IDLE
    notify_fn.assert_not_awaited()
    assert any("helper agent acbc82d02083a19c6" in r.getMessage()
               and "What would you like to do next?" in r.getMessage()
               for r in caplog.records)


def test_claudes_own_question_while_idle_is_shown(receiver, run_async):
    registry, recv, notify_fn = receiver
    _session(registry, Status.IDLE)

    _ask(recv, run_async)

    assert registry.get(NAME).status == Status.INTERACTIVE
    _, event, _ = notify_fn.await_args.args
    assert event == "permission_prompt"


def test_an_agents_question_during_a_turn_is_shown(receiver, run_async):
    """A foreground subagent asking mid-turn keeps today's behaviour."""
    registry, recv, notify_fn = receiver
    _session(registry, Status.BUSY)

    _ask(recv, run_async, agent_id="a1foreground")

    assert registry.get(NAME).status == Status.INTERACTIVE
    notify_fn.assert_awaited()


def test_a_known_background_agents_question_is_shown(receiver, run_async):
    """An agent the session knows (it started with a type) is not a helper,
    even while the turn has ended and its job waits on it."""
    registry, recv, notify_fn = receiver
    sess = _session(registry, Status.IDLE)
    sess.bg_agent_started("a1known", "pipeline-runner", time.monotonic())

    _ask(recv, run_async, agent_id="a1known")

    assert registry.get(NAME).status == Status.INTERACTIVE
    notify_fn.assert_awaited()


def test_a_shown_question_logs_who_asked(receiver, run_async, caplog):
    registry, recv, _notify_fn = receiver
    _session(registry, Status.BUSY)

    with caplog.at_level(logging.INFO, logger="aipager.dtach.hook_receiver"):
        _ask(recv, run_async, agent_id="a1foreground")

    assert any("AskUserQuestion (agent a1foreground)" in r.getMessage()
               for r in caplog.records)


def test_a_recently_stopped_agents_question_is_shown(receiver, run_async):
    """An agent the session knew that has just stopped (its SubagentStop
    came first) is not a helper either (review rev-iter1-003)."""
    registry, recv, notify_fn = receiver
    sess = _session(registry, Status.IDLE)
    sess.bg_agent_started("a1late", "pipeline-runner", time.monotonic())
    sess.bg_agent_stopped("a1late", time.monotonic())

    _ask(recv, run_async, agent_id="a1late")

    assert registry.get(NAME).status == Status.INTERACTIVE
    notify_fn.assert_awaited()


def test_a_question_whose_text_is_not_a_string_does_not_break(receiver,
                                                              run_async):
    registry, recv, notify_fn = receiver
    _session(registry, Status.IDLE)

    payload = {"hook_event_name": "PreToolUse", "session": NAME,
               "tool_name": "AskUserQuestion", "agent_id": "ahelper",
               "tool_input": {"questions": [{"question": 42}]}}
    raised = None
    try:
        run_async(recv._on_datagram(json.dumps(payload).encode()))
    except Exception as exc:  # the receiver must not raise on it
        raised = exc

    assert raised is None, raised
    assert registry.get(NAME).status == Status.IDLE
    notify_fn.assert_not_awaited()
