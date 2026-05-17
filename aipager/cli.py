"""Top-level CLI for aipager.

Subcommands:
  start    run the daemon in the foreground
  config   interactive setup wizard (configures Telegram + Claude Code)
  version  print version
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from aipager import __version__

log = logging.getLogger("aipager")


async def _run_daemon() -> None:
    """Boot the daemon and run until SIGINT/SIGTERM."""
    from aipager.config import BOT_TOKEN, OBSERVER_BOTS
    from aipager.hook_receiver import HookReceiver
    from aipager.observer import ObserverBroadcaster
    from aipager.session_monitor import SessionMonitor
    from aipager.state import SessionRegistry
    from aipager.telegram_bot import TelegramBot

    if not BOT_TOKEN:
        log.error("CLAUDE_TG_BOT_TOKEN not set — run `aipager config` first")
        sys.exit(1)

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
    asyncio.run(_run_daemon())
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    from aipager.setup_wizard import run
    return run()


def _cmd_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0


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
