"""In-memory session registry with idempotent state machine.

State transitions:
    UNKNOWN → BUSY → IDLE        (sends idle notification with summary)
                   → INTERACTIVE  (sends permission/question buttons)
    IDLE → BUSY                   (edits old msg: "→ Working...")
    INTERACTIVE → BUSY            (button tap already handled)
    any → GONE                    (session disappeared)

transition() is idempotent — same-state calls are no-ops, eliminating
all duplicate notification bugs from the old system.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

log = logging.getLogger(__name__)


class Status(Enum):
    UNKNOWN = auto()
    BUSY = auto()
    IDLE = auto()
    INTERACTIVE = auto()
    GONE = auto()


# Minimum seconds between IDLE notifications for the same session.
# Prevents spam from rapid IDLE→BUSY→IDLE cycling (e.g. user sends
# quick command, Claude responds in <1s, hook fires idle again).
IDLE_DEBOUNCE: float = 10.0


@dataclass
class TrackedSession:
    name: str           # session name, e.g. "claude-dev"
    label: str          # short label, e.g. "dev"
    status: Status = Status.UNKNOWN
    last_msg_id: int | None = None   # most recent Telegram notification msg
    summary: str = ""                # last pane summary (for idle notifications)
    last_idle_at: float = 0.0        # monotonic timestamp of last IDLE transition
    transcript_path: str = ""        # path to Claude Code JSONL transcript
    # Live busy-status tracking
    busy_msg_id: int | None = None   # Telegram message_id of the "Working…" msg
    last_tool_edit_at: float = 0.0   # monotonic timestamp of last busy-msg edit
    last_tool_name: str = ""         # last tool name displayed in busy message
    # Animation state
    animate_task: Any = field(default=None, repr=False)  # asyncio.Task for spinner
    last_tool_summary: str = ""      # cached tool summary text for display
    last_token_pct: int = 0          # cached context % for display
    last_output_tokens: int = 0      # output tokens THIS TURN (delta from baseline)
    output_baseline: int | None = None  # total_output_tokens at first statusLine read this cycle
    busy_started_at: float = 0.0     # monotonic timestamp when BUSY started (for "thought Xs")
    # Queued messages (sent one-at-a-time when session becomes IDLE)
    pending_queue: list = field(default_factory=list)  # list of (text, trigger_msg_id)
    # Reply threading — Telegram message_id of the user's prompt that started this work
    trigger_msg_id: int | None = None
    # Compact warning — prevents spamming "context high" alerts
    compact_warned: bool = False
    # Last injected prompt text — enables retry on API errors
    last_prompt: str = ""


class SessionRegistry:
    """Single source of truth for all tracked Claude sessions."""

    def __init__(self):
        self._sessions: dict[str, TrackedSession] = {}  # keyed by session name
        self._msg_map: dict[int, str] = {}  # message_id → session name
        self.last_active_session: str = ""  # last session that sent a notification

    def get(self, name: str) -> TrackedSession | None:
        return self._sessions.get(name)

    def get_or_create(self, name: str) -> TrackedSession:
        if name not in self._sessions:
            label = name.removeprefix("claude-") if name.startswith("claude-") else name
            self._sessions[name] = TrackedSession(name=name, label=label)
            log.info("Tracking new session: %s [%s]", name, label)
        return self._sessions[name]

    def transition(self, name: str, new_status: Status,
                   summary: str = "") -> TrackedSession | None:
        """Attempt a state transition. Returns session only if state actually changed.

        Idempotency: same-state calls return None (no duplicate notification).
        Debounce: IDLE transitions within IDLE_DEBOUNCE seconds of the last
        IDLE notification are suppressed (prevents spam from quick responses).
        """
        sess = self.get_or_create(name)
        if sess.status == new_status:
            return None  # no-op

        # Debounce: suppress rapid re-IDLE (e.g. user sends quick command,
        # Claude responds in <1s, triggers another idle notification)
        if new_status == Status.IDLE:
            now = time.monotonic()
            if sess.last_idle_at and (now - sess.last_idle_at) < IDLE_DEBOUNCE:
                log.debug("[%s] IDLE debounced (%.1fs since last)", sess.label,
                          now - sess.last_idle_at)
                sess.status = new_status  # update state silently
                if summary:
                    sess.summary = summary
                return None  # don't notify
            sess.last_idle_at = now

        # Reset idle timer when entering BUSY so next IDLE always notifies
        if new_status == Status.BUSY:
            sess.last_idle_at = 0.0

        old = sess.status
        sess.status = new_status
        if summary:
            sess.summary = summary

        log.info("[%s] %s → %s", sess.label, old.name, new_status.name)
        return sess

    def track_message(self, msg_id: int, session_name: str) -> None:
        """Associate a Telegram message_id with a session (for reply lookups)."""
        self._msg_map[msg_id] = session_name
        self.last_active_session = session_name
        sess = self._sessions.get(session_name)
        if sess:
            sess.last_msg_id = msg_id

    def get_session_by_msg(self, msg_id: int) -> TrackedSession | None:
        """Find session that owns a Telegram message."""
        name = self._msg_map.get(msg_id)
        if name:
            return self._sessions.get(name)
        return None

    def remove_message(self, msg_id: int) -> None:
        self._msg_map.pop(msg_id, None)

    def all_sessions(self) -> dict[str, TrackedSession]:
        return dict(self._sessions)

    def remove(self, name: str) -> None:
        sess = self._sessions.pop(name, None)
        if sess:
            # Clean up message mappings
            to_remove = [mid for mid, sn in self._msg_map.items() if sn == name]
            for mid in to_remove:
                del self._msg_map[mid]
