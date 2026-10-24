"""Pure, I/O-free session-shaping helpers for the Mini App's read routes.

Everything in this module takes a :class:`~aipager.state.TrackedSession`
(and, where relevant, a caller-supplied monotonic timestamp) and returns
plain dicts/lists — no registry access, no subprocess, no network. That
keeps it trivially unit-testable and keeps ``server.py``'s handlers thin:
auth + scope resolution + registry lookup live in ``server.py``; shaping
the JSON lives here.

One deliberate exception: ``session_detail``'s ``queue_depth`` reads
``policy_snapshot.combined_queue_depth``, a best-effort local ``/tmp``
directory listing (design.md "queue handoff") — the SAME helper chat's
own Stop/`/clearqueue` use, so the two surfaces can never disagree about
how many prompts sit behind a session's turn. No registry, subprocess or
network access is added; the file-shaping contract above still holds for
everything else in this module.

A third exception: a live session's context %, cost and model come from
its status-line file (``statusline_file.latest_stats``, one small read per
row), because the daemon's in-memory copy is empty after a restart and
zeroed at a turn's start.

A second exception: ``last_message`` stats a live session's transcript
and tail-reads it when it changed (roadmap 8.34), cached by (size, mtime)
in ``transcript.cached_last_assistant_preview``.

See design.md Decision 1 for why "waiting on permission" is derived here
rather than becoming a new ``Status`` enum member, and Decision 3 for why
the timeline is a new, simpler function rather than a reuse of
``bot/animation.py``'s Telegram-markdown renderer.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from aipager.clockfmt import elapsed as clock
from aipager.i18n import Msg
from aipager.miniapp import ui_text
from aipager.miniapp.phrase import Phrase, say
from aipager.policy_snapshot import combined_queue_depth
from aipager.state import QUEUE_CAP, Status
from aipager.statusline_file import latest_stats
from aipager.transcript import cached_last_assistant_preview

if TYPE_CHECKING:
    from aipager.state import TrackedSession

# Wire contract (entrypoints.md): the externally-visible status name for
# "blocked on a human" is always "waiting" — the Status enum's own
# "interactive" member name never appears in a stage-2 response body.
_WAITING_STATUS = "waiting"
# A live session parked on a Claude Code background job.
_BACKGROUND_STATUS = "background"

# Shared verbatim between session_actions()'s client-facing reason and
# server.py's Resume 409 body (design.md file-by-file plan), so the
# pre-check reason and the enforced refusal always read identically —
# one string, not two that could drift apart.
#
# Each is a Msg, a str equal to its English: the chat (session_ops,
# handlers) shows them as they are, the Mini App in its viewer's language
# (``phrase.say``).
NO_TRANSCRIPT_REASON = Msg("app.reason.no_transcript",
                           "No resumable transcript - start a fresh session instead.",
                           note="Why a finished session can't be resumed")
NO_PERMISSION_REASON = Msg("app.reason.no_permission",
                           "You don't have permission to control this session.")
# Shared verbatim between session_actions()'s client-facing reason and the
# corresponding write route's refusal body (entrypoints.md), same rationale
# as the two constants above — one string, not two that could drift apart.
PERMS_ADMIN_REQUIRED_REASON = Msg("app.reason.perms_admin",
                                  "Switching to Full access mode requires admin.",
                                  note="Full access is the permission mode (as in /mode); "
                                       "only an admin may switch a session to it")
# An Ask/Auto switch presses keys in the terminal: refused while it shows a
# question or permission prompt, which the keys would answer. Shared by the
# detail payload's mode entries and the perms route's 409.
MODE_PROMPT_OPEN_REASON = Msg("app.reason.mode_prompt_open",
                              "Answer the open prompt first, then switch the mode.",
                              note="An Ask/Auto mode switch is refused while the session shows "
                                   "a question or permission prompt")
QUEUE_EMPTY_REASON = Msg("app.reason.queue_empty", "Nothing queued to clear.",
                         note="Clear queue: no queued messages")
# Switching a running session's model (roadmap 8.35). Shared verbatim by
# the detail payload's `model_switch` entry, the Mini App route's 409 body
# and the chat's own refusal (bot/session_ops.model_switch_refusal), so
# both surfaces say the same thing.
#
# Refused while BUSY rather than queued: Claude Code's `/model` is an
# `immediate` command — it runs mid-turn instead of waiting for the turn
# to end — so typing it into a running turn would swap the model under
# that turn and can raise Claude Code's "Switch model?" confirmation on
# top of it, where the daemon cannot see it.
MODEL_SWITCH_BUSY_REASON = Msg(
    "app.reason.model_busy", "Claude is working - switch the model when this turn ends.",
    note="Why a session's model can't be switched now")
MODEL_SWITCH_PROMPT_OPEN_REASON = Msg(
    "app.reason.model_prompt_open",
    "A prompt is open in the terminal - answer it before switching the model.")
MODEL_SWITCH_NOT_LIVE_REASON = Msg("app.reason.model_not_live", "Session isn't running.")
# UNKNOWN is the state right after a daemon restart, before the monitor
# has looked: aipager does not yet know whether a turn is running.
MODEL_SWITCH_UNKNOWN_REASON = Msg(
    "app.reason.model_unknown",
    "aipager hasn't seen this session's state yet - try again in a moment.")
MODEL_SWITCH_PENDING_REASON = Msg(
    "app.reason.model_pending",
    "The last model switch isn't confirmed yet - check the terminal for "
    "Claude Code's \"Switch model?\" question before switching again.",
    note="\"Switch model?\" is Claude Code's own question in the terminal, in English: "
         "keep it as it is")
# What the page and the chat say when the statusline never reported a new
# model within the wait. The likely cause is Claude Code's own "Switch
# model?" confirmation still being up: aipager's PreModelSwitch hook
# pre-approves the switches it types (dtach/model_switch_marker.py), but
# a Claude Code older than 2.1.251, or an `opusplan` switch that asks the
# hook twice, still shows it — and keystrokes are never guessed at it.
MODEL_SWITCH_UNCONFIRMED = Msg(
    "app.reason.model_unconfirmed",
    "not confirmed - check the session. Claude Code may be asking "
    "\"Switch model?\" in the terminal, or it was already on that model.",
    note="After a model switch the session never reported the new model. Starts lower "
         "case: it follows the model's name. \"Switch model?\" is Claude Code's own "
         "question, in English: keep it as it is")
_QUEUE_FULL = Msg("app.reason.queue_full",
                  "Queue is full ({cap} pending) - clear it or wait for it to drain.",
                  eg={"cap": 50}, note="Too many messages wait behind the running turn")
QUEUE_FULL_REASON = Phrase(_QUEUE_FULL, cap=QUEUE_CAP)
# design.md Decision 6 — the Mini App's own unavailable-with-reason
# mechanism, reused verbatim rather than adding new client code. Greys
# out Compact when there is self-evidently nothing to compact, closing
# the most visible trigger point of the reported live-message-stack bug
# (a human tapping a button whose tap does nothing). A UI affordance
# only — the server-side POST route is unaffected (session_ops.py's
# `_compact_session_core` still accepts it regardless of context_pct).
NO_CONTEXT_REASON = Msg("app.reason.no_context",
                        "Nothing to compact - context is already empty.",
                        note="Compact (as in /compact) has nothing to shrink")

# Stop on a session parked on a Claude Code background job stops that job's
# work and brings the conversation back; with no turn running there is
# nothing to stop. Shared verbatim with the stop route's 409 (server.py).
NOT_BUSY_REASON = Msg("app.err.not_busy", "This session isn't busy right now - nothing to stop.")

# The session page's facts: their labels, keyed by the fact's id.
_FACT_LABELS = {
    "model": Msg("app.fact.model", "Model", note="Mini App session page: a fact's label"),
    "context": Msg("app.fact.context", "Context",
                   note="Mini App session page: how full the context window is"),
    "cost": Msg("app.fact.cost", "Cost", note="Mini App session page: the session's cost"),
    "working": Msg("app.fact.working", "Working for",
                   note="Mini App session page: followed by a clock (3:12)"),
    "last_active": Msg("app.fact.last_active", "Last active",
                       note="Mini App session page: followed by 5m ago"),
    "cwd": Msg("app.fact.cwd", "Directory",
               note="Mini App session page: the session's working directory"),
    "mode": Msg("app.fact.mode", "Mode",
                note="Mini App session page: the session's permission mode (as in /mode)"),
}
#: The mode fact while a switch waits for Claude Code to confirm it.
_MODE_PENDING = Msg("app.fact.mode_pending", "{mode} (waiting for Claude Code)",
                    note="Mini App session page: the mode a switch was sent for, not yet "
                         "confirmed; {mode} is the mode's label", eg={"mode": "Auto mode"})


def _derive_status(sess: "TrackedSession") -> tuple[str, str | None, str | None]:
    """Return ``(status, waiting_kind, waiting_summary)``.

    ``Status.INTERACTIVE`` alone means waiting — every hook path that
    reaches it (PermissionRequest, the ``permission_prompt`` fallback,
    AskUserQuestion) is entered only via a permission-request or
    question path, so it is blocked on a human by construction, whether
    or not ``pending_permission`` happens to be populated (it is
    ``None`` on the "fell back to a separate Telegram message" path —
    ``bot/notify.py:982,988`` — while status still reads INTERACTIVE).
    ``pending_permission`` is therefore only consulted here to *enrich*
    ``waiting_kind``/``waiting_summary``, never to gate ``"waiting"``
    itself.

    A live session whose conversation moved into a Claude Code background
    job (``parked_job``) is ``"background"``, whatever its own status: its
    terminal shows Claude Code's agent view, and nothing may be typed
    there until it is brought back.
    """
    if sess.parked_job and sess.status != Status.GONE:
        return _BACKGROUND_STATUS, None, None
    if sess.status != Status.INTERACTIVE:
        return sess.status.name.lower(), None, None

    kind, summary = sess.waiting_on_human()
    return _WAITING_STATUS, kind, summary


# Display order for the grid (design §2: "ordered by last activity", with
# gone sorted last regardless of recency so the grid never becomes a
# graveyard — the failure observed live, 14 dead sessions padding the view).
# Computed server-side rather than in JavaScript so it is a pure function
# pytest can exercise directly; the client renders the order it is given.
_STATUS_RANK = {"waiting": 0, "busy": 1, "idle": 2, "background": 2, "unknown": 3, "gone": 4}
_UNKNOWN_RANK = _STATUS_RANK["unknown"]


def _order_key(row: dict[str, Any]) -> tuple[int, float, str]:
    """Sort key: status group, then most-recently-active, then label.

    ``last_active_seconds_ago`` is ``None`` for a session with no recorded
    activity — those sort LAST within their group rather than being read as
    "0 seconds ago" (which would push never-used sessions to the top).
    Label is the final tiebreaker so the order is stable across polls
    instead of shuffling on every refresh.
    """
    rank = _STATUS_RANK.get(row.get("status", ""), _UNKNOWN_RANK)
    age = row.get("last_active_seconds_ago")
    return (rank, float("inf") if age is None else age, row.get("label", ""))


def sort_for_display(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order grid rows for the two-column Sessions tab."""
    return sorted(rows, key=_order_key)


def grid_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Header stats derived from the same rows the grid renders.

    Computed here, not client-side, so the numbers the operator reads are
    covered by the same tests as the rows themselves.
    """
    # A session in Claude Code's background is live: its claude still runs.
    live = [r for r in rows if r.get("status") != "gone"]
    return {
        "total": len(rows),
        "live": len(live),
        "gone": len(rows) - len(live),
        "waiting": sum(1 for r in rows if r.get("status") == "waiting"),
        # Spend is meaningful across every session that ever ran, including
        # finished ones — that is what the run actually cost.
        "cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in rows), 4),
    }


#: The grid row's ``waiting_summary`` cap, in characters (ellipsis included).
GRID_WAITING_SUMMARY_MAX = 160


def grid_waiting_summary(summary: str | None) -> str:
    """One line of at most :data:`GRID_WAITING_SUMMARY_MAX` characters for
    the grid's "needs you" tray: every whitespace run (newlines included)
    collapses to one space, and a longer text is cut and ends in "…".
    ``""`` when there is nothing to say."""
    line = " ".join((summary or "").split())
    if len(line) > GRID_WAITING_SUMMARY_MAX:
        line = line[:GRID_WAITING_SUMMARY_MAX - 1].rstrip() + "…"
    return line


def session_summary(sess: "TrackedSession", now: float) -> dict[str, Any]:
    """Shape one grid-row entry for ``GET /api/sessions``.

    Deliberately excludes ``cwd`` (never sent to the polled grid — see
    design.md Decision 5). ``waiting_summary`` used to be drill-down only;
    the grid's "needs you" tray shows the pending prompt without a fetch
    per waiting session, so a waiting row now carries it too, collapsed
    to one line and capped by :func:`grid_waiting_summary`. The detail
    route already sends the same text to the same members, so nothing new
    is exposed. ``now`` is a caller-supplied ``time.monotonic()`` reading
    so every row in one response is measured against the same instant,
    and so this stays a pure function for testing.
    """
    status, waiting_kind, summary = _derive_status(sess)
    last_active = round(now - sess.last_hook_at) if sess.last_hook_at else None
    stats = latest_stats(sess)
    return {
        "label": sess.label,
        "status": status,
        "waiting_kind": waiting_kind,
        "waiting_summary": (
            grid_waiting_summary(summary) if status == _WAITING_STATUS else None
        ),
        "model": stats["model"],
        "context_pct": stats["context_pct"],
        "cost_usd": stats["cost_usd"],
        "last_active_seconds_ago": last_active,
        "project": os.path.basename(sess.cwd) if sess.cwd else "",
        "parked": status == _BACKGROUND_STATUS,
    }


# Canonical, stable emission order (design.md): the server always builds
# the ``actions`` dict by walking this tuple, so key order in the JSON
# response never depends on set/dict iteration order. The client's own
# ACTION_ORDER (static/_app.py) is the same ten keys in the same order —
# the first six are the "session control" menu group, the last four are
# "destructive/disruptive" (confirm-modal) — so filtering this tuple by
# presence is exactly what produces the client's grouped rendering.
# ``bringback`` (a session parked on a Claude Code background job) comes
# first: it is the one thing such a session is waiting for.
_CANONICAL_ACTION_ORDER = (
    "bringback", "stop", "clearqueue", "compact", "resume", "rename",
    "kill", "perms", "restart", "delete",
)

# Which keys are even relevant for a given status, before any
# availability rule is evaluated. `clearqueue` is busy/waiting only —
# `pending_queue` is only ever populated while a turn is in flight, so
# offering it at idle would only ever 409. `compact` excludes `waiting`
# on purpose: the terminal is displaying an open permission/question
# prompt, and typing `/compact` there risks being read as input to that
# prompt rather than a new turn.
_STATUS_ACTION_KEYS: dict[str, frozenset[str]] = {
    "busy": frozenset({"stop", "clearqueue", "compact", "rename", "perms", "restart"}),
    "waiting": frozenset({"stop", "clearqueue", "rename", "perms", "restart"}),
    "idle": frozenset({"kill", "compact", "rename", "perms", "restart"}),
    "gone": frozenset({"resume", "rename", "delete"}),
    # Parked on a Claude Code background job: nothing is typed into it, so
    # no compact, mode switch or queue clear. Stop stops the job's work and
    # brings the conversation back; Restart brings it back too.
    "background": frozenset({"bringback", "stop", "rename", "kill", "restart"}),
}


def session_actions(
    status: str, *, resumable: bool, can_act: bool,
    is_admin: bool = False, queue_depth: int = 0,
    context_pct: int = 0, turn_running: bool = True, tr=None,
) -> dict[str, dict[str, Any]]:
    """The status→button matrix (design.md), computed server-side so
    every refusal rule — status match, resumability, permission, admin,
    queue depth — is testable directly in pytest with zero DOM, and so
    the client can never show a button whose tap would just bounce off
    a 409.

    Returns only the keys :data:`_STATUS_ACTION_KEYS` says are relevant
    for ``status``, in :data:`_CANONICAL_ACTION_ORDER`; anything else
    (``"unknown"``) → ``{}``.

    Each present entry is ``{"available": bool, "reason": str | None}``.
    ``available`` is ``False`` whenever ``can_act`` is ``False``
    (reason = :data:`NO_PERMISSION_REASON`) — this wins over every
    OTHER reason for every key, generalising the original resume-only
    rule to all nine: a caller who cannot act at all should not be told
    some OTHER reason it can't act. When ``can_act`` is ``True``, three
    keys carry their own extra rule:

    - ``resume``: unavailable when ``not resumable``
      (:data:`NO_TRANSCRIPT_REASON`).
    - ``clearqueue``: unavailable when ``queue_depth <= 0``
      (:data:`QUEUE_EMPTY_REASON`) — nothing queued to clear.
    - ``compact``: unavailable when ``context_pct <= 0`` on either status
      (:data:`NO_CONTEXT_REASON`, design.md Decision 6 — there is
      self-evidently nothing to compact); unavailable, on a BUSY session
      only, when ``queue_depth >= QUEUE_CAP`` (:data:`QUEUE_FULL_REASON`).
      An IDLE compact sends immediately rather than queueing, so it has no
      queue-depth rule at all.
    - ``stop`` on a ``background`` session: unavailable when no turn is
      running (``turn_running`` False, :data:`NOT_BUSY_REASON`): the
      stop route would only answer "nothing to stop".
    - ``perms``: the menu's mode slot. Which modes it offers, and which
      of them this caller may pick, is :func:`mode_options` (Full access
      needs an admin; Ask and Auto do not), so the slot itself has no
      extra rule. ``is_admin`` is kept for callers that pass it.

    ``tr``: the reasons in that reader's language (the Mini App passes the
    viewer's); None leaves them as the English constants.
    """
    relevant = _STATUS_ACTION_KEYS.get(status)
    if relevant is None:
        return {}

    actions: dict[str, dict[str, Any]] = {}
    for key in _CANONICAL_ACTION_ORDER:
        if key not in relevant:
            continue
        if not can_act:
            actions[key] = {"available": False, "reason": say(tr, NO_PERMISSION_REASON)}
        elif key == "resume" and not resumable:
            actions[key] = {"available": False, "reason": say(tr, NO_TRANSCRIPT_REASON)}
        elif key == "clearqueue" and queue_depth <= 0:
            actions[key] = {"available": False, "reason": say(tr, QUEUE_EMPTY_REASON)}
        elif key == "compact" and context_pct <= 0:
            actions[key] = {"available": False, "reason": say(tr, NO_CONTEXT_REASON)}
        elif key == "compact" and status == "busy" and queue_depth >= QUEUE_CAP:
            actions[key] = {"available": False, "reason": say(tr, QUEUE_FULL_REASON)}
        elif key == "stop" and status == _BACKGROUND_STATUS and not turn_running:
            actions[key] = {"available": False, "reason": say(tr, NOT_BUSY_REASON)}
        else:
            actions[key] = {"available": True, "reason": None}
    return actions


def model_switch_state(
    status: str, *, can_act: bool, switch_pending: bool = False, tr=None,
) -> dict[str, Any] | None:
    """Whether the session page's Model picker is live, and why not.

    ``None`` for a session that is not running at all (``gone``/
    ``unknown``): there is no model to switch, so no control is drawn.
    Otherwise ``{"available", "reason"}`` in the same shape as
    :func:`session_actions` entries, with the same precedence — a caller
    who cannot prompt is told that, never some other reason.
    """
    if status not in ("idle", "busy", _WAITING_STATUS):
        return None
    if not can_act:
        return {"available": False, "reason": say(tr, NO_PERMISSION_REASON)}
    if status == "busy":
        return {"available": False, "reason": say(tr, MODEL_SWITCH_BUSY_REASON)}
    if status == _WAITING_STATUS:
        return {"available": False, "reason": say(tr, MODEL_SWITCH_PROMPT_OPEN_REASON)}
    if switch_pending:
        return {"available": False, "reason": say(tr, MODEL_SWITCH_PENDING_REASON)}
    return {"available": True, "reason": None}


def answer_state(status: str, *, can_act: bool, tr=None) -> dict[str, Any] | None:
    """Whether the page may offer "Answer in chat" (re-send the pending
    prompt into the chat), and why not.

    ``None`` unless the session is waiting on a prompt. Otherwise
    ``{"available", "reason"}`` in the same shape and precedence as
    :func:`model_switch_state`: a caller who cannot prompt is told that.
    """
    if status != _WAITING_STATUS:
        return None
    if not can_act:
        return {"available": False, "reason": say(tr, NO_PERMISSION_REASON)}
    return {"available": True, "reason": None}


def session_detail(
    sess: "TrackedSession", now: float, *,
    can_act: bool = True, is_admin: bool = False, tr=None,
) -> dict[str, Any]:
    """Shape the drill-down payload for ``GET /api/sessions/{label}``.

    ``busy_elapsed_seconds`` is populated whenever a turn is actually in
    flight — BUSY, or INTERACTIVE while that turn's clock is still
    running (``busy_started_at`` is shifted forward across a permission
    wait per its docstring in state.py, so it stays meaningful there
    too); ``None`` for IDLE/GONE/UNKNOWN, where it would just be stale.

    ``can_act``/``is_admin`` default to permissive-off values so the
    existing unit tests that call this positionally
    (``session_detail(sess, time.monotonic())``) need no changes — they
    get a permissive default and simply gain the new keys without
    asserting on them.

    ``tr``: facts and reasons in that reader's language (the Mini App
    passes its viewer's); None is today's English.
    """
    status, waiting_kind, waiting_summary = _derive_status(sess)
    last_active = round(now - sess.last_hook_at) if sess.last_hook_at else None
    busy_elapsed = None
    if sess.busy_started_at and sess.status in (Status.BUSY, Status.INTERACTIVE):
        # Floored, never rounded: the busy card truncates, and the app and
        # the card show the same clock for the same moment.
        busy_elapsed = int(max(0.0, now - sess.busy_started_at))
    # The unit the busy card last rendered its clock in (true seconds, or
    # 30 s steps on an old turn): the app shows the clock the card shows.
    unit = getattr(sess, "card_elapsed_unit", "s")
    queue_depth = combined_queue_depth(sess)
    stats = latest_stats(sess)
    detail = {
        "label": sess.label,
        "status": status,
        "waiting_kind": waiting_kind,
        "waiting_summary": waiting_summary,
        "model": stats["model"],
        "context_pct": stats["context_pct"],
        "cost_usd": stats["cost_usd"],
        "cwd": sess.cwd or "",
        "last_active_seconds_ago": last_active,
        "busy_elapsed_seconds": busy_elapsed,
        "busy_elapsed_unit": unit if unit in ("s", "m", "h") else "s",
        # The session's current permission mode and how many prompts sit
        # behind its in-progress turn — both consulted client-side to
        # word the perms confirm dialog and to decide whether Clear
        # queue/Compact would just bounce off a 409, and both fed into
        # session_actions() below so the SAME numbers gate the buttons.
        "mode": sess.current_mode(),
        "mode_pending": sess.mode_switch is not None,
        "queue_depth": queue_depth,
        # The page's headline content. Unlike `timeline`, this survives a
        # daemon restart, so it is the one thing that reliably has content
        # for an older session. See last_message() for where it comes from.
        "last_message": preview_lines(last_message(sess)),
        "timeline": build_timeline(sess, now),
        # Parked on a Claude Code background job, and since when (seconds,
        # from the wall clock the park was recorded with; None otherwise).
        "parked": status == _BACKGROUND_STATUS,
        "parked_seconds_ago": _parked_age(sess) if status == _BACKGROUND_STATUS else None,
    }
    detail["modes"] = mode_options(sess, can_act=can_act, is_admin=is_admin, tr=tr)
    if status != "gone":
        detail["mode_label"] = _shown_mode(sess, tr)
    detail["facts"] = display_facts(detail, tr=tr)
    detail["model_switch"] = model_switch_state(
        detail["status"], can_act=can_act,
        switch_pending=sess.model_switch_pending(), tr=tr,
    )
    detail["answer"] = answer_state(detail["status"], can_act=can_act, tr=tr)
    detail["actions"] = session_actions(
        detail["status"], resumable=bool(sess.claude_session_id), can_act=can_act,
        is_admin=is_admin, queue_depth=queue_depth,
        context_pct=detail["context_pct"], turn_running=sess.can_be_stopped(), tr=tr,
    )
    return detail


def _parked_age(sess: "TrackedSession") -> int | None:
    """Whole seconds since *sess* was parked, or None when not known."""
    since = getattr(sess, "parked_since", 0.0) or 0.0
    if since <= 0:
        return None
    return max(0, int(time.time() - since))


def mode_options(sess: "TrackedSession", *, can_act: bool, is_admin: bool,
                 tr=None) -> list[dict[str, Any]]:
    """The three modes for the session page's menu, in the viewer's
    language: ``{id, label, current, available, reason, restarts}``.

    Full access needs an admin (:data:`PERMS_ADMIN_REQUIRED_REASON`); an
    in-place Ask/Auto switch is not offered while the session shows a
    prompt the key presses would answer (:data:`MODE_PROMPT_OPEN_REASON`);
    nothing is for someone who cannot prompt it. ``restarts``: switching
    to it restarts the session (into or out of Full access)."""
    from aipager import perm_modes
    from aipager.bot.mode_labels import mode_label
    tr = tr or _english()
    current = sess.current_mode()
    options = []
    for mode in perm_modes.MODES:
        restarts = sess.mode_switch_restarts(mode)
        reason = None
        if not can_act:
            reason = NO_PERMISSION_REASON
        elif mode == perm_modes.FULL and not is_admin:
            reason = PERMS_ADMIN_REQUIRED_REASON
        elif not restarts and sess.dialog_is_open():
            reason = MODE_PROMPT_OPEN_REASON
        options.append({
            "id": mode,
            "label": tr.text(mode_label(mode)),
            "current": mode == current,
            "available": reason is None,
            "reason": say(tr, reason) if reason is not None else None,
            "restarts": restarts,
        })
    return options


def _shown_mode(sess: "TrackedSession", tr=None) -> str:
    """The session's mode as the page shows it (plain text)."""
    from aipager.bot.mode_labels import shown_label
    tr = tr or _english()
    label = shown_label(tr, sess, html=False)
    if sess.mode_switch is not None:
        from aipager.i18n import Safe
        return tr.text(_MODE_PENDING, mode=Safe(label))
    return label


def last_message(sess: "TrackedSession") -> str:
    """The session's latest real assistant reply, for the detail page.

    A GONE session shows its snapshot (``last_assistant_preview``, taken
    at the GONE transition and persisted for /resume), or its transcript
    when no snapshot was taken. Any other session
    reads its transcript — the snapshot is never consulted, because on a
    live session it can only be a leftover from an earlier death (roadmap
    8.34: a week-old API-error snapshot shown for a healthy session).

    The second exception to this module's I/O-free contract: a stat per
    call, plus a backward tail read only when the transcript's (size,
    mtime) changed since the last call (``cached_last_assistant_preview``)
    — so the page's 2.5 s poll costs one stat while nothing is happening.
    """
    if sess.status == Status.GONE:
        # A session ended by Claude's own SessionEnd hook (every /exit)
        # goes GONE without a snapshot; read what the picker and the
        # resume recap would. A GONE transcript no longer changes, so
        # the cache holds it for good.
        return (sess.last_assistant_preview
                or cached_last_assistant_preview(sess.transcript_path))
    return cached_last_assistant_preview(sess.transcript_path)


# Roughly three phone-width lines. `last_assistant_preview` is already a
# ~200-char extract upstream; this only guards against a longer one
# arriving and pushing everything below it off the screen.
_PREVIEW_MAX_CHARS = 240


def preview_lines(preview: str | None) -> str:
    """Normalise the stored assistant preview for display.

    Collapses runs of blank lines (the stored extract can carry markdown
    paragraph breaks that waste half the visible box on a phone) and caps
    the length on a word boundary so the section stays about three lines.
    Returns "" for anything empty, so the client renders its explicit
    "nothing captured" state rather than an empty box.
    """
    if not preview or not preview.strip():
        return ""
    lines = [line.strip() for line in preview.strip().splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) <= _PREVIEW_MAX_CHARS:
        return text
    clipped = text[:_PREVIEW_MAX_CHARS]
    # Prefer cutting at the last space so the tail is not a half-word.
    cut = clipped.rfind(" ")
    if cut > _PREVIEW_MAX_CHARS // 2:
        clipped = clipped[:cut]
    return clipped.rstrip() + "…"


def display_facts(detail: dict[str, Any], tr=None) -> list[dict[str, str]]:
    """The info line as ordered ``{id, label, value}`` entries, omitting
    what would be noise.

    A finished session legitimately reports ``model: ""``, ``cost 0`` and
    ``context 0``; rendering those as "0% ctx · $0.00" tells the operator
    something false-looking about a session that simply never recorded
    them. Built here rather than in JavaScript so the omission rules are
    pinned by pytest.

    ``id`` (``model``, ``context``, ``cost``, ``working``, ``last_active``,
    ``cwd``) is what the page decides by, never the shown label. With
    ``tr`` the labels and numbers are in that reader's language and
    digits; the model and the folder are names and stay as they are.
    """
    facts: list[dict[str, str]] = []

    def add(fid: str, value: str) -> None:
        label = _FACT_LABELS[fid]
        facts.append({"id": fid, "label": say(tr, label), "value": value})

    def num(text: str) -> str:
        return tr.num(text) if tr is not None else text

    if detail.get("model"):
        add("model", str(detail["model"]))
    if detail.get("context_pct"):
        add("context", num(f"{detail['context_pct']}%"))
    if detail.get("cost_usd"):
        add("cost", num(f"${detail['cost_usd']:.2f}"))
    if detail.get("busy_elapsed_seconds"):
        unit = detail.get("busy_elapsed_unit") or "s"
        seconds = detail["busy_elapsed_seconds"]
        add("working", tr.clock(seconds, unit) if tr is not None else clock(seconds, unit))
    age = detail.get("last_active_seconds_ago")
    if age is not None:
        add("last_active", _ago(age, tr))
    if detail.get("mode_label"):
        add("mode", str(detail["mode_label"]))
    if detail.get("cwd"):
        add("cwd", str(detail["cwd"]))
    return facts


def _ago(seconds, tr=None) -> str:
    """"5m ago": the page's own short form (``ui_text.AGO_*``)."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        msg, n = ui_text.AGO_S, max(0, seconds)
    elif seconds < 3600:
        msg, n = ui_text.AGO_M, seconds // 60
    elif seconds < 86400:
        msg, n = ui_text.AGO_H, seconds // 3600
    else:
        msg, n = ui_text.AGO_D, seconds // 86400
    return (tr or _english()).text(msg, n=n)


def _english():
    from aipager import i18n
    return i18n.get("en")


def build_timeline(sess: "TrackedSession", now: float | None = None) -> list[dict[str, Any]]:
    """Interleave ``tool_history`` and ``stream_commentary`` in
    chronological order, uncapped — a scrollable webview has no 32 768
    byte ceiling and no live-batch-holding to honor (design.md
    Decision 3). Naturally bounded by ``TOOL_HISTORY_CAP`` (state.py) —
    no separate cap needed here.

    Each row is ``{"kind": "commentary", "text": str}`` or
    ``{"kind": "tool", "text": str, "state": "done"|"failed"|"running",
    "elapsed_seconds": int|None}``.

    *now* is the payload's clock (``session_detail``'s), so a running
    agent's time and "Working for" come from one reading; ``None`` reads
    the clock here.
    """
    if now is None:
        now = time.monotonic()
    pending: dict[int, list[str]] = defaultdict(list)
    for anchor, text in sess.stream_commentary:
        pending[min(anchor, len(sess.tool_history))].append(text)

    subagent_started = {
        info["history_idx"]: info["started_at"]
        for info in sess.active_subagents.values()
        if info.get("history_idx") is not None
    }

    rows: list[dict[str, Any]] = []
    for idx, (summary, done) in enumerate(sess.tool_history):
        for text in pending.pop(idx, ()):
            rows.append({"kind": "commentary", "text": text})
        state = "failed" if done == "failed" else ("done" if done else "running")
        elapsed = None
        if state == "running" and idx in subagent_started:
            elapsed = int(max(0.0, now - subagent_started[idx]))
        rows.append({
            "kind": "tool", "text": summary, "state": state,
            "elapsed_seconds": elapsed,
        })
    for idx in sorted(pending):
        for text in pending[idx]:
            rows.append({"kind": "commentary", "text": text})
    return rows


__all__ = [
    "MODEL_SWITCH_BUSY_REASON",
    "MODEL_SWITCH_NOT_LIVE_REASON",
    "MODEL_SWITCH_PENDING_REASON",
    "MODEL_SWITCH_PROMPT_OPEN_REASON",
    "MODEL_SWITCH_UNCONFIRMED",
    "MODEL_SWITCH_UNKNOWN_REASON",
    "NOT_BUSY_REASON",
    "NO_PERMISSION_REASON",
    "NO_TRANSCRIPT_REASON",
    "PERMS_ADMIN_REQUIRED_REASON",
    "QUEUE_EMPTY_REASON",
    "QUEUE_FULL_REASON",
    "build_timeline",
    "display_facts",
    "preview_lines",
    "grid_totals",
    "model_switch_state",
    "session_actions",
    "session_detail",
    "session_summary",
    "sort_for_display",
]
