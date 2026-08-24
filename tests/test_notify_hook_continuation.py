"""``notify_hook._run()`` — the <task-notification> continuation skip
(design.md "model Claude Code background-agent jobs", spec.md's
documented safety leak (c)).

A self-triggered continuation turn must not consume notes or overwrite
the pinned policy snapshot (``_match_and_promote`` must never run) and
must not print the style/reply-context ``additionalContext`` JSON either
— contrast with a non-continuation prompt, which does both when a
matching/outstanding note exists.

Mirrors ``tests/test_dtach_notify_hook_reply_context.py``'s harness
(stdin-driven ``notify_hook.main()``, ``snapshot_path``/``notes_dir``
isolated to ``tmp_path``).
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


def test_continuation_prompt_never_calls_match_and_promote(monkeypatch, tmp_path):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _isolate_snapshot_path(monkeypatch, tmp_path)

    calls = []
    monkeypatch.setattr(
        notify_hook, "_match_and_promote",
        lambda *a, **k: calls.append((a, k)) or ([], []),
    )
    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "<task-notification>\n<task-id>abc</task-id>\ndone.",
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-hiva")
    notify_hook.main()
    assert calls == []


def test_continuation_prompt_prints_no_additional_context(
    monkeypatch, tmp_path, capsys,
):
    """Contrast case: a matching note WOULD normally produce
    additionalContext output for a real prompt (see
    test_dtach_notify_hook_reply_context.py) — a continuation prompt must
    print nothing at all, even with an outstanding note sitting there."""
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps.write_note(
        "claude-hiva", None, None, None,
        msg_id=1, chat_id=1, sender_key=(1, 1),
        body="analyze X", raw_text="analyze X",
        style_text="Keep it short.",
    )
    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "<task-notification>\n<task-id>abc</task-id>\ndone.",
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-hiva")
    notify_hook.main()
    assert capsys.readouterr().out == ""


def test_continuation_prompt_leaves_the_note_outstanding(monkeypatch, tmp_path):
    """Since _match_and_promote never runs, a note that WOULD have
    matched this continuation's own text is left untouched — it stays
    available for the real next prompt."""
    ps = _isolate_snapshot_path(monkeypatch, tmp_path)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    ps.write_note(
        "claude-hiva", None, None, None,
        msg_id=1, chat_id=1, sender_key=(1, 1),
        body="<task-notification>", raw_text="<task-notification>",
    )
    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "<task-notification>\n<task-id>abc</task-id>\ndone.",
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-hiva")
    notify_hook.main()
    assert len(ps.list_outstanding_notes("claude-hiva")) == 1


def test_non_continuation_prompt_still_calls_match_and_promote(
    monkeypatch, tmp_path,
):
    """Contrast case, pinning the skip to the prefix and nothing else: an
    ordinary prompt (no <task-notification> prefix) takes the normal
    path."""
    _isolate_snapshot_path(monkeypatch, tmp_path)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))

    calls = []
    monkeypatch.setattr(
        notify_hook, "_match_and_promote",
        lambda *a, **k: calls.append((a, k)) or ([], []),
    )
    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "analyze X and web-search Y",
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-hiva")
    notify_hook.main()
    assert len(calls) == 1
