"""Claude Remote v2 — single-process async daemon.

Usage:
    screen -S tgremote python3 -m aipager

All components run in one asyncio event loop:
- HookReceiver: Unix datagram socket for instant hook notifications
- SessionMonitor: dtach session discovery and liveness checks
- TelegramBot: python-telegram-bot long-polling (30s timeout)
"""

import asyncio
import logging
import signal
import sys

from aipager.config import BOT_TOKEN, OBSERVER_BOTS
from aipager.hook_receiver import HookReceiver
from aipager.observer import ObserverBroadcaster
from aipager.session_monitor import SessionMonitor
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
    registry.load()
    bot = TelegramBot(registry)
    hook_receiver = HookReceiver(registry, bot.notify)
    session_monitor = SessionMonitor(registry, bot.notify)

    # Startup sequence: bot first (needs to be ready for notifications),
    # then observers (reuse proxy detection), then hook receiver and session monitor
    await bot.start()
    observers = None
    if OBSERVER_BOTS:
        observers = ObserverBroadcaster(OBSERVER_BOTS, bot.use_proxy)
        await observers.start()
        bot.observers = observers
    await hook_receiver.start()
    await session_monitor.start()

    log.info("Claude Remote v2 running — all components started")

    # Run until interrupted
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()

    log.info("Shutting down...")
    registry.save()
    session_monitor.stop()
    hook_receiver.stop()
    if observers:
        await observers.stop()
    await bot.stop()
    log.info("Goodbye")


if __name__ == "__main__":
    asyncio.run(main())
