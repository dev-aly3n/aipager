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
from aipager.state import SessionRegistry, Status
from aipager.transcript import (
    extract_last_response,
    find_transcript,
    last_assistant_preview,
    turn_appears_complete,
)

log = logging.getLogger(__name__)

# Item 2.2 — auto-demote INTERACTIVE sessions back to BUSY if they've sat
# in INTERACTIVE state with no hook activity for this long. The assumption:
# claude crashed mid-permission-prompt, the user can never see / answer
# it, so the session shouldn't sit forever. Demoting to BUSY lets the
# session_monitor's existing stale-busy logic surface it after another
# STALE_BUSY_TIMEOUT, instead of silently rotting.
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
        for name, sess in self.registry.all_sessions().items():
            # INTERACTIVE watchdog (item 2.2)
            if sess.status == Status.INTERACTIVE:
                baseline = sess.last_hook_at or sess.busy_started_at
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

            # Idle-recovery fallback: a missed Stop hook can strand a session
            # in BUSY, animating forever. If the transcript shows the turn
            # finished and the file has gone quiet, recover to IDLE exactly
            # as the hook would (transition + idle_prompt notify finalizes
            # the busy message and flushes the queue).
            if sess.status == Status.BUSY:
                tp = sess.transcript_path or find_transcript(name)
                busy_for = (now - sess.busy_started_at) if sess.busy_started_at else 0.0
                quiet_for = 0.0
                if tp:
                    try:
                        quiet_for = time.time() - os.path.getmtime(tp)
                    except OSError:
                        quiet_for = 0.0
                if (tp and busy_for >= IDLE_RECOVERY_GRACE
                        and quiet_for >= IDLE_RECOVERY_GRACE
                        and turn_appears_complete(tp)):
                    log.warning(
                        "[%s] BUSY but transcript shows the turn finished and has "
                        "been quiet %.0fs — recovering to IDLE (missed Stop hook)",
                        sess.label, quiet_for,
                    )
                    recovered = self.registry.transition(name, Status.IDLE)
                    if recovered:
                        summary = ""
                        try:
                            summary = extract_last_response(tp) or ""
                        except Exception:
                            log.debug("[%s] idle-recovery summary failed", name,
                                      exc_info=True)
                        try:
                            await self.notify_fn(recovered, "idle_prompt",
                                                 {"summary": summary})
                        except Exception:
                            log.warning("[%s] idle-recovery notify failed", name,
                                        exc_info=True)
                        self.registry.mark_dirty()
                    continue  # handled this session this scan

            # Stale BUSY warning (existing)
            if sess.status != Status.BUSY or sess.stale_warned:
                continue
            baseline = sess.last_hook_at or sess.busy_started_at
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
