"""Claude Remote v2 — single-process async daemon.

Usage:
    screen -S tgremote python3 -m aipager

All components run in one asyncio event loop:
- HookReceiver: Unix datagram socket for instant hook notifications
- PaneMonitor: fallback tmux pane scraping every 2s
- TelegramBot: python-telegram-bot long-polling (30s timeout)
"""

import asyncio
import logging
import signal
import sys

from aipager.config import BOT_TOKEN
from aipager.hook_receiver import HookReceiver
from aipager.pane_monitor import PaneMonitor
from aipager.state import SessionRegistry
from aipager.telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aipager")


async def main() -> None:
    if not BOT_TOKEN:
        log.error("CLAUDE_TG_BOT_TOKEN not set — exiting")
        sys.exit(1)

    registry = SessionRegistry()
    bot = TelegramBot(registry)
    hook_receiver = HookReceiver(registry, bot.notify)
    pane_monitor = PaneMonitor(registry, bot.notify)

    # Startup sequence: bot first (needs to be ready for notifications),
    # then hook receiver and pane monitor
    await bot.start()
    await hook_receiver.start()
    await pane_monitor.start()

    log.info("Claude Remote v2 running — all components started")

    # Run until interrupted
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()

    log.info("Shutting down...")
    pane_monitor.stop()
    hook_receiver.stop()
    await bot.stop()
    log.info("Goodbye")


if __name__ == "__main__":
    asyncio.run(main())
