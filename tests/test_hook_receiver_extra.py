"""Additional hook_receiver tests: start(), token-usage extraction,
permission_prompt fallback, statusline edge cases."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    return registry, recv, notify_fn


def _send(recv, run_async, **fields):
    payload = json.dumps(fields).encode()
    run_async(recv._on_datagram(payload))


# ---- _extract_token_usage ----------------------------------------------

def test_extract_token_usage_missing_file(tmp_path):
    assert hr._extract_token_usage(str(tmp_path / "no.jsonl")) is None


def test_extract_token_usage_no_assistant_returns_none(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps({"type": "user", "message": {}}) + "\n")
    assert hr._extract_token_usage(str(f)) is None


def test_extract_token_usage_returns_pct(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps({
        "type": "assistant",
        "message": {"usage": {
            "input_tokens": 10000,
            "cache_read_input_tokens": 5000,
            "cache_creation_input_tokens": 5000,
        }},
    }) + "\n")
    out = hr._extract_token_usage(str(f))
    assert out["total_input"] == 20000
    assert out["context_pct"] == 10  # 20000 / 200000 = 10%


def test_extract_token_usage_assistant_without_usage(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps({"type": "assistant", "message": {}}) + "\n")
    assert hr._extract_token_usage(str(f)) is None


def test_extract_token_usage_zero_tokens(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps({
        "type": "assistant",
        "message": {"usage": {"input_tokens": 0}},
    }) + "\n")
    out = hr._extract_token_usage(str(f))
    assert out["context_pct"] == 0


def test_extract_token_usage_skips_malformed_lines(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        "not json\n"
        + json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 1000}},
        }) + "\n"
    )
    out = hr._extract_token_usage(str(f))
    assert out is not None


# ---- permission_prompt fallback ---------------------------------------

def test_permission_prompt_fallback_extracts_tool_from_transcript(receiver, run_async, tmp_path):
    registry, recv, notify_fn = receiver
    f = tmp_path / "transcript.jsonl"
    f.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        ]},
    }) + "\n")
    _send(recv, run_async,
          notification_type="permission_prompt",
          session="claude-jim",
          transcript_path=str(f),
          message="Claude needs permission to use Bash")
    assert registry.get("claude-jim").status == Status.INTERACTIVE
    _, event, ctx = notify_fn.await_args.args
    assert event == "permission_prompt"
    assert ctx["tool_info"]["name"] == "Bash"


def test_permission_prompt_fallback_uses_hook_name_when_mismatch(receiver, run_async, tmp_path):
    registry, recv, notify_fn = receiver
    # Transcript has Read but hook says permission for Bash
    f = tmp_path / "transcript.jsonl"
    f.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "rm"}},
        ]},
    }) + "\n")
    _send(recv, run_async,
          notification_type="permission_prompt",
          session="claude-jim",
          transcript_path=str(f),
          message="Claude needs permission to use Bash")
    # Got tool_info for Bash (from specific lookup)
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["name"] == "Bash"


def test_permission_prompt_no_transcript_falls_back_to_hook_name(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          notification_type="permission_prompt",
          session="claude-jim",
          message="Claude needs permission to use Bash")
    _, _, ctx = notify_fn.await_args.args
    # Hook name is used as the tool name
    assert ctx["tool_info"]["name"] == "Bash"


def test_permission_request_no_tool_name_drops(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PermissionRequest",
          session="claude-jim",
          tool_name="")  # empty tool_name → drop
    notify_fn.assert_not_awaited()


# ---- statusline edge cases --------------------------------------------

def test_statusline_high_pct_triggers_context_warning_once(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async,
          type="statusline",
          session="claude-jim",
          context_pct=85)
    # Send a second statusline with high pct — warning should NOT fire again
    notify_fn.reset_mock()
    _send(recv, run_async,
          type="statusline",
          session="claude-jim",
          context_pct=88)
    # No second context_warning notify (sess.compact_warned is set)
    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "context_warning" not in events


def test_statusline_low_pct_resets_compact_warned(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.compact_warned = True
    _send(recv, run_async,
          type="statusline",
          session="claude-jim",
          context_pct=10)
    assert sess.compact_warned is False


def test_statusline_compact_done_fallback_via_statusline(receiver, run_async):
    """When pre_compact_pct > 0 and statusline pct drops below 30%,
    fire compact_done."""
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pre_compact_pct = 80
    _send(recv, run_async,
          type="statusline",
          session="claude-jim",
          context_pct=5)
    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "compact_done" in events


def test_statusline_null_values_coerced_to_zero(receiver, run_async):
    """An explicit null in statusline data (early ticks) should be safely coerced."""
    registry, recv, _ = receiver
    _send(recv, run_async,
          type="statusline",
          session="claude-jim",
          context_pct=None,
          total_output=None,
          cost_usd=None,
          lines_added=None,
          lines_removed=None)
    sess = registry.get("claude-jim")
    assert sess.last_token_pct == 0
    assert sess.last_cost_usd == 0


# ---- PreCompact ---------------------------------------------------------

# ---- origin tagging (Phase D) ------------------------------------------

def test_userpromptsubmit_marker_sets_telegram(receiver, run_async):
    registry, recv, _ = receiver
    registry.get_or_create("claude-jim")
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-jim",
          prompt="[via Telegram · @bob]\nfix the bug")
    assert registry.get("claude-jim").last_prompt_origin == "telegram"


def test_userpromptsubmit_markerless_sets_terminal(receiver, run_async):
    registry, recv, _ = receiver
    registry.get_or_create("claude-jim")
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-jim", prompt="fix the bug")
    assert registry.get("claude-jim").last_prompt_origin == "terminal"


def test_userpromptsubmit_empty_payload_unchanged(receiver, run_async):
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.last_prompt_origin = "terminal"
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-jim")  # no prompt field
    assert registry.get("claude-jim").last_prompt_origin == "terminal"


def test_stop_resets_origin_failclosed(receiver, run_async):
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.last_prompt_origin = "terminal"
    _send(recv, run_async, hook_event_name="Stop", session="claude-jim",
          last_assistant_message="done")
    assert registry.get("claude-jim").last_prompt_origin == "telegram"


def test_session_end_resets_origin_failclosed(receiver, run_async):
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.last_prompt_origin = "terminal"
    _send(recv, run_async, hook_event_name="SessionEnd", session="claude-jim",
          source="clear")
    assert registry.get("claude-jim").last_prompt_origin == "telegram"


def test_pre_compact_uses_cached_token_pct(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.last_token_pct = 75
    _send(recv, run_async,
          hook_event_name="PreCompact",
          session="claude-jim",
          trigger="manual")
    assert sess.pre_compact_pct == 75


def test_pre_compact_falls_back_to_sl_tokens(receiver, run_async):
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    # sess.last_token_pct is 0
    _send(recv, run_async,
          hook_event_name="PreCompact",
          session="claude-jim",
          trigger="auto",
          sl_tokens={"context_pct": 88})
    assert sess.pre_compact_pct == 88


# ---- SessionStart compact source ---------------------------------------

def test_session_start_compact_post_pct_stale_defers(receiver, run_async):
    """If post-compact pct hasn't dropped below pre-compact, defer the
    notification."""
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pre_compact_pct = 80
    _send(recv, run_async,
          hook_event_name="SessionStart",
          session="claude-jim",
          source="compact",
          sl_tokens={"context_pct": 85})  # still high
    # Pre-compact preserved for next chance
    assert sess.pre_compact_pct == 80
    # No compact_done event
    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "compact_done" not in events
