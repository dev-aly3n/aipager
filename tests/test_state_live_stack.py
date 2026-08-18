"""Tests for the live message stack (design.md "Live Message Stack").

Covers Decision 1's full busy_msg_id getter/setter matrix (4 setter
values × {empty, non-empty} stack), push_compacting/pop_compacting/
stack_top_kind, and the load() migration for a state file written by
pre-feature code (Decision 7).

``sess._live_stack`` is read directly in a few assertions below purely
to confirm the STACK SHAPE (length, kind, which physical msg_id) that
the public property/methods produce — never to bypass them. Production
code and integration tests must never do this (see entrypoints.md's
"NOT exported" section); it is fine here because this file's whole job
is verifying that private shape.
"""

from __future__ import annotations

import json

from aipager.state import SessionRegistry, Status, TrackedSession


def _sess(label="jim", *, status=Status.IDLE) -> TrackedSession:
    return TrackedSession(name=f"claude-{label}", label=label, status=status)


# ===== busy_msg_id getter/setter — Decision 1's full matrix ===============

# ---- empty stack -----------------------------------------------------

def test_setter_none_on_empty_stack_is_noop():
    sess = _sess()
    sess.busy_msg_id = None
    assert sess.busy_msg_id is None
    assert sess._live_stack == []


def test_setter_negative_one_on_empty_stack_pushes_busy_entry():
    sess = _sess()
    sess.busy_msg_id = -1
    assert sess.busy_msg_id == -1
    assert len(sess._live_stack) == 1
    assert sess._live_stack[0].kind == "busy"
    assert sess._live_stack[0].msg_id == -1


def test_setter_zero_on_empty_stack_pushes_busy_entry():
    sess = _sess()
    sess.busy_msg_id = 0
    assert sess.busy_msg_id == 0
    assert len(sess._live_stack) == 1
    assert sess._live_stack[0].kind == "busy"


def test_setter_positive_int_on_empty_stack_pushes_busy_entry():
    sess = _sess()
    sess.busy_msg_id = 42
    assert sess.busy_msg_id == 42
    assert len(sess._live_stack) == 1
    assert sess._live_stack[0].kind == "busy"
    assert sess._live_stack[0].msg_id == 42


# ---- non-empty stack ---------------------------------------------------

def test_setter_none_on_non_empty_stack_clears_entire_stack():
    """None clears the WHOLE stack, not just the top layer (Decision 1) —
    the specific case this test proves: a compacting layer pushed OVER a
    busy layer is entirely wiped by one None assignment, not popped down
    to the busy layer beneath."""
    sess = _sess()
    sess.busy_msg_id = 42  # busy layer
    sess.push_compacting(42, now=1000.0, deadline_seconds=None)
    assert len(sess._live_stack) == 2  # busy + compacting, sanity check
    sess.busy_msg_id = None
    assert sess.busy_msg_id is None
    assert sess._live_stack == []


def test_setter_negative_one_on_non_empty_stack_mutates_top_in_place():
    sess = _sess()
    sess.busy_msg_id = 42
    sess.busy_msg_id = -1
    assert sess.busy_msg_id == -1
    assert len(sess._live_stack) == 1  # mutated, not pushed a second entry
    assert sess._live_stack[0].kind == "busy"


def test_setter_zero_on_non_empty_stack_mutates_top_in_place():
    sess = _sess()
    sess.busy_msg_id = 42
    sess.busy_msg_id = 0
    assert sess.busy_msg_id == 0
    assert len(sess._live_stack) == 1


def test_setter_zero_on_compacting_top_mutates_that_entry_in_place():
    """Criterion 13: RichMessageGone during a COMPACTING-card edit sets
    the observable value to 0, not None — and the entry stays kind
    "compacting" (mutate-in-place, not clear-and-replace)."""
    sess = _sess()
    sess.busy_msg_id = 42
    sess.push_compacting(42, now=1000.0, deadline_seconds=None)
    sess.busy_msg_id = 0  # simulates the RichMessageGone handler
    assert sess.busy_msg_id == 0
    assert sess.busy_msg_id is not None  # observably 0, never collapsed to None
    assert len(sess._live_stack) == 2  # busy layer beneath untouched
    assert sess._live_stack[-1].kind == "compacting"
    assert sess._live_stack[-1].msg_id == 0
    assert sess._live_stack[0].msg_id == 42  # busy layer's id unchanged
    # And it is falsy exactly like a legacy consumer checking
    # `sess.busy_msg_id and sess.busy_msg_id > 0` expects.
    assert not (sess.busy_msg_id and sess.busy_msg_id > 0)


def test_setter_positive_int_on_non_empty_stack_mutates_top_in_place():
    """Covers the -1-claim -> real-id resolution sequence
    (animation.py's send-busy path)."""
    sess = _sess()
    sess.busy_msg_id = -1  # sentinel claim
    sess.busy_msg_id = 777  # resolved to the real id
    assert sess.busy_msg_id == 777
    assert len(sess._live_stack) == 1
    assert sess._live_stack[0].kind == "busy"


# ===== push_compacting / pop_compacting / stack_top_kind ===================

def test_push_compacting_reuses_caller_supplied_msg_id():
    sess = _sess()
    sess.busy_msg_id = 42
    sess.push_compacting(42, now=1000.0, deadline_seconds=180.0)
    assert sess.busy_msg_id == 42
    assert sess.stack_top_kind() == "compacting"


def test_push_compacting_sets_deadline_from_now_plus_seconds():
    sess = _sess()
    sess.push_compacting(1, now=1000.0, deadline_seconds=180.0)
    assert sess._live_stack[-1].deadline == 1180.0


def test_push_compacting_none_deadline_seconds_means_no_expiry():
    sess = _sess()
    sess.push_compacting(1, now=1000.0, deadline_seconds=None)
    assert sess._live_stack[-1].deadline is None


def test_pop_compacting_reveals_busy_layer_beneath():
    sess = _sess()
    sess.busy_msg_id = 42
    sess.push_compacting(42, now=1000.0, deadline_seconds=None)
    popped = sess.pop_compacting()
    assert popped is not None
    assert popped.msg_id == 42
    assert sess.busy_msg_id == 42  # reverted to the busy layer
    assert sess.stack_top_kind() == "busy"


def test_pop_compacting_on_solo_compacting_entry_empties_stack():
    sess = _sess()
    sess.push_compacting(555, now=1000.0, deadline_seconds=None)
    popped = sess.pop_compacting()
    assert popped is not None
    assert popped.msg_id == 555
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None


def test_pop_compacting_is_a_noop_on_busy_top():
    """A pop_compacting() call that arrives when the top is "busy" (not
    "compacting") must not touch anything — the idempotent no-op
    contract, exercised against a mismatched kind rather than an empty
    stack."""
    sess = _sess()
    sess.busy_msg_id = 42
    result = sess.pop_compacting()
    assert result is None
    assert sess.busy_msg_id == 42
    assert sess.stack_top_kind() == "busy"


def test_pop_compacting_is_a_noop_on_empty_stack():
    sess = _sess()
    assert sess.pop_compacting() is None
    assert sess.busy_msg_id is None


def test_pop_compacting_called_twice_is_idempotent():
    """Research.md's documented race: PostCompact after SessionEnd
    already cleared everything. The second pop must be a silent no-op."""
    sess = _sess()
    sess.push_compacting(1, now=1000.0, deadline_seconds=None)
    first = sess.pop_compacting()
    second = sess.pop_compacting()
    assert first is not None
    assert second is None
    assert sess.busy_msg_id is None


def test_stack_top_kind_empty_stack_is_none():
    sess = _sess()
    assert sess.stack_top_kind() is None


def test_stack_top_kind_busy():
    sess = _sess()
    sess.busy_msg_id = 42
    assert sess.stack_top_kind() == "busy"


def test_stack_top_kind_compacting():
    sess = _sess()
    sess.push_compacting(1, now=1000.0, deadline_seconds=None)
    assert sess.stack_top_kind() == "compacting"


# ===== load() migration (Decision 7) =======================================

def test_load_pre_feature_state_file_with_positive_busy_msg_id(tmp_state_file):
    """A state file written by 3a9e2ab-era code (busy_msg_id: <int>) loads
    unchanged: sess.busy_msg_id returns that same id, and the stack holds
    exactly one kind="busy" entry."""
    data = {
        "version": 1,
        "last_active_session": "claude-jim",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-jim": {
                "name": "claude-jim",
                "label": "jim",
                "busy_msg_id": 9001,
            },
        },
    }
    tmp_state_file.write_text(json.dumps(data))
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-jim")
    assert sess is not None
    assert sess.busy_msg_id == 9001
    assert sess.stack_top_kind() == "busy"
    assert len(sess._live_stack) == 1


def test_load_pre_feature_state_file_with_null_busy_msg_id(tmp_state_file):
    """busy_msg_id: null loads to an EMPTY stack, not a phantom entry."""
    data = {
        "version": 1,
        "last_active_session": "",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-jim": {
                "name": "claude-jim",
                "label": "jim",
                "busy_msg_id": None,
            },
        },
    }
    tmp_state_file.write_text(json.dumps(data))
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-jim")
    assert sess is not None
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None
    assert sess._live_stack == []


def test_load_missing_busy_msg_id_key_defaults_to_empty_stack(tmp_state_file):
    """A session dict with no busy_msg_id key at all (older/hand-edited
    file) must not raise — sd.get("busy_msg_id") returns None, same as
    the explicit-null case above."""
    data = {
        "version": 1,
        "last_active_session": "",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-jim": {"name": "claude-jim", "label": "jim"},
        },
    }
    tmp_state_file.write_text(json.dumps(data))
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-jim")
    assert sess is not None
    assert sess.busy_msg_id is None


def test_save_then_load_round_trip_preserves_positive_busy_msg_id(tmp_state_file):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_msg_id = 4242
    registry._sessions["claude-jim"] = sess
    registry.save()

    reloaded = SessionRegistry()
    reloaded.load()
    got = reloaded.get("claude-jim")
    assert got is not None
    assert got.busy_msg_id == 4242


def test_save_never_persists_sentinel_values(tmp_state_file):
    """save()'s existing normalization (state.py's busy_msg_id special
    case) must keep collapsing -1 and 0 to null — unaffected by the
    property conversion, since save() reads via getattr() transparently."""
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_msg_id = -1
    registry._sessions["claude-jim"] = sess
    registry.save()
    on_disk = json.loads(tmp_state_file.read_text())
    assert on_disk["sessions"]["claude-jim"]["busy_msg_id"] is None
