"""Top-level CLI for aipager.

Subcommands:
  start    run the daemon in the foreground
  config   interactive setup wizard (configures Telegram + Claude Code)
  version  print version
  doctor   run health checks
  status   show daemon and session snapshot
  logs     tail the daemon log
  update   upgrade aipager via uv / pipx / Homebrew
  uninstall  stop the daemon, remove config + state, uninstall the binary
  resume   resume a previously-gone Claude session
  session  open / manage a Claude Code session under dtach
  service  install / manage daemon as a systemd-user or launchd service

The ``_cmd_*`` functions in this file are thin dispatchers that
delegate to feature modules (cli.daemon, cli.session, cli.resume) or
sibling packages (``aipager.doctor``, ``aipager.status``, etc).
``main()`` builds the argparse tree and routes to the right ``fn``.
"""

from __future__ import annotations

import argparse
import sys

from aipager import __version__
from aipager.cli.daemon import _cmd_start as _cmd_start
from aipager.cli.resume import _cmd_resume as _cmd_resume
from aipager.cli.session import (
    _cmd_session as _cmd_session,
    _session_kill as _session_kill,
    _session_ls as _session_ls,
)


def _cmd_config(args: argparse.Namespace) -> int:
    from aipager.wizard import run
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

    resume_p = sub.add_parser(
        "resume",
        help="resume a previously-gone Claude session by name "
             "(no arg → paginated picker)",
    )
    resume_p.add_argument(
        "name", nargs="?",
        help="session label to resume; omit for an interactive picker",
    )
    resume_p.set_defaults(fn=_cmd_resume)

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
