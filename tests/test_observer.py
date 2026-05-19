"""Tests for aipager.bot.observer.ObserverBroadcaster — read-only notifications.

The observer broadcaster wraps secondary ``telegram.Bot`` instances and is
expected to *never* propagate errors from those bots to the caller.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.bot.observer import ObserverBroadcaster


@pytest.fixture
def fake_bot_cls(monkeypatch):
    """Stub `telegram.Bot` so no network is touched."""
    instances = []

    class _FakeBot:
        def __init__(self, token):
            self.token = token
            self.initialize = AsyncMock()
            self.shutdown = AsyncMock()
            self.send_message = AsyncMock()
            self.send_document = AsyncMock()
            instances.append(self)

    monkeypatch.setattr("aipager.bot.observer.Bot", _FakeBot)
    return instances


# ---- start / stop lifecycle ---------------------------------------------

def test_start_initializes_each_bot(fake_bot_cls, run_async):
    b = ObserverBroadcaster([("tok1", "chat1"), ("tok2", "chat2")])
    run_async(b.start())
    assert len(b._bots) == 2
    assert {bot.token for bot, _ in b._bots} == {"tok1", "tok2"}
    for bot in fake_bot_cls:
        bot.initialize.assert_awaited_once()


def test_start_swallows_bot_init_failure(fake_bot_cls, run_async, caplog):
    # First bot.initialize raises; second succeeds. Broadcaster keeps the
    # working one.
    b = ObserverBroadcaster([("tok-bad", "chat-bad"), ("tok-ok", "chat-ok")])
    fake_bot_cls.clear()

    real_init = ObserverBroadcaster.start

    async def _start_with_first_failing(self):
        # Replace the real init pattern with custom error injection
        await real_init.__wrapped__(self) if hasattr(real_init, "__wrapped__") else None

    # Patch the first fake bot's initialize to raise
    with patch("aipager.bot.observer.Bot") as bot_cls:
        ok_bot = MagicMock()
        ok_bot.initialize = AsyncMock()
        bad_bot = MagicMock()
        bad_bot.initialize = AsyncMock(side_effect=ConnectionError("no"))
        bot_cls.side_effect = [bad_bot, ok_bot]
        run_async(b.start())

    assert len(b._bots) == 1


def test_start_with_empty_config_keeps_bots_empty(fake_bot_cls, run_async):
    b = ObserverBroadcaster([])
    run_async(b.start())
    assert b._bots == []


def test_stop_shuts_down_each_and_clears(fake_bot_cls, run_async):
    b = ObserverBroadcaster([("t1", "c1")])
    run_async(b.start())
    bot_instance = fake_bot_cls[0]
    run_async(b.stop())
    bot_instance.shutdown.assert_awaited_once()
    assert b._bots == []


def test_stop_swallows_shutdown_failure(fake_bot_cls, run_async):
    b = ObserverBroadcaster([("t1", "c1")])
    run_async(b.start())
    fake_bot_cls[0].shutdown = AsyncMock(side_effect=OSError("conn closed"))
    # MUST NOT raise
    run_async(b.stop())
    assert b._bots == []


# ---- broadcast (text) ---------------------------------------------------

def test_broadcast_sends_to_every_bot(fake_bot_cls, run_async):
    b = ObserverBroadcaster([("t1", "c1"), ("t2", "c2")])
    run_async(b.start())
    run_async(b.broadcast("hello"))
    for bot in fake_bot_cls:
        bot.send_message.assert_awaited_once()


def test_broadcast_swallows_per_bot_failure(fake_bot_cls, run_async):
    b = ObserverBroadcaster([("t1", "c1"), ("t2", "c2")])
    run_async(b.start())
    fake_bot_cls[0].send_message = AsyncMock(side_effect=RuntimeError("oops"))
    # MUST NOT raise — second bot still gets the message
    run_async(b.broadcast("hi"))
    fake_bot_cls[1].send_message.assert_awaited_once()


def test_broadcast_with_no_bots_is_noop(run_async):
    b = ObserverBroadcaster([])
    # No start() called → no bots. Should not raise.
    run_async(b.broadcast("hi"))


def test_broadcast_uses_html_parse_mode_by_default(fake_bot_cls, run_async):
    b = ObserverBroadcaster([("t1", "c1")])
    run_async(b.start())
    run_async(b.broadcast("hi"))
    call = fake_bot_cls[0].send_message.await_args
    # signature: send_message(chat_id, text, parse_mode=...)
    assert call.kwargs.get("parse_mode") == "HTML"


# ---- broadcast_document --------------------------------------------------

def test_broadcast_document_sends_msg_then_doc(fake_bot_cls, run_async):
    b = ObserverBroadcaster([("t1", "c1")])
    run_async(b.start())
    run_async(b.broadcast_document("summary", b"payload", "out.txt"))
    fake_bot_cls[0].send_message.assert_awaited_once()
    fake_bot_cls[0].send_document.assert_awaited_once()


def test_broadcast_document_swallows_failure(fake_bot_cls, run_async):
    b = ObserverBroadcaster([("t1", "c1")])
    run_async(b.start())
    fake_bot_cls[0].send_document = AsyncMock(side_effect=OSError("io"))
    # MUST NOT raise
    run_async(b.broadcast_document("hi", b"x", "x.bin"))
