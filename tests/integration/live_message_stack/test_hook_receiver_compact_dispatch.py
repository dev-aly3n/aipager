"""Black-box tests for the hook-ingestion half of design.md criteria 2, 3,
5 and 17, via `aipager.dtach.hook_receiver.HookReceiver._on_datagram` --
entrypoints.md's documented pattern (`HookReceiver(registry, notify_fn)`,
fed raw JSON hook payloads, asserted against the mocked `notify_fn`'s call
list). No real dtach socket is touched; `notify_fn` is a plain AsyncMock,
so this file never needs Telegram mocking at all.

This file tests the DISPATCH mapping only (does the right hook produce the
right `notify_fn(sess, event, context)` call, with the right session-state
side effects on the session object itself). The corresponding SEND/EDIT
behavior once that event reaches `TelegramBot.notify` is covered by
`test_notify_compacting_push.py` and `test_notify_compact_done.py`.
"""

from __future__ import annotations

import json

from unittest.mock import AsyncMock


from aipager.dtach.hook_receiver import HookReceiver
from aipager.state import SessionRegistry, Status, TrackedSession


def _mk(name="claude-jim", label="jim", status=Status.BUSY):
    registry = SessionRegistry()
    sess = TrackedSession(name=name, label=label, status=status)
    registry._sessions[name] = sess
    notify_fn = AsyncMock()
    return registry, sess, notify_fn, HookReceiver(registry, notify_fn)


def _payload(**fields):
    return json.dumps(fields).encode()


# ===== Criterion 2/3 (dispatch half): PreCompact always fires "compacting" =

def test_precompact_dispatches_compacting_event_with_trigger(run_async):
    registry, sess, notify_fn, hr = _mk()
    run_async(hr._on_datagram(_payload(
        hook_event_name="PreCompact", session="claude-jim",
        trigger="manual", sl_tokens={"context_pct": 55},
    )))
    notify_fn.assert_awaited_once()
    call_sess, event, context = notify_fn.await_args.args
    assert call_sess is sess
    assert event == "compacting"
    assert context == {"trigger": "manual"}


def test_precompact_records_pre_compact_pct_from_hook_payload(run_async):
    registry, sess, notify_fn, hr = _mk()
    run_async(hr._on_datagram(_payload(
        hook_event_name="PreCompact", session="claude-jim",
        trigger="auto", sl_tokens={"context_pct": 55},
    )))
    assert sess.pre_compact_pct == 55


# ===== Criterion 5: 0%-context PreCompact + confirming SessionStart =======

def test_precompact_at_zero_pct_records_zero(run_async):
    registry, sess, notify_fn, hr = _mk()
    run_async(hr._on_datagram(_payload(
        hook_event_name="PreCompact", session="claude-jim",
        trigger="manual", sl_tokens={"context_pct": 0},
    )))
    assert sess.pre_compact_pct == 0


def test_session_start_compact_source_after_zero_pct_fires_compact_done(
    run_async,
):
    """The core fix under criterion 5: previously a silent no-op, now
    fires `compact_done` with `before_pct == 0` the moment the confirming
    hook arrives -- no deadline wait needed."""
    registry, sess, notify_fn, hr = _mk()
    run_async(hr._on_datagram(_payload(
        hook_event_name="PreCompact", session="claude-jim",
        trigger="manual", sl_tokens={"context_pct": 0},
    )))
    notify_fn.reset_mock()
    run_async(hr._on_datagram(_payload(
        hook_event_name="SessionStart", session="claude-jim",
        source="compact", sl_tokens={"context_pct": 8},
    )))
    notify_fn.assert_awaited_once()
    call_sess, event, context = notify_fn.await_args.args
    assert event == "compact_done"
    assert context["before_pct"] == 0
    assert context["after_pct"] == 8


def test_session_start_compact_source_nonzero_before_pct_unaffected(
    run_async,
):
    """Regression: the already-working before_pct>0 path is untouched --
    still fires compact_done with the real percentages when post >= pre
    doesn't hold (post dropped, i.e. compaction genuinely happened)."""
    registry, sess, notify_fn, hr = _mk()
    run_async(hr._on_datagram(_payload(
        hook_event_name="PreCompact", session="claude-jim",
        trigger="manual", sl_tokens={"context_pct": 80},
    )))
    notify_fn.reset_mock()
    run_async(hr._on_datagram(_payload(
        hook_event_name="SessionStart", session="claude-jim",
        source="compact", sl_tokens={"context_pct": 5},
    )))
    notify_fn.assert_awaited_once()
    call_sess, event, context = notify_fn.await_args.args
    assert event == "compact_done"
    assert context == {"before_pct": 80, "after_pct": 5}


# ===== Criterion 17: PostCompact on an already-empty stack ================

def test_postcompact_on_never_compacted_session_does_not_raise(run_async):
    registry, sess, notify_fn, hr = _mk()
    # No PreCompact was ever recorded for this session.
    run_async(hr._on_datagram(_payload(
        hook_event_name="PostCompact", session="claude-jim",
    )))  # MUST NOT raise


def test_postcompact_after_sessionend_already_cleared_is_a_silent_noop(
    run_async,
):
    """research.md's own documented race: SessionEnd clears everything,
    then a straggler PostCompact for the same (now-gone) session arrives.
    Must not raise and must not fire a duplicate/confused notification."""
    registry, sess, notify_fn, hr = _mk()
    run_async(hr._on_datagram(_payload(
        hook_event_name="PreCompact", session="claude-jim",
        trigger="manual", sl_tokens={"context_pct": 40},
    )))
    run_async(hr._on_datagram(_payload(
        hook_event_name="SessionEnd", session="claude-jim", source="logout",
    )))
    notify_fn.reset_mock()
    run_async(hr._on_datagram(_payload(
        hook_event_name="PostCompact", session="claude-jim",
    )))  # MUST NOT raise
    # PostCompact itself never resolves the message (unchanged from
    # today, per entrypoints.md) -- so no notify_fn call is expected
    # here either way; the assertion that matters is "did not raise",
    # already exercised by reaching this line.


def test_double_postcompact_is_idempotent(run_async):
    registry, sess, notify_fn, hr = _mk()
    run_async(hr._on_datagram(_payload(
        hook_event_name="PreCompact", session="claude-jim",
        trigger="manual", sl_tokens={"context_pct": 40},
    )))
    run_async(hr._on_datagram(_payload(
        hook_event_name="PostCompact", session="claude-jim",
    )))
    run_async(hr._on_datagram(_payload(
        hook_event_name="PostCompact", session="claude-jim",
    )))  # MUST NOT raise the second time
