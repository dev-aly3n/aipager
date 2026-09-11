"""Both HTTP paths under the 8.21 budget (rows E1, E2, F, I, L; rules R5,
R6, R7, R10).

Everything here is driven through the documented surface: PTB's path via
``transport._send_with_retry`` on a bot double that routes through
``BudgetRateLimiter.process_request`` exactly as ``ExtBot`` does, and the
rich path via the real ``rich_message._post`` behind an
``httpx.MockTransport``. No test sleeps for real and ``asyncio.sleep`` is
never patched through a module path — ``rich_message._sleep`` is the
module attribute the suite already owns.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from telegram.error import RetryAfter

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import BudgetRateLimiter, FloodSkipped
from aipager.bot.rich_message import (
    RichMessageFloodBanned,
    edit_message_text_rich,
    send_rich_message,
)
from aipager.bot.transport import MUTED, SKIPPED, _send_with_retry, edit_text_at

# Captured before the autouse ``_block_real_telegram_http`` replaces it, so
# the rich-path rows can drive the REAL transport through MockTransport.
_REAL_POST = rm._post

CHAT = 123456
BAN = 19289  # the design's row-F value


@pytest.fixture
def run_async():
    """Override the shared fixture so every loop is CLOSED — see
    ``tests/test_miniapp_webapp_sdk.py``. These tests leave httpx clients
    and limiter waiters behind and the suite has no address space to
    spare."""
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


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)
        await asyncio.sleep(0)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def limiter(clock):
    """Signal path passed explicitly — see the note on the same fixture in
    ``test_budget_rules.py``: without it, "no backoff file was written" is
    vacuously true on a machine with no daemon socket."""
    lim = BudgetRateLimiter(clock=clock, sleep=clock.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    yield lim
    lim.reset()


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setattr(rm, "_client", None)
    yield
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def no_sleep(monkeypatch):
    """Record every sleep the rich path asks for, without sleeping. Since
    8.21 the contract is that this list stays EMPTY on a 429."""
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(rm, "_sleep", _fake_sleep)
    return slept


class _LimitedBot:
    """A bot double shaped like PTB's ``ExtBot``: every method funnels its
    payload through the limiter before the call is made, which is the only
    reason the daemon's sends are paced at all."""

    def __init__(self, limiter, clock, outcomes):
        self._limiter, self._clock = limiter, clock
        self._outcomes = list(outcomes)
        self.sends: list[float] = []
        self.reactions: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        async def _call():
            self.sends.append(self._clock.now)
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return await self._limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": chat_id, "text": text},
            rate_limit_args=kw.pop("rate_limit_args", None),
        )

    async def set_message_reaction(self, chat_id, message_id, emoji):
        async def _call():
            self.reactions.append(emoji)

        return await self._limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint="setMessageReaction",
            data={"chat_id": chat_id}, rate_limit_args=None,
        )


def _mock_http(monkeypatch, handler):
    """Route the REAL ``_post`` through an httpx MockTransport, so the
    limiter inside it is exercised."""
    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(
        rm, "_client",
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _ok(message_id=1):
    return {"ok": True, "result": {"message_id": message_id}}


def _429(retry_after):
    return {"ok": False, "error_code": 429, "description": "Too Many Requests",
            "parameters": {"retry_after": retry_after}}


def _one_429_then_ok(clock, retry_after=5):
    """A fake Telegram that 429s the first POST and accepts the rest,
    recording the clock at each POST."""
    stamps: list[float] = []

    def handler(request):
        stamps.append(clock.now)
        if len(stamps) == 1:
            return httpx.Response(200, json=_429(retry_after))
        return httpx.Response(200, json=_ok(1))

    handler.stamps = stamps  # type: ignore[attr-defined]
    return handler


def _rich_call(which, chat_id=CHAT):
    if which == "send":
        return send_rich_message(chat_id, "an answer")
    return edit_message_text_rich(chat_id, 7, "a card frame")


# ── row E1: the PTB path ─────────────────────────────────────────────────────

def _e1(limiter, clock, run_async, retry_after=5):
    bot = _LimitedBot(limiter, clock, [RetryAfter(retry_after), "MSG"])
    result = run_async(asyncio.wait_for(
        _send_with_retry(bot, chat_id=CHAT, text="the answer"), timeout=10))
    return bot, result


def test_a_small_429_on_the_ptb_path_still_delivers_the_answer(
    limiter, clock, run_async,
):
    """Row E1 / R5: the 429 is a deferral, not a drop. Mutation: give up
    on the first ``RetryAfter`` (0.7.10's ``max_retries=0``) and the answer
    is lost."""
    _, result = _e1(limiter, clock, run_async)
    assert result == "MSG"


def test_no_call_reaches_the_chat_until_the_retry_after_has_elapsed(
    limiter, clock, run_async,
):
    """Row E1's core: zero calls for ``retry_after`` seconds, then exactly
    one. Mutation: retry immediately and the retry storm that caused the
    2026-09-10 ban is back."""
    bot, _ = _e1(limiter, clock, run_async)
    assert bot.sends[1] - bot.sends[0] >= 5.0


def test_a_small_429_on_the_ptb_path_doubles_the_chats_cadence(
    limiter, clock, run_async,
):
    """Row E1 / R5: the card must slow down for a chat Telegram just
    pushed back on. Mutation: account the 429 only on the rich path and a
    PTB 429 changes nothing."""
    _e1(limiter, clock, run_async)
    assert limiter.cadence_multiplier(CHAT) == 2.0


def test_a_small_429_on_the_ptb_path_logs_one_warning_without_a_traceback(
    limiter, clock, run_async, caplog,
):
    """Row E1 / R5: 0.7.10 logged ``AIORateLimiter … after maximum of 0
    retries`` WITH a traceback, once per tick. Mutation: log with
    ``exception()`` and ``exc_info`` comes back."""
    with caplog.at_level("WARNING", logger="aipager.bot.flood_budget"):
        _e1(limiter, clock, run_async)
    records = [r for r in caplog.records
               if r.name == "aipager.bot.flood_budget"]
    assert len(records) == 1 and records[0].exc_info is None


def test_a_small_429_on_the_ptb_path_never_mutes_the_chat(
    limiter, clock, run_async,
):
    """Row E1 / R5 against R6: only a ban-sized retry_after mutes.
    Mutation: mute on every 429 and one rate limit silences the chat for
    hours."""
    _e1(limiter, clock, run_async)
    assert not MUTE.is_muted(CHAT)


def test_the_transport_never_sleeps_out_a_small_retry_after_itself(
    limiter, clock, run_async,
):
    """Row E1's last clause: ``transport._send_with_retry`` slept
    ``min(retry_after, 30)`` in 0.7.10, blocking the whole daemon task.
    The only waiting allowed now is the limiter's, on the INJECTED clock —
    so this whole scenario must cost no real time at all. Mutation:
    restore the private ``await asyncio.sleep(retry_after)`` and this
    takes 5 real seconds."""
    started = time.monotonic()
    _e1(limiter, clock, run_async, retry_after=5)
    assert time.monotonic() - started < 1.0


def test_a_ban_sized_retry_after_on_the_ptb_path_still_mutes_and_reacts(
    limiter, clock, run_async,
):
    """Row F / R6: 0.7.10's give-up branch is untouched — one attempt, the
    mute armed, the 🚨 reaction still delivered. Mutation: route the ban
    into the backoff and the chat keeps being hammered for eight hours."""
    bot = _LimitedBot(limiter, clock, [RetryAfter(BAN)])
    with pytest.raises(RetryAfter):
        run_async(asyncio.wait_for(
            _send_with_retry(bot, chat_id=CHAT, text="hi",
                             reply_to_message_id=3), timeout=10))
    assert (len(bot.sends), bot.reactions, MUTE.is_muted(CHAT)) == \
        (1, ["🚨"], True)


def test_a_ban_is_not_also_counted_as_a_backoff(limiter, clock, run_async):
    """Row F / R5's boundary: the mute owns a ban, the budget must not
    double-count it. Mutation: call ``note_retry_after`` unconditionally
    and a lifted ban leaves the chat at ×2 with a stale signal file."""
    bot = _LimitedBot(limiter, clock, [RetryAfter(BAN)])
    with pytest.raises(RetryAfter):
        run_async(asyncio.wait_for(
            _send_with_retry(bot, chat_id=CHAT, text="hi"), timeout=10))
    assert limiter.cadence_multiplier(CHAT) == 1.0
    assert not Path(config.FLOOD_BACKOFF_FILE).exists()


def test_a_muted_chat_still_costs_zero_attempts_under_the_new_limiter(
    limiter, clock, run_async,
):
    """Row F / R6: the 8.17 mute check runs before the budget, so a muted
    chat never even reaches the limiter. Mutation: acquire first and every
    muted send burns a token."""
    MUTE.mute(CHAT, BAN)
    bot = _LimitedBot(limiter, clock, ["MSG"])
    with pytest.raises(FloodMuted):
        run_async(asyncio.wait_for(
            _send_with_retry(bot, chat_id=CHAT, text="hi"), timeout=10))
    assert bot.sends == []


# ── row E2: the rich path ────────────────────────────────────────────────────

@pytest.mark.parametrize("which", ["send", "edit"])
def test_the_rich_path_never_sleeps_out_a_small_429_itself(
    which, limiter, clock, run_async, monkeypatch, no_sleep,
):
    """Row E2 — the busy card's own path. 0.7.10 handled the JSON 429
    privately with ``min(retry_after, 30)`` + ``await _sleep(...)`` + a
    re-POST, in two places. Mutation: restore either clamp-sleep-retry and
    ``no_sleep`` records a 5."""
    rm.set_rate_limiter(limiter)
    _mock_http(monkeypatch, _one_429_then_ok(clock))
    result = run_async(asyncio.wait_for(_rich_call(which), timeout=10))
    assert (result, no_sleep) == ({"message_id": 1}, [])


@pytest.mark.parametrize("which", ["send", "edit"])
def test_the_rich_paths_second_post_waits_out_the_deferral_on_the_limiter(
    which, limiter, clock, run_async, monkeypatch, no_sleep,
):
    """Row E2 / R5: exactly one POST before the deferral and one after —
    and the gap is the limiter's, measured on the injected clock.
    Mutation: re-POST immediately and the gap collapses to zero."""
    rm.set_rate_limiter(limiter)
    handler = _one_429_then_ok(clock)
    _mock_http(monkeypatch, handler)
    run_async(asyncio.wait_for(_rich_call(which), timeout=10))
    assert len(handler.stamps) == 2
    assert handler.stamps[1] - handler.stamps[0] >= 5.0


@pytest.mark.parametrize("which", ["send", "edit"])
def test_a_rich_path_429_is_accounted_on_the_very_same_limiter(
    which, limiter, clock, run_async, monkeypatch, no_sleep,
):
    """Row E2 / R5: identical accounting from either path — one budget, one
    backoff. Mutation: keep the rich path's private bookkeeping and the
    card never slows down."""
    rm.set_rate_limiter(limiter)
    _mock_http(monkeypatch, _one_429_then_ok(clock))
    run_async(asyncio.wait_for(_rich_call(which), timeout=10))
    assert limiter.cadence_multiplier(CHAT) == 2.0
    assert not MUTE.is_muted(CHAT)


def test_a_ban_on_the_rich_path_is_one_post_a_mute_and_no_backoff(
    limiter, clock, run_async, monkeypatch, no_sleep,
):
    """Row F on the rich path: 0.7.10 behaviour exactly. Mutation: send
    the ban through ``note_retry_after`` and the chat both mutes AND
    slows."""
    rm.set_rate_limiter(limiter)
    posts: list[float] = []

    def handler(request):
        posts.append(clock.now)
        return httpx.Response(200, json=_429(BAN))

    _mock_http(monkeypatch, handler)
    with pytest.raises(RichMessageFloodBanned):
        run_async(asyncio.wait_for(send_rich_message(CHAT, "x"), timeout=10))
    assert (len(posts), MUTE.is_muted(CHAT),
            limiter.cadence_multiplier(CHAT)) == (1, True, 1.0)


def test_a_skippable_rich_card_edit_raises_flood_skipped_when_short(
    limiter, clock, run_async, monkeypatch, no_sleep,
):
    """R3 on the rich path: ``kind="skip"`` must surface as
    ``FloodSkipped``, never be swallowed into ``None`` (which the caller
    reads as "permanent failure, stop the animation"). Mutation: swallow
    it and the card loop dies whenever the chat is busy."""
    rm.set_rate_limiter(limiter)
    posts: list[float] = []

    def handler(request):
        posts.append(clock.now)
        return httpx.Response(200, json=_ok(1))

    _mock_http(monkeypatch, handler)

    async def scenario():
        for _ in range(3):  # drain the chat's burst through the same path
            await send_rich_message(CHAT, "answer")
        await edit_message_text_rich(CHAT, 7, "card", kind="skip")

    with pytest.raises(FloodSkipped):
        run_async(asyncio.wait_for(scenario(), timeout=10))
    assert len(posts) == 3


# ── row I: the pinned dashboard through the transport seam ───────────────────

def test_a_skipped_dashboard_refresh_returns_the_skipped_sentinel(
    limiter, clock, run_async,
):
    """Row I / R3: nothing went out and the chat is healthy — so the
    caller must be able to tell this apart from a mute and from success.
    Mutation: return ``True`` (PTB's only safe sentinel) and
    ``dashboard.py`` records a refresh that never happened."""
    tg = MagicMock()

    async def _edit(*a, **kw):
        async def _call():
            return "EDITED"
        return await limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint="editMessageText",
            data={"chat_id": kw.get("chat_id", CHAT)},
            rate_limit_args=kw.pop("rate_limit_args", None),
        )

    tg.edit_message_text = _edit

    async def scenario():
        for _ in range(3):
            await _edit(chat_id=CHAT)
        return await edit_text_at(tg, chat_id=CHAT, message_id=1, text="t",
                                  rate_limit_args={"kind": "skip"})

    assert run_async(asyncio.wait_for(scenario(), timeout=10)) is SKIPPED


def test_the_skipped_sentinel_is_falsy_and_distinct_from_muted():
    """Row I's contract shape: ``SKIPPED`` must be usable in the same
    ``if not sent:`` guards as ``MUTED`` and must never be mistaken for
    it. Mutation: make ``SKIPPED`` truthy and every caller records a
    refused edit as delivered."""
    assert (bool(SKIPPED), repr(SKIPPED), SKIPPED is MUTED) == \
        (False, "SKIPPED", False)


def test_the_refresh_the_skip_refused_lands_on_the_next_trigger(
    limiter, clock, run_async,
):
    """Row I's second half: a skip is "try again on the next trigger", not
    a drop. Mutation: latch the refusal and the pinned dashboard never
    updates again."""
    tg = MagicMock()

    async def _edit(*a, **kw):
        async def _call():
            return "EDITED"
        return await limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint="editMessageText",
            data={"chat_id": CHAT},
            rate_limit_args=kw.pop("rate_limit_args", None),
        )

    tg.edit_message_text = _edit

    async def scenario():
        for _ in range(3):
            await _edit(chat_id=CHAT)
        await edit_text_at(tg, chat_id=CHAT, message_id=1, text="t",
                           rate_limit_args={"kind": "skip"})
        clock.now += 3.0  # the chat's bucket refills
        return await edit_text_at(tg, chat_id=CHAT, message_id=1, text="t",
                                  rate_limit_args={"kind": "skip"})

    assert run_async(asyncio.wait_for(scenario(), timeout=10)) == "EDITED"


# ── row L: one limiter, both paths ───────────────────────────────────────────

def test_the_builders_limiter_is_the_object_the_rich_path_paces_itself_with(
    mk_bot, monkeypatch,
):
    """Row L / R10, the identity assert 8.17 already encoded. Mutation:
    construct a second limiter for the rich path and each path gets its
    own half of the budget — which is how the daemon out-sent its own
    limit in the first place."""
    from aipager.bot import lifecycle as lc

    seen = {}

    class _RecordingBuilder:
        def __getattr__(self, name):
            def _chain(*args, **kwargs):
                if name == "rate_limiter":
                    seen["limiter"] = args[0] if args else None
                return self
            return _chain

    monkeypatch.setattr(lc, "ApplicationBuilder", _RecordingBuilder)
    mk_bot()._make_builder()

    assert isinstance(seen["limiter"], BudgetRateLimiter)
    assert rm.get_rate_limiter() is seen["limiter"]


def test_an_unpaced_daemon_reports_no_limiter_at_all():
    """The state every test starts in (the autouse fixture nulls it), and
    the reason a pacing assertion must check for a limiter first.
    Mutation: return a fresh limiter from ``get_rate_limiter()`` and a
    test that forgot to install one passes vacuously."""
    assert rm.get_rate_limiter() is None
