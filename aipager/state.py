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

from aipager.scope import strip_scope_suffix
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
# Max GONE entries retained for `/resume` history. When a new session is
# created and the GONE count exceeds this, the oldest-by-`gone_at` entry
# is evicted. Lives on disk in aipager-sessions.json — kept here so
# /clear_gone still gives users a manual "forget everything" lever.
MAX_GONE_HISTORY: int = 50


def _default_scope() -> tuple[int, str] | None:
    """Resolve the single configured chat for backfilling legacy sessions.

    Returns ``(chat_id, kind)`` or ``None`` when nothing is configured
    (e.g. unit tests with no env). Read at call time so tests can
    monkeypatch ``config``.

    - One v2 scope configured → its ``(chat_id, kind)``.
    - Multiple → the group scope (the §7 "prefer group" rule; only
      reachable once multi-scope auth lands).
    - Else fall back to ``CHAT_ID`` (kind inferred from its sign).
    """
    from aipager import config

    scopes = getattr(config, "SCOPES", None)
    if scopes:
        if len(scopes) == 1:
            return scopes[0].chat_id, scopes[0].kind
        for s in scopes:
            if s.kind == "group":
                return s.chat_id, "group"
        return scopes[0].chat_id, scopes[0].kind

    raw = getattr(config, "CHAT_ID", "") or ""
    try:
        cid = int(raw)
    except (TypeError, ValueError):
        return None
    return cid, ("group" if cid < 0 else "dm")


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

# Preference field name -> the TrackedSession attribute holding that
# field's session-level override. Shared by preference_overrides() below
# and by the Mini App's per-session preference routes, so there is one
# place that knows the field<->attribute mapping rather than a second
# hand-maintained copy that could silently drift out of step.
PREFERENCE_OVERRIDE_FIELDS: dict[str, str] = {
    "layout": "override_layout",
    "simple_formatting": "override_simple_formatting",
    "answer_length": "override_answer_length",
    "language_level": "override_language_level",
}


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
    # Wall-clock twin of busy_started_at, comparable against transcript
    # mtimes (which are wall-clock). Kept separate because busy_started_at
    # is shifted forward while a permission prompt is waiting so the
    # "thought Xs" display excludes that wait — shifting this one would
    # let the idle-recovery guard accept a pre-turn transcript again.
    busy_started_wall: float = 0.0
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
    # Monotonic timestamp of the most recent PreToolUse hook when the
    # matching PostToolUse hasn't fired yet — i.e. a tool call is
    # legitimately in flight, so the "no hooks for 2 min" stale detector
    # should stand down. Never persisted: a monotonic value is
    # meaningless across daemon restarts, and a fresh daemon should
    # start with in-flight state clear.
    pending_tool_started_at: float | None = None
    # Monotonic timestamp of the most recent PreCompact hook when the
    # matching post-compact SessionStart hasn't fired yet — compaction
    # can take minutes on a large transcript with no hooks in between,
    # which would otherwise trip the stale-busy detector. Not persisted
    # (same reason as pending_tool_started_at above).
    compact_started_at: float | None = None
    # Monotonic deadline until which this session is being deliberately
    # restarted (``/perms`` kills and relaunches to change the permission
    # flag). The old process's SessionEnd hook and the monitor noticing its
    # socket vanish both mean "expected", not "crashed", inside this window,
    # so neither raises an alarm. A deadline rather than a boolean because a
    # flag that fails to clear would silence real crashes forever; this one
    # expires on its own. Not persisted — a restart never spans a daemon
    # restart.
    restarting_until: float = 0.0
    # Team-mode attribution (None in personal mode). `created_by` is the
    # user who first created/owns the session; `last_driver` is whoever
    # most recently injected a prompt into it — used to attribute
    # auto-deny rule triggers and to display "🚗 @driver" in the pinned
    # dashboard.
    created_by_user_id: int | None = None
    last_driver_user_id: int | None = None
    # Resume support. `claude_session_id` is the UUID of the Claude Code
    # transcript (derived from `Path(transcript_path).stem`) and is what
    # `claude --resume <id>` consumes. `cwd` is the working dir the
    # session was launched from — Claude organizes transcripts by encoded
    # cwd, so resume must launch from the same dir. `gone_at` stamps the
    # GONE transition (wall-clock) and drives the MAX_GONE_HISTORY LRU.
    # `last_assistant_preview` is a short (~200 chars) extract of the
    # session's final assistant message, shown next to the entry in the
    # /resume picker and after a successful resume.
    claude_session_id: str = ""
    cwd: str = ""
    gone_at: float | None = None
    last_assistant_preview: str = ""
    # /status-only visibility flag. Set to True by the "Clear gone
    # sessions" button so users can declutter the live dashboard
    # without losing resume metadata. Only consulted when
    # status == GONE; ignored otherwise. /resume picker ignores this
    # flag entirely so previously-hidden sessions remain resumable.
    hidden_from_status: bool = False
    # Permission mode for this session. When True, claude runs with
    # --dangerously-skip-permissions (Auto mode, no per-tool prompts).
    # When False (default), claude prompts before each tool call (Ask mode).
    skip_perms: bool = False
    # Per-session preference overrides (batch 4's "Per session settings").
    # `None` means unset/inherit the scope's own /settings value — never a
    # legal value for any of these four: layout/answer_length/
    # language_level are closed enumerations that never include None, and
    # simple_formatting's own legal `False` must not be confused with
    # "unset", which is exactly why `None` alone (not `False`, not a
    # missing-key sentinel) is the unset marker — it is disjoint from
    # every field's real values. Never read directly: resolve together
    # with the scope's own preferences via
    # `aipager.preferences.resolve_preferences(scope_chat_id,
    # sess.preference_overrides())`, the same rule for chat and the Mini
    # App. Persisted like `skip_perms` above (a per-session boolean that
    # changes behaviour); intentionally NOT cleared by the `/new` replace
    # flow (callbacks.py) — see design.md Risks.
    override_layout: str | None = None
    override_simple_formatting: bool | None = None
    override_answer_length: str | None = None
    override_language_level: str | None = None
    # Multi-scope (Phase B): which Telegram chat this session belongs to.
    # All outbound notifications for the session route here instead of the
    # global CHAT_ID. `scope_chat_id == 0` means "not yet stamped" — the
    # notify resolver falls back to CHAT_ID, and `load()` backfills the
    # value from the single configured chat. `scope_kind` is "dm"|"group".
    scope_chat_id: int = 0
    scope_kind: str = ""
    # Origin of the most recent prompt (Phase D). "telegram" or "terminal".
    # Transient (NOT persisted). Defaults fail-closed to "telegram" so the
    # safety boundary (Phase E) treats unknown/between-turn state as
    # restricted; a markerless terminal prompt flips it to "terminal".
    last_prompt_origin: str = "telegram"
    # Concurrency guard for `_send_busy_and_animate` — closes the race where
    # two coroutines could both observe `busy_msg_id is None` and both send.
    # Transient; never persisted.
    animate_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    # Rich-message streaming state — transient, never in _PERSIST_FIELDS.
    # stream_offset: byte offset into the transcript for the current turn;
    #   seeded to the file size at turn start so a new turn never reads
    #   the previous turn's text.
    # stream_transcript_path: the transcript file pinned at turn-seed time.
    #   _read_stream_text reads exclusively from this path for the whole turn,
    #   so the offset can never be applied to a file it wasn't measured against.
    # stream_commentary: Claude's prose blocks for this turn, each paired with
    #   the tool_history index of the row that follows it in the transcript.
    #   That anchor is what lets the card interleave prose with tool rows in
    #   the order they actually happened; tool_history itself must keep its
    #   shape because active_subagents[...]["history_idx"] indexes into it.
    # stream_tool_cursor: how far into tool_history the transcript scan has
    #   walked this turn — the anchor handed to the next prose block. Only
    #   used by the transcript fallback; the MessageDisplay hook anchors on
    #   len(tool_history) at arrival, which is accurate because the hook
    #   fires as the prose is generated rather than seconds later.
    # stream_msg_id: Claude's message id for the commentary block currently
    #   being appended to, so a later chunk of the same message grows that
    #   block instead of starting a new row.
    # stream_batch_since: when the first tool row of the current unattributed
    #   batch was recorded, or None when every row has prose above it. Rows
    #   from the floor up are held out of the live card until that prose
    #   arrives, because a short preamble reaches the hook only at message
    #   end — measured at 20-515 ms after the batch's first PreToolUse — and
    #   drawing the row first made the quote jump in above it a moment later.
    # stream_anchor_floor: where the next new prose block is anchored — the
    #   tool count as of the last block that was placed. A short preamble is
    #   flushed to the hook at message end, i.e. after Claude has already
    #   emitted that message's tool calls and their PreToolUse rows landed,
    #   so anchoring on the live count puts prose below the tools it
    #   introduced. The rows since the last block are exactly that message's,
    #   so the floor is where the prose belongs.
    # stream_hook_live: set once a MessageDisplay chunk has been seen from
    #   this session, which proves the hook is wired. From then on the
    #   transcript is never read for prose, so the two sources cannot both
    #   deliver the same text. Session-scoped, not per-turn: the capability
    #   does not come and go mid-session.
    # stream_dirty: set True by hook handlers when the card needs a redraw;
    #   cleared by _edit_busy_rich after a successful POST.
    # stream_last_rendered: last markdown successfully POSTed; used to skip
    #   byte-identical edits before hitting the network.
    stream_offset: int = 0
    stream_transcript_path: str = ""
    stream_commentary: list[tuple[int, str]] = field(default_factory=list, repr=False)
    stream_tool_cursor: int = 0
    stream_msg_id: str = ""
    stream_anchor_floor: int = 0
    stream_batch_since: float | None = field(default=None, repr=False)
    stream_hook_live: bool = False
    stream_dirty: bool = False
    stream_last_rendered: str = ""

    def queue_prompt(self, text: str, msg_id: int | None,
                     cap: int = QUEUE_CAP) -> bool:
        """Append a queued prompt with a current wall-clock timestamp.

        Returns False (without modifying the queue) if the queue is
        already at ``cap`` entries — the caller is expected to surface
        the rejection to the user. New entries are stored as
        ``(text, msg_id, queued_at)`` 3-tuples; existing 2-tuples loaded
        from older state files retain their shape (see ``load`` for the
        compatibility shim).

        ``msg_id`` is ``None`` for a prompt with no originating Telegram
        message to attribute a reply to (the Mini App's Compact route) —
        already the documented meaning of ``trigger_msg_id: int | None``
        downstream, so this only makes the type honest about a case that
        already occurs.
        """
        if len(self.pending_queue) >= cap:
            log.warning("[%s] queue full (cap=%d); rejecting prompt: %r",
                        self.label, cap, text[:60])
            return False
        self.pending_queue.append((text, msg_id, time.time()))
        return True

    def is_restarting(self) -> bool:
        """True while a deliberate kill-and-relaunch is still in flight."""
        return time.monotonic() < self.restarting_until

    def preference_overrides(self) -> dict:
        """Only the fields this session has explicitly set — the exact
        shape `aipager.preferences.resolve_preferences` expects as its
        `session_overrides` argument. An unset field (`None`) is omitted
        entirely rather than included as `None`: `resolve_preferences`
        treats an *absent* key as "fall back to scope", so sending `None`
        explicitly would need a second special case instead of just not
        being there.

        A session created (or recreated in place — see `override_layout`'s
        docstring above) fresh from `SessionRegistry.get_or_create` starts
        with every `override_*` at its `None` default, so this returns
        `{}` until the Mini App writes to it — closing the label-reuse
        collision by construction, not by a cleanup step that could be
        forgotten.
        """
        return {
            field: getattr(self, attr)
            for field, attr in PREFERENCE_OVERRIDE_FIELDS.items()
            if getattr(self, attr) is not None
        }

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
        # Open a batch on the first row that has no prose above it yet. Every
        # append lands here, so no call site can add a row the live card would
        # draw before the sentence introducing it.
        if self.stream_batch_since is None:
            self.stream_batch_since = time.monotonic()
        new_idx = len(self.tool_history) - 1
        if len(self.tool_history) > TOOL_HISTORY_CAP:
            drop = len(self.tool_history) - TOOL_HISTORY_CAP
            del self.tool_history[:drop]
            new_idx -= drop
            for info in self.active_subagents.values():
                if "history_idx" in info and info["history_idx"] is not None:
                    info["history_idx"] -= drop
            # The card's anchors index into the same list, so they shift with
            # it. Left unshifted they would drift toward the top of the
            # timeline on a turn long enough to trim, quietly putting prose
            # above tools it never introduced.
            self.stream_anchor_floor = max(0, self.stream_anchor_floor - drop)
            self.stream_tool_cursor = max(0, self.stream_tool_cursor - drop)
            self.stream_commentary = [
                (max(0, anchor - drop), text)
                for anchor, text in self.stream_commentary
            ]
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
            # Strip the scope disambiguator too, not just the prefix:
            # a discovered session would otherwise wear its INTERNAL
            # name everywhere the label is shown (grid, gone list,
            # /status, keyboard) — "Jkhk__d256113222" for a session
            # the operator named "Jkhk".
            core = name.removeprefix("claude-") if name.startswith("claude-") else name
            label = strip_scope_suffix(core)
            self._sessions[name] = TrackedSession(name=name, label=label)
            log.info("Tracking new session: %s [%s]", name, label)
            self._evict_gone_overflow()
        return self._sessions[name]

    def _evict_gone_overflow(self) -> None:
        """Keep at most ``MAX_GONE_HISTORY`` GONE entries in the registry.

        Sorted by ``gone_at`` ascending so the oldest die first. A GONE
        entry without a ``gone_at`` stamp (legacy / pre-feature) sorts as
        oldest and is evicted preferentially.
        """
        gone = [
            (s.gone_at if s.gone_at is not None else 0.0, n)
            for n, s in self._sessions.items()
            if s.status == Status.GONE
        ]
        if len(gone) <= MAX_GONE_HISTORY:
            return
        gone.sort()
        for _, name in gone[: len(gone) - MAX_GONE_HISTORY]:
            log.info("Evicting GONE session from history (LRU): %s", name)
            self._sessions.pop(name, None)
            self._dirty = True

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
            # Stamp the turn start here rather than at any single call
            # site: several paths enter BUSY (Telegram prompt, terminal
            # UserPromptSubmit, permission answer, the INTERACTIVE
            # watchdog), and a path that missed the stamp would leave the
            # idle-recovery guard permanently unsatisfied.
            #
            # Coming back from INTERACTIVE is a permission answer resuming
            # the SAME turn, so the existing stamp is still the true turn
            # start — re-stamping there would move the marker past writes
            # this turn already made and blind the guard for the rest of
            # it. (BUSY→BUSY can't reach here; same-state returns above.)
            if sess.status != Status.INTERACTIVE:
                sess.busy_started_wall = time.time()

        old = sess.status
        sess.status = new_status
        if summary:
            sess.summary = summary

        # Always stamp gone_at on the canonical GONE transition. Two
        # paths reach GONE — session_monitor (socket disappeared) and
        # hook_receiver (Claude's SessionEnd hook) — but only the
        # former used to stamp the timestamp. Centralizing here means
        # /resume always has a "X ago" to display, regardless of which
        # path fired. If a caller has already stamped gone_at (legacy
        # behaviour) we preserve it.
        if new_status == Status.GONE and sess.gone_at is None:
            sess.gone_at = time.time()

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

    def all_sessions(
        self, scope_chat_id: int | None = None,
    ) -> dict[str, TrackedSession]:
        """All tracked sessions, optionally filtered to a scope.

        Multi-scope safe: when ``scope_chat_id`` is given, returns only
        sessions belonging to that chat. A not-yet-stamped session
        (``scope_chat_id == 0``) matches any scope — same legacy-tolerant
        rule as :meth:`find_by_label` / :meth:`live_labels`. ``None``
        (the default) returns everything, preserving single-scope callers.
        """
        if scope_chat_id is None:
            return dict(self._sessions)
        return {
            name: s for name, s in self._sessions.items()
            if s.scope_chat_id in (0, scope_chat_id)
        }

    def find_by_label(
        self, label: str, scope_chat_id: int | None = None,
        *, include_gone: bool = False,
    ) -> TrackedSession | None:
        """Resolve a session by its user-facing label within a scope.

        Multi-scope safe: a label may repeat across scopes, so callers
        pass the calling chat's ``scope_chat_id`` to disambiguate. When
        ``scope_chat_id`` is None, matches by label alone (legacy /
        single-scope callers). Skips GONE sessions unless
        ``include_gone``. Works for both flat (grandfathered) and
        suffixed session names because it matches on ``sess.label``,
        never on parsing the name.
        """
        for sess in self._sessions.values():
            if sess.label != label:
                continue
            if scope_chat_id is not None and sess.scope_chat_id not in (
                0, scope_chat_id,
            ):
                continue
            if not include_gone and sess.status == Status.GONE:
                continue
            return sess
        return None

    def live_labels(self, scope_chat_id: int | None = None) -> set[str]:
        """Labels of non-GONE sessions, optionally filtered to a scope."""
        out: set[str] = set()
        for sess in self._sessions.values():
            if sess.status == Status.GONE or not sess.label:
                continue
            if scope_chat_id is not None and sess.scope_chat_id not in (
                0, scope_chat_id,
            ):
                continue
            out.add(sess.label)
        return out

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
        # Team-mode attribution — preserved across restarts so the
        # pinned dashboard remembers who's driving each session.
        "created_by_user_id", "last_driver_user_id",
        # Resume support — see TrackedSession docstring.
        "claude_session_id", "cwd", "gone_at", "last_assistant_preview",
        "hidden_from_status",
        # Permission mode — persisted so daemon restarts preserve Auto/Ask.
        "skip_perms",
        # Multi-scope routing.
        "scope_chat_id", "scope_kind",
        # Per-session preference overrides — see TrackedSession docstring.
        "override_layout", "override_simple_formatting",
        "override_answer_length", "override_language_level",
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

        # Resolve the default scope once (for backfilling legacy sessions).
        _default = _default_scope()

        # Reconstruct sessions with only persistable fields; transient fields
        # stay at dataclass defaults. Status → IDLE, last_idle_at → 0.0 so
        # the first real IDLE notification is not debounced.
        for name, sd in data.get("sessions", {}).items():
            gone_at_raw = sd.get("gone_at")
            try:
                gone_at = float(gone_at_raw) if gone_at_raw is not None else None
            except (TypeError, ValueError):
                gone_at = None
            # Backfill: sessions saved with claude_session_id but no
            # gone_at (orphans from the SessionEnd hook path that
            # forgot to stamp pre-v0.4) get their "last activity"
            # timestamp derived from the transcript file's mtime —
            # the most accurate fallback available. Without this they
            # would render as "long ago" in /resume; with it they
            # show a real relative timestamp.
            backfilled = False
            if gone_at is None and sd.get("claude_session_id"):
                tp = sd.get("transcript_path", "")
                if tp:
                    try:
                        gone_at = Path(tp).stat().st_mtime
                        backfilled = True
                    except OSError:
                        pass
            # Sessions that were GONE at save time stay GONE on load —
            # the dtach socket is gone too, so session_monitor would
            # mark them GONE again on its first scan anyway. Preserving
            # the state up-front keeps `gone_at` meaningful for /resume.
            initial_status = Status.GONE if gone_at is not None else Status.UNKNOWN
            sess = TrackedSession(
                name=sd.get("name", name),
                label=sd.get("label", name),
                status=initial_status,
                last_msg_id=sd.get("last_msg_id"),
                transcript_path=sd.get("transcript_path", ""),
                trigger_msg_id=sd.get("trigger_msg_id"),
                last_prompt=sd.get("last_prompt", ""),
                last_idle_at=0.0,
                busy_msg_id=sd.get("busy_msg_id"),
                created_by_user_id=sd.get("created_by_user_id"),
                last_driver_user_id=sd.get("last_driver_user_id"),
                claude_session_id=sd.get("claude_session_id", ""),
                cwd=sd.get("cwd", ""),
                gone_at=gone_at,
                last_assistant_preview=sd.get("last_assistant_preview", ""),
                hidden_from_status=sd.get("hidden_from_status", False),
                skip_perms=sd.get("skip_perms", False),
                scope_chat_id=int(sd.get("scope_chat_id", 0) or 0),
                scope_kind=sd.get("scope_kind", ""),
                # Raw passthrough, exactly like every other field above —
                # NOT re-validated here. A hand-edited or since-invalidated
                # value is caught at resolution time by
                # preferences.resolve_preferences's is_valid_value check
                # (fails safe to "unset" for that one field alone), not at
                # load time, matching the fail-safe philosophy the scope
                # store already uses in preferences._resolve_field.
                override_layout=sd.get("override_layout"),
                override_simple_formatting=sd.get("override_simple_formatting"),
                override_answer_length=sd.get("override_answer_length"),
                override_language_level=sd.get("override_language_level"),
            )
            # Multi-scope backfill: stamp legacy sessions (scope_chat_id == 0)
            # with the single configured chat so notify routing is explicit.
            if sess.scope_chat_id == 0 and _default is not None:
                sess.scope_chat_id, sess.scope_kind = _default
                backfilled = True
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
            if backfilled:
                log.info("[%s] backfilled gone_at from transcript mtime "
                         "(orphan session — was missing the stamp)",
                         sess.label)
                self._dirty = True  # persist on next save_if_dirty

        log.info("Loaded %d sessions, %d message mappings",
                 len(self._sessions), len(self._msg_map))
        # `_dirty` may have been set True by the backfill loop above so
        # the next save_if_dirty cycle persists the derived gone_at.
        # Otherwise it stays False (load alone doesn't dirty the state).

    def save_if_dirty(self) -> None:
        """Save state if it has been modified since last save."""
        if self._dirty:
            self.save()
            self._dirty = False
