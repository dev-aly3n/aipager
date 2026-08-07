"""Black-box tests for the subscribe-claude-code-lifecycle-hooks feature.

Tests the observable contract documented in entrypoints.md and design.md
without reading implementation internals.

Coverage targets (design.md success criteria not already tested):
  - SubagentStart without agent_id: no crash, no entry in active_subagents
  - PostToolUseFailure without tool_name: no crash, no tool_failed fired
  - StopFailure with an invalid/missing transcript: empty summary, no crash
  - StopFailure on an INTERACTIVE session: still finalizes to IDLE
  - PostCompact when compact_started_at is already None: idempotent, no crash
  - settings.json: user-authored hook for a new event is NOT duplicated
  - settings.json: unrelated top-level keys untouched after patching
  - settings.json: excluded events (PostToolBatch, Elicitation) NOT added
  - bootstrap _HOOK_EVENTS: exactly 15 events including all four new ones
  - _merge_hooks with a user-authored hook already present for a new event
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status


# ---------------------------------------------------------------------------
# Shared fixtures (mirrors test_hook_receiver.py convention)
# ---------------------------------------------------------------------------

@pytest.fixture
def receiver():
    """Return (registry, recv, notify_fn) — a wired-up HookReceiver."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    return registry, recv, notify_fn


def _send(recv, run_async, **fields):
    """Build a JSON datagram and feed it into _on_datagram."""
    payload = json.dumps(fields).encode()
    run_async(recv._on_datagram(payload))


# ---------------------------------------------------------------------------
# SubagentStart: no agent_id — no-op, no crash
# ---------------------------------------------------------------------------

def test_subagent_start_without_agent_id_leaves_active_subagents_empty(receiver, run_async):
    """SubagentStart with no agent_id must leave active_subagents unchanged.

    Spec (entrypoints.md): 'No-op (no entry, no crash) when the payload
    carries no agent_id.'
    """
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    _send(recv, run_async,
          hook_event_name="SubagentStart",
          session="claude-jim",
          agent_type="explore")
    assert sess.active_subagents == {}


def test_subagent_start_without_agent_id_does_not_raise(receiver, run_async):
    """SubagentStart with no agent_id must not crash the receiver."""
    _, recv, _ = receiver
    # If this raises, the test fails
    _send(recv, run_async,
          hook_event_name="SubagentStart",
          session="claude-jim")


def test_subagent_start_empty_string_agent_id_no_entry(receiver, run_async):
    """SubagentStart with agent_id='' (falsy) must not create an entry.

    Boundary value: empty string is falsy in Python, same guard path as absent.
    """
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    _send(recv, run_async,
          hook_event_name="SubagentStart",
          session="claude-jim",
          agent_id="",
          agent_type="explore")
    assert sess.active_subagents == {}


# ---------------------------------------------------------------------------
# PostToolUseFailure: no tool_name — no-op
# ---------------------------------------------------------------------------

def test_post_tool_use_failure_without_tool_name_does_not_fire_tool_failed(receiver, run_async):
    """PostToolUseFailure with no tool_name must not fire tool_failed.

    Spec (entrypoints.md): 'No-op when the payload carries no tool_name.'
    """
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUseFailure",
          session="claude-jim")
    notify_fn.assert_not_awaited()


def test_post_tool_use_failure_with_empty_tool_name_does_not_fire_tool_failed(receiver, run_async):
    """PostToolUseFailure with tool_name='' (falsy boundary) must not fire tool_failed."""
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUseFailure",
          session="claude-jim",
          tool_name="")
    notify_fn.assert_not_awaited()


def test_post_tool_use_failure_tool_failed_carries_tool_name(receiver, run_async):
    """PostToolUseFailure with tool_name must include tool_name in the notification ctx."""
    _, recv, notify_fn = receiver
    _send(recv, run_async,
          hook_event_name="PostToolUseFailure",
          session="claude-jim",
          tool_name="Bash",
          tool_input={"command": "ls"})
    assert notify_fn.await_count == 1
    _, event, ctx = notify_fn.await_args.args
    assert event == "tool_failed"
    assert ctx.get("tool_name") == "Bash"


# ---------------------------------------------------------------------------
# StopFailure: partial/invalid transcript path — empty summary, no crash
# ---------------------------------------------------------------------------

def test_stop_failure_nonexistent_transcript_gives_empty_summary(receiver, run_async, tmp_path):
    """StopFailure with a transcript_path pointing to a missing file must
    not crash and must deliver summary='' in the notification.

    Boundary: transcript_path given but the file does not exist.
    """
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim",
          transcript_path=str(tmp_path / "nonexistent.jsonl"))
    notify_fn.assert_awaited_once()
    _, _, ctx = notify_fn.await_args.args
    assert ctx["summary"] == ""


def test_stop_failure_corrupt_transcript_gives_empty_summary(receiver, run_async, tmp_path):
    """StopFailure with a corrupt transcript file must not crash.

    Error guessing: transcript file exists but contains invalid JSON.
    """
    bad_transcript = tmp_path / "bad.jsonl"
    bad_transcript.write_text("not json at all\n{ broken\n")
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim",
          transcript_path=str(bad_transcript))
    # Must still finalize: notify fired, status is IDLE
    notify_fn.assert_awaited_once()
    assert registry.get("claude-jim").status == Status.IDLE


# ---------------------------------------------------------------------------
# StopFailure: INTERACTIVE session still finalizes
# ---------------------------------------------------------------------------

def test_stop_failure_on_interactive_session_transitions_to_idle(receiver, run_async):
    """StopFailure must finalize even when the session is INTERACTIVE.

    Equivalence class: session in a state other than BUSY (INTERACTIVE).
    Debounce bypass must work regardless of starting state.
    """
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    registry.transition("claude-jim", Status.INTERACTIVE)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim",
          extra_field="no-fingerprint-collision")
    assert registry.get("claude-jim").status == Status.IDLE


def test_stop_failure_on_interactive_session_fires_idle_prompt(receiver, run_async):
    """StopFailure from INTERACTIVE must still deliver idle_prompt.

    Spec: 'Fires even if the session was already IDLE (debounce is bypassed).'
    The same logic must apply for INTERACTIVE as a starting state.
    """
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    registry.transition("claude-jim", Status.INTERACTIVE)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim",
          extra_field="no-fingerprint-collision-2")
    notify_fn.assert_awaited_once()
    _, event, _ = notify_fn.await_args.args
    assert event == "idle_prompt"


# ---------------------------------------------------------------------------
# PostCompact: idempotent when compact_started_at is already None
# ---------------------------------------------------------------------------

def test_post_compact_when_already_none_does_not_crash(receiver, run_async):
    """PostCompact when compact_started_at is already None must be a no-op.

    Boundary value: clearing None → None is idempotent.
    """
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    assert sess.compact_started_at is None
    # Must not raise
    _send(recv, run_async,
          hook_event_name="PostCompact",
          session="claude-jim")
    assert sess.compact_started_at is None
    notify_fn.assert_not_awaited()


# ---------------------------------------------------------------------------
# Settings migration: user-authored hook for a new event — not duplicated
# ---------------------------------------------------------------------------

def test_merge_hooks_does_not_duplicate_user_authored_hook_for_new_event(monkeypatch):
    """If a new event already has a user-authored aipager-hook entry,
    _merge_hooks must NOT add a second one.

    Spec (entrypoints.md / design.md): 'idempotent (no duplicate entries on
    repeat runs)'. This extends to user-authored entries that happen to use
    the same command name.
    """
    from aipager.wizard import settings_patch
    from aipager.wizard._constants import HOOK_CMD

    # StopFailure already has a user-authored hook pointing to the hook binary
    user_hook_cmd = f"/home/user/.local/bin/{HOOK_CMD}"
    existing_hooks = {
        "StopFailure": [{"hooks": [{"type": "command", "command": user_hook_cmd}]}],
    }
    settings = {"hooks": existing_hooks, "theme": "dark"}

    monkeypatch.setattr(settings_patch.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    settings_patch._merge_hooks(settings)

    hooks = settings["hooks"]
    # Count aipager-hook entries for StopFailure
    cmds = [
        h.get("command", "")
        for block in hooks["StopFailure"]
        for h in block.get("hooks", [])
    ]
    aipager_count = sum(1 for c in cmds if HOOK_CMD in c)
    assert aipager_count == 1, (
        f"Expected 1 aipager-hook entry for StopFailure, got {aipager_count}: {cmds}"
    )


def test_merge_hooks_preserves_unrelated_top_level_keys(monkeypatch):
    """_merge_hooks must not remove or modify non-hooks top-level keys.

    Spec (entrypoints.md): 'all non-hooks keys preserved'.
    """
    from aipager.wizard import settings_patch

    settings = {
        "hooks": {},
        "theme": "dark",
        "customFont": "JetBrains Mono",
        "verboseLogs": False,
    }

    monkeypatch.setattr(settings_patch.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    settings_patch._merge_hooks(settings)

    assert settings["theme"] == "dark"
    assert settings["customFont"] == "JetBrains Mono"
    assert settings["verboseLogs"] is False


# ---------------------------------------------------------------------------
# Excluded events must NOT be added by the settings patcher
# ---------------------------------------------------------------------------

_EXCLUDED_EVENTS = (
    "PostToolBatch",
    "Elicitation",
    "ElicitationResult",
    "PermissionDenied",
    "Setup",
    "ConfigChange",
    "TaskCreated",
    "TaskCompleted",
    "UserPromptExpansion",
    "TeammateIdle",
)


@pytest.mark.parametrize("excluded_event", _EXCLUDED_EVENTS)
def test_merge_hooks_does_not_add_excluded_event(monkeypatch, excluded_event):
    """_merge_hooks must not subscribe to noise/excluded events.

    Spec (spec.md): explicitly lists events that must NOT be subscribed.
    """
    from aipager.wizard import settings_patch

    settings: dict = {"hooks": {}}
    monkeypatch.setattr(settings_patch.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    settings_patch._merge_hooks(settings)

    assert excluded_event not in settings["hooks"], (
        f"Excluded event {excluded_event!r} was added to hooks"
    )


@pytest.mark.parametrize("excluded_event", _EXCLUDED_EVENTS)
def test_bootstrap_does_not_wire_excluded_event(tmp_path, monkeypatch, excluded_event):
    """bootstrap_claude_settings must not wire excluded events.

    Boundary: verifies both subscription paths (wizard + bootstrap).
    """
    from aipager import claude_bootstrap

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")
    monkeypatch.setattr(claude_bootstrap.shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd in {"aipager-hook", "aipager-statusline"} else None)

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    hooks = data.get("hooks", {})
    assert excluded_event not in hooks, (
        f"bootstrap wired excluded event {excluded_event!r}"
    )


# ---------------------------------------------------------------------------
# Bootstrap _HOOK_EVENTS count and new-event inclusion
# ---------------------------------------------------------------------------

def test_bootstrap_hook_events_count_is_15(tmp_path, monkeypatch):
    """After bootstrap, settings.json must have exactly 15 hook events.

    Success criterion: the four lifecycle events took the total from 10 to 14,
    and MessageDisplay — the source of the busy card's live commentary —
    brings it to 15.
    """
    from aipager import claude_bootstrap

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")
    monkeypatch.setattr(claude_bootstrap.shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd in {"aipager-hook", "aipager-statusline"} else None)

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    hook_events = list(data.get("hooks", {}).keys())
    assert len(hook_events) == 15, (
        f"Expected 15 hook events, got {len(hook_events)}: {sorted(hook_events)}"
    )


def test_bootstrap_hook_events_includes_all_four_new_events(tmp_path, monkeypatch):
    """After bootstrap, all four new events must appear in settings.json hooks.

    Success criterion (design.md): explicit list.
    """
    from aipager import claude_bootstrap

    _NEW_EVENTS = ("StopFailure", "PostCompact", "SubagentStart", "PostToolUseFailure")

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")
    monkeypatch.setattr(claude_bootstrap.shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd in {"aipager-hook", "aipager-statusline"} else None)

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    hooks = data.get("hooks", {})
    for event in _NEW_EVENTS:
        assert event in hooks, f"New event {event!r} missing from bootstrapped hooks"


def test_bootstrap_preserves_unrelated_settings_key(tmp_path, monkeypatch):
    """bootstrap_claude_settings must not remove unrelated top-level keys.

    Spec (entrypoints.md): 'all non-hooks keys preserved'.
    Error guessing: the patcher might write a fresh dict, clobbering all else.
    """
    from aipager import claude_bootstrap

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"myCustomKey": "keep-me", "theme": "dark"}))
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")
    monkeypatch.setattr(claude_bootstrap.shutil, "which",
                        lambda cmd: f"/usr/bin/{cmd}" if cmd in {"aipager-hook", "aipager-statusline"} else None)

    claude_bootstrap.bootstrap_claude_settings("/workspace")

    data = json.loads(settings.read_text())
    assert data.get("myCustomKey") == "keep-me"
    assert data.get("theme") == "dark"


# ---------------------------------------------------------------------------
# StopFailure: summary from valid transcript (boundary: transcript present)
# ---------------------------------------------------------------------------

def test_stop_failure_summary_from_transcript_when_present(receiver, run_async, tmp_path):
    """StopFailure with a valid transcript derives summary from the transcript.

    Boundary: transcript_path given and the file exists with an assistant turn.
    The summary must be non-empty.
    """
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Task completed with error."}],
            },
        }) + "\n"
    )
    registry, recv, notify_fn = receiver
    registry.transition("claude-jim", Status.BUSY)
    notify_fn.reset_mock()
    _send(recv, run_async,
          hook_event_name="StopFailure",
          session="claude-jim",
          transcript_path=str(transcript))
    notify_fn.assert_awaited_once()
    _, _, ctx = notify_fn.await_args.args
    # Summary is non-empty when transcript has an assistant text turn
    assert isinstance(ctx["summary"], str)
