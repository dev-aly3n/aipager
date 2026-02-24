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

from aipager import dtach_inject
from aipager.config import PANE_POLL_INTERVAL
from aipager.state import SessionRegistry, Status

log = logging.getLogger(__name__)


class SessionMonitor:
    """Periodically discovers dtach sessions and marks dead ones GONE."""

    def __init__(self, registry: SessionRegistry, notify_fn):
        self.registry = registry
        self.notify_fn = notify_fn
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("Session monitor started (every %.1fs)", PANE_POLL_INTERVAL)

    async def _loop(self) -> None:
        while True:
            try:
                await self._scan()
            except Exception:
                log.exception("Session monitor error")
            await asyncio.sleep(PANE_POLL_INTERVAL)

    async def _scan(self) -> None:
        sessions = await dtach_inject.list_sessions()

        # Mark disappeared sessions as GONE
        for name, sess in list(self.registry.all_sessions().items()):
            if name not in sessions and sess.status != Status.GONE:
                self.registry.transition(name, Status.GONE)

        # Discover new sessions
        for name in sessions:
            self.registry.get_or_create(name)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
