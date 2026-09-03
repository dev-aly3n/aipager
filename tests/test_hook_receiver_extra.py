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


# ---- design.md "model Claude Code background-agent jobs" ---------------

def test_pretooluse_rebusy_preserves_wall_stamp_when_agents_open(receiver, run_async):
    """A PreToolUse arriving while NOT already BUSY, WITH real background
    evidence (an open active_subagents entry — a background agent's own
    tool call re-entering after an interim Stop), must not restamp
    busy_started_wall — it passes preserve_job_state=True."""
    registry, recv, _ = receiver
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-hiva",
          prompt="[via Telegram msg=1]\nanalyze X")
    sess = registry.get("claude-hiva")
    original_wall = sess.busy_started_wall
    assert original_wall > 0
    registry.transition("claude-hiva", Status.IDLE)
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": 0.0}
    _send(recv, run_async, hook_event_name="PreToolUse",
          session="claude-hiva", tool_name="Bash",
          tool_input={"command": "ls"})
    assert sess.status == Status.BUSY
    assert sess.busy_started_wall == original_wall  # NOT restamped


def test_pretooluse_rebusy_restamps_wall_when_no_agents_open(receiver, run_async):
    """Contrast case (review-1#rev-iter1-001): a PreToolUse arriving while
    NOT already BUSY, with NO active_subagents evidence, is a genuinely
    fresh terminal turn whose UserPromptSubmit datagram may simply have
    been dropped (lossy UDP, daemon restart) — this must restamp
    busy_started_wall exactly as it did before this feature existed, or
    session_monitor's idle-recovery written_this_turn guard is defeated."""
    registry, recv, _ = receiver
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-hiva",
          prompt="[via Telegram msg=1]\nanalyze X")
    sess = registry.get("claude-hiva")
    original_wall = sess.busy_started_wall
    assert original_wall > 0
    registry.transition("claude-hiva", Status.IDLE)
    sess.busy_started_wall = 111.0  # sentinel, distinguishable from "now"
    assert sess.active_subagents == {}  # no background evidence
    _send(recv, run_async, hook_event_name="PreToolUse",
          session="claude-hiva", tool_name="Bash",
          tool_input={"command": "ls"})
    assert sess.status == Status.BUSY
    assert sess.busy_started_wall != 111.0  # RESTAMPED fresh
    assert sess.busy_started_wall != original_wall


def test_pretooluse_rebusy_notifies_tool_use(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-hiva")
    sess.status = Status.IDLE
    notify_fn.reset_mock()
    _send(recv, run_async, hook_event_name="PreToolUse",
          session="claude-hiva", tool_name="Bash",
          tool_input={"command": "ls"})
    _, event, _ = notify_fn.await_args.args
    assert event == "tool_use"


def test_continuation_user_prompt_submit_fires_job_continuation(receiver, run_async):
    """A <task-notification> UserPromptSubmit dispatches "job_continuation"
    (NOT "user_prompt_submit"), even when the session is already BUSY (the
    background agent's own PreToolUse having re-entered BUSY first — the
    common case per the hiva sequence, entrypoints.md step 10)."""
    registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-hiva",
          prompt="[via Telegram msg=1]\nanalyze X")
    sess = registry.get("claude-hiva")
    assert sess.status == Status.BUSY
    notify_fn.reset_mock()
    _send(recv, run_async, hook_event_name="PreToolUse",
          session="claude-hiva", tool_name="Bash",
          tool_input={"command": "ls"})  # no-op transition, already BUSY
    notify_fn.reset_mock()
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-hiva",
          prompt="<task-notification>\n<task-id>abc</task-id>\ndone.")
    notify_fn.assert_awaited_once()
    fired_sess, event, ctx = notify_fn.await_args.args
    assert event == "job_continuation"
    assert ctx == {}
    assert fired_sess.name == "claude-hiva"
    assert sess.status == Status.BUSY


def test_continuation_user_prompt_submit_does_not_tag_origin(receiver, run_async):
    """The continuation prompt carries no Telegram marker of its own —
    the ORIGINAL prompt's origin must survive, never flipped to
    "terminal" (spec.md's documented safety leak)."""
    registry, recv, _ = receiver
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-hiva",
          prompt="[via Telegram msg=1]\nanalyze X")
    sess = registry.get("claude-hiva")
    assert sess.last_prompt_origin == "telegram"
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-hiva",
          prompt="<task-notification>\n<task-id>abc</task-id>\ndone.")
    assert sess.last_prompt_origin == "telegram"  # unchanged


def test_continuation_user_prompt_submit_preserves_busy_started_wall(receiver, run_async):
    registry, recv, _ = receiver
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-hiva",
          prompt="[via Telegram msg=1]\nanalyze X")
    sess = registry.get("claude-hiva")
    original_wall = sess.busy_started_wall
    registry.transition("claude-hiva", Status.IDLE)
    _send(recv, run_async, hook_event_name="UserPromptSubmit",
          session="claude-hiva",
          prompt="<task-notification>\n<task-id>abc</task-id>\ndone.")
    assert sess.status == Status.BUSY
    assert sess.busy_started_wall == original_wall


# ---- PermissionRequest: always_available + detail (2.1.259 dialog) ---------

def _perm_request(recv, run_async, **extra):
    _send(recv, run_async, hook_event_name="PermissionRequest", session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls -la /tmp && echo hi", "description": "List tmp"},
          **extra)


def test_permission_request_with_suggestions_marks_always_available(receiver, run_async):
    """The dialog's "don't ask again" row exists exactly when the hook
    carries permission_suggestions — the flag mirrors that."""
    registry, recv, notify_fn = receiver
    _perm_request(recv, run_async,
                  permission_suggestions=[{"type": "addRules"}, {"type": "addDirectories"}])
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is True
    assert ctx["tool_info"]["detail"] == "ls -la /tmp && echo hi"
    # the timeline row keeps Claude's description; only the card gains detail
    assert ctx["tool_info"]["summary"] == "Bash: List tmp"
    assert registry.get("claude-jim").status == Status.INTERACTIVE


@pytest.mark.parametrize("payload", [
    {"permission_suggestions": []},
    {},
    {"permission_suggestions": "nope"},
    {"permission_suggestions": None},
])
def test_permission_request_without_suggestions_is_not_always_available(
        receiver, run_async, payload):
    """Empty, missing or malformed suggestions → False, never truthy: on
    2.1.259 the second row is then "switch to auto mode"."""
    registry, recv, notify_fn = receiver
    _perm_request(recv, run_async, **payload)
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is False


def test_notification_fallback_leaves_always_available_unknown(receiver, run_async):
    registry, recv, notify_fn = receiver
    _send(recv, run_async, notification_type="permission_prompt", session="claude-jim",
          message="Claude needs permission to use Bash")
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"].get("always_available") is None


def test_tool_detail_is_the_command_or_path_only():
    assert hr._tool_detail("Bash", {"command": "  ls -la  ", "description": "x"}) == "ls -la"
    assert hr._tool_detail("Edit", {"file_path": "/a/b.py", "old_string": "x"}) == "/a/b.py"
    assert hr._tool_detail("Read", {"file_path": "/a/b.py"}) == "/a/b.py"
    assert hr._tool_detail("NotebookEdit", {"notebook_path": "/n.ipynb"}) == "/n.ipynb"
    assert hr._tool_detail("WebSearch", {"query": "x"}) == ""
    assert hr._tool_detail("Bash", {"command": 12}) == ""


def test_bash_with_non_string_command_is_never_always_available(receiver, run_async):
    """The one suppression case the hook CAN see (rev-iter1-002): Claude
    hides both extra rows when the command is withheld/not a string, so
    suggestions alone must not make Allow-always navigate."""
    registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="PermissionRequest", session="claude-jim",
          tool_name="Bash", tool_input={"command": None, "description": "x"},
          permission_suggestions=[{"type": "addRules"}])
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is False


# ---- reads outside the working directory (2.1.259 "block outside reads?") --

def _read_request(recv, run_async, path, *, cwd="/work", suggestions=None):
    _send(recv, run_async, hook_event_name="PermissionRequest", session="claude-jim",
          cwd=cwd, tool_name="Read", tool_input={"file_path": path},
          permission_suggestions=[{"type": "addDirectories"}] if suggestions is None else suggestions)


def test_read_outside_cwd_never_offers_allow_always(receiver, run_async):
    """Slot 2 of that dialog is "No, BLOCK reads outside the working
    directories from now on" — a persistent, every-project setting — and
    the hook cannot tell it from an ordinary Read prompt. Suggestions
    present or not, the flag must be False."""
    registry, recv, notify_fn = receiver
    _read_request(recv, run_async, "/tmp/aipager-files/shot.png")
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is False
    assert ctx["tool_info"]["detail"] == "/tmp/aipager-files/shot.png"


def test_read_inside_cwd_keeps_allow_always(receiver, run_async):
    registry, recv, notify_fn = receiver
    _read_request(recv, run_async, "/work/src/app.py")
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is True


def test_edit_outside_cwd_is_not_affected(receiver, run_async):
    """Only read-only file access (Read/Grep/Glob/LSP) draws the block
    dialog; an Edit outside cwd gets the ordinary rule row and keeps
    Allow-always."""
    registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="PermissionRequest", session="claude-jim",
          cwd="/work", tool_name="Edit",
          tool_input={"file_path": "/tmp/x.py", "old_string": "a", "new_string": "b"},
          permission_suggestions=[{"type": "addDirectories"}])
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is True


def test_read_with_unknown_cwd_is_treated_as_outside(receiver, run_async):
    registry, recv, notify_fn = receiver
    _read_request(recv, run_async, "/work/src/app.py", cwd="")
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is False


@pytest.mark.parametrize("path,cwd,outside", [
    ("/work/a.py", "/work", False),
    ("/work/sub/../a.py", "/work/", False),
    ("/work2/a.py", "/work", True),          # sibling with a shared prefix
    ("/tmp/a.py", "/work", True),
    ("src/a.py", "/work", False),            # relative → inside
    ("", "/work", True),
    (None, "/work", True),
    ("/work/a.py", "", True),
])
def test_outside_working_dir_edges(path, cwd, outside):
    assert hr._outside_working_dir(path, cwd) is outside


@pytest.mark.parametrize("tool,tool_input", [
    ("Grep", {"pattern": "x", "path": "/etc"}),
    ("Glob", {"pattern": "*.py", "path": "/tmp"}),
    ("LSP", {"file_path": "/tmp/a.py"}),
])
def test_grep_glob_lsp_outside_cwd_never_offer_allow_always(receiver, run_async, tool, tool_input):
    """rev-iter3-001: Claude's settings schema names "Read, Grep, Glob,
    LSP" as the tools the outside-reads block applies to."""
    registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="PermissionRequest", session="claude-jim",
          cwd="/work", tool_name=tool, tool_input=tool_input,
          permission_suggestions=[{"type": "addDirectories"}])
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is False


@pytest.mark.parametrize("tool,tool_input", [
    ("Grep", {"pattern": "x"}),                       # no path → the cwd itself
    ("Glob", {"pattern": "*.py", "path": "/work/src"}),
    ("Grep", {"pattern": "x", "path": "src"}),        # relative → inside
])
def test_grep_glob_inside_cwd_keep_allow_always(receiver, run_async, tool, tool_input):
    registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="PermissionRequest", session="claude-jim",
          cwd="/work", tool_name=tool, tool_input=tool_input,
          permission_suggestions=[{"type": "addRules"}])
    _, _, ctx = notify_fn.await_args.args
    assert ctx["tool_info"]["always_available"] is True
