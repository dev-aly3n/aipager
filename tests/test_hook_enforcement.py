"""Phase E: PreToolUse hook enforcement (decide())."""

from __future__ import annotations

import json

from aipager.dtach import enforce
from aipager.dtach.enforce import decide


def _transcript(tmp_path, marker: bool):
    p = tmp_path / (f"t_{'tg' if marker else 'term'}.jsonl")
    text = "[via Telegram · @bob · role:user]\nread it" if marker else "read it"
    p.write_text(json.dumps({"type": "user", "message": {"content": text}}) + "\n")
    return str(p)


def _snap(tmp_path, session, **over):
    base = {
        "bypass_safety": False, "deny_tools": [], "allow_tools": [],
        "deny_paths_no_access": ["~/.claude/**"],
        "deny_paths_no_write": [], "deny_bash_patterns": [r"\bclaude\b"],
    }
    base.update(over)
    (tmp_path / f"{session}.json").write_text(json.dumps(base))


def _data(tmp_path, **over):
    d = {
        "hook_event_name": "PreToolUse",
        "session": "claude-x__g100",
        "tool_name": "Read",
        "tool_input": {"file_path": "~/.claude/projects/o.jsonl"},
        "transcript_path": _transcript(tmp_path, marker=True),
    }
    d.update(over)
    return d


def _patch_snap(tmp_path, monkeypatch):
    monkeypatch.setattr(enforce, "read_snapshot",
                        lambda s: json.loads((tmp_path / f"{s}.json").read_text())
                        if (tmp_path / f"{s}.json").exists() else None)


def test_terminal_origin_allows(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(tmp_path, transcript_path=_transcript(tmp_path, marker=False))
    assert decide(d) is None  # terminal → unrestricted


def test_telegram_b1_read_blocked(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    block = decide(_data(tmp_path))
    assert block and "protected path" in block["reason"]


def test_owner_bypass(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100", bypass_safety=True)
    assert decide(_data(tmp_path)) is None


def test_missing_snapshot_failclosed(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)  # no snapshot file written
    block = decide(_data(tmp_path))  # B1 Read of transcript
    assert block is not None  # fail-closed deny


def test_bash_nested_claude_blocked(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(tmp_path, tool_name="Bash",
              tool_input={"command": "claude --resume abc"})
    block = decide(d)
    assert block and "blocked pattern" in block["reason"]


def test_normal_edit_allowed(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(tmp_path, tool_name="Edit",
              tool_input={"file_path": "/home/u/proj/app.js"})
    assert decide(d) is None


def test_non_pretooluse_ignored(tmp_path):
    assert decide({"hook_event_name": "Stop"}) is None


def test_deny_json_shape():
    payload = json.loads(enforce.deny_decision_json("X on Y"))
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "X on Y" in hso["permissionDecisionReason"]


# ---- regression: 2nd+ tool call in a Telegram turn (tool_result skip) ----

def _multi_tool_transcript(tmp_path, marker: bool, name="t_multi.jsonl"):
    """A turn where the marked (or unmarked) prompt is followed by a
    tool_result entry — which Claude records as type:"user". Mimics the
    transcript state when the hook fires for the SECOND tool call."""
    p = tmp_path / name
    prompt = ("[via Telegram · @bob · role:user]\ncheck claude version"
              if marker else "check claude version")
    lines = [
        {"type": "user", "message": {"content": prompt}},
        {"type": "assistant",
         "message": {"content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "claude --version"}}]}},
        # tool result — type:"user", no marker (this is what tricked the bug)
        {"type": "user",
         "message": {"content": [{"type": "tool_result",
                                  "content": "blocked"}]}},
        {"type": "assistant",
         "message": {"content": [{"type": "text", "text": "let me retry"}]}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


def test_origin_skips_tool_result_entries(tmp_path):
    # The governing prompt carries the marker → telegram, even though the
    # last type:"user" entry is a marker-less tool_result.
    assert enforce._origin_from_transcript(
        _multi_tool_transcript(tmp_path, marker=True)) == "telegram"
    assert enforce._origin_from_transcript(
        _multi_tool_transcript(tmp_path, marker=False, name="t_term.jsonl")
    ) == "terminal"


def test_second_tool_call_bash_still_blocked(tmp_path, monkeypatch):
    """Regression: a 2nd Bash call in a Telegram turn (after a tool_result)
    must still be blocked. Pre-fix this leaked through as 'terminal'."""
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Bash",
        tool_input={"command": "cat $(which claude); npm ls -g | grep claude"},
        transcript_path=_multi_tool_transcript(tmp_path, marker=True),
    )
    block = decide(d)
    assert block and "blocked pattern" in block["reason"]


def test_second_tool_call_read_still_blocked(tmp_path, monkeypatch):
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Read",
        tool_input={"file_path": "~/.claude/projects/o.jsonl"},
        transcript_path=_multi_tool_transcript(tmp_path, marker=True),
    )
    block = decide(d)
    assert block and "protected path" in block["reason"]


def test_second_tool_call_terminal_still_allowed(tmp_path, monkeypatch):
    """A terminal turn (no marker) stays unrestricted across tool calls."""
    _patch_snap(tmp_path, monkeypatch)
    _snap(tmp_path, "claude-x__g100")
    d = _data(
        tmp_path,
        tool_name="Bash",
        tool_input={"command": "claude --version"},
        transcript_path=_multi_tool_transcript(
            tmp_path, marker=False, name="t_term2.jsonl"),
    )
    assert decide(d) is None
