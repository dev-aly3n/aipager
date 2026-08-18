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

from aipager.dtach import notify_hook


def _set_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _isolate_snapshot_path(monkeypatch, tmp_path):
    from aipager import policy_snapshot
    monkeypatch.setattr(policy_snapshot, "snapshot_path",
                        lambda n: tmp_path / f"{n}.json")
    return policy_snapshot


def test_reply_context_alone_is_printed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    ps.write_snapshot("claude-r1", None, None, None,
                      reply_context="pointing at an older message")
    _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
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
    ps.write_snapshot("claude-both", None, None, None,
                      style_text="Keep it short.",
                      reply_context="pointing at an older message")
    _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
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
    """A snapshot written before reply_context existed (partial-rollout)
    must degrade to using style_text alone, never crash."""
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _isolate_snapshot_path(monkeypatch, tmp_path)
    (tmp_path / "claude-old-shape.json").write_text(
        json.dumps({"style_text": "Use simple words."})
    )
    _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
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
