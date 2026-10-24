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
import itertools
import json
import logging
import os
import time
import weakref
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from aipager import agent_tree as _agent_tree
from aipager import bg_shells as _bg_shells
from aipager import perm_modes as _perm_modes
from aipager._test_guard import check_write
from aipager import prompt_stack as _prompt_stack
from aipager.open_prompt import (
    snapshot as snapshot_open_prompt, validate as validate_open_prompt,
)
from aipager.scope import (
    chat_from_suffix, follow_chat_migrations, home_scope, load_chat_migrations,
    strip_scope_suffix,
)
from aipager.config import (
    GONE_SESSION_MAX_AGE_DAYS,
    COMPACT_INFLIGHT_MAX_SECONDS,
    SESSION_STATE_FILE,
    TOOL_INFLIGHT_MAX_SECONDS,
)

log = logging.getLogger(__name__)

#: `TrackedSession.turn_key`s: seeded from the clock (microseconds) when the
#: daemon starts, so no key handed out after a restart, or to a re-created
#: session, can equal one an old button still carries.
_TURN_KEYS = itertools.count(time.time_ns() // 1000)

# --- Bounded-growth constants (item 2.3, 2.4 of group B hardening) -----
#
# `pending_queue` is capped at this many entries; the 51st `queue_prompt`
# call returns False and the caller surfaces "queue full" feedback.
QUEUE_CAP: int = 50
# Persisted queue entries older than this are dropped at load time. This
# ALSO bounds a queue-handoff note's own on-disk lifetime
# (policy_snapshot.list_outstanding_notes), which in turn is how long a
# note keeps contributing to merge_snapshots' safety-floor computation
# (the owner-bypass flag a note carries) — 24h stays generous here
# deliberately, so a legitimately still-outstanding note (behind a
# long-running turn that hasn't picked it up yet) never loses its
# bypass just because this TTL was tightened. See
# MIXED_SENDER_HOLD_WINDOW_SECONDS below for the SEPARATE, much shorter
# bound that governs how long a note may block a different-looking
# sender — that one needed its own constant precisely so it could be
# tightened without touching this one.
QUEUE_MAX_AGE_SECONDS: float = 86400.0  # 24h
# How long a queue-handoff note may still cause the mixed-sender hold
# (transport.mixed_sender_note_outstanding) to fire, independent of
# QUEUE_MAX_AGE_SECONDS above. A genuine pick-up — either the normal
# UserPromptSubmit match, or the Stop-triggered sweep in
# policy_snapshot.expire_notes_after_turn_end — resolves within low
# single-digit seconds of the turn that wrote the note ending. A note
# the hold can still see after 30 minutes is therefore essentially
# certain to be an orphan (the live incident this fixes: 15 notes
# roughly 11-12h old, still "outstanding" and blocking the sole human's
# own later messages), not a legitimately in-flight one — 30 minutes is
# comfortably larger than any observed real pick-up latency while
# remaining far short of the 24h that let that incident block a whole
# day's messages.
MIXED_SENDER_HOLD_WINDOW_SECONDS: float = 1800.0  # 30 min

#: ``TrackedSession.turn_sender_id`` for a turn typed in the terminal.
TURN_SENDER_TERMINAL = "terminal"
#: ``TrackedSession.turn_sender_id`` for a turn several people's messages
#: started together.
TURN_SENDER_MIXED = "mixed"


def turn_sender_from_report(turn: dict) -> int | str | None:
    """``TrackedSession.turn_sender_id`` for the turn the hook reported on
    a UserPromptSubmit (the ``aipager_turn`` that
    ``notify_hook._match_and_promote`` reports):
    TURN_SENDER_TERMINAL for a terminal turn, the one author of the
    Telegram messages that started it, TURN_SENDER_MIXED for several, and
    None when no author is known (nothing is held then)."""
    if not isinstance(turn, dict):
        return None
    if turn.get("origin") in ("terminal", "peer", "scheduled"):
        # A turn started by a message from another session holds every
        # other sender's message like a terminal turn ("peer message
        # authority", C2): until the job ends, nobody's message joins it.
        # So does a turn this session's own scheduled prompt started.
        return TURN_SENDER_TERMINAL
    known = set(turn_authors_from_report(turn))
    if not known:
        return None
    if len(known) > 1:
        return TURN_SENDER_MIXED
    return next(iter(known))


def turn_authors_from_report(turn: dict) -> tuple[int, ...]:
    """The known Telegram authors of the messages that started the turn
    the hook reported (``aipager_turn["authors"]``), sorted; empty for a
    terminal turn or when none is known. Kept beside a TURN_SENDER_MIXED
    ``turn_sender_id`` (``TrackedSession.turn_mixed_authors``) so a live
    reload can tell whose turn it is (roadmap 8.80)."""
    if not isinstance(turn, dict) or turn.get("origin") == "terminal":
        return ()
    authors = turn.get("authors")
    if not isinstance(authors, list):
        return ()
    return tuple(sorted({a for a in authors if isinstance(a, int)
                         and not isinstance(a, bool) and a > 0}))


# `tool_history` is trimmed to the most recent N entries on each append.
TOOL_HISTORY_CAP: int = 200

# The longest a new turn's own hook events wait for its card state while an
# older turn's finish is out (roadmap 8.62): past it, they are handled anyway
# (as before the hold existed). Above the finish gate's own bound (30 s) plus
# the paced sends that follow it.
TURN_STATE_HOLD_SECONDS: float = 60.0

# How long after a Send now press a `dequeue` queue-operation still counts as
# Claude having cancelled the running step and taken its oldest queued
# message as a new prompt (roadmap 8.64). That take is written within a
# second of the chord; a dequeue later than this is some other pop.
SEND_NOW_DEQUEUE_WINDOW: float = 15.0
# `active_subagents` is held to this many entries at insertion; the oldest
# (by `started_at`, the same age the TTL sweep uses) is evicted when a new
# start would exceed it. The TTL sweep and SubagentStop pops are TIME and
# EVENT bounds — a single fan-out turn can outrun both inside the hour.
# 100 is >5x the worst fan-out ever recorded here (4 agents -> 18), so a
# legitimate run never hits it; a runaway one stops growing.
ACTIVE_SUBAGENTS_CAP: int = 100
# Each `active_subagents` entry's own `tools` list (its attributed tool
# summaries — "agent activity rows on the busy card") is held to this many
# entries. 100 is comfortably above the single largest per-agent tool
# count this feature's own worked example names (57), while bounding
# worst-case memory for concurrently active agents to
# ACTIVE_SUBAGENTS_CAP * AGENT_TOOLS_CAP = 10,000 short strings — the same
# order of magnitude TOOL_HISTORY_CAP=200 already accepts as reasonable
# for one session.
AGENT_TOOLS_CAP: int = 100
# `finished_subagents` — agents that have SubagentStop'd this turn — is
# held to this many entries, same value and rationale as
# ACTIVE_SUBAGENTS_CAP: the worst recorded fan-out is 4 agents -> 18 total
# calls, so 100 only ever engages on a runaway loop.
FINISHED_SUBAGENTS_CAP: int = 100
# How long a job stays open after its last background agent stops, waiting
# for the <task-notification> continuation turn Claude enqueues on agent
# completion (observed arriving within ~1s live; 60s is a generous bound).
# Armed only when an interim idle has already happened — a blocking
# subagent stopping mid-turn never arms it, so ordinary turns close
# exactly as before.
JOB_CONTINUATION_GRACE_SECONDS: float = 60.0
# Roadmap 8.41 — the background agents a session has running, for the
# status block under an answer and the pinned bar.
# How long an agent or shell an answer's block lists must have stayed gone
# before its row (or the block) shows it done. An agent that stops with background work of its
# own still running is followed by the parent's <task-notification> saying
# so within tens of milliseconds (live 2026-09-24 18:03:29.4); that note
# puts the agent back, and this window is what lets it land first. Short
# enough that nobody watching the chat notices the wait.
BG_AGENTS_SETTLE_SECONDS: float = 5.0
# How long a SKIPPED settle edit (flood budget refused it, or the chat was
# muted with no deadline known) waits before its one retry. A mute with a
# known deadline waits for the lift instead, whichever is later.
BG_AGENTS_RETRY_SECONDS: float = 60.0
# Stopped agents remembered (id -> entry) so the still-running note can
# restore one. Same bound as the table the ids come from.
BG_AGENTS_RECENT_CAP: int = 20
# Answers whose status block is still kept current, per session. Each is
# edited on its own (its final edit gets one retry). Several can be pending when a
# job's agent reports back more than once or the operator asks something
# while agents run; five is well above that, and bounds a runaway.
AGENTS_LINES_CAP: int = 5
# An answer's status block is edited at most once per this many seconds
# (its send counts as the first), and only when what it shows changed.
AGENTS_LINE_EDIT_INTERVAL: float = 30.0
# The Agent calls waiting for their SubagentStart (the session's own and
# its agents'), to give each started agent its call's description and,
# when it can be told, the agent that started it: at most this many, none
# older than AGENT_CALL_MAX_AGE seconds.
AGENT_CALLS_CAP: int = 40
AGENT_CALL_MAX_AGE: float = 600.0
# How much of a background agent's latest message is kept (its first
# line is what the answer's block shows).
BG_AGENT_PROSE_CAP: int = 1000
# A background agent that ended its turn while an agent it started still
# runs is WAITING, not done (it resumes when that agent reports). Once none
# of its own agents runs any more it has this long to resume (Claude Code
# resumes it within a second of its child's stop) before it stops counting
# as running: silence, never "done".
WAITING_PARENT_RESUME_SECONDS: float = 120.0
# The running agents a restart keeps (the state file's per-session
# "agents" record) are restored only from a file saved this recently:
# agents that ended while the daemon was down longer would read as running.
AGENTS_RESTORE_MAX_GAP_SECONDS: float = 1800.0
# `TrackedSession.agent_parents` (child -> the agent that started it) is
# held to this many links; the oldest goes first.
AGENT_PARENTS_CAP: int = 200
# An answer's status block lists at most this many agents: the ones
# running when it went out, plus the agents they start later. A row cap
# (agents_line.ROWS_MAX) bounds what it shows; this bounds what it keeps.
AGENTS_LINE_ITEMS_CAP: int = 100
# Background shells a session has running (`TrackedSession.bg_shells`) are
# held to this many at insertion; the oldest (by launch) is evicted when a
# new launch would exceed it. Shells send no hooks while they run, so
# nothing else bounds the table between an end that was never seen and the
# max-age sweep (session_monitor.BG_SHELL_MAX_TRACK_SECONDS). A busy
# session runs a handful at once; 50 only ever engages on a runaway loop.
BG_SHELLS_CAP: int = 50
# Ends seen for a task id no running shell has (`TrackedSession.
# bg_shell_early_ends`): kept this long, at most this many per session, so
# a launch handled after its own end (its PostToolUse held behind an
# earlier turn's finish, roadmap 8.62, while the wake-up is not) settles
# at once. The hold gives up after TURN_STATE_HOLD_SECONDS; this outlives
# it with room. Most entries are agents' and Monitors' ids that no launch
# ever claims, and simply expire.
BG_SHELL_EARLY_END_SECONDS: float = 300.0
BG_SHELL_EARLY_ENDS_CAP: int = 50
# Max GONE entries retained for `/resume` history. When a new session is
# created and the GONE count exceeds this, the oldest-by-`gone_at` entry
# is evicted. Lives on disk in aipager-sessions.json — kept here so
# /clear_gone still gives users a manual "forget everything" lever.
MAX_GONE_HISTORY: int = 50
# Roadmap 8.102: how many closed prompt surfaces a session remembers
# (``TrackedSession.closed_prompt_msgs``). A stale client shows the last
# few cards at most; older ids fall back to the answer taps' old handling.
MAX_CLOSED_PROMPT_SURFACES: int = 16
# How many SendUserFile call ids a session remembers as already copied
# (``TrackedSession.files_sent_ids``), newest kept: no call is copied or
# sent twice, a replayed datagram included.
MAX_FILES_SENT_IDS: int = 256
# Roadmap 8.120: how many unfinished tool calls are remembered per agent
# (``TrackedSession.agent_tool_calls``), newest kept.
AGENT_TOOL_CALLS_CAP: int = 32

# How many delivered answer digests a session remembers
# (``TrackedSession.delivered_digests``). Small on purpose: it exists to
# stop the SAME body coming back around a turn or two later, not to dedup
# a session's whole history — a legitimately repeated answer this many
# turns later still goes out.
DELIVERED_DIGEST_RING: int = 8

# Labels kept for sessions a /kill removed, so a re-creation racing the
# kill can recover the name the user chose. Bounded because nothing prunes
# it by time: 50 is far more than the handful of kills that can be in
# flight at once, and each entry is a short string.
_MAX_REMEMBERED_LABELS: int = 50

# How long a killed session's label stays recoverable. It only has to
# outlive the gap between `kill_session` unlinking the socket and the
# monitor's next 2s scan; a minute is generous. Past that the session is
# genuinely forgotten, so the name must not resurface on a different
# session that happens to reuse the internal dtach name.
REMEMBERED_LABEL_TTL_SECONDS: float = 60.0


# The object whose ``scopes`` / ``policy`` are the daemon's LIVE config
# (the TelegramBot, which a SIGUSR1 reload updates in place). Held weakly
# so a test's throwaway bot is never kept alive by it. ``None`` before a
# bot exists (the CLI, the registry's load at daemon start): config's
# import-time SCOPES / POLICY are used then.
_live_scope_source: weakref.ref | None = None


def set_live_scope_source(obj) -> None:
    """Register *obj* (``.scopes``, ``.policy``) as the live scope config,
    or clear it with ``None``."""
    global _live_scope_source
    _live_scope_source = weakref.ref(obj) if obj is not None else None


def _live_scopes_and_policy():
    src = _live_scope_source() if _live_scope_source is not None else None
    if src is not None:
        return getattr(src, "scopes", None), getattr(src, "policy", None)
    from aipager import config
    return getattr(config, "SCOPES", None), getattr(config, "POLICY", None)


def home_chat() -> tuple[int, str] | None:
    """``(chat_id, kind)`` of the home chat in scope mode (roadmap 8.82):
    where a session with no chat of its own belongs, by
    :func:`aipager.scope.home_scope` over the live scopes. ``None`` in
    personal/legacy mode (no scopes), where callers keep using
    ``config.CHAT_ID`` exactly as before."""
    scopes, policy = _live_scopes_and_policy()
    home = home_scope(scopes, policy)
    if home is None:
        return None
    return home.chat_id, home.kind


def chat_from_name(name: str) -> tuple[int, str] | None:
    """The chat a session name's scope suffix names (``__d<n>`` /
    ``__g<n>``), or ``None`` without one, after following any group
    Telegram upgraded to a supergroup (roadmap 8.87): a name keeps the
    old id (names are internal), and ``aipager.yaml``'s
    ``chat_migrations`` says where that chat lives now. Not followed when
    the old id is a configured scope again (the operator added it back)."""
    return follow_migrated_chat(chat_from_suffix(name))


def follow_migrated_chat(
    chat: tuple[int, str] | None,
) -> tuple[int, str] | None:
    """*chat* (``(chat_id, kind)``), or where it lives now when Telegram
    upgraded that group to a supergroup and the daemon followed it
    (``aipager.yaml``'s ``chat_migrations``, roadmap 8.87). Unchanged when
    the old id is a configured scope again."""
    if chat is None:
        return None
    try:
        migrations = load_chat_migrations()
    except Exception:
        log.debug("could not read the chat migrations", exc_info=True)
        return chat
    if chat[0] not in migrations:
        return chat
    scopes, _policy = _live_scopes_and_policy()
    if any(s.chat_id == chat[0] for s in scopes or ()):
        return chat
    new = follow_chat_migrations(chat[0], migrations)
    return (new, "group") if new != chat[0] else chat


def chat_for_new_session(name: str) -> tuple[int, str] | None:
    """The chat a session the daemon has just learned of belongs to:
    the chat its scope suffix names (``__d<n>`` / ``__g<n>``, following a
    group's upgrade to a supergroup: :func:`chat_from_name`), else the
    home chat in scope mode, else ``None`` (personal/legacy mode: left
    unstamped, ``config.CHAT_ID`` resolves it as before)."""
    return chat_from_name(name) or home_chat()


def session_in_chat(sess, chat_id) -> bool:
    """Whether *sess* is one of *chat_id*'s sessions (roadmap 8.72): a
    stamped session belongs to its own chat only. One with no chat
    stamped (``scope_chat_id`` 0) belongs to NO chat in scope mode:
    every session is stamped when the daemon learns of it, so this is a
    missed stamp (a WARNING, once per session), and "any chat" would make
    it every chat's session and target. In personal/legacy mode, which
    has one chat, it is that chat's (any chat, as always)."""
    if sess.scope_chat_id:
        return sess.scope_chat_id == chat_id
    if home_chat() is None:
        return True
    warn_unstamped(sess)
    return False


def session_foreign_to(sess, chat_id) -> bool:
    """True when *sess* is not one of *chat_id*'s sessions
    (:func:`session_in_chat`). ``None`` chat (a legacy unscoped update):
    never foreign, as before."""
    return chat_id is not None and not session_in_chat(sess, chat_id)


def warn_unstamped(sess) -> None:
    """Log, once per session, that *sess* was found with no chat stamped
    in scope mode (roadmap 8.72): some path learned of it without
    stamping it, and it belongs to no chat until it is."""
    if getattr(sess, "unstamped_warned", False):
        return
    try:
        sess.unstamped_warned = True
    except AttributeError:
        pass
    log.warning(
        "[%s] session %s has no chat stamped in scope mode (a missed "
        "stamp): it belongs to no chat until it is stamped",
        getattr(sess, "label", ""), getattr(sess, "name", ""))


def _default_scope() -> tuple[int, str] | None:
    """Resolve the configured home chat for backfilling legacy sessions.

    Returns ``(chat_id, kind)`` or ``None`` when nothing is configured
    (e.g. unit tests with no env). Read at call time so tests can
    monkeypatch ``config``.

    - Scope mode → :func:`home_chat` (the owner's DM, never "prefer the
      group", roadmap 8.82; a lone scope wins outright).
    - Else fall back to ``CHAT_ID`` (kind inferred from its sign).
    """
    from aipager import config

    home = home_chat()
    if home is not None:
        return home

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
    "diff_preview": "override_diff_preview",
    "card_age_decay": "override_card_age_decay",
}


@dataclass
class LiveMessageEntry:
    """One live Telegram message "layer" on a session's stack.

    Private to state.py — external code (including tests) interacts with
    the stack only via ``TrackedSession.busy_msg_id`` (the compatibility
    property) and the three public methods ``push_compacting``,
    ``pop_compacting``, ``stack_top_kind``. See design.md's "Live Message
    Stack" Decisions 1-4 for the full contract.

    ``kind`` is one of ``{"busy", "compacting"}`` — never a third
    "waiting" kind (design.md Decision 2: permission prompts already have
    their own, working, status-gated watchdog and never push a stack
    entry at all).

    ``deadline`` is precomputed at push time (``created_at + N``, or
    ``None`` for no forced expiry) so the sweep in
    ``session_monitor.expired_compacting_sessions`` is a single
    comparison, no per-tick arithmetic.
    """

    msg_id: int
    kind: str
    created_at: float
    deadline: float | None = None


@dataclass
class ModeSwitch:
    """An in-place Ask<->Auto switch aipager pressed Shift+Tab for and is
    waiting on a main-agent hook to confirm (aipager/bot/mode_switch.py).

    ``reply`` is where the person saw "switch sent" — ``(chat_id,
    message_id, kind)`` with kind ``mode_card`` | ``ready`` | ``mirror`` —
    or None; ``by`` is who switched in a group ("" in a DM)."""

    target: str
    presses: int
    sent_at: float
    retried: bool = False
    reply: tuple[int, int, str] | None = None
    by: str = ""


@dataclass
class TrackedSession:
    name: str           # session name, e.g. "claude-dev"
    label: str          # short label, e.g. "dev"
    status: Status = Status.UNKNOWN
    last_msg_id: int | None = None   # most recent Telegram notification msg
    summary: str = ""                # last pane summary (for idle notifications)
    last_idle_at: float = 0.0        # monotonic timestamp of last IDLE transition
    transcript_path: str = ""        # path to Claude Code JSONL transcript
    # Live busy-status tracking — a stack of live message "layers" (busy,
    # compacting), so a compaction card that never receives its terminal
    # hook signal can be reclaimed without losing what was live underneath
    # (design.md "Live Message Stack"). Transient — never in
    # _PERSIST_FIELDS; see load()'s post-construction assignment for how a
    # persisted busy_msg_id repopulates this on restart.
    # `busy_msg_id` (defined as a @property further below, after every
    # dataclass field — a property cannot share a name with a dataclass
    # field) is a compatibility view over the top of this stack: existing
    # readers, the CLI, the persisted state-file format, and 179 test
    # assertions all keep working unchanged. See Decision 1 for the full
    # getter/setter contract.
    _live_stack: list["LiveMessageEntry"] = field(default_factory=list, repr=False)
    last_tool_edit_at: float = 0.0   # monotonic timestamp of last busy-msg edit
    last_tool_name: str = ""         # last tool name displayed in busy message
    # Animation state
    animate_task: Any = field(default=None, repr=False)  # asyncio.Task for spinner
    last_tool_summary: str = ""      # cached tool summary text (current tool)
    # Tool history for busy message — list of (summary, done_bool)
    tool_history: list = field(default_factory=list)
    model_name: str = ""              # e.g. "Opus 4.6" from statusLine
    # The model id aipager started this session with (`--model`), or last
    # switched it to from Telegram; "" = Claude Code's own default. A
    # kill-and-relaunch (/perms, restart) starts it with the same one, so
    # a mode switch never silently drops the model /new picked.
    launch_model: str = ""
    last_token_pct: int = 0          # cached context % for display
    last_output_tokens: int = 0      # output tokens THIS TURN (delta from baseline)
    output_baseline: int | None = None  # total_output_tokens at first statusLine read this cycle
    # Cost tracking (item 4.6) — cumulative session cost from statusLine,
    # plus a baseline captured at BUSY-start so the busy message can show
    # "$ this turn" instead of lifetime cost.
    last_cost_usd: float = 0.0
    cost_baseline: float | None = None
    # When the daemon last applied a statusLine update (wall clock) and the
    # context % it carried. Kept apart from `last_token_pct`, which a new
    # turn zeroes for the busy card, so displays can tell the in-memory
    # numbers from the status-line file's and take the newer
    # (statusline_file.latest_stats). Not persisted: after a restart the
    # file is the only source.
    statusline_at: float = 0.0
    statusline_ctx_pct: int = 0
    # Subagent count THIS TURN (item 4.5) — increment per subagent_start,
    # reset on BUSY transition.
    subagent_count_this_turn: int = 0
    # A permission or question prompt was raised during THIS turn (on the
    # card or as its own message); reset with the turn's card. A `card`
    # layout turn that ran no tool and raised no prompt has its card
    # removed at the finish instead of left as an empty Done card
    # (operator, 2026-10-18). Transient.
    turn_prompt_shown: bool = False
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
    # (One exception, harmless: a card adopted at a restart (8.101) sets it
    # to the saved card anchor, which is shifted by the waits answered
    # before the save; a later stamp only makes that guard stricter.)
    busy_started_wall: float = 0.0
    # Wall-clock stamp of the most recent entry into BUSY from any status
    # other than INTERACTIVE — INCLUDING the background-job re-entries
    # (``preserve_job_state=True``) that deliberately leave
    # ``busy_started_wall`` anchored at the job's original prompt. Read by
    # exactly one consumer: the transcript-summary age guard
    # (``transcript.extract_last_response(since=...)``), which needs to
    # know when the CURRENT turn began so an assistant message written
    # before it — the previous turn's answer, still the newest text in the
    # file whenever this turn produced none — is never published as this
    # turn's. Not a substitute for ``busy_started_wall`` anywhere else.
    turn_entered_wall: float = 0.0
    # Queued messages (sent one-at-a-time when session becomes IDLE)
    pending_queue: list = field(default_factory=list)  # list of (text, trigger_msg_id)
    # Reply threading — Telegram message_id of the user's prompt that started this work
    trigger_msg_id: int | None = None
    # The Claude conversation (Claude Code's session_id, or the session's
    # own name while that is unknown) the "context almost full" notice was
    # last sent for. The notice is a one-time heads-up per conversation:
    # compaction keeps the id, so it never re-arms the notice; /clear or
    # another conversation changes it. Persisted, so a daemon restart does
    # not send it again.
    context_warned_conversation: str = ""
    # Context % before compaction (for delta display in compact_done notification)
    pre_compact_pct: int = 0
    # Last injected prompt text — enables retry on API errors
    last_prompt: str = ""
    # The person's own text of the prompt that started the running turn
    # (a Telegram message without aipager's marker, or what was typed in
    # the terminal). Only its direction is read: before Claude has written
    # anything, a message carrying Claude's text takes the direction of the
    # prompt it answers (multilingual design 5.7, rule 5). Kept by a
    # self-woken turn (the job's own prompt still stands).
    turn_prompt: str = ""
    # Who sent ``last_prompt`` (Telegram user id), when aipager knows.
    # Retry runs as the tapper only when the tapper IS this person —
    # otherwise the prompt is someone else's text and Retry must not lend
    # it the tapper's rights; it then runs on the floor. None = unknown.
    last_prompt_driver_user_id: int | None = None
    # Who the running turn belongs to (roadmap 8.77, D-H), for the hold of
    # a different person's message while it runs (``_turn_sender_differs``
    # in bot/session_ops.py): a Telegram user id, TURN_SENDER_TERMINAL (typed
    # in the terminal: the operator's), TURN_SENDER_MIXED (several people's
    # messages started it), or None (not known: nothing is held for it).
    # Set from the hook's report on the UserPromptSubmit that started the
    # turn (hook_receiver) and when a message Claude Code queued becomes
    # the next turn; cleared when the session goes IDLE (unless a
    # background job is still open: a prompt then joins the job's turn,
    # as the hook sees it too) or GONE, and by /stop and a safety halt.
    # Transient, never persisted: a restart has no turn.
    turn_sender_id: int | str | None = None
    # The authors of a TURN_SENDER_MIXED turn (roadmap 8.80), set with
    # ``turn_sender_id`` from the same hook report and read only while it
    # says MIXED, so a live reload can narrow the turn for a removed or
    # demoted one of them. Transient, never persisted.
    turn_mixed_authors: tuple[int, ...] = ()
    # "Peer message authority" (display only, never persisted): who sent
    # the message that started the running turn, as the hook verified it
    # (``peer_resolved``), and whether a message from another session
    # joined and narrowed it.
    peer_from: dict | None = None
    peer_narrowed: bool = False
    # Inline permission context (tool_info, question, etc.) — set when permission
    # is displayed inside the busy message instead of as a separate message
    pending_permission: dict | None = None
    # The prompt as it was SENT when it could not go inline into the busy
    # card (the separate-message fallback leaves ``pending_permission``
    # None): ``{"text", "keyboard", "summary", "prompt_token", "perm"}``,
    # plus ``"msg_id"`` and ``"chat_id"`` once the send landed (roadmap
    # 8.102: what a restart needs to bind the message to the restored
    # prompt). What the pinned bar's "Answer" button re-sends (8.31).
    # Meaningful only while ``status`` is INTERACTIVE (it is never cleared
    # when the prompt is answered); transient, never in _PERSIST_FIELDS:
    # the state file keeps a sanitized copy instead (aipager.open_prompt).
    pending_prompt_msg: dict | None = None
    # Roadmap 8.102: the parent's latest PreToolUse ``(tool_name,
    # tool_use_id)``, the id a PermissionRequest that carries none takes
    # for its prompt when the names match. None when that PreToolUse had
    # no id. Transient.
    last_parent_tool_use: tuple[str, str] | None = None
    # Roadmap 8.120: each agent's tool calls between their PreToolUse and
    # their end, ``{agent_id: {tool_use_id: (tool_name, input_digest)}}``:
    # an agent's PermissionRequest that carries no id of its own takes the
    # one call of that agent with the same tool and the same whole input,
    # so its PostToolUse closes the prompt. Two such calls (parallel calls
    # with one input) give none. Bounded per agent
    # (``AGENT_TOOL_CALLS_CAP``, newest kept). Transient.
    agent_tool_calls: dict = field(default_factory=dict)
    # Roadmap 8.102: monotonic stamp of the latest entry into INTERACTIVE
    # (``SessionRegistry.transition``). A prompt dict counts as the open
    # prompt only if its ``wait_started_at`` is not older: that shuts out
    # the ``pending_prompt_msg`` an earlier wait left behind. Transient.
    interactive_entered_at: float = 0.0
    # Roadmap 8.102: the open prompt the state file held for this session
    # (``open_prompt``, validated by ``aipager.open_prompt.validate``),
    # read once by the startup recovery, which restores the session as
    # waiting on it or drops it. Transient.
    restored_open_prompt: dict | None = None
    # Roadmap 8.102: message ids of prompt surfaces (the busy card, a
    # separate prompt message) whose prompt is KNOWN to be closed: answered
    # by a tap, answered in the terminal (its tool's PostToolUse), its turn
    # ended, or a saved prompt the restart dropped as answered, moved on or
    # unreadable as a record. An answer tap on one of them is refused with
    # no keys (callbacks.py). A prompt shown on a surface again takes it
    # off (``reopen_prompt_surface``). Never stamped by the INTERACTIVE
    # watchdog: a prompt nobody answered is not known closed, and its
    # buttons still answer it. Bounded (MAX_CLOSED_PROMPT_SURFACES, newest
    # kept) and persisted, so a stale tap after a restart is refused too.
    closed_prompt_msgs: list[int] = field(default_factory=list)
    # Files Claude sends (SendUserFile, aipager/bot/outbox.py): the turn
    # they may come from. Opened only by prompt evidence (a prompt the
    # daemon typed, a UserPromptSubmit), never by a tool call's hook, so a
    # forged PreToolUse that moves an idle session to BUSY opens nothing;
    # closed when the session goes idle with no background job open, or
    # gone. ``files_turn_wall`` is the wall-clock start a call's transcript
    # line must not predate, ``files_transcript`` the transcript the
    # daemon tracked for the session then (the one a call is looked up
    # in, never a path a later datagram names). ``files_sent_ids`` are the
    # call ids already copied (bounded, MAX_FILES_SENT_IDS). All four are
    # persisted, so a restart mid-turn keeps the turn and replays stay
    # refused.
    files_turn_open: bool = False
    files_turn_wall: float = 0.0
    files_transcript: str = ""
    files_sent_ids: list[str] = field(default_factory=list)
    # When the files turn last ended (a Stop, wall clock), kept with
    # ``files_transcript``: a call of a turn Claude Code starts after it
    # with no UserPromptSubmit (a queued message it dequeues, a Stop
    # hook's feedback that makes it go on) counts, but only once that
    # transcript shows the start (outbox.hookless_turn_started). Zero
    # when no turn of this conversation ended yet (a new or resumed one,
    # gone). Persisted.
    files_turn_closed_wall: float = 0.0
    # Monotonic moment the files turn opened (transient): an open turn on
    # an idle session with no job that is older than
    # session_monitor.FILES_TURN_UNSTARTED_SECONDS started nothing (a
    # typed prompt that ran no turn) and is closed.
    files_turn_opened_at: float = 0.0
    # Roadmap 8.120: the prompts that opened while another was already
    # open, in arrival order, each sent as its own message. Every entry has
    # the ``pending_prompt_msg`` shape (``text, keyboard, summary,
    # prompt_token, perm``, then ``msg_id, chat_id`` once sent). The first
    # prompt of a wait stays in ``pending_permission`` / ``pending_prompt_msg``
    # (the "primary"); ``open_prompts()`` lists them all. Transient, never
    # saved: a restart with two or more open restores none of them.
    extra_prompts: list = field(default_factory=list)
    # Roadmap 8.120: monotonic stamp of the latest hook of Claude's own
    # (no ``agent_id``) that says its turn is alive: the INTERACTIVE
    # watchdog's quiet time is measured from it and the open prompts'
    # owners' hooks, never from another agent's. Transient.
    parent_hook_at: float = 0.0
    # Roadmap 8.120: set by the hook receiver while Claude's Stop finishes
    # the turn with an agent's prompt still open, so the IDLE moment keeps
    # that prompt (``dialog_is_open`` stays true, no held message is typed
    # into its dialog) and the session waits on it again afterwards.
    # Transient.
    prompt_wait_carry: bool = False
    # The card's inline prompt kept through that Stop, sent again as its
    # own message once the answer is out. Transient.
    carried_inline_prompt: dict | None = None
    # Roadmap 8.120: the session waits again on prompts carried over a
    # Stop (``SessionRegistry.resume_wait``), with no turn of Claude's
    # running: the last answer leaves it IDLE, not BUSY, and a prompt typed
    # at the terminal starts a new turn. Cleared by any status change and
    # by Claude's own turn activity (a tool call, a continuation).
    # Transient.
    resumed_after_stop: bool = False
    # Active subagents — keyed by agent_id ("agent activity rows on the
    # busy card"). Format: {agent_id: {"type": str, "started_at": float,
    # "last_seen": float, "history_idx": int | None, "activity": str,
    # "tool_count": int, "last_tool_at": float, "tools": list[str]}} — the
    # last four keys are updated by `record_agent_tool` as the agent's own
    # tool calls are attributed to it instead of the parent's
    # `tool_history` ("activity" is the latest attributed summary, ""
    # before the first; "tools" is capped at AGENT_TOOLS_CAP).
    #
    # `last_seen` (roadmap 8.22) is the monotonic stamp of the most recent
    # hook datagram this agent emitted — written by `touch_subagent`, which
    # hook_receiver calls for EVERY datagram carrying a matching
    # `agent_id`. It is the liveness signal session_monitor's sweep judges
    # an entry by; `started_at` is only its age, and age is not evidence of
    # anything (agents legitimately run for hours). Monotonic, so
    # meaningless across a restart — see `normalize_subagent_liveness`.
    active_subagents: dict = field(default_factory=dict)
    # Agents that SubagentStop'd this turn, appended in stop order by
    # `archive_finished_subagent`, capped at FINISHED_SUBAGENTS_CAP. Entry
    # shape: {"type": str, "started_at": float, "elapsed": float,
    # "tool_count": int, "tools": list[str]} — a snapshot, not a live
    # reference into `active_subagents`. Reset every turn alongside
    # `tool_history`/`active_subagents`; never persisted (same reason as
    # those two — transient, per-turn state).
    finished_subagents: list = field(default_factory=list)
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
    # Monotonic timestamp of the PARENT's own PreToolUse (no agent_id)
    # whose PostToolUse has not fired yet: the step Claude itself is in.
    # Unlike pending_tool_started_at, a subagent's tool calls never touch
    # it, so a foreground agent's inner tools do not restart its age and a
    # detached background agent's tools never count as the parent's step.
    # No longer read since the "⏳ Queued" line goes up at once (2026-09-29);
    # kept, with its bookkeeping, until that dead state is removed on its
    # own. Cleared by the parent's PostToolUse / PostToolUseFailure and at
    # each new turn. Never persisted (monotonic).
    parent_tool_started_at: float | None = None
    # Monotonic timestamp of the most recent PreCompact hook when the
    # matching post-compact SessionStart hasn't fired yet — compaction
    # can take minutes on a large transcript with no hooks in between,
    # which would otherwise trip the stale-busy detector. Not persisted
    # (same reason as pending_tool_started_at above).
    compact_started_at: float | None = None
    # "Prompt not taken" watchdog (roadmap 8.11; session_monitor.
    # prompt_not_taken). All monotonic and transient — never persisted,
    # for the same reason as pending_tool_started_at. ``_inject_prompt``
    # stamps the first two on every SUCCESSFUL turn-STARTING Telegram
    # send — when it went out, and the (msg_id, chat_id) of the Telegram
    # message so the handler can drop its policy note. A send made while
    # the session is already BUSY leaves both untouched: it is not judged
    # (the running turn's own hooks keep arriving, so "no hook since the
    # send" proves nothing), and overwriting the slot would let it
    # silently cancel the deadline of an earlier, still-unanswered
    # from-idle send (review rev-iter1-001). ``turn_hook_at`` is stamped
    # by the hook receiver for every datagram that proves a turn is
    # alive — every one except ``statusline`` repaints and the phantom
    # SubagentStop.
    prompt_sent_at: float = 0.0
    prompt_sent_msg: tuple[int, int] | None = None
    # Monotonic deadline of an outstanding `/model` switch (roadmap
    # 8.35): set when one is typed, cleared when the statusline reports
    # the new model. While it holds, a second switch is refused — the
    # first may be sitting behind Claude Code's "Switch model?" dialog,
    # which aipager cannot see, and a second `/model` would be typed into
    # it. Transient, never persisted.
    model_switch_pending_until: float = 0.0
    # The statusline's model when that switch was typed. The mark stops
    # counting as soon as the session reports anything else — whoever
    # (or whatever) observed it.
    model_switch_pending_from: str = ""
    # Monotonic end of the time a typed `/model` may still be checking its
    # model. Claude Code (2.1.303, probed) first sends the model a request,
    # giving up after 15 s, and keystrokes typed meanwhile are lost: a
    # message is held until the statusline reports the switch or this
    # passes (``model_check_running``). Transient, never persisted.
    model_check_until: float = 0.0
    # A message was held for that check: sent on once it is over
    # (``notify``'s ``_settle_model_hold``, the session monitor's backstop).
    model_hold: bool = False
    # Claude Code's default model for new sessions (``model`` in its user
    # settings) when the switch was typed: it saves a typed `/model` there,
    # and the switch's confirmation says so when it changed.
    model_default_from: str | None = None
    # That file could be read then (a missing one counts: no default). An
    # unreadable one says nothing about the owner's default, so a member's
    # switch is not undone from it (session_ops.restore_default_model).
    model_default_known: bool = False
    # The model that switch typed: the default Claude Code saves must be it.
    model_switch_to: str = ""
    turn_hook_at: float = 0.0
    # One "still waiting for your answer" reminder per INTERACTIVE wait
    # (roadmap 8.4): set by the hook receiver when Claude Code's idle
    # notification arrives while the session is blocked on a human,
    # cleared by ``transition()`` on every entry into INTERACTIVE.
    # Transient — a daemon restart starts a fresh wait anyway.
    waiting_reminder_sent: bool = False
    # Messages held because a dialog was open (handlers'
    # ``_hold_for_open_dialog``): ``{"entered_at": the wait's
    # ``interactive_entered_at``, "told": sender ids already given the
    # "answer it first" notice for that wait, "notices": [(chat_id,
    # msg_id), ...] of those notices, "closed": "answered" | "ended"}``.
    # ``closed`` is set by ``transition()`` when the session leaves
    # INTERACTIVE (BUSY: answered, the held messages go out in order; any
    # other status: the turn or the session ended, the queue is left to
    # the paths that handled it before) and dropped again on a new entry.
    # The bot settles it (``NotifyMixin._settle_dialog_hold``) once no
    # dialog is open: releases, deletes the notices, clears it.
    # Transient, never persisted: the held messages themselves are in
    # ``pending_queue``.
    dialog_hold: dict | None = None
    # True while the bot sends the held messages on: one release at a
    # time per session, whichever hold it is for. Transient.
    dialog_settling: bool = False
    # Guards the idle-recovery fallback's own INFO log (session_monitor.py)
    # so a tool/compaction that stands the recovery down for minutes logs
    # ONE line for the whole episode rather than one per 2s scan tick.
    # Cleared by work_in_flight_reason() returning None (the tool/compact
    # finished or blew its cap), so the next stand-down episode logs again.
    # Not persisted — monotonic-scan bookkeeping, meaningless across a
    # daemon restart.
    recovery_stand_down_logged: bool = False
    # Monotonic timestamp of the busy-card watchdog's last action on this
    # session (session_monitor.busy_card_watchdog_action). Throttles the
    # watchdog to one restart / forced refresh per CARD_STALE_SECONDS, so
    # a card whose edits fail permanently (bot blocked) is retried at that
    # cadence rather than on every 2s scan. Not persisted (monotonic).
    card_watchdog_at: float = 0.0
    # Monotonic deadline until which this session is being deliberately
    # restarted (``/perms`` kills and relaunches to change the permission
    # flag). The old process's SessionEnd hook and the monitor noticing its
    # socket vanish both mean "expected", not "crashed", inside this window,
    # so neither raises an alarm. A deadline rather than a boolean because a
    # flag that fails to clear would silence real crashes forever; this one
    # expires on its own. Not persisted — a restart never spans a daemon
    # restart.
    restarting_until: float = 0.0
    # True from a kill-and-relaunch's kill to its relaunch (or failure):
    # what refuses a second switch or restart while one runs. Not the
    # window above, which outlives a SUCCESSFUL relaunch by design (the
    # late SessionEnd) and used to refuse, in silence, a switch sent in
    # the seconds after one (2026-09-30). Cleared in a `finally`, so it
    # cannot stick; not persisted.
    relaunch_in_flight: bool = False
    # Monotonic deadline while a resume's launch is in flight — read by
    # `is_resuming()`, which the ageing sweep honours. Transient: a
    # daemon restart means no resume is in flight any more.
    resuming_until: float = 0.0
    # Claude Code background jobs (aipager/claude_jobs.py). The session's
    # own claude moved its conversation into background job
    # `parked_job` (``←``): it stays in the terminal, parked, and aipager
    # never types into it (dtach/inject.py's parked probe reads this
    # field). Set and cleared by the session monitor's ``check_parked``
    # from Claude's session registry. Persisted, with the job's
    # conversation id, when it was noticed (wall clock) and the job the
    # park notice was sent for, so a daemon restart neither types into a
    # parked session nor repeats the notice.
    parked_job: str = ""
    parked_job_session_id: str = ""
    parked_since: float = 0.0
    park_notified_job: str = ""
    # Where the park was found: "registry" (the session's own claude's
    # entry says ``parkedJobId``) or "transcript" (no entry was found, and
    # the conversation's transcript ends with ``continued-in``; the id of
    # that conversation, from before the move, is kept, since
    # ``claude_session_id`` moves to the job's). A transcript park ends
    # when that transcript no longer ends with ``continued-in`` (its old
    # copy was resumed on purpose). Persisted with the park.
    parked_source: str = ""
    parked_from_session_id: str = ""
    # How many parks of this session ended (``forget_park``): the terminal
    # guard tells one park from the next by it. Transient.
    park_epoch: int = 0
    # When the park last changed (a park noticed, moved to another job, or
    # forgotten), on the monotonic clock: a registry read that started
    # before it is older than what it would decide, and may not change the
    # park (``SessionMonitor._check_parked``). 0 for a park loaded from
    # disk (any read is newer). Transient.
    park_changed_at: float = 0.0
    # The pid of the session's own claude (its hooks forward CLAUDE_PID),
    # and that process's start time when known (0: unknown). Transient:
    # found again from the registry after a restart.
    claude_pid: int = 0
    claude_pid_start: int = 0
    # The senders already told their message waits for the park
    # (handlers' parked hold); emptied when ``parked_job`` changes.
    # Transient.
    park_told: set = field(default_factory=set, repr=False)
    # After a bring-back from the background: the held messages may go
    # out (``bg_release``). Armed by the bring-back right before it
    # launches the conversation again (``bg_release_armed_at``, monotonic),
    # so the relaunched claude's first own SessionStart can never come
    # before it; ``bg_start_seen`` records a SessionStart that came while
    # the bring-back was still running (the bring-back then releases when
    # it is done); ``bg_released_at`` (monotonic) is when the bring-back
    # finished, the clock of the monitor's backstop. Exactly one held
    # message goes out per release: :meth:`claim_bg_release` checks and
    # clears the flag with no await in between. ``bg_release_pending`` is
    # persisted (a daemon restart between a bring-back and its release
    # still releases once); the rest is transient.
    bg_release_pending: bool = False
    bg_release_armed_at: float = 0.0
    bg_start_seen: bool = False
    bg_released_at: float = 0.0
    # A safety halt is waiting out a bring-back already in flight: nothing
    # held may be released meanwhile (the halt drops the held messages,
    # and disarms the release when its wait is over). Transient.
    bg_release_blocked: bool = False
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
    # Permission mode aipager last launched this session in, or asked it
    # to switch to: "ask" (--permission-mode default), "auto"
    # (--permission-mode auto) or "full" (--dangerously-skip-permissions);
    # see aipager/perm_modes.py. Persisted: it decides the flag on every
    # restart and resume. A hook never changes it towards a wider mode
    # (the hook socket is world-writable), so what Claude Code REPORTS
    # lives apart, in `reported_mode` below. "ask" for a session aipager
    # did not launch (a CLI session): its first hook shows the real mode.
    perm_mode: str = "ask"
    # Transient, never in _PERSIST_FIELDS: the raw `permission_mode` the
    # last main-agent hook reported since the last launch or switch ("" =
    # nothing reported yet).
    reported_mode: str = ""
    # Transient: Claude Code reported Manual for a session aipager asked
    # for Auto (Auto is not available for its model, plan or org).
    auto_unavailable: bool = False
    # Transient: set at every launch, relaunch and resume; the first
    # main-agent hook after it checks whether Auto was available.
    mode_launch_unchecked: bool = False
    # Transient: an in-place Ask<->Auto switch waiting for a hook to
    # confirm it (aipager/bot/mode_switch.py).
    mode_switch: ModeSwitch | None = None
    # Transient, per launch: a main-agent hook reported bypassPermissions
    # since the last launch, so the live process has bypass in its
    # Shift+Tab cycle (a CLI session started with the bypass flag keeps
    # `perm_mode` "ask"). Every switch then restarts: presses in place
    # could cycle it back into bypass. A forged report can only make a
    # switch restart (narrower), never widen.
    bypass_seen: bool = False
    # Per-session preference overrides (batch 4's "Per session settings").
    # `None` means unset/inherit the scope's own /settings value — never a
    # legal value for any of these: layout/answer_length/
    # language_level are closed enumerations that never include None, and
    # the booleans' (simple_formatting, diff_preview, card_age_decay) own
    # legal `False`
    # must not be confused with
    # "unset", which is exactly why `None` alone (not `False`, not a
    # missing-key sentinel) is the unset marker — it is disjoint from
    # every field's real values. Never read directly: resolve together
    # with the scope's own preferences via
    # `aipager.preferences.resolve_preferences(scope_chat_id,
    # sess.preference_overrides())`, the same rule for chat and the Mini
    # App. Persisted like `perm_mode` above (a per-session setting that
    # changes behaviour); intentionally NOT cleared by the `/new` replace
    # flow (callbacks.py) — see design.md Risks.
    override_layout: str | None = None
    override_simple_formatting: bool | None = None
    override_answer_length: str | None = None
    override_language_level: str | None = None
    override_diff_preview: bool | None = None
    override_card_age_decay: bool | None = None
    # Multi-scope (Phase B): which Telegram chat this session belongs to.
    # All outbound notifications for the session route here instead of the
    # global CHAT_ID. `scope_chat_id == 0` means "not stamped": in scope
    # mode every session is stamped when the daemon learns of it, and an
    # unstamped one belongs to no chat (`session_in_chat`, roadmap 8.72);
    # in personal/legacy mode it is the one chat's (CHAT_ID).
    # `scope_kind` is "dm"|"group".
    scope_chat_id: int = 0
    scope_kind: str = ""
    # Transient: the "found unstamped in scope mode" WARNING was logged
    # for this session (`warn_unstamped`, once per session).
    unstamped_warned: bool = field(default=False, repr=False)
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
    #   _read_stream_text (hook not live) and _sync_anchors_from_transcript
    #   (hook live) both read exclusively from this path for the whole turn,
    #   so the offset can never be applied to a file it wasn't measured against.
    # stream_commentary: Claude's prose blocks for this turn, each paired with
    #   the tool_history index of the row that follows it in the transcript.
    #   That anchor is what lets the card interleave prose with tool rows in
    #   the order they actually happened; tool_history itself must keep its
    #   shape because active_subagents[...]["history_idx"] indexes into it.
    # stream_tool_cursor: how far into tool_history the transcript scan has
    #   walked this turn. For the transcript fallback (hook not live) it is
    #   the anchor handed to the next prose block read from the file. Once
    #   the MessageDisplay hook is live the same cursor is driven by the
    #   exact-anchor scan (_sync_anchors_from_transcript), which walks the
    #   flushed rounds only to PLACE hook-delivered sentences — never to
    #   read text ("transcript-exact-sentence-anchors").
    # stream_block_index: message_id -> index into stream_commentary of the
    #   block the hook created for that message, so the exact scan can
    #   correct its anchor once the message's round is readable.
    # stream_exact_anchor: message_id -> the tool_history index of that
    #   message's FIRST tool row, learned from the transcript. Consulted
    #   when the hook text arrives (its round may already have flushed —
    #   the last tool was quick — and the next message's rows may already
    #   sit above the floor) and applied to an existing block otherwise.
    #   Both maps are per-turn and never persisted.
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
    stream_block_index: dict[str, int] = field(default_factory=dict, repr=False)
    stream_exact_anchor: dict[str, int] = field(default_factory=dict, repr=False)
    stream_hook_live: bool = False
    stream_dirty: bool = False
    stream_last_rendered: str = ""
    # design.md "turn anchor follows consumption" — busy_card_trigger:
    # which message the CURRENT live/finished card is replying to (set
    # by send_busy/_reanchor_busy_card). Compared against trigger_msg_id
    # to decide whether a live card has gone stale (R3). Transient —
    # never in _PERSIST_FIELDS; see SessionRegistry.load()'s post-
    # construction seed for why a restart re-derives it from
    # trigger_msg_id rather than leaving it None.
    busy_card_trigger: int | None = None
    # roadmap 8.21 starvation guard: monotonic stamp of the FIRST
    # skip-refused card edit in the current run of refusals, 0.0 when the
    # last attempt landed. `_animate_tick` makes one BLOCKING attempt once
    # this is 2 x the card interval old, so a busy card under a tight
    # per-chat budget can be slow but never frozen. Transient — never in
    # _PERSIST_FIELDS: a restart has no budget state to be starved by, and
    # a limiter that starts every chat unthrottled must not meet a card
    # that thinks it has been refused for an hour.
    card_skipped_since: float = 0.0
    # The message id of the last card rendered in its FINISHED form (a
    # final edit, a final move's copy). Several finish paths retire
    # `busy_msg_id` only after a further await (the moved card's delete,
    # the superseded card's log attachment); a busy-card edit that gets
    # the card-edit lock in that window must not paint a "Working" frame
    # and a Stop button back over the settled card, so `_edit_busy_rich`
    # refuses any non-final edit of this id. Transient: message ids are
    # never reused, and a restart has no edit in flight.
    card_settled_msg_id: int = 0
    # roadmap 8.29 T3 / ruling 1: set when MINIMAL MODE LIFTS while this
    # session's card is still showing the static "updates paused" line.
    # The next tick then makes ONE non-debounced, ESSENTIAL edit, so the
    # card visibly comes back to life instead of waiting out an interval
    # and then possibly being refused as an ornament by a budget that is
    # still tight. Transient — never in _PERSIST_FIELDS, for the same
    # reason as `card_skipped_since`: it describes a limiter state a
    # restart does not have.
    card_resume_due: bool = False
    # roadmap 8.30 — the busy card's cadence DECAYS WITH TURN AGE. All three
    # transient, like `card_skipped_since`: they describe the card on
    # screen right now, and a restart restarts the age anchor anyway.
    # `card_elapsed_unit` is the unit ("s" / "m" / "h") every live counter
    # on the card renders in, set by `_edit_busy_rich` before it builds so
    # the renderer stays pure. `card_frame_state` is the card's frame
    # state (status, waiting, prompt) at the last LANDED edit — a state
    # change against it is what may bypass the age decay — and
    # `card_bypass_at` stamps the last bypass, at most one per
    # CARD_STATE_BYPASS_MIN_GAP.
    card_elapsed_unit: str = "s"
    card_frame_state: tuple | None = None
    card_bypass_at: float = 0.0
    # roadmap 8.24: the "typing…" task for this session's busy card —
    # `animation._animate_typing`, started and cancelled alongside
    # `animate_task` by `_start_animation` / `_stop_animation`. A SECOND
    # task rather than work inside the animator's, because the bubble has
    # to be refreshed on its own ~4.5 s clock: riding the card's wake grid
    # quantized it to 5.28-6.6 s, past the 5 s expiry Telegram gives a
    # typing status. Deliberately not cadence state — nothing here is read
    # by `_card_interval`, the card debounce or the starvation guard.
    # Transient, like `animate_task`: never in _PERSIST_FIELDS (a Task is
    # not serialisable, and a restart has no bubble lit to resume).
    typing_task: Any = field(default=None, repr=False)
    # roadmap 8.32: a self-woken turn's DEFERRED busy card. Non-zero (the
    # monotonic time it was armed) while the turn is running without a
    # card it has not earned yet — `_send_busy_and_animate(lazy=True)`
    # did every turn-start reset but held the send back. The first tool
    # use, or `lazy_card_task` after `SELF_WOKEN_CARD_DELAY`, sends it
    # (`_send_lazy_card`); a turn that ends first sends only its answer.
    # Transient, never in _PERSIST_FIELDS: a restart has no turn to defer
    # a card for.
    lazy_card_at: float = 0.0
    lazy_card_task: Any = field(default=None, repr=False)
    # roadmap 8.17c: this turn's busy card was REFUSED by the flood gate —
    # the chat is muted, or in minimal mode and cannot afford an ornament
    # (a ban always leaves it there for a while). The card is owed, not
    # lost: the session monitor's tick sends it (`_send_owed_card`) once
    # the chat can take it, if the turn is still running then. Cleared
    # with the 8.32 mark by `_cancel_lazy_card`, i.e. by every path that
    # ends the turn, so a finished turn never gets a stray card. Transient,
    # never in _PERSIST_FIELDS: a restart has no running turn to owe.
    busy_card_owed: bool = False
    # A scratch outbox: notes matched-and-already-deleted by
    # _sync_anchors_from_transcript's absorption detection, staged here
    # rather than returned (that function's bool return — "did an
    # anchor move" — is asserted byte-for-byte by
    # tests/test_transcript_exact_anchors.py and must not change shape).
    # Drained (and reset to []) by whichever caller reads it next
    # tick/event/finish-sync. Never persisted.
    stream_consumed_notes: list[dict] = field(default_factory=list, repr=False)
    # Roadmap 8.47: how far animation._scan_queue_operations has read the
    # turn's pinned transcript for `queue-operation` lines. Its OWN offset,
    # never `stream_offset`: the prose fallback shares that one and drops
    # queue lines, which lost an `absorbed_mid_turn` line live. Seeded with
    # `stream_offset` at turn start. Never persisted.
    queue_scan_offset: int = 0
    # Messages Claude Code has QUEUED during a running turn whose fate is
    # not yet known ("anchor-on-transcript-consumption"): the hook's
    # pick-up fires at SUBMIT time for such a message, so it is recorded
    # here ({msg_id, chat_id, raw_text}, submit order) instead of moving
    # the reply target. A transcript `absorbed_mid_turn` line for it
    # consumes it (the real signal, see _sync_anchors_from_transcript);
    # whatever is still here when the turn ends is the NEXT turn's prompt
    # (Claude Code pops its queue at once and fires no hook for it).
    # Spans turns by design; cleared by /stop, /clearqueue, kill and
    # restart. Never persisted.
    queued_targets: list[dict] = field(default_factory=list, repr=False)
    # "Send now" (bot/send_now.py): the "⏳ Queued" reply line under each
    # queued target Claude still holds, sent as soon as its queue record is
    # seen, keyed by the target's msg_id -> (chat_id, line message id). A
    # line exists only for a msg_id still in `queued_targets`. Transient,
    # never in _PERSIST_FIELDS: a restart has no queued targets, and the
    # line ids a restart would orphan are owed on the REGISTRY
    # (`SessionRegistry.queued_line_deletes`), which outlives a /kill.
    queued_lines: dict[int, tuple[int, int]] = field(
        default_factory=dict, repr=False)
    # The pending line timer per queued msg_id (the dict is its strong
    # reference). Transient: a task cannot be persisted, and a restart has
    # nothing queued to time.
    queued_line_timers: dict[int, asyncio.Task] = field(
        default_factory=dict, repr=False)
    # > 0 while aipager is typing a prompt into this session (text written,
    # Enter not yet): send-now then would submit the half-typed text, so a
    # tap or /now is refused. Transient: no injection survives a restart.
    prompt_injecting: int = 0
    # True while a send-now chord is being written, so a second tap does not
    # write it twice. Transient, for the same reason.
    send_now_inflight: bool = False
    # monotonic stamp of the last send-now chord that reached the terminal,
    # 0.0 when none is pending. When Claude Code cancels the running step
    # instead of backgrounding it, it takes the oldest queued message as a
    # new prompt with no hook at all, only a `dequeue` queue-operation; a
    # dequeue read while this is fresh is that take (roadmap 8.64, see
    # send_now_take_open). Cleared when the session goes IDLE: a dequeue
    # after a Stop is the ordinary next-turn pop. Transient.
    send_now_pressed_at: float = 0.0
    # monotonic stamp of the last explicit /stop (or safety halt). A Stop
    # hook that lands on an already-IDLE session shortly after it is
    # Claude finalising the INTERRUPTED turn, not an unanswered one — the
    # already-IDLE delivery guard must not send that partial answer under
    # a card that already says Stopped (review rev-iter1-003). Transient.
    user_stopped_at: float = 0.0
    # MD5 hex digest of the last DELIVERED Finished body's raw (pre-HTML)
    # text — design.md "model Claude Code background-agent jobs",
    # requirement 2 (a job's interim answers are deduped by the ring below
    # since roadmap 8.42). Transient (never in _PERSIST_FIELDS): a job
    # never spans a daemon restart (Decision 2/6), so there is nothing to
    # persist. Cleared only where a genuinely NEW job starts
    # (_send_busy_and_animate's existing per-turn reset) so a duplicate
    # answer in the NEXT job is never mistaken for a repeat of this one.
    last_idle_summary_hash: str = ""
    # Digests of the last few bodies actually sent to the chat as a turn's
    # answer (a Finished body, or a job's interim answer). Unlike the
    # single hash above, this ring is never cleared by a new turn: it is
    # for exactly the case the per-turn reset cannot see — the newest text
    # in the transcript belonging to an EARLIER turn (this one ended on
    # tool calls, or was a missed-Stop recovery) and coming back around as
    # "this turn's answer". An identical body is therefore never re-posted
    # within the session's lifetime. Bounded by DELIVERED_DIGEST_RING.
    #
    # PERSISTED (roadmap 8.39), but only the digests of bodies that really
    # LANDED: a restart between a delivered Stop and Claude Code's idle
    # notification 60 s later used to find an empty ring, and the
    # already-IDLE late path re-posted the answer. The finish path records
    # a digest BEFORE it sends (so a concurrent re-run in this process is
    # refused); such a digest sits in `pending_digests` until the send is
    # confirmed, and `persisted_digests()` leaves it out — otherwise a
    # shutdown mid-send, or a failed or held send (held answers are not
    # persisted either), would write "delivered" for an answer the chat
    # never got, and the restart could no longer deliver it.
    delivered_digests: deque = field(
        default_factory=lambda: deque(maxlen=DELIVERED_DIGEST_RING),
        repr=False,
    )
    pending_digests: set[str] = field(default_factory=set, repr=False)
    # Wall-clock moment the finish path received the turn end of the last
    # CONFIRMED answer (roadmap 8.39 R2). Persisted. A transcript text entry
    # written before it already existed when that answer went out, so the
    # already-IDLE late path, which reads the transcript's newest text,
    # refuses it: it is the delivered answer or something older still. A
    # genuinely undelivered answer (the daemon died between its Stop and
    # the send) was written AFTER the previous confirmed delivery and
    # still goes out. Stamped when the finish path receives the turn end,
    # not at landing, so a fast next turn's answer written during a slow
    # card render or send is not mistaken for old.
    answer_delivered_wall: float = 0.0
    # Background-job endgame state (all transient, never persisted):
    # `job_interim_seen` — an idle-transition happened while this job's
    # background agents were open (the waiting card went up); the signal
    # that a continuation is expected when the table later empties.
    # `job_continuation_active` — a <task-notification> continuation turn
    # is running; its own Stop is the job's one true Finished.
    # `job_grace_until` — monotonic deadline keeping the job open across
    # the SubagentStop→continuation gap (see JOB_CONTINUATION_GRACE_SECONDS).
    job_interim_seen: bool = False
    job_continuation_active: bool = False
    job_grace_until: float = 0.0
    # One-shot marker set by ``SessionRegistry.transition()`` when a
    # GENUINE new turn enters BUSY (prior state not BUSY/INTERACTIVE, no
    # preserve_job_state) — the only place the before-state is visible.
    # ``_send_busy_and_animate`` consumes it to decide "reclaim the live
    # waiting card" vs "same turn already running, no-op" (review
    # rev-iter2: a status check there is always too late, every caller
    # transitions to BUSY first; a same-state BUSY→BUSY no-op never sets
    # this, which is exactly the mid-continuation case that must be
    # swallowed). Transient, never persisted.
    job_reclaim_pending: bool = False
    # One card per turn (roadmap 8.57). `turn_seq` counts genuine turn
    # starts: bumped by ``SessionRegistry.transition()`` in exactly the
    # branch that sets ``job_reclaim_pending``, so a permission answer, a
    # background job's re-entry or a BUSY→BUSY no-op never counts. Every
    # card request carries the turn it was made for, and the card records
    # the turn it belongs to (`card_turn_seq`, ``None`` for a card whose
    # turn is unknown: restored from disk, or hand-built). A request for a
    # turn that is no longer current, or whose finish already ran
    # (`closed_turn_seq`), sends nothing; a second request for the card's
    # own turn re-uses it. `finish_gate` is set (an ``asyncio.Event``)
    # while a finish path for turn `finishing_turn` is disposing of that
    # turn's card and answer: a NEWER turn's card waits for it rather than
    # racing it for the same card, reply target and stream state.
    # All transient.
    turn_seq: int = 0
    # What a button on a message edited in place (/status's Stop, the ⋮
    # End confirm) carries to say which turn it was shown for. New on
    # every turn AND for every new session, unique across restarts (see
    # `_TURN_KEYS`): `turn_seq` starts at 0 for each new entry, so a kill
    # then `/new` of the same name, or a restart, would hand an old button
    # the number of the new session's first turn. Transient.
    turn_key: int = field(default_factory=lambda: next(_TURN_KEYS))
    card_turn_seq: int | None = None
    closed_turn_seq: int | None = None
    finishing_turn: int | None = None
    finish_gate: asyncio.Event | None = None
    # A turn that has started while an older turn's finish still holds the
    # shared card state (roadmap 8.62): a queued message popped at the
    # Stop, or a prompt sent meanwhile. Its card, and the reset that gives
    # it card state of its own, wait for that finish; until then the hook
    # receiver holds this turn's own events on `turn_state_hold`, so they
    # neither land on the older turn's card nor get wiped by the reset.
    # Transient, never in _PERSIST_FIELDS: a restart has no finish out.
    turn_state_hold_for: int | None = None
    turn_state_hold: asyncio.Event | None = None
    turn_state_hold_since: float = 0.0
    # The answer (its Stop's text) of the latest turn whose finish started:
    # a late MessageDisplay carrying it is that turn's, never a held newer
    # turn's (roadmap 8.62). Kept after the finish, since the hold it serves
    # can outlast the finish's gate; every finish overwrites it before it
    # can pop or hold anything. Transient.
    finishing_answer: str = ""
    # How many ``notify()`` calls are running for this session, and when
    # the session monitor first saw its card orphaned (live, Stop button
    # up, no turn or job running, nothing animating it, nothing in
    # flight). The orphan sweep settles such a card after
    # ORPHAN_CARD_GRACE_SECONDS (roadmap 8.55/8.57). Transient.
    notify_in_flight: int = 0
    orphan_card_seen_at: float = 0.0
    # A card restored from disk whose turn looked still running at startup
    # is ADOPTED (roadmap 8.55): the orphan sweep leaves it alone until
    # this monotonic deadline. Since 8.101 the adoption makes the session
    # BUSY with the card ticking at once (``_adopt_running_card``), so the
    # deadline only matters if it leaves BUSY unfinished. Transient.
    card_adopt_until: float = 0.0
    # The persisted card's clock anchor as the previous run left it
    # (``card_started_wall`` in the state file, see ``card_started_wall``
    # below): read once by the adoption at startup (roadmap 8.101) so the
    # adopted card's elapsed counter continues from the turn's real start.
    # 0.0 when the file had none. Transient.
    restored_card_started_wall: float = 0.0
    # Cards owed a delete that has not landed yet (a deferred tool-less
    # delete, an orphan's). Persisted, so a restart that kills the delete
    # still removes the card at the next startup (roadmap 8.55).
    pending_card_deletes: list[int] = field(default_factory=list)
    # Roadmap 8.41 — live background agents, apart from `active_subagents`
    # on purpose: that table is per-TURN card bookkeeping (a new card
    # clears it, /stop clears it, and job_background_open() reads it), while
    # this one follows the AGENT until its own SubagentStop, whatever the
    # turns in between do. Entry: {"label": str, "last_seen": float
    # (monotonic), "started": float (monotonic), "prose": str (its latest
    # own message, raw, capped at BG_AGENT_PROSE_CAP), "prose_msg": str
    # (that message's id), "tool": str, "tool_arg": str (its latest tool
    # call: the name and a short argument), "desc": str (its Agent call's
    # description, or "")}. `bg_agents_recent` remembers stopped ones (id
    # -> entry plus "stopped_at" (monotonic, its stop), capped at
    # BG_AGENTS_RECENT_CAP) for the still-running note.
    # `pending_agent_calls` holds the Agent calls not yet matched to a
    # SubagentStart: [{"type", "desc", "at", "launcher" (the calling
    # agent's id, "" for the session's own call)}], capped at
    # AGENT_CALLS_CAP. `agents_lines` holds what each answer that carried
    # the status block needs to keep it current, oldest first, capped at
    # AGENTS_LINES_CAP: {"chat_id", "msg_id", "body" (the exact rich
    # markdown before the divider and the block), "shown" (the block in
    # the message), "items" ([{"id", "kind" agent|shell, "label",
    # "started", "parent" (an agent's nearest ancestor the answer lists,
    # or ""), "late" (True on an agent that joined after the send)}]: the
    # snapshot at the send, then each agent a listed agent starts later,
    # up to AGENTS_LINE_ITEMS_CAP), "ids", "ends" (id ->
    # {"outcome" done|finished|stopped|gone, "at"}), "is_rtl", "sent_at",
    # "last_try", "refused", "final", "attempts", "next_try",
    # "gone_at"}. An item keeps its "parent" once known: the links in
    # `agent_parents` go when an agent stops. None of these is a persisted
    # field (monotonic stamps are meaningless across a restart), so a
    # restart leaves pending blocks as last shown. The running set itself
    # is kept across a restart, though: save() writes it, with its waiting
    # flags and links, as the computed "agents" record (wall-clock starts,
    # see agents_record), and load() puts it back (restore_agents).
    # An entry of an agent that ended its turn while an agent it started
    # still runs also carries "waiting": True, "paused" (monotonic, its
    # pause) and "children_gone_at" (monotonic or None: when the monitor
    # first saw none of its own agents running).
    bg_agents: dict = field(default_factory=dict)
    bg_agents_recent: dict = field(default_factory=dict)
    pending_agent_calls: list = field(default_factory=list)
    agents_lines: list = field(default_factory=list)
    # Which agent started which: child id -> parent id, from the parent's
    # own PostToolUse of its Agent call (agent_tree.launched_child), or
    # earlier, at the child's SubagentStart, when the waiting Agent calls
    # of its type all came from one agent (_match_agent_call). Capped at
    # AGENT_PARENTS_CAP; a link goes when its child or its parent
    # finishes, and all go with the session.
    agent_parents: dict = field(default_factory=dict)
    # The job's agents (the `active_subagents` table) the previous run
    # saved, read once by the startup adoption of a still-running card
    # (lifecycle._adopt_running_card), which puts them back on it, and
    # emptied by the startup recovery whatever it decides: a job is never
    # reopened without its card. Entries as agent_tree.validate_agents
    # gives them. Transient.
    restored_job_agents: list = field(default_factory=list)
    # Background shells the main loop started (a `Bash` PostToolUse whose
    # `tool_response` carries a `backgroundTaskId`), from launch until
    # their end is seen: a `<task-notification>` with a `<status>`, a
    # TaskStop/KillShell, the max age, or the session going GONE. task id
    # -> {"label", "summary" (the launch's `Bash: ...` card summary),
    # "started" (monotonic), "in_job" (bool), "history_idx" (int | None)}.
    # ONE table in both roles agents split across two: as a whole it is
    # the running set (like `bg_agents`: the pin, the answer line), and
    # filtered to `in_job` it is the job's table (like `active_subagents`:
    # job_background_open(), the waiting phrase, the card rows). /stop only
    # takes shells out of the job: Escape does not end them. Capped at
    # BG_SHELLS_CAP. Transient, never in _PERSIST_FIELDS (monotonic stamps).
    bg_shells: dict = field(default_factory=dict)
    # Ends seen before their launch: task id -> {"outcome", "exit_code",
    # "at" (monotonic)}, recorded by end_bg_shell for an id no running
    # shell has, read by bg_shell_started. Short-lived
    # (BG_SHELL_EARLY_END_SECONDS), capped (BG_SHELL_EARLY_ENDS_CAP),
    # transient like `bg_shells`.
    bg_shell_early_ends: dict = field(default_factory=dict)
    # Whether the most recent card render had to hide anything (collapsed
    # runs, folded sections, or byte truncation) — stashed by
    # _edit_busy_rich from build_stream_card_ex's report and read by the
    # close path to decide the full-log .md attachment
    # ("layered-card-shedding" requirement 2). Transient.
    last_card_truncated: bool = False

    # -- Live message stack (design.md "Live Message Stack") ---------------

    @property
    def busy_msg_id(self) -> int | None:
        """Compatibility view over the top of ``_live_stack``.

        Returns the top entry's ``msg_id``, or ``None`` when the stack is
        empty. See the setter below and Decision 1's four-value ×
        {empty, non-empty} table for the full contract.
        """
        return self._live_stack[-1].msg_id if self._live_stack else None

    @busy_msg_id.setter
    def busy_msg_id(self, value: int | None) -> None:
        """Round-trips ``None``, ``0``, ``-1``, or a positive int.

        - ``None`` clears the ENTIRE stack — every production call site
          that assigns ``None`` means "this physical message is gone for
          good", never "pop one layer and keep tracking another" (Decision
          1). The one place a true pop-one-keep-the-other operation is
          needed — resuming busy after compacting resolves — uses the
          explicit ``pop_compacting()`` method instead, never this setter.
        - Any other value pushes a fresh ``kind="busy"`` entry when the
          stack is empty, or mutates the top entry's ``msg_id`` in place
          when the stack is non-empty (covers the ``-1``-claim → real-id
          resolution sequence and the ``RichMessageGone`` → ``0``
          sequence, both of which mutate the same logical slot, never
          create a new one).
        """
        if value is None:
            self._live_stack.clear()
            return
        if self._live_stack:
            self._live_stack[-1].msg_id = value
        else:
            self._live_stack.append(
                LiveMessageEntry(
                    msg_id=value, kind="busy",
                    created_at=time.monotonic(), deadline=None,
                )
            )

    def push_compacting(
        self, msg_id: int, now: float, deadline_seconds: float | None,
    ) -> None:
        """Record that a compaction is now in progress at ``msg_id``.

        The caller supplies whichever msg_id is already correct — an
        existing live message's id, or a freshly sent one; this method
        only records it, it never decides whether to reuse or send
        (that decision stays exactly where it lives today, in
        ``notify.py``'s "compacting" branch). After this call,
        ``self.busy_msg_id`` returns ``msg_id``.

        ``deadline_seconds=None`` means no forced expiry; a numeric value
        means ``session_monitor.expired_compacting_sessions`` will include
        this session once ``now`` advances past
        ``now_at_push + deadline_seconds``.
        """
        deadline = now + deadline_seconds if deadline_seconds is not None else None
        self._live_stack.append(
            LiveMessageEntry(
                msg_id=msg_id, kind="compacting",
                created_at=now, deadline=deadline,
            )
        )

    def pop_compacting(self) -> LiveMessageEntry | None:
        """Resolve an in-progress compaction.

        Removes the top entry **iff** its ``kind == "compacting"`` and
        returns it; no-op (returns ``None``) otherwise, including on an
        empty stack. This makes "PostCompact after SessionEnd already
        cleared everything" (research.md's documented race) trivially
        idempotent — calling this twice in a row is always safe.

        After a successful pop, if a message was live *before* the
        ``push_compacting`` call that started this compaction,
        ``self.busy_msg_id`` reverts to returning that message's id
        again; otherwise it returns ``None``.
        """
        if self._live_stack and self._live_stack[-1].kind == "compacting":
            return self._live_stack.pop()
        return None

    def stack_top_kind(self) -> str | None:
        """``"busy"``, ``"compacting"``, or ``None`` if nothing is live."""
        return self._live_stack[-1].kind if self._live_stack else None

    def compacting_is_overdue(self, now: float) -> bool:
        """Is a live compacting card past the deadline it was pushed with?

        The single, shared definition of "this compacting card has been up
        too long to still be believable". Both the monitor's sweeper and
        ``_send_busy_and_animate``'s reclaim branch ask this, so the two
        can never disagree about whether a card is stale.

        False when nothing is live, when the top is an ordinary busy card,
        or when the compaction was pushed with no deadline at all.
        """
        if not self._live_stack:
            return False
        top = self._live_stack[-1]
        if top.kind != "compacting" or top.deadline is None:
            return False
        return now >= top.deadline

    def compacting_started_at(self) -> float | None:
        """``created_at`` of a live compacting top, else ``None``.

        Exists so callers outside this module can report how long a card
        has been up without reaching into ``_live_stack`` — the docstring
        on ``LiveMessageEntry`` promises that list stays private.
        """
        if not self._live_stack:
            return None
        top = self._live_stack[-1]
        return top.created_at if top.kind == "compacting" else None

    def waiting_on_human(self) -> tuple[str | None, str | None]:
        """``(kind, summary)`` of what this session is blocked on —
        ``("question", q)`` for an AskUserQuestion, ``("permission",
        tool_summary)`` for a permission prompt, ``(None, None)`` when the
        inline prompt could not be recorded (the separate-message
        fallback leaves ``pending_permission`` None while the status is
        still INTERACTIVE). Meaningful only while ``status`` is
        INTERACTIVE: the status alone says "waiting", this only
        enriches it — never gate on ``pending_permission``. Shared by
        the Mini App grid and the chat reminder so both agree.
        """
        perm = self.pending_permission
        if not perm:
            return None, None
        if perm.get("ask_question"):
            return "question", perm.get("question")
        return "permission", perm.get("tool_summary")

    def queue_prompt(self, text: str, msg_id: int | None,
                     reply_context: str = "",
                     driver_user_id: int | None = None,
                     cap: int = QUEUE_CAP) -> bool:
        """Append a queued prompt with a current wall-clock timestamp.

        Returns False (without modifying the queue) if the queue is
        already at ``cap`` entries — the caller is expected to surface
        the rejection to the user. Entries are stored as
        ``(text, msg_id, queued_at, reply_context, driver_user_id)``
        5-tuples; existing 2-/3-/4-tuples loaded from older state files
        are upgraded to this shape (see ``load`` for the compatibility
        shim).

        ``msg_id`` is ``None`` for a prompt with no originating Telegram
        message to attribute a reply to (the Mini App's Compact route) —
        already the documented meaning of ``trigger_msg_id: int | None``
        downstream, so this only makes the type honest about a case that
        already occurs.

        ``reply_context`` is the already-rendered reply-pointer string
        (design.md Part 4) computed at queue time, while the live
        ``Update`` this queued prompt came from still exists — empty
        when the queued prompt wasn't a reply. Inserted before ``cap`` so
        every pre-existing 2-positional-arg call site keeps working
        unchanged.

        ``driver_user_id`` is the raw Telegram user id of whoever sent
        this specific message (``transport.driver_id_from_update(update)``
        at every call site with a live ``Update``), captured NOW because
        it will not still be available when this entry is eventually
        drained (review-2#rev-iter2-001). ``None`` when there is no live
        sender to capture (e.g. the hook-triggered ``/compact`` queued by
        ``_compact_session_core``) — the drain then falls back to floor
        permissions, exactly as design.md's merge invariant requires,
        rather than guessing. Never resolved from
        ``sess.last_driver_user_id`` here — that mutable field is the
        exact leak rev-iter1-001/rev-iter2-001 both closed elsewhere;
        this parameter only ever carries an id the caller can vouch for
        as belonging to THIS message.
        """
        if len(self.pending_queue) >= cap:
            log.warning("[%s] queue full (cap=%d); rejecting prompt: %r",
                        self.label, cap, text[:60])
            return False
        self.pending_queue.append(
            (text, msg_id, time.time(), reply_context, driver_user_id)
        )
        return True

    def add_subagent(self, agent_id: str, info: dict) -> list[tuple[str, dict]]:
        """Record a subagent start, holding the table to
        :data:`ACTIVE_SUBAGENTS_CAP` entries.

        The cap is enforced HERE, not at the call site, for the same
        reason ``record_tool`` enforces ``TOOL_HISTORY_CAP`` itself: a
        second caller added later cannot forget it. Returns the evicted
        ``(agent_id, info)`` pairs (oldest by ``started_at``, the same age
        the TTL sweep uses) so the caller can log them with its own
        session context. The just-inserted entry can never evict itself:
        it always carries the newest ``started_at``, and on a tie dict
        insertion order makes the older entry the ``min``.
        """
        # Roadmap 8.22: the liveness stamp is set HERE, not at the call
        # site, for the same reason the cap is — a second caller added
        # later cannot forget it, and an entry with no `last_seen` is
        # exactly the shape `normalize_subagent_liveness` exists to repair.
        # `started_at` first, `time.monotonic()` only as a last resort:
        # this is state.py's own `time`, which the `steady_clock` fixture
        # does NOT rebind (it patches session_monitor and bot.animation
        # only), so a test building an entry without `started_at` under a
        # fake clock would otherwise mix two time bases.
        info.setdefault("last_seen", info.get("started_at") or time.monotonic())
        self.active_subagents[agent_id] = info
        evicted: list[tuple[str, dict]] = []
        while len(self.active_subagents) > ACTIVE_SUBAGENTS_CAP:
            oldest = min(
                self.active_subagents,
                key=lambda a: self.active_subagents[a].get("started_at") or 0.0,
            )
            evicted.append((oldest, self.active_subagents.pop(oldest)))
        return evicted

    def touch_subagent(self, agent_id: str, now: float) -> bool:
        """Refresh the liveness stamp of the LIVE ``active_subagents`` entry
        keyed by *agent_id* (roadmap 8.22). Returns whether one matched.

        hook_receiver calls this for every datagram that carries an
        ``agent_id`` — PreToolUse, PostToolUse, Notification, statusline,
        the folded-prose path, all of them — so a working agent refreshes
        itself several times a minute. That refresh, not the entry's age,
        is what ``session_monitor``'s sweep judges it by.

        Never CREATES an entry. The phantom ``SubagentStop`` events that
        arrive constantly (empty type, unknown id, 0.0s — tolerated by
        design) carry ids this table has never seen, and inventing a row
        for one would resurrect a dead agent and hold the job open forever.
        """
        info = self.active_subagents.get(agent_id)
        if info is None:
            return False
        info["last_seen"] = now
        return True

    def normalize_subagent_liveness(self, now: float, *,
                                    reset: bool = False) -> None:
        """Give every ``active_subagents`` entry a usable ``last_seen``
        stamp (roadmap 8.22). Without *reset*, only entries that have none
        are filled in, preferring their own ``started_at``; with *reset*,
        EVERY entry is restamped to *now*.

        The sweep calls it plainly, to cover entries built by anything
        other than :meth:`add_subagent`: an entry that reached the sweep
        without a stamp must start its silence window now rather than be
        dropped on the first scan, which would empty the table and hand
        idle-recovery a false "finished". Once stamped it is swept like any
        other entry after the window, so a genuinely dead agent still
        clears — one window late, never not at all.

        ``SessionRegistry.load()`` calls it with ``reset=True``, and the
        distinction is the whole point: ``last_seen`` and ``started_at``
        are ``time.monotonic()`` values, seconds since THIS boot. A stamp
        that came out of a file was written against a different boot's
        clock, so it is not merely stale, it is meaningless — large enough
        to drop a live agent on the first scan, or (when the previous
        uptime was longer than this one) in the future, which no elapsed
        comparison can ever exceed and which would make the row immortal.
        Filling in only the missing ones would leave exactly those values
        in place, so a restart restamps all of them and every restored row
        gets one honest silence window. Note this repairs ``last_seen``
        only — ``started_at`` is a previous boot's monotonic value too, and
        it still feeds the card's agent-elapsed row and the
        ``ACTIVE_SUBAGENTS_CAP`` eviction key, so persisting the table
        would need that handled as well; ``reset=True`` alone is not
        enough to make a restored entry wholly safe.

        ``active_subagents`` is transient and deliberately absent from
        ``_PERSIST_FIELDS`` (see the note there), so the ``load()`` call
        has nothing to restamp; it is wired so the invariant holds on the
        day the table is persisted. The job's agents a restart keeps (the
        "agents" record) come back through ``restored_job_agents`` and the
        startup adoption instead, each with ``last_seen`` set to the
        adoption's now and its ``started_at`` placed on this boot's clock.
        """
        for info in self.active_subagents.values():
            if reset or info.get("last_seen") is None:
                info["last_seen"] = now if reset else (
                    info.get("started_at") or now
                )

    def record_agent_tool(self, agent_id: str, summary: str) -> None:
        """Attribute *summary* to the LIVE ``active_subagents`` entry keyed
        by *agent_id* — updates its ``activity``, ``tool_count``,
        ``last_tool_at``, and appends to its own capped ``tools`` list
        instead of the parent's ``tool_history`` ("agent activity rows on
        the busy card": fold an agent's tool calls under its own row
        rather than flooding the parent's timeline).

        A no-op when *agent_id* isn't a live entry (already stopped,
        evicted, or unknown) — the CALLER is responsible for falling back
        to :meth:`record_tool` in that case; this method never creates a
        parent-row entry itself. The cap is enforced HERE, not at the call
        site, for the same reason :meth:`add_subagent` enforces
        ``ACTIVE_SUBAGENTS_CAP`` itself: a second caller added later
        cannot forget it.
        """
        info = self.active_subagents.get(agent_id)
        if info is None:
            return
        info["activity"] = summary
        info["tool_count"] = info.get("tool_count", 0) + 1
        info["last_tool_at"] = time.monotonic()
        tools = info.get("tools")
        if tools is None:
            tools = []
            info["tools"] = tools
        tools.append(summary)
        if len(tools) > AGENT_TOOLS_CAP:
            del tools[: len(tools) - AGENT_TOOLS_CAP]

    def archive_finished_subagent(
        self, agent_type: str, info: dict, elapsed: float,
    ) -> None:
        """Snapshot a just-stopped agent's bookkeeping into
        ``finished_subagents``, capped at ``FINISHED_SUBAGENTS_CAP``
        (oldest dropped from the front — same idiom as
        :meth:`record_tool`'s ``TOOL_HISTORY_CAP`` trim). *info* is the
        popped ``active_subagents`` entry; *agent_type* and *elapsed* come
        from the caller (SubagentStop's own payload), not re-derived from
        *info*, since a hand-built *info* dict in a test may not carry a
        ``type`` matching the event's.

        ``history_idx`` (collapse-busy-card-timeline's one state addition)
        links the archived entry back to its own row/tools in
        ``tool_history``, so the busy card's per-section renderer can find
        a SETTLED agent's own nested `<details>` fold. It is already set
        synchronously by the ``subagent_start`` handler before any
        ``SubagentStop`` can arrive on this single-threaded loop, so
        *info* always carries the right value (or ``None`` for a
        hand-built *info* in a test that never went through that path).
        """
        self.finished_subagents.append({
            "type": agent_type,
            "started_at": info.get("started_at", 0.0),
            "elapsed": elapsed,
            "tool_count": info.get("tool_count", 0),
            "tools": list(info.get("tools", [])),
            "history_idx": info.get("history_idx"),
        })
        if len(self.finished_subagents) > FINISHED_SUBAGENTS_CAP:
            drop = len(self.finished_subagents) - FINISHED_SUBAGENTS_CAP
            del self.finished_subagents[:drop]

    def can_be_stopped(self) -> bool:
        """True while Stop has something to interrupt: a turn running or
        waiting on its person, or an idle session whose job still has a
        background agent or shell going (its card still shows it working,
        CLAUDE.md "Background agents end the turn"). The one definition
        the stop core, /stop and /status's Stop share."""
        return (self.status in (Status.BUSY, Status.INTERACTIVE)
                or self.job_background_open())

    def job_background_open(self) -> bool:
        """True while a background agent launched by this job is still
        running (design.md "model Claude Code background-agent jobs").

        A **job** is a derived condition, not a new ``Status`` value: it is
        ``True`` iff the session is genuinely in the middle of a turn
        (``BUSY``) or has produced an interim Stop while a subagent it
        launched is still going (``IDLE`` with ``active_subagents``
        non-empty). Any other status (``INTERACTIVE``, ``GONE``,
        ``UNKNOWN``) is never "job open" even with stale entries still in
        the table — those statuses mean the session itself isn't mid-turn
        in any sense a card can honestly represent as "waiting".
        """
        if self.status not in (Status.IDLE, Status.BUSY):
            return False
        return bool(
            self.active_subagents
            or self.job_shell_ids()
            or self.job_continuation_active
            or (self.job_grace_until
                and time.monotonic() < self.job_grace_until)
        )

    # ── roadmap 8.41: background agents still running ─────────────────────
    #
    # Pure set bookkeeping: hook_receiver calls these per hook, and nothing
    # here sends, renders or reads a file.

    def bg_agent_started(self, agent_id: str, agent_type: str,
                         now: float) -> bool:
        """SubagentStart: *agent_id* is running. An empty id or type is not
        an agent (the phantom shape) and adds nothing. A restart of a known
        id (an agent resuming itself) refreshes it and keeps what it was
        doing. A stopped one coming back (an id in ``bg_agents_recent``)
        keeps its description and takes no call.

        A new agent is matched to the waiting Agent calls of its type
        (:meth:`_match_agent_call`): it takes a call's description only
        when exactly one is waiting, and is linked to the agent that made
        the calls only when they all came from that one agent. Calls that
        cannot be told apart give nothing, never a wrong description or a
        wrong parent; the parent's own PostToolUse links it later."""
        agent_type = (agent_type or "").strip()
        if not agent_id or not agent_type:
            return False
        parent = None
        info = self.bg_agents.get(agent_id)
        if info is not None:
            info["label"] = agent_type
            info["last_seen"] = now
            # A waiting agent resuming: it works again.
            info.pop("waiting", None)
            info.pop("paused", None)
            info.pop("children_gone_at", None)
        else:
            recent = self.bg_agents_recent.get(agent_id)
            if recent is not None:
                # A stopped agent resumed (SendMessage): no Agent call of
                # its own started it, so it takes none of the waiting
                # ones (it would take another agent's child's place) and
                # keeps the description it had.
                desc, parent = recent.get("desc", ""), None
                launch = recent.get("launch", "unknown")
            else:
                desc, parent, launch = self._match_agent_call(agent_type, now)
            self.bg_agents[agent_id] = {
                "label": agent_type, "last_seen": now, "started": now,
                "prose": "", "prose_msg": "", "tool": "", "tool_arg": "",
                "desc": desc,
                # Roadmap 8.120: how it was launched, for which of several
                # open dialogs Claude Code shows first (prompt_stack).
                "launch": launch,
            }
        self.bg_agents_recent.pop(agent_id, None)
        while len(self.bg_agents) > ACTIVE_SUBAGENTS_CAP:
            oldest = min(self.bg_agents,
                         key=lambda a: self.bg_agents[a]["last_seen"])
            self.bg_agents.pop(oldest)
        self._set_agent_parent(agent_id, parent or "", now)
        return True

    def _expire_agent_calls(self, now: float) -> None:
        self.pending_agent_calls = [
            call for call in self.pending_agent_calls
            if now - call["at"] <= AGENT_CALL_MAX_AGE]

    def _match_agent_call(self, agent_type: str,
                          now: float) -> tuple[str, str | None, str]:
        """``(description, parent, launch)`` for a new agent of
        *agent_type*, from the waiting Agent calls of that type; exact or
        nothing. *launch* (roadmap 8.120) is ``"background"`` or
        ``"foreground"`` only when every waiting call of the type agrees
        (``run_in_background``), else ``"unknown"``:

        - none waiting: ``("", None)``;
        - exactly one: its description and its launcher (None for the
          session's own call), and it is taken off the list;
        - two or more, all from one launcher: that launcher, no
          description (which call this is cannot be told); the oldest is
          taken off and the others of the type lose their description,
          so a later start of the type can never take a sibling's;
        - two or more from different launchers: ``("", None)``, and all
          of that type go (one kept could later link to the wrong one)."""
        self._expire_agent_calls(now)
        same = [c for c in self.pending_agent_calls if c["type"] == agent_type]
        if not same:
            return "", None, "unknown"
        kinds = {"background" if c.get("background") is True else "foreground"
                 for c in same}
        launch = kinds.pop() if len(kinds) == 1 else "unknown"
        launchers = {c.get("launcher", "") for c in same}
        if len(same) == 1:
            self.pending_agent_calls.remove(same[0])
            return same[0]["desc"], same[0].get("launcher", "") or None, launch
        if len(launchers) > 1:
            self.pending_agent_calls = [
                c for c in self.pending_agent_calls if c["type"] != agent_type]
            return "", None, launch
        self.pending_agent_calls.remove(same[0])
        for call in same[1:]:
            call["desc"] = ""
        return "", same[0].get("launcher", "") or None, launch

    def agent_call_launched(self, subagent_type: str, description: str,
                            now: float, launcher: str = "", *,
                            background: bool = False) -> None:
        """An Agent tool call: remember its type, description and launcher
        (the calling agent's id, "" for the session's own call) until the
        agent it starts says SubagentStart. A call with no type records
        nothing (it could match nothing). *background*: the call asked to
        run in the background (``run_in_background: true``, roadmap
        8.120), kept only when true."""
        subagent_type = (subagent_type or "").strip() if isinstance(
            subagent_type, str) else ""
        if not subagent_type:
            return
        self._expire_agent_calls(now)
        call = {
            "type": subagent_type,
            "desc": description if isinstance(description, str) else "",
            "at": now,
            "launcher": launcher if isinstance(launcher, str) else "",
        }
        if background is True:
            call["background"] = True
        self.pending_agent_calls.append(call)
        if len(self.pending_agent_calls) > AGENT_CALLS_CAP:
            del self.pending_agent_calls[:len(self.pending_agent_calls)
                                         - AGENT_CALLS_CAP]

    def bg_agent_prose(self, agent_id: str, message_id: str, delta: str,
                       now: float) -> None:
        """A MessageDisplay of the agent's own: its latest message. A new
        message replaces the kept one only once it says something visible;
        more of the same message is added to it (up to
        BG_AGENT_PROSE_CAP). An id that is not a running agent changes
        nothing."""
        info = self.bg_agents.get(agent_id)
        if info is None or not isinstance(delta, str):
            return
        message_id = message_id if isinstance(message_id, str) else str(message_id or "")
        if message_id and message_id == info.get("prose_msg"):
            info["prose"] = (info.get("prose", "") + delta)[:BG_AGENT_PROSE_CAP]
        elif delta.strip():
            info["prose"] = delta[:BG_AGENT_PROSE_CAP]
            info["prose_msg"] = message_id

    def bg_agent_tool(self, agent_id: str, tool_name: str, arg: str,
                      now: float) -> None:
        """A PreToolUse of the agent's own: its latest tool call (the name
        and a short argument). An id that is not a running agent changes
        nothing."""
        info = self.bg_agents.get(agent_id)
        if info is None or not isinstance(tool_name, str):
            return
        info["tool"] = tool_name
        info["tool_arg"] = arg if isinstance(arg, str) else ""

    def _end_in_lines(self, item_id: str, outcome: str, now: float) -> None:
        """Stamp *item_id*'s end on every pending answer that lists it."""
        for rec in self.agents_lines:
            if item_id in rec.get("ids", ()):
                rec.setdefault("ends", {})[item_id] = {"outcome": outcome,
                                                       "at": now}

    def bg_agent_stopped(self, agent_id: str, now: float) -> bool:
        """SubagentStop: the matching agent stopped. An id never started
        (the constant phantom SubagentStops) matches nothing. Its links go
        with it: an agent finished is nobody's running child, and an id
        used again starts with no children of its own."""
        info = self.bg_agents.pop(agent_id, None)
        if info is None:
            return False
        self._forget_agent_links(agent_id)
        # Its Agent calls that no agent took: none can start now, and kept,
        # one could later link an agent of another to it.
        self.pending_agent_calls = [
            c for c in self.pending_agent_calls
            if c.get("launcher", "") != agent_id]
        info.pop("waiting", None)
        info.pop("paused", None)
        info.pop("children_gone_at", None)
        info["stopped_at"] = now
        self.bg_agents_recent[agent_id] = info
        while len(self.bg_agents_recent) > BG_AGENTS_RECENT_CAP:
            self.bg_agents_recent.pop(next(iter(self.bg_agents_recent)))
        self._end_in_lines(agent_id, "done", now)
        return True

    def agent_launch(self, agent_id: str) -> str:
        """How agent *agent_id* was launched (roadmap 8.120):
        ``"background"``, ``"foreground"`` or ``"unknown"`` (a teammate, a
        call that could not be matched, an agent not running)."""
        info = self.bg_agents.get(agent_id) if agent_id else None
        launch = info.get("launch") if isinstance(info, dict) else None
        return launch if launch in ("background", "foreground") else "unknown"

    def bg_agent_went_async(self, agent_id: str) -> None:
        """An Agent call's end says *agent_id* runs on asynchronously: one
        recorded as foreground was moved to the background mid-run, so
        whether its next dialogs race their hook is no longer known."""
        info = self.bg_agents.get(agent_id)
        if isinstance(info, dict) and info.get("launch") == "foreground":
            info["launch"] = "unknown"
            info["went_async"] = True

    def agent_survives_interrupt(self, agent_id: str | None) -> bool:
        """Whether running agent *agent_id* goes on after Claude's turn is
        interrupted at the terminal (roadmap 8.120): one launched in the
        background, or a foreground one moved there mid-run. A foreground
        agent, or one whose launch is not known, ends with the turn."""
        if not agent_id:
            return False
        if (agent_id not in self.active_subagents
                and agent_id not in self.bg_agents):
            return False
        info = self.bg_agents.get(agent_id)
        if not isinstance(info, dict):
            return False
        return info.get("launch") == "background" or info.get("went_async") is True

    def bg_agent_touch(self, agent_id: str, now: float) -> None:
        """Any hook carrying *agent_id*: that agent is alive."""
        info = self.bg_agents.get(agent_id)
        if info is not None:
            info["last_seen"] = now

    def bg_agent_still_running(self, agent_id: str, now: float) -> bool:
        """The parent's <task-notification> says *agent_id* stopped with
        background work of its own still running: it resumes later, so it
        is still one of this session's running agents. Only an id this
        session saw start can come back — a background shell's task id
        never had a SubagentStart."""
        if agent_id in self.bg_agents:
            self.bg_agents[agent_id]["last_seen"] = now
            return True
        info = self.bg_agents_recent.pop(agent_id, None)
        if info is None:
            return False
        info["last_seen"] = now
        self.bg_agents[agent_id] = info
        for rec in self.agents_lines:
            rec.get("ends", {}).pop(agent_id, None)
        return True

    def sweep_bg_agents(self, now: float, window: float) -> list[str]:
        """Drop agents not heard from in *window* seconds (a lost
        SubagentStop) and return their ids.

        Silence is not completion: an agent parked on long background work
        of its own emits nothing. So an answer whose status block lists an
        expired agent is left exactly as last shown — never settled to
        done on no evidence. A waiting agent is silent by nature while its
        own agents work: it is never dropped here while shielded
        (agent_shielded); sweep_unresumed_parents bounds it instead."""
        # An agent that owns an open prompt fires no hooks while its
        # dialog waits (roadmap 8.120): it is not silent, it is waiting.
        owners = self.prompt_owners()
        silent = [a for a, info in self.bg_agents.items()
                  if now - info["last_seen"] > window
                  and not self.agent_shielded(a, now)
                  and a not in owners]
        for agent_id in silent:
            self.bg_agents.pop(agent_id, None)
            self._forget_agent_links(agent_id)
        if silent:
            self.agents_lines = [
                rec for rec in self.agents_lines
                if not set(rec["ids"]) & set(silent)
            ]
        return silent

    def clear_bg_agents(self) -> None:
        """The session is gone: no agent or shell of it is running, and no
        line of it is owed an edit."""
        self.bg_agents.clear()
        self.bg_agents_recent.clear()
        self.agent_parents.clear()
        self.restored_job_agents = []
        self.pending_agent_calls = []
        self.bg_shells.clear()
        self.bg_shell_early_ends.clear()
        self.agents_lines = []

    def add_agents_line(self, rec: dict) -> None:
        """Remember an answer that carried the status block, capped at
        AGENTS_LINES_CAP (the oldest is then left as sent)."""
        self.agents_lines.append(rec)
        if len(self.agents_lines) > AGENTS_LINES_CAP:
            del self.agents_lines[:len(self.agents_lines) - AGENTS_LINES_CAP]

    def bg_agent_labels(self) -> list[str]:
        return [info["label"] for info in self.bg_agents.values()]

    # ── agents waiting for agents of their own ────────────────────────────
    #
    # A background agent P that starts an agent C and then ends its own
    # turn to wait for it gets an ordinary SubagentStop at that pause, and
    # a fresh SubagentStart with the same id when it resumes. Which agent
    # started which comes from P's own PostToolUse of its Agent call (it
    # carries agent_id=P and the child's id in its tool_response), never
    # from a SubagentStart (it carries the child's id only).

    def _forget_agent_links(self, agent_id: str) -> None:
        """*agent_id* finished or left: its own link, and its children's."""
        self.agent_parents.pop(agent_id, None)
        for child in [c for c, p in self.agent_parents.items()
                      if p == agent_id]:
            self.agent_parents.pop(child, None)

    def agent_children_running(self, agent_id: str) -> list[str]:
        """The running agents *agent_id* started."""
        return [child for child, parent in self.agent_parents.items()
                if parent == agent_id and child in self.bg_agents]

    def agent_is_waiting(self, agent_id: str) -> bool:
        info = self.bg_agents.get(agent_id)
        return bool(info is not None and info.get("waiting"))

    def _set_agent_parent(self, child: str, parent: str, now: float) -> bool:
        """*parent* started *child*: the one place a link is made. Every
        pending answer's block follows it: a *child* the answer lists goes
        under its nearest listed ancestor (the link is the truth, so a row
        shown at the top moves), and a *child* it does not list joins it,
        under that ancestor, when one is listed (an agent a listed agent
        started after the answer went out). A child already stopped (its
        link came at its end) joins as done. An agent the session itself
        starts has no link, so it never joins an earlier answer. False
        (and nothing changes) for an empty or self link.

        A link that replaces a different one (the parent's PostToolUse
        correcting a launcher match) and reaches nothing an answer lists
        proves the row's place wrong there: a row the answer was sent with
        goes to the top, and a row that joined late only through the old
        link leaves the answer (see :meth:`_drop_late_item`)."""
        if not parent or not child or parent == child:
            return False
        old = self.agent_parents.pop(child, None)
        self.agent_parents[child] = parent
        while len(self.agent_parents) > AGENT_PARENTS_CAP:
            self.agent_parents.pop(next(iter(self.agent_parents)))
        replaced = old is not None and old != parent
        for rec in self.agents_lines:
            self._adopt_in_line(rec, child, now, replaced)
        return True

    def _adopt_in_line(self, rec: dict, child: str, now: float,
                       replaced: bool = False) -> None:
        ids = rec.setdefault("ids", [])
        listed = set(ids)
        anc = _agent_tree.nearest_listed(self.agent_parents, child, listed)
        items = rec.setdefault("items", [])
        if child in listed:
            mine = [item for item in items if item.get("id") == child]
            if not anc and replaced and any(item.get("late")
                                            for item in mine):
                self._drop_late_item(rec, child)
                return
            if anc or replaced:
                for item in mine:
                    item["parent"] = anc
        elif anc and sum(1 for item in items if item.get("kind") == "agent"
                         ) < AGENTS_LINE_ITEMS_CAP:
            info = self.bg_agents.get(child)
            ended = False
            if info is None:
                info = self.bg_agents_recent.get(child)
                ended = info is not None
            if info is None:
                return
            items.append({"id": child, "kind": "agent",
                          "label": info.get("label", ""),
                          "started": info.get("started"), "parent": anc,
                          "late": True})
            ids.append(child)
            listed.add(child)
            if ended:
                rec.setdefault("ends", {})[child] = {
                    "outcome": "done", "at": info.get("stopped_at", now)}
        else:
            return
        # The child's own agents the answer lists sit under it now, when it
        # is nearer than the ancestor they were under.
        for item in items:
            if item.get("kind") != "agent" or item.get("id") == child:
                continue
            nearer = _agent_tree.nearest_listed(self.agent_parents,
                                                item["id"], listed)
            if nearer == child:
                item["parent"] = child

    def _drop_late_item(self, rec: dict, child: str) -> None:
        """*child* joined *rec* after the send only through a link now
        replaced by one that reaches nothing *rec* lists: it is not one of
        the answer's agents (as an agent the session starts never joins an
        earlier answer), so it leaves, and with it each late row that
        joined under it (it joined through *child*, the same way). A row
        the answer was sent with that sat under a leaving row goes under
        its nearest listed ancestor that stays, or to the top."""
        gone = {child}
        while True:
            more = {item["id"] for item in rec.get("items", ())
                    if item.get("late") and item["id"] not in gone
                    and item.get("parent") in gone}
            if not more:
                break
            gone |= more
        rec["items"] = [item for item in rec.get("items", ())
                        if item.get("id") not in gone]
        rec["ids"] = [i for i in rec.get("ids", ()) if i not in gone]
        for agent_id in gone:
            rec.get("ends", {}).pop(agent_id, None)
        listed = set(rec["ids"])
        for item in rec["items"]:
            if item.get("kind") == "agent" and item.get("parent") in gone:
                item["parent"] = _agent_tree.nearest_listed(
                    self.agent_parents, item["id"], listed)

    def agent_card_label(self, agent_id: str, agent_type: str) -> str:
        """What the busy card names *agent_id* by: its type, or ``"<top>
        › <type>"`` when an agent started it and the agent at the top of
        its chain is known by name (an agent the session started itself,
        or one whose starter is not known, is its type alone)."""
        top = _agent_tree.top_ancestor(self.agent_parents, agent_id)
        if not top:
            return agent_type
        label = ((self.bg_agents.get(top) or {}).get("label")
                 or (self.active_subagents.get(top) or {}).get("type")
                 or (self.bg_agents_recent.get(top) or {}).get("label"))
        if not label:
            return agent_type
        return f"{label}{_agent_tree.LINEAGE_SEP}{agent_type}"

    def agent_launched_child(self, parent: str, child: str,
                             now: float) -> bool:
        """*parent* started *child* (its own Agent call returned). Returns
        whether this brought *parent* back as a running agent: the link
        arrived after the parent's pause had already been taken for its
        finish (it is in ``bg_agents_recent``) while the child runs. Then
        it is waiting, exactly as if the link had come first, on the line
        and the pin; its job row is not reopened (its resume is)."""
        if not self._set_agent_parent(child, parent, now):
            return False
        if (parent in self.bg_agents or child not in self.bg_agents
                or parent not in self.bg_agents_recent):
            return False
        info = self.bg_agents_recent.pop(parent)
        info["last_seen"] = now
        info["waiting"] = True
        info["paused"] = now
        info["children_gone_at"] = None
        self.bg_agents[parent] = info
        for rec in self.agents_lines:
            rec.get("ends", {}).pop(parent, None)
        return True

    def bg_agent_pause(self, agent_id: str, now: float) -> list[str]:
        """SubagentStop for *agent_id*, asked first: if it is a running
        agent with at least one agent of its own still running, it has
        only paused to wait for them. It is then marked waiting and stays
        running everywhere; the running children are returned. An empty
        list means the stop is a finish (or a phantom): nothing changed.
        Recursion falls out: a child that is itself waiting is still in
        ``bg_agents``, so it counts as running.

        This holds for an agent its caller launched in the foreground too,
        on purpose: Claude Code (2.1.296, the Agent tool's synchronous
        path) moves a foreground agent that is done while an agent of its
        own still runs into the background (``isBackgrounded``) and returns
        ``{isAsync: true, status: "async_launched"}`` to its caller, so it
        waits and resumes exactly like an agent launched in the background.
        Its caller's Agent PostToolUse only arrives after this stop, so
        whether it was launched in the background is not known here, and
        need not be."""
        info = self.bg_agents.get(agent_id)
        if info is None:
            return []
        children = self.agent_children_running(agent_id)
        if not children:
            return []
        info["waiting"] = True
        info["paused"] = now
        info["children_gone_at"] = None
        info["last_seen"] = now
        return children

    def agent_resumed(self, agent_id: str) -> bool:
        """A tool event of *agent_id*'s own: if it was waiting, it works
        again (its resume SubagentStart was lost, or dropped as a repeat).
        Returns whether it was waiting."""
        info = self.bg_agents.get(agent_id)
        if info is None or not info.get("waiting"):
            return False
        info.pop("waiting", None)
        info.pop("paused", None)
        info.pop("children_gone_at", None)
        return True

    def agent_shielded(self, agent_id: str, now: float) -> bool:
        """Whether the silence sweeps must leave *agent_id* alone: it is
        waiting, and an agent of its own runs, or the last of them ended
        at most WAITING_PARENT_RESUME_SECONDS ago (or that is not stamped
        yet)."""
        info = self.bg_agents.get(agent_id)
        if info is None or not info.get("waiting"):
            return False
        if self.agent_children_running(agent_id):
            return True
        gone = info.get("children_gone_at")
        return gone is None or now - gone <= WAITING_PARENT_RESUME_SECONDS

    def sweep_unresumed_parents(self, now: float) -> list[str]:
        """Drop the waiting agents that did not resume within
        WAITING_PARENT_RESUME_SECONDS after the last of their own agents
        ended, and return their ids. When none of its agents runs is
        stamped lazily, by the first sweep that sees it (and cleared if
        one runs again). A dropped agent leaves the running set and the
        job's table; an answer whose block lists it is left exactly as
        last shown (silence, never "done")."""
        dropped = []
        for agent_id, info in self.bg_agents.items():
            if not info.get("waiting"):
                continue
            if self.agent_children_running(agent_id):
                info["children_gone_at"] = None
                continue
            gone = info.get("children_gone_at")
            if gone is None:
                info["children_gone_at"] = now
            elif now - gone > WAITING_PARENT_RESUME_SECONDS:
                dropped.append(agent_id)
        for agent_id in dropped:
            self.bg_agents.pop(agent_id, None)
            self.active_subagents.pop(agent_id, None)
            self._forget_agent_links(agent_id)
        if dropped:
            self.agents_lines = [
                rec for rec in self.agents_lines
                if not set(rec["ids"]) & set(dropped)
            ]
        return dropped

    # ── the running agents across a restart ───────────────────────────────

    def agents_record(self, now_mono: float, now_wall: float) -> dict | None:
        """The state file's ``"agents"`` record: the running set (waiting
        flags and links included) and the job's table, their monotonic
        starts as wall-clock times. None when no agent runs."""
        def wall(started: object) -> float:
            if isinstance(started, bool) or not isinstance(
                    started, (int, float)):
                started = now_mono
            return min(now_wall, now_wall - (now_mono - float(started)))

        running = [{
            "id": agent_id, "label": info.get("label", ""),
            "started_wall": wall(info.get("started")),
            "waiting": bool(info.get("waiting")),
            "parent": self.agent_parents.get(agent_id, ""),
            "desc": info.get("desc", ""), "prose": info.get("prose", ""),
            "prose_msg": info.get("prose_msg", ""),
            "tool": info.get("tool", ""), "tool_arg": info.get("tool_arg", ""),
        } for agent_id, info in self.bg_agents.items()]
        job = [{
            "id": agent_id, "type": info.get("type") or "",
            "started_wall": wall(info.get("started_at")),
            "tool_count": info.get("tool_count") or 0,
            "activity": info.get("activity") or "",
        } for agent_id, info in self.active_subagents.items()]
        return _agent_tree.encode_agents(running, job, now_wall,
                                         cap=ACTIVE_SUBAGENTS_CAP)

    def restore_agents(self, raw: object, now_mono: float,
                       now_wall: float) -> tuple[int, int]:
        """Put back the running agents a previous run saved
        (:meth:`agents_record`): the running set with its waiting flags
        and links, and the job's table into ``restored_job_agents`` for
        the startup adoption. Returns (running, of which waiting). An
        unusable record restores nothing (one WARNING) and never raises.
        Every restored agent gets one honest silence window from *now_mono*
        (its ``last_seen``), as normalize_subagent_liveness(reset=True)
        intends for any restored stamp."""
        rec = _agent_tree.validate_agents(
            raw, now_mono, now_wall,
            max_gap=AGENTS_RESTORE_MAX_GAP_SECONDS, cap=ACTIVE_SUBAGENTS_CAP)
        if rec is None:
            log.warning("[%s] saved running agents ignored - invalid or "
                        "too old a record", self.label)
            return 0, 0
        waiting = 0
        restarted = 0
        for entry in rec["running"]:
            info = {
                "label": entry["label"], "last_seen": now_mono,
                "started": entry["started"], "prose": entry["prose"],
                "prose_msg": entry["prose_msg"], "tool": entry["tool"],
                "tool_arg": entry["tool_arg"], "desc": entry["desc"],
            }
            if entry["waiting"]:
                info["waiting"] = True
                info["paused"] = now_mono
                info["children_gone_at"] = None
                waiting += 1
            restarted += entry["clock_restarted"]
            self.bg_agents[entry["id"]] = info
            if entry["parent"]:
                self.agent_parents[entry["id"]] = entry["parent"]
        self.restored_job_agents = list(rec["job"])
        if restarted:
            log.info("[%s] %d restored agents' clocks restarted (the saved "
                     "start is older than this boot's clock)", self.label,
                     restarted)
        return len(rec["running"]), waiting

    def take_restored_job_agents(self) -> list[dict]:
        """The saved job agents, read once (the startup adoption)."""
        restored, self.restored_job_agents = self.restored_job_agents, []
        return restored

    # ── background shells ─────────────────────────────────────────────────
    #
    # Pure bookkeeping, like the agents' set above: hook_receiver and the
    # card's queue scan call these, and nothing here sends or reads a file.

    def bg_shell_started(self, task_id: str, label: str, summary: str,
                         now: float) -> dict | None:
        """A background shell launched by the main loop is running. An
        empty id adds nothing (``None``). It belongs to the running job
        until it ends, /stop takes it out, or a new turn does not carry it.
        Returns its entry.

        A launch whose end was already seen (bg_shell_early_ends: the
        launch's PostToolUse was held while the wake-up was not) is ended
        at once with that outcome, exactly as end_bg_shell does for the
        job state at this moment, and never counts as running. Its entry
        is returned popped, with the settled ``row`` (text, mark) for the
        launch's card row."""
        if not task_id:
            return None
        self.bg_shells[task_id] = {
            "label": label, "summary": summary, "started": now,
            "in_job": True, "history_idx": None,
        }
        early = self._pop_early_end(task_id, now)
        if early is not None:
            return self.end_bg_shell(task_id, early["outcome"],
                                     max(now, early["at"]),
                                     early["exit_code"])
        while len(self.bg_shells) > BG_SHELLS_CAP:
            oldest = min(self.bg_shells,
                         key=lambda t: self.bg_shells[t]["started"])
            self.bg_shells.pop(oldest)
        return self.bg_shells.get(task_id)

    def _pop_early_end(self, task_id: str, now: float) -> dict | None:
        """The unexpired end recorded for *task_id* before its launch, if
        any; expired entries are dropped on the way."""
        for old in [t for t, e in self.bg_shell_early_ends.items()
                    if now - e["at"] > BG_SHELL_EARLY_END_SECONDS]:
            self.bg_shell_early_ends.pop(old)
        return self.bg_shell_early_ends.pop(task_id, None)

    def _record_early_end(self, task_id: str, outcome: str, now: float,
                          exit_code: int | None) -> None:
        """Remember an end seen for an id no running shell has, in case
        its launch is handled after it. Capped: the oldest goes first."""
        if not task_id:
            return
        self.bg_shell_early_ends[task_id] = {
            "outcome": outcome, "exit_code": exit_code, "at": now,
        }
        while len(self.bg_shell_early_ends) > BG_SHELL_EARLY_ENDS_CAP:
            oldest = min(self.bg_shell_early_ends,
                         key=lambda t: self.bg_shell_early_ends[t]["at"])
            self.bg_shell_early_ends.pop(oldest)

    def job_shell_ids(self) -> list[str]:
        """The running shells the open job waits on."""
        return [task_id for task_id, info in self.bg_shells.items()
                if info.get("in_job")]

    def job_work_running(self) -> bool:
        """Background work of the job's own still runs: an agent or a
        shell."""
        return bool(self.active_subagents or self.job_shell_ids())

    def bg_shell_labels(self) -> list[str]:
        return [info["label"] for info in self.bg_shells.values()]

    def bg_shells_leave_job(self) -> None:
        """/stop: the job is over, but Escape does not end a background
        shell, so each stays counted as running (pin, answer line) without
        holding a job open or a row on a card."""
        for info in self.bg_shells.values():
            info["in_job"] = False
            info["history_idx"] = None

    def end_bg_shell(self, task_id: str, outcome: str, now: float,
                     exit_code: int | None = None) -> dict | None:
        """The ONE place a shell's end is applied. *outcome* is a
        notification status (completed, failed, killed), ``stopped``
        (TaskStop/KillShell) or ``lost`` (the max age). An id that is not
        a running shell of this session (an agent's, a subagent's shell's,
        a Monitor's, one already ended, or one whose launch is not handled
        yet) changes nothing and returns ``None``; its end is remembered a
        short while for a launch handled after it (bg_shell_started).
        Otherwise the popped entry, with its settled ``row`` (text, mark).

        Its card row settles to the outcome. A real end of the job's last
        background work after an interim Stop keeps the job open for the
        wake-up Claude sends next, exactly as SubagentStop does. A lost
        shell is not known to have ended: an answer whose line named it is
        left as sent, never settled to done on no evidence."""
        info = self.bg_shells.pop(task_id, None)
        if info is None:
            if outcome != "lost":
                self._record_early_end(task_id, outcome, now, exit_code)
            return None
        text, mark = _bg_shells.settled_row(
            info["label"], outcome, now - info["started"], exit_code)
        info["row"] = (text, mark)
        idx = info.get("history_idx")
        if idx is not None and 0 <= idx < len(self.tool_history):
            self.tool_history[idx] = (text, mark)
            self.stream_dirty = True
        if outcome == "lost":
            self.agents_lines = [rec for rec in self.agents_lines
                                 if task_id not in rec["ids"]]
            return info
        # An answer's block: killed or stopped by Claude reads "stopped",
        # an exit of its own (even a failing one) "finished".
        self._end_in_lines(
            task_id, "stopped" if outcome in ("killed", "stopped")
            else "finished", now)
        if (info.get("in_job") and self.job_interim_seen
                and not self.job_work_running()):
            self.job_grace_until = now + JOB_CONTINUATION_GRACE_SECONDS
        return info

    def sweep_bg_shells(self, now: float, max_age: float) -> list[str]:
        """Drop the shells running longer than *max_age* seconds (an end
        that was never seen: a lost TaskStop datagram, a daemon restart
        between launch and end) and return their ids."""
        old = [task_id for task_id, info in self.bg_shells.items()
               if now - info["started"] > max_age]
        for task_id in old:
            self.end_bg_shell(task_id, "lost", now)
        return old

    def agents_lines_due(self, now: float) -> list[dict]:
        """The answers whose status block may be edited at *now*: the last
        attempt (the send counts as one) is AGENTS_LINE_EDIT_INTERVAL old
        and any retry's wait has passed. Called by the monitor's scan.

        It keeps every answer's ends current first: an item that left
        without an end of its own (evicted at a cap) is stamped ``gone``;
        one that is running again (an agent back after the still-running
        note) loses its stamp. ``final`` is set once every item has ended
        at least BG_AGENTS_SETTLE_SECONDS ago, and ``gone_at`` is when the
        last of them ended (0.0 while one runs)."""
        due = []
        for rec in self.agents_lines:
            ends = rec.setdefault("ends", {})
            for item_id in rec["ids"]:
                if item_id in self.bg_agents or item_id in self.bg_shells:
                    ends.pop(item_id, None)
                elif item_id not in ends:
                    ends[item_id] = {"outcome": "gone", "at": now}
            if all(item_id in ends for item_id in rec["ids"]):
                rec["gone_at"] = max((e["at"] for e in ends.values()),
                                     default=now)
            else:
                rec["gone_at"] = 0.0
            rec["final"] = bool(rec["gone_at"]) and (
                now - rec["gone_at"] >= BG_AGENTS_SETTLE_SECONDS)
            if (now >= rec.get("last_try", 0.0) + AGENTS_LINE_EDIT_INTERVAL
                    and now >= rec.get("next_try", 0.0)):
                due.append(rec)
        return due

    def work_in_flight_reason(self, now: float) -> tuple[str, float] | None:
        """Why a "gone quiet" reading of this session would be a false
        positive right now, or ``None`` if neither applies.

        Returns ``(kind, elapsed_seconds)`` — ``kind`` is ``"tool"`` or
        ``"compact"`` — while a PreToolUse/PostToolUse pair or a
        PreCompact/post-compact-SessionStart pair is still open AND
        younger than its own cap (``TOOL_INFLIGHT_MAX_SECONDS`` /
        ``COMPACT_INFLIGHT_MAX_SECONDS``), or ``"turn start"`` while the
        running turn's own hooks are held (roadmap 8.62). No hooks fire mid-tool-call or
        mid-compaction, so the transcript and every hook-driven timestamp
        look quiet even though the session is genuinely working.

        The single, shared definition of "still legitimately working" —
        both the stale-busy watchdog and the idle-recovery fallback ask
        THIS instead of re-deriving the condition, so the two checks can
        never drift apart on what counts as a false "gone quiet" alarm
        (the idle-recovery fallback used to skip this check entirely,
        recovering BUSY→IDLE mid-tool-call on any quiet, complete-looking
        transcript tail).

        A tool or compaction older than its own cap returns ``None`` — a
        genuinely wedged tool must not stand either check down forever.
        """
        tool_start = self.pending_tool_started_at
        if tool_start is not None:
            elapsed = now - tool_start
            if elapsed < TOOL_INFLIGHT_MAX_SECONDS:
                return "tool", elapsed
        compact_start = self.compact_started_at
        if compact_start is not None:
            elapsed = now - compact_start
            if elapsed < COMPACT_INFLIGHT_MAX_SECONDS:
                return "compact", elapsed
        if self.turn_state_held():
            # A turn waiting for its card state (roadmap 8.62): its own
            # hooks, a tool call's PreToolUse included, are held, so the
            # tool it is running is not recorded yet.
            elapsed = now - self.turn_state_hold_since
            if elapsed < TURN_STATE_HOLD_SECONDS:
                return "turn start", elapsed
        return None

    def work_in_flight(self, now: float) -> bool:
        """True while a tool call or compaction is legitimately in flight
        — see :meth:`work_in_flight_reason` for the shared definition."""
        return self.work_in_flight_reason(now) is not None

    def forget_park(self) -> None:
        """Clear every field of a Claude Code background-job park (the
        caller removes the link file first: it reads them). The terminal
        guard's once-per-park warning starts over for the next park."""
        self.parked_job = ""
        self.parked_job_session_id = ""
        self.parked_since = 0.0
        self.parked_source = ""
        self.parked_from_session_id = ""
        self.park_told.clear()
        self.park_epoch += 1
        self.park_changed_at = time.monotonic()

    def arm_bg_release(self) -> tuple:
        """A bring-back is about to launch the conversation again: the
        messages held for the park go out once the relaunched claude is up
        (its first own SessionStart, or the monitor's backstop). Returns
        the release state from before, for :meth:`restore_bg_release`."""
        prior = (self.bg_release_pending, self.bg_release_armed_at,
                 self.bg_start_seen)
        self.bg_release_pending = True
        self.bg_release_armed_at = time.monotonic()
        self.bg_start_seen = False
        return prior

    def restore_bg_release(self, prior: tuple) -> None:
        """The bring-back that armed the release (:meth:`arm_bg_release`
        returned *prior*) did not bring the session back: the release is
        as it was before, so an earlier one still pending stays pending,
        and no later SessionStart releases anything for this one."""
        self.bg_release_pending, self.bg_release_armed_at, self.bg_start_seen = prior

    def disarm_bg_release(self) -> None:
        """The bring-back that armed the release did not bring the session
        back: no later SessionStart may release anything for it."""
        self.bg_release_pending = False
        self.bg_release_armed_at = 0.0
        self.bg_start_seen = False

    def claim_bg_release(self) -> bool:
        """True exactly once per armed release: checks and clears it with
        no await in between, so the bring-back, the SessionStart and the
        backstop together send one held message, never two."""
        if not self.bg_release_pending or self.bg_release_blocked:
            return False
        self.disarm_bg_release()
        return True

    def busy_card_should_animate(self) -> bool:
        """True when this session has a live busy card that ought to be
        ticking: a real (positive) message id whose live-stack top is the
        busy card itself, on a session that is BUSY or has a background
        job open — the same liveness rule ``_animate_busy`` loops on.

        Two states own a card that is NOT the animator's to touch, and both
        are excluded here: a ``compacting`` top (the card is the compaction
        dots, driven by ``_animate_compact`` in the same ``animate_task``
        slot) by the explicit check, and INTERACTIVE (the card is the
        permission prompt — its keyboard would be repainted away) by the
        last line, since ``job_background_open()`` is by definition never
        true outside IDLE/BUSY. Shared by the session monitor's watchdog
        and the bot's resume path so the two cannot disagree.
        """
        msg_id = self.busy_msg_id
        if not msg_id or msg_id < 0:
            return False
        if self.stack_top_kind() != "busy":
            return False
        return self.status == Status.BUSY or self.job_background_open()

    def close_turn(self, turn: int | None = None) -> None:
        """Mark *turn* (the current one by default) and every earlier turn
        finished for the card step (roadmap 8.57): no card is opened for
        them from here on. Never moves backwards — a slow finish of an
        older turn must not reopen a newer turn that has already ended."""
        turn = self.turn_seq if turn is None else turn
        if self.closed_turn_seq is None or turn > self.closed_turn_seq:
            self.closed_turn_seq = turn

    def hold_turn_state(self, turn: int) -> None:
        """Hold *turn*'s own hook events until its card state exists
        (roadmap 8.62). Only the running turn is held: a late request for
        a turn that is over holds nothing (and so never frees the running
        turn's hold). An older turn's hold is released first: its turn is
        over, and whatever it held may run now."""
        if turn != self.turn_seq:
            return
        if self.turn_state_hold_for == turn and self.turn_state_hold is not None:
            return
        self.release_turn_state()
        self.turn_state_hold_for = turn
        self.turn_state_hold = asyncio.Event()
        self.turn_state_hold_since = time.monotonic()

    def release_turn_state(self, turn: int | None = None) -> None:
        """Let the held events through: *turn*'s card state exists now, or
        its card request was dropped, or the hold gave up. ``None`` releases
        any hold; a turn releases its own hold and any older one, never a
        newer turn's. Idempotent."""
        held_for = self.turn_state_hold_for
        if held_for is None or (turn is not None and held_for > turn):
            return
        if self.turn_state_hold is not None:
            self.turn_state_hold.set()
        self.turn_state_hold_for = None
        self.turn_state_hold = None

    def turn_state_held(self) -> bool:
        """True while the running turn's events must wait: it is the turn
        being held, and the card state still belongs to an older turn."""
        return (self.turn_state_hold is not None
                and self.turn_state_hold_for == self.turn_seq
                and self.card_turn_seq != self.turn_seq)

    def mark_send_now_pressed(self) -> None:
        """A send-now chord reached this session's terminal."""
        self.send_now_pressed_at = time.monotonic()

    def send_now_take_open(self) -> bool:
        """Whether a `dequeue` read now is Claude taking its oldest queued
        message after cancelling the running step for a Send now: a press
        is pending (no IDLE since) and within SEND_NOW_DEQUEUE_WINDOW."""
        return (self.send_now_pressed_at > 0.0
                and time.monotonic() - self.send_now_pressed_at
                <= SEND_NOW_DEQUEUE_WINDOW)

    def card_started_wall(self, now_mono: float, now_wall: float) -> float:
        """The live busy card's clock anchor (``busy_started_at``, the
        instant its elapsed counter counts from) as wall-clock seconds, or
        0.0 when no card is up or its clock never started. Monotonic stamps
        mean nothing to the next process; this is what the state file keeps
        so a card adopted after a restart (roadmap 8.101) goes on counting
        from the same instant (permission waits answered by then already
        discounted; one still open at the save counts as time so far)."""
        msg_id = self.busy_msg_id
        if not msg_id or msg_id < 0 or not self.busy_started_at:
            return 0.0
        return now_wall - (now_mono - self.busy_started_at)

    def card_orphaned(self, now: float, *, own_notify: int = 0,
                      own_lock: bool = False) -> bool:
        """True when this session shows a live busy card (Stop button up)
        that nothing owns any more (roadmap 8.55/8.57): no turn is running
        (not BUSY, not INTERACTIVE), no background job is open, nothing is
        animating it or opening a card, no event for the session is being
        handled, no finish is disposing of it, and it is not a restored
        card still inside its adoption window. Such a card was sent for a
        turn that has ended, and no path is left that would settle it.

        Pure apart from reading the lock and task state; the session
        monitor adds the grace period before acting on it. ``own_notify``
        is the caller's own ``notify()`` call, when it is running inside
        one, and ``own_lock`` says the caller holds ``animate_lock`` (the
        settle re-checks from within its event, under the lock)."""
        msg_id = self.busy_msg_id
        if not msg_id or msg_id < 0:
            return False
        if self.stack_top_kind() != "busy":
            return False
        if self.status in (Status.BUSY, Status.INTERACTIVE):
            return False
        if self.job_background_open():
            return False
        if (self.notify_in_flight > own_notify
                or self.finish_gate is not None):
            return False
        if (self.animate_lock.locked() and not own_lock) \
                or self.animation_running():
            return False
        return now >= self.card_adopt_until

    def animation_running(self) -> bool:
        """True while an animate task exists and has not finished."""
        task = self.animate_task
        return task is not None and not task.done()

    def was_delivered(self, digest: str) -> bool:
        """True if a body with this digest already went out to the chat
        during this session's lifetime (see ``delivered_digests``)."""
        return bool(digest) and digest in self.delivered_digests

    def remember_delivered(self, digest: str, *, pending: bool = False) -> None:
        """Record a body as delivered so ``was_delivered`` can refuse a
        later identical re-send. A repeat is not re-appended, so one body
        cannot push the others out of the ring by being sent twice.

        ``pending=True`` records a body that is ABOUT to be sent: it is
        refused in this process at once, but not written to the state
        file until ``confirm_delivered`` says the send landed."""
        if not digest:
            return
        if digest not in self.delivered_digests:
            if len(self.delivered_digests) == self.delivered_digests.maxlen:
                self.pending_digests.discard(self.delivered_digests[0])
            self.delivered_digests.append(digest)
        if pending:
            self.pending_digests.add(digest)
        else:
            self.pending_digests.discard(digest)

    def confirm_delivered(self, digests: Iterable[str],
                          selected_wall: float) -> None:
        """The send of these pending digests landed: make them persistable
        and stamp ``answer_delivered_wall`` (see the field)."""
        for digest in digests:
            self.pending_digests.discard(digest)
        if selected_wall > self.answer_delivered_wall:
            self.answer_delivered_wall = selected_wall

    def mark_prompt_closed(self, msg_id: object) -> None:
        """Record that the prompt shown on message *msg_id* is closed
        (see ``closed_prompt_msgs``). Anything but a positive int is
        ignored."""
        if type(msg_id) is not int or msg_id <= 0:
            return
        if msg_id in self.closed_prompt_msgs:
            self.closed_prompt_msgs.remove(msg_id)
        self.closed_prompt_msgs.append(msg_id)
        del self.closed_prompt_msgs[:-MAX_CLOSED_PROMPT_SURFACES]

    def reopen_prompt_surface(self, msg_id: object) -> None:
        """A prompt is shown on message *msg_id* again: its buttons
        answer that prompt, whatever an earlier one on it was."""
        if msg_id in self.closed_prompt_msgs:
            self.closed_prompt_msgs.remove(msg_id)

    def prompt_known_closed(self, msg_id: object) -> bool:
        """Whether the prompt on message *msg_id* is known to be closed."""
        return (type(msg_id) is int and msg_id > 0
                and msg_id in self.closed_prompt_msgs)

    def files_turn_opened(self, wall: float) -> bool:
        """Prompt evidence that a turn starts at *wall* (a prompt the
        daemon typed, a UserPromptSubmit): opens the turn files may come
        from, unless one is already open (a message absorbed by a running
        turn, a background job's continuation), whose start stays. The
        transcript is the one the daemon tracks for the session now (and
        the first one it learns, when it knew none). True when it opened."""
        if self.files_turn_open:
            if not self.files_transcript and self.transcript_path:
                self.files_transcript = self.transcript_path
            return False
        self.files_turn_open = True
        self.files_turn_wall = wall
        self.files_transcript = self.transcript_path or ""
        self.files_turn_opened_at = time.monotonic()
        return True

    def files_turn_resumed(self, wall: float) -> bool:
        """A turn of the same conversation began at *wall* with no prompt
        hook: a queued message the daemon typed, which Claude Code popped
        when the last turn ended (notify._take_next_prompt). Open the
        files turn again, still looking in the transcript noted for that
        conversation (never a path a datagram names now). True when it
        opened. (A start only the transcript shows does not open it: each
        call of such a turn is proven on its own, see
        outbox.hookless_turn_started.)"""
        if self.files_turn_open or not self.files_transcript or wall <= 0:
            return False
        self.files_turn_open = True
        self.files_turn_wall = wall
        self.files_turn_opened_at = time.monotonic()
        return True

    def files_turn_closed(self, ended_wall: float = 0.0) -> None:
        """No turn or job runs any more: no file can come from one.
        *ended_wall* is when that turn ended (its Stop), kept so a turn
        Claude Code starts with no hook after it can be proven
        (``files_turn_closed_wall``); zero forgets it (a new
        conversation, the session gone)."""
        self.files_turn_open = False
        self.files_turn_closed_wall = ended_wall if ended_wall > 0 else 0.0

    def files_turn_stopped(self, wall: float) -> None:
        """Claude's own turn ended at *wall* (a Stop). With the files turn
        closed already (a Stop on an idle session: a turn Claude Code ran
        with no hook and no tool call), that turn's end is the newest one:
        a later hookless start must come after it."""
        if not self.files_turn_open and self.files_turn_closed_wall > 0:
            self.files_turn_closed_wall = max(self.files_turn_closed_wall, wall)

    def files_call_used(self, tool_use_id: str) -> bool:
        """Record *tool_use_id* as copied. False when it already was (a
        replay, or a duplicate), or is not a usable id."""
        if not isinstance(tool_use_id, str) or not tool_use_id:
            return False
        if tool_use_id in self.files_sent_ids:
            return False
        self.files_sent_ids.append(tool_use_id)
        del self.files_sent_ids[:-MAX_FILES_SENT_IDS]
        return True

    def persisted_digests(self) -> list[str]:
        """The ring as written to the state file: confirmed digests only,
        oldest first."""
        return [d for d in self.delivered_digests
                if d not in self.pending_digests]

    def forget_chat_messages(self) -> None:
        """Forget every message id this session holds in its chat, because
        the chat is not where they live any more (roadmap 8.87: Telegram
        upgraded the group to a supergroup, and message ids are per chat).
        An edit, reply, reaction or delete aimed at one of them in the new
        chat would reach a different message, or nothing. Kept: the
        prompts themselves (a held message keeps its text and sender, and
        loses only its reply pointer); a card claim in flight (``-1``),
        whose send to the old chat fails on its own."""
        self.last_msg_id = None
        self.trigger_msg_id = None
        self.busy_card_trigger = None
        self.card_settled_msg_id = 0
        self.pending_card_deletes = []
        self.closed_prompt_msgs = []
        self.prompt_sent_msg = None
        busy = self.busy_msg_id
        if busy is not None and busy > 0:
            self.busy_msg_id = None
        self.pending_queue[:] = [
            (item[0], None, *item[2:]) if len(item) > 1 else item
            for item in self.pending_queue]

    def restore_delivered_digests(self, raw: object) -> None:
        """Refill the ring from a state file's value. Anything that is not
        a list of strings is ignored (an old or hand-edited file), and only
        the newest DELIVERED_DIGEST_RING entries survive."""
        if not isinstance(raw, list):
            return
        for digest in raw:  # the deque's maxlen keeps the newest
            if isinstance(digest, str) and digest:
                self.remember_delivered(digest)

    def model_switch_pending(self) -> bool:
        """Is a typed ``/model`` still unaccounted for (roadmap 8.35)?

        True until its window runs out, or until the statusline reports a
        model other than the one recorded when it was typed — a change the
        confirmation wait may never have seen (the operator answered Claude
        Code's dialog after the wait gave up, or the chat reply the wait
        would have edited was never sent)."""
        if self.model_switch_pending_until <= time.monotonic():
            return False
        current = self.model_name or ""
        baseline = self.model_switch_pending_from
        return not (baseline and current and current != baseline)

    def model_check_running(self) -> bool:
        """Is a typed ``/model`` maybe still checking its model, so that a
        message typed now would be lost? Until the statusline reports the
        switch (``model_switch_pending`` ends) or ``model_check_until``
        passes, whichever comes first."""
        return time.monotonic() < self.model_check_until and self.model_switch_pending()

    def model_hold_active(self) -> bool:
        """Must text wait before it is typed, because of a typed ``/model``?
        While its check may run, and while messages held for it still wait
        to go out (the window's end is noticed on the monitor's next tick):
        then it goes after them."""
        return self.model_check_running() or (self.model_hold and bool(self.pending_queue))

    # -- Permission mode (aipager/perm_modes.py) ---------------------------

    def reset_mode_tracking(self) -> None:
        """Forget what Claude Code reported and any pending switch: a
        launch, relaunch, resume or the end of the session starts over."""
        self.reported_mode = ""
        self.auto_unavailable = False
        self.mode_launch_unchecked = False
        self.mode_switch = None
        self.bypass_seen = False

    def note_reported_mode(self, raw: object, now: float) -> str:
        """Record the ``permission_mode`` a main-agent hook reported.

        ``"ignored"`` for a value that is not a mode Claude Code has;
        ``"settling"`` (nothing recorded) while a switch's presses are
        younger than ``MODE_SETTLE_SECONDS`` (the hook was very likely
        fired before Claude Code read them); else ``"recorded"``. Never
        touches ``perm_mode``: a hook never widens a launch flag."""
        mapped = _perm_modes.from_claude(raw)
        if mapped is None:
            return "ignored"
        if mapped == _perm_modes.FULL:
            # Even while settling: whenever it was fired, bypass is in
            # this process's cycle.
            self.bypass_seen = True
        pending = self.mode_switch
        if pending is not None and now - pending.sent_at < _perm_modes.MODE_SETTLE_SECONDS:
            return "settling"
        self.reported_mode = raw  # type: ignore[assignment]
        if self.mode_launch_unchecked and pending is None:
            if self.perm_mode == _perm_modes.AUTO and mapped == _perm_modes.ASK:
                self.auto_unavailable = True
            self.mode_launch_unchecked = False
        if mapped == _perm_modes.AUTO:
            self.auto_unavailable = False
        return "recorded"

    def current_mode(self) -> str:
        """The mode the session is in: what a hook last reported (``ask``,
        ``auto``, ``full`` or ``other``), else what aipager asked for."""
        mapped = _perm_modes.from_claude(self.reported_mode)
        return mapped if mapped is not None else self.perm_mode

    def shown_mode(self) -> tuple[str, str]:
        """``(id, raw)`` to show: id is ``ask``, ``auto``, ``full``,
        ``ask_no_auto`` (Ask because Auto is not available here) or
        ``other`` (raw names Claude Code's own mode). While a switch waits
        for its hook, the target."""
        if self.mode_switch is not None and not self.reported_mode:
            return self.mode_switch.target, ""
        mode = self.current_mode()
        if mode == _perm_modes.ASK and self.auto_unavailable:
            return "ask_no_auto", self.reported_mode
        return mode, self.reported_mode

    def mode_switch_restarts(self, target: str) -> bool:
        """Does switching to *target* need a restart? Into or out of Full
        access does (bypass is only ever set at launch), so does a process
        that reported bypass since its launch (presses could cycle it back
        into bypass), and so does any start Shift+Tab cannot cycle from."""
        if (target == _perm_modes.FULL or self.perm_mode == _perm_modes.FULL
                or self.bypass_seen):
            return True
        return _perm_modes.presses(self.mode_raw(), target,
                                   auto_in_cycle=not self.auto_unavailable) is None

    def mode_raw(self) -> str:
        """Claude Code's raw mode to count presses from: the last reported
        one, else the raw form of what aipager asked for."""
        if self.reported_mode:
            return self.reported_mode
        return _perm_modes.to_claude(self.perm_mode)

    def dialog_is_open(self) -> bool:
        """True while the terminal is showing a permission/question prompt.

        An inbound prompt must be queued rather than injected while this
        holds: keystrokes sent to a session displaying a modal dialog are
        read as input to that dialog, not as a new turn. The Mini App
        already withholds ``/compact`` for the same reason (see
        ``miniapp.sessions._STATUS_ACTION_KEYS``); this is the same rule
        for every inbound prompt path.
        """
        return self.status is Status.INTERACTIVE or self.prompt_wait_carry

    # ── roadmap 8.120: more than one open prompt ───────────────────────────

    def agent_tool_started(self, agent_id: str, tool_use_id: str,
                           tool_name: str, digest: str) -> None:
        """An agent's PreToolUse: its call is in flight until it ends."""
        if not (agent_id and tool_use_id):
            return
        calls = self.agent_tool_calls.setdefault(agent_id, {})
        calls.pop(tool_use_id, None)
        calls[tool_use_id] = (tool_name, digest)
        while len(calls) > AGENT_TOOL_CALLS_CAP:
            calls.pop(next(iter(calls)))

    def agent_tool_ended(self, agent_id: str, tool_use_id: str) -> None:
        """An agent's PostToolUse / PostToolUseFailure."""
        calls = self.agent_tool_calls.get(agent_id)
        if calls is None:
            return
        calls.pop(tool_use_id, None)
        if not calls:
            self.agent_tool_calls.pop(agent_id, None)

    def agent_tool_call_for(self, agent_id: str, tool_name: str,
                            digest: str) -> str:
        """The id of the one unfinished call of *agent_id* with this tool
        and this whole input, else ``""`` (none, no digest, or two parallel
        calls that cannot be told apart)."""
        if not (agent_id and digest):
            return ""
        calls = self.agent_tool_calls.get(agent_id) or {}
        found = [tuid for tuid, (name, dig) in calls.items()
                 if name == tool_name and dig == digest]
        return found[0] if len(found) == 1 else ""

    def _primary_records(self) -> list[tuple[str, dict, dict | None]]:
        """``[(where, perm, record)]`` of the primary prompt: the card's
        inline prompt (``"inline"``, no record), else the prompt sent as
        its own message during this wait (``"separate"``)."""
        perm = self.pending_permission
        if isinstance(perm, dict):
            return [("inline", perm, None)]
        sent = self.pending_prompt_msg
        cand = sent.get("perm") if isinstance(sent, dict) else None
        if (isinstance(cand, dict)
                and (cand.get("wait_started_at") or 0) >= self.interactive_entered_at):
            return [("separate", cand, sent)]
        return []

    def _open_records(self) -> list[tuple[str, dict, dict | None]]:
        if self.status is not Status.INTERACTIVE and not self.prompt_wait_carry:
            return []
        out = self._primary_records()
        carried = self.carried_inline_prompt
        if self.prompt_wait_carry and isinstance(carried, dict):
            # The card's prompt kept through Claude's Stop (8.120) is
            # still open: a close during the carry (its agent's tool
            # ended, the agent stopped) must see it, or it is sent again
            # for a dialog that is gone. No surface: the card shows the
            # answer, and its message comes once the carry ends.
            out.insert(0, ("carried", carried, None))
        for rec in self.extra_prompts:
            perm = rec.get("perm") if isinstance(rec, dict) else None
            if isinstance(perm, dict):
                out.append(("extra", perm, rec))
        return out

    def open_prompts(self) -> list[dict]:
        """The open prompts in arrival order (empty when none): the
        primary, then every prompt that opened while it was open. Each is
        the prompt's answer context (``tool_info``, ``hook_reply``, ...)."""
        return [perm for _where, perm, _rec in self._open_records()]

    def prompt_owners(self) -> set[str]:
        """The agent ids that own an open prompt (Claude's own excluded):
        an agent waiting on its dialog fires no hooks, so it is never
        dropped as silent."""
        owners = set()
        for perm in self.open_prompts():
            info = perm.get("tool_info")
            if isinstance(info, dict) and info.get("agent_id"):
                owners.add(str(info["agent_id"]))
        return owners

    def close_prompts(self, pred, *, mark: bool = True) -> tuple[int, bool]:
        """Close every open prompt *pred(perm)* is true for: its surface
        (the card for the inline one, else its own message) is marked
        closed, so its buttons type nothing, and its record goes. When the
        primary slot empties while others stay open, the oldest of them
        becomes the primary (its message, token and registration are
        unchanged). *mark* False leaves the surfaces unmarked (the
        INTERACTIVE watchdog: a prompt nobody answered is not known
        closed). Returns ``(closed_count, inline_closed)``."""
        stamp = self.mark_prompt_closed if mark else (lambda _msg_id: None)
        closed, inline_closed = 0, False
        for where, perm, rec in self._open_records():
            try:
                hit = bool(pred(perm))
            except Exception:  # noqa: BLE001 - a bad predicate closes nothing
                hit = False
            if not hit:
                continue
            closed += 1
            if where == "inline":
                stamp(self.busy_msg_id)
                self.pending_permission = None
                inline_closed = True
            elif where == "carried":
                self.carried_inline_prompt = None
            elif where == "separate":
                stamp(rec.get("msg_id"))
                self.pending_prompt_msg = None
            else:
                stamp(rec.get("msg_id"))
                self.extra_prompts = [r for r in self.extra_prompts if r is not rec]
        shown = [r for r in self.extra_prompts
                 if isinstance(r, dict) and not r.get("auto_deny_due")]
        if (self.pending_permission is None and not self._primary_records()
                and self.carried_inline_prompt is None and shown):
            # A refusal waiting to be typed has no message: never the
            # primary (the pinned bar's Answer would re-send nothing).
            promoted = shown[0]
            self.extra_prompts = [r for r in self.extra_prompts if r is not promoted]
            promoted["promoted"] = True
            self.pending_prompt_msg = promoted
        return closed, inline_closed

    def prompt_surface(self, perm) -> int | None:
        """The message an open prompt's buttons sit on: the busy card for
        the inline one, else its own message. None when not open."""
        for where, p, rec in self._open_records():
            if p is perm:
                if where == "carried":
                    return None
                return self.busy_msg_id if where == "inline" else rec.get("msg_id")
        return None

    def keys_verdict(self, perm, now: float) -> str:
        """Whether keys typed now reach open prompt *perm*'s dialog:
        ``"yes"``, ``"behind"`` or ``"unknown"`` (aipager.prompt_stack).
        Always ``"yes"`` with one prompt open."""
        def order_of(p) -> dict:
            info = p.get("tool_info") if isinstance(p, dict) else None
            order = info.get("order") if isinstance(info, dict) else None
            if isinstance(order, dict):
                return order
            # Recorded without one (restored, or its tool not known):
            # Claude's own, there since it was shown.
            return {"owner": "", "launch": _prompt_stack.OWN,
                    "arrived": (p.get("wait_started_at") or 0.0)
                    if isinstance(p, dict) else 0.0}
        others = [order_of(p) for p in self.open_prompts() if p is not perm]
        return _prompt_stack.keys_verdict(order_of(perm), others, now)

    def close_all_prompts(self) -> tuple[int, bool]:
        """Close every open prompt (/stop, kill, a turn ended at its
        dialog): see :meth:`close_prompts`."""
        result = self.close_prompts(lambda _perm: True)
        for rec in self.extra_prompts:
            self.mark_prompt_closed(rec.get("msg_id"))
        self.extra_prompts = []
        return result

    def unsaved_prompt_surfaces(self) -> list[int]:
        """The message ids of every open prompt's surface when more than
        one prompt is open (none of them is saved for a restart), else
        ``[]``: the card for an inline prompt, each sent message."""
        records = self._open_records()
        if len(records) < 2:
            return []
        ids = []
        for where, _perm, rec in records:
            if where == "carried":
                continue
            msg_id = self.busy_msg_id if where == "inline" else rec.get("msg_id")
            if type(msg_id) is int and msg_id > 0 and msg_id not in ids:
                ids.append(msg_id)
        return ids

    def _drop_extra_prompts(self) -> None:
        """The wait ended: no prompt that opened during it is open any
        more, and their buttons type nothing."""
        for rec in self.extra_prompts:
            if isinstance(rec, dict):
                self.mark_prompt_closed(rec.get("msg_id"))
        self.extra_prompts = []

    def dialog_release_pending(self) -> bool:
        """True when a dialog that held messages was answered and those
        messages have not all gone out yet. A message arriving meanwhile is
        held behind them, so the person's messages reach Claude in the order
        they were sent."""
        hold = self.dialog_hold
        return hold is not None and hold.get("closed") == "answered"

    def tap_is_for_this_turn(self, tapped_msg_id: int | None, *,
                             turn: int | None = None) -> bool:
        """True if a button tap belongs to the turn currently running.

        ``turn``: the `turn_key` a button was shown for, carried in its
        own callback data. For buttons on a message that is edited in
        place (/status, the ⋮ menu's End confirm), whose id says nothing
        about when they were shown, and which a mid-turn card move would
        make look stale: the tap is for this turn iff no new turn started
        since (a self-woken continuation of the same job is the same
        turn). Without it, the message-id ordering below applies.

        Ordering, not identity. Telegram message ids increase within a
        chat, and every card that can carry a destructive button for the
        live turn — the busy card, plus any permission or question card
        raised during it — is sent at or after the busy card. So a tap on
        a message older than the busy card came from an earlier turn.

        Fails OPEN when there is nothing to compare against: no busy card,
        the ``-1`` sentinel while one is being created, or a tap with no
        message id. A refusal that cannot be justified is worse than an
        action the operator asked for, and silence is what the project's
        long-form-button contract forbids outright.
        """
        if turn is not None:
            return turn == self.turn_key
        current = self.busy_msg_id
        if not current or current < 0 or not tapped_msg_id:
            return True
        return tapped_msg_id >= current

    def is_restarting(self) -> bool:
        """True while a deliberate kill-and-relaunch is still in flight."""
        return time.monotonic() < self.restarting_until

    def is_resuming(self) -> bool:
        """True while this session's resume is between its launch and the
        state restore that follows it.

        `_do_resume_core` leaves the session GONE, with its original
        `gone_at`, for the whole of `launch_session` — so a resume that
        happens to land on the tick the entry crosses
        `GONE_SESSION_MAX_AGE_DAYS` had it swept out from under itself:
        the later `transition()` re-created a blank entry and the resume's
        restored cwd, scope, permission mode and message routing were lost
        on the orphan, while the operator was told "Resumed" (roadmap 8.12
        follow-up). Distinct from `is_restarting()` on purpose: that one
        also suppresses the `session_end` alert, which a resumed session
        that dies immediately should still produce.
        """
        return time.monotonic() < self.resuming_until

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
            for info in self.bg_shells.values():
                if info.get("history_idx") is not None:
                    info["history_idx"] -= drop
                    if info["history_idx"] < 0:
                        info["history_idx"] = None
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
            self.stream_exact_anchor = {
                mid: max(0, anchor - drop)
                for mid, anchor in self.stream_exact_anchor.items()
            }
        return new_idx


def _derive_label(name: str) -> str:
    """The label a session gets when nothing better is known — its internal
    name with the ``claude-`` prefix and any scope disambiguator stripped."""
    core = name.removeprefix("claude-") if name.startswith("claude-") else name
    return strip_scope_suffix(core)


def _is_group_chat(chat_id) -> bool:
    """A group or supergroup: Telegram gives them negative chat ids (the
    rule ``bot.group_intake.is_group_chat`` applies too)."""
    return (isinstance(chat_id, int) and not isinstance(chat_id, bool)
            and chat_id < 0)


def _is_user_id(user_id) -> bool:
    return isinstance(user_id, int) and not isinstance(user_id, bool)


def _load_pinned_msg_ids(data: dict) -> dict[int, int]:
    """The per-chat pinned-bar ids from a state file (8.31).

    A file written before 8.31 has one ``pinned_msg_id``: the legacy
    single-chat dashboard's, which only ever lived in ``config.CHAT_ID``.
    It is migrated into that chat's slot. With no parseable ``CHAT_ID``
    there is no chat it could belong to, and it is dropped: the next
    refresh then sends (and pins) a fresh bar instead of editing a
    message in the wrong chat.
    """
    out: dict[int, int] = {}
    raw = data.get("pinned_msg_ids")
    if isinstance(raw, dict):
        for chat, msg in raw.items():
            try:
                chat_i, msg_i = int(chat), int(msg)
            except (TypeError, ValueError):
                continue
            if msg_i > 0:
                out[chat_i] = msg_i
    legacy = data.get("pinned_msg_id")
    if isinstance(legacy, int) and legacy > 0:
        from aipager import config
        try:
            chat_i = int(config.CHAT_ID)
        except (TypeError, ValueError):
            chat_i = None
        if chat_i is not None:
            out.setdefault(chat_i, legacy)
    return out


def _load_keyboard_labels(raw: object) -> dict[int, list[str]]:
    """The persisted per-chat keyboard labels
    (``SessionRegistry.keyboard_labels``). A file from before they were
    kept has none; a chat entry that is not an int-like key with a list of
    strings is dropped (that chat then counts as never recorded)."""
    out: dict[int, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for chat, labels in raw.items():
        try:
            chat_i = int(chat)
        except (TypeError, ValueError):
            continue
        if (not isinstance(labels, list)
                or not all(isinstance(lbl, str) and lbl for lbl in labels)):
            continue
        out[chat_i] = sorted(set(labels))
    return out


#: Whose card each group message is (``bot.card_owner``): the kinds of
#: card a record may name, and the newest records kept (also on disk).
CARD_OWNER_KINDS = frozenset({
    "end", "restart", "delete", "mode", "ask",
    "stop", "rename", "diff", "resume",
})
MAX_CARD_OWNERS = 4096


def _load_button_index(
    raw: object, known: set[str],
) -> tuple[dict[int, list[str | None]], dict[int, int], bool]:
    """The persisted button tables (``SessionRegistry.button_index``):
    ``(tables, bases, lost)``. A name the registry no longer knows comes
    back as a None tombstone in its slot. A missing key (a file from
    before 8.103) or any malformed chat entry sets *lost* and leaves that
    chat out, so its next table starts where no old button points."""
    if not isinstance(raw, dict):
        return {}, {}, True
    tables: dict[int, list[str | None]] = {}
    bases: dict[int, int] = {}
    lost = False
    for chat, entry in raw.items():
        try:
            chat_id = int(chat)
        except (TypeError, ValueError):
            lost = True
            continue
        names = entry.get("names") if isinstance(entry, dict) else None
        base = entry.get("base") if isinstance(entry, dict) else None
        if (not isinstance(names, list) or type(base) is not int or base < 0
                or not all(n is None or isinstance(n, str) for n in names)):
            lost = True
            continue
        tables[chat_id] = [n if n in known else None for n in names]
        bases[chat_id] = base
    return tables, bases, lost


def _load_card_owners(raw: object) -> dict[tuple[int, int], dict]:
    """The persisted card owners (roadmap 8.94h), oldest first, at most
    :data:`MAX_CARD_OWNERS` of the newest. State saved before they were
    persisted has none. A malformed record is dropped, and so is one in a
    private chat (nothing is ever recorded there: a DM's cards answer
    everyone, as they always have)."""
    out: dict[tuple[int, int], dict] = {}
    if not isinstance(raw, list):
        return out
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        chat, msg, user = rec.get("chat_id"), rec.get("msg_id"), rec.get("user_id")
        label, kind = rec.get("label"), rec.get("kind")
        command, picker = rec.get("command"), rec.get("picker")
        if not (_is_group_chat(chat) and type(msg) is int and msg > 0
                and _is_user_id(user) and isinstance(label, str)
                and kind in CARD_OWNER_KINDS and isinstance(command, str)
                and isinstance(picker, bool)):
            continue
        out.pop((chat, msg), None)
        out[(chat, msg)] = {"user_id": user, "label": label, "kind": kind,
                            "command": command, "picker": picker}
    for key in list(out)[:-MAX_CARD_OWNERS]:
        del out[key]
    return out


def _clean_line_deletes(raw: object) -> list[list[int]]:
    """The owed "send now" line deletes, as ``[chat_id, message_id]``
    pairs of non-zero ints with a positive message id. Anything else (a
    missing key, a wrong type, a malformed pair) is dropped: an entry that
    cannot name a message cannot be deleted."""
    out: list[list[int]] = []
    if not isinstance(raw, list):
        return out
    for pair in raw:
        if (isinstance(pair, (list, tuple)) and len(pair) == 2
                and all(type(v) is int for v in pair)
                and pair[0] != 0 and pair[1] > 0):
            out.append([pair[0], pair[1]])
    return out


def _restore_parked(sess: TrackedSession, sd: dict) -> None:
    """The saved Claude Code background-job park of *sess* (see
    ``TrackedSession.parked_job``). A job id aipager could not name a file
    after restores as no park, with its other fields; a missing key (a
    file an older aipager wrote) restores as none."""
    from aipager.dtach import bg_jobs  # stdlib only, as the hook needs
    job = sd.get("parked_job")
    if (isinstance(job, str) and bg_jobs._JOB_ID.match(job)
            and job not in bg_jobs._RESERVED_JOBS):
        sess.parked_job = job
        job_sid = sd.get("parked_job_session_id")
        sess.parked_job_session_id = job_sid if isinstance(job_sid, str) else ""
        try:
            since = float(sd.get("parked_since") or 0.0)
        except (TypeError, ValueError):
            since = 0.0
        sess.parked_since = since if 0.0 < since <= time.time() else 0.0
        source = sd.get("parked_source")
        sess.parked_source = source if source in ("registry", "transcript") else ""
        from_sid = sd.get("parked_from_session_id")
        sess.parked_from_session_id = (
            from_sid if isinstance(from_sid, str) and len(from_sid) <= 64 else "")
    notified = sd.get("park_notified_job")
    if isinstance(notified, str) and bg_jobs._JOB_ID.match(notified):
        sess.park_notified_job = notified
    # A bring-back whose held messages had not gone out yet: the monitor's
    # backstop releases them once (its clock starts again at zero).
    sess.bg_release_pending = sd.get("bg_release_pending") is True


def _drop_bg_files(name: str, sess: TrackedSession | None) -> None:
    """Best effort: remove the link file of the job *sess* is parked on
    and *name*'s entries in the pid map (aipager/dtach/bg_jobs.py), so a
    worker of that job no longer counts for a session that ended. The
    parked fields stay: ``session_end`` still reads them. Never raises."""
    try:
        from aipager.dtach import bg_jobs
        if sess is not None and sess.parked_job:
            link = bg_jobs.link_path(sess.parked_job)
            data = bg_jobs.read_own_json(link)
            if data is None or data.get("session") in (None, name):
                bg_jobs.remove_own(link)
        path = bg_jobs.pidmap_path()
        pidmap = bg_jobs.read_own_json(path)
        pids = pidmap.get("pids") if isinstance(pidmap, dict) else None
        if isinstance(pids, dict):
            kept = {pid: rec for pid, rec in pids.items()
                    if not (isinstance(rec, dict) and rec.get("session") == name)}
            if len(kept) != len(pids):
                bg_jobs.write_own_json(path, {"pids": kept})
    except Exception:  # noqa: BLE001 - cleanup never blocks the caller
        log.debug("background-job files of %s not removed", name, exc_info=True)


class SessionRegistry:
    """Single source of truth for all tracked Claude sessions."""

    def __init__(self):
        self._sessions: dict[str, TrackedSession] = {}  # keyed by session name
        # Roadmap 8.120: name -> how many open prompts the save last logged
        # as "none saved for a restart", so the line comes once per count.
        self._unsaved_prompts_told: dict[str, int] = {}
        self._msg_map: dict[tuple[int, int], str] = {}  # (chat_id, msg_id) → session name
        # name → the label it carried when a /kill removed it. See
        # `remove(remember_label=True)`.
        self._remembered_labels: dict[str, str] = {}
        # The message target, PER CHAT (F11, 2026-09-30): where a plain
        # message, voice note or file that is not a reply goes, and what a
        # bare /mode, /diff, /clearqueue or /now acts on. chat_id → (session
        # name, order), keyed by the session's OWN chat (`scope_chat_id`,
        # 0 for a session not stamped with one): a session belongs to one
        # chat, so "x1 became active" means "x1 is now the target in x1's
        # chat", and every setter of `last_active_session` records it with
        # no chat of its own to pass. Read with `target_for(chat_id)`.
        # It used to be one value for the whole install, so a message in
        # one chat could be typed into another chat's session.
        self._targets: dict[int, tuple[str, int]] = {}
        # A group's targets, one per PERSON (roadmap 8.90): (chat_id,
        # user_id) -> (session name, order). In a team the chat-level
        # target above follows whichever session answered last, so one
        # member's message or keyboard Clear went to another member's
        # session. Moved only by that person's own actions
        # (`set_target`), never by session output. Read through
        # `target_for(chat, user)`; a DM never uses it.
        self._user_targets: dict[tuple[int, int], tuple[str, int]] = {}
        self._target_seq: int = 0
        self._last_active_session: str = ""
        # The pinned "needs you" bar's message in each chat that has one
        # (8.31): chat_id → message_id. Persisted, so a restart edits the
        # same message instead of pinning a new one. Replaces the single
        # ``pinned_msg_id`` of the legacy one-chat dashboard, which
        # ``load()`` migrates into this map.
        self.pinned_msg_ids: dict[int, int] = {}
        # The session labels the latest shared main reply keyboard in each
        # chat carried (chat_id → sorted labels): what that chat's keyboard
        # shows now. Written only by `_send_keyboard` once such a keyboard
        # went out (in single-scope mode too, where nothing reads it), and
        # read by `_update_bot_commands` in multi-scope mode,
        # which re-sends a chat's keyboard when its sessions no longer
        # match. Persisted, so a restart sends nothing when nothing changed.
        self.keyboard_labels: dict[int, list[str]] = {}
        # The live (non-GONE) session names the latest `/` menu and
        # keyboard refresh (`_update_bot_commands`) rendered: the session
        # monitor refreshes when the live set differs from it, so a
        # command that refreshed by itself is not refreshed twice. None
        # until the first refresh. Transient.
        self.refreshed_live: set[str] | None = None
        self._dirty: bool = False
        # Claude Code session id -> monotonic time aipager's own /kill
        # ended that process (bot.session_ops._kill_session_core). Its
        # SessionEnd is not announced; a new process under the same name
        # has a new id. Shared here because both the bot (kill, notice)
        # and the hook receiver (SessionStart of a resumed id) touch it.
        # Transient: never persisted.
        self.killed_sessions: dict[str, float] = {}
        # "Send now" reply lines owed a delete, as [chat_id, message_id]:
        # every line sent and not yet deleted (bot/send_now.py). Persisted,
        # and on the registry rather than a session, so a restart, or a
        # /kill that removes the session entry, never leaves a live button
        # behind: startup deletes whatever is still here.
        self.queued_line_deletes: list[list[int]] = []
        # Whose card each group message is (bot/card_owner.py, roadmap
        # 8.91c): (chat_id, message_id) -> record, oldest first, at most
        # MAX_CARD_OWNERS. Persisted (roadmap 8.94h), so a restart does
        # not open a member's confirm card to everyone in the group.
        self.card_owners: dict[tuple[int, int], dict] = {}
        # The per-chat table behind the short session buttons
        # (`_:sx:<idx>:<verb>`, `_:spref:<idx>`, bot/session_parity.py):
        # chat_id -> session names by position, and the number its first
        # position stands for. Persisted (roadmap 8.103): an old message's
        # button must name the same session after a restart, and a table
        # rebuilt in render order would hand its index to another one. A
        # removed session's slot becomes a None tombstone (at `remove`, and
        # at load for a name no longer known), never dropped: positions
        # must not shift. Grows by one slot per distinct session a chat
        # has shown, for the life of the install.
        self.button_index: dict[int, list[str | None]] = {}
        self.button_base: dict[int, int] = {}
        # True when a state file existed but its button tables could not
        # be read (written before 8.103, unreadable, corrupt): buttons
        # from before this start are unknown, so a table created now
        # starts at a random base no old button can name (fail closed).
        self.button_tables_lost = False

    def get(self, name: str) -> TrackedSession | None:
        return self._sessions.get(name)

    def _blank_button_slots(self, name: str) -> None:
        """A dropped session's button slots name nothing from now on
        (roadmap 8.103): a later session reusing the internal name gets a
        slot of its own, so an old message's button cannot act on it.
        Every path that drops a session from the registry calls this."""
        for table in self.button_index.values():
            for i, slot in enumerate(table):
                if slot == name:
                    table[i] = None

    def _prune_remembered_labels(self) -> None:
        """Drop remembered labels past their TTL."""
        cutoff = time.monotonic() - REMEMBERED_LABEL_TTL_SECONDS
        for key in [k for k, (_, at) in self._remembered_labels.items()
                    if at < cutoff]:
            del self._remembered_labels[key]

    def get_or_create(self, name: str) -> TrackedSession:
        if name not in self._sessions:
            # Strip the scope disambiguator too, not just the prefix:
            # a discovered session would otherwise wear its INTERNAL
            # name everywhere the label is shown (grid, gone list,
            # /status, keyboard) — "Jkhk__d256113222" for a session
            # the operator named "Jkhk".
            # A label remembered from a kill wins over re-derivation: the
            # user picked it, the derivation is only a fallback for a
            # session we have genuinely never seen.
            self._prune_remembered_labels()
            remembered = self._remembered_labels.pop(name, None)
            label = (remembered[0] if remembered else None) or _derive_label(name)
            sess = TrackedSession(name=name, label=label)
            # Every session belongs to one chat from the moment the daemon
            # learns of it (roadmap 8.82/8.72): a terminal `claude-x` found
            # by the socket scan or a hook would otherwise resolve to a
            # fallback chat, and match every chat's lookups.
            chat = chat_for_new_session(name)
            if chat is not None:
                sess.scope_chat_id, sess.scope_kind = chat
            self._sessions[name] = sess
            log.info("Tracking new session: %s [%s]", name, label)
            self._evict_gone_overflow()
        else:
            # A known session still unstamped in scope mode (a missed
            # stamp, roadmap 8.72) is stamped the moment anything (the
            # monitor's 2 s socket scan, a hook, an adoption) touches it.
            sess = self._sessions[name]
            if not sess.scope_chat_id and home_chat() is not None:
                warn_unstamped(sess)
                self.stamp_unstamped(name)
        return self._sessions[name]

    def stamp_unstamped(self, only: str | None = None) -> list[str]:
        """Scope mode: stamp every session with no chat stamped (or just
        *only*) by the stamping rule, its name's suffix, else the home
        chat (:func:`chat_for_new_session`, the rule ``get_or_create`` and
        ``load`` apply). Returns the names stamped. Personal/legacy mode:
        nothing (there, unstamped means the one chat)."""
        if home_chat() is None:
            return []
        stamped: list[str] = []
        names = [only] if only is not None else list(self._sessions)
        for name in names:
            sess = self._sessions.get(name)
            if sess is None or sess.scope_chat_id:
                continue
            chat = chat_for_new_session(name)
            if chat is None:
                continue
            sess.scope_chat_id, sess.scope_kind = chat
            stamped.append(name)
            log.info("[%s] stamped with chat %s", sess.label, chat[0])
        if stamped:
            self._dirty = True
        return stamped

    def expire_gone(self, now: float | None = None) -> list[str]:
        """Drop every GONE session whose ``gone_at`` is older than
        ``GONE_SESSION_MAX_AGE_DAYS`` (roadmap 8.12). Returns the names
        dropped. Called at the end of ``load()`` and once per monitor
        scan; ``MAX_GONE_HISTORY``'s count cap is a separate guard.

        An entry with no ``gone_at`` is never aged — there is nothing to
        measure, and ``load()`` only marks an entry GONE when it has (or
        could backfill) a stamp. ``remove()`` does the dropping so the
        session's ``msg_map`` keys go with it, same as ``/kill``.
        """
        if GONE_SESSION_MAX_AGE_DAYS <= 0:
            return []
        now = time.time() if now is None else now
        cutoff = now - GONE_SESSION_MAX_AGE_DAYS * 86400.0
        dropped: list[str] = []
        for name, sess in list(self._sessions.items()):
            if sess.status != Status.GONE or sess.gone_at is None:
                continue
            if sess.is_resuming():
                # Its resume is mid-launch; dropping the entry now would
                # strand that resume on an orphan. Silent: if the resume
                # fails the session stays GONE and old, and a later tick
                # sweeps it normally.
                continue
            if sess.gone_at < cutoff:
                log.info("Dropping gone session %s (gone %dd ago)",
                         name, int((now - sess.gone_at) // 86400))
                self.remove(name)
                dropped.append(name)
        if dropped:
            # (`remove` forgets a dropped session as a target, too.)
            self._dirty = True
        return dropped

    @property
    def last_active_session(self) -> str:
        """The session made a target most recently, in ANY chat. Kept for
        callers with no chat (a legacy unscoped update) and saved state;
        routing reads :meth:`target_for`."""
        return self._last_active_session

    @last_active_session.setter
    def last_active_session(self, name: str) -> None:
        self._last_active_session = name or ""
        if not name:
            # "No target" is what the one install-wide value meant: none
            # in any chat, and for no one.
            self._targets.clear()
            self._user_targets.clear()
            return
        self._target_seq += 1
        # A session is the target of one chat: drop it from any other
        # (it was stamped into a chat since, say).
        for chat, (target, _order) in list(self._targets.items()):
            if target == name:
                del self._targets[chat]
        self._targets[self._target_chat(self._sessions.get(name))] = (
            name, self._target_seq)

    @staticmethod
    def _target_chat(sess: TrackedSession | None) -> int:
        """The slot *sess* is recorded as a target in: its own chat; 0 for
        a session with no chat stamped. A target is only ever returned for
        a chat it belongs to (:func:`session_in_chat`, the rule
        `all_sessions` and `find_by_label` apply too): the 0 slot is the
        one chat's in personal/legacy mode, and no chat's in scope mode
        (roadmap 8.72) until the session is stamped."""
        return sess.scope_chat_id if sess is not None and sess.scope_chat_id else 0

    def set_target(
        self, name: str, chat_id: int | None = None, user_id: int | None = None,
    ) -> None:
        """A person made *name* their target by their own action (a send,
        a switch, a reply, a new session). The chat-level target moves
        exactly as the ``last_active_session`` setter moves it; in a group
        the person's own target is recorded too (roadmap 8.90)."""
        self.last_active_session = name
        if not name or not _is_group_chat(chat_id) or not _is_user_id(user_id):
            return
        sess = self._sessions.get(name)
        if sess is None or not session_in_chat(sess, chat_id):
            return
        self._user_targets[(chat_id, user_id)] = (name, self._target_seq)
        self._dirty = True

    def target_for(
        self, chat_id: int | None, user_id: int | None = None,
    ) -> TrackedSession | None:
        """Where an unaddressed message in *chat_id* goes: the newest
        target made in that chat (or, in personal/legacy mode, by a
        session with no chat stamped) that is still one of the chat's
        sessions (:func:`session_in_chat`) (a newer one since
        stamped into another chat is skipped, not a dead end). Never a
        session that belongs to another chat. ``None`` chat (a legacy
        unscoped update) falls back to the install-wide latest.

        In a group with *user_id* (roadmap 8.90): that person's own
        target while it is a live session of the chat; ``None`` when it
        has ended (roadmap 8.91g: the only live session left may be
        someone else's, see :meth:`ended_target_for`); for a person who
        has no target, the chat's only live session; else ``None`` (ask
        which). Session output never moves it, so it is never another
        member's latest conversation."""
        if user_id is not None and _is_group_chat(chat_id) and _is_user_id(user_id):
            return self._person_target(chat_id, user_id)
        if chat_id is None:
            name = self._last_active_session
            return self._sessions.get(name) if name else None
        entries = sorted(
            (e for e in (self._targets.get(chat_id), self._targets.get(0)) if e),
            key=lambda e: e[1], reverse=True)
        for name, _order in entries:
            sess = self._sessions.get(name)
            if sess is not None and session_in_chat(sess, chat_id):
                return sess
        return None

    def _person_target(self, chat_id: int, user_id: int) -> TrackedSession | None:
        entry = self._user_targets.get((chat_id, user_id))
        if entry is not None:
            sess = self._sessions.get(entry[0])
            if (sess is not None and sess.status != Status.GONE
                    and session_in_chat(sess, chat_id)):
                return sess
        if self.ended_target_for(chat_id, user_id) is not None:
            return None
        live = [s for s in self.all_sessions(chat_id).values()
                if s.label and s.status != Status.GONE]
        return live[0] if len(live) == 1 else None

    def own_target(
        self, chat_id: int | None, user_id: int | None,
    ) -> TrackedSession | None:
        """In a group: the session the person made their target by their
        own action, live or ended, while it is still one of the chat's
        labelled sessions (roadmap 8.93). Never "the chat's only live
        session", which may be another member's. Always ``None`` in a
        private chat."""
        if not (_is_group_chat(chat_id) and _is_user_id(user_id)):
            return None
        entry = self._user_targets.get((chat_id, user_id))
        if entry is None:
            return None
        sess = self._sessions.get(entry[0])
        if sess is not None and sess.label and session_in_chat(sess, chat_id):
            return sess
        return None

    def ended_target_for(
        self, chat_id: int | None, user_id: int | None,
    ) -> TrackedSession | None:
        """In a group: the person's own target when it is one of the
        chat's sessions that has ended (roadmap 8.91g), else ``None``.
        Their message must then not fall to "the only live session",
        which may be another member's: they are asked which one, with a
        Resume button for theirs. Always ``None`` in a private chat."""
        if not (_is_group_chat(chat_id) and _is_user_id(user_id)):
            return None
        entry = self._user_targets.get((chat_id, user_id))
        if entry is None:
            return None
        sess = self._sessions.get(entry[0])
        if (sess is not None and sess.status == Status.GONE and sess.label
                and not sess.is_resuming()
                and session_in_chat(sess, chat_id)):
            return sess
        return None

    def _forget_target(self, name: str) -> None:
        for chat, (target, _order) in list(self._targets.items()):
            if target == name:
                del self._targets[chat]
        for key, (target, _order) in list(self._user_targets.items()):
            if target == name:
                del self._user_targets[key]
        if self._last_active_session == name:
            # The newest target left, for an update with no chat.
            self._last_active_session = max(
                self._targets.values(), key=lambda e: e[1], default=("", 0))[0]

    def _evict_gone_overflow(self) -> None:
        """Keep at most ``MAX_GONE_HISTORY`` GONE entries in the registry.

        Sorted by ``gone_at`` ascending so the oldest die first. A GONE
        entry without a ``gone_at`` stamp (legacy / pre-feature) sorts as
        oldest and is evicted preferentially.

        A session whose resume is in flight is never the one evicted, even
        when it is the oldest — which it usually is, since a resume
        targets an old entry. Popping it would strand the resume on an
        orphan (roadmap 8.15, the race ``expire_gone`` already guards).
        """
        gone = [
            (s.gone_at if s.gone_at is not None else 0.0, n, s)
            for n, s in self._sessions.items()
            if s.status == Status.GONE
        ]
        if len(gone) <= MAX_GONE_HISTORY:
            return
        over = len(gone) - MAX_GONE_HISTORY
        # A resuming session still counts toward the cap (it is GONE right
        # now) but is never the one evicted, so the cap is met by taking
        # the next-oldest instead. If every candidate is resuming the
        # registry simply stays over the cap for a few seconds.
        gone.sort(key=lambda row: (row[0], row[1]))
        victims = [n for _, n, sess in gone if not sess.is_resuming()][:over]
        for name in victims:
            log.info("Evicting GONE session from history (LRU): %s", name)
            self._sessions.pop(name, None)
            self._blank_button_slots(name)
            self._dirty = True

    def transition(self, name: str, new_status: Status,
                   summary: str = "", *,
                   preserve_job_state: bool = False) -> TrackedSession | None:
        """Attempt a state transition. Returns session only if state actually changed.

        Idempotency: same-state calls return None (no duplicate notification).
        Debounce: IDLE transitions within IDLE_DEBOUNCE seconds of the last
        IDLE notification are suppressed (prevents spam from quick responses).

        ``preserve_job_state`` (design.md "model Claude Code background-agent
        jobs"): when True and the entry is a re-entry into BUSY on behalf of
        the SAME job (a background agent's own PreToolUse, or a
        ``<task-notification>`` continuation prompt), the ``busy_started_wall``
        stamp below is skipped so the job's original turn-start anchor
        survives every re-entry in between. Both call sites that pass it are
        re-entries INTO BUSY specifically — the parameter has no effect on
        any other transition.
        """
        sess = self.get_or_create(name)
        if sess.status == new_status:
            return None  # no-op

        # The preview is a GONE-time snapshot for /resume. Leaving GONE —
        # by the monitor seeing the socket again, a resume, a /new
        # replace, a hook — makes it a leftover that froze at the old
        # death and leaked into every reader (roadmap 8.34). Cleared here,
        # the one gate every path out of GONE passes, before the debounce
        # below can return early.
        if sess.status == Status.GONE:
            sess.last_assistant_preview = ""
        # Ending, or coming back (a resume, a relaunch), is a new turn for
        # every button that carries one (End, Restart, the mode switch,
        # Stop): one shown before must not act on the session after.
        if Status.GONE in (sess.status, new_status):
            sess.turn_key = next(_TURN_KEYS)

        if new_status == Status.GONE or (
                new_status == Status.IDLE and not sess.job_background_open()):
            # No turn runs: nobody's message waits for one (roadmap 8.77).
            # An interim Stop (a background agent of the job still runs)
            # keeps it: the job is the turn, and its agent and its later
            # continuation run under the job's rules.
            sess.turn_sender_id = None
            # And no file Claude sends can come from one (outbox.py). An
            # idle session keeps when it ended: a call of a turn Claude
            # Code starts with no hook after it can still count
            # (notify._copy_in_hookless_turn).
            sess.files_turn_closed(
                time.time() if new_status == Status.IDLE else 0.0)
        # Messages held for a dialog: how it closed, for the bot's release
        # (``dialog_hold``). Before the debounce below, which can return
        # early; a new dialog takes the mark back off.
        if sess.dialog_hold is not None:
            if new_status == Status.INTERACTIVE:
                sess.dialog_hold.pop("closed", None)
            elif sess.status == Status.INTERACTIVE:
                # The last prompt carried over a Stop answered (8.120):
                # Claude sits at its input, so what was held goes now.
                sess.dialog_hold["closed"] = (
                    "answered" if (new_status == Status.BUSY
                                   or (new_status == Status.IDLE
                                       and sess.resumed_after_stop))
                    else "ended")
        sess.resumed_after_stop = False
        # Debounce: suppress rapid re-IDLE (e.g. user sends quick command,
        # Claude responds in <1s, triggers another idle notification)
        if new_status == Status.IDLE:
            # A dequeue from here on is the next turn's pop, not a Send now
            # take (roadmap 8.64).
            sess.send_now_pressed_at = 0.0
            now = time.monotonic()
            if sess.last_idle_at and (now - sess.last_idle_at) < IDLE_DEBOUNCE:
                log.debug("[%s] IDLE debounced (%.1fs since last)", sess.label,
                          now - sess.last_idle_at)
                sess.status = new_status  # update state silently
                if summary:
                    sess.summary = summary
                # Saved all the same: `aipager status` reads the status
                # from the file (roadmap 8.118).
                self._dirty = True
                return None  # don't notify
            sess.last_idle_at = now

        # Roadmap 8.120: the prompts that opened during a wait belong to
        # it. A new wait starts with none, and a wait that ends closes the
        # ones still recorded (their buttons type nothing), except across
        # the Stop that carries an agent's prompt over (prompt_wait_carry).
        if (new_status == Status.INTERACTIVE
                or (sess.status == Status.INTERACTIVE
                    and not sess.prompt_wait_carry)):
            sess._drop_extra_prompts()
        # A new wait on a human gets its own reminder (roadmap 8.4).
        if new_status == Status.INTERACTIVE:
            sess.waiting_reminder_sent = False
            # The wait's start: only a prompt shown from here on is the
            # open one the state file may keep (roadmap 8.102).
            sess.interactive_entered_at = time.monotonic()
        # Reset idle timer when entering BUSY so next IDLE always notifies
        if new_status == Status.BUSY:
            sess.last_idle_at = 0.0
            sess.stale_warned = False
            # Same reason as stale_warned above: without this, a stand-down
            # logged for one turn's tool call could survive (PostToolUse and
            # the real Stop hook landing between two monitor ticks, so
            # work_in_flight_reason never observably clears mid-turn) into a
            # LATER, unrelated BUSY turn and silently swallow that turn's
            # own first idle-recovery stand-down log line.
            sess.recovery_stand_down_logged = False
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
            # A background-job re-entry (``preserve_job_state=True``) is the
            # same rule for a different reason: the turn this stamp anchors
            # started at the job's ORIGINAL prompt, not at this background
            # agent's own tool call or the self-triggered continuation
            # prompt that woke the session back up.
            if sess.status != Status.INTERACTIVE:
                # Stamped on EVERY turn entry, preserved re-entries
                # included — see the field's comment for its one consumer.
                sess.turn_entered_wall = time.time()
            if sess.status != Status.INTERACTIVE and not preserve_job_state:
                sess.busy_started_wall = time.time()
                # A genuinely new turn also supersedes any previous job's
                # endgame state and its delivered-summary hash (review
                # rev-iter1-002): resetting HERE — the one gate every
                # BUSY entry passes through — rather than only in
                # _send_busy_and_animate covers turns that begin with a
                # PreToolUse after a lost UserPromptSubmit datagram too.
                # Continuation/background re-entries pass
                # preserve_job_state=True and never reach this branch.
                sess.last_idle_summary_hash = ""
                sess.job_interim_seen = False
                sess.job_continuation_active = False
                sess.job_grace_until = 0.0
                sess.job_reclaim_pending = True
                # A new turn (roadmap 8.57): card requests made for the
                # previous one are stale from here on.
                sess.turn_seq += 1
                sess.turn_key = next(_TURN_KEYS)
                # No step of the parent's is running at a new turn's start
                # (the Send now line's "behind a long step" clock). A
                # message queued mid-turn is a BUSY->BUSY no-op and never
                # reaches here, so it cannot erase the running step.
                sess.parent_tool_started_at = None

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
        if new_status == Status.GONE:
            if sess.gone_at is None:
                sess.gone_at = time.time()
            # Roadmap 8.41: a gone session runs no agents and is owed no
            # ✅ edit into its chat.
            sess.clear_bg_agents()
            # What its Claude Code reported, and a switch still waiting for
            # a hook, end with it.
            sess.reset_mode_tracking()
            # Best-effort cleanup of this session's /tmp policy + reply-
            # context files (design.md Part 5). Both GONE-inducing paths
            # (socket-vanish via session_monitor, Claude's SessionEnd hook
            # via hook_receiver) converge here, and the idempotency guard
            # at the top of this method (same-state calls return early)
            # makes this fire exactly once per real transition. A failed
            # removal must never block the transition — never raise.
            try:
                from aipager.policy_snapshot import clear_session_files
                # Its scheduled prompts stay recorded: a resume brings them
                # back ("peer message authority").
                clear_session_files(sess.name, keep_triggers=True)
            except Exception:
                pass
            # Its background-job link and pid-map entry: no worker counts
            # for an ended session (its own claude is gone).
            _drop_bg_files(sess.name, sess)
            sess.claude_pid = 0
            sess.claude_pid_start = 0

        log.info("[%s] %s → %s", sess.label, old.name, new_status.name)
        self._dirty = True
        return sess

    def resume_wait(self, name: str, *,
                    after_stop: bool = True) -> TrackedSession | None:
        """Back to INTERACTIVE after a Stop that left an agent's prompt
        open (roadmap 8.120), without a new wait's stamps: the surviving
        prompts were shown during this wait (``interactive_entered_at``
        is kept) and a message held for them stays held. None when the
        session is not known or is gone. *after_stop*: no turn of Claude's
        runs (``resumed_after_stop``); False when a new turn just started
        (:meth:`begin_turn_while_waiting`)."""
        sess = self._sessions.get(name)
        if sess is None or sess.status in (Status.GONE, Status.INTERACTIVE):
            return None
        old = sess.status
        sess.status = Status.INTERACTIVE
        sess.resumed_after_stop = after_stop
        if sess.dialog_hold is not None:
            sess.dialog_hold.pop("closed", None)
        log.info("[%s] %s → %s (still waiting on a prompt)", sess.label,
                 old.name, Status.INTERACTIVE.name)
        self._dirty = True
        return sess

    def begin_turn_while_waiting(self, name: str) -> TrackedSession | None:
        """A prompt typed at the terminal while the session waits on
        prompts carried over a Stop (roadmap 8.120): Claude's turn was over,
        so this is a new turn, entered from IDLE (its turn-start stamps, a
        card of its own), while the agents' prompts stay recorded. The
        caller holds ``prompt_wait_carry`` until it has sent the turn's card
        and then calls :meth:`resume_wait` with ``after_stop=False``. None
        when the session is not waiting after a Stop."""
        sess = self._sessions.get(name)
        if (sess is None or sess.status is not Status.INTERACTIVE
                or not sess.resumed_after_stop):
            return None
        # Silently, as the IDLE debounce does: the Stop already ended the
        # turn, and the wait is not over (no hold release, no extras
        # dropped).
        sess.status = Status.IDLE
        sess.resumed_after_stop = False
        return self.transition(name, Status.BUSY)

    def track_message(self, msg_id: int, session_name: str, chat_id: int) -> None:
        """Associate a Telegram message_id with a session (for reply lookups).

        Keyed by ``(chat_id, msg_id)`` (design.md Part 0) so a message id
        that collides across two different chats can never resolve into
        the wrong chat's session — see :meth:`get_session_by_msg`.
        """
        self._msg_map[(chat_id, msg_id)] = session_name
        self.last_active_session = session_name
        sess = self._sessions.get(session_name)
        if sess:
            sess.last_msg_id = msg_id
        self._dirty = True

    def get_session_by_msg(self, msg_id: int, chat_id: int) -> TrackedSession | None:
        """Find session that owns a Telegram message, scoped to ``chat_id``.

        A stored chat_id of ``0`` is a wildcard for any calling
        ``chat_id``. This matters because bot-message tracking sites
        resolve their chat id via ``resolve_chat_id_int``, which can
        legitimately return ``None`` (→ ``0``) on an unconfigured
        personal install; the wildcard keeps that degenerate case working
        exactly as before. In scope mode (where such an entry can only be
        left over from personal mode) it is honoured only for a session
        of *chat_id* (:func:`session_in_chat`, roadmap 8.72). An entry
        under *chat_id* itself is that chat's message whatever the
        session's stamp: an unstamped session's output goes to the chat
        it would be stamped with (``resolve_chat_id``), so a reply to it
        there reaches it, never another chat.
        """
        name = self._msg_map.get((chat_id, msg_id))
        if name is None and chat_id != 0:
            name = self._msg_map.get((0, msg_id))
            sess = self._sessions.get(name) if name else None
            if (sess is not None and home_chat() is not None
                    and not session_in_chat(sess, chat_id)):
                return None
        if name:
            return self._sessions.get(name)
        return None

    def remove_message(self, msg_id: int, chat_id: int) -> None:
        self._msg_map.pop((chat_id, msg_id), None)

    def all_sessions(
        self, scope_chat_id: int | None = None,
    ) -> dict[str, TrackedSession]:
        """All tracked sessions, optionally filtered to a scope.

        Multi-scope safe: when ``scope_chat_id`` is given, returns only
        sessions belonging to that chat (:func:`session_in_chat`, the
        rule :meth:`find_by_label` / :meth:`live_labels` apply too): an
        unstamped session (``scope_chat_id == 0``) is no chat's in scope
        mode (roadmap 8.72) and the one chat's in personal/legacy mode.
        ``None`` (the default) returns everything, preserving single-scope
        callers.
        """
        if scope_chat_id is None:
            return dict(self._sessions)
        return {
            name: s for name, s in self._sessions.items()
            if session_in_chat(s, scope_chat_id)
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

        A LIVE match always wins over a GONE one, whatever their
        insertion order; a GONE match is returned only when no live
        session carries the label (resume-by-label still reaches it).
        Live regression (2026-09-04): a GONE orphan that had inherited a
        renamed label sat before the live session of the same name, so
        every ``include_gone`` lookup — a per-session setting from the
        Mini App included — landed on the dead twin and the change never
        applied to the session the operator was looking at.
        """
        gone_match: TrackedSession | None = None
        for sess in self._sessions.values():
            if sess.label != label:
                continue
            if (scope_chat_id is not None
                    and not session_in_chat(sess, scope_chat_id)):
                continue
            if sess.status == Status.GONE:
                if include_gone and gone_match is None:
                    gone_match = sess
                continue
            return sess
        return gone_match

    def live_names(self) -> set[str]:
        """Internal names of the non-GONE sessions."""
        return {name for name, sess in self._sessions.items()
                if sess.status != Status.GONE}

    def live_labels(self, scope_chat_id: int | None = None) -> set[str]:
        """Labels of non-GONE sessions, optionally filtered to a scope."""
        out: set[str] = set()
        for sess in self._sessions.values():
            if sess.status == Status.GONE or not sess.label:
                continue
            if (scope_chat_id is not None
                    and not session_in_chat(sess, scope_chat_id)):
                continue
            out.add(sess.label)
        return out

    def remove(self, name: str, *, remember_label: bool = False) -> None:
        """Drop a session. ``remember_label`` keeps its user-facing label
        on a small side table so a re-created entry can recover it.

        Only ``/kill`` passes it. `inject.kill_session` leaves a window
        between SIGTERM and unlinking the socket, and a monitor scan that
        lands in it re-creates the entry from the socket NAME — deriving
        the label afresh and silently discarding a `/rename`. Observed
        live: a session renamed to `shortname` returned to the gone list
        as `livetest_payment_pipeline_refactor_v2_xyz`, and resume by the
        new name 404'd.

        `delete` deliberately does NOT remember: it is the explicit
        "forget this session" action, and a remembered label would then
        attach itself to a future session that happened to reuse the
        internal name.
        """
        sess = self._sessions.pop(name, None)
        self._forget_target(name)
        self._blank_button_slots(name)
        if sess and remember_label and sess.label:
            # Only worth remembering a label derivation would NOT reproduce:
            # for an unrenamed session the two are identical, so storing it
            # buys nothing and only widens the window below.
            if sess.label != _derive_label(name):
                self._remembered_labels[name] = (sess.label, time.monotonic())
            # Bounded by size AND by time. Size, because nothing prunes this
            # on a quiet daemon. Time, because the entry exists only to
            # bridge the seconds between a kill and the monitor scan that
            # re-creates the record — kept longer, it would attach the dead
            # session's name to a genuinely different session that later
            # reused the same internal name.
            self._prune_remembered_labels()
            while len(self._remembered_labels) > _MAX_REMEMBERED_LABELS:
                self._remembered_labels.pop(next(iter(self._remembered_labels)))
        if sess:
            # Clean up message mappings
            to_remove = [key for key, sn in self._msg_map.items() if sn == name]
            for key in to_remove:
                del self._msg_map[key]
            # Best-effort cleanup of this session's /tmp policy + reply-
            # context files (design.md Part 5). Explicit /kill deletes
            # the record outright — it never passes through transition()'s
            # GONE branch — so this is the only place that sees that
            # disjoint path. A failed removal must never block the kill.
            try:
                from aipager.policy_snapshot import clear_session_files
                clear_session_files(name)
            except Exception:
                pass
            _drop_bg_files(name, sess)

    def migrate_chat(self, old: int, new: int) -> list[str]:
        """Telegram upgraded the group *old* to the supergroup *new*
        (roadmap 8.87): every session stamped with *old* moves to *new*
        (its internal name keeps the old suffix; names are internal), and
        so do the chat's message target and its members' own targets. The
        pinned bar's message id and owed "send now" deletes in *old* are
        dropped (those messages are not in the new chat). Returns the
        names re-stamped. Idempotent: a second call finds nothing."""
        moved: list[str] = []
        for name, sess in self._sessions.items():
            if sess.scope_chat_id == old:
                sess.scope_chat_id, sess.scope_kind = new, "group"
                sess.forget_chat_messages()
                moved.append(name)
        changed = bool(moved)
        if old in self._targets:
            self._targets.setdefault(new, self._targets.pop(old))
            changed = True
        for (chat, user), entry in list(self._user_targets.items()):
            if chat == old:
                del self._user_targets[(chat, user)]
                self._user_targets.setdefault((new, user), entry)
                changed = True
        if self.pinned_msg_ids.pop(old, None) is not None:
            changed = True
        # The keyboard the chat showed follows it, like its target.
        if old in self.keyboard_labels:
            self.keyboard_labels.setdefault(new, self.keyboard_labels.pop(old))
            changed = True
        kept = [p for p in self.queued_line_deletes if p[0] != old]
        if len(kept) != len(self.queued_line_deletes):
            self.queued_line_deletes[:] = kept
            changed = True
        # Whose card each message was: those messages are in the old chat
        # (roadmap 8.94h).
        for key in [k for k in self.card_owners if k[0] == old]:
            del self.card_owners[key]
            changed = True
        if changed:
            self._dirty = True
        return moved

    def keyboard_labels_for(self, chat_id: int) -> set[str] | None:
        """The labels *chat_id*'s shared main keyboard shows, or None when
        no such keyboard was recorded (a fresh install, or the first run
        after this record existed)."""
        labels = self.keyboard_labels.get(chat_id)
        return None if labels is None else set(labels)

    def set_keyboard_labels(self, chat_id: int, labels) -> None:
        """Record that *chat_id*'s shared main keyboard now shows *labels*."""
        new = sorted(labels)
        if self.keyboard_labels.get(chat_id) != new:
            self.keyboard_labels[chat_id] = new
            self._dirty = True

    def mark_dirty(self) -> None:
        """Flag that state has changed and needs saving."""
        self._dirty = True

    # -- Persistence ---------------------------------------------------------

    _PERSIST_FIELDS = (
        "name", "label", "last_msg_id", "transcript_path",
        "trigger_msg_id", "pending_queue", "last_prompt",
        "last_prompt_driver_user_id",
        "model_name", "launch_model", "busy_msg_id",
        # Team-mode attribution — preserved across restarts so the
        # pinned dashboard remembers who's driving each session.
        "created_by_user_id", "last_driver_user_id",
        # Resume support — see TrackedSession docstring.
        "claude_session_id", "cwd", "gone_at", "last_assistant_preview",
        "hidden_from_status",
        # The conversation the context notice was sent for (once each).
        "context_warned_conversation",
        # Permission mode — persisted so daemon restarts preserve it.
        # save() also writes the legacy `skip_perms` mirror beside it.
        "perm_mode",
        # Multi-scope routing.
        "scope_chat_id", "scope_kind",
        # Per-session preference overrides — see TrackedSession docstring.
        "override_layout", "override_simple_formatting",
        "override_answer_length", "override_language_level",
        "override_diff_preview", "override_card_age_decay",
        # Roadmap 8.39 — what already reached the chat, so a restart
        # cannot re-post it. `delivered_digests` is written through
        # `persisted_digests()` (confirmed sends only) and read back
        # through `restore_delivered_digests`.
        "delivered_digests", "answer_delivered_wall",
        # Roadmap 8.55: cards whose delete a restart may have cut off.
        "pending_card_deletes",
        # Roadmap 8.102: prompt surfaces whose prompt is known closed.
        "closed_prompt_msgs",
        # Files Claude sends: the turn they may come from, and the call
        # ids already copied (see TrackedSession.files_turn_open).
        "files_turn_open", "files_turn_wall", "files_transcript",
        "files_sent_ids", "files_turn_closed_wall",
        # Claude Code background jobs: a session parked on one stays
        # parked (never typed into) across a restart, and its notice is
        # not sent twice.
        "parked_job", "parked_job_session_id", "parked_since",
        "parked_source", "parked_from_session_id",
        "park_notified_job", "bg_release_pending",
        # NOT here, and not by oversight: `active_subagents` /
        # `finished_subagents` / `tool_history` are per-turn state, and the
        # subagent rows carry `started_at` / `last_seen` as
        # `time.monotonic()` values — seconds since THIS boot. Persisting
        # them would restore stamps written against a different clock, so
        # roadmap 8.22's silence sweep would read a live agent as decades
        # silent (drop it, and publish the false "Finished" card) or as
        # stamped in the future (never sweep it at all). If they are ever
        # persisted, `load()`'s `normalize_subagent_liveness(..., reset=True)`
        # is what keeps that honest. The same goes for `bg_agents` and
        # `bg_shells` (monotonic stamps): after a restart a shell's wake-up
        # takes the "no open job" fresh-turn path. The running agents are
        # kept another way: save() writes `bg_agents` and the job's
        # `active_subagents` as the computed "agents" record with
        # wall-clock starts (agents_record), and load() places them on
        # this boot's clock (restore_agents).
    )
    _MAX_MSG_MAP = 2000  # cap _msg_map entries to avoid unbounded growth (doubled — Part 1 roughly doubles density by also tracking user prompts)

    def save(self) -> None:
        """Serialize persistable state to JSON (atomic write)."""
        sessions = {}
        for name, sess in self._sessions.items():
            d: dict = {}
            for f in self._PERSIST_FIELDS:
                val = getattr(sess, f)
                if f == "pending_queue":
                    # tuples → lists for JSON. Always persist as 5-tuples
                    # (text, msg_id, queued_at, reply_context,
                    # driver_user_id) so future loads can apply the TTL /
                    # reply pointer / sender attribution even if the queue
                    # contained legacy 2-, 3-, or 4-tuples (upgraded in
                    # load()). Legacy shapes have no sender id to widen
                    # with — None, not a guess (see queue_prompt's
                    # docstring).
                    normalized = []
                    for item in val:
                        if len(item) == 5:
                            normalized.append(list(item))
                        elif len(item) == 4:
                            normalized.append(
                                [item[0], item[1], item[2], item[3], None]
                            )
                        elif len(item) == 3:
                            normalized.append(
                                [item[0], item[1], item[2], "", None]
                            )
                        elif len(item) == 2:
                            normalized.append(
                                [item[0], item[1], time.time(), "", None]
                            )
                    val = normalized
                elif f == "busy_msg_id":
                    # Never persist sentinel (-1) or None
                    val = val if val and val > 0 else None
                elif f == "delivered_digests":
                    val = sess.persisted_digests()
                elif f == "pending_card_deletes":
                    val = [m for m in val if type(m) is int and m > 0]
                elif f == "closed_prompt_msgs":
                    val = [m for m in val if type(m) is int and m > 0][
                        -MAX_CLOSED_PROMPT_SURFACES:]
                elif f == "files_sent_ids":
                    val = [t for t in val if isinstance(t, str) and t][
                        -MAX_FILES_SENT_IDS:]
                d[f] = val
            # The mode as an older daemon reads it (a rollback): Full
            # access was its Auto; Ask and Auto read as its Ask, never
            # wider than what was asked for.
            d["skip_perms"] = sess.perm_mode == _perm_modes.FULL
            # Roadmap 8.118: the live status, and what a session waiting
            # on a person waits on, for `aipager status` (aipager/status.py)
            # only. load() never restores them: a restart finds each
            # session's state again.
            d["status"] = sess.status.name
            if sess.status == Status.INTERACTIVE:
                kind, summary = sess.waiting_on_human()
                if kind:
                    d["waiting_kind"] = kind
                    d["waiting_summary"] = summary or ""
            # Roadmap 8.101: computed, not a field (see card_started_wall).
            started_wall = sess.card_started_wall(time.monotonic(), time.time())
            if started_wall > 0:
                d["card_started_wall"] = started_wall
            # The running agents, waiting ones and their links included,
            # and the job's table: computed, only while an agent runs, so a
            # restart brings them back (restore_agents) instead of losing
            # every agent that is still working.
            try:
                agents = sess.agents_record(time.monotonic(), time.time())
            except Exception:  # noqa: BLE001 - the save itself must go on
                log.warning("[%s] running agents not saved", sess.label,
                            exc_info=True)
                agents = None
            if agents is not None:
                d["agents"] = agents
            # Roadmap 8.102: computed from the live prompt, only while the
            # session waits on it, so every way out of the wait drops it
            # from the next save with no clear site of its own.
            try:
                open_prompt = snapshot_open_prompt(
                    sess, time.monotonic(), time.time())
            except Exception:  # noqa: BLE001 - the save itself must go on
                log.warning("[%s] open prompt not saved", sess.label,
                            exc_info=True)
                open_prompt = None
            unsaved = sess.unsaved_prompt_surfaces() if open_prompt is None else []
            if not unsaved:
                self._unsaved_prompts_told.pop(name, None)
            if open_prompt is not None:
                d["open_prompt"] = open_prompt
            elif unsaved:
                told = self._unsaved_prompts_told
                count = len(sess.open_prompts())
                if told.get(name) != count:
                    told[name] = count
                    log.info("[%s] %d open prompts, none saved for a restart",
                             sess.label, count)
                # Two or more prompts open (roadmap 8.120): none is saved,
                # since after a restart aipager could not tell which dialog
                # is on top, and every one of their messages refuses its
                # buttons after it. In the file only: the live prompts here
                # still answer.
                d["closed_prompt_msgs"] = (
                    [m for m in d.get("closed_prompt_msgs", []) if m not in unsaved]
                    + unsaved)[-MAX_CLOSED_PROMPT_SURFACES:]
            elif (sess.status == Status.INTERACTIVE and sess.pending_permission
                    and sess.busy_msg_id and sess.busy_msg_id > 0):
                # The card's open prompt is not saved (asked inside a
                # subagent, or its snapshot failed): a restart cannot bring
                # it back and adopts the card as working, so after one its
                # old answer buttons type nothing. In the file only: the
                # live prompt here still answers.
                d["closed_prompt_msgs"] = (
                    [m for m in d.get("closed_prompt_msgs", [])
                     if m != sess.busy_msg_id] + [sess.busy_msg_id]
                )[-MAX_CLOSED_PROMPT_SURFACES:]
            sessions[name] = d

        # Cap _msg_map: keep only the most recent entries (by insertion order)
        msg_map = self._msg_map
        if len(msg_map) > self._MAX_MSG_MAP:
            keys = list(msg_map.keys())
            msg_map = {k: msg_map[k] for k in keys[-self._MAX_MSG_MAP:]}

        data = {
            "version": 1,
            "last_active_session": self.last_active_session,
            "targets": {str(c): [n, o] for c, (n, o) in self._targets.items()},
            "user_targets": {f"{c}:{u}": [n, o]
                             for (c, u), (n, o) in self._user_targets.items()},
            "pinned_msg_ids": {str(c): m for c, m in self.pinned_msg_ids.items()},
            "keyboard_labels": {str(c): list(labels)
                                for c, labels in self.keyboard_labels.items()},
            "msg_map": {f"{cid}:{mid}": v for (cid, mid), v in msg_map.items()},
            "sessions": sessions,
            "queued_line_deletes": _clean_line_deletes(self.queued_line_deletes),
            "button_index": {
                str(c): {"base": self.button_base.get(c, 0), "names": list(names)}
                for c, names in self.button_index.items()},
            "card_owners": [
                {"chat_id": c, "msg_id": m, **rec}
                for (c, m), rec in list(self.card_owners.items())[-MAX_CARD_OWNERS:]],
        }

        state_file = Path(SESSION_STATE_FILE)
        check_write(state_file)
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
            # A first start and a deleted file (`aipager uninstall`, a new
            # machine on the same bot) look alike, and only the second has
            # old buttons in the chat: counted as lost (roadmap 8.103).
            self.button_tables_lost = True
            return
        except OSError:
            log.warning("Cannot read session state file — starting fresh")
            self.button_tables_lost = True
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Corrupt session state JSON — starting fresh")
            self.button_tables_lost = True
            return
        if not isinstance(data, dict):
            log.warning("Corrupt session state JSON — starting fresh")
            self.button_tables_lost = True
            return

        # Set once the sessions are loaded (below): the per-chat targets
        # key on each session's own chat.
        saved_last_active = data.get("last_active_session", "") or ""
        self.pinned_msg_ids = _load_pinned_msg_ids(data)
        self.keyboard_labels = _load_keyboard_labels(data.get("keyboard_labels"))
        self.queued_line_deletes = _clean_line_deletes(
            data.get("queued_line_deletes"))
        self.card_owners = _load_card_owners(data.get("card_owners"))

        # Resolve the default scope once (for backfilling legacy sessions).
        _default = _default_scope()
        # The sessions whose dtach socket exists: running, not orphans.
        # Read the way the monitor's scan reads them, so a session it is
        # about to find alive is not loaded as ended. Unreadable → none
        # (every entry then loads as it did before this check existed).
        from aipager.dtach import inject
        try:
            live_sockets = inject.live_session_names()
        except OSError:
            log.warning("Cannot list the dtach sockets - every saved "
                        "session without an end stamp loads as ended")
            live_sockets = set()

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
            #
            # Only an entry whose socket is gone: a running session is
            # also saved without gone_at, and stamping it made every
            # session running across a restart load as ended (its / menu
            # entry missing until the first scan revived it).
            backfilled = False
            if (gone_at is None and sd.get("claude_session_id")
                    and name not in live_sockets):
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
            # A running one (live socket, no gone_at) loads UNKNOWN: the
            # first scan and the startup recovery settle it.
            initial_status = Status.GONE if gone_at is not None else Status.UNKNOWN
            sess = TrackedSession(
                name=sd.get("name", name),
                label=sd.get("label", name),
                status=initial_status,
                last_msg_id=sd.get("last_msg_id"),
                transcript_path=sd.get("transcript_path", ""),
                trigger_msg_id=sd.get("trigger_msg_id"),
                last_prompt=sd.get("last_prompt", ""),
                last_prompt_driver_user_id=sd.get("last_prompt_driver_user_id"),
                last_idle_at=0.0,
                created_by_user_id=sd.get("created_by_user_id"),
                last_driver_user_id=sd.get("last_driver_user_id"),
                claude_session_id=sd.get("claude_session_id", ""),
                cwd=sd.get("cwd", ""),
                gone_at=gone_at,
                # The preview is a GONE-time snapshot, stamped together
                # with gone_at. On an entry saved without gone_at (a live
                # session — backfilled above or not) it is a leftover from
                # an earlier death that was never cleared (pre-0.7.14
                # state, roadmap 8.34) — drop it.
                last_assistant_preview=(
                    sd.get("last_assistant_preview", "")
                    if gone_at is not None and not backfilled else ""
                ),
                hidden_from_status=sd.get("hidden_from_status", False),
                context_warned_conversation=str(
                    sd.get("context_warned_conversation") or ""),
                perm_mode=_perm_modes.mode_from_saved(sd),
                launch_model=str(sd.get("launch_model") or ""),
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
                override_diff_preview=sd.get("override_diff_preview"),
                override_card_age_decay=sd.get("override_card_age_decay"),
            )
            # busy_msg_id is a @property (Decision 1) and cannot also be a
            # dataclass __init__ parameter name — set it post-construction
            # via the setter, exactly as every test fixture already does
            # (`s.busy_msg_id = busy_msg_id` right after `TrackedSession(...)`,
            # the `_sess()` pattern in test_bot_notify.py). A persisted
            # positive int pushes a single kind="busy" entry; `None` leaves
            # the stack empty — either way byte-for-byte the same runtime
            # state as before this property existed.
            sess.busy_msg_id = sd.get("busy_msg_id")
            # design.md "turn anchor follows consumption": busy_card_trigger
            # is transient (never persisted), so a restart mid-turn would
            # otherwise see it default to None != trigger_msg_id on the
            # very first tick and fire one spurious (harmless but visible)
            # re-anchor purely because of the restart. Seeding it from the
            # just-restored trigger_msg_id is a best-effort mitigation, not
            # a hard guarantee — see design.md's Risks for the narrow
            # window this doesn't cover.
            if sess.busy_msg_id:
                sess.busy_card_trigger = sess.trigger_msg_id
            # Roadmap 8.39: an entry saved before this field existed has
            # neither key and restores to an empty ring / zero stamp.
            sess.restore_delivered_digests(sd.get("delivered_digests"))
            # Roadmap 8.55: deletes a previous run owed and never landed.
            raw_deletes = sd.get("pending_card_deletes") or []
            if isinstance(raw_deletes, list):
                sess.pending_card_deletes = [
                    m for m in raw_deletes if type(m) is int and m > 0]
            # Roadmap 8.102: prompt surfaces known closed (ints only, the
            # newest MAX_CLOSED_PROMPT_SURFACES).
            raw_closed = sd.get("closed_prompt_msgs") or []
            if isinstance(raw_closed, list):
                sess.closed_prompt_msgs = [
                    m for m in raw_closed if type(m) is int and m > 0][
                        -MAX_CLOSED_PROMPT_SURFACES:]
            # Files Claude sends: the open turn and the ids already copied.
            # Anything unusable restores as no turn and no ids.
            sess.files_turn_open = sd.get("files_turn_open") is True
            try:
                files_wall = float(sd.get("files_turn_wall") or 0.0)
            except (TypeError, ValueError):
                files_wall = 0.0
            sess.files_turn_wall = (files_wall if 0.0 < files_wall <= time.time()
                                    else 0.0)
            if not sess.files_turn_wall:
                sess.files_turn_open = False
            try:
                closed_wall = float(sd.get("files_turn_closed_wall") or 0.0)
            except (TypeError, ValueError):
                closed_wall = 0.0
            sess.files_turn_closed_wall = (
                closed_wall if 0.0 < closed_wall <= time.time() else 0.0)
            files_tp = sd.get("files_transcript")
            sess.files_transcript = files_tp if isinstance(files_tp, str) else ""
            raw_ids = sd.get("files_sent_ids") or []
            if isinstance(raw_ids, list):
                sess.files_sent_ids = [
                    t for t in raw_ids if isinstance(t, str) and t][
                        -MAX_FILES_SENT_IDS:]
            try:
                sess.answer_delivered_wall = float(
                    sd.get("answer_delivered_wall") or 0.0)
            except (TypeError, ValueError):
                sess.answer_delivered_wall = 0.0
            _restore_parked(sess, sd)
            # Roadmap 8.101: the card's clock anchor, only with its card.
            # An unusable value restores as 0.0 (the adoption then counts
            # from itself).
            try:
                started_wall = float(sd.get("card_started_wall") or 0.0)
            except (TypeError, ValueError):
                started_wall = 0.0
            # (A NaN or an infinity fails the range test too.)
            if sess.busy_msg_id and 0.0 < started_wall <= time.time():
                sess.restored_card_started_wall = started_wall
            # Roadmap 8.102: the prompt the session waited on, for the
            # startup recovery. Anything but a valid record is ignored as
            # a whole: the session then loads exactly as before 8.102.
            if "open_prompt" in sd:
                rec = validate_open_prompt(
                    sd.get("open_prompt"), busy_msg_id=sess.busy_msg_id,
                    now_wall=time.time())
                if rec is None:
                    log.warning("[%s] saved permission prompt ignored - "
                                "invalid record", sess.label)
                    # Its card is adopted as working (8.101) and its old
                    # buttons can no longer be read: what their keys would
                    # pick in the dialog is unknown, so none is typed.
                    raw = sd.get("open_prompt")
                    if (isinstance(raw, dict)
                            and raw.get("kind") != "separate"):
                        sess.mark_prompt_closed(sess.busy_msg_id)
                sess.restored_open_prompt = rec
            # A stamp in the future (a hand edit, or the clock stepped back
            # since the save) would make every late answer "predate" it.
            if not 0.0 <= sess.answer_delivered_wall <= time.time():
                sess.answer_delivered_wall = 0.0
            # Multi-scope backfill: stamp legacy sessions (scope_chat_id == 0)
            # with the single configured chat so notify routing is explicit.
            if sess.scope_chat_id == 0:
                # The chat its name's suffix names, else the home chat
                # (never "prefer the group", roadmap 8.82).
                _own = chat_from_name(name) or _default
                if _own is not None:
                    sess.scope_chat_id, sess.scope_kind = _own
                    backfilled = True
            # pending_queue: accept old 2-tuples, 3-tuples, 4-tuples, and
            # new 5-tuples (text, msg_id, queued_at, reply_context,
            # driver_user_id). Drop entries older than the TTL so a
            # daemon that was down for days doesn't flush stale prompts
            # the moment a session goes IDLE.
            now = time.time()
            ttl_cutoff = now - QUEUE_MAX_AGE_SECONDS
            dropped = 0
            for item in sd.get("pending_queue", []):
                if not isinstance(item, list):
                    continue
                if len(item) == 2:
                    # Legacy: no timestamp, no reply_context, no sender
                    # id — treat as fresh with no reply pointer and an
                    # unknown (never guessed) sender.
                    sess.pending_queue.append(
                        (item[0], item[1], now, "", None)
                    )
                elif len(item) in (3, 4, 5):
                    text, msg_id, queued_at = item[0], item[1], item[2]
                    # reply_context: fail-safe pattern already used for
                    # override_layout above — a hand-edited or
                    # since-invalidated value degrades to "no reply
                    # pointer" rather than crashing the load.
                    reply_context = item[3] if len(item) >= 4 else ""
                    if not isinstance(reply_context, str):
                        reply_context = ""
                    # driver_user_id: same fail-safe pattern. A 4-tuple
                    # from a pre-this-change build, or a hand-edited
                    # non-int value, degrades to None (unknown sender —
                    # floors permissions on drain) rather than crashing
                    # or, worse, being misread as a real id.
                    driver_user_id = item[4] if len(item) == 5 else None
                    if driver_user_id is not None and not isinstance(
                        driver_user_id, int
                    ):
                        driver_user_id = None
                    try:
                        queued_at_f = float(queued_at)
                    except (TypeError, ValueError):
                        queued_at_f = now
                    if queued_at_f < ttl_cutoff:
                        dropped += 1
                        continue
                    sess.pending_queue.append(
                        (text, msg_id, queued_at_f, reply_context,
                         driver_user_id)
                    )
            if dropped:
                log.warning(
                    "[%s] dropped %d queue entries older than %d h",
                    sess.label, dropped, int(QUEUE_MAX_AGE_SECONDS / 3600),
                )
            # Roadmap 8.22: every restored subagent row starts one honest
            # silence window here. `reset=True` and not the plain fill-in:
            # a `last_seen` that came out of the file is a monotonic stamp
            # from a PREVIOUS boot, so keeping it would either drop a live
            # agent on the first scan (the false-"Finished" card this fix
            # removes) or, if that boot ran longer than this one, sit in
            # the future and make the row immortal. `active_subagents` is
            # transient (not in _PERSIST_FIELDS), so there is nothing to
            # restamp here: the job's saved agents wait in
            # `restored_job_agents` for the adoption, which stamps them.
            sess.normalize_subagent_liveness(time.monotonic(), reset=True)
            # The running agents the previous run saved, for a session
            # whose socket is live only: a GONE one runs none.
            if "agents" in sd and sess.status == Status.UNKNOWN:
                restored, waiting = sess.restore_agents(
                    sd.get("agents"), time.monotonic(), time.time())
                if restored or sess.restored_job_agents:
                    log.info("[%s] restored %d running agents (%d waiting)",
                             sess.label, restored, waiting)
            self._sessions[name] = sess
            log.info("Restored session: %s [%s] (msg_id=%s, trigger=%s, queue=%d)",
                     name, sess.label, sess.last_msg_id, sess.trigger_msg_id,
                     len(sess.pending_queue))
            if backfilled:
                log.info("[%s] backfilled gone_at from transcript mtime "
                         "(orphan session — was missing the stamp)",
                         sess.label)
                self._dirty = True  # persist on next save_if_dirty

        # Roadmap 8.103: after the sessions, to tombstone the names gone.
        self.button_index, self.button_base, lost = _load_button_index(
            data.get("button_index"), set(self._sessions))
        self.button_tables_lost = lost

        # Rebuild _msg_map AFTER the sessions loop above, not before it.
        # ORCHESTRATOR-VERIFIED LOAD-ORDER BUG: the migration below needs
        # sess.scope_chat_id, which only exists once a session has been
        # reconstructed (and is itself backfilled from _default_scope()
        # inside that same loop, right above). Rebuilding _msg_map before
        # the loop ran would silently drop every legacy (bare-digit-key)
        # entry, because self._sessions would still be empty.
        saved_map = data.get("msg_map", {})
        for k, v in saved_map.items():
            if ":" in k:
                # New shape: "<chat_id>:<msg_id>". Split on the FIRST
                # ':' only — a group chat_id is negative (contains '-')
                # but never contains ':'.
                cid_s, mid_s = k.split(":", 1)
                try:
                    self._msg_map[(int(cid_s), int(mid_s))] = v
                except (ValueError, TypeError):
                    continue
            else:
                # Old (pre-Part-0) shape: a bare message_id string, no
                # chat_id. Attribute it to the owning session's
                # now-resolved scope_chat_id; drop it if that session is
                # missing or was never stamped (scope_chat_id == 0) —
                # cannot attribute safely. Costs one restart's worth of
                # reply-routing history for that entry; the existing
                # text-guess/last-active fallback still applies.
                try:
                    mid = int(k)
                except (ValueError, TypeError):
                    continue
                owner = self._sessions.get(v)
                if owner is None or owner.scope_chat_id == 0:
                    continue
                self._msg_map[(owner.scope_chat_id, mid)] = v

        log.info("Loaded %d sessions, %d message mappings",
                 len(self._sessions), len(self._msg_map))
        # `_dirty` may have been set True by the backfill loop above so
        # the next save_if_dirty cycle persists the derived gone_at.
        # Otherwise it stays False (load alone doesn't dirty the state).

        self._load_targets(data.get("targets"), saved_last_active)
        self._load_user_targets(data.get("user_targets"))
        self._follow_chat_migrations()

        # Age out sessions that ended more than GONE_SESSION_MAX_AGE_DAYS
        # ago (roadmap 8.12) — after msg_map and last_active_session are
        # restored, so their references go with the entries.
        self.expire_gone()

    def _follow_chat_migrations(self) -> None:
        """At load: state saved before the daemon followed a group's
        upgrade to a supergroup (roadmap 8.87: the daemon stopped before
        it saved) still names the old chat. Move it, as
        :meth:`migrate_chat` does live."""
        try:
            migrations = load_chat_migrations()
        except Exception:
            log.debug("could not read the chat migrations", exc_info=True)
            return
        for old in migrations:
            moved_to = follow_migrated_chat((old, "group"))
            if moved_to is None or moved_to[0] == old:
                continue
            names = self.migrate_chat(old, moved_to[0])
            if names:
                log.info("Chat %s was upgraded to %s: moved %d session(s) "
                         "saved with the old id", old, moved_to[0], len(names))

    def _load_targets(self, raw, saved_last_active: str) -> None:
        """Restore the per-chat targets. State saved before they existed
        has only the install-wide one: it becomes its own chat's target,
        which is exactly where a single-chat install sent everything."""
        self._targets = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                try:
                    chat = int(key)
                    name, order = value
                    order = int(order)
                except (TypeError, ValueError):
                    continue
                if not (isinstance(name, str) and name in self._sessions):
                    continue
                # Keyed by the session's chat NOW: the load's backfill may
                # just have stamped a session that had none.
                chat = self._target_chat(self._sessions[name])
                if chat not in self._targets or self._targets[chat][1] < order:
                    self._targets[chat] = (name, order)
        self._target_seq = max((o for _n, o in self._targets.values()), default=0)
        if saved_last_active in self._sessions:
            if not self._targets:
                self.last_active_session = saved_last_active
            else:
                self._last_active_session = saved_last_active

    def _load_user_targets(self, raw) -> None:
        """Restore the group members' own targets (roadmap 8.90). State
        saved before they existed has none: everyone starts without one,
        which is the chat's only session or a "which session?"."""
        self._user_targets = {}
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            try:
                chat_s, user_s = str(key).split(":")
                chat, user = int(chat_s), int(user_s)
                name, order = value
                order = int(order)
            except (TypeError, ValueError, OverflowError):
                continue
            if not (_is_group_chat(chat) and isinstance(name, str)
                    and name in self._sessions):
                continue
            self._user_targets[(chat, user)] = (name, order)
        self._target_seq = max(
            [self._target_seq] + [o for _n, o in self._user_targets.values()])

    def save_if_dirty(self) -> None:
        """Save state if it has been modified since last save."""
        if self._dirty:
            self.save()
            self._dirty = False
