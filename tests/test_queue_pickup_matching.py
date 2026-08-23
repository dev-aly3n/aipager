"""``notify_hook._match_and_promote`` — the pick-up matcher (design.md
"queue handoff").

White-box: entrypoints.md explicitly excludes this function from the
Tester's contract ("exercise only through its effect on reactions and
on decide()'s outcome"), so this is the Developer's own test, calling
the function directly. Covers: full match, partial-prefix match, no
match at all (the all-outstanding fallback), separator-agnostic
batching (the matcher is deliberately format-unaware — intent.md's
confirmed unknown about how Claude batches queued messages), a
mixed-sender note held BEFORE it ever reaches the matcher, and TTL
pruning.

``notes_dir``/``snapshot_path`` are isolated to ``tmp_path`` — the
former by the suite-wide autouse fixture in ``tests/conftest.py``, the
latter explicitly here.
"""

from __future__ import annotations

import time

import pytest

from aipager import policy_snapshot as ps
from aipager.bot import transport
from aipager.dtach import notify_hook


@pytest.fixture(autouse=True)
def _isolate_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")


_counter = {"n": 0}


def _note(session, body, *, sender_key=(1, 1), style_text="", reply_context=""):
    """Write a note, then force a strictly-increasing ``queued_at`` —
    ``write_note`` stamps real ``time.time()``, which is fine-grained
    enough in production but could tie between two calls made back to
    back in a fast test loop, making sort order (and therefore "which
    note is oldest") nondeterministic. An explicit, monotonic counter
    removes that flakiness without changing what's under test."""
    import json as _json
    path = ps.write_note(
        session, None, None, None,
        msg_id=len(list(ps.notes_dir(session).glob("*.json"))) + 1,
        chat_id=1, sender_key=sender_key,
        body=body, raw_text=body,
        style_text=style_text, reply_context=reply_context,
    )
    _counter["n"] += 1
    data = _json.loads(path.read_text())
    # Relative to real time.time(), not a bare counter — an absolute
    # tiny integer here would read as ancient by the TTL check
    # (queued_at < now - QUEUE_MAX_AGE_SECONDS), pruning every note
    # this helper writes before the matcher ever sees it.
    data["queued_at"] = time.time() + _counter["n"]
    path.write_text(_json.dumps(data))
    return path, body


def _write_note_with_timestamp(session, body, queued_at, *, sender_key=(1, 1)):
    """Write a note then rewrite its queued_at — for TTL tests, where the
    public write_note() always stamps "now"."""
    import json as _json
    path, _ = _note(session, body, sender_key=sender_key)
    data = _json.loads(path.read_text())
    data["queued_at"] = queued_at
    path.write_text(_json.dumps(data))
    return path


SESSION = "claude-pickup-x"


# ---- full match ---------------------------------------------------------

def test_full_match_consumes_the_single_note(tmp_path):
    _note(SESSION, "do the thing")

    consumed, expired = notify_hook._match_and_promote(SESSION, "do the thing")

    assert [n["body"] for n in consumed] == ["do the thing"]
    assert expired == []
    assert ps.list_outstanding_notes(SESSION) == []  # consumed → deleted


def test_full_match_writes_the_consumed_notes_merge_to_the_canonical_snapshot():
    _note(SESSION, "do the thing", style_text="Keep it brief.")

    notify_hook._match_and_promote(SESSION, "do the thing")

    snap = ps.read_snapshot(SESSION)
    assert snap is not None
    assert snap["style_text"] == "Keep it brief."


# ---- partial-prefix match (matched run) ----------------------------------

def test_partial_match_consumes_only_the_matching_prefix():
    _note(SESSION, "first message")
    _note(SESSION, "second message")
    _note(SESSION, "third message")

    # The prompt only contains the first two — matcher stops at the
    # third, which was never found.
    consumed, expired = notify_hook._match_and_promote(
        SESSION, "first message\n\nsecond message",
    )

    assert [n["body"] for n in consumed] == ["first message", "second message"]
    remaining = ps.list_outstanding_notes(SESSION)
    assert [n["body"] for n in remaining] == ["third message"]


def test_partial_match_requires_order_not_just_presence():
    """Substring-in-order, not "all present anywhere" — a prompt that
    contains both bodies but with ``beta`` BEFORE ``alpha`` must only
    match ``alpha`` (the older, first-checked note): the search for
    ``beta`` starts AFTER wherever ``alpha`` was found, and ``beta``
    only appears earlier in the string, so it is never found there. The
    design's own soundness argument only holds for a genuine
    oldest-first prefix run, not "all bodies present somewhere"."""
    _note(SESSION, "alpha")
    _note(SESSION, "beta")

    consumed, _ = notify_hook._match_and_promote(
        SESSION, "beta comes first, then alpha comes second",
    )
    assert [n["body"] for n in consumed] == ["alpha"]
    remaining = ps.list_outstanding_notes(SESSION)
    assert [n["body"] for n in remaining] == ["beta"]


# ---- no match at all → all-outstanding fallback --------------------------

def test_no_match_falls_back_to_merging_every_outstanding_note_none_deleted():
    _note(SESSION, "totally unrelated queued text")

    consumed, expired = notify_hook._match_and_promote(SESSION, "Claude reworded everything")

    assert consumed == []
    assert expired == []
    # Nothing deleted — an unmatched note stays outstanding and feeds
    # the next merge (design.md's soundness argument).
    remaining = ps.list_outstanding_notes(SESSION)
    assert [n["body"] for n in remaining] == ["totally unrelated queued text"]


def test_no_match_still_writes_a_merged_snapshot_from_all_outstanding():
    _note(SESSION, "unrelated", style_text="Answer at length.")

    notify_hook._match_and_promote(SESSION, "something else entirely")

    snap = ps.read_snapshot(SESSION)
    assert snap is not None
    assert snap["style_text"] == "Answer at length."


def test_empty_directory_merges_to_the_floor():
    consumed, expired = notify_hook._match_and_promote(SESSION, "anything at all")
    assert consumed == []
    assert expired == []
    snap = ps.read_snapshot(SESSION)
    assert snap == ps.FLOOR_SNAPSHOT


# ---- separator-agnostic batching ------------------------------------------

@pytest.mark.parametrize("separator", [
    "\n", "\n\n", "\n---\n", " | ", "\n>>> ", "",
])
def test_matches_regardless_of_batching_separator(separator):
    """The matcher is deliberately format-unaware (intent.md's confirmed
    unknown: how Claude actually batches queued messages into one
    prompt) — substring-in-order must match no matter what glue Claude
    puts between the original messages."""
    _note(SESSION, "message one")
    _note(SESSION, "message two")

    prompt = f"message one{separator}message two"
    consumed, _ = notify_hook._match_and_promote(SESSION, prompt)

    assert [n["body"] for n in consumed] == ["message one", "message two"]


# ---- mixed-sender: held BEFORE it ever reaches the matcher ---------------

def test_mixed_sender_note_is_flagged_before_any_matching_happens():
    """The mixed-sender rule lives upstream of the matcher entirely
    (``transport.mixed_sender_note_outstanding``, consulted by
    ``_hold_for_open_dialog`` and Retry) — a message from a second
    sender never reaches ``_inject_prompt``/``write_note`` at all while
    held, so this asserts the upstream check directly rather than
    faking a downstream symptom."""
    class _Sess:
        name = SESSION
        scope_chat_id = 555

    class _User:
        def __init__(self, uid):
            self.id = uid

    class _Update:
        def __init__(self, uid):
            self.effective_user = _User(uid)

    sess = _Sess()
    _note(SESSION, "sender A's message", sender_key=(555, 111))

    # Same sender (111) — never held.
    assert transport.mixed_sender_note_outstanding(sess, _Update(111)) is False
    # A different sender (222) — held.
    assert transport.mixed_sender_note_outstanding(sess, _Update(222)) is True


# ---- TTL pruning -----------------------------------------------------------

def test_ttl_expired_note_is_pruned_and_reported_as_expired():
    from aipager.state import QUEUE_MAX_AGE_SECONDS

    now = time.time()
    _write_note_with_timestamp(
        SESSION, "long-forgotten message", now - QUEUE_MAX_AGE_SECONDS - 1,
    )
    _note(SESSION, "fresh message")

    consumed, expired = notify_hook._match_and_promote(SESSION, "fresh message")

    assert [n["body"] for n in consumed] == ["fresh message"]
    assert [n["body"] for n in expired] == ["long-forgotten message"]
    # The expired note's FILE is gone — pruning is a real unlink, not
    # just an exclusion from the returned list.
    remaining_files = list(ps.notes_dir(SESSION).glob("*.json"))
    assert len(remaining_files) == 0  # both consumed (matched) and expired


def test_note_just_under_the_ttl_is_not_pruned():
    from aipager.state import QUEUE_MAX_AGE_SECONDS

    now = time.time()
    _write_note_with_timestamp(SESSION, "still fresh enough", now - (QUEUE_MAX_AGE_SECONDS - 5))

    outstanding = ps.list_outstanding_notes(SESSION, now=now)
    assert [n["body"] for n in outstanding] == ["still fresh enough"]
