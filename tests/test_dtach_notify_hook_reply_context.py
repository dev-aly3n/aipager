"""Tests for design.md Part 3 — the ``UserPromptSubmit`` hook now joins
``style_text`` and ``reply_context`` with ``"\\n\\n"``.

Drives the hook exactly as entrypoints.md's stated harness (mirrors
``tests/integration/add-telegram-settings-menu/test_hook_style_contract.py``,
which already covers ``style_text`` alone and must keep passing
unmodified — this file only adds ``reply_context`` coverage).
"""

from __future__ import annotations

import io
import json
import sys
import time

from aipager.dtach import notify_hook


def _set_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _isolate_snapshot_path(monkeypatch, tmp_path):
    from aipager import policy_snapshot
    monkeypatch.setattr(policy_snapshot, "snapshot_path",
                        lambda n: tmp_path / f"{n}.json")
    return policy_snapshot


def _seed_matching_note(policy_snapshot, session, body, *, style_text="",
                        reply_context=""):
    """A per-message note whose ``body`` a submitted prompt of the same
    text will match (queue handoff) — the canonical snapshot this hook
    reads is populated by ITS OWN pick-up match now, not a pre-written
    file, so this stands in for the old direct ``write_snapshot`` seed."""
    policy_snapshot.write_note(
        session, None, None, None,
        msg_id=1, chat_id=1, sender_key=(1, 1),
        body=body, raw_text=body,
        style_text=style_text, reply_context=reply_context,
    )


def test_reply_context_alone_is_printed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    _seed_matching_note(ps, "claude-r1", "do the thing",
                        reply_context="pointing at an older message")
    _set_stdin(monkeypatch,
              '{"hook_event_name":"UserPromptSubmit","prompt":"do the thing"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-r1")
    notify_hook.main()
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["additionalContext"] == (
        "pointing at an older message"
    )


def test_style_text_and_reply_context_joined_with_blank_line(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    _seed_matching_note(ps, "claude-both", "do the thing",
                        style_text="Keep it short.",
                        reply_context="pointing at an older message")
    _set_stdin(monkeypatch,
              '{"hook_event_name":"UserPromptSubmit","prompt":"do the thing"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-both")
    notify_hook.main()
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["additionalContext"] == (
        "Keep it short.\n\npointing at an older message"
    )


def test_both_empty_prints_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    ps.write_snapshot("claude-empty", None, None, None)
    _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-empty")
    notify_hook.main()
    assert capsys.readouterr().out == ""


def test_missing_reply_context_key_old_shape_snapshot_does_not_crash(
    monkeypatch, tmp_path, capsys,
):
    """A NOTE written before reply_context existed (partial-rollout) must
    degrade to using style_text alone, never crash — merge_snapshots
    reads every field via ``.get(..., default)``, so a note missing keys
    entirely (not just an empty string) must still merge cleanly."""
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    from aipager import policy_snapshot as ps
    _isolate_snapshot_path(monkeypatch, tmp_path)
    notes_dir = ps.notes_dir("claude-old-shape")
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "1-aaaa.json").write_text(json.dumps({
        "style_text": "Use simple words.",
        "body": "do the thing",
        "queued_at": time.time(),
    }))
    _set_stdin(monkeypatch,
              '{"hook_event_name":"UserPromptSubmit","prompt":"do the thing"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-old-shape")
    notify_hook.main()  # must not raise
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["additionalContext"] == "Use simple words."


def test_non_string_reply_context_does_not_crash_and_is_dropped(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _isolate_snapshot_path(monkeypatch, tmp_path)
    (tmp_path / "claude-weird-rc.json").write_text(
        json.dumps({"style_text": "", "reply_context": 12345})
    )
    _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-weird-rc")
    notify_hook.main()  # must not raise
    assert capsys.readouterr().out == ""


def test_reply_context_never_leaks_onto_a_pre_tool_use_event(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    ps.write_snapshot("claude-pretool", None, None, None,
                      reply_context="pointing at an older message")
    _set_stdin(monkeypatch, '{"hook_event_name":"PreToolUse","tool_name":"Read"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-pretool")
    notify_hook.main()
    assert capsys.readouterr().out == ""
