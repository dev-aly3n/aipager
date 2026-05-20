"""Pre-flight checks for aipager subcommands.

Each ``require_*`` function either returns silently (when the check
passes) or prints a multi-line user-friendly error to stderr and exits
with code 2 (conventionally used for misconfiguration).

The exit code matters: a wrapper script can check ``$? == 2`` and know
"this is a setup problem, not a crash."
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from aipager.errors import friendly_error, friendly_warn


def require_config() -> None:
    """Exit with code 2 if aipager isn't configured.

    v2 (``aipager.yaml`` with scopes) fully satisfies config — once the
    v1 ``config.env`` is retired, ``CHAT_ID`` is empty but the token +
    scopes come from ``aipager.yaml``. Falls back to the legacy
    BOT_TOKEN/CHAT_ID check for un-migrated installs.
    """
    from aipager.config import BOT_TOKEN, CHAT_ID, SCOPES

    if SCOPES and BOT_TOKEN:
        return  # v2 is authoritative

    missing: list[str] = []
    if not BOT_TOKEN:
        missing.append("CLAUDE_TG_BOT_TOKEN")
    if not CHAT_ID and not SCOPES:
        missing.append("CLAUDE_TG_CHAT_ID")
    if not missing:
        return

    friendly_error(
        "aipager isn't configured yet.",
        "",
        f"  Missing: {', '.join(missing)}",
        "",
        "  Run this once to set up your Telegram bot and patch",
        "  ~/.claude/settings.json:",
        "",
        "      aipager config",
        "",
    )
    sys.exit(2)


def require_claude() -> str:
    """Exit with code 2 if the `claude` binary isn't on PATH. Returns the path."""
    p = shutil.which("claude")
    if p:
        return p
    friendly_error(
        "Claude Code CLI not found on PATH.",
        "",
        "  aipager wraps the `claude` command — install it from:",
        "      https://docs.anthropic.com/claude/docs/claude-code",
        "",
        "  After install, verify with: `claude --version`",
        "",
    )
    sys.exit(2)


def require_daemon() -> None:
    """Exit with code 2 if the aipager daemon isn't listening on /tmp/aipager.sock."""
    from aipager.config import SOCKET_PATH

    if Path(SOCKET_PATH).exists():
        return
    friendly_error(
        "aipager daemon isn't running.",
        "",
        f"  Expected the socket at {SOCKET_PATH} but it's not there.",
        "  Without the daemon, your session won't be mirrored to Telegram.",
        "",
        "  Start the daemon, then re-run this command:",
        "",
        "      aipager start              # foreground (Ctrl-C to stop)",
        "      aipager service start      # via systemd-user / launchd",
        "",
        "  Or, if you haven't yet installed the service, do that once:",
        "",
        "      aipager service install",
        "",
    )
    sys.exit(2)


def warn_if_daemon_down() -> None:
    """Print a warning if the daemon socket isn't there, but don't exit.

    Use for non-critical commands where the user might intentionally be
    in a partial state (e.g. configuring while the daemon is stopped).
    """
    from aipager.config import SOCKET_PATH

    if Path(SOCKET_PATH).exists():
        return
    friendly_warn(
        "aipager daemon isn't running.",
        f"  ({SOCKET_PATH} is missing)",
        "  Start it with `aipager start` or `aipager service start`.",
        "",
    )
