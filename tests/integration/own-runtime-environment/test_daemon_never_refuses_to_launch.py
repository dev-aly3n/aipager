"""design.md success criterion 10:

    "With logged_in=False, _run_daemon() still completes startup and
    the process does not exit -- 'never refuse to launch' at the one
    integration point that matters."

The task brief is explicit that checking only the return value proves
nothing here, so this test additionally asserts that bot/hook-receiver/
session-monitor/recover_sessions all actually ran -- not just that the
coroutine returned without raising. It also drains the fire-and-forget
startup-notice task (asyncio.create_task never gets a scheduler turn
inside a synchronous run_until_complete unless something explicitly
lets it) and asserts the notice text names the auth-absent state by its
documented wording, tying this to criterion 9's "unknown"/"none"
distinction and criterion 5's provenance format at the one call site
that actually matters in production.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.cli import daemon as daemon_mod


_REAL_EVENT = asyncio.Event  # captured before any test patches asyncio.Event


def _make_stop_event_pre_set():
    ev = _REAL_EVENT()
    ev.set()
    return ev


def test_run_daemon_completes_and_starts_every_component_with_auth_absent(
    tmp_path, monkeypatch, claude_fixture_factory, real_candidate_paths,
    isolate_from_real_local_bin_claude,
):
    import aipager.claude_resolve as claude_resolve_mod
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)
    monkeypatch.setenv("PATH", str(tmp_path / "empty_path_dir"))

    # A REAL, resolvable claude that reports "not logged in" -- this is
    # the "auth absent" case criterion 10 names, not "no binary found"
    # (a weaker scenario the daemon must ALSO tolerate, but not the one
    # this criterion is about).
    fixture_path = claude_fixture_factory(
        version="2.1.235 (Claude Code)", logged_in=False, auth_method="none",
    )
    monkeypatch.setenv("AIPAGER_CLAUDE_BIN", fixture_path)

    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)

    bot = MagicMock()
    bot.start = AsyncMock()
    bot.stop = AsyncMock()
    bot.notify = AsyncMock()
    bot.recover_sessions = AsyncMock()
    bot.reload_team = AsyncMock()
    bot.observers = None
    bot._update_bot_commands = AsyncMock()
    bot.publish_miniapp_button = AsyncMock()
    bot.send_startup_notice = AsyncMock()

    hook_receiver = MagicMock()
    hook_receiver.start = AsyncMock()
    hook_receiver.stop = MagicMock()

    session_monitor = MagicMock()
    session_monitor.start = AsyncMock()
    session_monitor.stop = MagicMock()
    session_monitor.on_sessions_changed = None

    registry = MagicMock()
    registry.load = MagicMock()
    registry.save = MagicMock()

    monkeypatch.setattr("aipager.bot.TelegramBot", lambda r: bot)
    monkeypatch.setattr(
        "aipager.dtach.hook_receiver.HookReceiver", lambda r, n: hook_receiver)
    monkeypatch.setattr(
        "aipager.session_monitor.SessionMonitor", lambda r, n: session_monitor)
    monkeypatch.setattr("aipager.state.SessionRegistry", lambda: registry)

    # Make stop.wait() resolve instantly instead of blocking on a real
    # SIGINT/SIGTERM -- the established pattern in this suite for
    # driving _run_daemon() to completion (see tests/test_cli_daemon_run.py).
    monkeypatch.setattr(
        daemon_mod.asyncio, "Event", lambda: _make_stop_event_pre_set())

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(daemon_mod._run_daemon("test_bot"))
        # Give the fire-and-forget startup-notice task (asyncio.create_
        # task, never awaited directly by _run_daemon) a scheduler turn
        # to actually run -- otherwise it may never execute at all
        # inside a synchronous run_until_complete that has no other
        # true suspension point.
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    finally:
        loop.close()

    # The actual point of criterion 10: not just "no exception", but
    # every other component genuinely started.
    bot.start.assert_awaited_once()
    hook_receiver.start.assert_awaited_once()
    session_monitor.start.assert_awaited_once()
    bot.recover_sessions.assert_awaited_once()

    bot.send_startup_notice.assert_awaited_once()
    (notice_text,), _ = bot.send_startup_notice.await_args
    assert "isn't logged in" in notice_text, notice_text
    assert "claude auth login" in notice_text, notice_text
    # The notice used to carry format_provenance()'s journal line, which
    # names the resolved binary's absolute path. It must not: this text
    # goes to every configured scope's chat, i.e. every member of every
    # team group, and the path leaks the home directory and OS username.
    assert fixture_path not in notice_text, notice_text
    assert "/home/" not in notice_text, notice_text


def test_run_daemon_exits_cleanly_when_no_token_configured(monkeypatch):
    """Control case: the ONE documented reason _run_daemon legitimately
    exits (misconfiguration, not an auth/binary problem) still behaves
    as before -- distinguishes "never refuse to launch on auth" from
    "never exits under any circumstance whatsoever".
    """
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "")

    with pytest.raises(SystemExit) as exc:
        asyncio.new_event_loop().run_until_complete(
            daemon_mod._run_daemon("test_bot"))
    assert exc.value.code == 1
