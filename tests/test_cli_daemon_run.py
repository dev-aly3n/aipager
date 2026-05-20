"""Tests for cli.daemon._run_daemon — the daemon main loop.

This is the hard path: starts the bot, hook receiver, session monitor,
optional observers, registers signal handlers, then blocks on
``stop.wait()`` until SIGINT/SIGTERM fires. We test it by stubbing every
component and faking the wait so the function returns synchronously.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.cli import daemon


def _make_event_done():
    """Return an asyncio.Event that's already 'set' so wait() returns instantly."""
    ev = asyncio.Event()
    ev.set()
    return ev


def _patch_components(monkeypatch, *, with_observers=False):
    """Common mocks for _run_daemon's collaborators. Returns the patches."""
    bot = MagicMock()
    bot.start = AsyncMock()
    bot.stop = AsyncMock()
    bot.notify = AsyncMock()
    bot.recover_sessions = AsyncMock()
    bot.reload_team = AsyncMock()
    bot.observers = None
    bot._update_bot_commands = AsyncMock()

    hook_receiver = MagicMock()
    hook_receiver.start = AsyncMock()
    hook_receiver.stop = MagicMock()  # plain (not async) per source

    session_monitor = MagicMock()
    session_monitor.start = AsyncMock()
    session_monitor.stop = MagicMock()
    session_monitor.on_sessions_changed = None

    registry = MagicMock()
    registry.load = MagicMock()
    registry.save = MagicMock()

    observers = None
    if with_observers:
        observers = MagicMock()
        observers.start = AsyncMock()
        observers.stop = AsyncMock()

    monkeypatch.setattr("aipager.bot.TelegramBot",
                        lambda r: bot)
    monkeypatch.setattr("aipager.dtach.hook_receiver.HookReceiver",
                        lambda r, n: hook_receiver)
    monkeypatch.setattr("aipager.session_monitor.SessionMonitor",
                        lambda r, n: session_monitor)
    monkeypatch.setattr("aipager.state.SessionRegistry",
                        lambda: registry)
    if observers:
        monkeypatch.setattr("aipager.bot.observer.ObserverBroadcaster",
                            lambda cfg: observers)

    # Make stop.wait() return instantly
    real_event = asyncio.Event
    def _fake_event():
        ev = real_event()
        ev.set()  # immediately resolved → wait() returns instantly
        return ev
    monkeypatch.setattr("aipager.cli.daemon.asyncio.Event", _fake_event)

    return bot, hook_receiver, session_monitor, registry, observers


def test_run_daemon_exits_when_no_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "")
    with pytest.raises(SystemExit) as exc:
        asyncio.new_event_loop().run_until_complete(
            daemon._run_daemon("bot_username"))
    assert exc.value.code == 1


def test_run_daemon_happy_path_personal_mode(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, hook, monitor, registry, _ = _patch_components(monkeypatch)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))

    # Components started in order
    bot.start.assert_awaited_once()
    hook.start.assert_awaited_once()
    monitor.start.assert_awaited_once()
    bot.recover_sessions.assert_awaited_once()
    # Components stopped in order on shutdown
    monitor.stop.assert_called_once()
    hook.stop.assert_called_once()
    bot.stop.assert_awaited_once()
    # State persisted
    registry.save.assert_called_once()


def test_run_daemon_with_observers_starts_and_stops_them(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS",
                        [("obs_tok", "obs_chat")])
    bot, hook, monitor, registry, observers = _patch_components(
        monkeypatch, with_observers=True,
    )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))

    observers.start.assert_awaited_once()
    observers.stop.assert_awaited_once()
    assert bot.observers is observers


def test_run_daemon_handles_sigusr1_not_supported(monkeypatch, caplog):
    """On Windows / unusual event loops, SIGUSR1 add_signal_handler raises.
    The daemon must keep booting."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    _patch_components(monkeypatch)

    # Make add_signal_handler for SIGUSR1 raise NotImplementedError
    real_loop = asyncio.new_event_loop()
    orig_add = real_loop.add_signal_handler
    def _selective(sig, *a):
        if sig == signal.SIGUSR1:
            raise NotImplementedError("Windows")
        return orig_add(sig, *a)
    real_loop.add_signal_handler = _selective
    asyncio.set_event_loop(real_loop)
    real_loop.run_until_complete(daemon._run_daemon("bot_username"))
    # No raise; daemon shut down cleanly
