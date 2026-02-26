"""Observer broadcaster — read-only notifications to secondary Telegram bots.

Each observer has its own telegram.Bot instance and chat_id.
Sends are fire-and-forget — observer failures never affect the primary bot.
"""

from __future__ import annotations

import io
import logging

from telegram import Bot
from telegram.request import HTTPXRequest

from aipager.config import PROXY

log = logging.getLogger(__name__)


class ObserverBroadcaster:
    """Broadcasts read-only notifications to observer bots.

    Each observer has its own telegram.Bot instance and chat_id.
    All sends are independently try/excepted — one bad observer
    never crashes others or blocks the primary bot.
    """

    def __init__(self, bots_config: list[tuple[str, str]], use_proxy: bool):
        self._config = bots_config
        self._use_proxy = use_proxy
        self._bots: list[tuple[Bot, str]] = []  # (bot_instance, chat_id)

    async def start(self) -> None:
        """Initialize all telegram.Bot instances."""
        for token, chat_id in self._config:
            try:
                if self._use_proxy:
                    request = HTTPXRequest(proxy=PROXY)
                    bot = Bot(token=token, request=request)
                else:
                    bot = Bot(token=token)
                await bot.initialize()
                self._bots.append((bot, chat_id))
                log.info("Observer bot initialized for chat_id=%s", chat_id)
            except Exception:
                log.warning("Failed to initialize observer bot for chat_id=%s",
                            chat_id, exc_info=True)
        if self._bots:
            log.info("Initialized %d observer bot(s)", len(self._bots))

    async def stop(self) -> None:
        """Shutdown all telegram.Bot instances."""
        for bot, chat_id in self._bots:
            try:
                await bot.shutdown()
            except Exception:
                log.debug("Observer bot shutdown error (chat_id=%s)", chat_id)
        self._bots.clear()

    async def broadcast(self, text: str, parse_mode: str = "HTML") -> None:
        """Send message to all observers. Never raises."""
        for bot, chat_id in self._bots:
            try:
                await bot.send_message(chat_id, text, parse_mode=parse_mode)
            except Exception:
                log.warning("Observer send failed (chat_id=%s)", chat_id,
                            exc_info=True)

    async def broadcast_document(self, text: str, document_bytes: bytes,
                                 filename: str,
                                 parse_mode: str = "HTML") -> None:
        """Send message + document to all observers. Never raises."""
        for bot, chat_id in self._bots:
            try:
                await bot.send_message(chat_id, text, parse_mode=parse_mode)
                doc = io.BytesIO(document_bytes)
                doc.name = filename
                await bot.send_document(chat_id, document=doc,
                                        filename=filename)
            except Exception:
                log.warning("Observer document send failed (chat_id=%s)",
                            chat_id, exc_info=True)
