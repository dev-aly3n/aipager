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
import time
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
        #: ``(endpoint, args, kwargs)`` for the same calls — what a row
        #: asserting on the TEXT of a card edit needs. Separate from
        #: ``calls`` so the tuple shape every other row unpacks is
        #: unchanged.
        self.sent: list[tuple[str, tuple, dict]] = []

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
                self.sent.append((endpoint, args, kwargs))
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


# ── the §6 replay harness: a virtual loop and a PENALISED Telegram ──────────
#
# The card loop has no injectable clock of its own: it sleeps on the global
# `asyncio.sleep`, and patching that (or `create_task`) through a module
# path is forbidden — `aipager.bot.notify.asyncio` IS the global module and
# doing so has hung this suite twice. So these rows supply an event LOOP
# whose `time()` is virtual and which jumps to the next scheduled timer when
# nothing is ready. Forty-five simulated minutes cost a few real
# milliseconds, every gap is exact rather than jittery, and NOTHING in
# `asyncio` is patched — only the loop object is ours.
#
# Copied from `tests/integration/per-chat-flood-budget/test_card_cadence_rule.py`
# rather than imported: that file is a test module, importing it would run
# its collection, and the sanctioned pattern in this repo is to copy the
# loop-closing harness per directory.

_LATENCY = 1e-6


class _VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps to the next timer instead of
    waiting for it."""

    def __init__(self) -> None:
        super().__init__()
        self._virtual = 1_000_000.0
        self._real_deadline = time.monotonic() + 20.0

    def time(self) -> float:
        return self._virtual

    def _run_once(self) -> None:
        if time.monotonic() > self._real_deadline:
            raise AssertionError(
                "the virtual loop spun for 20 REAL seconds — something is "
                "polling without a timer")
        if self._scheduled and not self._ready:
            when = self._scheduled[0]._when
            if when > self._virtual:
                # `+ _LATENCY`: a real loop never wakes a hair EARLY, and
                # code comparing `now - last_edit` against the interval it
                # just slept would otherwise sit exactly on the boundary.
                self._virtual = when + _LATENCY
        super()._run_once()


@pytest.fixture
def vloop(monkeypatch):
    """A virtual-time loop with the animation module's OWN clock bound to
    it — the sanctioned trick, never the global `time` module.

    Torn down like every flood fixture in this repo: pending tasks
    cancelled, executor shut down, loop closed. The suite runs pinned
    under a 1 GiB RLIMIT_AS and a leaked loop kills an unrelated later
    test with "can't start new thread".
    """
    from aipager.bot import animation as an

    loop = _VirtualLoop()
    monkeypatch.setattr(an, "time", types.SimpleNamespace(
        monotonic=loop.time, time=time.time, sleep=time.sleep))
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


class _PenalisedTelegram:
    """Telegram as it behaves toward a bot WITH A HISTORY (§6, row N).

    `_StrictTelegram` in the 8.21 suite models a CLEAN bot: a fixed
    1/s-burst-3 allowance per chat, and a 429 with `retry_after: 5` when
    you exceed it. Measuring a flood fix against that is the mistake
    `per-chat-flood-budget.md` §12 made — it proves nothing about the
    account the incident actually happened to.

    This one adds the two properties that made 2026-09-15 what it was:

    * **A SHRINKING allowance.** Every violation tightens the rate this
      chat is permitted, so a bot that keeps pushing keeps being wrong
      about what is safe. That is why a guessed constant cannot work and
      the rate has to be learned.
    * **AN ESCALATING LADDER.** Violations are answered `retry_after`
      1283 -> 312 -> 34212 s, the real measured sequence. The third is
      9.5 hours.

    And it records the one thing the whole ship is judged on:
    `into_ban`, every request that arrived while a ban was still running.
    On 0.7.12 that list had 3, then 5, then 9 entries.
    """

    #: The real ladder, in order, from the 2026-09-15 journal.
    LADDER = (1283.0, 312.0, 34212.0)

    #: Endpoints Telegram does not meter against a chat's message budget
    #: (measured 2026-09-12 — chat actions answered 200 throughout a real
    #: `retry_after=10` window that refused every edit into the same chat).
    EXEMPT: frozenset = frozenset({"sendChatAction"})

    def __init__(self, clock, rate: float = 1.0, burst: float = 3.0) -> None:
        self.clock = clock
        self.rate = rate
        self.burst = burst
        #: Every request, admitted or not: (endpoint, chat, message_id, t).
        self.calls: list[tuple] = []
        #: Requests that violated the CURRENT allowance.
        self.violations: list[tuple] = []
        #: Requests that arrived while a ban was active — the number this
        #: whole ship exists to drive to zero.
        self.into_ban: list[tuple] = []
        #: The retry_after answered for each violation, in order.
        self.answered: list[float] = []
        self._tokens: dict = {}
        self._banned_until: dict = {}
        self._rung: dict = {}

    # ── the regime ──

    def _allowance(self, chat_id) -> float:
        """This chat's CURRENT permitted rate — it shrinks per ban."""
        return self.rate / (2 ** self._rung.get(chat_id, 0))

    def ban(self, chat_id, retry_after: float) -> None:
        """Ban *chat_id* from the TELEGRAM side, without a violation.

        A replay arms the daemon's mute directly (that is what
        `transport._send_with_retry` does on a ban-sized `retry_after`),
        and the fake has to be told the same thing — otherwise it does not
        consider itself banned and `into_ban` silently counts nothing,
        which makes every "zero requests into a ban" assertion vacuous.
        Found exactly that way, by deleting the gate and watching the row
        still pass.
        """
        self._banned_until[chat_id] = self.clock() + float(retry_after)
        self._rung[chat_id] = self._rung.get(chat_id, 0) + 1

    def banned(self, chat_id) -> bool:
        return self.clock() < self._banned_until.get(chat_id, 0.0)

    def ban_remaining(self, chat_id) -> float:
        return max(0.0, self._banned_until.get(chat_id, 0.0) - self.clock())

    def admit(self, endpoint: str, chat_id, message_id=None):
        """Record a request and answer it.

        Returns ``None`` when the request is accepted, or the
        ``retry_after`` seconds Telegram answers with.
        """
        now = self.clock()
        self.calls.append((endpoint, chat_id, message_id, now))

        if self.banned(chat_id):
            # THE NUMBER THAT MATTERS. A request into an ACTIVE ban is
            # itself a violation, and on the real API it is what escalated
            # the ladder. Recorded, and answered with what is left.
            self.into_ban.append((endpoint, chat_id, message_id, now))
            return self.ban_remaining(chat_id)

        if endpoint in self.EXEMPT:
            return None

        allowance = self._allowance(chat_id)
        tokens, last = self._tokens.get(chat_id, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * allowance)
        if tokens < 1.0:
            self._tokens[chat_id] = (tokens, now)
            self.violations.append((endpoint, chat_id, message_id, now))
            rung = self._rung.get(chat_id, 0)
            retry_after = self.LADDER[min(rung, len(self.LADDER) - 1)]
            self._rung[chat_id] = rung + 1
            self._banned_until[chat_id] = now + retry_after
            self.answered.append(retry_after)
            return retry_after
        self._tokens[chat_id] = (tokens - 1.0, now)
        return None

    # ── accessors ──

    def stamps_for(self, chat_id) -> list[float]:
        return [t for _e, chat, _m, t in self.calls if chat == chat_id]

    def metered_stamps_for(self, chat_id) -> list[float]:
        return [t for endpoint, chat, _m, t in self.calls
                if chat == chat_id and endpoint not in self.EXEMPT]


@pytest.fixture
def penalised_telegram(vloop, monkeypatch):
    """`_PenalisedTelegram` wired to the REAL `rich_message._post` through
    an `httpx.MockTransport`, so every card edit and every answer is
    metered by the limiter inside it rather than by the harness."""
    fake = _PenalisedTelegram(vloop.time)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        retry_after = fake.admit(endpoint, payload.get("chat_id"),
                                 payload.get("message_id"))
        if retry_after is not None:
            return httpx.Response(200, json={
                "ok": False, "error_code": 429, "description": "Too Many",
                "parameters": {"retry_after": int(retry_after)}})
        return httpx.Response(200, json={
            "ok": True,
            "result": {"message_id": payload.get("message_id", 1)}})

    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(
        rm, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield fake
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def replay_limiter(vloop, monkeypatch):
    """The daemon's limiter on the virtual clock, installed exactly the
    way `lifecycle._make_builder` installs it."""
    lim = BudgetRateLimiter(clock=vloop.time, sleep=asyncio.sleep)
    rm.set_rate_limiter(lim)
    yield lim
    lim.reset()
