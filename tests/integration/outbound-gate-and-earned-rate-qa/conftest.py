"""Independent QA plumbing for the 8.26-8.29 contract (spec rows A-N).

Written black-box against `entrypoints.md`: nothing here imports anything
that document does not name, and every fixture exists so a row can be
stated as one sentence.

Two rules this directory keeps, both learned the hard way in this repo:

* **Nothing in ``asyncio`` is patched, ever.** ``aipager.bot.notify.asyncio``
  IS the global module; patching ``sleep``/``create_task`` through a module
  path has hung this suite twice. Time moves through the injected
  ``clock=``/``sleep=`` seam of ``BudgetRateLimiter`` or by rebinding a
  MODULE'S OWN ``time`` reference (``flood.time``), which is the sanctioned
  trick (``tests/test_flood_mute.py:42-57``).
* **A ``MagicMock`` bot proves nothing about the gate.** ``conftest.mk_bot``
  binds ``bot._app.bot`` to a ``MagicMock``, which enforces nothing, so
  every "zero Bot API calls while muted" claim here runs on
  :class:`_QaGatedBot` (each method funnels through the real
  ``BudgetRateLimiter.process_request`` with the real endpoint name) or on
  the real ``rich_message._post`` behind an ``httpx.MockTransport``.

Every fixture is deliberately paired with a *positive control* in the tests
that use it: a row first proves the call it is about actually happens when
the chat is healthy, and only then arms the mute. A "zero calls" assertion
on a path that never ran is the vacuous pass this directory exists to
avoid.
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

#: The REAL ``_post``, captured at import time — before the autouse
#: ``_block_real_telegram_http`` fixture swaps in its refusing stub.
_REAL_POST = rm._post


@pytest.fixture
def run_async():
    """Loop-CLOSING override of the shared fixture.

    These rows leave limiter waiters behind and the suite runs pinned
    under a 1 GiB ``RLIMIT_AS`` (roadmap 8.19): a leaked loop fails an
    unrelated later test with "can't start new thread".
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
                    asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


class QaClock:
    """One ``advance()`` that moves the limiter's monotonic clock AND the
    wall clock ``flood.py`` reads for the mute deadline (D-7).

    Moving only one of them is the classic false pass here: the deadline
    is wall-clock while the budget is monotonic, so a row that combines a
    mute with the budget and advances one clock proves nothing about the
    other.
    """

    def __init__(self, mono: float = 500_000.0, wall: float | None = None) -> None:
        self.now = mono
        # The wall clock starts at the REAL now: `aipager status` and
        # `doctor` are OTHER PROCESSES that read the signal files against
        # the real clock, so a fake wall a week in the past would make
        # every mute they see look lapsed — a fixture deciding the
        # outcome, which is the disease `steady_clock` exists for.
        self.wall = time.time() if wall is None else wall
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)
        self.wall += float(seconds)

    def rewind_wall(self, seconds: float) -> None:
        """Move the WALL clock backwards only — an NTP step, the stated
        risk of making the deadline wall-clock."""
        self.wall -= float(seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.advance(seconds)
        await asyncio.sleep(0)


@pytest.fixture
def qa_clock(monkeypatch):
    """A :class:`QaClock` bound to each module's OWN ``time`` reference."""
    clock = QaClock()
    fake = types.SimpleNamespace(
        monotonic=lambda: clock.now, time=lambda: clock.wall)
    monkeypatch.setattr(flood, "time", fake)
    monkeypatch.setattr(flood_budget, "time", fake)
    return clock


@pytest.fixture
def anim_clock(qa_clock, monkeypatch):
    """``animation.py``'s OWN ``time`` reference, moved by the same
    ``qa_clock``.

    The card loop paces itself off ``animation.time.monotonic()``. A row
    that advances only the limiter's clock leaves the animator frozen at
    one instant, so its cadence floor suppresses every edit after the
    first — and a "exactly one edit in ten ticks" assertion would then
    pass for a chat that is perfectly healthy. Found exactly that way.
    """
    from aipager.bot import animation as an
    monkeypatch.setattr(an, "time", types.SimpleNamespace(
        monotonic=lambda: qa_clock.now, time=lambda: qa_clock.wall,
        sleep=time.sleep))
    return qa_clock


@pytest.fixture
def limiter(qa_clock):
    """The daemon's limiter on the QA clock.

    ``signal_path=`` is passed EXPLICITLY — without it any assertion about
    the backoff signal file is vacuous on a machine with no daemon socket.

    It is also installed as THE live limiter through
    ``rich_message.set_rate_limiter``, the way ``lifecycle._make_builder``
    installs it: ``flood_state`` finds the limiter that way, so a fixture
    that skipped this would persist the mute and silently lose the rate —
    and the restart row would then be measuring the fixture.
    """
    lim = BudgetRateLimiter(clock=qa_clock, sleep=qa_clock.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(lim)
    yield lim
    lim.reset()


class _Sent:
    """What a successful send returns: something with a ``message_id``."""

    message_id = 4242

    def __bool__(self) -> bool:
        return True


class _QaGatedBot:
    """``ExtBot``-shaped double: every method funnels through
    ``limiter.process_request`` under the endpoint name PTB would use.

    A refused call can never reach ``.calls`` — only the callback appends,
    and the gate is specified to never invoke the callback.
    """

    _ENDPOINTS = {
        "send_message": ("sendMessage", 0),
        "edit_message_text": ("editMessageText", 1),
        "edit_message_caption": ("editMessageCaption", 1),
        "edit_message_reply_markup": ("editMessageReplyMarkup", 1),
        "delete_message": ("deleteMessage", 0),
        "send_document": ("sendDocument", 0),
        "send_photo": ("sendPhoto", 0),
        "send_chat_action": ("sendChatAction", 0),
        "set_message_reaction": ("setMessageReaction", 0),
        "pin_chat_message": ("pinChatMessage", 0),
        "unpin_chat_message": ("unpinChatMessage", 0),
        "copy_message": ("copyMessage", 0),
        "forward_message": ("forwardMessage", 0),
    }

    def __init__(self, limiter, clock, *, result=None):
        self._limiter = limiter
        self._clock = clock
        self._result = result
        #: ``(endpoint, chat_id, t)`` for every call that actually RAN.
        self.calls: list[tuple[str, object, float]] = []
        #: The same calls with their arguments, for the rows that have to
        #: read what a card actually SAID and not merely that it was sent.
        self.payloads: list[tuple[str, tuple, dict]] = []
        #: Every attribute the caller reached for that is not an endpoint,
        #: so a missed send surface shows up instead of passing silently.
        self.unknown: list[str] = []

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ENDPOINTS:
            self.__dict__.setdefault("unknown", []).append(name)
            raise AttributeError(name)
        endpoint, position = self._ENDPOINTS[name]

        async def _method(*args, rate_limit_args=None, **kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id is None and len(args) > position:
                chat_id = args[position]

            async def _call():
                self.calls.append((endpoint, chat_id, self._clock()))
                self.payloads.append((endpoint, args, kwargs))
                return self._result if self._result is not None else _Sent()

            return await self._limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": chat_id}, rate_limit_args=rate_limit_args,
            )

        return _method

    def endpoints(self) -> list[str]:
        return [endpoint for endpoint, _chat, _t in self.calls]

    def stamps(self) -> list[float]:
        return [t for _e, _c, t in self.calls]

    def calls_for(self, chat_id) -> list[tuple[str, object, float]]:
        return [c for c in self.calls if c[1] == chat_id]

    def texts(self) -> list[str]:
        """The text of every call that carried one.

        PTB takes the body positionally (``edit_message_text(text, chat_id,
        message_id)``) or by keyword depending on the caller, so both are
        looked at — reading only one of them would silently return ``[]``
        and make every assertion about card COPY vacuous.
        """
        out: list[str] = []
        for _endpoint, args, kwargs in self.payloads:
            if isinstance(kwargs.get("text"), str):
                out.append(kwargs["text"])
                continue
            out.extend(a for a in args if isinstance(a, str))
        return out


@pytest.fixture
def gated_bot(limiter, qa_clock):
    return _QaGatedBot(limiter, qa_clock)


@pytest.fixture
def make_gated_bot(qa_clock):
    """Bind a fresh double to an arbitrary limiter — a restart row builds
    a SECOND limiter and the double has to follow it."""
    def _make(limiter):
        return _QaGatedBot(limiter, qa_clock)
    return _make


class _HttpRecorder:
    """Every HTTP request the rich path actually issued, recorded at the
    transport — below ``_post``, below the limiter, so nothing between
    them can fake it."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, object, dict]] = []

    def endpoints(self) -> list[str]:
        return [m for m, _chat, _p in self.requests]

    def texts(self) -> list[str]:
        return [p.get("text", "") for _m, _c, p in self.requests]


@pytest.fixture
def rich_http(monkeypatch):
    """The REAL ``rich_message._post`` wired to an ``httpx.MockTransport``.

    The autouse ``_block_real_telegram_http`` fixture replaces ``_post``
    with a stub that raises, which is right for the rest of the suite and
    useless for a row about the boundary INSIDE ``_post``. The
    MockTransport underneath means nothing can reach api.telegram.org even
    if a guard fails.
    """
    recorder = _HttpRecorder()
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        recorder.requests.append((endpoint, payload.get("chat_id"), payload))
        return httpx.Response(200, json={
            "ok": True,
            "result": {"message_id": payload.get("message_id", 4242)},
        })

    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(
        rm, "_client",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield recorder
    monkeypatch.setattr(rm, "_client", None)


# ── the §6 replay: a virtual loop and a PENALISED Telegram ─────────────────
#
# The replay needs 45 simulated minutes to cost milliseconds and every gap
# to be exact. Supplying our own event LOOP whose ``time()`` is virtual
# does that without patching one thing in ``asyncio``: only the loop
# object is ours. Copied per-directory, which is this repo's sanctioned
# pattern (importing another test module would run its collection).

_LATENCY = 1e-6


class _VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps to the next timer."""

    def __init__(self) -> None:
        super().__init__()
        self._virtual = 500_000.0
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
                self._virtual = when + _LATENCY
        super()._run_once()


@pytest.fixture
def vloop(monkeypatch):
    """A virtual-time loop, with ``flood``'s and ``flood_budget``'s OWN
    ``time`` references bound to it.

    Binding ``flood.time`` matters more here than anywhere else: the mute
    deadline is wall-clock, so a replay that fast-forwards 45 minutes of
    VIRTUAL time while ``flood`` reads the REAL wall clock would never see
    a single mute lift — the "zero answers lost" half of §6 would be
    unfalsifiable.
    """
    loop = _VirtualLoop()
    base_wall = 1_789_000_000.0
    origin = loop.time()
    fake = types.SimpleNamespace(
        monotonic=loop.time,
        time=lambda: base_wall + (loop.time() - origin),
        sleep=time.sleep,
    )
    monkeypatch.setattr(flood, "time", fake)
    monkeypatch.setattr(flood_budget, "time", fake)
    # The card loop paces itself off `animation.time.monotonic()`; left on
    # the real clock it would sleep 45 REAL minutes inside a virtual-time
    # replay, or (worse) spin.
    from aipager.bot import animation as an
    monkeypatch.setattr(an, "time", fake)
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

    Two properties made 2026-09-15 what it was, and both are modelled:

    * a **shrinking allowance** — every ban halves what this chat is
      permitted, so a guessed constant rate cannot stay safe;
    * an **escalating ladder** — violations are answered ``retry_after``
      1283 -> 312 -> 34212 s, the real measured sequence.

    ``into_ban`` is the number the whole ship is judged on: every request
    that arrived while a ban was still running (3, then 5, then 9 on
    0.7.12).
    """

    LADDER = (1283.0, 312.0, 34212.0)
    EXEMPT: frozenset = frozenset({"sendChatAction"})

    def __init__(self, clock, rate: float = 1.0, burst: float = 3.0) -> None:
        self.clock = clock
        self.rate = rate
        self.burst = burst
        self.calls: list[tuple] = []
        self.violations: list[tuple] = []
        self.into_ban: list[tuple] = []
        self.answered: list[float] = []
        self._tokens: dict = {}
        self._banned_until: dict = {}
        self._rung: dict = {}

    def _allowance(self, chat_id) -> float:
        return self.rate / (2 ** self._rung.get(chat_id, 0))

    def ban(self, chat_id, retry_after: float) -> None:
        """Ban from the TELEGRAM side without a violation, so a replay can
        arm both sides of the world at once."""
        self._banned_until[chat_id] = self.clock() + float(retry_after)
        self._rung[chat_id] = self._rung.get(chat_id, 0) + 1

    def banned(self, chat_id) -> bool:
        return self.clock() < self._banned_until.get(chat_id, 0.0)

    def ban_remaining(self, chat_id) -> float:
        return max(0.0, self._banned_until.get(chat_id, 0.0) - self.clock())

    def admit(self, endpoint: str, chat_id, message_id=None):
        """Record a request and answer it: ``None`` accepted, else the
        ``retry_after`` Telegram replies with."""
        now = self.clock()
        self.calls.append((endpoint, chat_id, message_id, now))

        if self.banned(chat_id):
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

    def stamps_for(self, chat_id) -> list[float]:
        return [t for _e, chat, _m, t in self.calls if chat == chat_id]

    def metered_stamps_for(self, chat_id) -> list[float]:
        return [t for endpoint, chat, _m, t in self.calls
                if chat == chat_id and endpoint not in self.EXEMPT]


@pytest.fixture
def penalised_telegram(vloop, monkeypatch):
    """`_PenalisedTelegram` wired to the REAL ``rich_message._post``
    through an ``httpx.MockTransport``."""
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
        rm, "_client",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield fake
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def replay_limiter(vloop):
    """The daemon's limiter on the virtual clock, installed into the rich
    path exactly the way ``lifecycle._make_builder`` installs it."""
    lim = BudgetRateLimiter(clock=vloop.time, sleep=asyncio.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(lim)
    yield lim
    lim.reset()


# ── small shared helpers ──────────────────────────────────────────────────

def most_in_window(stamps, window: float) -> int:
    """The largest number of stamps inside any rolling *window*."""
    ordered = sorted(stamps)
    worst = 0
    for i, start in enumerate(ordered):
        count = sum(1 for t in ordered[i:] if t - start < window)
        worst = max(worst, count)
    return worst
