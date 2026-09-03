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
    # Redirect the /tmp status-file lookup into tmp_path via a Path
    # *factory* (matching the sibling tests). NB: do not subclass
    # pathlib.Path here — subclassing is unsupported on Python 3.10/3.11
    # (`AttributeError: ... has no attribute '_flavour'`); a factory that
    # returns a real Path works on every version.
    _real = hr.Path
    monkeypatch.setattr(hr, "Path",
                        lambda p: _real(tmp_path / p.split("/")[-1]))
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


# ---- queue_pickup (design.md "queue handoff") ---------------------------

def test_queue_pickup_sets_trigger_and_last_prompt_from_the_last_consumed(
    receiver, run_async,
):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="queue_pickup",
          session="claude-jim",
          consumed=[
              {"msg_id": 10, "chat_id": -100, "raw_text": "first"},
              {"msg_id": 11, "chat_id": -100, "raw_text": "second"},
          ],
          expired=[])
    sess = registry.get("claude-jim")
    assert sess.trigger_msg_id == 11  # the LAST consumed, not the first
    assert sess.last_prompt == "second"


def test_queue_pickup_forwards_consumed_and_expired_to_notify(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="queue_pickup",
          session="claude-jim",
          consumed=[{"msg_id": 10, "chat_id": -100, "raw_text": "hi"}],
          expired=[{"msg_id": 5, "chat_id": -100, "raw_text": "stale"}])
    notify_fn.assert_awaited_once()
    sess, event, ctx = notify_fn.await_args.args
    assert event == "queue_pickup"
    assert ctx["consumed"] == [{"msg_id": 10, "chat_id": -100, "raw_text": "hi"}]
    assert ctx["expired"] == [{"msg_id": 5, "chat_id": -100, "raw_text": "stale"}]


def test_queue_pickup_expired_only_still_notifies(receiver, run_async):
    """Expired-only (no match at all) must still surface — the
    best-effort notice depends on it."""
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="queue_pickup",
          session="claude-jim",
          consumed=[],
          expired=[{"msg_id": 5, "chat_id": -100, "raw_text": "stale"}])
    notify_fn.assert_awaited_once()
    sess = registry.get("claude-jim")
    assert sess.trigger_msg_id is None  # nothing consumed → unchanged
    assert sess.last_prompt == ""


def test_queue_pickup_with_neither_consumed_nor_expired_is_a_noop(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="queue_pickup",
          session="claude-jim",
          consumed=[],
          expired=[])
    notify_fn.assert_not_awaited()


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


def test_pre_tool_use_forwards_agent_id_in_tool_use_context(receiver, run_async):
    """Design "agent activity rows on the busy card": a PreToolUse fired
    from inside a subagent carries agent_id, forwarded verbatim so
    notify.py can attribute the tool call to that agent instead of the
    parent's tool_history."""
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PreToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"},
          agent_id="agent-1")
    _, event, ctx = notify_fn.await_args.args
    assert event == "tool_use"
    assert ctx["agent_id"] == "agent-1"


def test_pre_tool_use_no_agent_id_forwards_empty_string(receiver, run_async):
    """The parent's own tool calls carry no agent_id — forwarded as ""
    (never None), the exact truthiness the attribution guard relies on."""
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PreToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    _, event, ctx = notify_fn.await_args.args
    assert ctx["agent_id"] == ""


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


def test_post_tool_use_forwards_agent_id_in_tool_done_context(receiver, run_async):
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"},
          agent_id="agent-1")
    _, event, ctx = notify_fn.await_args.args
    assert event == "tool_done"
    assert ctx["agent_id"] == "agent-1"


def test_pre_tool_use_marks_tool_in_flight(receiver, run_async):
    registry, recv, _ = receiver
    _send(recv, run_async,
          hook_event_name="PreToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    sess = registry.get("claude-jim")
    assert sess.pending_tool_started_at is not None
    assert sess.pending_tool_started_at <= time.monotonic()


def test_post_tool_use_clears_tool_in_flight(receiver, run_async):
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pending_tool_started_at = time.monotonic() - 10.0
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    assert sess.pending_tool_started_at is None


def test_post_tool_use_failure_fires_tool_failed(receiver, run_async):
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUseFailure",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    _, event, _ = notify_fn.await_args.args
    assert event == "tool_failed"


def test_post_tool_use_failure_forwards_agent_id_in_tool_failed_context(
    receiver, run_async,
):
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUseFailure",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"},
          agent_id="agent-1")
    _, event, ctx = notify_fn.await_args.args
    assert event == "tool_failed"
    assert ctx["agent_id"] == "agent-1"


def test_post_tool_use_failure_clears_tool_in_flight(receiver, run_async):
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pending_tool_started_at = time.monotonic() - 10.0
    _send(recv, run_async,
          hook_event_name="PostToolUseFailure",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    assert sess.pending_tool_started_at is None


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


# ---- synthetic no-response placeholder --------------------------------------
#
# The Stop hook's last_assistant_message takes precedence over both
# transcript readers, so filtering only those would leave the reported
# symptom in place. The payload carries no model field, so this path has to
# recognise the placeholder by its exact text.

_NO_RESPONSE = "No response requested."


def test_stop_hook_no_response_placeholder_is_not_the_summary(receiver, run_async):
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="Stop",
          session="claude-jim",
          last_assistant_message=_NO_RESPONSE)
    _, event, ctx = notify_fn.await_args.args
    assert event == "idle_prompt"
    assert ctx["summary"] == "", f"placeholder leaked as the answer: {ctx!r}"


def test_stop_hook_no_response_placeholder_sets_no_response_flag(receiver, run_async):
    """Without the flag, notify falls back to the previous turn's summary."""
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="Stop",
          session="claude-jim",
          last_assistant_message=_NO_RESPONSE)
    _, _event, ctx = notify_fn.await_args.args
    assert ctx.get("no_response") is True


def test_stop_hook_placeholder_matched_exactly_not_as_substring(receiver, run_async):
    """A real answer quoting the phrase must still be published."""
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    real = f'Claude Code writes "{_NO_RESPONSE}" after a compact.'
    _send(recv, run_async,
          hook_event_name="Stop",
          session="claude-jim",
          last_assistant_message=real)
    _, _event, ctx = notify_fn.await_args.args
    assert ctx["summary"] == real
    assert not ctx.get("no_response")


def test_stop_hook_real_message_sets_no_flag(receiver, run_async):
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="Stop",
          session="claude-jim",
          last_assistant_message="All done.")
    _, _event, ctx = notify_fn.await_args.args
    assert not ctx.get("no_response")


def test_stop_hook_placeholder_falls_through_to_transcript(receiver, run_async, tmp_path):
    """Payload placeholder ⇒ consult the transcript, which is also filtered.

    The transcript's newest assistant entry is the same placeholder, so
    extract_last_response returns "" rather than scanning back to the
    previous turn's answer.
    """
    registry, recv, notify_fn = receiver
    tp = tmp_path / "t.jsonl"
    tp.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "PREVIOUS TURN"}]},
        }) + "\n"
        + json.dumps({
            "type": "assistant",
            "isApiErrorMessage": False,
            "message": {
                "model": "<synthetic>",
                "content": [{"type": "text", "text": _NO_RESPONSE}],
            },
        }) + "\n"
    )
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="Stop",
          session="claude-jim",
          transcript_path=str(tp),
          last_assistant_message=_NO_RESPONSE)
    _, _event, ctx = notify_fn.await_args.args
    assert ctx["summary"] == "", f"stale turn republished: {ctx!r}"
    assert ctx.get("no_response") is True


def test_stop_hook_api_error_message_still_published(receiver, run_async):
    """API errors share the synthetic model; they must reach the error card."""
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="Stop",
          session="claude-jim",
          last_assistant_message="API Error: 529 Overloaded.")
    _, _event, ctx = notify_fn.await_args.args
    assert "529" in ctx["summary"]
    assert not ctx.get("no_response")


def test_unknown_event_just_ensures_tracking(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="some_unknown_event",
          session="claude-jim")
    assert registry.get("claude-jim") is not None
    notify_fn.assert_not_awaited()


# ---- receiver-side dedup (defense against double-wired hooks) -------

def test_duplicate_hook_event_dropped_within_window(receiver, run_async):
    """Identical events within HOOK_DEDUP_WINDOW_SECONDS: second is
    dropped. Belt-and-braces against settings.json double-wiring."""
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    # notify_fn was awaited once for the first event's tool_done; the
    # duplicate must have been dropped before reaching the notify path.
    assert notify_fn.await_count == 1


def test_duplicate_hook_event_kept_after_window(receiver, run_async,
                                                monkeypatch):
    """Same event JSON, but the second arrives past the dedup window
    (backdated) → both processed."""
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    # Backdate the cached fingerprint so it's outside the window.
    from aipager.config import HOOK_DEDUP_WINDOW_SECONDS
    now = time.monotonic()
    old = now - HOOK_DEDUP_WINDOW_SECONDS - 1
    recv._recent_fingerprints = {k: old for k in recv._recent_fingerprints}
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    assert notify_fn.await_count == 2


def test_different_events_same_session_not_deduped(receiver, run_async):
    """PostToolUse then SubagentStop on the same session are distinct
    events — both must be processed."""
    _, recv, notify_fn = receiver
    # Prime a subagent so SubagentStop finds it.
    sess = receiver[0].get_or_create("claude-jim")
    sess.active_subagents["agent-x"] = {
        "type": "explore", "started_at": time.monotonic() - 3.0,
        "history_idx": 0,
    }
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    _send(recv, run_async,
          hook_event_name="SubagentStop",
          session="claude-jim",
          agent_id="agent-x",
          agent_type="explore")
    assert notify_fn.await_count == 2


def test_different_sessions_same_event_not_deduped(receiver, run_async):
    """Same event payload but different session — both must be
    processed (dedup key includes session)."""
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-alice",
          tool_name="Bash",
          tool_input={"command": "ls"})
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-bob",
          tool_name="Bash",
          tool_input={"command": "ls"})
    assert notify_fn.await_count == 2


def test_dedup_cache_pruned_of_old_entries(receiver, run_async):
    """Old fingerprints past the prune horizon are evicted on the next
    datagram, keeping memory bounded."""
    from aipager.config import HOOK_DEDUP_WINDOW_SECONDS
    _, recv, _ = receiver
    stale_age = time.monotonic() - HOOK_DEDUP_WINDOW_SECONDS * 20
    recv._recent_fingerprints = {
        "old-1": stale_age, "old-2": stale_age, "old-3": stale_age,
    }
    _send(recv, run_async,
          hook_event_name="PostToolUse",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    # Stale entries evicted; only the new fingerprint remains.
    assert all(k.startswith("claude-jim:") for k in recv._recent_fingerprints)
    assert len(recv._recent_fingerprints) == 1


# ---- hook_memory_cap_hit dispatch ---------------------------------------

def test_hook_memory_cap_hit_dispatches_notify(receiver, run_async):
    """A datagram with type=hook_memory_cap_hit for a known session
    fires the notify callback with the right event + hook name."""
    registry, recv, notify_fn = receiver
    registry.get_or_create("claude-jim")
    _send(recv, run_async,
          type="hook_memory_cap_hit",
          session="claude-jim",
          hook="aipager-hook")
    notify_fn.assert_awaited_once()
    sess, event, context = notify_fn.call_args.args
    assert sess.name == "claude-jim"
    assert event == "hook_memory_cap_hit"
    assert context == {"hook": "aipager-hook", "tool": ""}


def test_hook_memory_cap_hit_defaults_hook_name(receiver, run_async):
    """Missing ``hook`` field falls back to ``aipager-hook``."""
    registry, recv, notify_fn = receiver
    registry.get_or_create("claude-jim")
    _send(recv, run_async,
          type="hook_memory_cap_hit",
          session="claude-jim")
    notify_fn.assert_awaited_once()
    _, _, context = notify_fn.call_args.args
    assert context == {"hook": "aipager-hook", "tool": ""}


def test_hook_memory_cap_hit_forwards_tool(receiver, run_async):
    """When the datagram carries a ``tool`` field, it's forwarded to
    notify in the context dict."""
    registry, recv, notify_fn = receiver
    registry.get_or_create("claude-jim")
    _send(recv, run_async,
          type="hook_memory_cap_hit",
          session="claude-jim",
          hook="aipager-hook",
          tool="Read")
    notify_fn.assert_awaited_once()
    _, _, context = notify_fn.call_args.args
    assert context == {"hook": "aipager-hook", "tool": "Read"}


def test_hook_memory_cap_hit_missing_tool_defaults_empty(receiver, run_async):
    """No ``tool`` field → context has ``tool=""`` (not missing)."""
    registry, recv, notify_fn = receiver
    registry.get_or_create("claude-jim")
    _send(recv, run_async,
          type="hook_memory_cap_hit",
          session="claude-jim",
          hook="aipager-hook")
    notify_fn.assert_awaited_once()
    _, _, context = notify_fn.call_args.args
    assert context["tool"] == ""


# ---- PostCompact handler -------------------------------------------------

def test_post_compact_clears_compact_started_at(receiver, run_async):
    """PostCompact sets compact_started_at back to None."""
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.compact_started_at = time.monotonic() - 30.0
    _send(recv, run_async,
          hook_event_name="PostCompact",
          session="claude-jim")
    assert sess.compact_started_at is None


def test_post_compact_does_not_fire_compact_done(receiver, run_async):
    """PostCompact must NOT fire compact_done — it lacks the delta context."""
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostCompact",
          session="claude-jim")
    # notify_fn must not have been called at all
    notify_fn.assert_not_awaited()


# ---- StopFailure handler -------------------------------------------------

def test_stop_failure_transitions_to_idle(receiver, run_async):
    """StopFailure moves a BUSY session to IDLE."""
    registry, recv, _ = receiver
    registry.transition("claude-jim", Status.BUSY)
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim")
    assert registry.get("claude-jim").status == Status.IDLE


def test_stop_failure_fires_idle_prompt(receiver, run_async):
    """StopFailure emits an idle_prompt notification."""
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim")
    notify_fn.assert_awaited_once()
    _, event, _ = notify_fn.await_args.args
    assert event == "idle_prompt"


def test_stop_failure_clears_pending_tool_and_compact(receiver, run_async):
    """StopFailure resets both in-flight markers."""
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pending_tool_started_at = time.monotonic() - 5.0
    sess.compact_started_at = time.monotonic() - 10.0
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim")
    assert sess.pending_tool_started_at is None
    assert sess.compact_started_at is None


def test_stop_failure_sets_last_prompt_origin_fail_closed(receiver, run_async):
    """StopFailure resets last_prompt_origin to 'telegram' (fail-closed)."""
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.last_prompt_origin = "terminal"
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim")
    assert sess.last_prompt_origin == "telegram"


def test_stop_failure_empty_summary_when_no_transcript(receiver, run_async):
    """StopFailure with no transcript path yields an empty summary (no crash)."""
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim")
    _, _, ctx = notify_fn.await_args.args
    assert ctx["summary"] == ""


def test_stop_failure_debounce_bypass(receiver, run_async):
    """StopFailure on an already-IDLE session still fires idle_prompt.

    This is the key property: debounce must not suppress finalization.
    When transition() returns None (session already IDLE), we fall back to
    get() and still deliver the notification — stranding must not occur.

    We put the session into IDLE via registry.transition() directly (not via
    a hook datagram, to avoid the fingerprint dedup) and then send a fresh
    StopFailure datagram. transition(IDLE) returns None (same-state no-op),
    so the handler must fall back to get() and still notify.
    """
    registry, recv, notify_fn = receiver
    # Bring session to IDLE directly (bypasses the dedup cache entirely)
    registry.transition("claude-jim", Status.BUSY)
    registry.transition("claude-jim", Status.IDLE)
    assert registry.get("claude-jim").status == Status.IDLE
    notify_fn.reset_mock()

    # Now send a StopFailure — transition(IDLE) returns None (same-state),
    # but the handler must still deliver idle_prompt.
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim",
          extra_field="unique-to-avoid-fingerprint-collision")
    # Must have notified even though session was already IDLE
    notify_fn.assert_awaited_once()
    _, event, _ = notify_fn.await_args.args
    assert event == "idle_prompt"



# ---- MessageDisplay ------------------------------------------------------

def test_message_display_forwards_the_delta(receiver, run_async):
    """The prose chunk reaches the bot as an assistant_text event."""
    _registry, recv, notify_fn = receiver
    _send(recv, run_async, session="claude-x", hook_event_name="MessageDisplay",
          delta="First step: listing the directories.", message_id="m1",
          index=0, final=False)
    notify_fn.assert_awaited_once()
    _sess, event, ctx = notify_fn.await_args.args
    assert event == "assistant_text"
    assert ctx["delta"] == "First step: listing the directories."
    assert ctx["message_id"] == "m1"
    assert ctx["index"] == 0
    assert ctx["final"] is False


def test_message_display_without_a_delta_is_ignored(receiver, run_async):
    _registry, recv, notify_fn = receiver
    _send(recv, run_async, session="claude-x", hook_event_name="MessageDisplay",
          delta="", message_id="m1", index=0, final=True)
    notify_fn.assert_not_awaited()


def test_successive_chunks_of_one_message_all_survive_dedup(receiver, run_async):
    """Chunks differ by index, so the payload-hash dedup must not eat them."""
    _registry, recv, notify_fn = receiver
    for i, text in enumerate(["one ", "two ", "three"]):
        _send(recv, run_async, session="claude-x",
              hook_event_name="MessageDisplay", delta=text,
              message_id="m1", index=i, final=(i == 2))
    assert notify_fn.await_count == 3


def test_identical_repeated_chunk_is_still_deduped(receiver, run_async):
    """A double-wired hook sends the same payload twice — drop the copy."""
    _registry, recv, notify_fn = receiver
    for _ in range(2):
        _send(recv, run_async, session="claude-x",
              hook_event_name="MessageDisplay", delta="same",
              message_id="m1", index=0, final=True)
    assert notify_fn.await_count == 1


def test_message_display_from_active_subagent_is_not_forwarded(receiver, run_async):
    """Orchestrator amendment 7 ("agent activity rows on the busy card"):
    a subagent's own prose is folded under its row like its tool calls,
    not forwarded into the parent's commentary/card — mirrors the
    tool-attribution guard."""
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-x")
    sess.active_subagents["agent-1"] = {
        "type": "explore", "started_at": 0.0, "history_idx": 0,
    }
    _send(recv, run_async, session="claude-x", hook_event_name="MessageDisplay",
          delta="agent's own prose", message_id="m1", index=0, final=False,
          agent_id="agent-1")
    notify_fn.assert_not_awaited()


def test_message_display_with_unknown_agent_id_still_forwards(receiver, run_async):
    """An agent_id that doesn't match a LIVE active_subagents entry (empty,
    already stopped, evicted) is the parent's own prose — forwarded exactly
    as before this feature."""
    _registry, recv, notify_fn = receiver
    _send(recv, run_async, session="claude-x", hook_event_name="MessageDisplay",
          delta="parent prose", message_id="m1", index=0, final=False,
          agent_id="unknown-agent")
    notify_fn.assert_awaited_once()
    _sess, event, ctx = notify_fn.await_args.args
    assert event == "assistant_text"
    assert ctx["delta"] == "parent prose"


# ---- active_subagents size cap ------------------------------------------
#
# `tool_history` has been size-capped at its insertion site since roadmap
# item 2.4; `active_subagents` was only bounded by the 1 h TTL sweep and
# by SubagentStop events actually arriving — TIME and EVENT bounds. A
# single fan-out turn can outrun both: probed on 2026-08-22, the dict
# accepted 5,000 entries and retained all 5,000. These pin the size bound
# and the safety of the eviction it introduces.

def test_active_subagents_never_exceeds_the_cap(receiver, run_async):
    """The reproduce-first test: cap+50 starts through the real datagram
    path must retain exactly the cap. Fails on pre-cap code with 150."""
    from aipager.state import ACTIVE_SUBAGENTS_CAP

    registry, recv, _notify = receiver
    for i in range(ACTIVE_SUBAGENTS_CAP + 50):
        _send(recv, run_async,
              hook_event_name="SubagentStart",
              session="claude-jim",
              agent_id=f"agent-{i}",
              agent_type="explore")
    sess = registry.get("claude-jim")
    assert len(sess.active_subagents) == ACTIVE_SUBAGENTS_CAP


def test_eviction_drops_the_oldest_and_keeps_the_newest(receiver, run_async):
    """Oldest-by-started_at goes first — the same notion of age the TTL
    sweep uses, so the two bounds never disagree about which entry is
    expendable."""
    from aipager.state import ACTIVE_SUBAGENTS_CAP

    registry, recv, _notify = receiver
    total = ACTIVE_SUBAGENTS_CAP + 10
    for i in range(total):
        _send(recv, run_async,
              hook_event_name="SubagentStart",
              session="claude-jim",
              agent_id=f"agent-{i}",
              agent_type="explore")
    sess = registry.get("claude-jim")
    assert f"agent-{total - 1}" in sess.active_subagents, "newest was evicted"
    assert "agent-0" not in sess.active_subagents, "oldest survived"
    assert f"agent-{ACTIVE_SUBAGENTS_CAP}" in sess.active_subagents


def test_late_stop_for_an_evicted_agent_is_a_safe_no_op(receiver, run_async):
    """A SubagentStop whose start was evicted must not raise, must not
    resurrect the entry, and must still fire the notify with
    history_idx=None — the same contract as a stop with no start at all."""
    from aipager.state import ACTIVE_SUBAGENTS_CAP

    registry, recv, notify_fn = receiver
    for i in range(ACTIVE_SUBAGENTS_CAP + 5):
        _send(recv, run_async,
              hook_event_name="SubagentStart",
              session="claude-jim",
              agent_id=f"agent-{i}",
              agent_type="explore")
    _send(recv, run_async,
          hook_event_name="SubagentStop",
          session="claude-jim",
          agent_id="agent-0",           # evicted
          agent_type="explore")
    sess = registry.get("claude-jim")
    assert "agent-0" not in sess.active_subagents, "the stop resurrected it"
    assert len(sess.active_subagents) == ACTIVE_SUBAGENTS_CAP
    _, event, ctx = notify_fn.await_args.args
    assert event == "subagent_stop"
    assert ctx["history_idx"] is None


def test_populations_under_the_cap_are_untouched(receiver, run_async):
    """The ordinary case must not move: start/stop pairing below the cap
    behaves exactly as before the cap existed."""
    registry, recv, _notify = receiver
    for i in range(18):                  # the worst fan-out ever recorded here
        _send(recv, run_async,
              hook_event_name="SubagentStart",
              session="claude-jim",
              agent_id=f"agent-{i}",
              agent_type="explore")
    sess = registry.get("claude-jim")
    assert len(sess.active_subagents) == 18
    _send(recv, run_async,
          hook_event_name="SubagentStop",
          session="claude-jim",
          agent_id="agent-3",
          agent_type="explore")
    assert len(sess.active_subagents) == 17
    assert "agent-3" not in sess.active_subagents
