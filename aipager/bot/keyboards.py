"""Telegram bot — python-telegram-bot v22 async Application.

Single owner of all Telegram communication. Handles:
- CallbackQuery (button taps) → dtach_inject.send_keys()
- Message replies → dtach_inject.send_text_and_enter()
- /status command → show all sessions
- /<label> <prompt> → direct send to session
"""

from __future__ import annotations

import html as html_mod
import logging
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from telegram.error import BadRequest, Forbidden


from aipager import i18n
from aipager.bot import kbwords, miniapp_link
from aipager.i18n import Msg, Safe
from aipager import perm_modes
from aipager.bot.mode_labels import mode_label
from aipager.config import (
    CHAT_ID,
    MODEL_CHOICES,
    QUICK_COMMANDS, QUICK_TEMPLATES,
)

# Pure-function helpers and constants live in aipager.bot.transport
# now. Re-export the names this module uses internally so the
# TelegramBot class body below (and any external consumers like the
# tests) keeps working without changes.
from aipager.bot import group_intake
from aipager.bot.flood import _key as _chat_key
from aipager.bot.transport import (  # noqa: F401
    MUTED,
    SKIPPED,
    calling_user_id,
    reply_text,
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
    resolve_chat_id_int,
)

if TYPE_CHECKING:
    from aipager.state import TrackedSession

log = logging.getLogger(__name__)

STOP_PLAIN = Msg("perm.btn.stop_plain", "Stop",
                 note="The busy card's button: stop what Claude is doing")
ALLOW = Msg("perm.btn.allow", "✅ Allow", note="Permission prompt button: let the tool run once")
DENY = Msg("perm.btn.deny", "❌ Deny", note="Permission prompt button: refuse the tool call")
ALLOW_ALWAYS = Msg("perm.btn.allow_always", "🟢 Allow always",
                   note="Permission prompt button: allow, and save a rule for next time")
STOP = Msg("perm.btn.stop", "⏹ Stop", note="Prompt button: stop what Claude is doing")
SUBMIT = Msg("perm.btn.submit", "✅ Submit",
             note="Multiple-choice question: send the ticked options")
OPTION = Msg("perm.btn.option", "Option {n}",
             note="A question's option that has no label of its own", eg={"n": 2})
DEFAULT_MODE = Msg("perm.btn.default_suffix", "{mode} (default)",
                   note="A mode button marked as the session's saved default; {mode} is "
                        "the Ask, Auto or Full access mode label", eg={"mode": "Auto mode"})
YES_SWITCH = Msg("perm.btn.yes_switch", "✅ Yes, switch",
                 note="Confirms switching a session to Full access mode")
CANCEL = Msg("perm.btn.cancel", "↩️ Cancel", note="Cancels the switch or the resume")
STOP_SWITCH = Msg("perm.btn.stop_switch", "🛑 Stop task & switch",
                  note="Stops the running turn, then switches the mode")
NOT_NOW = Msg("perm.btn.not_now", "⏳ Not now", note="Leaves the mode as it is for now")
RETRY = Msg("perm.btn.retry", "🔄 Retry", note="Sends the last prompt again after an error")
COMPACT_NOW = Msg("perm.btn.compact_now", "📦 Compact Now",
                  note="Asks Claude Code to compact the conversation now")
NO_QUESTIONS = Msg("perm.no_questions", "❓ <b>{session}</b> · No questions",
                   note="A question prompt that came without its questions",
                   eg={"session": "x1"})
NEEDS_INPUT = Msg("perm.needs_input", "🔐 <b>{session}</b> · Needs input",
                  note="A prompt in Claude Code's terminal with options to pick",
                  eg={"session": "x1"})
PERM_NEEDED = Msg("perm.needed", "Permission needed",
                  note="A permission prompt with no tool details")
SEPARATE_PERM = Msg("perm.separate", "🔐 <b>{session}</b> · Permission needed",
                    note="A permission prompt sent as its own message; the tool call follows",
                    eg={"session": "x1"})
LOADING = Msg("perm.loading", "{tool} (loading…)",
              note="A question prompt whose question has not arrived yet; {tool} is "
                   "the tool's name", eg={"tool": "AskUserQuestion"})





class KeyboardMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    @staticmethod
    def _build_button_rows(labels: list[str], per_row: int = 3,
                           chat_id: int | None = None) -> list[list[KeyboardButton]]:
        """Pack labels into rows of KeyboardButtons (marked in a group,
        roadmap 8.91f)."""
        rows = []
        for i in range(0, len(labels), per_row):
            rows.append([KeyboardButton(group_intake.mark_label(lbl, chat_id))
                         for lbl in labels[i:i + per_row]])
        return rows

    def _keyboard_level_key(self, chat_id, user_id: int | None = None):
        """Where a keyboard level is kept (roadmap 8.91b): per chat, and in
        a group per member, since a group member's sub-keyboards are
        their own (selective). The chat key is the flood module's, so
        ``"123"`` and ``123`` are one chat."""
        target = chat_id if chat_id is not None else CHAT_ID
        key = _chat_key(target)
        if user_id is not None and group_intake.is_group_chat(target):
            return (key, user_id)
        return key

    def _keyboard_level_for(self, chat_id, user_id: int | None = None) -> str:
        """The keyboard this chat (a group: this member) last got."""
        levels = self._keyboard_levels
        return levels.get(self._keyboard_level_key(chat_id, user_id), "main")

    async def _send_keyboard(
        self, level: str | None = None, chat_id: int | None = None,
        *, tap=None,
    ) -> None:
        """Send a message with the persistent keyboard.

        Clears any deferred-keyboard hold as a side effect: once a
        keyboard has actually gone out, the hold has nothing left to
        protect, and releasing it later would send a second one. This
        matters for the paths that reach here WITHOUT going through
        ``_update_bot_commands``'s hold check — ``/start`` most of all,
        which a user can send at any point during the up-to-45s window.

        Args:
            level: Which keyboard to show — "main", "templates", "commands",
                   or "models".  Defaults to the level this chat (in a
                   group with a ``tap``, this member) last got.
            chat_id: Target chat (defaults to the global ``CHAT_ID``). In
                   multi-scope mode the main keyboard's session buttons are
                   filtered to this chat's scope, so it never shows another
                   scope's labels.
            tap: The update of the member's keyboard tap that asked for
                   this keyboard. In a group the keyboard is sent as a
                   reply to that message with ``selective=True``, so only
                   that member's keyboard changes (roadmap 8.91b). Ignored
                   in a private chat, whose keyboard is sent as before.
        """
        if not self._app:
            return

        # Resolved up front because the main keyboard needs to know the
        # chat type before it builds its rows. A positive Telegram chat
        # id IS the peer's user id, i.e. a private chat; groups and
        # channels are negative. That matters here because a `web_app`
        # button is private-chat-only, and Telegram rejects the WHOLE
        # keyboard if one is present in a group — costing the operator
        # every other button too, not just this one.
        target = chat_id if chat_id is not None else CHAT_ID
        try:
            is_private = int(target) > 0
        except (TypeError, ValueError):
            is_private = False
        # A group member's own keyboard (8.91b): a reply to their tap,
        # selective, its level kept for them alone. Everything else (a
        # private chat, the main keyboard aipager sends on its own) is the
        # chat's shared keyboard, as before.
        is_group = group_intake.is_group_chat(target)
        tap_msg = getattr(tap, "message", None) if (tap is not None and is_group) else None
        tapper = calling_user_id(tap) if tap_msg is not None else None
        personal = tapper is not None
        level_key = self._keyboard_level_key(target, tapper)
        if level is None:
            level = self._keyboard_levels.get(level_key, "main")

        # The chat's language, for a group member's own keyboard too: a
        # keyboard is the chat's, whoever asked for it.
        tr = i18n.for_chat(target)

        def button(label: str) -> KeyboardButton:
            return KeyboardButton(group_intake.mark_label(label, target))

        def shown(pairs) -> list[str]:
            return [kbwords.shown_label(tr, lbl, payload) for lbl, payload in pairs]

        if level == "templates":
            rows = self._build_button_rows(shown(QUICK_TEMPLATES), chat_id=target)
            rows.append([button(tr.text(kbwords.KB_BACK))])
            msg_text = tr.text(kbwords.KBMSG_TEMPLATES)
        elif level == "commands":
            rows = self._build_button_rows(shown(QUICK_COMMANDS), chat_id=target)
            rows.append([button(tr.text(kbwords.KB_MODELS)), button(tr.text(kbwords.KB_BACK))])
            msg_text = tr.text(kbwords.KBMSG_COMMANDS)
        elif level == "models":
            # Model labels are names: never translated.
            rows = self._build_button_rows([lbl for lbl, _ in MODEL_CHOICES], chat_id=target)
            rows.append([button(tr.text(kbwords.KB_BACK))])
            msg_text = tr.text(kbwords.KBMSG_MODELS)
        else:
            # Main keyboard: session labels + command/nav rows.
            # Multi-scope: only this chat's labels (and never leak others'
            # when no chat is known, e.g. a startup broadcast).
            if self.scopes is not None:
                label_src = (self.registry.live_labels(chat_id)
                             if chat_id is not None else set())
            else:
                label_src = self.registry.live_labels()
            labels = sorted(label_src)
            rows = []
            if labels:
                rows = self._build_button_rows(labels, chat_id=target)
            # `new` where `kill` was (4.7, 2026-09-30): the common action
            # up front, not a destructive one beside Stop. Ending a session
            # is in /status → ⋮, and `kill` still works when typed.
            rows.append([button(tr.text(kbwords.KB_STATUS)), button(tr.text(kbwords.KB_STOP)),
                         button(tr.text(kbwords.KB_NEW))])
            # Rides along on the existing nav row rather than claiming one
            # of its own: three across matches the status/stop/new row
            # above it, and the keyboard is already the busiest surface in
            # the chat.
            nav = [button(tr.text(kbwords.KB_TEMPLATES)), button(tr.text(kbwords.KB_COMMANDS))]
            if self._miniapp_url and is_private:
                nav.append(KeyboardButton(
                    kbwords.KB_APP,
                    web_app=WebAppInfo(url=miniapp_link.with_lang(self._miniapp_url, tr.code)),
                ))
            rows.append(nav)
            msg_text = "\u2328\ufe0f"

        levels = self._keyboard_levels
        touched = [level_key]
        if is_group and not personal:
            # The group's shared keyboard replaces every member's own.
            chat_key = _chat_key(target)
            touched += [k for k in levels
                        if isinstance(k, tuple) and k[0] == chat_key]
        prev_levels = {k: levels.get(k) for k in touched}
        prev_deferred = self._keyboard_deferred
        for key in touched:
            levels.pop(key, None)
        levels[level_key] = level

        if personal:
            keyboard = ReplyKeyboardMarkup(
                rows,
                resize_keyboard=True,
                selective=True,
            )
        else:
            keyboard = ReplyKeyboardMarkup(
                rows,
                resize_keyboard=True,
            )

        # Only the MAIN keyboard satisfies the hold. Tapping a stale
        # Templates/Commands/Models button after a restart also lands
        # here, and clearing on those levels would consume the hold
        # without the user ever receiving a main keyboard carrying the
        # App button — silently recreating the "buttonless until some
        # unrelated later event" bug this whole feature exists to fix.
        # `« Back` passes its parent level, so a Back-to-main does count.
        #
        # Cleared before the send, not after: the send's exceptions are
        # swallowed below, and a failed attempt should still count as
        # this keyboard having been attempted rather than leaving a hold
        # that fires a second one later.
        if level == "main" and not personal:
            self._keyboard_deferred = False
            # And whatever a mute owed this chat (8.17c): this send is
            # the one that pays it — or re-owes it, just below.
            self._keyboard_owed.pop(_chat_key(target), None)

        # The chat's shared main keyboard carries this chat's session
        # labels: noted as on its way (before the first await, so a
        # multi-scope refresh overlapping this send does not send the same
        # one again), and recorded as what the chat shows once it went out.
        record_chat = None
        if level == "main" and not personal:
            try:
                record_chat = int(target)
            except (TypeError, ValueError):
                record_chat = None
        inflight = getattr(self, "_keyboard_inflight", None)
        shown_labels = set(labels) if record_chat is not None else None
        if record_chat is not None and inflight is not None:
            inflight[record_chat] = shown_labels

        try:
            if personal:
                sent = await reply_text(tap_msg, msg_text, reply_markup=keyboard,
                                        do_quote=True)
            else:
                sent = await send_text(self._app.bot,
                    target, msg_text,
                    reply_markup=keyboard,
                )
            if (record_chat is not None
                    and sent is not MUTED and sent is not SKIPPED):
                self.registry.set_keyboard_labels(record_chat, shown_labels)
                # The chat is up to date: a failed refresh's mark is void.
                getattr(self, "_keyboard_sync_failed", {}).pop(record_chat, None)
            if sent is MUTED:
                # Flood-muted (roadmap 8.17b): nothing was attempted, so
                # the hold must survive. Clearing before the send is
                # deliberate for a *failed* attempt, but a main keyboard
                # suppressed for the length of a ban would otherwise never
                # be re-sent — exactly the "buttonless until some
                # unrelated later event" bug this feature exists to fix.
                # Restore both flags so the next trigger sends it once.
                # Only what this call wrote and nobody has changed since:
                # a member's tap handled during the await keeps its level.
                for key, prev in prev_levels.items():
                    wrote = level if key == level_key else None
                    if levels.get(key) != wrote:
                        continue
                    if prev is None:
                        levels.pop(key, None)
                    else:
                        levels[key] = prev
                self._keyboard_deferred = prev_deferred
                # Restoring the flags is not enough on its own (8.17c):
                # with no hold armed — a commands refresh, a Back to the
                # main keyboard — nothing would ever send this keyboard
                # again, and a restored URL hold is not released by the
                # lift either (it waits for the Mini App URL). A MAIN
                # keyboard the mute refused is owed to this chat, and the
                # session monitor's tick sends it after the lift
                # (`flush_owed_keyboards`). A sub-menu is the reply to a
                # tap made during the ban, withheld like any command
                # reply, and not worth re-sending hours later.
                if level == "main" and not personal:
                    self._keyboard_owed[_chat_key(target)] = chat_id
                return
        except Forbidden as e:
            _log_blocked_once(e)
        except BadRequest as e:
            if "chat not found" in str(e).lower():
                bot_user = (await self._app.bot.get_me()).username
                log.error(
                    "Cannot send to Telegram chat %s: %s\n"
                    "  → The bot @%s has never received a message from this chat.\n"
                    "  → Open https://t.me/%s in Telegram and tap Start, then\n"
                    "    the next message you send will let the daemon proceed.",
                    target, e, bot_user, bot_user,
                )
            else:
                log.warning("Failed to send keyboard: %s", e)
        except Exception as e:
            if _is_bot_blocked(e):
                _log_blocked_once(e)
            else:
                log.warning("Failed to send keyboard", exc_info=True)
        finally:
            if (record_chat is not None and inflight is not None
                    and inflight.get(record_chat) is shown_labels):
                del inflight[record_chat]

    def _build_ask_keyboard(self, sess: TrackedSession, label: str,
                            tool_input: dict) -> tuple[str, InlineKeyboardMarkup | None]:
        """Build message and buttons for AskUserQuestion."""
        tr = i18n.for_sess(sess)
        questions = tool_input.get("questions", [])
        if not questions:
            return tr.html(NO_QUESTIONS, session=label), None

        q = questions[0]
        question = q.get("question", "?")
        options = q.get("options", [])

        # The session's name and Claude's question: no words of ours.
        text = f"❓ <b>{html_mod.escape(label)}</b> · {html_mod.escape(question)}"
        if not options:
            return text, None

        for i, opt in enumerate(options):
            opt_label = opt["label"] if "label" in opt else tr.text(OPTION, n=i + 1)
            desc = opt.get("description", "")
            text += f"\n  {tr.num(i + 1)}. {html_mod.escape(opt_label)}"
            if desc:
                text += f" - {html_mod.escape(desc[:60])}"

        # Local import, not top-level — avoids an import cycle with
        # session_parity (which itself imports from this package).
        # Mirrors the precedent below at _build_resume_mode_keyboard;
        # do not "tidy" this into a module-level import.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        buttons = []
        for i, opt in enumerate(options[:4]):
            opt_label = opt["label"] if "label" in opt else tr.text(OPTION, n=i + 1)
            buttons.append(InlineKeyboardButton(
                opt_label,
                callback_data=session_parity.session_cb(self, chat_id, sess, f"opt{i}"),
            ))

        return text, InlineKeyboardMarkup([buttons])

    def _build_selector_keyboard(self, sess: TrackedSession, label: str,
                                  question: str,
                                  options: list[tuple[int, str]]) -> tuple[str, InlineKeyboardMarkup | None]:
        """Build message and buttons from pane-scraped selector."""
        tr = i18n.for_sess(sess)
        if question:
            text = f"❓ <b>{html_mod.escape(label)}</b> · {html_mod.escape(question)}"
        else:
            text = tr.html(NEEDS_INPUT, session=label)

        for num, opt_label in options:
            text += f"\n  {tr.num(num)}. {html_mod.escape(opt_label)}"

        # Local import — see the precedent + rationale at
        # _build_resume_mode_keyboard below; do not hoist to module top.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        buttons = []
        for num, opt_label in options[:4]:
            buttons.append(InlineKeyboardButton(
                opt_label[:20],
                callback_data=session_parity.session_cb(self, chat_id, sess, f"opt{num - 1}"),
            ))

        keyboard = InlineKeyboardMarkup([buttons]) if buttons else None
        return text, keyboard

    def _build_stop_keyboard(self, sess: TrackedSession) -> InlineKeyboardMarkup:
        # Local import — see the precedent + rationale at
        # _build_resume_mode_keyboard below; do not hoist to module top.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                i18n.for_sess(sess).text(STOP_PLAIN),
                callback_data=session_parity.session_cb(self, chat_id, sess, "stop")),
        ]])

    def _build_retry_keyboard(self, sess: TrackedSession) -> InlineKeyboardMarkup:
        # Local import — see the precedent + rationale at
        # _build_resume_mode_keyboard below; do not hoist to module top.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                i18n.for_sess(sess).text(RETRY),
                callback_data=session_parity.session_cb(self, chat_id, sess, "retry")),
        ]])

    def _build_compact_keyboard(self, sess: TrackedSession) -> InlineKeyboardMarkup:
        # Local import — see the precedent + rationale at
        # _build_resume_mode_keyboard below; do not hoist to module top.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                i18n.for_sess(sess).text(COMPACT_NOW),
                callback_data=session_parity.session_cb(self, chat_id, sess, "compact")),
        ]])

    def _build_permission_keyboard(
        self, sess: TrackedSession, *, always_available: bool | None = None,
    ) -> InlineKeyboardMarkup:
        """Permission buttons for an inline permission prompt.

        Row 0 = Allow / Deny. Row 1 = Allow always / Stop — but **Allow
        always exists only when the prompt carries a standing rule to
        widen** (``always_available is True``, from the hook's
        ``permission_suggestions``); otherwise row 1 is Stop alone. Since
        Claude Code 2.1.259 the dialog's second row without such a rule is
        "Yes, and switch to auto mode", which is what a blind Allow-always
        would select. ``None`` (the default) reads the flag from
        ``sess.pending_permission``; ``None``/unknown is treated as absent.
        """
        # Local import — see the precedent + rationale at
        # _build_resume_mode_keyboard below; do not hoist to module top.
        from aipager.bot import session_parity

        if always_available is None:
            always_available = (
                (sess.pending_permission or {}).get("tool_info") or {}
            ).get("always_available")
        chat_id = resolve_chat_id_int(sess) or 0
        tr = i18n.for_sess(sess)
        stop = InlineKeyboardButton(
            tr.text(STOP), callback_data=session_parity.session_cb(self, chat_id, sess, "stop"))
        rows = [
            [InlineKeyboardButton(
                tr.text(ALLOW),
                callback_data=session_parity.session_cb(self, chat_id, sess, "allow")),
             InlineKeyboardButton(
                tr.text(DENY),
                callback_data=session_parity.session_cb(self, chat_id, sess, "deny"))],
        ]
        if always_available is True:
            rows.append([
                InlineKeyboardButton(
                    tr.text(ALLOW_ALWAYS),
                    callback_data=session_parity.session_cb(self, chat_id, sess, "allow_always")),
                stop,
            ])
        else:
            rows.append([stop])
        return InlineKeyboardMarkup(rows)

    def _build_perms_confirm_keyboard(
        self, sess: TrackedSession,
    ) -> InlineKeyboardMarkup:
        """Confirmation keyboard for an IDLE switch into Full access."""
        # Local import — see the precedent + rationale at
        # _build_resume_mode_keyboard below; do not hoist to module top.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        tr = i18n.for_sess(sess)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                tr.text(YES_SWITCH),
                callback_data=session_parity.session_cb(self, chat_id, sess, "perms_confirm")),
             InlineKeyboardButton(
                tr.text(CANCEL),
                callback_data=session_parity.session_cb(self, chat_id, sess, "perms_cancel"))],
        ])

    def _build_perms_busy_keyboard(
        self, sess: TrackedSession,
    ) -> InlineKeyboardMarkup:
        """Keyboard for /perms on a BUSY session."""
        # Local import — see the precedent + rationale at
        # _build_resume_mode_keyboard below; do not hoist to module top.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        tr = i18n.for_sess(sess)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                tr.text(STOP_SWITCH),
                callback_data=session_parity.session_cb(self, chat_id, sess, "perms_stop_switch")),
             InlineKeyboardButton(
                tr.text(NOT_NOW),
                callback_data=session_parity.session_cb(self, chat_id, sess, "perms_wait"))],
        ])

    def _build_resume_mode_keyboard(
        self, sess: TrackedSession, saved_mode: str,
        *, chat_id: int = 0, may_full: bool = False,
    ) -> InlineKeyboardMarkup:
        """Mode-picker keyboard shown after a session is chosen to resume.

        Ask and Auto for everyone; Full access only when *may_full* (the
        resumer is an admin). "(default)" marks what a plain resume gives
        this person: *saved_mode*, or Auto in place of Full access for
        someone who may not use it.

        Callbacks carry the short per-chat index, not the session name:
        ``{name}:resume_mode_cancel`` measured 83 bytes for a
        maximum-length name — past Telegram's 64-byte cap, which used to
        make this keyboard un-renderable for exactly the sessions the
        64-byte work was about. The caller (session_parity.handle_callback,
        currently the only production caller) already resolves the
        session before calling this, so there is no "unknown session"
        case left to fall back for.

        Local import, not top-level: session_parity imports from this
        package (settings_menu → keyboards' own module tree), so a
        module-level ``import session_parity`` here risks a cycle that
        is not exercised by today's import order but would be the
        moment some other module's import graph shifts. Every other
        builder in this class that needs session_parity mirrors this
        same local-import shape — this is the original precedent the
        comments above point back to.
        """
        from aipager.bot import session_parity

        tr = i18n.for_chat(chat_id) if chat_id else i18n.for_sess(sess)

        def label(mode, default: bool) -> str:
            shown = tr.text(mode)
            return tr.text(DEFAULT_MODE, mode=Safe(shown)) if default else shown

        default = perm_modes.narrowest_allowed(saved_mode, may_full)

        # One mode per row: a label names the mode as a mode ("💬 Ask mode
        # (default)", "💬 Режим «Спрашивать» (по умолчанию)"), too wide for
        # half a row, and the end Telegram would cut is "(default)".
        rows = [
            [InlineKeyboardButton(
                label(mode_label(perm_modes.ASK), default == perm_modes.ASK),
                callback_data=session_parity.session_cb(self, chat_id, sess, "resume-ask"))],
            [InlineKeyboardButton(
                label(mode_label(perm_modes.AUTO), default == perm_modes.AUTO),
                callback_data=session_parity.session_cb(self, chat_id, sess, "resume-auto"))],
        ]
        if may_full:
            rows.append([InlineKeyboardButton(
                label(mode_label(perm_modes.FULL), default == perm_modes.FULL),
                callback_data=session_parity.session_cb(self, chat_id, sess, "resume-full"))])
        rows.append([InlineKeyboardButton(
            tr.text(CANCEL),
            callback_data=session_parity.session_cb(self, chat_id, sess, "resume-cancel"))])
        return InlineKeyboardMarkup(rows)

    def _build_inline_ask_keyboard(self, sess: TrackedSession, options: list,
                                    multi_select: bool = False,
                                    selected: set | None = None) -> InlineKeyboardMarkup:
        """AskUserQuestion option buttons + Stop for inline display.

        For multi_select: each option on its own row with ☑/⬜ prefix,
        plus a "✅ Submit" button row at the bottom.
        """
        # Local import — see the precedent + rationale at
        # _build_resume_mode_keyboard above; do not hoist to module top.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        tr = i18n.for_sess(sess)
        sel = selected or set()
        rows = []
        if multi_select:
            for i, opt in enumerate(options[:4]):
                label = opt["label"] if "label" in opt else tr.text(OPTION, n=i + 1)
                prefix = "☑" if i in sel else "⬜"
                rows.append([InlineKeyboardButton(
                    f"{prefix} {label}",
                    callback_data=session_parity.session_cb(self, chat_id, sess, f"opt{i}"))])
            rows.append([InlineKeyboardButton(
                tr.text(SUBMIT),
                callback_data=session_parity.session_cb(self, chat_id, sess, "submit"))])
        else:
            buttons = []
            for i, opt in enumerate(options[:4]):
                opt_label = opt["label"] if "label" in opt else tr.text(OPTION, n=i + 1)
                buttons.append(InlineKeyboardButton(
                    opt_label,
                    callback_data=session_parity.session_cb(self, chat_id, sess, f"opt{i}"),
                ))
            if buttons:
                rows.append(buttons)
        rows.append([InlineKeyboardButton(
            tr.text(STOP), callback_data=session_parity.session_cb(self, chat_id, sess, "stop"))])
        return InlineKeyboardMarkup(rows)
