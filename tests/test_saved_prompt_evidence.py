"""The restart's transcript check for a saved permission prompt (roadmap
8.102): ``saved_prompt_evidence`` over hand-written JSONL.

What Claude Code writes is lazy: while its permission dialog waits, the
pending tool_use is normally not on disk and the tail can still be the
previous turn's end. So the check asks only whether anything contradicts
the saved prompt; the prompt's own tool_use, when it does show, is no
contradiction.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from aipager.transcript import saved_prompt_evidence

SHOWN = 1_800_000_000.0
TUID = "toolu_saved"


def _ts(wall: float) -> str:
    return datetime.fromtimestamp(wall, timezone.utc).isoformat()


def _write(path, *entries, mtime: float = SHOWN - 100) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    os.utime(path, (mtime, mtime))
    return str(path)


def _assistant(at, *blocks, stop="tool_use"):
    return {"type": "assistant", "timestamp": _ts(at), "message": {
        "role": "assistant", "stop_reason": stop, "content": list(blocks)}}


def _tool_use(tid=TUID, name="Bash"):
    return {"type": "tool_use", "id": tid, "name": name,
            "input": {"command": "ls"}}


def _result(at, tid=TUID, text="ok"):
    return {"type": "user", "timestamp": _ts(at), "message": {
        "role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text}]}}


# The previous turn's end, then Claude Code's queue bookkeeping for the
# prompt that started the waiting turn: the tail seen live while a dialog
# waited (2026-10-07).
_PREVIOUS_TURN = [
    {"type": "user", "timestamp": _ts(SHOWN - 300), "message": {
        "role": "user", "content": "earlier prompt"}},
    _assistant(SHOWN - 290, {"type": "text", "text": "done"}, stop="end_turn"),
    {"type": "system", "subtype": "turn_duration", "timestamp": _ts(SHOWN - 289),
     "durationMs": 11000},
    {"type": "queue-operation", "operation": "enqueue",
     "timestamp": _ts(SHOWN - 20), "content": "next prompt"},
    {"type": "queue-operation", "operation": "remove",
     "timestamp": _ts(SHOWN - 20)},
]


def test_the_previous_turns_tail_contradicts_nothing(tmp_path):
    tp = _write(tmp_path / "t.jsonl", *_PREVIOUS_TURN)
    assert saved_prompt_evidence(tp, SHOWN, TUID, "Bash") == "none"


def test_queue_operations_and_other_system_lines_after_the_prompt_are_skipped(
        tmp_path):
    tp = _write(tmp_path / "t.jsonl", *_PREVIOUS_TURN,
                {"type": "queue-operation", "operation": "enqueue",
                 "timestamp": _ts(SHOWN + 30), "content": "typed meanwhile"},
                {"type": "system", "subtype": "informational",
                 "timestamp": _ts(SHOWN + 31)},
                {"type": "file-history-snapshot", "timestamp": _ts(SHOWN + 32)},
                {"type": "summary", "summary": "x"},
                {"type": "ai-title", "timestamp": _ts(SHOWN + 33)})
    assert saved_prompt_evidence(tp, SHOWN, TUID, "Bash") == "none"


def test_matching_tool_use_is_not_a_contradiction(tmp_path):
    tp = _write(tmp_path / "t.jsonl", *_PREVIOUS_TURN,
                _assistant(SHOWN + 1, _tool_use()))
    assert saved_prompt_evidence(tp, SHOWN, TUID, "Bash") == "none"


def test_without_a_saved_id_the_tool_name_matches(tmp_path):
    tp = _write(tmp_path / "t.jsonl", _assistant(SHOWN + 1, _tool_use("x1")))
    assert saved_prompt_evidence(tp, SHOWN, "", "Bash") == "none"
    assert saved_prompt_evidence(tp, SHOWN, "", "Write") == "moved_on"


def test_another_tool_use_after_the_prompt_moved_on(tmp_path):
    tp = _write(tmp_path / "t.jsonl", _assistant(SHOWN + 1, _tool_use("other")))
    assert saved_prompt_evidence(tp, SHOWN, TUID, "Bash") == "moved_on"


def test_tool_result_for_saved_id_even_if_dated_earlier(tmp_path):
    """The result is the answer whatever its timestamp says (a clock step,
    a line dated by Claude's own clock)."""
    tp = _write(tmp_path / "t.jsonl", _assistant(SHOWN - 2, _tool_use()),
                _result(SHOWN - 1))
    assert saved_prompt_evidence(tp, SHOWN, TUID, "Bash") == "answered"


def test_a_result_for_another_call_before_the_prompt_is_history(tmp_path):
    tp = _write(tmp_path / "t.jsonl", _result(SHOWN - 1, tid="toolu_old"))
    assert saved_prompt_evidence(tp, SHOWN, TUID, "Bash") == "none"


@pytest.mark.parametrize("entry", [
    pytest.param(_result(SHOWN + 5, tid="toolu_other"), id="other-result"),
    pytest.param({"type": "user", "timestamp": _ts(SHOWN + 5), "message": {
        "role": "user", "content": "[Request interrupted by user for tool use]"}},
        id="interrupt-marker"),
    pytest.param({"type": "user", "timestamp": _ts(SHOWN + 5), "message": {
        "role": "user", "content": "a new prompt"}}, id="new-prompt"),
    pytest.param({"type": "system", "subtype": "turn_duration",
                  "timestamp": _ts(SHOWN + 5)}, id="turn-duration"),
    pytest.param({"type": "system", "subtype": "stop_hook_summary",
                  "timestamp": _ts(SHOWN + 5)}, id="stop-hook-summary"),
    pytest.param(_assistant(SHOWN + 5, {"type": "text", "text": "done"},
                            stop="end_turn"), id="assistant-text"),
    pytest.param(_assistant(SHOWN + 5, _tool_use(), {"type": "text", "text": "x"}),
                 id="mixed-blocks"),
])
def test_a_turn_line_after_the_prompt_moved_on(tmp_path, entry):
    tp = _write(tmp_path / "t.jsonl", *_PREVIOUS_TURN, entry)
    assert saved_prompt_evidence(tp, SHOWN, TUID, "Bash") == "moved_on"


def test_an_undated_line_is_dated_by_the_file(tmp_path):
    undated = {"type": "user", "message": {"role": "user", "content": "hi"}}
    old = _write(tmp_path / "a.jsonl", undated, mtime=SHOWN - 10)
    assert saved_prompt_evidence(old, SHOWN, TUID, "Bash") == "none"
    new = _write(tmp_path / "b.jsonl", undated, mtime=SHOWN + 10)
    assert saved_prompt_evidence(new, SHOWN, TUID, "Bash") == "moved_on"


def test_the_answer_wins_over_a_later_line(tmp_path):
    tp = _write(tmp_path / "t.jsonl", _result(SHOWN + 1),
                _assistant(SHOWN + 2, {"type": "text", "text": "ok"},
                           stop="end_turn"))
    assert saved_prompt_evidence(tp, SHOWN, TUID, "Bash") == "answered"


def test_no_file_yet_contradicts_nothing(tmp_path):
    assert saved_prompt_evidence(str(tmp_path / "missing.jsonl"), SHOWN, TUID,
                                 "Bash") == "none"


def test_no_path_or_an_unreadable_file_is_unreadable(tmp_path):
    assert saved_prompt_evidence("", SHOWN, TUID, "Bash") == "unreadable"
    assert saved_prompt_evidence(str(tmp_path), SHOWN, TUID,
                                 "Bash") == "unreadable"  # a directory


def test_garbage_lines_are_skipped(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("not json\n[1, 2]\n\n" + json.dumps(_PREVIOUS_TURN[0]) + "\n")
    os.utime(path, (SHOWN - 100, SHOWN - 100))
    assert saved_prompt_evidence(str(path), SHOWN, TUID, "Bash") == "none"
