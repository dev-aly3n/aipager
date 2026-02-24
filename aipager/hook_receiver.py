"""Asyncio Unix datagram socket listener — receives hook events from notify_hook.py.

Parses JSON datagrams, maps notification_type to state transitions,
and triggers Telegram notifications when state actually changes.

Idle notifications are NOT sent from here — the hook fires before the
terminal finishes rendering, so the pane summary would be stale. The
pane_monitor handles idle detection once the spinner disappears.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from pathlib import Path

from aipager.config import SOCKET_PATH
from aipager.state import SessionRegistry, Status

log = logging.getLogger(__name__)


def _extract_pending_tool(transcript_path: str) -> dict | None:
    """Read the last lines of the transcript to find the pending tool_use."""
    try:
        lines = Path(transcript_path).read_text().strip().splitlines()
    except (FileNotFoundError, PermissionError):
        return None

    for line in reversed(lines[-10:]):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content", [])
        for block in reversed(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            inp = block.get("input", {})
            return {"name": name, "input": inp, "summary": _summarize_tool(name, inp)}
    return None


def _summarize_tool(name: str, inp: dict) -> str:
    if name == "Bash":
        return f"Bash: {inp.get('description') or inp.get('command', '')[:80]}"
    if name == "AskUserQuestion":
        questions = inp.get("questions", [])
        return questions[0].get("question", "")[:120] if questions else "AskUserQuestion"
    if name in ("Read", "Write", "Edit"):
        return f"{name}: {inp.get('file_path', '')}"
    if name == "Task":
        return f"Task: {inp.get('description', inp.get('prompt', '')[:80])}"
    if name == "WebFetch":
        return f"WebFetch: {inp.get('url', '')[:80]}"
    if name == "WebSearch":
        return f"WebSearch: {inp.get('query', '')[:80]}"
    return name


class HookReceiver:
    """Receives UDP datagrams from notify_hook.py and drives state transitions.

    Only handles permission_prompt → INTERACTIVE (needs instant response).
    Idle events just ensure the session is tracked; the pane_monitor handles
    idle notifications since it waits for the spinner to disappear.
    """

    def __init__(self, registry: SessionRegistry, notify_fn):
        self.registry = registry
        self.notify_fn = notify_fn

    async def start(self) -> None:
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self._on_datagram),
            local_addr=SOCKET_PATH,
            family=socket.AF_UNIX,
        )
        os.chmod(SOCKET_PATH, 0o666)
        self._transport = transport
        log.info("Hook receiver listening on %s", SOCKET_PATH)

    async def _on_datagram(self, data: bytes) -> None:
        try:
            msg = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        event = msg.get("notification_type", msg.get("type", ""))
        tmux_session = msg.get("tmux_session", "")
        transcript_path = msg.get("transcript_path", "")

        if not tmux_session or not event:
            return

        if event == "permission_prompt":
            tool_info = _extract_pending_tool(transcript_path) if transcript_path else None
            new_status = Status.INTERACTIVE
            context = {"tool_info": tool_info, "transcript_path": transcript_path}

            sess = self.registry.transition(tmux_session, new_status)
            if sess:
                await self.notify_fn(sess, event, context)
        else:
            # idle, auth_success, etc. — just ensure session is tracked.
            # Pane monitor handles idle notifications (waits for spinner to clear).
            self.registry.get_or_create(tmux_session)

    def stop(self) -> None:
        if hasattr(self, "_transport"):
            self._transport.close()
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, handler):
        self._handler = handler

    def datagram_received(self, data: bytes, addr) -> None:
        asyncio.ensure_future(self._handler(data))
