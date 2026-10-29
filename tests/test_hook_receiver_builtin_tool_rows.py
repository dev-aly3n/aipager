"""The card row of a built-in tool names its short, safe input field.

Read from Claude Code 2.1.312's tool schemas: SendMessage names whom it
writes to (never ``message`` or ``summary``), ToolSearch its query, Monitor
its description (else its command), TaskStop and its older names the id
they stop, and so on. A field that is missing, blank or not text, or an
input that is not a dict, gives the bare tool name. Every field is one
line, at most 80 characters, with direction controls revealed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.bot.animation import _find_tool_row
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, TrackedSession

RLO = "\u202e"
RLO_SHOWN = "⟨U+202E⟩"
SECRET = "the person's private words sk-ant-0000"


def row(tool, inp):
    return hr._summarize_tool(tool, inp)


# ---- each row, from its field ------------------------------------------------

CASES = [
    ("SendMessage", {"to": "frontend", "message": SECRET, "summary": SECRET},
     "SendMessage: to frontend"),
    ("ToolSearch", {"query": "select:Monitor,TaskStop", "max_results": 3},
     "ToolSearch: select:Monitor,TaskStop"),
    ("Monitor", {"description": "Watch the build log", "command": "tail -f b.log"},
     "Monitor: Watch the build log"),
    ("Monitor", {"command": "tail -f b.log"}, "Monitor: tail -f b.log"),
    ("TaskStop", {"task_id": "b6uvcit3y"}, "TaskStop: b6uvcit3y"),
    ("TaskStop", {"shell_id": "bash_2"}, "TaskStop: bash_2"),
    ("TaskStop", {"task_id": "b1", "shell_id": "b2"}, "TaskStop: b1"),
    ("KillShell", {"shell_id": "bash_1"}, "KillShell: bash_1"),
    ("KillBash", {"shell_id": "bash_1"}, "KillBash: bash_1"),
    ("GetTask", {"taskId": "b7"}, "GetTask: b7"),
    ("TaskGet", {"taskId": "4"}, "TaskGet: 4"),
    ("TaskCreate", {"subject": "Fix the login test", "description": SECRET},
     "TaskCreate: Fix the login test"),
    ("TaskUpdate", {"taskId": "3", "status": "completed", "description": SECRET},
     "TaskUpdate: 3 (completed)"),
    ("TaskUpdate", {"taskId": "3"}, "TaskUpdate: 3"),
    ("ScheduleWakeup", {"delaySeconds": 300, "reason": "Wait for the deploy",
                        "prompt": SECRET}, "ScheduleWakeup: Wait for the deploy"),
    ("CronCreate", {"cron": "*/5 * * * *", "prompt": SECRET},
     "CronCreate: */5 * * * *"),
    ("CronDelete", {"id": "job-1"}, "CronDelete: job-1"),
    ("EnterWorktree", {"name": "fix-login"}, "EnterWorktree: fix-login"),
    ("EnterWorktree", {"path": "/home/alice/wt"}, "EnterWorktree: /home/alice/wt"),
    ("EnterWorktree", {"name": "fix-login", "path": "/home/alice/wt"},
     "EnterWorktree: fix-login"),
    ("ExitWorktree", {"action": "keep"}, "ExitWorktree: keep"),
    ("Workflow", {"name": "spec", "script": SECRET}, "Workflow: spec"),
    ("TodoWrite", {"todos": [{"content": SECRET, "status": "completed"},
                             {"content": SECRET, "status": "in_progress"},
                             {"content": SECRET, "status": "pending"}]},
     "TodoWrite: 1/3"),
    ("LSP", {"operation": "hover", "filePath": "/home/alice/app.py", "line": 1},
     "LSP: hover /home/alice/app.py"),
    ("LSP", {"operation": "workspaceSymbol"}, "LSP: workspaceSymbol"),
    ("PowerShell", {"description": "List files", "command": "Get-ChildItem"},
     "PowerShell: List files"),
    ("PowerShell", {"command": "Get-ChildItem"}, "PowerShell: Get-ChildItem"),
    ("ListMcpResourcesTool", {"server": "docs"}, "ListMcpResourcesTool: docs"),
    ("ReadMcpResourceTool", {"server": "docs", "uri": "file:///a.md"},
     "ReadMcpResourceTool: docs file:///a.md"),
]


@pytest.mark.parametrize("tool,inp,expected", CASES,
                         ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_row_names_the_field(tool, inp, expected):
    assert row(tool, inp) == expected


@pytest.mark.parametrize("tool,inp,expected", CASES,
                         ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_row_never_shows_a_body_or_prompt(tool, inp, expected):
    assert "private" not in row(tool, inp)


# ---- SendMessage's guard -----------------------------------------------------

@pytest.mark.parametrize("inp", [
    {"to": "frontend", "message": SECRET},
    {"to": "frontend", "summary": SECRET},
    {"message": SECRET, "summary": SECRET},
    {"to": "", "message": SECRET},
    {"to": None, "message": SECRET, "summary": SECRET},
    {"message": {"type": "shutdown_request", "reason": SECRET}},
], ids=["message", "summary", "no-to", "blank-to", "none-to", "structured"])
def test_send_message_row_never_shows_its_message(inp):
    out = row("SendMessage", inp)
    assert "private" not in out and "sk-ant" not in out
    assert out in ("SendMessage", "SendMessage: to frontend")


# ---- missing fields and malformed input --------------------------------------

BARE = [
    ("SendMessage", {"message": "hi"}), ("ToolSearch", {"max_results": 5}),
    ("Monitor", {"timeout_ms": 1000}), ("TaskStop", {}), ("KillShell", {}),
    ("KillBash", {}), ("GetTask", {}), ("TaskGet", {}),
    ("TaskCreate", {"description": "x"}), ("TaskUpdate", {"status": "completed"}),
    ("ScheduleWakeup", {"stop": True}), ("CronCreate", {"prompt": "x"}),
    ("CronDelete", {}), ("EnterWorktree", {}), ("ExitWorktree", {}),
    ("Workflow", {"script": "export const meta = {}"}), ("TodoWrite", {"todos": []}),
    ("TodoWrite", {"todos": "three"}), ("TodoWrite", {"todos": ["a", None]}),
    ("TodoWrite", {}), ("LSP", {"line": 1}),
    ("PowerShell", {}), ("ListMcpResourcesTool", {}),
    ("ReadMcpResourceTool", {}),
]


@pytest.mark.parametrize("tool,inp", BARE,
                         ids=[f"{c[0]}-{i}" for i, c in enumerate(BARE)])
def test_a_missing_field_gives_the_bare_name(tool, inp):
    assert row(tool, inp) == tool


TOOLS = sorted({c[0] for c in CASES})


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("bad", ["", "   ", None, 7, ["x"], {"x": 1}],
                         ids=["empty", "blank", "none", "int", "list", "dict"])
def test_a_field_that_is_not_text_gives_the_bare_name(tool, bad):
    keys = ("to", "query", "description", "command", "task_id", "shell_id",
            "taskId", "subject", "status", "reason", "cron", "id", "name", "path",
            "action", "operation", "filePath", "server", "uri", "todos")
    assert row(tool, {k: bad for k in keys}) == tool


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("inp", ["not a dict", None, ["to"], 7],
                         ids=["str", "none", "list", "int"])
def test_a_malformed_input_gives_the_bare_name(tool, inp):
    assert row(tool, inp) == tool


def test_todo_write_counts_dict_items_only():
    out = row("TodoWrite", {"todos": [{"status": "completed"}, "x",
                                      {"status": "pending"}, None]})
    assert out == "TodoWrite: 1/2"


# ---- one line, cut, bidi -----------------------------------------------------

@pytest.mark.parametrize("tool,key,prefix", [
    ("SendMessage", "to", "SendMessage: to "), ("ToolSearch", "query", "ToolSearch: "),
    ("Monitor", "description", "Monitor: "), ("TaskStop", "task_id", "TaskStop: "),
    ("ScheduleWakeup", "reason", "ScheduleWakeup: "), ("CronCreate", "cron", "CronCreate: "),
    ("EnterWorktree", "name", "EnterWorktree: "), ("Workflow", "name", "Workflow: "),
    ("PowerShell", "command", "PowerShell: "), ("TaskCreate", "subject", "TaskCreate: "),
])
def test_row_is_one_line_cut_to_80_with_controls_revealed(tool, key, prefix):
    assert row(tool, {key: "a\n b\t c"}) == prefix + "a b c"
    assert row(tool, {key: "x" * 300}) == prefix + "x" * 80
    out = row(tool, {key: f"a{RLO}b"})
    assert out == f"{prefix}a{RLO_SHOWN}b" and RLO not in out


def test_two_field_rows_cut_each_field():
    out = row("LSP", {"operation": "o" * 200, "filePath": "p" * 200})
    assert out == "LSP: " + "o" * 80 + " " + "p" * 80
    out = row("TaskUpdate", {"taskId": "t" * 200, "status": "s" * 200})
    assert out == "TaskUpdate: " + "t" * 80 + " (" + "s" * 80 + ")"


@pytest.mark.parametrize("tool", ["mcp__server__do_thing", "UnknownTool",
                                  "ExitPlanMode", "EnterPlanMode", "PushNotification",
                                  "SendUserMessage", "StructuredOutput", "TaskList",
                                  "CronList"])
def test_tools_without_a_chosen_field_keep_the_bare_name(tool):
    assert row(tool, {"to": "x", "query": "q", "description": "d", "name": "n",
                      "message": SECRET, "plan": SECRET}) == tool


# ---- through the receiver and onto the card ----------------------------------

def test_pre_tool_use_send_message_sends_the_recipient_row(run_async):
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(SessionRegistry(), notify_fn)
    payload = json.dumps({
        "hook_event_name": "PreToolUse", "session": "claude-alice",
        "tool_name": "SendMessage",
        "tool_input": {"to": "frontend", "message": SECRET}}).encode()
    run_async(recv._on_datagram(payload))
    _, event, ctx = notify_fn.await_args.args
    assert event == "tool_use"
    assert ctx["tool_summary"] == "SendMessage: to frontend"


def test_named_rows_still_match_their_transcript_block():
    sess = TrackedSession(name="claude-alice", label="alice")
    sess.tool_history = [(row("SendMessage", {"to": "frontend"}), True),
                         (row("TaskStop", {"task_id": "b1"}), False)]
    assert _find_tool_row(sess, "SendMessage", 0) == 0
    assert _find_tool_row(sess, "TaskStop", 0) == 1


# ---- the permission card shows what runs, not Claude's description -----------

@pytest.mark.parametrize("tool", ["Monitor", "PowerShell"])
def test_permission_detail_is_the_command(tool):
    inp = {"description": "Watch the build", "command": "  tail -f b.log\n"}
    assert hr._tool_detail(tool, inp) == "tail -f b.log"


def test_monitor_permission_detail_is_the_socket_url_without_a_command():
    inp = {"description": "Watch events", "ws": {"url": "wss://example.test/feed"}}
    assert hr._tool_detail("Monitor", inp) == "wss://example.test/feed"


def test_monitor_permission_detail_skips_a_blank_command_for_the_url():
    inp = {"command": "   ", "ws": {"url": "wss://example.test/feed"}}
    assert hr._tool_detail("Monitor", inp) == "wss://example.test/feed"


def test_powershell_permission_detail_never_takes_a_socket_url():
    inp = {"ws": {"url": "wss://example.test/feed"}}
    assert hr._tool_detail("PowerShell", inp) == ""


def test_permission_detail_reveals_a_direction_control():
    out = hr._tool_detail("PowerShell", {"command": f"a{RLO}b"})
    assert out == f"a{RLO_SHOWN}b"


@pytest.mark.parametrize("tool", ["Monitor", "PowerShell"])
@pytest.mark.parametrize("inp", [
    {}, {"command": 7}, {"command": None}, {"ws": "wss://x"}, {"ws": {"url": 3}},
    {"ws": {}}, "not a dict", None, ["command"],
], ids=["missing", "int", "none", "ws-str", "ws-url-int", "ws-empty", "str",
        "none-input", "list"])
def test_permission_detail_without_a_command_is_empty(tool, inp):
    assert hr._tool_detail(tool, inp) == ""


def test_permission_request_carries_the_monitor_command(run_async):
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(SessionRegistry(), notify_fn)
    payload = json.dumps({
        "hook_event_name": "PermissionRequest", "session": "claude-alice",
        "tool_name": "Monitor",
        "tool_input": {"description": "Watch the build",
                       "command": "tail -f b.log"}}).encode()
    run_async(recv._on_datagram(payload))
    infos = [c.args[2].get("tool_info") for c in notify_fn.await_args_list
             if isinstance(c.args[2], dict) and c.args[2].get("tool_info")]
    assert infos and infos[-1]["summary"] == "Monitor: Watch the build"
    assert infos[-1]["detail"] == "tail -f b.log"
