"""Top-level CLI for aipager.

Subcommands:
  start    run the daemon in the foreground
  config   interactive setup wizard (configures Telegram + Claude Code)
  version  print version
  doctor   run health checks
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

from aipager import __version__

log = logging.getLogger("aipager")


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
    from aipager.config import BOT_TOKEN, CHAT_ID, OBSERVER_BOTS
    from aipager.hook_receiver import HookReceiver
    from aipager.observer import ObserverBroadcaster
    from aipager.session_monitor import SessionMonitor
    from aipager.state import SessionRegistry
    from aipager.telegram_bot import TelegramBot

    if not BOT_TOKEN:
        log.error("CLAUDE_TG_BOT_TOKEN not set — run `aipager config` first")
        sys.exit(1)

    log.info("connected as @%s, will message chat %s", bot_username, CHAT_ID)

    registry = SessionRegistry()
    registry.load()
    bot = TelegramBot(registry)
    hook_receiver = HookReceiver(registry, bot.notify)
    session_monitor = SessionMonitor(registry, bot.notify)

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

    log.info("AIPager running — all components started")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await stop.wait()

    log.info("Shutting down...")
    registry.save()
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
    _check_existing_daemon()
    bot_username = _telegram_preflight()
    asyncio.run(_run_daemon(bot_username))
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    from aipager.setup_wizard import run
    return run()


def _cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from aipager.doctor import cmd_doctor
    return cmd_doctor(args)


def _cmd_service(args: argparse.Namespace) -> int:
    from aipager.service import cmd_service
    return cmd_service(args)


def _cmd_session(args: argparse.Namespace) -> int:
    from aipager.preflight import require_claude, require_config, require_daemon
    require_config()
    require_claude()
    require_daemon()
    from aipager.dtach_launcher import launch
    return launch(
        name=args.name,
        skip_perms=args.skip_perms,
        resume=args.resume,
        claude_args=args.claude_args or [],
    )


def main() -> None:
    from aipager.errors import install_excepthook
    install_excepthook()
    parser = argparse.ArgumentParser(
        prog="aipager",
        description="Telegram remote-control daemon for Claude Code sessions",
    )
    parser.add_argument("--version", action="version",
                        version=f"aipager {__version__}")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("start", help="run the daemon in the foreground"
                   ).set_defaults(fn=_cmd_start)
    sub.add_parser("config", help="interactive setup wizard"
                   ).set_defaults(fn=_cmd_config)
    sub.add_parser("version", help="print version"
                   ).set_defaults(fn=_cmd_version)
    sub.add_parser("doctor", help="run health checks and print a report"
                   ).set_defaults(fn=_cmd_doctor)

    session_p = sub.add_parser(
        "session",
        help="open a Claude Code session under dtach (creates if it doesn't "
             "exist, reattaches if it does)",
    )
    session_p.add_argument(
        "-y", dest="skip_perms", action="store_true",
        help="pass --dangerously-skip-permissions to claude",
    )
    session_p.add_argument(
        "--resume", dest="resume", action="store_true",
        help="when creating a fresh dtach session, also pass --continue to "
             "claude so it resumes the most recent saved conversation in "
             "this cwd. No-op when reattaching to an existing dtach session "
             "(claude is already running its conversation).",
    )
    session_p.add_argument("name", help="session label (becomes claude-<name>)")
    session_p.add_argument(
        "claude_args", nargs=argparse.REMAINDER,
        help="extra args passed through to claude (e.g. --resume <session-id>)",
    )
    session_p.set_defaults(fn=_cmd_session)

    service_p = sub.add_parser(
        "service",
        help="install/manage the daemon as a systemd-user or launchd service",
    )
    service_p.set_defaults(fn=_cmd_service)
    service_sub = service_p.add_subparsers(dest="service_cmd")
    for name, summary in [
        ("install",   "write the service unit and enable+start it"),
        ("start",     "start the running service"),
        ("stop",      "stop the running service"),
        ("status",    "show service status"),
        ("logs",      "tail service logs (Ctrl-C to exit)"),
        ("uninstall", "stop the service and remove the unit"),
    ]:
        service_sub.add_parser(name, help=summary)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)
    if args.cmd == "service" and not getattr(args, "service_cmd", None):
        service_p.print_help()
        sys.exit(0)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
