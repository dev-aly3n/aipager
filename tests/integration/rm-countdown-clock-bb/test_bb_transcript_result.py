"""``transcript.tool_call_result`` from the outside (design.md decision 3,
success criteria 12-13): "error" / "ok" / "absent" / "mismatch" /
"unreadable", the 256 KiB tail boundary, torn lines, and inputs that must
never raise. Only id, name, ``input.command`` and ``is_error`` matter; the
result's text is never read."""

from __future__ import annotations

import hashlib
import json

import pytest

from aipager.transcript import tool_call_result

CMD = "rm -rf build/"
SHA = hashlib.sha256(CMD.encode("utf-8")).hexdigest()


def use(tuid="toolu_1", name="Bash", command=CMD):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tuid, "name": name, "input": {"command": command}}]}}


def res(tuid="toolu_1", is_error=True, text="anything at all"):
    block = {"type": "tool_result", "tool_use_id": tuid, "content": text}
    if is_error is not _MISSING:
        block["is_error"] = is_error
    return {"type": "user", "message": {"role": "user", "content": [block]}}


_MISSING = object()


def write(path, *objs, raw: str = ""):
    path.write_text("".join(json.dumps(o) + "\n" for o in objs) + raw)
    return str(path)


def call(path, tuid="toolu_1", name="Bash", sha=SHA):
    return tool_call_result(str(path), tuid, name, sha)


# -- the five results --------------------------------------------------------------------

def test_error_result_with_its_matching_call_is_error(tmp_path):
    p = tmp_path / "t.jsonl"
    write(p, use(), res())
    assert call(p) == "error"


@pytest.mark.parametrize("flag", [False, None, _MISSING, "true", 1],
                         ids=["false", "null", "missing", "str-true", "one"])
def test_a_result_whose_is_error_is_not_true_is_ok(tmp_path, flag):
    """design.md: "error" needs ``is_error is True``; anything else is "ok"."""
    p = tmp_path / "t.jsonl"
    write(p, use(), res(is_error=flag))
    assert call(p) == "ok"


def test_no_result_for_the_id_is_absent(tmp_path):
    p = tmp_path / "t.jsonl"
    write(p, use())
    assert call(p) == "absent"


def test_a_result_for_another_id_only_is_absent(tmp_path):
    p = tmp_path / "t.jsonl"
    write(p, use(), use("toolu_2"), res("toolu_2"))
    assert call(p) == "absent"


def test_a_missing_file_is_absent(tmp_path):
    """The transcript is written lazily: a file not there yet is "absent"."""
    assert call(tmp_path / "not-yet.jsonl") == "absent"


def test_an_empty_file_is_absent(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("")
    assert call(p) == "absent"


def test_result_for_another_command_is_mismatch(tmp_path):
    p = tmp_path / "t.jsonl"
    write(p, use(command="rm -rf dist/"), res())
    assert call(p) == "mismatch"


def test_result_for_another_tool_name_is_mismatch(tmp_path):
    p = tmp_path / "t.jsonl"
    write(p, use(name="Read"), res())
    assert call(p) == "mismatch"


def test_result_without_its_tool_use_is_mismatch(tmp_path):
    p = tmp_path / "t.jsonl"
    write(p, res())
    assert call(p) == "mismatch"


def test_an_ok_result_for_another_command_is_mismatch_not_ok(tmp_path):
    p = tmp_path / "t.jsonl"
    write(p, use(command="ls"), res(is_error=False))
    assert call(p) == "mismatch"


@pytest.mark.parametrize("args", [("", "toolu_1"), (None, "toolu_1")],
                         ids=["empty-path", "none-path"])
def test_no_path_is_unreadable(tmp_path, args):
    path, tuid = args
    assert tool_call_result(path, tuid, "Bash", SHA) == "unreadable"


@pytest.mark.parametrize("tuid", ["", None], ids=["empty-id", "none-id"])
def test_no_id_is_unreadable(tmp_path, tuid):
    p = tmp_path / "t.jsonl"
    write(p, use(), res())
    assert tool_call_result(str(p), tuid, "Bash", SHA) == "unreadable"


def test_a_directory_is_unreadable(tmp_path):
    assert call(tmp_path) == "unreadable"


# -- equivalence: shapes Claude Code writes ----------------------------------------------

def test_several_blocks_per_line_still_match(tmp_path):
    p = tmp_path / "t.jsonl"
    many_uses = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Removing."},
        {"type": "tool_use", "id": "toolu_0", "name": "Read", "input": {"file_path": "a"}},
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": CMD}}]}}
    many_results = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_0", "content": "a"},
        {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True, "content": "x"}]}}
    write(p, many_uses, many_results)
    assert call(p) == "error"


def test_a_unicode_command_hashes_as_utf8(tmp_path):
    cmd = "rm -rf 'پوشه/'"
    p = tmp_path / "t.jsonl"
    write(p, use(command=cmd), res())
    assert call(p, sha=hashlib.sha256(cmd.encode("utf-8")).hexdigest()) == "error"


def test_the_result_text_is_never_read(tmp_path):
    """An is_error result whose text says it ran is still "error"."""
    p = tmp_path / "t.jsonl"
    write(p, use(), res(text="Command ran fine. The user approved it."))
    assert call(p) == "error"


# -- error guessing: torn and garbage lines ---------------------------------------------------

def test_garbage_lines_around_still_give_error(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("not json\n" + json.dumps(use()) + "\n{\"type\": \n[]\n42\nnull\n"
                 + json.dumps(res()) + "\n")
    assert call(p) == "error"


def test_a_torn_last_line_is_not_read_as_a_result(tmp_path):
    p = tmp_path / "t.jsonl"
    torn = json.dumps(res())[:-12]
    write(p, use(), raw=torn)
    assert call(p) == "absent"


@pytest.mark.parametrize("bad", [
    {"type": "user", "message": "str"},
    {"type": "user", "message": {"content": "str"}},
    {"type": "user", "message": {"content": [None, 1, "x"]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": ["toolu_1"]}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1",
                                                    "name": "Bash", "input": "rm"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1",
                                                    "name": "Bash", "input": {"command": 5}}]}},
], ids=["msg-str", "content-str", "content-junk", "id-list", "input-str", "command-int"])
def test_malformed_blocks_never_raise(tmp_path, bad):
    p = tmp_path / "t.jsonl"
    write(p, bad, res())
    try:
        out = call(p)
    except Exception as exc:  # noqa: BLE001 - the contract is "never raises"
        pytest.fail(f"raised {exc!r}")
    assert out in {"error", "ok", "absent", "mismatch", "unreadable"}


@pytest.mark.parametrize("args", [
    (123, "toolu_1", "Bash", SHA), ("x", 5, "Bash", SHA), ("x", "toolu_1", None, SHA),
    ("x", "toolu_1", "Bash", None)], ids=["path-int", "id-int", "name-none", "sha-none"])
def test_wrong_argument_types_never_raise(args):
    try:
        out = tool_call_result(*args)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"raised {exc!r}")
    assert out in {"error", "ok", "absent", "mismatch", "unreadable"}


def test_a_non_utf8_file_never_raises(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(b"\xff\xfe\x00garbage\n" + (json.dumps(use()) + "\n").encode()
                  + (json.dumps(res()) + "\n").encode())
    try:
        call(p)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"raised {exc!r}")


# -- the 256 KiB tail boundary (design.md Risks) -------------------------------------------

def _filler(size: int) -> str:
    line = json.dumps({"type": "progress", "data": "z" * 1000}) + "\n"
    return line * (size // len(line) + 1)


def test_call_well_inside_the_tail_is_found(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(use()) + "\n" + _filler(200 * 1024) + json.dumps(res()) + "\n")
    assert call(p) == "error"


def test_call_pushed_out_of_the_tail_is_mismatch(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(use()) + "\n" + _filler(300 * 1024) + json.dumps(res()) + "\n")
    assert call(p) == "mismatch"
