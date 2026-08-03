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


def test_turn_complete_skips_messageless_sidecar_entries(tmp_path):
    # Newer claude-code appends bookkeeping records after the final
    # assistant message. None carry a "message" field, so they must be
    # skipped when walking the tail — otherwise a finished turn is never
    # detected and the busy bubble animates forever.
    path = _write_jsonl(tmp_path, [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done."}],
            "stop_reason": "end_turn"}},
        {"type": "system"},
        {"type": "last-prompt", "lastPrompt": "hello", "sessionId": "s1"},
        {"type": "ai-title", "aiTitle": "Greeting", "sessionId": "s1"},
        {"type": "mode", "mode": "default", "sessionId": "s1"},
        {"type": "permission-mode", "permissionMode": "bypassPermissions",
         "sessionId": "s1"},
    ])
    assert transcript.turn_appears_complete(path) is True


def test_turn_incomplete_on_unknown_message_bearing_entry(tmp_path):
    # An unknown entry type that DOES carry a message must still hit the
    # conservative branch — never cut a possibly-live turn short.
    path = _write_jsonl(tmp_path, [
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Done."}],
            "stop_reason": "end_turn"}},
        {"type": "mystery-turn", "message": {"role": "assistant",
                                             "content": "..."}},
    ])
    assert transcript.turn_appears_complete(path) is False


# ----- _strip_leaked_tool_xml (defensive sanitizer) --------------------

def test_strip_leaked_tool_xml_removes_complete_invoke_block():
    text = (
        "Here's what I found:\n"
        "<invoke name=\"Bash\">\n"
        "<parameter name=\"command\">ls -la</parameter>\n"
        "</invoke>\n"
        "That should do it."
    )
    out = transcript._strip_leaked_tool_xml(text)
    assert "<invoke" not in out
    assert "<parameter" not in out
    assert "Here's what I found:" in out
    assert "That should do it." in out


def test_strip_leaked_tool_xml_removes_orphan_parameter_after_truncation():
    # Truncated emission: invoke closed but parameter block left dangling.
    text = ("done\n"
            "<parameter name=\"command\">rm -rf tmp</parameter>")
    out = transcript._strip_leaked_tool_xml(text)
    assert "<parameter" not in out
    assert "done" in out


def test_strip_leaked_tool_xml_removes_function_calls_wrapper():
    text = ("<function_calls>\n"
            "<invoke name=\"Bash\"><parameter name=\"cmd\">ls</parameter>"
            "</invoke>\n"
            "</function_calls>\n"
            "Result: nothing to see.")
    out = transcript._strip_leaked_tool_xml(text)
    assert "<function_calls" not in out
    assert "<invoke" not in out
    assert "Result: nothing to see." in out


def test_strip_leaked_tool_xml_removes_orphan_opening_tag():
    # Emission cut off mid-tool-use: only the opening tag survived.
    text = "checking\n<invoke name=\"Bash\">\n"
    out = transcript._strip_leaked_tool_xml(text)
    assert "<invoke" not in out
    assert "checking" in out


def test_strip_leaked_tool_xml_passthrough_when_no_xml():
    text = "Perfectly normal reply with < and > but no invoke tags."
    assert transcript._strip_leaked_tool_xml(text) == text


def test_strip_leaked_tool_xml_only_xml_returns_empty():
    text = ("<invoke name=\"Bash\">"
            "<parameter name=\"cmd\">ls</parameter>"
            "</invoke>")
    assert transcript._strip_leaked_tool_xml(text) == ""


def test_strip_leaked_tool_xml_empty_input_returns_empty():
    assert transcript._strip_leaked_tool_xml("") == ""


def test_extract_last_response_scrubs_leaked_invoke_xml(tmp_path):
    # Real observed pattern: bare "court" word followed by leaked
    # invoke XML — the assistant typed its tool markup as plain text.
    leaked = (
        "court\n"
        "<invoke name=\"Bash\">\n"
        "<parameter name=\"command\">"
        "tmux send-keys -t main:mohandes C-u"
        "</parameter>\n"
        "</invoke>"
    )
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": leaked}],
            "stop_reason": "end_turn",
        },
    }) + "\n")
    out = transcript.extract_last_response(str(p))
    assert out is not None
    assert "<invoke" not in out
    assert "<parameter" not in out
    assert "tmux send-keys" not in out


# ---- fence-aware sanitizer -----------------------------------------------

def test_strip_leaked_tool_xml_preserves_invoke_inside_xml_fence():
    text = (
        "Here's the structure:\n"
        "```xml\n"
        "<invoke name=\"Bash\">\n"
        "<parameter name=\"command\">ls</parameter>\n"
        "</invoke>\n"
        "```\n"
        "Any questions?"
    )
    out = transcript._strip_leaked_tool_xml(text)
    assert "<invoke name=\"Bash\">" in out
    assert "<parameter name=\"command\">ls</parameter>" in out
    assert "</invoke>" in out
    assert "Here's the structure:" in out
    assert "Any questions?" in out


def test_strip_leaked_tool_xml_preserves_invoke_inside_bare_fence():
    text = (
        "Look:\n"
        "```\n"
        "<invoke name=\"Bash\"><parameter name=\"cmd\">ls</parameter>"
        "</invoke>\n"
        "```"
    )
    out = transcript._strip_leaked_tool_xml(text)
    assert "<invoke name=\"Bash\">" in out
    assert "<parameter" in out


def test_strip_leaked_tool_xml_strips_prose_xml_preserves_fenced():
    # Prose leak (should be stripped) + fenced example (should survive)
    # in the SAME response.
    text = (
        "court\n"
        "<invoke name=\"Bash\">"
        "<parameter name=\"command\">rm -rf tmp</parameter>"
        "</invoke>\n"
        "\n"
        "Here's how one looks:\n"
        "```xml\n"
        "<invoke name=\"Read\">"
        "<parameter name=\"file_path\">/x</parameter>"
        "</invoke>\n"
        "```"
    )
    out = transcript._strip_leaked_tool_xml(text)
    # The prose leak (rm -rf tmp) must be gone.
    assert "rm -rf tmp" not in out
    # The fenced example (Read /x) must survive verbatim.
    assert "<invoke name=\"Read\">" in out
    assert "/x" in out
    assert "```xml" in out


def test_strip_leaked_tool_xml_preserves_parameter_in_json_fence():
    text = (
        "For the tool schema:\n"
        "```json\n"
        "{\"parameters\": \"<parameter name=\\\"foo\\\">bar</parameter>\"}\n"
        "```"
    )
    out = transcript._strip_leaked_tool_xml(text)
    assert "<parameter" in out
    assert "```json" in out


def test_strip_leaked_tool_xml_unbalanced_fence_conservatively_preserves():
    # Unclosed fence: everything after the opening ``` is treated as
    # code and left untouched. Fail-open: prefer real content over
    # accidental clipping.
    text = (
        "reference:\n"
        "```xml\n"
        "<invoke name=\"Bash\"><parameter name=\"cmd\">ls</parameter>"
        "</invoke>\n"
        "(fence not closed)"
    )
    out = transcript._strip_leaked_tool_xml(text)
    assert "<invoke" in out
    assert "(fence not closed)" in out


def test_strip_leaked_tool_xml_fast_path_no_op_on_clean_text():
    # No tags → return input unchanged (no split/join round-trip cost).
    text = "Normal reply with no tags anywhere."
    assert transcript._strip_leaked_tool_xml(text) == text


# ---- read_turn_text ---------------------------------------------------------

def _entry(text_blocks: list[str]) -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": t} for t in text_blocks],
            "stop_reason": "end_turn",
        },
    }


def _write_jsonl_bytes(tmp_path, lines: list[dict]) -> str:
    p = tmp_path / "t.jsonl"
    content = "\n".join(json.dumps(x) for x in lines) + "\n"
    p.write_bytes(content.encode("utf-8"))
    return str(p)


def test_read_turn_text_empty_path():
    text, offset = transcript.read_turn_text("", 0)
    assert text == ""
    assert offset == 0


def test_read_turn_text_missing_file(tmp_path):
    text, offset = transcript.read_turn_text(str(tmp_path / "nope.jsonl"), 0)
    assert text == ""
    assert offset == 0


def test_read_turn_text_empty_file(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_bytes(b"")
    text, offset = transcript.read_turn_text(str(p), 0)
    assert text == ""
    assert offset == 0


def test_read_turn_text_reads_assistant_blocks(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [_entry(["Hello", "World"])])
    text, new_off = transcript.read_turn_text(path, 0)
    assert "Hello" in text
    assert "World" in text
    assert new_off > 0


def test_read_turn_text_skips_non_assistant(tmp_path):
    lines = [
        {"type": "user", "message": {"content": "prompt"}},
        _entry(["Answer"]),
        {"type": "system"},
    ]
    path = _write_jsonl_bytes(tmp_path, lines)
    text, _ = transcript.read_turn_text(path, 0)
    assert "Answer" in text
    assert "prompt" not in text


def test_read_turn_text_advances_offset(tmp_path):
    """After consuming one entry, the new offset lets the next call skip it."""
    path = _write_jsonl_bytes(tmp_path, [_entry(["First"])])
    _, off1 = transcript.read_turn_text(path, 0)
    # Second call from the advanced offset sees nothing new.
    text2, off2 = transcript.read_turn_text(path, off1)
    assert text2 == ""
    assert off2 == off1


def test_read_turn_text_partial_line_not_consumed(tmp_path):
    """A trailing line without \\n is not consumed and offset not advanced past it."""
    p = tmp_path / "t.jsonl"
    full_line = (json.dumps(_entry(["Block one"])) + "\n").encode("utf-8")
    partial = json.dumps(_entry(["Block two"]))  # NO trailing newline
    p.write_bytes(full_line + partial.encode("utf-8"))

    text1, off1 = transcript.read_turn_text(str(p), 0)
    # Only "Block one" from the complete line
    assert "Block one" in text1
    assert "Block two" not in text1

    # Simulate the partial line being completed later.
    p.write_bytes(full_line + (partial + "\n").encode("utf-8"))
    text2, off2 = transcript.read_turn_text(str(p), off1)
    assert "Block two" in text2
    assert off2 > off1


def test_read_turn_text_multiple_blocks_joined_with_blank_lines(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [
        _entry(["First block"]),
        _entry(["Second block"]),
    ])
    text, _ = transcript.read_turn_text(path, 0)
    assert "First block" in text
    assert "Second block" in text
    # Blocks are joined by double newline
    assert "\n\n" in text


def test_read_turn_text_strips_leaked_xml(tmp_path):
    leaked = "Answer\n<invoke name=\"Bash\"><parameter name=\"cmd\">ls</parameter></invoke>"
    path = _write_jsonl_bytes(tmp_path, [_entry([leaked])])
    text, _ = transcript.read_turn_text(path, 0)
    assert "Answer" in text
    assert "<invoke" not in text


def test_read_turn_text_no_assistant_returns_empty(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [
        {"type": "user", "message": {"content": "go"}},
    ])
    text, off = transcript.read_turn_text(path, 0)
    assert text == ""
    # Offset still advances past the consumed complete line.
    assert off > 0


def test_read_turn_text_cross_turn_isolation(tmp_path):
    """Given a transcript with a previous turn already written, a call
    starting from that turn's byte size returns nothing new — the previous
    turn's text is never streamed as the current turn's.

    This is the regression test for success criterion 13 in design.md
    (same class of bug as the false-idle-recovery incident fixed in 0.4.26).
    """
    prev_turn_lines = [
        _entry(["Previous turn text"]),
    ]
    content = ("\n".join(json.dumps(x) for x in prev_turn_lines) + "\n").encode("utf-8")
    p = tmp_path / "t.jsonl"
    p.write_bytes(content)
    # The offset is seeded to the current file size (as _send_busy_and_animate does).
    offset_at_turn_start = len(content)
    # No new assistant blocks have been written yet.
    text, new_off = transcript.read_turn_text(str(p), offset_at_turn_start)
    assert text == ""
    assert new_off == offset_at_turn_start
