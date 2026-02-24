"""Async tmux subprocess wrappers — inject keystrokes and capture pane content."""

import asyncio
import logging

log = logging.getLogger(__name__)


async def _run(args: list[str], timeout: float = 5) -> tuple[bool, str]:
    """Run a subprocess, return (success, stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return True, stdout.decode()
        log.error("tmux %s failed: %s", args[1], stderr.decode().strip())
        return False, ""
    except asyncio.TimeoutError:
        log.error("tmux %s timed out", args[1])
        return False, ""
    except FileNotFoundError:
        log.error("tmux not found")
        return False, ""


async def send_keys(session: str, keys: str) -> bool:
    ok, _ = await _run(["tmux", "send-keys", "-t", session, keys])
    if ok:
        log.info("Sent keys %r → %s", keys, session)
    return ok


async def send_text_and_enter(session: str, text: str) -> bool:
    ok, _ = await _run(["tmux", "send-keys", "-t", session, "-l", text])
    if not ok:
        return False
    ok, _ = await _run(["tmux", "send-keys", "-t", session, "Enter"])
    if ok:
        log.info("Sent text %r + Enter → %s", text[:50], session)
    return ok


async def is_alive(session: str) -> bool:
    ok, _ = await _run(["tmux", "has-session", "-t", session], timeout=3)
    return ok


async def list_sessions() -> list[str]:
    """Return names of all tmux sessions starting with 'claude-'."""
    ok, out = await _run(["tmux", "list-sessions", "-F", "#{session_name}"], timeout=3)
    if not ok:
        return []
    return [s for s in out.strip().splitlines() if s.startswith("claude-")]


async def capture_pane(session: str) -> list[str]:
    """Capture visible pane content, return non-empty lines."""
    ok, out = await _run(["tmux", "capture-pane", "-t", session, "-p"], timeout=3)
    if not ok:
        return []
    return [l for l in out.rstrip().splitlines() if l.strip()]
