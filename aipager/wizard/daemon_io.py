"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations

import os


from aipager.ui import console
from aipager.wizard._constants import (
    CONFIG_DIR, CONFIG_ENV,
)


def _read_env_file() -> tuple[str, str]:
    """Return ``(token, chat_id)`` from CONFIG_ENV, or ``("", "")``
    if the file is missing or malformed."""
    if not CONFIG_ENV.exists():
        return "", ""
    token = ""
    chat_id = ""
    try:
        for line in CONFIG_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip("\"'")
            if k == "CLAUDE_TG_BOT_TOKEN":
                token = v
            elif k == "CLAUDE_TG_CHAT_ID":
                chat_id = v
    except OSError:
        return "", ""
    return token, chat_id


def _write_env_file(token: str, chat_id: int | str) -> None:
    """Overwrite CONFIG_ENV (mode 0600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_ENV.write_text(
        f"CLAUDE_TG_BOT_TOKEN={token}\nCLAUDE_TG_CHAT_ID={chat_id}\n"
    )
    try:
        os.chmod(CONFIG_ENV, 0o600)
    except OSError:
        pass


def _detect_daemon_running() -> int | None:
    """Probe ``/tmp/aipager.sock`` and return the daemon's PID if
    we can find one, ``None`` otherwise. Used for the post-edit hint
    ("daemon needs a restart")."""
    import socket as _socket
    p = "/tmp/aipager.sock"
    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.sendto(b'{"event":"_wizard_probe"}', p)
        s.close()
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return None
    # Best-effort PID lookup via pgrep
    import shutil as _shutil
    import subprocess as _subprocess
    if _shutil.which("pgrep"):
        try:
            r = _subprocess.run(
                ["pgrep", "-f", "aipager start"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                first = r.stdout.strip().split("\n", 1)[0]
                if first.isdigit():
                    return int(first)
        except (OSError, _subprocess.TimeoutExpired):
            pass
    return -1  # daemon up, PID unknown


def _restart_hint() -> None:
    """Print a one-line reminder that the daemon must be restarted
    for config changes to take effect."""
    pid = _detect_daemon_running()
    if pid is None:
        # Daemon not running — nothing to restart.
        return
    console.print()
    console.print(
        "[warn]⚠[/warn]  [warn]Restart the daemon to apply this change:[/warn]"
    )
    console.print(
        "    [path]aipager service restart[/path]"
        "  [muted](or kill the foreground daemon and re-run `aipager start`)[/muted]"
    )


def _signal_reload() -> bool:
    """Send SIGUSR1 to the running daemon to live-reload team.yaml.

    Returns ``True`` iff a signal was delivered. ``False`` when the
    daemon isn't running, the PID is unknown, or the platform
    doesn't support signals (Windows). Caller handles fallback.
    """
    import signal as _signal

    pid = _detect_daemon_running()
    if pid is None or pid < 0:
        return False
    try:
        os.kill(pid, _signal.SIGUSR1)
        return True
    except (OSError, AttributeError):
        return False


def _apply_team_change_hint() -> None:
    """Post-edit feedback for changes that ONLY touched team.yaml.

    Prefers a live SIGUSR1 reload when the daemon is reachable; falls
    back to the legacy restart hint otherwise. Use the bare
    :func:`_restart_hint` for edits that also touched ``config.env``
    or the bot token (those still require a full restart).
    """
    if _signal_reload():
        console.print()
        console.print(
            "[ok]✓[/ok]  Team config reloaded live "
            "[muted](no daemon restart needed)[/muted]"
        )
        return
    _restart_hint()
