"""Priority classes: an answer never waits behind a busy card (row L, R3).

The inversion this ends, measured 2026-09-15: the busy card is ~95 % of a
chat's outbound volume and the answer ~5 %, and until 0.7.12 they competed
for the same tokens on equal terms once a card escalated from skip-kind to
blocking. Under pressure the daemon therefore shed exactly the wrong thing
— five answers were dropped in one day while the cards kept animating.

Three classes, declared through the ``rate_limit_args`` dict both callers
already have. ESSENTIAL is the DEFAULT (D-9): a call nobody classified is
never dropped, so the 66-site inventory needed no hand-edits and a
forgotten classification degrades safely.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager.bot.flood_budget import (
    PRIORITY_ESSENTIAL,
    PRIORITY_ORNAMENT,
    PRIORITY_SIGNAL,
    FloodSkipped,
    _class_of,
    _SKIP_RESERVE,
    rate_limit_args,
)

CHAT = 256113222


# ── the declaration itself ───────────────────────────────────────────────────

def test_an_unclassified_call_is_essential():
    """D-9, and the single most important default in this ship. Invert it
    and every one of the 66 outbound sites nobody has reviewed becomes
    droppable — "silently dropped" instead of "never dropped"."""
    assert _class_of(None) == PRIORITY_ESSENTIAL
    assert _class_of({}) == PRIORITY_ESSENTIAL
    assert _class_of({"kind": "skip"}) == PRIORITY_ESSENTIAL
    assert _class_of(3) == PRIORITY_ESSENTIAL          # PTB's retry count
    assert _class_of({"class": "typo"}) == PRIORITY_ESSENTIAL


@pytest.mark.parametrize("priority", [PRIORITY_ORNAMENT, PRIORITY_SIGNAL])
def test_a_declared_class_is_read_back(priority):
    assert _class_of({"class": priority}) == priority
    assert _class_of(rate_limit_args(priority=priority)) == priority


def test_a_blocking_essential_call_builds_none_not_an_empty_dict():
    """PTB drops a FALSY ``rate_limit_args`` before the limiter sees it
    (``_extbot.py:335``), so ``{}`` and ``None`` are indistinguishable
    downstream — and only ``None`` says so honestly. Mutation: return
    ``{}`` and nothing breaks here, which is why the assertion is on the
    identity rather than on behaviour."""
    assert rate_limit_args() is None
    assert rate_limit_args(kind="blocking", priority=PRIORITY_ESSENTIAL) is None
    assert rate_limit_args(kind="skip") == {"kind": "skip"}
    assert rate_limit_args(priority=PRIORITY_ORNAMENT) == {"class": "ornament"}
    assert rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT) == {
        "kind": "skip", "class": "ornament"}


# ── row L: the reserve, on BOTH acquire paths ────────────────────────────────

def _drain_to(limiter, chat, tokens: float) -> None:
    """Leave exactly *tokens* in the chat's bucket."""
    budget = limiter._budget_for(chat)
    budget.chat._refill(limiter._clock())
    budget.chat._tokens = tokens


def test_a_skip_kind_ornament_is_refused_when_only_the_reserve_is_left(
    limiter, flood_clock, run_async,
):
    """Row L, the skip path — unchanged since 8.21 and pinned here so the
    blocking half below is a comparison rather than an assertion in a
    vacuum."""
    async def _callback():
        return "sent"

    _drain_to(limiter, CHAT, 1.0)
    with pytest.raises(FloodSkipped):
        run_async(limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint="editMessageText",
            data={"chat_id": CHAT},
            rate_limit_args=rate_limit_args(kind="skip",
                                            priority=PRIORITY_ORNAMENT),
        ))


def test_an_essential_takes_the_last_token_immediately(
    limiter, flood_clock, run_async,
):
    """Row L, first half: with one token left an ESSENTIAL does not wait.
    Mutation: give ESSENTIAL a reserve too and the answer starts queueing
    behind the very cards it is supposed to outrank."""
    async def _callback():
        return "sent"

    _drain_to(limiter, CHAT, 1.0)
    before = flood_clock.now
    assert run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
        data={"chat_id": CHAT}, rate_limit_args=None,
    )) == "sent"
    assert flood_clock.now == before, "an ESSENTIAL waited for a token it had"


def test_a_blocking_ornament_waits_for_the_reserve_it_must_leave_behind(
    limiter, flood_clock, run_async,
):
    """Row L, the half that did not exist before 8.26.

    ``_edit_busy_raw`` escalates from ``kind="skip"`` to ``kind="blocking"``
    after two refusals so a card is slow but never frozen. On the blocking
    path the reserve used not to apply at all, so that escalated card edit
    could take the last token an answer was about to need. Now an ORNAMENT
    waits until the chat has ``1 + _SKIP_RESERVE`` — it is delayed, never
    dropped.

    Mutation: drop the ``reserve=`` argument from ``_acquire_blocking``'s
    ``chat.time_until`` call and this returns at once with no wait.
    """
    async def _callback():
        return "sent"

    _drain_to(limiter, CHAT, 1.0)
    before = flood_clock.now
    run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint="editMessageText",
        data={"chat_id": CHAT},
        rate_limit_args=rate_limit_args(priority=PRIORITY_ORNAMENT),
    ))
    waited = flood_clock.now - before
    assert waited > 0.0, "a blocking ORNAMENT took the reserve"
    # It had 1.0 and needs 1.0 + _SKIP_RESERVE, refilling at the chat rate.
    expected = _SKIP_RESERVE / limiter._budget_for(CHAT).chat.rate
    assert waited == pytest.approx(expected, rel=0.05)


class _ParkingClock:
    """A clock whose ``sleep`` genuinely PARKS until the test releases it.

    ``FloodClock`` advances itself on every sleep, which is right for
    measuring waits and useless for observing a caller WHILE it waits: the
    ornament's whole wait completes inside one loop iteration, so nothing
    is ever actually queued behind it. Here a sleeping caller stays
    sleeping, which is what a chat short of tokens really looks like.
    """

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.parked = asyncio.Event()
        self.sleeping = asyncio.Event()

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeping.set()
        await self.parked.wait()
        self.now += max(seconds, 0.0)

    def release(self) -> None:
        self.parked.set()


def test_an_essential_overtakes_an_ornament_that_is_already_waiting(
    run_async, flood_clock,
):
    """The property row L is really about, stated as a race: the ornament
    asks FIRST and parks, the answer asks second, and the ANSWER lands
    first.

    The reserve alone does not buy this. ``budget.waiters`` is a strict
    FIFO, so an ornament waiting out ``1 + _SKIP_RESERVE`` tokens would
    head-of-line block an answer that needs one and could have it now —
    the same inversion R3 exists to end, merely moved from the bucket into
    the queue. A non-ornament is therefore inserted ahead of every
    ORNAMENT ticket (and behind every non-ornament one, so FIFO still
    holds within a class). Nothing is dropped: the card is overtaken, not
    refused.

    Mutation: append every ticket unconditionally, as 8.21 did, and the
    answer never runs until the card's wait is released — ``order``
    becomes ``["card", "answer"]``.
    """
    from aipager import config
    from aipager.bot.flood_budget import BudgetRateLimiter

    clock = _ParkingClock()
    lim = BudgetRateLimiter(clock=clock, sleep=clock.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    order: list[str] = []

    async def _ornament():
        order.append("card")

    async def _essential():
        order.append("answer")

    _drain_to(lim, CHAT, 1.0)

    async def _race():
        card = asyncio.ensure_future(lim.process_request(
            callback=_ornament, args=(), kwargs={}, endpoint="editMessageText",
            data={"chat_id": CHAT},
            rate_limit_args=rate_limit_args(priority=PRIORITY_ORNAMENT)))
        # Let the card reach its wait and PARK there, holding the queue.
        await asyncio.wait_for(clock.sleeping.wait(), timeout=5)

        answer = asyncio.ensure_future(lim.process_request(
            callback=_essential, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": CHAT}, rate_limit_args=None))
        # The answer must complete while the card is STILL parked.
        await asyncio.wait_for(answer, timeout=5)
        assert order == ["answer"], "the answer waited behind the card"

        clock.release()
        await asyncio.wait_for(card, timeout=5)

    try:
        run_async(_race())
    finally:
        lim.reset()
    assert order == ["answer", "card"], order


def test_a_signal_spends_no_chat_token_and_is_never_suspended(
    limiter, flood_clock, run_async,
):
    """R8 for the SIGNAL class: reactions stay exempt from the per-chat
    BUDGET (Telegram meters them elsewhere, measured 2026-09-12). The
    class is declarative — it keeps them out of minimal-mode suspension —
    and it buys them no exemption from the mute, which nothing has."""
    async def _callback():
        return "reacted"

    _drain_to(limiter, CHAT, 0.0)
    before = flood_clock.now
    assert run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint="setMessageReaction",
        data={"chat_id": CHAT},
        rate_limit_args=rate_limit_args(priority=PRIORITY_SIGNAL),
    )) == "reacted"
    assert flood_clock.now == before, "a SIGNAL waited on a chat token"


# ── the declarations actually reached the wire ───────────────────────────────

def test_the_busy_card_declares_itself_an_ornament(
    mk_bot, limiter, flood_clock, run_async, monkeypatch,
):
    """The class is only worth anything if the call sites pass it. Records
    what ``send_busy`` hands the limiter.

    Mutation: drop ``rate_limit_args=`` from ``send_busy``'s
    ``send_message`` and the card is silently promoted to ESSENTIAL —
    which is the priority inversion, back again and invisible.
    """
    seen: list[object] = []

    class _Recorder:
        async def send_message(self, *a, rate_limit_args=None, **kw):
            seen.append(rate_limit_args)
            return type("M", (), {"message_id": 7})()

    bot = mk_bot()
    bot._app.bot = _Recorder()
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = CHAT
    run_async(bot.send_busy(sess))
    assert seen == [{"class": "ornament"}]


def test_the_typing_bubble_declares_itself_an_ornament(
    mk_bot, limiter, flood_clock, run_async,
):
    """Mutation: drop the class here and the most disposable call this
    daemon makes stops being sheddable in minimal mode. AMENDED by 8.30 R2:
    it declares the skip kind as well — budgeted again, never waiting."""
    seen: list[object] = []

    class _Recorder:
        async def send_chat_action(self, *a, rate_limit_args=None, **kw):
            seen.append(rate_limit_args)

    bot = mk_bot()
    bot._app.bot = _Recorder()
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    from aipager.state import Status
    sess.status = Status.BUSY
    sess.scope_chat_id = CHAT
    run_async(bot._send_typing(sess, CHAT))
    assert seen == [{"kind": "skip", "class": "ornament"}]


def test_the_consumed_reaction_declares_itself_a_signal(
    mk_bot, limiter, flood_clock, run_async,
):
    """The 👍 that marks a queued message delivered. Mutation: drop the
    class and a chat in minimal mode stops acknowledging input."""
    seen: list[object] = []

    class _Recorder:
        async def set_message_reaction(self, *a, rate_limit_args=None, **kw):
            seen.append(rate_limit_args)

    bot = mk_bot()
    bot._app.bot = _Recorder()
    sess = bot.registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = CHAT
    run_async(bot._apply_consumption(sess, [{"msg_id": 5, "chat_id": CHAT}]))
    assert seen == [{"class": "signal"}]
