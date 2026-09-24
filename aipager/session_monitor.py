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
from pathlib import Path

from aipager.dtach import inject as dtach_inject
from aipager.config import (
    COMPACT_INFLIGHT_MAX_SECONDS,
    PANE_POLL_INTERVAL,
    PROMPT_HOOK_GRACE_SECONDS,
    STALE_BUSY_TIMEOUT,
    STATUSLINE_ALIVE_SECONDS,
    TOOL_INFLIGHT_MAX_SECONDS,
)
from aipager.state import SessionRegistry, Status, TrackedSession
from aipager.transcript import (
    extract_last_response,
    last_assistant_preview,
    turn_appears_complete,
)

log = logging.getLogger(__name__)

# Item 2.2 — auto-demote INTERACTIVE sessions back to BUSY if they've sat
# in INTERACTIVE state with no hook activity for this long. The assumption:
# claude crashed mid-permission-prompt, the user can never see / answer
# it, so the session shouldn't sit forever. Demoting to BUSY lets the
# session_monitor's existing stale-busy logic surface it after another
# STALE_BUSY_TIMEOUT, instead of silently rotting. The two compound, so
# a crashed permission prompt surfaces in 300s + STALE_BUSY_TIMEOUT.
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


def busy_card_watchdog_action(
    sess: TrackedSession, now: float, *, suppressed: bool = False,
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
    ``CARD_STALE_SECONDS`` without a successful edit, and ``None`` otherwise.

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
    if now - sess.card_watchdog_at < CARD_STALE_SECONDS:
        return None
    if not sess.animation_running():
        return "restart", 0.0
    baseline = sess.last_tool_edit_at or sess.busy_started_at
    if not baseline:
        return None
    since = now - baseline
    if since < CARD_STALE_SECONDS:
        return None
    return "refresh", since


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
    # And the DURABLE state (8.28). Same shape as the registry's own
    # `save_if_dirty()` two lines up the call stack in `_loop`: a dirty
    # flag plus this 2 s tick IS the debounce, so there is no timer of our
    # own to leak. A no-op unless something material changed, and it never
    # raises — a full disk must not be able to stop the session scan.
    flood_state.save_if_dirty()


class SessionMonitor:
    """Periodically discovers dtach sessions and marks dead ones GONE."""

    def __init__(self, registry: SessionRegistry, notify_fn):
        self.registry = registry
        self.notify_fn = notify_fn
        self._task: asyncio.Task | None = None
        self.on_sessions_changed = None  # optional async callback
        # Session names whose busy card was suppressed on the PREVIOUS
        # tick, so `card_suppression_transition` can emit exactly one INFO
        # when suppression starts and one when it lifts (8.29 R7) rather
        # than 1,800 an hour at a 2 s tick.
        self._cards_suppressed_seen: set = set()
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
            try:
                await self._scan()
                self.registry.save_if_dirty()
            except Exception:
                log.exception("Session monitor error")
            await asyncio.sleep(PANE_POLL_INTERVAL)

    async def _scan(self) -> None:
        _sweep_flood_backoff()
        sessions = await dtach_inject.list_sessions()
        old_names = set(self.registry.all_sessions().keys())

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
                try:
                    await self.notify_fn(sess, "session_end", {
                        "source": "disappeared", "notes": gone_notes})
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

        # Notify if session list changed (for bot command/keyboard updates)
        new_names = set(self.registry.all_sessions().keys())
        if new_names != old_names and self.on_sessions_changed:
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

        for name, sess in self.registry.all_sessions().items():
            # INTERACTIVE watchdog (item 2.2)
            if sess.status == Status.INTERACTIVE:
                baseline = _quiet_since(sess)
                if baseline and (now - baseline) > INTERACTIVE_TIMEOUT_SECONDS:
                    log.warning(
                        "[%s] INTERACTIVE > %d min with no hooks — "
                        "demoting to BUSY (likely a crashed permission prompt)",
                        sess.label, int(INTERACTIVE_TIMEOUT_SECONDS / 60),
                    )
                    sess.pending_permission = None
                    self.registry.transition(name, Status.BUSY)
                    self.registry.mark_dirty()
                    # Fall through so stale-busy logic still applies.

            # "Prompt not taken" watchdog (roadmap 8.11). Cleared BEFORE
            # the notify so it fires exactly once per send, whatever the
            # handler does; the card watchdog is skipped for this scan
            # because the handler is about to settle the card itself.
            if prompt_not_taken(sess, now):
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
                else busy_card_watchdog_action(sess, now,
                                               suppressed=suppressed)
            )
            if card_action is not None:
                action_kind, since = card_action
                sess.card_watchdog_at = now
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
                silent_ids = [
                    aid for aid, info in sess.active_subagents.items()
                    if (now - info["last_seen"]) > SUBAGENT_SILENCE_SECONDS
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
                # when the guard is removed).
                if (was_job_open and sess.status == Status.IDLE
                        and not sess.active_subagents):
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
                    and not sess.active_subagents
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
                            cap = (TOOL_INFLIGHT_MAX_SECONDS if kind == "tool"
                                   else COMPACT_INFLIGHT_MAX_SECONDS)
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
                        try:
                            # Only text written during THIS turn: the
                            # newest text in the file is the previous
                            # turn's whenever this one produced none, and
                            # busy_started_wall is the wrong anchor here —
                            # a background-job re-entry keeps the job's
                            # original stamp on purpose.
                            summary = extract_last_response(
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
                statusline_path = Path(f"/tmp/claude-status-{name}.json")
                try:
                    sl_age = time.time() - statusline_path.stat().st_mtime
                    if sl_age < STATUSLINE_ALIVE_SECONDS:
                        continue
                except OSError:
                    pass  # no statusLine yet — fall through
                sess.stale_warned = True
                stale_mins = int((now - baseline) / 60)
                try:
                    await self.notify_fn(sess, "stale_busy", {"minutes": stale_mins})
                except Exception:
                    log.warning("Failed to notify stale_busy for %s", name)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
