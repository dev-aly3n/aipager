"""Tests for transcript.last_assistant_preview — short last-message snippet."""

import json

from aipager.transcript import last_assistant_preview


def _write_jsonl(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_empty_path_returns_empty():
    assert last_assistant_preview("") == ""


def test_missing_file_returns_empty(tmp_path):
    assert last_assistant_preview(str(tmp_path / "nope.jsonl")) == ""


def test_no_assistant_entries_returns_empty(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "permission-mode", "permissionMode": "default"},
    ])
    assert last_assistant_preview(str(p)) == ""


def test_basic_preview_uses_last_assistant_text(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        {"type": "user", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Old reply"},
        ]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Newest reply"},
        ]}},
    ])
    assert last_assistant_preview(str(p)) == "Newest reply"


def test_collapses_whitespace(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "one\n\ntwo   three\tfour"},
        ]}},
    ])
    assert last_assistant_preview(str(p)) == "one two three four"


def test_truncates_to_max_chars(tmp_path):
    p = tmp_path / "t.jsonl"
    body = "x" * 500
    _write_jsonl(p, [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": body},
        ]}},
    ])
    out = last_assistant_preview(str(p), max_chars=50)
    assert len(out) == 50
    assert out.endswith("…")


def test_ignores_tool_use_blocks(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "text", "text": "I ran ls"},
        ]}},
    ])
    assert last_assistant_preview(str(p)) == "I ran ls"
