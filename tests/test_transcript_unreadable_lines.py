"""Roadmap 8.106: no transcript reader stops on a line it cannot read.

A line nested deeper than Python's parser goes raises RecursionError, an
over-long integer a plain ValueError, neither a JSONDecodeError. Every
reader used to catch only the last, so such a line escaped: the busy card
froze for the rest of the turn (each tick read the same line again), and
a hook event whose handler read it was dropped. Each reader now skips the
line like a torn one, reads on past it, and the streaming readers move
their offset beyond it.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from aipager import transcript
from aipager.dtach import hook_receiver

DEEP = "[" * 100_000 + "]" * 100_000
LONG_INT = "9" * 5_000      # over Python's integer-string limit (3.11+)
NOW = time.time()


def _ts(t: float = NOW) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _assistant(content, **message) -> dict:
    return {"type": "assistant", "timestamp": _ts(), "message": {
        "role": "assistant", "id": "msg_1", "content": content, **message}}


def _bad_lines(*, holding: str = "") -> list[str]:
    """A too-deep line, an over-long integer and a line that is not an
    object, each mentioning *holding* so a reader's own pre-filter (a
    substring it looks for before parsing) lets it through."""
    tag = json.dumps(holding)
    return ['{"t": ' + tag + ', "x": ' + DEEP + "}",
            '{"t": ' + tag + ', "x": ' + LONG_INT + "}",
            "[" + tag + "]"]


def _write(tmp_path, *lines) -> str:
    path = tmp_path / "t.jsonl"
    path.write_text("".join((ln if isinstance(ln, str) else json.dumps(ln)) + "\n"
                            for ln in lines))
    return str(path)


def _call(fn, *args, **kwargs):
    """*fn*'s result, or what it raised (so a raise fails an assertion)."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the guard under test
        return f"raised {type(exc).__name__}"


def _pair(fn, *args, **kwargs):
    """:func:`_call` for a reader that returns a pair: a raise fails here,
    naming what was raised, rather than in the unpacking."""
    result = _call(fn, *args, **kwargs)
    assert not isinstance(result, str), result
    return result


# ---- transcript.py -----------------------------------------------------------------

def test_turn_tail_since_reads_past_it(tmp_path):
    marker = {"type": "user", "timestamp": _ts(), "message": {
        "role": "user", "content": "[Request interrupted by user for tool use]"}}
    path = _write(tmp_path, marker, *_bad_lines())
    assert _call(transcript.turn_tail_since, path, NOW - 60) == "marker"


def test_extract_last_response_entry_reads_past_it(tmp_path):
    path = _write(tmp_path, _assistant([{"type": "text", "text": "the answer"}],
                                       stop_reason="end_turn"),
                  *_bad_lines(holding="assistant"),
                  {"type": "assistant", "message": "not an object"})
    text, _written = _pair(transcript.extract_last_response_entry, path)
    assert text == "the answer"


def test_a_plain_string_answer_is_read_as_one_text(tmp_path):
    path = _write(tmp_path, _assistant("a plain answer", stop_reason="end_turn"))
    text, _written = _pair(transcript.extract_last_response_entry, path)
    assert text == "a plain answer"


def test_an_entry_whose_message_is_not_an_object_is_no_placeholder():
    assert transcript.is_no_response_entry({"type": "assistant", "message": "x"}) is False


def test_read_turn_blocks_moves_past_it(tmp_path):
    path = _write(tmp_path, *_bad_lines(holding="assistant"),
                  {"type": "assistant", "message": "not an object"},
                  _assistant([{"type": "text", "text": "after it"}]))
    items, offset = _pair(transcript.read_turn_blocks, path, 0)
    assert ("text", "after it", "msg_1") in items
    assert offset == (tmp_path / "t.jsonl").stat().st_size   # not read again


def test_read_queue_events_moves_past_it(tmp_path):
    enqueue = {"type": "queue-operation", "operation": "enqueue",
               "content": "hello", "timestamp": _ts()}
    path = _write(tmp_path, *_bad_lines(holding="queue-operation"), enqueue)
    events, offset = _pair(transcript.read_queue_events, path, 0)
    assert [e[:3] for e in events] == [("enqueue", None, "hello")]
    assert offset == (tmp_path / "t.jsonl").stat().st_size


def test_read_still_queued_reads_past_it(tmp_path):
    enqueue = {"type": "queue-operation", "operation": "enqueue",
               "content": "hello", "timestamp": _ts()}
    path = _write(tmp_path, enqueue, *_bad_lines(holding="queue-operation"))
    assert _call(transcript.read_still_queued, path) == ["hello"]


@pytest.mark.parametrize("bad", range(3))
def test_the_real_text_of_such_a_line_is_nothing(bad):
    line = _bad_lines(holding="assistant")[bad].encode()
    assert _call(transcript._real_text_of_line, line) == ""


# ---- hook_receiver.py -----------------------------------------------------------------

def test_token_usage_reads_past_it(tmp_path):
    path = _write(tmp_path, _assistant([], usage={"input_tokens": 1000}),
                  *_bad_lines(), "42", {"type": "assistant", "message": "odd"})
    usage = _call(hook_receiver._extract_token_usage, path)
    assert isinstance(usage, dict) and usage["total_input"] == 1000


@pytest.mark.parametrize("reader", ["pending", "specific"])
def test_the_pending_tool_is_found_past_it(tmp_path, reader):
    tool = _assistant([{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                        "input": {"command": "ls"}}])
    path = _write(tmp_path, tool, *_bad_lines(), "42",
                  {"type": "assistant", "message": {"content": 5}})
    if reader == "pending":
        found = _call(hook_receiver._extract_pending_tool, path)
    else:
        found = _call(hook_receiver._extract_specific_tool, path, "Bash")
    assert isinstance(found, dict) and found["name"] == "Bash"


def test_a_transcript_that_is_not_utf8_is_read_as_far_as_it_goes(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(json.dumps(_assistant([], usage={"input_tokens": 7})).encode()
                     + b"\n\xc3\x28\xff\n")
    usage = _call(hook_receiver._extract_token_usage, str(path))
    assert isinstance(usage, dict) and usage["total_input"] == 7


# ---- review round 1 ---------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(__import__("sys"), "get_int_max_str_digits"),
                    reason="this Python reads integers of any length")
def test_turn_appears_complete_reads_past_an_integer_too_long_to_read(tmp_path):
    path = _write(tmp_path, _assistant([], stop_reason="end_turn"), '{"x": ' + LONG_INT + "}")
    assert _call(transcript.turn_appears_complete, path) is True


def test_a_number_where_text_belongs_is_no_text(tmp_path):
    path = _write(tmp_path,
                  _assistant([{"type": "text", "text": "the answer"}], stop_reason="end_turn"),
                  {"type": "assistant", "message": {"content": 5}},
                  _assistant([{"type": "text", "text": 5}, {"type": "text", "text": None}]))
    text, _written = _pair(transcript.extract_last_response_entry, path)
    assert text == "the answer"


def test_the_card_reader_takes_only_text_and_names_that_are_strings(tmp_path):
    path = _write(tmp_path, _assistant([
        {"type": "text", "text": 5}, {"type": "tool_use", "name": 7},
        {"type": "text", "text": "kept"}]))
    items, _offset = _pair(transcript.read_turn_blocks, path, 0)
    assert items == [("tool", "", "msg_1"), ("text", "kept", "msg_1")]


def test_token_usage_of_the_wrong_shape_is_no_usage(tmp_path):
    path = _write(tmp_path, _assistant([], usage={"input_tokens": 1000}),
                  {"type": "assistant", "message": {"usage": "x"}})
    usage = _call(hook_receiver._extract_token_usage, path)
    assert isinstance(usage, dict) and usage["total_input"] == 1000
    path = _write(tmp_path, _assistant([], usage={"input_tokens": "1000",
                                              "cache_read_input_tokens": 5}))
    usage = _call(hook_receiver._extract_token_usage, path)
    assert isinstance(usage, dict) and usage["total_input"] == 5
