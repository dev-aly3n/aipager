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
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from aipager.config import SESSION_STATE_FILE

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
    last_tool_summary: str = ""      # cached tool summary text (current tool)
    # Tool history for busy message — list of (summary, done_bool)
    tool_history: list = field(default_factory=list)
    model_name: str = ""              # e.g. "Opus 4.6" from statusLine
    last_token_pct: int = 0          # cached context % for display
    last_output_tokens: int = 0      # output tokens THIS TURN (delta from baseline)
    output_baseline: int | None = None  # total_output_tokens at first statusLine read this cycle
    # Lines changed tracking (lazy baseline, same pattern as output tokens)
    lines_added_baseline: int | None = None
    lines_removed_baseline: int | None = None
    last_lines_added: int = 0       # lines added THIS TURN (delta)
    last_lines_removed: int = 0     # lines removed THIS TURN (delta)
    busy_started_at: float = 0.0     # monotonic timestamp when BUSY started (for "thought Xs")
    # Queued messages (sent one-at-a-time when session becomes IDLE)
    pending_queue: list = field(default_factory=list)  # list of (text, trigger_msg_id)
    # Reply threading — Telegram message_id of the user's prompt that started this work
    trigger_msg_id: int | None = None
    # Compact warning — prevents spamming "context high" alerts
    compact_warned: bool = False
    # Context % before compaction (for delta display in compact_done notification)
    pre_compact_pct: int = 0
    # Last injected prompt text — enables retry on API errors
    last_prompt: str = ""
    # Inline permission context (tool_info, question, etc.) — set when permission
    # is displayed inside the busy message instead of as a separate message
    pending_permission: dict | None = None
    # Stale session detection
    last_hook_at: float = 0.0        # monotonic timestamp of last hook event received
    stale_warned: bool = False       # prevents re-alerting every scan cycle


class SessionRegistry:
    """Single source of truth for all tracked Claude sessions."""

    def __init__(self):
        self._sessions: dict[str, TrackedSession] = {}  # keyed by session name
        self._msg_map: dict[int, str] = {}  # message_id → session name
        self.last_active_session: str = ""  # last session that sent a notification
        self.pinned_msg_id: int = 0  # pinned status message in Telegram
        self._dirty: bool = False

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
            sess.stale_warned = False

        old = sess.status
        sess.status = new_status
        if summary:
            sess.summary = summary

        log.info("[%s] %s → %s", sess.label, old.name, new_status.name)
        self._dirty = True
        return sess

    def track_message(self, msg_id: int, session_name: str) -> None:
        """Associate a Telegram message_id with a session (for reply lookups)."""
        self._msg_map[msg_id] = session_name
        self.last_active_session = session_name
        sess = self._sessions.get(session_name)
        if sess:
            sess.last_msg_id = msg_id
        self._dirty = True

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

    def mark_dirty(self) -> None:
        """Flag that state has changed and needs saving."""
        self._dirty = True

    # -- Persistence ---------------------------------------------------------

    _PERSIST_FIELDS = (
        "name", "label", "last_msg_id", "transcript_path",
        "trigger_msg_id", "pending_queue", "last_prompt",
        "model_name",
    )
    _MAX_MSG_MAP = 100  # cap _msg_map entries to avoid unbounded growth

    def save(self) -> None:
        """Serialize persistable state to JSON (atomic write)."""
        sessions = {}
        for name, sess in self._sessions.items():
            d: dict = {}
            for f in self._PERSIST_FIELDS:
                val = getattr(sess, f)
                if f == "pending_queue":
                    # tuples → lists for JSON
                    val = [list(item) for item in val]
                d[f] = val
            sessions[name] = d

        # Cap _msg_map: keep only the most recent entries (by insertion order)
        msg_map = self._msg_map
        if len(msg_map) > self._MAX_MSG_MAP:
            keys = list(msg_map.keys())
            msg_map = {k: msg_map[k] for k in keys[-self._MAX_MSG_MAP:]}

        data = {
            "version": 1,
            "last_active_session": self.last_active_session,
            "pinned_msg_id": self.pinned_msg_id,
            "msg_map": {str(k): v for k, v in msg_map.items()},
            "sessions": sessions,
        }

        state_file = Path(SESSION_STATE_FILE)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2))
            os.rename(tmp, state_file)  # atomic on Linux
        except OSError:
            log.exception("Failed to save session state")
            tmp.unlink(missing_ok=True)

    def load(self) -> None:
        """Load persisted state from JSON. Missing or corrupt file → start fresh."""
        state_file = Path(SESSION_STATE_FILE)
        try:
            raw = state_file.read_text()
        except FileNotFoundError:
            log.info("No saved session state — starting fresh")
            return
        except OSError:
            log.warning("Cannot read session state file — starting fresh")
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Corrupt session state JSON — starting fresh")
            return

        self.last_active_session = data.get("last_active_session", "")
        self.pinned_msg_id = data.get("pinned_msg_id", 0)

        # Rebuild _msg_map (JSON keys are strings → convert to int)
        saved_map = data.get("msg_map", {})
        for k, v in saved_map.items():
            try:
                self._msg_map[int(k)] = v
            except (ValueError, TypeError):
                continue

        # Reconstruct sessions with only persistable fields; transient fields
        # stay at dataclass defaults. Status → IDLE, last_idle_at → 0.0 so
        # the first real IDLE notification is not debounced.
        for name, sd in data.get("sessions", {}).items():
            sess = TrackedSession(
                name=sd.get("name", name),
                label=sd.get("label", name),
                status=Status.UNKNOWN,
                last_msg_id=sd.get("last_msg_id"),
                transcript_path=sd.get("transcript_path", ""),
                trigger_msg_id=sd.get("trigger_msg_id"),
                last_prompt=sd.get("last_prompt", ""),
                last_idle_at=0.0,
            )
            # pending_queue: list of [text, trigger_msg_id] → list of tuples
            for item in sd.get("pending_queue", []):
                if isinstance(item, list) and len(item) == 2:
                    sess.pending_queue.append(tuple(item))
            self._sessions[name] = sess
            log.info("Restored session: %s [%s] (msg_id=%s, trigger=%s, queue=%d)",
                     name, sess.label, sess.last_msg_id, sess.trigger_msg_id,
                     len(sess.pending_queue))

        log.info("Loaded %d sessions, %d message mappings",
                 len(self._sessions), len(self._msg_map))
        self._dirty = False

    def save_if_dirty(self) -> None:
        """Save state if it has been modified since last save."""
        if self._dirty:
            self.save()
            self._dirty = False
