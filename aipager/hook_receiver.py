"""Asyncio Unix datagram socket listener — receives hook events from notify_hook.py.

Parses JSON datagrams, maps notification_type to state transitions,
and triggers Telegram notifications when state actually changes.

Handles both INTERACTIVE (permission prompts) and IDLE (task complete)
notifications. IDLE uses transcript-based rich summaries when available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from pathlib import Path

from aipager.config import RICH_SUMMARIES, SOCKET_PATH
from aipager.md_to_tg import markdown_to_telegram_html
from aipager.state import SessionRegistry, Status
from aipager.transcript import extract_last_response, find_transcript

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

    Handles permission_prompt → INTERACTIVE and idle events → IDLE with
    transcript-based rich summaries.
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

        event = msg.get("notification_type") or msg.get("hook_event_name") or msg.get("type", "")
        session_name = msg.get("session") or msg.get("tmux_session", "")
        transcript_path = msg.get("transcript_path", "")

        if not session_name or not event:
            return

        # Store transcript_path on ALL events for rich summaries
        if transcript_path:
            self.registry.get_or_create(session_name).transcript_path = transcript_path
            log.debug("[%s] Stored transcript_path: %s", session_name, transcript_path)

        log.debug("Hook event: %s from %s", event, session_name)

        if event == "permission_prompt":
            tool_info = _extract_pending_tool(transcript_path) if transcript_path else None
            new_status = Status.INTERACTIVE
            context = {"tool_info": tool_info, "transcript_path": transcript_path}

            sess = self.registry.transition(session_name, new_status)
            if sess:
                await self.notify_fn(sess, event, context)

        elif event.lower() in ("idle_prompt", "idle", "stop", "notification"):
            sess = self.registry.transition(session_name, Status.IDLE)
            if sess is None:
                return  # already idle or debounced

            notify_ctx: dict = {"summary": ""}

            # Primary: last_assistant_message from hook JSON (always current)
            last_msg = msg.get("last_assistant_message", "")

            if last_msg and RICH_SUMMARIES and "```" in last_msg:
                # Rich HTML formatting for code-heavy responses
                try:
                    html_summary = markdown_to_telegram_html(last_msg)
                    notify_ctx = {
                        "summary": html_summary,
                        "html_summary": True,
                        "raw_md": last_msg,
                    }
                    log.info("[%s] Rich summary from hook (%d chars)", session_name, len(html_summary))
                except Exception:
                    notify_ctx = {"summary": last_msg}
            elif last_msg:
                notify_ctx = {"summary": last_msg}
            else:
                # Fallback: transcript (for hooks that don't include last_assistant_message)
                tracked = self.registry.get(session_name)
                tp = transcript_path or (tracked.transcript_path if tracked else "")
                if not tp and RICH_SUMMARIES:
                    tp = find_transcript(session_name)
                if tp:
                    try:
                        md = extract_last_response(tp)
                        if md and RICH_SUMMARIES and "```" in md:
                            html_summary = markdown_to_telegram_html(md)
                            notify_ctx = {
                                "summary": html_summary,
                                "html_summary": True,
                                "raw_md": md,
                            }
                        elif md:
                            notify_ctx = {"summary": md}
                    except Exception:
                        log.info("[%s] Transcript summary failed", session_name)

            await self.notify_fn(sess, "idle_prompt", notify_ctx)

        else:
            # auth_success, etc. — just ensure session is tracked
            self.registry.get_or_create(session_name)

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
