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
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from aipager import dtach_inject as inject
import random

from aipager.config import BOT_TOKEN, BUSY_EDIT_INTERVAL, CHAT_ID, PROXY, SPINNER_VERBS
from aipager.state import SessionRegistry, Status, TrackedSession

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Module-level reference set by TelegramBot.start()
_bot_instance: TelegramBot | None = None

def _md_safe_boundaries(md: str) -> list[int]:
    """Find character positions in markdown that are safe to cut at.

    Returns positions of paragraph breaks (\\n\\n) that are NOT inside
    fenced code blocks. Cutting at these positions guarantees both halves
    are valid markdown that can be independently converted to HTML.
    """
    boundaries = []
    in_fence = False
    pos = 0
    for line in md.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        pos += len(line) + 1  # +1 for the \n
        # Check if next char starts a paragraph break and we're outside a fence
        if not in_fence and pos < len(md) and md[pos - 1:pos + 1] == "\n\n":
            boundaries.append(pos)
    return boundaries


def _safe_truncate(text: str, limit: int, is_html: bool) -> str:
    """Truncate text to limit, ensuring HTML tags aren't split mid-tag."""
    if not is_html or len(text) <= limit:
        return text[:limit] + "…"
    # Cut at limit, then back up to avoid splitting an HTML tag
    cut = text[:limit]
    last_lt = cut.rfind("<")
    last_gt = cut.rfind(">")
    if last_lt > last_gt:
        cut = cut[:last_lt]
    # Use a stack to track nesting order, then close in reverse
    stack: list[str] = []
    for m in re.finditer(r"<(/?)(b|i|code|pre|a)\b[^>]*>", cut):
        is_close, tag = m.group(1), m.group(2)
        if is_close:
            if stack and stack[-1] == tag:
                stack.pop()
        else:
            stack.append(tag)
    # Close remaining open tags in reverse (innermost first)
    for tag in reversed(stack):
        cut += f"</{tag}>"
    return cut + "…"


ACTION_VERBS = {
    "allow": "Allowed",
    "deny": "Denied",
    "continue": "Continued",
}


class TelegramBot:
    """Wraps python-telegram-bot Application with session-aware handlers."""

    def __init__(self, registry: SessionRegistry):
        self.registry = registry
        self._app: Application | None = None

    async def start(self) -> None:
        global _bot_instance
        _bot_instance = self

        use_proxy = not await self._test_direct()
        builder = ApplicationBuilder().token(BOT_TOKEN)
        if use_proxy:
            log.info("Using proxy: %s", PROXY)
            builder = builder.proxy(PROXY).get_updates_proxy(PROXY)
        else:
            log.info("Direct connection to Telegram OK")

        # Long-poll config: timeout=30 means Telegram holds the connection
        # for up to 30s waiting for updates → instant response to taps
        builder = (
            builder
            .get_updates_read_timeout(30)
            .get_updates_write_timeout(10)
            .get_updates_connect_timeout(10)
            .read_timeout(10)
            .write_timeout(10)
            .connect_timeout(10)
        )

        self._app = builder.build()

        # Register handlers
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        # Catch-all for text messages (replies and /<label> commands)
        self._app.add_handler(MessageHandler(
            filters.TEXT & filters.Chat(int(CHAT_ID)),
            self._handle_message,
        ))

        await self._app.initialize()
        await self._app.start()
        # Start polling with 30s long-poll timeout
        await self._app.updater.start_polling(
            poll_interval=0,          # no sleep between polls
            timeout=30,               # Telegram server holds connection 30s
            drop_pending_updates=True, # ignore old updates on restart
        )
        log.info("Telegram bot polling started")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    @staticmethod
    async def _test_direct() -> bool:
        """Test if Telegram API is directly reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get("https://api.telegram.org/")
                return r.status_code in (200, 302)
        except Exception:
            return False

    async def _react(self, update: Update, emoji: str) -> None:
        """React to the user's message with an emoji."""
        try:
            await self._app.bot.set_message_reaction(
                update.effective_chat.id, update.message.message_id, emoji,
            )
        except Exception:
            pass  # reaction API may not be available in all contexts

    # ── Notification methods (called by hook_receiver and session_monitor) ──

    async def send_busy(self, sess: TrackedSession) -> int | None:
        """Send initial 'Working...' message and start animation. Returns message_id."""
        if not self._app:
            return None
        text = f"⚙️ <b>{html_mod.escape(sess.label)}</b> · Thinking…"
        try:
            msg = await self._app.bot.send_message(
                CHAT_ID, text, parse_mode="HTML",
                reply_to_message_id=sess.trigger_msg_id,
            )
            return msg.message_id
        except Exception:
            log.warning("Failed to send busy message", exc_info=True)
            return None

    def _build_busy_text(self, label: str, verb: str, sess: TrackedSession) -> str:
        """Build the animated busy message text."""
        text = f"⚙️ <b>{html_mod.escape(label)}</b> · {html_mod.escape(verb)}…"
        if sess.last_token_pct:
            text += f"  <i>({sess.last_token_pct}% ctx)</i>"
        if sess.last_tool_summary:
            text += f"\n<code>{html_mod.escape(sess.last_tool_summary)}</code>"
        return text

    async def _edit_busy_raw(self, msg_id: int, text: str) -> bool | None:
        """Edit busy message with pre-built text.

        Returns True on success, False on transient error,
        None on permanent failure (message gone).
        """
        if not self._app:
            return False
        try:
            await self._app.bot.edit_message_text(
                text, chat_id=CHAT_ID, message_id=msg_id, parse_mode="HTML",
            )
            return True
        except Exception as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return True
            if "message to edit not found" in err:
                return None  # permanent: message deleted
            log.debug("Edit busy failed: %s", e)
            return False  # transient: rate-limit, network, etc.

    async def _animate_busy(self, sess: TrackedSession) -> None:
        """Background task: rotate spinner verbs while session is BUSY."""
        verbs = list(SPINNER_VERBS)
        random.shuffle(verbs)
        idx = 0
        try:
            while sess.busy_msg_id and sess.status == Status.BUSY:
                await asyncio.sleep(BUSY_EDIT_INTERVAL)
                if not sess.busy_msg_id or sess.status != Status.BUSY:
                    break
                # Skip if a tool edit happened recently (let tool info stay visible)
                if time.monotonic() - sess.last_tool_edit_at < BUSY_EDIT_INTERVAL - 0.5:
                    continue
                verb = verbs[idx % len(verbs)]
                idx += 1
                text = self._build_busy_text(sess.label, verb, sess)
                result = await self._edit_busy_raw(sess.busy_msg_id, text)
                if result is None:
                    sess.busy_msg_id = None  # message gone
                    break
        except asyncio.CancelledError:
            pass

    def _start_animation(self, sess: TrackedSession) -> None:
        """Start the spinner animation task, cancelling any existing one."""
        self._stop_animation(sess)
        sess.animate_task = asyncio.create_task(self._animate_busy(sess))

    def _stop_animation(self, sess: TrackedSession) -> None:
        """Cancel the animation task if running."""
        if sess.animate_task and not sess.animate_task.done():
            sess.animate_task.cancel()
        sess.animate_task = None

    async def _send_busy_and_animate(self, sess: TrackedSession) -> None:
        """Send 'Working...' message and start spinner animation.

        Idempotent: uses a synchronous sentinel (-1) before the async send
        to prevent double-send when _handle_message and UserPromptSubmit
        hook coroutines race through the same await point.
        """
        if sess.busy_msg_id:
            return  # already showing busy (or sentinel claimed by other coroutine)
        sess.busy_msg_id = -1  # sentinel: claim slot before async yield
        self._stop_animation(sess)
        sess.last_tool_summary = ""
        sess.last_token_pct = 0
        msg_id = await self.send_busy(sess)
        if msg_id:
            sess.busy_msg_id = msg_id
            sess.last_tool_edit_at = 0.0
            sess.last_tool_name = ""
            self._start_animation(sess)
            log.info("[%s] Busy message sent (msg_id=%d, trigger=%s)",
                     sess.label, msg_id, sess.trigger_msg_id)
        else:
            sess.busy_msg_id = None  # release slot on failure

    async def notify(self, sess: TrackedSession, event: str, context: dict) -> None:
        """Send appropriate Telegram notification for a state change."""
        if not self._app:
            return

        bot = self._app.bot
        label = sess.label

        # ── Live busy-status events ──
        if event == "user_prompt_submit":
            # Fallback for terminal-initiated prompts only.
            # If busy_msg_id is already set, _handle_message already sent it.
            if not sess.busy_msg_id:
                await self._send_busy_and_animate(sess)
            return

        if event == "tool_use":
            tool_summary = context.get("tool_summary", "")
            tool_name = context.get("tool_name", "")
            token_usage = context.get("token_usage")
            if not sess.busy_msg_id or sess.busy_msg_id < 0 or not tool_summary:
                return
            # Cache for animation display
            sess.last_tool_summary = tool_summary
            if token_usage:
                sess.last_token_pct = token_usage.get("context_pct", 0)
            now = time.monotonic()
            # Edit if tool changed OR interval elapsed since last edit
            if (tool_name != sess.last_tool_name or
                    now - sess.last_tool_edit_at >= BUSY_EDIT_INTERVAL):
                text = self._build_busy_text(label, "Working", sess)
                result = await self._edit_busy_raw(sess.busy_msg_id, text)
                if result is True:
                    sess.last_tool_edit_at = now
                    sess.last_tool_name = tool_name
                elif result is None:
                    sess.busy_msg_id = None  # message gone, stop editing
                    self._stop_animation(sess)
            return

        if sess.status == Status.IDLE:
            # Stop animation and clean up busy message
            self._stop_animation(sess)
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                try:
                    await bot.delete_message(
                        chat_id=CHAT_ID,
                        message_id=sess.busy_msg_id,
                    )
                except Exception:
                    pass
                sess.busy_msg_id = None

            summary = context.get("summary", sess.summary)
            is_html = context.get("html_summary", False)
            raw_md = context.get("raw_md", "")
            text = f"✅ <b>{html_mod.escape(label)}</b> · Finished"
            send_file = False
            if summary:
                escaped = summary if is_html else html_mod.escape(summary)
                log.info("[%s] IDLE summary: is_html=%s, raw_md=%d, escaped=%d",
                         label, is_html, len(raw_md), len(escaped))
                if len(escaped) > 3400 and is_html and raw_md:
                    # Split at safe markdown boundaries (outside fenced code
                    # blocks), convert each piece to HTML independently.
                    from aipager.md_to_tg import markdown_to_telegram_html
                    bounds = _md_safe_boundaries(raw_md)
                    md_limit = len(raw_md) // 3
                    # Head: largest safe boundary within first ~1/3
                    head_cut = 0
                    for b in bounds:
                        if b <= md_limit:
                            head_cut = b
                    head_md = raw_md[:head_cut] if head_cut else raw_md[:md_limit]
                    # Tail: smallest safe boundary within last ~1/3
                    tail_start = len(raw_md)
                    for b in reversed(bounds):
                        if b >= len(raw_md) - md_limit:
                            tail_start = b
                    tail_md = raw_md[tail_start:] if tail_start < len(raw_md) else raw_md[-md_limit:]
                    head_html = markdown_to_telegram_html(head_md)
                    tail_html = markdown_to_telegram_html(tail_md)
                    # Safety: truncate each half if HTML blew up.
                    # Budget: 1500+1500+sep(~100)+header(~60)+blockquote(~40) < 4096
                    if len(head_html) > 1500:
                        head_html = _safe_truncate(head_html, 1500, True)
                    if len(tail_html) > 1500:
                        tail_html = _safe_truncate(tail_html, 1500, True)
                    sep = (
                        "\n\n"
                        "╔══════════════════════╗\n"
                        "║   ✂️ TRUNCATED ✂️   ║\n"
                        "╚══════════════════════╝"
                        "\n\n"
                    )
                    escaped = head_html + sep + tail_html
                    send_file = True
                # Hard safety cap — never exceed Telegram's 4096 limit.
                # Header + blockquote tags use ~80 chars, leave ~4000 for content.
                if len(escaped) > 3800:
                    escaped = _safe_truncate(escaped, 3800, is_html)
                    send_file = True
                if len(escaped) > 500:
                    text += f"\n\n<blockquote expandable>{escaped}</blockquote>"
                else:
                    text += f"\n\n<blockquote>{escaped}</blockquote>"
            log.debug("[%s] Sending IDLE notification (%d chars)", label, len(text))
            msg = await bot.send_message(
                CHAT_ID, text, parse_mode="HTML",
                reply_to_message_id=sess.trigger_msg_id,
            )
            sess.trigger_msg_id = None  # reply cycle complete
            self.registry.track_message(msg.message_id, sess.name)
            # Send full response as file for long messages
            file_content = raw_md or (summary if send_file else "")
            if send_file and file_content:
                try:
                    tmp = Path(tempfile.mktemp(suffix=".txt", prefix=f"{label}_"))
                    tmp.write_text(file_content, encoding="utf-8")
                    with open(tmp, "rb") as f:
                        await bot.send_document(
                            CHAT_ID, document=f, filename=f"{label}_response.txt",
                            reply_to_message_id=msg.message_id,
                        )
                    tmp.unlink(missing_ok=True)
                except Exception:
                    log.warning("Failed to send full response file", exc_info=True)

            # Flush queued text (user sent message while session was BUSY)
            if sess.pending_text:
                queued = sess.pending_text
                sess.pending_text = ""
                sess.trigger_msg_id = sess.queued_trigger_msg_id  # promote
                sess.queued_trigger_msg_id = None
                ok = await inject.send_text_and_enter(sess.name, queued)
                if ok:
                    self.registry.transition(sess.name, Status.BUSY)
                    await self._send_busy_and_animate(sess)
                    log.info("[%s] Flushed queued: %s", sess.label, queued[:80])

        elif sess.status == Status.INTERACTIVE:
            self._stop_animation(sess)
            tool_info = context.get("tool_info")
            selector_text = context.get("selector_text", "")
            selector_options = context.get("selector_options")

            if tool_info and tool_info["name"] == "AskUserQuestion":
                text, keyboard = self._build_ask_keyboard(sess.name, label, tool_info["input"])
            elif selector_options:
                text, keyboard = self._build_selector_keyboard(sess.name, label,
                                                                selector_text, selector_options)
            else:
                tool_summary = tool_info["summary"] if tool_info else ""
                text = f"🔐 <b>{html_mod.escape(label)}</b> · Permission needed"
                if tool_summary:
                    text += f"\n<code>{html_mod.escape(tool_summary)}</code>"
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Allow", callback_data=f"{sess.name}:allow"),
                    InlineKeyboardButton("❌ Deny", callback_data=f"{sess.name}:deny"),
                ]])

            msg = await bot.send_message(
                CHAT_ID, text, reply_markup=keyboard, parse_mode="HTML",
                reply_to_message_id=sess.trigger_msg_id,
            )
            self.registry.track_message(msg.message_id, sess.name)

        elif sess.status == Status.BUSY:
            # Session went back to working — edit the last idle/interactive message
            if sess.last_msg_id:
                try:
                    await bot.edit_message_text(
                        f"⚙️ <b>{html_mod.escape(label)}</b> · Working…",
                        chat_id=CHAT_ID,
                        message_id=sess.last_msg_id,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass  # message may be too old or already edited

    def _build_ask_keyboard(self, session_name: str, label: str,
                            tool_input: dict) -> tuple[str, InlineKeyboardMarkup | None]:
        """Build message and buttons for AskUserQuestion."""
        questions = tool_input.get("questions", [])
        if not questions:
            return f"❓ <b>{html_mod.escape(label)}</b> · No questions", None

        q = questions[0]
        question = q.get("question", "?")
        options = q.get("options", [])

        text = f"❓ <b>{html_mod.escape(label)}</b> · {html_mod.escape(question)}"
        if not options:
            return text, None

        for i, opt in enumerate(options):
            opt_label = opt.get("label", f"Option {i+1}")
            desc = opt.get("description", "")
            text += f"\n  {i+1}. {html_mod.escape(opt_label)}"
            if desc:
                text += f" — {html_mod.escape(desc[:60])}"

        buttons = []
        for i, opt in enumerate(options[:4]):
            opt_label = opt.get("label", f"Option {i+1}")
            buttons.append(InlineKeyboardButton(
                opt_label, callback_data=f"{session_name}:opt{i}",
            ))

        return text, InlineKeyboardMarkup([buttons])

    def _build_selector_keyboard(self, session_name: str, label: str,
                                  question: str,
                                  options: list[tuple[int, str]]) -> tuple[str, InlineKeyboardMarkup | None]:
        """Build message and buttons from pane-scraped selector."""
        if question:
            text = f"❓ <b>{html_mod.escape(label)}</b> · {html_mod.escape(question)}"
        else:
            text = f"🔐 <b>{html_mod.escape(label)}</b> · Needs input"

        for num, opt_label in options:
            text += f"\n  {num}. {html_mod.escape(opt_label)}"

        buttons = []
        for num, opt_label in options[:4]:
            buttons.append(InlineKeyboardButton(
                opt_label[:20], callback_data=f"{session_name}:opt{num - 1}",
            ))

        keyboard = InlineKeyboardMarkup([buttons]) if buttons else None
        return text, keyboard

    # ── Telegram handlers ──

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button tap."""
        query = update.callback_query
        cb_data = query.data or ""
        original_text = query.message.text or "" if query.message else ""

        if ":" not in cb_data:
            await query.answer("Invalid callback")
            return

        session_name, action = cb_data.split(":", 1)
        is_option = action.startswith("opt") and action[3:].isdigit()

        if action not in ACTION_VERBS and not is_option:
            await query.answer(f"Unknown: {action}")
            return

        sess = self.registry.get(session_name)
        if not sess:
            await query.answer("Session not found")
            return

        if not await inject.is_alive(session_name):
            await query.answer(f"Session '{session_name}' not found")
            return

        # Inject keystrokes
        ok = True
        if is_option:
            option_index = int(action[3:])
            verb = f"Selected option {option_index + 1}"
            for _ in range(option_index):
                if not await inject.send_keys(session_name, "Down"):
                    ok = False
                    break
            if ok:
                await asyncio.sleep(0.1)
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "allow":
            verb = ACTION_VERBS[action]
            ok = await inject.send_keys(session_name, "Enter")
        elif action == "deny":
            verb = ACTION_VERBS[action]
            ok = await inject.send_keys(session_name, "Down")
            if ok:
                await asyncio.sleep(0.1)
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "continue":
            verb = ACTION_VERBS[action]
            ok = await inject.send_keys(session_name, "Enter")
        else:
            verb = action

        if ok:
            await query.answer(f"{verb} [{sess.label}]")
            try:
                await query.edit_message_text(f"{original_text}\n\n→ {verb}")
            except Exception:
                pass
            self.registry.remove_message(query.message.message_id)
            # Mark session as busy after user interaction
            self.registry.transition(session_name, Status.BUSY)
            log.info("[%s] %s", sess.label, verb)
        else:
            await query.answer(f"Failed to send to {session_name}")

    async def _handle_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        sessions = self.registry.all_sessions()
        if not sessions:
            # Try discovering sessions
            discovered = await inject.list_sessions()
            if not discovered:
                await update.message.reply_text("No sessions found.")
                return
            for name in discovered:
                self.registry.get_or_create(name)
            sessions = self.registry.all_sessions()

        lines = ["<b>Sessions</b>\n"]
        for name, sess in sessions.items():
            alive = await inject.is_alive(name)
            icon = "🟢" if alive else "🔴"
            status_str = sess.status.name.lower()
            lines.append(f"{icon} <b>{html_mod.escape(sess.label)}</b> · {status_str}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle text messages — replies to notifications or /<label> commands."""
        text = update.message.text.strip()
        if not text:
            return

        # /<label> <prompt> — direct send
        if text.startswith("/") and " " in text:
            parts = text.split(" ", 1)
            target_label = parts[0][1:]
            prompt_text = parts[1].strip()
            if target_label and prompt_text:
                await self._direct_send(update, target_label, prompt_text)
                return

        # Reply to a notification — or bare message goes to last active session
        reply_to = update.message.reply_to_message
        if reply_to:
            sess = self.registry.get_session_by_msg(reply_to.message_id)
        else:
            # Bare message → send to last session that notified us
            name = self.registry.last_active_session
            sess = self.registry.get(name) if name else None

        if not sess:
            return

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if session is busy — inject when it goes IDLE
        if sess.status == Status.BUSY:
            sess.pending_text = text
            sess.queued_trigger_msg_id = update.message.message_id
            await self._react(update, "🕐")
            log.info("[%s] Queued (busy): %s", sess.label, text[:80])
            return

        sess.trigger_msg_id = update.message.message_id
        ok = await inject.send_text_and_enter(sess.name, text)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Sent text: %s", sess.label, text[:80])
        else:
            await update.message.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _direct_send(self, update: Update, target_label: str, prompt_text: str) -> None:
        """Send prompt directly to a session by label."""
        sessions = self.registry.all_sessions()
        for name, sess in sessions.items():
            if sess.label == target_label:
                if not await inject.is_alive(name):
                    await update.message.reply_text(f"⚠️ [{target_label}] session not alive")
                    return
                sess.trigger_msg_id = update.message.message_id
                ok = await inject.send_text_and_enter(name, prompt_text)
                if ok:
                    await self._react(update, "👀")
                    self.registry.transition(name, Status.BUSY)
                    await self._send_busy_and_animate(sess)
                    log.info("[%s] Direct send: %s", target_label, prompt_text[:80])
                else:
                    await update.message.reply_text(f"❌ Failed to send to [{target_label}]")
                return

        # Not found in registry — try session discovery
        session_name = f"claude-{target_label}"
        if await inject.is_alive(session_name):
            new_sess = self.registry.get_or_create(session_name)
            new_sess.trigger_msg_id = update.message.message_id
            ok = await inject.send_text_and_enter(session_name, prompt_text)
            if ok:
                await self._react(update, "👀")
                self.registry.transition(session_name, Status.BUSY)
                await self._send_busy_and_animate(new_sess)
            else:
                await update.message.reply_text(f"❌ Failed to send to [{target_label}]")
        else:
            await update.message.reply_text(f"⚠️ Unknown session: {target_label}")
