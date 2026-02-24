"""Telegram bot — python-telegram-bot v22 async Application.

Single owner of all Telegram communication. Handles:
- CallbackQuery (button taps) → tmux_inject.send_keys()
- Message replies → tmux_inject.send_text_and_enter()
- /status command → show all sessions
- /<label> <prompt> → direct send to session
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import time
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

from aipager import tmux_inject
from aipager.config import BOT_TOKEN, CHAT_ID, PROXY
from aipager.state import SessionRegistry, Status, TrackedSession

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Module-level reference set by TelegramBot.start()
_bot_instance: TelegramBot | None = None

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

    # ── Notification methods (called by hook_receiver and pane_monitor) ──

    async def notify(self, sess: TrackedSession, event: str, context: dict) -> None:
        """Send appropriate Telegram notification for a state change."""
        if not self._app:
            return

        bot = self._app.bot
        label = sess.label

        if sess.status == Status.IDLE:
            summary = context.get("summary", sess.summary)
            text = f"✅ <b>{html_mod.escape(label)}</b> · Finished"
            if summary:
                text += f"\n\n<blockquote>{html_mod.escape(summary)}</blockquote>"
            msg = await bot.send_message(CHAT_ID, text, parse_mode="HTML")
            self.registry.track_message(msg.message_id, sess.name)

        elif sess.status == Status.INTERACTIVE:
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

            msg = await bot.send_message(CHAT_ID, text, reply_markup=keyboard, parse_mode="HTML")
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

        if not await tmux_inject.is_alive(session_name):
            await query.answer(f"tmux '{session_name}' not found")
            return

        # Inject keystrokes
        ok = True
        if is_option:
            option_index = int(action[3:])
            verb = f"Selected option {option_index + 1}"
            for _ in range(option_index):
                if not await tmux_inject.send_keys(session_name, "Down"):
                    ok = False
                    break
            if ok:
                await asyncio.sleep(0.1)
                ok = await tmux_inject.send_keys(session_name, "Enter")
        elif action == "allow":
            verb = ACTION_VERBS[action]
            ok = await tmux_inject.send_keys(session_name, "Enter")
        elif action == "deny":
            verb = ACTION_VERBS[action]
            ok = await tmux_inject.send_keys(session_name, "Down")
            if ok:
                await asyncio.sleep(0.1)
                ok = await tmux_inject.send_keys(session_name, "Enter")
        elif action == "continue":
            verb = ACTION_VERBS[action]
            ok = await tmux_inject.send_keys(session_name, "Enter")
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
            # Try discovering from tmux
            tmux_sessions = await tmux_inject.list_sessions()
            if not tmux_sessions:
                await update.message.reply_text("No sessions found.")
                return
            for name in tmux_sessions:
                self.registry.get_or_create(name)
            sessions = self.registry.all_sessions()

        lines = ["<b>Sessions</b>\n"]
        for name, sess in sessions.items():
            alive = await tmux_inject.is_alive(name)
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

        # Reply to a notification
        reply_to = update.message.reply_to_message
        if not reply_to:
            return

        sess = self.registry.get_session_by_msg(reply_to.message_id)
        if not sess:
            return

        if not await tmux_inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ tmux '{sess.name}' not found")
            return

        ok = await tmux_inject.send_text_and_enter(sess.name, text)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            log.info("[%s] Sent text: %s", sess.label, text[:80])
        else:
            await update.message.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _direct_send(self, update: Update, target_label: str, prompt_text: str) -> None:
        """Send prompt directly to a session by label."""
        sessions = self.registry.all_sessions()
        for name, sess in sessions.items():
            if sess.label == target_label:
                if not await tmux_inject.is_alive(name):
                    await update.message.reply_text(f"⚠️ [{target_label}] tmux not alive")
                    return
                ok = await tmux_inject.send_text_and_enter(name, prompt_text)
                if ok:
                    await self._react(update, "👀")
                    self.registry.transition(name, Status.BUSY)
                    log.info("[%s] Direct send: %s", target_label, prompt_text[:80])
                else:
                    await update.message.reply_text(f"❌ Failed to send to [{target_label}]")
                return

        # Not found in registry — try tmux discovery
        tmux_name = f"claude-{target_label}"
        if await tmux_inject.is_alive(tmux_name):
            self.registry.get_or_create(tmux_name)
            ok = await tmux_inject.send_text_and_enter(tmux_name, prompt_text)
            if ok:
                await self._react(update, "👀")
                self.registry.transition(tmux_name, Status.BUSY)
            else:
                await update.message.reply_text(f"❌ Failed to send to [{target_label}]")
        else:
            await update.message.reply_text(f"⚠️ Unknown session: {target_label}")
