"""Tests for the small free-standing helpers in aipager.telegram_bot.

Covers _log_blocked_once throttle, _is_bot_blocked, _send_with_retry
RetryAfter / too-long handling, and the document size guard.
"""

from __future__ import annotations

import asyncio

import pytest
from telegram.error import BadRequest, Forbidden, RetryAfter

from aipager import telegram_bot as tb


# ----- _log_blocked_once -----

def test_log_blocked_once_throttles(monkeypatch, caplog):
    monkeypatch.setattr(tb, "_LAST_BLOCKED_LOG_TS", 0.0)
    monkeypatch.setattr(tb.time, "monotonic", lambda: 100.0)
    caplog.set_level("ERROR", logger="aipager.telegram_bot")
    tb._log_blocked_once(Exception("bot was blocked"))
    n1 = sum("blocked or deleted" in r.message for r in caplog.records)
    tb._log_blocked_once(Exception("bot was blocked"))  # within 60s
    n2 = sum("blocked or deleted" in r.message for r in caplog.records)
    assert n1 == 1
    assert n2 == 1, "second log within 60s should be suppressed"


def test_log_blocked_after_interval_logs_again(monkeypatch, caplog):
    monkeypatch.setattr(tb, "_LAST_BLOCKED_LOG_TS", 0.0)
    monkeypatch.setattr(tb.time, "monotonic", lambda: 100.0)
    caplog.set_level("ERROR", logger="aipager.telegram_bot")
    tb._log_blocked_once(Exception("bot was blocked"))
    monkeypatch.setattr(tb.time, "monotonic", lambda: 200.0)  # +100s later
    tb._log_blocked_once(Exception("bot was blocked"))
    assert sum("blocked or deleted" in r.message for r in caplog.records) == 2


# ----- _is_bot_blocked -----

def test_is_bot_blocked_forbidden_class():
    assert tb._is_bot_blocked(Forbidden("Forbidden")) is True


@pytest.mark.parametrize("msg,expected", [
    ("bot was blocked by the user", True),
    ("Bot was blocked by the user", True),
    ("user blocked by the user", True),
    ("chat not found", False),
    ("rate limited", False),
])
def test_is_bot_blocked_string_match(msg, expected):
    assert tb._is_bot_blocked(Exception(msg)) is expected


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


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if False else asyncio.new_event_loop().run_until_complete(coro)


def test_send_with_retry_passes_through_on_success():
    bot = _FakeBot(["MSG"])
    out = _run(tb._send_with_retry(bot, chat_id=1, text="hi"))
    assert out == "MSG"
    assert len(bot.calls) == 1


def test_send_with_retry_retries_on_flood(monkeypatch):
    # First call: RetryAfter. Second: success.
    bot = _FakeBot([RetryAfter(0), "MSG"])

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(tb.asyncio, "sleep", _no_sleep)
    out = _run(tb._send_with_retry(bot, chat_id=1, text="hi"))
    assert out == "MSG"
    assert len(bot.calls) == 2


def test_send_with_retry_truncates_on_too_long():
    long = "x" * (tb.TELEGRAM_MAX_TEXT_LEN * 2)
    bot = _FakeBot([BadRequest("Bad Request: message is too long"), "MSG"])
    out = _run(tb._send_with_retry(bot, chat_id=1, text=long))
    assert out == "MSG"
    second_call_text = bot.calls[1][0]
    assert len(second_call_text) <= tb.TELEGRAM_MAX_TEXT_LEN
    assert "truncated" in second_call_text


def test_send_with_retry_propagates_other_badrequest():
    bot = _FakeBot([BadRequest("Bad Request: chat not found")])
    with pytest.raises(BadRequest):
        _run(tb._send_with_retry(bot, chat_id=1, text="hi"))


def test_send_with_retry_propagates_forbidden(monkeypatch, caplog):
    bot = _FakeBot([Forbidden("Forbidden: bot was blocked")])
    # Force the throttle gate open regardless of how small time.monotonic()
    # is on a fresh CI runner (uptime < 60s).
    monkeypatch.setattr(tb, "_LAST_BLOCKED_LOG_TS", -1e9)
    caplog.set_level("ERROR", logger="aipager.telegram_bot")
    with pytest.raises(Forbidden):
        _run(tb._send_with_retry(bot, chat_id=1, text="hi"))
    assert any("blocked or deleted" in r.message for r in caplog.records)


def test_max_doc_bytes_is_below_telegram_50mb():
    assert tb.TELEGRAM_MAX_DOC_BYTES < 50 * 1024 * 1024


# ----- 3.6 — retry-after extraction -----

def test_detect_api_error_returns_tuple():
    result = tb._detect_api_error("API Error: 500 internal server error")
    assert result is not None
    msg, retry = result
    assert "internal error" in msg.lower()
    assert retry is None


def test_detect_api_error_none_when_no_match():
    assert tb._detect_api_error("normal response text") is None
    assert tb._detect_api_error("") is None


def test_detect_api_error_rate_limit_extracts_retry_after():
    """The common Anthropic format: 'Please retry after 60 seconds'."""
    text = "API Error: 429 rate_limit_error. Please retry after 60 seconds."
    msg, retry = tb._detect_api_error(text)
    assert retry == 60
    assert "60s" in msg
    assert "Wait 60s" in msg


def test_detect_api_error_rate_limit_extracts_alt_format():
    text = "rate_limit_error: wait 30 seconds"
    msg, retry = tb._detect_api_error(text)
    assert retry == 30


def test_detect_api_error_rate_limit_extracts_cooldown():
    text = "rate_limit hit; 45 second cooldown"
    msg, retry = tb._detect_api_error(text)
    assert retry == 45


def test_detect_api_error_rate_limit_without_seconds_keeps_generic():
    """When the error matches rate-limit pattern but has no parseable
    retry-after, the generic message stays."""
    text = "API Error: 429 rate_limit_error"
    msg, retry = tb._detect_api_error(text)
    assert retry is None
    assert "Wait a moment" in msg


def test_detect_api_error_non_rate_limit_doesnt_extract_retry():
    """Even if the error text happens to contain 'retry after X', a
    non-rate-limit error doesn't pull it in."""
    text = "API Error: 500 internal server error. retry after 30 seconds"
    msg, retry = tb._detect_api_error(text)
    # retry-after extraction is gated on the rate_limit kind
    assert retry is None


# ----- 2.8 — TruncationFailed after N attempts -----

def test_send_with_retry_caps_truncation_attempts():
    """A pathological payload that stays "too long" after every truncation
    attempt should raise TruncationFailed instead of looping forever."""
    long_text = "x" * (tb.TELEGRAM_MAX_TEXT_LEN * 4)
    # Server keeps rejecting as too long, no matter what we send.
    side_effects = [
        BadRequest("Bad Request: message is too long"),
    ] * (tb._MAX_TRUNCATIONS + 5)
    bot = _FakeBot(side_effects)

    with pytest.raises(tb.TruncationFailed):
        _run(tb._send_with_retry(bot, chat_id=1, text=long_text))

    # Sent _MAX_TRUNCATIONS + 1 times (initial + N truncation retries
    # before raising on the (N+1)-th attempt).
    assert len(bot.calls) == tb._MAX_TRUNCATIONS + 1


def test_send_with_retry_succeeds_within_truncation_budget():
    """Single truncation that succeeds on the second call works (no
    TruncationFailed raised)."""
    long_text = "x" * (tb.TELEGRAM_MAX_TEXT_LEN * 2)
    bot = _FakeBot([
        BadRequest("Bad Request: message is too long"),
        "MSG",  # second call succeeds
    ])
    out = _run(tb._send_with_retry(bot, chat_id=1, text=long_text))
    assert out == "MSG"
