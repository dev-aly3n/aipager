"""Async session monitor — discovers dtach sessions, detects dead ones.

Replaces the old pane_monitor. No pane scraping (dtach has no capture_pane).
Status transitions (IDLE, INTERACTIVE) come from hook_receiver only.
This monitor handles:
1. Discovering new dtach sessions not yet in the registry
2. Marking dead sessions as GONE
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from aipager import claude_jobs, rm_countdown, statusline_file, term_dialog
from aipager.dtach import bg_jobs
from aipager.report import store as report_store
from aipager.dtach import inject as dtach_inject
from aipager.config import (
    COMPACT_INFLIGHT_MAX_SECONDS,
    PANE_POLL_INTERVAL,
    PROMPT_HOOK_GRACE_SECONDS,
    STALE_BUSY_TIMEOUT,
    STATUSLINE_ALIVE_SECONDS,
    TOOL_INFLIGHT_MAX_SECONDS,
)
from aipager.state import (
    TURN_STATE_HOLD_SECONDS,
    WAITING_PARENT_RESUME_SECONDS,
    SessionRegistry,
    Status,
    TrackedSession,
)
from aipager.transcript import (
    extract_last_response_flagged,
    last_exchange_at,
    last_assistant_preview,
    saved_prompt_evidence,
    tool_call_result,
    turn_appears_complete,
    turn_tail_since,
)

log = logging.getLogger(__name__)

# The monitor loop's wait between ticks. A seam: tests replace THIS, never
# ``asyncio.sleep`` (the global module; CLAUDE.md). Defaults to the real one.
_loop_sleep = asyncio.sleep
# The wait until a terminal dialog's card falls due (roadmap 8.127,
# ``SessionMonitor._card_due_later``). A seam, like ``_loop_sleep``.
_card_due_sleep = asyncio.sleep
# The wait until a risky-removal prompt's refusal time (roadmap 8.128,
# ``SessionMonitor._rm_due_later``). A seam, like ``_loop_sleep``.
_rm_due_sleep = asyncio.sleep

# Item 2.2 — after this long with no hook activity, the INTERACTIVE
# watchdog asks the transcript whether the dialog is still open, and
# demotes the session to BUSY only when it shows the dialog was answered
# or the turn went past it (a closing hook lost). A dialog nobody answered
# stays open with no time limit (2026-10-12: the old blind demotion made
# aipager type the next Telegram message into a question still on
# screen, and its Enter picked the first option). A Claude Code that died
# with the dialog open is a session that ends, which the scan sees.
#
# Tunable via `AIPAGER_INTERACTIVE_TIMEOUT` (seconds) for ops testing.
INTERACTIVE_TIMEOUT_SECONDS: float = float(
    os.environ.get("AIPAGER_INTERACTIVE_TIMEOUT", "300")
)

# Roadmap 8.22 — drop a subagent entry once it has gone SILENT, not once
# it is merely old. Item 2.4 originally swept by age on the rationale
# "real subagents finish in seconds"; that is false. Agents run for
# hours: on 2026-09-11 a /ship pipeline's ship-developer had been working
# for 60 minutes when the age sweep dropped it, which emptied
# `active_subagents` and let idle-recovery publish a "Finished (95m)"
# card while the agent was still going.
#
# A working agent, by contrast, emits tool hooks every few seconds, and
# every hook fired inside a subagent carries its `agent_id` — so
# hook_receiver stamps the entry's `last_seen` on each one
# (TrackedSession.touch_subagent). Silence for this long is the real
# evidence that a SubagentStop was missed (daemon restart, crash, dropped
# datagram); age is evidence of nothing.
#
# Tunable via `AIPAGER_SUBAGENT_SILENCE` (seconds).
SUBAGENT_SILENCE_DEFAULT: float = 1800.0

_LEGACY_SILENCE_ENV = "AIPAGER_SUBAGENT_TTL"
_SILENCE_ENV = "AIPAGER_SUBAGENT_SILENCE"

# Pre-8.22 name for the old age-based bound. Retained as a deprecated
# alias so an operator's existing override keeps working: it feeds the
# silence window below when the new variable is unset. The constant
# itself is kept because the pre-8.22 tests still import it; no
# production module does, and nothing reads it to decide a drop.
#
# Blank counts as unset here too, for the same reason it does in
# `_resolve_silence_window` and unlike the pre-8.22 line this replaces:
# a unit file or CI job that spells "no override" as `FOO=` took the
# daemon down at import with `float('')`.
SUBAGENT_TTL_SECONDS: float = float(
    os.environ.get(_LEGACY_SILENCE_ENV) or "3600"
)


def _resolve_silence_window(env: dict | None = None) -> tuple[float, str | None]:
    """Resolve the subagent silence window from *env*.

    Returns ``(seconds, deprecated_env_name_or_None)``. The new variable
    wins whenever it is set; the pre-8.22 one is honoured as an alias only
    when it is not, and is reported back so the caller can say so once at
    startup. An empty value counts as unset (a blank env var is how CI and
    systemd units spell "no override", and ``float("")`` would otherwise
    take the daemon down at import).
    """
    src = os.environ if env is None else env
    fresh = src.get(_SILENCE_ENV)
    if fresh:
        return float(fresh), None
    legacy = src.get(_LEGACY_SILENCE_ENV)
    if legacy:
        return float(legacy), _LEGACY_SILENCE_ENV
    return SUBAGENT_SILENCE_DEFAULT, None


SUBAGENT_SILENCE_SECONDS, _SILENCE_DEPRECATED_ENV = _resolve_silence_window()
# Set once the deprecation notice has been emitted, so a daemon logs it at
# startup and not once per SessionMonitor ever constructed.
_silence_deprecation_logged: bool = False

# How long a background shell counts as running with no end seen. Shells
# send no hooks while they run, so there is no silence to judge by, and a
# legitimate test run or build can take an hour: past this age the shell
# stops holding its job open and stops counting in the pin, and its card
# row reads "no end seen". Overridable with AIPAGER_BG_SHELL_MAX_TRACK
# (seconds); read at every scan, so a test can patch the global.
_BG_SHELL_MAX_TRACK_ENV = "AIPAGER_BG_SHELL_MAX_TRACK"
BG_SHELL_MAX_TRACK_DEFAULT: float = 7200.0


def resolve_bg_shell_max_track(env: dict | None = None) -> float:
    """The background-shell max age from *env* (default ``os.environ``).
    Blank or unset gives the default; an unparsable or non-positive value
    logs a warning and gives the default. Never raises: this runs at
    import, and a bad value must not take the daemon down."""
    src = os.environ if env is None else env
    raw = (src.get(_BG_SHELL_MAX_TRACK_ENV) or "").strip()
    if not raw:
        return BG_SHELL_MAX_TRACK_DEFAULT
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0 or value == float("inf"):
        log.warning("%s=%r is not a positive number of seconds; using %d",
                    _BG_SHELL_MAX_TRACK_ENV, raw,
                    int(BG_SHELL_MAX_TRACK_DEFAULT))
        return BG_SHELL_MAX_TRACK_DEFAULT
    return value


BG_SHELL_MAX_TRACK_SECONDS: float = resolve_bg_shell_max_track()

# Idle-recovery fallback. The normal BUSY→IDLE transition comes from
# Claude's Stop hook (hook_receiver). If that hook is ever missed — e.g.
# the user interrupts a pending permission then immediately sends a new
# prompt — the session would animate "Thinking…" forever. When a BUSY
# session's transcript shows the turn finished AND the file has been quiet
# for this long, the monitor recovers it to IDLE the same way the hook
# would. The grace must comfortably exceed normal hook latency so a
# fast-completing turn is finalized by the hook, not raced by the monitor.
IDLE_RECOVERY_GRACE: float = float(
    os.environ.get("AIPAGER_IDLE_RECOVERY_GRACE", "8")
)

# Slack for the "was the transcript written during this turn?" check.
# Some filesystems (tmpfs, HFS+, older NFS) store mtime at 1-second
# resolution, so a write that really did land just after the turn started
# can report an mtime a fraction under it. Well below IDLE_RECOVERY_GRACE,
# so this cannot re-admit a transcript that is genuinely a turn behind.
MTIME_GRANULARITY_SLACK: float = 1.0

# Busy-card staleness watchdog. A live busy card is owned by one animate
# task that repaints it every STREAM_EDIT_INTERVAL..BUSY_EDIT_INTERVAL
# seconds while the session works. If that task is gone (stopped for a
# permission prompt and never resumed, or ended by an exception) or is
# alive but has not landed an edit in this long, the card sits frozen —
# an elapsed counter that stops moving reads as a wedged session. The
# healthy cadence is at most a few seconds, so this is a generous
# multiple of it; it doubles as the per-session throttle on the
# watchdog's own actions.
CARD_STALE_SECONDS: float = 20.0

# How long a busy card must stay orphaned — live, Stop button up, while no
# turn or job is running and nothing is handling the session — before the
# monitor settles it (roadmap 8.55/8.57). Longer than any finish path's
# flood-delayed calls, so a card a finish is still closing is never
# touched; short enough that a stray Stop button does not linger.
ORPHAN_CARD_GRACE_SECONDS: float = 30.0


def orphan_card_due(sess: TrackedSession, now: float) -> bool:
    """Whether *sess*'s card has been orphaned (``card_orphaned``) for
    ``ORPHAN_CARD_GRACE_SECONDS`` without a break. Keeps the first-seen
    stamp on the session (``orphan_card_seen_at``): any tick that sees the
    card owned again restarts the wait, and a due result restarts it too,
    so a settle that was refused is retried one grace period later."""
    if not sess.card_orphaned(now):
        sess.orphan_card_seen_at = 0.0
        return False
    if not sess.orphan_card_seen_at:
        sess.orphan_card_seen_at = now
        return False
    if now - sess.orphan_card_seen_at < ORPHAN_CARD_GRACE_SECONDS:
        return False
    sess.orphan_card_seen_at = now
    return True


def busy_card_watchdog_action(
    sess: TrackedSession, now: float, *, suppressed: bool = False,
    stale_after: float = CARD_STALE_SECONDS,
) -> tuple[str, float] | None:
    """What the busy-card watchdog should do for *sess* at *now*, if anything.

    ``suppressed`` is "this chat is flood-muted or in minimal mode"
    (roadmap 8.29 R7). It returns ``None`` FIRST when set: there is no
    point restarting an animation that cannot animate, and doing so
    anyway is where 348 restarts in 54 minutes came from on 2026-09-15 —
    two log lines each, against six actual HTTP refusals all day. The
    mute path returned ``None`` from ``_animate_tick``, which ended
    ``_animate_busy``'s loop and killed the task; this function then saw
    a BUSY session with no animate task and restarted it, every 20 s, for
    the length of the ban.

    Passed IN rather than looked up, so this function stays PURE — no
    I/O, no ``time.monotonic()``, no ``MUTE`` lookup inside — and every
    existing row that fabricates a ``now`` keeps working untouched.
    ``cards_suppressed`` is the impure half, and it lives next door.

    Returns ``("restart", 0.0)`` when
    the card should be ticking but no animate task is alive, ``("refresh",
    seconds_since_last_edit)`` when a task is alive but the card has gone
    ``stale_after`` without a successful edit, and ``None`` otherwise.

    ``stale_after`` defaults to ``CARD_STALE_SECONDS``; the scan passes
    ``max(CARD_STALE_SECONDS, 2 x the card's turn-age floor)`` (8.30), so a
    card that is SUPPOSED to be edited every 30 s is not force-refreshed
    every 20 s — which is what the log showed on vm3: "forced stale-card
    refresh (63s since last edit)", an extra edit per cycle on exactly the
    long-running card the decay exists to slow down.

    Never acts on a session that has no card, is INTERACTIVE (the card is
    the permission prompt), has a compacting card on top, is mid
    ``_send_busy_and_animate`` (``animate_lock`` held — the old task is
    stopped on purpose there, moments before the card is replaced), or
    was acted on less than ``CARD_STALE_SECONDS`` ago. That last rule is
    what keeps a permanently un-editable card (bot blocked) from being
    retried on every 2s scan.

    Staleness is measured from ``last_tool_edit_at`` — stamped only by a
    successful rich-card edit — falling back to ``busy_started_at`` for a
    card that has never been edited (``_send_busy_and_animate`` zeroes the
    stamp on send; the first tick lands ~1.5s later). With neither stamp
    there is no baseline and nothing is forced.
    """
    if suppressed:
        return None
    if not sess.busy_card_should_animate():
        return None
    if sess.animate_lock.locked():
        return None
    if sess.card_watchdog_at and now - sess.card_watchdog_at < CARD_STALE_SECONDS:
        return None
    if not sess.animation_running():
        return "restart", 0.0
    baseline = sess.last_tool_edit_at or sess.busy_started_at
    if not baseline:
        return None
    since = now - baseline
    if since < stale_after:
        return None
    return "refresh", since


def card_stale_after(sess: TrackedSession, now: float) -> float:
    """How long *sess*'s card may go without an edit before the watchdog
    forces one: ``CARD_STALE_SECONDS``, or twice the card's turn-age floor
    once that is larger (8.30). Late imports — ``aipager.bot`` imports
    back into this package — and never raises."""
    try:
        from aipager.bot.animation import card_age, card_age_decay_enabled
        from aipager.flood_policy import card_age_floor

        floor = card_age_floor(card_age(sess, now), card_age_decay_enabled(sess))
    except Exception:  # pragma: no cover - defensive
        log.debug("could not read the card age floor", exc_info=True)
        return CARD_STALE_SECONDS
    return max(CARD_STALE_SECONDS, 2.0 * floor)


def _session_chat_id(sess):
    """The numeric chat a session resolves to, or ``None``. Never raises.

    Late import: ``aipager.bot.transport`` imports back into this package,
    so binding it at module scope would cycle.
    """
    try:
        from aipager.bot.transport import resolve_chat_id_int

        return resolve_chat_id_int(sess)
    except Exception:  # pragma: no cover - defensive
        return None


def cards_suppressed(chat_id) -> bool:
    """Is this chat's busy card suppressed — muted, or in minimal mode?

    The impure half of the watchdog's decision, kept apart from
    :func:`busy_card_watchdog_action` so that function stays pure and
    directly callable with a fabricated ``now``.

    Late imports and NEVER raises: this runs on the 2 s scan for every
    session, and a limiter problem must not be able to stop it. A chat it
    cannot answer for is treated as not suppressed — the watchdog then
    behaves exactly as it did before 8.29, which is the safe direction.
    """
    if not chat_id:
        return False
    try:
        from aipager.bot.flood import MUTE

        if MUTE.is_muted(chat_id):
            return True
        from aipager.bot.flood_budget import BudgetRateLimiter
        from aipager.bot.rich_message import get_rate_limiter

        limiter = get_rate_limiter()
        if isinstance(limiter, BudgetRateLimiter):
            return limiter.minimal_mode(chat_id)
    except Exception:  # pragma: no cover - defensive
        log.debug("could not determine card suppression for %s", chat_id,
                  exc_info=True)
    return False


def card_suppression_transition(seen: set, chat_key, suppressed: bool):
    """``"start"``, ``"lift"`` or ``None`` for one chat's suppression.

    Pure over the set it is passed, which it MUTATES — the set is the
    caller's memory of which chats were suppressed on the previous tick.
    Returns ``"start"`` the first tick a chat becomes suppressed,
    ``"lift"`` the first tick it stops, and ``None`` on every tick in
    between.

    A transition function rather than an ``if suppressed: log`` at the
    call site, because "exactly one INFO each way" is the actual
    requirement and at a 2 s tick the naive form emits 1,800 lines an
    hour. Written as a pure function so a mutation can break it and a
    test can name it.
    """
    if suppressed:
        if chat_key in seen:
            return None
        seen.add(chat_key)
        return "start"
    if chat_key in seen:
        seen.discard(chat_key)
        return "lift"
    return None


#: How long a files turn may stay open on an idle session with no job
#: before it counts as one that started nothing (session_monitor's tick
#: closes it, :func:`files_turn_unstarted`). A real turn makes the session
#: busy within a second of its prompt; a typed prompt that runs no turn,
#: or a turn ended with no Stop through a path that sets the session idle
#: directly (an interrupt, a prompt Claude Code refused), leaves it open.
FILES_TURN_UNSTARTED_SECONDS: float = 15.0


def files_turn_unstarted(sess: TrackedSession, now: float) -> bool:
    """Whether *sess*'s files turn (the turn files Claude sends may come
    from, ``TrackedSession.files_turn_open``) is open on an idle session
    with no background job, and opened more than
    FILES_TURN_UNSTARTED_SECONDS ago (*now* is monotonic). Pure."""
    return (sess.files_turn_open and sess.status == Status.IDLE
            and not sess.job_background_open()
            and now - sess.files_turn_opened_at > FILES_TURN_UNSTARTED_SECONDS)


def prompt_not_taken(sess: TrackedSession, now: float) -> bool:
    """Did Claude Code refuse the Telegram send that put *sess* into
    BUSY? Pure — no I/O, no clock inside (roadmap 8.11).

    True when the session is BUSY, a turn-starting send is stamped
    (``_inject_prompt`` stamps only a send made while the session was
    not BUSY), no datagram that proves a turn is alive has arrived
    since it (``turn_hook_at`` older
    than ``prompt_sent_at`` — the receiver stamps every hook except the
    ``statusline`` repaint and the phantom SubagentStop), and the send
    is older than ``PROMPT_HOOK_GRACE_SECONDS``. An accepted prompt
    fires UserPromptSubmit within ~0.15 s; nothing at all arrives for an
    input Claude Code rejected outright (an unknown slash command, a
    built-in that only opens a dialog), which used to leave the busy
    card spinning until STALE_BUSY_TIMEOUT.

    A send made while the session was already BUSY is deliberately out
    of scope: the running turn's own hooks keep stamping
    ``turn_hook_at``, so "no hook since the send" cannot be told from
    "the turn is busy elsewhere" — and the 👀 reaction that never turns
    into 👍 already shows that message was not taken. Such a send is
    never stamped, so it can neither trip this nor cancel the deadline
    of an earlier from-idle send.
    """
    if sess.status != Status.BUSY:
        return False
    if sess.prompt_sent_at <= 0.0 or sess.turn_hook_at >= sess.prompt_sent_at:
        return False
    return (now - sess.prompt_sent_at) > PROMPT_HOOK_GRACE_SECONDS


def _quiet_since(sess: TrackedSession) -> float | None:
    """When this turn last showed a sign of life — or ``None`` if that is
    not knowable yet.

    The **later** of the two stamps, not the first truthy one.
    ``last_hook_at`` is the last hook of the *previous* turn once a
    session has taken one, so ``last_hook_at or busy_started_at``
    preferred a stale value forever. Invisible while a session works —
    hooks refresh it every few seconds — but a session left idle past
    the timeout and then re-prompted got warned about on the very next
    2s scan, quoting its entire idle stretch as the quiet period
    (roadmap 8.5: "still working — quiet for 20038 min", one second
    after the operator's message, on a turn that then answered fine).

    ``or None`` keeps the fresh-daemon case honest: both stamps are
    ``time.monotonic()`` and neither is persisted, so after a restart
    both are ``0.0`` and every caller's ``if baseline and …`` guard
    skips the check rather than measuring from the epoch.

    The same correction fixes a second, far more reachable false alarm:
    answering a permission prompt shifts ``busy_started_at`` *forward* by
    the wait (``callbacks.py``, so a human's thinking time is discounted
    from "thought for Xs"), while ``last_hook_at`` still points at the
    moment the prompt was raised. Sitting on a prompt past the timeout
    and then tapping Allow used to produce the identical bogus warning
    within a second — no fortnight required.

    Shared with the INTERACTIVE watchdog for one implementation rather
    than two, NOT because that site was broken: every hook stamps
    ``last_hook_at`` before any event branching
    (``hook_receiver.py``), and all three transitions into INTERACTIVE
    are downstream of that stamp, so the old and new expressions agree
    there in every reachable state. It is shared so a future
    INTERACTIVE-entry path that skips the stamp cannot reintroduce the
    bug at a second site.
    """
    return max(sess.last_hook_at, sess.busy_started_at) or None


def _prompt_agent(perm) -> str:
    """The agent id that owns open prompt *perm*, ``""`` for Claude's own
    (or one whose tool is not known)."""
    info = perm.get("tool_info") if isinstance(perm, dict) else None
    return str(info.get("agent_id") or "") if isinstance(info, dict) else ""


def _interactive_quiet_since(sess: TrackedSession) -> float | None:
    """When the dialogs a waiting session shows last had a sign of life,
    for the INTERACTIVE watchdog (roadmap 8.120): the latest of Claude's
    own hook (``parent_hook_at``), the turn's start, and each open agent
    prompt's owner's latest hook (or its arrival). A background agent that
    owns no open prompt keeps working while Claude waits, and its hooks
    said nothing about the dialog: counted, they kept the watchdog from
    ever firing. ``None`` when nothing is known (a fresh daemon)."""
    stamps = [sess.parent_hook_at, sess.busy_started_at]
    for perm in sess.open_prompts():
        if not _prompt_agent(perm):
            continue
        order = perm["tool_info"].get("order")
        if isinstance(order, dict):
            stamps.append(max(order.get("owner_hook_at") or 0.0,
                              order.get("arrived") or 0.0))
        else:
            stamps.append(perm.get("wait_started_at") or 0.0)
    # Nothing of Claude's own or an owner's on record (a hand-built or
    # older state): the session's latest hook, as before 8.120.
    return max(stamps) or sess.last_hook_at or None


def expired_compacting_sessions(
    sessions: dict[str, TrackedSession], now: float,
) -> list[str]:
    """Names whose live message is a compaction card whose deadline has
    passed (design.md "Live Message Stack", Decisions 3 and 5).

    Pure — no I/O, no asyncio, no ``time.monotonic()`` call inside it, so
    it is directly callable in pytest with a fabricated ``now``; no test
    needs to wait out a real timeout. Deliberately **not** gated on
    ``status == BUSY``, unlike every other watchdog in this module: a
    session can desync from BUSY while a compacting card is still live
    (the observed live bug — status: idle, card still spinning), which
    would make it invisible to a status-gated check.

    A session with no live ``compacting`` entry, or one that hasn't
    reached its deadline yet (including one pushed with
    ``deadline_seconds=None``, which never expires), is never included.
    """
    # Delegates the staleness rule to the session itself, so the sweeper
    # and _send_busy_and_animate's reclaim branch can never disagree about
    # whether a given card is stale — they ask the same predicate.
    return [name for name, sess in sessions.items()
            if sess.compacting_is_overdue(now)]


def _has_held_answer(sess) -> bool:
    """Does *sess* have an answer waiting for its chat's mute to lift?

    Late import and never raises: this runs on the 2 s scan for every
    session, and a buffer problem must not be able to stop the scan.
    The mute itself is re-checked by ``flush_held_answers``; asking here
    would double the work for no benefit, since the flush returns 0
    immediately while still muted.
    """
    try:
        from aipager.bot.held import HELD

        if HELD.count() == 0:
            return False
        return any(session == sess.name
                   for _chat, session in HELD.sessions_with_held())
    except Exception:  # pragma: no cover - defensive
        return False


def _has_pending_files(sess) -> bool:
    """Does *sess* have files Claude sent waiting to be delivered (for a
    mute to lift, a held answer to land, or copies no finish took)? Late
    import, never raises, cheap when nothing waits."""
    try:
        from aipager.bot.outbox import OUTBOX

        if not OUTBOX.pending and not OUTBOX.batches:
            return False
        return (OUTBOX.has_pending(sess.name)
                or (sess.status is Status.IDLE and bool(OUTBOX.batches.get(sess.name))))
    except Exception:  # pragma: no cover - defensive
        return False


#: The outbox's day sweep runs at most this often (monotonic seconds).
_OUTBOX_SWEEP_EVERY = 60.0
_outbox_swept_at: float | None = None


def _sweep_outbox() -> None:
    """Give up the files that waited more than a day (each audited, no
    chat line: the chat may still be muted) and delete copies a day old.
    Throttled to once a minute; never raises."""
    global _outbox_swept_at
    now = time.monotonic()
    if _outbox_swept_at is not None and now - _outbox_swept_at < _OUTBOX_SWEEP_EVERY:
        return
    _outbox_swept_at = now
    try:
        from aipager.bot import outbox

        for p in outbox.OUTBOX.sweep():
            for b in p.batches:
                for item in b.items:
                    outbox.audit_file(p.audit or {"session": p.session},
                                      outbox.ACTION_FAILED, item.name, item.size,
                                      outbox.CAUSE_EXPIRED)
                log.warning("turn file failed: %d file(s) of %s waited a day "
                            "and were dropped", len(b.items), p.session)
    except Exception:  # pragma: no cover - defensive
        log.warning("outbox sweep failed", exc_info=True)


def _held_chat_key(sess):
    """The chat key ``_hold_answer`` files this session's answers under:
    the numeric chat, else the raw one. Never raises."""
    try:
        from aipager.bot.transport import resolve_chat_id, resolve_chat_id_int

        return resolve_chat_id_int(sess) or resolve_chat_id(sess)
    except Exception:  # pragma: no cover - defensive
        return None


def _chat_holds_answers(chat_id) -> bool:
    """Is anything still held for *chat_id*? Never raises.

    The order gate of the 8.17c catch-up: a chat's held answers go first,
    and an owed card or keyboard waits behind any that are still there —
    one that failed a delivery attempt for a non-mute reason stays held
    for a few ticks, and nothing may overtake it. Keyed the way
    ``_hold_answer`` keys it (the session's numeric chat).
    """
    if not chat_id:
        return False
    try:
        from aipager.bot.held import HELD

        return HELD.count(chat_id) > 0
    except Exception:  # pragma: no cover - defensive
        return False


def _sweep_flood_backoff() -> None:
    """Let every chat's 429 backoff decay on the clock, not only while a
    busy card happens to be ticking (roadmap 8.21).

    The backoff halves once per quiet minute and the daemon publishes the
    chats still above ×1 to a small file `aipager status` and `aipager
    doctor` read. Both the decay and that file are driven from the card
    animator — so a chat that 429s and then goes quiet, with every session
    IDLE, would keep reporting "×4" for as long as nobody started a turn.
    This scan already runs every 2 s; one sweep here is the whole fix.

    Imported late and typed on the limiter we install, so a daemon running
    without one (or with PTB's, in a test) is simply skipped. Never
    raises: a diagnostic file must not be able to stop the session scan.
    """
    from aipager.bot import flood_state, held
    from aipager.bot.flood_budget import BudgetRateLimiter
    from aipager.bot.rich_message import get_rate_limiter

    limiter = get_rate_limiter()
    if isinstance(limiter, BudgetRateLimiter):
        limiter.sweep()
    # And the held-answer buffer's AGE cap (8.29 T4). The count cap is
    # enforced where entries arrive; the age cap needs a clock, and this
    # tick is the clock the rest of this function already runs on. An
    # answer older than the longest mute that can exist is not waiting
    # for a ban to lift — delivering it later as news would be worse than
    # the WARNING `expire()` logs for it.
    held.HELD.expire()
    # And the files Claude sent that waited a day (aipager/bot/outbox.py).
    _sweep_outbox()
    # And the DURABLE state (8.28). Same shape as the registry's own
    # `save_if_dirty()` two lines up the call stack in `_loop`: a dirty
    # flag plus this 2 s tick IS the debounce, so there is no timer of our
    # own to leak. A no-op unless something material changed, and it never
    # raises — a full disk must not be able to stop the session scan.
    flood_state.save_if_dirty()


def dialog_interrupted_in_terminal(sess: TrackedSession, now_mono: float,
                                   now_wall: float) -> bool:
    """True when a session waiting on a dialog (INTERACTIVE) has Claude
    Code's interrupt marker as the newest turn entry of its transcript,
    written since the dialog appeared (roadmap 8.113). Choosing "No" in
    Claude Code's own permission dialog at the terminal, or Escape on a
    question there, ends the turn like an interrupt: it writes the marker
    and fires no hook. An Allow writes the tool's result (or nothing yet,
    the transcript being lazy), a dialog still open writes nothing: neither
    reads as the marker (:func:`aipager.transcript.turn_tail_since`).

    False once aipager itself has typed into the terminal since the dialog
    appeared (:func:`aipager.dtach.inject.last_write_at`): its own typed
    Deny and /stop's Escapes write the same marker while the session is
    still INTERACTIVE, and the path that typed them ends the turn itself.
    A terminal answer given after such keys (a question's Escape after a
    Telegram tap moved its cursor) is left to the INTERACTIVE watchdog.

    *now_mono* / *now_wall*: the monotonic and the wall clock of this tick,
    to place the dialog's start (a monotonic stamp) on the transcript's
    wall-clock time line. Never raises."""
    if (sess.status != Status.INTERACTIVE or not sess.transcript_path
            or sess.interactive_entered_at <= 0):
        return False
    if dtach_inject.last_write_at(sess.name) >= sess.interactive_entered_at:
        return False
    since = now_wall - max(0.0, now_mono - sess.interactive_entered_at)
    return turn_tail_since(sess.transcript_path, since) == "marker"


def _open_prompt_tool(sess: TrackedSession, perm=None) -> tuple[str, str]:
    """``(tool_use_id, tool_name)`` of the dialog *sess* waits on, from
    *perm* when given (one of several open prompts, roadmap 8.120), else
    the inline prompt or the separate message shown during this wait;
    ``("", "")`` when neither is known (a dialog left to the terminal)."""
    if perm is None:
        perm = sess.pending_permission
    if not isinstance(perm, dict):
        sent = sess.pending_prompt_msg
        cand = sent.get("perm") if isinstance(sent, dict) else None
        perm = (cand if isinstance(cand, dict)
                and (cand.get("wait_started_at") or 0) >= sess.interactive_entered_at
                else None)
    info = (perm or {}).get("tool_info")
    if not isinstance(info, dict):
        return "", ""
    tuid, name = info.get("tool_use_id"), info.get("name")
    return (tuid if isinstance(tuid, str) else "",
            name if isinstance(name, str) else "")


def dialog_closed_in_transcript(sess: TrackedSession, now_mono: float,
                                now_wall: float, perm=None) -> bool:
    """True when the transcript of a session waiting on a dialog shows
    the dialog is over: its tool's result, or any turn entry written since
    it appeared other than its own pending call
    (:func:`aipager.transcript.saved_prompt_evidence`, the check a restart
    makes of a saved prompt). False when nothing contradicts the dialog,
    when the transcript cannot be read, and when the wait's start is not
    known: a dialog is never taken as closed without evidence.

    *now_mono* / *now_wall*: this tick's clocks, to place the wait's start
    (a monotonic stamp) on the transcript's wall-clock time line. *perm*:
    one of Claude's own open prompts (roadmap 8.120), read from when it
    was shown; the parent's transcript says nothing about an agent's."""
    if not sess.transcript_path or sess.interactive_entered_at <= 0:
        return False
    start = sess.interactive_entered_at
    if isinstance(perm, dict):
        start = max(start, perm.get("wait_started_at") or 0.0)
    since = now_wall - max(0.0, now_mono - start)
    tool_use_id, tool_name = _open_prompt_tool(sess, perm)
    evidence = saved_prompt_evidence(sess.transcript_path, since,
                                     tool_use_id, tool_name)
    return evidence in ("answered", "moved_on")


def kill_unconfirmed(sess, now: float) -> bool:
    """Whether the window aipager's kill chord armed for its wake
    (``kill_expected_until``, monotonic) ran out with no wake: the
    one-shot is consumed (zeroed) by the wake. Pure."""
    until = sess.kill_expected_until
    return bool(until) and now >= until


class SessionMonitor:
    """Periodically discovers dtach sessions and marks dead ones GONE."""

    def __init__(self, registry: SessionRegistry, notify_fn):
        self.registry = registry
        self.notify_fn = notify_fn
        self._task: asyncio.Task | None = None
        self.on_sessions_changed = None  # optional async callback
        # The live (non-GONE) session names the previous scan ended with:
        # what a change is measured from until the first refresh stamps
        # `registry.refreshed_live`. None before the first scan.
        self._last_live: set[str] | None = None
        # Optional async callback run at the end of every scan: the pinned
        # "needs you" bar's refresh (8.31). It re-renders from state and
        # sends nothing unless what the bar shows changed, so this tick is
        # what notices a transition no call site announces — a status set
        # by a hook, a flood regime entered or left.
        self.on_tick = None
        # Optional async callback run after every scan, BEFORE `on_tick`:
        # the flood-mute catch-up that is not per session (8.17c) — today
        # the main keyboard a mute refused. After the scan because the
        # scan is what flushes held answers and owed cards, and the
        # keyboard goes after both.
        self.on_mute_catchup = None
        # Session names whose busy card was suppressed on the PREVIOUS
        # tick, so `card_suppression_transition` can emit exactly one INFO
        # when suppression starts and one when it lifts (8.29 R7) rather
        # than 1,800 an hour at a 2 s tick.
        self._cards_suppressed_seen: set = set()
        # Roadmap 8.113: per session waiting on a dialog, the (dialog start,
        # transcript mtime, size) last read, so the transcript is read
        # again only after it changed.
        self._dialog_seen: dict[str, tuple[float, int, int]] = {}
        # The same for the INTERACTIVE watchdog's evidence check.
        self._dialog_checked: dict[str, tuple] = {}
        # Claude Code background jobs (check_parked): Claude's session
        # registry as read for the scan in progress (shared by every
        # session's own check in one tick, and by nothing else: the public
        # check_parked always reads afresh; None outside a scan), the link file last
        # written per session, the pid map last written, and the parks
        # noticed since the last scan, whose event is still to be sent.
        self._tick_registry: list[dict] | None = None
        #: When the scan's registry read started (``time.monotonic()``).
        self._tick_read_at = 0.0
        self._links_written: dict[str, dict] = {}
        self._pidmap_written: dict | None = None
        self._new_parks: dict[str, str] = {}
        #: session name -> the one-shot look at a terminal dialog's card
        #: when it falls due (roadmap 8.127), and the opening it is for.
        self._card_checks: dict[str, tuple[int, asyncio.Task]] = {}
        #: (session name, tool_use_id, refuse_at) -> the one-shot look at a
        #: risky-removal prompt's result at its refusal time (roadmap 8.128).
        self._rm_checks: dict[tuple[str, str, float], asyncio.Task] = {}
        #: session name -> tool_use_id -> the transcript (mtime, size) last
        #: read for that prompt's result: read again only after a change.
        self._rm_read: dict[str, dict[str, tuple[int, int]]] = {}
        #: The same for the look that may disprove a prompt's clock
        #: (roadmap 8.152): one read per transcript change once due.
        self._rm_disproof_read: dict[str, dict[str, tuple[int, int]]] = {}
        # One deprecation line per process, emitted here rather than at
        # import: `logging.basicConfig` runs in cli/daemon.py's start
        # command, so a message logged while this module is being imported
        # would miss the journal's formatting entirely.
        global _silence_deprecation_logged
        if _SILENCE_DEPRECATED_ENV and not _silence_deprecation_logged:
            _silence_deprecation_logged = True
            log.warning(
                "%s is deprecated — it now sets the subagent SILENCE window "
                "(%.0fs), not an age limit. Rename it to %s.",
                _SILENCE_DEPRECATED_ENV, SUBAGENT_SILENCE_SECONDS,
                _SILENCE_ENV,
            )

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("Session monitor started (every %.1fs)", PANE_POLL_INTERVAL)

    async def _loop(self) -> None:
        while True:
            await self.tick()
            await _loop_sleep(PANE_POLL_INTERVAL)

    def _dialog_transcript_changed(self, name: str, sess: TrackedSession) -> bool:
        """Whether the transcript of a session waiting on a dialog changed
        since this monitor last read it for this dialog (roadmap 8.113):
        the transcript is read only when it did, so a long wait costs one
        stat a tick. The size is part of it: a file's time is coarse (two
        appends a few ms apart can share it), but an append always grows
        the file."""
        try:
            st = os.stat(sess.transcript_path) if sess.transcript_path else None
        except OSError:
            st = None
        if st is None:
            return False
        key = (sess.interactive_entered_at, st.st_mtime_ns, st.st_size)
        if self._dialog_seen.get(name) == key:
            return False
        self._dialog_seen[name] = key
        return True

    async def _interactive_watchdog(self, name: str, sess: TrackedSession,
                                    now: float) -> None:
        """INTERACTIVE watchdog (item 2.2): demotes only on the
        transcript's evidence that the dialog is over, read again only when
        the transcript changed since this wait's last reading. Quiet time
        counts Claude's own hooks and the open prompts' owners' only
        (roadmap 8.120). With several prompts open the parent's transcript
        closes only Claude's own: it says nothing about an agent's dialog,
        which its own hooks, a tap, /stop or the turn's end close."""
        baseline = _interactive_quiet_since(sess)
        if not (baseline and (now - baseline) > INTERACTIVE_TIMEOUT_SECONDS):
            return
        prompts = sess.open_prompts()
        own = [p for p in prompts if not _prompt_agent(p)]
        if len(prompts) > 1:
            if not own or not self._dialog_evidence_changed(name, sess):
                return
            wall = time.time()
            ended = [p for p in own if dialog_closed_in_transcript(sess, now, wall, p)]
            if not ended:
                return
            report_store.record_counter("interactive_demoted")
            closed, inline_closed = sess.close_prompts(
                lambda p: any(p is e for e in ended), mark=False)
            left = len(sess.open_prompts())
            log.warning("[%s] INTERACTIVE > %d min with no hooks, and the "
                        "transcript shows %d of Claude's prompts are over - "
                        "%d still open", sess.label,
                        int(INTERACTIVE_TIMEOUT_SECONDS / 60), closed, left)
            self.registry.mark_dirty()
            if not left:
                self.registry.transition(name, Status.BUSY)
            else:
                await self.notify_fn(sess, "prompt_closed",
                                     {"inline_closed": inline_closed})
            return
        if prompts and not own:
            # An agent's prompt: the parent's transcript proves nothing.
            return
        if (self._dialog_evidence_changed(name, sess)
                and dialog_closed_in_transcript(sess, now, time.time())):
            log.warning(
                "[%s] INTERACTIVE > %d min with no hooks, and the "
                "transcript shows the dialog is over — demoting to BUSY",
                sess.label, int(INTERACTIVE_TIMEOUT_SECONDS / 60),
            )
            report_store.record_counter("interactive_demoted")
            sess.pending_permission = None
            self.registry.transition(name, Status.BUSY)
            self.registry.mark_dirty()

    def _dialog_evidence_changed(self, name: str, sess: TrackedSession) -> bool:
        """:meth:`_dialog_transcript_changed` for the INTERACTIVE
        watchdog, with its own record: a long wait reads the transcript only
        after it changed. A transcript that does not exist (yet) counts as
        changed, so its absence is asked about, and answers "no evidence"."""
        try:
            st = os.stat(sess.transcript_path) if sess.transcript_path else None
        except OSError:
            st = None
        key = ((sess.interactive_entered_at, st.st_mtime_ns, st.st_size)
               if st is not None else (sess.interactive_entered_at, None, None))
        if self._dialog_checked.get(name) == key:
            return False
        self._dialog_checked[name] = key
        return True

    # -- Claude Code background jobs ------------------------------------

    def check_parked(self, sess: TrackedSession) -> bool:
        """Whether *sess* is parked on a Claude Code background job, read
        again from Claude's files now; sets or clears ``parked_job`` (and
        the link file) to match. Synchronous: the message gate and the hook
        receiver call it too. Never raises.

        The session's own claude's registry entry decides when it is found
        (``claude_jobs.find_client``): ``parkedJobId`` parks, no
        ``parkedJobId`` clears. Whether the job still runs never clears a
        park (Claude Code leaves ``parkedJobId`` set after the job stops).
        Only when no entry is found does the transcript decide: a
        conversation whose last record is ``continued-in`` parks on the
        job its new id starts with. Nothing readable changes nothing.

        Always reads the registry afresh, also while a scan is in progress:
        the scan awaits Telegram sends, and a park newer than the scan's
        own read must still hold the message that called this."""
        read_at = time.monotonic()
        return self._check_parked_with(sess, claude_jobs.read_registry(), read_at)

    async def check_parked_async(self, sess: TrackedSession) -> bool:
        """:meth:`check_parked` with its reads made off the event loop
        (the registry, and the transcript lookups of its fallback), then
        decided on the loop. What the message gate and the hook receiver
        call (wired by cli/daemon.py). Never raises."""
        # Stamped before the thread hop: a park the scan (or another
        # check) records while this reads is newer than this read, and
        # this read may not undo it (:meth:`_check_parked`).
        read_at = time.monotonic()
        try:
            entries = await claude_jobs.read_registry_async()
            await claude_jobs.off_loop(claude_jobs.prefetch_transcripts,
                                       self._fallback_ids(sess))
        except Exception:
            log.warning("[%s] parked check failed", sess.label, exc_info=True)
            return bool(sess.parked_job)
        return self._check_parked_with(sess, entries, read_at)

    @staticmethod
    def _fallback_ids(sess: TrackedSession) -> list[str]:
        """The conversations whose transcripts the transcript fallback of
        :meth:`_check_parked` reads."""
        return [sess.claude_session_id, sess.parked_from_session_id]

    def _check_parked_with(self, sess: TrackedSession, entries,
                           read_at: float) -> bool:
        """:meth:`check_parked` against *entries* (a registry read already
        made: the scan's own), whose read started at *read_at*
        (``time.monotonic()``). Never raises."""
        try:
            return self._check_parked(sess, entries, read_at)
        except Exception:
            log.warning("[%s] parked check failed", sess.label, exc_info=True)
            return bool(sess.parked_job)

    def _check_parked(self, sess: TrackedSession, entries, read_at: float) -> bool:
        # Only while the relaunch itself runs (the old client is being
        # stopped and killed): the restart's quiet window after it hushes
        # the old process's late SessionEnd, and must not hide a new ←
        # of the relaunched claude. The old client's entry, left behind
        # by a kill, is never its client (find_client takes live
        # processes only).
        if sess.status == Status.GONE or sess.relaunch_in_flight:
            return bool(sess.parked_job)
        if read_at < sess.park_changed_at:
            # The park changed after this read started (the message gate or
            # the receiver saw a fresh ←, or a bring-back ended it, while the
            # scan awaited a Telegram send): what was read is older than the
            # park, so it decides nothing. The next read does.
            log.debug("[%s] parked check skipped: the read is older than the park",
                      sess.label)
            return bool(sess.parked_job)
        client = claude_jobs.find_client(sess, entries)
        if client is not None:
            job = client.get("parkedJobId") or ""
            if claude_jobs.valid_job(job):
                self._park(sess, job, self._job_session_id(sess, job, entries, client),
                           source="registry")
            else:
                self._unpark(sess)
            return bool(sess.parked_job)
        if sess.parked_job and sess.parked_source == "transcript":
            # Parked by the transcript alone: it decides the end too. Its
            # conversation from before the move (claude_session_id is the
            # job's by now) no longer ending with `continued-in` means the
            # old copy was resumed in the terminal: not parked any more.
            from_sid = sess.parked_from_session_id
            path = (claude_jobs.find_transcript(from_sid, cache_misses=True)
                    if from_sid else None)
            if path is None:
                return True             # nothing readable: nothing changes
            target = claude_jobs.transcript_continued_in(path)
            if target and claude_jobs.valid_job(target[:8]):
                self._park(sess, target[:8], target, source="transcript",
                           from_sid=from_sid)
            else:
                self._unpark(sess)
            return bool(sess.parked_job)
        # Not parked, or parked by the registry (sticky, Fact 7: only the
        # client's entry ends it): the transcript can park, never unpark.
        from_sid = sess.claude_session_id
        path = claude_jobs.find_transcript(from_sid, cache_misses=True)
        target = claude_jobs.transcript_continued_in(path) if path is not None else ""
        if self._move_handled(sess, from_sid, path, target):
            return bool(sess.parked_job)
        if target and claude_jobs.valid_job(target[:8]):
            self._park(sess, target[:8], target, source="transcript", from_sid=from_sid)
        return bool(sess.parked_job)

    def _move_handled(self, sess: TrackedSession, from_sid: str, path,
                      target: str) -> bool:
        """Whether *target*, the ``continued-in`` that conversation
        *from_sid*'s transcript ends with, is the move aipager already dealt
        with by launching that conversation on purpose
        (``TrackedSession.moved_handled_sid``): the relaunched claude has not
        written its registry entry yet, and the job named there was stopped.
        That record parks nothing. The note is forgotten once that
        transcript ends otherwise (a prompt was typed, or it moved again):
        a later ``←`` parks as ever."""
        if not sess.moved_handled_sid or from_sid != sess.moved_handled_sid:
            return False
        if target and sess.moved_handled_job and target.startswith(sess.moved_handled_job):
            log.debug("[%s] transcript still ends moved into job %s, already handled",
                      sess.label, sess.moved_handled_job)
            return True
        if path is not None:
            sess.note_moved_handled("", "")
            self.registry.mark_dirty()
        return False

    @staticmethod
    def _job_session_id(sess: TrackedSession, job: str, entries, client) -> str:
        """The conversation job *job* runs: its registry entry's
        ``sessionId``, else where the client's conversation moved when that
        id starts with the job, else what was known already."""
        entry = claude_jobs.job_entry(job, entries)
        if entry is not None and claude_jobs.valid_session_id(entry.get("sessionId")):
            return entry["sessionId"]
        for sid in (client.get("sessionId"), sess.claude_session_id):
            path = claude_jobs.find_transcript(sid, cache_misses=True) if sid else None
            target = claude_jobs.transcript_continued_in(path) if path is not None else ""
            if target.startswith(job):
                return target
        return sess.parked_job_session_id if sess.parked_job == job else ""

    def _park(self, sess: TrackedSession, job: str, job_sid: str, *,
              source: str, from_sid: str = "") -> None:
        if sess.parked_job != job:
            log.info("[%s] parked on Claude Code background job %s (from the %s)",
                     sess.label, job, source)
            if sess.parked_job:
                sess.forget_park()      # another job: a park of its own
            sess.parked_job = job
            sess.parked_job_session_id = job_sid
            sess.parked_since = time.time()
            sess.park_changed_at = time.monotonic()
            sess.park_told.clear()
            self._new_parks[sess.name] = job
            self.registry.mark_dirty()
        elif job_sid and job_sid != sess.parked_job_session_id:
            sess.parked_job_session_id = job_sid
            self.registry.mark_dirty()
        if (sess.parked_source, sess.parked_from_session_id) != (source, from_sid):
            sess.parked_source = source
            sess.parked_from_session_id = from_sid
            self.registry.mark_dirty()
        self._write_link(sess)

    def _unpark(self, sess: TrackedSession) -> None:
        if not sess.parked_job:
            return
        log.info("[%s] no longer parked on background job %s", sess.label,
                 sess.parked_job)
        bg_jobs.remove_own(bg_jobs.link_path(sess.parked_job))
        self._links_written.pop(sess.name, None)
        self._new_parks.pop(sess.name, None)
        sess.forget_park()
        self.registry.mark_dirty()

    def _write_link(self, sess: TrackedSession) -> None:
        """The link file of the job *sess* is parked on (the hook reads it
        to count that job's workers for the session), written when what it
        says changed. An ``ended`` mark the receiver put there is kept."""
        job = sess.parked_job
        want = {"job": job, "session": sess.name,
                "job_session_id": sess.parked_job_session_id,
                "client_pid": sess.claude_pid or None,
                "at": sess.parked_since}
        if self._links_written.get(sess.name) == want:
            return
        path = bg_jobs.link_path(job)
        old = bg_jobs.read_own_json(path)
        ended = bool(old and old.get("job") == job and old.get("session") == sess.name
                     and old.get("ended") is True)
        if bg_jobs.write_own_json(path, {**want, "ended": ended}):
            self._links_written[sess.name] = want

    def _sync_pidmap(self) -> None:
        """The pid map (pid of each live session's own claude -> session),
        rewritten when it changed: a worker's hook uses it when it runs
        before the link file exists."""
        pids = {}
        for name, sess in self.registry.all_sessions().items():
            if sess.status != Status.GONE and sess.claude_pid > 0:
                pids[str(sess.claude_pid)] = {
                    "session": name, "start": sess.claude_pid_start or None}
        want = {"pids": pids}
        if want == self._pidmap_written:
            return
        path = bg_jobs.pidmap_path()
        if not pids and self._pidmap_written is None and not path.exists():
            self._pidmap_written = want
            return
        if bg_jobs.write_own_json(path, want):
            self._pidmap_written = want

    async def _check_parked_tick(self, name: str, sess: TrackedSession) -> None:
        """The scan's parked check of one session, and the ``bg_parked``
        event of a park noticed since the last scan (by this check or by
        the message gate or the receiver in between)."""
        if sess.status != Status.GONE and not sess.relaunch_in_flight:
            # The scan's registry read: only this call uses it. Read on
            # the loop, once per scan (the files parse once per change),
            # and the transcript fallback's misses are cached: the scan
            # runs no user's request, and a worker-thread hop here would
            # let other work in between the scan's own steps.
            self._check_parked_with(sess, self._tick_registry, self._tick_read_at)
        job = self._new_parks.pop(name, None)
        if job and sess.parked_job == job:
            try:
                await self.notify_fn(sess, "bg_parked", {"job": job})
            except Exception:
                log.warning("Failed to notify bg_parked for %s", name,
                            exc_info=True)
        if (sess.bg_release_pending and sess.status == Status.IDLE
                and not sess.parked_job and not sess.relaunch_in_flight
                and time.monotonic() - sess.bg_released_at
                >= claude_jobs.RELEASE_BACKSTOP):
            # A bring-back's held messages, when the relaunched claude's
            # first SessionStart never said it was up. Only while IDLE: a
            # turn typed in the terminal meanwhile (after a daemon restart,
            # say) would take the message mid-turn; its end releases it.
            try:
                await self.notify_fn(sess, "bg_release", {})
            except Exception:
                log.warning("Failed to notify bg_release for %s", name,
                            exc_info=True)

    async def _check_term_dialog_tick(self, name: str, sess: TrackedSession) -> None:
        """The scan's look for a dialog open in the session's terminal that
        no hook tells of (roadmap 8.127, aipager/term_dialog.py): Claude
        Code's record of the session's own claude, found in the scan's
        registry read (no file read of its own; the transcript's tail once
        per new opening), taken into the session's state; the bot is told
        of an opening, a close, a card that is due and cards to close, and
        a settled close releases what was held for it."""
        change = None
        try:
            # An ended session, or one being relaunched, has no dialog to
            # find (and its old claude's record is not its own any more).
            if sess.status != Status.GONE and not sess.relaunch_in_flight:
                entries = self._tick_registry
                entry = (claude_jobs.find_client(sess, entries)
                         if entries is not None else None)
                complete = entries is not None and getattr(entries, "complete", True)
                change = term_dialog.observe(
                    sess, entry, complete=complete,
                    now_mono=term_dialog._now_mono(), now_wall=term_dialog._now_wall(),
                    last_exchange_wall=lambda: last_exchange_at(sess.transcript_path),
                    settle=True)
        except Exception:
            log.warning("[%s] terminal dialog check failed", sess.label, exc_info=True)
            return
        if change == "settled":
            hold = sess.dialog_hold
            if hold is not None and hold.pop("settling", None):
                hold["closed"] = "answered"
                log.info("[%s] terminal dialog settled: releasing %d held message(s)",
                         sess.label, len(sess.pending_queue))
                try:
                    await self.notify_fn(sess, "dialog_hold_settle", {})
                except Exception:
                    log.warning("Failed to settle the dialog hold for %s", name,
                                exc_info=True)
            return
        if (change is not None or sess.term_dialog_closing
                or term_dialog.card_due(sess, term_dialog._now_wall())):
            try:
                await self.notify_fn(sess, "term_dialog", {"change": change or "due"})
            except Exception:
                log.warning("Failed to notify term_dialog for %s", name, exc_info=True)
        self._schedule_card_check(name, sess)

    def _schedule_card_check(self, name: str, sess: TrackedSession) -> None:
        """When the open opening's card falls due before the next scan
        (:func:`term_dialog.card_due_in`), one look then, not at the scan
        after (roadmap 8.127): live, the opening is first seen about 1.2 s
        after the draw and its card falls due a moment later."""
        delay = term_dialog.card_due_in(sess, term_dialog._now_wall())
        cur = sess.term_dialog
        if delay is None or cur is None:
            return
        epoch = cur["epoch"]
        held = self._card_checks.get(name)
        if held is not None and held[0] == epoch and not held[1].done():
            return
        task = asyncio.get_running_loop().create_task(
            self._card_due_later(name, sess, epoch, delay))
        self._card_checks[name] = (epoch, task)

    async def _card_due_later(self, name: str, sess: TrackedSession, epoch: int,
                              delay: float) -> None:
        try:
            await _card_due_sleep(delay)
            # The bot sends it only if it is due by then (``on_opened``).
            await self.notify_fn(sess, "term_dialog", {"change": "due"})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Failed to send a terminal dialog's card for %s", name,
                        exc_info=True)
        finally:
            held = self._card_checks.get(name)
            if held is not None and held[0] == epoch:
                self._card_checks.pop(name, None)

    # -- Claude Code's risky-removal countdown (roadmap 8.128) -----------

    @staticmethod
    def _rm_prompts(sess: TrackedSession) -> list[dict]:
        """Claude's own open prompts that carry the risky-removal clock and
        the id of the tool call they ask about."""
        out = []
        for perm in sess.open_prompts():
            info = perm.get("tool_info") if isinstance(perm, dict) else None
            if not isinstance(info, dict) or info.get("agent_id"):
                continue
            tuid = info.get("tool_use_id")
            if (rm_countdown.clock_of(info) is not None
                    and isinstance(tuid, str) and tuid):
                out.append(perm)
        return out

    def _rm_transcript_changed(self, name: str, sess: TrackedSession,
                               tool_use_id: str) -> bool:
        """Whether the transcript changed since this prompt's result was
        last looked for (a long wait costs one stat a tick)."""
        try:
            st = os.stat(sess.transcript_path) if sess.transcript_path else None
        except OSError:
            st = None
        if st is None:
            return False
        key = (st.st_mtime_ns, st.st_size)
        seen = self._rm_read.setdefault(name, {})
        if seen.get(tool_use_id) == key:
            return False
        seen[tool_use_id] = key
        return True

    async def _check_rm_prompt(self, name: str, sess: TrackedSession, perm: dict,
                               *, force: bool = False) -> None:
        """Close risky-removal prompt *perm* when its refusal window is open
        and the transcript shows how its tool call came back: an error is
        Claude Code's own refusal (nobody answered in time), a result that
        is not one means it was answered in the terminal and ran. Nothing
        else closes it: silence proves nothing (the transcript is written
        lazily). *force*: read even if the transcript did not change."""
        if sess.status != Status.INTERACTIVE:
            return
        if not any(p is perm for p in sess.open_prompts()):
            return
        if not rm_countdown.result_check_open(perm, rm_countdown._now_wall()):
            return
        # A "No" at the terminal writes the same error result plus the
        # interrupt marker: that is the person's answer, never Claude
        # Code's own refusal. The scan checks it first; the timer runs
        # on its own, so the check is here for both.
        if dialog_interrupted_in_terminal(sess, time.monotonic(), time.time()):
            return
        info = perm["tool_info"]
        tuid = info["tool_use_id"]
        changed = self._rm_transcript_changed(name, sess, tuid)
        if not (changed or force):
            return
        clock = rm_countdown.clock_of(info)
        if clock is None:
            return
        found = tool_call_result(sess.transcript_path, tuid,
                                 str(info.get("name") or ""), clock["command_sha"])
        if found == "error":
            outcome = "refused"
        elif found == "ok":
            outcome = "ran"
        else:
            return
        log.info("[%s] risky-removal prompt's tool call came back (%s)",
                 sess.label, outcome)
        await self.notify_fn(sess, "rm_refused", {
            "perms": [perm], "outcome": outcome, "close": True})

    async def _check_rm_refusal_tick(self, name: str, sess: TrackedSession) -> None:
        """The scan's look at the risky-removal prompts of a session
        waiting on a dialog: each is checked, and its refusal-time look is
        scheduled. Never raises."""
        try:
            for perm in self._rm_prompts(sess):
                self._schedule_rm_check(name, sess, perm)
                await self._check_rm_prompt(name, sess, perm)
                await self._check_rm_disproof(name, sess, perm)
        except Exception:  # noqa: BLE001 - the scan goes on
            log.warning("Failed to check a risky-removal prompt for %s", name,
                        exc_info=True)

    async def _check_rm_disproof(self, name: str, sess: TrackedSession,
                                 perm: dict) -> None:
        """Disprove risky-removal prompt *perm*'s clock (roadmap 8.152) when
        the shown time plus ``rm_countdown.DISPROVE_AFTER_SECONDS`` has
        passed, the prompt is still open, and the readable transcript
        holds no result for its call (no refusal came: an ask rule made
        Claude Code show its plain ask, or a server set a longer
        countdown). The clock line goes and taps are no longer blocked;
        the monitor still closes it as refused if the refusal comes later.
        Read once per transcript change. Silence of an unreadable or
        missing file proves nothing. Only the scan calls it, for the open
        prompts of a session waiting on a dialog (a terminal answer
        writes the call's result, so it never reads as no refusal)."""
        if not rm_countdown.disproof_due(perm, rm_countdown._now_wall()):
            return
        info = perm["tool_info"]
        tuid = info["tool_use_id"]
        try:
            st = os.stat(sess.transcript_path) if sess.transcript_path else None
        except OSError:
            st = None
        if st is None:
            return
        key = (st.st_mtime_ns, st.st_size)
        seen = self._rm_disproof_read.setdefault(name, {})
        if seen.get(tuid) == key:
            return
        seen[tuid] = key
        clock = rm_countdown.clock_of(info)
        if clock is None:
            return
        found = tool_call_result(sess.transcript_path, tuid,
                                 str(info.get("name") or ""), clock["command_sha"])
        if found != "absent":
            return  # a result is the refusal check's; anything else proves nothing
        if not rm_countdown.disprove(info):
            return
        log.info("[%s] risky-removal prompt's clock disproven: no refusal %.0f s "
                 "after the shown time", sess.label, rm_countdown.DISPROVE_AFTER_SECONDS)
        self.registry.mark_dirty()
        await self.notify_fn(sess, "rm_clock_disproven", {"perms": [perm]})

    def _schedule_rm_check(self, name: str, sess: TrackedSession, perm: dict) -> None:
        """One look at *perm*'s result at its refusal time plus
        ``CHECK_GRACE_SECONDS``, even if no scan comes then. Once per
        prompt; none when that time has passed (the scan looks then)."""
        due = rm_countdown.next_check_at(perm)
        if due is None:
            return
        delay = due - rm_countdown._now_wall()
        if delay <= 0:
            return
        key = (name, str(perm["tool_info"]["tool_use_id"]), due)
        held = self._rm_checks.get(key)
        if held is not None and not held.done():
            return
        self._rm_checks[key] = asyncio.get_running_loop().create_task(
            self._rm_due_later(name, sess, perm, key, delay))

    async def _rm_due_later(self, name: str, sess: TrackedSession, perm: dict,
                            key: tuple[str, str, float], delay: float) -> None:
        try:
            await _rm_due_sleep(delay)
            await self._check_rm_prompt(name, sess, perm, force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Failed to check a risky-removal prompt for %s", name,
                        exc_info=True)
        finally:
            self._rm_checks.pop(key, None)

    async def tick(self) -> None:
        """One pass of the loop: scan, save, the mute catch-up, the bar.

        The order is the 8.17c catch-up order: ``_scan`` flushes each
        session's held answers and then its owed card, and only then does
        ``on_mute_catchup`` send an owed keyboard. Never raises.
        """
        try:
            await self._scan()
            self.registry.save_if_dirty()
        except Exception:
            log.exception("Session monitor error")
        # Problem reports (8.112): the same dirty-flag-plus-tick debounce as
        # the registry; a no-op unless something was recorded. Never raises.
        report_store.save_if_dirty()
        if self.on_mute_catchup is not None:
            try:
                await self.on_mute_catchup()
            except Exception:
                log.warning("on_mute_catchup callback failed", exc_info=True)
        if self.on_tick is not None:
            try:
                await self.on_tick()
            except Exception:
                log.warning("on_tick callback failed", exc_info=True)

    def _exit_context(self, sess: TrackedSession) -> dict:
        """What the launch shell's exit record says about why *sess*
        vanished (``aipager.claude_launch.exit_record``): ``{"exit":
        {"code", "start", "end"}}`` when this launch left one, plus
        ``bg_holder`` for a claude that stopped right after starting while
        a Claude Code background job holds its conversation. Empty when
        there is no fresh record of ours (today's "crashed or killed").
        Never raises; logs the code."""
        try:
            record = claude_jobs.read_exit(sess.name, time.time())
        except Exception:
            log.debug("[%s] exit record unreadable", sess.name, exc_info=True)
            record = None
        if record is None:
            log.info("[%s] socket vanished: no exit record from claude", sess.name)
            return {}
        log.info("[%s] socket vanished: claude exit code %d after %ds",
                 sess.name, record["code"], record["end"] - record["start"])
        ctx: dict = {"exit": record}
        if claude_jobs.is_start_failure(record):
            held = self._held_conversation(sess)
            ctx["bg_holder"] = bool(held)
            if held:
                log.info("[%s] its conversation %s is held by a Claude Code "
                         "background job", sess.name, held)
                if not sess.claude_session_id:
                    # A resume clears the id before its launch (so a claude
                    # that dies at once is not resumed in a loop); the
                    # "Bring it back" button resumes this id, and the
                    # resume stops the job first.
                    sess.claude_session_id = held
                    self.registry.mark_dirty()
        return ctx

    @staticmethod
    def _held_conversation(sess: TrackedSession) -> str:
        """The conversation of *sess* a live Claude Code background job
        holds (itself, or where it moved), or "": the session's id, else
        the id its transcript is named after (a resume clears the id
        before its launch)."""
        try:
            stem = os.path.basename(sess.transcript_path or "")
            ids = [sess.claude_session_id, stem.removesuffix(".jsonl")]
            entries = claude_jobs.read_registry()
            if entries is None:
                return ""
            for sid in dict.fromkeys(i for i in ids if claude_jobs.valid_session_id(i)):
                moved = claude_jobs.follow_continued(sid, entries)
                for candidate in dict.fromkeys((sid, moved)):
                    if claude_jobs.live_holder(candidate, entries) is not None:
                        return sid
        except Exception:
            log.debug("[%s] background holder check failed", sess.name,
                      exc_info=True)
        return ""

    async def _scan(self) -> None:
        _sweep_flood_backoff()
        sessions = await dtach_inject.list_sessions()
        old_names = set(self.registry.all_sessions().keys())
        old_live = (self._last_live if self._last_live is not None
                    else self.registry.live_names())

        # Mark disappeared sessions as GONE and notify
        for name, sess in list(self.registry.all_sessions().items()):
            if name not in sessions and sess.status != Status.GONE:
                if sess.is_restarting():
                    # Deliberate kill-and-relaunch (`/perms`): the socket is
                    # expected to be missing for the moment between the two.
                    # Marking it GONE here would race the relaunch and alarm
                    # the user about a session that is coming right back.
                    continue
                # The GONE transition deletes the notes dir: capture what
                # was still outstanding for the not-delivered reaction.
                try:
                    from aipager.policy_snapshot import list_outstanding_notes
                    gone_notes = [
                        {"msg_id": n.get("msg_id"), "chat_id": n.get("chat_id"),
                         "raw_text": n.get("raw_text", "")}
                        for n in list_outstanding_notes(name)]
                except Exception:
                    gone_notes = []
                self.registry.transition(name, Status.GONE)
                # Stamp the GONE moment + capture a last-message preview
                # so /resume can show "where you left off" without
                # re-reading the transcript at picker time.
                sess.gone_at = time.time()
                try:
                    sess.last_assistant_preview = last_assistant_preview(
                        sess.transcript_path
                    )
                except Exception:
                    log.debug("preview extraction failed for %s", name,
                              exc_info=True)
                self.registry.mark_dirty()
                end_ctx = {"source": "disappeared", "notes": gone_notes}
                end_ctx.update(self._exit_context(sess))
                try:
                    await self.notify_fn(sess, "session_end", end_ctx)
                except Exception:
                    log.warning("Failed to notify session_end for %s", name)

        # Discover new sessions and recover GONE sessions whose socket reappeared
        for name in sessions:
            sess = self.registry.get_or_create(name)
            if sess.status in (Status.UNKNOWN, Status.GONE):
                # Coming back from GONE means a resume worked (or the
                # user rebooted dtach manually). Clear the GONE-only
                # fields so this entry no longer surfaces in the picker.
                if sess.status == Status.GONE:
                    sess.gone_at = None
                self.registry.transition(name, Status.IDLE)

        # Notify if the session list changed (for bot command/keyboard
        # updates): a name added or removed in this scan, or live sessions
        # (the / menu and the keyboard list those only) other than the
        # ones the latest refresh rendered: one that came back or ended
        # under the same name, here or by a hook since (a SessionEnd marks
        # it GONE before its socket goes). Measured from the refresh, not
        # the previous scan: a /new, /kill or /resume refreshed by itself
        # and is not refreshed again (single-scope sends a keyboard each
        # time), a `/clear` (SessionEnd, then the process goes on) is no
        # change, and a refresh run inside that window is corrected here.
        # Not when nothing changed: this runs every scan.
        new_names = set(self.registry.all_sessions().keys())
        new_live = self.registry.live_names()
        self._last_live = new_live
        rendered = self.registry.refreshed_live
        changed = (new_names != old_names
                   or new_live != (rendered if rendered is not None else old_live))
        if changed and self.on_sessions_changed:
            try:
                await self.on_sessions_changed()
            except Exception:
                log.warning("on_sessions_changed callback failed", exc_info=True)

        # Age out sessions that ended more than GONE_SESSION_MAX_AGE_DAYS
        # ago (roadmap 8.12); the loop's save_if_dirty persists the drop.
        self.registry.expire_gone()

        # Check for stale BUSY sessions (no hook activity for too long).
        # Also: auto-demote INTERACTIVE sessions whose permission prompt
        # has been hanging for too long (claude crashed mid-prompt), and
        # garbage-collect subagent entries whose Stop hook never arrived.
        now = time.monotonic()

        # Compact-card deadline sweep (design.md Decisions 3/5) — one pass
        # per tick, deliberately NOT folded into the per-status loop below
        # since it is the one watchdog that is NOT gated on
        # status == Status.BUSY (see expired_compacting_sessions'
        # docstring for why that gate is what let the reported bug hide
        # from every existing watchdog).
        #
        # Sessions swept here are skipped by the busy-card watchdog below
        # for the rest of this scan: resolving the compaction card resumes
        # the busy animation itself (notify.py's compact_timeout handler),
        # and the fresh task deserves its first tick before it is judged.
        compact_swept: set[str] = set()
        for expired_name in expired_compacting_sessions(
            self.registry.all_sessions(), now,
        ):
            expired_sess = self.registry.get(expired_name)
            if expired_sess is None:
                continue
            compact_swept.add(expired_name)
            started = expired_sess.compacting_started_at()
            elapsed = (now - started) if started is not None else 0.0
            try:
                await self.notify_fn(
                    expired_sess, "compact_timeout", {"elapsed_seconds": elapsed},
                )
            except Exception:
                log.warning("Failed to notify compact_timeout for %s", expired_name)

        # Claude Code background jobs: the registry once for this scan.
        self._tick_read_at = time.monotonic()
        self._tick_registry = claude_jobs.read_registry()
        try:
            await self._scan_sessions(now, compact_swept)
        finally:
            self._tick_registry = None
        try:
            self._sync_pidmap()
        except Exception:
            log.warning("pid map not written", exc_info=True)

    async def _scan_sessions(self, now: float, compact_swept: set[str]) -> None:
        """The per-session part of :meth:`_scan`."""
        for name, sess in self.registry.all_sessions().items():
            # A files turn on an idle session that never started (a typed
            # prompt that ran no turn): no file Claude sends can come from
            # it any more.
            if files_turn_unstarted(sess, now):
                log.info("[%s] files turn closed: no turn started from it",
                         sess.label)
                sess.files_turn_closed(sess.files_turn_closed_wall)
                self.registry.mark_dirty()
            # Roadmap 8.121 F5: aipager's kill chord was pressed, and no turn
            # of Claude Code's said the agents were stopped in time. Its
            # tables already dropped them; if the chord did nothing (a
            # rebound key, a changed release) they run on unseen: said once.
            if kill_unconfirmed(sess, now):
                sess.kill_expected_until = 0.0
                log.warning("[%s] Stop pressed the kill-agents chord, but no turn "
                            "said the background agents were stopped", sess.label)
                report_store.record_counter("kill_agents_unconfirmed")
            # Messages held while a dialog was open, and the dialog is not
            # open any more: the bot sends them on (answered) and deletes
            # its "answer it first" notice. Normally done at once by the
            # hook that closed the dialog; this is the backstop for a close
            # no hook reported (a tap, /stop, a turn ended in the terminal).
            if sess.dialog_hold is not None and not sess.any_dialog_open():
                try:
                    await self.notify_fn(sess, "dialog_hold_settle", {})
                except Exception:
                    log.warning("Failed to settle the dialog hold for %s",
                                name, exc_info=True)
            # Messages held while a typed /model checked its model, and the
            # check's time ran out with no statusline change (a refused
            # model, or one Claude Code calls by the same name).
            if sess.model_hold and not sess.model_check_running():
                try:
                    await self.notify_fn(sess, "model_hold_settle", {})
                except Exception:
                    log.warning("Failed to settle the model hold for %s",
                                name, exc_info=True)
            # Parked on a Claude Code background job: never typed into
            # (dtach/inject.py's probe reads the field this sets).
            await self._check_parked_tick(name, sess)
            # A dialog open in Claude Code's terminal that no hook tells of
            # (roadmap 8.127), from the same registry read.
            await self._check_term_dialog_tick(name, sess)
            # A dialog answered "No" at the terminal (roadmap 8.113): Claude
            # Code ends the turn like an interrupt and fires no hook, so
            # only the transcript tells. Checked before the watchdog below,
            # which would otherwise wait out its 5 minutes.
            if sess.status != Status.INTERACTIVE:
                self._dialog_seen.pop(name, None)
                self._dialog_checked.pop(name, None)
                self._rm_read.pop(name, None)
                self._rm_disproof_read.pop(name, None)
            elif (self._dialog_transcript_changed(name, sess)
                  and dialog_interrupted_in_terminal(sess, now, time.time())):
                log.info("[%s] the dialog was answered in the terminal: "
                         "interrupt marker in the transcript", sess.label)
                try:
                    await self.notify_fn(sess, "dialog_ended_in_terminal", {
                        "entered_at": sess.interactive_entered_at})
                except Exception:
                    log.warning("Failed to notify dialog_ended_in_terminal "
                                "for %s", name, exc_info=True)
                    # Read again next tick: the transcript may not change
                    # again after the interrupt.
                    self._dialog_seen.pop(name, None)
                continue

            # Claude Code's risky-removal prompt refused by itself when its
            # countdown ran out (roadmap 8.128): no hook says so, only the
            # tool call's result in the transcript.
            if sess.status == Status.INTERACTIVE:
                await self._check_rm_refusal_tick(name, sess)

            # INTERACTIVE watchdog (item 2.2): demotes only on the
            # transcript's evidence that the dialog is over. Read again only
            # when the transcript changed since this wait's last reading.
            if sess.status == Status.INTERACTIVE:
                await self._interactive_watchdog(name, sess, now)

            # "Prompt not taken" watchdog (roadmap 8.11). Cleared BEFORE
            # the notify so it fires exactly once per send, whatever the
            # handler does; the card watchdog is skipped for this scan
            # because the handler is about to settle the card itself.
            if prompt_not_taken(sess, now):
                report_store.record_counter("prompt_not_taken")
                sess.prompt_sent_at = 0.0
                try:
                    await self.notify_fn(sess, "prompt_not_taken", {
                        "grace": PROMPT_HOOK_GRACE_SECONDS,
                        "msg": sess.prompt_sent_msg,
                    })
                except Exception:
                    log.warning(
                        "Failed to notify prompt_not_taken for %s", name,
                        exc_info=True,
                    )
                continue

            # Busy-card staleness watchdog. Placed right after the
            # INTERACTIVE demotion above on purpose: that transition
            # never restarts the animation the prompt stopped, so the
            # very same scan is what brings the card back to life. The
            # bot performs the action (restart via _start_animation, or
            # one forced _edit_busy_rich — lock, dedupe and rate stamps
            # included) so Telegram's edit discipline is unchanged.
            # R7: while this chat is muted or in minimal mode the card
            # cannot animate, so restarting its animation achieves
            # nothing except two log lines every 20 s.
            suppressed = cards_suppressed(_session_chat_id(sess))
            transition = card_suppression_transition(
                self._cards_suppressed_seen, sess.name, suppressed)
            if transition == "start":
                log.info(
                    "[%s] busy-card updates suppressed — the chat is "
                    "flood-muted or in minimal mode; the card stays as last "
                    "rendered and the animation resumes by itself",
                    sess.label,
                )
            elif transition == "lift":
                log.info("[%s] busy-card updates resumed", sess.label)
            card_action = (
                None if name in compact_swept
                else busy_card_watchdog_action(
                    sess, now, suppressed=suppressed,
                    stale_after=card_stale_after(sess, now))
            )
            if card_action is not None:
                action_kind, since = card_action
                sess.card_watchdog_at = now
                report_store.record_counter(
                    "watchdog_restart" if action_kind == "restart" else "watchdog_refresh")
                if action_kind == "restart":
                    log.warning(
                        "[%s] no animate task while BUSY — restarting the "
                        "busy-card animation", sess.label,
                    )
                try:
                    await self.notify_fn(sess, "busy_card_watchdog", {
                        "action": action_kind, "since": since,
                    })
                except Exception:
                    log.warning(
                        "Failed to notify busy_card_watchdog for %s", name,
                        exc_info=True,
                    )

            # Orphaned busy card (roadmap 8.55/8.57): a card whose turn
            # has ended with nothing left to settle it.
            if orphan_card_due(sess, now):
                report_store.record_counter("orphan_card")
                try:
                    await self.notify_fn(sess, "orphan_card", {})
                except Exception:
                    log.warning("Failed to settle an orphaned card for %s",
                                name, exc_info=True)

            # Held answers (8.29 R6): an answer a flood mute refused is
            # kept, and this is what delivers it. Driven from the tick
            # that already runs rather than a timer or a busy-wait, so a
            # held answer lands within 2 s of the ban lifting. The guard
            # is cheap and false almost always — nothing is held unless a
            # chat has actually been banned.
            if _has_held_answer(sess):
                try:
                    await self.notify_fn(sess, "held_answer_flush", {})
                except Exception:
                    log.warning(
                        "Failed to flush held answers for %s", name,
                        exc_info=True,
                    )
            # The files Claude sent that wait for the same mute (or behind
            # a held answer): after the answers, on the same tick.
            if _has_pending_files(sess):
                try:
                    await self.notify_fn(sess, "turn_files_flush", {})
                except Exception:
                    log.warning("Failed to flush turn files for %s", name,
                                exc_info=True)

            # Roadmap 8.41: the running-agents set gets the same silence
            # bound as the job's table — a lost SubagentStop can pin
            # "still running" for at most one window — and each answer's
            # status block is looked at once its edit interval has passed
            # (edited only when what it shows changed).
            # A waiting agent (it ended its turn to wait for an agent of its
            # own) sends nothing while that agent works, so the silence
            # bounds below leave it alone; this one bounds it instead: once
            # none of its own agents runs, it must resume within
            # WAITING_PARENT_RESUME_SECONDS. A job left waiting only on it
            # then closes, like one whose agents went silent.
            was_job_open = sess.job_background_open()
            unresumed = sess.sweep_unresumed_parents(now)
            for aid in unresumed:
                log.info("[%s] waiting agent %s did not resume within %d s "
                         "after its own agents ended - no longer counted as "
                         "running", sess.label, aid,
                         int(WAITING_PARENT_RESUME_SECONDS))
            if unresumed:
                self.registry.mark_dirty()
            if (unresumed and was_job_open and sess.status == Status.IDLE
                    and not sess.job_work_running()):
                sess.job_interim_seen = False
                sess.job_continuation_active = False
                sess.job_grace_until = 0.0
                try:
                    await self.notify_fn(sess, "job_agents_lost", {})
                except Exception:
                    log.warning("Failed to notify job_agents_lost for %s",
                                name)
            for aid in sess.sweep_bg_agents(now, SUBAGENT_SILENCE_SECONDS):
                log.info("[%s] background agent %s silent for %d min — no "
                         "longer counted as running", sess.label, aid,
                         int(SUBAGENT_SILENCE_SECONDS / 60))
            # Background shells get an age bound instead (they send no
            # hooks to be silent by). A job waiting only on the shell that
            # aged out closes, with its own notice.
            was_job_open = sess.job_background_open()
            aged = sess.sweep_bg_shells(now, BG_SHELL_MAX_TRACK_SECONDS)
            for sid in aged:
                log.info("[%s] background shell %s running past %d min "
                         "with no end seen - no longer counted as running",
                         sess.label, sid,
                         int(BG_SHELL_MAX_TRACK_SECONDS / 60))
            if (aged and was_job_open and sess.status == Status.IDLE
                    and not sess.job_background_open()):
                sess.job_interim_seen = False
                sess.job_continuation_active = False
                sess.job_grace_until = 0.0
                try:
                    await self.notify_fn(sess, "job_agents_lost",
                                         {"what": "shell"})
                except Exception:
                    log.warning("Failed to notify job_agents_lost for %s",
                                name)
            if sess.agents_lines_due(now):
                try:
                    await self.notify_fn(sess, "agents_line_done",
                                         {"now": now})
                except Exception:
                    log.warning("Failed to settle the agents line for %s",
                                name, exc_info=True)

            # Subagent silence sweep (roadmap 8.22; was item 2.4's age TTL)
            if sess.active_subagents:
                # Captured BEFORE popping (design.md "model Claude Code
                # background-agent jobs" requirement 6) — job_background_open()
                # would read the now-empty table otherwise, and this
                # sweep's own eviction below is exactly what needs to be
                # observed as "was this job open a moment ago".
                was_job_open = sess.job_background_open()
                # An entry built by anything other than add_subagent (a
                # restored table, a hand-built one) starts its silence
                # window from its own start stamp, or from now when it has
                # neither — never from zero, which would drop it on sight.
                sess.normalize_subagent_liveness(now)
                prompt_owners = sess.prompt_owners()
                silent_ids = [
                    aid for aid, info in sess.active_subagents.items()
                    if (now - info["last_seen"]) > SUBAGENT_SILENCE_SECONDS
                    # A waiting agent is silent while its own agents work:
                    # sweep_unresumed_parents above bounds it.
                    and not sess.agent_shielded(aid, now)
                    # Nor while its dialog waits on a person (roadmap 8.120).
                    and aid not in prompt_owners
                ]
                for aid in silent_ids:
                    log.info("[%s] dropping silent subagent %s (no hook event "
                             "in %d min)", sess.label, aid,
                             int(SUBAGENT_SILENCE_SECONDS / 60))
                    sess.active_subagents.pop(aid, None)
                # A job cannot wait forever: once the silence sweep empties
                # the table for a session sitting IDLE with a job open, produce
                # the terminal "background agent lost" card rather than
                # leaving the waiting card ticking indefinitely. Gated on
                # IDLE specifically — a session that flipped back to BUSY
                # (the background agent's own tool call re-entered before
                # this scan) is still genuinely working, not orphaned.
                # The emptiness check IS the liveness check (roadmap 8.22):
                # the sweep immediately above has just removed every entry
                # that went silent, so anything still in the table was heard
                # from inside the window. An explicit second liveness test
                # here would be unreachable by construction — and therefore
                # unkillable by any test, which this project treats as worse
                # than absent (CLAUDE.md: every guard gets a test that fails
                # when the guard is removed). A background shell of the
                # job's own still running keeps it open: the shell ends
                # it, or its max age does.
                if (was_job_open and sess.status == Status.IDLE
                        and not sess.job_work_running()):
                    sess.job_interim_seen = False
                    sess.job_continuation_active = False
                    sess.job_grace_until = 0.0
                    try:
                        await self.notify_fn(sess, "job_agents_lost", {})
                    except Exception:
                        log.warning(
                            "Failed to notify job_agents_lost for %s", name,
                        )

            # Continuation-grace expiry ("close the background-job endgame"
            # requirement 2's fallback): the last background agent stopped
            # and an interim was delivered, but no <task-notification>
            # continuation arrived within the grace window. Close the job
            # honestly — the interim answer stands as the result. Gated on
            # IDLE: a BUSY session is genuinely working (the continuation
            # itself, or a new real turn), not orphaned.
            if (sess.status == Status.IDLE and sess.job_interim_seen
                    and not sess.job_work_running()
                    and not sess.job_continuation_active
                    and sess.job_grace_until
                    and now >= sess.job_grace_until):
                sess.job_grace_until = 0.0
                sess.job_interim_seen = False
                log.info(
                    "[%s] background job's continuation never arrived "
                    "within the grace window — closing the job",
                    sess.label,
                )
                try:
                    await self.notify_fn(sess, "job_grace_expired", {})
                except Exception:
                    log.warning(
                        "Failed to notify job_grace_expired for %s", name,
                    )
                self.registry.mark_dirty()

            # Idle-recovery fallback: a missed Stop hook can strand a session
            # in BUSY, animating forever. If the transcript shows the turn
            # finished and the file has gone quiet, recover to IDLE exactly
            # as the hook would (transition + idle_prompt notify finalizes
            # the busy message and flushes the queue).
            #
            # Only the hook-stamped path is trusted. A session with no stamped
            # path recovers nothing and falls through to STALE_BUSY_TIMEOUT —
            # guessing here once published another session's answer.
            # Where the "don't finalize a session whose agent is still
            # working" guarantee actually comes from (roadmap 8.22), so
            # nobody re-adds a dead guard here: a working agent refreshes
            # its `last_seen` several times a minute, the silence sweep
            # above therefore leaves its row in `active_subagents`, and
            # `job_background_open()` is True for as long as that row
            # exists — so this branch is never reached. The incident was
            # the age sweep DELETING that row; it is fixed at the sweep,
            # not by a second test here. A liveness check in this
            # condition would be unreachable by construction.
            #
            # Known limitation, unchanged by this: a session waiting only
            # on a background TASK — a job with no agent rows at all — has
            # no row to keep alive, so it stays recoverable exactly as
            # before.
            if sess.status == Status.BUSY and not sess.job_background_open():
                # The job-open guard ("close the background-job endgame"
                # requirement 1): while a background agent is running (or a
                # continuation turn is), the transcript on disk lags —
                # Claude Code 2.1.x flushes it lazily (observed 72-minute
                # lag live) — so "transcript finished + quiet" describes
                # the INTERIM turn, not the session. Recovering here
                # ping-ponged BUSY→IDLE eight times in one live run.
                tp = sess.transcript_path
                busy_for = (now - sess.busy_started_at) if sess.busy_started_at else 0.0
                quiet_for = 0.0
                mtime: float | None = None
                if tp:
                    try:
                        mtime = os.path.getmtime(tp)
                        quiet_for = time.time() - mtime
                    except OSError:
                        mtime = None
                        quiet_for = 0.0
                # The transcript must have been written since this turn
                # began. Without that, a turn whose prompt never reached
                # claude is indistinguishable from one that finished and
                # went quiet — turn_appears_complete() would be reading the
                # PREVIOUS turn's tail, and recovery would publish that
                # turn's answer as the reply to the new prompt.
                written_this_turn = bool(
                    tp and sess.busy_started_wall
                    and mtime is not None
                    and mtime >= sess.busy_started_wall - MTIME_GRANULARITY_SLACK
                )
                # Same false positive the stale-busy check below exists to
                # avoid: a tool call or compaction currently in flight emits
                # no hooks, so the transcript can look finished-and-quiet
                # while the turn is genuinely still running.
                # work_in_flight_reason is the ONE shared definition of that
                # condition (TrackedSession.work_in_flight_reason) — asked
                # here and by the stale-busy check below so the two can
                # never drift apart on what "still working" means. Cleared
                # here (not only inside the branch below) so a later,
                # different stand-down episode always gets its own log line
                # even if this tick's outer condition happens to be false.
                in_flight = sess.work_in_flight_reason(now)
                if in_flight is None:
                    sess.recovery_stand_down_logged = False
                if (tp and busy_for >= IDLE_RECOVERY_GRACE
                        and quiet_for >= IDLE_RECOVERY_GRACE
                        and written_this_turn
                        and turn_appears_complete(tp)):
                    if in_flight is not None:
                        kind, elapsed = in_flight
                        if not sess.recovery_stand_down_logged:
                            cap = {"tool": TOOL_INFLIGHT_MAX_SECONDS,
                                   "compact": COMPACT_INFLIGHT_MAX_SECONDS,
                                   }.get(kind, TURN_STATE_HOLD_SECONDS)
                            log.info(
                                "[%s] idle-recovery stood down — %s still in "
                                "flight (%.0fs, cap %.0fs); the transcript "
                                "looks finished and quiet but this is not a "
                                "missed Stop hook", sess.label, kind, elapsed,
                                cap,
                            )
                            sess.recovery_stand_down_logged = True
                        continue  # standing down — handled this scan
                    log.warning(
                        "[%s] BUSY but transcript shows the turn finished and has "
                        "been quiet %.0fs — recovering to IDLE (missed Stop hook)",
                        sess.label, quiet_for,
                    )
                    recovered = self.registry.transition(name, Status.IDLE)
                    if recovered:
                        summary = None
                        api_error = False
                        try:
                            # Only text written during THIS turn: the
                            # newest text in the file is the previous
                            # turn's whenever this one produced none, and
                            # busy_started_wall is the wrong anchor here —
                            # a background-job re-entry keeps the job's
                            # original stamp on purpose.
                            summary, api_error = extract_last_response_flagged(
                                tp, since=sess.turn_entered_wall or None,
                            )
                        except Exception:
                            log.debug("[%s] idle-recovery summary failed", name,
                                      exc_info=True)
                        # `recovered` tells bot/notify.py's IDLE branch this
                        # transition came from this fallback, not a real Stop
                        # hook — it skips the standalone "Finished" header
                        # when there's nothing new to say (empty or
                        # already-delivered content), instead of posting a
                        # bare notice for a turn that never actually ended.
                        ctx = {"summary": summary or "", "recovered": True}
                        # "" (not None) means the turn ended having produced
                        # no text — say so, or notify falls back to the
                        # previous turn's cached summary.
                        if summary == "":
                            ctx["no_response"] = True
                        if api_error:
                            # Claude Code's own error record (roadmap 8.145).
                            ctx["api_error"] = "transcript"
                        try:
                            await self.notify_fn(recovered, "idle_prompt", ctx)
                        except Exception:
                            log.warning("[%s] idle-recovery notify failed", name,
                                        exc_info=True)
                        self.registry.mark_dirty()
                    continue  # handled this session this scan

            # Stale BUSY warning (existing)
            if sess.status != Status.BUSY or sess.stale_warned:
                continue
            baseline = _quiet_since(sess)
            if baseline and (now - baseline) > STALE_BUSY_TIMEOUT:
                # A tool call or compaction is legitimately in flight — no
                # hooks fire between PreToolUse/PostToolUse or across a
                # compaction, so the session looks quiet even though it's
                # working. Stand down until it finishes (PostToolUse/
                # post-compact SessionStart clears the timestamp) or it has
                # been running long enough to count as genuinely wedged.
                # stale_warned stays False so the check re-arms as soon as
                # it does. work_in_flight is the ONE shared definition of
                # this condition (TrackedSession.work_in_flight_reason) —
                # also asked by the idle-recovery fallback above, so the two
                # can never drift apart on what "still working" means.
                if sess.work_in_flight(now):
                    continue
                # Fallback liveness signal: the Claude Code statusLine hook
                # writes /tmp/claude-status-<session>.json on many state
                # changes during active work. A fresh mtime means the
                # session is doing something even if no aipager-tracked
                # hook has fired. mtime is walltime, so compare via
                # time.time() (not the monotonic `now` above).
                statusline_path = statusline_file.status_file_path(name)
                try:
                    sl_age = time.time() - statusline_path.stat().st_mtime
                    if sl_age < STATUSLINE_ALIVE_SECONDS:
                        continue
                except OSError:
                    pass  # no statusLine yet — fall through
                sess.stale_warned = True
                stale_mins = int((now - baseline) / 60)
                report_store.record_counter("stale_busy")
                try:
                    await self.notify_fn(sess, "stale_busy", {"minutes": stale_mins})
                except Exception:
                    log.warning("Failed to notify stale_busy for %s", name)

        # The busy cards a flood mute refused (8.17c), owed while their
        # turns run. A pass of its own AFTER the loop above, because that
        # loop is what flushes every session's held answers: an owed card
        # never overtakes an answer — another session's, or one whose
        # delivery failed and stays held for a retry — and the owed
        # keyboard (`on_mute_catchup`, after this scan) never overtakes a
        # card. Whether the chat can afford the card yet — the mute, and
        # the minimal mode a ban leaves behind — is `_send_owed_card`'s
        # call: it keeps the debt without asking the limiter, so the
        # catch-up never spends the budget the answers need.
        await self._flush_owed_cards()

    async def _flush_owed_cards(self) -> None:
        for name, sess in list(self.registry.all_sessions().items()):
            if not sess.busy_card_owed:
                continue
            if _chat_holds_answers(_held_chat_key(sess)):
                continue
            try:
                await self.notify_fn(sess, "owed_card_flush", {})
            except Exception:
                log.warning("Failed to send the owed busy card for %s", name,
                            exc_info=True)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
