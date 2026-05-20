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
)
from telegram.error import BadRequest, Forbidden


from aipager.config import (
    BACK_BUTTON, CHAT_ID, COMMANDS_BUTTON,
    MODEL_CHOICES, MODELS_BUTTON,
    QUICK_COMMANDS, QUICK_TEMPLATES, TEMPLATES_BUTTON,
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





class KeyboardMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    @staticmethod
    def _build_button_rows(labels: list[str], per_row: int = 3) -> list[list[KeyboardButton]]:
        """Pack labels into rows of KeyboardButtons."""
        rows = []
        for i in range(0, len(labels), per_row):
            rows.append([KeyboardButton(lbl) for lbl in labels[i:i + per_row]])
        return rows

    async def _send_keyboard(
        self, level: str | None = None, chat_id: int | None = None,
    ) -> None:
        """Send a message with the persistent keyboard.

        Args:
            level: Which keyboard to show — "main", "templates", "commands",
                   or "models".  Defaults to current ``_keyboard_level``.
            chat_id: Target chat (defaults to the global ``CHAT_ID``). In
                   multi-scope mode the main keyboard's session buttons are
                   filtered to this chat's scope, so it never shows another
                   scope's labels.
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
                rows = self._build_button_rows(labels)
            rows.append([KeyboardButton("status"), KeyboardButton("stop"), KeyboardButton("kill")])
            rows.append([KeyboardButton(TEMPLATES_BUTTON), KeyboardButton(COMMANDS_BUTTON)])
            msg_text = "\u2328\ufe0f"

        self._keyboard_level = level

        keyboard = ReplyKeyboardMarkup(
            rows,
            resize_keyboard=True,
        )

        target = chat_id if chat_id is not None else CHAT_ID
        try:
            await self._app.bot.send_message(
                target, msg_text,
                reply_markup=keyboard,
            )
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
