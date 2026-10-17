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
import time
from types import SimpleNamespace
# Path itself is no longer dereferenced in this module (the poll loop
# that used it moved to session_ops.py's _kill_and_relaunch_core — see
# design.md ORCHESTRATOR OVERRIDE) but the name is kept importable here
# on purpose: tests/test_bot_callbacks_perms.py and
# tests/integration/perms_mode_ux/test_perms_command.py patch
# "aipager.bot.callbacks.Path", and removing the import would turn a
# harmless no-op patch into an AttributeError on every one of them.
from pathlib import Path  # noqa: F401
from typing import TYPE_CHECKING

from telegram import (
    Update,
)
from telegram.ext import (
    ContextTypes,
)

from aipager.bot.mode_labels import mode_label
from aipager.bot.sibling_text import shown
from aipager.bot import (
    appearance,
    card_owner,
    card_rows,
    held_message,
    keyboards,
    language_menu,
    language_offer,
    new_flow,
    report_flow,
    session_parity,
    settings_menu,
    tap_gate,
    update_flow,
)
from aipager.dtach import hook_reply, inject
from aipager.report import store as report_store

from aipager import i18n, perm_modes, preferences
from aipager.i18n import Command, Msg, Plural, Safe, Span
from aipager.i18n.locales import is_selectable
from aipager.bot.settings_menu import (
    SECTIONS as SETTINGS_SECTIONS,
    render_settings_root,
    render_settings_section,
)
from aipager.state import Status, session_foreign_to, warn_unstamped
from aipager.team import (
    attribution_label,
)

# Pure-function helpers and constants live in aipager.bot.transport
# now. Re-export the names this module uses internally so the
# TelegramBot class body below (and any external consumers like the
# tests) keeps working without changes.
from aipager.bot.dashboard import ALREADY_ANSWERED, PINNED_ANSWER_VERB, open_prompt_tokens
from aipager.bot.flood import MUTE, clear_time
from aipager.bot.transport import (  # noqa: F401
    _message_chat_id,
    resolve_chat_id,
    edit_message,
    MUTED,
    edit_markup,
    edit_text,
    send_text,
    ACTION_VERBS,
    TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES,
    TELEGRAM_MAX_DOC_BYTES,
    TELEGRAM_MAX_TEXT_LEN,
    TruncationFailed,
    _build_diff_block,
    calling_chat_id,
    calling_user_id,
    driver_id_from_update,
    mixed_sender_note_outstanding,
    NEEDS_ADMIN_REPLY,
    PROMPT_REFUSED,
    RETRY_OTHERS_COMMAND_REPLY,
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
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Seam: tests replace THIS, never ``asyncio.sleep`` (``aipager.bot.callbacks.
# asyncio`` IS the global module; patching it has hung the suite twice,
# CLAUDE.md). Defaults to the real function.
_sleep = asyncio.sleep

#: Seconds a tap waits for its handler to answer (with a toast) before the
#: dispatcher sends the empty ack that stops the button's spinner.
CALLBACK_ACK_BOUND = 1.0


# The mode card's notes (HTML, the chat's language): what a switch did.
MODE_REFUSED_RESTARTING = Msg(
    "modecard.refused_restarting",
    "⚠️ <b>{session}</b> is restarting right now - mode not changed. Try again in a moment.",
    note="HTML. A mode switch refused: the session is restarting", eg={"session": "x1"})
MODE_REFUSED_NOT_RUNNING = Msg(
    "modecard.refused_not_running", "⚠️ <b>{session}</b> is not running - mode not changed.",
    note="HTML. A mode switch refused: the session is not running", eg={"session": "x1"})
MODE_MOVED_ON = Msg("modecard.moved_on",
                    "⚠️ <b>{session}</b> moved on to new work, so nothing changed.",
                    note="HTML. A mode card tapped after a new turn began", eg={"session": "x1"})
MODE_SWITCHING = Msg("modecard.switching",
                     "⚙️ Switching <b>{session}</b> to {mode} (the session restarts)…",
                     note="HTML. A mode switch that restarts the session is under way; {mode} "
                          "is the mode's label, which already says mode",
                     eg={"session": "x1", "mode": "Ask mode"})
MODE_SWITCHED_BY = Msg("modecard.switched_by", "⚙️ Switched to {mode} by {person}.",
                       note="HTML. Above the mode card in a group: who switched it",
                       eg={"mode": "Ask mode", "person": "@bob"})
MODE_STILL_STOPPING = Msg("modecard.still_stopping",
                          "⚠️ <b>{session}</b> is still stopping - mode not changed.",
                          note="HTML. The session did not stop in time for the switch",
                          eg={"session": "x1"})
MODE_SWITCH_FAILED = Msg("modecard.switch_failed", "❌ Couldn't switch mode: {error}",
                         note="HTML. The switch failed; {error} is why (often English)",
                         eg={"error": "dtach not installed"})
MODE_STALE = Msg("modecard.stale", "⚠️ That card was out of date, so nothing changed.",
                 note="HTML. A mode confirm whose record is gone")
MODE_SESSION_MISSING = Msg("modecard.session_missing", "⚠️ Session not found.",
                           note="HTML. The mode card's session no longer exists")
CANCELLED = Msg("sess.cancelled", "↩️ Cancelled.", note="A card cancelled")
TOAST_KILL_STALE = Msg("toast.kill_stale", "This button is out of date - send /kill again",
                       note="Pop-up: an old /kill button")
KILL_CANCELLED = Msg("sess.kill_cancelled", "↩️ Cancelled. Nothing was ended.",
                     note="The End confirm, cancelled")
# Retry, Compact and /status's Clear all (pop-ups: the tapper's; a note
# on the card: the chat's).
TOAST_RETRY_NOTHING = Msg("toast.retry_nothing", "Nothing to retry",
                          note="Pop-up: Retry with no message to send again")
TOAST_RETRY_NOT_ALIVE = Msg("toast.retry_not_alive", "Session '{session}' not alive",
                            note="Pop-up: Retry while the session's process is gone; {session} "
                                 "is its internal name", eg={"session": "claude-x1"})
TOAST_RETRY_OTHERS_TURN = Msg("toast.retry_others_turn",
                              "Another person's turn is running - retry when it ends",
                              note="Pop-up: Retry refused while someone else's turn runs")
TOAST_RETRY_PROMPT_OPEN = Msg("toast.retry_prompt_open",
                              "Answer the open prompt first, then retry",
                              note="Pop-up: Retry refused while a prompt waits")
TOAST_RETRY_RETRYING = Msg("toast.retry_retrying", "Retrying [{session}]",
                           note="Pop-up: the message was sent again", eg={"session": "x1"})
TOAST_RETRY_FAILED = Msg("toast.retry_failed", "Failed to retry",
                         note="Pop-up: the message could not be sent again")
RETRY_NOTE_OTHERS = Msg("route.retry_note_others",
                        "⏸ Not retried - another person's turn is running. Tap Retry again when "
                        "it ends.", note="Added under the error card: Retry refused; Retry is "
                                         "the button (perm.btn.retry)")
RETRY_NOTE_PROMPT = Msg("route.retry_note_prompt",
                        "⏸ Not retried - a prompt is open. Answer it, then tap Retry again.",
                        note="Added under the error card: Retry refused; Retry is the button "
                             "(perm.btn.retry)")
TOAST_COMPACT_SENDING = Msg("toast.compact_sending", "Compacting [{session}]",
                            note="Pop-up: /compact was sent", eg={"session": "x1"})
TOAST_COMPACT_FAILED = Msg("toast.compact_failed", "Failed to send /compact",
                           note="Pop-up: /compact could not be sent")
TOAST_CLEAR_DONE = Plural("toast.clear_done",
                          one="Cleared {n} from /status (still in /resume)",
                          other="Cleared {n} from /status (still in /resume)",
                          note="Pop-up: {n} ended sessions hidden from /status; /resume still "
                               "lists them")
TOAST_CLEAR_NOTHING = Msg("toast.clear_nothing", "Nothing to clear",
                          note="Pop-up: no ended session to hide")
UNKNOWN_ACTION = Msg("toast.unknown_action", "Unknown: {action}",
                     note="Pop-up: a button aipager does not know; {action} is its code",
                     eg={"action": "zzz"})
PICK_CANCELLED = Msg("sess.pick_cancelled", "Cancelled.",
                     note="A list of sessions to pick from, cancelled")
# /new's conflict card taps (edits: the chat's; pop-ups: the tapper's).
TOAST_NEW_ONLY_SENDER = Msg("toast.new_only_sender",
                            "Only the person who sent /new can use this card.",
                            note="Pop-up: a tap on someone else's /new card")
TOAST_NEW_CANT_START_RESUME = Msg("toast.new_cant_start_resume",
                                  "You can't start or resume sessions here.",
                                  note="Pop-up: the person may not start sessions here")
NEW_CANCELLED_NOTHING = Msg("newflow.cancelled_nothing", "↩️ Cancelled - no session changed.",
                            note="The conflict card, cancelled")
NEW_SWITCHED_TO = Msg("newflow.switched_to", "↩️ Switched to <b>{session}</b>",
                      note="HTML. The conflict card: messages now go to the running session",
                      eg={"session": "x1"})
NEW_PROMPT_QUEUED = Msg("newflow.prompt_queued", "📝 Prompt queued",
                        note="Under newflow.switched_to: the first message waits for it")
TOAST_NEW_EXPIRED = Msg("toast.new_expired", "This card has expired. Send /new again.",
                        note="Pop-up: the conflict card's record is gone")
TOAST_NEW_STARTED_SINCE = Msg("toast.new_started_since",
                              "{session} started working since. Send /new again to replace "
                              "it.", note="Pop-up: Replace refused for work the card never "
                                          "showed", eg={"session": "x1"})
NEW_STARTING_FRESH = Msg("newflow.starting_fresh", "🚀 Starting <b>{session}</b> (fresh)…",
                         note="HTML. Replace: a fresh session starts with the name",
                         eg={"session": "x1"})
NEW_CREATE_FAILED = Msg("newflow.create_failed", "❌ {error}",
                        note="Starting the session failed; {error} is why (English)",
                        eg={"error": "a session named x1 already exists"})
VOICE_NOT_INSTALLED_OK = Msg("media.voice_not_installed_ok", "↩️ OK, voice not installed.",
                             note="The voice extra's offer, declined")
TOAST_PERMS_RESTARTING = Msg("toast.perms_restarting",
                             "{session} is restarting right now - tap again in a moment",
                             note="Pop-up: a switch or restart is already running",
                             eg={"session": "x1"})
TOAST_PERMS_STALE = Msg("toast.perms_stale", "This card is out of date - nothing changed",
                        note="Pop-up: the mode confirm's record is gone")


def _switch_refused(outcome, tr=None) -> str:
    """Why a mode switch did not happen, for the reasons left after
    still_stopping and launch_failed, which say their own (in *tr*'s
    language, English when None)."""
    tr = tr or i18n.get("en")
    if outcome.reason == "already_restarting":
        return tr.html(MODE_REFUSED_RESTARTING, session=outcome.label)
    return tr.html(MODE_REFUSED_NOT_RUNNING, session=outcome.label)


STOPPED = Msg("ans.stopped", "⏹ Stopped {session}",
              note="After a Stop: the session's turn was stopped", eg={"session": "x1"})
STOPPED_BY = Msg("ans.stopped_by", "⏹ Stopped {session} by {person}",
                 note="After a Stop in a group: who stopped it; {person} is @username",
                 eg={"session": "x1", "person": "@bob"})
STOPPED_DROPPED = Plural(
    "ans.stopped_dropped", one="⏹ Stopped {session} ({n} queued message discarded)",
    other="⏹ Stopped {session} ({n} queued messages discarded)",
    note="After a Stop: the messages waiting for the turn were thrown away",
    eg={"session": "x1"})
STOPPED_BY_DROPPED = Plural(
    "ans.stopped_by_dropped",
    one="⏹ Stopped {session} by {person} ({n} queued message discarded)",
    other="⏹ Stopped {session} by {person} ({n} queued messages discarded)",
    note="After a Stop in a group: who stopped it, and the waiting messages thrown away",
    eg={"session": "x1", "person": "@bob"})
NOT_FOUND = Msg("toast.session_not_found", "Session not found",
                note="Pop-up: the button's session no longer exists")
SESSION_GONE = Msg("toast.session_gone", "Session '{session}' not found",
                   note="Pop-up after a prompt tap: the session's Claude Code is no longer "
                        "running", eg={"session": "claude-x1"})
SHORT_CB_GONE = Msg("toast.session_unavailable", "That session is no longer available",
                    note="Pop-up: a button whose session has ended or is no longer known "
                         "in this chat")
PROMPT_EXPIRED = Msg("toast.prompt_expired", "this prompt has expired",
                     note="Pop-up: a tap on a prompt message that is no longer the prompt "
                          "Claude Code is waiting on")
#: What Claude reads when aipager refuses a tool through its hook (never
#: translated: tests/test_claude_facing_golden.py pins it).
DENIED_VIA_AIPAGER = "Denied via aipager"

PROMPT_BEHIND = Msg(
    "toast.prompt_behind_other",
    "Claude Code is showing another prompt first, so answer that one before this one.",
    note="Pop-up: two prompts are open and Claude Code shows the other one on top, so "
         "this tap would type into the wrong dialog; nothing was typed")
PROMPT_ORDER_UNKNOWN = Msg(
    "toast.prompt_order_unknown",
    "aipager can't tell which prompt Claude Code is showing first, so answer this one "
    "in the terminal.",
    note="Pop-up: two prompts are open and the order Claude Code shows them in is not "
         "known, so typing could answer the wrong one; nothing was typed")
CANT_STOP = Msg("toast.cant_stop", "You can't stop this session.",
                note="Pop-up: the person may not stop this session")
NOT_HERE = Msg("toast.session_not_here", "That session isn't running here.",
               note="Pop-up: the session belongs to another chat")
ENDED = Msg("toast.session_ended", "That session has ended.", note="Pop-up")
MOVED_ON_STATUS = Msg("toast.moved_on_status",
                      "{session} moved on to new work - tap ⏹ Stop again to stop it",
                      note="Pop-up on /status's Stop: the turn it showed is over and a new "
                           "one runs", eg={"session": "x1"})
MOVED_ON_STOP = Msg("toast.moved_on_stop", "{session} moved on to new work - send /stop again",
                    note="Pop-up on the /stop list: the turn it showed is over",
                    eg={"session": "x1"})
NOT_WORKING = Msg("toast.not_working", "{session} is not working",
                  note="Pop-up: Stop tapped while the session is idle", eg={"session": "x1"})
SEND_KEYS_FAILED = Msg("toast.send_keys_failed", "Failed to send keys",
                       note="Pop-up: the answer could not be typed into Claude Code")
SEND_FAILED = Msg("toast.send_failed", "Failed to send to {session}",
                  note="Pop-up: the answer could not reach the session",
                  eg={"session": "x1"})
ANSWERED_TOAST = Msg("toast.answered", "✅ {verb}",
                     note="Pop-up after a question's answers were sent: how it was answered",
                     eg={"verb": "Selected: Postgres"})
VERB_TOAST = Msg("toast.verb", "{verb} [{session}]",
                 note="Pop-up after a prompt tap: the answer and the session",
                 eg={"verb": "Allowed", "session": "x1"})
ALWAYS_DEGRADED = Msg("toast.always_degraded",
                      "No always-rule for this command - allowed once [{session}]",
                      note="Pop-up after Allow always when Claude Code offered no rule to "
                           "save: the tool was allowed this once", eg={"session": "x1"})
ARROW_VERB = Msg("ans.arrow_verb", "→ {verb}",
                 note="Under an answered prompt message: how it was answered. RTL "
                      "catalogs use ←", eg={"verb": "Allowed"})
ANSWERED = Msg("ans.answered", "✓ <b>{session}</b> · Answered · {person} · {question} → {option}",
               note="In the chat after a question was answered: who, the question, the "
                    "answer. RTL catalogs use ←",
               eg={"session": "x1", "person": "@bob", "question": "Which database?",
                   "option": "Selected: Postgres"})
ANSWERED_ANON = Msg("ans.answered_anon", "✓ <b>{session}</b> · Answered · {question} → {option}",
                    note="After a question was answered (nobody named). RTL catalogs use ←",
                    eg={"session": "x1", "question": "Which database?",
                        "option": "Selected: Postgres"})
ANSWER_LINE = Msg("ans.line", "{icon} <b>{session}</b> · {verb} · {person} · {detail}",
                  note="In the chat after a prompt was answered: how, by whom, and the "
                       "tool call or question",
                  eg={"icon": "✅", "session": "x1", "verb": "Allowed", "person": "@bob",
                      "detail": "Bash: ls"})
ANSWER_LINE_ANON = Msg("ans.line_anon", "{icon} <b>{session}</b> · {verb} · {detail}",
                       note="After a prompt was answered (nobody named)",
                       eg={"icon": "✅", "session": "x1", "verb": "Allowed",
                           "detail": "Bash: ls"})

RATE_LIMITED = Msg("toast.rate_limited",
                   "Telegram is rate-limiting this chat - try again after {time}",
                   note="Pop-up on Retry while Telegram limits this chat; {time} is a clock "
                        "time", eg={"time": "14:05"})

#: The shown answer verb by the action id; the audit log keeps the English
#: of ``transport.ACTION_VERBS``.
ANSWER_VERBS = card_rows.ANSWER_VERBS


def _stopped_line(outcome, *, html: bool = True, tr=None) -> str:
    """`⏹ Stopped x1`, with the queued messages it dropped: what every
    Stop says (a toast is plain text, a message HTML), in *tr*'s language
    (English when None). In a group, who stopped it (`outcome.by`,
    roadmap 8.91c): `⏹ Stopped x1 by @bob`."""
    tr = tr or i18n.get("en")
    render = tr.html if html else tr.text
    session = (Span(f"<b>{html_mod.escape(outcome.label)}</b>") if html
               else outcome.label)
    by = getattr(outcome, "by", "")
    params = {"session": session}
    if isinstance(by, str) and by:
        params["person"] = Command(by)
    if outcome.dropped:
        plural = STOPPED_BY_DROPPED if "person" in params else STOPPED_DROPPED
        return tr.n(plural, outcome.dropped, kind="html" if html else "text", **params)
    return render(STOPPED_BY if "person" in params else STOPPED, **params)


class _QueryAck:
    """Whether a callback query has had its one answer yet."""

    __slots__ = ("query", "answered")

    def __init__(self, query) -> None:
        self.query = query          # held so id(query) is not reused
        self.answered = False


#: id(query) → its answer state, for the taps in flight.
_ACKS: dict[int, _QueryAck] = {}


async def _send_empty_answer(query) -> None:
    """The bare empty ``answerCallbackQuery``; the caller has claimed it."""
    try:
        await query.answer()
    except Exception:
        log.debug("answerCallbackQuery failed", exc_info=True)


# Claude Code renders its permission prompt as a cursor menu whose shape
# depends on the tool. Observed on v2.1.220 for a Bash call:
#
#     > 1. Yes
#       2. Yes, and always allow access to <dir>/ from this project
#       3. No
#
# A tool with no directory scope to widen shows only Yes/No, so the index
# of "No" is NOT fixed. The daemon cannot read the labels: the
# PermissionRequest hook payload carries only tool_name/tool_input
# (dtach/hook_receiver.py), never the rendered options. So a deny has to
# be expressed as cursor movement that is correct for every shape.
#
# The invariant that makes that possible: the menu CLAMPS at the last
# item, it does not wrap. Verified by pushing Down five times at a
# three-item prompt — the cursor stopped on "3. No" rather than cycling
# back to "Yes". Overshooting therefore always lands on the last option,
# which is the negative one in every shape observed.
#
# Getting this wrong is not symmetric. Until this constant existed, deny
# sent a single Down and selected "Yes, and always allow" — it ran the
# tool the operator had just refused AND widened permissions for the rest
# of the session, while reporting "Denied" to the user and to the audit
# log. Overshooting can only ever land on a refusal, so it fails safe.
# Claude Code 2.1.259 added "Yes, and switch to auto mode" as the row just
# above "No" on Bash prompts (Yes / [don't ask again] / [switch to auto] /
# No), so the menu can be four rows deep: two Downs from the top would stop
# ON the auto-mode row; five clamp onto "No" whatever the length.
_DENY_OVERSHOOT = 5

# Mirrors aipager.dtach.hook_receiver._STANDING_RULE_SUGGESTION_TYPES —
# deliberately duplicated, not imported, so this decision-building code
# doesn't take on hook_receiver.py's module surface as a dependency (same
# decoupling convention as _TASK_NOTIFICATION_PREFIX elsewhere in this
# codebase). See the "Defense in depth" comment at its one call site
# (allow_always) for why this check exists here too, not only there.
_STANDING_RULE_SUGGESTION_TYPES = frozenset({"addRules", "addDirectories"})

#: The icon of an answered prompt's line, by the EFFECTIVE decision's id
#: (never by the verb shown, which is display text). Anything else (an
#: option, an unknown action) gets "·".
_DECISION_ICONS = {
    "allow": "✅",
    "allow_always": "🟢",
    "deny": "🚫",
    "continue": "▶️",
}

# The verbs (with ``opt<N>``) that answer a prompt by typing into Claude's
# dialog: refused on a surface whose prompt is known closed (roadmap 8.102).
# ``submit`` is a multi-select question's Submit button.
_CARD_ANSWER_VERBS = frozenset({"allow", "allow_always", "deny", "submit"})


def _prompt_for_token(sess, token) -> tuple[str | None, dict | None]:
    """``(where, perm)`` of the open prompt bound to *token* (roadmap
    8.120): ``"inline"`` (the card's, via a bar copy), ``"separate"`` (the
    primary sent as its own message) or ``"extra"``; ``(None, None)`` when
    none is."""
    if token is None:
        return None, None
    perm = sess.pending_permission
    if isinstance(perm, dict) and perm.get("prompt_token") == token:
        return "inline", perm
    sent = sess.pending_prompt_msg
    if isinstance(sent, dict) and sent.get("prompt_token") == token \
            and isinstance(sent.get("perm"), dict):
        return "separate", sent["perm"]
    for rec in sess.extra_prompts:
        if (isinstance(rec, dict) and rec.get("prompt_token") == token
                and isinstance(rec.get("perm"), dict)):
            return "extra", rec["perm"]
    return None, None


_PERMS_POLL_COUNT = 15
_PERMS_POLL_INTERVAL = 0.2
# How long a `/perms` restart stays "expected" for the exit-alarm paths.
# Generous on purpose: the dying session's SessionEnd hook is a separate
# subprocess and can land after the relaunch has already succeeded, and that
# late arrival is precisely what used to be announced as a crash. The window
# expires on its own, so a genuine crash a moment later is still reported.
_PERMS_RESTART_QUIET = 10.0


class CallbackDispatchMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    @staticmethod
    async def _safe_answer(query, text: str | None = None, **kwargs) -> None:
        """Call ``query.answer(text, **kwargs)`` swallowing any "query is
        too old" or "already answered" errors.

        Used for every toast. Telegram refuses a second answer for the
        same query, so inside ``_handle_callback`` only the first call
        answers; a later one is dropped (see ``_QueryAck``). Outside it
        (a query with no ``_ACKS`` entry) every call is sent.
        ``kwargs`` forwards e.g. ``show_alert=True`` for a modal toast.

        Skipped outright while the tapped message's chat is flood-muted
        (8.26 D-1). ``answerCallbackQuery`` carries no ``chat_id``, so the
        limiter's gate structurally cannot see it — this is the one
        chokepoint in this module, and without the check every button tap
        during a ban would be a fresh request into it. The toast is not
        worth extending a 9.5-hour ban for; the tap simply goes
        unacknowledged, exactly as it does while the daemon is busy.
        """
        if MUTE.is_muted(_message_chat_id(getattr(query, "message", None))):
            return
        # One answer per query (Telegram refuses a second). Inside
        # `_handle_callback` the first caller takes it; a later one is
        # dropped here rather than sent to be refused.
        ack = _ACKS.get(id(query))
        if ack is not None:
            if ack.answered:
                if text is not None:
                    log.debug("callback toast dropped, the query was "
                              "already answered: %r", text)
                return
            ack.answered = True
        try:
            await query.answer(text, **kwargs)
        except Exception:
            log.debug("answerCallbackQuery failed", exc_info=True)

    async def _do_perms_switch_via_fn(self, sess, target_mode: str,
                                      edit_fn, *,
                                      tapped_msg_id: int | None = None,
                                      turn: int | None = None,
                                      chat_id=None, announce: bool = True,
                                      by: str = ""):
        """Switch *sess* to *target_mode* (a restart, into or out of Full
        access), showing each step
        through ``edit_fn`` (an async ``(text, **kw)``: the card being
        edited in place) and ending in the /mode card: the new mode and the
        opposite switch, or what went wrong above the current one.

        Thin wrapper around :meth:`SessionOpsMixin._mode_switch_core` —
        the one switch dispatcher chat and the Mini App both go through;
        its restarts take the single kill/poll/relaunch seam (design.md
        ORCHESTRATOR OVERRIDE).

        ``turn``: the turn the card was shown for (its pending record's);
        it decides whether the tap is current. Without one, the tapped
        message id is compared with the busy card's, which an older card
        edited in place would fail. ``announce``: show "Switching…" first
        (the caller may have already). ``by``: who switched it, named
        above the card in a group (roadmap 8.91c; ``""`` in a DM). Returns
        the outcome, or None when the tap was refused as stale.
        """
        # The mode card is the chat's (a stand-in may take this exact call:
        # the language is resolved here).
        tr = i18n.for_chat(chat_id)
        mode = Safe(tr.html(mode_label(target_mode)))
        if not (sess.tap_is_for_this_turn(None, turn=turn) if turn is not None
                else sess.tap_is_for_this_turn(tapped_msg_id)):
            # A card offered while the session was idle can be tapped much
            # later, by which time a different turn is running — and this
            # path kills and relaunches it.
            text, kb = self._mode_card(chat_id, sess, tr.html(MODE_MOVED_ON, session=sess.label))
            await edit_fn(text, parse_mode="HTML", reply_markup=kb)
            return None
        if announce:
            # The buttons go at once: a second tap during the relaunch has
            # nothing left to press.
            await edit_fn(tr.html(MODE_SWITCHING, session=sess.label, mode=mode),
                          parse_mode="HTML")
        outcome = await self._mode_switch_core(sess, target_mode)

        if outcome.reason == "already":
            note = ""
        elif outcome.reason == "sent":
            # The session's mode moved under the card: the switch went in
            # place after all; the next hook confirms it.
            from aipager.bot import mode_switch  # local: import cycle
            note = tr.html(mode_switch.SWITCH_SENT, session=sess.label, mode=mode)
        elif outcome.ok:
            note = (tr.html(MODE_SWITCHED_BY, mode=mode, person=Command(by)) if by else "")
            log.info("[%s] mode switched to %s", sess.label, target_mode)
        elif outcome.reason == "still_stopping":
            note = tr.html(MODE_STILL_STOPPING, session=sess.label)
        elif outcome.reason == "launch_failed":
            note = tr.html(MODE_SWITCH_FAILED, error=outcome.err)
        else:
            # not_live / already_restarting: never silence (a refused
            # switch used to leave its card and buttons as they were).
            note = _switch_refused(outcome, tr)
        text, kb = self._mode_card(chat_id, sess, note)
        await edit_fn(text, parse_mode="HTML", reply_markup=kb)
        return outcome

    # Callback-data value tokens → the field value `preferences.set_preference`
    # expects. Only the boolean sections need translation (bool ↔ on/off);
    # every other field's values already double as their own tokens, so
    # they round-trip through this map unchanged.
    _SETTINGS_VALUE_TOKENS = {
        "formatting": {"on": True, "off": False},
        "diffs": {"on": True, "off": False},
        "cadence": {"on": True, "off": False},
    }

    async def _dispatch_settings_action(self, update: Update, query, action: str) -> None:
        """Route one `_:set...` callback (see settings_menu.py's module
        docstring for the full callback-data contract).

        Navigation (root/section/back/close) is always allowed — viewing
        current values is never gated. A value-set tap
        (`_:set:<section>:<value>`) additionally requires admin in a
        **group** scope (`chat_id < 0`); DM scopes and personal-mode
        installs skip the check entirely, matching `_is_admin`'s own
        semantics there. An unresolvable `chat_id` (`None`) is treated the
        same as a group scope — fail closed rather than silently skip
        the check.
        """
        chat_id = calling_chat_id(update)
        parts = action.split(":")[1:]  # drop the leading "set"

        # The Language row (multilingual design, 7.2): its own branch,
        # before the generic section branches below, which would answer
        # "Invalid callback" for it.
        if parts and parts[0] == "lang":
            await self._dispatch_language_action(update, query, chat_id, parts[1:])
            return
        # The upgrade offer card's buttons (language_offer, sent once to
        # each existing private chat by language_launch).
        if parts and parts[0] == language_offer.SECTION:
            if len(parts) != 2:
                await self._safe_answer(
                    query, i18n.for_person(update).text(language_menu.INVALID_CALLBACK))
                return
            await language_offer.handle_callback(self, update, query, parts[1])
            return

        if not parts or parts == ["back"]:
            text, kb = render_settings_root(
                chat_id or 0, problem_reports=report_flow.settings_row_state(
                    self, chat_id, getattr(getattr(query, "from_user", None), "id", None)),
                language=i18n.for_update(update).code)
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        if parts == ["close"]:
            try:
                await edit_markup(query, reply_markup=None)
            except Exception:
                pass
            return

        # A pop-up is seen only by the person who tapped: their language.
        # The menu itself is the chat's.
        person = i18n.for_person(update)
        shared = i18n.for_update(update)
        if len(parts) == 1:
            section = parts[0]
            rendered = render_settings_section(chat_id or 0, section, language=shared.code)
            if rendered is None:
                await self._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))
                return
            text, kb = rendered
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        if len(parts) == 2:
            section, token = parts
            if section not in SETTINGS_SECTIONS:
                await self._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))
                return

            # Fail closed: an unresolvable chat_id (`calling_chat_id`
            # returning None) is treated the same as a group scope rather
            # than skipped like a DM. `_is_admin` still returns True for a
            # genuinely-ungated case (personal-mode installs are always
            # admin), so this only tightens the check for the team/scope
            # modes where it matters — a permission gate should never
            # degrade to "allow" just because it couldn't identify the
            # chat.
            if (chat_id is None or chat_id < 0) and not self._is_admin(update):
                await self._safe_answer(
                    query, person.text(language_menu.ADMIN_ONLY), show_alert=True)
                return

            # Safe: `section` was checked against SETTINGS_SECTIONS above,
            # and this dict's keys are exactly SETTINGS_SECTIONS's members
            # (both come from settings_menu.SECTIONS), so the lookup below
            # can never raise KeyError.
            field = {
                "layout": "layout", "diffs": "diff_preview",
                "cadence": "card_age_decay",
                "formatting": "simple_formatting",
                "length": "answer_length", "level": "language_level",
            }[section]
            value_map = self._SETTINGS_VALUE_TOKENS.get(section)
            # `.get(token, token)` — an unrecognised token (e.g.
            # "_:set:formatting:sideways") falls through as the raw string
            # rather than raising KeyError here. That lets `set_preference`
            # be the single validation authority: its allow-list check
            # rejects the bogus value with `ValueError`, caught below, the
            # same way every other section's malformed tokens are already
            # handled.
            value = value_map.get(token, token) if value_map is not None else token
            try:
                preferences.set_preference(chat_id or 0, field, value)
            except ValueError:
                await self._safe_answer(query, person.text(language_menu.INVALID_VALUE))
                return

            text, kb = render_settings_section(chat_id or 0, section, language=shared.code)
            text += self._changed_by_line(query, chat_id, shared)
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        await self._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))

    def _changed_by_line(self, query, chat_id, tr=None) -> str:
        """``\\n\\n<i>Changed by @x.</i>`` under a changed setting in a
        group (roadmap 8.91c), in the chat's language *tr*; ``""`` in a
        private chat."""
        tapper = getattr(query, "from_user", None)
        by = self._actor_label(getattr(tapper, "id", None), chat_id, tapper)
        if not by:
            return ""
        tr = tr or i18n.for_chat(chat_id)
        return "\n\n" + tr.html(settings_menu.CHANGED_BY, person=Command(by))

    async def _dispatch_language_action(self, update: Update, query, chat_id,
                                        parts: list[str]) -> None:
        """``_:set:lang`` opens the list (anyone may look);
        ``_:set:lang:<code|auto>`` sets the chat's language.

        The token must be ``auto`` (a private chat only) or a language a
        person may pick, else "Invalid value". In a group (or a chat that
        cannot be identified) only an admin may change it, as every other
        setting. Nothing is written when the value would not change. The
        menu, keyboard and menu button are re-sent only when the language
        in force actually changed."""
        private = isinstance(chat_id, int) and chat_id > 0
        if not parts:
            tr = i18n.for_update(update)
            text, kb = language_menu.render_language_menu(
                tr, private=private,
                current=language_menu.current_choice(chat_id, private=private),
                style=appearance.choice_style(chat_id))
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return
        # A pop-up is seen only by the person who tapped: their language.
        person = i18n.for_person(update)
        if len(parts) != 1:
            await self._safe_answer(query, person.text(language_menu.INVALID_CALLBACK))
            return
        token = parts[0]
        if token == language_menu.AUTO_TOKEN and private:
            code = preferences.UI_LANGUAGE_AUTO
        elif token != language_menu.AUTO_TOKEN and is_selectable(token):
            code = token
        else:
            await self._safe_answer(query, person.text(language_menu.INVALID_VALUE))
            return
        if (chat_id is None or chat_id < 0) and not self._is_admin(update):
            await self._safe_answer(
                query, person.text(language_menu.ADMIN_ONLY), show_alert=True)
            return
        before = i18n.for_chat(chat_id).code
        try:
            preferences.set_ui_language(chat_id, code)
        except ValueError:
            await self._safe_answer(query, person.text(language_menu.INVALID_VALUE))
            return
        tr = i18n.for_chat(chat_id)
        menu_text, kb = language_menu.render_language_menu(
            tr, private=private,
            current=language_menu.current_choice(chat_id, private=private),
            style=appearance.choice_style(chat_id))
        text = f"{language_menu.confirmation(tr, tr.code)}\n\n{menu_text}"
        text += self._changed_by_line(query, chat_id, tr)
        try:
            await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
        if tr.code != before:
            await self._on_language_changed(chat_id)

    async def _stop_the_turn_shown(self, update: Update, query, session_name: str,
                                   turn: int, *, again: str):
        """Stop *session_name* from a button carrying the turn it was shown
        for (/status's Stop, the /stop picker), answering the tap.

        Returns ``(looked, outcome)``: ``looked`` is False when the tap is
        refused outright (no such session, no right to prompt, another
        chat's session); ``outcome`` is None when a new turn started since,
        which is not stopped: the button was for the work it listed."""
        person = i18n.for_person(update)
        sess = self.registry.get(session_name)
        if not sess:
            await self._safe_answer(query, person.text(NOT_FOUND))
            return False, None
        chat = calling_chat_id(update)
        if not self._can_prompt_user(
                getattr(getattr(query, "from_user", None), "id", None), chat):
            await self._safe_answer(query, person.text(CANT_STOP))
            return False, None
        if session_foreign_to(sess, chat):
            await self._safe_answer(query, person.text(NOT_HERE))
            return False, None
        if sess.status == Status.GONE:
            await self._safe_answer(query, person.text(ENDED))
            return True, None
        if not sess.tap_is_for_this_turn(None, turn=turn):
            await self._safe_answer(query, person.text(again, session=sess.label))
            return True, None
        tapper = getattr(query, "from_user", None)
        outcome = await self._stop_session_core(sess, by=self._actor_label(
            getattr(tapper, "id", None), chat, tapper))
        # The count stays visible (tester-iter1-001 of /stop).
        await self._safe_answer(query, _stopped_line(outcome, html=False, tr=person)
                                if outcome.ok
                                else person.text(NOT_WORKING, session=sess.label))
        return True, outcome

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button tap.

        EXACTLY ONE ``answerCallbackQuery`` per tap: Telegram refuses a
        second one. The handler answers first, with its toast, through
        :py:meth:`_safe_answer`. The dispatcher sends the empty ack that
        clears the button's spinner only if nothing has answered yet —
        once the handler returns (or raises), or after
        ``CALLBACK_ACK_BOUND`` seconds, whichever is first, so a slow
        handler never leaves the spinner turning. A toast a handler sends
        after the bound's ack is dropped (logged at debug), not refused.

        Until 8.31 this sent the empty ack BEFORE the handler ran, so every
        toast after it ("Session not found", "Unknown: …", …) was refused
        by Telegram and never shown; the mocks recorded them anyway.
        """
        query = update.callback_query
        # Team mode: reject taps from users not on the allow-list with a
        # toast. Personal mode passes through unchanged (sentinel value).
        member = await self._authorize_callback(query)
        if member is None:
            return
        cb_data = query.data or ""
        original_text = query.message.text or "" if query.message else ""

        _ACKS[id(query)] = _QueryAck(query)
        bound = asyncio.ensure_future(self._ack_after_bound(query))
        try:
            await self._dispatch_callback(
                update, query, member, cb_data, original_text)
        finally:
            # Cancels the bound only while it sleeps: an answer it already
            # started is shielded (see `_ack_after_bound`) and completes.
            bound.cancel()
            try:
                # The empty ack, unless the handler (or the bound) answered.
                await self._safe_answer(query)
            finally:
                # Even if this task is cancelled mid-answer (shutdown).
                _ACKS.pop(id(query), None)

    async def _ack_after_bound(self, query) -> None:
        await _sleep(CALLBACK_ACK_BOUND)
        # Claim the query HERE, synchronously, in the step the bound wakes
        # in: a claim made inside a new (shielded) task would run a loop
        # step later, when the handler may already have answered and
        # dropped the entry — and a missing entry means "send".
        ack = _ACKS.get(id(query))
        if ack is None or ack.answered:
            return
        if MUTE.is_muted(_message_chat_id(getattr(query, "message", None))):
            return
        ack.answered = True
        # Shielded: the handler finishing must not cancel an answer that
        # is already on the wire, or the tap would end with none at all.
        await asyncio.shield(_send_empty_answer(query))

    async def _tap_passes_gate(self, query, member, session_name: str,
                               action: str) -> bool:
        """The one gate every button tap passes (roadmap 8.75 + 8.78).

        Scope mode only; personal mode and legacy team mode are unchanged
        (``_authorize_callback`` already admitted the tapper there).

        1. A tap naming a session that is not one of the tapped chat's
           is refused: a button is only ever acted on in its session's
           own chat. An unstamped session (``scope_chat_id`` 0) is no
           chat's in scope mode, so it is refused too (roadmap 8.72).
        2. The tapper's role in the TAPPED MESSAGE'S chat must grant the
           capability ``tap_gate.required_capability`` names for the verb.
        """
        if self.scopes is None:
            return True
        chat_id = _message_chat_id(getattr(query, "message", None))
        if session_name not in tap_gate.SENTINEL_NAMESPACES:
            sess = self.registry.get(session_name)
            # Scope mode here, so a 0 (unstamped) never equals a chat.
            if sess is not None and sess.scope_chat_id != chat_id:
                if not sess.scope_chat_id:
                    warn_unstamped(sess)
                log.info("tap refused: %s:%s belongs to chat %s, tapped in %s",
                         session_name, action, sess.scope_chat_id, chat_id)
                await self._safe_answer(query, i18n.for_person(
                    SimpleNamespace(callback_query=query)).text(tap_gate.OTHER_CHAT_TEXT))
                return False
        perms_target_full = False
        if action in ("perms_confirm", "perms_stop_switch"):
            # The card's own record, matched exactly as the perms branch
            # matches it below; a switch to Full access needs an admin
            # TAPPING it.
            pending = self._perms_pending.get(session_name)
            tapped = getattr(getattr(query, "message", None), "message_id", None)
            perms_target_full = bool(
                pending is not None
                and pending.get("msg_id") in (None, tapped)
                and pending.get("target_mode") == perm_modes.FULL)
        cap = tap_gate.required_capability(
            session_name, action, perms_target_full=perms_target_full)
        user_id = getattr(getattr(query, "from_user", None), "id", None)
        if self._member_can(member, cap, user_id, chat_id):
            return True
        log.info("tap refused: user %s in chat %s lacks %s for %s:%s",
                 user_id, chat_id, cap.value, session_name, action)
        # An update-level refusal reads as /update's own does: installing
        # the voice extra or updating both need the update admin.
        await self._safe_answer(
            query,
            update_flow.tapper_tr(query).text(update_flow.DENIED_TEXT)
            if cap is tap_gate.UPDATE
            else i18n.for_person(SimpleNamespace(callback_query=query)).text(
                tap_gate.REFUSED_TEXT),
            show_alert=True)
        return False

    async def _dispatch_callback(self, update: Update, query, member,
                                 cb_data: str, original_text: str) -> None:
        """The body of :meth:`_handle_callback`, after authorization."""
        if ":" not in cb_data:
            await self._safe_answer(query, i18n.for_person(update).text(
                language_menu.INVALID_CALLBACK))
            return

        session_name, action = cb_data.split(":", 1)

        # Resolve the indexed short form ONCE, centrally, right here —
        # before new_flow or session_parity see the pair — so every
        # branch below (and inside both of those) keeps operating on a
        # real session name exactly as it did before `_:sx:<idx>:<verb>`
        # existed. Not the short form → pair returned unchanged. Stale
        # or malformed → None, and we stop here rather than falling
        # through to a wrong or dead session.
        #
        # chat_id here is calling_chat_id(update) — the real chat this
        # tap arrived from — while every builder registered its index
        # under resolve_chat_id_int(sess) or 0 instead (a session-
        # derived value, not update-derived). See session_cb's
        # docstring in session_parity.py for why that asymmetry is safe
        # (review-1.md rev-iter1-003).
        resolved = session_parity.resolve_short_cb(
            self, calling_chat_id(update) or 0, session_name, action)
        if resolved is None:
            await self._safe_answer(query, i18n.for_person(update).text(SHORT_CB_GONE))
            return
        session_name, action = resolved

        # Roadmap 8.75 + 8.78: one gate for every tap, before any handler
        # (update_flow, new_flow, session_parity, the branches below).
        if not await self._tap_passes_gate(query, member, session_name, action):
            return

        # Roadmap 8.91c (D-D): in a group, a confirm card (and the picker
        # that leads to one) answers only the person who asked for it.
        toast = card_owner.refusal(
            self, calling_chat_id(update),
            getattr(getattr(query, "message", None), "message_id", None),
            getattr(getattr(query, "from_user", None), "id", None),
            session_name, action, tr=i18n.for_person(update))
        if toast is not None:
            await self._safe_answer(query, toast)
            return

        # All three return False unless the callback belongs to their own
        # namespace, so every pre-existing callback below is unaffected.
        # `_:up:` (self-update) re-checks the admin rule on every tap.
        if await update_flow.handle_callback(self, update, query, session_name, action):
            return
        # `_:rp:` (problem reports) re-checks the owner on every tap.
        if await report_flow.handle_callback(self, update, query, session_name, action):
            return
        if await new_flow.handle_callback(self, update, query, session_name, action):
            return
        # `_:set:ap...`: /settings → Appearance (a change is admin-gated inside).
        if await appearance.handle_callback(self, update, query, session_name, action):
            return
        if await session_parity.handle_callback(self, update, query, session_name, action):
            return

        # The pinned bar's "Answer <label>" (8.31): re-send the session's
        # pending prompt at the bottom of the chat. Same gate as answering
        # the prompt itself (`allow`/`deny`/`opt<N>`): `_authorize_callback`
        # above, then the session resolved from this chat's short-form
        # index (or the long form), exactly as those verbs resolve it.
        if action == PINNED_ANSWER_VERB:
            sess = self.registry.get(session_name)
            if sess is None:
                await self._safe_answer(query, i18n.for_person(update).text(NOT_FOUND))
                return
            await self._pinned_answer(query, sess)
            return

        if action == "stop":
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, i18n.for_person(update).text(NOT_FOUND))
                return
            # Staleness is checked inside `_stop_session` (the seam every
            # caller shares), not here — a Stop button outlives its turn
            # whenever the edit that strips its keyboard fails, and gating
            # per-caller is how the perms and restart paths were missed.
            outcome = await self._stop_session(sess, update=update, query=query)
            if not outcome.ok and outcome.reason != "stale":
                # A stale tap already explained itself at the seam; adding
                # "is not busy" on top would be both wrong and louder.
                await self._safe_answer(
                    query, i18n.for_person(update).text(NOT_WORKING, session=sess.label))
            return

        if action.startswith("ststop") and action[6:].isdigit():
            # /status's ⏹ Stop (4.2): stop the turn the list showed, then
            # redraw the list in place (never overwrite it with one line).
            looked, _outcome = await self._stop_the_turn_shown(
                update, query, session_name, int(action[6:]),
                again=MOVED_ON_STATUS)
            if not looked:
                return
            text, kb = self._render_status_list(calling_chat_id(update), update)
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        if action.startswith("pstop") and action[5:].isdigit():
            # The /stop picker's ⏹ (4.4): stop the turn it listed; the
            # picker becomes the result. A refused tap leaves the picker.
            _looked, outcome = await self._stop_the_turn_shown(
                update, query, session_name, int(action[5:]), again=MOVED_ON_STOP)
            if outcome is not None and outcome.ok:
                # The picker is now the result: nobody's card (8.94j).
                card_owner.release(self, calling_chat_id(update),
                                   getattr(query.message, "message_id", None))
                try:
                    await edit_text(query, _stopped_line(outcome, tr=i18n.for_update(update)),
                                    parse_mode="HTML")
                except Exception:
                    pass
            return

        if action.startswith("now:"):
            # The "⚡ Send now" button under a queued message
            # (bot/send_now.py). The message's id travels in the data: a tap
            # acts only while Claude still holds THAT message.
            await self._handle_send_now_tap(
                update, query, session_name, action.split(":", 1)[1])
            return

        if action in ("kill", "kill-confirm"):
            # The old /kill picker (one tap, and it listed every chat's
            # sessions) and its confirm. No longer rendered: /kill opens the
            # End confirm (session_parity "end" / "endok<turn_key>"). One
            # still sitting in a chat does nothing; it was shown for a
            # session and a turn nothing can check any more.
            await self._safe_answer(query, i18n.for_person(update).text(TOAST_KILL_STALE))
            return

        if action == "kill-cancel":
            card_owner.release(self, calling_chat_id(update),
                               getattr(query.message, "message_id", None))
            try:
                await edit_text(query, i18n.for_update(update).text(KILL_CANCELLED))
            except Exception:
                pass
            return

        # ---- Voice-extra remote-install flow (item 5.3 follow-up) ----
        if session_name == "__voice__":
            if action == "cancel":
                try:
                    await edit_text(query, i18n.for_update(update).text(VOICE_NOT_INSTALLED_OK))
                except Exception:
                    pass
                return
            if action == "install":
                # Fire-and-forget — the install can take a couple
                # minutes and we want the callback handler to return
                # quickly so Telegram doesn't time out.
                asyncio.create_task(self._install_voice_extra(query))
                return
            if action == "restart":
                asyncio.create_task(self._restart_daemon(query))
                return
            return  # unknown __voice__ sub-action

        if action == "retry":
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, i18n.for_person(update).text(NOT_FOUND))
                return
            # Pop-ups are the tapper's; the card's note is the chat's.
            person = i18n.for_person(update)
            if not sess.last_prompt:
                await self._safe_answer(query, person.text(TOAST_RETRY_NOTHING))
                return
            # Roadmap 8.17c R3: not during a flood mute — neither the
            # tapped card's chat nor the session's. Retry is three steps
            # (inject the prompt, delete this error card, open a busy
            # card) and the ban refuses the two Telegram ones, so on
            # 0.7.14 the prompt went to Claude while the card, and its
            # Retry button, stayed: half applied, and a second tap after
            # the lift injected the prompt again. Refusing all of it keeps
            # `last_prompt` and the button exactly as they were, and the
            # tap can simply be repeated after the ban. The toast goes
            # through the dispatcher, which withholds it while the ban
            # runs (8.26 D-1) — so this tap makes no request of any kind.
            chat_muted = next(
                (chat for chat in (
                    _message_chat_id(getattr(query, "message", None)),
                    resolve_chat_id(sess),
                ) if chat and MUTE.is_muted(chat)),
                None,
            )
            if chat_muted is not None:
                until = clear_time(MUTE.until(chat_muted))
                log.info("[%s] Retry refused — chat %s is flood-muted until %s",
                         sess.label, chat_muted, until)
                await self._safe_answer(
                    query, person.text(RATE_LIMITED, time=person.num(until)))
                return
            if not await inject.is_alive(session_name):
                await self._safe_answer(query, person.text(TOAST_RETRY_NOT_ALIVE,
                                                           session=session_name))
                return
            if (not sess.dialog_is_open()
                    and not mixed_sender_note_outstanding(sess, update)
                    and self._turn_sender_differs(
                        sess, driver_id_from_update(update),
                        chat_id=calling_chat_id(update))):
                # Roadmap 8.77 (D-H): another person's turn is running.
                # Re-injected now, the prompt would join that turn under its
                # rules; refused like the open-prompt case below, the card
                # keeps its button for a tap once the turn ends.
                await self._safe_answer(query, person.text(TOAST_RETRY_OTHERS_TURN))
                try:
                    await edit_message(query.message,
                        f"{original_text}\n\n"
                        + i18n.for_update(update).text(RETRY_NOTE_OTHERS),
                        reply_markup=query.message.reply_markup,
                    )
                except Exception:
                    log.debug("[%s] could not annotate the retry card",
                              sess.label, exc_info=True)
                return
            if sess.dialog_is_open() or mixed_sender_note_outstanding(sess, update):
                # Retry buttons outlive the error they were attached to, so
                # this one can be tapped long after the session has moved on
                # into a permission or question prompt — OR after a
                # different Telegram user's message is already outstanding
                # (design.md "queue handoff"'s mixed-sender rule, applied
                # here beside the dialog check). Re-injecting either would
                # be typing the old prompt somewhere it doesn't belong.
                # Refuse rather than queue: the operator is looking at the
                # prompt, and tapping Retry again after answering is one tap.
                await self._safe_answer(query, person.text(TOAST_RETRY_PROMPT_OPEN))
                # A toast is gone the moment the operator looks away, and
                # tapping Retry is exactly the sort of thing done on the way
                # out. Leave the reason on the card itself, keeping the
                # button so it can be tapped again once the prompt is
                # answered. `last_prompt` is untouched, so nothing is lost.
                try:
                    await edit_message(query.message,
                        f"{original_text}\n\n"
                        + i18n.for_update(update).text(RETRY_NOTE_PROMPT),
                        reply_markup=query.message.reply_markup,
                    )
                except Exception:
                    log.debug("[%s] could not annotate the retry card",
                              sess.label, exc_info=True)
                return
            # Re-inject the last prompt (last_prompt stays set for retry-of-retry)
            prompt = sess.last_prompt
            # Runs as whoever tapped Retry when that person also sent the
            # prompt, so an owner's own Retry keeps its rights (a note
            # with no sender falls to the floor, which has no Bash).
            # Someone else's text never borrows the tapper's rights
            # (rev-iter1-001 of the queue-handoff review): with a
            # different or unknown author, no sender — the floor.
            tapper = driver_id_from_update(update)
            author = sess.last_prompt_driver_user_id
            was_busy = sess.status is Status.BUSY
            ok = await self._inject_prompt(
                sess, prompt, msg_id=sess.trigger_msg_id,
                chat_id=calling_chat_id(update),
                driver_user_id=tapper if tapper == author else None)
            if ok:
                await self._safe_answer(query, person.text(TOAST_RETRY_RETRYING,
                                                           session=sess.label))
                # Delete the error message — busy animation replaces it.
                # In the chat the tapped message lives in: the same id in
                # another chat is someone else's message.
                try:
                    await self._app.bot.delete_message(
                        chat_id=_message_chat_id(query.message),
                        message_id=query.message.message_id,
                    )
                except Exception:
                    pass
                self.registry.transition(session_name, Status.BUSY)
                await self._card_for_injected(sess, was_busy=was_busy)
                log.info("[%s] Retry: %s", sess.label, prompt[:80])
            elif ok is PROMPT_REFUSED:
                # A slash command that may not be resent (roadmap 8.74).
                # Someone else's text runs with no sender, which may send
                # none, so even an owner cannot resend another member's.
                await self._safe_answer(
                    query,
                    shown(person, NEEDS_ADMIN_REPLY
                          if self._command_needs_admin(
                              prompt, tapper, chat_id=self._attribution_chat(
                                  sess, calling_chat_id(update)))
                          else RETRY_OTHERS_COMMAND_REPLY))
            else:
                await self._safe_answer(query, person.text(TOAST_RETRY_FAILED))
            return

        if action == "compact":
            person = i18n.for_person(update)
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, person.text(NOT_FOUND))
                return
            if not await inject.is_alive(session_name):
                await self._safe_answer(query, person.text(SESSION_GONE, session=session_name))
                return
            # The tapper is the sender: a sender-less note carried an
            # unknown sender's rights and, lingering (a local command no
            # prompt consumes), dropped the next owner turn to them
            # ("peer message authority", C13).
            was_idle = sess.status is Status.IDLE
            ok = await self._inject_prompt(
                sess, "/compact", driver_user_id=calling_user_id(update),
                chat_id=calling_chat_id(update))
            if ok and was_idle:
                self._mark_injected_command_ran(sess, "/compact")
            if ok:
                await self._safe_answer(query, person.text(TOAST_COMPACT_SENDING,
                                                           session=sess.label))
                # The tapped message, in its own chat (see Retry).
                try:
                    await self._app.bot.delete_message(
                        chat_id=_message_chat_id(query.message),
                        message_id=query.message.message_id,
                    )
                except Exception:
                    pass
                log.info("[%s] Compact triggered by user", sess.label)
            elif ok is PROMPT_REFUSED:
                await self._safe_answer(query, shown(person, NEEDS_ADMIN_REPLY))
            else:
                await self._safe_answer(query, person.text(TOAST_COMPACT_FAILED))
            return

        # ---- /settings menu callbacks ----------------------------------
        # `_:set`, `_:set:<section>`, `_:set:back`, `_:set:close` (nav —
        # always allowed) and `_:set:<section>:<value>` (write — admin-
        # gated in groups). `action` already carries everything after the
        # first colon (e.g. "set:layout:merged"); the "set" prefix is a
        # decoy of the `session_name, action = cb_data.split(":", 1)`
        # split above landing on the `_` global-action sentinel.
        if session_name == "_" and (action == "set" or action.startswith("set:")):
            await self._dispatch_settings_action(update, query, action)
            return

        if session_name == "_" and action == "pick:cancel":
            # Any session command's picker (4.4).
            card_owner.release(self, calling_chat_id(update),
                               getattr(query.message, "message_id", None))
            # A "Which session?" card's held message goes with it (8.94i).
            held_message.drop(self, calling_chat_id(update),
                              getattr(query.message, "message_id", None))
            try:
                await edit_text(query, i18n.for_update(update).text(PICK_CANCELLED))
            except Exception:
                pass
            return

        if session_name == "_" and action in ("st:list", "st:ended"):
            # /status's own navigation (4.2): back to the list, or the
            # Ended view. Re-rendered from state on every tap.
            chat = calling_chat_id(update)
            text, kb = (self._render_status_list(chat, update)
                        if action == "st:list" else self._render_ended_view(chat))
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        if action == "clear_gone":
            # Hide THIS chat's GONE sessions from /status (never another
            # chat's). Preserves resume metadata (claude_session_id, cwd)
            # so /resume keeps working — see TrackedSession.hidden_from_status.
            chat = calling_chat_id(update)
            hidden = []
            for name, sess in list(self.registry.all_sessions(chat).items()):
                if sess.status == Status.GONE and not sess.hidden_from_status:
                    sess.hidden_from_status = True
                    hidden.append(sess.label)
            person = i18n.for_person(update)
            if hidden:
                self.registry.mark_dirty()
                await self._safe_answer(
                    query, person.n(TOAST_CLEAR_DONE, len(hidden), kind="text"))
                log.info("Hid gone sessions from /status: %s", hidden)
            else:
                await self._safe_answer(query, person.text(TOAST_CLEAR_NOTHING))
            text, kb = self._render_status_list(chat, update)
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        # ---- /perms callbacks -----------------------------------------
        if action in ("perms_confirm", "perms_cancel",
                      "perms_stop_switch", "perms_wait"):
            # The card's own record says which mode it switches to. A tap
            # on another card for the session (an older one) must neither
            # use nor clear the newer card's record.
            tapped = getattr(query.message, "message_id", None)
            pending = self._perms_pending.get(session_name)
            if pending is not None and pending.get("msg_id") not in (None, tapped):
                # (A tap that carries no message id cannot show it is this
                # card's, so it is not.)
                pending = None
            label = (pending or {}).get("label", session_name.removeprefix("claude-"))
            sess = self.registry.get(session_name)

            def _consume() -> None:
                # Only this card's record, and only if it is still the one
                # stored: a newer card's must survive a tap on this one.
                if pending is not None and self._perms_pending.get(session_name) is pending:
                    del self._perms_pending[session_name]

            chat = calling_chat_id(update)
            shared = i18n.for_update(update)
            person = i18n.for_person(update)

            async def _show_card(note: str = "") -> None:
                """Back to the /mode card (the current mode, a fresh switch)."""
                if sess is None:
                    text, kb = (note or shared.html(CANCELLED)), None
                else:
                    text, kb = self._mode_card(chat, sess, note)
                try:
                    await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
                except Exception:
                    pass

            if action in ("perms_cancel", "perms_wait"):
                # Cancel / Not now: the card again, for the mode it is in.
                _consume()
                card_owner.release(self, chat, tapped)
                await _show_card()
                return

            if sess is not None and sess.relaunch_in_flight:
                # A switch or restart is running right now (perhaps this
                # card's own, tapped twice). Say so, and keep the card and
                # any record: the same button works in a moment.
                await self._safe_answer(
                    query, person.text(TOAST_PERMS_RESTARTING, session=sess.label))
                return

            if pending is None:
                # Which mode this card was for is not known any more (it
                # was used, a newer card replaced it, or the daemon
                # restarted). Never guess: it used to default to Ask, so a
                # "Switch to Auto?" card relaunched the session in Ask. It
                # becomes the current card, so a tap on it is a fresh one.
                await self._safe_answer(query, person.text(TOAST_PERMS_STALE))
                await _show_card(shared.html(MODE_STALE))
                return
            target_mode = pending["target_mode"]

            if sess is None:
                _consume()
                try:
                    await edit_text(query, shared.text(MODE_SESSION_MISSING))
                except Exception:
                    pass
                return

            fallback: list = []

            async def _edit(text, **kw):
                """The card, edited; if that fails once, one message in the
                tapped chat carries every later step (never the card again)."""
                if not fallback:
                    try:
                        await edit_text(query, text, **kw)
                        return
                    except Exception as exc:
                        if "not modified" in str(exc).lower():
                            return        # it already shows this
                        log.debug("[%s] mode card edit failed, sending", label,
                                  exc_info=True)
                elif fallback[0] is not MUTED:
                    try:
                        await edit_message(fallback[0], text, **kw)
                        return
                    except Exception:
                        log.debug("[%s] mode message edit failed", label, exc_info=True)
                fallback[:] = [await send_text(self._app.bot,
                    chat_id=chat or resolve_chat_id(sess), text=text, **kw,
                )]

            # Confirmed (into Full access on an idle session) or Stop &
            # switch (a
            # busy one; the core interrupts first). Whatever happens next
            # edits this card, its buttons going first, so its record is
            # spent. The record's turn decides whether the tap is current.
            tapper = getattr(query, "from_user", None)
            # A switch relaunches the session: a role without can_manage
            # only for one inside its members' root folder.
            from aipager import member_root
            refusal = member_root.relaunch_refusal(
                self, getattr(tapper, "id", None), chat, sess.cwd)
            if refusal:
                await self._safe_answer(query, shown(person, refusal), show_alert=True)
                return
            _consume()
            await self._do_perms_switch_via_fn(
                sess, target_mode, _edit, tapped_msg_id=tapped,
                turn=pending.get("turn"), chat_id=chat,
                by=self._actor_label(getattr(tapper, "id", None), chat, tapper))
            # After the switch: until the card shows its result, any button
            # still on it is the requester's (8.91c).
            card_owner.release(self, chat, tapped)
            return

        # ---- /resume mode-picker callbacks ----------------------------
        if action in ("resume_mode_ask", "resume_mode_auto", "resume_mode_cancel"):
            sess = self.registry.get(session_name)
            label = self._resume_mode_pending.pop(session_name, None)
            if label is None and sess is not None:
                label = sess.label
            if label is None:
                label = session_name.removeprefix("claude-")

            if action == "resume_mode_cancel":
                try:
                    await edit_text(query, i18n.for_update(update).text(CANCELLED))
                except Exception:
                    pass
                return

            # Cards from before Full access existed: their Auto is now
            # Auto (a safety check), never wider.
            mode_override = (perm_modes.AUTO if action == "resume_mode_auto"
                             else perm_modes.ASK)

            async def _reply(text, **kw):
                try:
                    await edit_text(query, text, **kw)
                except Exception:
                    # The picker's own chat, else the session's; never
                    # the install's default chat (a group, on a DM+group
                    # install).
                    reply_chat = calling_chat_id(update) or (
                        resolve_chat_id(sess) if sess is not None else None)
                    if reply_chat:
                        await send_text(self._app.bot,
                            chat_id=reply_chat, text=text, **kw,
                        )

            # The session this button names, never a label lookup that
            # could land on another chat's session (roadmap 8.78); with no
            # such session left, the label resolves in THIS chat only.
            # Only Full access needs an admin, and these old buttons never
            # offer it.
            await self._do_resume(
                label=label, reply_fn=_reply,
                update=update, query=query, sess=sess,
                mode_override=mode_override,
            )
            return

        # ---- /resume picker callbacks ---------------------------------
        # The `{name}:resume` branch that used to live here is gone: every
        # resume button now carries the short indexed form and is claimed
        # by `session_parity.handle_callback` at the top of this method,
        # which also answers "Session not found" for an unknown session —
        # so this branch could never run again. Dead code that reads as
        # live is how the wrong thing gets maintained.
        #
        # `resume_mode_*` below is NOT dead: buttons rendered before that
        # change still carry it, and `session_parity` does not claim those
        # verbs (its own are hyphenated: `resume-ask` / `resume-auto` /
        # `resume-cancel`).
        if action.startswith("resume_page:"):
            try:
                page = int(action.split(":", 1)[1])
            except (IndexError, ValueError):
                page = 0
            text, kb = self._render_resume_picker(
                page=page, scope_chat_id=calling_chat_id(query),
            )
            try:
                await edit_text(query,
                    text, parse_mode="HTML", reply_markup=kb,
                )
            except Exception:
                pass
            return

        if action == "resume_noop":
            # The page indicator is a no-op tap; just clear the spinner.
            return

        # ---- /new name-conflict callbacks -----------------------------
        if action in ("new_resume", "new_replace", "new_cancel"):
            # Looked at, not taken: a refused Replace leaves the card (and
            # its first message) to its author. Each action that goes
            # ahead takes it.
            pending = self._new_conflict_pending.get(session_name)
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name.removeprefix("claude-")
            # The card belongs to the person who sent /new, like the Name
            # card: nobody else cancels it, resumes with their first
            # message, or replaces the session. Resume and Replace also
            # need the right to prompt here.
            # A card whose author is unknown (0) has nobody to check
            # against: anyone who may prompt uses it. A new session from
            # it is then refused Full access below (no author is an
            # admin); a Resume needs the tapper to be an admin for Full
            # access (`_resume_full_gate`, roadmap 8.83).
            tapper = getattr(getattr(query, "from_user", None), "id", None)
            author = (pending or {}).get("user_id")
            shared = i18n.for_update(update)
            person = i18n.for_person(update)
            if author and tapper != author:
                await self._safe_answer(
                    query, person.text(TOAST_NEW_ONLY_SENDER), show_alert=True)
                return
            if action != "new_cancel" and not self._can_prompt_user(
                    tapper, calling_chat_id(update)):
                await self._safe_answer(
                    query, person.text(TOAST_NEW_CANT_START_RESUME), show_alert=True)
                return

            if action == "new_cancel":
                self._new_conflict_pending.pop(session_name, None)
                try:
                    await edit_text(query, shared.text(NEW_CANCELLED_NOTHING))
                except Exception:
                    pass
                return

            if sess is None:
                # A genuine /new conflict prompt always names a session
                # that is (or was) in the registry — _send_new_conflict_prompt
                # only fires for an `existing` session resolved via
                # registry.find_by_label. A stale long-form tap for a
                # name that is no longer tracked at all has nothing to
                # switch to (new_resume) or kill-and-relaunch (new_replace);
                # failing closed here is what stops new_replace from
                # calling inject.launch_session for an arbitrary tapped
                # name (the severe case design.md's success criteria and
                # the "Old buttons" section both promise against).
                self._new_conflict_pending.pop(session_name, None)
                await self._safe_answer(query, person.text(NOT_FOUND))
                return

            prompt = (pending or {}).get("prompt", "")
            # Full access is re-checked at creation, like every other
            # start: the card may be old, and its author since demoted.
            new_mode = perm_modes.narrowest_allowed(
                (pending or {}).get("mode") or perm_modes.AUTO,
                bool(self._may_use_full_access(
                    (pending or {}).get("user_id"), calling_chat_id(update))))
            # The queued prompt is the text the /new AUTHOR typed, so it
            # runs as the author, never as whoever tapped the button: a
            # tapper's rights must not be lent to someone else's prompt
            # (the same rule as Retry; review 2026-09-27). 0 means the
            # author is unknown, which gets the floor at pick-up.
            prompt_author = (pending or {}).get("user_id") or None

            if action == "new_resume":
                # A role without can_manage resumes (or switches to) only a
                # session inside its members' root folder: refused before
                # the card is spent, so it can still Cancel.
                from aipager import member_root
                refusal = member_root.relaunch_refusal(
                    self, tapper, calling_chat_id(update), sess.cwd)
                if refusal:
                    await self._safe_answer(query, shown(person, refusal),
                                            show_alert=True)
                    return
                self._new_conflict_pending.pop(session_name, None)
                # Live session → switch to it; GONE session → /resume flow.
                if sess and sess.status != Status.GONE:
                    self.registry.set_target(
                        session_name, calling_chat_id(update),
                        calling_user_id(update))
                    self.registry.mark_dirty()
                    asyncio.create_task(
                        self._maybe_update_bot_name(session_name)
                    )
                    if prompt and sess.queue_prompt(
                        prompt, pending.get("msg_id", 0), "",
                        prompt_author,
                    ):
                        self.registry.mark_dirty()
                    try:
                        await edit_text(query,
                            shared.html(NEW_SWITCHED_TO, session=label)
                            + ("\n" + shared.html(NEW_PROMPT_QUEUED) if prompt else ""),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    return

                # GONE: route through the shared _do_resume helper.
                async def _reply(text, **kw):
                    try:
                        await edit_text(query, text, **kw)
                    except Exception:
                        # The tapped card's chat, else the session's;
                        # with neither (an unstamped session on an
                        # install with no default chat) nothing is sent.
                        reply_chat = calling_chat_id(update) or resolve_chat_id(sess)
                        if reply_chat:
                            await send_text(self._app.bot,
                                chat_id=reply_chat, text=text, **kw,
                            )

                # Exactly the session this card is about: a label lookup
                # could find another chat's session of the same name.
                await self._do_resume(label=label, reply_fn=_reply,
                                      update=update, query=query, sess=sess)
                # Queue the prompt only into a session that came back.
                if prompt and sess.status != Status.GONE and sess.queue_prompt(
                    prompt, pending.get("msg_id", 0), "", prompt_author,
                ):
                    self.registry.mark_dirty()
                return

            if action == "new_replace":
                # Replace kills a session and starts a fresh one: only on a
                # card whose state is known (author and choices), and only
                # on the work the card showed.
                if pending is None:
                    await self._safe_answer(query, person.text(TOAST_NEW_EXPIRED),
                                            show_alert=True)
                    return
                working = sess.status in (Status.BUSY, Status.INTERACTIVE)
                if (not sess.tap_is_for_this_turn(pending.get("msg_id"))
                        or (working and not pending.get("was_working"))):
                    # The card appeared right after the message that named
                    # the session (it may be the older Name card, edited),
                    # so a turn whose card came later is work it never
                    # showed; and so is one that started while the session
                    # was idle on the card, card or not yet (a self-woken
                    # turn holds its card back for seconds). This kills the
                    # socket and drops resume_id: a stale tap would destroy
                    # a live session for good.
                    await self._safe_answer(
                        query, person.text(TOAST_NEW_STARTED_SINCE, session=sess.label),
                        show_alert=True)
                    return
                # The fresh session's folder must be one its author may
                # start in (roadmap 8.79, and a role without can_manage only
                # inside its members' root folder), checked before the old
                # one is killed: create_session would refuse it only
                # afterwards. Nor does such a role kill a session working
                # outside its root.
                from aipager import member_root
                refusal = (member_root.relaunch_refusal(
                               self, tapper, calling_chat_id(update), sess.cwd)
                           or member_root.launch_refusal(
                               self, prompt_author, sess.scope_chat_id or None,
                               (pending or {}).get("cwd") or None))
                if refusal:
                    await self._safe_answer(query, shown(person, refusal), show_alert=True)
                    return
                self._new_conflict_pending.pop(session_name, None)
                # Kill alive socket first, then launch fresh (no resume_id).
                if sess and sess.status != Status.GONE:
                    # The replaced session's turn will never earn a card
                    # (roadmap 8.32): stand a self-woken turn's deferral
                    # down before the kill, or its timer could put a card
                    # up for the dead turn before the new session starts.
                    # (No card lock: nothing below settles a card.)
                    self._cancel_lazy_card(sess)
                    await inject.kill_session(session_name)
                    # Wait briefly for socket to disappear so the next
                    # launch's "already exists" check passes. The socket
                    # is named after the INTERNAL name (chat-suffixed in a
                    # scoped chat), never the bare label.
                    sock = (f"{inject.SOCK_PREFIX}"
                            f"{session_name.removeprefix('claude-')}.sock")
                    from pathlib import Path as _Path
                    for _ in range(10):
                        await _sleep(0.2)
                        if not _Path(sock).is_socket():
                            break
                # Drop the resume metadata so the new session is truly fresh.
                if sess:
                    sess.claude_session_id = ""
                    sess.transcript_path = ""
                    sess.cwd = ""
                    sess.gone_at = None
                    self.registry.mark_dirty()

                try:
                    await edit_text(query, shared.html(NEW_STARTING_FRESH, session=label),
                                    parse_mode="HTML")
                except Exception:
                    pass

                # The shared seam, like every other start: the session's
                # system prompt, and the mode, model and folder /new chose
                # (Name card or the chat's defaults). It used to relaunch
                # the bare label, which in a scoped chat is a different
                # session. The fresh process keeps the replaced one's exact
                # internal name (a renamed session's label no longer gives
                # it) and its own scope, never the tapping chat's.
                scope = sess.scope_chat_id or None
                new_name, err = await self.create_session(
                    label, scope_chat_id=scope, reuse_name=session_name,
                    mode=new_mode,
                    cwd=(pending or {}).get("cwd") or None,
                    driver_user_id=prompt_author,
                    model=(pending or {}).get("model") or None,
                )
                if not new_name:
                    try:
                        await edit_text(query, shared.html(NEW_CREATE_FAILED,
                                                           error=shown(shared, err)),
                                        parse_mode="HTML")
                    except Exception:
                        pass
                    return

                new_sess = self.registry.get_or_create(new_name)
                if prompt and new_sess.queue_prompt(
                    prompt, pending.get("msg_id", 0), "",
                    prompt_author,
                ):
                    self.registry.mark_dirty()

                # The model line comes from the session's launch model,
                # by its label ("opus" shows as "Opus").
                ready_text, ready_kb = new_flow.render_ready(
                    self, update, new_sess, first_message=bool(prompt))
                try:
                    await edit_text(query, ready_text, parse_mode="HTML",
                                    reply_markup=ready_kb)
                except Exception:
                    pass
                log.info("[%s] /new conflict resolved via Replace (prompt=%s)",
                         label, bool(prompt))
                return

        is_option = action.startswith("opt") and action[3:].isdigit()

        if action not in ACTION_VERBS and not is_option and action != "submit":
            await self._safe_answer(query, i18n.for_person(update).text(
                UNKNOWN_ACTION, action=action))
            return

        # Pop-ups are the tapper's; the lines in the chat are the chat's.
        person = i18n.for_person(update)
        sess = self.registry.get(session_name)
        if not sess:
            await self._safe_answer(query, person.text(NOT_FOUND))
            return

        # A message carrying answer buttons outside the busy card — a
        # separate-message prompt, or a copy the pinned bar's "Answer"
        # button re-sent (8.31) — is bound to the prompt it showed. Tapped
        # once that prompt is answered (in the terminal, on the card, on
        # another copy), the session may be working, or already waiting on
        # a DIFFERENT prompt: either way the tap must not type into it.
        resent_key = (
            _message_chat_id(getattr(query, "message", None)) or 0,
            getattr(getattr(query, "message", None), "message_id", None),
        )
        surface = self._resent_prompts.get(resent_key)
        is_resent_copy = surface is not None
        # Every open prompt's identity (roadmap 8.120), not only the
        # first: a prompt that opened while another was open has its own.
        open_tokens = open_prompt_tokens(sess)
        refusal = None
        strip_refused = True  # take the refused message's buttons away
        tapped_id = resent_key[1]
        if is_resent_copy:
            # Kept, not popped: the entry IS the guard, and the message
            # keeps its buttons if the edit below does not land. A missing
            # token on either side never matches (fail closed).
            if surface[1] is None or surface[1] not in open_tokens:
                refusal = ALREADY_ANSWERED
        elif ((is_option or action in _CARD_ANSWER_VERBS)
                and sess.prompt_known_closed(tapped_id)):
            # An answer button on a message whose prompt is KNOWN to be
            # closed (roadmap 8.102): answered by a tap already, answered
            # in the terminal (its tool finished), its turn ended, or a
            # saved prompt the restart did not bring back. A double tap or
            # a client still showing the old keyboard: typed now, the keys
            # would land in Claude's input, not in a dialog. A prompt that
            # is only not known open (the INTERACTIVE watchdog's demotion,
            # a stale BUSY) is not in the record: its buttons still type.
            # Its buttons are not taken off: the message is the working
            # card (its Stop), a finished card (its own buttons) or a
            # prompt already marked answered.
            refusal = ALREADY_ANSWERED
            strip_refused = False
        elif (sess.status == Status.INTERACTIVE
                and (sess.pending_permission or sess.pending_prompt_msg)):
            # A prompt is pending, and this tap is on a message that is
            # neither the busy card it is shown in nor registered to it: a
            # copy the bar re-sent before a restart (the map is not
            # persisted; only the prompt a restart restored re-registers
            # its own separate message, roadmap 8.102), an older prompt's
            # message, or any other stale surface. Fail closed.
            #
            # Fails OPEN, like `tap_is_for_this_turn`, when there is nothing
            # to compare against: a tap with no message id, or an inline
            # prompt with no busy card id (it cannot be shown inline without
            # one, so that is not a state a real tap meets).
            card = sess.busy_msg_id
            if isinstance(tapped_id, int) and not isinstance(tapped_id, bool):
                if sess.pending_permission:
                    if card and card > 0 and tapped_id != card:
                        refusal = PROMPT_EXPIRED
                else:
                    # A separate-message prompt is always registered when
                    # it is sent, and again when a restart restores it
                    # (8.102); an unregistered message is not it.
                    refusal = PROMPT_EXPIRED
        if refusal is not None:
            await self._safe_answer(query, person.text(refusal))
            if strip_refused:
                try:
                    await edit_markup(query, reply_markup=None)
                except Exception:
                    pass
            return

        if not await inject.is_alive(session_name):
            await self._safe_answer(query, person.text(SESSION_GONE, session=session_name))
            return

        # Inject keystrokes
        ok = True
        perm = sess.pending_permission or {}
        # A separate-message prompt (sent before the turn's busy card
        # existed) keeps the same answer context in its own record
        # (roadmap 8.99): its Allow/Deny answer through the parked hook
        # and post the same attributed line as the inline prompt. Only a
        # tap on that message (or the bar's copy of it) reaches here with
        # the prompt still current (the refusal check above).
        separate_perm = None
        if is_resent_copy:
            # The tapped message's token picks its prompt (roadmap 8.120):
            # the card's own (a bar copy), the one sent as its own message,
            # or one of those that opened while another was open.
            where, picked = _prompt_for_token(sess, surface[1])
            if where in ("separate", "extra"):
                separate_perm = picked
                perm = picked
            elif where is None and not perm:
                separate_perm = (sess.pending_prompt_msg or {}).get("perm") or None
                perm = separate_perm or {}
        if (is_option or action in ("submit", "continue")) and await self._keys_refused(
                query, person, sess, perm):
            return
        # Taken before any key is typed: the keystroke fallback's watch
        # reads only what Claude wrote after it (roadmap 8.99).
        answered_at = time.time()
        # What Claude's own queue held before a refusal was typed (the
        # watch's /stop-like end needs it; read before the keys).
        claude_held: list[dict] = []
        answer_text = None  # a branch may override the "<verb> [label]" toast
        shared = i18n.for_sess(sess)
        # design.md "answer PermissionRequest hooks with a decision
        # instead of keystrokes": allow/allow_always/deny set this to
        # "hook_decision" or "keystroke_fallback"; every other action
        # (opt<N>, submit, continue, ...) leaves it empty — audit.append's
        # own `via` kwarg defaults to "" too, so this changes nothing for
        # them.
        via = ""
        # The effective decision's id (``_DECISION_ICONS``), decided next
        # to ``verb`` in every branch: the record and the line read it,
        # never the verb shown. None for an option or an unknown action.
        decision: str | None = None
        if is_option or action == "submit":
            log.info("[%s] Callback: action=%s, multi_select=%s, has_perm=%s",
                     sess.label, action, perm.get("multi_select"), bool(perm))

        if is_option and perm.get("multi_select"):
            # ── Multi-select: toggle checkbox, update keyboard, return early ──
            option_index = int(action[3:])
            cursor_pos = perm.get("cursor_pos", 0)
            selected = perm.get("selected", set())

            # Navigate from cursor_pos to option_index
            delta = option_index - cursor_pos
            key = "Down" if delta > 0 else "Up"
            for _ in range(abs(delta)):
                if not await inject.send_keys(session_name, key):
                    ok = False
                    break
            if ok:
                await _sleep(0.1)
                ok = await inject.send_keys(session_name, "Enter")  # toggle checkbox

            if ok:
                # Update selected set
                if option_index in selected:
                    selected.discard(option_index)
                else:
                    selected.add(option_index)
                perm["selected"] = selected
                perm["cursor_pos"] = option_index

                option = perm["options"][option_index]
                opt_label = (option["label"] if "label" in option
                             else person.text(keyboards.OPTION, n=option_index + 1))
                toggled = "☑" if option_index in selected else "⬜"
                await self._safe_answer(query, f"{toggled} {opt_label}")

                # Rebuild keyboard with updated checkmarks
                keyboard = self._build_inline_ask_keyboard(
                    sess, perm["options"],
                    multi_select=True, selected=selected)
                text = self._build_busy_text(sess.label, "Waiting", sess)
                await self._edit_busy_raw(
                    sess.busy_msg_id, text, reply_markup=keyboard,
                    chat_id=resolve_chat_id(sess))
                log.info("[%s] Multi-select toggle: opt%d (%s), selected=%s",
                         sess.label, option_index, toggled, selected)
            else:
                await self._safe_answer(query, person.text(SEND_KEYS_FAILED))
            return

        elif action == "submit" and perm.get("multi_select"):
            # ── Multi-select: advance to next tab via Right arrow ──
            # TUI tabs: ← ☒ Q1  ☐ Q2  ...  ✔ Submit →
            # Right arrow moves one tab forward.
            selected = perm.get("selected", set())
            options = perm.get("options", [])
            questions = perm.get("questions", [])
            current_idx = perm.get("current_idx", 0)
            next_idx = current_idx + 1
            is_last = next_idx >= len(questions)

            # Build verb from selected options
            sel_labels = [options[i].get("label", f"#{i+1}")
                          for i in sorted(selected)]
            verb = "Selected: " + ", ".join(sel_labels) if sel_labels else "Submitted (none)"

            # Right to advance one tab (to next question, or to Submit)
            ok = await inject.send_keys(session_name, "Right")
            if ok and is_last:
                # Last question — landed on Submit tab, press Enter to submit
                await _sleep(0.15)
                ok = await inject.send_keys(session_name, "Enter")

            if ok:
                await self._safe_answer(query, person.text(
                    ANSWERED_TOAST, verb=Safe(card_rows.verb_text(person, verb, "text")[:180])))

                # Collapse into tool_history
                question_text = perm.get("question", "?")
                collapsed = f"❓ {question_text[:40]} → {verb}"
                sess.record_tool(collapsed, True)

                # Audit reply (multi-select submit path)
                from aipager import audit as audit_mod
                # `member` was resolved by _authorize_callback at the top
                # of _handle_callback; in personal mode it's a sentinel
                # (id=0) — only record real allow-listed users.
                actor = member if member is not None and member.id != 0 else None
                audit_mod.append(
                    session=sess.name, label=sess.label, action="Answered",
                    tool="AskUserQuestion",
                    summary=f"{question_text[:120]} → {verb[:80]}",
                    user_id=actor.id if actor else None,
                    username=actor.label if actor else "",
                    scope_label=self._scope_label(sess.scope_chat_id),
                    scope_chat_id=sess.scope_chat_id or None,
                )
                # "· @bob", never "by @bob": a translated line then needs
                # no grammatical gender for the person (multilingual design).
                params = {"session": sess.label, "question": question_text[:80],
                          "option": Safe(card_rows.verb_text(shared, verb[:80], "html"))}
                if actor:
                    params["person"] = Command(attribution_label(actor))
                try:
                    await send_text(self._app.bot,
                        resolve_chat_id(sess),
                        shared.html(ANSWERED if actor else ANSWERED_ANON, **params),
                        parse_mode="HTML",
                        reply_to_message_id=(sess.busy_msg_id
                                             if sess.busy_msg_id and sess.busy_msg_id > 0
                                             else None),
                    )
                except Exception:
                    log.debug("[%s] audit message send failed", sess.label,
                              exc_info=True)

                if not is_last:
                    # More questions — Right moved to next question tab
                    next_q = questions[next_idx]
                    next_options = next_q.get("options", [])
                    next_multi = next_q.get("multiSelect", False)
                    sess.pending_permission = {
                        "ask_question": True,
                        "question": next_q.get("question", "?"),
                        "options": next_options,
                        "questions": questions,
                        "current_idx": next_idx,
                        "multi_select": next_multi,
                        "cursor_pos": 0,
                        "selected": set(),
                        "tool_info": perm.get("tool_info"),
                        "wait_started_at": perm.get("wait_started_at"),
                    }
                    await _sleep(0.3)
                    keyboard = self._build_inline_ask_keyboard(
                        sess, next_options,
                        multi_select=next_multi)
                    text = self._build_busy_text(sess.label, "Waiting", sess)
                    await self._edit_busy_raw(
                        sess.busy_msg_id, text, reply_markup=keyboard,
                        chat_id=resolve_chat_id(sess))
                    log.info("[%s] Multi-select submit, advanced to Q%d/%d",
                             sess.label, next_idx + 1, len(questions))
                else:
                    # Last question — done; discount wait time from elapsed timer
                    wait_start = perm.get("wait_started_at", 0)
                    if wait_start and sess.busy_started_at:
                        sess.busy_started_at += time.monotonic() - wait_start
                    # Answered: a later tap on the card is refused (8.102).
                    if await self._inline_prompt_answered(sess, perm):
                        log.info("[%s] Multi-select submit complete", sess.label)
            else:
                await self._safe_answer(query, person.text(SEND_KEYS_FAILED))
            return

        elif is_option:
            option_index = int(action[3:])
            verb = f"Selected option {option_index + 1}"
            for _ in range(option_index):
                if not await inject.send_keys(session_name, "Down"):
                    ok = False
                    break
            if ok:
                await _sleep(0.1)
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "allow":
            verb = ACTION_VERBS[action]
            decision = "allow"
            # design.md "answer PermissionRequest hooks with a decision
            # instead of keystrokes": try the hook-returned-decision fast
            # path first — skipped entirely for AskUserQuestion, which
            # keeps its existing keystroke-only handling untouched.
            hook_answered = False
            if (perm.get("tool_info") or {}).get("name") != "AskUserQuestion":
                try:
                    hook_answered = hook_reply.send_decision(
                        perm.get("hook_reply"), hook_reply.allow_decision())
                except Exception:
                    hook_answered = False
            if hook_answered:
                via = "hook_decision"
                ok = True
                perm["hook_reply"] = None  # one decision per parked hook
            else:
                if await self._keys_refused(query, person, sess, perm):
                    return
                via = "keystroke_fallback"
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "allow_always":
            perm_info = perm.get("tool_info") or {}
            standing_rule = perm_info.get("standing_rule_suggestion")
            # Defense in depth: re-validate the retained suggestion's
            # ``type`` here too, not only at hook_receiver.py's own
            # retention point (_standing_rule_suggestion). A future bug
            # upstream that let a non-standing-rule entry (e.g. a
            # ``setMode`` session-wide mode switch) through onto
            # tool_info["standing_rule_suggestion"] must still never get
            # echoed as "Allow always"'s updatedPermissions — treat
            # anything outside this set exactly like "no suggestion at
            # all" (the degraded case below).
            if not (isinstance(standing_rule, dict)
                    and standing_rule.get("type") in _STANDING_RULE_SUGGESTION_TYPES):
                standing_rule = None
            hook_answered = False
            if perm_info.get("name") != "AskUserQuestion":
                try:
                    if standing_rule is not None:
                        hook_answered = hook_reply.send_decision(
                            perm.get("hook_reply"),
                            hook_reply.allow_decision(
                                updated_permissions=[standing_rule]),
                        )
                    else:
                        # Degraded case (no standing rule to echo) — a
                        # PLAIN allow still eliminates the dialog-row-
                        # order fragility the keystroke fallback below
                        # exists to work around, since it needs no
                        # navigation at all.
                        hook_answered = hook_reply.send_decision(
                            perm.get("hook_reply"), hook_reply.allow_decision())
                except Exception:
                    hook_answered = False
            if hook_answered:
                via = "hook_decision"
                ok = True
                perm["hook_reply"] = None  # one decision per parked hook
                if standing_rule is not None:
                    verb = ACTION_VERBS[action]
                    decision = "allow_always"
                else:
                    verb = ACTION_VERBS["allow"]
                    decision = "allow"
                    answer_text = person.text(ALWAYS_DEGRADED, session=sess.label)
                    log.info("[%s] allow_always degraded to a single allow "
                             "(always_available=%r)", sess.label,
                             perm_info.get("always_available"))
                    report_store.record_counter("allow_always_degraded")
            elif await self._keys_refused(query, person, sess, perm):
                return
            elif perm_info.get("always_available") is True:
                via = "keystroke_fallback"
                verb = ACTION_VERBS[action]
                decision = "allow_always"
                # One Down → "Yes, and don't ask again for …", the second
                # row — present exactly when the hook carried permission
                # suggestions (hook_receiver's PermissionRequest branch).
                ok = await inject.send_keys(session_name, "Down")
                if ok:
                    await _sleep(0.1)
                    ok = await inject.send_keys(session_name, "Enter")
            else:
                # No standing rule on offer, or unknown (a button from
                # before this guard, the Notification-fallback prompt, a
                # race). Since Claude Code 2.1.259 the second row of a Bash
                # prompt is then "Yes, and switch to auto mode": navigating
                # blind would drop the session into auto mode while
                # reporting "Allowed always". Never navigate — confirm the
                # pre-selected "Yes" and say so.
                via = "keystroke_fallback"
                verb = ACTION_VERBS["allow"]
                decision = "allow"
                answer_text = person.text(ALWAYS_DEGRADED, session=sess.label)
                log.info("[%s] allow_always degraded to a single allow "
                         "(always_available=%r)", sess.label,
                         perm_info.get("always_available"))
                report_store.record_counter("allow_always_degraded")
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "deny":
            verb = ACTION_VERBS[action]
            decision = "deny"
            hook_answered = False
            if (perm.get("tool_info") or {}).get("name") != "AskUserQuestion":
                actor_for_reason = (
                    member if member is not None and member.id != 0 else None
                )
                reason = (
                    f"{DENIED_VIA_AIPAGER} by {attribution_label(actor_for_reason)}"
                    if actor_for_reason else DENIED_VIA_AIPAGER
                )
                try:
                    # PermissionRequest's {behavior, message, interrupt}
                    # shape — NEVER enforce.py's deny_decision_json
                    # (PreToolUse's incompatible permissionDecision
                    # shape). interrupt is always False here: this is
                    # "no to this one tool call", never "abort the turn".
                    hook_answered = hook_reply.send_decision(
                        perm.get("hook_reply"),
                        hook_reply.deny_decision(message=reason, interrupt=False),
                    )
                except Exception:
                    hook_answered = False
            if hook_answered:
                via = "hook_decision"
                ok = True
                perm["hook_reply"] = None  # one decision per parked hook
            else:
                if await self._keys_refused(query, person, sess, perm):
                    return
                via = "keystroke_fallback"
                if (perm.get("tool_info") or {}).get("name") != "AskUserQuestion":
                    claude_held = await self._claude_queue_before_typed_refusal(sess)
                    answered_at = time.time()
                # Overshoot to the last item — see _DENY_OVERSHOOT.
                for _ in range(_DENY_OVERSHOOT):
                    if not await inject.send_keys(session_name, "Down"):
                        ok = False
                        break
                    await _sleep(0.1)
                if ok:
                    ok = await inject.send_keys(session_name, "Enter")
        elif action == "continue":
            verb = ACTION_VERBS[action]
            decision = "continue"
            ok = await inject.send_keys(session_name, "Enter")
        else:
            verb = action

        if ok:
            await self._safe_answer(query, answer_text or person.text(
                VERB_TOAST, verb=Safe(card_rows.verb_text(person, verb, "text")),
                session=sess.label))

            inline_answer = (sess.pending_permission is not None
                             and perm is sess.pending_permission)
            if is_resent_copy and inline_answer:
                # Answered from the bar's copy of an inline prompt: the
                # busy card is updated below as usual, and the copy shows
                # the verdict instead of its buttons. (A separate message
                # is edited by its own branch below.) Its registry entry
                # stays: the prompt it was bound to is over, so any later
                # tap on it is refused above.
                try:
                    await edit_text(query, f"{original_text}\n\n" + shared.text(
                        ARROW_VERB, verb=Safe(card_rows.verb_text(shared, verb, "text"))))
                except Exception:
                    pass

            if inline_answer:
                # Collapse current question into tool_history, write the
                # audit record and post the attributed line.
                perm = sess.pending_permission
                await self._record_prompt_answer(sess, perm, verb, member, via,
                                                 decision=decision)

                # Multi-question AskUserQuestion: advance to next question
                questions = perm.get("questions", [])
                current_idx = perm.get("current_idx", 0)
                next_idx = current_idx + 1

                if perm.get("ask_question") and next_idx < len(questions):
                    # More questions — show the next one inline
                    # NOTE: No Tab needed — Claude Code TUI auto-advances
                    # to the next unanswered question tab after Enter.
                    next_q = questions[next_idx]
                    next_options = next_q.get("options", [])
                    next_multi = next_q.get("multiSelect", False)
                    sess.pending_permission = {
                        "ask_question": True,
                        "question": next_q.get("question", "?"),
                        "options": next_options,
                        "questions": questions,
                        "current_idx": next_idx,
                        "multi_select": next_multi,
                        "cursor_pos": 0,
                        "selected": set(),
                        "tool_info": perm.get("tool_info"),
                        "wait_started_at": perm.get("wait_started_at"),
                    }
                    await _sleep(0.3)  # let TUI process and auto-advance
                    keyboard = self._build_inline_ask_keyboard(
                        sess, next_options,
                        multi_select=next_multi)
                    text = self._build_busy_text(sess.label, "Waiting", sess)
                    await self._edit_busy_raw(
                        sess.busy_msg_id, text, reply_markup=keyboard,
                        chat_id=resolve_chat_id(sess))
                    log.info("[%s] Multi-question: advanced to Q%d/%d",
                             sess.label, next_idx + 1, len(questions))
                else:
                    # Last question (or non-AskUserQuestion) — done
                    if perm.get("ask_question") and len(questions) > 1:
                        # Multi-question form: TUI auto-advances to Submit tab
                        # after last option selection. Send Enter to submit.
                        await _sleep(0.3)
                        await inject.send_keys(session_name, "Enter")
                    # Discount wait time from elapsed timer
                    wait_start = perm.get("wait_started_at", 0)
                    if wait_start and sess.busy_started_at:
                        sess.busy_started_at += time.monotonic() - wait_start
                    # Answered: a later tap on the card is refused (8.102).
                    await self._inline_prompt_answered(sess, perm)
                    log.info("[%s] Inline permission: %s (via=%s)",
                             sess.label, verb, via or "n/a")
            else:
                # The separate permission message. Claude has its answer
                # already: the state is claimed before any send below
                # waits in the flood pacing, so a quick Stop is not undone
                # by a late BUSY.
                if separate_perm:
                    # The wait is not "thinking" time: discounted from the
                    # card's clock, from when the card's clock started if
                    # the card came after the prompt.
                    wait_start = separate_perm.get("wait_started_at", 0)
                    if wait_start and sess.busy_started_at:
                        sess.busy_started_at += time.monotonic() - max(
                            wait_start, sess.busy_started_at)
                # Answered: a later tap on this message is refused, after
                # a restart too (8.102). Only the separate prompt or the
                # busy card (a prompt the watchdog stopped waiting on); any
                # other message keeps its old handling.
                if separate_perm or (tapped_id and tapped_id == sess.busy_msg_id):
                    sess.mark_prompt_closed(tapped_id)
                left = 0
                if separate_perm:
                    # Only this prompt closes (roadmap 8.120).
                    sess.close_prompts(lambda p: p is separate_perm)
                    left = len(sess.open_prompts())
                if left:
                    log.info("[%s] prompt closed (answered), %d still open",
                             sess.label, left)
                else:
                    # Mark session as busy after user interaction
                    self.registry.transition(session_name, Status.BUSY)
                log.info("[%s] %s (via=%s)", sess.label, verb, via or "n/a")
                try:
                    await edit_text(query, f"{original_text}\n\n" + shared.text(
                        ARROW_VERB, verb=Safe(card_rows.verb_text(shared, verb, "text"))))
                except Exception:
                    pass
                self.registry.remove_message(
                    query.message.message_id, calling_chat_id(query) or 0,
                )
                if separate_perm:
                    # The same record and attributed line as the inline
                    # prompt (roadmap 8.99), threaded under the busy card
                    # when there is one, else under the prompt itself.
                    await self._record_prompt_answer(
                        sess, separate_perm, verb, member, via, decision=decision,
                        reply_to=getattr(query.message, "message_id", None))
            if via == "keystroke_fallback":
                # A tool's prompt is answered by its hook when the hook is
                # still waiting; typing it is the fallback (problem reports,
                # 8.112). A question is always typed: not counted.
                if (perm.get("tool_info") or {}).get("name") != "AskUserQuestion":
                    report_store.record_counter("keystroke_fallback")
                # Typed into Claude Code's dialog: a refusal row ends the
                # turn like an interrupt, with no Stop hook (roadmap 8.99).
                person = getattr(query, "from_user", None)
                self._watch_keystroke_answer(
                    sess,
                    # Only a tool's Deny chose the dialog's refusal row; a
                    # question's rows are its options (the degraded
                    # "AskUserQuestion (loading...)" prompt has Deny too).
                    refusal=(action == "deny" and (perm.get("tool_info") or {})
                             .get("name") != "AskUserQuestion"),
                    held=claude_held,
                    by=self._actor_label(
                        getattr(person, "id", None),
                        _message_chat_id(getattr(query, "message", None)), person),
                    answered_at=answered_at)
        else:
            await self._safe_answer(query, person.text(SEND_FAILED, session=session_name))

    async def _keys_refused(self, query, person, sess, perm) -> bool:
        """Refuse a tap whose keys could land in another dialog (roadmap
        8.120): with more than one prompt open, keys go only to the one
        Claude Code certainly shows first. A refused tap types nothing,
        changes nothing and keeps its buttons. True when refused."""
        if not isinstance(perm, dict) or not perm:
            return False
        verdict = sess.keys_verdict(perm, time.monotonic())
        if verdict == "yes":
            return False
        if verdict == "behind":
            log.info("[%s] tap refused: another prompt is on top", sess.label)
            await self._safe_answer(query, person.text(PROMPT_BEHIND))
        else:
            log.info("[%s] tap refused: which prompt is on top is not known",
                     sess.label)
            await self._safe_answer(query, person.text(PROMPT_ORDER_UNKNOWN))
        return True

    async def _inline_prompt_answered(self, sess, perm) -> bool:
        """The card's own prompt was answered: closed, and its buttons type
        nothing (8.102). With no other prompt open the card works again
        (BUSY, Working, its animation); with others open it shows the
        Waiting frame without answer buttons (roadmap 8.120). True when the
        session works again."""
        sess.close_prompts(lambda p: p is perm)
        if sess.pending_permission is perm:
            sess.pending_permission = None
        sess.mark_prompt_closed(sess.busy_msg_id)
        left = len(sess.open_prompts())
        if left:
            log.info("[%s] prompt closed (answered), %d still open",
                     sess.label, left)
            await self._after_prompts_changed(sess, {"inline_closed": True})
            return False
        # Transition back to BUSY and restart animation
        self.registry.transition(sess.name, Status.BUSY)
        keyboard = self._build_stop_keyboard(sess)
        text = self._build_busy_text(sess.label, "Working", sess)
        await self._edit_busy_raw(
            sess.busy_msg_id, text, reply_markup=keyboard,
            chat_id=resolve_chat_id(sess))
        self._start_animation(sess)
        return True

    async def _record_prompt_answer(self, sess, perm: dict, verb: str, member,
                                    via: str, *, decision: str | None = None,
                                    reply_to: int | None = None) -> None:
        """The record of an answered prompt, the same for the inline prompt
        and a separate-message one (roadmap 8.99): the collapsed row in
        the tool history, the audit-log record, and the attributed line in
        the session's chat ("✅ x1 · Allowed · @bob · Write: ...").
        Threaded under the busy card, else under *reply_to*.

        *decision* is the effective action id (``"allow"``,
        ``"allow_always"``, ``"deny"``, ``"continue"``, or None): it alone
        decides ``denied`` and the icon. *verb* is what is shown and
        audited as the action, and may be reworded or translated."""
        if perm.get("ask_question"):
            audit_detail = (perm.get("question") or "?")[:80]
            collapsed = f"❓ {audit_detail[:40]} → {verb}"
            audit_tool_name = "AskUserQuestion"
        else:
            audit_detail = (perm.get("tool_summary") or "Permission")[:80]
            collapsed = f"🔑 {audit_detail[:60]} → {verb}"
            audit_tool_name = (perm.get("tool_info") or {}).get("name", "")
        sess.record_tool(collapsed, True)

        # Persistent audit trail to disk (jsonl).
        from aipager import audit as audit_mod
        actor = (
            member if member is not None and member.id != 0 else None
        )
        audit_mod.append(
            session=sess.name, label=sess.label, action=verb,
            tool=audit_tool_name, summary=audit_detail,
            user_id=actor.id if actor else None,
            username=actor.label if actor else "",
            scope_label=self._scope_label(sess.scope_chat_id),
            scope_chat_id=sess.scope_chat_id or None,
            # Any refusal, tap-driven or rule-driven (docs/security.md).
            denied=(decision == "deny"),
            via=via,
        )

        # Audit reply in chat — persistent record of the decision
        # the user just made. Threaded under the busy message so
        # the scrollback reads as a conversation.
        audit_icon = _DECISION_ICONS.get(decision, "·")
        # "· @bob", never "by @bob": a translated line then needs no
        # grammatical gender for the person (multilingual design). The
        # verb shown comes from the action id; the audit above keeps the
        # English verb.
        tr = i18n.for_sess(sess)
        verb_shown = (tr.html(ANSWER_VERBS[decision]) if decision in ANSWER_VERBS
                      else card_rows.verb_text(tr, verb, "html"))
        params = {"icon": Safe(audit_icon), "session": sess.label, "verb": Safe(verb_shown),
                  "detail": audit_detail}
        if actor:
            params["person"] = Command(attribution_label(actor))
        anchor = (sess.busy_msg_id if sess.busy_msg_id and sess.busy_msg_id > 0
                  else reply_to)
        try:
            await send_text(self._app.bot,
                resolve_chat_id(sess),
                tr.html(ANSWER_LINE if actor else ANSWER_LINE_ANON, **params),
                parse_mode="HTML",
                reply_to_message_id=anchor or None,
            )
        except Exception:
            log.debug("[%s] audit message send failed", sess.label,
                      exc_info=True)
