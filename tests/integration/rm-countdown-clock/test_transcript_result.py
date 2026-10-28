"""How one Bash tool call came back, from the transcript (roadmap 8.128;
design.md decision 3): ``transcript.tool_call_result`` matches the call by
its id, its tool name and its command's digest, and reads only the
result's ``is_error``, never Claude Code's text. Never raises."""

from __future__ import annotations

import hashlib
import json

import pytest

from aipager import transcript

TUID = "toolu_rm1"
COMMAND = "rm -rf /home/alice/app"
SHA = hashlib.sha256(COMMAND.encode()).hexdigest()


def tool_use(tuid=TUID, name="Bash", command=COMMAND):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Removing it."},
        {"type": "tool_use", "id": tuid, "name": name, "input": {"command": command}}]}}


def tool_result(tuid=TUID, is_error=True, text="anything at all"):
    block = {"type": "tool_result", "tool_use_id": tuid, "content": text}
    if is_error is not None:
        block["is_error"] = is_error
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def write(tmp_path, *entries, raw=()):
    path = tmp_path / "t.jsonl"
    lines = [json.dumps(e) for e in entries] + list(raw)
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def result(path, tuid=TUID, name="Bash", sha=SHA):
    return transcript.tool_call_result(path, tuid, name, sha)


def test_an_error_result_is_error(tmp_path):
    assert result(write(tmp_path, tool_use(), tool_result())) == "error"


@pytest.mark.parametrize("is_error", [False, None, "true", 1],
                         ids=["false", "missing", "str", "int"])
def test_a_result_not_marked_error_is_ok(tmp_path, is_error):
    assert result(write(tmp_path, tool_use(), tool_result(is_error=is_error))) == "ok"


def test_no_result_is_absent(tmp_path):
    assert result(write(tmp_path, tool_use())) == "absent"


def test_another_calls_result_is_absent(tmp_path):
    path = write(tmp_path, tool_use(), tool_result(tuid="toolu_other"))
    assert result(path) == "absent"


def test_a_missing_file_is_absent(tmp_path):
    assert result(str(tmp_path / "none.jsonl")) == "absent"


def test_another_command_is_a_mismatch(tmp_path):
    """The id was borrowed from a parallel Bash call (the PermissionRequest
    carries none): its own failure is not this prompt's refusal."""
    path = write(tmp_path, tool_use(command="make test"), tool_result())
    assert result(path) == "mismatch"


def test_another_tool_name_is_a_mismatch(tmp_path):
    path = write(tmp_path, tool_use(name="Write"), tool_result())
    assert result(path) == "mismatch"


def test_a_result_whose_call_is_out_of_the_tail_is_a_mismatch(tmp_path):
    path = write(tmp_path, tool_result())
    assert result(path) == "mismatch"


def test_a_call_pushed_out_of_the_tail_by_a_huge_output_is_a_mismatch(tmp_path):
    big = {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": "x" * (transcript.TAIL_READ_BYTES + 10)}]}}
    path = write(tmp_path, tool_use(), big, tool_result())
    assert result(path) == "mismatch"


def test_the_result_before_its_call_still_matches(tmp_path):
    assert result(write(tmp_path, tool_result(), tool_use())) == "error"


def test_torn_and_odd_lines_are_skipped(tmp_path):
    path = write(tmp_path, tool_use(), tool_result(),
                 raw=["{\"type\": \"user\", \"mess", "[1, 2]", "null", "",
                      json.dumps({"type": "user", "message": "text"}),
                      json.dumps({"type": "user", "message": {"content": ["x", 3]}})])
    assert result(path) == "error"


def test_the_last_result_line_decides(tmp_path):
    path = write(tmp_path, tool_use(), tool_result(is_error=False), tool_result())
    assert result(path) == "error"


@pytest.mark.parametrize("args", [("", TUID, "Bash", SHA), ("x", "", "Bash", SHA),
                                  ("x", None, "Bash", SHA)],
                         ids=["no-path", "no-id", "none-id"])
def test_no_path_or_id_is_unreadable(args):
    assert transcript.tool_call_result(*args) == "unreadable"


def test_a_directory_is_unreadable(tmp_path):
    assert result(str(tmp_path)) == "unreadable"


def test_an_empty_digest_never_matches(tmp_path):
    path = write(tmp_path, tool_use(), tool_result())
    assert result(path, sha="") == "mismatch"


def test_the_text_is_never_read(tmp_path):
    """Claude Code's wording is not compared: a result that reads like
    success is still the error its flag says."""
    path = write(tmp_path, tool_use(), tool_result(text="Command ran successfully."))
    assert result(path) == "error"


@pytest.mark.parametrize("name, sha", [(None, SHA), ("Bash", None), (5, 5)],
                         ids=["none-name", "none-sha", "ints"])
def test_odd_arguments_never_raise(tmp_path, name, sha):
    path = write(tmp_path, tool_use(), tool_result())
    assert transcript.tool_call_result(path, TUID, name, sha) in (
        "error", "ok", "absent", "mismatch", "unreadable")
