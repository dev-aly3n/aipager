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
  the hook receiver, the session monitor, and (optionally, opt-in) the
  loopback Mini App server.
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

# The factory for the Event ``_run_daemon`` waits on until SIGINT/SIGTERM.
# A seam: tests replace THIS to hand back a pre-set event, never
# ``asyncio.Event`` (``daemon.asyncio`` IS the global module; CLAUDE.md).
_new_stop_event = asyncio.Event

# Held-for-lifetime file descriptor for the daemon's advisory lock.
# Module-level so garbage collection can't close it and silently
# release the lock while the daemon is still running.
_daemon_lock_fd: int | None = None


def _check_existing_daemon() -> None:
    """If the control socket exists, decide whether to abort or clean up.

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
            "  Continuing - if the daemon fails to bind, kill the stale process.",
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
    from aipager._test_guard import check_write
    from aipager.errors import friendly_error
    global _daemon_lock_fd
    lock_path = (
        Path.home() / ".local" / "share" / "aipager" / "daemon.lock"
    )
    check_write(lock_path)
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
    from aipager import telegram_endpoint
    from aipager.config import BOT_TOKEN, CHAT_ID
    from aipager.errors import friendly_error, redact_token

    def _call(url: str) -> tuple[dict | None, int | None, str]:
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.load(r), r.status, ""
        # Redacted: the preflight URL embeds the bot token and these
        # strings reach the terminal through `friendly_error`.
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except Exception:
                body = {"description": redact_token(str(e))}
            return body, e.code, redact_token(body.get("description", ""))
        except urllib.error.URLError as e:
            return None, None, redact_token(f"network: {e.reason}")
        except (OSError, json.JSONDecodeError) as e:
            return None, None, redact_token(str(e))

    body, code, err = _call(telegram_endpoint.method_url(BOT_TOKEN, "getMe"))
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
            "  Probably transient - wait a moment and retry `aipager start`.",
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
        f"{telegram_endpoint.method_url(BOT_TOKEN, 'getChat')}?chat_id={CHAT_ID}"
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


# Comfortably past the observed 10-30s cold-tunnel window (cloudflared
# start + DNS propagation + the probe's own retry), while still short
# enough that a broken tunnel does not leave someone staring at a
# keyboardless chat wondering if the daemon is up.
KEYBOARD_HOLD_SECONDS: float = 45.0


def _is_private_target() -> bool:
    """Is the configured chat a private chat (the startup hold's gate)?

    A positive Telegram chat id is a user (private chat); groups and
    channels are negative. The hold was for the keyboard's ``web_app``
    App button, private-chat-only, which the keyboard no longer has
    (2026-10-12).

    Single-``CHAT_ID`` by construction, which is not a meaningful notion
    in multi-scope mode where the real targets live in ``scopes``. That
    is latent rather than live: the hold is independently gated off in
    multi-scope mode (``_update_bot_commands`` only sends the startup
    keyboard when ``self.scopes is None``, and ``release_deferred_keyboard``
    returns early for the same reason). If the hold ever grows to cover
    multi-scope, this helper has to grow with it.
    """
    from aipager.config import CHAT_ID

    try:
        return int(CHAT_ID) > 0
    except (TypeError, ValueError):
        return False


async def _release_keyboard_after(bot, delay: float) -> None:
    """Send the held keyboard once *delay* has passed, if still held.

    Fire-and-forget, so it swallows everything: an escape here would
    surface only as "Task exception was never retrieved".
    """
    try:
        await asyncio.sleep(delay)
        await bot.release_deferred_keyboard()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.debug("deferred-keyboard release failed", exc_info=True)


async def _finish_auth_check(bot, provenance) -> None:
    """Resolve and deliver the startup auth warning, off the hot path.

    When ``provenance.pending`` is set, Claude reported itself logged
    out and the recovery sweep has NOT run yet — it is deferred to here
    because it can spend ~20s in blocking subprocesses, and doing that
    before ``bot.start()`` would delay remote control coming up in
    exactly the situation where the operator needs it most. Run in a
    worker thread so the event loop keeps serving Telegram meanwhile.

    Never raises: this is a fire-and-forget task, and an unhandled
    exception here would be swallowed by the loop and reported as a
    "Task exception was never retrieved" warning rather than anything
    actionable.
    """
    from aipager import claude_bootstrap

    try:
        notice = await asyncio.to_thread(
            claude_bootstrap.recover_auth_or_notice, provenance.pending)
        if notice is not None:
            await bot.send_startup_notice(notice)
    except Exception:
        log.debug("startup auth check failed", exc_info=True)


def _wire_parked_state(registry, hook_receiver, session_monitor, bot) -> None:
    """Claude Code background jobs: the session registry is the one source
    of truth for "parked". dtach/inject.py asks it before every write (a
    park loaded from disk is honoured at once), and the hook receiver and
    the message gate look for a park again through the monitor's check."""
    from aipager.dtach import inject

    def parked(name: str):
        # Falsy when not parked; else which park (the job, and the park's
        # number on this session), so the guard warns once per park.
        sess = registry.get(name)
        if sess is None or not sess.parked_job:
            return False
        return (sess.parked_job, sess.park_epoch)

    inject.set_parked_probe(parked)
    # The check's reads run off the event loop (claude_jobs.off_loop).
    hook_receiver.parked_check = session_monitor.check_parked_async
    bot.parked_check = session_monitor.check_parked_async


def _wire_dialog_probe(registry, hook_receiver) -> None:
    """Roadmap 8.143: dtach/inject.py asks before every prompt it types,
    and before each piece and the Enter, whether a dialog is open in the
    session, so no prompt's keys answer one. Open: the session waits on a
    prompt (INTERACTIVE, a carried or recorded one, another open prompt),
    or a datagram telling of one has arrived and is not handled yet (it
    can wait behind an older turn's finish while its dialog is on screen)."""
    from aipager.dtach import inject

    def dialog_open(name: str) -> bool:
        if hook_receiver.dialog_datagram_pending(name):
            return True
        sess = registry.get(name)
        if sess is None:
            return False
        return bool(sess.dialog_is_open() or sess.pending_permission is not None
                    or sess.extra_prompts)

    inject.set_dialog_probe(dialog_open)


async def _run_daemon(bot_username: str) -> None:
    """Boot the daemon and run until SIGINT/SIGTERM."""
    from aipager.config import (
        BOT_TOKEN, CHAT_ID, MINIAPP_ENABLED, MINIAPP_PORT, MINIAPP_PUBLIC_URL,
    )
    from aipager.dtach.hook_receiver import HookReceiver
    from aipager.session_monitor import SessionMonitor
    from aipager.state import SessionRegistry
    from aipager.bot import TelegramBot
    from aipager.claude_bootstrap import bootstrap_claude_settings

    if not BOT_TOKEN:
        log.error("CLAUDE_TG_BOT_TOKEN not set — run `aipager config` first")
        sys.exit(1)

    # Write claude-code's first-run acceptance flags so dtach-launched
    # sessions never block on dialogs a Telegram-only user can't dismiss,
    # and resolve the claude binary + auth shape for the startup
    # provenance line. Runs BEFORE bot.start() so it cannot send Telegram
    # itself — the notice is fired below, after the bot is up, so a slow
    # send never delays recover_sessions() or the session monitor.
    provenance = bootstrap_claude_settings()

    log.info("connected as @%s, will message chat %s", bot_username, CHAT_ID)
    # A setting for a feature that is gone: say so once and carry on. The
    # value holds bot tokens, so the line never carries it.
    if os.environ.get("OBSERVER_BOTS", "").strip():
        log.warning("OBSERVER_BOTS is set but observer bots were removed; "
                    "to let someone watch, add them to a group with the read_only role")

    registry = SessionRegistry()
    registry.load()
    bot = TelegramBot(registry)
    # Kept for problem reports: never probed again at tap time.
    bot.claude_auth_source = (provenance.auth_source if provenance is not None
                              else "unknown")
    hook_receiver = HookReceiver(registry, bot.notify)
    # Hooks newer than this daemon (an update installed, not yet running):
    # the owner is told once per hook version.
    hook_receiver.on_hook_version = bot.updates.note_hook_version
    session_monitor = SessionMonitor(registry, bot.notify)
    _wire_parked_state(registry, hook_receiver, session_monitor, bot)
    from aipager.dtach import hook_receiver as hook_receiver_mod
    _wire_dialog_probe(registry, hook_receiver_mod)

    # Resolved BEFORE bot.start(), because start() ends by sending the
    # first persistent keyboard and that keyboard has to know whether
    # there is a Mini App to offer. No Telegram call happens here — the
    # menu button still waits until the server below is confirmed up.
    from aipager.miniapp.tunnel import resolve_public_url
    miniapp_url = await resolve_public_url() if MINIAPP_ENABLED else ""
    bot.prime_miniapp_url(miniapp_url)
    # `_is_private_target`: the hold was for a `web_app` keyboard button,
    # which is private-chat-only. Since 2026-10-12 the keyboard has no
    # App button (Telegram gives a Mini App opened from one no init
    # data), so the hold no longer changes the keyboard; it only delays
    # it. Kept until the operator decides to retire it.
    if MINIAPP_ENABLED and not miniapp_url and _is_private_target():
        # A managed tunnel is about to start and will hand us a URL in
        # ~10-30s. Released by publish_miniapp_button, or by the timeout
        # below if no tunnel ever arrives.
        bot.defer_first_keyboard()

    await bot.start()
    if provenance is not None:
        # Always scheduled once a binary resolved — ProvenanceInfo's own
        # invariant guarantees `pending` is set, because the cheap auth
        # check cannot see an expired token and so must never be the
        # last word. Whether anything is actually SENT is decided inside
        # _finish_auth_check, and only an unusable credential says
        # anything: the provenance detail (absolute paths, versions,
        # every install found) goes to the journal, never to a chat
        # stored on Telegram's servers and, in a team scope, read by
        # every member of every configured group.
        #
        # Fire-and-forget, mirroring TunnelManager.start()'s precedent —
        # a slow or failing send must never delay recover_sessions() or
        # the session monitor. Once per process lifetime; nothing
        # re-sends on team-reload, Mini App reconnect, or any session
        # event.
        asyncio.create_task(_finish_auth_check(bot, provenance))
    await hook_receiver.start()
    await bot.recover_sessions()
    session_monitor.on_sessions_changed = bot._update_bot_commands
    session_monitor.on_mute_catchup = bot.flush_owed_keyboards
    # The pinned bar, then the problem report offer check (8.112).
    session_monitor.on_tick = bot.monitor_tick
    await session_monitor.start()

    # A self-update (roadmap 8.36) that restarted the daemon left a marker:
    # announce "aipager updated A → B, N sessions re-adopted" once the
    # monitor's first scans have re-adopted the dtach sockets. Fire-and-
    # forget, flood-safe, never raises; cancelled at shutdown below.
    from aipager.bot import update_flow
    marker_task = asyncio.create_task(
        update_flow.deliver_update_marker(bot, registry))
    # And the owner's "update is only half done" notices an older daemon
    # sent: those for a version this one runs become "finished" (edits
    # only, never a new message).
    hook_notices_task = asyncio.create_task(update_flow.finish_hook_notices(bot))

    # Mini App server — newest and highest-risk component, so it starts
    # last and (below) stops first. Behind a lazy import to keep
    # aiohttp (the largest dependency in the tree) off cold start-up
    # paths; aiohttp itself ships with aipager. A MiniAppUnavailable —
    # which now means an incomplete install, not a missing extra — logs
    # a friendly one-liner instead of taking the whole daemon down.
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
        manager = TunnelManager(MINIAPP_PORT, bot.publish_managed_miniapp_button)
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
    #
    # Except when a planned restart kept the tunnel: then the tunnel's own
    # first answer (the same address, or none) goes out instead, so the
    # button is never cleared and set again, and a clear here can never
    # land after the kept address.
    startup_url = miniapp_url if miniapp_server is not None else ""
    if manager is None or startup_url or not _tunnel_kept():
        await bot.publish_miniapp_button(startup_url)

    # Backstop for the held keyboard: if no tunnel URL has arrived by
    # now, send it anyway. A Mini App that never comes up must cost the
    # operator a button, never their whole keyboard.
    keyboard_release_task = asyncio.create_task(
        _release_keyboard_after(bot, KEYBOARD_HOLD_SECONDS))

    log.info("AIPager running — all components started")

    from aipager import self_restart

    stop = _new_stop_event()
    # A "Restart now" without a service unit stops through this same event
    # and is exec'd once the shutdown below has run (_cmd_start).
    self_restart.attach(stop.set)
    loop = asyncio.get_running_loop()

    def _on_stop_signal() -> None:
        # A real stop (Ctrl+C, docker stop, systemctl stop) always wins over
        # a pending restart: never undo an operator's stop.
        self_restart.cancel("signal")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_stop_signal)

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

    try:
        log.info("Shutting down...")
        # Cancel the deferred-keyboard timer before anything else: it is a
        # long sleep (KEYBOARD_HOLD_SECONDS) and leaving it pending keeps a
        # task alive well past the daemon it belongs to. Harmless in
        # production, but it also made every daemon test that drains pending
        # tasks sit for the full 45s — the suite went from 100s to 413s
        # before this was tracked and cancelled.
        keyboard_release_task.cancel()
        marker_task.cancel()
        hook_notices_task.cancel()
        # Before bot.stop(): a running self-update gets a short grace, then its
        # installer's process group is killed (it would otherwise outlive us
        # under KillMode=process and write the venv while the next daemon
        # starts); its final status edit still goes out.
        await _shutdown_updates(bot)
        registry.save()
        if manager is not None:
            # Before miniapp_server.stop(): stop accepting the world's
            # traffic (terminate the tunnel that publishes it) before
            # stopping what serves it. A planned restart keeps the tunnel
            # instead, for the next daemon to adopt: same address.
            await manager.stop(keep=_restart_planned(bot))
        if miniapp_server is not None:
            await miniapp_server.stop()
        session_monitor.stop()
        hook_receiver.stop()
        await bot.stop()
        # Again, now that nothing sends any more: a busy card whose send was in
        # flight at the first save landed since, and the next daemon can close
        # it only if it is on disk (roadmap 8.55).
        registry.save()
        log.info("Goodbye")
    finally:
        # No restart can be asked for once the stop has run.
        self_restart.detach()


def _tunnel_kept() -> bool:
    from aipager.miniapp.tunnel_manager import has_kept_tunnel
    return has_kept_tunnel()


def _restart_planned(bot) -> bool:
    """Whether this stop is a planned restart: the in-place restart is
    pending, or an update scheduled the service manager's restart (whose
    SIGTERM is this stop). A real stop (Ctrl+C, docker stop) cancels an
    in-place one (``_on_stop_signal``), so it is never planned. Never
    raises."""
    from aipager import self_restart

    try:
        if self_restart.pending():
            return True
        updates = getattr(bot, "updates", None)
        return getattr(updates, "service_restart_pending", False) is True
    except Exception:
        return False


# Slack on top of update_flow.SHUTDOWN_DEADLINE_SECONDS: the updates'
# shutdown bounds itself, and this bounds it again so the registry save and
# bot stop below it always run before systemd's TimeoutStopSec=15.
_UPDATES_SHUTDOWN_SLACK_SECONDS = 1.0


async def _shutdown_updates(bot) -> None:
    updates = getattr(bot, "updates", None)
    shutdown = getattr(updates, "shutdown", None)
    if shutdown is None:
        return
    from aipager.bot import update_flow
    limit = update_flow.SHUTDOWN_DEADLINE_SECONDS + _UPDATES_SHUTDOWN_SLACK_SECONDS
    try:
        await asyncio.wait_for(shutdown(), timeout=limit)
    except asyncio.TimeoutError:
        log.warning("self-update shutdown overran %.0fs; going on with the stop", limit)
    except Exception:
        log.warning("self-update shutdown failed", exc_info=True)


def _cmd_start(args: argparse.Namespace) -> int:
    from aipager import self_restart
    # The launch argv, environment and folder, before anything below can
    # change them: an in-place restart execs exactly these.
    self_restart.capture_launch()
    from aipager import instance, telegram_endpoint
    from aipager.errors import friendly_error
    # Isolated-instance and API-base checks come FIRST: before reading
    # config, migrating, locking or touching the network, so a mistyped
    # setting cannot write into the real home or send the token anywhere.
    problems = instance.start_check()
    if problems:
        friendly_error(*problems)
        sys.exit(2)
    problems = telegram_endpoint.check()
    if problems:
        friendly_error(*problems)
        sys.exit(2)
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
    # Problem reports (8.112): fingerprints and counts from the daemon's own
    # log records, never their text. In memory until the lock is held.
    from aipager.report import capture
    capture.install()
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
    _run_daemon_reporting_crashes(bot_username)
    # After the clean stop: a pending "Restart now" execs the installed
    # aipager in place (or hands over to a detached respawn). A crash or
    # Ctrl+C never gets here.
    return self_restart.after_shutdown(release_lock=_release_daemon_lock)


def _release_daemon_lock() -> None:
    """Unlock and close the daemon lock (before an in-place exec; the
    exec'd daemon takes it again)."""
    global _daemon_lock_fd
    fd, _daemon_lock_fd = _daemon_lock_fd, None
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _running_as_service() -> bool:
    """Whether systemd or launchd runs this daemon. Only then is an unclean
    exit a bug: a foreground ``aipager start`` dies with its terminal."""
    try:
        import platform
        if platform.system() == "Linux":
            from aipager import self_update
            return self_update._under_unit_cgroup()
        from aipager import service
        return os.environ.get("XPC_SERVICE_NAME") == service.MACOS_LABEL
    except Exception:
        return False


def _run_daemon_reporting_crashes(bot_username: str) -> None:
    """Run the daemon between the problem-report markers (8.112), and log a
    fatal error WITH its traceback before it propagates (8.110: the CLI's
    friendly excepthook prints one line and drops every frame)."""
    from aipager.i18n import persist
    from aipager.report import capture, runtime
    # Before daemon_starting, which writes the first-install marker: was
    # this machine running aipager already (multilingual phase 5)?
    persist.note_prior_run()
    runtime.daemon_starting(service=_running_as_service())
    try:
        asyncio.run(_run_daemon(bot_username))
    except (KeyboardInterrupt, SystemExit):
        runtime.daemon_stopped(clean=True)
        raise
    except BaseException:
        log.critical("aipager stopped on an unexpected error", exc_info=True,
                     extra={capture.TRIGGER_ATTR: "crash"})
        runtime.daemon_stopped(clean=False)
        raise
    runtime.daemon_stopped(clean=True)
