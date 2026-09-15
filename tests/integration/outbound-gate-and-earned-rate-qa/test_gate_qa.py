"""R1 — one outbound gate: rows A and C, criteria 1, 3, 4, 5, 22.

Independent QA of the promise the incident cost 9.5 hours to buy: *no Bot
API call reaches Telegram for a chat with an active mute, by any path, zero
exceptions*. Every row here runs on a limiter-routed double or on the real
``rich_message._post`` behind an ``httpx.MockTransport`` — never on a
``MagicMock``, which would happily record the call the gate was supposed to
refuse.

Every "zero calls" row is preceded by a POSITIVE CONTROL in the same test:
the same call on a healthy chat must actually reach the callback first,
otherwise "zero calls" is a statement about a code path that never ran.
"""

from __future__ import annotations

import pytest
from telegram.error import RetryAfter

from aipager import config
from aipager.bot import transport
from aipager.bot.flood import MUTE, FloodMuted
from aipager.state import Status, TrackedSession

CHAT = -1001234567
BAN = 34212.0


# ===== row A — the card creation that leaked on 2026-09-15 ================

def _busy_session(chat_id=CHAT) -> TrackedSession:
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = chat_id
    return sess


def test_send_busy_reaches_telegram_when_the_chat_is_healthy(
    mk_bot, gated_bot, run_async,
):
    """The positive control for row A.

    Without this the next test is a statement about a path that never ran:
    if ``send_busy`` returned ``None`` for an unrelated reason, "zero
    calls while muted" would pass on a corpse.
    """
    bot = mk_bot()
    bot._app.bot = gated_bot
    assert run_async(bot.send_busy(_busy_session())) == 4242
    assert gated_bot.endpoints() == ["sendMessage"]


def test_send_busy_on_a_muted_chat_makes_no_call_and_returns_none(
    mk_bot, gated_bot, run_async,
):
    """Row A / criterion 1 — the exact site of the 18:10:21 traceback.

    ``animation.send_busy`` -> ``self._app.bot.send_message`` ->
    ``process_request`` -> HTTP -> ``RetryAfter: 33147``. The gate must
    refuse it, the caller must survive, and nothing may be raised at it.
    """
    bot = mk_bot()
    bot._app.bot = gated_bot
    MUTE.mute(CHAT, BAN)

    assert run_async(bot.send_busy(_busy_session())) is None
    assert gated_bot.calls == []


def test_send_busy_mutes_only_its_own_chat(mk_bot, gated_bot, run_async):
    """Equivalence partition: a muted chat and a healthy one are separate
    classes, and a mute on one must not silence the other."""
    bot = mk_bot()
    bot._app.bot = gated_bot
    MUTE.mute(CHAT, BAN)

    assert run_async(bot.send_busy(_busy_session(chat_id=-100999))) == 4242
    assert [c[1] for c in gated_bot.calls] == [-100999]


# ===== row C — every endpoint, exempt or not =============================

@pytest.mark.parametrize("endpoint", [
    "deleteMessage", "sendDocument", "sendChatAction", "setMessageReaction",
])
def test_the_gate_refuses_every_endpoint_for_a_muted_chat(
    limiter, run_async, endpoint,
):
    """Row C / criterion 3. ``sendChatAction`` and ``setMessageReaction``
    are exempt from the BUDGET, never from the MUTE (D-1): the gate sits
    before the exemption branch precisely so this holds."""
    ran: list[str] = []

    async def _callback():
        ran.append(endpoint)
        return "sent"

    MUTE.mute(CHAT, BAN)
    with pytest.raises(FloodMuted):
        run_async(limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint=endpoint,
            data={"chat_id": CHAT}, rate_limit_args=None))
    assert ran == [], "the callback ran — the request left the daemon"


@pytest.mark.parametrize("endpoint", [
    "deleteMessage", "sendDocument", "sendChatAction", "setMessageReaction",
])
def test_the_same_endpoints_go_through_on_a_healthy_chat(
    limiter, run_async, endpoint,
):
    """The control for the row above: the refusal must be the mute's doing
    and not a typo in an endpoint name."""
    ran: list[str] = []

    async def _callback():
        ran.append(endpoint)
        return "sent"

    assert run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint=endpoint,
        data={"chat_id": CHAT}, rate_limit_args=None)) == "sent"
    assert ran == [endpoint]


def test_the_refusal_carries_the_remaining_ban_and_the_chat(
    limiter, run_async,
):
    """``FloodMuted(retry_after, chat_id)`` is the contract callers
    discriminate on; an empty exception would make the held-answer path
    unable to say how long it waited."""
    MUTE.mute(CHAT, BAN)

    async def _callback():
        return "sent"

    with pytest.raises(FloodMuted) as excinfo:
        run_async(limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": CHAT}, rate_limit_args=None))
    assert excinfo.value.chat_id == CHAT
    assert 0 < excinfo.value.retry_after <= BAN


def test_a_chat_id_that_arrives_as_a_string_is_the_same_mute(
    limiter, run_async,
):
    """Error guessing: the legacy path hands ``config.CHAT_ID`` over as a
    str and the rich path as an int. If they resolved to two entries the
    gate would leak every call made by whichever path did not arm it."""
    MUTE.mute(str(CHAT), BAN)

    async def _callback():
        return "sent"

    with pytest.raises(FloodMuted):
        run_async(limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": CHAT}, rate_limit_args=None))


# ===== criterion 4 — a mute must not mute the daemon =====================

@pytest.mark.parametrize("endpoint", ["getMe", "getFile", "setMyCommands"])
@pytest.mark.parametrize("data", [None, {}, {"chat_id": None}, "not-a-dict"])
def test_calls_with_no_resolvable_chat_are_never_gated(
    limiter, run_async, endpoint, data,
):
    """Criterion 4. There is no chat to check, so there is nothing to
    refuse — and muting these would take the daemon off the air for the
    length of a 9.5-hour ban."""
    MUTE.mute(CHAT, BAN)
    MUTE.mute(config.CHAT_ID, BAN)
    ran: list[str] = []

    async def _callback():
        ran.append(endpoint)
        return "ok"

    assert run_async(limiter.process_request(
        callback=_callback, args=(), kwargs={}, endpoint=endpoint,
        data=data, rate_limit_args=None)) == "ok"
    assert ran == [endpoint]


# ===== criterion 22 / R8 — exempt from the BUDGET, not from the mute =====

def test_chat_actions_spend_no_chat_token_and_never_wait(
    limiter, qa_clock, run_async,
):
    """R8 / criterion 22: measured 2026-09-12, chat actions answered 200
    throughout a window that refused every edit into the same chat. Ten
    in a row is far past ``TELEGRAM_CHAT_BURST``; if they were metered the
    injected sleep would have been used."""
    async def _callback():
        return "ok"

    async def _drive():
        for _ in range(10):
            await limiter.process_request(
                callback=_callback, args=(), kwargs={},
                endpoint="sendChatAction", data={"chat_id": CHAT},
                rate_limit_args=None)

    run_async(_drive())
    assert qa_clock.sleeps == []


def test_reactions_spend_no_chat_token(limiter, run_async):
    """Same class, the other exempt endpoint: the chat's bucket must be
    untouched by ten reactions."""
    async def _callback():
        return "ok"

    async def _drive():
        await limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": CHAT}, rate_limit_args=None)
        before = _tokens(limiter, CHAT)
        for _ in range(10):
            await limiter.process_request(
                callback=_callback, args=(), kwargs={},
                endpoint="setMessageReaction", data={"chat_id": CHAT},
                rate_limit_args=None)
        return before, _tokens(limiter, CHAT)

    before, after = run_async(_drive())
    assert after == pytest.approx(before)


def _tokens(limiter, chat_id) -> float:
    for chat in limiter.snapshot()["chats"]:
        if chat["chat_id"] == chat_id:
            return chat["tokens"]
    raise AssertionError(f"chat {chat_id} not in the snapshot")


# ===== criterion 5 / D-1 — the give-up branch fires nothing ==============

class _RecordingBot:
    """Records EVERY Bot API method reached, not only the ones it knows:
    any attribute is a coroutine that appends its own name, so a call this
    test never thought of still shows up."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.calls.append("sendMessage")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def __getattr__(self, name):
        async def _record(*a, **kw):
            self.calls.append(name)
        return _record


def test_a_ban_sized_retry_after_arms_the_mute_and_fires_nothing_else(
    run_async,
):
    """Criterion 5 / D-1. Until 0.7.12 this branch armed the mute and then
    fired a 🚨 INTO the chat it had just banned. A request into an ACTIVE
    ban is what escalated 1283 -> 312 -> 34212; the endpoint it targets is
    irrelevant to the escalation."""
    bot = _RecordingBot([RetryAfter(28911)])

    with pytest.raises(RetryAfter):
        run_async(transport._send_with_retry(
            bot, chat_id=7, text="hi", reply_to_message_id=42))

    assert bot.calls == ["sendMessage"], "something was sent into the ban"
    assert MUTE.is_muted(7), "the ban itself must still be remembered"


def test_a_retry_after_exactly_at_the_cap_is_a_429_and_not_a_ban(run_async):
    """Boundary value, the 429/ban frontier.

    ``TELEGRAM_MAX_RETRY_AFTER`` is the documented boundary: at the value
    itself this is a rate limit the limiter absorbs, not a ban, so nothing
    may be muted. One second past it (next row) is a ban.
    """
    bot = _RecordingBot([RetryAfter(config.TELEGRAM_MAX_RETRY_AFTER)])

    with pytest.raises(RetryAfter):
        run_async(transport._send_with_retry(bot, chat_id=7, text="hi"))

    assert not MUTE.is_muted(7)
    assert bot.calls == ["sendMessage"], "no retry into the same window"


def test_one_second_past_the_cap_is_a_ban(run_async):
    """The just-outside half of the same boundary."""
    bot = _RecordingBot([RetryAfter(config.TELEGRAM_MAX_RETRY_AFTER + 1)])

    with pytest.raises(RetryAfter):
        run_async(transport._send_with_retry(bot, chat_id=7, text="hi"))

    assert MUTE.is_muted(7)
    assert bot.calls == ["sendMessage"]


def test_a_muted_chat_never_touches_the_bot_at_all(run_async):
    """The pre-check at the seam, which is the only guard when the bot is
    not the limiter-bound one."""
    MUTE.mute(7, 1000)
    bot = _RecordingBot(["MSG"])

    with pytest.raises(FloodMuted):
        run_async(transport._send_with_retry(
            bot, chat_id=7, text="hi", reply_to_message_id=3))

    assert bot.calls == []
