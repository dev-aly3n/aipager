"""Tests for aipager.dtach.hook_receiver — UDP datagram event dispatcher.

The HookReceiver is the daemon's side of Claude Code's hook system: it
parses datagrams emitted by ``aipager-hook`` / ``aipager-statusline``
and updates the session registry + fires notifications.

Strategy: drive ``HookReceiver._on_datagram`` directly with crafted JSON
payloads; verify that the registry transitions correctly and the
``notify_fn`` callback is invoked with the right event + context.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status


@pytest.fixture
def receiver():
    """Return (registry, recv, notify_fn) — a wired-up HookReceiver."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    return registry, recv, notify_fn


def _send(recv, run_async, **fields):
    """Helper: build a JSON datagram and feed it into _on_datagram."""
    payload = json.dumps(fields).encode()
    run_async(recv._on_datagram(payload))


# ---- pure helpers --------------------------------------------------------

def test_summarize_tool_bash_uses_description():
    out = hr._summarize_tool("Bash", {"description": "git status",
                                       "command": "git status"})
    assert "Bash" in out
    assert "git status" in out


def test_summarize_tool_bash_truncates_long_command():
    long = "x " * 100
    out = hr._summarize_tool("Bash", {"command": long})
    assert len(out) < 100  # truncated to 80 chars + prefix


@pytest.mark.parametrize("name,inp,expected_substr", [
    ("Read", {"file_path": "/foo"}, "Read: /foo"),
    ("Write", {"file_path": "/foo"}, "Write: /foo"),
    ("Edit", {"file_path": "/foo"}, "Edit: /foo"),
    ("Task", {"description": "do thing"}, "Task: do thing"),
    ("Glob", {"pattern": "*.py"}, "Glob: *.py"),
    ("Glob", {"pattern": "*.py", "path": "/src"}, "Glob: *.py in /src"),
    ("Grep", {"pattern": "foo"}, "Grep: foo"),
    ("WebFetch", {"url": "https://x.com"}, "WebFetch: https://x.com"),
    ("WebSearch", {"query": "claude"}, "WebSearch: claude"),
    ("NotebookEdit", {"notebook_path": "/x.ipynb"}, "NotebookEdit: /x.ipynb"),
    ("UnknownTool", {}, "UnknownTool"),
])
def test_summarize_tool_variants(name, inp, expected_substr):
    assert expected_substr in hr._summarize_tool(name, inp)


def test_summarize_tool_ask_user_question():
    inp = {"questions": [{"question": "Pick one:", "options": []}]}
    assert "Pick one:" in hr._summarize_tool("AskUserQuestion", inp)


def test_summarize_tool_ask_user_question_no_questions():
    assert hr._summarize_tool("AskUserQuestion", {"questions": []}) == "AskUserQuestion"


def test_read_statusline_missing_file_returns_none(tmp_path, monkeypatch):
    # Default lookup goes to /tmp; redirect via Path
    real_path = hr.Path

    class _RedirPath(real_path):
        def __new__(cls, p):
            if "claude-status-" in p:
                p = str(tmp_path / p.split("/")[-1])
            return real_path.__new__(cls, p)

    monkeypatch.setattr(hr, "Path", _RedirPath)
    assert hr._read_statusline("missing") is None


def test_read_statusline_parses_used_percentage(tmp_path, monkeypatch):
    f = tmp_path / "claude-status-jim.json"
    f.write_text(json.dumps({
        "context_window": {
            "used_percentage": 33.4,
            "total_input_tokens": 1000,
            "total_output_tokens": 200,
        },
    }))
    _real = hr.Path
    monkeypatch.setattr(hr, "Path",
                        lambda p: _real(tmp_path / p.split("/")[-1]))
    out = hr._read_statusline("jim")
    assert out is not None
    assert out["context_pct"] == 33
    assert out["total_input"] == 1000
    assert out["total_output"] == 200


def test_read_statusline_falls_back_to_remaining(tmp_path, monkeypatch):
    f = tmp_path / "claude-status-jim.json"
    f.write_text(json.dumps({
        "context_window": {"remaining_percentage": 75},
    }))
    _real = hr.Path
    monkeypatch.setattr(hr, "Path",
                        lambda p: _real(tmp_path / p.split("/")[-1]))
    assert hr._read_statusline("jim")["context_pct"] == 25


def test_extract_pending_tool_returns_last_tool_use(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "user", "message": {}}) + "\n"
        + json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ]},
        }) + "\n"
    )
    out = hr._extract_pending_tool(str(f))
    assert out["name"] == "Bash"


def test_extract_pending_tool_missing_file_returns_none(tmp_path):
    assert hr._extract_pending_tool(str(tmp_path / "no.jsonl")) is None


def test_extract_specific_tool_finds_by_name(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
            ]},
        }) + "\n"
    )
    out = hr._extract_specific_tool(str(f), "Read")
    assert out["name"] == "Read"
    assert out["input"]["file_path"] == "/x"


def test_extract_specific_tool_returns_none_when_absent(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps({"type": "assistant", "message": {"content": []}}) + "\n")
    assert hr._extract_specific_tool(str(f), "Bash") is None


# ---- _on_datagram: invalid input -----------------------------------------

def test_invalid_json_silently_dropped(receiver, run_async):
    _, recv, notify_fn = receiver
    run_async(recv._on_datagram(b"not json"))
    notify_fn.assert_not_awaited()


def test_missing_event_dropped(receiver, run_async):
    _, recv, notify_fn = receiver
    _send(recv, run_async, session="claude-jim")
    notify_fn.assert_not_awaited()


def test_missing_session_dropped(receiver, run_async):
    _, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="PreToolUse", tool_name="Bash")
    notify_fn.assert_not_awaited()


# ---- transcript_path / cwd capture --------------------------------------

def test_transcript_path_derives_claude_session_id(receiver, run_async):
    registry, recv, _ = receiver
    _send(recv, run_async,
          hook_event_name="UserPromptSubmit",
          session="claude-jim",
          transcript_path="/home/x/.claude/projects/p/UUID-ABC.jsonl")
    sess = registry.get("claude-jim")
    assert sess.transcript_path == "/home/x/.claude/projects/p/UUID-ABC.jsonl"
    assert sess.claude_session_id == "UUID-ABC"


def test_cwd_capture(receiver, run_async):
    registry, recv, _ = receiver
    _send(recv, run_async,
          hook_event_name="UserPromptSubmit",
          session="claude-jim",
          cwd="/home/user/proj")
    assert registry.get("claude-jim").cwd == "/home/user/proj"


# ---- per-event branches --------------------------------------------------

def test_permission_request_transitions_to_interactive(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PermissionRequest",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    assert registry.get("claude-jim").status == Status.INTERACTIVE
    notify_fn.assert_awaited_once()
    sess, event, ctx = notify_fn.await_args.args
    assert event == "permission_prompt"
    assert ctx["tool_info"]["name"] == "Bash"


def test_user_prompt_submit_transitions_to_busy(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="UserPromptSubmit",
          session="claude-jim")
    assert registry.get("claude-jim").status == Status.BUSY
    notify_fn.assert_awaited_once()
    _, event, _ = notify_fn.await_args.args
    assert event == "user_prompt_submit"


def test_pre_tool_use_ask_user_question_goes_interactive(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PreToolUse",
          session="claude-jim",
          tool_name="AskUserQuestion",
          tool_input={"questions": [{"question": "?", "options": []}]})
    assert registry.get("claude-jim").status == Status.INTERACTIVE
    _, event, _ = notify_fn.await_args.args
    assert event == "permission_prompt"


def test_pre_tool_use_bash_fires_tool_use_notification(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PreToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    assert registry.get("claude-jim").status == Status.BUSY
    _, event, ctx = notify_fn.await_args.args
    assert event == "tool_use"
    assert ctx["tool_name"] == "Bash"
    # tool_input is NOT forwarded for Bash (only for Write/Edit)
    assert ctx["tool_input_full"] is None


def test_pre_tool_use_write_forwards_full_input(receiver, run_async):
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PreToolUse",
          session="claude-jim",
          tool_name="Write",
          tool_input={"file_path": "/x", "content": "hi"})
    _, event, ctx = notify_fn.await_args.args
    assert event == "tool_use"
    assert ctx["tool_input_full"] == {"file_path": "/x", "content": "hi"}


def test_pre_tool_use_with_sl_tokens_populates_session(receiver, run_async):
    registry, recv, _ = receiver
    _send(recv, run_async,
          hook_event_name="PreToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"},
          sl_tokens={
              "context_pct": 45,
              "total_output": 200,
              "lines_added": 3,
              "lines_removed": 1,
          })
    sess = registry.get("claude-jim")
    assert sess.last_token_pct == 45
    assert sess.output_baseline == 200
    assert sess.last_output_tokens == 0  # at baseline


def test_post_tool_use_fires_tool_done(receiver, run_async):
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    _, event, _ = notify_fn.await_args.args
    assert event == "tool_done"


def test_post_tool_use_failure_fires_tool_failed(receiver, run_async):
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUseFailure",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    _, event, _ = notify_fn.await_args.args
    assert event == "tool_failed"


def test_subagent_start_records_in_session(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="SubagentStart",
          session="claude-jim",
          agent_id="agent-1",
          agent_type="explore")
    sess = registry.get("claude-jim")
    assert "agent-1" in sess.active_subagents
    assert sess.active_subagents["agent-1"]["type"] == "explore"
    _, event, _ = notify_fn.await_args.args
    assert event == "subagent_start"


def test_subagent_stop_removes_and_fires_notify(receiver, run_async):
    registry, recv, notify_fn = receiver
    # Plant a subagent record manually so stop can find it
    sess = registry.get_or_create("claude-jim")
    sess.active_subagents["agent-1"] = {
        "type": "explore",
        "started_at": time.monotonic() - 5.0,
        "history_idx": 3,
    }
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="SubagentStop",
          session="claude-jim",
          agent_id="agent-1",
          agent_type="explore")
    assert "agent-1" not in sess.active_subagents
    _, event, ctx = notify_fn.await_args.args
    assert event == "subagent_stop"
    assert ctx["elapsed"] >= 4.5


def test_session_end_transitions_to_gone(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="UserPromptSubmit",  # first put it in BUSY
          session="claude-jim")
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="SessionEnd",
          session="claude-jim",
          source="user")
    assert registry.get("claude-jim").status == Status.GONE
    _, event, ctx = notify_fn.await_args.args
    assert event == "session_end"
    assert ctx["source"] == "user"


def test_pre_compact_records_pct_and_fires(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.last_token_pct = 85
    _send(recv, run_async,
          hook_event_name="PreCompact",
          session="claude-jim",
          trigger="manual")
    assert registry.get("claude-jim").pre_compact_pct == 85
    _, event, ctx = notify_fn.await_args.args
    assert event == "compacting"
    assert ctx["trigger"] == "manual"


def test_session_start_compact_fires_compact_done(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pre_compact_pct = 80
    _send(recv, run_async,
          hook_event_name="SessionStart",
          session="claude-jim",
          source="compact",
          sl_tokens={"context_pct": 5})
    _, event, ctx = notify_fn.await_args.args
    assert event == "compact_done"
    assert ctx["before_pct"] == 80
    assert ctx["after_pct"] == 5


def test_session_start_non_compact_just_tracks_session(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="SessionStart",
          session="claude-jim",
          source="")
    # Session is now tracked but no notification fired
    assert registry.get("claude-jim") is not None
    notify_fn.assert_not_awaited()


def test_statusline_updates_session_metrics(receiver, run_async):
    registry, recv, _ = receiver
    _send(recv, run_async,
          type="statusline",
          session="claude-jim",
          context_pct=42,
          total_output=500,
          model_name="Opus 4.7",
          cost_usd=0.25,
          lines_added=5,
          lines_removed=2)
    sess = registry.get("claude-jim")
    assert sess.last_token_pct == 42
    assert sess.model_name == "Opus 4.7"
    assert sess.last_cost_usd == 0.25
    assert sess.output_baseline == 500


def test_statusline_context_warning_fires_at_80pct(receiver, run_async):
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          type="statusline",
          session="claude-jim",
          context_pct=85)
    # Either the model-change notify or the context warning fires.
    events = [args[1] for args, _ in
              [(c.args, c.kwargs) for c in notify_fn.await_args_list]]
    assert "context_warning" in events


def test_statusline_pinned_update_on_model_change(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.model_name = "Old"
    _send(recv, run_async,
          type="statusline",
          session="claude-jim",
          context_pct=10,
          model_name="New")
    events = [args[1] for args, _ in
              [(c.args, c.kwargs) for c in notify_fn.await_args_list]]
    assert "pinned_update" in events


def test_idle_event_with_summary_fires_idle_prompt(receiver, run_async):
    registry, recv, notify_fn = receiver
    # Seed BUSY first so IDLE actually transitions
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="Stop",
          session="claude-jim",
          last_assistant_message="All done.")
    assert registry.get("claude-jim").status == Status.IDLE
    _, event, ctx = notify_fn.await_args.args
    assert event == "idle_prompt"
    assert ctx["summary"] == "All done."


def test_unknown_event_just_ensures_tracking(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="some_unknown_event",
          session="claude-jim")
    assert registry.get("claude-jim") is not None
    notify_fn.assert_not_awaited()
