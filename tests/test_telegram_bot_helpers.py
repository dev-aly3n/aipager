"""Tests for the small free-standing helpers in aipager.bot.transport.

Covers _log_blocked_once throttle, _is_bot_blocked, _send_with_retry
RetryAfter / too-long handling, and the document size guard.
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


def test_send_with_retry_retries_on_flood(monkeypatch, run_async):
    # First call: RetryAfter. Second: success.
    bot = _FakeBot([RetryAfter(0), "MSG"])

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(tbt.asyncio, "sleep", _no_sleep)
    out = run_async(_send_with_retry(bot, chat_id=1, text="hi"))
    assert out == "MSG"
    assert len(bot.calls) == 2


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


def test_send_with_retry_caps_long_retry_after(monkeypatch, run_async):
    """Retry_after longer than the cap → raise, do NOT sleep."""
    from aipager.config import TELEGRAM_MAX_RETRY_AFTER
    bot = _ReactionBot([RetryAfter(17000)])

    async def _guard_sleep(seconds):
        # If we ever sleep here it must be well under the cap; the
        # cap-exceeded path must NOT call sleep at all.
        if seconds > TELEGRAM_MAX_RETRY_AFTER:
            raise AssertionError(
                f"unexpected asyncio.sleep({seconds}) beyond cap")

    monkeypatch.setattr(tbt.asyncio, "sleep", _guard_sleep)
    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=1, text="hi"))


def test_send_with_retry_normal_retry_after_still_sleeps(monkeypatch,
                                                         run_async):
    """Retry_after under the cap: sleep and retry as before."""
    bot = _ReactionBot([RetryAfter(30), "MSG"])
    slept: list[float] = []

    async def _record_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(tbt.asyncio, "sleep", _record_sleep)
    out = run_async(_send_with_retry(bot, chat_id=1, text="hi"))
    assert out == "MSG"
    assert slept == [30]


def test_send_with_retry_sets_flood_reaction_when_giving_up(monkeypatch,
                                                            run_async):
    """When give-up threshold is exceeded and a reply-to target is set,
    react on it with 🚨 (visible signal for the user) before re-raising."""
    bot = _ReactionBot([RetryAfter(1000)])

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(tbt.asyncio, "sleep", _no_sleep)
    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(
            bot, chat_id=7, text="hi", reply_to_message_id=42,
        ))
    assert bot.reactions == [(7, 42, "🚨")]


def test_send_with_retry_no_reaction_when_no_reply_target(monkeypatch,
                                                          run_async):
    """No reply target → no reaction attempted (nothing to attach to)."""
    bot = _ReactionBot([RetryAfter(1000)])

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(tbt.asyncio, "sleep", _no_sleep)
    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=7, text="hi"))
    assert bot.reactions == []


def test_send_with_retry_reaction_failure_does_not_mask_retryafter(
        monkeypatch, run_async):
    """A failing reaction call must NOT swallow the original RetryAfter —
    the caller depends on that exception to know the send failed."""
    bot = _ReactionBot(
        [RetryAfter(1000)],
        reaction_side_effect=RuntimeError("reaction api down"),
    )

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(tbt.asyncio, "sleep", _no_sleep)
    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(
            bot, chat_id=7, text="hi", reply_to_message_id=42,
        ))
    # Reaction was attempted before the raise.
    assert bot.reactions == [(7, 42, "🚨")]
