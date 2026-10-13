"""aipager.agent_tree: the still-running note, and the parent link a
PostToolUse carries."""

from __future__ import annotations

import pytest

from aipager import agent_tree as at

_NOTE_STILL_RUNNING = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed"
    "</status>\n<note>This agent stopped with background work of its own "
    "still running. It may resume on its own when that work completes or "
    "reports, and the same task-id notifies again if it does; the result "
    "below may be interim.</note>\n<result>Waiting.</result>\n"
    "</task-notification>")
_NOTE_DONE = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed"
    "</status>\n<note>A task-notification fires each time this agent stops "
    "with no live background children of its own.</note>\n"
    "</task-notification>")


# ── the note parser (moved from hook_receiver) ─────────────────────────────

def test_the_still_running_note_names_its_task():
    assert at.still_running_task_ids(_NOTE_STILL_RUNNING.format(tid="a2d0")) \
        == ["a2d0"]


def test_the_final_note_names_nothing():
    assert at.still_running_task_ids(_NOTE_DONE.format(tid="a2d0")) == []


def test_a_result_quoting_the_note_is_not_the_note():
    text = _NOTE_DONE.format(tid="a2d0").replace(
        "</task-notification>",
        "<result>It says \"background work of its own still running\"."
        "</result>\n</task-notification>")
    assert at.still_running_task_ids(text) == []


def test_each_notification_is_read_on_its_own():
    shell = ("<task-notification>\n<task-id>bhvkhjl4x</task-id>\n<status>"
             "completed</status>\n</task-notification>\n")
    assert at.still_running_task_ids(
        shell + _NOTE_STILL_RUNNING.format(tid="a2d0")) == ["a2d0"]


def test_not_text_names_nothing():
    assert at.still_running_task_ids(None) == []  # type: ignore[arg-type]
    assert at.still_running_task_ids("no notification here") == []


# ── launched_child ──────────────────────────────────────────────────────────

def _post(**over):
    msg = {"hook_event_name": "PostToolUse", "agent_id": "aparent01",
           "tool_name": "Agent",
           "tool_response": {"isAsync": True, "status": "async_launched",
                             "agentId": "bchild001"}}
    msg.update(over)
    return msg


def test_an_agent_call_inside_an_agent_links_its_child():
    assert at.launched_child(_post()) == "bchild001"


def test_the_older_task_tool_links_too():
    assert at.launched_child(_post(tool_name="Task")) == "bchild001"


def test_another_status_still_links():
    msg = _post(tool_response={"agentId": "bchild001", "status": "completed"})
    assert at.launched_child(msg) == "bchild001"


_NO_LINK = [
    ("main loop call", _post(agent_id="")),
    ("no agent id", {k: v for k, v in _post().items() if k != "agent_id"}),
    ("bad parent id", _post(agent_id="a b")),
    ("non-dict response", _post(tool_response="launched bchild001")),
    ("list response", _post(tool_response=[{"agentId": "bchild001"}])),
    ("no agentId", _post(tool_response={"status": "async_launched"})),
    ("non-string agentId", _post(tool_response={"agentId": 12345})),
    ("bad agentId", _post(tool_response={"agentId": "../etc"})),
    ("too long agentId", _post(tool_response={"agentId": "a" * 65})),
    ("self link", _post(tool_response={"agentId": "aparent01"})),
    ("not an agent tool", _post(tool_name="Bash")),
    ("not a dict", "PostToolUse"),
]


@pytest.mark.parametrize("case, msg", _NO_LINK, ids=[c for c, _m in _NO_LINK])
def test_anything_else_links_nothing(case, msg):
    assert at.launched_child(msg) == ""
