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

from aipager import dtach_inject
from aipager.config import PANE_POLL_INTERVAL, STALE_BUSY_TIMEOUT
from aipager.state import SessionRegistry, Status

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
                try:
                    await self.notify_fn(sess, "session_end", {"source": "disappeared"})
                except Exception:
                    log.warning("Failed to notify session_end for %s", name)

        # Discover new sessions and recover GONE sessions whose socket reappeared
        for name in sessions:
            sess = self.registry.get_or_create(name)
            if sess.status in (Status.UNKNOWN, Status.GONE):
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

            # Stale BUSY warning (existing)
            if sess.status != Status.BUSY or sess.stale_warned:
                continue
            baseline = sess.last_hook_at or sess.busy_started_at
            if baseline and (now - baseline) > STALE_BUSY_TIMEOUT:
                sess.stale_warned = True
                stale_mins = int((now - baseline) / 60)
                try:
                    await self.notify_fn(sess, "stale_busy", {"minutes": stale_mins})
                except Exception:
                    log.warning("Failed to notify stale_busy for %s", name)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
