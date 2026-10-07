"""The id of the tool call a prompt asks about (roadmap 8.102).

A restart's transcript check (``saved_prompt_evidence``) drops a saved
permission prompt whose tool call already has a result. It needs the
call's ``tool_use_id``: the PermissionRequest payload's own, else the
parent's latest PreToolUse's for the same tool, else none. An
AskUserQuestion takes its own PreToolUse's id.
"""

from __future__ import annotations

import itertools
import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status

NAME = "claude-tuid"
_SEQ = itertools.count(1)


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    sess = registry.get_or_create(NAME)
    sess.label = "tuid"
    registry.transition(NAME, Status.BUSY)
    return registry, hr.HookReceiver(registry, notify_fn), notify_fn


def _send(recv, run_async, **payload):
    payload.setdefault("session", NAME)
    payload["_seq"] = next(_SEQ)  # never a duplicate fingerprint
    run_async(recv._on_datagram(json.dumps(payload).encode()))


def _pre(recv, run_async, tool, tool_use_id=None, **extra):
    fields = {"hook_event_name": "PreToolUse", "tool_name": tool,
              "tool_input": {"command": "ls"}, **extra}
    if tool_use_id is not None:
        fields["tool_use_id"] = tool_use_id
    _send(recv, run_async, **fields)


def _permission(recv, run_async, tool="Bash", **extra):
    _send(recv, run_async, hook_event_name="PermissionRequest",
          tool_name=tool, tool_input={"command": "ls"}, **extra)


def _prompt_tool_info(notify_fn) -> dict:
    for call in reversed(notify_fn.await_args_list):
        _sess, event, context = call.args
        if event == "permission_prompt":
            return context["tool_info"]
    raise AssertionError("no permission prompt was notified")


def test_the_permission_requests_own_id_wins(receiver, run_async):
    registry, recv, notify_fn = receiver
    _pre(recv, run_async, "Bash", "toolu_pre")
    _permission(recv, run_async, tool_use_id="toolu_own")
    assert _prompt_tool_info(notify_fn)["tool_use_id"] == "toolu_own"


def test_without_its_own_id_the_parents_pre_tool_use_id_is_taken(receiver,
                                                                  run_async):
    registry, recv, notify_fn = receiver
    _pre(recv, run_async, "Bash", "toolu_pre")
    assert registry.get(NAME).last_parent_tool_use == ("Bash", "toolu_pre")
    _permission(recv, run_async)
    assert _prompt_tool_info(notify_fn)["tool_use_id"] == "toolu_pre"


def test_another_tools_pre_tool_use_id_is_not_taken(receiver, run_async):
    registry, recv, notify_fn = receiver
    _pre(recv, run_async, "Read", "toolu_read")
    _permission(recv, run_async, tool="Bash")
    assert _prompt_tool_info(notify_fn)["tool_use_id"] == ""


def test_an_agents_permission_request_does_not_take_the_parents_id(
        receiver, run_async):
    registry, recv, notify_fn = receiver
    _pre(recv, run_async, "Bash", "toolu_parent")
    _permission(recv, run_async, agent_id="a1agent")
    assert _prompt_tool_info(notify_fn)["tool_use_id"] == ""


def test_an_agents_pre_tool_use_is_not_the_parents(receiver, run_async):
    registry, recv, notify_fn = receiver
    _pre(recv, run_async, "Bash", "toolu_parent")
    _pre(recv, run_async, "Bash", "toolu_agent", agent_id="a1agent")
    assert registry.get(NAME).last_parent_tool_use == ("Bash", "toolu_parent")


def test_a_pre_tool_use_without_an_id_forgets_the_older_one(receiver,
                                                            run_async):
    registry, recv, notify_fn = receiver
    _pre(recv, run_async, "Bash", "toolu_old")
    _pre(recv, run_async, "Bash")
    assert registry.get(NAME).last_parent_tool_use is None
    _permission(recv, run_async)
    assert _prompt_tool_info(notify_fn)["tool_use_id"] == ""


@pytest.mark.parametrize("bad", [None, 7, "", ["toolu_x"]])
def test_an_unusable_payload_id_is_none(receiver, run_async, bad):
    registry, recv, notify_fn = receiver
    _permission(recv, run_async, tool_use_id=bad)
    assert _prompt_tool_info(notify_fn)["tool_use_id"] == ""


def test_a_question_takes_its_own_pre_tool_use_id(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="PreToolUse",
          tool_name="AskUserQuestion", tool_use_id="toolu_ask",
          tool_input={"questions": [{"question": "Which?", "options": [
              {"label": "A"}, {"label": "B"}]}]})
    info = _prompt_tool_info(notify_fn)
    assert info["name"] == "AskUserQuestion"
    assert info["tool_use_id"] == "toolu_ask"


def test_the_notification_fallback_has_no_id(receiver, run_async, tmp_path):
    registry, recv, notify_fn = receiver
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_t", "name": "Bash",
             "input": {"command": "ls"}}]}}) + "\n")
    _pre(recv, run_async, "Bash", "toolu_pre")
    _send(recv, run_async, type="permission_prompt",
          transcript_path=str(transcript),
          message="Claude needs your permission to use Bash")
    assert _prompt_tool_info(notify_fn)["tool_use_id"] == ""
