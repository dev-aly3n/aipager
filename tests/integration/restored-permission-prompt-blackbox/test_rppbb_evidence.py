"""Black-box rows for ``aipager.transcript.saved_prompt_evidence``, the
exported transcript check (entrypoints.md; design.md section 4 and
success criteria 5 and 6).

Equivalence classes over hand-written JSONL, with the timestamp boundary
(``>= since_wall``) probed on both sides, and the error cases (missing
file, a directory, an empty path, a torn line) guessed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from aipager.transcript import saved_prompt_evidence

SINCE = 1_800_000_000.0
TUID = "toolu_01"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _write(path, *entries, raw: str = "") -> str:
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
        fh.write(raw)
    os.utime(path, (SINCE - 3600, SINCE - 3600))
    return str(path)


def _user_text(t, text="hello"):
    return {"type": "user", "timestamp": _iso(t),
            "message": {"role": "user", "content": text}}


def _tool_result(t, tuid=TUID):
    return {"type": "user", "timestamp": _iso(t), "message": {
        "role": "user", "content": [{"type": "tool_result",
                                     "tool_use_id": tuid, "content": "ok"}]}}


def _tool_use(t, tuid=TUID, name="Bash", extra_text: bool = False):
    content = [{"type": "tool_use", "id": tuid, "name": name, "input": {}}]
    if extra_text:
        content.insert(0, {"type": "text", "text": "let me run it"})
    return {"type": "assistant", "timestamp": _iso(t), "message": {
        "id": "m1", "role": "assistant", "stop_reason": "tool_use",
        "content": content}}


def _assistant_text(t):
    return {"type": "assistant", "timestamp": _iso(t), "message": {
        "id": "m2", "role": "assistant", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "done"}]}}


def _system(t, subtype):
    return {"type": "system", "subtype": subtype, "timestamp": _iso(t)}


# ── errors and missing input ─────────────────────────────────────────────

def test_missing_file_is_none(tmp_path):
    assert saved_prompt_evidence(str(tmp_path / "nope.jsonl"), SINCE, TUID,
                                 "Bash") == "none"


def test_empty_path_is_unreadable():
    assert saved_prompt_evidence("", SINCE, TUID, "Bash") == "unreadable"


def test_directory_is_unreadable(tmp_path):
    d = tmp_path / "dir.jsonl"
    d.mkdir()
    assert saved_prompt_evidence(str(d), SINCE, TUID, "Bash") == "unreadable"


def test_empty_file_is_none(tmp_path):
    p = _write(tmp_path / "t.jsonl")
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"


def test_torn_last_line_does_not_raise(tmp_path):
    p = _write(tmp_path / "t.jsonl", _user_text(SINCE - 10),
               raw='{"type": "assistant", "message": {"id"')
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"


def test_non_object_lines_do_not_raise(tmp_path):
    p = _write(tmp_path / "t.jsonl", raw="[1, 2]\n42\n\"x\"\nnull\n")
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"


# ── "answered": a tool_result for the saved id, at any time ──────────────

@pytest.mark.parametrize("offset", [-86400.0, -1.0, 0.0, 30.0])
def test_tool_result_for_saved_id_is_answered(tmp_path, offset):
    p = _write(tmp_path / "t.jsonl", _tool_result(SINCE + offset))
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "answered"


def test_tool_result_for_another_id_before_since_is_none(tmp_path):
    p = _write(tmp_path / "t.jsonl", _tool_result(SINCE - 5, "toolu_other"))
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"


def test_tool_result_with_no_saved_id_before_since_is_none(tmp_path):
    """With no saved id, an earlier tool_result is some other call's."""
    p = _write(tmp_path / "t.jsonl", _tool_result(SINCE - 5, TUID))
    assert saved_prompt_evidence(p, SINCE, "", "Bash") == "none"


# ── "moved_on": turn lines at or after since_wall ────────────────────────

@pytest.mark.parametrize("entry", [
    _user_text(SINCE + 1),
    _tool_result(SINCE + 1, "toolu_other"),
    _assistant_text(SINCE + 1),
    _tool_use(SINCE + 1, "toolu_other"),
    _tool_use(SINCE + 1, TUID, extra_text=True),
    _system(SINCE + 1, "turn_duration"),
    _system(SINCE + 1, "stop_hook_summary"),
], ids=["user-text", "other-tool-result", "assistant-text", "other-tool-use",
        "matching-tool-use-plus-text", "turn-duration", "stop-hook-summary"])
def test_turn_line_after_since_is_moved_on(tmp_path, entry):
    p = _write(tmp_path / "t.jsonl", entry)
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "moved_on"


def test_turn_line_exactly_at_since_is_moved_on(tmp_path):
    p = _write(tmp_path / "t.jsonl", _user_text(SINCE))
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "moved_on"


@pytest.mark.parametrize("entry", [
    _user_text(SINCE - 1),
    _assistant_text(SINCE - 1),
    _system(SINCE - 1, "turn_duration"),
    _tool_use(SINCE - 1, "toolu_other"),
], ids=["user-text", "assistant-text", "turn-duration", "other-tool-use"])
def test_turn_line_before_since_is_none(tmp_path, entry):
    p = _write(tmp_path / "t.jsonl", entry)
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"


# ── ignored lines ────────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", [
    {"type": "queue-operation", "operation": "enqueue",
     "timestamp": _iso(SINCE + 1), "content": "x"},
    {"type": "queue-operation", "operation": "remove",
     "timestamp": _iso(SINCE + 1)},
    _system(SINCE + 1, "compact_boundary"),
    {"type": "file-history-snapshot", "timestamp": _iso(SINCE + 1)},
    {"type": "summary", "summary": "s", "timestamp": _iso(SINCE + 1)},
    {"type": "user", "timestamp": _iso(SINCE + 1)},
    {"type": "assistant", "timestamp": _iso(SINCE + 1)},
], ids=["enqueue", "remove", "other-system", "file-history", "summary",
        "user-no-message", "assistant-no-message"])
def test_sidecar_line_after_since_is_none(tmp_path, entry):
    p = _write(tmp_path / "t.jsonl", entry)
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"


def test_previous_turn_end_then_queue_operations_is_none(tmp_path):
    """The tail observed live while a dialog waits (intent.md)."""
    p = _write(tmp_path / "t.jsonl",
               _user_text(SINCE - 60), _assistant_text(SINCE - 50),
               _system(SINCE - 49, "turn_duration"),
               {"type": "queue-operation", "operation": "enqueue",
                "timestamp": _iso(SINCE + 2), "content": "x"},
               {"type": "queue-operation", "operation": "remove",
                "timestamp": _iso(SINCE + 2)})
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"


# ── the positive match is not a contradiction ────────────────────────────

def test_matching_tool_use_by_id_is_none(tmp_path):
    p = _write(tmp_path / "t.jsonl", _tool_use(SINCE + 1, TUID, "Bash"))
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"


def test_matching_tool_use_by_name_without_id_is_none(tmp_path):
    p = _write(tmp_path / "t.jsonl", _tool_use(SINCE + 1, "toolu_x", "Bash"))
    assert saved_prompt_evidence(p, SINCE, "", "Bash") == "none"


def test_other_tool_name_without_id_is_moved_on(tmp_path):
    p = _write(tmp_path / "t.jsonl", _tool_use(SINCE + 1, "toolu_x", "Write"))
    assert saved_prompt_evidence(p, SINCE, "", "Bash") == "moved_on"


def test_same_name_other_id_with_saved_id_is_moved_on(tmp_path):
    p = _write(tmp_path / "t.jsonl", _tool_use(SINCE + 1, "toolu_x", "Bash"))
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "moved_on"


def test_matching_tool_use_then_its_result_is_answered(tmp_path):
    p = _write(tmp_path / "t.jsonl", _tool_use(SINCE + 1),
               _tool_result(SINCE + 5))
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "answered"


# ── undated lines use the file's mtime ───────────────────────────────────

def _undated_user(path) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {
            "role": "user", "content": "x"}}) + "\n")
    return str(path)


def test_undated_line_in_a_file_modified_after_since_is_moved_on(tmp_path):
    p = _undated_user(tmp_path / "t.jsonl")
    os.utime(p, (SINCE + 10, SINCE + 10))
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "moved_on"


def test_undated_line_in_a_file_modified_before_since_is_none(tmp_path):
    p = _undated_user(tmp_path / "t.jsonl")
    os.utime(p, (SINCE - 10, SINCE - 10))
    assert saved_prompt_evidence(p, SINCE, TUID, "Bash") == "none"
