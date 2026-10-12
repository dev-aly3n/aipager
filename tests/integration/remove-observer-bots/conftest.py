"""Fixtures for the black-box "remove observer bots" tests.

The daemon boot path ``aipager.cli.daemon._run_daemon`` is driven with
every collaborator stubbed (the seam ``tests/test_cli_daemon_run.py``
uses): no real Telegram, no hook socket, no session monitor thread. The
stop event is already set, so the daemon starts and stops at once.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _stub_components(monkeypatch):
    bot = MagicMock()
    for name in ("start", "stop", "notify", "recover_sessions", "reload_team",
                 "_update_bot_commands", "publish_miniapp_button"):
        setattr(bot, name, AsyncMock())

    hook = MagicMock()
    hook.start = AsyncMock()
    hook.stop = MagicMock()

    monitor = MagicMock()
    monitor.start = AsyncMock()
    monitor.stop = MagicMock()
    monitor.on_sessions_changed = None

    registry = MagicMock()

    monkeypatch.setattr("aipager.bot.TelegramBot", lambda r: bot)
    monkeypatch.setattr("aipager.dtach.hook_receiver.HookReceiver",
                        lambda r, n: hook)
    monkeypatch.setattr("aipager.session_monitor.SessionMonitor",
                        lambda r, n: monitor)
    monkeypatch.setattr("aipager.state.SessionRegistry", lambda: registry)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)

    real_event = asyncio.Event

    def _done_event():
        ev = real_event()
        ev.set()
        return ev

    monkeypatch.setattr("aipager.cli.daemon._new_stop_event", _done_event)
    return SimpleNamespace(bot=bot, hook=hook, monitor=monitor)


@pytest.fixture
def run_daemon(monkeypatch, caplog):
    """``run_daemon(value)`` runs one daemon start with ``OBSERVER_BOTS``
    set to ``value`` (``None``: unset) and returns the stubs plus the
    outcome. Records of every level are captured."""
    from aipager.cli import daemon

    def _run(value):
        if value is None:
            monkeypatch.delenv("OBSERVER_BOTS", raising=False)
        else:
            monkeypatch.setenv("OBSERVER_BOTS", value)
        stubs = _stub_components(monkeypatch)
        stubs.error = None
        loop = asyncio.new_event_loop()
        try:
            with caplog.at_level(logging.DEBUG):
                loop.run_until_complete(daemon._run_daemon("bot_username"))
        except BaseException as exc:  # SystemExit included
            stubs.error = exc
        finally:
            loop.close()
        return stubs

    return _run

