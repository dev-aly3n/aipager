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

# --- Bounded-growth constants (item 2.3, 2.4 of group B hardening) -----
#
# `pending_queue` is capped at this many entries; the 51st `queue_prompt`
# call returns False and the caller surfaces "queue full" feedback.
QUEUE_CAP: int = 50
# Persisted queue entries older than this are dropped at load time.
QUEUE_MAX_AGE_SECONDS: float = 86400.0  # 24h
# `tool_history` is trimmed to the most recent N entries on each append.
TOOL_HISTORY_CAP: int = 200


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
    # Cost tracking (item 4.6) — cumulative session cost from statusLine,
    # plus a baseline captured at BUSY-start so the busy message can show
    # "$ this turn" instead of lifetime cost.
    last_cost_usd: float = 0.0
    cost_baseline: float | None = None
    # Subagent count THIS TURN (item 4.5) — increment per subagent_start,
    # reset on BUSY transition.
    subagent_count_this_turn: int = 0
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
    # Active subagents — keyed by agent_id
    # Format: {agent_id: {"type": str, "started_at": float, "history_idx": int}}
    active_subagents: dict = field(default_factory=dict)
    # Stale session detection
    last_hook_at: float = 0.0        # monotonic timestamp of last hook event received
    stale_warned: bool = False       # prevents re-alerting every scan cycle
    # Concurrency guard for `_send_busy_and_animate` — closes the race where
    # two coroutines could both observe `busy_msg_id is None` and both send.
    # Transient; never persisted.
    animate_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def queue_prompt(self, text: str, msg_id: int,
                     cap: int = QUEUE_CAP) -> bool:
        """Append a queued prompt with a current wall-clock timestamp.

        Returns False (without modifying the queue) if the queue is
        already at ``cap`` entries — the caller is expected to surface
        the rejection to the user. New entries are stored as
        ``(text, msg_id, queued_at)`` 3-tuples; existing 2-tuples loaded
        from older state files retain their shape (see ``load`` for the
        compatibility shim).
        """
        if len(self.pending_queue) >= cap:
            log.warning("[%s] queue full (cap=%d); rejecting prompt: %r",
                        self.label, cap, text[:60])
            return False
        self.pending_queue.append((text, msg_id, time.time()))
        return True

    def record_tool(self, summary: str, done: bool | str = False) -> int:
        """Append to ``tool_history``, trim to cap, return the new index.

        Multi-day sessions can accumulate thousands of tool invocations;
        we only display the last few anyway, so keeping unbounded history
        is wasted memory. When the cap is exceeded, oldest entries are
        dropped from the front. The returned index is the **absolute**
        position of the just-appended entry **after** trimming — callers
        that store it (e.g. for subagent bookkeeping) should subtract by
        the amount dropped, which this method does automatically by
        shifting any persisted ``history_idx`` references in
        ``active_subagents``.
        """
        self.tool_history.append((summary, done))
        new_idx = len(self.tool_history) - 1
        if len(self.tool_history) > TOOL_HISTORY_CAP:
            drop = len(self.tool_history) - TOOL_HISTORY_CAP
            del self.tool_history[:drop]
            new_idx -= drop
            for info in self.active_subagents.values():
                if "history_idx" in info and info["history_idx"] is not None:
                    info["history_idx"] -= drop
        return new_idx


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
        "model_name", "busy_msg_id",
    )
    _MAX_MSG_MAP = 1000  # cap _msg_map entries to avoid unbounded growth

    def save(self) -> None:
        """Serialize persistable state to JSON (atomic write)."""
        sessions = {}
        for name, sess in self._sessions.items():
            d: dict = {}
            for f in self._PERSIST_FIELDS:
                val = getattr(sess, f)
                if f == "pending_queue":
                    # tuples → lists for JSON. Always persist as 3-tuples
                    # so future loads can apply the TTL even if the queue
                    # contained legacy 2-tuples (now upgraded in load()).
                    normalized = []
                    for item in val:
                        if len(item) == 3:
                            normalized.append(list(item))
                        elif len(item) == 2:
                            normalized.append([item[0], item[1], time.time()])
                    val = normalized
                elif f == "busy_msg_id":
                    # Never persist sentinel (-1) or None
                    val = val if val and val > 0 else None
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
                busy_msg_id=sd.get("busy_msg_id"),
            )
            # pending_queue: accept old 2-tuples and new 3-tuples
            # (text, msg_id, queued_at). Drop entries older than the
            # TTL so a daemon that was down for days doesn't flush
            # stale prompts the moment a session goes IDLE.
            now = time.time()
            ttl_cutoff = now - QUEUE_MAX_AGE_SECONDS
            dropped = 0
            for item in sd.get("pending_queue", []):
                if not isinstance(item, list):
                    continue
                if len(item) == 2:
                    # Legacy: no timestamp — treat as fresh.
                    sess.pending_queue.append(
                        (item[0], item[1], now)
                    )
                elif len(item) == 3:
                    text, msg_id, queued_at = item
                    try:
                        queued_at_f = float(queued_at)
                    except (TypeError, ValueError):
                        queued_at_f = now
                    if queued_at_f < ttl_cutoff:
                        dropped += 1
                        continue
                    sess.pending_queue.append((text, msg_id, queued_at_f))
            if dropped:
                log.warning(
                    "[%s] dropped %d queue entries older than %d h",
                    sess.label, dropped, int(QUEUE_MAX_AGE_SECONDS / 3600),
                )
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
