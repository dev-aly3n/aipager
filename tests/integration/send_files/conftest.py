"""Plumbing for the files-Claude-sends scenarios. No assertions.

``FilesBot`` records every Bot API call on one timeline with the rich
answer sends, and refuses a call to a muted chat with ``FloodMuted`` before
recording it, as the outbound gate does (``BudgetRateLimiter.process_request``).
Nothing in ``asyncio`` is patched.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.rich_message import RichMessageFloodBanned

CHAT = 256113222          # the chat `_pin_single_chat_config` pins


@pytest.fixture
def run_async():
    loops: list[asyncio.AbstractEventLoop] = []

    def _run(coro):
        loop = asyncio.new_event_loop()
        loops.append(loop)
        return loop.run_until_complete(coro)

    yield _run
    for loop in loops:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


class FilesBot:
    """An ``ExtBot``-shaped recorder. ``fail`` maps a method name to an
    exception it raises (once per call)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict]] = []
        #: Calls the outbound gate refused (the chat was muted): nothing
        #: reached Telegram, but the call was attempted.
        self.refused: list[str] = []
        self.fail: dict[str, BaseException] = {}
        #: A method name -> an event its calls wait for (an upload that
        #: takes as long as the test wants).
        self.hold: dict[str, asyncio.Event] = {}
        self.id = 999
        self._next = 5000

    def _id(self) -> int:
        self._next += 1
        return self._next

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def _method(*args, **kwargs):
            chat = kwargs.get("chat_id", args[0] if args else None)
            if chat is not None and MUTE.is_muted(chat):
                self.refused.append(name)
                raise FloodMuted(MUTE.remaining(chat), chat)
            if name in self.hold:
                await self.hold[name].wait()
            if name in self.fail:
                raise self.fail[name]
            self.calls.append((name, chat, dict(kwargs, _args=args)))
            if name == "send_media_group":
                return tuple(SimpleNamespace(message_id=self._id())
                             for _ in kwargs.get("media", ()))
            return SimpleNamespace(message_id=self._id())
        return _method

    def file_calls(self):
        return [c for c in self.calls
                if c[0] in ("send_photo", "send_video", "send_animation", "send_audio",
                            "send_document", "send_media_group")]


@pytest.fixture
def files_bot():
    return FilesBot()


@pytest.fixture
def rich(monkeypatch, files_bot):
    """``send_rich_message`` as it behaves: refused while muted, else
    recorded on the bot's timeline with a fresh message id."""
    async def _send(chat_id, markdown, **kw):
        if MUTE.is_muted(chat_id):
            raise RichMessageFloodBanned(MUTE.remaining(chat_id), chat_id)
        mid = files_bot._id()
        files_bot.calls.append(("sendRichMessage", chat_id, {"text": markdown,
                                                              "message_id": mid, **kw}))
        return {"message_id": mid}

    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send)
    return _send
