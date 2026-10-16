"""Drain the busy-card steps message handlers start in the background.

Since 2026-10-16 every injected message's card step runs as its own task
(``animation._card_for_injected``), so a handler returns before its card is
sent. A test that reads the card right after a handler awaits
:func:`drain_card_tasks` first, never a sleep.
"""

from __future__ import annotations

import asyncio

from aipager.bot import animation


async def drain_card_tasks(rounds: int = 10) -> None:
    """Wait until no card step is pending: the handlers' steps
    (``_CARD_TASKS``) and the ones waiting for a previous turn's finish
    (``_GATED_CARDS``), which a step may start, so a few rounds."""
    loop = asyncio.get_running_loop()
    for _ in range(rounds):
        # This test's own loop only: a task another test left behind on
        # its stopped loop can never finish here.
        pending = [t for t in (*animation._CARD_TASKS,
                               *animation._GATED_CARDS)
                   if not t.done() and t.get_loop() is loop]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
