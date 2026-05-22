"""Tests for aipager.transcript — finding + extracting Claude Code
transcript content.

The transcript module is the bridge between Claude Code's JSONL session
log and our notify/resume display.
"""

from __future__ import annotations

import json
import time

import pytest

from aipager import transcript


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """Clear the module-level path cache between tests."""
    monkeypatch.setattr(transcript, "_path_cache", {})


# ---- find_transcript -----------------------------------------------------

def test_find_transcript_returns_none_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(transcript, "_PROJECTS_DIR", tmp_path / "nope")
    assert transcript.find_transcript("claude-jim") is None


def test_find_transcript_no_files_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript, "_PROJECTS_DIR", tmp_path)
    assert transcript.find_transcript("claude-jim") is None


def test_find_transcript_picks_recent_file(tmp_path, monkeypatch):
    """A fresh JSONL (mtime within 5s) is selected."""
    monkeypatch.setattr(transcript, "_PROJECTS_DIR", tmp_path)
    proj = tmp_path / "proj-1"
    proj.mkdir()
    jsonl = proj / "UUID.jsonl"
    jsonl.write_text('{"type":"assistant","message":{"content":[]}}\n')
    # Force a recent mtime
    now = time.time()
    import os
    os.utime(jsonl, (now, now))
    out = transcript.find_transcript("claude-jim")
    assert out and out.endswith("UUID.jsonl")


def test_find_transcript_stale_file_returns_none(tmp_path, monkeypatch):
    """A JSONL older than 5s is ignored."""
    monkeypatch.setattr(transcript, "_PROJECTS_DIR", tmp_path)
    proj = tmp_path / "proj-1"
    proj.mkdir()
    jsonl = proj / "old.jsonl"
    jsonl.write_text("{}\n")
    import os
    old_ts = time.time() - 100
    os.utime(jsonl, (old_ts, old_ts))
    assert transcript.find_transcript("claude-jim") is None


def test_find_transcript_uses_cache_when_recent(tmp_path, monkeypatch):
    """If a recent cache entry exists and the file is still on disk,
    short-circuit."""
    monkeypatch.setattr(transcript, "_PROJECTS_DIR", tmp_path)
    f = tmp_path / "cached.jsonl"
    f.write_text("{}\n")
    monkeypatch.setattr(transcript, "_path_cache",
                        {"claude-jim": (str(f), time.time())})
    assert transcript.find_transcript("claude-jim") == str(f)


def test_find_transcript_cache_miss_when_file_gone(tmp_path, monkeypatch):
    """Cached path but file is deleted → cache is bypassed, fallback scan."""
    monkeypatch.setattr(transcript, "_PROJECTS_DIR", tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    bogus = "/nonexistent/path.jsonl"
    monkeypatch.setattr(transcript, "_path_cache",
                        {"claude-jim": (bogus, time.time())})
    # No files in _PROJECTS_DIR → returns None
    assert transcript.find_transcript("claude-jim") is None


def test_find_transcript_falls_back_to_cache_when_files_stale(tmp_path, monkeypatch):
    """Stale on-disk file + valid cache → returns cache."""
    monkeypatch.setattr(transcript, "_PROJECTS_DIR", tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    # Stale file
    stale = proj / "stale.jsonl"
    stale.write_text("{}\n")
    import os
    old_ts = time.time() - 100
    os.utime(stale, (old_ts, old_ts))
    # Cached path that DOES exist
    cached = tmp_path / "cached.jsonl"
    cached.write_text("{}\n")
    monkeypatch.setattr(transcript, "_path_cache",
                        {"claude-jim": (str(cached), 0.0)})
    # The cache time is 0 (very old), so it shouldn't be the "recent" cache
    # hit — but the fallback path uses it
    assert transcript.find_transcript("claude-jim") == str(cached)


# ---- extract_last_response ----------------------------------------------

def test_extract_last_response_missing_file_returns_none(tmp_path):
    out = transcript.extract_last_response(str(tmp_path / "nope.jsonl"))
    assert out is None


def test_extract_last_response_no_assistant_returns_none(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "user", "message": {}}) + "\n"
        + json.dumps({"type": "permission-mode"}) + "\n"
    )
    assert transcript.extract_last_response(str(f)) is None


def test_extract_last_response_returns_text_blocks(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "user", "message": {"content": "go"}}) + "\n"
        + json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": "World"},
            ]},
        }) + "\n"
    )
    out = transcript.extract_last_response(str(f))
    assert "Hello" in out
    assert "World" in out


def test_extract_last_response_picks_last_assistant(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "OLD"},
        ]}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "NEW"},
        ]}}) + "\n"
    )
    assert transcript.extract_last_response(str(f)) == "NEW"


def test_extract_last_response_skips_corrupt_lines(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        "{\nNOT JSON\n"
        + json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "ok"},
        ]}}) + "\n"
    )
    assert transcript.extract_last_response(str(f)) == "ok"


def test_extract_last_response_assistant_with_no_text_blocks(tmp_path):
    """Tool-only assistant turn shouldn't be returned."""
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {}},
        ]}}) + "\n"
    )
    assert transcript.extract_last_response(str(f)) is None


def test_extract_last_response_handles_str_blocks(tmp_path):
    """Some content blocks are plain strings (not dicts)."""
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            "plain string content",
        ]}}) + "\n"
    )
    out = transcript.extract_last_response(str(f))
    assert out == "plain string content"


# ---- last_assistant_preview ---------------------------------------------

def test_last_assistant_preview_empty_path():
    assert transcript.last_assistant_preview("") == ""


def test_last_assistant_preview_missing_file(tmp_path):
    assert transcript.last_assistant_preview(str(tmp_path / "no.jsonl")) == ""


def test_last_assistant_preview_collapses_whitespace(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "line1\n\nline2   line3"},
        ]}}) + "\n"
    )
    assert transcript.last_assistant_preview(str(f)) == "line1 line2 line3"


def test_last_assistant_preview_truncates_with_ellipsis(tmp_path):
    f = tmp_path / "t.jsonl"
    long_text = "x" * 500
    f.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": long_text},
        ]}}) + "\n"
    )
    out = transcript.last_assistant_preview(str(f), max_chars=50)
    assert len(out) == 50
    assert out.endswith("…")


def test_last_assistant_preview_under_limit_no_ellipsis(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "short"},
        ]}}) + "\n"
    )
    assert transcript.last_assistant_preview(str(f), max_chars=200) == "short"


# ----- turn_appears_complete (idle-recovery fallback detector) -----

def _write_jsonl(tmp_path, lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


def test_turn_complete_on_assistant_end_turn(tmp_path):
    path = _write_jsonl(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hi!"}],
            "stop_reason": "end_turn"}},
        {"type": "system"},  # trailing hook/bookkeeping records are skipped
        {"type": "system"},
    ])
    assert transcript.turn_appears_complete(path) is True


def test_turn_incomplete_on_tool_use(tmp_path):
    path = _write_jsonl(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "do it"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Bash"}],
            "stop_reason": "tool_use"}},
    ])
    assert transcript.turn_appears_complete(path) is False


def test_turn_incomplete_while_thinking(tmp_path):
    # Last meaningful entry is the user prompt — the agent hasn't replied yet.
    path = _write_jsonl(tmp_path, [
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "earlier"}],
            "stop_reason": "end_turn"}},
        {"type": "user", "message": {"role": "user", "content": "next question"}},
    ])
    assert transcript.turn_appears_complete(path) is False


def test_turn_incomplete_after_tool_result(tmp_path):
    path = _write_jsonl(tmp_path, [
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Read"}],
            "stop_reason": "tool_use"}},
        {"type": "user", "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": "data"}]}},
    ])
    assert transcript.turn_appears_complete(path) is False


def test_turn_complete_on_user_interrupt(tmp_path):
    path = _write_jsonl(tmp_path, [
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Bash"}],
            "stop_reason": "tool_use"}},
        {"type": "user", "message": {
            "role": "user",
            "content": "[Request interrupted by user for tool use]"}},
    ])
    assert transcript.turn_appears_complete(path) is True


def test_turn_complete_missing_path_is_false():
    assert transcript.turn_appears_complete("") is False
    assert transcript.turn_appears_complete("/no/such/file.jsonl") is False
