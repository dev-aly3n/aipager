"""Tests for aipager.transcript — extracting Claude Code transcript content.

The transcript module is the bridge between Claude Code's JSONL session
log and our notify/resume display. It deliberately does NOT discover paths:
callers pass the hook-stamped ``TrackedSession.transcript_path``. See
``test_module_exposes_no_path_discovery`` at the bottom.
"""

from __future__ import annotations

import json

from aipager import transcript


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


# ---- synthetic no-response placeholder --------------------------------------
#
# Claude Code records `{"model": "<synthetic>", ... "No response requested."}`
# when a turn ends without the model emitting text — reliably reproduced by
# the continuation prompt an auto-compact injects. Published unfiltered it
# became the session's answer in Telegram while the turn was still running.
#
# The same "<synthetic>" model marks API errors (rate limits, expired auth,
# "Prompt is too long"), which the notify path turns into the error card and
# retry button. Hence the discriminator is the isApiErrorMessage flag, not
# the model alone — filtering on model alone would silence every API-failure
# notification.

def _no_response_entry() -> dict:
    """Shape observed in real transcripts, fields that matter preserved."""
    return {
        "type": "assistant",
        "isApiErrorMessage": False,
        "message": {
            "model": "<synthetic>",
            "role": "assistant",
            "stop_reason": "stop_sequence",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "content": [{"type": "text", "text": "No response requested."}],
        },
    }


def _api_error_entry(text: str) -> dict:
    return {
        "type": "assistant",
        "isApiErrorMessage": True,
        "message": {
            "model": "<synthetic>",
            "role": "assistant",
            "stop_reason": "stop_sequence",
            "content": [{"type": "text", "text": text}],
        },
    }


def test_is_no_response_entry_true_for_placeholder():
    assert transcript.is_no_response_entry(_no_response_entry()) is True


def test_is_no_response_entry_false_for_api_error():
    """API errors share the synthetic model but must keep flowing through.

    _detect_api_error builds the error card and the retry button from them;
    filtering these would silence every rate-limit and auth notification.
    """
    entry = _api_error_entry("API Error: 529 Overloaded.")
    assert transcript.is_no_response_entry(entry) is False


def test_is_no_response_entry_false_for_real_assistant_message():
    assert transcript.is_no_response_entry(_entry(["a real answer"])) is False


def test_extract_last_response_returns_empty_for_placeholder(tmp_path):
    """"" (not None) — the turn is over and produced nothing."""
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps(_no_response_entry()) + "\n")
    assert transcript.extract_last_response(str(f)) == ""


def test_extract_last_response_does_not_scan_past_placeholder(tmp_path):
    """The previous turn's answer must not surface as this turn's.

    Skipping the placeholder and continuing would find "OLD ANSWER" — real
    assistant text, but belonging to an earlier turn. Publishing that as the
    reply to the current prompt is a stale answer, which reads as plausible
    and so is harder to catch than an empty reply.
    """
    f = tmp_path / "t.jsonl"
    f.write_text(
        json.dumps(_entry(["OLD ANSWER"])) + "\n"
        + json.dumps({"type": "user", "message": {"content": "Continue"}}) + "\n"
        + json.dumps(_no_response_entry()) + "\n"
    )
    out = transcript.extract_last_response(str(f))
    assert out == "", f"expected no text, got {out!r}"


def test_extract_last_response_still_returns_api_errors(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(json.dumps(_api_error_entry("API Error: 500")) + "\n")
    assert transcript.extract_last_response(str(f)) == "API Error: 500"


def test_extract_last_response_keeps_real_message_quoting_the_phrase(tmp_path):
    """A real answer may quote the placeholder; only synthetic ones are filtered."""
    f = tmp_path / "t.jsonl"
    text = 'Claude Code logs "No response requested." after a compact.'
    f.write_text(json.dumps(_entry([text])) + "\n")
    assert transcript.extract_last_response(str(f)) == text


def test_read_turn_text_skips_placeholder(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [_no_response_entry()])
    text, _ = transcript.read_turn_text(path, 0)
    assert text == "", f"placeholder must not stream into the draft; got {text!r}"


def test_read_turn_text_advances_offset_past_placeholder(tmp_path):
    """Skipping the entry must not stall the offset, or it re-reads forever."""
    path = _write_jsonl_bytes(tmp_path, [_no_response_entry()])
    size = len(open(path, "rb").read())
    text, off = transcript.read_turn_text(path, 0)
    assert text == ""
    assert off == size, f"offset must consume the line; got {off} of {size}"
    # A second call from the returned offset sees nothing new.
    text2, off2 = transcript.read_turn_text(path, off)
    assert text2 == ""
    assert off2 == off


def test_read_turn_text_offset_lands_exactly_on_the_next_line(tmp_path):
    """Sequential reads of an appended-to transcript must not skip a line.

    An offset one byte past the newline eats the next line's leading "{",
    so it fails to parse and is dropped — the streaming draft goes silent
    after its first chunk.
    """
    path = _write_jsonl_bytes(tmp_path, [_entry(["first"])])
    text, off = transcript.read_turn_text(path, 0)
    assert "first" in text
    assert off == len(open(path, "rb").read())
    with open(path, "a") as fh:
        fh.write(json.dumps(_entry(["second"])) + "\n")
    text2, _ = transcript.read_turn_text(path, off)
    assert "second" in text2, "appended line was skipped by a drifting offset"


def test_read_turn_text_keeps_real_text_around_placeholder(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [
        _entry(["real answer"]),
        _no_response_entry(),
    ])
    text, _ = transcript.read_turn_text(path, 0)
    assert "real answer" in text
    assert "No response requested." not in text


def test_read_turn_text_still_streams_api_errors(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [_api_error_entry("API Error: 500")])
    text, _ = transcript.read_turn_text(path, 0)
    assert "API Error: 500" in text


# ---- no path discovery ---------------------------------------------------

def test_module_exposes_no_path_discovery():
    """Regression guard for the cross-session transcript leak.

    ``find_transcript`` resolved a path by globbing ~/.claude/projects across
    every project on the machine and taking the most recently modified JSONL,
    with no check that the file belonged to the asking session. On a host
    running concurrent Claude Code sessions that regularly returned somebody
    else's transcript, which was then streamed or published to Telegram.

    Callers must use the hook-stamped ``TrackedSession.transcript_path`` and
    fail closed when it is empty. This asserts the guessing helper — and the
    module state that made it sticky — stay gone.
    """
    assert not hasattr(transcript, "find_transcript")
    assert not hasattr(transcript, "_path_cache")
    assert not hasattr(transcript, "_PROJECTS_DIR")


# ---- read_turn_stream -------------------------------------------------------

def test_read_turn_stream_preserves_interleaving(tmp_path):
    """Prose and tool calls come back in file order, which is what anchors the card."""
    entry = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "first"},
                {"type": "tool_use", "id": "a", "name": "Bash", "input": {}},
                {"type": "text", "text": "second"},
            ],
            "stop_reason": "tool_use",
        },
    }
    path = _write_jsonl_bytes(tmp_path, [entry])
    items, _off = transcript.read_turn_stream(path, 0)
    assert items == [("text", "first"), ("tool", "Bash"), ("text", "second")]


def test_read_turn_stream_skips_thinking_blocks(tmp_path):
    entry = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "internal"},
                {"type": "text", "text": "shown"},
            ],
            "stop_reason": "end_turn",
        },
    }
    path = _write_jsonl_bytes(tmp_path, [entry])
    items, _off = transcript.read_turn_stream(path, 0)
    assert items == [("text", "shown")]


def test_read_turn_stream_missing_file_returns_empty(tmp_path):
    items, off = transcript.read_turn_stream(str(tmp_path / "nope.jsonl"), 7)
    assert items == []
    assert off == 7
