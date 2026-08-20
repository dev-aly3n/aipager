"""Additional session_monitor tests covering the start/stop loop and
the on_sessions_changed callback."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock


from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession


async def _coroutine_returning(value):
    return value


def _mk_monitor(registry, notify_fn=None):
    async def _noop(*a, **k): return None
    return SessionMonitor(registry, notify_fn or _noop)


# ---- start / stop ------------------------------------------------------

def test_start_creates_background_task(monkeypatch, run_async):
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))

    async def _go():
        await monitor.start()
        # Cancel after a moment
        monitor.stop()
        # Give the task a chance to settle
        try:
            await monitor._task
        except asyncio.CancelledError:
            pass

    run_async(_go())


def test_stop_with_no_task_is_noop():
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    monitor.stop()  # MUST NOT raise


# ---- _scan: session_end notify failure swallowed -----------------------

def test_scan_session_end_notify_failure_swallowed(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.transcript_path = "/nope"
    registry._sessions["claude-jim"] = sess

    async def _failing_notify(*a, **k):
        raise RuntimeError("notify broken")

    monitor = _mk_monitor(registry, _failing_notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))  # session disappeared
    # MUST NOT raise
    run_async(monitor._scan())
    assert sess.status == Status.GONE


# ---- _scan: on_sessions_changed fires on additions ---------------------

def test_scan_calls_on_sessions_changed_on_new_session(monkeypatch, run_async):
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    monitor.on_sessions_changed = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-new"]))
    run_async(monitor._scan())
    monitor.on_sessions_changed.assert_awaited_once()


def test_scan_on_sessions_changed_failure_swallowed(monkeypatch, run_async):
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    async def _failing():
        raise RuntimeError("callback broken")
    monitor.on_sessions_changed = _failing
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-new"]))
    run_async(monitor._scan())  # MUST NOT raise


# ---- _scan: GONE session recovered on socket reappearance ---------------

def test_scan_recovers_gone_session_clears_gone_at(monkeypatch, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.gone_at = 1234.0
    registry._sessions["claude-jim"] = sess
    monitor = _mk_monitor(registry)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))  # came back
    run_async(monitor._scan())
    assert sess.status == Status.IDLE
    assert sess.gone_at is None


# ---- _scan: stale_busy notify failure swallowed ------------------------

def test_scan_stale_busy_notify_failure_swallowed(steady_clock, monkeypatch, run_async):
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = steady_clock() - STALE_BUSY_TIMEOUT - 60
    registry._sessions["claude-jim"] = sess

    async def _failing_notify(*a, **k):
        raise RuntimeError("notify broken")

    monitor = _mk_monitor(registry, _failing_notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    # MUST NOT raise
    run_async(monitor._scan())
    # stale_warned was set so we don't spam
    assert sess.stale_warned is True


# ---- _loop: error in scan is logged but loop continues ------------------

def test_loop_swallows_scan_exception(monkeypatch, run_async):
    """The loop catches Exception from _scan and continues to next iteration."""
    registry = SessionRegistry()
    monitor = _mk_monitor(registry)
    calls = {"n": 0}
    async def _raising_scan():
        calls["n"] += 1
        raise RuntimeError("scan broken")
    monitor._scan = _raising_scan
    # Make sleep raise CancelledError on second call → exits the loop
    sleep_calls = {"n": 0}
    async def _sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError("done")
    monkeypatch.setattr("aipager.session_monitor.asyncio.sleep", _sleep)
    async def _go():
        try:
            await monitor._loop()
        except asyncio.CancelledError:
            pass
    run_async(_go())
    # _scan ran twice — the first error was swallowed and the loop iterated again
    assert calls["n"] >= 2


# ---- _scan: stale_busy suppressed while a tool call is in flight -------

def test_scan_stale_busy_suppressed_during_tool_in_flight(monkeypatch, run_async):
    """A tool that runs longer than STALE_BUSY_TIMEOUT must NOT trigger the
    'stuck' warning, because no hooks fire between PreToolUse and
    PostToolUse. The check must re-arm after the tool finishes, so
    stale_warned stays False."""
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
    # Tool started 3 min ago — well under TOOL_INFLIGHT_MAX_SECONDS.
    sess.pending_tool_started_at = time.monotonic() - 180.0
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_not_awaited()
    assert sess.stale_warned is False  # re-armed for post-tool


def test_scan_stale_busy_fires_when_tool_exceeds_inflight_cap(steady_clock, monkeypatch,
                                                              run_async):
    """A tool that has been in flight beyond TOOL_INFLIGHT_MAX_SECONDS is
    treated as genuinely wedged — the warning must fire."""
    from aipager.session_monitor import (
        STALE_BUSY_TIMEOUT,
        TOOL_INFLIGHT_MAX_SECONDS,
    )
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = steady_clock() - STALE_BUSY_TIMEOUT - 60
    # Tool started 16 min ago — over the 15 min cap.
    sess.pending_tool_started_at = (
        steady_clock() - TOOL_INFLIGHT_MAX_SECONDS - 60
    )
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_awaited_once()
    assert notify.await_args.args[1] == "stale_busy"
    assert sess.stale_warned is True


def test_scan_stale_busy_fires_when_no_tool_in_flight(steady_clock, monkeypatch, run_async):
    """Preserve existing behavior: no tool in flight, 2+ min of quiet →
    warning fires. Regression guard for the plain 'stuck' path."""
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = steady_clock() - STALE_BUSY_TIMEOUT - 60
    sess.pending_tool_started_at = None  # nothing in flight
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_awaited_once()
    assert notify.await_args.args[1] == "stale_busy"
    assert sess.stale_warned is True


# ---- _scan: stale_busy suppressed during compaction --------------------

def test_scan_stale_busy_suppressed_during_compact_in_flight(monkeypatch,
                                                             run_async):
    """Between PreCompact and post-compact SessionStart no hooks fire —
    that window can be minutes on a large transcript, and the stale-busy
    warning must NOT fire during it."""
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-cmp1", label="cmp1", status=Status.BUSY)
    sess.last_hook_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
    sess.compact_started_at = time.monotonic() - 300.0  # 5 min ago
    registry._sessions["claude-cmp1"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-cmp1"]))
    run_async(monitor._scan())

    notify.assert_not_awaited()
    assert sess.stale_warned is False


def test_scan_stale_busy_fires_when_compact_exceeds_inflight_cap(steady_clock, monkeypatch,
                                                                 run_async):
    """A compact that has been running longer than
    COMPACT_INFLIGHT_MAX_SECONDS is treated as genuinely wedged."""
    from aipager.session_monitor import (
        COMPACT_INFLIGHT_MAX_SECONDS,
        STALE_BUSY_TIMEOUT,
    )
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-cmp2", label="cmp2", status=Status.BUSY)
    sess.last_hook_at = steady_clock() - STALE_BUSY_TIMEOUT - 60
    sess.compact_started_at = (
        steady_clock() - COMPACT_INFLIGHT_MAX_SECONDS - 60
    )
    registry._sessions["claude-cmp2"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-cmp2"]))
    run_async(monitor._scan())

    notify.assert_awaited_once()
    assert notify.await_args.args[1] == "stale_busy"
    assert sess.stale_warned is True


# ---- _scan: stale_busy suppressed by recent statusLine heartbeat --------

def _statusline_path(name):
    from pathlib import Path
    return Path(f"/tmp/claude-status-{name}.json")


def test_scan_stale_busy_suppressed_by_recent_statusline(monkeypatch,
                                                         run_async):
    """A recent statusLine mtime is a liveness heartbeat — even with no
    hook activity and no tool/compact in flight, the session is
    clearly doing something."""
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    NAME = "claude-slalive"
    p = _statusline_path(NAME)
    p.write_text("{}")
    try:
        registry = SessionRegistry()
        sess = TrackedSession(name=NAME, label="sl", status=Status.BUSY)
        sess.last_hook_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
        registry._sessions[NAME] = sess

        notify = AsyncMock()
        monitor = _mk_monitor(registry, notify)
        monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                            AsyncMock(return_value=[NAME]))
        run_async(monitor._scan())

        notify.assert_not_awaited()
        assert sess.stale_warned is False
    finally:
        p.unlink(missing_ok=True)


def test_scan_stale_busy_fires_when_statusline_stale(steady_clock, monkeypatch, run_async):
    """A statusLine file older than STATUSLINE_ALIVE_SECONDS is NOT a
    heartbeat — the warning must fire normally."""
    import os
    from aipager.session_monitor import (
        STALE_BUSY_TIMEOUT,
        STATUSLINE_ALIVE_SECONDS,
    )
    NAME = "claude-slstale"
    p = _statusline_path(NAME)
    p.write_text("{}")
    old = time.time() - STATUSLINE_ALIVE_SECONDS - 60
    os.utime(p, (old, old))
    try:
        registry = SessionRegistry()
        sess = TrackedSession(name=NAME, label="sl2", status=Status.BUSY)
        sess.last_hook_at = steady_clock() - STALE_BUSY_TIMEOUT - 60
        registry._sessions[NAME] = sess

        notify = AsyncMock()
        monitor = _mk_monitor(registry, notify)
        monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                            AsyncMock(return_value=[NAME]))
        run_async(monitor._scan())

        notify.assert_awaited_once()
        assert notify.await_args.args[1] == "stale_busy"
        assert sess.stale_warned is True
    finally:
        p.unlink(missing_ok=True)


# ---- _scan: a re-prompted long-idle session is NOT stale (item 8.5) -----

def test_scan_no_stale_warning_when_only_the_PREVIOUS_turn_was_old(
    monkeypatch, run_async,
):
    """Roadmap 8.5. A session idle for days, then re-prompted, was warned
    about instantly: `last_hook_at` still held the last hook of the
    PREVIOUS turn, and the baseline preferred it over this turn's
    `busy_started_at`. The operator saw "still working — quiet for 20038
    min" one second after sending a message, on a turn that then answered
    normally.

    The stall window must start at the most recent sign of life OR the
    start of this turn, whichever came last.
    """
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    # Last hook of the previous turn: 14 days ago, as reported.
    sess.last_hook_at = time.monotonic() - 14 * 24 * 3600
    # This turn started just now — the operator's message a second ago.
    sess.busy_started_at = time.monotonic()
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_not_awaited()
    assert sess.stale_warned is False, (
        "the warning was armed as if this turn had already stalled"
    )
    assert STALE_BUSY_TIMEOUT  # referenced so the import is not decorative


def test_scan_still_warns_when_THIS_turn_has_genuinely_stalled(steady_clock, 
    monkeypatch, run_async,
):
    """The other direction, and the one that matters most: the fix must
    not simply silence the warning. A turn that started long ago and has
    produced no hooks at all is exactly what the feature exists for.
    """
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = steady_clock() - STALE_BUSY_TIMEOUT - 60
    sess.last_hook_at = 0.0          # no hook has ever fired for this turn
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_awaited_once()
    assert notify.await_args.args[1] == "stale_busy"
    assert sess.stale_warned is True


def test_scan_reported_quiet_minutes_describe_this_turn_not_the_session(
    monkeypatch, run_async,
):
    """The number in the message was the session's whole idle stretch
    (20038 min == the card's own "Active 333h58m ago"), which is what
    made it obviously wrong. It must describe the current turn."""
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.last_hook_at = time.monotonic() - 14 * 24 * 3600     # 20160 min
    # This turn has been quiet for a bit over the timeout, no more.
    sess.busy_started_at = time.monotonic() - STALE_BUSY_TIMEOUT - 60
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_awaited_once()
    minutes = notify.await_args.args[2]["minutes"]
    assert minutes == int((STALE_BUSY_TIMEOUT + 60) / 60), (
        f"reported {minutes} min — that is the session's lifetime, not this turn"
    )


def test_scan_interactive_watchdog_uses_the_same_corrected_baseline(
    monkeypatch, run_async,
):
    """A DEFENSIVE pin, not a bug-fix regression test — worth being
    honest about which it is.

    The INTERACTIVE watchdog carried the identical expression, but not
    the identical bug: every hook stamps `last_hook_at` before any event
    branching (`hook_receiver.py`), and all three transitions into
    INTERACTIVE are downstream of that, so `last_hook_at` is always the
    fresher stamp there and the old and new formulas agree in every
    reachable state. The state constructed below is therefore synthetic.

    It is pinned anyway because the consequence at this site is worse
    than a bogus message — a stale reading demotes the session to BUSY
    and drops `pending_permission`, losing a prompt the operator was
    meant to answer. If someone later adds an INTERACTIVE-entry path
    that skips the `last_hook_at` stamp, this fails instead of that
    shipping.
    """
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.last_hook_at = time.monotonic() - 14 * 24 * 3600   # previous turn
    sess.busy_started_at = time.monotonic()                 # this turn, just now
    sess.pending_permission = {"tool": "Bash"}
    registry._sessions["claude-jim"] = sess

    monitor = _mk_monitor(registry, AsyncMock())
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    assert sess.status is Status.INTERACTIVE, "the prompt was demoted to BUSY"
    assert sess.pending_permission == {"tool": "Bash"}, (
        "the pending permission prompt was thrown away"
    )


def test_quiet_since_prefers_the_later_stamp_either_way_round():
    """Pure-function pin: whichever stamp is newer wins, and two zeros
    mean "not knowable" rather than "the epoch"."""
    from aipager.session_monitor import _quiet_since

    s = TrackedSession(name="n", label="l")
    s.last_hook_at, s.busy_started_at = 100.0, 500.0
    assert _quiet_since(s) == 500.0
    s.last_hook_at, s.busy_started_at = 900.0, 500.0
    assert _quiet_since(s) == 900.0
    s.last_hook_at, s.busy_started_at = 0.0, 0.0
    assert _quiet_since(s) is None


def test_scan_no_stale_warning_right_after_a_long_permission_wait(
    monkeypatch, run_async,
):
    """The second real false-alarm path, and the one that bites in normal
    use rather than after two weeks away.

    When the operator answers a permission prompt, `callbacks.py` shifts
    `busy_started_at` FORWARD by however long they took
    (`sess.busy_started_at += now - wait_start`), so the wait is
    discounted from the "thought for Xs" timer — the session wasn't
    thinking, it was waiting for a human. `last_hook_at` is not shifted,
    so it still points at the moment the prompt was raised.

    Under the old `last_hook_at or busy_started_at`, sitting on a prompt
    for longer than STALE_BUSY_TIMEOUT and then tapping Allow produced
    "still working — quiet for 30 min" immediately — the identical false
    alarm as roadmap 8.5, reachable in an afternoon instead of a
    fortnight. Taking the later stamp discounts the wait here too.
    """
    from aipager.session_monitor import STALE_BUSY_TIMEOUT
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    now = time.monotonic()
    waited = STALE_BUSY_TIMEOUT + 1200          # 30 min on the prompt
    # The prompt hook fired when it was raised, and nothing since.
    sess.last_hook_at = now - waited
    # Answering shifted the turn's start forward by the wait.
    sess.busy_started_at = (now - waited) + waited
    registry._sessions["claude-jim"] = sess

    notify = AsyncMock()
    monitor = _mk_monitor(registry, notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=["claude-jim"]))
    run_async(monitor._scan())

    notify.assert_not_awaited()
    assert sess.stale_warned is False


# ---- a discovered session wears its own name, not the internal one ------

def test_get_or_create_derives_a_label_without_the_scope_suffix():
    """Reported 2026-08-17. Killing a session removes it from the
    registry; the socket can outlive that into the next 2s scan, so
    discovery re-creates the entry through `get_or_create` — which is the
    only path that has to DERIVE a label. It kept the disambiguator, so
    the session then showed up in the gone list as
    "Jkhk__d256113222" instead of "Jkhk"."""
    reg = SessionRegistry()
    assert reg.get_or_create("claude-Jkhk__d256113222").label == "Jkhk"
    assert reg.get_or_create("claude-dev").label == "dev"
    assert reg.get_or_create("claude-my__thing").label == "my__thing"


def test_get_or_create_never_rewrites_an_existing_label():
    """The derivation is a fallback for entries it CREATES. An operator's
    chosen label — or one already stored — must survive being looked up
    again, however odd it looks."""
    reg = SessionRegistry()
    sess = reg.get_or_create("claude-Jkhk__d256113222")
    sess.label = "renamed__d999"          # deliberately suffix-shaped
    again = reg.get_or_create("claude-Jkhk__d256113222")
    assert again is sess
    assert again.label == "renamed__d999"
