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
import json
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
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

from aipager.config import (
    BACK_BUTTON, BOT_TOKEN, BUSY_EDIT_INTERVAL, CHAT_ID, COMMANDS_BUTTON,
    FILE_DOWNLOAD_DIR, KEYBOARD_PARENTS, MODEL_CHOICES, MODELS_BUTTON,
    PROXY, QUICK_COMMANDS, QUICK_TEMPLATES, SPINNER_VERBS, TEMPLATES_BUTTON,
)
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

# ── API error detection ──

_ERROR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"API Error:\s*500|api_error|internal server error", re.I),
     "Anthropic's servers hit an internal error. Usually resolves in seconds."),
    (re.compile(r"API Error:\s*529|overloaded_error|overloaded", re.I),
     "Anthropic's servers are overloaded. Try again in a moment."),
    (re.compile(r"API Error:\s*429|rate_limit_error|rate.?limit", re.I),
     "Rate limit hit. Wait a moment before retrying."),
    (re.compile(r"connection.?(error|reset|refused|timeout)|ECONNR|network.?error", re.I),
     "Lost connection to Anthropic. Check network and retry."),
    (re.compile(r"API Error:\s*\d{3}", re.I),
     "API error occurred."),
]


def _detect_api_error(text: str) -> str | None:
    """Check if text contains an API error. Returns friendly message or None."""
    if not text:
        return None
    for pattern, friendly_msg in _ERROR_PATTERNS:
        if pattern.search(text):
            return friendly_msg
    return None


class TelegramBot:
    """Wraps python-telegram-bot Application with session-aware handlers."""

    def __init__(self, registry: SessionRegistry):
        self.registry = registry
        self._app: Application | None = None
        self.observers = None  # ObserverBroadcaster | None, injected by __main__
        self.use_proxy: bool = False
        self._registered_labels: set[str] = set()  # cached to skip redundant setMyCommands
        self._keyboard_level: str = "main"  # "main", "templates", "commands", "models"
        self._template_map: dict[str, str] = {label: prompt for label, prompt in QUICK_TEMPLATES}
        self._command_map: dict[str, str] = {label: cmd for label, cmd in QUICK_COMMANDS}
        self._model_map: dict[str, str] = {label: cmd for label, cmd in MODEL_CHOICES}
        self._last_pinned_text: str = ""  # dedup pinned message edits

    async def start(self) -> None:
        global _bot_instance
        _bot_instance = self

        self.use_proxy = not await self._test_direct()
        builder = ApplicationBuilder().token(BOT_TOKEN)
        if self.use_proxy:
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
        self._app.add_handler(CommandHandler("stop", self._handle_stop_cmd))
        self._app.add_handler(CommandHandler("kill", self._handle_kill_cmd))
        self._app.add_handler(CommandHandler("new", self._handle_new_cmd))
        # Media handler: photos and documents → save file, inject prompt
        self._app.add_handler(MessageHandler(
            (filters.PHOTO | filters.Document.ALL) & filters.Chat(int(CHAT_ID)),
            self._handle_file,
        ))
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
        await self._update_bot_commands()

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def recover_sessions(self) -> None:
        """Clean up orphaned busy messages from a previous daemon lifecycle."""
        if not self._app:
            return
        bot = self._app.bot
        live_sessions = set(await inject.list_sessions())

        for name, sess in self.registry.all_sessions().items():
            if not sess.busy_msg_id or sess.busy_msg_id <= 0:
                continue

            orphaned_id = sess.busy_msg_id
            sess.busy_msg_id = None  # clear synchronously before any await
            is_alive = name in live_sessions
            label = html_mod.escape(sess.label)

            if is_alive:
                text = f"🔄 <b>{label}</b> · Daemon restarted"
            else:
                text = f"🔴 <b>{label}</b> · Session ended"

            try:
                await bot.edit_message_text(
                    text, chat_id=CHAT_ID, message_id=orphaned_id,
                    parse_mode="HTML",
                )
            except Exception:
                pass  # message too old or already deleted
            log.info("Recovered orphaned busy msg %d for [%s] (alive=%s)",
                     orphaned_id, sess.label, is_alive)

        self.registry.mark_dirty()

    async def _update_bot_commands(self) -> None:
        """Register bot commands (/ menu) and update persistent keyboard.

        Skips API call if session labels haven't changed since last update.
        """
        if not self._app:
            return

        # Collect live session labels
        labels = set()
        for name, sess in self.registry.all_sessions().items():
            if sess.status != Status.GONE and sess.label:
                labels.add(sess.label)

        if labels == self._registered_labels:
            return  # no change

        # Build command list: static + dynamic session labels
        commands = [
            BotCommand("status", "Show all sessions"),
            BotCommand("stop", "Stop active session"),
            BotCommand("kill", "Kill a session (destroy)"),
            BotCommand("new", "Launch new session"),
        ]
        for label in sorted(labels):
            commands.append(BotCommand(label, f"Send to [{label}]"))

        try:
            await self._app.bot.set_my_commands(commands)
            self._registered_labels = labels
            log.info("Bot commands updated: status, stop + %s",
                     ", ".join(sorted(labels)) or "(none)")
        except Exception:
            log.warning("Failed to set bot commands", exc_info=True)

        # Send/update persistent keyboard (always main — session labels changed)
        await self._send_keyboard(level="main")

    @staticmethod
    def _build_button_rows(labels: list[str], per_row: int = 3) -> list[list[KeyboardButton]]:
        """Pack labels into rows of KeyboardButtons."""
        rows = []
        for i in range(0, len(labels), per_row):
            rows.append([KeyboardButton(lbl) for lbl in labels[i:i + per_row]])
        return rows

    async def _send_keyboard(self, level: str | None = None) -> None:
        """Send a message with the persistent keyboard.

        Args:
            level: Which keyboard to show — "main", "templates", "commands",
                   or "models".  Defaults to current ``_keyboard_level``.
        """
        if not self._app:
            return

        if level is None:
            level = self._keyboard_level

        if level == "templates":
            rows = self._build_button_rows([lbl for lbl, _ in QUICK_TEMPLATES])
            rows.append([KeyboardButton(BACK_BUTTON)])
            msg_text = "\U0001f4cb Templates"
        elif level == "commands":
            rows = self._build_button_rows([lbl for lbl, _ in QUICK_COMMANDS])
            rows.append([KeyboardButton(MODELS_BUTTON), KeyboardButton(BACK_BUTTON)])
            msg_text = "\U0001f39b Commands"
        elif level == "models":
            rows = self._build_button_rows([lbl for lbl, _ in MODEL_CHOICES])
            rows.append([KeyboardButton(BACK_BUTTON)])
            msg_text = "\U0001f916 Model"
        else:
            # Main keyboard: session labels + command/nav rows
            labels = sorted(
                sess.label for sess in self.registry.all_sessions().values()
                if sess.status != Status.GONE and sess.label
            )
            rows = []
            if labels:
                rows = self._build_button_rows(labels)
            rows.append([KeyboardButton("status"), KeyboardButton("stop"), KeyboardButton("kill")])
            rows.append([KeyboardButton(TEMPLATES_BUTTON), KeyboardButton(COMMANDS_BUTTON)])
            msg_text = "\u2328\ufe0f"

        self._keyboard_level = level

        keyboard = ReplyKeyboardMarkup(
            rows,
            resize_keyboard=True,
        )

        try:
            await self._app.bot.send_message(
                CHAT_ID, msg_text,
                reply_markup=keyboard,
            )
        except Exception:
            log.warning("Failed to send keyboard", exc_info=True)

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

    async def _maybe_update_bot_name(self, session_name: str) -> None:
        """Update pinned status message to reflect the active session."""
        if not self._app:
            return
        sess = self.registry.get(session_name)
        if not sess:
            return
        model = f" · {sess.model_name}" if sess.model_name else ""
        text = f"📌 <b>{sess.label}</b>{model}"
        if text == self._last_pinned_text:
            return  # skip redundant edit
        chat = int(CHAT_ID)
        try:
            if self.registry.pinned_msg_id:
                await self._app.bot.edit_message_text(
                    text, chat, self.registry.pinned_msg_id,
                    parse_mode="HTML",
                )
            else:
                msg = await self._app.bot.send_message(
                    chat, text, parse_mode="HTML",
                )
                await self._app.bot.pin_chat_message(chat, msg.message_id, disable_notification=True)
                self.registry.pinned_msg_id = msg.message_id
                self.registry.mark_dirty()
            self._last_pinned_text = text
        except Exception:
            log.debug("Pinned message update failed", exc_info=True)

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
                reply_markup=self._build_stop_keyboard(sess.name),
            )
            return msg.message_id
        except Exception:
            log.warning("Failed to send busy message", exc_info=True)
            return None

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        """Format token count: 1.2k, 15k, 150k, etc."""
        if n >= 100_000:
            return f"{n // 1000}k"
        if n >= 1_000:
            return f"{n / 1000:.1f}k"
        return str(n)

    def _build_busy_text(self, label: str, verb: str, sess: TrackedSession) -> str:
        """Build the animated busy message text with tool history."""
        elapsed = ""
        if sess.busy_started_at:
            secs = int(time.monotonic() - sess.busy_started_at)
            if secs >= 2:
                elapsed = f" {secs}s"
        text = f"⚙️ <b>{html_mod.escape(label)}</b> · {html_mod.escape(verb)}…{elapsed}"
        # Show tool history — collapse old done tools if too many
        history = sess.tool_history
        max_visible = 15
        if len(history) <= max_visible:
            visible = history
            hidden_done = 0
        else:
            # Count done tools that will be hidden
            hidden = history[:-max_visible]
            hidden_done = sum(1 for _, d in hidden if d)
            visible = history[-max_visible:]
        if hidden_done:
            text += f"\n✅ <i>{hidden_done} earlier tool{'s' if hidden_done != 1 else ''}</i>"
        # Build a map of history_idx → started_at for live subagent elapsed time
        _subagent_started: dict[int, float] = {}
        for info in sess.active_subagents.values():
            idx = info.get("history_idx")
            if idx is not None:
                _subagent_started[idx] = info["started_at"]
        # Compute offset into tool_history for visible slice indices
        _vis_offset = len(history) - len(visible)
        for i, (summary, done) in enumerate(visible):
            if done == "failed":
                text += f"\n❌ <code>{html_mod.escape(summary)}</code>"
            elif done:
                text += f"\n✅ <code>{html_mod.escape(summary)}</code>"
            else:
                display = summary
                # Append live elapsed time for active subagent entries
                started_at = _subagent_started.get(_vis_offset + i)
                if started_at:
                    sa_secs = int(time.monotonic() - started_at)
                    if sa_secs >= 60:
                        display = f"{summary} ({sa_secs // 60}m {sa_secs % 60}s)"
                    elif sa_secs >= 2:
                        display = f"{summary} ({sa_secs}s)"
                text += f"\n⏳ <code>{html_mod.escape(display)}</code>"
        # Append inline permission display if active
        if sess.pending_permission:
            perm = sess.pending_permission
            if perm.get("ask_question"):
                q = perm["question"]
                text += f"\n\n❓ {html_mod.escape(q[:120])}"
                for i, opt in enumerate(perm.get("options", [])):
                    opt_label = opt.get("label", f"Option {i+1}")
                    desc = opt.get("description", "")
                    text += f"\n  {i+1}. {html_mod.escape(opt_label)}"
                    if desc:
                        text += f" — {html_mod.escape(desc[:60])}"
            else:
                tool_summary = perm.get("tool_summary", "Permission needed")
                text += f"\n\n🔐 <code>{html_mod.escape(tool_summary)}</code>"
        return text

    def _build_session_dashboard(self, sess: TrackedSession) -> str:
        """Build a rich HTML dashboard for a session (used on switch)."""
        # Status icon
        status_icons = {
            Status.IDLE: "\U0001f7e2",       # green circle
            Status.BUSY: "\u2699\ufe0f",     # gear
            Status.INTERACTIVE: "\u2753",     # question mark
            Status.GONE: "\U0001f534",        # red circle
            Status.UNKNOWN: "\U0001f534",     # red circle
        }
        icon = status_icons.get(sess.status, "\U0001f534")
        status_str = sess.status.name.lower()

        # Elapsed time for BUSY sessions
        elapsed = ""
        if sess.status == Status.BUSY and sess.busy_started_at:
            secs = int(time.monotonic() - sess.busy_started_at)
            if secs >= 60:
                elapsed = f" {secs // 60}m{secs % 60}s"
            elif secs >= 2:
                elapsed = f" {secs}s"

        header = f"{icon} <b>[{html_mod.escape(sess.label)}]</b> \u00b7 {status_str}{elapsed}"

        # Read fresh data from statusLine file
        sl = self._read_status_file(sess.name)

        # Build table rows — omit rows with no meaningful data
        rows: list[str] = []

        model = (sl.get("model") if sl else None) or sess.model_name
        if model:
            rows.append(f"  Model   {html_mod.escape(model)}")

        ctx_pct = sl["ctx_pct"] if sl else (sess.last_token_pct or 0)
        if ctx_pct:
            filled = round(ctx_pct / 10)
            bar = "\u2588" * filled + "\u2591" * (10 - filled)
            rows.append(f"  Ctx     {ctx_pct}% {bar}")

        cost = sl["cost"] if sl else 0
        if cost and cost >= 0.01:
            rows.append(f"  Cost    ${cost:.2f}")

        # Output tokens — prefer fresh total from statusLine, fall back to cached delta
        output_tokens = (sl.get("total_output") if sl else 0) or sess.last_output_tokens
        if output_tokens:
            rows.append(f"  Output  {self._fmt_tokens(output_tokens)} tokens")

        # Lines changed — only show if non-zero
        if sess.last_lines_added or sess.last_lines_removed:
            parts = []
            if sess.last_lines_added:
                parts.append(f"+{sess.last_lines_added}")
            if sess.last_lines_removed:
                parts.append(f"-{sess.last_lines_removed}")
            rows.append(f"  Lines   {' '.join(parts)}")

        # Queue depth — only if non-empty
        if sess.pending_queue:
            rows.append(f"  Queue   {len(sess.pending_queue)} pending")

        # Last activity — from last_hook_at (monotonic)
        if sess.last_hook_at > 0:
            ago_s = int(time.monotonic() - sess.last_hook_at)
            if ago_s < 5:
                ago_str = "just now"
            elif ago_s < 60:
                ago_str = f"{ago_s}s ago"
            elif ago_s < 3600:
                ago_str = f"{ago_s // 60}m ago"
            else:
                ago_str = f"{ago_s // 3600}h{(ago_s % 3600) // 60}m ago"
            rows.append(f"  Active  {ago_str}")
        elif sess.status != Status.UNKNOWN:
            rows.append("  Active  unknown")

        # Assemble header + table
        parts_out = [header]
        if rows:
            parts_out.append("<code>" + "\n".join(rows) + "</code>")

        # Recent tool history — last 5 items
        tool_hist = sess.tool_history[-5:] if sess.tool_history else []
        if tool_hist:
            tool_lines = []
            for summary, done in tool_hist:
                if done == "failed":
                    t_icon = "\u274c"
                elif done:
                    t_icon = "\u2705"
                else:
                    t_icon = "\u23f3"
                tool_lines.append(f"  {t_icon} {html_mod.escape(summary[:60])}")
            parts_out.append("Recent:\n<code>" + "\n".join(tool_lines) + "</code>")

        return "\n\n".join(parts_out)

    async def _edit_busy_raw(self, msg_id: int, text: str,
                             reply_markup=None) -> bool | None:
        """Edit busy message with pre-built text.

        Returns True on success, False on transient error,
        None on permanent failure (message gone).
        """
        if not self._app:
            return False
        try:
            await self._app.bot.edit_message_text(
                text, chat_id=CHAT_ID, message_id=msg_id, parse_mode="HTML",
                reply_markup=reply_markup,
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
        keyboard = self._build_stop_keyboard(sess.name)
        first_tick = True
        try:
            while sess.busy_msg_id and sess.status == Status.BUSY:
                # First tick at 1.5s for quick stats display, then normal interval
                await asyncio.sleep(1.5 if first_tick else BUSY_EDIT_INTERVAL)
                first_tick = False
                if not sess.busy_msg_id or sess.status != Status.BUSY:
                    break
                # Debounce: skip if any handler edited the busy msg recently
                if time.monotonic() - sess.last_tool_edit_at < BUSY_EDIT_INTERVAL:
                    # Still send typing (no edit to cancel it)
                    try:
                        await self._app.bot.send_chat_action(int(CHAT_ID), "typing")
                    except Exception:
                        pass
                    continue
                verb = verbs[idx % len(verbs)]
                idx += 1
                text = self._build_busy_text(sess.label, verb, sess)
                result = await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                if result is True:
                    sess.last_tool_edit_at = time.monotonic()
                elif result is None:
                    sess.busy_msg_id = None  # message gone
                    break
                # Send typing AFTER edit (edit cancels typing indicator)
                try:
                    await self._app.bot.send_chat_action(int(CHAT_ID), "typing")
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    def _start_animation(self, sess: TrackedSession) -> None:
        """Start the spinner animation task, cancelling any existing one."""
        self._stop_animation(sess)
        sess.animate_task = asyncio.create_task(self._animate_busy(sess))

    async def _animate_compact(self, sess: TrackedSession) -> None:
        """Dot animation while compacting: . → .. → ... → loop."""
        dots = [".", "..", "..."]
        idx = 0
        try:
            while sess.busy_msg_id and sess.busy_msg_id > 0:
                await asyncio.sleep(1.0)
                if not sess.busy_msg_id or sess.busy_msg_id < 0:
                    break
                dot = dots[idx % len(dots)]
                idx += 1
                text = f"🔄 <b>{html_mod.escape(sess.label)}</b> · Compacting{dot}"
                result = await self._edit_busy_raw(sess.busy_msg_id, text)
                if result is None:
                    sess.busy_msg_id = None
                    break
        except asyncio.CancelledError:
            pass

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
        # Clear stale busy state from previous lifecycle (e.g. GONE → BUSY).
        # If busy_msg_id is set but the animation task is dead, the previous
        # cycle ended abnormally — reset so we can send a fresh busy message.
        if (sess.busy_msg_id and sess.busy_msg_id > 0
                and (not sess.animate_task or sess.animate_task.done())):
            log.debug("[%s] Clearing stale busy_msg_id=%s (animation dead)",
                      sess.label, sess.busy_msg_id)
            sess.busy_msg_id = None
        if sess.busy_msg_id:
            return  # already showing busy (or sentinel claimed by other coroutine)
        sess.busy_msg_id = -1  # sentinel: claim slot before async yield
        self._stop_animation(sess)
        sess.last_tool_summary = ""
        sess.tool_history.clear()
        sess.active_subagents.clear()
        sess.pending_permission = None
        sess.last_token_pct = 0
        sess.last_output_tokens = 0
        sess.output_baseline = None  # lazy: set on first statusLine read this cycle
        sess.lines_added_baseline = None
        sess.lines_removed_baseline = None
        sess.last_lines_added = 0
        sess.last_lines_removed = 0
        sess.busy_started_at = time.monotonic()
        msg_id = await self.send_busy(sess)
        if msg_id:
            # Send typing AFTER the busy message (sending a message cancels typing)
            try:
                await self._app.bot.send_chat_action(int(CHAT_ID), "typing")
            except Exception:
                pass
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

        # Keep pinned message current on every notification
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        bot = self._app.bot
        label = sess.label

        # ── Pinned message refresh (e.g. model changed) ──
        if event == "pinned_update":
            return  # _maybe_update_bot_name already fired at top

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
            # Update tool history — mark previous as done, append new
            if tool_summary:
                # Append new tool as in-progress (PostToolUse marks it done)
                sess.tool_history.append((tool_summary, False))
                sess.last_tool_summary = tool_summary
            # Skip edit if busy msg not ready yet (animation will pick up cached stats)
            if not sess.busy_msg_id or sess.busy_msg_id < 0 or not tool_summary:
                return
            now = time.monotonic()
            # Debounce: only edit if enough time since ANY busy msg edit
            if now - sess.last_tool_edit_at >= BUSY_EDIT_INTERVAL:
                keyboard = self._build_stop_keyboard(sess.name)
                text = self._build_busy_text(label, "Working", sess)
                result = await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                if result is True:
                    sess.last_tool_edit_at = now
                elif result is None:
                    sess.busy_msg_id = None  # message gone, stop editing
                    self._stop_animation(sess)
            return

        if event in ("tool_done", "tool_failed"):
            # PostToolUse / PostToolUseFailure — mark tool as done or failed
            tool_summary = context.get("tool_summary", "")
            mark = "failed" if event == "tool_failed" else True
            if tool_summary:
                for i, (s, done) in enumerate(sess.tool_history):
                    if s == tool_summary and not done:
                        sess.tool_history[i] = (s, mark)
                        break
                else:
                    # No exact match — mark the last undone tool
                    for i in range(len(sess.tool_history) - 1, -1, -1):
                        if not sess.tool_history[i][1]:
                            sess.tool_history[i] = (sess.tool_history[i][0], mark)
                            break
            # Update display (debounced — animation picks up state if skipped)
            now = time.monotonic()
            if (sess.busy_msg_id and sess.busy_msg_id > 0
                    and now - sess.last_tool_edit_at >= BUSY_EDIT_INTERVAL):
                keyboard = self._build_stop_keyboard(sess.name)
                text = self._build_busy_text(label, "Working", sess)
                if await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard):
                    sess.last_tool_edit_at = now
            return

        if event == "subagent_start":
            agent_type = context.get("agent_type", "agent")
            agent_id = context.get("agent_id", "")
            # Append to tool_history SYNCHRONOUSLY before any await
            summary = f"\U0001f916 {agent_type}"
            sess.tool_history.append((summary, False))
            history_idx = len(sess.tool_history) - 1
            # Store index in active_subagents so SubagentStop can find it
            if agent_id and agent_id in sess.active_subagents:
                sess.active_subagents[agent_id]["history_idx"] = history_idx
            # Edit busy message if ready (debounced)
            now = time.monotonic()
            if (sess.busy_msg_id and sess.busy_msg_id > 0
                    and now - sess.last_tool_edit_at >= BUSY_EDIT_INTERVAL):
                keyboard = self._build_stop_keyboard(sess.name)
                text = self._build_busy_text(label, "Working", sess)
                if await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard):
                    sess.last_tool_edit_at = now
            return

        if event == "subagent_stop":
            agent_type = context.get("agent_type", "agent")
            elapsed = context.get("elapsed", 0.0)
            history_idx = context.get("history_idx")
            # Format elapsed time
            if elapsed >= 60:
                elapsed_str = f"{int(elapsed) // 60}m {int(elapsed) % 60}s"
            elif elapsed >= 1:
                elapsed_str = f"{int(elapsed)}s"
            else:
                elapsed_str = ""
            suffix = f" ({elapsed_str})" if elapsed_str else ""
            done_summary = f"\U0001f916 {agent_type}{suffix}"
            # Mark the matching tool_history entry as done SYNCHRONOUSLY
            if history_idx is not None and 0 <= history_idx < len(sess.tool_history):
                sess.tool_history[history_idx] = (done_summary, True)
            else:
                # No matching start (daemon restart?) — append as done entry
                sess.tool_history.append((done_summary, True))
            # Edit busy message if ready (debounced)
            now = time.monotonic()
            if (sess.busy_msg_id and sess.busy_msg_id > 0
                    and now - sess.last_tool_edit_at >= BUSY_EDIT_INTERVAL):
                keyboard = self._build_stop_keyboard(sess.name)
                text = self._build_busy_text(label, "Working", sess)
                if await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard):
                    sess.last_tool_edit_at = now
            return

        if event == "compacting":
            # Context compaction started — show dot animation
            self._stop_animation(sess)
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                text = f"🔄 <b>{html_mod.escape(label)}</b> · Compacting"
                await self._edit_busy_raw(sess.busy_msg_id, text)
            else:
                # No busy message — send a new one
                try:
                    text = f"🔄 <b>{html_mod.escape(label)}</b> · Compacting"
                    msg = await bot.send_message(
                        CHAT_ID, text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                    sess.busy_msg_id = msg.message_id
                except Exception:
                    log.warning("Failed to send compact message", exc_info=True)
            # Start dot animation
            sess.animate_task = asyncio.create_task(
                self._animate_compact(sess))
            if self.observers:
                obs_text = f"🔄 <b>{html_mod.escape(label)}</b> · Compacting"
                asyncio.create_task(self.observers.broadcast(obs_text))
            return

        if event == "context_warning":
            ctx_pct = context.get("context_pct", 0)
            warn_text = (f"⚠️ <b>{html_mod.escape(label)}</b> · Context at "
                         f"{ctx_pct}% — auto-compact soon")
            try:
                keyboard = self._build_compact_keyboard(sess.name)
                await bot.send_message(CHAT_ID, warn_text, parse_mode="HTML",
                                       reply_markup=keyboard)
            except Exception:
                pass
            if self.observers:
                asyncio.create_task(self.observers.broadcast(warn_text))
            return

        if event == "stale_busy":
            minutes = context.get("minutes", 10)
            stale_text = (f"⚠️ <b>{html_mod.escape(label)}</b> · Busy for "
                          f"{minutes}+ min with no activity — may be stalled")
            try:
                keyboard = self._build_stop_keyboard(sess.name)
                await bot.send_message(CHAT_ID, stale_text, parse_mode="HTML",
                                       reply_markup=keyboard)
            except Exception:
                pass
            if self.observers:
                asyncio.create_task(self.observers.broadcast(stale_text))
            return

        if event == "compact_done":
            # Compaction finished — show delta, then resume busy animation
            before_pct = context.get("before_pct", 0)
            after_pct = context.get("after_pct", 0)
            self._stop_animation(sess)
            text = (f"📦 <b>{html_mod.escape(label)}</b> · "
                    f"Compacted: {before_pct}% → {after_pct}%")
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                result = await self._edit_busy_raw(sess.busy_msg_id, text)
                if result is None:
                    sess.busy_msg_id = None
            else:
                try:
                    msg = await bot.send_message(
                        CHAT_ID, text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                    sess.busy_msg_id = msg.message_id
                except Exception:
                    log.warning("Failed to send compact_done message", exc_info=True)
            if self.observers:
                asyncio.create_task(self.observers.broadcast(text))
            # Brief pause so user can read the delta, then resume busy animation
            await asyncio.sleep(2.0)
            sess.last_token_pct = after_pct
            self._start_animation(sess)
            return

        if event == "session_end":
            # Session exited — clean up busy state and alert user
            self._stop_animation(sess)
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                try:
                    await bot.delete_message(chat_id=CHAT_ID, message_id=sess.busy_msg_id)
                except Exception:
                    pass
                sess.busy_msg_id = None
            source = context.get("source", "unknown")
            source_labels = {
                "clear": "cleared",
                "logout": "logged out",
                "prompt_input_exit": "exited",
                "bypass_permissions_disabled": "permissions error",
                "disappeared": "crashed or killed",
                "other": "exited unexpectedly",
                "unknown": "exited",
            }
            reason = source_labels.get(source, "exited")
            text = f"🔴 <b>{html_mod.escape(label)}</b> · Session {reason}"
            try:
                await bot.send_message(CHAT_ID, text, parse_mode="HTML")
            except Exception:
                log.warning("Failed to send session_end notification", exc_info=True)
            if self.observers:
                asyncio.create_task(self.observers.broadcast(text))
            return

        if sess.status == Status.IDLE:
            # Mark all tools as done
            sess.tool_history = [(s, True) for s, _ in sess.tool_history]
            sess.active_subagents.clear()
            # Stop animation and clean up busy message
            self._stop_animation(sess)
            sess.pending_permission = None  # clear stale inline permission if any
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

            # ── API error detection → friendly message + retry button ──
            error_source = raw_md or summary or ""
            friendly_error = _detect_api_error(error_source)
            if friendly_error:
                text = (f"⚠️ <b>{html_mod.escape(label)}</b> · {friendly_error}")
                keyboard = (self._build_retry_keyboard(sess.name)
                            if sess.last_prompt else None)
                try:
                    msg = await bot.send_message(
                        CHAT_ID, text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                        reply_markup=keyboard,
                    )
                    self.registry.track_message(msg.message_id, sess.name)
                    await self._maybe_update_bot_name(sess.name)
                except Exception:
                    log.warning("Failed to send error notification", exc_info=True)
                if self.observers:
                    asyncio.create_task(self.observers.broadcast(text))
                # Don't clear trigger_msg_id — retry needs it
                # Don't flush pending queue — nothing was processed
                return

            # Compute elapsed time since BUSY started
            elapsed_str = ""
            if sess.busy_started_at:
                elapsed_s = int(time.monotonic() - sess.busy_started_at)
                if elapsed_s >= 60:
                    elapsed_str = f"{elapsed_s // 60}m {elapsed_s % 60}s"
                elif elapsed_s > 0:
                    elapsed_str = f"{elapsed_s}s"
            # Lines changed this turn
            lines_str = ""
            if sess.last_lines_added or sess.last_lines_removed:
                lines_str = f"+{sess.last_lines_added} -{sess.last_lines_removed}"
            # Build suffix: combine non-empty parts with comma
            parts = [p for p in (elapsed_str, lines_str) if p]
            suffix = f" ({', '.join(parts)})" if parts else ""
            text = f"✅ <b>{html_mod.escape(label)}</b> · Finished{suffix}"
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
            try:
                msg = await bot.send_message(
                    CHAT_ID, text, parse_mode="HTML",
                    reply_to_message_id=sess.trigger_msg_id,
                )
            except Exception as e:
                # Retry once on flood control — this is the response, can't lose it
                retry_after = getattr(e, "retry_after", None)
                if retry_after:
                    log.warning("[%s] Flood control on Finished, retrying in %ds", label, retry_after)
                    await asyncio.sleep(retry_after)
                    msg = await bot.send_message(
                        CHAT_ID, text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                else:
                    raise
            sess.trigger_msg_id = None  # reply cycle complete
            self.registry.mark_dirty()
            self.registry.track_message(msg.message_id, sess.name)
            await self._maybe_update_bot_name(sess.name)
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

            # Broadcast to observer bots (text only, or text + document)
            if self.observers:
                if send_file and file_content:
                    doc_bytes = file_content.encode("utf-8")
                    asyncio.create_task(self.observers.broadcast_document(
                        text, doc_bytes, f"{label}_response.txt"))
                else:
                    asyncio.create_task(self.observers.broadcast(text))

            # Flush next queued message (one at a time, rest flush on next IDLE)
            if sess.pending_queue:
                queued_text, queued_trigger = sess.pending_queue.pop(0)
                sess.trigger_msg_id = queued_trigger
                sess.last_prompt = queued_text
                self.registry.mark_dirty()
                ok = await inject.send_text_and_enter(sess.name, queued_text)
                if ok:
                    self.registry.transition(sess.name, Status.BUSY)
                    await self._send_busy_and_animate(sess)
                    log.info("[%s] Flushed queued: %s", sess.label, queued_text[:80])

        elif sess.status == Status.INTERACTIVE:
            self._stop_animation(sess)
            tool_info = context.get("tool_info")
            selector_text = context.get("selector_text", "")
            selector_options = context.get("selector_options")

            # Can we inline into the existing busy message?
            can_inline = sess.busy_msg_id and sess.busy_msg_id > 0

            if can_inline:
                # Set pending_permission SYNCHRONOUSLY before any await
                # (lesson: claim state before async yield to prevent races)
                if tool_info and tool_info["name"] == "AskUserQuestion":
                    questions = tool_info["input"].get("questions", [])
                    if questions:
                        q = questions[0]
                        options = q.get("options", [])
                        is_multi = q.get("multiSelect", False)
                        log.info("[%s] AskUserQuestion: multi_select=%s, %d options, q_keys=%s",
                                 sess.label, is_multi, len(options), list(q.keys()))
                        sess.pending_permission = {
                            "ask_question": True,
                            "question": q.get("question", "?"),
                            "options": options,
                            "questions": questions,
                            "current_idx": 0,
                            "multi_select": is_multi,
                            "cursor_pos": 0,
                            "selected": set(),
                            "tool_info": tool_info,
                            "wait_started_at": time.monotonic(),
                        }
                        keyboard = self._build_inline_ask_keyboard(
                            sess.name, options,
                            multi_select=is_multi)
                    else:
                        # AskUserQuestion detected but no questions data (transcript
                        # not flushed). Degrade to Allow/Deny — Allow sends Enter.
                        sess.pending_permission = {
                            "tool_summary": "AskUserQuestion (loading…)",
                            "tool_info": tool_info,
                            "wait_started_at": time.monotonic(),
                        }
                        keyboard = self._build_permission_keyboard(sess.name)
                else:
                    tool_summary = tool_info["summary"] if tool_info else "Permission needed"
                    sess.pending_permission = {
                        "tool_summary": tool_summary,
                        "tool_info": tool_info,
                        "wait_started_at": time.monotonic(),
                    }
                    keyboard = self._build_permission_keyboard(sess.name)

                text = self._build_busy_text(label, "Waiting", sess)
                result = await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                if result is None:
                    # Busy message was deleted — fall back to separate message
                    sess.pending_permission = None
                    sess.busy_msg_id = None
                    can_inline = False

            if not can_inline:
                # Fallback: send separate message (original behavior)
                sess.pending_permission = None  # ensure clean state

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
                await self._maybe_update_bot_name(sess.name)

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

    def _build_stop_keyboard(self, session_name: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Stop", callback_data=f"{session_name}:stop"),
        ]])

    def _build_retry_keyboard(self, session_name: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Retry", callback_data=f"{session_name}:retry"),
        ]])

    def _build_compact_keyboard(self, session_name: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("📦 Compact Now", callback_data=f"{session_name}:compact"),
        ]])

    def _build_permission_keyboard(self, session_name: str) -> InlineKeyboardMarkup:
        """Permission buttons + Stop for inline permission."""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Allow", callback_data=f"{session_name}:allow"),
             InlineKeyboardButton("❌ Deny", callback_data=f"{session_name}:deny")],
            [InlineKeyboardButton("⏹ Stop", callback_data=f"{session_name}:stop")],
        ])

    def _build_inline_ask_keyboard(self, session_name: str, options: list,
                                    multi_select: bool = False,
                                    selected: set | None = None) -> InlineKeyboardMarkup:
        """AskUserQuestion option buttons + Stop for inline display.

        For multi_select: each option on its own row with ☑/⬜ prefix,
        plus a "✅ Submit" button row at the bottom.
        """
        sel = selected or set()
        rows = []
        if multi_select:
            for i, opt in enumerate(options[:4]):
                label = opt.get("label", f"Option {i+1}")
                prefix = "☑" if i in sel else "⬜"
                rows.append([InlineKeyboardButton(
                    f"{prefix} {label}",
                    callback_data=f"{session_name}:opt{i}")])
            rows.append([InlineKeyboardButton(
                "✅ Submit", callback_data=f"{session_name}:submit")])
        else:
            buttons = []
            for i, opt in enumerate(options[:4]):
                opt_label = opt.get("label", f"Option {i+1}")
                buttons.append(InlineKeyboardButton(
                    opt_label, callback_data=f"{session_name}:opt{i}",
                ))
            if buttons:
                rows.append(buttons)
        rows.append([InlineKeyboardButton("⏹ Stop", callback_data=f"{session_name}:stop")])
        return InlineKeyboardMarkup(rows)

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
            await query.answer(ack)
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

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button tap."""
        query = update.callback_query
        cb_data = query.data or ""
        original_text = query.message.text or "" if query.message else ""

        if ":" not in cb_data:
            await query.answer("Invalid callback")
            return

        session_name, action = cb_data.split(":", 1)

        if action == "stop":
            sess = self.registry.get(session_name)
            if not sess:
                await query.answer("Session not found")
                return
            await self._stop_session(sess, query=query)
            return

        if action == "kill":
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name
            await query.answer(f"Killing {label}...")
            await self._kill_session_by_label(query, label)
            return

        if action == "retry":
            sess = self.registry.get(session_name)
            if not sess:
                await query.answer("Session not found")
                return
            if not sess.last_prompt:
                await query.answer("Nothing to retry")
                return
            if not await inject.is_alive(session_name):
                await query.answer(f"Session '{session_name}' not alive")
                return
            # Re-inject the last prompt (last_prompt stays set for retry-of-retry)
            prompt = sess.last_prompt
            ok = await inject.send_text_and_enter(session_name, prompt)
            if ok:
                await query.answer(f"Retrying [{sess.label}]")
                # Delete the error message — busy animation replaces it
                try:
                    await self._app.bot.delete_message(
                        chat_id=CHAT_ID,
                        message_id=query.message.message_id,
                    )
                except Exception:
                    pass
                self.registry.transition(session_name, Status.BUSY)
                await self._send_busy_and_animate(sess)
                log.info("[%s] Retry: %s", sess.label, prompt[:80])
            else:
                await query.answer("Failed to retry")
            return

        if action == "compact":
            sess = self.registry.get(session_name)
            if not sess:
                await query.answer("Session not found")
                return
            if not await inject.is_alive(session_name):
                await query.answer(f"Session '{session_name}' not found")
                return
            ok = await inject.send_text_and_enter(session_name, "/compact")
            if ok:
                await query.answer(f"Compacting [{sess.label}]")
                try:
                    await self._app.bot.delete_message(
                        chat_id=CHAT_ID,
                        message_id=query.message.message_id,
                    )
                except Exception:
                    pass
                log.info("[%s] Compact triggered by user", sess.label)
            else:
                await query.answer("Failed to send /compact")
            return

        if action == "clear_gone":
            # Remove all dead sessions from registry
            removed = []
            for name, sess in list(self.registry.all_sessions().items()):
                if not await inject.is_alive(name):
                    removed.append(sess.label)
                    self.registry.remove(name)
            if removed:
                await query.answer(f"Cleared {len(removed)} session(s)")
                try:
                    await query.edit_message_text(
                        f"Cleared: {', '.join(removed)}", parse_mode="HTML",
                    )
                except Exception:
                    pass
                log.info("Cleared gone sessions: %s", removed)
            else:
                await query.answer("No gone sessions to clear")
            return

        is_option = action.startswith("opt") and action[3:].isdigit()

        if action not in ACTION_VERBS and not is_option and action != "submit":
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
        perm = sess.pending_permission or {}
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
                await asyncio.sleep(0.1)
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
                await query.answer(f"{toggled} {opt_label}")

                # Rebuild keyboard with updated checkmarks
                keyboard = self._build_inline_ask_keyboard(
                    session_name, perm["options"],
                    multi_select=True, selected=selected)
                text = self._build_busy_text(sess.label, "Waiting", sess)
                await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                log.info("[%s] Multi-select toggle: opt%d (%s), selected=%s",
                         sess.label, option_index, toggled, selected)
            else:
                await query.answer("Failed to send keys")
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
                await asyncio.sleep(0.15)
                ok = await inject.send_keys(session_name, "Enter")

            if ok:
                await query.answer(f"✅ {verb[:180]}")

                # Collapse into tool_history
                collapsed = f"❓ {perm.get('question', '?')[:40]} → {verb}"
                sess.tool_history.append((collapsed, True))

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
                    await asyncio.sleep(0.3)
                    keyboard = self._build_inline_ask_keyboard(
                        session_name, next_options,
                        multi_select=next_multi)
                    text = self._build_busy_text(sess.label, "Waiting", sess)
                    await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                    log.info("[%s] Multi-select submit, advanced to Q%d/%d",
                             sess.label, next_idx + 1, len(questions))
                else:
                    # Last question — done; discount wait time from elapsed timer
                    wait_start = perm.get("wait_started_at", 0)
                    if wait_start and sess.busy_started_at:
                        sess.busy_started_at += time.monotonic() - wait_start
                    sess.pending_permission = None
                    self.registry.transition(session_name, Status.BUSY)
                    keyboard = self._build_stop_keyboard(session_name)
                    text = self._build_busy_text(sess.label, "Working", sess)
                    await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                    self._start_animation(sess)
                    log.info("[%s] Multi-select submit complete", sess.label)
            else:
                await query.answer("Failed to send keys")
            return

        elif is_option:
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

            if sess.pending_permission:
                # Collapse current question into tool_history
                perm = sess.pending_permission
                if perm.get("ask_question"):
                    collapsed = f"❓ {perm['question'][:40]} → {verb}"
                else:
                    tool_summary = perm.get("tool_summary", "Permission")[:60]
                    collapsed = f"🔑 {tool_summary} → {verb}"
                sess.tool_history.append((collapsed, True))

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
                    await asyncio.sleep(0.3)  # let TUI process and auto-advance
                    keyboard = self._build_inline_ask_keyboard(
                        session_name, next_options,
                        multi_select=next_multi)
                    text = self._build_busy_text(sess.label, "Waiting", sess)
                    await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                    log.info("[%s] Multi-question: advanced to Q%d/%d",
                             sess.label, next_idx + 1, len(questions))
                else:
                    # Last question (or non-AskUserQuestion) — done
                    if perm.get("ask_question") and len(questions) > 1:
                        # Multi-question form: TUI auto-advances to Submit tab
                        # after last option selection. Send Enter to submit.
                        await asyncio.sleep(0.3)
                        await inject.send_keys(session_name, "Enter")
                    # Discount wait time from elapsed timer
                    wait_start = perm.get("wait_started_at", 0)
                    if wait_start and sess.busy_started_at:
                        sess.busy_started_at += time.monotonic() - wait_start
                    sess.pending_permission = None
                    # Transition back to BUSY and restart animation
                    self.registry.transition(session_name, Status.BUSY)
                    keyboard = self._build_stop_keyboard(session_name)
                    text = self._build_busy_text(sess.label, "Working", sess)
                    await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                    self._start_animation(sess)
                    log.info("[%s] Inline permission: %s", sess.label, verb)
            else:
                # Original behavior: edit the separate permission message
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
        """Handle /status command — rich per-session dashboard."""
        sessions = self.registry.all_sessions()
        if not sessions:
            discovered = await inject.list_sessions()
            if not discovered:
                await update.message.reply_text("No sessions found.")
                return
            for name in discovered:
                self.registry.get_or_create(name)
            sessions = self.registry.all_sessions()

        blocks = []
        has_gone = False
        for name, sess in sessions.items():
            alive = await inject.is_alive(name)
            # Reconcile: socket alive but status GONE → recover to IDLE
            if alive and sess.status == Status.GONE:
                self.registry.transition(name, Status.IDLE)
            icon = "🟢" if alive else "🔴"
            if not alive:
                has_gone = True
            status_str = sess.status.name.lower()
            if sess.status == Status.BUSY and sess.busy_started_at:
                elapsed_s = int(time.monotonic() - sess.busy_started_at)
                if elapsed_s >= 60:
                    status_str += f" {elapsed_s // 60}m{elapsed_s % 60}s"
                else:
                    status_str += f" {elapsed_s}s"
            # Read live data from statusLine file
            sl = self._read_status_file(name)
            # Build table rows
            rows = []
            model = (sl.get("model") if sl else None) or sess.model_name or "—"
            ctx_pct = sl["ctx_pct"] if sl else (sess.last_token_pct or 0)
            cost = f"${sl['cost']:.2f}" if sl and sl["cost"] >= 0.01 else "—"
            queue = str(len(sess.pending_queue)) if sess.pending_queue else "0"
            rows.append(f"  Model  {html_mod.escape(model)}")
            rows.append(f"  Ctx    {ctx_pct}%")
            rows.append(f"  Cost   {cost}")
            if sess.pending_queue:
                rows.append(f"  Queue  {queue}")
            # Last tool for BUSY sessions
            if sess.status == Status.BUSY and sess.tool_history:
                last_summary, last_done = sess.tool_history[-1]
                t_icon = "✅" if last_done and last_done != "failed" else ("❌" if last_done == "failed" else "⏳")
                rows.append(f"  Tool   {t_icon} {html_mod.escape(last_summary[:50])}")
            header = f"{icon} <b>{html_mod.escape(sess.label)}</b> · {status_str}"
            table = "\n".join(rows)
            blocks.append(f"{header}\n<code>{table}</code>")

        keyboard = None
        if has_gone:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Clear gone sessions", callback_data="_:clear_gone"),
            ]])
        await update.message.reply_text(
            "\n\n".join(blocks), parse_mode="HTML", reply_markup=keyboard,
        )
        asyncio.create_task(self._update_bot_commands())

    @staticmethod
    def _read_status_file(session_name: str) -> dict | None:
        """Read cost and context from the statusLine JSON file."""
        try:
            data = json.loads(Path(f"/tmp/claude-status-{session_name}.json").read_text())
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None
        ctx = data.get("context_window", {})
        pct = ctx.get("used_percentage")
        remaining = ctx.get("remaining_percentage")
        if pct is not None:
            ctx_pct = round(pct)
        elif remaining is not None:
            ctx_pct = round(100 - remaining)
        else:
            ctx_pct = 0
        return {
            "ctx_pct": ctx_pct,
            "cost": data.get("cost", {}).get("total_cost_usd", 0),
            "model": data.get("model", {}).get("display_name", ""),
            "total_output": ctx.get("total_output_tokens", 0),
        }

    async def _handle_stop_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /stop command — stop the last active session."""
        name = self.registry.last_active_session
        if not name:
            await update.message.reply_text("No active session to stop.")
            return
        sess = self.registry.get(name)
        if not sess or sess.status not in (Status.BUSY, Status.INTERACTIVE):
            label = sess.label if sess else "?"
            await update.message.reply_text(f"[{label}] is not busy.")
            return
        await self._stop_session(sess, update=update)

    async def _handle_kill_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /kill <label> — destroy a session entirely."""
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            # No label given — show inline keyboard with session choices
            sessions = self.registry.all_sessions()
            alive = [
                sess for sess in sessions.values()
                if sess.status != Status.GONE and sess.label
            ]
            if not alive:
                await update.message.reply_text("No sessions to kill.")
                return
            buttons = [
                [InlineKeyboardButton(f"💀 {sess.label}", callback_data=f"{sess.name}:kill")]
                for sess in alive
            ]
            await update.message.reply_text(
                "Which session to kill?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        target_label = parts[1].strip()
        await self._kill_session_by_label(update, target_label)

    async def _kill_session_by_label(self, source, target_label: str) -> None:
        """Kill a session by label. source is Update or CallbackQuery."""
        async def _reply(text: str) -> None:
            if hasattr(source, 'message') and source.message:
                await source.message.reply_text(text)
            else:
                await source.edit_message_text(text)

        # Find session
        sessions = self.registry.all_sessions()
        session_name = None
        for name, sess in sessions.items():
            if sess.label == target_label:
                session_name = name
                break

        if not session_name:
            session_name = f"claude-{target_label}"

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

    async def _handle_new_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new <name> [prompt] — launch a new Claude Code session."""
        text = update.message.text.strip()
        parts = text.split(maxsplit=2)  # /new <name> [prompt...]
        if len(parts) < 2:
            await update.message.reply_text(
                "Usage: /new &lt;name&gt; [initial prompt]\n"
                "Example: /new dev fix the auth bug",
                parse_mode="HTML",
            )
            return

        name = parts[1].strip()
        prompt = parts[2].strip() if len(parts) > 2 else ""

        status_msg = await update.message.reply_text(
            f"🚀 Launching <b>{html_mod.escape(name)}</b>...",
            parse_mode="HTML",
        )

        ok, err = await inject.launch_session(name)
        if not ok:
            await status_msg.edit_text(f"❌ {html_mod.escape(err)}")
            return

        session_name = f"claude-{name}"

        # Switch active session to the new one
        sess = self.registry.get_or_create(session_name)
        # Recover from GONE/UNKNOWN — we just verified the socket is alive
        if sess.status in (Status.GONE, Status.UNKNOWN):
            self.registry.transition(session_name, Status.IDLE)
        self.registry.last_active_session = session_name
        self.registry.mark_dirty()
        asyncio.create_task(self._maybe_update_bot_name(session_name))
        asyncio.create_task(self._update_bot_commands())

        # Queue the initial prompt if given — it'll drain on first IDLE
        if prompt:
            # Flatten newlines (lesson: newlines cause premature Enter)
            prompt = prompt.replace("\n", " — ")
            sess.pending_queue.append((prompt, update.message.message_id))
            self.registry.mark_dirty()

        await status_msg.edit_text(
            f"✅ <b>{html_mod.escape(name)}</b> launched"
            + (f"\n📝 Prompt queued" if prompt else ""),
            parse_mode="HTML",
        )
        log.info("Launched session %s (prompt=%s)", name, bool(prompt))

    async def _stop_by_label(self, update: Update, target_label: str) -> None:
        """Stop a session by its label."""
        sessions = self.registry.all_sessions()
        for name, sess in sessions.items():
            if sess.label == target_label:
                if sess.status not in (Status.BUSY, Status.INTERACTIVE):
                    await update.message.reply_text(f"[{target_label}] is not busy.")
                    return
                await self._stop_session(sess, update=update)
                return
        await update.message.reply_text(f"⚠️ Unknown session: {target_label}")

    async def _switch_session(self, update: Update, target_label: str) -> None:
        """Switch active session when bare /<label> is tapped (no prompt)."""
        # Find in registry
        for name, sess in self.registry.all_sessions().items():
            if sess.label == target_label:
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

    async def _handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle text messages — replies to notifications or /<label> commands."""
        text = update.message.text.strip()
        if not text:
            return

        # Keyboard navigation + button matching — MUST stay above /<label> handlers
        # because some buttons (e.g. "/compact") start with slash.
        if text == TEMPLATES_BUTTON:
            await self._send_keyboard(level="templates")
            return
        if text == COMMANDS_BUTTON:
            await self._send_keyboard(level="commands")
            return
        if text == MODELS_BUTTON:
            await self._send_keyboard(level="models")
            return
        if text == BACK_BUTTON:
            # Context-aware: go to parent of current level (models→commands, etc.)
            parent = KEYBOARD_PARENTS.get(self._keyboard_level, "main")
            await self._send_keyboard(level=parent)
            return

        # Quick template buttons — inject predefined prompt into active session
        if text in self._template_map:
            await self._send_template(update, self._template_map[text])
            return

        # Claude Code slash commands — inject instantly, no BUSY transition
        if text in self._command_map:
            await self._send_command(update, self._command_map[text])
            return

        # Model choices — instant commands
        if text in self._model_map:
            await self._send_command(update, self._model_map[text])
            return

        # /<label> <prompt> — direct send
        if text.startswith("/") and " " in text:
            parts = text.split(" ", 1)
            target_label = parts[0][1:]
            prompt_text = parts[1].strip()
            if target_label and prompt_text:
                if prompt_text.lower() == "stop":
                    await self._stop_by_label(update, target_label)
                    return
                await self._direct_send(update, target_label, prompt_text)
                return

        # Bare /<label> — switch active session (e.g. keyboard tap)
        if text.startswith("/") and " " not in text:
            target_label = text[1:]
            if target_label and target_label not in ("status", "stop", "kill"):
                await self._switch_session(update, target_label)
                return

        # Bare text matching keyboard buttons (no slash)
        text_lower = text.lower()
        if " " not in text and not text.startswith("/"):
            if text_lower == "status":
                await self._handle_status(update, ctx)
                return
            if text_lower == "stop":
                await self._handle_stop_cmd(update, ctx)
                return
            if text_lower == "kill":
                await self._handle_kill_cmd(update, ctx)
                return
            if text_lower == "new":
                await self._handle_new_cmd(update, ctx)
                return
            # Check if it matches a known session label
            for sess in self.registry.all_sessions().values():
                if sess.label == text and sess.status != Status.GONE:
                    await self._switch_session(update, text)
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

        self.registry.last_active_session = sess.name  # user is talking to this session now
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if session is busy — inject when it goes IDLE
        if sess.status == Status.BUSY:
            sess.pending_queue.append((text, update.message.message_id))
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] Queued (busy): %s", sess.label, text[:80])
            return

        sess.trigger_msg_id = update.message.message_id
        sess.last_prompt = text
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, text)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Sent text: %s", sess.label, text[:80])
        else:
            await update.message.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _handle_file(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photo/document messages — download file and inject prompt."""
        msg = update.message

        # Download the file
        try:
            FILE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())

            if msg.photo:
                # Use largest photo size
                tg_file = await msg.photo[-1].get_file()
                filename = f"{ts}_photo.jpg"
            elif msg.document:
                tg_file = await msg.document.get_file()
                raw_name = msg.document.file_name or "document"
                orig = Path(raw_name).name or "document"
                filename = f"{ts}_{orig}"
            else:
                return

            save_path = FILE_DOWNLOAD_DIR / filename
            await tg_file.download_to_drive(custom_path=str(save_path))
        except Exception:
            log.warning("File download failed", exc_info=True)
            await msg.reply_text("❌ Failed to download file")
            return

        # Construct prompt — keep it clean, let the file path do the work
        caption = msg.caption or ""
        if caption:
            prompt = f"{caption} {save_path}"
        elif msg.photo:
            prompt = f"Describe this image: {save_path}"
        else:
            prompt = f"Read and analyze this file: {save_path}"

        # Resolve target session (same pattern as _handle_message)
        reply_to = msg.reply_to_message
        if reply_to:
            sess = self.registry.get_session_by_msg(reply_to.message_id)
        else:
            name = self.registry.last_active_session
            sess = self.registry.get(name) if name else None

        if not sess:
            await msg.reply_text("⚠️ No active session")
            return

        self.registry.last_active_session = sess.name
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await msg.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if busy
        if sess.status == Status.BUSY:
            sess.pending_queue.append((prompt, msg.message_id))
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] File queued (busy): %s", sess.label, filename)
            return

        sess.trigger_msg_id = msg.message_id
        sess.last_prompt = prompt
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, prompt)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] File sent: %s", sess.label, filename)
        else:
            await msg.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _send_template(self, update: Update, prompt_text: str) -> None:
        """Inject a quick-template prompt into the last active session."""
        name = self.registry.last_active_session
        sess = self.registry.get(name) if name else None

        if not sess or sess.status == Status.GONE:
            await update.message.reply_text("⚠️ No active session")
            return

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if session is busy
        if sess.status == Status.BUSY:
            sess.pending_queue.append((prompt_text, update.message.message_id))
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] Template queued (busy): %s", sess.label, prompt_text[:80])
            return

        sess.trigger_msg_id = update.message.message_id
        sess.last_prompt = prompt_text
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, prompt_text)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Template sent: %s", sess.label, prompt_text[:80])
        else:
            await update.message.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _send_command(self, update: Update, command_text: str) -> None:
        """Inject an instant Claude Code slash command (no BUSY transition).

        Unlike _send_template(), this does NOT transition to BUSY or start
        animation. Slash commands like /model, /cost, /context complete
        instantly and produce no Claude response.
        """
        name = self.registry.last_active_session
        sess = self.registry.get(name) if name else None

        if not sess or sess.status == Status.GONE:
            await update.message.reply_text("⚠️ No active session")
            return

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # /clear is destructive — refuse while session is actively working
        if command_text == "/clear" and sess.status == Status.BUSY:
            await update.message.reply_text("⚠️ Can't clear while session is busy")
            return

        ok = await inject.send_text_and_enter(sess.name, command_text)
        if ok:
            await self._react(update, "✅")
            # Explicit feedback for model changes
            if command_text.startswith("/model "):
                model_arg = command_text.split(" ", 1)[1]
                await update.message.reply_text(
                    f"🔄 <b>{html_mod.escape(sess.label)}</b> → {html_mod.escape(model_arg)}",
                    parse_mode="HTML",
                )
            log.info("[%s] Command sent: %s", sess.label, command_text)
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
                sess.last_prompt = prompt_text
                self.registry.mark_dirty()
                self.registry.last_active_session = name  # user explicitly targeted this session
                asyncio.create_task(self._maybe_update_bot_name(name))
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
            new_sess.last_prompt = prompt_text
            self.registry.mark_dirty()
            self.registry.last_active_session = session_name
            asyncio.create_task(self._maybe_update_bot_name(session_name))
            ok = await inject.send_text_and_enter(session_name, prompt_text)
            if ok:
                await self._react(update, "👀")
                self.registry.transition(session_name, Status.BUSY)
                await self._send_busy_and_animate(new_sess)
            else:
                await update.message.reply_text(f"❌ Failed to send to [{target_label}]")
        else:
            await update.message.reply_text(f"⚠️ Unknown session: {target_label}")
