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
import time

from aipager import dtach_inject
from aipager.config import PANE_POLL_INTERVAL, STALE_BUSY_TIMEOUT
from aipager.state import SessionRegistry, Status

log = logging.getLogger(__name__)


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

        # Discover new sessions (start as IDLE — they're alive but not working)
        for name in sessions:
            sess = self.registry.get_or_create(name)
            if sess.status == Status.UNKNOWN:
                self.registry.transition(name, Status.IDLE)

        # Notify if session list changed (for bot command/keyboard updates)
        new_names = set(self.registry.all_sessions().keys())
        if new_names != old_names and self.on_sessions_changed:
            try:
                await self.on_sessions_changed()
            except Exception:
                log.warning("on_sessions_changed callback failed", exc_info=True)

        # Check for stale BUSY sessions (no hook activity for too long)
        now = time.monotonic()
        for name, sess in self.registry.all_sessions().items():
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
