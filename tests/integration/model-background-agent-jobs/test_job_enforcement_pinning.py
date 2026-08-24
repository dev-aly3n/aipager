"""design.md success criteria (requirement 4):
- A `<task-notification>` UserPromptSubmit never flips
  `enforce._origin_from_transcript`'s verdict to "terminal" for a session
  whose real prompt carried the Telegram marker.
- `_turn_already_blocked` stays sticky across a task-notification entry
  and still resets correctly at the true prior-turn boundary.

Fixture format follows entrypoints.md's literal shape and
tests/test_hook_enforcement.py's pre-existing marker convention
(``"[via Telegram · @bob · role:user]\\n..."``), independently
reconstructed here (that file's own new job-related cases are the
Developer's, not read for this suite).
"""

from __future__ import annotations

import json

from aipager.dtach import enforce
from aipager.dtach.enforce import decide

TASK_NOTIFICATION_ENTRY = {
    "type": "user",
    "message": {
        "role": "user",
        "content": "<task-notification>\n<task-id>abc123</task-id>\n"
                   "Background agent finished.",
    },
}


def _write(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


def _real_prompt(marker: bool, text="analyze X and web-search Y"):
    prompt = f"[via Telegram · @bob · role:user]\n{text}" if marker else text
    return {"type": "user", "message": {"role": "user", "content": prompt}}


def _tool_result(text="some tool output"):
    return {"type": "user",
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "content": text}]}}


def _assistant(text="ok"):
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


def _blocked_tool_result():
    return {"type": "user",
            "message": {"role": "user",
                        "content": [{"type": "tool_result",
                                    "content": "aipager safety policy: Bash "
                                               "command blocked by safety "
                                               "policy"}]}}


# ---- _origin_from_transcript: task-notification entry is transparent -----

def test_origin_skips_task_notification_real_prompt_below_governs(tmp_path):
    """entrypoints.md's literal fixture: real prompt -> tool_results ->
    task-notification -> more tool_results. The real prompt below must
    govern; expected 'telegram'."""
    path = _write(tmp_path, "t1.jsonl", [
        _real_prompt(marker=True),
        _tool_result(),
        TASK_NOTIFICATION_ENTRY,
        _tool_result(),
    ])
    assert enforce._origin_from_transcript(path) == "telegram"


def test_origin_skips_task_notification_terminal_prompt_stays_terminal(tmp_path):
    """Symmetric equivalence case: an unmarked (terminal) original prompt
    must stay 'terminal' across the same shape — the skip must not
    accidentally upgrade a terminal turn's restrictions."""
    path = _write(tmp_path, "t2.jsonl", [
        _real_prompt(marker=False),
        _tool_result(),
        TASK_NOTIFICATION_ENTRY,
        _tool_result(),
    ])
    assert enforce._origin_from_transcript(path) == "terminal"


def test_origin_fail_closed_when_task_notification_is_the_only_user_entry(tmp_path):
    """Fail-closed expectation: a transcript whose ONLY type:"user" entry
    is a task-notification (no real prompt anywhere — e.g. a truncated
    or synthetic-only log) must be transparent all the way through and
    land on the SAME fail-closed default as an empty/missing transcript
    (pinned by the pre-existing suite: 'telegram', the RESTRICTIVE
    origin) rather than defaulting to the unrestricted 'terminal'."""
    path = _write(tmp_path, "t3.jsonl", [TASK_NOTIFICATION_ENTRY])
    assert enforce._origin_from_transcript(path) == "telegram"


def test_origin_enforcement_still_blocks_after_a_continuation(tmp_path, monkeypatch):
    """End-to-end (via decide()): a Telegram-origin session must still
    have a normally-blocked operation blocked even after a
    task-notification continuation entry sits between the real prompt
    and the current tool call — this is the actual safety gap spec.md
    documents, closed."""
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: json.loads((tmp_path / f"{s}.json").read_text())
                        if (tmp_path / f"{s}.json").exists() else None)
    (tmp_path / "claude-hiva.json").write_text(json.dumps({
        "bypass_safety": False, "deny_tools": [], "allow_tools": [],
        "deny_paths_no_access": ["~/.claude/**"],
        "deny_paths_no_write": [], "deny_bash_patterns": [r"\bclaude\b"],
    }))
    transcript = _write(tmp_path, "t4.jsonl", [
        _real_prompt(marker=True),
        TASK_NOTIFICATION_ENTRY,
        _assistant(),
    ])
    data = {
        "hook_event_name": "PreToolUse",
        "session": "claude-hiva",
        "tool_name": "Read",
        "tool_input": {"file_path": "~/.claude/projects/o.jsonl"},
        "transcript_path": transcript,
    }
    block = decide(data)
    assert block is not None, (
        "a protected-path Read was ALLOWED after a task-notification "
        "continuation for a session whose real prompt was Telegram-"
        "marked — the origin misread as 'terminal' safety gap spec.md "
        "documents is back")
    assert "protected path" in block["reason"]


# ---- _turn_already_blocked: sticky across continuation, resets at the ---
# ---- true prior-turn boundary --------------------------------------------

def test_turn_already_blocked_survives_the_continuation(tmp_path):
    """entrypoints.md's literal fixture: denial marker BEFORE a
    task-notification entry, no new real prompt after it — the block
    must still be seen as covering the current (open) turn."""
    path = _write(tmp_path, "t5.jsonl", [
        _real_prompt(marker=True),
        _blocked_tool_result(),
        _assistant("blocked, dodging"),
        TASK_NOTIFICATION_ENTRY,
        _assistant("still trying"),
    ])
    assert enforce._turn_already_blocked(path) is True


def test_turn_already_blocked_resets_at_a_true_new_prompt_after_continuation(
        tmp_path):
    """The other half of the pair: once a genuinely NEW real user prompt
    follows the task-notification entry, that prompt is the new turn
    boundary — an OLDER block from before the continuation must not leak
    forward into the new turn."""
    path = _write(tmp_path, "t6.jsonl", [
        _real_prompt(marker=True, text="first request"),
        _blocked_tool_result(),
        _assistant("blocked, dodging"),
        TASK_NOTIFICATION_ENTRY,
        _assistant("finishing up turn one"),
        _real_prompt(marker=True, text="second, unrelated request"),
        _tool_result("benign output"),
    ])
    assert enforce._turn_already_blocked(path) is False


def test_turn_already_blocked_false_when_only_task_notification_present(tmp_path):
    path = _write(tmp_path, "t7.jsonl", [TASK_NOTIFICATION_ENTRY])
    assert enforce._turn_already_blocked(path) is False
