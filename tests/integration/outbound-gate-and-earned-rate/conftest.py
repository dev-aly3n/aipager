"""Shared plumbing for the 8.26–8.29 rows. No assertions, no production
logic — every fixture here exists so a row can be written as one sentence.

Two rules this directory exists to keep, both learned the hard way:

* **Nothing in ``asyncio`` is ever patched.** ``aipager.bot.notify.asyncio``
  IS the global module, and patching ``sleep``/``create_task`` through a
  module path has hung this suite twice. Time is moved by an injected
  ``clock=``/``sleep=`` seam, or by rebinding a MODULE'S OWN ``time``
  reference (``flood.time``), which is the sanctioned trick.
* **A ``MagicMock`` bot proves nothing about the gate.** ``conftest.mk_bot``
  gives ``bot._app.bot`` a ``MagicMock``, which enforces nothing at all, so
  every "no HTTP while muted" claim in this directory runs on
  :class:`_GatedBot` — a double that funnels each call through the real
  ``BudgetRateLimiter.process_request`` with the real endpoint name.
"""

from __future__ import annotations

import asyncio
import json
import types

import httpx
import pytest

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot import flood, flood_budget
from aipager.bot.flood_budget import BudgetRateLimiter

#: The REAL ``_post``, captured before conftest's autouse
#: ``_block_real_telegram_http`` swaps in its refusing stub.
_REAL_POST = rm._post


@pytest.fixture
def run_async():
    """Override the shared fixture so every loop is CLOSED.

    These rows leave limiter waiters and animate tasks behind, and the
    suite runs pinned under a 1 GiB ``RLIMIT_AS`` (roadmap 8.19) — a leaked
    loop fails an unrelated later test with "can't start new thread".
    """
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
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


class FloodClock:
    """The limiter's monotonic clock AND ``flood.py``'s wall clock, moved
    together by one ``advance()``.

    Advancing only one of them is the classic false pass in this area: the
    mute deadline is wall-clock (8.26 D-7) while the budget is monotonic,
    so a row that combines a mute with the budget and moves one clock
    proves nothing about the other. ``flood.time`` is rebound — the
    module's OWN reference, exactly as ``tests/test_flood_mute.py``'s
    ``clock`` fixture does — never the global ``time`` module.
    """

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
    """A :class:`FloodClock` with BOTH modules' own ``time`` bound to it.

    ``flood.py`` reads the wall clock for the mute deadline (8.26 D-7) and
    ``flood_budget.py`` reads it for ban stamps and for deciding whether a
    chat is inside its post-ban recovery window. Rebinding only one leaves
    the other on the real clock, where a test that "advances six hours"
    advances nothing — the classic false pass in this area, and the reason
    both are done here rather than per test.

    Each module's OWN ``time`` reference is rebound, never the global
    ``time`` module and never anything in ``asyncio``.
    """
    clock = FloodClock()
    fake = types.SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: clock.wall,
    )
    monkeypatch.setattr(flood, "time", fake)
    monkeypatch.setattr(flood_budget, "time", fake)
    return clock


@pytest.fixture
def limiter(flood_clock):
    """The daemon's limiter on the shared clock.

    ``signal_path`` is passed EXPLICITLY: without it any assertion about
    the backoff signal file is vacuous on a machine with no daemon socket
    (the documented CI-parity bug — see ``test_budget_rules.py``'s copy of
    this fixture).
    """
    lim = BudgetRateLimiter(clock=flood_clock, sleep=flood_clock.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    yield lim
    lim.reset()


class _GatedBot:
    """An ``ExtBot``-shaped double whose every method funnels through
    ``limiter.process_request`` with the endpoint name PTB would use.

    This is the double every "zero Bot API calls" assertion in this
    directory must use. ``mk_bot``'s ``MagicMock`` would happily record a
    call the gate was supposed to refuse and the test would pass for the
    wrong reason; here a refused call never reaches ``.calls`` because the
    callback is the only thing that appends to it.
    """

    #: PTB method name -> Bot API endpoint, and where ``chat_id`` lives.
    _ENDPOINTS = {
        "send_message": ("sendMessage", 0),
        "edit_message_text": ("editMessageText", 1),
        "delete_message": ("deleteMessage", 0),
        "send_document": ("sendDocument", 0),
        "send_chat_action": ("sendChatAction", 0),
        "set_message_reaction": ("setMessageReaction", 0),
        "pin_chat_message": ("pinChatMessage", 0),
        "edit_message_reply_markup": ("editMessageReplyMarkup", 1),
    }

    def __init__(self, limiter, clock, *, result=None):
        self._limiter = limiter
        self._clock = clock
        self._result = result
        #: ``(endpoint, chat_id, t)`` for every call that actually RAN.
        self.calls: list[tuple[str, object, float]] = []

    def __getattr__(self, name):
        if name not in self._ENDPOINTS:
            raise AttributeError(name)
        endpoint, position = self._ENDPOINTS[name]

        async def _method(*args, rate_limit_args=None, **kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id is None and len(args) > position:
                chat_id = args[position]

            async def _call():
                self.calls.append((endpoint, chat_id, self._clock()))
                return self._result if self._result is not None else _Sent()

            return await self._limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": chat_id}, rate_limit_args=rate_limit_args,
            )

        return _method

    def endpoints(self) -> list[str]:
        return [endpoint for endpoint, _chat, _t in self.calls]

    def calls_for(self, chat_id) -> list[tuple[str, object, float]]:
        return [c for c in self.calls if c[1] == chat_id]


class _Sent:
    """What a successful send returns: something with a ``message_id``."""

    message_id = 4242

    def __bool__(self) -> bool:
        return True


@pytest.fixture
def gated_bot(limiter, flood_clock):
    return _GatedBot(limiter, flood_clock)


class _HttpRecorder:
    """Every HTTP request the rich path actually issued.

    ``requests == []`` is the honest form of "no HTTP happened" for the
    rich path: it is recorded at the transport, below the limiter, below
    `_post`, so nothing between them can fake it.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, object]] = []

    def endpoints(self) -> list[str]:
        return [m for m, _chat in self.requests]


@pytest.fixture
def rich_http(monkeypatch):
    """The REAL ``rich_message._post`` wired to an ``httpx.MockTransport``.

    The autouse ``_block_real_telegram_http`` fixture replaces ``_post``
    with a stub that raises, which is right for the rest of the suite and
    useless here: a test about the boundary INSIDE ``_post`` has to run
    the real one. Restoring it the way
    ``per-chat-flood-budget/test_card_cadence_rule.py``'s ``telegram``
    fixture does, with a MockTransport underneath so nothing can reach
    ``api.telegram.org`` even if a guard fails.
    """
    recorder = _HttpRecorder()
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        recorder.requests.append((endpoint, payload.get("chat_id")))
        return httpx.Response(200, json={
            "ok": True,
            "result": {"message_id": payload.get("message_id", 4242)},
        })

    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(
        rm, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield recorder
    monkeypatch.setattr(rm, "_client", None)
