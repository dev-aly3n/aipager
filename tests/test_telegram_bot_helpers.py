"""Tests for the small free-standing helpers in aipager.bot.transport.

Covers _log_blocked_once throttle, _is_bot_blocked, _send_with_retry
RetryAfter / too-long handling, and the document size guard.

Nothing here patches ``asyncio.sleep`` through ``transport``'s module
path any more: since roadmap 8.21 ``_send_with_retry`` has no sleep at
all (the limiter owns every wait), so those patches neutralised nothing
while doing the one thing CLAUDE.md forbids — ``aipager.bot.transport.
asyncio`` IS the global module.
"""

from __future__ import annotations


import pytest
from telegram.error import BadRequest, Forbidden, RetryAfter

from aipager.bot.transport import (
    TELEGRAM_MAX_DOC_BYTES,
    TELEGRAM_MAX_TEXT_LEN,
    TruncationFailed,
    _MAX_TRUNCATIONS,
    _detect_api_error,
    _is_bot_blocked,
    _log_blocked_once,
    _send_with_retry,
)
from aipager.bot import transport as tbt


# ----- _log_blocked_once -----

def test_log_blocked_once_throttles(monkeypatch, caplog, run_async):
    monkeypatch.setattr(tbt, "_LAST_BLOCKED_LOG_TS", 0.0)
    monkeypatch.setattr(tbt.time, "monotonic", lambda: 100.0)
    caplog.set_level("ERROR", logger="aipager.bot.transport")
    _log_blocked_once(Exception("bot was blocked"))
    n1 = sum("blocked or deleted" in r.message for r in caplog.records)
    _log_blocked_once(Exception("bot was blocked"))  # within 60s
    n2 = sum("blocked or deleted" in r.message for r in caplog.records)
    assert n1 == 1
    assert n2 == 1, "second log within 60s should be suppressed"


def test_log_blocked_after_interval_logs_again(monkeypatch, caplog, run_async):
    monkeypatch.setattr(tbt, "_LAST_BLOCKED_LOG_TS", 0.0)
    monkeypatch.setattr(tbt.time, "monotonic", lambda: 100.0)
    caplog.set_level("ERROR", logger="aipager.bot.transport")
    _log_blocked_once(Exception("bot was blocked"))
    monkeypatch.setattr(tbt.time, "monotonic", lambda: 200.0)  # +100s later
    _log_blocked_once(Exception("bot was blocked"))
    assert sum("blocked or deleted" in r.message for r in caplog.records) == 2


# ----- _is_bot_blocked -----

def test_is_bot_blocked_forbidden_class(run_async):
    assert _is_bot_blocked(Forbidden("Forbidden")) is True


@pytest.mark.parametrize("msg,expected", [
    ("bot was blocked by the user", True),
    ("Bot was blocked by the user", True),
    ("user blocked by the user", True),
    ("chat not found", False),
    ("rate limited", False),
])
def test_is_bot_blocked_string_match(msg, expected, run_async):
    assert _is_bot_blocked(Exception(msg)) is expected


# ----- _send_with_retry -----

class _FakeBot:
    def __init__(self, side_effects):
        self._side = list(side_effects)
        self.calls = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append((text, kwargs))
        s = self._side.pop(0)
        if isinstance(s, Exception):
            raise s
        return s


def test_send_with_retry_passes_through_on_success(run_async):
    bot = _FakeBot(["MSG"])
    out = run_async(_send_with_retry(bot, chat_id=1, text="hi"))
    assert out == "MSG"
    assert len(bot.calls) == 1


def test_send_with_retry_makes_one_attempt_and_propagates_a_flood(run_async):
    """Since 8.21 the limiter defers a small 429 and makes the one retry
    itself, so a ``RetryAfter`` that still reaches here has ALREADY been
    retried — retrying again would put a third attempt into the window
    that just rejected two. One attempt, and the error propagates.

    Mutation: re-add the ``await asyncio.sleep(wait)`` + ``continue`` arm
    and this makes three attempts and returns "MSG".
    """
    bot = _FakeBot([RetryAfter(0), "MSG", "MSG"])

    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=1, text="hi"))
    assert len(bot.calls) == 1


def test_send_with_retry_truncates_on_too_long(run_async):
    long = "x" * (TELEGRAM_MAX_TEXT_LEN * 2)
    bot = _FakeBot([BadRequest("Bad Request: message is too long"), "MSG"])
    out = run_async(_send_with_retry(bot, chat_id=1, text=long))
    assert out == "MSG"
    second_call_text = bot.calls[1][0]
    assert len(second_call_text) <= TELEGRAM_MAX_TEXT_LEN
    assert "truncated" in second_call_text


def test_send_with_retry_propagates_other_badrequest(run_async):
    bot = _FakeBot([BadRequest("Bad Request: chat not found")])
    with pytest.raises(BadRequest):
        run_async(_send_with_retry(bot, chat_id=1, text="hi"))


def test_send_with_retry_propagates_forbidden(monkeypatch, caplog, run_async):
    bot = _FakeBot([Forbidden("Forbidden: bot was blocked")])
    # Force the throttle gate open regardless of how small time.monotonic()
    # is on a fresh CI runner (uptime < 60s).
    monkeypatch.setattr(tbt, "_LAST_BLOCKED_LOG_TS", -1e9)
    caplog.set_level("ERROR", logger="aipager.bot.transport")
    with pytest.raises(Forbidden):
        run_async(_send_with_retry(bot, chat_id=1, text="hi"))
    assert any("blocked or deleted" in r.message for r in caplog.records)


def test_max_doc_bytes_is_below_telegram_50mb(run_async):
    assert TELEGRAM_MAX_DOC_BYTES < 50 * 1024 * 1024


# ----- 3.6 — retry-after extraction -----

def test_detect_api_error_returns_tuple(run_async):
    result = _detect_api_error("API Error: 500 internal server error")
    assert result is not None
    msg, retry = result
    assert "internal error" in msg.lower()
    assert retry is None


def test_detect_api_error_none_when_no_match(run_async):
    assert _detect_api_error("normal response text") is None
    assert _detect_api_error("") is None


def test_detect_api_error_rate_limit_extracts_retry_after(run_async):
    """The common Anthropic format: 'Please retry after 60 seconds'."""
    text = "API Error: 429 rate_limit_error. Please retry after 60 seconds."
    msg, retry = _detect_api_error(text)
    assert retry == 60
    assert "60s" in msg
    assert "Wait 60s" in msg


def test_detect_api_error_rate_limit_extracts_alt_format(run_async):
    text = "rate_limit_error: wait 30 seconds"
    msg, retry = _detect_api_error(text)
    assert retry == 30


def test_detect_api_error_rate_limit_extracts_cooldown(run_async):
    text = "rate_limit_error: 45 second cooldown"
    msg, retry = _detect_api_error(text)
    assert retry == 45


def test_detect_api_error_ignores_prose_about_third_party_rate_limit(run_async):
    """Claude often discusses third-party rate limits in its prose (e.g.
    'Waiting on the NearBlocks rate-limit'). That MUST NOT trigger the
    Anthropic rate-limit warning — only real API error markers do.
    Reported on 2026-06-25 in a multi-turn convo about NearBlocks."""
    prose = (
        "He's writing a script to decode every envelope per stable trade "
        "and isolate the solver's actual cut, separate from the aggregator's "
        "appFee. NearBlocks rate-limited him so he's waiting it out, then "
        "running."
    )
    assert _detect_api_error(prose) is None
    assert _detect_api_error("Waiting on the NearBlocks rate-limit") is None
    assert _detect_api_error("hit the rate limit on a third-party service") is None


def test_detect_api_error_matches_canonical_anthropic_body(run_async):
    """Anthropic's verbatim 429 message body should match even without
    the explicit ``API Error: 429`` prefix."""
    body = "This request would exceed your account's rate limit. Please try again later."
    result = _detect_api_error(body)
    assert result is not None
    msg, _ = result
    assert "Rate limit" in msg


def test_detect_api_error_matches_http_429(run_async):
    """The ``HTTP 429: rate_limit_error`` shape observed in claude-code logs."""
    result = _detect_api_error("HTTP 429: rate_limit_error: too many requests")
    assert result is not None


def test_detect_api_error_rate_limit_without_seconds_keeps_generic(run_async):
    """When the error matches rate-limit pattern but has no parseable
    retry-after, the generic message stays."""
    text = "API Error: 429 rate_limit_error"
    msg, retry = _detect_api_error(text)
    assert retry is None
    assert "Wait a moment" in msg


def test_detect_api_error_non_rate_limit_doesnt_extract_retry(run_async):
    """Even if the error text happens to contain 'retry after X', a
    non-rate-limit error doesn't pull it in."""
    text = "API Error: 500 internal server error. retry after 30 seconds"
    msg, retry = _detect_api_error(text)
    # retry-after extraction is gated on the rate_limit kind
    assert retry is None


# ----- 2.8 — TruncationFailed after N attempts -----

def test_send_with_retry_caps_truncation_attempts(run_async):
    """A pathological payload that stays "too long" after every truncation
    attempt should raise TruncationFailed instead of looping forever."""
    long_text = "x" * (TELEGRAM_MAX_TEXT_LEN * 4)
    # Server keeps rejecting as too long, no matter what we send.
    side_effects = [
        BadRequest("Bad Request: message is too long"),
    ] * (_MAX_TRUNCATIONS + 5)
    bot = _FakeBot(side_effects)

    with pytest.raises(TruncationFailed):
        run_async(_send_with_retry(bot, chat_id=1, text=long_text))

    # Sent _MAX_TRUNCATIONS + 1 times (initial + N truncation retries
    # before raising on the (N+1)-th attempt).
    assert len(bot.calls) == _MAX_TRUNCATIONS + 1


def test_send_with_retry_succeeds_within_truncation_budget(run_async):
    """Single truncation that succeeds on the second call works (no
    TruncationFailed raised)."""
    long_text = "x" * (TELEGRAM_MAX_TEXT_LEN * 2)
    bot = _FakeBot([
        BadRequest("Bad Request: message is too long"),
        "MSG",  # second call succeeds
    ])
    out = run_async(_send_with_retry(bot, chat_id=1, text=long_text))
    assert out == "MSG"


# ----- _send_with_retry: RetryAfter cap + flood-control reaction -----


class _ReactionBot:
    """Bot stub whose send_message raises the given exceptions and whose
    set_message_reaction records positional calls."""

    def __init__(self, send_side_effects, reaction_side_effect=None):
        self._send_side = list(send_side_effects)
        self._reaction_side = reaction_side_effect
        self.reactions: list[tuple] = []

    async def send_message(self, chat_id, text, **kwargs):
        s = self._send_side.pop(0)
        if isinstance(s, Exception):
            raise s
        return s

    async def set_message_reaction(self, chat_id, message_id, emoji):
        self.reactions.append((chat_id, message_id, emoji))
        if self._reaction_side is not None:
            raise self._reaction_side


def test_send_with_retry_caps_long_retry_after(run_async):
    """Retry_after longer than the cap → raise, do NOT sleep.

    Nothing patches a sleep here any more: since 8.21 this function has
    none to patch, so a reinstated one would block the test for its full
    duration rather than pass quietly.
    """
    bot = _ReactionBot([RetryAfter(17000)])

    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=1, text="hi"))


def test_send_with_retry_never_sleeps_on_a_small_retry_after(run_async):
    """Retry_after under the cap: propagate it, do not wait again.

    Until 8.21 this slept the full ``retry_after`` and retried — on top
    of whatever the limiter had already waited, and into the same window.
    The deferral now happens once, in the limiter, and the chat is barred
    for exactly as long as Telegram asked.

    Mutation: restore ``await asyncio.sleep(wait)`` and this test takes
    30 real seconds and returns "MSG" instead of raising.
    """
    bot = _ReactionBot([RetryAfter(30), "MSG"])

    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=1, text="hi"))


def test_send_with_retry_sets_no_reaction_when_giving_up(run_async):
    """8.26 D-1: the give-up branch reacts on NOTHING, reply target or not.

    Until 0.7.12 it fired a 🚨 ``setMessageReaction`` on the reply target
    right after arming the mute, on the theory that reactions ride a
    separate rate-limit bucket. That bucket governs PACING, not bans:
    measured 2026-09-15, requests into an ACTIVE ban escalated retry_after
    1283 -> 312 -> 34212 (9.5 h) regardless of endpoint.

    Mutation: re-add the reaction call and ``reactions`` is non-empty.
    """
    bot = _ReactionBot([RetryAfter(1000)])

    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(
            bot, chat_id=7, text="hi", reply_to_message_id=42,
        ))
    assert bot.reactions == []


def test_send_with_retry_no_reaction_when_no_reply_target(run_async):
    """No reply target → no reaction attempted (nothing to attach to)."""
    bot = _ReactionBot([RetryAfter(1000)])

    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=7, text="hi"))
    assert bot.reactions == []


def test_send_with_retry_raises_retryafter_with_no_reaction_attempted(run_async):
    """The caller depends on the ``RetryAfter`` to know the send failed,
    and nothing between the mute and the raise may swallow it.

    This used to guard a reaction call whose failure could have masked the
    exception. 8.26 D-1 removed the call outright, which is the stronger
    form of the same guarantee: there is no longer anything there to mask
    it. The bot is still built with a reaction side effect that WOULD
    raise, so re-adding the call is caught here as well as by the
    assertion below.
    """
    bot = _ReactionBot(
        [RetryAfter(1000)],
        reaction_side_effect=RuntimeError("reaction api down"),
    )

    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(
            bot, chat_id=7, text="hi", reply_to_message_id=42,
        ))
    assert bot.reactions == []
