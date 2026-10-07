"""``transcript.turn_appears_complete`` on hostile lines.

The startup recovery reads it to decide whether a card's turn still runs
(roadmap 8.101), and the session monitor's idle recovery reads it every
scan. A line that is not a JSON object, an entry whose ``message`` is not
an object, a line nested past the parser's depth, a non-string text block
or undecodable bytes must never raise out of it.
"""

from __future__ import annotations

import json

import pytest

from aipager.transcript import turn_appears_complete


# ── the transcript reader the restart's adoption uses ────────────────────

@pytest.mark.parametrize("line", [
    "42", '"a line"', "null", "[1, 2]", "true",
    "[" * 100_000 + "]" * 100_000,
], ids=["number", "string", "null", "list", "bool", "deep-nesting"])
def test_a_non_object_line_is_skipped(tmp_path, line):
    """Skipped like a torn line: the entry before it decides."""
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {
        "stop_reason": "end_turn", "content": []}}) + "\n" + line + "\n")
    assert turn_appears_complete(str(p)) is True


@pytest.mark.parametrize("message", ["hi", [1, 2], 5],
                         ids=["str", "list", "int"])
def test_a_turn_entry_of_an_unknown_shape_is_not_a_turn_end(tmp_path,
                                                            message):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {
        "stop_reason": "end_turn", "content": []}}) + "\n"
        + json.dumps({"type": "assistant", "message": message}) + "\n")
    assert turn_appears_complete(str(p)) is False


def test_a_non_string_text_block_does_not_raise(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {"content": [
        {"type": "text", "text": 5}, {"type": "text", "text": ["x"]}]}})
        + "\n")
    assert turn_appears_complete(str(p)) is False


def test_invalid_utf8_does_not_raise(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(json.dumps({"type": "assistant", "message": {
        "stop_reason": "end_turn", "content": []}}).encode() + b"\n"
        + b"\xc3\x28\xff\n")
    assert turn_appears_complete(str(p)) is True
