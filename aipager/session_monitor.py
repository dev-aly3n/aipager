"""Async session monitor — discovers dtach sessions, detects dead ones.

Replaces the old pane_monitor. No pane scraping (dtach has no capture_pane).
Status transitions (IDLE, INTERACTIVE) come from hook_receiver only.
This monitor handles:
1. Discovering new dtach sessions not yet in the registry
2. Marking dead sessions as GONE
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from aipager.dtach import inject as dtach_inject
from aipager.config import (
    COMPACT_INFLIGHT_MAX_SECONDS,
    PANE_POLL_INTERVAL,
    STALE_BUSY_TIMEOUT,
    STATUSLINE_ALIVE_SECONDS,
    TOOL_INFLIGHT_MAX_SECONDS,
)
from aipager.state import SessionRegistry, Status, TrackedSession
from aipager.transcript import (
    extract_last_response,
    last_assistant_preview,
    turn_appears_complete,
)

log = logging.getLogger(__name__)

# Item 2.2 — auto-demote INTERACTIVE sessions back to BUSY if they've sat
# in INTERACTIVE state with no hook activity for this long. The assumption:
# claude crashed mid-permission-prompt, the user can never see / answer
# it, so the session shouldn't sit forever. Demoting to BUSY lets the
# session_monitor's existing stale-busy logic surface it after another
# STALE_BUSY_TIMEOUT, instead of silently rotting. The two compound, so
# a crashed permission prompt surfaces in 300s + STALE_BUSY_TIMEOUT.
#
# Tunable via `AIPAGER_INTERACTIVE_TIMEOUT` (seconds) for ops testing.
INTERACTIVE_TIMEOUT_SECONDS: float = float(
    os.environ.get("AIPAGER_INTERACTIVE_TIMEOUT", "300")
)

# Item 2.4 — drop subagent entries that have been "live" for more than
# this without a corresponding SubagentStop. Real subagents finish in
# seconds; entries older than this almost certainly mean a missed stop
# event (daemon restart, crash, dropped hook).
SUBAGENT_TTL_SECONDS: float = float(
    os.environ.get("AIPAGER_SUBAGENT_TTL", "3600")
)

# Idle-recovery fallback. The normal BUSY→IDLE transition comes from
# Claude's Stop hook (hook_receiver). If that hook is ever missed — e.g.
# the user interrupts a pending permission then immediately sends a new
# prompt — the session would animate "Thinking…" forever. When a BUSY
# session's transcript shows the turn finished AND the file has been quiet
# for this long, the monitor recovers it to IDLE the same way the hook
# would. The grace must comfortably exceed normal hook latency so a
# fast-completing turn is finalized by the hook, not raced by the monitor.
IDLE_RECOVERY_GRACE: float = float(
    os.environ.get("AIPAGER_IDLE_RECOVERY_GRACE", "8")
)

# Slack for the "was the transcript written during this turn?" check.
# Some filesystems (tmpfs, HFS+, older NFS) store mtime at 1-second
# resolution, so a write that really did land just after the turn started
# can report an mtime a fraction under it. Well below IDLE_RECOVERY_GRACE,
# so this cannot re-admit a transcript that is genuinely a turn behind.
MTIME_GRANULARITY_SLACK: float = 1.0


def _quiet_since(sess: TrackedSession) -> float | None:
    """When this turn last showed a sign of life — or ``None`` if that is
    not knowable yet.

    The **later** of the two stamps, not the first truthy one.
    ``last_hook_at`` is the last hook of the *previous* turn once a
    session has taken one, so ``last_hook_at or busy_started_at``
    preferred a stale value forever. Invisible while a session works —
    hooks refresh it every few seconds — but a session left idle past
    the timeout and then re-prompted got warned about on the very next
    2s scan, quoting its entire idle stretch as the quiet period
    (roadmap 8.5: "still working — quiet for 20038 min", one second
    after the operator's message, on a turn that then answered fine).

    ``or None`` keeps the fresh-daemon case honest: both stamps are
    ``time.monotonic()`` and neither is persisted, so after a restart
    both are ``0.0`` and every caller's ``if baseline and …`` guard
    skips the check rather than measuring from the epoch.

    The same correction fixes a second, far more reachable false alarm:
    answering a permission prompt shifts ``busy_started_at`` *forward* by
    the wait (``callbacks.py``, so a human's thinking time is discounted
    from "thought for Xs"), while ``last_hook_at`` still points at the
    moment the prompt was raised. Sitting on a prompt past the timeout
    and then tapping Allow used to produce the identical bogus warning
    within a second — no fortnight required.

    Shared with the INTERACTIVE watchdog for one implementation rather
    than two, NOT because that site was broken: every hook stamps
    ``last_hook_at`` before any event branching
    (``hook_receiver.py``), and all three transitions into INTERACTIVE
    are downstream of that stamp, so the old and new expressions agree
    there in every reachable state. It is shared so a future
    INTERACTIVE-entry path that skips the stamp cannot reintroduce the
    bug at a second site.
    """
    return max(sess.last_hook_at, sess.busy_started_at) or None


def expired_compacting_sessions(
    sessions: dict[str, TrackedSession], now: float,
) -> list[str]:
    """Names whose live message is a compaction card whose deadline has
    passed (design.md "Live Message Stack", Decisions 3 and 5).

    Pure — no I/O, no asyncio, no ``time.monotonic()`` call inside it, so
    it is directly callable in pytest with a fabricated ``now``; no test
    needs to wait out a real timeout. Deliberately **not** gated on
    ``status == BUSY``, unlike every other watchdog in this module: a
    session can desync from BUSY while a compacting card is still live
    (the observed live bug — status: idle, card still spinning), which
    would make it invisible to a status-gated check.

    A session with no live ``compacting`` entry, or one that hasn't
    reached its deadline yet (including one pushed with
    ``deadline_seconds=None``, which never expires), is never included.
    """
    # Delegates the staleness rule to the session itself, so the sweeper
    # and _send_busy_and_animate's reclaim branch can never disagree about
    # whether a given card is stale — they ask the same predicate.
    return [name for name, sess in sessions.items()
            if sess.compacting_is_overdue(now)]


class SessionMonitor:
    """Periodically discovers dtach sessions and marks dead ones GONE."""

    def __init__(self, registry: SessionRegistry, notify_fn):
        self.registry = registry
        self.notify_fn = notify_fn
        self._task: asyncio.Task | None = None
        self.on_sessions_changed = None  # optional async callback

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("Session monitor started (every %.1fs)", PANE_POLL_INTERVAL)

    async def _loop(self) -> None:
        while True:
            try:
                await self._scan()
                self.registry.save_if_dirty()
            except Exception:
                log.exception("Session monitor error")
            await asyncio.sleep(PANE_POLL_INTERVAL)

    async def _scan(self) -> None:
        sessions = await dtach_inject.list_sessions()
        old_names = set(self.registry.all_sessions().keys())

        # Mark disappeared sessions as GONE and notify
        for name, sess in list(self.registry.all_sessions().items()):
            if name not in sessions and sess.status != Status.GONE:
                if sess.is_restarting():
                    # Deliberate kill-and-relaunch (`/perms`): the socket is
                    # expected to be missing for the moment between the two.
                    # Marking it GONE here would race the relaunch and alarm
                    # the user about a session that is coming right back.
                    continue
                self.registry.transition(name, Status.GONE)
                # Stamp the GONE moment + capture a last-message preview
                # so /resume can show "where you left off" without
                # re-reading the transcript at picker time.
                sess.gone_at = time.time()
                try:
                    sess.last_assistant_preview = last_assistant_preview(
                        sess.transcript_path
                    )
                except Exception:
                    log.debug("preview extraction failed for %s", name,
                              exc_info=True)
                self.registry.mark_dirty()
                try:
                    await self.notify_fn(sess, "session_end", {"source": "disappeared"})
                except Exception:
                    log.warning("Failed to notify session_end for %s", name)

        # Discover new sessions and recover GONE sessions whose socket reappeared
        for name in sessions:
            sess = self.registry.get_or_create(name)
            if sess.status in (Status.UNKNOWN, Status.GONE):
                # Coming back from GONE means a resume worked (or the
                # user rebooted dtach manually). Clear the GONE-only
                # fields so this entry no longer surfaces in the picker.
                if sess.status == Status.GONE:
                    sess.gone_at = None
                self.registry.transition(name, Status.IDLE)

        # Notify if session list changed (for bot command/keyboard updates)
        new_names = set(self.registry.all_sessions().keys())
        if new_names != old_names and self.on_sessions_changed:
            try:
                await self.on_sessions_changed()
            except Exception:
                log.warning("on_sessions_changed callback failed", exc_info=True)

        # Check for stale BUSY sessions (no hook activity for too long).
        # Also: auto-demote INTERACTIVE sessions whose permission prompt
        # has been hanging for too long (claude crashed mid-prompt), and
        # garbage-collect subagent entries whose Stop hook never arrived.
        now = time.monotonic()

        # Compact-card deadline sweep (design.md Decisions 3/5) — one pass
        # per tick, deliberately NOT folded into the per-status loop below
        # since it is the one watchdog that is NOT gated on
        # status == Status.BUSY (see expired_compacting_sessions'
        # docstring for why that gate is what let the reported bug hide
        # from every existing watchdog).
        for expired_name in expired_compacting_sessions(
            self.registry.all_sessions(), now,
        ):
            expired_sess = self.registry.get(expired_name)
            if expired_sess is None:
                continue
            started = expired_sess.compacting_started_at()
            elapsed = (now - started) if started is not None else 0.0
            try:
                await self.notify_fn(
                    expired_sess, "compact_timeout", {"elapsed_seconds": elapsed},
                )
            except Exception:
                log.warning("Failed to notify compact_timeout for %s", expired_name)

        for name, sess in self.registry.all_sessions().items():
            # INTERACTIVE watchdog (item 2.2)
            if sess.status == Status.INTERACTIVE:
                baseline = _quiet_since(sess)
                if baseline and (now - baseline) > INTERACTIVE_TIMEOUT_SECONDS:
                    log.warning(
                        "[%s] INTERACTIVE > %d min with no hooks — "
                        "demoting to BUSY (likely a crashed permission prompt)",
                        sess.label, int(INTERACTIVE_TIMEOUT_SECONDS / 60),
                    )
                    sess.pending_permission = None
                    self.registry.transition(name, Status.BUSY)
                    self.registry.mark_dirty()
                    # Fall through so stale-busy logic still applies.

            # Subagent TTL (item 2.4)
            if sess.active_subagents:
                # Captured BEFORE popping (design.md "model Claude Code
                # background-agent jobs" requirement 6) — job_background_open()
                # would read the now-empty table otherwise, and this
                # sweep's own eviction below is exactly what needs to be
                # observed as "was this job open a moment ago".
                was_job_open = sess.job_background_open()
                stale_ids = [
                    aid for aid, info in sess.active_subagents.items()
                    if info.get("started_at")
                    and (now - info["started_at"]) > SUBAGENT_TTL_SECONDS
                ]
                for aid in stale_ids:
                    log.info("[%s] dropping stale subagent %s (no Stop hook in "
                             "%d min)", sess.label, aid,
                             int(SUBAGENT_TTL_SECONDS / 60))
                    sess.active_subagents.pop(aid, None)
                # A job cannot wait forever: once the TTL sweep empties the
                # table for a session sitting IDLE with a job open, produce
                # the terminal "background agent lost" card rather than
                # leaving the waiting card ticking indefinitely. Gated on
                # IDLE specifically — a session that flipped back to BUSY
                # (the background agent's own tool call re-entered before
                # this scan) is still genuinely working, not orphaned.
                if (was_job_open and sess.status == Status.IDLE
                        and not sess.active_subagents):
                    sess.job_interim_seen = False
                    sess.job_continuation_active = False
                    sess.job_grace_until = 0.0
                    try:
                        await self.notify_fn(sess, "job_agents_lost", {})
                    except Exception:
                        log.warning(
                            "Failed to notify job_agents_lost for %s", name,
                        )

            # Continuation-grace expiry ("close the background-job endgame"
            # requirement 2's fallback): the last background agent stopped
            # and an interim was delivered, but no <task-notification>
            # continuation arrived within the grace window. Close the job
            # honestly — the interim answer stands as the result. Gated on
            # IDLE: a BUSY session is genuinely working (the continuation
            # itself, or a new real turn), not orphaned.
            if (sess.status == Status.IDLE and sess.job_interim_seen
                    and not sess.active_subagents
                    and not sess.job_continuation_active
                    and sess.job_grace_until
                    and now >= sess.job_grace_until):
                sess.job_grace_until = 0.0
                sess.job_interim_seen = False
                log.info(
                    "[%s] background job's continuation never arrived "
                    "within the grace window — closing the job",
                    sess.label,
                )
                try:
                    await self.notify_fn(sess, "job_grace_expired", {})
                except Exception:
                    log.warning(
                        "Failed to notify job_grace_expired for %s", name,
                    )
                self.registry.mark_dirty()

            # Idle-recovery fallback: a missed Stop hook can strand a session
            # in BUSY, animating forever. If the transcript shows the turn
            # finished and the file has gone quiet, recover to IDLE exactly
            # as the hook would (transition + idle_prompt notify finalizes
            # the busy message and flushes the queue).
            #
            # Only the hook-stamped path is trusted. A session with no stamped
            # path recovers nothing and falls through to STALE_BUSY_TIMEOUT —
            # guessing here once published another session's answer.
            if sess.status == Status.BUSY and not sess.job_background_open():
                # The job-open guard ("close the background-job endgame"
                # requirement 1): while a background agent is running (or a
                # continuation turn is), the transcript on disk lags —
                # Claude Code 2.1.x flushes it lazily (observed 72-minute
                # lag live) — so "transcript finished + quiet" describes
                # the INTERIM turn, not the session. Recovering here
                # ping-ponged BUSY→IDLE eight times in one live run.
                tp = sess.transcript_path
                busy_for = (now - sess.busy_started_at) if sess.busy_started_at else 0.0
                quiet_for = 0.0
                mtime: float | None = None
                if tp:
                    try:
                        mtime = os.path.getmtime(tp)
                        quiet_for = time.time() - mtime
                    except OSError:
                        mtime = None
                        quiet_for = 0.0
                # The transcript must have been written since this turn
                # began. Without that, a turn whose prompt never reached
                # claude is indistinguishable from one that finished and
                # went quiet — turn_appears_complete() would be reading the
                # PREVIOUS turn's tail, and recovery would publish that
                # turn's answer as the reply to the new prompt.
                written_this_turn = bool(
                    tp and sess.busy_started_wall
                    and mtime is not None
                    and mtime >= sess.busy_started_wall - MTIME_GRANULARITY_SLACK
                )
                if (tp and busy_for >= IDLE_RECOVERY_GRACE
                        and quiet_for >= IDLE_RECOVERY_GRACE
                        and written_this_turn
                        and turn_appears_complete(tp)):
                    log.warning(
                        "[%s] BUSY but transcript shows the turn finished and has "
                        "been quiet %.0fs — recovering to IDLE (missed Stop hook)",
                        sess.label, quiet_for,
                    )
                    recovered = self.registry.transition(name, Status.IDLE)
                    if recovered:
                        summary = None
                        try:
                            summary = extract_last_response(tp)
                        except Exception:
                            log.debug("[%s] idle-recovery summary failed", name,
                                      exc_info=True)
                        ctx = {"summary": summary or ""}
                        # "" (not None) means the turn ended having produced
                        # no text — say so, or notify falls back to the
                        # previous turn's cached summary.
                        if summary == "":
                            ctx["no_response"] = True
                        try:
                            await self.notify_fn(recovered, "idle_prompt", ctx)
                        except Exception:
                            log.warning("[%s] idle-recovery notify failed", name,
                                        exc_info=True)
                        self.registry.mark_dirty()
                    continue  # handled this session this scan

            # Stale BUSY warning (existing)
            if sess.status != Status.BUSY or sess.stale_warned:
                continue
            baseline = _quiet_since(sess)
            if baseline and (now - baseline) > STALE_BUSY_TIMEOUT:
                # A tool call is legitimately in flight — no hooks fire
                # between PreToolUse and PostToolUse, so the session
                # looks quiet even though it's working. Stand down
                # until either the tool finishes (PostToolUse clears
                # pending_tool_started_at) or the tool itself has been
                # running long enough to count as genuinely wedged.
                # stale_warned stays False so the check re-arms as soon
                # as the tool completes.
                tool_start = sess.pending_tool_started_at
                if (tool_start is not None
                        and (now - tool_start) < TOOL_INFLIGHT_MAX_SECONDS):
                    continue
                # Compaction between PreCompact and post-compact SessionStart
                # emits no hooks — treat the same as tool-in-flight, with a
                # longer cap since compacting a large transcript is slow.
                compact_start = sess.compact_started_at
                if (compact_start is not None
                        and (now - compact_start) < COMPACT_INFLIGHT_MAX_SECONDS):
                    continue
                # Fallback liveness signal: the Claude Code statusLine hook
                # writes /tmp/claude-status-<session>.json on many state
                # changes during active work. A fresh mtime means the
                # session is doing something even if no aipager-tracked
                # hook has fired. mtime is walltime, so compare via
                # time.time() (not the monotonic `now` above).
                statusline_path = Path(f"/tmp/claude-status-{name}.json")
                try:
                    sl_age = time.time() - statusline_path.stat().st_mtime
                    if sl_age < STATUSLINE_ALIVE_SECONDS:
                        continue
                except OSError:
                    pass  # no statusLine yet — fall through
                sess.stale_warned = True
                stale_mins = int((now - baseline) / 60)
                try:
                    await self.notify_fn(sess, "stale_busy", {"minutes": stale_mins})
                except Exception:
                    log.warning("Failed to notify stale_busy for %s", name)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
