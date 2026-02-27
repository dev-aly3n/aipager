"""Async dtach wrappers — inject keystrokes and check session liveness.

Drop-in replacement for tmux_inject.py. Uses dtach -p for input injection
(sends raw bytes to the session's PTY via stdin pipe).

Socket naming: session "claude-dev" → /tmp/claude-dtach-dev.sock
"""

import asyncio
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

SOCK_PREFIX = "/tmp/claude-dtach-"

# Logical key names → ANSI escape sequences
KEYS = {
    "Enter": "\r",
    "Down": "\x1b[B",
    "Up": "\x1b[A",
    "Right": "\x1b[C",
    "Left": "\x1b[D",
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
    # Claude Code's Ink TUI needs time to process text input before
    # Enter is recognized as "submit". Too short → \r is swallowed.
    # Scale with text length: longer text = more rendering time needed.
    delay = max(0.15, min(0.5, len(text) * 0.003))
    await asyncio.sleep(delay)
    ok, _ = await _run(["dtach", "-p", sock], stdin=b"\r")
    if ok:
        log.info("Sent text %r + Enter → %s", text[:50], session)
    return ok


async def kill_session(session: str) -> bool:
    """Kill a dtach session by finding its host PID and terminating it."""
    sock = _sock_path(session)
    sock_path = Path(sock)
    if not sock_path.is_socket():
        return False

    # Find the dtach host process (dtach -n <sock> ...)
    try:
        proc = await asyncio.create_subprocess_exec(
            "fuser", sock,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        pids = stdout.decode().split()
        for pid_str in pids:
            pid_str = pid_str.strip()
            if pid_str.isdigit():
                import os, signal
                os.kill(int(pid_str), signal.SIGTERM)
                log.info("Killed dtach PID %s for %s", pid_str, session)
    except Exception:
        log.warning("Failed to find/kill dtach PID for %s", session, exc_info=True)

    # Remove socket as fallback (dtach should clean up, but ensure it)
    try:
        sock_path.unlink(missing_ok=True)
    except OSError:
        pass
    return True


async def is_alive(session: str) -> bool:
    """Check if a dtach session socket exists and is connectable."""
    sock = _sock_path(session)
    return Path(sock).is_socket()


_VALID_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_RESERVED = {"status", "stop", "kill", "new", "help", "start", "settings"}
_PROJECT_DIR = "/path/to/project"
_CLAUDE_BIN = "claude"


async def launch_session(name: str, skip_perms: bool = True) -> tuple[bool, str]:
    """Launch a new Claude Code session inside dtach.

    Returns (success, error_message). The session_monitor will auto-discover
    the new session within 2 seconds.
    """
    if not name or not _VALID_NAME.match(name):
        return False, "Invalid name (use letters, numbers, hyphens)"
    if name.lower() in _RESERVED:
        return False, f"'{name}' is a reserved command name"
    if len(name) > 30:
        return False, "Name too long (max 30 chars)"

    sock = f"{SOCK_PREFIX}{name}.sock"
    if Path(sock).is_socket():
        return False, f"Session '{name}' already exists"

    # Build the bash -c command matching scripts/claude-dtach.sh line 84
    perms = "--dangerously-skip-permissions" if skip_perms else ""
    sys_prompt = (f'Your session name is "{name}". '
                  f'When users address you by this name, respond naturally '
                  f'-- it is your name in this session.')
    bash_cmd = (
        f"unset CLAUDECODE; "
        f"export CLAUDE_DTACH_SESSION=claude-{name}; "
        f"{_CLAUDE_BIN} {perms} "
        f"--append-system-prompt '{sys_prompt}'"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "dtach", "-n", sock, "-Ez", "bash", "-c", bash_cmd,
            cwd=_PROJECT_DIR,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return False, f"dtach failed: {stderr.decode().strip()}"
    except FileNotFoundError:
        return False, "dtach not installed"
    except asyncio.TimeoutError:
        return False, "dtach launch timed out"

    # Wait for socket to appear (dtach creates it asynchronously)
    for _ in range(10):
        await asyncio.sleep(0.3)
        if Path(sock).is_socket():
            log.info("Launched session claude-%s (socket: %s)", name, sock)
            return True, ""
    return False, "Socket never appeared after launch"


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
