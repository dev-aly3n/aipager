"""Tests for the compact-card deadline sweep (design.md Decisions 3/5).

Pure-function coverage for `expired_compacting_sessions` (no sleeping, no
`time.monotonic()` patching — `now` is always an explicit float argument,
matching `test_session_monitor.py:29`'s established convention of setting
fields directly). Impure coverage for `SessionMonitor._scan()`'s dispatch
of the synthetic `compact_timeout` event, exercised via `run_async` with
`aipager.dtach.inject.list_sessions` monkeypatched, mirroring
`test_session_monitor.py`'s pattern.

Deadlines-already-past are constructed with a NEGATIVE `deadline_seconds`
at push time (e.g. `-1.0`) rather than by sleeping or patching the clock:
since `deadline = now_at_push + deadline_seconds`, a negative
`deadline_seconds` guarantees the deadline is already behind any later
`time.monotonic()` reading, by construction, with zero real time elapsed.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager.session_monitor import SessionMonitor, expired_compacting_sessions
from aipager.state import SessionRegistry, Status, TrackedSession


def _sess(label="jim", *, status=Status.IDLE) -> TrackedSession:
    return TrackedSession(name=f"claude-{label}", label=label, status=status)


# ===== expired_compacting_sessions (pure) ===================================

def test_no_sessions_returns_empty_list():
    assert expired_compacting_sessions({}, now=1000.0) == []


def test_session_with_no_live_message_is_not_expired():
    sess = _sess()
    assert expired_compacting_sessions({"jim": sess}, now=1000.0) == []


def test_session_with_busy_top_is_never_expired_even_far_in_the_future():
    sess = _sess()
    sess.busy_msg_id = 42  # kind="busy", deadline=None by construction
    assert expired_compacting_sessions({"jim": sess}, now=10_000_000.0) == []


def test_compacting_entry_past_its_deadline_is_expired():
    sess = _sess()
    sess.push_compacting(msg_id=1, now=1000.0, deadline_seconds=5.0)
    assert expired_compacting_sessions({"jim": sess}, now=2000.0) == ["jim"]


def test_compacting_entry_exactly_at_deadline_is_expired():
    """now >= deadline, not now > deadline — the boundary is inclusive."""
    sess = _sess()
    sess.push_compacting(msg_id=1, now=1000.0, deadline_seconds=5.0)
    assert expired_compacting_sessions({"jim": sess}, now=1005.0) == ["jim"]


def test_compacting_entry_before_its_deadline_is_not_expired():
    sess = _sess()
    sess.push_compacting(msg_id=1, now=1000.0, deadline_seconds=180.0)
    assert expired_compacting_sessions({"jim": sess}, now=1005.0) == []


def test_compacting_entry_with_no_deadline_never_expires():
    sess = _sess()
    sess.push_compacting(msg_id=1, now=1000.0, deadline_seconds=None)
    assert expired_compacting_sessions({"jim": sess}, now=10_000_000.0) == []


def test_multiple_sessions_only_the_expired_one_is_returned():
    fresh = _sess("fresh")
    fresh.push_compacting(msg_id=1, now=1000.0, deadline_seconds=180.0)
    stale = _sess("stale")
    stale.push_compacting(msg_id=2, now=1000.0, deadline_seconds=5.0)
    # Past stale's deadline (1005.0) but well before fresh's (1180.0).
    result = expired_compacting_sessions(
        {"fresh": fresh, "stale": stale}, now=1010.0,
    )
    assert result == ["stale"]


# ===== SessionMonitor._scan() dispatch (impure) =============================

def _patch_list_sessions(monkeypatch, names):
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        AsyncMock(return_value=names),
    )


def test_scan_fires_compact_timeout_for_an_expired_session(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = _sess(status=Status.IDLE)
    sess.push_compacting(msg_id=3218, now=time.monotonic(), deadline_seconds=-1.0)
    registry._sessions["claude-jim"] = sess
    notify_fn = AsyncMock()
    monitor = SessionMonitor(registry, notify_fn)
    _patch_list_sessions(monkeypatch, ["claude-jim"])

    run_async(monitor._scan())

    compact_timeout_calls = [
        c for c in notify_fn.await_args_list if c.args[1] == "compact_timeout"
    ]
    assert len(compact_timeout_calls) == 1
    called_sess, event, ctx = compact_timeout_calls[0].args
    assert called_sess is sess
    assert event == "compact_timeout"
    assert ctx["elapsed_seconds"] >= 0.0


def test_scan_does_not_fire_compact_timeout_before_the_deadline(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = _sess(status=Status.IDLE)
    sess.push_compacting(msg_id=3218, now=time.monotonic(), deadline_seconds=3600.0)
    registry._sessions["claude-jim"] = sess
    notify_fn = AsyncMock()
    monitor = SessionMonitor(registry, notify_fn)
    _patch_list_sessions(monkeypatch, ["claude-jim"])

    run_async(monitor._scan())

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "compact_timeout" not in events


def test_scan_does_not_fire_compact_timeout_for_a_plain_busy_session(monkeypatch, run_async):
    """A session with an ordinary (non-compacting) busy card, however
    long it's been live, must never trip this sweep — only `compacting`
    tops with an actual deadline do."""
    registry = SessionRegistry()
    sess = _sess(status=Status.BUSY)
    sess.busy_msg_id = 42
    registry._sessions["claude-jim"] = sess
    notify_fn = AsyncMock()
    monitor = SessionMonitor(registry, notify_fn)
    _patch_list_sessions(monkeypatch, ["claude-jim"])

    run_async(monitor._scan())

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "compact_timeout" not in events


def test_scan_is_not_gated_on_status_busy(monkeypatch, run_async):
    """The reported live bug: a compacting card observed at status=idle
    was invisible to every status==BUSY-gated watchdog. This sweep must
    fire regardless of status — asserted here against Status.IDLE
    specifically, reproducing the exact observed state."""
    registry = SessionRegistry()
    sess = _sess(status=Status.IDLE)
    sess.push_compacting(msg_id=3218, now=time.monotonic(), deadline_seconds=-1.0)
    registry._sessions["claude-jim"] = sess
    notify_fn = AsyncMock()
    monitor = SessionMonitor(registry, notify_fn)
    _patch_list_sessions(monkeypatch, ["claude-jim"])

    run_async(monitor._scan())

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "compact_timeout" in events


# ===== end-to-end through the real notify() handler (BUSY-resume /
#       non-BUSY-clear branches) ============================================

def test_scan_expired_compact_with_busy_beneath_resumes_animation(mk_bot, run_async, monkeypatch):
    registry = SessionRegistry()
    bot = mk_bot(registry)
    sess = _sess(status=Status.BUSY)
    sess.busy_msg_id = 100  # busy layer, underneath the compaction
    sess.push_compacting(msg_id=100, now=time.monotonic(), deadline_seconds=-1.0)
    registry._sessions["claude-jim"] = sess
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    monitor = SessionMonitor(registry, bot.notify)
    _patch_list_sessions(monkeypatch, ["claude-jim"])

    run_async(monitor._scan())

    bot._edit_busy_raw.assert_awaited_once()
    edited_msg_id, edited_text = bot._edit_busy_raw.await_args.args[:2]
    assert edited_msg_id == 100
    assert "didn't confirm completion" in edited_text
    assert "Compacted" not in edited_text
    bot._start_animation.assert_called_once()
    assert sess.busy_msg_id == 100
    assert sess.stack_top_kind() == "busy"


def test_scan_expired_compact_non_busy_status_clears_the_whole_stack(mk_bot, run_async, monkeypatch):
    """Reproduces the exact reported bug state: status=idle with a
    compacting card still live. After the sweep resolves it, the stack
    must be fully empty and no animation resumed."""
    registry = SessionRegistry()
    bot = mk_bot(registry)
    sess = _sess(status=Status.IDLE)
    sess.push_compacting(msg_id=3218, now=time.monotonic(), deadline_seconds=-1.0)
    registry._sessions["claude-jim"] = sess
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    monitor = SessionMonitor(registry, bot.notify)
    _patch_list_sessions(monkeypatch, ["claude-jim"])

    run_async(monitor._scan())

    bot._start_animation.assert_not_called()
    assert sess.busy_msg_id is None
    assert sess.stack_top_kind() is None
