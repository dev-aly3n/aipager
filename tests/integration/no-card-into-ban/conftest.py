"""Plumbing for roadmap 8.17c: what a muted chat's CALLERS do with the gate's
refusal. No assertions, no production logic.

Copied (not imported) from ``outbound-gate-and-earned-rate/conftest.py``,
the sanctioned per-directory pattern: that directory's conftest is not an
importable package. The two rules it exists to keep hold here too:

* **Nothing in ``asyncio`` is ever patched.** Time moves through the
  limiter's injected ``clock=``/``sleep=`` and by rebinding ``flood``'s and
  ``flood_budget``'s OWN ``time`` references.
* **A ``MagicMock`` bot proves nothing about the gate.** Every "no call
  reached Telegram" claim runs on :class:`GatedBot`, which funnels each
  call through the real ``BudgetRateLimiter.process_request`` and records
  only the calls whose callback actually ran.
"""

from __future__ import annotations

import asyncio
import types

import pytest

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot import flood, flood_budget
from aipager.bot.flood_budget import BudgetRateLimiter


@pytest.fixture
def run_async():
    """Every loop is CLOSED, its leftover tasks cancelled — a leaked
    animate or typing task would otherwise outlive the test."""
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
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


class FloodClock:
    """The limiter's monotonic clock AND ``flood``'s wall clock, moved
    together — the mute deadline is wall-clock, the budget monotonic."""

    def __init__(self, mono: float = 1_000_000.0,
                 wall: float = 1_800_000_000.0) -> None:
        self.now = mono
        self.wall = wall

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        seconds = max(float(seconds), 0.0)
        self.now += seconds
        self.wall += seconds

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)


@pytest.fixture
def flood_clock(monkeypatch):
    clock = FloodClock()
    fake = types.SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: clock.wall,
    )
    monkeypatch.setattr(flood, "time", fake)
    monkeypatch.setattr(flood_budget, "time", fake)
    return clock


@pytest.fixture
def limiter(flood_clock, monkeypatch):
    """The daemon's limiter on the shared clock, installed as the
    process-wide one so ``minimal_mode``/``cards_suppressed`` and the
    ban's rate drop see the same object the gated bot calls through."""
    lim = BudgetRateLimiter(clock=flood_clock, sleep=flood_clock.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    monkeypatch.setattr(rm, "_rate_limiter", lim)
    yield lim
    lim.reset()


class Sent:
    """What a successful send returns: something with a ``message_id``."""

    def __init__(self, message_id: int) -> None:
        self.message_id = message_id

    def __bool__(self) -> bool:
        return True


class GatedBot:
    """An ``ExtBot``-shaped double: every method goes through
    ``limiter.process_request`` with the endpoint PTB would name, and only
    a call whose callback RAN is recorded in :attr:`calls`.

    Each ``sendMessage`` answers a fresh ``message_id`` (5000, 5001, …) so
    a test can tell a late card from an earlier one.
    """

    _ENDPOINTS = {
        "send_message": ("sendMessage", 0),
        "edit_message_text": ("editMessageText", 1),
        "edit_message_reply_markup": ("editMessageReplyMarkup", 1),
        "delete_message": ("deleteMessage", 0),
        "send_document": ("sendDocument", 0),
        "send_chat_action": ("sendChatAction", 0),
        "set_message_reaction": ("setMessageReaction", 0),
    }

    def __init__(self, limiter, clock) -> None:
        self._limiter = limiter
        self._clock = clock
        self._next_id = 5000
        #: ``(endpoint, chat_id, kwargs)`` for every call that actually ran.
        self.calls: list[tuple[str, object, dict]] = []

    def __getattr__(self, name):
        if name not in self._ENDPOINTS:
            raise AttributeError(name)
        endpoint, position = self._ENDPOINTS[name]

        async def _method(*args, rate_limit_args=None, **kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id is None and len(args) > position:
                chat_id = args[position]

            async def _call():
                self.calls.append((endpoint, chat_id, dict(kwargs, _args=args)))
                if endpoint == "sendMessage":
                    self._next_id += 1
                    return Sent(self._next_id - 1)
                return True

            return await self._limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": chat_id}, rate_limit_args=rate_limit_args,
            )

        return _method

    def endpoints(self, *, skip=("sendChatAction",)) -> list[str]:
        """Endpoints that ran, the typing bubble left out by default: it
        has its own loop and its own rules, and rows about the card do
        not want to race it."""
        return [e for e, _c, _k in self.calls if e not in skip]


@pytest.fixture
def gated_bot(limiter, flood_clock):
    return GatedBot(limiter, flood_clock)


@pytest.fixture(autouse=True)
def _pin_keyboard_chat(monkeypatch):
    """The keyboard rows target ``CHAT_ID`` as ``keyboards.py`` and
    ``lifecycle.py`` bound it at import. ``tests/conftest.py`` pins only
    ``aipager.config.CHAT_ID``, so on a machine with no configured chat —
    CI — these modules' own copies are ``""`` and the keyboard goes to a
    chat no mute covers. Pinned per module, the way the suite pins
    ``notify.FINISH_CARD_GRACE_SECONDS``."""
    monkeypatch.setattr("aipager.bot.keyboards.CHAT_ID", "256113222")
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "256113222")
