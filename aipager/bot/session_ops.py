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
import re
import time
from typing import TYPE_CHECKING

from telegram import (
    Update,
)

from aipager.dtach import inject

from aipager.state import Status, TrackedSession
from aipager.transcript import last_assistant_preview as _read_preview

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
    calling_chat_id,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)





class SessionOpsMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    # ── Telegram handlers ──

    async def _stop_session(self, sess: TrackedSession,
                            update: Update | None = None,
                            query=None) -> None:
        """Interrupt a busy session: send Escape, clean up state."""
        # 1. Send Escape twice to Claude Code
        await inject.send_keys(sess.name, "Escape")
        await asyncio.sleep(0.15)
        await inject.send_keys(sess.name, "Escape")

        # 2. Cancel animation
        self._stop_animation(sess)

        # 3. Edit busy message to show "Stopped" (no keyboard)
        if sess.busy_msg_id and sess.busy_msg_id > 0:
            await self._edit_busy_raw(
                sess.busy_msg_id,
                f"⚠️ <b>{html_mod.escape(sess.label)}</b> · Stopped",
            )
        sess.busy_msg_id = None

        # 4. Transition to IDLE directly (skip notify — we handle UI here)
        dropped = len(sess.pending_queue)
        sess.pending_queue.clear()
        sess.pending_permission = None
        sess.status = Status.IDLE
        sess.trigger_msg_id = None
        sess.last_idle_at = time.monotonic()  # prevent debounce of next real IDLE
        self.registry.mark_dirty()

        # 5. Acknowledge
        ack = f"Stopped [{sess.label}]"
        if dropped:
            ack += f" ({dropped} queued message{'s' if dropped > 1 else ''} discarded)"

        if query:
            await self._safe_answer(query, ack)
            # Also edit the callback query's message if it's the busy message
            try:
                await query.edit_message_text(
                    f"⚠️ <b>{html_mod.escape(sess.label)}</b> · Stopped",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        elif update:
            await self._react(update, "✅")

        log.info("[%s] Stopped by user (dropped %d queued)", sess.label, dropped)

    async def _kill_session_by_label(self, source, target_label: str) -> None:
        """Kill a session by label. source is Update or CallbackQuery."""
        async def _reply(text: str) -> None:
            if hasattr(source, 'message') and source.message:
                await source.message.reply_text(text)
            else:
                await source.edit_message_text(text)

        # Find session within the calling scope (label may repeat across scopes)
        found = self.registry.find_by_label(
            target_label, calling_chat_id(source), include_gone=True)
        session_name = found.name if found else f"claude-{target_label}"

        # Stop animation if running
        sess = self.registry.get(session_name)
        if sess:
            self._stop_animation(sess)

        # Kill the dtach process
        killed = await inject.kill_session(session_name)
        if killed:
            self.registry.remove(session_name)
            self.registry.mark_dirty()
            await _reply(f"💀 Killed [{target_label}]")
            asyncio.create_task(self._update_bot_commands())
        else:
            await _reply(f"⚠️ Session [{target_label}] not found")

    async def _do_resume(self, *, label: str, reply_fn,
                          update: Update | None = None,
                          query=None) -> None:
        """Shared resume logic for /resume <name> and picker callbacks.

        ``reply_fn`` is the async-callable used to send the result back
        (``update.message.reply_text`` for command, ``query.edit_message_text``
        for callbacks). ``update`` is used to attribute the driver in team
        mode when available.
        """
        sess = self.registry.find_by_label(
            label, calling_chat_id(update or query), include_gone=True)
        session_name = sess.name if sess is not None else f"claude-{label}"

        if sess is None:
            await reply_fn(
                f"⚠️ No session named <b>{html_mod.escape(label)}</b> in history.\n"
                f"Send <code>/resume</code> with no name to see what's available.",
                parse_mode="HTML",
            )
            return

        if sess.status != Status.GONE:
            await reply_fn(
                f"⚠️ <b>{html_mod.escape(label)}</b> is already running.\n"
                f"Tap <code>/{html_mod.escape(label)}</code> in the keyboard "
                f"to switch to it.",
                parse_mode="HTML",
            )
            return

        if not sess.claude_session_id:
            await reply_fn(
                f"⚠️ Session <b>{html_mod.escape(label)}</b> has no resumable "
                f"transcript on disk.\n"
                f"Start a fresh one with <code>/new {html_mod.escape(label)}</code>.",
                parse_mode="HTML",
            )
            return

        resume_id = sess.claude_session_id
        cwd = sess.cwd or None
        # Defensive: clear the id BEFORE launch so a repeat-failure doesn't
        # rope us into an infinite resume loop (claude --resume against a
        # deleted transcript dies → socket disappears → /resume retries).
        sess.claude_session_id = ""
        self.registry.mark_dirty()

        ok, err = await inject.launch_session(
            label, resume_id=resume_id, cwd=cwd,
        )
        if not ok:
            # Restore the id so the user can try again after fixing whatever
            # broke (e.g. removing a stale socket).
            sess.claude_session_id = resume_id
            self.registry.mark_dirty()
            await reply_fn(
                f"❌ Couldn't resume <b>{html_mod.escape(label)}</b>: "
                f"{html_mod.escape(err)}",
                parse_mode="HTML",
            )
            return

        # Resume succeeded — recover state, dashboard out the result.
        sess.gone_at = None
        self.registry.transition(session_name, Status.IDLE)
        if update is not None:
            self._mark_driver(sess, update)
        self.registry.last_active_session = session_name
        self.registry.mark_dirty()
        asyncio.create_task(self._maybe_update_bot_name(session_name))
        asyncio.create_task(self._update_bot_commands())

        dashboard = self._build_session_dashboard(sess)
        # Always try to surface where the user left off. If the cached
        # preview is empty (e.g. SessionEnd hook was dropped at GONE
        # time) re-derive from the transcript file on disk. A longer
        # cap here gives enough context to remember the conversation.
        preview = sess.last_assistant_preview or _read_preview(
            sess.transcript_path, max_chars=500,
        )
        header = f"♻️ Resumed <b>{html_mod.escape(label)}</b>"
        if preview:
            body = (
                f"{header}\n\n"
                f"{dashboard}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>📜 Last response from this session</i>\n"
                f"<blockquote>{html_mod.escape(preview)}</blockquote>"
            )
        else:
            body = f"{header}\n\n{dashboard}"

        await reply_fn(body, parse_mode="HTML")
        log.info("[%s] Resumed (claude_session_id=%s, cwd=%s)",
                 label, resume_id, cwd or "<daemon>")

    async def _stop_by_label(self, update: Update, target_label: str) -> None:
        """Stop a session by its label."""
        sess = self.registry.find_by_label(target_label, calling_chat_id(update))
        if sess is not None:
            if sess.status not in (Status.BUSY, Status.INTERACTIVE):
                await update.message.reply_text(f"[{target_label}] is not busy.")
                return
            await self._stop_session(sess, update=update)
            return
        await update.message.reply_text(f"⚠️ Unknown session: {target_label}")

    def _guess_session_from_text(self, text: str) -> TrackedSession | None:
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
        """
        if not text:
            return None
        matches: list[TrackedSession] = []
        for cand in self.registry.all_sessions().values():
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

    async def _switch_session(self, update: Update, target_label: str) -> None:
        """Switch active session when bare /<label> is tapped (no prompt)."""
        # Find in registry within the calling scope
        sess = self.registry.find_by_label(target_label, calling_chat_id(update))
        if sess is not None:
            name = sess.name
            self.registry.last_active_session = name
            self.registry.mark_dirty()
            asyncio.create_task(self._maybe_update_bot_name(name))
            dashboard = self._build_session_dashboard(sess)
            await update.message.reply_text(dashboard, parse_mode="HTML")
            return

        # Try auto-discover
        session_name = f"claude-{target_label}"
        if await inject.is_alive(session_name):
            sess = self.registry.get_or_create(session_name)
            self.registry.last_active_session = session_name
            self.registry.mark_dirty()
            asyncio.create_task(self._maybe_update_bot_name(session_name))
            asyncio.create_task(self._update_bot_commands())
            dashboard = self._build_session_dashboard(sess)
            await update.message.reply_text(dashboard, parse_mode="HTML")
            return

        await update.message.reply_text(f"⚠️ Unknown session: {target_label}")
