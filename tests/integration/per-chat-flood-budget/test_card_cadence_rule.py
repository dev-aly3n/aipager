"""The adaptive busy-card cadence, driven end to end (rows A, B1–B4, C2,
M; rules R1, R3, R4, R9).

**How the clock works here, and why it is not the forbidden patch.** The
card loop has no injectable clock of its own: it sleeps on the global
``asyncio.sleep``, and CLAUDE.md forbids patching that (or ``create_task``)
through a module path — ``aipager.bot.notify.asyncio`` IS the global
module and doing so has hung this suite twice. So these tests supply an
event LOOP whose ``time()`` is virtual: when nothing is ready to run it
jumps straight to the next scheduled timer, so twelve simulated seconds of
card animation cost about three real milliseconds and every gap is exact
rather than jittery. Nothing in ``asyncio`` is patched; only the loop
object is ours. ``aipager.bot.animation``'s OWN ``time`` reference is
rebound to that same virtual clock — the identical, sanctioned trick
``tests/conftest.py::steady_clock`` uses, never the global ``time``
module.

Everything the card sends is routed through the limiter by a double
shaped like PTB's ``ExtBot`` (typing indicators) or by the real
``rich_message._post`` behind an ``httpx.MockTransport`` (card edits), so
the rate assertions are about the code, not about the harness.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import statistics
import time
import types
from typing import Any

import httpx
import pytest

import aipager.bot.rich_message as rm
import aipager.config as _config
from aipager.bot import animation as an
from aipager.bot.flood_budget import BudgetRateLimiter, card_interval
from aipager.state import Status

_REAL_POST = rm._post

PRIVATE = 256113222          # the chat `_pin_single_chat_config` pins
GROUP = -1001234567890


_LATENCY = 1e-6


class _VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps to the next timer instead of
    waiting for it. Nothing is patched: the loop's ``time()`` and its
    idle behaviour are the whole mechanism."""

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
                # ``+ _LATENCY``: a real loop never wakes a hair EARLY, and
                # code that compares ``now - last_edit`` against the same
                # interval it slept would sit exactly on the boundary
                # without it. Measured against a real-clock run of the same
                # scenario (gaps 1.322 s), this reproduces it to 1 µs.
                self._virtual = when + _LATENCY
        super()._run_once()


@pytest.fixture
def vloop(monkeypatch):
    """A virtual-time loop, with the animation module's own clock bound to
    it. Torn down like ``tests/test_miniapp_webapp_sdk.py``'s
    ``run_async``: pending tasks cancelled, executor shut down, loop
    closed — a leaked loop or thread kills an unrelated later test under
    the suite's 1 GiB address-space cap."""
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


class _StrictTelegram:
    """Telegram as the design assumes it behaves: one call per second per
    chat, burst three. Anything faster is recorded as a violation and
    answered with a 429 — so "no 429 came back" is a property of the code
    under test, not of the assertion.

    ``sendChatAction`` is METERED BY NOTHING here, which is not a
    convenience: it is what the real API was measured doing on 2026-09-12
    (design §12). Bounded probes drove one chat into a genuine
    ``retry_after=10``; through that whole window every
    ``editMessageText`` was refused and all ELEVEN ``sendChatAction
    typing`` calls returned 200, with the bubble visible on the phone.
    A fake that charged the action to the message bucket would be
    asserting 8.21's disproved assumption, and would fail any daemon that
    sends the indicator however sparingly. The calls are still RECORDED,
    so a row can count them (and ``metered_stamps_for`` is what the
    budget ceilings are asserted on)."""

    #: Endpoints Telegram does not count against a chat's message budget.
    EXEMPT: frozenset = frozenset({"sendChatAction"})

    def __init__(self, clock, rate=1.0, burst=3.0):
        self.clock, self.rate, self.burst = clock, rate, burst
        self.calls: list[tuple[str, Any, Any, float]] = []
        self.violations: list[tuple[str, Any, Any, float]] = []
        self._tokens: dict[Any, tuple[float, float]] = {}

    def admit(self, endpoint: str, chat_id: Any, message_id: Any = None) -> bool:
        now = self.clock()
        self.calls.append((endpoint, chat_id, message_id, now))
        if endpoint in self.EXEMPT:
            return True
        tokens, last = self._tokens.get(chat_id, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1.0:
            self.violations.append((endpoint, chat_id, message_id, now))
            self._tokens[chat_id] = (tokens, now)
            return False
        self._tokens[chat_id] = (tokens - 1.0, now)
        return True

    def stamps_for(self, chat_id: Any) -> list[float]:
        return [t for _, chat, _, t in self.calls if chat == chat_id]

    def metered_stamps_for(self, chat_id: Any) -> list[float]:
        """Only the calls that cost the chat a token — what a budget
        ceiling (1/s private, 20/minute group) is actually about."""
        return [t for endpoint, chat, _, t in self.calls
                if chat == chat_id and endpoint not in self.EXEMPT]

    def actions_for(self, chat_id: Any) -> list[float]:
        """The stamps of the ``sendChatAction`` calls into *chat_id*."""
        return [t for endpoint, chat, _, t in self.calls
                if chat == chat_id and endpoint == "sendChatAction"]


@pytest.fixture
def telegram(vloop, monkeypatch):
    """The strict fake, wired to the REAL ``rich_message._post`` through
    MockTransport so every card edit is metered by the limiter inside
    it."""
    fake = _StrictTelegram(vloop.time)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if not fake.admit(endpoint, payload.get("chat_id"),
                          payload.get("message_id")):
            return httpx.Response(200, json={
                "ok": False, "error_code": 429, "description": "Too Many",
                "parameters": {"retry_after": 5}})
        return httpx.Response(200, json={
            "ok": True, "result": {"message_id": payload.get("message_id", 1)}})

    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(rm, "_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(rm, "_client_closed", False, raising=False)
    yield fake
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def limiter(vloop, monkeypatch):
    """The daemon's limiter on the virtual clock, installed exactly the
    way ``lifecycle._make_builder`` installs it."""
    lim = BudgetRateLimiter(clock=vloop.time, sleep=asyncio.sleep)
    rm.set_rate_limiter(lim)
    yield lim
    lim.reset()


def _card(bot, label, msg_id, chat_id):
    """A BUSY session with an open, streaming busy card."""
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = Status.BUSY
    sess.busy_msg_id = msg_id
    sess.stream_last_rendered = ""
    sess.stream_dirty = True
    sess.scope_kind = "dm" if (chat_id or 1) > 0 else "group"
    sess.scope_chat_id = chat_id
    sess.busy_started_at = 0.0
    sess.last_tool_edit_at = 0.0
    sess.record_tool(f"Bash: {label}", True)
    return sess


def _ext_bot(bot, telegram, limiter, chat_id):
    """Give the bot's ``_app.bot`` the one ExtBot behaviour that matters
    here: every call goes through the limiter before it goes out."""
    async def send_chat_action(*a, rate_limit_args=None, **kw):
        async def _call():
            telegram.admit("sendChatAction", kw.get("chat_id", chat_id))

        return await limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint="sendChatAction",
            data={"chat_id": kw.get("chat_id", chat_id)},
            rate_limit_args=rate_limit_args)

    bot._app.bot.send_chat_action = send_chat_action
    return bot


def _gaps(stamps: list[float]) -> list[float]:
    return [round(b - a, 6) for a, b in zip(stamps, stamps[1:])]


async def _stimulus(sessions, every=0.1):
    """A session that is actually working: a new tool row every 0.1 s, so
    the card has something new to show on every tick. Without it the card
    text only changes when its elapsed-seconds display does, and the
    observed edit rate says more about the fixture than about the
    cadence."""
    n = 0
    while True:
        await asyncio.sleep(every)
        n += 1
        for sess in sessions:
            sess.record_tool(f"Bash: step {n}", True)
            sess.stream_dirty = True


def _run_cards(vloop, bot, sessions, seconds: float, *, at=None, busy=None,
               typing=True):
    """Animate ``sessions`` for ``seconds`` of SIMULATED time. ``at`` is an
    optional ``(when, fn)`` hook fired mid-run; ``busy`` names the sessions
    kept visibly working (default: the animated ones).

    Starts BOTH tasks a live busy card owns, exactly as
    ``_start_animation`` does in the daemon: the card animator and the
    "typing…" refresher (roadmap 8.24). They are independent tasks with
    independent clocks, which is the property most of the rows below are
    about — so a harness that started only the animator would assert the
    cadence of a daemon that does not exist. ``typing=False`` is for the
    rows that deliberately measure the card alone.
    """
    async def main():
        tasks = [asyncio.ensure_future(bot._animate_busy(s)) for s in sessions]
        if typing:
            tasks += [asyncio.ensure_future(bot._animate_typing(s))
                      for s in sessions]
        tasks.append(asyncio.ensure_future(
            _stimulus(sessions if busy is None else busy)))
        if at is not None:
            when, fn = at

            async def _later():
                await asyncio.sleep(when)
                out = fn()
                if inspect.isawaitable(out):
                    await out

            tasks.append(asyncio.ensure_future(_later()))
        await asyncio.sleep(seconds)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    vloop.run_until_complete(main())


def _card_calls(telegram, chat_id, message_id=None):
    """The stamps of the card edits that went out — for one card when
    ``message_id`` is given, for the whole chat otherwise."""
    return [t for endpoint, chat, mid, t in telegram.calls
            if chat == chat_id and endpoint == "editMessageText"
            and (message_id is None or mid == message_id)]


def _most_in_window(stamps: list[float], window: float) -> int:
    return max((sum(1 for t in stamps if s <= t < s + window) for s in stamps),
               default=0)


# ── row A: one session, one private chat ─────────────────────────────────────

def test_a_single_streaming_card_edits_once_per_computed_interval(
    mk_bot, vloop, telegram, limiter,
):
    """Row A / R4: ``max(1.2, 1×1.0) × 1.1 = 1.32`` s between card edits.
    Mutation: drop the margin or the floor and the gap stops matching
    ``card_interval`` — the one function the rule is written in."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    sess = _card(bot, "a", 10, PRIVATE)
    _run_cards(vloop, bot, [sess], 8.0)
    expected = card_interval(base=an.STREAM_EDIT_INTERVAL, busy_sessions=1,
                             is_group=False)
    assert _gaps(_card_calls(telegram, PRIVATE)) == \
        [pytest.approx(expected)] * len(_gaps(_card_calls(telegram, PRIVATE)))


def test_a_single_card_keeps_its_chat_under_one_call_per_second(
    mk_bot, vloop, telegram, limiter,
):
    """Row A / R1, counting every call the card makes that COSTS the chat
    a token. Since 8.24 the typing indicator is not one of them (§12: the
    real API answered 200 on it throughout a `retry_after` window that
    refused every edit), so it is excluded here the same way Telegram
    excludes it — while the total, bubble included, is asserted right
    below so the exemption cannot hide an unbounded call rate.

    Mutation: budget the card edits per session instead of per chat, or
    drop the margin, and the metered rate breaks the ceiling."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    sess = _card(bot, "a", 10, PRIVATE)
    _run_cards(vloop, bot, [sess], 12.0)
    assert rm.get_rate_limiter() is limiter, "a pacing row needs a limiter"
    assert _most_in_window(telegram.metered_stamps_for(PRIVATE), 1.0) <= 3
    # The whole truth, off-budget calls included: one card edit every
    # 1.32 s and one bubble refresh every 4.5 s cannot put more than two
    # calls of any kind into one second.
    assert _most_in_window(telegram.stamps_for(PRIVATE), 1.0) <= 2


# ── row B1: two sessions sharing one private chat ────────────────────────────

def test_two_sessions_in_one_private_chat_each_slow_to_two_point_two_seconds(
    mk_bot, vloop, telegram, limiter,
):
    """Row B1 / R4 — the incident itself: two cards at 0.9 s put 2.2
    edits/s into one DM. Mutation: compute the interval from the session
    instead of from the chat's BUSY count and this returns to 1.32.

    Asserted on EVERY gap, not just the smallest. A floor
    (``min(gaps) == 2.2``) is what this row used to assert, and it stayed
    green through iteration 1 while the real gaps ran 2.2 s / 6.6 s — the
    cards were being refused by the budget half the time and the
    assertion could not see it. With no other traffic in the chat the
    cadence rule, not the limiter's reserve, must be what paces a card:
    mean within 10% of the interval, and no single gap past twice it."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    cards = [_card(bot, "a", 10, PRIVATE),
             _card(bot, "b", 11, PRIVATE)]
    _run_cards(vloop, bot, cards, 20.0)
    interval = card_interval(base=an.STREAM_EDIT_INTERVAL, busy_sessions=2,
                             is_group=False)
    for mid in (10, 11):
        gaps = _gaps(_card_calls(telegram, PRIVATE, mid))
        assert gaps, f"card {mid} never edited twice"
        assert min(gaps) >= interval - 1e-3, \
            "a card edited faster than the two-session interval"
        assert statistics.mean(gaps) <= 1.1 * interval, (mid, gaps)
        assert max(gaps) <= 2 * interval, (mid, gaps)


def test_two_sessions_in_one_private_chat_draw_no_429_from_telegram(
    mk_bot, vloop, telegram, limiter,
):
    """Row B1 / R1 in its strongest form: a fake Telegram that answers 429
    whenever the daemon exceeds 1 call/s (burst 3) into a chat never has
    to. Mutation: raise ``STREAM_EDIT_INTERVAL`` back to 0.9 without the
    N term, and the fake starts refusing."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    cards = [_card(bot, "a", 10, PRIVATE),
             _card(bot, "b", 11, PRIVATE)]
    _run_cards(vloop, bot, cards, 30.0)
    assert telegram.violations == []


def test_three_sessions_in_one_chat_stay_inside_the_chats_budget(
    mk_bot, vloop, telegram, limiter,
):
    """Row B1 widened: the rule has to hold for any N, not just two — and
    every card must actually GET its interval, not merely stay legal.
    Mutation: cap N at two and three sessions overrun the chat again; make
    the cards contend for tokens instead of pacing themselves and the mean
    gap runs past the interval while `violations` stays empty."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    cards = [_card(bot, name, 10 + i, PRIVATE)
             for i, name in enumerate(("a", "b", "c"))]
    _run_cards(vloop, bot, cards, 30.0)
    assert telegram.violations == []
    interval = card_interval(base=an.STREAM_EDIT_INTERVAL, busy_sessions=3,
                             is_group=False)
    for mid in (10, 11, 12):
        gaps = _gaps(_card_calls(telegram, PRIVATE, mid))
        assert gaps, f"card {mid} never edited twice"
        assert statistics.mean(gaps) <= 1.1 * interval, (mid, gaps)
        assert max(gaps) <= 2 * interval, (mid, gaps)


# ── row B2: N changes while the cards run ────────────────────────────────────

def test_a_sibling_going_idle_speeds_every_other_card_up_on_its_next_tick(
    mk_bot, vloop, telegram, limiter,
):
    """Row B2 / R4: three BUSY sessions tick at 3.3 s; the moment one goes
    IDLE the survivors' NEXT tick is 2.2 s later, with no rescheduling.
    Mutation: cache N per session at loop start and the card keeps the
    stale interval for the rest of the turn."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    cards = [_card(bot, name, 10 + i, PRIVATE)
             for i, name in enumerate(("a", "b", "c"))]

    def _retire():
        cards[2].status = Status.IDLE

    _run_cards(vloop, bot, [cards[0]], 14.0, at=(6.0, _retire))
    gaps = _gaps(_card_calls(telegram, PRIVATE))
    assert gaps[0] == pytest.approx(3.3)
    assert gaps[-1] == pytest.approx(2.2)


# ── row B3: a group chat ─────────────────────────────────────────────────────

def test_a_card_in_a_group_chat_ticks_on_the_group_floor(
    mk_bot, vloop, telegram, limiter,
):
    """Row B3 / R4: the group floor is 3.0 s per BUSY session, so one
    session ticks every 3.3 s — Telegram's group limit is 20/minute.
    Mutation: use the private floor everywhere and a group card sends 54
    edits a minute."""
    bot = _ext_bot(mk_bot(), telegram, limiter, GROUP)
    sess = _card(bot, "g", 10, GROUP)
    _run_cards(vloop, bot, [sess], 14.0)
    gaps = _gaps(_card_calls(telegram, GROUP))
    assert gaps == [pytest.approx(3.3)] * len(gaps)


def test_a_group_card_stays_under_twenty_calls_a_minute(
    mk_bot, vloop, telegram, limiter,
):
    """Row B3 / R1's group clause, counted over a full simulated minute.
    Mutation: drop the group floor and the card alone spends the whole
    group budget.

    Metered calls only, since 8.24: the group's 20-a-minute window is a
    MESSAGE limit, and the chat action is not in it (§12). The bubble
    refreshes ~13 times a minute here, which is precisely why 8.21
    concluded the indicator cost "the other half of the minute" — it
    would, if Telegram counted it."""
    bot = _ext_bot(mk_bot(), telegram, limiter, GROUP)
    sess = _card(bot, "g", 10, GROUP)
    _run_cards(vloop, bot, [sess], 61.0)
    assert _most_in_window(telegram.metered_stamps_for(GROUP), 60.0) <= 20


def test_two_cards_in_one_group_stay_inside_the_groups_minute(
    mk_bot, vloop, telegram, limiter,
):
    """Row B3 / R1's group clause where it is tightest: two BUSY sessions
    share the 20-a-minute window, each at ``max(1.2, 2×3.0) × 1.1 = 6.6``
    s, and the whole minute must still fit. The one-session row cannot see
    an N-blind group floor. Mutation: compute the group interval per
    session instead of per chat and the two cards ask for 36 calls a
    minute — the window then refuses a third of them."""
    bot = _ext_bot(mk_bot(), telegram, limiter, GROUP)
    cards = [_card(bot, "g1", 10, GROUP), _card(bot, "g2", 11, GROUP)]
    _run_cards(vloop, bot, cards, 61.0)
    assert telegram.violations == []
    assert _most_in_window(telegram.metered_stamps_for(GROUP), 60.0) <= 20
    assert limiter.snapshot()["chats"][0]["skipped"] == 0


def test_the_strict_telegram_does_refuse_a_daemon_that_paces_itself_badly(
    mk_bot, vloop, telegram, monkeypatch,
):
    """Anti-vacuity, and the reason every ``violations == []`` row above is
    worth anything: the fake Telegram must be ABLE to answer 429. This is
    the incident's own configuration — the blind 0.9 s cadence with
    nothing metering the chat (``get_rate_limiter() is None``, the state
    the autouse fixture leaves behind) — and it must be refused.
    Mutation: make ``_StrictTelegram.admit`` always return True and this
    is the only test in the file that notices."""
    monkeypatch.setattr(an, "card_interval", lambda **kw: 0.9)
    bot = mk_bot()
    cards = [_card(bot, "a", 10, PRIVATE), _card(bot, "b", 11, PRIVATE)]
    assert rm.get_rate_limiter() is None, "this row is the UNPACED state"
    _run_cards(vloop, bot, cards, 20.0)
    assert telegram.violations != []


# ── row B4: a legacy session counts too ──────────────────────────────────────

def test_a_legacy_session_without_a_stamped_chat_still_counts_towards_n(
    mk_bot, vloop, telegram, limiter,
):
    """Row B4 / R4 / D9: a session from before per-scope stamping has
    ``scope_chat_id == 0`` and resolves to the configured chat. Mutation:
    count N with ``all_sessions(chat_id)`` (which treats 0 as "every
    scope") or filter on the raw field, and the two cards in this one DM
    each think they are alone."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    stamped = _card(bot, "a", 10, PRIVATE)
    legacy = _card(bot, "old", 11, PRIVATE)
    legacy.scope_chat_id = 0
    _run_cards(vloop, bot, [stamped], 10.0)
    gaps = _gaps(_card_calls(telegram, PRIVATE))
    assert gaps == [pytest.approx(2.2)] * len(gaps)


def test_a_legacy_session_never_creates_a_phantom_chat_zero(
    mk_bot, vloop, telegram, limiter,
):
    """Row B4's second half: chat ``0`` is not a chat. Mutation: budget on
    the raw ``scope_chat_id`` and a phantom chat appears in the snapshot
    while the real chat goes unmetered."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    legacy = _card(bot, "old", 11, PRIVATE)
    legacy.scope_chat_id = 0
    _run_cards(vloop, bot, [legacy], 8.0)
    assert [c["chat_id"] for c in limiter.snapshot()["chats"]] == [PRIVATE]


# ── row M: the typing indicator ──────────────────────────────────────────────

def test_the_typing_indicator_holds_its_own_interval_while_a_card_is_live(
    mk_bot, vloop, telegram, limiter,
):
    """Row M, as amended by roadmap 8.24 — **the reverse of what this row
    asserted in 0.7.11** (``sendChatAction`` "sent NOT AT ALL while a busy
    card is live").

    ``sendChatAction`` fired once per loop wake in 0.7.10, once per
    interval after §11 U3, and not at all after §11's superseding note. It
    is back, because §12 measured the premise of that removal to be false:
    during a real ``retry_after=10`` window every edit into the chat was
    refused while all eleven typing calls returned 200.

    The contract now is exact: one refresh per ``TYPING_INDICATOR_INTERVAL``
    per session, on the indicator's own task, off the chat's budget — and
    therefore EVERY gap strictly under the 5 s Telegram gives a typing
    status, so the bubble never visibly drops. Review iteration 1 caught
    the first draft failing precisely here: offered from inside the card
    tick, the 4.5 s interval realized as ``ceil(4.5 / wake) * wake`` =
    5.28 s with one session and 6.6 s with two, above the expiry in every
    configuration.

    Mutation: send the indicator from ``_animate_tick`` again and the gaps
    become 5.28 s (the wake grid); drop the sleep and it floods; drop the
    send and this counts none.
    """
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    sess = _card(bot, "a", 10, PRIVATE)
    _run_cards(vloop, bot, [sess], 20.0)
    actions = telegram.actions_for(PRIVATE)
    assert len(actions) >= 4, [round(a - 1_000_000.0, 3) for a in actions]
    gaps = _gaps(actions)
    assert gaps == [pytest.approx(_config.TYPING_INDICATOR_INTERVAL)] * len(gaps)
    assert max(gaps) < 5.0, "the typing status lapsed between refreshes"
    assert _card_calls(telegram, PRIVATE), "the card stopped editing"


def test_a_slow_chat_action_does_not_stretch_the_refresh_interval(
    mk_bot, vloop, telegram, limiter,
):
    """The period is measured from the START of each refresh, so the round
    trip is spent INSIDE the interval rather than added to it. Telegram's
    5 s expiry leaves 0.5 s of headroom at the 4.5 s default; a single slow
    call would eat it and the bubble would blink.

    Mutation: ``await asyncio.sleep(TYPING_INDICATOR_INTERVAL)`` after the
    send instead of sleeping the remainder, and this row's gaps become
    4.5 + 0.8 = 5.3 s — past the expiry.
    """
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    real = bot._app.bot.send_chat_action

    async def slow(*a, **kw):
        await asyncio.sleep(0.8)      # simulated latency, on the virtual clock
        return await real(*a, **kw)

    bot._app.bot.send_chat_action = slow
    sess = _card(bot, "a", 10, PRIVATE)
    _run_cards(vloop, bot, [sess], 20.0)
    gaps = _gaps(telegram.actions_for(PRIVATE))
    assert gaps, "the bubble was never lit"
    assert gaps == [pytest.approx(_config.TYPING_INDICATOR_INTERVAL)] * len(gaps)


def test_every_budgeted_call_a_live_card_makes_is_a_card_edit(
    mk_bot, vloop, telegram, limiter,
):
    """Row M's other arm: the only call a live card makes that COSTS the
    chat anything is the edit itself. The typing indicator is the one
    permitted extra, and only because Telegram does not meter it (§12).
    Mutation: send anything else from the loop — a second edit shape, a
    stray sendMessage — and this names the endpoint."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    sess = _card(bot, "a", 10, PRIVATE)
    _run_cards(vloop, bot, [sess], 12.0)
    assert {endpoint for endpoint, _, _, _ in telegram.calls} == \
        {"editMessageText", "sendChatAction"}
    assert {endpoint for endpoint, _, _, _ in telegram.calls
            if endpoint not in telegram.EXEMPT} == {"editMessageText"}


def test_a_group_card_sends_the_typing_indicator_too(
    mk_bot, vloop, telegram, limiter,
):
    """Row M as amended, on the OTHER chat kind — the one where 8.21
    reckoned a typing bubble cost 18 of the group's 20 calls a minute. It
    costs none of them: the 20-a-minute window is a message limit. The DM
    row above cannot see a group-only regression, and 8.21's own
    intermediate design (§11 U3) was exactly a group-only suppression, so
    this row is the one that would catch its return.

    Mutation: gate ``_typing_chat`` on ``is_group_chat`` and this counts
    nothing. (That the action spends no slot of the group's rolling window
    is asserted directly on the limiter, in
    ``tests/test_typing_indicator.py::test_the_typing_action_never_spends_a_group_window_slot``
    — here it would only show up as a delay, which this row cannot see.)"""
    bot = _ext_bot(mk_bot(), telegram, limiter, GROUP)
    sess = _card(bot, "g", 10, GROUP)
    _run_cards(vloop, bot, [sess], 20.0)
    actions = telegram.actions_for(GROUP)
    assert actions, "no bubble in a group"
    assert telegram.violations == []
    gaps = _gaps(actions)
    assert gaps == [pytest.approx(_config.TYPING_INDICATOR_INTERVAL)] * len(gaps)
    assert _most_in_window(telegram.metered_stamps_for(GROUP), 60.0) <= 20


def test_the_bot_package_sends_the_chat_action_from_one_place_only(
    mk_bot, vloop, telegram, limiter,
):
    """Row M's third arm: every chat action in the package goes through
    ``animation._send_typing``, which is the only function holding the
    interval gate, the BUSY check and the mute check. A loop row can only
    see the branches it takes; this covers the ones it does not, card
    CREATION included. Swept statically, streamed line by line, since the
    suite runs under a 1 GiB address-space cap.

    (Until 8.24 this row asserted the package held NO call site at all —
    what 8.21 removed. The claim is the same: nothing sends the action
    except through the gate.)

    Mutation: add a second ``send_chat_action(`` call site anywhere in
    ``aipager/bot/`` and this names the file and line."""
    from pathlib import Path

    from aipager import bot as bot_pkg

    sites = []
    for path in sorted(Path(bot_pkg.__file__).parent.rglob("*.py")):
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                if line.lstrip().startswith("#"):
                    continue  # the comments that RECORD the history
                if "send_chat_action(" in line:
                    sites.append(f"{path.name}:{lineno}")
    assert len(sites) == 1 and sites[0].startswith("animation.py:"), sites


# ── CARD_RETRY_WAKE: a refused card comes back sooner, never faster ──────────

@pytest.mark.parametrize("sessions,chat", [
    (1, PRIVATE), (2, PRIVATE), (3, PRIVATE), (1, GROUP),
])
def test_no_card_ever_edits_faster_than_its_own_interval(
    mk_bot, vloop, telegram, limiter, sessions, chat,
):
    """``CARD_RETRY_WAKE = 1.0`` makes a REFUSED card come back in a
    second instead of a whole interval — it must never turn into a second
    way to edit. The in-phase three-session cluster is the case it was
    added for, so it is one of the parameters. Mutation: retry on the wake
    unconditionally (drop the "not before the interval" gate) and a
    contended card starts editing every 1 s."""
    bot = _ext_bot(mk_bot(), telegram, limiter, chat)
    cards = [_card(bot, f"s{i}", 10 + i, chat) for i in range(sessions)]
    _run_cards(vloop, bot, cards, 30.0)
    interval = card_interval(base=an.STREAM_EDIT_INTERVAL,
                             busy_sessions=sessions, is_group=chat < 0)
    for mid in range(10, 10 + sessions):
        gaps = _gaps(_card_calls(telegram, chat, mid))
        assert gaps, f"card {mid} never edited twice"
        assert min(gaps) >= interval - 1e-3, (mid, gaps)


def test_the_retry_wake_never_costs_the_chat_a_429(
    mk_bot, vloop, telegram, limiter,
):
    """R1 under the new wake: three in-phase cards in one DM retry every
    second while they are being refused, which is three times the wake
    traffic of iteration 1. A strict Telegram must still never answer 429.
    Mutation: let the retry bypass the skip acquire and the fake refuses."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    cards = [_card(bot, f"s{i}", 10 + i, PRIVATE) for i in range(3)]
    _run_cards(vloop, bot, cards, 40.0)
    assert telegram.violations == []


def test_a_refused_card_comes_back_in_a_second_not_in_an_interval(
    mk_bot, vloop, telegram, limiter,
):
    """The point of ``CARD_RETRY_WAKE``, in a GROUP where the interval
    (3.3 s) is far longer than the wake (1.0 s): a card whose first tick
    is refused must land its edit on a one-second retry, not wait out the
    whole interval. The chat's burst is spent just before the first tick,
    so that tick is certainly refused and a token is back 1 s later.
    Mutation: set ``CARD_RETRY_WAKE`` to the interval (0.7.10's behaviour)
    and the first edit slips past 5 s."""
    bot = _ext_bot(mk_bot(), telegram, limiter, GROUP)
    sess = _card(bot, "g", 10, GROUP)

    async def _spend():
        for _ in range(3):
            async def _call():
                telegram.admit("sendMessage", GROUP)
            await limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": GROUP}, rate_limit_args=None)

    # 2.9 s: one tenth of a second before the card's first tick, so the
    # tick is certainly refused and two tokens are back at 4.9 s. A card
    # retrying on the wake lands at 5.0; one retrying on its interval not
    # before 6.3.
    _run_cards(vloop, bot, [sess], 9.0, at=(2.9, _spend))
    edits = _card_calls(telegram, GROUP, 10)
    assert edits, "the card never edited at all"
    assert edits[0] - 1_000_000.0 < 5.5, [round(e - 1_000_000.0, 3) for e in edits]


# ── the acceptance table: what a minute of card traffic costs ────────────────

@pytest.mark.parametrize("sessions,chat,edits,actions,skipped,gap", [
    (1, GROUP, 18, 14, 0, 3.30),
    (1, PRIVATE, 46, 14, 0, 1.32),
    (2, PRIVATE, 56, 28, 0, 2.20),
    (3, PRIVATE, 56, 42, 1, 3.30),
])
def test_a_minute_of_card_traffic_costs_exactly_what_was_promised(
    mk_bot, vloop, telegram, limiter, sessions, chat, edits, actions,
    skipped, gap,
):
    """The operator-facing acceptance criterion, measured independently:
    over 61 simulated seconds the card loop makes exactly this many calls,
    with every gap exactly the promised interval and no 429 from a strict
    Telegram. Mutation: anything that changes the cadence, or that lets the
    budget refuse a card in an otherwise quiet chat, moves one of these
    numbers.

    ``edits``, ``skipped`` and ``gap`` are 8.21's OWN acceptance numbers,
    unchanged, reproduced here with the typing indicator switched back ON
    (roadmap 8.24) — that is the whole claim of 8.24 and this is where it
    is checked. ``actions`` is the new column: the bubble refreshes on the
    animator's own ≤4.5 s schedule and costs the chat's budget nothing
    (``admitted`` counts them, since the limiter counts every callback it
    runs, but ``skipped`` and the gaps do not move and ``violations`` stays
    empty). Mutation: route the action through the chat budget and the
    private rows lose edits to skips; await it in the tick instead of
    spawning it and every gap grows.

    ``actions`` is ``sessions x 14``: the indicator's own task refreshes
    every 4.5 s from the moment the card is created, and 61 s holds
    fourteen of those (t=0, 4.5, … 58.5) per session, whatever the card's
    cadence happens to be. That independence is the point — review
    iteration 1 rejected the first draft, where the indicator rode the
    card's wake grid and the realized spacing became ``ceil(4.5/W)*W`` =
    5.28–6.6 s, past the 5 s expiry Telegram gives a typing status.
    """
    bot = _ext_bot(mk_bot(), telegram, limiter, chat)
    cards = [_card(bot, f"s{i}", 10 + i, chat) for i in range(sessions)]
    _run_cards(vloop, bot, cards, 61.0)
    snap = limiter.snapshot()["chats"][0]
    observed = {
        "calls": len(telegram.calls),
        "edits": len([1 for e, _, _, _ in telegram.calls
                      if e == "editMessageText"]),
        "actions": len(telegram.actions_for(chat)),
        "other_endpoints": sorted({e for e, _, _, _ in telegram.calls}
                                  - {"editMessageText", "sendChatAction"}),
        "admitted": snap["calls"],
        "chat_actions": snap["chat_actions"],
        "skipped": snap["skipped"],
        "violations": telegram.violations,
        "gaps": sorted({round(g, 3) for mid in range(10, 10 + sessions)
                        for g in _gaps(_card_calls(telegram, chat, mid))}),
    }
    assert observed == {
        "calls": edits + actions, "edits": edits, "actions": actions,
        "other_endpoints": [],
        "admitted": edits + actions, "chat_actions": actions,
        "skipped": skipped, "violations": [],
        "gaps": [gap],
    }


# ── row C2: the starvation guard ─────────────────────────────────────────────

async def _chatter(limiter, telegram, chat_id, every=1.0):
    """Real content into the same chat, for ever: one blocking send per
    second, which is the whole of that chat's sustained budget. A skip
    acquire (which needs two tokens) can never win against it — only the
    starvation guard can."""
    while True:
        await asyncio.sleep(every)

        async def _call():
            telegram.admit("sendMessage", chat_id)

        await limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": chat_id}, rate_limit_args=None)


def _run_with_chatter(vloop, bot, sessions, limiter, telegram, seconds,
                      *, sample=False):
    samples: list[float] = []

    async def main():
        tasks = [asyncio.ensure_future(bot._animate_busy(s)) for s in sessions]
        tasks.append(asyncio.ensure_future(_stimulus(sessions)))
        tasks.append(asyncio.ensure_future(
            _chatter(limiter, telegram, PRIVATE)))
        if sample:
            async def _sample():
                while True:
                    await asyncio.sleep(0.05)
                    samples.append(sessions[0].card_skipped_since)
            tasks.append(asyncio.ensure_future(_sample()))
        await asyncio.sleep(seconds)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    vloop.run_until_complete(main())
    return samples


def test_a_card_starved_by_real_traffic_still_gets_its_turn(
    mk_bot, vloop, telegram, limiter,
):
    """Row C2 / R3: with a message a second going into the chat, the card's
    skip acquires can never find the two tokens they need — so without the
    starvation guard the spinner freezes for the whole turn. Mutation:
    delete the guard (or never stamp ``card_skipped_since``) and no card
    edit goes out at all."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    sess = _card(bot, "a", 10, PRIVATE)
    _run_with_chatter(vloop, bot, [sess], limiter, telegram, 12.0)
    assert _card_calls(telegram, PRIVATE, 10) != []


def test_a_starved_card_is_stamped_while_it_is_being_refused(
    mk_bot, vloop, telegram, limiter,
):
    """The ``card_skipped_since`` contract, first half: the stamp marks the
    START of the current run of refusals, and is what the guard measures
    2 × interval from. Mutation: never stamp it and the guard has no
    clock."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    sess = _card(bot, "a", 10, PRIVATE)
    samples = _run_with_chatter(vloop, bot, [sess], limiter, telegram, 12.0,
                                sample=True)
    assert max(samples) > 0.0, "the card was never marked as being refused"


def test_a_landed_card_edit_clears_the_skip_stamp(
    mk_bot, vloop, telegram, limiter,
):
    """The same contract's second half: a landed edit resets it to 0.0, so
    the NEXT run of refusals is measured from its own start. Mutation:
    leave it stamped and every later tick believes it is starving — the
    card blocks for ever after its first refusal."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    sess = _card(bot, "a", 10, PRIVATE)
    _run_cards(vloop, bot, [sess], 8.0)
    assert (sess.card_skipped_since, _card_calls(telegram, PRIVATE) != []) == \
        (0.0, True)


# ── row K: R1 does not depend on R4 ──────────────────────────────────────────

def test_the_limiter_alone_holds_the_line_when_the_cadence_rule_is_disabled(
    mk_bot, vloop, telegram, limiter, monkeypatch,
):
    """Row K — today's replay: two sessions in one DM at the old, blind
    0.9 s cadence, with the whole adaptive rule patched out. The budget
    alone must still keep the chat under 1 call/s, refusing what it cannot
    pace. Mutation: let skip callers through when the bucket is short and
    the fake Telegram starts answering 429."""
    monkeypatch.setattr(an, "card_interval", lambda **kw: 0.9)
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    cards = [_card(bot, "a", 10, PRIVATE), _card(bot, "b", 11, PRIVATE)]
    _run_cards(vloop, bot, cards, 30.0)
    assert telegram.violations == []


# ── row E4 through the loop: the backoff slows the card ──────────────────────

def test_a_backed_off_chat_slows_its_card_by_the_backoff_factor(
    mk_bot, vloop, telegram, limiter,
):
    """Row E4 / R4's ``× backoff`` term, observed end to end: after one
    small 429 the chat is at ×2, so the card's own gaps double from 1.32 s
    to 2.64 s. Asserted on EVERY gap: a ``min(gaps)`` floor is what let a
    2.2/6.6 s alternation hide in row B1 through iteration 1, and the same
    hiding place must not exist here. Mutation: read the backoff nowhere in
    the cadence and a chat Telegram just pushed back on keeps its old
    rhythm."""
    bot = _ext_bot(mk_bot(), telegram, limiter, PRIVATE)
    sess = _card(bot, "a", 10, PRIVATE)
    limiter.note_retry_after(PRIVATE, 1)
    _run_cards(vloop, bot, [sess], 14.0)
    gaps = _gaps(_card_calls(telegram, PRIVATE, 10))
    assert gaps and gaps == [pytest.approx(2.64)] * len(gaps)


# ── R9: the two env-configurable bases ───────────────────────────────────────

def test_the_two_card_bases_have_the_documented_defaults():
    """R9: ``STREAM_EDIT_INTERVAL`` rose from 0.9 to 1.2 and
    ``BUSY_EDIT_INTERVAL`` became configurable at 3.0. Mutation: leave the
    default at 0.9 and row B1's arithmetic quietly changes."""
    assert (an.STREAM_EDIT_INTERVAL, an.BUSY_EDIT_INTERVAL) == (1.2, 3.0)


def test_both_card_bases_are_read_from_the_environment(monkeypatch):
    """R9's "env-configurable" half, re-imported under a set environment
    and restored attribute by attribute (a second reload would bake the
    test's own isolation patches into the module for the rest of the
    session). Mutation: hardcode either constant and this fails."""
    saved = dict(vars(_config))
    try:
        monkeypatch.setenv("STREAM_EDIT_INTERVAL", "2.5")
        monkeypatch.setenv("BUSY_EDIT_INTERVAL", "7.5")
        reloaded = importlib.reload(_config)
        assert (reloaded.STREAM_EDIT_INTERVAL,
                reloaded.BUSY_EDIT_INTERVAL) == (2.5, 7.5)
    finally:
        for name in set(vars(_config)) - set(saved):
            delattr(_config, name)
        for name, value in saved.items():
            setattr(_config, name, value)
