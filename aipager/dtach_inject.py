"""Async dtach wrappers — inject keystrokes and check session liveness.

Drop-in replacement for tmux_inject.py. Uses dtach -p for input injection
(sends raw bytes to the session's PTY via stdin pipe).

Socket naming: session "claude-dev" → /tmp/claude-dtach-dev.sock
"""

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)

SOCK_PREFIX = "/tmp/claude-dtach-"

# Logical key names → ANSI escape sequences
KEYS = {
    "Enter": "\r",
    "Down": "\x1b[B",
    "Up": "\x1b[A",
    "Tab": "\t",
    "Escape": "\x1b",
}


async def _run(args: list[str], stdin: bytes = b"",
               timeout: float = 5) -> tuple[bool, str]:
    """Run subprocess, optionally piping stdin, return (success, stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin or None), timeout=timeout,
        )
        if proc.returncode == 0:
            return True, stdout.decode()
        log.error("dtach cmd failed: %s — %s", args, stderr.decode().strip())
        return False, ""
    except asyncio.TimeoutError:
        log.error("dtach cmd timed out: %s", args)
        return False, ""
    except FileNotFoundError:
        log.error("dtach not found")
        return False, ""


def _sock_path(session: str) -> str:
    """Convert session name 'claude-dev' to socket path '/tmp/claude-dtach-dev.sock'."""
    name = session.removeprefix("claude-")
    return f"{SOCK_PREFIX}{name}.sock"


async def send_keys(session: str, keys: str) -> bool:
    """Send a key sequence to the dtach session.

    `keys` can be a logical name ("Enter", "Down") or raw text.
    """
    seq = KEYS.get(keys, keys)
    sock = _sock_path(session)
    ok, _ = await _run(["dtach", "-p", sock], stdin=seq.encode())
    if ok:
        log.info("Sent keys %r → %s", keys, session)
    return ok


async def send_text_and_enter(session: str, text: str) -> bool:
    """Send literal text followed by Enter.

    Text and Enter must be separate dtach -p calls — Claude Code's TUI
    treats a single chunk (text + CR) as all-text input. A separate CR
    write is needed to trigger the submit keypress event.
    """
    sock = _sock_path(session)
    ok, _ = await _run(["dtach", "-p", sock], stdin=text.encode())
    if not ok:
        return False
    await asyncio.sleep(0.05)
    ok, _ = await _run(["dtach", "-p", sock], stdin=b"\r")
    if ok:
        log.info("Sent text %r + Enter → %s", text[:50], session)
    return ok


async def is_alive(session: str) -> bool:
    """Check if a dtach session socket exists and is connectable."""
    sock = _sock_path(session)
    return Path(sock).is_socket()


async def list_sessions() -> list[str]:
    """Return names of all active claude-dtach sessions.

    Scans /tmp for claude-dtach-*.sock files that are Unix sockets.
    """
    results = []
    for sock_file in Path("/tmp").glob("claude-dtach-*.sock"):
        if not sock_file.is_socket():
            continue
        name = "claude-" + sock_file.stem.removeprefix("claude-dtach-")
        results.append(name)
    return results
