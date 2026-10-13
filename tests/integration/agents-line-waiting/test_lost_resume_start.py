"""A parent's resume ``SubagentStart`` never arrives (lost, or dropped by
the 3 s dedup), but its own tool hooks do: P is working again and the
120 s rule does not drop it. ``MessageDisplay`` alone does not end the
wait (entrypoints.md "Hook datagrams"; design.md test plan)."""

from __future__ import annotations

import pytest

from aipager.state import WAITING_PARENT_RESUME_SECONDS
from tests import agents_line_waiting_bb as h

P, C, P_TYPE, C_TYPE = h.P, h.C, h.P_TYPE, h.C_TYPE


@pytest.fixture
def waiting(rig):
    rig.prompt("ship it")
    rig.agent_call(P, P_TYPE)
    rig.agent_call(C, C_TYPE, by=P)
    rig.stop(P, P_TYPE)
    return rig


def _event(r, event):
    fields = {"agent_id": P, "agent_type": P_TYPE}
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"):
        fields.update(tool_name="Read", tool_input={"file_path": "/home/alice/x.py"})
    if event == "PostToolUse":
        fields["tool_response"] = {"ok": True}
    if event == "PostToolUseFailure":
        fields["error"] = "no such file"
    if event == "Notification":
        fields.update(message="Claude needs your permission", notification_type="other")
    r.hook(event, **fields)


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse", "PostToolUseFailure",
                                   "PermissionRequest", "Notification"])
def test_the_parents_own_tool_event_ends_the_wait(waiting, event):
    _event(waiting, event)
    assert waiting.sess.agent_is_waiting(P) is False


def test_a_message_display_does_not_end_the_wait(waiting):
    waiting.hook("MessageDisplay", agent_id=P, delta="Waiting for the reviewer.",
                 message_id="m1", index=0, final=True)
    assert waiting.sess.agent_is_waiting(P) is True


def test_a_message_display_still_updates_the_parents_activity(waiting):
    waiting.hook("MessageDisplay", agent_id=P, delta="Waiting for the reviewer.",
                 message_id="m1", index=0, final=True)
    assert "Waiting for the reviewer." in h.row_of(waiting.answer(), P_TYPE)


def test_another_agents_tool_event_does_not_end_the_wait(waiting):
    waiting.tool(C)
    assert waiting.sess.agent_is_waiting(P) is True


def test_the_main_loops_tool_event_does_not_end_the_wait(waiting):
    waiting.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
    assert waiting.sess.agent_is_waiting(P) is True


@pytest.fixture
def resumed_by_tool(waiting, monkeypatch):
    waiting.stop(C, C_TYPE)
    mon = h.Monitor(waiting, monkeypatch)
    mon.scan()
    waiting.sleep(1)
    waiting.hook("PreToolUse", tool_name="Read",
                 tool_input={"file_path": "/home/alice/x.py"}, agent_id=P,
                 agent_type=P_TYPE)
    waiting.sleep(WAITING_PARENT_RESUME_SECONDS + 30)
    mon.scan()
    waiting.mon = mon
    return waiting


def test_a_parent_resumed_by_its_tool_is_not_dropped_at_120_s(resumed_by_tool):
    assert P in resumed_by_tool.sess.bg_agents


def test_a_parent_resumed_by_its_tool_stays_in_the_job(resumed_by_tool):
    assert P in resumed_by_tool.sess.active_subagents


def test_its_final_stop_then_finishes_it(resumed_by_tool):
    resumed_by_tool.stop(P, P_TYPE)
    assert P not in resumed_by_tool.sess.bg_agents


def test_a_message_display_alone_does_not_save_it_from_the_deadline(waiting, monkeypatch):
    waiting.stop(C, C_TYPE)
    mon = h.Monitor(waiting, monkeypatch)
    mon.scan()
    waiting.hook("MessageDisplay", agent_id=P, delta="All done here.",
                 message_id="m2", index=0, final=True)
    waiting.sleep(WAITING_PARENT_RESUME_SECONDS + 1)
    mon.scan()
    assert P not in waiting.sess.bg_agents

