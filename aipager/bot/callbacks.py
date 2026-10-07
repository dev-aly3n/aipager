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

from aipager.bot import card_owner, held_message, new_flow, session_parity, tap_gate, update_flow
from aipager.dtach import hook_reply, inject

from aipager import preferences
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
from aipager.bot.dashboard import PINNED_ANSWER_VERB, current_prompt_token
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


def _switch_refused(outcome) -> str:
    """Why a mode switch did not happen, for the reasons left after
    still_stopping and launch_failed, which say their own."""
    label = html_mod.escape(outcome.label)
    if outcome.reason == "already_restarting":
        return (f"⚠️ <b>{label}</b> is restarting right now - mode not changed. "
                "Try again in a moment.")
    return f"⚠️ <b>{label}</b> is not running - mode not changed."


def _stopped_line(outcome, *, html: bool = True) -> str:
    """`⏹ Stopped x1`, with the queued messages it dropped: what every
    Stop says (a toast is plain text, a message HTML). In a group, who
    stopped it (`outcome.by`, roadmap 8.91c): `⏹ Stopped x1 by @bob`."""
    label = html_mod.escape(outcome.label) if html else outcome.label
    line = f"⏹ Stopped <b>{label}</b>" if html else f"⏹ Stopped {label}"
    by = getattr(outcome, "by", "")
    if isinstance(by, str) and by:
        line += f" by {html_mod.escape(by) if html else by}"
    if outcome.dropped:
        line += (f" ({outcome.dropped} queued message"
                 f"{'s' if outcome.dropped > 1 else ''} discarded)")
    return line


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

    async def _do_perms_switch_via_fn(self, sess, target_skip_perms: bool,
                                      edit_fn, *,
                                      tapped_msg_id: int | None = None,
                                      turn: int | None = None,
                                      chat_id=None, announce: bool = True,
                                      by: str = ""):
        """Kill + poll + relaunch with toggled skip_perms, showing each step
        through ``edit_fn`` (an async ``(text, **kw)``: the card being
        edited in place) and ending in the /mode card: the new mode and the
        opposite switch, or what went wrong above the current one.

        Thin wrapper around :meth:`SessionOpsMixin._perms_switch_core` —
        the single kill/poll/relaunch seam chat and the Mini App now
        both go through (design.md ORCHESTRATOR OVERRIDE).

        ``turn``: the turn the card was shown for (its pending record's);
        it decides whether the tap is current. Without one, the tapped
        message id is compared with the busy card's, which an older card
        edited in place would fail. ``announce``: show "Switching…" first
        (the caller may have already). ``by``: who switched it, named
        above the card in a group (roadmap 8.91c; ``""`` in a DM). Returns
        the outcome, or None when the tap was refused as stale.
        """
        label = html_mod.escape(sess.label)
        if not (sess.tap_is_for_this_turn(None, turn=turn) if turn is not None
                else sess.tap_is_for_this_turn(tapped_msg_id)):
            # A card offered while the session was idle can be tapped much
            # later, by which time a different turn is running — and this
            # path kills and relaunches it.
            text, kb = self._mode_card(chat_id, sess, (
                f"⚠️ <b>{label}</b> moved on to new work, so nothing changed."))
            await edit_fn(text, parse_mode="HTML", reply_markup=kb)
            return None
        if announce:
            # The buttons go at once: a second tap during the relaunch has
            # nothing left to press.
            await edit_fn(f"⚙️ Switching <b>{label}</b> to "
                          f"{'🤖 Auto' if target_skip_perms else '💬 Ask'} mode…",
                          parse_mode="HTML")
        outcome = await self._perms_switch_core(sess, target_skip_perms)

        if outcome.ok:
            note = (f"⚙️ Switched to {'🤖 Auto' if target_skip_perms else '💬 Ask'} "
                    f"by {html_mod.escape(by)}." if by else "")
            log.info("[%s] perms switched to skip_perms=%s", sess.label, target_skip_perms)
        elif outcome.reason == "still_stopping":
            note = f"⚠️ <b>{label}</b> is still stopping - mode not changed."
        elif outcome.reason == "launch_failed":
            note = f"❌ Couldn't switch mode: {html_mod.escape(outcome.err)}"
        else:
            # not_live / already_restarting: never silence (a refused
            # switch used to leave its card and buttons as they were).
            note = _switch_refused(outcome)
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

        if not parts or parts == ["back"]:
            text, kb = render_settings_root(chat_id or 0)
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

        if len(parts) == 1:
            section = parts[0]
            rendered = render_settings_section(chat_id or 0, section)
            if rendered is None:
                await self._safe_answer(query, "Invalid callback")
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
                await self._safe_answer(query, "Invalid callback")
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
                    query, "Only an admin can change settings in this group.",
                    show_alert=True,
                )
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
                await self._safe_answer(query, "Invalid value")
                return

            text, kb = render_settings_section(chat_id or 0, section)
            # In a group, who changed it (roadmap 8.91c); a DM is as before.
            tapper = getattr(query, "from_user", None)
            by = self._actor_label(getattr(tapper, "id", None), chat_id, tapper)
            if by:
                text += f"\n\n<i>Changed by {html_mod.escape(by)}.</i>"
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        await self._safe_answer(query, "Invalid callback")

    async def _stop_the_turn_shown(self, update: Update, query, session_name: str,
                                   turn: int, *, again: str):
        """Stop *session_name* from a button carrying the turn it was shown
        for (/status's Stop, the /stop picker), answering the tap.

        Returns ``(looked, outcome)``: ``looked`` is False when the tap is
        refused outright (no such session, no right to prompt, another
        chat's session); ``outcome`` is None when a new turn started since,
        which is not stopped: the button was for the work it listed."""
        sess = self.registry.get(session_name)
        if not sess:
            await self._safe_answer(query, "Session not found")
            return False, None
        chat = calling_chat_id(update)
        if not self._can_prompt_user(
                getattr(getattr(query, "from_user", None), "id", None), chat):
            await self._safe_answer(query, "You can't stop this session.")
            return False, None
        if session_foreign_to(sess, chat):
            await self._safe_answer(query, "That session isn't running here.")
            return False, None
        if sess.status == Status.GONE:
            await self._safe_answer(query, "That session has ended.")
            return True, None
        if not sess.tap_is_for_this_turn(None, turn=turn):
            await self._safe_answer(query, f"{sess.label} moved on to new work - {again}")
            return True, None
        tapper = getattr(query, "from_user", None)
        outcome = await self._stop_session_core(sess, by=self._actor_label(
            getattr(tapper, "id", None), chat, tapper))
        # The count stays visible (tester-iter1-001 of /stop).
        await self._safe_answer(query, _stopped_line(outcome, html=False) if outcome.ok
                                else f"{sess.label} is not working")
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
                await self._safe_answer(query, tap_gate.OTHER_CHAT_TEXT)
                return False
        perms_target_auto = False
        if action in ("perms_confirm", "perms_stop_switch"):
            # The card's own record, matched exactly as the perms branch
            # matches it below; a switch to Auto needs an admin TAPPING it.
            pending = self._perms_pending.get(session_name)
            tapped = getattr(getattr(query, "message", None), "message_id", None)
            perms_target_auto = bool(
                pending is not None
                and pending.get("msg_id") in (None, tapped)
                and pending.get("target_skip_perms"))
        cap = tap_gate.required_capability(
            session_name, action, perms_target_auto=perms_target_auto)
        user_id = getattr(getattr(query, "from_user", None), "id", None)
        if self._member_can(member, cap, user_id, chat_id):
            return True
        log.info("tap refused: user %s in chat %s lacks %s for %s:%s",
                 user_id, chat_id, cap.value, session_name, action)
        # An update-level refusal reads as /update's own does: installing
        # the voice extra or updating both need the update admin.
        await self._safe_answer(
            query,
            update_flow.DENIED_TEXT if cap is tap_gate.UPDATE else tap_gate.REFUSED_TEXT,
            show_alert=True)
        return False

    async def _dispatch_callback(self, update: Update, query, member,
                                 cb_data: str, original_text: str) -> None:
        """The body of :meth:`_handle_callback`, after authorization."""
        if ":" not in cb_data:
            await self._safe_answer(query, "Invalid callback")
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
            await self._safe_answer(query, "That session is no longer available")
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
            session_name, action)
        if toast is not None:
            await self._safe_answer(query, toast)
            return

        # All three return False unless the callback belongs to their own
        # namespace, so every pre-existing callback below is unaffected.
        # `_:up:` (self-update) re-checks the admin rule on every tap.
        if await update_flow.handle_callback(self, update, query, session_name, action):
            return
        if await new_flow.handle_callback(self, update, query, session_name, action):
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
                await self._safe_answer(query, "Session not found")
                return
            await self._pinned_answer(query, sess)
            return

        if action == "stop":
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, "Session not found")
                return
            # Staleness is checked inside `_stop_session` (the seam every
            # caller shares), not here — a Stop button outlives its turn
            # whenever the edit that strips its keyboard fails, and gating
            # per-caller is how the perms and restart paths were missed.
            outcome = await self._stop_session(sess, update=update, query=query)
            if not outcome.ok and outcome.reason != "stale":
                # A stale tap already explained itself at the seam; adding
                # "is not busy" on top would be both wrong and louder.
                await self._safe_answer(query, f"{sess.label} is not working")
            return

        if action.startswith("ststop") and action[6:].isdigit():
            # /status's ⏹ Stop (4.2): stop the turn the list showed, then
            # redraw the list in place (never overwrite it with one line).
            looked, _outcome = await self._stop_the_turn_shown(
                update, query, session_name, int(action[6:]),
                again="tap ⏹ Stop again to stop it")
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
                update, query, session_name, int(action[5:]), again="send /stop again")
            if outcome is not None and outcome.ok:
                # The picker is now the result: nobody's card (8.94j).
                card_owner.release(self, calling_chat_id(update),
                                   getattr(query.message, "message_id", None))
                try:
                    await edit_text(query, _stopped_line(outcome), parse_mode="HTML")
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
            await self._safe_answer(query, "This button is out of date - send /kill again")
            return

        if action == "kill-cancel":
            card_owner.release(self, calling_chat_id(update),
                               getattr(query.message, "message_id", None))
            try:
                await edit_text(query,
                    "↩️ Cancelled. Nothing was ended.",
                )
            except Exception:
                pass
            return

        # ---- Voice-extra remote-install flow (item 5.3 follow-up) ----
        if session_name == "__voice__":
            if action == "cancel":
                try:
                    await edit_text(query,
                        "↩️ OK, voice not installed."
                    )
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
                await self._safe_answer(query, "Session not found")
                return
            if not sess.last_prompt:
                await self._safe_answer(query, "Nothing to retry")
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
                    query, "Telegram is rate-limiting this chat - try again "
                    f"after {until}")
                return
            if not await inject.is_alive(session_name):
                await self._safe_answer(query, f"Session '{session_name}' not alive")
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
                await self._safe_answer(
                    query, "Another person's turn is running - retry when "
                    "it ends")
                try:
                    await edit_message(query.message,
                        f"{original_text}\n\n⏸ Not retried - another "
                        "person's turn is running. Tap Retry again when it "
                        "ends.",
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
                await self._safe_answer(
                    query, "Answer the open prompt first, then retry")
                # A toast is gone the moment the operator looks away, and
                # tapping Retry is exactly the sort of thing done on the way
                # out. Leave the reason on the card itself, keeping the
                # button so it can be tapped again once the prompt is
                # answered. `last_prompt` is untouched, so nothing is lost.
                try:
                    await edit_message(query.message,
                        f"{original_text}\n\n⏸ Not retried - a prompt is "
                        "open. Answer it, then tap Retry again.",
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
                await self._safe_answer(query, f"Retrying [{sess.label}]")
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
                    NEEDS_ADMIN_REPLY
                    if self._command_needs_admin(
                        prompt, tapper, chat_id=self._attribution_chat(
                            sess, calling_chat_id(update)))
                    else RETRY_OTHERS_COMMAND_REPLY)
            else:
                await self._safe_answer(query, "Failed to retry")
            return

        if action == "compact":
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, "Session not found")
                return
            if not await inject.is_alive(session_name):
                await self._safe_answer(query, f"Session '{session_name}' not found")
                return
            ok = await self._inject_prompt(sess, "/compact")
            if ok:
                await self._safe_answer(query, f"Compacting [{sess.label}]")
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
                await self._safe_answer(query, NEEDS_ADMIN_REPLY)
            else:
                await self._safe_answer(query, "Failed to send /compact")
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
                await edit_text(query, "Cancelled.")
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
            if hidden:
                self.registry.mark_dirty()
                await self._safe_answer(
                    query, f"Cleared {len(hidden)} from /status (still in /resume)")
                log.info("Hid gone sessions from /status: %s", hidden)
            else:
                await self._safe_answer(query, "Nothing to clear")
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

            async def _show_card(note: str = "") -> None:
                """Back to the /mode card (the current mode, a fresh switch)."""
                if sess is None:
                    text, kb = (note or "↩️ Cancelled."), None
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
                    query, f"{sess.label} is restarting right now - tap again in a moment")
                return

            if pending is None:
                # Which mode this card was for is not known any more (it
                # was used, a newer card replaced it, or the daemon
                # restarted). Never guess: it used to default to Ask, so a
                # "Switch to Auto?" card relaunched the session in Ask. It
                # becomes the current card, so a tap on it is a fresh one.
                await self._safe_answer(query, "This card is out of date - nothing changed")
                await _show_card("⚠️ That card was out of date, so nothing changed.")
                return
            target_skip_perms = pending["target_skip_perms"]

            if sess is None:
                _consume()
                try:
                    await edit_text(query, "⚠️ Session not found.")
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

            # Confirmed (Ask→Auto on an idle session) or Stop & switch (a
            # busy one; the core interrupts first). Whatever happens next
            # edits this card, its buttons going first, so its record is
            # spent. The record's turn decides whether the tap is current.
            _consume()
            tapper = getattr(query, "from_user", None)
            await self._do_perms_switch_via_fn(
                sess, target_skip_perms, _edit, tapped_msg_id=tapped,
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
                    await edit_text(query, "↩️ Cancelled.")
                except Exception:
                    pass
                return

            skip_perms_override = (action == "resume_mode_auto")

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
            # Auto needs an admin: the tap gate requires MANAGE for
            # `resume_mode_auto` before this branch runs.
            await self._do_resume(
                label=label, reply_fn=_reply,
                update=update, query=query, sess=sess,
                skip_perms_override=skip_perms_override,
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
            # it is then refused Auto below (no author is an admin); a
            # Resume needs the tapper to be an admin for Auto
            # (`_resume_auto_gate`, roadmap 8.83).
            tapper = getattr(getattr(query, "from_user", None), "id", None)
            author = (pending or {}).get("user_id")
            if author and tapper != author:
                await self._safe_answer(
                    query, "Only the person who sent /new can use this card.",
                    show_alert=True)
                return
            if action != "new_cancel" and not self._can_prompt_user(
                    tapper, calling_chat_id(update)):
                await self._safe_answer(
                    query, "You can't start or resume sessions here.",
                    show_alert=True)
                return

            if action == "new_cancel":
                self._new_conflict_pending.pop(session_name, None)
                try:
                    await edit_text(query,
                        "↩️ Cancelled - no session changed.",
                    )
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
                await self._safe_answer(query, "Session not found")
                return

            prompt = (pending or {}).get("prompt", "")
            skip_perms = (pending or {}).get("skip_perms", False)
            if skip_perms and not self._is_admin_user(
                    (pending or {}).get("user_id"), calling_chat_id(update)):
                # Auto is re-checked at creation, like every other start:
                # the card may be old, and its author since demoted.
                skip_perms = False
            # The queued prompt is the text the /new AUTHOR typed, so it
            # runs as the author, never as whoever tapped the button: a
            # tapper's rights must not be lent to someone else's prompt
            # (the same rule as Retry; review 2026-09-27). 0 means the
            # author is unknown, which gets the floor at pick-up.
            prompt_author = (pending or {}).get("user_id") or None

            if action == "new_resume":
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
                            f"↩️ Switched to <b>{html_mod.escape(label)}</b>"
                            + ("\n📝 Prompt queued" if prompt else ""),
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
                    await self._safe_answer(
                        query, "This card has expired. Send /new again.",
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
                        query, f"{sess.label} started working since. "
                               "Send /new again to replace it.",
                        show_alert=True)
                    return
                # The fresh session's folder must be one its author may
                # start in (roadmap 8.79), checked before the old one is
                # killed: create_session would refuse it only afterwards.
                from aipager.miniapp import launch as launch_rules
                refusal = launch_rules.launch_folder_refusal(
                    (pending or {}).get("cwd") or None,
                    self._is_confined_user(prompt_author,
                                           sess.scope_chat_id or None))
                if refusal:
                    await self._safe_answer(query, refusal, show_alert=True)
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
                    await edit_text(query,
                        f"🚀 Starting <b>{html_mod.escape(label)}</b> "
                        f"(fresh)…",
                        parse_mode="HTML",
                    )
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
                    skip_perms=skip_perms,
                    cwd=(pending or {}).get("cwd") or None,
                    driver_user_id=prompt_author,
                    model=(pending or {}).get("model") or None,
                )
                if not new_name:
                    try:
                        await edit_text(query, f"❌ {html_mod.escape(err)}",
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
            await self._safe_answer(query, f"Unknown: {action}")
            return

        sess = self.registry.get(session_name)
        if not sess:
            await self._safe_answer(query, "Session not found")
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
        current_token = current_prompt_token(sess)
        refusal = None
        if is_resent_copy:
            # Kept, not popped: the entry IS the guard, and the message
            # keeps its buttons if the edit below does not land. A missing
            # token on either side never matches (fail closed).
            if (surface[1] is None or current_token is None
                    or surface[1] != current_token):
                refusal = "already answered"
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
            tapped_id = resent_key[1]
            card = sess.busy_msg_id
            if isinstance(tapped_id, int) and not isinstance(tapped_id, bool):
                if sess.pending_permission:
                    if card and card > 0 and tapped_id != card:
                        refusal = "this prompt has expired"
                else:
                    # A separate-message prompt is always registered when
                    # it is sent, and again when a restart restores it
                    # (8.102); an unregistered message is not it.
                    refusal = "this prompt has expired"
        if refusal is not None:
            await self._safe_answer(query, refusal)
            try:
                await edit_markup(query, reply_markup=None)
            except Exception:
                pass
            return

        if not await inject.is_alive(session_name):
            await self._safe_answer(query, f"Session '{session_name}' not found")
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
        if not perm and is_resent_copy:
            separate_perm = (sess.pending_prompt_msg or {}).get("perm") or None
            perm = separate_perm or {}
        # Taken before any key is typed: the keystroke fallback's watch
        # reads only what Claude wrote after it (roadmap 8.99).
        answered_at = time.time()
        # What Claude's own queue held before a refusal was typed (the
        # watch's /stop-like end needs it; read before the keys).
        claude_held: list[dict] = []
        answer_text = None  # a branch may override the "<verb> [label]" toast
        # design.md "answer PermissionRequest hooks with a decision
        # instead of keystrokes": allow/allow_always/deny set this to
        # "hook_decision" or "keystroke_fallback"; every other action
        # (opt<N>, submit, continue, ...) leaves it empty — audit.append's
        # own `via` kwarg defaults to "" too, so this changes nothing for
        # them.
        via = ""
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

                opt_label = perm["options"][option_index].get("label", f"Option {option_index+1}")
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
                await self._safe_answer(query, "Failed to send keys")
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
                await self._safe_answer(query, f"✅ {verb[:180]}")

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
                by_attr = f" by {attribution_label(actor)}" if actor else ""
                try:
                    await send_text(self._app.bot,
                        resolve_chat_id(sess),
                        f"✓ <b>{html_mod.escape(sess.label)}</b> · "
                        f"Answered{html_mod.escape(by_attr)} · "
                        f"{html_mod.escape(question_text[:80])} → "
                        f"{html_mod.escape(verb[:80])}",
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
                    sess.pending_permission = None
                    self.registry.transition(session_name, Status.BUSY)
                    keyboard = self._build_stop_keyboard(sess)
                    text = self._build_busy_text(sess.label, "Working", sess)
                    await self._edit_busy_raw(
                        sess.busy_msg_id, text, reply_markup=keyboard,
                        chat_id=resolve_chat_id(sess))
                    self._start_animation(sess)
                    log.info("[%s] Multi-select submit complete", sess.label)
            else:
                await self._safe_answer(query, "Failed to send keys")
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
                else:
                    verb = ACTION_VERBS["allow"]
                    answer_text = (
                        f"No always-rule for this command - allowed once [{sess.label}]"
                    )
                    log.info("[%s] allow_always degraded to a single allow "
                             "(always_available=%r)", sess.label,
                             perm_info.get("always_available"))
            elif perm_info.get("always_available") is True:
                via = "keystroke_fallback"
                verb = ACTION_VERBS[action]
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
                answer_text = (
                    f"No always-rule for this command - allowed once [{sess.label}]"
                )
                log.info("[%s] allow_always degraded to a single allow "
                         "(always_available=%r)", sess.label,
                         perm_info.get("always_available"))
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "deny":
            verb = ACTION_VERBS[action]
            hook_answered = False
            if (perm.get("tool_info") or {}).get("name") != "AskUserQuestion":
                actor_for_reason = (
                    member if member is not None and member.id != 0 else None
                )
                reason = (
                    f"Denied via aipager by {attribution_label(actor_for_reason)}"
                    if actor_for_reason else "Denied via aipager"
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
            ok = await inject.send_keys(session_name, "Enter")
        else:
            verb = action

        if ok:
            await self._safe_answer(query, answer_text or f"{verb} [{sess.label}]")

            if is_resent_copy and sess.pending_permission:
                # Answered from the bar's copy of an inline prompt: the
                # busy card is updated below as usual, and the copy shows
                # the verdict instead of its buttons. (A separate message
                # is edited by its own branch below.) Its registry entry
                # stays: the prompt it was bound to is over, so any later
                # tap on it is refused above.
                try:
                    await edit_text(query, f"{original_text}\n\n→ {verb}")
                except Exception:
                    pass

            if sess.pending_permission:
                # Collapse current question into tool_history, write the
                # audit record and post the attributed line.
                perm = sess.pending_permission
                await self._record_prompt_answer(sess, perm, verb, member, via)

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
                    sess.pending_permission = None
                    # Transition back to BUSY and restart animation
                    self.registry.transition(session_name, Status.BUSY)
                    keyboard = self._build_stop_keyboard(sess)
                    text = self._build_busy_text(sess.label, "Working", sess)
                    await self._edit_busy_raw(
                        sess.busy_msg_id, text, reply_markup=keyboard,
                        chat_id=resolve_chat_id(sess))
                    self._start_animation(sess)
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
                # Mark session as busy after user interaction
                self.registry.transition(session_name, Status.BUSY)
                log.info("[%s] %s (via=%s)", sess.label, verb, via or "n/a")
                try:
                    await edit_text(query, f"{original_text}\n\n→ {verb}")
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
                        sess, separate_perm, verb, member, via,
                        reply_to=getattr(query.message, "message_id", None))
            if via == "keystroke_fallback":
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
            await self._safe_answer(query, f"Failed to send to {session_name}")

    async def _record_prompt_answer(self, sess, perm: dict, verb: str, member,
                                    via: str, *, reply_to: int | None = None) -> None:
        """The record of an answered prompt, the same for the inline prompt
        and a separate-message one (roadmap 8.99): the collapsed row in
        the tool history, the audit-log record, and the attributed line in
        the session's chat ("✅ x1 · Allowed by @bob · Write: ...").
        Threaded under the busy card, else under *reply_to*."""
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
            denied=(verb == ACTION_VERBS["deny"]),
            via=via,
        )

        # Audit reply in chat — persistent record of the decision
        # the user just made. Threaded under the busy message so
        # the scrollback reads as a conversation.
        audit_icon = {
            ACTION_VERBS["allow"]: "✅",
            ACTION_VERBS["allow_always"]: "🟢",
            ACTION_VERBS["deny"]: "🚫",
            ACTION_VERBS["continue"]: "▶️",
        }.get(verb, "·")
        by_attr = f" by {attribution_label(actor)}" if actor else ""
        anchor = (sess.busy_msg_id if sess.busy_msg_id and sess.busy_msg_id > 0
                  else reply_to)
        try:
            await send_text(self._app.bot,
                resolve_chat_id(sess),
                f"{audit_icon} <b>{html_mod.escape(sess.label)}</b> · "
                f"{verb}{html_mod.escape(by_attr)} · "
                f"{html_mod.escape(audit_detail)}",
                parse_mode="HTML",
                reply_to_message_id=anchor or None,
            )
        except Exception:
            log.debug("[%s] audit message send failed", sess.label,
                      exc_info=True)
