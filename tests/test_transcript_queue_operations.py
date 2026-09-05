"""Tests for the ``queue-operation`` transcript line ("turn anchor
follows consumption") — ``read_turn_blocks`` yields it as a distinct
``"queue"`` item; ``read_turn_stream`` (and therefore ``read_turn_text``)
omit it entirely so it can never corrupt the live prose draft.
"""

from __future__ import annotations

import json

from aipager import transcript


def _write_jsonl_bytes(tmp_path, lines: list[dict]) -> str:
    p = tmp_path / "t.jsonl"
    content = "\n".join(json.dumps(x) for x in lines) + "\n"
    p.write_bytes(content.encode("utf-8"))
    return str(p)


def _queue_op(operation, reason=None, content="", ts=1234567890.0) -> dict:
    entry = {"type": "queue-operation", "operation": operation, "content": content,
             "timestamp": ts}
    if reason is not None:
        entry["reason"] = reason
    return entry


def _block_line(message_id: str, block: dict) -> dict:
    return {"type": "assistant", "message": {"id": message_id, "content": [block]}}


# ---- read_turn_blocks yields a "queue" 3-tuple ---------------------------

def test_read_turn_blocks_yields_queue_tuple_shape(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [
        _queue_op("remove", "absorbed_mid_turn", "the injected text", 111.5),
    ])
    items, off = transcript.read_turn_blocks(path, 0)
    assert items == [
        ("queue", ("remove", "absorbed_mid_turn", "the injected text", 111.5), ""),
    ]
    assert off == len(open(path, "rb").read())


def test_read_turn_blocks_queue_item_message_id_is_always_empty(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [_queue_op("dequeue", content="x")])
    items, _off = transcript.read_turn_blocks(path, 0)
    assert items[0][2] == ""


def test_read_turn_blocks_reason_absent_is_none(tmp_path):
    """A bare `remove` (discard) or `popAll`/`enqueue`/`dequeue` line
    carries no `reason` key at all — the tuple's second field is `None`,
    never a KeyError or a synthesized empty string."""
    path = _write_jsonl_bytes(tmp_path, [_queue_op("remove", content="discarded")])
    items, _off = transcript.read_turn_blocks(path, 0)
    assert items == [("queue", ("remove", None, "discarded", 1234567890.0), "")]


def test_read_turn_blocks_every_operation_kind_is_carried_through(tmp_path):
    """enqueue/dequeue/remove/popAll all surface — filtering which ones
    matter is the CALLER's job (animation._sync_anchors_from_transcript),
    never this reader's."""
    lines = [
        _queue_op("enqueue", content="a"),
        _queue_op("dequeue", content="b"),
        _queue_op("remove", "absorbed_mid_turn", content="c"),
        _queue_op("remove", "delivered_to_agent", content="d"),
        _queue_op("remove", content="e"),  # bare remove == discard
        _queue_op("popAll", content="f"),
    ]
    path = _write_jsonl_bytes(tmp_path, lines)
    items, _off = transcript.read_turn_blocks(path, 0)
    operations = [value[0] for _kind, value, _mid in items]
    assert operations == ["enqueue", "dequeue", "remove", "remove", "remove", "popAll"]
    reasons = [value[1] for _kind, value, _mid in items]
    assert reasons == [None, None, "absorbed_mid_turn", "delivered_to_agent", None, None]


def test_read_turn_blocks_interleaves_queue_and_assistant_lines_in_file_order(tmp_path):
    lines = [
        _block_line("M1", {"type": "text", "text": "hello"}),
        _queue_op("remove", "absorbed_mid_turn", content="hi"),
        _block_line("M1", {"type": "tool_use", "id": "a", "name": "Bash", "input": {}}),
    ]
    path = _write_jsonl_bytes(tmp_path, lines)
    items, _off = transcript.read_turn_blocks(path, 0)
    kinds = [kind for kind, _v, _m in items]
    assert kinds == ["text", "queue", "tool"]


# ---- partial-line / offset rules, identical to an assistant line --------

def test_read_turn_blocks_partial_trailing_queue_line_not_consumed(tmp_path):
    full = json.dumps(_queue_op("remove", "absorbed_mid_turn", "done")) + "\n"
    partial = json.dumps(_queue_op("dequeue", content="half"))[:-4]
    p = tmp_path / "t.jsonl"
    p.write_bytes((full + partial).encode("utf-8"))
    items, off = transcript.read_turn_blocks(str(p), 0)
    assert items == [
        ("queue", ("remove", "absorbed_mid_turn", "done", 1234567890.0), ""),
    ]
    assert off == len(full.encode("utf-8")), (
        "offset must not advance past the incomplete trailing line"
    )


def test_read_turn_blocks_malformed_queue_line_is_skipped_not_raised(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(b'{"type": "queue-operation", "operation": "remove"\n')  # truncated JSON
    items, off = transcript.read_turn_blocks(str(p), 0)
    assert items == []
    assert off == len(p.read_bytes())


# ---- read_turn_stream omits "queue" items entirely -----------------------

def test_read_turn_stream_omits_queue_items(tmp_path):
    lines = [
        _block_line("M1", {"type": "text", "text": "hello"}),
        _queue_op("remove", "absorbed_mid_turn", content="hi"),
    ]
    path = _write_jsonl_bytes(tmp_path, lines)
    items, off = transcript.read_turn_stream(path, 0)
    assert items == [("text", "hello")]
    blocks, block_off = transcript.read_turn_blocks(path, 0)
    assert off == block_off, "offset advances past the queue line even though it's dropped"


def test_read_turn_stream_all_queue_lines_yields_empty_but_advances_offset(tmp_path):
    path = _write_jsonl_bytes(tmp_path, [
        _queue_op("enqueue", content="a"),
        _queue_op("dequeue", content="b"),
    ])
    items, off = transcript.read_turn_stream(path, 0)
    assert items == []
    assert off == len(open(path, "rb").read())


def test_read_turn_text_never_sees_queue_lines(tmp_path):
    """A 4-tuple `.split()`ed as prose would raise AttributeError deep
    inside the live card's rendering path — this guard is what prevents
    that (design.md "turn anchor follows consumption")."""
    lines = [
        _block_line("M1", {"type": "text", "text": "hello"}),
        _queue_op("remove", "absorbed_mid_turn", content="hi"),
        _block_line("M2", {"type": "text", "text": "world"}),
    ]
    path = _write_jsonl_bytes(tmp_path, lines)
    text, _off = transcript.read_turn_text(path, 0)
    assert text == "hello\n\nworld"


# ---- the byte-identical assertion this feature must not disturb ---------

def test_existing_message_id_contract_unaffected_by_queue_support(tmp_path):
    """Mirrors test_transcript.py's own
    `test_read_turn_blocks_carries_message_ids` — its fixture has no
    queue-operation lines, so this feature must leave it byte-identical."""
    lines = [
        _block_line("M1", {"type": "text", "text": "first"}),
        _block_line("M1", {"type": "tool_use", "id": "a", "name": "Bash", "input": {}}),
        _block_line("M2", {"type": "tool_use", "id": "b", "name": "Read", "input": {}}),
    ]
    path = _write_jsonl_bytes(tmp_path, lines)
    items, off = transcript.read_turn_blocks(path, 0)
    assert items == [("text", "first", "M1"), ("tool", "Bash", "M1"), ("tool", "Read", "M2")]
    assert transcript.read_turn_stream(path, 0) == (
        [("text", "first"), ("tool", "Bash"), ("tool", "Read")], off,
    )
