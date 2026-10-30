"""Iteration 2: Send runs in its own task after a synchronous single-flight
claim, so the tap's handler returns while Sentry is still being reached
and other updates keep flowing (design.md Success criterion 10, "the send
runs in a worker thread"; coordinator's iteration-2 rule "other updates
keep flowing during a send"; spec.md "Single-flight: a double tap sends
once"). An unexpected failure inside the sender ends the card with a
final line, never "try again later" (spec.md item 2 outcome lines).

Telegram delivers updates to aipager one at a time, so a handler that
waited for the network would hold up every other chat. The Driver here
awaits each handler in turn, just like that.

Methods: equivalence partitioning over what arrives during a send (a
group update, an owner DM update, a second and a third Send tap) and
over the send's ending (sent, an exception inside the sender); error
guessing: Sentry hanging, a sender bug, a tap racing the outcome."""

from __future__ import annotations

import asyncio
import contextlib
import threading

import httpx
import pytest

from aipager.report import wording

SENT_PREFIX = "Sent. Reference "
# "A handler returns while Sentry hangs" is checked without a wall clock (a
# loaded CI runner can stall a correct handler for seconds): it returns while
# Sentry still holds the send (FakeNet.released is 0), within MAX_TURNS turns
# of the event loop. A handler that waits for the send, even with a timeout,
# lets the loop turn thousands of times in 10 ms; one that does not wait
# takes a few turns however slow the runner is. The limit also trips on a
# tap that waits a millisecond or more for other work off the loop (a
# to_thread save, a paced sleep): move such work out of the tap's handler
# too. HANDLER_DEADLINE only stops a handler that waits from hanging the test.
HOLD = 30.0              # seconds
HANDLER_DEADLINE = 10.0  # seconds
MAX_TURNS = 100


async def _wait(pred, timeout=15.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end and not pred():
        await asyncio.sleep(0.01)
    return pred()


def _hanging(h, use_net):
    """Sentry accepts the connection and then hangs until the gate opens."""
    gate = threading.Event()
    return use_net(h.FakeNet(gate=gate, hold=HOLD)), gate


async def _loop_turns(coro):
    """Await *coro*; return its result and how many times the event loop
    turned meanwhile."""
    turns = 0

    async def _count():
        nonlocal turns
        while True:
            await asyncio.sleep(0)
            turns += 1
    counter = asyncio.create_task(_count())
    try:
        result = await coro
    finally:
        counter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await counter
    return result, turns


async def _tap_returns(d, fake, cid, mid, *, user=None) -> bool:
    """True when the Send tap's handlers finished while Sentry still held
    the send, without waiting for it (see MAX_TURNS)."""
    kw = {"user": user} if user is not None else {}
    try:
        _, turns = await _loop_turns(asyncio.wait_for(
            d.tap("_:rp:send", chat=cid, message_id=mid, settle_rounds=0,
                  wait_outcome=False, **kw),
            HANDLER_DEADLINE))
    except asyncio.TimeoutError:
        return False
    return fake.released == 0 and turns <= MAX_TURNS


def _in_flight(bot, h, d, fake, gate, during):
    """Open a card, tap Send, wait until Sentry holds the request, run
    *during(cid, mid, tap_returned)* while it hangs, then let it go and
    wait for the outcome. Returns whatever *during* returned."""
    async def go():
        cid, mid = await h.open_card(bot, d)
        try:
            returned = await _tap_returns(d, fake, cid, mid)
            assert await _wait(lambda: len(fake.posts) >= 1), "the send never reached Sentry"
            result = await during(cid, mid, returned)
        finally:
            gate.set()
        await h.wait_for_outcome(bot, cid, mid)
        return result
    return go()


# ---- the tap does not wait for the network --------------------------------------------

def test_send_tap_returns_while_sentry_hangs(bot, drive, h, run_async, use_net):
    fake, gate = _hanging(h, use_net)

    async def during(cid, mid, returned):
        return returned and not gate.is_set() and len(fake.posts) == 1
    assert run_async(_in_flight(bot, h, drive(bot), fake, gate, during)) is True


def test_group_update_is_answered_while_a_send_hangs(make_bot, drive, h, run_async, use_net):
    """Another chat's update, delivered after the Send tap, is served
    before Sentry answers."""
    bot = make_bot("scope")
    fake, gate = _hanging(h, use_net)
    d = drive(bot)

    async def during(cid, mid, returned):
        await asyncio.wait_for(d.command("/help", user=h.ADMIN, chat=h.GROUP), HANDLER_DEADLINE)
        return len(bot.tg.sent(h.GROUP)), fake.released
    # One answer, and Sentry still held the send when it came.
    assert run_async(_in_flight(bot, h, d, fake, gate, during)) == (1, 0)


def test_owner_dm_update_is_answered_while_a_send_hangs(bot, drive, h, run_async, use_net):
    fake, gate = _hanging(h, use_net)
    d = drive(bot)

    async def during(cid, mid, returned):
        before = len(bot.tg.sent(h.OWNER))
        await asyncio.wait_for(d.command("/help"), HANDLER_DEADLINE)
        return len(bot.tg.sent(h.OWNER)) - before, fake.released
    # One answer, and Sentry still held the send when it came.
    assert run_async(_in_flight(bot, h, d, fake, gate, during)) == (1, 0)


def test_card_says_sending_while_sentry_hangs(bot, drive, h, run_async, use_net):
    fake, gate = _hanging(h, use_net)

    async def during(cid, mid, returned):
        return bot.tg.text_of(cid, mid)
    assert "Sending..." in run_async(_in_flight(bot, h, drive(bot), fake, gate, during))


def test_card_has_no_send_button_while_sending(bot, drive, h, run_async, use_net):
    fake, gate = _hanging(h, use_net)

    async def during(cid, mid, returned):
        return [data for _, data in bot.tg.buttons_of(cid, mid)]
    assert "_:rp:send" not in run_async(_in_flight(bot, h, drive(bot), fake, gate, during))


def test_outcome_reaches_the_card_after_the_background_send(bot, drive, h, run_async,
                                                            use_net):
    fake, gate = _hanging(h, use_net)
    holder = {}

    async def during(cid, mid, returned):
        holder["card"] = (cid, mid)
    run_async(_in_flight(bot, h, drive(bot), fake, gate, during))
    assert SENT_PREFIX in bot.tg.text_of(*holder["card"])


def test_outcome_is_not_on_the_card_before_sentry_answers(bot, drive, h, run_async, use_net):
    """Negative control for the test above: the line is the send's."""
    fake, gate = _hanging(h, use_net)

    async def during(cid, mid, returned):
        return bot.tg.text_of(cid, mid)
    assert SENT_PREFIX not in run_async(_in_flight(bot, h, drive(bot), fake, gate, during))


# ---- double and triple taps while the background send runs ------------------------------

@pytest.mark.parametrize("extra_taps", [1, 2])
def test_taps_during_background_send_send_once(bot, drive, h, run_async, use_net, extra_taps):
    fake, gate = _hanging(h, use_net)
    d = drive(bot)

    async def during(cid, mid, returned):
        for _ in range(extra_taps):
            await _tap_returns(d, fake, cid, mid)
    run_async(_in_flight(bot, h, d, fake, gate, during))
    assert len(fake.posts) == 1


def test_second_tap_during_background_send_returns_at_once(bot, drive, h, run_async,
                                                          use_net):
    fake, gate = _hanging(h, use_net)
    d = drive(bot)

    async def during(cid, mid, returned):
        return await _tap_returns(d, fake, cid, mid)
    assert run_async(_in_flight(bot, h, d, fake, gate, during)) is True


def test_second_tap_during_background_send_says_already_sending(bot, drive, h, run_async,
                                                               use_net):
    fake, gate = _hanging(h, use_net)
    d = drive(bot)

    async def during(cid, mid, returned):
        await _tap_returns(d, fake, cid, mid)
        await h.settle()
    run_async(_in_flight(bot, h, d, fake, gate, during))
    assert "Already sending." in bot.tg.toast_texts()


def test_tap_after_background_send_finished_sends_nothing_more(bot, drive, h, run_async,
                                                              use_net):
    fake, gate = _hanging(h, use_net)
    d = drive(bot)
    holder = {}

    async def during(cid, mid, returned):
        holder["card"] = (cid, mid)

    async def go():
        await _in_flight(bot, h, d, fake, gate, during)
        cid, mid = holder["card"]
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert len(fake.posts) == 1


def test_stranger_tap_during_background_send_sends_nothing_more(make_bot, drive, h,
                                                               run_async, use_net):
    """Error guessing: a non-owner's tap in the middle of a send."""
    bot = make_bot("scope")
    fake, gate = _hanging(h, use_net)
    d = drive(bot)

    async def during(cid, mid, returned):
        await _tap_returns(d, fake, cid, mid, user=h.ADMIN)
    run_async(_in_flight(bot, h, d, fake, gate, during))
    assert len(fake.posts) == 1


# ---- a failure inside the sender is final ------------------------------------------------

class _SenderBug(RuntimeError):
    """Not a network error: a bug somewhere under send()."""


def _broken_send(bot, d, h, run_async, use_net):
    fake = use_net(h.FakeNet(sentry_error=_SenderBug("boom")))

    async def go():
        cid, mid = await h.open_card(bot, d)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        await _wait(lambda: fake.posts and "Sending..." not in bot.tg.text_of(cid, mid))
        await h.settle()
        return cid, mid
    return run_async(go())


def test_sender_bug_is_not_a_network_error(h):
    """The partition below is real: the fake raises something httpx does
    not treat as a transport error."""
    assert not issubclass(_SenderBug, httpx.HTTPError)


def test_sender_bug_does_not_say_try_later(bot, drive, h, run_async, use_net):
    cid, mid = _broken_send(bot, drive(bot), h, run_async, use_net)
    assert wording.TRY_LATER not in bot.tg.text_of(cid, mid)


def test_sender_bug_does_not_leave_the_card_sending(bot, drive, h, run_async, use_net):
    cid, mid = _broken_send(bot, drive(bot), h, run_async, use_net)
    assert "Sending..." not in bot.tg.text_of(cid, mid)


def test_sender_bug_removes_the_send_button(bot, drive, h, run_async, use_net):
    cid, mid = _broken_send(bot, drive(bot), h, run_async, use_net)
    assert "_:rp:send" not in [data for _, data in bot.tg.buttons_of(cid, mid)]


def test_sender_bug_does_not_claim_it_was_sent(bot, drive, h, run_async, use_net):
    cid, mid = _broken_send(bot, drive(bot), h, run_async, use_net)
    assert SENT_PREFIX not in bot.tg.text_of(cid, mid)


def test_sender_bug_card_has_no_em_dash(bot, drive, h, run_async, use_net):
    cid, mid = _broken_send(bot, drive(bot), h, run_async, use_net)
    assert h.EM_DASH not in bot.tg.text_of(cid, mid)


def test_tap_after_sender_bug_sends_nothing_more(bot, drive, h, run_async, use_net):
    """A final line is final: tapping the (removed) Send again does nothing."""
    d = drive(bot)
    cid, mid = _broken_send(bot, d, h, run_async, use_net)
    fresh = use_net(h.FakeNet())
    run_async(d.tap("_:rp:send", chat=cid, message_id=mid))
    assert fresh.posts == []
