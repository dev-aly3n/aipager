"""Tests for design.md's `SessionStart(source="compact")` fix: a
compaction whose `PreCompact` recorded `pre_compact_pct == 0` now fires
`compact_done` (with `before_pct == 0`) instead of silently no-op'ing —
the second, independent half of the observed live-bug wedge (the first
half is the deadline sweeper, covered in
test_session_monitor_compact_sweep.py).

Also covers criterion 17's "PostCompact after SessionEnd already
cleared everything" race: PostCompact's own handler never touches the
live message stack (unchanged), so it must never raise or fire a
duplicate notification regardless of what SessionEnd already did to the
session's stack.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    return registry, recv, notify_fn


def _send(recv, run_async, **fields):
    payload = json.dumps(fields).encode()
    run_async(recv._on_datagram(payload))


def test_session_start_compact_zero_before_pct_now_fires_compact_done(receiver, run_async):
    """Criterion 5: pre_compact_pct == 0 (PreCompact recorded 0% context
    — nothing to record) previously left this a silent no-op forever;
    now it fires compact_done with before_pct == 0 the moment the
    confirming hook arrives."""
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pre_compact_pct = 0
    _send(recv, run_async,
          hook_event_name="SessionStart",
          session="claude-jim",
          source="compact",
          sl_tokens={"context_pct": 3})
    notify_fn.assert_awaited_once()
    _, event, ctx = notify_fn.await_args.args
    assert event == "compact_done"
    assert ctx["before_pct"] == 0
    assert ctx["after_pct"] == 3


def test_session_start_compact_zero_before_pct_resets_pre_compact_pct(receiver, run_async):
    registry, recv, _ = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pre_compact_pct = 0
    _send(recv, run_async,
          hook_event_name="SessionStart",
          session="claude-jim",
          source="compact",
          sl_tokens={"context_pct": 3})
    assert sess.pre_compact_pct == 0


def test_session_start_compact_nonzero_before_pct_still_defers_when_stale(receiver, run_async):
    """Regression check: the existing before_pct > 0 defer-on-stale-file
    behaviour (post_pct >= before_pct) is untouched by the `and` rewrite."""
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.pre_compact_pct = 80
    _send(recv, run_async,
          hook_event_name="SessionStart",
          session="claude-jim",
          source="compact",
          sl_tokens={"context_pct": 85})
    notify_fn.assert_not_awaited()
    assert sess.pre_compact_pct == 80  # preserved for next chance


# ---- criterion 17: PostCompact after SessionEnd already cleared everything

def test_post_compact_after_stack_already_cleared_does_not_raise_or_duplicate_notify(
    receiver, run_async,
):
    """Simulates research.md's documented race: SessionEnd (via
    notify.py's "session_end" branch, exercised separately in
    test_bot_notify.py) has already cleared the stack by the time
    PostCompact's datagram arrives. PostCompact's own handler never
    touches busy_msg_id/the stack (unchanged) — it must be a safe no-op
    regardless."""
    registry, recv, notify_fn = receiver
    sess = registry.get_or_create("claude-jim")
    sess.busy_msg_id = None  # stack already empty, as SessionEnd would leave it
    sess.compact_started_at = 123.0
    # MUST NOT raise
    _send(recv, run_async, hook_event_name="PostCompact", session="claude-jim")
    notify_fn.assert_not_awaited()  # PostCompact never fires a notification
    assert sess.busy_msg_id is None  # still empty, no phantom entry
    assert sess.compact_started_at is None  # the in-flight marker still clears
