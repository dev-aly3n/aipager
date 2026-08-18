"""Black-box tests for design.md success criterion 1 (and parts of 13, 17):

`TrackedSession.busy_msg_id`'s getter/setter round-trips all four sentinel
values (`None`, `0`, `-1`, a positive int) correctly on both an empty and a
non-empty stack, matching entrypoints.md's documented contract:

  - Getter: `None` (no live message), `0` (confirmed gone), `-1` (send-in-
    flight claim), or a positive Telegram message id.
  - Setter: accepts any of the four and always leaves the getter returning
    exactly that value immediately afterward. Setting `None` clears the
    ENTIRE stack. Setting anything else establishes/updates a SINGLE live
    message — on an empty stack this pushes a fresh entry; on a non-empty
    stack this mutates the top entry's id in place (it does not add a
    second entry).

Also covers `stack_top_kind()` (`"busy"` / `"compacting"` / `None`) and
`pop_compacting()`'s no-op contract on an empty stack / a non-compacting
top / a double-pop — feeding criterion 17's "no-op-on-mismatch" claim.

Written strictly from entrypoints.md's "Exported functions" section — no
`aipager/state.py` source was read beyond what that section documents.
"""

from __future__ import annotations

import pytest

from aipager.state import TrackedSession


def _sess(label="jim"):
    return TrackedSession(name=f"claude-{label}", label=label)


# ===== Criterion 1: the empty-stack half of the Decision-1 table ==========

@pytest.mark.parametrize("value", [None, -1, 0, 7])
def test_setter_on_empty_stack_round_trips_exactly(value):
    sess = _sess()
    assert sess.busy_msg_id is None  # precondition: genuinely empty
    sess.busy_msg_id = value
    assert sess.busy_msg_id == value, (
        f"setting {value!r} on an empty stack must make the getter "
        f"return exactly {value!r} immediately afterward"
    )


def test_none_on_empty_stack_is_a_true_no_op():
    """entrypoints.md: setting None 'clears' a stack that has nothing to
    clear -- must not raise and must leave the getter at None."""
    sess = _sess()
    sess.busy_msg_id = None
    assert sess.busy_msg_id is None


# ===== Criterion 1: the non-empty-stack half of the Decision-1 table ======

@pytest.mark.parametrize("value", [-1, 0, 7])
def test_setter_on_non_empty_stack_mutates_top_in_place(value):
    """A non-null value on an already-non-empty stack must overwrite the
    SAME logical slot (top entry), not create a second entry underneath
    it -- verified by round-tripping through a second overwrite."""
    sess = _sess()
    sess.busy_msg_id = 100  # establish a non-empty stack
    sess.busy_msg_id = value
    assert sess.busy_msg_id == value
    # And it must still be mutable again afterward (proves "in place",
    # not "pushed a second layer that now shadows the first forever").
    sess.busy_msg_id = 200
    assert sess.busy_msg_id == 200


def test_none_on_non_empty_stack_clears_the_whole_stack_not_one_layer():
    """This is Decision 1's central, deliberately-surprising rule: None
    NEVER pops just the top -- it always clears everything. Verified by
    round-tripping a compacting layer over a busy layer, then setting
    None, then confirming there is nothing left to resume."""
    sess = _sess()
    sess.busy_msg_id = 42
    sess.push_compacting(msg_id=42, now=1000.0, deadline_seconds=180.0)
    assert sess.stack_top_kind() == "compacting"
    sess.busy_msg_id = None
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None
    # A pop_compacting() after a None-clear must be the documented no-op,
    # not resurrect the busy layer that was "underneath" -- there is
    # nothing underneath by design.
    assert sess.pop_compacting() is None
    assert sess.busy_msg_id is None


# ===== stack_top_kind() ====================================================

def test_stack_top_kind_none_when_empty():
    assert _sess().stack_top_kind() is None


def test_stack_top_kind_busy_after_plain_assignment():
    sess = _sess()
    sess.busy_msg_id = 42
    assert sess.stack_top_kind() == "busy"


def test_stack_top_kind_compacting_after_push():
    sess = _sess()
    sess.busy_msg_id = 42
    sess.push_compacting(msg_id=42, now=1000.0, deadline_seconds=180.0)
    assert sess.stack_top_kind() == "compacting"


# ===== pop_compacting(): the no-op-on-mismatch contract (criterion 17) ====

def test_pop_compacting_on_totally_empty_stack_is_a_safe_noop():
    sess = _sess()
    assert sess.pop_compacting() is None
    assert sess.busy_msg_id is None


def test_pop_compacting_on_busy_only_top_is_a_safe_noop():
    """PostCompact arriving on a session whose top is an ordinary busy
    card (no compaction ever recorded) must not touch it."""
    sess = _sess()
    sess.busy_msg_id = 10
    assert sess.pop_compacting() is None
    assert sess.busy_msg_id == 10  # untouched


def test_pop_compacting_twice_in_a_row_is_idempotent():
    sess = _sess()
    sess.busy_msg_id = 10
    sess.push_compacting(msg_id=10, now=1.0, deadline_seconds=5.0)
    first = sess.pop_compacting()
    second = sess.pop_compacting()
    assert first is not None  # a real compaction was recorded and removed
    assert second is None  # the second call finds nothing left to pop
    assert sess.busy_msg_id == 10  # reverted to the busy layer underneath


def test_pop_compacting_reverts_to_prior_busy_id_when_one_existed():
    sess = _sess()
    sess.busy_msg_id = 77
    sess.push_compacting(msg_id=77, now=1.0, deadline_seconds=5.0)
    sess.pop_compacting()
    assert sess.busy_msg_id == 77
    assert sess.stack_top_kind() == "busy"


def test_pop_compacting_leaves_none_when_nothing_was_live_before_push():
    """A fresh compaction that sent its own new message (nothing was
    live before it) must pop back to genuinely empty, not resurrect a
    phantom busy id."""
    sess = _sess()
    assert sess.busy_msg_id is None
    sess.push_compacting(msg_id=999, now=1.0, deadline_seconds=5.0)
    assert sess.busy_msg_id == 999
    sess.pop_compacting()
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None


# ===== Criterion 13 (property half): 0 is distinguishable from None,     ==
# ===== regardless of which kind currently occupies the top ================

def test_zero_is_not_none_on_a_busy_top():
    sess = _sess()
    sess.busy_msg_id = 42
    sess.busy_msg_id = 0
    assert sess.busy_msg_id == 0
    assert sess.busy_msg_id is not None
    # Matches every production consumer's truthiness/">0" usage today.
    assert not (sess.busy_msg_id and sess.busy_msg_id > 0)


def test_zero_is_not_none_on_a_compacting_top():
    """The 'RichMessageGone -> 0' contract must hold identically whether
    the live message is currently showing 'busy' or 'compacting' text --
    the setter does not special-case kind."""
    sess = _sess()
    sess.busy_msg_id = 42
    sess.push_compacting(msg_id=42, now=1.0, deadline_seconds=5.0)
    assert sess.stack_top_kind() == "compacting"
    sess.busy_msg_id = 0
    assert sess.busy_msg_id == 0
    assert sess.busy_msg_id is not None
    # The compacting *kind* itself is untouched by the value mutation --
    # only the msg_id changed. pop_compacting() must still find it.
    assert sess.stack_top_kind() == "compacting"
    assert sess.pop_compacting() is not None
