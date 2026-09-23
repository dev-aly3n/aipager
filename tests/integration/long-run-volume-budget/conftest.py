"""Shared plumbing for the 8.30 long-run volume rows. No assertions, no
production logic — every fixture here exists so a row can be written as
one sentence.

Copied from ``tests/integration/outbound-gate-and-earned-rate/conftest.py``
rather than imported (the repo's convention: that file's collection must
not run here), and it keeps that file's two rules:

* **Nothing in ``asyncio`` is ever patched.** ``aipager.bot.notify.asyncio``
  IS the global module, and patching ``sleep``/``create_task`` through a
  module path has hung this suite twice. Time is moved by an injected
  ``clock=``/``sleep=``/``wall_clock=`` seam, or by rebinding a MODULE'S
  OWN ``time`` reference (``flood.time``, ``flood_budget.time``).
* **A ``MagicMock`` bot proves nothing about the gate.** Every "zero calls
  on the wire" claim here runs on :class:`_GatedBot`, which funnels each
  call through the real ``BudgetRateLimiter.process_request``.

What 8.30 adds to the 0.7.13 harness is a WALL clock that moves with the
monotonic one everywhere: the warning regime (6 h) and the ban memory
(7 days) are wall-clock state, and a row that "advances six hours" on the
monotonic clock alone advances neither.
"""

from __future__ import annotations

import asyncio
import json
import time
import types

import httpx
import pytest

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot import flood, flood_budget
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = 256113222

#: The REAL ``_post``, captured before conftest's autouse
#: ``_block_real_telegram_http`` swaps in its refusing stub.
_REAL_POST = rm._post


@pytest.fixture
def run_async():
    """Loop-CLOSING override of the shared fixture: these rows leave
    limiter waiters and typing tasks behind, and a leaked loop fails an
    unrelated later test with "can't start new thread" under the suite's
    address-space cap."""
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
    """The limiter's monotonic clock AND the wall clock, moved together by
    one ``advance()``. Advancing only one is the classic false pass in this
    area: the ban memory and the warning regime are wall-clock while the
    budget is monotonic."""

    def __init__(self, mono: float = 1_000_000.0,
                 wall: float = 1_800_000_000.0) -> None:
        self.now = mono
        self.wall = wall

    def __call__(self) -> float:
        return self.now

    def wall_clock(self) -> float:
        return self.wall

    def advance(self, seconds: float) -> None:
        seconds = max(float(seconds), 0.0)
        self.now += seconds
        self.wall += seconds

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)


@pytest.fixture
def flood_clock(monkeypatch):
    """A :class:`FloodClock` with ``flood.py``'s and ``flood_budget.py``'s
    OWN ``time`` bound to it — never the global module, never ``asyncio``."""
    clock = FloodClock()
    fake = types.SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: clock.wall)
    monkeypatch.setattr(flood, "time", fake)
    monkeypatch.setattr(flood_budget, "time", fake)
    return clock


@pytest.fixture
def limiter(flood_clock):
    """The daemon's limiter on the shared clock. ``signal_path`` explicit,
    so no assertion about the backoff signal is vacuous on a machine
    without a daemon socket."""
    lim = BudgetRateLimiter(clock=flood_clock, sleep=flood_clock.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    yield lim
    lim.reset()


class _Sent:
    """What a successful send returns: something with a ``message_id``."""

    message_id = 4242

    def __bool__(self) -> bool:
        return True


class _GatedBot:
    """An ``ExtBot``-shaped double whose every method funnels through
    ``limiter.process_request`` with the endpoint PTB would use. A refused
    call never reaches ``.calls``: the callback is the only thing that
    appends to it."""

    _ENDPOINTS = {
        "send_message": ("sendMessage", 0),
        "edit_message_text": ("editMessageText", 1),
        "delete_message": ("deleteMessage", 0),
        "send_chat_action": ("sendChatAction", 0),
        "set_message_reaction": ("setMessageReaction", 0),
    }

    def __init__(self, limiter, clock, *, fail=None):
        self._limiter = limiter
        self._clock = clock
        #: ``endpoint -> exception factory`` raised INSIDE the callback,
        #: i.e. after the gate admitted the call — Telegram's answer.
        self.fail = dict(fail or {})
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
                factory = self.fail.pop(endpoint, None)
                if factory is not None:
                    raise factory()
                return _Sent()

            return await self._limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": chat_id}, rate_limit_args=rate_limit_args,
            )

        return _method

    def stamps(self, endpoint, chat_id=CHAT) -> list[float]:
        return [t for e, c, t in self.calls if e == endpoint and c == chat_id]


@pytest.fixture
def gated_bot(limiter, flood_clock):
    return _GatedBot(limiter, flood_clock)


class _HttpRecorder:
    """Every HTTP request the rich path actually issued, recorded at the
    transport — below the limiter and below ``_post`` — so nothing between
    them can fake "no HTTP happened"."""

    def __init__(self) -> None:
        #: ``(endpoint, chat_id, payload)``
        self.requests: list[tuple[str, object, dict]] = []
        #: ``t`` per request, on :attr:`clock` when a row sets one.
        self.stamps: list[float] = []
        self.clock = None

    def endpoints(self) -> list[str]:
        return [m for m, _chat, _p in self.requests]

    def edits(self, message_id=None) -> list[tuple[float, str]]:
        """``(t, markdown)`` for every rich ``editMessageText``."""
        out = []
        for (endpoint, _chat, payload), t in zip(self.requests, self.stamps):
            if endpoint != "editMessageText":
                continue
            if message_id is not None and payload.get("message_id") != message_id:
                continue
            rich = payload.get("rich_message") or {}
            out.append((t, rich.get("markdown") or payload.get("text") or ""))
        return out


@pytest.fixture
def rich_http(monkeypatch):
    """The REAL ``rich_message._post`` over an ``httpx.MockTransport``, so
    a rich card edit is metered by the limiter exactly as in the daemon
    and nothing can reach ``api.telegram.org`` even if a guard fails."""
    recorder = _HttpRecorder()
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        recorder.requests.append((endpoint, payload.get("chat_id"), payload))
        recorder.stamps.append(recorder.clock() if recorder.clock else 0.0)
        return httpx.Response(200, json={
            "ok": True,
            "result": {"message_id": payload.get("message_id", 4242)},
        })

    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(
        rm, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield recorder
    monkeypatch.setattr(rm, "_client", None)


# ── a virtual event loop (copied from the 0.7.13 harness) ────────────────────
#
# The card loop and the typing loop sleep on the global `asyncio.sleep`,
# and patching that through a module path is forbidden. So these rows get
# an event LOOP whose `time()` is virtual and which jumps to the next timer
# when nothing is ready: hours of virtual time cost milliseconds, and
# NOTHING in `asyncio` is patched — only the loop object is ours.

_LATENCY = 1e-6
#: The wall clock at virtual-loop time 1_000_000.0.
_WALL0 = 1_800_000_000.0


class _VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps to the next timer instead of
    waiting for it."""

    def __init__(self, real_seconds: float = 60.0) -> None:
        super().__init__()
        self._virtual = 1_000_000.0
        self._real_deadline = time.monotonic() + real_seconds

    def time(self) -> float:
        return self._virtual

    def wall(self) -> float:
        return _WALL0 + (self._virtual - 1_000_000.0)

    def _run_once(self) -> None:
        if time.monotonic() > self._real_deadline:
            raise AssertionError(
                "the virtual loop spun for too many REAL seconds — something "
                "is polling without a timer")
        if self._scheduled and not self._ready:
            when = self._scheduled[0]._when
            if when > self._virtual:
                self._virtual = when + _LATENCY
        super()._run_once()


@pytest.fixture
def vloop(monkeypatch):
    """A virtual-time loop with every module that reads time for this
    feature bound to it — each module's OWN ``time`` reference, never the
    global module and never ``asyncio``: the animator (card cadence, typing
    stamps), notify (the hook-driven edit gate), and the flood modules
    (the mute deadline, ban stamps, the warning regime)."""
    from aipager.bot import animation, notify

    loop = _VirtualLoop()
    fake = types.SimpleNamespace(monotonic=loop.time, time=loop.wall,
                                 sleep=time.sleep)
    for module in (animation, notify, flood, flood_budget):
        monkeypatch.setattr(module, "time", fake)
    yield loop
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


@pytest.fixture
def vlimiter(vloop):
    """The daemon's limiter on the virtual loop — monotonic AND wall clock
    — installed the way ``lifecycle._make_builder`` installs it."""
    lim = BudgetRateLimiter(clock=vloop.time, sleep=asyncio.sleep,
                            wall_clock=vloop.wall,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(lim)
    yield lim
    lim.reset()


@pytest.fixture
def vbot(vloop, vlimiter):
    """A PTB double on the virtual loop's clock, gated by ``vlimiter``."""
    return _GatedBot(vlimiter, vloop.time)
