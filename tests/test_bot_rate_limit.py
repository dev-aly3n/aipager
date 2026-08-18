"""Telegram flood control and transient network errors must not take the
bot down or bury the log in tracebacks.

Both failure modes have really cost this daemon uptime: a runaway animation
earned a 9.6-hour flood-control penalty and the bot went mute for most of a
day (prompts still reached Claude; no reply could be sent back), and a
`Bad Gateway` killed the daemon overnight with nothing to restart it.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

from telegram.error import NetworkError, RetryAfter


def test_this_daemons_builder_installs_a_rate_limiter(mk_bot, monkeypatch):
    """Without a rate limiter, every one of ~64 send/edit sites hits
    Telegram unthrottled and a flood penalty surfaces as an unhandled
    RetryAfter at each of them.

    Asserts on THIS daemon's own builder chain. An earlier version of this
    test constructed its own ApplicationBuilder and passed happily with
    aipager's rate_limiter() call deleted — it proved the library works,
    not that we use it.
    """
    from telegram.ext import AIORateLimiter

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

    assert "limiter" in seen, "this daemon never calls .rate_limiter()"
    assert isinstance(seen["limiter"], AIORateLimiter)


def test_flood_control_logs_one_line_not_a_traceback(mk_bot, run_async, caplog):
    bot = mk_bot()
    ctx = MagicMock()
    ctx.error = RetryAfter(7096)

    with caplog.at_level(logging.WARNING, logger="aipager.bot.lifecycle"):
        run_async(bot._on_telegram_error(None, ctx))

    msgs = [r.getMessage() for r in caplog.records]
    assert any("flood control" in m.lower() for m in msgs), msgs
    assert any("7096" in m for m in msgs), "must state the wait so it is diagnosable"
    assert not any(r.exc_info for r in caplog.records), "logged a traceback"


def test_transient_network_error_logs_one_line_not_a_traceback(
    mk_bot, run_async, caplog,
):
    bot = mk_bot()
    ctx = MagicMock()
    ctx.error = NetworkError("Bad Gateway")

    with caplog.at_level(logging.WARNING, logger="aipager.bot.lifecycle"):
        run_async(bot._on_telegram_error(None, ctx))

    msgs = [r.getMessage() for r in caplog.records]
    assert any("network error" in m.lower() for m in msgs), msgs
    assert not any(r.exc_info for r in caplog.records), "logged a traceback"


def test_an_unexpected_error_keeps_its_traceback(mk_bot, run_async, caplog):
    """The quieting must be surgical: an error nobody predicted should stay
    loud, or this handler becomes a way to hide real bugs."""
    bot = mk_bot()
    ctx = MagicMock()
    ctx.error = ValueError("something nobody predicted")

    with caplog.at_level(logging.ERROR, logger="aipager.bot.lifecycle"):
        run_async(bot._on_telegram_error(None, ctx))

    assert any(r.exc_info for r in caplog.records), "swallowed an unexpected error"
