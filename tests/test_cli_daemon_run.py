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


def _patch_components(monkeypatch, *, with_observers=False, with_miniapp=None):
    """Common mocks for _run_daemon's collaborators. Returns the patches.

    ``with_miniapp``: None → MINIAPP_ENABLED patched False;
    a MagicMock → MINIAPP_ENABLED=True and MiniAppServer(...) returns
    it; the string "unavailable" → MINIAPP_ENABLED=True and
    MiniAppServer.start() raises MiniAppUnavailable.
    """
    bot = MagicMock()
    bot.start = AsyncMock()
    bot.stop = AsyncMock()
    bot.notify = AsyncMock()
    bot.recover_sessions = AsyncMock()
    bot.reload_team = AsyncMock()
    bot.observers = None
    bot._update_bot_commands = AsyncMock()
    bot.publish_miniapp_button = AsyncMock()

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

    miniapp_server = None
    if with_miniapp is not None:
        monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
        monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
        # Pinned so the launch button's URL never depends on whatever
        # tunnel the machine running the tests happens to have configured.
        monkeypatch.setattr(
            "aipager.config.MINIAPP_PUBLIC_URL", "https://test.example/")
        from aipager.miniapp.server import MiniAppUnavailable
        if with_miniapp == "unavailable":
            miniapp_server = MagicMock()
            miniapp_server.start = AsyncMock(
                side_effect=MiniAppUnavailable("aipager[miniapp] not installed"))
            miniapp_server.stop = AsyncMock()
        elif with_miniapp == "port_in_use":
            miniapp_server = MagicMock()
            miniapp_server.start = AsyncMock(
                side_effect=OSError(98, "Address already in use"))
            miniapp_server.stop = AsyncMock()
        else:
            miniapp_server = MagicMock()
            miniapp_server.start = AsyncMock()
            miniapp_server.stop = AsyncMock()
        monkeypatch.setattr(
            "aipager.miniapp.server.MiniAppServer",
            lambda bot_, r, port: miniapp_server,
        )
    else:
        monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)

    # Make stop.wait() return instantly
    real_event = asyncio.Event
    def _fake_event():
        ev = real_event()
        ev.set()  # immediately resolved → wait() returns instantly
        return ev
    monkeypatch.setattr("aipager.cli.daemon.asyncio.Event", _fake_event)

    return bot, hook_receiver, session_monitor, registry, observers, miniapp_server


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
    bot, hook, monitor, registry, _, _ = _patch_components(monkeypatch)

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


def test_run_daemon_fires_startup_notice_after_bot_start(monkeypatch):
    """The provenance notice is fire-and-forget, scheduled only AFTER
    bot.start() — bootstrap_claude_settings() itself runs before
    bot.start() and must never send Telegram on its own."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, hook, monitor, registry, _, _ = _patch_components(monkeypatch)

    from aipager.claude_bootstrap import PendingAuthCheck, ProvenanceInfo
    # Only an unusable-auth start speaks at all now, so the ordering this
    # test pins is only reachable with a notice present.
    provenance = ProvenanceInfo(
        lines=["claude: /x/claude (2.1.235) · auth: none (not logged in)"],
        auth_ok=False,
        pending=PendingAuthCheck("/x/claude", "2.1.235", {}),
    )
    monkeypatch.setattr("aipager.claude_bootstrap.bootstrap_claude_settings",
                        lambda: provenance)
    monkeypatch.setattr("aipager.claude_bootstrap.recover_auth_or_notice",
                        lambda p: "\u26a0\ufe0f needs login")

    order = []
    real_bot_start = bot.start

    async def _tracked_start():
        order.append("bot.start")
        await real_bot_start()

    async def _tracked_notice(text):
        order.append("send_startup_notice")
        # The sanitized notice is what goes out — never provenance.lines.
        assert text == "\u26a0\ufe0f needs login"

    bot.start = _tracked_start
    bot.send_startup_notice = _tracked_notice

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))
    # Drain the fire-and-forget auth-check task properly. A bare
    # `sleep(0)` used to be enough, but that task now hands the sweep to
    # a real OS thread via asyncio.to_thread, so a single loop tick races
    # it — and losing that race would silently drop "send_startup_notice"
    # from `order`, i.e. pass or fail for reasons unrelated to the
    # ordering this test is about.
    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

    assert order == ["bot.start", "send_startup_notice"]


def test_run_daemon_never_refuses_to_launch_when_auth_is_absent(monkeypatch):
    """criterion 10 / the non-negotiable: `logged_in=False` must never
    stop the daemon from completing startup."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, hook, monitor, registry, _, _ = _patch_components(monkeypatch)
    bot.send_startup_notice = AsyncMock()

    from aipager.claude_bootstrap import PendingAuthCheck, ProvenanceInfo
    # Spelled out in full: `lines` saying "not logged in" while the auth
    # fields defaulted to "healthy, stay silent" was a self-contradiction
    # waiting to become a false pass, so those fields now have no
    # defaults at all.
    monkeypatch.setattr(
        "aipager.claude_bootstrap.bootstrap_claude_settings",
        lambda: ProvenanceInfo(
            lines=["claude: /x/claude (2.1.235) · auth: none (not logged in)"],
            auth_ok=False,
            pending=PendingAuthCheck("/x/claude", "2.1.235", {}),
        ),
    )
    monkeypatch.setattr(
        "aipager.claude_bootstrap.recover_auth_or_notice", lambda p: None)

    loop = asyncio.new_event_loop()
    # Must not raise SystemExit or any other exception.
    loop.run_until_complete(daemon._run_daemon("bot_username"))

    bot.start.assert_awaited_once()
    monitor.start.assert_awaited_once()
    registry.save.assert_called_once()


def test_run_daemon_no_notice_task_when_provenance_is_none(monkeypatch):
    """Resolution failure (no claude binary at all) must not crash
    startup or schedule a notice with nothing to say."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, hook, monitor, registry, _, _ = _patch_components(monkeypatch)
    monkeypatch.setattr("aipager.claude_bootstrap.bootstrap_claude_settings",
                        lambda: None)
    # If send_startup_notice were (wrongly) called, a bare MagicMock
    # attribute access would still succeed but produce a non-awaitable —
    # leaving this unset makes any accidental call fail loudly instead
    # of silently no-op-ing.

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))
    bot.start.assert_awaited_once()


def test_run_daemon_with_observers_starts_and_stops_them(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS",
                        [("obs_tok", "obs_chat")])
    bot, hook, monitor, registry, observers, _ = _patch_components(
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


# ===== Mini App server lifecycle (opt-in, off by default) ===============

def test_run_daemon_miniapp_disabled_never_constructed(monkeypatch):
    """MINIAPP_ENABLED unset/false → no MiniAppServer is ever built, so a
    base install (no `miniapp` extra) never even attempts the import."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    _patch_components(monkeypatch)  # with_miniapp=None → MINIAPP_ENABLED=False

    called = MagicMock()
    monkeypatch.setattr(
        "aipager.miniapp.server.MiniAppServer",
        lambda *a, **k: called() or MagicMock(),
    )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))
    called.assert_not_called()


def test_run_daemon_miniapp_enabled_starts_last_stops_first(monkeypatch):
    """Design.md ordering: the Mini App server is the newest/highest-risk
    component, so it starts after everything else and stops before
    session_monitor (the first teardown step)."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, hook, monitor, registry, _, miniapp = _patch_components(
        monkeypatch, with_miniapp=True,
    )

    order = []
    monitor.start.side_effect = lambda: order.append("monitor.start")
    miniapp.start.side_effect = lambda: order.append("miniapp.start")
    miniapp.stop.side_effect = lambda: order.append("miniapp.stop")
    monitor.stop.side_effect = lambda: order.append("monitor.stop")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))

    miniapp.start.assert_awaited_once()
    miniapp.stop.assert_awaited_once()
    assert order.index("monitor.start") < order.index("miniapp.start")
    assert order.index("miniapp.stop") < order.index("monitor.stop")


def test_run_daemon_miniapp_unavailable_does_not_crash_daemon(monkeypatch, caplog):
    """aiohttp missing (extra not installed) → MiniAppServer.start() raises
    MiniAppUnavailable; the daemon logs a friendly warning and keeps
    every other component running instead of crashing."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, hook, monitor, registry, _, miniapp = _patch_components(
        monkeypatch, with_miniapp="unavailable",
    )

    loop = asyncio.new_event_loop()
    with caplog.at_level("WARNING"):
        loop.run_until_complete(daemon._run_daemon("bot_username"))

    miniapp.start.assert_awaited_once()
    # Never stopped — start() failed, so there's nothing to stop.
    miniapp.stop.assert_not_awaited()
    bot.start.assert_awaited_once()
    bot.stop.assert_awaited_once()
    assert any("Mini App" in r.getMessage() for r in caplog.records)


def test_run_daemon_miniapp_port_in_use_does_not_crash_daemon(monkeypatch, caplog):
    """rev-iter1-001: a non-MiniAppUnavailable startup failure (most
    plausibly OSError/EADDRINUSE from a second `aipager start`, or an
    unrelated local service already bound to MINIAPP_PORT) must not
    propagate out of _run_daemon and take the bot, hook receiver,
    session monitor, and observers down with it — the Mini App is
    opt-in and must never take the rest of the daemon down."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, hook, monitor, registry, _, miniapp = _patch_components(
        monkeypatch, with_miniapp="port_in_use",
    )

    loop = asyncio.new_event_loop()
    with caplog.at_level("WARNING"):
        # Must not raise — this is exactly the bug: previously an OSError
        # here propagated out of the un-wrapped asyncio.run(...) in
        # _cmd_start and crashed the whole daemon before stop.wait() was
        # even reached.
        loop.run_until_complete(daemon._run_daemon("bot_username"))

    miniapp.start.assert_awaited_once()
    miniapp.stop.assert_not_awaited()
    # Every other component still came up AND shut down cleanly.
    bot.start.assert_awaited_once()
    hook.start.assert_awaited_once()
    monitor.start.assert_awaited_once()
    bot.recover_sessions.assert_awaited_once()
    monitor.stop.assert_called_once()
    hook.stop.assert_called_once()
    bot.stop.assert_awaited_once()
    registry.save.assert_called_once()
    assert any("Mini App" in r.getMessage() for r in caplog.records)
    # …and no launch button is published for a server that is not
    # listening. An empty URL clears whatever a previous run left, so a
    # Mini App that failed to start does not leave a button that opens a
    # broken page and survives every restart.
    bot.publish_miniapp_button.assert_awaited_once_with("")


def test_run_daemon_publishes_no_button_when_miniapp_is_disabled(monkeypatch):
    """Same rule for the ordinary "never enabled" case — the empty URL
    still goes out, so disabling the Mini App really does remove a button
    an earlier run published."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, _hook, _monitor, _registry, _, _miniapp = _patch_components(
        monkeypatch, with_miniapp=None,
    )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))

    bot.publish_miniapp_button.assert_awaited_once_with("")


def test_run_daemon_publishes_the_button_once_the_server_is_up(monkeypatch):
    """The button exists iff the server it points at is listening."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, _hook, _monitor, _registry, _, miniapp = _patch_components(
        monkeypatch, with_miniapp=True,
    )

    async def _url():
        return "https://tunnel.example/"

    monkeypatch.setattr("aipager.miniapp.tunnel.resolve_public_url", _url)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))

    miniapp.start.assert_awaited_once()
    bot.publish_miniapp_button.assert_awaited_once_with("https://tunnel.example/")


def test_run_daemon_primes_the_url_before_the_bot_starts(monkeypatch):
    """rev-iter1-001. `bot.start()` ends by sending the first persistent
    keyboard. If the Mini App URL is not known by then, that keyboard has
    no App button and nothing re-sends it — the feature is silently
    missing from the surface the operator looks at most, on every
    restart."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, _hook, _monitor, _registry, _, _miniapp = _patch_components(
        monkeypatch, with_miniapp=True,
    )

    order = []
    bot.prime_miniapp_url = MagicMock(
        side_effect=lambda url: order.append(("prime", url)))
    bot.start = AsyncMock(side_effect=lambda: order.append(("start", None)))

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))

    assert [step for step, _ in order] == ["prime", "start"], (
        f"the URL must be known before start() sends the keyboard: {order}"
    )
    assert order[0][1] == "https://test.example/"


def test_run_daemon_primes_nothing_when_the_miniapp_is_off(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, _hook, _monitor, _registry, _, _miniapp = _patch_components(
        monkeypatch, with_miniapp=None,
    )

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))

    bot.prime_miniapp_url.assert_called_once_with("")


# ===== Managed tunnel (TunnelManager) wiring =============================

def _patch_tunnel_manager_class(monkeypatch):
    """Stub out aipager.miniapp.tunnel_manager.TunnelManager itself (not
    its internals — that's tests/test_tunnel_manager.py's job). Returns
    (constructor_calls, manager_double) so a test can assert on the
    (port, on_url_change) it was built with and on start()/stop() call
    order relative to the rest of _run_daemon."""
    manager = MagicMock()
    manager.start = AsyncMock()
    manager.stop = AsyncMock()
    calls = []

    def _fake_ctor(port, on_url_change):
        calls.append((port, on_url_change))
        return manager

    monkeypatch.setattr(
        "aipager.miniapp.tunnel_manager.TunnelManager", _fake_ctor)
    return calls, manager


def test_run_daemon_constructs_and_starts_the_tunnel_manager_with_no_override(
    monkeypatch,
):
    """design.md: TunnelManager is only ever constructed when
    MINIAPP_ENABLED, MINIAPP_PUBLIC_URL is empty, AND the server actually
    started."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, _hook, _monitor, _registry, _, miniapp = _patch_components(
        monkeypatch, with_miniapp=True,
    )
    # _patch_components(with_miniapp=True) pins a public_url override —
    # clear it so the managed-tunnel gate actually opens for this test.
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    calls, manager = _patch_tunnel_manager_class(monkeypatch)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))
    loop.close()

    assert len(calls) == 1
    port, on_url_change = calls[0]
    assert port == 8765
    assert on_url_change == bot.publish_miniapp_button
    manager.start.assert_awaited_once()
    manager.stop.assert_awaited_once()


def test_run_daemon_never_constructs_a_tunnel_manager_when_url_is_overridden(
    monkeypatch,
):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    # with_miniapp=True already pins MINIAPP_PUBLIC_URL to a non-empty
    # override — the gate must stay closed.
    _patch_components(monkeypatch, with_miniapp=True)
    calls, manager = _patch_tunnel_manager_class(monkeypatch)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))
    loop.close()

    assert calls == []
    manager.start.assert_not_awaited()
    manager.stop.assert_not_awaited()


def test_run_daemon_never_constructs_a_tunnel_manager_when_miniapp_disabled(
    monkeypatch,
):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    _patch_components(monkeypatch, with_miniapp=None)  # MINIAPP_ENABLED=False
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    calls, manager = _patch_tunnel_manager_class(monkeypatch)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))
    loop.close()

    assert calls == []


def test_run_daemon_never_constructs_a_tunnel_manager_when_the_server_failed(
    monkeypatch,
):
    """No override, Mini App enabled, but the server itself never came
    up (port in use) — no server means nothing for a tunnel to point
    at, so the manager must not be built either."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    _patch_components(monkeypatch, with_miniapp="port_in_use")
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    calls, manager = _patch_tunnel_manager_class(monkeypatch)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))
    loop.close()

    assert calls == []


def test_run_daemon_stops_the_tunnel_manager_before_the_miniapp_server(
    monkeypatch,
):
    """design.md shutdown ordering: stop accepting the world's traffic
    (the tunnel) before stopping what serves it (the loopback server)."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    _bot, _hook, monitor, _registry, _, miniapp = _patch_components(
        monkeypatch, with_miniapp=True,
    )
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    _calls, manager = _patch_tunnel_manager_class(monkeypatch)

    order = []
    manager.stop = AsyncMock(side_effect=lambda: order.append("manager.stop"))
    miniapp.stop = AsyncMock(side_effect=lambda: order.append("miniapp.stop"))
    monitor.stop.side_effect = lambda: order.append("monitor.stop")

    loop = asyncio.new_event_loop()
    loop.run_until_complete(daemon._run_daemon("bot_username"))
    loop.close()

    assert order.index("manager.stop") < order.index("miniapp.stop")
    assert order.index("miniapp.stop") < order.index("monitor.stop")
