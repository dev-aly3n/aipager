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
from typing import TYPE_CHECKING

from telegram import (
    Update,
)

from aipager.dtach import inject

from aipager.config import (
    CHAT_ID,
)
from aipager.state import Status, TrackedSession
from aipager.team import (
    Role,
    User as TeamUser,
    attribution_label,
    record_pending_user,
    remember_unauthorized,
)

# Pure-function helpers and constants live in aipager.bot.transport
# now. Re-export the names this module uses internally so the
# TelegramBot class body below (and any external consumers like the
# tests) keeps working without changes.
from aipager.bot.transport import (  # noqa: F401
    ACTION_VERBS,
    TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES,
    TELEGRAM_MAX_DOC_BYTES,
    TELEGRAM_MAX_TEXT_LEN,
    TruncationFailed,
    _build_diff_block,
    _detect_api_error,
    _DIFF_MAX_CHARS,
    _DIFF_MAX_LINES,
    _diff_view_enabled,
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





class AuthMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    # ------------------------------------------------------------------
    # Authorization helpers (team mode)
    #
    # In personal mode (``self.team is None``) every chat-id-passing
    # message is implicitly authorized. In team mode we re-check the
    # sender's user_id against ``self.team`` allow-list at the top of
    # every handler — never trust the chat-level filter alone for
    # actions that mutate state.
    # ------------------------------------------------------------------

    def _team_user(self, update: Update) -> TeamUser | None:
        """Resolve the Telegram sender to a ``team.User``.

        Returns ``None`` in personal mode OR when the sender isn't on
        the allow-list. Callers distinguish the two via
        ``self.team is None``.
        """
        if self.team is None:
            return None
        tg_user = update.effective_user
        if tg_user is None:
            return None
        return self.team.get(tg_user.id)

    async def _authorize(
        self, update: Update, *, allow_read_only: bool = False,
    ) -> bool:
        """Return True iff the message's sender is allowed to act.

        Personal mode: always True (chat filter already gated it).
        Team mode: True iff the sender is on the allow-list AND
        either ``allow_read_only`` or their role is not READ_ONLY.

        On rejection, sends a one-shot polite reply explaining why,
        then silently ignores subsequent messages from that user
        until daemon restart.
        """
        if self.team is None:
            return True

        tg_user = update.effective_user
        if tg_user is None:
            return False  # malformed update, ignore

        member = self.team.get(tg_user.id)
        if member is None:
            # Not on the allow-list. Two things happen:
            #   1. Persist the identity to ~/.claude/aipager-pending-users.json
            #      so the admin can review + approve later via the wizard
            #      (no scrolling chat / grep'ing logs).
            #   2. Log at INFO level + send one-shot in-chat reply.
            handle = tg_user.username or ""
            display = (tg_user.first_name or "") + (
                f" {tg_user.last_name}" if tg_user.last_name else ""
            )
            chat = update.effective_chat
            chat_id = chat.id if chat is not None else None
            try:
                record_pending_user(
                    tg_user.id,
                    username=handle,
                    display_name=display.strip(),
                    chat_id=chat_id,
                )
            except Exception:
                log.debug("record_pending_user failed", exc_info=True)

            already_seen = remember_unauthorized(tg_user.id)
            log.info(
                "unauthorized user %s mentioned the bot (id=%d, name=%r) "
                "— %s",
                f"@{handle}" if handle else "(no handle)",
                tg_user.id,
                display.strip(),
                "already replied earlier" if already_seen
                else "sending one-shot reply",
            )
            if not already_seen:
                msg = update.effective_message
                if msg is not None:
                    try:
                        await msg.reply_text(
                            "🚫 You're not on this bot's allow-list. "
                            "Ask an admin to add your Telegram user ID "
                            f"({tg_user.id}) to ~/.config/aipager/team.yaml — "
                            "or `aipager config` → Review pending users.",
                        )
                    except Exception:
                        log.debug("reply to unauthorized user failed", exc_info=True)
            return False

        if member.role == Role.READ_ONLY and not allow_read_only:
            msg = update.effective_message
            if msg is not None:
                try:
                    await msg.reply_text(
                        f"👀 {attribution_label(member)} — your role is "
                        "<i>read_only</i>; you can use <code>/status</code> "
                        "but can't drive sessions.",
                        parse_mode="HTML",
                    )
                except Exception:
                    log.debug("reply to read-only user failed", exc_info=True)
            return False

        return True

    async def _auto_deny(
        self,
        sess: TrackedSession,
        tool_info: dict,
        triggerer: TeamUser | None,
    ) -> None:
        """Auto-deny a tool prompt via the same key-injection path the
        ``[❌ Deny]`` button uses. Called when a team rule matches.

        Posts a one-line notice in the chat naming the rule and the
        triggering user. Writes a structured audit record. Returns the
        session to BUSY so the next claude tick proceeds normally.
        """
        tool_name = tool_info.get("name", "?")
        summary = tool_info.get("summary", "")[:120]

        # Inject Down + Enter — same sequence the Deny button uses to
        # walk claude's "Allow / Deny" picker.
        ok = await inject.send_keys(sess.name, "Down")
        if ok:
            await asyncio.sleep(0.1)
            ok = await inject.send_keys(sess.name, "Enter")
        if not ok:
            log.warning(
                "[%s] auto-deny key injection failed for %s",
                sess.label, tool_name,
            )

        # Restore BUSY (claude is proceeding past the denied prompt).
        self.registry.transition(sess.name, Status.BUSY)
        sess.pending_permission = None

        by_attr = (
            f" (triggered by {attribution_label(triggerer)})"
            if triggerer is not None
            else ""
        )
        try:
            await self._app.bot.send_message(
                CHAT_ID,
                f"⛔ <b>{html_mod.escape(sess.label)}</b> · "
                f"Auto-denied · {html_mod.escape(tool_name)} · "
                f"per rules.deny_tools{html_mod.escape(by_attr)}\n"
                f"<i>{html_mod.escape(summary)}</i>",
                parse_mode="HTML",
                reply_to_message_id=(sess.busy_msg_id
                                     if sess.busy_msg_id and sess.busy_msg_id > 0
                                     else None),
            )
        except Exception:
            log.debug(
                "[%s] auto-deny chat message failed", sess.label,
                exc_info=True,
            )

        try:
            from aipager import audit as audit_mod
            audit_mod.append(
                session=sess.name, label=sess.label,
                action="Auto-denied",
                tool=tool_name,
                summary=summary,
                user_id=triggerer.id if triggerer else None,
                username=triggerer.label if triggerer else "",
            )
        except Exception:
            log.debug("[%s] auto-deny audit failed", sess.label, exc_info=True)

        log.info(
            "[%s] auto-denied %s per rules.deny_tools (triggerer=%s)",
            sess.label, tool_name,
            triggerer.label if triggerer else "unknown",
        )

    def _mark_driver(
        self, sess: TrackedSession, update: Update,
    ) -> TeamUser | None:
        """Record the message sender as the session's last driver.

        Personal-mode no-op (returns ``None``). In team mode, sets
        ``sess.last_driver_user_id`` and (if first-touch) also
        ``sess.created_by_user_id``. The returned :class:`team.User`
        is used by callers to attribute prompts and audit records.
        """
        if self.team is None:
            return None
        tg_user = update.effective_user
        if tg_user is None:
            return None
        member = self.team.get(tg_user.id)
        if member is None:
            return None
        sess.last_driver_user_id = member.id
        if sess.created_by_user_id is None:
            sess.created_by_user_id = member.id
        return member

    def _driver_user(self, sess: TrackedSession) -> TeamUser | None:
        """Resolve a session's ``last_driver_user_id`` to a ``TeamUser``.

        Returns ``None`` if no driver is recorded, the team isn't
        loaded, or the driver was removed from the allow-list since
        their last interaction.
        """
        if self.team is None or sess.last_driver_user_id is None:
            return None
        return self.team.get(sess.last_driver_user_id)

    async def _authorize_callback(self, query) -> TeamUser | None:
        """Allow-list check for inline-keyboard taps.

        Returns the matching ``team.User`` on success, or ``None`` on
        rejection (in which case a Telegram toast is shown to the
        user via ``answer``). Personal mode returns a synthetic
        sentinel — never ``None`` — so callers can keep their
        existing flow.
        """
        if self.team is None:
            return _PERSONAL_MODE_SENTINEL

        tg_user = query.from_user
        if tg_user is None:
            return None

        member = self.team.get(tg_user.id)
        if member is None:
            try:
                await query.answer(
                    "Not on the allow-list", show_alert=True,
                )
            except Exception:
                log.debug("toast to unauthorized callback failed", exc_info=True)
            return None
        return member
