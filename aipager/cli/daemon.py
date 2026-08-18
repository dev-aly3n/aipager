"""Daemon-startup paths for `aipager start`.

Split out of the original ``aipager.cli`` so the argparse setup and
the daemon lifecycle live in separate files. Four concerns:

- ``_check_existing_daemon`` — the fast, informative front door:
  probes the Unix socket and refuses to start if a live listener
  responds. Handles the common case cleanly and gives a friendly
  "already running, stop it first" error.
- ``_acquire_daemon_lock`` — the airtight backstop: fcntl advisory
  lock on ``~/.local/share/aipager/daemon.lock``, held for the
  daemon's lifetime. Closes the socket-probe race window (two
  ``aipager start`` invocations fired within milliseconds could
  both pass the probe and both proceed, with the second's
  HookReceiver silently stealing the socket from the first).
  fcntl locks auto-release on any process exit including crash.
- ``_telegram_preflight`` — short circuit before async boot if the
  bot token or chat-id is misconfigured (catches the common errors
  during install / token rotation).
- ``_run_daemon`` — the actual async boot that wires up the bot,
  the hook receiver, the session monitor, (optionally) observer
  broadcasters, and (optionally, opt-in) the loopback Mini App server.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import signal
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("aipager")

# Held-for-lifetime file descriptor for the daemon's advisory lock.
# Module-level so garbage collection can't close it and silently
# release the lock while the daemon is still running.
_daemon_lock_fd: int | None = None


def _check_existing_daemon() -> None:
    """If /tmp/aipager.sock exists, decide whether to abort or clean up.

    - Live daemon (sendto succeeds) → exit 1 with friendly "already running".
    - Stale socket (ConnectionRefusedError) → leave for HookReceiver to unlink.
    - Permission / other OSError → warn but don't abort.
    """
    from aipager.config import SOCKET_PATH
    from aipager.errors import friendly_error, friendly_warn
    p = Path(SOCKET_PATH)
    if not p.exists():
        return
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        s.sendto(b'{"event":"_startup_probe"}', SOCKET_PATH)
    except ConnectionRefusedError:
        # Old socket file with no live daemon — HookReceiver.start() unlinks.
        return
    except OSError as e:
        friendly_warn(
            f"Could not probe existing socket at {SOCKET_PATH}: {e}",
            "  Continuing — if the daemon fails to bind, kill the stale process.",
        )
        return
    finally:
        s.close()
    friendly_error(
        "Another aipager daemon already owns the socket.",
        "",
        f"  Found a responsive listener at {SOCKET_PATH}.",
        "  Stop it before starting a new one:",
        "",
        "      aipager service stop       # if installed as a service",
        "      pkill -f 'aipager start'   # otherwise",
        "",
    )
    sys.exit(1)


def _acquire_daemon_lock() -> None:
    """Acquire an exclusive fcntl lock on the daemon lockfile.

    Closes the socket-probe race in :func:`_check_existing_daemon`:
    when two ``aipager start`` invocations arrive within
    milliseconds, both can pass the probe simultaneously. The
    second one's ``HookReceiver.start()`` then silently unlinks and
    rebinds the socket, stealing hook delivery from the first
    daemon which continues running with an orphaned socket handle.
    fcntl advisory locks are atomic and process-associated (auto-
    released on any process exit including SIGKILL / segfault), so
    exactly one holder is guaranteed regardless of race timing.

    Stores the fd on a module-level global (``_daemon_lock_fd``)
    for the daemon's lifetime — closing the fd releases the lock,
    so a function-local would silently drop the guard once this
    function returned.
    """
    from aipager.errors import friendly_error
    global _daemon_lock_fd
    lock_path = (
        Path.home() / ".local" / "share" / "aipager" / "daemon.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Someone else holds the lock. Read whatever PID they wrote
        # so the user can find the offending process.
        try:
            with os.fdopen(fd, "r") as f:
                other_pid = f.read().strip() or "?"
        except OSError:
            other_pid = "?"
        friendly_error(
            f"aipager is already running (pid={other_pid}).",
            "",
            f"  Lockfile: {lock_path}",
            "  Stop the running daemon first:",
            "",
            "      aipager service stop       # if installed as a service",
            "      pkill -f 'aipager start'   # otherwise",
            "",
        )
        sys.exit(1)
    # Locked. Record our PID for humans; keep fd open for lifetime.
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _daemon_lock_fd = fd


def _telegram_preflight() -> str:
    """Verify the bot token and chat id reach Telegram. Returns the bot's
    username. Exits with code 2 (misconfiguration) on user-fixable failures
    so wrappers can distinguish setup problems from crashes.
    """
    from aipager.config import BOT_TOKEN, CHAT_ID
    from aipager.errors import friendly_error

    def _call(url: str) -> tuple[dict | None, int | None, str]:
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.load(r), r.status, ""
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except Exception:
                body = {"description": str(e)}
            return body, e.code, body.get("description", "")
        except urllib.error.URLError as e:
            return None, None, f"network: {e.reason}"
        except (OSError, json.JSONDecodeError) as e:
            return None, None, str(e)

    body, code, err = _call(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
    if code == 401:
        friendly_error(
            "Telegram rejected the bot token (HTTP 401).",
            "",
            "  The token in ~/.config/aipager/config.env is no longer valid.",
            "  Generate a fresh one in @BotFather and re-run:",
            "",
            "      aipager config",
            "",
        )
        sys.exit(2)
    if code and code >= 500:
        friendly_error(
            f"Telegram API error (HTTP {code}).",
            "  Probably transient — wait a moment and retry `aipager start`.",
        )
        sys.exit(1)
    if not body or not body.get("ok"):
        friendly_error(
            "Could not reach api.telegram.org.",
            "",
            f"  Detail: {err or 'unknown'}",
            "",
            "  Check your network, then retry `aipager start`.",
        )
        sys.exit(1)
    username = body["result"].get("username", "")

    body, code, err = _call(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id={CHAT_ID}"
    )
    if err and "chat not found" in err.lower():
        friendly_error(
            "Telegram knows the bot but cannot reach your chat.",
            "",
            f"  Chat id: {CHAT_ID}",
            f"  Bot:     @{username}",
            "",
            "  Telegram refuses bot→user messages until you DM the bot once.",
            "",
            f"  1. Open https://t.me/{username}",
            "  2. Tap Start (or send any message)",
            "  3. Re-run `aipager start`",
            "",
        )
        sys.exit(2)
    if not body or not body.get("ok"):
        # Non-fatal — getChat fail can be transient and the daemon will retry.
        log.warning("getChat returned %s — daemon may have trouble sending: %s",
                    code, err)
    return username


async def _run_daemon(bot_username: str) -> None:
    """Boot the daemon and run until SIGINT/SIGTERM."""
    from aipager.config import (
        BOT_TOKEN, CHAT_ID, MINIAPP_ENABLED, MINIAPP_PORT, MINIAPP_PUBLIC_URL,
        OBSERVER_BOTS,
    )
    from aipager.dtach.hook_receiver import HookReceiver
    from aipager.bot.observer import ObserverBroadcaster
    from aipager.session_monitor import SessionMonitor
    from aipager.state import SessionRegistry
    from aipager.bot import TelegramBot
    from aipager.claude_bootstrap import bootstrap_claude_settings

    if not BOT_TOKEN:
        log.error("CLAUDE_TG_BOT_TOKEN not set — run `aipager config` first")
        sys.exit(1)

    # Write claude-code's first-run acceptance flags so dtach-launched
    # sessions never block on dialogs a Telegram-only user can't dismiss.
    bootstrap_claude_settings()

    log.info("connected as @%s, will message chat %s", bot_username, CHAT_ID)

    registry = SessionRegistry()
    registry.load()
    bot = TelegramBot(registry)
    hook_receiver = HookReceiver(registry, bot.notify)
    session_monitor = SessionMonitor(registry, bot.notify)

    # Resolved BEFORE bot.start(), because start() ends by sending the
    # first persistent keyboard and that keyboard has to know whether
    # there is a Mini App to offer. No Telegram call happens here — the
    # menu button still waits until the server below is confirmed up.
    from aipager.miniapp.tunnel import resolve_public_url
    miniapp_url = await resolve_public_url() if MINIAPP_ENABLED else ""
    bot.prime_miniapp_url(miniapp_url)

    await bot.start()
    observers = None
    if OBSERVER_BOTS:
        observers = ObserverBroadcaster(OBSERVER_BOTS)
        await observers.start()
        bot.observers = observers
    await hook_receiver.start()
    await bot.recover_sessions()
    session_monitor.on_sessions_changed = bot._update_bot_commands
    await session_monitor.start()

    # Mini App server — newest and highest-risk component, so it starts
    # last and (below) stops first. Behind a lazy import so a base
    # install with no `miniapp` extra never imports aiohttp; a
    # MiniAppUnavailable (extra not installed) logs a friendly one-liner
    # instead of taking the whole daemon down.
    miniapp_server = None
    if MINIAPP_ENABLED:
        from aipager.miniapp.server import MiniAppServer, MiniAppUnavailable
        miniapp_server = MiniAppServer(bot, registry, MINIAPP_PORT)
        try:
            await miniapp_server.start()
        except MiniAppUnavailable as e:
            log.warning("Mini App server not started: %s", e)
            miniapp_server = None
        except OSError as e:
            # Most plausibly EADDRINUSE — another `aipager start`, or an
            # unrelated local service, already bound MINIAPP_PORT. The
            # Mini App is opt-in and must never take the rest of the
            # daemon (bot, hooks, session monitor) down with it.
            log.warning(
                "Mini App server not started — port %d unavailable: %s",
                MINIAPP_PORT, e,
            )
            miniapp_server = None

    # The managed cloudflared tunnel — only ever constructed when the
    # Mini App is on, nothing has overridden the URL, and the server it
    # would point at is actually listening. An override or a disabled
    # Mini App means this object never exists: no spawn, no binary
    # fetch, no cache write, nothing to race with the probe guard above.
    # Lazy import matches this file's existing discipline (resolve_public_url,
    # MiniAppServer just above) so a base install never imports this either.
    manager = None
    if MINIAPP_ENABLED and not MINIAPP_PUBLIC_URL and miniapp_server is not None:
        from aipager.miniapp.tunnel_manager import TunnelManager
        manager = TunnelManager(MINIAPP_PORT, bot.publish_miniapp_button)
        await manager.start()

    # The permanent launch button, published only once the server it
    # points at is actually listening — a button that opens a dead port
    # is worse than no button. An empty URL clears any button a previous
    # run left behind, so disabling the Mini App really does remove it.
    # `manager.start()` above is fire-and-forget (never awaits the first
    # URL), so for a pure managed-tunnel install this first publish call
    # runs with whatever resolve_public_url() returned earlier — "" —
    # and the button is simply absent until the tunnel's first URL
    # republishes it a few seconds later.
    await bot.publish_miniapp_button(
        miniapp_url if miniapp_server is not None else "",
    )

    log.info("AIPager running — all components started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # SIGUSR1 → live-reload of team.yaml. Sent by `aipager config` on
    # success so admins don't have to restart the daemon for every
    # add-user / role-change / deny-rule tweak.
    def _on_sigusr1() -> None:
        log.info("SIGUSR1 received — reloading team config")
        asyncio.create_task(bot.reload_team())
    try:
        loop.add_signal_handler(signal.SIGUSR1, _on_sigusr1)
    except (NotImplementedError, AttributeError):
        # Windows / unusual asyncio loop — skip; admins still have
        # the restart fallback.
        log.debug("SIGUSR1 handler not supported on this platform")

    await stop.wait()

    log.info("Shutting down...")
    registry.save()
    if manager is not None:
        # Before miniapp_server.stop(): stop accepting the world's
        # traffic (terminate the tunnel that publishes it) before
        # stopping what serves it.
        await manager.stop()
    if miniapp_server is not None:
        await miniapp_server.stop()
    session_monitor.stop()
    hook_receiver.stop()
    if observers:
        await observers.stop()
    await bot.stop()
    log.info("Goodbye")


def _cmd_start(args: argparse.Namespace) -> int:
    from aipager.preflight import require_config
    require_config()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx logs the full request URL (including the bot token) at INFO.
    # Suppress it here so neither the getUpdates polling loop nor the
    # sendRichMessage / sendRichMessageDraft calls leak the token into logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Generate the v2 config (aipager.yaml + policy.yaml seed) from the
    # current install if it doesn't exist yet. Phase A: this is
    # additive — the runtime still authorizes via CHAT_ID/TEAM, and the
    # v1 files are backed up but retained. Idempotent.
    try:
        from aipager.migrate import migrate_to_v2, retire_v1, upgrade_to_v3
        migrate_to_v2()
        # Carry any Mini App settings out of config.env BEFORE retiring it —
        # they used to live there, and retire_v1() is what silently turned
        # the Mini App off on the second restart.
        upgrade_to_v3()
        # Once v2 is the source of truth, retire the v1 files so they
        # can't drift. Guarded: only runs when aipager.yaml loads
        # cleanly with a token.
        retire_v1()
    except Exception:
        log.warning("v2 config migration skipped (non-fatal)", exc_info=True)
    _check_existing_daemon()   # fast socket-probe with friendly error
    _acquire_daemon_lock()     # airtight fcntl guard against startup races
    bot_username = _telegram_preflight()
    asyncio.run(_run_daemon(bot_username))
    return 0
