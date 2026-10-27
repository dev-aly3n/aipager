"""Telegram bot — python-telegram-bot v22 async Application.

Single owner of all Telegram communication. Handles:
- CallbackQuery (button taps) → dtach_inject.send_keys()
- Message replies → dtach_inject.send_text_and_enter()
- /status command → show all sessions
- /<label> <prompt> → direct send to session
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import os
import re
import time
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import (
    Update,
)

from aipager import i18n, invisible, perm_modes
from aipager.config import RESUME_GUARD_SECONDS
from aipager.i18n import Command, Msg, Safe, Span
from aipager.dtach import inject
from aipager.bot import reactions
from aipager.bot.sibling_text import shown
from aipager.miniapp import sessions as _mini_sessions

# Single source of truth for the poll timing (design.md ORCHESTRATOR
# OVERRIDE) — callbacks.py does not import this module, so this is not
# a cycle. Kept here as a plain import rather than redefined so a future
# tuning of the poll window only has to change one place.
from aipager.bot.callbacks import (
    _PERMS_POLL_COUNT,
    _PERMS_POLL_INTERVAL,
    _PERMS_RESTART_QUIET,
    _stopped_line,
)
from aipager.state import TURN_SENDER_TERMINAL, Status, TrackedSession
from aipager.transcript import last_assistant_preview as _read_preview
from aipager.transcript import read_still_queued

# Pure-function helpers and constants live in aipager.bot.transport
# now. Re-export the names this module uses internally so the
# TelegramBot class body below (and any external consumers like the
# tests) keeps working without changes.
from aipager.bot.transport import (  # noqa: F401
    RESUME_FULL_NEEDS_ADMIN,
    reply_text,
    edit_text,
    send_text,
    ACTION_VERBS,
    TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES,
    TELEGRAM_MAX_DOC_BYTES,
    TELEGRAM_MAX_TEXT_LEN,
    TruncationFailed,
    _build_diff_block,
    _detect_api_error,
    _DIFF_MAX_CHARS,
    _DIFF_MAX_LINES,
    _ERROR_PATTERNS,
    _extract_retry_after,
    _is_bot_blocked,
    _log_blocked_once,
    _MAX_TRUNCATIONS,
    _md_safe_boundaries,
    _PERSONAL_MODE_SENTINEL,
    _RETRY_AFTER_RE,
    _safe_truncate,
    _send_with_retry,
    _TRUNC_SUFFIX,
    _truncate_diff,
    calling_chat_id,
    calling_user_id,
    driver_id_from_update,
    NEEDS_ADMIN_REPLY,
    PROMPT_DIALOG,
    PROMPT_REFUSED,
    resolve_chat_id_int,
)

# ``/model <name>``: a model switch, which any member may send (roadmap
# 8.74, D-G). One argument, nothing after it.
_MODEL_SWITCH_COMMAND = re.compile(r"/model[ \t]+[^\s]+")

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Seams: tests replace THESE module attributes, never ``asyncio.sleep`` /
# ``asyncio.create_task`` themselves. ``aipager.bot.session_ops.asyncio`` IS
# the global asyncio module, so patching through it reaches every coroutine
# in the process and has hung this suite twice (CLAUDE.md). Both default
# to the real functions; production behaviour is unchanged.
_sleep = asyncio.sleep
#: How long a safety halt waits for a bring-back already running before
#: it stops a Claude Code background job itself, and how often it looks.
_HALT_WAIT_SECONDS = 30.0
_HALT_WAIT_POLL = 0.25
#: A safety halt's bring-back outcomes that leave the background job
#: running (the stop never happened, or failed).
_HALT_JOB_LEFT = frozenset({"bg_stop_failed", "already_restarting",
                            "bg_no_conversation", "not_live"})
_spawn = asyncio.create_task

#: How long aipager remembers the Claude Code session id its own /kill
#: ended, so that process's SessionEnd is not announced (the operator
#: already has "💀 Killed"). Only prunes: the id itself decides.
KILL_NOTICE_QUIET_SECONDS: float = 60.0


@dataclass
class StopOutcome:
    """Result of :meth:`SessionOpsMixin._stop_session_core`."""

    ok: bool
    label: str
    dropped: int = 0
    reason: str = ""     # "stale" when the tap came from an earlier turn
    by: str = ""         # who stopped it, in a group (roadmap 8.91c)
    #: A parked session's forced bring-back that failed: its error
    #: ``(code, params)`` (``reason`` is the bring-back's).
    bg_err: tuple = ()


CARD_STOPPED = Msg("card.stopped", "⚠️ <b>{session}</b> · Stopped",
                   note="The busy card after a Stop", eg={"session": "x1"})
CARD_STOPPED_BY = Msg("card.stopped_by", "⚠️ <b>{session}</b> · Stopped by {person}",
                      note="The busy card after a Stop in a group: who stopped it",
                      eg={"session": "x1", "person": "@bob"})
CARD_DENIED = Msg("card.denied", "🚫 <b>{session}</b> · Denied",
                  note="The busy card after a refused permission ended the turn",
                  eg={"session": "x1"})
CARD_DENIED_BY = Msg("card.denied_by", "🚫 <b>{session}</b> · Denied by {person}",
                     note="The busy card after a refusal in a group: who refused",
                     eg={"session": "x1", "person": "@bob"})


def _stopped_card_text(label: str, by: str = "", tr=None) -> str:
    """The busy card after a Stop: `⚠️ x1 · Stopped`, and in a group who
    stopped it, `⚠️ x1 · Stopped by @bob` (roadmap 8.91c). In *tr*'s
    language (English when None)."""
    tr = tr or i18n.get("en")
    if by:
        return tr.html(CARD_STOPPED_BY, session=label, person=Command(by))
    return tr.html(CARD_STOPPED, session=label)


def _refused_card_text(label: str, by: str = "", *, refused: bool = True,
                       tr=None) -> str:
    """The busy card after an answer typed into Claude Code's dialog ended
    the turn (roadmap 8.99): `🚫 x1 · Denied`, in a group `🚫 x1 · Denied
    by @bob`. When the typed answer was not a refusal but the turn still
    ended like an interrupt, it reads as a Stop."""
    if not refused:
        return _stopped_card_text(label, by, tr)
    tr = tr or i18n.get("en")
    if by:
        return tr.html(CARD_DENIED_BY, session=label, person=Command(by))
    return tr.html(CARD_DENIED, session=label)


# Resume, stop and switch replies (HTML unless said; the calling chat's,
# a pop-up the tapper's).
TOAST_STOP_STALE = Msg("toast.stop_stale", "That task already finished - use the current card",
                       note="Pop-up: Stop tapped on the card of a turn that ended")
NOT_WORKING = Msg("route.not_working", "<b>{session}</b> is not working.",
                  note="HTML. Stop asked of a session that is idle", eg={"session": "x1"})
NO_SESSION_HERE = Msg("route.no_session_here", "⚠️ No session named <b>{session}</b> here.",
                      note="HTML. A typed name matches no session in this chat",
                      eg={"session": "x1"})
UNKNOWN_SESSION = Msg("route.unknown_session", "⚠️ Unknown session: {session}",
                      note="A typed /name that is no session here", eg={"session": "x1"})
RESUME_NOT_FOUND = Msg("sess.resume_not_found",
                       "⚠️ No session named <b>{session}</b> in history.\nSend {command} with no "
                       "name to see what's available.",
                       note="/resume of a name nobody had; {command} is /resume",
                       eg={"session": "x1", "command": "/resume"})
RESUME_RUNNING = Msg("sess.resume_running",
                     "⚠️ <b>{session}</b> is already running.\nTap {command} in the keyboard to "
                     "switch to it.", note="/resume of a running session; {command} is its "
                                           "/name", eg={"session": "x1", "command": "/x1"})
RESUME_NO_TRANSCRIPT = Msg("sess.resume_no_transcript",
                           "⚠️ Session <b>{session}</b> has no resumable transcript on disk.\n"
                           "Start a fresh one with {command}.",
                           note="/resume of a session with nothing to resume from; {command} "
                                "is /new with its name", eg={"session": "x1",
                                                              "command": "/new x1"})
RESUME_FAILED = Msg("sess.resume_failed", "❌ Couldn't resume <b>{session}</b>: {error}",
                    note="The resume failed; {error} is why (English)",
                    eg={"session": "x1", "error": "dtach not installed"})
RESUMED = Msg("sess.resumed", "♻️ Resumed <b>{session}</b>", note="The session is back",
              eg={"session": "x1"})
#: One whole sentence with its reason (the reason is
#: ``transport.RESUME_FULL_NEEDS_ADMIN``'s words, not glued in).
RESUMED_IN_AUTO = Msg("sess.resumed_in_auto",
                      "♻️ Resumed <b>{session}</b> in Auto mode: Full access mode needs an admin.",
                      note="The session came back in Auto mode, not Full access, because the "
                           "person who resumed it is not an admin. Auto and Full access are "
                           "the mode names",
                      eg={"session": "x1"})
RESUME_LAST_RESPONSE = Msg("sess.resume_last_response",
                           "<i>📜 Last response from this session</i>\n"
                           "<blockquote>{preview}</blockquote>",
                           note="Under a resumed session: the start of its last answer",
                           eg={"preview": "All tests pass."})

# Claude Code background jobs: a session whose conversation moved into a
# background job (``←`` in Claude Code) is parked, and nothing is typed into
# it until someone brings it back (aipager/claude_jobs.py). The words live in
# aipager/bg_text.py (session_parity and the command line use them too).
from aipager.bg_text import (  # noqa: E402,F401 - re-exported
    BG_ANSWER_REFUSED,
    BG_BACK,
    BG_BTN_BRING_BACK,
    BG_BTN_LEAVE,
    BG_BTN_STOP_AND_BRING,
    BG_BUSY_QUESTION,
    BG_FAILED,
    BG_FAILURES,
    BG_HELD_NOTICE,
    BG_LEFT,
    BG_NO_CONVERSATION,
    BG_PARK_NOTICE,
    BG_REFUSED_PARKED,
    BG_TOAST_ANSWER_REFUSED,
    BG_TOAST_BRINGING_BACK,
    BG_TOAST_FAILED,
    BG_TOAST_NO_CONVERSATION,
    BG_TOAST_NOT_PARKED,
    BG_TOAST_REFUSED_PARKED,
    bg_error,
    bg_outcome_html,
    bg_outcome_text,
    stop_refused_html,
    stop_refused_text,
)

CARD_DENIED_TERMINAL = Msg(
    "card.denied_terminal", "🚫 <b>{session}</b> · Denied in the terminal",
    note="The busy card after a permission was refused at the computer's own terminal",
    eg={"session": "x1"})
CARD_STOPPED_TERMINAL = Msg(
    "card.stopped_terminal", "⚠️ <b>{session}</b> · Stopped in the terminal",
    note="The busy card after a question was dismissed at the computer's own terminal",
    eg={"session": "x1"})


def _terminal_card_text(label: str, *, refused: bool, tr=None) -> str:
    """The busy card after Claude Code's own dialog was answered at the
    terminal in a way that ended the turn (roadmap 8.113): `🚫 x1 · Denied
    in the terminal` for a permission, `⚠️ x1 · Stopped in the terminal`
    for a question left with Escape. In *tr*'s language (English when
    None)."""
    tr = tr or i18n.get("en")
    return tr.html(CARD_DENIED_TERMINAL if refused else CARD_STOPPED_TERMINAL,
                   session=label)


#: How long, after an answer was TYPED into Claude Code's permission dialog
#: (the keystroke fallback), the daemon watches for the turn having ended
#: like an interrupt (roadmap 8.99). For a refusal, also the bounded grace
#: after which the turn is treated as ended without seeing the transcript's
#: marker, as long as nothing shows Claude carried on. Far below the 900 s
#: tool-in-flight cap, which was the only thing that released it before.
KEYSTROKE_ANSWER_WATCH_SECONDS: float = 10.0
#: How often the watch above reads the transcript's end.
KEYSTROKE_ANSWER_POLL_SECONDS: float = 0.5

#: How long after typing a message that may hold a context-dependent
#: invisible character aipager waits for it to be picked up before
#: pressing Enter once more (roadmap 8.124). An accepted prompt fires
#: UserPromptSubmit within ~0.15 s.
HELD_BACK_ENTER_AFTER_SECONDS: float = 3.0
#: After pressing a held-back message's Enter, before typing the next one.
HELD_BACK_SETTLE_SECONDS: float = 0.3
#: How many times the held-back check runs while a dialog may be on top
#: (ten minutes: a permission dialog can wait that long for its answer).
HELD_BACK_TRIES: int = 200
#: A message that met a dialog while it was typed (roadmap 8.143): how long
#: to wait for that dialog to be on record before holding the message
#: anyway, how often to look, and how many times it is typed again when the
#: dialog is gone without being recorded.
MET_DIALOG_WAIT_SECONDS: float = 2.0
MET_DIALOG_POLL_SECONDS: float = 0.1
MET_DIALOG_TRIES: int = 2
#: session name -> the note of the last message that may still sit held
#: back in Claude Code's input box (waiting for its second Enter).
_HELD_BACK: dict[str, Path] = {}

#: session name -> the running watch (one per session; a newer answer
#: replaces it). Also keeps a reference so the task is not collected.
_KEYSTROKE_WATCHES: dict[str, asyncio.Task] = {}


@dataclass
class KillOutcome:
    """Result of :meth:`SessionOpsMixin._kill_session_core`.

    ``result`` is one of ``"killed"``, ``"still_running"``,
    ``"not_found"``, ``"resuming"`` (a resume is in flight — the kill was
    refused and NOTHING was destroyed; see the core's own docstring).
    """

    result: str
    label: str
    session_name: str


@dataclass
class ResumeOutcome:
    """Result of :meth:`SessionOpsMixin._do_resume_core`.

    ``reason`` is one of ``"not_gone"``, ``"no_transcript"``,
    ``"launch_failed"``, ``"resumed"``, and when a Claude Code background
    job holds the conversation ``"bg_busy"`` (it is working: nothing was
    done), ``"bg_stop_failed"`` (``bg_err`` says why) or
    ``"bg_no_conversation"``. Never ``"not_found"`` — the
    caller resolves the session before calling the core, and handles a
    missing session itself (see the core's own docstring).
    """

    ok: bool
    reason: str
    err: str = ""
    #: ``(code, params)`` of ``aipager.bg_text.BG_ERRORS`` for
    #: ``bg_stop_failed``; empty otherwise.
    bg_err: tuple = ()


@dataclass
class RestartOutcome:
    """Result of :meth:`SessionOpsMixin._kill_and_relaunch_core` (and its
    thin callers :meth:`_perms_switch_core` / :meth:`_restart_session_core`).

    ``reason`` is one of ``"not_live"``, ``"already_restarting"``,
    ``"still_stopping"``, ``"launch_failed"``, ``"done"``; for a Claude
    Code background job (:meth:`SessionOpsMixin._bring_back_core`)
    ``"not_parked"``, ``"bg_busy"``, ``"bg_stop_failed"`` (``bg_err``
    says why), ``"bg_no_conversation"``; ``"parked"`` for an in-place
    mode switch refused while parked. Never
    ``"not_found"`` — like the other cores in this module, the caller
    resolves the session before calling in.
    """

    ok: bool
    reason: str
    label: str
    mode: str = "ask"
    err: str = ""
    #: True for an in-place Ask<->Auto switch (Shift+Tab, no restart).
    in_place: bool = False
    #: ``(code, params)`` of ``aipager.bg_text.BG_ERRORS`` for
    #: ``bg_stop_failed``; empty otherwise.
    bg_err: tuple = ()
    #: For ``bg_busy``: seconds until the park is
    #: ``claude_jobs.PARK_SETTLE_SECONDS`` old, when its youth was the only
    #: reason (a just-forked job reads idle before it continues a moved
    #: turn); 0 when the job is busy, or its state unknown, for real.
    retry_after: float = 0.0


@dataclass
class ClearQueueOutcome:
    """Result of :meth:`SessionOpsMixin._clear_queue_core`."""

    ok: bool
    label: str
    dropped: int = 0


@dataclass
class CompactOutcome:
    """Result of :meth:`SessionOpsMixin._compact_session_core`.

    ``reason`` is one of ``"not_live"``, ``"parked"`` (on a Claude Code
    background job), ``"queue_full"``, ``"send_failed"``, ``"queued"``,
    ``"sent"``.
    """

    ok: bool
    reason: str
    label: str


@dataclass
class ModelSwitchOutcome:
    """Result of :meth:`SessionOpsMixin._switch_model_core`.

    ``reason`` is one of ``"not_live"``, ``"parked"`` (on a Claude Code
    background job), ``"busy"``, ``"prompt_open"``, ``"unknown"``,
    ``"switch_pending"``, ``"send_failed"``, ``"sent"``.
    ``detail`` is the operator-facing reason for a refusal.
    ``previous_model`` is the statusline's model name at the moment
    ``/model`` was typed — what :func:`await_model_change` compares
    against.
    """

    ok: bool
    reason: str
    label: str
    previous_model: str = ""
    detail: str = ""


# How long a switch waits for the statusline to report the new model
# before it is reported as "not confirmed". A switch that is still
# unconfirmed after this long is most likely waiting behind Claude Code's
# own "Switch model?" confirmation in the terminal (or the statusline had
# nothing new to say — the same model under another name).
MODEL_SWITCH_CONFIRM_SECONDS = 15.0
# How long an unconfirmed switch blocks the next one (see
# TrackedSession.model_switch_pending_until). Must be at least the
# PreModelSwitch marker's TTL (model_switch_marker.MARKER_TTL_SECONDS):
# a marker must never outlive the refusal that stops a second /model.
MODEL_SWITCH_PENDING_SECONDS = 60.0
_MODEL_SWITCH_POLL_SECONDS = 0.25
# How long a message sent right after a typed `/model` is held
# (TrackedSession.model_check_until). Claude Code (2.1.303) checks the
# model with a request before it switches and gives up after 15 s; text
# typed during the check is lost (probed with a slow model: a message
# 0.5 s after the `/model` never reached the model, one 6 s after did).
# The hold ends earlier as soon as the statusline reports the switch:
# typing right then was taken every time.
MODEL_CHECK_HOLD_SECONDS = 17.0
# How long the confirmation waits for Claude Code to save the switch as
# the default for new sessions after the statusline reported it.
MODEL_DEFAULT_SAVE_SECONDS = 1.0


#: The words for each refusal code of :func:`model_switch_refusal`: the
#: Mini App's own keys (``miniapp.sessions``), one key per sentence for
#: both surfaces.
MODEL_REFUSAL_MSGS: dict[str, Msg] = {
    "not_live": _mini_sessions.MODEL_SWITCH_NOT_LIVE_REASON,
    "busy": _mini_sessions.MODEL_SWITCH_BUSY_REASON,
    "prompt_open": _mini_sessions.MODEL_SWITCH_PROMPT_OPEN_REASON,
    "unknown": _mini_sessions.MODEL_SWITCH_UNKNOWN_REASON,
    "switch_pending": _mini_sessions.MODEL_SWITCH_PENDING_REASON,
}
#: A /model whose switch the statusline never confirmed (the Mini App's
#: key too).
MODEL_UNCONFIRMED = _mini_sessions.MODEL_SWITCH_UNCONFIRMED


def model_refusal_text(tr, code: str, detail: str) -> str:
    """Why /model was refused, in *tr*'s language (plain text): the key of
    *code*, or *detail* as received for a code without one. In English,
    *detail* itself: the same words (a test keeps the keys equal to the
    Mini App's)."""
    msg = MODEL_REFUSAL_MSGS.get(code)
    return tr.text(msg) if msg is not None and tr.code != "en" else detail


def model_switch_refusal(sess) -> tuple[str, str] | None:
    """Why ``/model`` must not be typed into ``sess`` right now, as
    ``(reason_code, operator_text)``, or ``None`` when it may.

    The single rule both surfaces apply (roadmap 8.35 R2): the chat's
    Models keyboard and the Mini App's picker refuse in exactly the same
    states with exactly the same words. An allow-list — only an IDLE
    session with no switch outstanding may take one:

    - GONE: nothing to type into.
    - BUSY: Claude Code's ``/model`` is an ``immediate`` command — it runs
      mid-turn rather than queueing behind the turn — so it would change
      the model under the running turn and can open Claude Code's
      "Switch model?" confirmation where nobody in chat can see it.
    - INTERACTIVE: a dialog is open, and keystrokes would answer it.
    - UNKNOWN (and anything else): aipager does not know yet.
    - a switch still pending: its "Switch model?" dialog may be up.
    """
    from aipager.miniapp.sessions import (
        MODEL_SWITCH_BUSY_REASON,
        MODEL_SWITCH_NOT_LIVE_REASON,
        MODEL_SWITCH_PENDING_REASON,
        MODEL_SWITCH_PROMPT_OPEN_REASON,
        MODEL_SWITCH_UNKNOWN_REASON,
    )

    if sess.status == Status.GONE:
        return "not_live", MODEL_SWITCH_NOT_LIVE_REASON
    if sess.status == Status.BUSY:
        return "busy", MODEL_SWITCH_BUSY_REASON
    if sess.dialog_is_open():
        return "prompt_open", MODEL_SWITCH_PROMPT_OPEN_REASON
    if sess.status != Status.IDLE:
        return "unknown", MODEL_SWITCH_UNKNOWN_REASON
    if sess.model_switch_pending():
        return "switch_pending", MODEL_SWITCH_PENDING_REASON
    return None


def _marker_dir() -> str:
    """Where the PreModelSwitch marker lives: beside the daemon's control
    socket, the same directory ``aipager-hook`` derives from the same
    precedence (``notify_hook.SOCKET_PATH``)."""
    from aipager import config
    return os.path.dirname(config.SOCKET_PATH) or "/tmp"


def mark_model_switch_sent(sess, model: str) -> str:
    """Record that ``/model <model>`` is about to be typed into ``sess``;
    return the model the statusline reported before it (the confirmation
    baseline).

    Also leaves the marker ``aipager-hook`` checks on PreModelSwitch, so
    Claude Code (2.1.251+) switches without its "Switch model?" cache
    confirmation — for this switch only. Best-effort: without the marker
    the dialog simply appears, as it would without aipager. The marker
    only ever matches a known Claude session id; SessionStart reports it
    at launch, long before any switch."""
    from aipager.dtach import model_switch_marker

    previous = sess.model_name or ""
    now = time.monotonic()
    sess.model_switch_pending_until = now + MODEL_SWITCH_PENDING_SECONDS
    sess.model_switch_pending_from = previous
    sess.model_check_until = now + MODEL_CHECK_HOLD_SECONDS
    sess.model_default_known, sess.model_default_from = _read_default()
    sess.model_switch_to = model
    if not model_switch_marker.write_marker(
        _marker_dir(), sess.name, model, sess.claude_session_id or "",
    ):
        log.debug("[%s] model switch marker not written", sess.label)
    return previous


def claude_default_model(path=None) -> str | None:
    """The model Claude Code starts a new session on: ``model`` in its user
    settings (*path*, by default the ``settings.json`` of the config home
    aipager's sessions use), or ``None`` when that is unset or cannot be
    read."""
    from aipager import claude_paths  # local: keeps this module light

    if path is None:
        path = claude_paths.for_sessions().user_settings
    model = claude_paths.read_settings(path).get("model")
    return model.strip() if isinstance(model, str) and model.strip() else None


def _read_default(path=None) -> tuple[bool, str | None]:
    """``(readable, model)`` for Claude Code's default model in the sessions'
    user settings: a missing file reads as no default; one that cannot be
    read whole, or is not a JSON object, as unknown (``(False, None)``)."""
    import json

    from aipager import claude_paths  # local: keeps this module light

    if path is None:
        path = claude_paths.for_sessions().user_settings
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return True, None
    except (OSError, ValueError, RecursionError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    model = data.get("model")
    return True, (model.strip() if isinstance(model, str) and model.strip() else None)


async def await_default_saved(
    sess, *, timeout: float | None = None,
    interval: float = _MODEL_SWITCH_POLL_SECONDS / 2,
) -> str | None:
    """After a switch the statusline confirmed: the model Claude Code saved
    as the default for new sessions, or ``None``.

    Claude Code (2.1.303) saves a ``/model`` typed in an interactive session
    in its user settings ("Set model to X and saved as your default for new
    sessions"), right after it switches, as typed (an alias in lower case:
    probed). This waits up to *timeout* (default
    :data:`MODEL_DEFAULT_SAVE_SECONDS`) for that ``model`` to be the one this
    switch typed (``model_switch_to``) and differ from the one read when it
    was typed (``model_default_from``). An unchanged default (the session
    switched to the model that already was the default, or the save failed)
    says nothing, and so does a default some other switch saved meanwhile."""
    if timeout is None:
        timeout = MODEL_DEFAULT_SAVE_SECONDS
    from aipager import claude_paths  # local: keeps this module light

    before = sess.model_default_from
    target = (sess.model_switch_to or "").strip().casefold()
    path = claude_paths.for_sessions().user_settings   # once, not every poll
    deadline = time.monotonic() + timeout
    while True:
        now_default = claude_default_model(path)
        if (now_default is not None and now_default != before
                and now_default.casefold() == target):
            return now_default
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        await _sleep(min(interval, remaining))


def holds_member_to_default(bot, user_id, chat_id) -> bool:
    """Does a model switch by *user_id* in *chat_id* change only that
    session? Yes for a person held to the chat's root folder
    (:func:`aipager.member_root.restricted_root`: a role without
    ``can_manage``, and, failing closed, someone who is not a member or has
    an unknown role): Claude Code's default model for new sessions is the
    machine owner's choice, so it is put back
    (:func:`restore_default_model`). Not in personal mode, nor for an owner
    or admin, who keep Claude Code's own behaviour and are told."""
    from aipager import member_root  # local: keeps this module light

    return member_root.restricted_root(bot, user_id, chat_id) is not None


#: What :func:`restore_default_model` did.
RESTORED, NOT_OURS, RESTORE_FAILED = "restored", "not_ours", "failed"


def restore_default_model(sess, saved: str, *, before=None, known=None) -> str:
    """Put Claude Code's default model back to what it was when the switch
    was typed (``model_default_from``; the key removed when there was none),
    in the sessions' user settings. Only while that file still holds
    *saved*, the model this switch saved: a change someone else made
    meanwhile is kept (:data:`NOT_OURS`). :data:`RESTORED` when the old
    default is back; :data:`RESTORE_FAILED` when it could not be written,
    or the file could not be read when the switch was typed (the owner's
    default is unknown then, so it is not guessed).

    Measured with Claude Code 2.1.303: a running session keeps the model it
    switched to when its settings file's ``model`` changes under it (by an
    atomic replace or an in-place write), so only new sessions see the old
    default again. Never written in a relative config folder (one inside
    each session's own folder), nor when the file cannot be read whole
    (``claude_bootstrap._update_json``)."""
    from aipager import claude_bootstrap, claude_paths  # local: keeps this module light

    if known is None:
        known, before = sess.model_default_known, sess.model_default_from
    paths = claude_paths.for_sessions()
    if paths.relative or not known:
        return RESTORE_FAILED
    ours = {"still": None}

    def change(data: dict) -> bool:
        current = data.get("model")
        ours["still"] = isinstance(current, str) and current.strip() == saved
        if not ours["still"]:
            return False
        if before is None:
            data.pop("model", None)
        else:
            data["model"] = before
        return True

    if claude_bootstrap._update_json(paths.user_settings, change):
        return RESTORED
    return NOT_OURS if ours["still"] is False else RESTORE_FAILED


async def settle_default(bot, sess, user_id, chat_id, *, confirmed: bool = True) -> str | None:
    """After a typed switch: the default Claude Code saved that the person
    should be told about, or ``None``. *confirmed*: the statusline reported
    the switch.

    Anyone :func:`holds_member_to_default` does not hold is told, after a
    confirmed switch, as in the terminal. For a held member the old default
    is put back whatever the statusline said (Claude Code saves the default
    only once it switched, which can be as late as the check's window), and
    nothing is said: the switch is that session's only. A default someone
    else changed meanwhile is left as it is, also silently. Only when the
    restore fails (the file cannot be written, or could not be read when
    the switch was typed) is the member told, as anyone else is."""
    if not holds_member_to_default(bot, user_id, chat_id):
        return await await_default_saved(sess) if confirmed else None
    # As they were when this switch was typed: another switch of the same
    # session during the wait records its own.
    known, before = sess.model_default_known, sess.model_default_from
    wait = MODEL_DEFAULT_SAVE_SECONDS
    if not confirmed:
        wait = max(wait, sess.model_check_until - time.monotonic() + wait)
    saved = await await_default_saved(sess, timeout=wait)
    if saved is None:
        return None
    result = restore_default_model(sess, saved, before=before, known=known)
    if result == RESTORED:
        log.info("[%s] model switch by a member: Claude Code's default model put back",
                 sess.label)
        return None
    if result == NOT_OURS:
        log.info("[%s] model switch by a member: Claude Code's default model changed "
                 "again meanwhile; left as it is", sess.label)
        return None
    log.warning("[%s] model switch by a member: Claude Code's default model could not "
                "be put back", sess.label)
    return saved


#: Under a confirmed switch (the chat's reply, the Ready card).
MODEL_DEFAULT_SAVED = Msg(
    "route.model_default_saved",
    "📌 Claude Code made it the default model for new sessions too.",
    note="under a /model switch: Claude Code itself saves a typed /model as the default "
         "for every new session, as it says in the terminal")


def clear_model_switch_pending(sess) -> None:
    """The typed ``/model`` is accounted for (confirmed, or never sent)."""
    from aipager.dtach import model_switch_marker

    sess.model_switch_pending_until = 0.0
    sess.model_switch_pending_from = ""
    model_switch_marker.clear_marker(_marker_dir(), sess.name)


async def await_model_change(
    sess, previous: str, *, timeout: float | None = None,
    interval: float = _MODEL_SWITCH_POLL_SECONDS,
) -> str | None:
    """Wait for the statusline to report a model other than ``previous``.

    Returns the new ``sess.model_name``, or ``None`` if it did not change
    within ``timeout`` (default :data:`MODEL_SWITCH_CONFIRM_SECONDS`).
    ``model_name`` is Claude Code's own display name for the model
    (``Opus 5.5``), fed by the statusline hook — the only evidence the
    daemon gets that a switch really happened. An empty reading is not
    evidence of anything, and neither is any reading when there was no
    baseline: a session that had not reported a model yet would count
    its FIRST report — possibly the old model — as the switch. That
    case returns ``None`` at once.

    The session's pending-switch mark needs no clearing here: it stops
    counting as soon as the statusline reports a model other than the one
    it recorded (``TrackedSession.model_switch_pending``), so the next
    switch is allowed straight away.
    """
    if timeout is None:
        timeout = MODEL_SWITCH_CONFIRM_SECONDS
    if not previous:
        return None
    deadline = time.monotonic() + timeout
    while True:
        current = sess.model_name or ""
        if current and current != previous:
            # The marker should already be claimed by the hook; tidy up
            # in case Claude Code never asked (no PreModelSwitch support).
            from aipager.dtach import model_switch_marker
            model_switch_marker.clear_marker(_marker_dir(), sess.name)
            return current
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        await _sleep(min(interval, remaining))


@dataclass
class RenameOutcome:
    """Result of :meth:`SessionOpsMixin._rename_session_core`.

    Has no failure path — validation (syntax + collision) happens in
    server.py, before the core is ever called.
    """

    ok: bool
    previous_label: str
    new_label: str
    changed: bool


# ---- Reply context (design.md Part 2) --------------------------------
#
# Pure wording helpers for SessionOpsMixin._build_reply_context. Kept as
# plain module-level functions (not methods) since none of them touch
# `self` — the same separation-of-concerns split as transport.py's pure
# helpers. Internal to this module; the Tester must observe their output
# only via read_snapshot(...)["reply_context"] or pending_queue[...][3]
# (entrypoints.md), never call these directly.

def _reply_author(reply_to, bot_id: int) -> str | None:
    """``"you"`` | ``"Telegram user <id>"`` | ``None``.

    Numeric id only, never a label — labels are mutable. ``None`` when
    ``from_user`` is absent (anonymous-admin / channel-post messages),
    in which case every template below drops the attribution clause
    entirely rather than guessing.
    """
    user = getattr(reply_to, "from_user", None)
    if user is None:
        return None
    if getattr(user, "id", None) == bot_id:
        return "you"
    return f"Telegram user {user.id}"


def _reply_fmt_time(date) -> str:
    """``"21:40"`` from a Telegram ``Message.date``, or ``""`` if unusable."""
    try:
        return date.strftime("%H:%M")
    except (AttributeError, TypeError, ValueError):
        return ""


def _reply_attribution_clause(date, author: str | None) -> str:
    """``"sent 21:40, by you"`` / ``"sent 21:40, from Telegram user
    256113222"`` / ``"sent 21:40"`` (no attribution available)."""
    t = _reply_fmt_time(date)
    if not author:
        return f"sent {t}" if t else ""
    verb = "by" if author == "you" else "from"
    return f"sent {t}, {verb} {author}" if t else f"{verb} {author}"


def _whole_message_context(
    excerpt: str, date, author: str | None, *, with_file: bool, file_path,
) -> str:
    attribution = _reply_attribution_clause(date, author)
    where = f" ({attribution})" if attribution else ""
    if with_file:
        tail = f"Full text: {file_path} (read only if needed)."
    else:
        tail = "(message was queued while busy — full text was not retained)."
    return (
        "The user's message is a reply to an earlier message in this "
        f"session{where}. They are pointing at it for reference — it is "
        f"not itself a new instruction. Excerpt: \"{excerpt}\"\n{tail}"
    )


def _nontext_context(date, author: str | None) -> str:
    attribution = _reply_attribution_clause(date, author)
    where = f" ({attribution})" if attribution else ""
    return (
        "The user's message is a reply to an earlier non-text message in "
        f"this session{where} — no text content is available to show. "
        "They are pointing at it for reference; it is not itself a new "
        "instruction."
    )


def _highlight_context(
    fragment: str, date, author: str | None, *, manual: bool, file_path=None,
) -> str:
    verb = "highlighting" if manual else "quoting"
    part = "this part of" if manual else "this quoted part of"
    if author == "you":
        owner = "your earlier message"
    elif author:
        owner = f"{author}'s earlier message"
    else:
        owner = "an earlier message"
    t = _reply_fmt_time(date)
    when = f" (sent {t})" if t else ""
    tail = f"\nFull text: {file_path} (read only if needed)." if file_path else ""
    return (
        f"The user replied while {verb} {part} {owner}{when}: "
        f"\"{fragment}\". They are pointing at that specific passage — "
        f"it is not itself a new instruction.{tail}"
    )


def _external_quote_context(fragment: str) -> str:
    return (
        "The user replied while quoting this fragment from a message I "
        "can't otherwise see (from another chat, a forum topic, or a "
        "message that's no longer available): "
        f"\"{fragment}\". They are pointing at that specific passage — "
        "it is not itself a new instruction."
    )


def _clear_turn_open(session_name: str) -> None:
    """The daemon's half of the hook's turn state (roadmap 8.77): a
    session it stops, kills, relaunches or launches has no turn running.
    Best-effort (``policy_snapshot.clear_turn_open`` never raises)."""
    from aipager.policy_snapshot import clear_turn_open
    clear_turn_open(session_name)


def _candidate_text(c: dict) -> str:
    """The text a held candidate was typed with: a note's ``body``, a
    queued target's ``raw_text``."""
    return str(c.get("body") or c.get("raw_text") or "")


def queued_chars(sess: TrackedSession, held: list[dict]) -> int:
    """How many characters an Escape can move from Claude Code's queue into
    its input box: every message the transcript shows still queued, one
    line break each, or, once the transcript has written their fate,
    *held*'s own texts. The queue wipe presses Ctrl+U at least this many
    times (each press deletes at least one character,
    :func:`aipager.dtach.inject.discard_presses`)."""
    queued = read_still_queued(
        sess.stream_transcript_path or sess.transcript_path or "")
    return max(sum(len(text) + 1 for text in queued),
               sum(len(_candidate_text(c)) + 1 for c in held))


def _drop_held_back(sess: TrackedSession, notes: list[dict]) -> list[dict]:
    """Forget the second Enter a message held back in Claude Code's input
    box still waits for (roadmap 8.124), and return its note among *notes*
    (``[]`` when it was picked up meanwhile): Stop wipes its text."""
    note_path = _HELD_BACK.pop(sess.name, None)
    if note_path is None:
        return []
    return [n for n in notes if n.get("_path") == Path(note_path)]


def held_by_claude(sess: TrackedSession, candidates: list[dict]) -> list[dict]:
    """The *candidates* (outstanding notes, queued targets) that Claude
    Code's transcript shows it still holding in its queue: each needs an
    ``enqueue`` line carrying its text with no fate written after it
    (:func:`aipager.transcript.read_still_queued`, roadmap 8.37).

    This is what a teardown's queue wipe (Escape, then KillLine) is gated
    on. Anything else aipager remembers - a note nothing consumed, a
    target the scan never settled - is not evidence: an Escape into
    Claude Code's empty queue interrupts the running turn.

    A note is matched by its ``body`` (the exact text typed, marker
    included), a target by its ``raw_text``. Longest text first, and
    each queued line backs one candidate only, so a short message ("ok")
    cannot borrow a longer one's line.
    """
    if not candidates:
        return []
    queued = read_still_queued(
        sess.stream_transcript_path or sess.transcript_path or "")
    if not queued:
        return []

    # A message sent by a second Enter (roadmap 8.124) was queued without
    # the invisible character Claude Code removed: those do not count.
    queued = [invisible.without_context_dependent(q) for q in queued]
    held: set[int] = set()
    for i in sorted(range(len(candidates)),
                    key=lambda k: -len(_candidate_text(candidates[k]))):
        text = invisible.without_context_dependent(_candidate_text(candidates[i]))
        if not text:
            continue
        for j, content in enumerate(queued):
            if text in content:
                held.add(i)
                del queued[j]
                break
    return [c for i, c in enumerate(candidates) if i in held]


class SessionOpsMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    def _adopt_trigger(
        self, sess: TrackedSession, msg_id: int | None, text: str | None = None,
        *, driver_user_id: int | None = None,
    ) -> None:
        """R1 (design.md "turn anchor follows consumption"): move the
        reply target only for a message that STARTS a turn.

        A send while BUSY must never move ``trigger_msg_id`` — the turn
        already running owns the target until consumption (R2) says
        otherwise. Only a non-BUSY session adopts ``msg_id`` immediately,
        because for that one message send and consumption are the same
        event. ``_drain_next_queued`` (a held message released at an
        idle moment) and the hook's ``queue_pickup`` handler both start
        or move a turn by definition and correctly keep setting the
        target unconditionally — neither goes through this method.
        """
        if sess.status == Status.BUSY:
            return
        sess.trigger_msg_id = msg_id
        if text is not None:
            sess.last_prompt = text
            sess.turn_prompt = text
            sess.last_prompt_driver_user_id = driver_user_id

    def _typed_name_foreign(self, session_name: str, chat_id: int | None) -> bool:
        """True when the internal name rebuilt from a typed ``/<label>``
        (``claude-<label>``) belongs to a chat other than *chat_id*
        (roadmap 8.76). Internal names carry the owning chat's id
        (``__d<user id>`` / ``__g<group id>``) and ids are not secret, so
        without this a group member could type ``/api__d555 ...`` and
        drive the operator's private DM session.

        Foreign when: the name ends in a scope suffix naming another chat;
        or the registry knows it stamped with another chat; or (scope
        mode) it has no chat stamped and the chat it would be stamped
        with, its home chat (``scope.home_scope``), is not *chat_id*
        (roadmap 8.72: never "any chat"; adopting it stamps it with that
        chat). Personal/legacy mode with no suffix and no stamp: never
        foreign (one chat).
        """
        from aipager.state import chat_from_name
        from aipager.bot.transport import home_chat

        # A group Telegram upgraded to a supergroup (roadmap 8.87): a name
        # still carrying the old id belongs to the new chat, so it is
        # accepted there and refused everywhere else.
        own = chat_from_name(session_name)
        if own is not None and own[0] != chat_id:
            return True
        known = self.registry.get(session_name)
        if known is not None and known.scope_chat_id:
            return known.scope_chat_id != chat_id
        if own is not None:
            return False
        if self.scopes is None:
            return False
        home = home_chat()
        return home is None or home[0] != chat_id

    def _adopt_by_typed_name(self, session_name: str, target_label: str,
                             chat_id: int | None) -> TrackedSession | None:
        """Adopt the session `session_name`, which was rebuilt from what
        the operator typed in *chat_id*, or ``None`` when that name
        belongs to another chat (:meth:`_typed_name_foreign`, roadmap
        8.76): the caller then answers its "Unknown session" reply and
        touches nothing.

        `/<label>` falls back to reconstructing `claude-{label}` and
        adopting whatever is alive under it. A session adopted that way
        should answer to the label that produced its name, rather than
        keeping whatever `get_or_create` derived from the internal name
        or revived from a recent kill.

        That only holds for a name the registry did not already know.
        A tracked session's label is authoritative: `/rename` moves the
        label while the internal name stays put, so the pre-rename name
        goes on resolving to a live socket indefinitely, and re-labelling
        an already-tracked entry here would silently undo the rename.
        In scope mode an adopted session is stamped with its chat by
        `get_or_create` (its suffix's, else the home chat; a known one
        still unstamped too, roadmap 8.72), which the check above has
        just proved is *chat_id*; in personal/legacy mode an unsuffixed
        name stays unstamped, as before.
        """
        if self._typed_name_foreign(session_name, chat_id):
            return None
        known = self.registry.get(session_name) is not None
        sess = self.registry.get_or_create(session_name)
        if not known:
            sess.label = target_label
        return sess

    def _prompt_marker(self, sess, member) -> str:
        """Identity/origin marker prepended to Telegram free-text prompts.

        ``member`` is THIS message's sender, resolved by the caller from
        the id it was handed (roadmap 8.74), never the session's last
        driver: a message must never reach Claude labelled as someone
        else. DM scope gives ``[via Telegram · @label]``; a group adds
        ``· role:X``. A sender aipager cannot resolve (unknown, or removed
        by a reload) still gets ``[via Telegram]``: the marker is what
        tells the hook the prompt came from Telegram, and without it the
        prompt would run as an unrestricted terminal prompt. Its note
        already carries the floor rules. Personal/legacy mode: no marker.
        """
        if self.scopes is None:
            return ""
        if member is None:
            return "[via Telegram]"
        if sess.scope_kind == "group":
            return f"[via Telegram · @{member.label} · role:{member.role}]"
        return f"[via Telegram · @{member.label}]"

    def _command_needs_admin(self, text: str,
                             driver_user_id: int | None, *,
                             chat_id: int | None) -> bool:
        """True when ``text`` is a slash command its sender may not send
        (roadmap 8.74, D-G).

        Scope mode only. A slash command is typed raw, with no marker, and
        a prompt-type command or skill runs a whole turn on its own text,
        so a role without ``bypass_role_denies`` (and a sender aipager
        cannot resolve) may send only aipager's own keyboard commands
        (the Commands keyboard, plus ``/compact``, which aipager's own
        Compact button and the Mini App type) and model switches. Owners
        and admins send any. Personal and legacy team mode: never.

        The sender's role is the one in *chat_id* (roadmap 8.73): the chat
        the command came from, else the session's own chat
        (``AuthMixin._attribution_chat``); ``None``, nobody (refused).
        """
        if self.scopes is None:
            return False
        stripped = text.strip()
        if not stripped.startswith("/"):
            return False
        allowed = {"/compact",
                   *getattr(self, "_command_map", {}).values(),
                   *getattr(self, "_model_map", {}).values()}
        if stripped in {c.strip() for c in allowed}:
            return False
        if _MODEL_SWITCH_COMMAND.fullmatch(stripped):
            return False
        member = self._driver_user_by_id(driver_user_id, chat_id=chat_id)
        role = (self.policy.get_role(member.role)
                if member is not None else None)
        return not (role is not None
                    and getattr(role, "bypass_role_denies", False) is True)

    def _turn_sender_differs(self, sess, sender_id: int | None, *,
                             chat_id: int | None = None) -> bool:
        """True when a message from *sender_id* must wait for the turn
        running in *sess* to end, because that turn is someone else's
        (roadmap 8.77, D-H).

        Joined to the running turn, the message would run under that
        turn's rules merged with its own (the hook never lets it widen the
        turn), not as its own turn with its own rights. Held, it drains
        when the turn ends and runs as the sender.

        - Scope mode only, and only while a turn whose sender is known
          (``turn_sender_id``) runs: the session is BUSY, or a background
          agent of its job still runs after an interim Stop (the job is
          the turn: its agent and its continuation run under its rules).
        - A terminal turn is the operator's: only a sender whose role has
          ``bypass_safety`` (an owner) joins it, or the member of the
          session's own DM (a DM is one person's; an install migrated from
          personal mode made that person an ``admin``); anyone else waits.
        - Otherwise anyone but the turn's own sender waits, so a turn
          several people started (TURN_SENDER_MIXED) holds everyone.
        - An unknown sender (``None``) is not held: its note carries the
          floor's rules, which can only narrow the turn it joins.

        A one-person install never holds: every turn is that person's or
        the terminal's, and that person joins a terminal turn in their DM.

        Whether the sender is an owner is read in *chat_id*, the chat the
        message came from, else the session's own chat (roadmap 8.73).

        Set only once the hook reports the turn (``hook_receiver``). Until
        then the turn's note is still outstanding, and the mixed-sender
        hold (``transport.mixed_sender_note_outstanding``) holds a
        different sender: the hook deletes the note and reports the turn in
        the same few milliseconds.
        """
        if sess.status is not Status.BUSY and not sess.job_background_open():
            return False
        running = sess.turn_sender_id
        held = self._sender_differs_from(sess, running, sender_id,
                                         chat_id=chat_id)
        if held:
            log.info("[%s] Turn-sender hold: turn=%s sender=%s",
                     sess.name, running, sender_id)
        return held

    def _sender_differs_from(self, sess, running, sender_id, *,
                             chat_id: int | None = None) -> bool:
        """:meth:`_turn_sender_differs`'s rule without the "a turn is
        running" gate: whether *sender_id*'s message (from *chat_id*, else
        the session's own chat) may not join a turn whose sender is
        *running*."""
        if self.scopes is None or sender_id is None or running is None:
            return False
        if running == TURN_SENDER_TERMINAL:
            return not (self._sender_is_owner(
                            sender_id, chat_id=self._attribution_chat(sess, chat_id))
                        or self._sender_owns_dm(sess, sender_id))
        return running != sender_id

    def _sender_is_owner(self, sender_id: int, *,
                         chat_id: int | None) -> bool:
        """True when *sender_id*'s role in *chat_id* has ``bypass_safety``
        (roadmap 8.73: an owner of another chat is not one here)."""
        member = self._driver_user_by_id(sender_id, chat_id=chat_id)
        role = (self.policy.get_role(member.role)
                if member is not None else None)
        return role is not None and getattr(role, "bypass_safety", False) is True

    def _sender_owns_dm(self, sess, sender_id: int) -> bool:
        """True when *sess* belongs to a DM scope *sender_id* is a member
        of: that person's own DM. A session not stamped with its chat yet
        belongs to the home chat, as for its messages
        (``transport.resolve_chat_id_int``)."""
        from aipager.bot.transport import resolve_chat_id_int
        scope = self._scope_for(resolve_chat_id_int(sess))
        return (scope is not None and scope.kind == "dm"
                and self._member_in_scope(scope, sender_id) is not None)

    async def _refuse_admin_command(self, update, text: str) -> bool:
        """Reply ``That command needs an admin.`` with a 🤷 and return True
        when the sender of ``update`` may not send ``text`` (see
        :meth:`_command_needs_admin`). Callers run it before anything
        moves (trigger, hold, tracking), so a refused command leaves no
        trace; :meth:`_inject_prompt` refuses it again regardless. The
        sender's role is the one in the chat the message came from
        (roadmap 8.73)."""
        if not self._command_needs_admin(
                text, driver_id_from_update(update),
                chat_id=calling_chat_id(update)):
            return False
        await self._reply_needs_admin(update)
        return True

    async def _reply_needs_admin(self, update) -> None:
        """The one answer to a refused slash command: a reply and a 🤷."""
        await reply_text(update.message, shown(i18n.for_update(update), NEEDS_ADMIN_REPLY))
        await self._react(update, reactions.NOT_DELIVERED)

    @staticmethod
    def _parked(sess) -> bool:
        """Whether *sess* is parked on a Claude Code background job: its
        own field, or the terminal guard's probe (dtach/inject.py) that
        every write asks too."""
        return bool(sess.parked_job) or inject.is_parked(sess.name)

    async def _inject_prompt(self, sess, text: str, reply_context: str = "",
                             *, msg_id: int | None = None,
                             chat_id: int | None = None,
                             driver_user_id: int | None = None) -> bool:
        """Send a Telegram-originated prompt into the session.

        Marks the session origin = "telegram" (the safety boundary keys
        off this) and prepends the identity marker for free-text prompts.
        Slash commands are sent raw (a marker line would break them); a
        slash command the sender may not send is refused before anything
        is written or typed and returns :data:`PROMPT_REFUSED` (falsy;
        roadmap 8.74, see :meth:`_command_needs_admin`). The explicit
        sender becomes the session's driver.

        ``reply_context`` defaults to ``""`` — deliberately, so that any
        caller (existing or future) that doesn't pass it explicitly
        clears a prior turn's reply pointer rather than leaking it into
        this one (design.md's staleness guard). Only the three
        immediate-inject reply handlers and the queue-drain site pass a
        real value.

        ``msg_id``/``chat_id`` identify the originating Telegram message
        (design.md "queue handoff") — carried on the per-message note so
        the ``UserPromptSubmit`` hook's pick-up matcher can report which
        specific message a turn consumed (``track_message`` + a 👍
        reaction), and so ``discard_queued_input`` has something to have
        cleared on Stop. ``None`` for callers with no single originating
        message (e.g. a literal ``/compact`` triggered by a hook event
        rather than operator input).

        ``driver_user_id`` is the raw Telegram user id of whoever sent
        THIS message — the note's ``sender_key`` half that
        ``transport.mixed_sender_note_outstanding`` compares against.
        Deliberately NOT read from ``sess.last_driver_user_id`` here:
        that field is only set by ``AuthMixin._mark_driver`` in
        team/scope mode, stays ``None`` for the entire lifetime of a
        personal-mode session, and — even in team mode — may not yet
        reflect THIS message at every call site. Falling back to it
        would write every personal-mode note under the same ``0``
        sender_key while the hold-check (which always reads the live
        ``update.effective_user.id``) compares against the real id,
        turning one operator's own back-to-back messages into a
        "mixed sender" false positive. Callers with a live ``Update``
        pass ``update.effective_user.id``; callers with none of their
        own (Retry, a literal ``/compact``) fall back to
        ``sess.last_driver_user_id`` — the best available approximation
        when there is no live sender to read. The queue-drain site
        (``notify.py``) is NOT in that group since review-2#rev-iter2-001:
        ``TrackedSession.queue_prompt`` now captures the sender's id at
        hold/queue time (from the same ``driver_id_from_update(update)``
        every immediate-inject caller already uses) and carries it on the
        queue entry, so the drain passes that captured id here — a real
        identity, not a fallback — whenever one was captured. Only an
        entry with no captured id (a pre-upgrade 4-tuple, or a call site
        with no live ``Update`` when it queued) drains with
        ``driver_user_id=None``, which still falls back to
        ``sess.last_driver_user_id`` for ``sender_key`` bookkeeping below.

        That fallback is only good enough for bookkeeping (grouping
        "same sender" runs for the mixed-sender hold), never for
        *permission attribution*. ``sess.last_driver_user_id`` is
        mutable and can move on to a different person between a message
        failing and a later Retry, or between a message being queued and
        the daemon draining it — review rev-iter1-001/002/rev-iter2-001.
        Role/member resolution below therefore reads only the id the
        CALLER explicitly supplied (before the fallback assigned below),
        never the fallback value: a caller with no live identity for this
        specific message (Retry, a literal ``/compact``, or a
        drained message with no captured sender) gets a note resolved
        with ``member=None``/``role=None`` — floor permissions,
        ``bypass_safety=False`` — rather than silently inheriting
        whoever ``sess.last_driver_user_id`` happens to name right now.
        Fail closed: an under-attributed note can only make a future
        merged turn *more* restrictive (design.md's merge invariant),
        never less, so this trades away Retry's permission elevation in
        the common single-user case rather than risk lending an
        unrelated sender's ``bypass_safety`` to it.

        A session parked on a Claude Code background job takes nothing:
        False, before any note is written (text typed there can start a
        stray background job).

        The text is first cleaned of the invisible characters Claude Code
        removes on Enter (an Enter that removed one sends nothing; roadmap
        8.124, :mod:`aipager.invisible`), so the note's body is what Claude
        Code receives. When a context-dependent one is still there, Enter
        is pressed once more if the message is not picked up
        (:meth:`_enter_again_if_held_back`; never for a slash command, whose
        note a local command never consumes). A message that is nothing
        but such characters is not sent (False).

        Never typed into a dialog (roadmap 8.143): when one is open, or
        opens while the message is typed, nothing reaches Claude, the note
        is removed and :data:`PROMPT_DIALOG` (falsy) comes back for the
        caller to hold the message (:func:`aipager.dtach.inject.send_text_and_enter`).
        """
        if self._parked(sess):
            log.info("[%s] Not sending a prompt: parked on a Claude Code "
                     "background job", sess.label)
            return False
        text = invisible.clean(text)
        if not text.strip():
            log.info("[%s] Not sending a message that is blank once its invisible "
                     "characters are removed", sess.label)
            return False
        explicit_driver_user_id = driver_user_id
        # Roadmap 8.73 (D-A): the sender's role is the one in the chat
        # this message came from, else the session's own chat; with
        # neither, the sender resolves to nobody (the floor).
        role_chat_id = self._attribution_chat(sess, chat_id)
        # Roadmap 8.74 (D-G): refused before anything is written or typed,
        # whichever path brought the text here.
        if self._command_needs_admin(text, explicit_driver_user_id,
                                     chat_id=role_chat_id):
            log.info("[%s] Refused a slash command from %s: %s",
                     sess.label, explicit_driver_user_id, text[:80])
            return PROMPT_REFUSED
        if driver_user_id is None:
            driver_user_id = sess.last_driver_user_id
        sess.last_prompt_origin = "telegram"
        # The sender of THIS message, never the session's last driver
        # (roadmap 8.74): it labels the marker, and it becomes the
        # session's driver on every path that reaches here.
        sender = self._driver_user_by_id(explicit_driver_user_id,
                                         chat_id=role_chat_id)
        if sender is not None:
            sess.last_driver_user_id = sender.id
            if sess.created_by_user_id is None:
                sess.created_by_user_id = sender.id
        if text.lstrip().startswith("/"):
            # Typed as checked: a slash command with leading whitespace
            # could reach Claude Code as a plain, markerless prompt.
            body = text.lstrip()
        else:
            body = text
            marker = self._prompt_marker(sess, sender)
            if marker:
                # A member's label comes from their Telegram name, which can
                # hold invisible characters too.
                body = invisible.clean(f"{marker}\n{text}")
        # Write a per-message policy note for this turn (design.md "queue
        # handoff") — replaces the old one-slot write_snapshot() call at
        # send time. Every message gets its own note now; the
        # UserPromptSubmit hook merges whichever ones a turn actually
        # picks up into the canonical snapshot at pick-up, not here. The
        # safety-relevant fields (role/scope/member, Phase E) only matter
        # in v2 scope mode — the marker line that flips a hook's origin
        # check to "telegram" is only ever prepended there (see
        # _prompt_marker above), so a note in legacy/personal mode cannot
        # change PreToolUse enforcement. style_text is different: it's
        # keyed by the scope's real Telegram chat id, not by aipager.yaml
        # scope config, so it's meaningful in every mode. Goes through
        # resolve_preferences (never get_preferences directly): this is
        # THE critical path per design.md — a caller here that bypassed
        # it would make the per-session settings feature do nothing.
        # Best-effort.
        note_path = None
        try:
            from aipager import member_root
            from aipager import preferences as prefs_mod
            from aipager.policy_snapshot import write_note
            member = role = scope = None
            root_bound = None
            if self.scopes is not None:
                # See the docstring above: permission attribution uses
                # ONLY the explicitly-passed id, never the fallback used
                # for sender_key below. scope is unaffected — it can
                # only add restrictions, never grant them, so it is safe
                # to resolve regardless of whether the sender is known.
                member = sender
                role = (self.policy.get_role(member.role)
                        if member is not None else None)
                # The session's own chat, else (scope mode) the chat it
                # would be stamped with, as for its messages (roadmap
                # 8.72): an unstamped session's note carries that chat's
                # deny_tools too.
                scope = self._scope_for(resolve_chat_id_int(sess))
                if member is not None:
                    # The members' root folder of the chat the role came
                    # from (role_chat_id), never the session's chat.
                    root_bound = member_root.snapshot_bound(
                        role, self._scope_for(role_chat_id), self.scopes)
            style = prefs_mod.style_text(
                prefs_mod.resolve_preferences(
                    sess.scope_chat_id or 0, sess.preference_overrides(),
                )
            )
            sender_key = (sess.scope_chat_id or 0, driver_user_id or 0)
            note_path = write_note(
                sess.name, role, scope, member,
                msg_id=msg_id, chat_id=chat_id, sender_key=sender_key,
                # Only the explicitly passed sender is the author: the
                # sender_key fallback must never be recorded as one.
                author_user_id=explicit_driver_user_id,
                body=body, raw_text=text,
                scope_mode=self.scopes is not None,
                style_text=style, reply_context=reply_context,
                # The safety floor's policy.yaml additions (roadmap
                # 8.96), from the same policy the role came from.
                policy=getattr(self, "policy", None),
                root_bound=root_bound,
            )
        except Exception:
            log.debug("policy note write failed", exc_info=True)
        # An earlier message may still sit held back in Claude Code's box:
        # it goes first, or this one would be typed onto it.
        await self._enter_held_back_now(sess)
        # Read BEFORE the send: every caller flips the session to BUSY
        # right after this returns, and the "prompt not taken" watchdog
        # (session_monitor.prompt_not_taken) only judges a send that
        # STARTED a turn. A send made while BUSY stamps nothing — it is
        # not judged, and it must not overwrite the deadline of an
        # earlier from-idle send Claude Code may still be ignoring
        # (review rev-iter1-001). A failed send stamps nothing either:
        # there is no turn to watch for.
        was_idle = sess.status != Status.BUSY  # read again before each try below
        # Typed but not yet submitted, the text would be submitted by a
        # send-now chord: /now and the "⚡ Send now" button refuse while
        # this is above zero (bot/send_now.py). Raised with no await
        # before the injection starts.
        for _try in range(MET_DIALOG_TRIES):
            was_idle = sess.status != Status.BUSY
            sess.prompt_injecting += 1
            typed_wall = time.time()
            try:
                ok = await inject.send_text_and_enter(sess.name, body)
            finally:
                sess.prompt_injecting -= 1
            if ok is not inject.DIALOG_BEFORE_TYPING and ok is not inject.DIALOG_WHILE_TYPING:
                break
            if await self._dialog_on_record(sess):
                break
            # No dialog any more and none on record: typed again.
        if ok is inject.DIALOG_BEFORE_TYPING or ok is inject.DIALOG_WHILE_TYPING:
            # A dialog opened (roadmap 8.143): nothing went to Claude and no
            # key answered it. Its note goes (the message is typed again,
            # with a new one, once the dialog is answered); the caller
            # holds the message.
            if note_path is not None:
                try:
                    Path(note_path).unlink(missing_ok=True)
                except OSError:
                    log.debug("[%s] could not remove the held message's note",
                              sess.label, exc_info=True)
            log.info("[%s] A prompt opened while a message was typed: held for it",
                     sess.label)
            return PROMPT_DIALOG
        if ok and not reactions.is_slash_command(text):
            # Prompt evidence: the turn files Claude sends may come from
            # (state.py files_turn_opened; kept when one already runs). A
            # slash command opens nothing: most run no turn (/cost,
            # /model, /clear), and one that does fires its own
            # UserPromptSubmit, which opens it.
            sess.files_turn_opened(typed_wall)
        if ok and was_idle:
            sess.prompt_sent_at = time.monotonic()
            sess.prompt_sent_msg = (
                (msg_id, chat_id)
                if msg_id is not None and chat_id is not None else None
            )
        if (ok and note_path is not None and not reactions.is_slash_command(text)
                and invisible.may_hold_back(body)):
            _HELD_BACK[sess.name] = note_path
            _spawn(self._enter_again_if_held_back(sess, note_path))
        return ok

    async def _dialog_on_record(self, sess: TrackedSession) -> bool:
        """After a message met a dialog (roadmap 8.143): True once the dialog
        is on record (``dialog_is_open``: the message is then held for it
        and its answer releases it), False when the typing gate no longer
        sees one (nothing to wait for: the message is typed again). The
        dialog's own event can wait behind an older turn's finish: given
        :data:`MET_DIALOG_WAIT_SECONDS`, then True (held, and out at the
        turn's end at the latest)."""
        deadline = time.monotonic() + MET_DIALOG_WAIT_SECONDS
        while True:
            if sess.dialog_is_open():
                return True
            if not inject.dialog_open(sess.name):
                return False
            if time.monotonic() >= deadline:
                log.info("[%s] a dialog is not on record %.0fs after a message met "
                         "it: held for the turn's end", sess.label, MET_DIALOG_WAIT_SECONDS)
                return True
            await _sleep(MET_DIALOG_POLL_SECONDS)

    async def _enter_held_back_now(self, sess: TrackedSession) -> None:
        """Before another message is typed: the second Enter of an earlier
        one that may still sit held back (:meth:`_held_back_verdict`),
        now rather than after its wait, so the two are not glued into one
        prompt. The messages a dialog held may be what is being typed (its
        release): that alone does not stop it."""
        note_path = _HELD_BACK.get(sess.name)
        if note_path is None:
            return
        verdict = self._held_back_verdict(sess, note_path, releasing=True)
        if verdict == "wait":
            return                      # its own watch tries again
        del _HELD_BACK[sess.name]
        if verdict == "press":
            pressed = await self._press_held_back_enter(sess)
            if pressed is inject.DIALOG_BEFORE_TYPING:
                # A dialog came first (roadmap 8.143): its own watch tries again.
                _HELD_BACK.setdefault(sess.name, note_path)
            elif pressed:
                await _sleep(HELD_BACK_SETTLE_SECONDS)

    async def _enter_again_if_held_back(self, sess: TrackedSession, note_path) -> None:
        """Press Enter once more when Claude Code held the message back.

        Its cleaner removes some invisible characters only in some contexts
        (:func:`aipager.invisible.may_hold_back`); when it removed one, the
        Enter sent nothing and the text waits in its input box ("review and
        press Enter to send"), cleaned. A second Enter sends it, once; its
        note still matches what Claude Code sent (the matching leaves those
        characters out: ``policy_snapshot.match_notes_prefix_run``). Checked
        every :data:`HELD_BACK_ENTER_AFTER_SECONDS`, at most
        :data:`HELD_BACK_TRIES` times, while a dialog may be on top (after
        that the next message's flush presses it); given up when a later
        message already pressed it, or Stop, /clearqueue, a typed refusal
        or a kill dropped it."""
        for _ in range(HELD_BACK_TRIES):
            await _sleep(HELD_BACK_ENTER_AFTER_SECONDS)
            if _HELD_BACK.get(sess.name) is not note_path:
                return
            verdict = self._held_back_verdict(sess, note_path)
            if verdict == "wait":
                continue
            del _HELD_BACK[sess.name]
            if verdict == "press":
                pressed = await self._press_held_back_enter(sess)
                if pressed is inject.DIALOG_BEFORE_TYPING and sess.name not in _HELD_BACK:
                    # A dialog came first (roadmap 8.143): try again later.
                    _HELD_BACK[sess.name] = note_path
                    continue
            return
        if _HELD_BACK.get(sess.name) is note_path:
            # Kept: the next message's flush presses it before typing.
            log.info("[%s] a held-back message waits for the next message: a dialog "
                     "stayed open", sess.label)

    def _held_back_verdict(self, sess: TrackedSession, note_path, *,
                           releasing: bool = False) -> str:
        """``"press"``, ``"wait"`` (a dialog may be on top: the Enter would
        answer it, row 1, with nobody's consent) or ``"drop"`` (picked up:
        its UserPromptSubmit consumed the note, also for a message queued
        behind a running turn; or the session ended, was replaced or is
        parked on a Claude Code background job). A dialog may be on top
        while the session is INTERACTIVE, while a prompt is open or carried
        (``dialog_is_open``, ``pending_permission``), while the new turn's
        events are held behind the old turn's card (``turn_state_held``,
        as the send-now chord checks), and while the messages a dialog held
        go out (unless that is what is being typed: *releasing*)."""
        try:
            if not Path(note_path).exists():
                return "drop"
        except OSError:
            return "drop"
        if (self.registry.get(sess.name) is not sess or sess.status == Status.GONE
                or self._parked(sess)):
            return "drop"
        if (sess.status == Status.INTERACTIVE or sess.dialog_is_open()
                or sess.pending_permission is not None or sess.turn_state_held()
                or (sess.dialog_release_pending() and not releasing)):
            return "wait"
        return "press"

    async def _press_held_back_enter(self, sess: TrackedSession):
        """The second Enter, through the typing gate (roadmap 8.143): True,
        False, or ``inject.DIALOG_BEFORE_TYPING`` when a dialog came first
        (nothing pressed)."""
        log.info("[%s] the message was held back by Claude Code's invisible-character "
                 "check: pressing Enter once more", sess.label)
        return await inject.press_enter_gated(sess.name)

    def _build_reply_context(
        self, msg, sess, *, bot_id: int, allow_file: bool,
    ) -> str:
        """The single place reply-pointer wording is built (design.md
        Part 2). Called from all three reply-bearing handlers
        (``_handle_message``, ``_dispatch_voice_transcript``,
        ``_handle_file``) — never from the Mini App or slash-command
        paths, which aren't replies.

        Returns ``""`` when the prompt isn't a reply worth flagging: no
        ``reply_to_message`` and no ``quote`` at all, or a reply to the
        session's own current latest message with no highlight attached
        (routing already lands on the right session in that case, so a
        pointer would be redundant). A highlighted fragment always
        produces context, even when the replied-to message is the
        latest — highlighting is itself the signal.

        ``allow_file`` gates writing the full-text `/tmp` fallback file:
        the caller passes ``False`` whenever the session is BUSY, so a
        queued reply's file write can never race a still-pending
        drain's file write for the same session (design.md Part 4).
        """
        reply_to = getattr(msg, "reply_to_message", None)
        quote = getattr(msg, "quote", None)
        quote_text = getattr(quote, "text", None) if quote else None

        if quote and quote_text:
            fragment = quote_text[:1000]
            if len(quote_text) > 1000:
                fragment += "…(truncated)"
            if reply_to is not None:
                author = _reply_author(reply_to, bot_id)
                file_path = None
                if allow_file and len(quote_text) > 1000:
                    from aipager.policy_snapshot import (
                        reply_context_path,
                        write_reply_context_file,
                    )
                    write_reply_context_file(
                        sess.name,
                        header=("Full text of the highlighted fragment "
                               "(truncated above to fit the prompt "
                               "context):"),
                        full_text=quote_text,
                    )
                    file_path = reply_context_path(sess.name)
                return _highlight_context(
                    fragment, getattr(reply_to, "date", None), author,
                    manual=bool(getattr(quote, "is_manual", False)),
                    file_path=file_path,
                )
            return _external_quote_context(fragment)

        if reply_to is None:
            return ""

        is_latest = reply_to.message_id in {
            v for v in (sess.last_msg_id, sess.busy_msg_id, sess.trigger_msg_id)
            if v and v > 0
        }
        if is_latest:
            return ""

        text = reply_to.text or reply_to.caption
        if not text:
            return _nontext_context(
                getattr(reply_to, "date", None), _reply_author(reply_to, bot_id),
            )

        excerpt = text[:80]
        if len(text) > 80:
            excerpt += "…"
        file_path = None
        if allow_file:
            from aipager.policy_snapshot import (
                reply_context_path,
                write_reply_context_file,
            )
            write_reply_context_file(
                sess.name,
                header="Full text of the message you're replying to:",
                full_text=text,
            )
            file_path = reply_context_path(sess.name)
        return _whole_message_context(
            excerpt, getattr(reply_to, "date", None),
            _reply_author(reply_to, bot_id),
            with_file=allow_file, file_path=file_path,
        )

    async def create_session(
        self, label: str, *, scope_chat_id, mode: str,
        cwd: str | None = None, driver_user_id: int | None = None,
        model: str | None = None, reuse_name: str | None = None,
    ) -> tuple[str, str]:
        """Launch a session and register it. Returns ``(session_name, "")``
        or ``("", error)``.

        ``mode`` (required): ``"ask"``, ``"auto"`` or ``"full"``; the
        caller has already applied the admin rule for Full access.

        ``reuse_name``: start the process under this exact internal name
        (Replace). A renamed session, or one named before chats had their
        own names, has a name its label and chat no longer produce.

        The single seam both entry points go through: chat's ``/new`` and
        the Mini App's create route. Everything that makes a launched
        process *a tracked aipager session* — the disambiguated name, the
        registry entry, the scope stamping, the GONE/UNKNOWN recovery,
        the bot-name and command refresh — lives here once, so the two
        surfaces cannot drift into registering sessions differently.

        Deliberately NOT included: the caller's own authorization, the
        name-conflict prompt, and the reply it sends. Those are
        surface-specific (chat asks Resume/Replace/Cancel with buttons;
        the Mini App answers inline before the request is even sent), and
        folding them in here would force one surface to fake the other's
        interaction model.
        """
        from aipager.scope import disambiguated_name
        from aipager.state import Status

        # A person whose turns are confined to the session's folder never
        # gets ``/``, the home folder or a folder above it as that folder
        # (roadmap 8.79), and a role without can_manage starts only inside
        # its members' root folder, whichever surface asked. The hook
        # would still hold their turns; this says why up front instead.
        from aipager import member_root
        refusal = member_root.launch_refusal(
            self, driver_user_id, scope_chat_id, cwd)
        if refusal:
            return "", refusal

        if reuse_name:
            scope_kind = ("group" if scope_chat_id is not None and scope_chat_id < 0
                          else "dm")
            session_name = reuse_name
        elif scope_chat_id is not None:
            scope_kind = "group" if scope_chat_id < 0 else "dm"
            session_name = disambiguated_name(label, scope_chat_id, scope_kind)
        else:
            scope_kind = "dm"
            session_name = f"claude-{label}"
        short_name = session_name.removeprefix("claude-")

        sys_extra = self._session_system_prompt(scope_chat_id, label)
        # A new process has no turn running (roadmap 8.77): a turn-open
        # file left by an earlier one under this name must not make its
        # first prompt join that turn's rules.
        _clear_turn_open(session_name)
        ok, err = await inject.launch_session(
            short_name, mode=mode, cwd=cwd or None,
            system_prompt_extra=sys_extra, model=model or None,
        )
        if not ok:
            return "", err

        sess = self.registry.get_or_create(session_name)
        # /new reusing a finished session's name reuses its entry: drop any
        # queued target the old process left behind (review rev-iter2-001).
        self._discard_queued_targets(sess)
        sess.label = label
        sess.perm_mode = mode
        sess.reset_mode_tracking()
        sess.mode_launch_unchecked = True
        sess.launch_model = model or ""
        if cwd:
            # The hook receiver stamps cwd on the first statusLine event,
            # but the operator picked this one explicitly — record it now
            # so the session page and the directory picker show the truth
            # before the first hook arrives.
            sess.cwd = cwd
        if scope_chat_id is not None:
            sess.scope_chat_id = scope_chat_id
            sess.scope_kind = scope_kind
        if sess.status in (Status.GONE, Status.UNKNOWN):
            self.registry.transition(session_name, Status.IDLE)
        if driver_user_id is not None:
            sess.created_by_user_id = sess.created_by_user_id or driver_user_id
            sess.last_driver_user_id = driver_user_id
        # The person who started it now talks to it; in a group that is
        # their own target, not the rest of the chat's (roadmap 8.90).
        self.registry.set_target(session_name, scope_chat_id, driver_user_id)
        self.registry.mark_dirty()
        _spawn(self._maybe_update_bot_name(session_name))
        _spawn(self._update_bot_commands())
        return session_name, ""

    def _session_system_prompt(self, scope_chat_id, label: str) -> str | None:
        """Write the session folder + SESSION.md and return its body for
        ``--append-system-prompt``. None for legacy/grandfathered sessions
        (no scope) or on any failure (best-effort)."""
        if not scope_chat_id or self.scopes is None:
            return None
        scope = self._scope_for(scope_chat_id)
        if scope is None:
            return None
        try:
            from aipager.session_store import write_session_files
            return write_session_files(scope, self.policy, label)
        except Exception:
            log.debug("SESSION.md generation failed", exc_info=True)
            return None

    # ── A turn ended by an answer typed into the dialog (roadmap 8.99) ──

    async def _claude_queue_before_typed_refusal(self, sess: TrackedSession) -> list[dict]:
        """What Claude Code holds in its own queue, read BEFORE a refusal
        is typed into its dialog, as `_stop_session_core` reads it before
        its Escapes: the typed refusal (Escape) ends the turn, which
        pulls the queue into the input box and writes `popAll`, after
        which the transcript no longer shows what was held. Best effort:
        ``[]`` on any failure."""
        from aipager.policy_snapshot import list_outstanding_notes
        try:
            await self._settle_queued_targets(sess, reanchor=False)
            outstanding = list_outstanding_notes(sess.name)
            held = held_by_claude(sess, outstanding + self._queued_candidates(sess))
            # A message still held back in the input box (roadmap 8.124)
            # goes with the turn, as on /stop: wiped, never sent after.
            return held + [n for n in _drop_held_back(sess, outstanding) if n not in held]
        except Exception:
            log.debug("[%s] reading Claude's queue before a typed refusal failed",
                      sess.label, exc_info=True)
            return []

    def _watch_keystroke_answer(self, sess: TrackedSession, *, refusal: bool,
                                by: str = "", answered_at: float,
                                held: list[dict] | None = None) -> None:
        """Start watching a turn whose permission prompt was answered by
        typing into Claude Code's dialog (the hook was no longer waiting).

        Pinned Claude Code behaviour (roadmap 8.99, 8.126): the Deny
        fallback types one Escape (``callbacks._type_refusal``), which on
        a tool prompt does what the "No, and tell Claude what to do
        differently (esc)" row does: Claude Code writes
        ``[Request interrupted by user for tool use]`` and waits for input.
        What the live 2.1.291 run (2026-10-07) showed: no PostToolUse and
        no Stop after the typed Deny, and the session kept BUSY ("tool
        still in flight") until the 900 s cap. On the auto-mode
        outside-reads question the Escape takes "No, and ask again next
        time": the read fails and the turn goes on (2.1.310), which the
        watch below leaves to the turn's own hooks.

        *refusal*: the typed answer was that refusal (a tool's Deny;
        never a question's, whose rows are its options). Any other typed
        answer is watched too, but only the transcript's interrupt marker
        may end its turn (a wrong guess about the row, or an Escape in the
        terminal). *answered_at*: ``time.time()`` taken before the keys
        were sent. *held*: what Claude's own queue held before the keys
        (:meth:`_claude_queue_before_typed_refusal`)."""
        old = _KEYSTROKE_WATCHES.pop(sess.name, None)
        if old is not None and not old.done():
            try:
                old.cancel()
            except RuntimeError:
                pass  # its loop is gone (never in the daemon's one loop)
        task = _spawn(self._keystroke_answer_watch(
            sess, refusal=refusal, by=by, answered_at=answered_at,
            held=list(held or ())))
        _KEYSTROKE_WATCHES[sess.name] = task

        def _forget(t, name=sess.name):
            if _KEYSTROKE_WATCHES.get(name) is t:
                del _KEYSTROKE_WATCHES[name]
        task.add_done_callback(_forget)

    async def _keystroke_answer_watch(self, sess: TrackedSession, *,
                                      refusal: bool, by: str,
                                      answered_at: float,
                                      held: list[dict] | None = None) -> None:
        """The watch :meth:`_watch_keystroke_answer` starts.

        Ends the turn as interrupted (:meth:`_end_turn_ended_at_dialog`)
        once the transcript's newest entry is Claude's interrupt marker
        written since the answer. For a refusal it also ends it once the
        bounded grace passes with NOTHING written since the answer and
        nothing else showing Claude carried on, unless Claude's own queue
        held messages (it may run one as the next turn): then only the
        marker ends it. Stands down the moment anything shows the turn
        going on: the session leaving BUSY (a Stop, a new prompt), a new
        turn, the parent's tool-in-flight stamp changing (a new PreToolUse
        restamps it, the tool's own PostToolUse clears it), or, at the
        grace, any other entry written since the answer (the rejected
        tool's result with no marker after it, a new prompt)."""
        from aipager.transcript import turn_tail_since
        turn = sess.turn_seq
        tool_stamp = sess.pending_tool_started_at
        deadline = time.monotonic() + KEYSTROKE_ANSWER_WATCH_SECONDS
        held = list(held or ())

        def carried_on() -> bool:
            return (self.registry.get(sess.name) is not sess
                    or sess.status != Status.BUSY
                    or sess.turn_seq != turn
                    or sess.pending_tool_started_at != tool_stamp)

        confirmed = False
        while True:
            await _sleep(KEYSTROKE_ANSWER_POLL_SECONDS)
            if carried_on():
                return
            tail = turn_tail_since(sess.transcript_path, answered_at)
            if tail == "marker":
                confirmed = True
                break
            if time.monotonic() >= deadline:
                if not refusal or held or tail is not None:
                    log.info("[%s] typed answer: the turn did not end at the "
                             "dialog (%s) - left to its hooks", sess.label,
                             "not a refusal" if not refusal
                             else "Claude had queued messages" if held
                             else "the transcript moved on")
                    return
                log.warning("[%s] typed refusal: no interrupt marker within "
                            "%.0fs and nothing since - ending the turn",
                            sess.label, KEYSTROKE_ANSWER_WATCH_SECONDS)
                break
        await self._end_turn_ended_at_dialog(
            sess, refused=refusal, by=by, confirmed=confirmed,
            carried_on=carried_on, held=held)

    async def _end_turn_ended_at_dialog(self, sess: TrackedSession, *,
                                        refused: bool, by: str,
                                        confirmed: bool,
                                        carried_on=None,
                                        held: list[dict] | None = None,
                                        card_text: str | None = None) -> bool:
        """End the running turn the way /stop does, minus the Escapes: the
        answer typed into the dialog already interrupted it. Settles the
        card ("Denied"), clears the tool-in-flight state and the hook's
        turn-open mark, and moves the session to IDLE. What Claude's queue
        held (*held*, read before the keys) was pulled into its input box
        by the interrupt, as by /stop's Escape: wiped there and marked not
        delivered, as /stop does. Then the messages aipager held while the
        prompt was open go out, as at any turn end.

        A background job still open keeps its own endgame: only the
        refused tool's in-flight stamp is cleared (it will never run).

        *carried_on*: re-checked after the card lock's await; when it
        reports Claude carried on, nothing is changed. *card_text*: the
        card's final text when not the typed answer's ("Denied in the
        terminal", roadmap 8.113). Returns True when the turn was ended
        here."""
        held = list(held or ())
        async with sess.animate_lock:
            if carried_on is not None and carried_on():
                return False
            if sess.job_background_open():
                sess.pending_tool_started_at = None
                sess.parent_tool_started_at = None
                log.info("[%s] turn ended at the dialog with a background "
                         "job open: tool in flight cleared, the job ends it",
                         sess.label)
                return False
            self._cancel_lazy_card(sess)
            sess.close_turn()  # no card for this turn from here (8.57)
            # The state flips here, synchronously, before any other await.
            sess.pending_tool_started_at = None
            sess.parent_tool_started_at = None
            # Every open prompt goes with the turn (roadmap 8.120), and
            # their buttons type nothing.
            sess.close_all_prompts()
            sess.pending_permission = None
            # No prompt on the card is open any more: a stale answer
            # button on it types nothing (8.102).
            sess.mark_prompt_closed(sess.busy_msg_id)
            _clear_turn_open(sess.name)
            sess.turn_sender_id = None
            if confirmed:
                # A Stop hook landing after this is Claude finalising the
                # interrupted turn, not an answer still owed (the
                # already-IDLE delivery guard). Not set on the grace
                # path: there, a late Stop's answer must still go out.
                sess.user_stopped_at = time.monotonic()
            self.registry.transition(sess.name, Status.IDLE)
        log.info("[%s] turn ended at the permission dialog (%s, %s) - IDLE",
                 sess.label, "refused" if refused else "answered",
                 "transcript marker" if confirmed else "grace")
        self._stop_animation(sess)
        await self._settle_card_text(
            sess, card_text or _refused_card_text(sess.label, by, refused=refused,
                                                  tr=i18n.for_sess(sess)))
        sess.busy_msg_id = None
        sess.trigger_msg_id = None
        sess.busy_card_trigger = None
        if held:
            # Only ever with the marker seen (the grace stands down when
            # Claude's queue held anything).
            try:
                await inject.discard_queued_input(sess.name,
                                                  queued_chars(sess, held))
                await self._mark_not_delivered(sess, held)
            except Exception:
                log.warning("[%s] wiping Claude's queue after the dialog's "
                            "interrupt failed", sess.label, exc_info=True)
            from aipager.policy_snapshot import clear_notes_dir
            clear_notes_dir(sess.name)
            self._discard_queued_targets(sess)
        self.registry.mark_dirty()
        try:
            await self._drain_next_queued(sess)
        except Exception:
            log.warning("[%s] draining held messages after the dialog's "
                        "interrupt failed", sess.label, exc_info=True)
        return True

    # ── Telegram handlers ──

    async def _stop_session_core(self, sess: TrackedSession, *,
                                 by: str = "") -> StopOutcome:
        """Interrupt a busy session: send Escape, clean up state.

        The shared seam both chat and the Mini App call — edit behaviour
        here, not in the wrapper. Refuses immediately, with no awaits and
        no side effects, when the session is not actually busy — this
        makes the refusal pytest-testable with zero dtach mocking.

        ``by``: who stopped it (``_actor_label``, plain text; ``""`` in a
        private chat), shown on the card in a group (roadmap 8.91c).
        """
        if not sess.can_be_stopped():
            # A session waiting on background work (design.md "model
            # Claude Code background-agent jobs") reads as IDLE, not BUSY
            # — the shared gate above widens to still recognise it as
            # stoppable. "Stop still tappable" (requirement 1) would be
            # meaningless if the button worked right up until the
            # foreground turn's own Stop hook fired and then refused
            # "is not busy" for however long the background agent kept
            # running afterward.
            return StopOutcome(ok=False, label=sess.label)
        if sess.parked_job:
            # The work runs in a Claude Code background job, and nothing
            # may be typed into the parked client: Stop means "end this
            # work", so the job is stopped and the session brought back.
            return await self._stop_parked(sess, by=by)

        # 0. Read Claude's queue BEFORE the Escapes below: the first one
        # makes Claude Code pull it into the input box and write
        # `popAll`, and a later read would see that line, forget the
        # message and skip wiping the input box (see 1b).
        await self._settle_queued_targets(sess, reanchor=False)
        from aipager.policy_snapshot import clear_notes_dir, list_outstanding_notes
        # Only what the transcript shows Claude holding counts (roadmap
        # 8.37): a note left by a command that already ran is not queued,
        # and the queue wipe below must not be typed for it.
        outstanding = list_outstanding_notes(sess.name)
        held = held_by_claude(sess, outstanding + self._queued_candidates(sess))
        # A message still held back in the input box (its second Enter not
        # pressed, roadmap 8.124) is wiped with the queue, never sent.
        held += [n for n in _drop_held_back(sess, outstanding) if n not in held]
        # Counted now too: the Escapes below make Claude Code write
        # `popAll`, and the transcript then no longer shows the text.
        held_chars = queued_chars(sess, held) if held else 0

        # 1. Send Escape twice to Claude Code — proven interrupt
        # behaviour this change does not touch.
        await inject.send_keys(sess.name, "Escape")
        await _sleep(0.15)
        await inject.send_keys(sess.name, "Escape")

        # 1b. Wipe whatever Claude's own not-yet-picked-up queue
        # surfaced into the input box (design.md "Stop and /clearqueue
        # on the same primitive") — the Escapes above can move it there
        # without cancelling the running task, and left alone it would
        # concatenate onto the next prompt. Read once, up front, so the
        # "was anything actually outstanding" check and the combined
        # drop count below agree on the same snapshot (`held`, step 0).
        if held:
            await inject.discard_queued_input(sess.name, held_chars)

        # 2. Cancel animation — and a self-woken turn's deferred card
        # (roadmap 8.32): this path ends the turn without the finish path,
        # which is where the deferral is otherwise stood down. Under the
        # card lock, so a late card whose send is in flight lands first
        # and is settled as "Stopped" below like any card.
        async with sess.animate_lock:
            self._cancel_lazy_card(sess)
            sess.close_turn()  # no card for this turn from here (8.57)
        self._stop_animation(sess)

        # 3. Edit busy message to show "Stopped" (no keyboard), and retire
        # it, under the card-edit lock: the Escapes above make Claude fire
        # tool hooks right now, and a card edit of theirs landing after
        # this line would put "Working" and a live Stop button back.
        await self._settle_card_text(sess, _stopped_card_text(sess.label, by,
                                                              i18n.for_sess(sess)))
        sess.busy_msg_id = None  # also a -1 claim, which the helper skips

        # 4. Transition to IDLE directly (skip notify — we handle UI here)
        dropped = len(sess.pending_queue) + len(held)
        # Every one of them is now never going to be taken (R3).
        await self._mark_not_delivered(
            sess, reactions.held_entries(sess.pending_queue) + held)
        sess.pending_queue.clear()
        clear_notes_dir(sess.name)
        # An interrupt may fire no Stop hook: the turn is over here
        # (roadmap 8.77), and nobody's message waits for it any more.
        _clear_turn_open(sess.name)
        sess.turn_sender_id = None
        # The job ends, background agents included: every open prompt
        # with it (roadmap 8.120), and their buttons type nothing.
        sess.close_all_prompts()
        sess.pending_permission = None
        sess.status = Status.IDLE
        # Genuinely end the job (design.md "model Claude Code
        # background-agent jobs") rather than leaving job_background_open()
        # true underneath the now-"Stopped" card — the operator asked for
        # this to be over, background agents included.
        # Nothing of the job is owed here: its interim answers went out
        # as they were written (roadmap 8.42).
        sess.active_subagents.clear()
        # Escape does not end a background shell: it stays counted as
        # running (pin, answer line), but no longer holds the job open.
        sess.bg_shells_leave_job()
        sess.job_interim_seen = False
        sess.job_continuation_active = False
        sess.job_grace_until = 0.0
        sess.trigger_msg_id = None
        sess.busy_card_trigger = None
        # Claude's own queue was discarded above — nothing left in it can
        # become the next turn ("anchor-on-transcript-consumption").
        self._discard_queued_targets(sess)
        sess.user_stopped_at = time.monotonic()
        sess.last_idle_at = time.monotonic()  # prevent debounce of next real IDLE
        self.registry.mark_dirty()

        log.info("[%s] Stopped by user (dropped %d queued)", sess.label, dropped)
        return StopOutcome(ok=True, label=sess.label, dropped=dropped, by=by)

    async def _stop_parked(self, sess: TrackedSession, *, by: str = "") -> StopOutcome:
        """Stop for a session parked on a Claude Code background job: a
        forced bring-back (``claude stop`` ends the job's turn), then the
        card settles as Stopped. Held messages go to the session that
        comes back. A failed bring-back changes nothing and says why."""
        outcome = await self._bring_back_core(sess, force=True)
        if not outcome.ok:
            return StopOutcome(ok=False, label=sess.label, reason=outcome.reason, by=by,
                               bg_err=outcome.bg_err)
        async with sess.animate_lock:
            self._cancel_lazy_card(sess)
            sess.close_turn()
        self._stop_animation(sess)
        await self._settle_card_text(sess, _stopped_card_text(sess.label, by,
                                                              i18n.for_sess(sess)))
        sess.busy_msg_id = None
        sess.turn_sender_id = None
        sess.close_all_prompts()
        sess.pending_permission = None
        sess.active_subagents.clear()
        sess.job_interim_seen = False
        sess.job_continuation_active = False
        sess.job_grace_until = 0.0
        sess.trigger_msg_id = None
        sess.busy_card_trigger = None
        sess.user_stopped_at = time.monotonic()
        sess.last_idle_at = time.monotonic()
        self.registry.mark_dirty()
        log.info("[%s] Stopped by user (background job stopped, session brought back)",
                 sess.label)
        return StopOutcome(ok=True, label=sess.label, by=by)

    async def _stop_session(self, sess: TrackedSession,
                            update: Update | None = None,
                            query=None) -> StopOutcome:
        """Interrupt a busy session: send Escape, clean up state.

        Thin wrapper around :meth:`_stop_session_core` — chat's own
        acknowledgement (edit the busy message / react to the command),
        the core's behaviour lives in the core.
        """
        if query is not None and not sess.tap_is_for_this_turn(
                getattr(getattr(query, "message", None), "message_id", None)):
            # Button taps are checked HERE rather than at the call site, so a
            # future caller cannot forget. `update`-driven callers (the /stop
            # command, `/<label> stop`) pass no query and are always current.
            await self._safe_answer(query, i18n.for_person(
                update if update is not None else SimpleNamespace(callback_query=query)
            ).text(TOAST_STOP_STALE))
            return StopOutcome(ok=False, label=sess.label, reason="stale")
        # Who stopped it, named in a group (roadmap 8.91c): the tapper or
        # the command's sender, in the chat the tap or command came from.
        person = (getattr(query, "from_user", None) if query is not None
                  else getattr(update, "effective_user", None))
        by = self._actor_label(
            getattr(person, "id", None),
            calling_chat_id(update if update is not None else query), person)
        outcome = await self._stop_session_core(sess, by=by)
        if not outcome.ok:
            if outcome.reason in BG_FAILURES:
                await self._say_stop_refused(sess, outcome, update, query)
            return outcome

        # Acknowledge, in the words every Stop uses.
        if query:
            await self._safe_answer(query, _stopped_line(
                outcome, html=False,
                tr=i18n.for_person(update if update is not None
                                   else SimpleNamespace(callback_query=query))))
            # Also edit the callback query's message if it's the busy message
            try:
                await edit_text(query, _stopped_card_text(sess.label, by, i18n.for_sess(sess)),
                                parse_mode="HTML")
            except Exception:
                pass
        elif update:
            # The 👌 reaction stays — it's the same instant, low-effort
            # acknowledgement every other command-driven action gets, and
            # is gone as soon as the operator looks away. (It was ✅,
            # which Telegram refuses as a bot reaction: 8.33.) The count itself
            # needs a durable, chat-visible message: tester-iter1-001
            # found /stop discarding Claude's own held-but-not-picked-up
            # queue (this feature's whole point) with the count nowhere
            # the operator could see it — a reaction alone is closer to a
            # vanishing toast than the "chat acknowledgement" entrypoints.md
            # promises (see intent.md's own open question #1).
            await self._react(update, reactions.ACK)
            if getattr(update, "message", None) is not None:
                try:
                    await reply_text(update.message,
                                     _stopped_line(outcome, tr=i18n.for_update(update)),
                                     parse_mode="HTML")
                except Exception:
                    log.debug("[%s] stop acknowledgement reply failed",
                              sess.label, exc_info=True)

        return outcome

    async def _say_stop_refused(self, sess: TrackedSession, outcome: StopOutcome,
                                update, query) -> None:
        """Why a Stop of a parked session did not happen: a pop-up for a
        tap, a reply for a command. Best-effort."""
        try:
            if query is not None:
                tr = i18n.for_person(update if update is not None
                                     else SimpleNamespace(callback_query=query))
                await self._safe_answer(query, stop_refused_text(tr, outcome))
            elif update is not None and getattr(update, "message", None) is not None:
                tr = i18n.for_update(update)
                await reply_text(update.message, stop_refused_html(tr, outcome),
                                 parse_mode="HTML")
        except Exception:
            log.debug("[%s] stop refusal notice failed", sess.label, exc_info=True)

    async def _tell_peer_sender(self, sess: TrackedSession, peer_from,
                                reason: str) -> None:
        """A turn a verified message from another session started was
        stopped by the safety rules: tell whoever handed the task over, so
        it is not silently lost. The sending session's chat when both
        sessions share a chat; otherwise only the owner's private chat, and
        only when the sending turn was the owner's or typed in its
        terminal: roles and session names never reach another chat
        (roadmap 8.73/8.76, C6)."""
        if not isinstance(peer_from, dict) or peer_from.get("status") != "verified":
            return
        sender = self.registry.get(peer_from.get("sender_session") or "")
        if sender is None or not self._app:
            return
        from aipager.bot import peer_notice, report_flow, safety_notice
        from aipager.bot.transport import resolve_chat_id
        to_chat, my_chat = resolve_chat_id(sender), resolve_chat_id(sess)
        driver = peer_from.get("driver") if isinstance(peer_from.get("driver"), dict) else {}
        if to_chat == my_chat:
            tr = i18n.for_sess(sender)
        else:
            if driver.get("class") not in ("owner", "terminal"):
                return
            owner = report_flow.resolve_owner(self)
            if owner is None:
                return
            to_chat = owner
            tr = i18n.for_chat(owner)
        text = tr.html(peer_notice.SENDER_NOTICE, receiver=sess.label,
                       sender=sender.label,
                       reason=Safe(safety_notice.reason_text(tr, reason)))
        await send_text(self._app.bot, to_chat, text, parse_mode="HTML")

    async def _halt_for_safety(self, sess: TrackedSession, reason: str) -> None:
        """Cleanly halt a session after a safety block.

        Same teardown as :meth:`_stop_session` — interrupt Claude
        (Escape×2), **cancel the busy animation** (else the spinner shows
        "thinking" forever), surface the block in place of the busy
        message, and return to IDLE so the session stays responsive.
        Best-effort throughout; never raises into the notify path.
        """
        from aipager.bot.transport import resolve_chat_id
        # 1. Interrupt Claude's turn. A session parked on a Claude Code
        # background job runs the turn in the job, and nothing may be typed
        # into the parked client: the job is stopped and the session
        # brought back instead (its held messages are dropped below). When
        # that fails the notice says so: the blocked work may go on there.
        job_left_running = False
        bg_outcome = None
        if sess.parked_job:
            try:
                bg_outcome = await self._halt_bring_back(sess)
                job_left_running = (bg_outcome.reason in _HALT_JOB_LEFT
                                    and bool(sess.parked_job))
                if not bg_outcome.ok:
                    log.warning("[%s] safety halt: background job not stopped (%s)",
                                sess.label, bg_outcome.reason)
            except Exception:
                job_left_running = True
                log.warning("[%s] safety halt bring-back failed", sess.label,
                            exc_info=True)
        else:
            try:
                await inject.send_keys(sess.name, "Escape")
                await _sleep(0.15)
                await inject.send_keys(sess.name, "Escape")
            except Exception:
                log.debug("[%s] safety halt interrupt failed", sess.label,
                          exc_info=True)
        # 2. Cancel the spinner (the piece whose absence wedged "thinking"),
        # and a self-woken turn's deferred card with it (roadmap 8.32) —
        # same reasoning, and the same card lock, as `_stop_session_core`.
        async with sess.animate_lock:
            self._cancel_lazy_card(sess)
            sess.close_turn()  # no card for this turn from here (8.57)
        self._stop_animation(sess)
        # 3. Replace the busy message with the block notice (or send fresh),
        # in the chat's language; the reason Claude got stays English.
        from aipager.bot import peer_notice, safety_notice
        tr = i18n.for_sess(sess)
        # The first line says "(stopped)" only when it was: a background job
        # the halt could not stop may go on (the line added below says so).
        notice = safety_notice.notice_html(tr, sess.label, reason,
                                           stopped=not job_left_running)
        # A turn another Claude session started or narrowed: say so here,
        # and tell the sending session's chat ("peer message authority").
        peer_from = getattr(sess, "peer_from", None)
        suffix = peer_notice.halt_suffix(tr, peer_from,
                                         getattr(sess, "peer_narrowed", False))
        if suffix:
            notice = f"{notice}\n{suffix}"
        if job_left_running:
            from aipager.bg_text import halt_not_stopped_html
            notice = f"{notice}\n{halt_not_stopped_html(tr, bg_outcome, sess.label)}"
        try:
            # Written and retired under the card-edit lock, as in
            # `_stop_session_core`.
            if not await self._settle_card_text(
                    sess, notice) and self._app:
                await send_text(self._app.bot,
                    resolve_chat_id(sess), notice, parse_mode="HTML")
        except Exception:
            log.debug("[%s] safety halt notice failed", sess.label,
                      exc_info=True)
        try:
            await self._tell_peer_sender(sess, peer_from, reason)
        except Exception:
            log.debug("[%s] peer sender notice failed", sess.label,
                      exc_info=True)
        # 4. Return to IDLE. Held messages are dropped (R3); what Claude
        # had queued is only pulled back into its input box by the
        # Escapes, not wiped, so its fate stays unknown and its 👀 stays.
        sess.busy_msg_id = None
        await self._mark_not_delivered(
            sess, reactions.held_entries(sess.pending_queue))
        sess.pending_queue.clear()
        sess.close_all_prompts()  # every open prompt (roadmap 8.120)
        sess.pending_permission = None
        sess.status = Status.IDLE
        # Interrupted like /stop: the turn is over (roadmap 8.77).
        _clear_turn_open(sess.name)
        sess.turn_sender_id = None
        sess.trigger_msg_id = None
        sess.busy_card_trigger = None
        self._discard_queued_targets(sess)
        sess.user_stopped_at = time.monotonic()
        sess.last_idle_at = time.monotonic()
        self.registry.mark_dirty()
        log.info("[%s] halted by safety policy", sess.label)

    async def _halt_bring_back(self, sess: TrackedSession) -> RestartOutcome:
        """A safety halt's forced bring-back of a parked session. While
        another bring-back runs (the message gate's, which never stops a
        busy job), it waits for that one to end, at most
        :data:`_HALT_WAIT_SECONDS`, and then stops the job itself when the
        session is still parked.

        Nothing held is released while it waits (``bg_release_blocked``):
        the other bring-back may have armed its release and its relaunched
        claude may come up meanwhile, but the halt drops the held messages,
        so the release is disarmed when the wait is over. Two halts at
        once each hold their own block (a count): the first one's end
        leaves the second still blocking."""
        sess.bg_release_blocks += 1
        try:
            outcome = await self._bring_back_core(sess, force=True, release_held=False)
            if outcome.reason != "already_restarting":
                return outcome
            waited = 0.0
            while sess.relaunch_in_flight and waited < _HALT_WAIT_SECONDS:
                await _sleep(_HALT_WAIT_POLL)
                waited += _HALT_WAIT_POLL
            if sess.relaunch_in_flight:
                return outcome
            if not sess.parked_job:
                # The other bring-back stopped the job and brought it back.
                return RestartOutcome(ok=True, reason="done", label=sess.label)
            return await self._bring_back_core(sess, force=True, release_held=False)
        finally:
            sess.disarm_bg_release()
            sess.bg_release_blocks = max(0, sess.bg_release_blocks - 1)

    async def _kill_session_core(self, session_name: str, label: str) -> KillOutcome:
        """Kill a session by its (already-resolved) dtach session name.

        The shared seam both chat and the Mini App call — edit behaviour
        here, not in the wrapper.

        Refuses with ``"resuming"`` while a resume is in flight. The
        session is GONE for the whole of that launch, so nothing else here
        would stop the kill from removing the entry, after which the
        resume's own ``transition()`` fabricates a blank replacement and
        restores cwd/scope/perms onto an orphan while reporting success
        (roadmap 8.16). The window is seconds and bounded by
        ``RESUME_GUARD_SECONDS``, so telling the operator to try again is
        honest; cancelling a resume mid-launch is not something this seam
        can do safely.
        """
        sess = self.registry.get(session_name)
        if sess is not None and sess.is_resuming():
            return KillOutcome(
                result="resuming", label=label, session_name=session_name,
            )
        _HELD_BACK.pop(session_name, None)  # no second Enter for the dead
        if sess:
            # A self-woken turn's deferred card (roadmap 8.32) dies with the
            # session: the entry is dropped from the registry below, out of
            # reach of every later cleanup, and its timer would otherwise
            # put a live card up for a dead session. Under the card lock,
            # as `_stop_session_core` does, so a send already in flight
            # lands as the session's known card before the animation stops.
            async with sess.animate_lock:
                self._cancel_lazy_card(sess)
                sess.close_turn()  # no card for this turn from here (8.57)
            self._stop_animation(sess)

        # Recorded BEFORE the kill: killing the dtach host closes Claude
        # Code's pty, and the SessionEnd it then fires (reason "other") can
        # be handled while kill_session is still waiting for the host to
        # exit. Keyed by this process's Claude session id, not the name:
        # session_end skips the notice only for a SessionEnd carrying that
        # id, so a new process under the same name — however it was
        # launched — has a new id and is announced. No known id: nothing
        # is recorded and the notice goes out (fail open).
        killed_sessions = self.registry.killed_sessions
        now = time.monotonic()
        for sid in [s for s, t in killed_sessions.items()
                    if now - t >= KILL_NOTICE_QUIET_SECONDS]:
            del killed_sessions[sid]
        killed_sid = sess.claude_session_id if sess is not None else ""
        if killed_sid:
            killed_sessions[killed_sid] = now

        # Kill the dtach process
        try:
            killed = await inject.kill_session(session_name)
        except BaseException:
            # It may still be running: nothing to hide (fail open).
            if killed_sid:
                killed_sessions.pop(killed_sid, None)
            raise
        if not killed and killed_sid:
            # It is still running (or was never there): nothing to hide.
            killed_sessions.pop(killed_sid, None)
        if killed:
            # Its turn died with it (roadmap 8.77). The registry removal
            # below clears the session's files too; this does not wait
            # for it.
            _clear_turn_open(session_name)
        if killed and sess is not None:
            # Held messages, notes and Claude's own queue all die with the
            # session: none of them will be taken (R3).
            from aipager.policy_snapshot import list_outstanding_notes
            queued_targets = await self._settle_queued_targets(
                sess, reanchor=False)
            await self._mark_not_delivered(
                sess, reactions.held_entries(sess.pending_queue)
                + list_outstanding_notes(session_name) + queued_targets)
            self._discard_queued_targets(sess)
        if killed:
            # remember_label: the entry is about to be re-created by the
            # monitor's next scan (the socket outlives SIGTERM briefly), and
            # without this the re-creation derives the label from the
            # internal name and silently undoes a /rename.
            self.registry.remove(session_name, remember_label=True)
            self.registry.mark_dirty()
            _spawn(self._update_bot_commands())
            return KillOutcome(result="killed", label=label, session_name=session_name)
        if await inject.is_alive(session_name):
            # Socket still there: the process survived. Saying "not found"
            # here would tell the user it is gone while it keeps running.
            return KillOutcome(
                result="still_running", label=label, session_name=session_name,
            )
        return KillOutcome(result="not_found", label=label, session_name=session_name)

    async def _do_resume_core(
        self, sess: TrackedSession, *,
        mode_override: str | None = None,
        driver_user_id: int | None = None,
        force: bool = False,
        force_job: str = "",
    ) -> ResumeOutcome:
        """Resume an already-resolved GONE session.

        The shared seam both chat and the Mini App call — edit behaviour
        here, not in the wrapper. Assumes ``sess`` is already resolved
        (never receives ``None`` — the caller handles "no such session"
        itself, since that reply differs by surface and needs no dtach
        work at all).

        ``driver_user_id``, when given, attributes this resume directly
        (mirroring :meth:`create_session`'s own precedent) rather than
        via :meth:`aipager.bot.auth.AuthMixin._mark_driver`, which needs
        an ``Update`` the Mini App does not have.

        Before the launch, where the conversation moved is followed and a
        Claude Code background job holding it is dealt with
        (:meth:`_resume_target`): an idle one is stopped, a busy one gives
        ``bg_busy`` unless *force*, a failed stop ``bg_stop_failed``; both
        leave the session as it was. *force_job* (a button's job key): force
        only a job whose id starts with it.
        """
        if sess.status != Status.GONE:
            return ResumeOutcome(ok=False, reason="not_gone")

        if not sess.claude_session_id:
            return ResumeOutcome(ok=False, reason="no_transcript")

        label = sess.label
        session_name = sess.name
        resume_id = sess.claude_session_id
        cwd = sess.cwd or None
        # Defensive: clear the id BEFORE launch so a repeat-failure doesn't
        # rope us into an infinite resume loop (claude --resume against a
        # deleted transcript dies → socket disappears → /resume retries).
        # Cleared before the background check too: a second resume while
        # it runs finds nothing to resume, as during the launch.
        sess.claude_session_id = ""
        self.registry.mark_dirty()
        sess.resuming_until = time.monotonic() + RESUME_GUARD_SECONDS
        target, refusal = await self._resume_target(resume_id, force=force,
                                                    force_job=force_job)
        if refusal is not None:
            sess.resuming_until = 0.0
            sess.claude_session_id = resume_id
            self.registry.mark_dirty()
            reason, err = refusal
            english = ""
            if err:
                from aipager import bg_text
                english = bg_text.error_text(i18n.get("en"), err[0], err[1])
            return ResumeOutcome(ok=False, reason=reason, err=english, bg_err=tuple(err))
        resume_id = target
        # Its last record may still say it moved into a job that is gone
        # now (it wrote no transcript): noted once the launch worked.
        stale_job = await self._stale_move(resume_id)

        # Use the session's actual (possibly scope-disambiguated) name so
        # resume reattaches the right socket, not a flat claude-<label>.
        short_name = session_name.removeprefix("claude-")
        # The effective mode: an explicit override wins, then the
        # persisted value (already recovered from the state file). Passed
        # explicitly: a resumed bypass session would otherwise drop back
        # to Claude Code's own starting mode.
        effective_mode = (
            mode_override if mode_override is not None else sess.perm_mode
        )
        sys_extra = self._session_system_prompt(sess.scope_chat_id, label)
        # Hold off the ageing sweep for the launch AND the restore that
        # follows it: until `gone_at` is cleared below, this session is
        # still GONE carrying its original stamp, so a sweep landing here
        # would `remove()` the entry and leave everything below mutating
        # an orphan while the operator was told "Resumed" (roadmap 8.12
        # follow-up). Released in the `finally` on every path.
        sess.resuming_until = time.monotonic() + RESUME_GUARD_SECONDS
        _clear_turn_open(session_name)  # no turn runs in it (8.77)
        try:
            ok, err = await inject.launch_session(
                short_name, resume_id=resume_id, cwd=cwd,
                mode=effective_mode, is_relaunch=True,
                system_prompt_extra=sys_extra, model=sess.launch_model or None,
            )
            if not ok:
                # Restore the id so the user can try again after fixing
                # whatever broke (e.g. removing a stale socket).
                sess.claude_session_id = resume_id
                self.registry.mark_dirty()
                return ResumeOutcome(ok=False, reason="launch_failed", err=err)

            # Resume succeeded — recover state.
            sess.gone_at = None
            sess.note_moved_handled(resume_id if stale_job else "", stale_job)
            # A resumed claude starts with an empty input queue: a target the
            # old process had queued but never absorbed must not start a
            # phantom turn later (review rev-iter2-001).
            self._discard_queued_targets(sess)
            sess.perm_mode = effective_mode
            sess.reset_mode_tracking()
            sess.mode_launch_unchecked = True
            self.registry.transition(session_name, Status.IDLE)
            if driver_user_id is not None:
                sess.last_driver_user_id = driver_user_id
                sess.created_by_user_id = (
                    sess.created_by_user_id or driver_user_id
                )
            self.registry.set_target(
                session_name, sess.scope_chat_id or None, driver_user_id)
            self.registry.mark_dirty()
        finally:
            sess.resuming_until = 0.0
        _spawn(self._maybe_update_bot_name(session_name))
        _spawn(self._update_bot_commands())

        log.info("[%s] Resumed (claude_session_id=%s, cwd=%s)",
                 label, resume_id, cwd or "<daemon>")
        return ResumeOutcome(ok=True, reason="resumed")

    def _resume_full_gate(
        self, sess: TrackedSession, mode_override: str | None,
        actor: int | None, chat_id: int | None,
    ) -> tuple[str | None, bool]:
        """The mode a resume may bring *sess* back in (roadmap 8.83).

        Returns ``(mode_override, full_refused)``: when the resume would
        land in Full access (the override, else the session's saved mode)
        and *actor* may not use it in *chat_id* (``_may_use_full_access``),
        the override becomes
        Auto (``perm_modes.narrowest_allowed``) and ``full_refused`` is
        True. Otherwise the override passes through unchanged. Every
        resume surface (typed ``/resume``, the picker, the ``/new``
        conflict card, the Mini App) asks this, so none brings Full access
        back for someone who could not switch to it."""
        wants = mode_override if mode_override is not None else sess.perm_mode
        if wants == perm_modes.FULL and not self._may_use_full_access(actor, chat_id):
            return perm_modes.narrowest_allowed(wants, False), True
        return mode_override, False

    async def _do_resume(self, *, label: str, reply_fn,
                          update: Update | None = None,
                          query=None,
                          mode_override: str | None = None,
                          sess: TrackedSession | None = None,
                          force: bool = False, force_job: str = "") -> None:
        """Shared resume logic for /resume <name> and picker callbacks.

        ``reply_fn`` is the async-callable used to send the result back
        (``update.message.reply_text`` for command, ``query.edit_message_text``
        for callbacks). ``update`` is used to attribute the driver in team
        mode when available.

        Thin wrapper around :meth:`_do_resume_core`: this is the only
        place that resolves the label to a session and handles "no such
        session" — the core is never called with ``sess is None``.
        """
        # The chat the command or tap came from (keyword signature kept:
        # stand-ins take this exact call).
        tr = i18n.for_update(update if update is not None
                             else SimpleNamespace(callback_query=query))
        if sess is None:
            # ``sess``: a caller that already holds the exact session (the
            # /new conflict card) passes it; a label can name another
            # chat's session.
            sess = self.registry.find_by_label(
                label, calling_chat_id(update or query), include_gone=True)

        if sess is None:
            await reply_fn(tr.html(RESUME_NOT_FOUND, session=label,
                                   command=Span("<code>/resume</code>")),
                           parse_mode="HTML")
            return

        driver_user_id = (
            update.effective_user.id
            if update is not None and update.effective_user is not None
            else None
        )
        # Full access needs an admin, like every other way into it
        # (roadmap 8.83): a session saved in Full access, or the Full
        # access button, comes back in Auto for anyone else, and the reply
        # says so.
        actor = driver_user_id
        if actor is None and query is not None:
            from_user = getattr(query, "from_user", None)
            actor = getattr(from_user, "id", None)
        mode_override, full_refused = self._resume_full_gate(
            sess, mode_override, actor,
            calling_chat_id(update or query))
        # A role without can_manage resumes only a session inside its
        # members' root folder.
        from aipager import member_root
        refusal = member_root.relaunch_refusal(
            self, actor, calling_chat_id(update or query), sess.cwd)
        if refusal:
            await reply_fn(member_root.as_html(tr, refusal), parse_mode="HTML")
            return
        # The GONE snapshot, read before the core resumes the session:
        # leaving GONE clears it (state.transition, roadmap 8.34), and it
        # is still the right "where you left off" for the recap below.
        snapshot = sess.last_assistant_preview
        if force:
            # *force_job*: a button shown for one job forces only that job.
            outcome = await self._do_resume_core(
                sess, mode_override=mode_override,
                driver_user_id=driver_user_id, force=True,
                **({"force_job": force_job} if force_job else {}),
            )
        else:
            outcome = await self._do_resume_core(
                sess, mode_override=mode_override,
                driver_user_id=driver_user_id,
            )

        if outcome.reason in BG_FAILURES:
            # A Claude Code background job holds the conversation: busy,
            # the question with its two buttons; otherwise why it failed.
            text = bg_outcome_html(tr, outcome, label)
            if outcome.reason == "bg_busy":
                from aipager.bot import session_parity
                kb = session_parity.bg_busy_keyboard(
                    self, calling_chat_id(update or query) or 0, sess, tr)
                await reply_fn(text, reply_markup=kb, parse_mode="HTML")
            else:
                await reply_fn(text, parse_mode="HTML")
            return

        if outcome.reason == "not_gone":
            # The command is code, kept whole (nothing invisible inside it).
            await reply_fn(tr.html(RESUME_RUNNING, session=label, command=Span(
                f"<code>/{html_mod.escape(label)}</code>")), parse_mode="HTML")
            return

        if outcome.reason == "no_transcript":
            await reply_fn(tr.html(RESUME_NO_TRANSCRIPT, session=label, command=Span(
                f"<code>/new {html_mod.escape(label)}</code>")), parse_mode="HTML")
            return

        if outcome.reason == "launch_failed":
            await reply_fn(tr.html(RESUME_FAILED, session=label, error=outcome.err),
                           parse_mode="HTML")
            return

        # Resumed — dashboard out the result.
        dashboard = self._build_session_dashboard(sess)
        # Always try to surface where the user left off. If the cached
        # preview is empty (e.g. SessionEnd hook was dropped at GONE
        # time) re-derive from the transcript file on disk. A longer
        # cap here gives enough context to remember the conversation.
        preview = snapshot or _read_preview(
            sess.transcript_path, max_chars=500,
        )
        header = (tr.html(RESUMED_IN_AUTO, session=label)
                  if full_refused else tr.html(RESUMED, session=label))
        if preview:
            body = (
                f"{header}\n\n"
                f"{dashboard}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{tr.html(RESUME_LAST_RESPONSE, preview=preview)}"
            )
        else:
            body = f"{header}\n\n{dashboard}"

        await reply_fn(body, parse_mode="HTML")

    async def _stop_by_label(self, update: Update, target_label: str) -> None:
        """Stop a session by its label."""
        sess = self.registry.find_by_label(target_label, calling_chat_id(update))
        tr = i18n.for_update(update)
        if sess is not None:
            outcome = await self._stop_session(sess, update=update)
            if not outcome.ok:
                await reply_text(update.message, tr.html(NOT_WORKING, session=sess.label),
                                 parse_mode="HTML")
            return
        await reply_text(update.message, tr.html(NO_SESSION_HERE, session=target_label),
                         parse_mode="HTML")

    def _guess_session_from_text(
        self, text: str, scope_chat_id: int | None = None,
    ) -> TrackedSession | None:
        """Try to recover a session by scanning bot-message text for its label.

        Used to route replies to OLD bot messages whose IDs aren't in the
        msg_map (e.g., busy / dashboard messages sent before track_message
        covered them, or after a long enough session that the map evicted
        them). Every session-specific bot message prefixes the label as
        visible text ("⚙️ jim · Thinking…", "[jim] · INTERACTIVE", etc.),
        so a simple substring search with word-boundary checks works.

        Falls back to None when no label is found or multiple labels match
        ambiguously — better to defer to last_active_session than to
        guess wrong.

        ``scope_chat_id`` (design.md Part 0) scopes the label scan to one
        chat, same as :meth:`SessionRegistry.all_sessions` /
        :meth:`SessionRegistry.find_by_label` — a label visible in one
        chat's transcript must never resolve to a same-named session
        living in a different chat. Defaults to ``None`` (no scoping) so
        the handful of unit tests that call this directly keep working.
        """
        if not text:
            return None
        matches: list[TrackedSession] = []
        for cand in self.registry.all_sessions(scope_chat_id).values():
            if not cand.label or cand.status == Status.GONE:
                continue
            # Word-bounded match: label preceded by start / space / bracket /
            # non-alnum, and followed by space / · / ] / dot / non-alnum.
            pattern = (
                rf"(?:^|[\s\W])\[?{re.escape(cand.label)}\]?(?:[\s·•\].:]|$)"
            )
            if re.search(pattern, text):
                matches.append(cand)
        if len(matches) == 1:
            return matches[0]
        return None

    def _resolve_reply_target(
        self, reply_to, chat_id: int | None,
    ) -> TrackedSession | None:
        """Scoped levels 1-3 of the reply-routing ladder (design.md Part 0).

        1. exact hit in the msg_map, 2. a scan for any session whose
        ``last_msg_id`` matches (catches replies too old for the capped
        map), 3. a text-guess from the replied-to message's own text
        (catches replies from before this daemon run). All three are
        scoped to ``chat_id`` — see :meth:`SessionRegistry.get_session_by_msg`
        — so a message id (or a visible label) that collides across two
        chats can never resolve into the wrong chat's session.

        Returns ``None`` to signal "fall through to last_active_session"
        (level 4) — that stays inline at each of the three call sites,
        since their "nothing found at all" wording (level 5) differs.
        """
        if not reply_to:
            return None
        scope = chat_id if chat_id is not None else 0
        sess = self.registry.get_session_by_msg(reply_to.message_id, scope)
        if sess:
            return sess
        for cand in self.registry.all_sessions(scope).values():
            if cand.last_msg_id == reply_to.message_id:
                return cand
        return self._guess_session_from_text(
            reply_to.text or reply_to.caption or "", scope,
        )

    async def _switch_session(self, update: Update, target_label: str) -> None:
        """Switch active session when bare /<label> is tapped (no prompt)."""
        # Find in registry within the calling scope
        sess = self.registry.find_by_label(target_label, calling_chat_id(update))
        if sess is not None:
            name = sess.name
            self.registry.set_target(
                name, calling_chat_id(update), calling_user_id(update))
            self.registry.mark_dirty()
            _spawn(self._maybe_update_bot_name(name))
            text, kb = self._render_switch_reply(
                calling_chat_id(update) or 0, sess, update)
            await reply_text(update.message, text, parse_mode="HTML", reply_markup=kb)
            return

        # Try auto-discover
        session_name = f"claude-{target_label}"
        sess = None
        if await inject.is_alive(session_name):
            sess = self._adopt_by_typed_name(
                session_name, target_label, calling_chat_id(update))
        if sess is not None:
            self.registry.set_target(
                session_name, calling_chat_id(update), calling_user_id(update))
            self.registry.mark_dirty()
            _spawn(self._maybe_update_bot_name(session_name))
            _spawn(self._update_bot_commands())
            text, kb = self._render_switch_reply(
                calling_chat_id(update) or 0, sess, update)
            await reply_text(update.message, text, parse_mode="HTML", reply_markup=kb)
            return

        await reply_text(update.message, i18n.for_update(update).text(
            UNKNOWN_SESSION, session=target_label))

    # ── Mini App session menu actions (perms / clear-queue / compact /
    #    restart / rename) — see design.md's ORCHESTRATOR OVERRIDE ──

    async def _kill_and_relaunch_core(
        self, sess: TrackedSession, *, target_mode: str,
        interrupt_first: bool, force: bool = False,
    ) -> RestartOutcome:
        """Kill (or interrupt) a live session, poll for its dtach socket
        to disappear, then relaunch it in place with a possibly-different
        permission mode — preserving ``claude_session_id``/``cwd`` so the
        transcript resume continuity is maintained.

        THE single seam every kill-then-relaunch sequence in this
        codebase goes through now — chat's own ``/perms`` (both its BUSY
        Ctrl-C-first branch and its IDLE hard-kill branch, in
        callbacks.py) and the Mini App's perms-switch and restart
        routes. This sequence has the worst bug history in the codebase
        (roadmap 8.1, two separate causes) — see design.md's
        ORCHESTRATOR OVERRIDE for why it now exists in exactly one
        place rather than being duplicated a third time.

        Re-validates liveness and the ``relaunch_in_flight`` guard itself
        rather than trusting the caller, the same belt-and-braces
        pattern every other core in this module already uses: a stale
        menu, a direct API probe, a second tap or a race with the session
        monitor must never be able to kick off a second kill/relaunch
        while one is already in flight. Every caller says so in words.

        A session parked on a Claude Code background job is brought back
        instead (:meth:`_bring_back_core`, keeping *target_mode*), before
        anything is killed or typed: its conversation lives in the job,
        and a Ctrl-C typed into the parked client could start a stray
        job. *force* stops a busy job (otherwise ``bg_busy``); for a
        session that is not parked it is the same answer to a live
        background holder of its conversation (:meth:`_resume_target`).
        """
        label = sess.label
        if sess.status not in (Status.BUSY, Status.INTERACTIVE, Status.IDLE):
            return RestartOutcome(ok=False, reason="not_live", label=label)
        if sess.parked_job:
            return await self._bring_back_core(sess, force=force,
                                               target_mode=target_mode)
        if sess.relaunch_in_flight:
            return RestartOutcome(ok=False, reason="already_restarting", label=label)
        sess.relaunch_in_flight = True
        try:
            return await self._kill_and_relaunch_in_flight(
                sess, target_mode=target_mode,
                interrupt_first=interrupt_first, force=force)
        finally:
            sess.relaunch_in_flight = False

    async def _kill_and_relaunch_in_flight(
        self, sess: TrackedSession, *, target_mode: str,
        interrupt_first: bool, resume_id: str | None = None,
        force: bool = False, before_launch=None,
    ) -> RestartOutcome:
        """The body of :meth:`_kill_and_relaunch_core`, run while its
        ``relaunch_in_flight`` is held.

        *before_launch*: called (synchronously) once the old process is
        gone, right before the new one is launched: the bring-back arms
        its release there, so the relaunched claude's first SessionStart
        can never come before it.

        *resume_id*: the conversation to relaunch with instead of
        ``claude_session_id`` (the bring-back's, which has already dealt
        with its background job); ``claude_session_id`` is that id
        afterwards. Without it, the session's own conversation is checked
        first (:meth:`_resume_target`): where it moved (``continued-in``)
        is followed, and a live background holder is stopped when idle,
        or refuses with ``bg_busy`` unless *force*."""
        label = sess.label
        session_name = sess.name
        own_conversation = resume_id is None
        if own_conversation:
            resume_id = sess.claude_session_id
        stale_job = ""
        cwd = sess.cwd or None
        sock = f"{inject.SOCK_PREFIX}{session_name.removeprefix('claude-')}.sock"

        # Everything from here to a confirmed relaunch is an expected
        # outage. Opened before the kill so the dying session's own
        # SessionEnd hook — which can arrive before kill_session/send_keys
        # returns — is covered too. A failure below gives back what was
        # there before, not zero: an earlier relaunch's window may still
        # be hushing its own old process's late SessionEnd.
        earlier_window = sess.restarting_until
        sess.restarting_until = time.monotonic() + _PERMS_RESTART_QUIET

        if own_conversation and resume_id:
            # R1/R2: where the conversation moved, and whether a Claude
            # Code background job holds it (it would refuse the resume).
            target, refusal = await self._resume_target(resume_id, force=force)
            if refusal is not None:
                sess.restarting_until = earlier_window
                return self._bg_refused_restart(sess, refusal, target_mode)
            resume_id = target
            # Its last record may still say it moved into a job that is
            # gone now: noted once the relaunch worked.
            stale_job = await self._stale_move(resume_id)
            # The stop above may have taken seconds: the window runs from now.
            sess.restarting_until = time.monotonic() + _PERMS_RESTART_QUIET

        # The turn being killed will never earn a card (roadmap 8.32):
        # stand its deferral down before the kill, because the monitor
        # skips a restarting session and a timer firing between the kill
        # and the relaunch would put a card up for a turn that is gone.
        # No card lock needed: nothing here settles a card afterwards, and
        # a send already in flight is past the point a cancel can reach.
        self._cancel_lazy_card(sess)

        if interrupt_first:
            await inject.send_keys(session_name, "C-c")
            # Short pause to let the Ctrl-C signal be processed before
            # the poll loop starts checking for the socket.
            await _sleep(0.5)
        else:
            await inject.kill_session(session_name)

        # Poll for socket disappearance (up to 3 s).
        for _ in range(_PERMS_POLL_COUNT):
            await _sleep(_PERMS_POLL_INTERVAL)
            if not Path(sock).is_socket():
                break
        else:
            # Socket still present after 3 s — give up. Reopen the
            # alarm: no relaunch is coming, so whatever state the
            # session is in now is news the caller needs.
            sess.restarting_until = earlier_window
            return RestartOutcome(ok=False, reason="still_stopping", label=label)

        short_name = session_name.removeprefix("claude-")
        sys_extra = self._session_system_prompt(sess.scope_chat_id, label)
        # The killed or interrupted turn is over (an interrupt may fire no
        # Stop), and the relaunched process starts with none (roadmap 8.77).
        _clear_turn_open(session_name)
        if before_launch is not None:
            before_launch()
        ok, err = await inject.launch_session(
            short_name, mode=target_mode,
            resume_id=resume_id or None, cwd=cwd, is_relaunch=True,
            system_prompt_extra=sys_extra, model=sess.launch_model or None,
        )
        if not ok:
            # The relaunch failed, so the session really is gone — stop
            # suppressing, or the caller is left believing it survived.
            sess.restarting_until = earlier_window
            return RestartOutcome(
                ok=False, reason="launch_failed", label=label, err=err,
            )

        # Relaunch succeeded — restore state.
        sess.perm_mode = target_mode
        sess.reset_mode_tracking()
        sess.mode_launch_unchecked = True
        sess.claude_session_id = resume_id  # restore — launch_session may clear
        if own_conversation:
            # A bring-back notes its own (at :meth:`_clear_park`).
            sess.note_moved_handled(resume_id if stale_job else "", stale_job)
        sess.cwd = cwd or sess.cwd
        sess.gone_at = None
        # The relaunched claude starts with an empty input queue: a
        # message the old process had queued but never absorbed must not
        # start a phantom turn later ("anchor-on-transcript-consumption",
        # review rev-iter1-002).
        self._discard_queued_targets(sess)
        self.registry.transition(session_name, Status.IDLE)
        # Nobody's turn survives the relaunch (roadmap 8.77), not even a
        # job whose agents the old process took with it.
        sess.turn_sender_id = None
        self.registry.mark_dirty()

        log.info("[%s] kill+relaunch done (mode=%s, interrupt_first=%s)",
                 label, target_mode, interrupt_first)
        return RestartOutcome(
            ok=True, reason="done", label=label, mode=target_mode,
        )

    # ── Claude Code background jobs: bring a parked session back ──

    def _bring_back_refusal(self, sess: TrackedSession, user_id, chat_id):
        """Why *user_id*, acting from *chat_id*, may not bring *sess* back
        from Claude Code's background, or None: exactly Restart's check
        (a bring-back relaunches the session). ``"prompt"`` (may not
        prompt here), ``"foreign"`` (another chat's session) or the
        members' root refusal (``member_root.relaunch_refusal``)."""
        from aipager import member_root
        from aipager.state import session_foreign_to
        if not self._can_prompt_user(user_id, chat_id):
            return "prompt"
        if session_foreign_to(sess, chat_id):
            return "foreign"
        refusal = member_root.relaunch_refusal(self, user_id, chat_id, sess.cwd)
        return refusal or None

    async def _stop_bg_job(self, job: str, session_ids) -> tuple:
        """``claude stop <job>``, then wait (bounded) until no live
        background process holds any of *session_ids*. ``()`` when that
        worked, else the error ``(code, params)`` (``aipager.bg_text``)."""
        from aipager import claude_jobs
        run = await claude_jobs.run_claude(["stop", job], claude_jobs.STOP_TIMEOUT)
        code, params = claude_jobs.stop_error(run)
        if code:
            log.warning("claude stop %s failed: %s (rc=%s)", job, code, run.rc)
            return code, params
        ids = [i for i in dict.fromkeys(session_ids) if i]
        polls = int(claude_jobs.HOLDER_WAIT / claude_jobs.HOLDER_POLL)
        for n in range(polls + 1):
            held = await claude_jobs.off_loop(
                lambda: any(claude_jobs.live_holder(i) is not None for i in ids))
            if not held:
                return ()
            if n < polls:
                await _sleep(claude_jobs.HOLDER_POLL)
        log.warning("background job %s still holds its conversation after claude stop",
                    job)
        return "still_running", {}

    async def _resume_target(self, session_id: str, *, force: bool,
                             force_job: str = "") -> tuple:
        """R1 and R2 before *session_id* is resumed: ``(id, refusal)``.

        *id* is where the conversation went (``continued-in`` followed,
        ``claude_jobs.follow_continued``). *refusal* is None when it may be
        launched: nothing holds it, or the Claude Code background job that
        held it was idle (or *force*) and has been stopped. Otherwise it is
        ``("bg_busy", ())`` (the job is working, or its state cannot be
        read; nothing was done) or ``("bg_stop_failed", (code, params))``.
        An unreadable registry launches as before. *force_job* (the job a
        keyed button was shown for): *force* applies only to a holder whose
        job id starts with it; any other holder is asked about. Never
        raises."""
        from aipager import claude_jobs

        def look():
            entries = claude_jobs.read_registry()
            target = claude_jobs.follow_continued(session_id, entries)
            holder = (claude_jobs.live_holder(target, entries)
                      if entries is not None else None)
            # A hop to a live holder's conversation with no transcript (an
            # idle job that never ran a prompt): that holder is dealt with
            # below, but the conversation launched is the last one saved
            # (hops through saved transcripts only), never one claude
            # cannot resume.
            saved = (target if target == session_id
                     or claude_jobs.find_transcript(target) is not None
                     else claude_jobs.follow_continued(session_id, []))
            return entries, target, holder, saved

        try:
            entries, target, holder, saved = await claude_jobs.off_loop(look)
        except Exception:
            log.warning("resume target of %s not checked", session_id, exc_info=True)
            return session_id, None
        if target != session_id:
            log.info("resuming %s instead of %s, where its conversation moved",
                     saved, session_id)
        if holder is None:
            return saved, None
        job = holder.get("jobId") or ""
        if not claude_jobs.valid_job(job):
            return saved, ("bg_stop_failed", ("still_running", {}))
        if force and force_job and not job.startswith(force_job):
            # A "Stop and bring back" shown for an earlier job: another job
            # holds the conversation now, and it was never asked about.
            log.info("conversation %s: a button for job %s does not stop job %s",
                     target, force_job, job)
            force = False
        try:
            state = await claude_jobs.job_state(job, entries)
        except Exception:
            log.warning("state of background job %s unknown", job, exc_info=True)
            state = claude_jobs.UNKNOWN
        if state in (claude_jobs.BUSY, claude_jobs.UNKNOWN) and not force:
            log.info("conversation %s is held by busy background job %s", target, job)
            return saved, ("bg_busy", ())
        if state != claude_jobs.GONE:
            err = await self._stop_bg_job(job, [target])
            if err:
                return saved, ("bg_stop_failed", err)
        log.info("stopped background job %s, which held conversation %s", job, target)
        return saved, None

    @staticmethod
    def _bg_refused_restart(sess: TrackedSession, refusal: tuple,
                            mode: str) -> RestartOutcome:
        reason, err = refusal
        english = ""
        if err:
            from aipager import bg_text
            english = bg_text.error_text(i18n.get("en"), err[0], err[1])
        return RestartOutcome(ok=False, reason=reason, label=sess.label, mode=mode,
                              err=english, bg_err=tuple(err))

    @staticmethod
    async def _bring_back_conversation(sess: TrackedSession, job: str, entries) -> str:
        """The conversation a bring-back resumes (B2.1): the job's, when
        its transcript exists; else the parked client's own (a fork that
        never ran a prompt), when its transcript exists; ``""`` when
        neither does. The session's own id stands in for the client's
        when the registry cannot name the client. The transcripts are
        looked for off the event loop."""
        from aipager import claude_jobs
        entry = claude_jobs.job_entry(job, entries)
        client = claude_jobs.find_client(sess, entries) if entries else None
        candidates = [
            entry.get("sessionId") if entry else "",
            sess.parked_job_session_id,
            client.get("sessionId") if client else "",
            sess.claude_session_id,
        ]

        def first_saved() -> str:
            for sid in dict.fromkeys(c for c in candidates if c):
                if claude_jobs.find_transcript(sid) is not None:
                    return sid
            return ""

        return await claude_jobs.off_loop(first_saved)

    def _clear_park(self, sess: TrackedSession, resume_id: str, job: str) -> None:
        """Forget *sess*'s park after a bring-back: the link file and its
        pid-map entry go (before the fields: the cleanup reads them), the
        fields are cleared. The old client's pid was forgotten before the
        launch (:meth:`_bring_back_in_flight`): the pid the relaunched
        claude's own SessionStart recorded meanwhile is kept.

        The move into job *job* is noted as handled for conversation
        *resume_id* (``TrackedSession.moved_handled_sid``): for an idle job
        that wrote no transcript, *resume_id* is the client's own
        conversation, whose last record still says ``continued-in`` to that
        job, and until the relaunched claude writes its registry entry the
        transcript fallback would park the session on it again."""
        from aipager.state import _drop_bg_files
        _drop_bg_files(sess.name, sess)
        sess.forget_park()
        sess.note_moved_handled(resume_id, job)
        self.registry.mark_dirty()

    @staticmethod
    async def _stale_move(resume_id: str) -> str:
        """The job conversation *resume_id* says it moved into (its last
        record is ``continued-in`` to an id that starts with it), when
        *resume_id* is about to be launched on purpose: that record is
        stale from then on (``TrackedSession.moved_handled_sid``). ``""``
        when it does not end moved. Read off the event loop; never
        raises."""
        from aipager import claude_jobs
        if not resume_id:
            return ""

        def look() -> str:
            path = claude_jobs.find_transcript(resume_id)
            target = claude_jobs.transcript_continued_in(path) if path is not None else ""
            return target[:8] if target and claude_jobs.valid_job(target[:8]) else ""

        try:
            return await claude_jobs.off_loop(look)
        except Exception:
            log.debug("transcript of %s not read", resume_id, exc_info=True)
            return ""

    async def _bring_back_core(
        self, sess: TrackedSession, *, force: bool = False,
        release_held: bool = True, target_mode: str | None = None,
        settled: str = "",
    ) -> RestartOutcome:
        """Bring a session parked on a Claude Code background job back into
        its own terminal: the one core the park notice's button, Restart,
        a restart-type mode switch, Stop, a safety halt and the message
        gate use.

        1. The conversation (:meth:`_bring_back_conversation`), followed
           where it moved; none gives ``bg_no_conversation`` and nothing
           is touched (never an empty session in its place).
        2. Unless *force*: ``bg_busy`` with nothing touched when aipager
           sees a turn in flight (the session BUSY or INTERACTIVE, or work
           in flight: a turn moved mid-run keeps its card until the
           job's Stop), when the job is busy or its state unknown, or
           while the park is younger than
           ``claude_jobs.PARK_SETTLE_SECONDS`` (a just-forked job reads
           idle for a moment before it continues a moved turn; then
           ``retry_after`` says how long until it is settled, and a caller
           that waited that long passes *settled*: the job it waited for,
           which counts only while the session is still parked on that
           job). The job's entry is
           read again right before ``claude stop``, and the stricter of
           the two answers counts.
        3. Under the restart window (opened first, so the job's
           SessionEnd and the parked client's exit are expected),
           ``claude stop <job>`` (skipped for a job that is gone), then a
           bounded wait until no process holds the conversation. A failure
           gives ``bg_stop_failed`` and leaves everything as it was.
        4. and 5. The parked client is killed and the conversation
           relaunched with aipager's own flags
           (:meth:`_kill_and_relaunch_in_flight`), in *target_mode* (else
           the session's own mode).
        6. The park is forgotten, ``bg.back`` is sent once, and with
           *release_held* the held messages go out once the relaunched
           claude is up: the release is armed right before the launch
           (``TrackedSession.arm_bg_release``); the relaunched claude's
           first own SessionStart releases them (``bg_release``), or, when
           it came while this was still running, this releases them at
           its end; the monitor's backstop releases them
           ``claude_jobs.RELEASE_BACKSTOP`` seconds later when no
           SessionStart came. One check-and-clear
           (``claim_bg_release``) makes it one release, whichever comes.
           Any failure after the arming disarms it.

        ``not_parked`` for a session that is not parked;
        ``already_restarting`` while a relaunch is in flight."""
        label = sess.label
        mode = target_mode or sess.perm_mode
        if not sess.parked_job:
            return RestartOutcome(ok=False, reason="not_parked", label=label, mode=mode)
        if sess.status == Status.GONE:
            return RestartOutcome(ok=False, reason="not_live", label=label, mode=mode)
        if sess.relaunch_in_flight:
            return RestartOutcome(ok=False, reason="already_restarting", label=label,
                                  mode=mode)
        sess.relaunch_in_flight = True
        try:
            return await self._bring_back_in_flight(
                sess, force=force, release_held=release_held, mode=mode,
                settled=settled)
        finally:
            sess.relaunch_in_flight = False

    @staticmethod
    def _turn_in_flight(sess: TrackedSession) -> bool:
        """Whether aipager sees a turn of *sess* in flight (the job's, for
        a parked session): its own view, independent of any timing."""
        return (sess.status in (Status.BUSY, Status.INTERACTIVE)
                or sess.work_in_flight(time.monotonic()))

    @staticmethod
    def _park_settles_in(sess: TrackedSession) -> float:
        """Seconds until *sess*'s park is ``PARK_SETTLE_SECONDS`` old; 0
        when it is (or its time is not known).

        The age is the persisted wall-clock ``parked_since`` (it survives a
        daemon restart). When the wall clock went back since the park, the
        age comes from the monotonic clock instead (``park_changed_at``,
        for a park noticed by this daemon), or the park counts as just
        noticed: never as settled at once."""
        from aipager import claude_jobs
        if sess.parked_since <= 0:
            return 0.0
        age = time.time() - sess.parked_since
        if age < 0:
            age = (time.monotonic() - sess.park_changed_at
                   if sess.park_changed_at > 0 else 0.0)
        if 0 <= age < claude_jobs.PARK_SETTLE_SECONDS:
            return claude_jobs.PARK_SETTLE_SECONDS - age
        return 0.0

    async def _bring_back_in_flight(self, sess: TrackedSession, *, force: bool,
                                    release_held: bool, mode: str,
                                    settled: str = "") -> RestartOutcome:
        from aipager import claude_jobs
        label = sess.label
        job = sess.parked_job
        # The wait was for this park only: a park on another job since
        # (brought back and parked again meanwhile) has a window of its own.
        waited_out = bool(settled) and settled == job
        if not force and self._turn_in_flight(sess):
            log.info("[%s] bring-back from job %s refused: a turn is in flight",
                     label, job)
            return RestartOutcome(ok=False, reason="bg_busy", label=label, mode=mode)
        # Claude's files are read off the event loop (claude_jobs.off_loop).
        entries = await claude_jobs.read_registry_async()
        resume_id = await self._bring_back_conversation(sess, job, entries)
        if not resume_id:
            log.info("[%s] bring-back from job %s: no saved conversation", label, job)
            return RestartOutcome(ok=False, reason="bg_no_conversation", label=label,
                                  mode=mode)

        def follow() -> str:
            # Only to a saved conversation: the client's own id was chosen
            # because the job's has none, and a hop to the job's (its
            # holder is alive until the stop) would resume nothing.
            moved = claude_jobs.follow_continued(resume_id, entries)
            if moved != resume_id and claude_jobs.find_transcript(moved) is not None:
                return moved
            return resume_id

        resume_id = await claude_jobs.off_loop(follow)
        state = await claude_jobs.job_state(job, entries)
        if not force:
            refused = self._bg_unforced_refusal(sess, job, state, settled=waited_out,
                                                mode=mode)
            if refused is not None:
                return refused
            # Right before the stop (job_state may have waited for `claude
            # agents`): the job's entry again, and the stricter answer.
            again = claude_jobs.job_entry(job, await claude_jobs.read_registry_async())
            if again is not None:
                state = claude_jobs.stricter_state(
                    state, claude_jobs.entry_state(again))
            refused = self._bg_unforced_refusal(sess, job, state, settled=waited_out,
                                                mode=mode)
            if refused is not None:
                return refused
        # B3: from here on the job's SessionEnd and the client's exit are
        # expected. Covers the stop and the wait too.
        earlier_window = sess.restarting_until
        sess.restarting_until = (time.monotonic() + _PERMS_RESTART_QUIET
                                 + claude_jobs.STOP_TIMEOUT + claude_jobs.HOLDER_WAIT)
        if state != claude_jobs.GONE:
            err = await self._stop_bg_job(
                job, [resume_id, sess.parked_job_session_id])
            if err:
                sess.restarting_until = earlier_window
                return self._bg_refused_restart(sess, ("bg_stop_failed", err), mode)
        armed: list[tuple] = []

        def arm() -> None:
            # The old client is gone and the new claude not launched yet:
            # its own hooks record their pid afresh, and its first
            # SessionStart finds the release armed (whatever its id).
            sess.claude_pid = 0
            sess.claude_pid_start = 0
            if release_held:
                armed.append(sess.arm_bg_release())

        def disarm() -> None:
            # Only this run's arming is undone: an earlier release still
            # pending (a re-park within its backstop) stays pending.
            if armed:
                sess.restore_bg_release(armed.pop())

        try:
            outcome = await self._kill_and_relaunch_in_flight(
                sess, target_mode=mode, interrupt_first=False, resume_id=resume_id,
                before_launch=arm)
        except BaseException:
            disarm()
            raise
        if not outcome.ok:
            disarm()
            sess.restarting_until = earlier_window
            return outcome
        self._clear_park(sess, resume_id, job)
        path = await claude_jobs.off_loop(claude_jobs.find_transcript, resume_id)
        if path is not None:
            sess.transcript_path = str(path)
        if release_held:
            sess.bg_released_at = time.monotonic()
        log.info("[%s] brought back from background job %s (conversation %s, forced=%s)",
                 label, job, resume_id, force)
        await self._say_brought_back(sess)
        if release_held and sess.bg_start_seen:
            # The relaunched claude said it was up while this still ran.
            await self._release_held(sess)
        return outcome

    def _bg_unforced_refusal(self, sess: TrackedSession, job: str, state: str, *,
                             settled: bool, mode: str) -> RestartOutcome | None:
        """Why an unforced bring-back must not stop job *job* (in *state*)
        now, as a ``bg_busy`` outcome; None when it may. S5: a turn in
        flight, a busy job and an unknown state all ask; a park younger
        than ``PARK_SETTLE_SECONDS`` asks too, unless the caller waited it
        out (*settled*), with ``retry_after`` when that is the only
        reason."""
        from aipager import claude_jobs
        label = sess.label
        if self._turn_in_flight(sess):
            log.info("[%s] bring-back from job %s refused: a turn is in flight", label, job)
            return RestartOutcome(ok=False, reason="bg_busy", label=label, mode=mode)
        if state in (claude_jobs.BUSY, claude_jobs.UNKNOWN):
            log.info("[%s] bring-back from job %s refused: the job is %s", label, job, state)
            return RestartOutcome(ok=False, reason="bg_busy", label=label, mode=mode)
        wait = 0.0 if settled else self._park_settles_in(sess)
        if wait > 0:
            log.info("[%s] bring-back from job %s refused: parked %.1fs ago", label,
                     job, claude_jobs.PARK_SETTLE_SECONDS - wait)
            return RestartOutcome(ok=False, reason="bg_busy", label=label, mode=mode,
                                  retry_after=wait)
        return None

    async def _say_brought_back(self, sess: TrackedSession) -> None:
        """``bg.back`` in the session's chat. Best-effort."""
        if not self._app:
            return
        from aipager.bot.transport import resolve_chat_id
        try:
            await send_text(self._app.bot, chat_id=resolve_chat_id(sess),
                            text=i18n.for_sess(sess).html(BG_BACK, session=sess.label),
                            parse_mode="HTML")
        except Exception:
            log.debug("[%s] brought-back notice failed", sess.label, exc_info=True)

    async def _perms_switch_core(
        self, sess: TrackedSession, target_mode: str, *, force: bool = False,
    ) -> RestartOutcome:
        """Switch an already-resolved, live session to *target_mode* by a
        restart (into or out of Full access; see :meth:`_mode_switch_core`,
        the dispatcher every surface calls).

        ``interrupt_first`` mirrors chat's own ``/mode`` branching
        exactly (``handlers._perms_flow`` /
        ``callbacks._handle_callback``'s ``perms_stop_switch``): BUSY
        gets a courtesy Ctrl-C first — one more chance to finish
        cleanly before the hard kill; anything else — including
        INTERACTIVE, which chat's own command folds into the same IDLE
        flow — goes straight to :meth:`_kill_and_relaunch_core`'s hard
        kill. So the outcome for a given starting status is identical
        to what ``/mode`` would already produce for it.

        *force*: a busy Claude Code background job in the way is stopped
        (the Mini App's "Stop and bring back" after ``bg_busy``).
        """
        interrupt_first = sess.status == Status.BUSY
        return await self._kill_and_relaunch_core(
            sess, target_mode=target_mode,
            interrupt_first=interrupt_first,
            **({"force": True} if force else {}),
        )

    async def _restart_session_core(self, sess: TrackedSession, *,
                                    force: bool = False) -> RestartOutcome:
        """Kill and relaunch a live session with its OWN current
        ``perm_mode`` unchanged — a restart is not a mode switch.

        Always a hard kill, regardless of status: deliberately does
        NOT reuse perms' busy-only Ctrl-C-first courtesy, which exists
        to give a MODE SWITCH one soft chance. A user-invoked restart
        means "stop it and bring it back" — the same hard-kill
        semantics Kill already applies.

        A session parked on a Claude Code background job is brought back
        (:meth:`_bring_back_core`); *force* also stops a busy job (the busy
        question's "Stop and bring back").
        """
        if force:
            return await self._kill_and_relaunch_core(
                sess, target_mode=sess.perm_mode, interrupt_first=False, force=True,
            )
        return await self._kill_and_relaunch_core(
            sess, target_mode=sess.perm_mode, interrupt_first=False,
        )

    def _already_in_mode(self, sess: TrackedSession, target: str) -> bool:
        """True when *sess* is already in *target* with no switch waiting:
        the "already in" answer every surface gives. A target narrower
        than the mode aipager would launch it in (``perm_mode``) is then
        recorded, so a later restart or resume launches what was asked
        for and never the wider mode: someone in the terminal may have
        switched Auto to Manual, and "already in Ask mode" must hold
        after a restart too. Never widens ``perm_mode``.

        Never for a narrower target in a session launched in Full access:
        that process keeps bypass in its Shift+Tab cycle, so it is
        restarted into the target instead (the relaunch drops the bypass
        flag); recording the narrower mode in place would let a later
        in-place switch cycle it back into bypass."""
        if sess.mode_switch is not None or sess.current_mode() != target:
            return False
        if sess.perm_mode == perm_modes.FULL and target != perm_modes.FULL:
            return False
        if perm_modes.wider(sess.perm_mode, target):
            sess.perm_mode = target
            self.registry.mark_dirty()
        return True

    async def _mode_switch_core(self, sess: TrackedSession, target: str, *,
                                force: bool = False) -> RestartOutcome:
        """Switch an already-resolved session to *target* (``ask``,
        ``auto`` or ``full``): the one dispatcher chat and the Mini App
        call. The caller has applied the admin rule for Full access.

        Into or out of Full access (or from a mode Shift+Tab cannot cycle
        from) is a restart through :meth:`_perms_switch_core`. Ask <->
        Auto is in place: Shift+Tab pressed in the terminal, counted from
        the session's current mode, refused while a dialog or question is
        open (the presses would answer it), fine while a turn runs. Its
        outcome is ``"sent"``: the next main-agent hook confirms it
        (aipager/bot/mode_switch.py).

        Reasons besides the restart ones: ``"not_live"``, ``"starting"``,
        ``"already_restarting"``, ``"already"``, ``"dialog_open"``,
        ``"send_failed"``, ``"sent"``."""
        label = sess.label
        if not perm_modes.is_mode(target):
            raise ValueError(target)
        if sess.status == Status.GONE:
            return RestartOutcome(ok=False, reason="not_live", label=label)
        if sess.status == Status.UNKNOWN:
            return RestartOutcome(ok=False, reason="starting", label=label)
        if sess.relaunch_in_flight:
            return RestartOutcome(ok=False, reason="already_restarting", label=label)
        if self._already_in_mode(sess, target):
            return RestartOutcome(ok=True, reason="already", label=label, mode=target)
        if sess.mode_switch_restarts(target):
            # *force*: only a restart has a background job to stop.
            if force:
                return await self._perms_switch_core(sess, target, force=True)
            return await self._perms_switch_core(sess, target)
        if self._parked(sess):
            # Shift+Tab would go to a client parked on a background job.
            return RestartOutcome(ok=False, reason="parked", label=label,
                                  mode=target, in_place=True)
        if sess.dialog_is_open():
            return RestartOutcome(ok=False, reason="dialog_open", label=label,
                                  mode=target, in_place=True)
        from aipager.state import ModeSwitch
        count = perm_modes.presses(sess.mode_raw(), target,
                                   auto_in_cycle=not sess.auto_unavailable)
        previous = (sess.perm_mode, sess.reported_mode, sess.mode_switch)
        # Recorded BEFORE the write: a hook racing the presses is
        # "settling", never read as their result.
        record = ModeSwitch(target=target, presses=count or 0, sent_at=time.monotonic())
        sess.mode_switch = record
        sess.reported_mode = ""
        sess.perm_mode = target
        self.registry.mark_dirty()
        if not await inject.press_mode_cycle(sess.name, count or 0):
            # Only this call's own switch: a relaunch or another switch
            # that started while the presses waited for the write lock
            # has set state that is newer than *previous*.
            if sess.mode_switch is record:
                sess.perm_mode, sess.reported_mode, sess.mode_switch = previous
                self.registry.mark_dirty()
            return RestartOutcome(ok=False, reason="send_failed", label=label,
                                  mode=target, in_place=True)
        # The presses may have waited for the send-now chord or a prompt
        # write: the settle window runs from the last key, not the stamp.
        record.sent_at = time.monotonic()
        log.info("[%s] mode switch to %s: %d Shift+Tab press(es) sent",
                 label, target, count or 0)
        return RestartOutcome(ok=True, reason="sent", label=label, mode=target,
                              in_place=True)

    @staticmethod
    def _queued_candidates(sess: TrackedSession) -> list[dict]:
        """The queued targets a teardown may wipe from Claude's queue,
        before the transcript's evidence (:func:`held_by_claude`) is
        asked. None in a background job's waiting window: the targets are
        kept there on purpose while Claude Code may already have started
        the next prompt (see ``_settle_queued_targets``)."""
        if sess.job_background_open():
            return []
        return list(sess.queued_targets)

    async def _clear_queue_core(self, sess: TrackedSession) -> ClearQueueOutcome:
        """Discard every prompt queued behind the session's in-progress
        turn AND everything Claude itself is holding but hasn't
        confirmed picking up yet. The turn itself is untouched — only
        Stop interrupts it.

        Refuses immediately, with no side effects, when there is
        nothing to clear — the same "no-op refusal" shape
        :meth:`_stop_session_core` already uses, so the refusal is
        pytest-testable with zero dtach mocking. The shared seam both
        chat's ``/clearqueue`` and the Mini App's clearqueue route call
        (design.md "Stop and /clearqueue on the same primitive") — ONE
        call to :func:`inject.discard_queued_input`, never preceded by
        the interrupt pair :meth:`_stop_session_core` sends, so this can
        never cancel the running turn. Sends no keys at all unless the
        transcript shows Claude holding a queued message
        (:func:`held_by_claude`, roadmap 8.37): clearing only
        ``pending_queue`` (never-injected, aipager-side-only holds)
        touches nothing in the pty, and neither does a note left behind
        by a command that already ran.
        """
        from aipager.policy_snapshot import clear_notes_dir, list_outstanding_notes

        if self._parked(sess):
            # Parked on a Claude Code background job: what waits is held
            # here, never typed, so nothing is wiped in the terminal.
            dropped = len(sess.pending_queue)
            if not dropped:
                return ClearQueueOutcome(ok=False, label=sess.label, dropped=0)
            await self._mark_not_delivered(
                sess, reactions.held_entries(sess.pending_queue))
            sess.pending_queue.clear()
            await self._drop_dialog_hold(sess)
            self.registry.mark_dirty()
            log.info("[%s] queue cleared while parked (dropped %d)",
                     sess.label, dropped)
            return ClearQueueOutcome(ok=True, label=sess.label, dropped=dropped)
        # Settle first: an absorption no tick has read yet is 👍, not 🤷.
        await self._settle_queued_targets(sess)
        notes = list_outstanding_notes(sess.name)
        # What Claude itself still holds - a message queued mid-turn (its
        # note went at the submit-time pick-up; it is a queued target) or
        # a command queued behind the turn (still a note) - counts only
        # with the transcript's evidence (roadmap 8.37): the wipe below
        # types Escape, and an Escape into Claude Code's empty queue
        # interrupts the running turn.
        held = held_by_claude(sess, notes + self._queued_candidates(sess))
        if held:
            # The wipe below also clears the input box: a message held back
            # there (roadmap 8.124) goes with the queue, never sent after.
            # With nothing queued it is left to its own second Enter (an
            # Escape into an empty queue would interrupt the turn).
            held += [n for n in _drop_held_back(sess, notes) if n not in held]
        dropped = len(sess.pending_queue) + len(held)
        if not dropped:
            return ClearQueueOutcome(ok=False, label=sess.label, dropped=0)
        await self._mark_not_delivered(
            sess, reactions.held_entries(sess.pending_queue) + held)
        sess.pending_queue.clear()
        # Its "answer it first" notices promised a send that will not come.
        await self._drop_dialog_hold(sess)
        clear_notes_dir(sess.name)
        if held:
            await inject.discard_queued_input(sess.name, queued_chars(sess, held))
        self._discard_queued_targets(sess)
        self.registry.mark_dirty()
        log.info("[%s] queue cleared (dropped %d)", sess.label, dropped)
        return ClearQueueOutcome(ok=True, label=sess.label, dropped=dropped)

    def _mark_injected_command_ran(self, sess: TrackedSession, command: str) -> None:
        """A local command aipager typed into an idle session ran at once:
        its note is no queued message, so let it expire (roadmap 8.37), as
        the typed-command path does."""
        try:
            from aipager.policy_snapshot import (
                list_outstanding_notes,
                mark_command_notes_ran,
            )
            mark_command_notes_ran(sess.name, [
                n for n in list_outstanding_notes(sess.name)
                if str(n.get("raw_text") or "").strip() == command])
        except Exception:
            log.debug("[%s] could not mark %s ran", sess.label, command,
                      exc_info=True)

    async def _compact_session_core(self, sess: TrackedSession,
                                    driver_user_id: int | None = None) -> CompactOutcome:
        """Trigger ``/compact``, treated exactly like a normal queued
        user prompt — NOT the unconditional inject chat's reactive
        "Compact Now" button uses.

        Deliberate departure from chat's own compact button (design.md):
        that button is only ever offered reactively, from a
        ``context_warning`` hook event, so it never had to consider an
        already-full queue. This route is offered proactively at any
        time, so it needs the same overflow protection a normal queued
        message already gets.
        """
        label = sess.label
        if sess.status not in (Status.BUSY, Status.IDLE):
            return CompactOutcome(ok=False, reason="not_live", label=label)
        if self._parked(sess):
            return CompactOutcome(ok=False, reason="parked", label=label)
        checking = sess.model_hold_active()
        if sess.status == Status.BUSY or checking:
            # Behind a running turn, or a typed /model still checking its
            # model (text typed then is lost): sent on with the others.
            if not sess.queue_prompt("/compact", None,
                                     driver_user_id=driver_user_id):
                return CompactOutcome(ok=False, reason="queue_full", label=label)
            if checking:
                sess.model_hold = True
            self.registry.mark_dirty()
            return CompactOutcome(ok=True, reason="queued", label=label)
        # The caller is the sender (C13), as for every other message.
        ok = await self._inject_prompt(sess, "/compact",
                                       driver_user_id=driver_user_id)
        if not ok:
            return CompactOutcome(ok=False, reason="send_failed", label=label)
        self._mark_injected_command_ran(sess, "/compact")
        return CompactOutcome(ok=True, reason="sent", label=label)

    async def _switch_model_core(
        self, sess: TrackedSession, model: str, *,
        driver_user_id: int | None = None,
    ) -> ModelSwitchOutcome:
        """Type exactly ``/model <model>`` into a running session.

        ``model`` must already be validated (``launch.validate_model``).
        Nothing is ever typed after it. The "Switch model?" confirmation
        is pre-approved through the PreModelSwitch hook marker written by
        :func:`mark_model_switch_sent`, not by keystrokes; where that does
        not apply (older Claude Code), the dialog is the operator's to
        answer in the terminal.
        """
        label = sess.label
        if self._parked(sess):
            return ModelSwitchOutcome(
                ok=False, reason="parked", label=label,
                detail=i18n.get("en").text(BG_TOAST_REFUSED_PARKED, session=label),
            )
        refusal = model_switch_refusal(sess)
        if refusal is not None:
            return ModelSwitchOutcome(
                ok=False, reason=refusal[0], label=label, detail=refusal[1],
            )
        if not await inject.is_alive(sess.name):
            from aipager.miniapp.sessions import MODEL_SWITCH_NOT_LIVE_REASON
            return ModelSwitchOutcome(
                ok=False, reason="not_live", label=label,
                detail=MODEL_SWITCH_NOT_LIVE_REASON,
            )
        # Again, after the await above: a chat message can have started a
        # turn while the liveness probe ran.
        refusal = model_switch_refusal(sess)
        if refusal is not None:
            return ModelSwitchOutcome(
                ok=False, reason=refusal[0], label=label, detail=refusal[1],
            )
        previous = mark_model_switch_sent(sess, model)
        ok = await self._inject_prompt(
            sess, f"/model {model}", driver_user_id=driver_user_id,
        )
        if not ok:
            clear_model_switch_pending(sess)
            return ModelSwitchOutcome(ok=False, reason="send_failed", label=label)
        # A later relaunch (/perms, restart) keeps this model.
        sess.launch_model = model
        self.registry.mark_dirty()
        log.info("[%s] model switch sent: /model %s", label, model)
        return ModelSwitchOutcome(
            ok=True, reason="sent", label=label, previous_model=previous,
        )

    async def _rename_session_core(
        self, sess: TrackedSession, new_label: str,
    ) -> RenameOutcome:
        """Pure metadata mutation — ``sess.label`` only, never the
        internal dtach session name, socket, resume id or transcript
        path. Syntax validation and the collision check both already
        happened in server.py before this is ever called, so this core
        has no ``ok=False`` path at all.

        No-op short-circuit when the new label equals the current one:
        no mutation, no ``mark_dirty``, no bot-commands refresh — an
        idempotent no-op, not a silent redundant write.
        """
        previous = sess.label
        if new_label == previous:
            return RenameOutcome(
                ok=True, previous_label=previous, new_label=new_label,
                changed=False,
            )
        sess.label = new_label
        self.registry.mark_dirty()
        # Refresh Telegram's own `/`-command autocomplete list — the
        # "command list staleness" landmine (research.md gotcha #4).
        # Without this, `/oldlabel` keeps appearing in the client's
        # autocomplete (it now resolves to nothing, since find_by_label
        # matches on the live label, which just changed) and `/newlabel`
        # never appears until something else happens to trigger a sync.
        _spawn(self._update_bot_commands())
        log.info("[%s] renamed to %s from Mini App", previous, new_label)
        return RenameOutcome(
            ok=True, previous_label=previous, new_label=new_label, changed=True,
        )
