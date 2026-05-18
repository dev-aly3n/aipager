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


def _cmd_status(args: argparse.Namespace) -> int:
    from aipager.status import cmd_status
    return cmd_status(args)


def _cmd_logs(args: argparse.Namespace) -> int:
    from aipager.service import cmd_logs
    return cmd_logs(follow=args.follow, lines=args.lines)


def _cmd_update(args: argparse.Namespace) -> int:
    from aipager.updater import cmd_update
    return cmd_update(args)


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from aipager.updater import cmd_uninstall
    return cmd_uninstall(args)


def _cmd_service(args: argparse.Namespace) -> int:
    from aipager.service import cmd_service
    return cmd_service(args)


def _cmd_session(args: argparse.Namespace) -> int:
    # `aipager session` has a positional `name` that can also be a verb:
    #   aipager session ls / list                       → list all sessions
    #   aipager session kill <name>                     → terminate session
    #   aipager session <name> [claude args...]         → launch / attach
    # The verbs `ls`, `list`, `kill` are reserved as session names by
    # `dtach_launcher._validate_name`, so no collision is possible.
    if args.name in ("ls", "list"):
        return _session_ls(args)
    if args.name == "kill":
        return _session_kill(args)
    from aipager.preflight import require_claude, require_config, require_daemon
    require_config()
    require_claude()
    require_daemon()
    from aipager.dtach_launcher import launch
    return launch(name=args.name, claude_args=args.claude_args or [])


def _session_ls(args: argparse.Namespace) -> int:
    """`aipager session ls [-a|--all] [--json]` — list dtach sessions."""
    import json as _json

    rest = list(args.claude_args or [])
    show_all = bool({"-a", "--all"} & set(rest))
    as_json = "--json" in rest

    from aipager.status import (
        _gather_sessions,
        render_sessions_plain,
        render_sessions_rich,
    )
    sessions, _live = _gather_sessions()
    if not show_all:
        sessions = [s for s in sessions if s["status"] != "GONE"]

    if as_json:
        print(_json.dumps({"sessions": sessions}, indent=2))
        return 0

    from aipager.ui import console
    if console.is_terminal:
        console.print()
        render_sessions_rich(sessions)
        console.print()
    else:
        render_sessions_plain(sessions)
    return 0


def _session_kill(args: argparse.Namespace) -> int:
    """`aipager session kill <name> [-y]` — terminate a dtach session."""
    rest = list(args.claude_args or [])
    force = bool({"-y", "--yes"} & set(rest))
    targets = [a for a in rest if a not in ("-y", "--yes")]
    if not targets:
        from aipager.errors import friendly_error
        friendly_error(
            "usage: aipager session kill <name> [-y]",
            "",
            "  Run `aipager session ls` to see active sessions.",
        )
        return 2
    name = targets[0]
    session = name if name.startswith("claude-") else f"claude-{name}"

    from pathlib import Path
    sock = Path(f"/tmp/claude-dtach-{name.removeprefix('claude-')}.sock")
    if not sock.exists():
        from aipager.errors import friendly_error
        friendly_error(
            f"session {name!r} not found.",
            f"  Expected socket at {sock} but it isn't there.",
            "  Run `aipager session ls` to see what's running.",
        )
        return 1

    if not force:
        answer = input(f"Kill session {name!r}? This will terminate the running "
                       f"claude process. [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            return 0

    from aipager.dtach_inject import kill_session
    from aipager.ui import ok as ui_ok
    killed = asyncio.run(kill_session(session))
    if killed:
        ui_ok(f"killed [path]{session}[/path]")
        return 0
    from aipager.errors import friendly_error
    friendly_error(f"could not kill session {name!r}.")
    return 1


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

    status_p = sub.add_parser(
        "status", help="show daemon and session snapshot",
    )
    status_p.add_argument("--json", dest="as_json", action="store_true",
                          help="emit machine-readable JSON instead of a table")
    status_p.set_defaults(fn=_cmd_status)

    logs_p = sub.add_parser("logs", help="tail the daemon log")
    logs_p.add_argument("-f", "--follow", action="store_true",
                        help="follow new log lines as they appear")
    logs_p.add_argument("-n", "--lines", type=int, default=100,
                        help="number of trailing lines to show (default: 100)")
    logs_p.set_defaults(fn=_cmd_logs)

    sub.add_parser(
        "update",
        help="upgrade aipager via uv / pipx / Homebrew (auto-detect)",
    ).set_defaults(fn=_cmd_update)

    uninstall_p = sub.add_parser(
        "uninstall",
        help="stop the daemon, remove config + state, uninstall the binary",
    )
    uninstall_p.add_argument("-y", "--yes", dest="force", action="store_true",
                             help="skip the confirmation prompt")
    uninstall_p.set_defaults(fn=_cmd_uninstall)

    help_p = sub.add_parser("help",
                            help="show help for aipager or a subcommand")
    help_p.add_argument("topic", nargs="?",
                        help="subcommand name (e.g. `aipager help session`)")

    session_p = sub.add_parser(
        "session",
        help="open or manage a Claude Code session under dtach "
             "(creates / reattaches by default; `ls`, `list`, `kill` "
             "are reserved subcommand verbs)",
    )
    session_p.add_argument(
        "name",
        help="session label, OR one of: `ls` / `list` (list sessions), "
             "`kill` (terminate a session — supply name after)",
    )
    session_p.add_argument(
        "claude_args", nargs=argparse.REMAINDER,
        help="extra args passed through to claude verbatim "
             "(e.g. --dangerously-skip-permissions, --continue, "
             "--resume <session-id>); for `ls`: -a/--all, --json; "
             "for `kill`: <name> and optional -y",
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
    if args.cmd == "help":
        topic = getattr(args, "topic", None)
        if not topic:
            parser.print_help()
            sys.exit(0)
        # Look up the topic in the subparsers and print its help.
        subparsers_action = next(
            (a for a in parser._actions
             if isinstance(a, argparse._SubParsersAction)),
            None,
        )
        choices = subparsers_action.choices if subparsers_action else {}
        if topic in choices:
            choices[topic].print_help()
            sys.exit(0)
        from aipager.errors import friendly_error
        friendly_error(
            f"Unknown subcommand: {topic}",
            f"  Available: {', '.join(sorted(choices))}",
        )
        sys.exit(2)
    if args.cmd == "service" and not getattr(args, "service_cmd", None):
        service_p.print_help()
        sys.exit(0)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
