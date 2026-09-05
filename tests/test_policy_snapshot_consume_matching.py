"""``aipager.policy_snapshot.match_notes_prefix_run`` /
``consume_notes_matching`` ("turn anchor follows consumption").

``match_notes_prefix_run`` is the pure matching loop extracted from
``notify_hook._match_and_promote`` (covered end-to-end in
``tests/test_queue_pickup_matching.py``) — these tests pin the SAME
prefix-run semantics on the extracted function directly, plus
``consume_notes_matching``'s own contract: matches+deletes, never
touches the merged policy snapshot file, and R7 (an already-swept note
is simply not found — no error, no resurrection).
"""

from __future__ import annotations

import json
import time

from aipager import policy_snapshot as ps

SESSION = "claude-consume-x"

_counter = {"n": 0}


def _note(session, body, *, msg_id=None, sender_key=(1, 1)):
    """Write a note with a strictly-increasing ``queued_at`` — mirrors
    ``tests/test_queue_pickup_matching.py``'s own helper, so "oldest
    first" ordering is deterministic even within one fast test."""
    path = ps.write_note(
        session, None, None, None,
        msg_id=msg_id if msg_id is not None
        else len(list(ps.notes_dir(session).glob("*.json"))) + 1,
        chat_id=1, sender_key=sender_key,
        body=body, raw_text=body,
    )
    _counter["n"] += 1
    data = json.loads(path.read_text())
    data["queued_at"] = time.time() + _counter["n"]
    path.write_text(json.dumps(data))
    return path


# ---- match_notes_prefix_run: pure, no I/O side effects -------------------

def test_match_notes_prefix_run_full_match():
    outstanding = [{"body": "do the thing"}]
    matched = ps.match_notes_prefix_run(outstanding, "do the thing")
    assert matched == outstanding


def test_match_notes_prefix_run_partial_prefix_stops_at_first_miss():
    outstanding = [{"body": "first"}, {"body": "second"}, {"body": "third"}]
    matched = ps.match_notes_prefix_run(outstanding, "first\n\nsecond")
    assert matched == outstanding[:2]


def test_match_notes_prefix_run_requires_order_not_just_presence():
    outstanding = [{"body": "alpha"}, {"body": "beta"}]
    matched = ps.match_notes_prefix_run(
        outstanding, "beta comes first, then alpha comes second",
    )
    assert matched == [{"body": "alpha"}]


def test_match_notes_prefix_run_no_match_returns_empty():
    outstanding = [{"body": "totally unrelated"}]
    assert ps.match_notes_prefix_run(outstanding, "something else") == []


def test_match_notes_prefix_run_empty_outstanding_returns_empty():
    assert ps.match_notes_prefix_run([], "anything") == []


def test_match_notes_prefix_run_empty_body_stops_the_run():
    outstanding = [{"body": ""}, {"body": "second"}]
    assert ps.match_notes_prefix_run(outstanding, "second") == []


def test_match_notes_prefix_run_is_pure_no_filesystem_touched(tmp_path, monkeypatch):
    """No ``notes_dir``/snapshot access at all — a bogus, never-created
    directory must not raise."""
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / "does-not-exist")
    outstanding = [{"body": "x"}]
    assert ps.match_notes_prefix_run(outstanding, "x") == outstanding
    assert not (tmp_path / "does-not-exist").exists()


# ---- consume_notes_matching: matches + deletes, in order -----------------

def test_consume_notes_matching_deletes_the_matched_notes():
    _note(SESSION, "no it was a test")

    consumed = ps.consume_notes_matching(SESSION, "no it was a test")

    assert [n["body"] for n in consumed] == ["no it was a test"]
    assert ps.list_outstanding_notes(SESSION) == []


def test_consume_notes_matching_leaves_unmatched_notes_outstanding():
    _note(SESSION, "first")
    _note(SESSION, "second")

    consumed = ps.consume_notes_matching(SESSION, "first")

    assert [n["body"] for n in consumed] == ["first"]
    remaining = ps.list_outstanding_notes(SESSION)
    assert [n["body"] for n in remaining] == ["second"]


def test_consume_notes_matching_no_match_deletes_nothing():
    _note(SESSION, "unrelated")

    consumed = ps.consume_notes_matching(SESSION, "absorbed something else")

    assert consumed == []
    assert len(ps.list_outstanding_notes(SESSION)) == 1


def test_consume_notes_matching_returns_full_note_dicts_not_just_bodies():
    _note(SESSION, "check on that", msg_id=42)

    consumed = ps.consume_notes_matching(SESSION, "check on that")

    assert consumed[0]["msg_id"] == 42
    assert consumed[0]["chat_id"] == 1
    assert consumed[0]["raw_text"] == "check on that"


# ---- deliberately does NOT touch the merged policy snapshot --------------

def test_consume_notes_matching_never_writes_the_merged_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    _note(SESSION, "no it was a test", sender_key=(1, 1))

    ps.consume_notes_matching(SESSION, "no it was a test")

    assert ps.read_snapshot(SESSION) is None, (
        "consume_notes_matching must never write the canonical snapshot "
        "— that's _match_and_promote's job for a genuine pick-up, and "
        "an absorbed message never gets its own UserPromptSubmit to "
        "merge into in the first place"
    )


def test_consume_notes_matching_leaves_a_pre_existing_snapshot_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    ps.write_merged_snapshot(SESSION, {"sentinel": "unchanged"})
    _note(SESSION, "no it was a test")

    ps.consume_notes_matching(SESSION, "no it was a test")

    assert ps.read_snapshot(SESSION) == {"sentinel": "unchanged"}


# ---- R7: an already-swept note is simply not found, never resurrected ---

def test_consume_notes_matching_ttl_swept_note_yields_empty_no_error():
    """R7 (design.md): the 5s Stop sweep — or the TTL prune
    ``list_outstanding_notes`` itself performs — may have already
    removed a note by the time an absorption line is matched against
    it. This must degrade to "nothing found", never raise, and never
    resurrect the note."""
    path = _note(SESSION, "long gone")
    data = json.loads(path.read_text())
    from aipager.state import QUEUE_MAX_AGE_SECONDS
    data["queued_at"] = time.time() - QUEUE_MAX_AGE_SECONDS - 10
    path.write_text(json.dumps(data))

    consumed = ps.consume_notes_matching(SESSION, "long gone")

    assert consumed == []
    assert not path.exists(), "the TTL prune still unlinks it as a side effect"


def test_consume_notes_matching_note_deleted_out_of_band_yields_empty():
    """A note deleted by some OTHER path (e.g. the Stop sweep itself,
    already run) between being written and being matched against — the
    match simply finds nothing; no crash, no resurrection."""
    path = _note(SESSION, "absorbed already")
    path.unlink()

    consumed = ps.consume_notes_matching(SESSION, "absorbed already")

    assert consumed == []
