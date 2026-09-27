"""``transcript.read_still_queued``: what Claude Code's queue-operation lines
say it still holds (roadmap 8.37). Only positive evidence counts: a
teardown types Escape on it, and an Escape into an empty queue interrupts
the running turn."""

from __future__ import annotations

import json

from aipager.transcript import read_still_queued


def _write(path, *ops, tail=b""):
    with open(path, "wb") as fh:
        for op in ops:
            if isinstance(op, bytes):
                fh.write(op + b"\n")
                continue
            operation, content, reason = (tuple(op) + (None, None))[:3]
            line = {"type": "queue-operation", "operation": operation}
            if content is not None:
                line["content"] = content
            if reason is not None:
                line["reason"] = reason
            fh.write(json.dumps(line).encode() + b"\n")
        fh.write(tail)


def test_an_enqueue_with_no_fate_is_still_queued(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("enqueue", "b"))
    assert read_still_queued(str(t)) == ["a", "b"]


def test_a_removal_naming_one_message_takes_only_that_one(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("enqueue", "b"),
           ("remove", "a", "absorbed_mid_turn"),
           ("enqueue", "c"), ("remove", "c", "delivered_to_agent"))
    assert read_still_queued(str(t)) == ["b"]


def test_a_removal_naming_nothing_queued_changes_nothing(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("remove", "zzz", "absorbed_mid_turn"))
    assert read_still_queued(str(t)) == ["a"]


def test_a_dequeue_ends_the_evidence_for_everything_before_it(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("enqueue", "b"), ("dequeue",),
           ("enqueue", "c"))
    assert read_still_queued(str(t)) == ["c"]


def test_one_dequeue_ends_the_evidence_even_for_a_message_left_behind(
        tmp_path):
    """Seen in real transcripts: two messages queued, one ``dequeue``, the
    second absorbed later. Which one the dequeue took is not written, so
    neither keeps its evidence: a miss (no keys), chosen on purpose over a
    guess that could report an empty queue as full."""
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("enqueue", "b"), ("dequeue",))
    assert read_still_queued(str(t)) == []


def test_a_bare_remove_ends_the_evidence_for_everything_before_it(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("remove",), ("enqueue", "b"))
    assert read_still_queued(str(t)) == ["b"]


def test_an_empty_content_remove_is_a_bare_remove(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("remove", ""))
    assert read_still_queued(str(t)) == []


def test_popall_ends_the_evidence(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("popAll", "a"))
    assert read_still_queued(str(t)) == []


def test_an_unknown_operation_ends_the_evidence(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"), ("shuffle", "a"))
    assert read_still_queued(str(t)) == []


def test_other_lines_and_junk_are_ignored(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"),
           b'{"type":"assistant","message":{"content":"queue-operation"}}',
           b'not json "queue-operation"',
           b'{"type":"user","operation":"dequeue","x":"queue-operation"}')
    assert read_still_queued(str(t)) == ["a"]


def test_a_line_still_being_written_is_not_read(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "a"),
           tail=b'{"type":"queue-operation","operation":"dequeue"')
    assert read_still_queued(str(t)) == ["a"]


def test_only_the_window_is_read(tmp_path):
    t = tmp_path / "t.jsonl"
    _write(t, ("enqueue", "old"), b"x" * 400, ("enqueue", "new"))
    assert read_still_queued(str(t), window=200) == ["new"]
    assert read_still_queued(str(t)) == ["old", "new"]


def test_a_window_starting_mid_line_skips_the_partial_line(tmp_path):
    """The tail of a line cut by the window is not a line, even when it
    happens to parse as one."""
    t = tmp_path / "t.jsonl"
    ghost = json.dumps({"type": "queue-operation", "operation": "enqueue",
                        "content": "ghost"}).encode()
    _write(t, b"JUNK" + ghost)
    assert read_still_queued(str(t)) == []
    assert read_still_queued(str(t), window=len(ghost) + 1) == []


def test_no_path_or_no_file_is_no_evidence(tmp_path):
    assert read_still_queued("") == []
    assert read_still_queued(str(tmp_path / "missing.jsonl")) == []


# ── held_by_claude: which candidates the evidence backs ─────────────────────

def _sess(tmp_path, *ops):
    from aipager.state import TrackedSession
    t = tmp_path / "held.jsonl"
    _write(t, *ops)
    sess = TrackedSession(name="claude-h", label="h")
    sess.transcript_path = str(t)
    return sess


def test_one_queued_line_backs_one_candidate_the_longest(tmp_path):
    from aipager.bot.session_ops import held_by_claude
    sess = _sess(tmp_path, ("enqueue", "[via Telegram]\nok go"))
    short, long_ = {"raw_text": "ok"}, {"raw_text": "ok go"}
    assert held_by_claude(sess, [short, long_]) == [long_]


def test_a_candidate_with_no_text_is_never_held(tmp_path):
    from aipager.bot.session_ops import held_by_claude
    sess = _sess(tmp_path, ("enqueue", "anything"))
    assert held_by_claude(sess, [{"raw_text": ""}, {"body": None}]) == []


def test_a_note_is_matched_by_its_body(tmp_path):
    from aipager.bot.session_ops import held_by_claude
    sess = _sess(tmp_path, ("enqueue", "[marker]\nhello"))
    note = {"body": "[marker]\nhello", "raw_text": "hello"}
    other = {"body": "[marker]\nbye", "raw_text": "bye"}
    assert held_by_claude(sess, [note, other]) == [note]


def test_the_turns_pinned_transcript_is_read_first(tmp_path):
    from aipager.bot.session_ops import held_by_claude
    sess = _sess(tmp_path)
    pinned = tmp_path / "pinned.jsonl"
    _write(pinned, ("enqueue", "x"))
    sess.stream_transcript_path = str(pinned)
    assert held_by_claude(sess, [{"raw_text": "x"}]) == [{"raw_text": "x"}]
