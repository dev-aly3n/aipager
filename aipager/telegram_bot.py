"""Telegram bot — python-telegram-bot v22 async Application.

Single owner of all Telegram communication. Handles:
- CallbackQuery (button taps) → dtach_inject.send_keys()
- Message replies → dtach_inject.send_text_and_enter()
- /status command → show all sessions
- /<label> <prompt> → direct send to session
"""

from __future__ import annotations

import asyncio
import difflib
import html as html_mod
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

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
from telegram.error import BadRequest, Forbidden, RetryAfter

from aipager.dtach import inject
import random

from aipager.config import (
    BACK_BUTTON, BOT_TOKEN, BUSY_EDIT_INTERVAL, CHAT_ID, COMMANDS_BUTTON,
    FILE_DOWNLOAD_DIR, KEYBOARD_PARENTS, MODEL_CHOICES, MODELS_BUTTON,
    QUICK_COMMANDS, QUICK_TEMPLATES, SPINNER_VERBS, TEMPLATES_BUTTON,
)
from aipager.state import QUEUE_CAP, SessionRegistry, Status, TrackedSession
from aipager.team import (
    Role,
    Team,
    User as TeamUser,
    attribution_label,
    record_pending_user,
    remember_unauthorized,
)

# Sentinel returned by ``_authorize_callback`` in personal mode so
# callers can distinguish "auth passed in personal mode" from "auth
# passed in team mode and here is the TeamUser." Not exported.
_PERSONAL_MODE_SENTINEL = TeamUser(id=0, label="me", role=Role.ADMIN)

# Telegram's documented document upload ceiling is 50 MB; stay below it.
TELEGRAM_MAX_DOC_BYTES = 40 * 1024 * 1024
# Telegram bot API can DOWNLOAD files up to 20 MB via `getFile`. Anything
# above that fails before the file reaches us — better to check `file_size`
# from the update payload and warn the user up front than to attempt and
# fail with a vague "Failed to download file".
TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024
# Single-message text limit Telegram enforces.
TELEGRAM_MAX_TEXT_LEN = 4000
_TRUNC_SUFFIX = "\n\n…[truncated]"

# Throttle for "bot was blocked" log spam — daemon would otherwise emit
# one line per send attempt while the user has the bot blocked.
_LAST_BLOCKED_LOG_TS: float = 0.0
_BLOCKED_LOG_INTERVAL = 60.0


def _log_blocked_once(e: Exception) -> None:
    """Log a friendly explanation when the user has blocked the bot.
    Suppresses subsequent identical events for one minute."""
    global _LAST_BLOCKED_LOG_TS
    now = time.monotonic()
    if now - _LAST_BLOCKED_LOG_TS < _BLOCKED_LOG_INTERVAL:
        return
    _LAST_BLOCKED_LOG_TS = now
    log.error(
        "Telegram refuses to deliver: %s\n"
        "  → The Telegram user has blocked or deleted the bot.\n"
        "  → Open the bot in Telegram and tap Start to unblock, then\n"
        "    new notifications will resume.",
        e,
    )


def _is_bot_blocked(e: Exception) -> bool:
    """Best-effort detection of 'user blocked the bot' across PTB versions."""
    if isinstance(e, Forbidden):
        return True
    msg = str(e).lower()
    return "bot was blocked" in msg or "blocked by the user" in msg


# Item 4.4 — Write/Edit diff preview.
#
# When claude calls a Write or Edit tool, we render the change as a
# unified diff and send it as a Telegram reply threaded under the busy
# message. This gives users on-the-go review without needing to ssh in.
#
# Trade-off: every Write/Edit is one message. The body is capped to
# `_DIFF_MAX_LINES` / `_DIFF_MAX_CHARS` to keep the chat readable. Users
# who find it too noisy can set ``AIPAGER_DIFF_VIEW=0`` to disable.

_DIFF_MAX_LINES = 30
_DIFF_MAX_CHARS = 2000


def _diff_view_enabled() -> bool:
    return os.environ.get("AIPAGER_DIFF_VIEW", "1") not in ("0", "false", "no", "")


def _truncate_diff(lines: list[str]) -> tuple[str, int]:
    """Truncate a list of diff lines to the per-message limits.

    Returns (body_text, dropped_line_count). The body never exceeds
    `_DIFF_MAX_CHARS` and includes a `…[N more lines]` marker when
    truncation happens.
    """
    total = len(lines)
    if total <= _DIFF_MAX_LINES:
        body = "\n".join(lines)
        if len(body) <= _DIFF_MAX_CHARS:
            return body, 0
    keep = lines[:_DIFF_MAX_LINES]
    body = "\n".join(keep)
    if len(body) > _DIFF_MAX_CHARS:
        body = body[:_DIFF_MAX_CHARS]
    dropped = max(0, total - len(keep))
    return body, dropped


def _build_diff_block(
    tool_name: str, tool_input: dict,
) -> tuple[str, str] | None:
    """Return (header, diff_body) for Write or Edit; None if input is malformed.

    For Write: treat as a brand-new file (empty original → all new lines).
    For Edit: unified diff between ``old_string`` and ``new_string``.
    """
    file_path = (tool_input.get("file_path") or "").strip()
    if not file_path:
        return None
    if tool_name == "Write":
        new = tool_input.get("content") or ""
        if not new:
            return None
        diff_lines = list(difflib.unified_diff(
            [], new.splitlines(),
            fromfile="/dev/null", tofile=file_path, lineterm="",
        ))
        header = f"📝 <b>Write</b> · <code>{html_mod.escape(file_path)}</code>"
        return header, "\n".join(diff_lines)
    if tool_name == "Edit":
        old = tool_input.get("old_string") or ""
        new = tool_input.get("new_string") or ""
        if not old and not new:
            return None
        diff_lines = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=file_path, tofile=file_path, lineterm="",
        ))
        header = f"📝 <b>Edit</b> · <code>{html_mod.escape(file_path)}</code>"
        return header, "\n".join(diff_lines)
    return None


class TruncationFailed(Exception):
    """Raised by ``_send_with_retry`` when text remains "too long" after the
    truncation cap. Caller in the IDLE path can catch this and fall back
    to sending the response as a document attachment.
    """


# Maximum number of times ``_send_with_retry`` will truncate-and-resend
# before giving up. HTML escaping can occasionally expand text on
# truncation, so without a cap a pathological input could loop forever.
_MAX_TRUNCATIONS = 2


async def _send_with_retry(bot, *, chat_id, text: str, parse_mode: str | None = None,
                           reply_to_message_id: int | None = None,
                           reply_markup=None, max_retries: int = 2):
    """Send a Telegram message with backoff for RetryAfter and graceful
    handling of "message is too long"."""
    last_err: Exception | None = None
    truncations = 0
    for _attempt in range(max_retries + 1):
        try:
            return await bot.send_message(
                chat_id, text, parse_mode=parse_mode,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )
        except RetryAfter as e:
            wait = getattr(e, "retry_after", None) or 1
            log.warning("Telegram flood control — retrying in %ss", wait)
            await asyncio.sleep(wait)
            last_err = e
            continue
        except BadRequest as e:
            if "too long" in str(e).lower():
                truncations += 1
                if truncations > _MAX_TRUNCATIONS:
                    log.warning(
                        "Telegram still rejects message as too long after %d "
                        "truncation attempts; caller should fall back to a "
                        "document send", _MAX_TRUNCATIONS,
                    )
                    raise TruncationFailed() from e
                # On each retry truncate more aggressively in case Telegram's
                # "too long" was about something other than raw char count
                # (e.g. HTML entity expansion). Halve the budget each time
                # but never below a sensible floor.
                new_limit = max(TELEGRAM_MAX_TEXT_LEN // (2 ** (truncations - 1)), 500)
                text = text[: new_limit - len(_TRUNC_SUFFIX)] + _TRUNC_SUFFIX
                last_err = e
                continue
            raise
        except Forbidden as e:
            _log_blocked_once(e)
            raise
    if last_err:
        raise last_err

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

# Each entry: (matcher regex, friendly message, kind).
# ``kind`` is used by ``_detect_api_error`` to know whether to also try
# extracting a retry-after hint — currently only rate-limit errors carry
# one in practice.
_ERROR_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(
        r"API Error:\s*402|payment.?required|credit.?balance.?too.?low"
        r"|insufficient.?credit|monthly.?limit|usage.?limit.?reached"
        r"|subscription.?(expired|inactive|required)",
        re.I,
     ),
     "Anthropic subscription / credit balance issue. "
     "Check your dashboard at https://console.anthropic.com",
     "subscription"),
    (re.compile(r"API Error:\s*500|api_error|internal server error", re.I),
     "Anthropic's servers hit an internal error. Usually resolves in seconds.",
     "server"),
    (re.compile(r"API Error:\s*529|overloaded_error|overloaded", re.I),
     "Anthropic's servers are overloaded. Try again in a moment.",
     "overload"),
    (re.compile(r"API Error:\s*429|rate_limit_error|rate.?limit", re.I),
     "Rate limit hit. Wait a moment before retrying.",
     "rate_limit"),
    (re.compile(r"connection.?(error|reset|refused|timeout)|ECONNR|network.?error", re.I),
     "Lost connection to Anthropic. Check network and retry.",
     "network"),
    (re.compile(r"API Error:\s*\d{3}", re.I),
     "API error occurred.",
     "api"),
]

# Capture an explicit retry-after hint from the error body in any of the
# common shapes Anthropic / proxies use. Returns the integer seconds in
# group(1) regardless of which alternation matched.
_RETRY_AFTER_RE = re.compile(
    r"retry[\s-]*after[^\d]{0,20}(\d{1,5})"
    r"|wait[^\d]{0,20}(\d{1,5})\s*sec"
    r"|(\d{1,5})\s*second\s+(?:cool|wait)",
    re.I,
)


def _extract_retry_after(text: str) -> int | None:
    m = _RETRY_AFTER_RE.search(text or "")
    if not m:
        return None
    for grp in m.groups():
        if grp is not None:
            try:
                return int(grp)
            except ValueError:
                continue
    return None


def _detect_api_error(text: str) -> tuple[str, int | None] | None:
    """Check if text contains a known API error pattern.

    Returns ``(friendly_message, retry_after_seconds_or_None)`` if a
    pattern matches; for rate-limit errors we additionally try to pull
    a "retry-after" hint out of the error body and substitute it into
    the message ("Wait 60s before retrying" instead of "Wait a
    moment"). Otherwise returns ``None``.
    """
    if not text:
        return None
    for pattern, friendly_msg, kind in _ERROR_PATTERNS:
        if pattern.search(text):
            retry_after = (
                _extract_retry_after(text) if kind == "rate_limit" else None
            )
            if retry_after is not None:
                friendly_msg = (
                    f"Rate limit hit. Wait {retry_after}s before retrying."
                )
            return friendly_msg, retry_after
    return None


class TelegramBot:
    """Wraps python-telegram-bot Application with session-aware handlers."""

    def __init__(self, registry: SessionRegistry):
        self.registry = registry
        self._app: Application | None = None
        self.observers = None  # ObserverBroadcaster | None, injected by __main__
        self._registered_labels: set[str] | None = None  # None = never synced this run
        self._keyboard_level: str = "main"  # "main", "templates", "commands", "models"
        self._template_map: dict[str, str] = {label: prompt for label, prompt in QUICK_TEMPLATES}
        self._command_map: dict[str, str] = {label: cmd for label, cmd in QUICK_COMMANDS}
        self._model_map: dict[str, str] = {label: cmd for label, cmd in MODEL_CHOICES}
        self._last_pinned_text: str = ""  # dedup pinned message edits
        # `/new <name>` collision state. Keyed by session_name; value is
        # {"prompt": str, "skip_perms": bool, "user_id": int, "msg_id": int}.
        # Populated when /new hits an existing name, drained when the user
        # taps Resume / Replace / Cancel. Multiple users colliding on the
        # same name race-overwrite — acceptable for a v1 single-admin tool.
        self._new_conflict_pending: dict[str, dict] = {}
        # Team / allow-list — None for personal-mode installs (no team.yaml),
        # which preserves the existing one-user-one-DM behaviour.
        from aipager.config import TEAM
        self.team: Team | None = TEAM

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

    async def reload_team(self) -> None:
        """Re-read ``team.yaml`` and swap ``self.team`` live.

        Triggered by the daemon's SIGUSR1 handler when the wizard
        finishes a team-config edit. On parse error, log a WARN and
        keep the previous team in memory — the admin can't lock
        themselves out by hand-editing a typo. Returning to personal
        mode (``team.yaml`` absent / archived) is a valid result:
        ``self.team`` becomes ``None`` and all handlers fall back to
        the personal-mode path.
        """
        from aipager.team import (
            TEAM_CONFIG_PATH, TeamConfigError, load_team,
        )
        try:
            new_team = load_team(TEAM_CONFIG_PATH)
        except TeamConfigError as e:
            log.warning(
                "Team config reload failed — keeping previous in-memory "
                "team. Fix and re-signal: %s", e,
            )
            return

        old = self.team
        self.team = new_team

        if old is None and new_team is None:
            log.info("Team reload: no change (still personal mode)")
        elif old is None and new_team is not None:
            log.info(
                "Team reload: personal → team (%d users, %d admin, "
                "deny=%s)",
                len(new_team.users), new_team.admin_count(),
                list(new_team.rules.deny_tools),
            )
        elif new_team is None:
            log.info("Team reload: team → personal (allow-list disabled)")
        else:
            log.info(
                "Team reload: %d → %d users · %d → %d admin · "
                "deny %s → %s",
                len(old.users), len(new_team.users),
                old.admin_count(), new_team.admin_count(),
                list(old.rules.deny_tools),
                list(new_team.rules.deny_tools),
            )

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

    async def start(self) -> None:
        global _bot_instance
        _bot_instance = self

        builder = ApplicationBuilder().token(BOT_TOKEN)

        # Long-poll config: timeout=30 means Telegram holds the connection
        # for up to 30s waiting for updates → instant response to taps.
        # Connect timeouts are 30s rather than 10s so the daemon stays
        # robust on networks where the initial TLS handshake is slow.
        builder = (
            builder
            .get_updates_read_timeout(30)
            .get_updates_write_timeout(15)
            .get_updates_connect_timeout(30)
            .read_timeout(20)
            .write_timeout(15)
            .connect_timeout(30)
        )

        self._app = builder.build()

        # Register handlers
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(CommandHandler("start", self._handle_start_cmd))
        self._app.add_handler(CommandHandler("help", self._handle_start_cmd))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CommandHandler("stop", self._handle_stop_cmd))
        self._app.add_handler(CommandHandler("kill", self._handle_kill_cmd))
        self._app.add_handler(CommandHandler("new", self._handle_new_cmd))
        self._app.add_handler(CommandHandler("resume", self._handle_resume_cmd))
        self._app.add_handler(CommandHandler("clearqueue", self._handle_clearqueue_cmd))
        # Media handler: photos and documents → save file, inject prompt
        self._app.add_handler(MessageHandler(
            (filters.PHOTO | filters.Document.ALL) & filters.Chat(int(CHAT_ID)),
            self._handle_file,
        ))
        # Voice messages → faster-whisper transcribe → inject as prompt.
        # Item 5.3. Only fires when the `voice` extra is installed; the
        # handler itself surfaces a friendly error otherwise.
        self._app.add_handler(MessageHandler(
            filters.VOICE & filters.Chat(int(CHAT_ID)),
            self._handle_voice,
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
        # Cancel every running per-session animation task FIRST and wait
        # for them to settle. Otherwise asyncio.run() at the outer layer
        # force-kills them mid-edit, which can leave orphan tasks logging
        # spurious "task was destroyed but is pending" warnings.
        to_cancel = [s.animate_task for s in self.registry.all_sessions().values()
                     if s.animate_task and not s.animate_task.done()]
        for t in to_cancel:
            t.cancel()
        for t in to_cancel:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def _recover_busy_message(
        self, bot, name: str, sess: TrackedSession, live_names: set[str]
    ) -> str:
        """Edit one session's orphaned busy message to a final state.

        Returns one of: ``"edited"`` / ``"vanished"`` (user deleted the
        message) / ``"too_old"`` (>48 h, Telegram refuses edits) /
        ``"blocked"`` (bot blocked by user) / ``"flooded"`` (RetryAfter)
        / ``"error:<short>"`` (unexpected). Always clears
        ``sess.busy_msg_id`` synchronously before any await so a hook
        firing mid-recovery can't race on it.
        """
        orphaned_id = sess.busy_msg_id
        sess.busy_msg_id = None  # clear before any await
        is_alive = name in live_names
        label = html_mod.escape(sess.label)
        text = (f"🔄 <b>{label}</b> · Daemon restarted" if is_alive
                else f"🔴 <b>{label}</b> · Session ended")
        try:
            await bot.edit_message_text(
                text, chat_id=CHAT_ID, message_id=orphaned_id,
                parse_mode="HTML",
            )
        except BadRequest as e:
            msg = str(e).lower()
            if "message to edit not found" in msg or "message not found" in msg:
                log.info("[%s] orphan msg %d already gone (user deleted)",
                         sess.label, orphaned_id)
                return "vanished"
            if "can't be edited" in msg or "message can't be edited" in msg:
                log.warning(
                    "[%s] orphan msg %d is too old to edit (>48h); user will "
                    "still see the stuck 'Working…' until they delete it",
                    sess.label, orphaned_id,
                )
                return "too_old"
            log.warning("[%s] orphan msg %d edit failed: %s",
                        sess.label, orphaned_id, e)
            return f"error:{str(e)[:80]}"
        except Forbidden as e:
            _log_blocked_once(e)
            return "blocked"
        except RetryAfter as e:
            wait = getattr(e, "retry_after", None) or "?"
            log.info("[%s] orphan msg %d skipped — Telegram flood control "
                     "(retry_after=%s)", sess.label, orphaned_id, wait)
            return "flooded"
        except Exception as e:  # noqa: BLE001 - true last-resort log
            log.warning("[%s] orphan msg %d recovery failed: %r",
                        sess.label, orphaned_id, e, exc_info=True)
            return f"error:{type(e).__name__}"
        return "edited"

    async def recover_sessions(self) -> None:
        """Clean up orphaned busy messages from a previous daemon lifecycle.

        For every session with a persisted ``busy_msg_id`` left over from
        the previous daemon run, try to edit the message to a terminal
        state ("Daemon restarted" if the dtach session is still alive,
        "Session ended" otherwise). Returns a summary log line so the
        outcome of each daemon startup is visible in `aipager logs`.
        """
        if not self._app:
            return
        bot = self._app.bot
        live_names = set(await inject.list_sessions())

        targets = [(name, sess) for name, sess in self.registry.all_sessions().items()
                   if sess.busy_msg_id and sess.busy_msg_id > 0]
        if not targets:
            return

        outcomes: dict[str, int] = {}
        stop_early = False
        for name, sess in targets:
            if stop_early:
                # Bot is blocked — clear remaining busy_msg_ids without trying
                # to edit (which would just generate more Forbidden noise).
                sess.busy_msg_id = None
                outcomes["skipped_blocked"] = outcomes.get("skipped_blocked", 0) + 1
                continue
            outcome = await self._recover_busy_message(bot, name, sess, live_names)
            key = outcome.split(":", 1)[0]  # "error:foo" → "error"
            outcomes[key] = outcomes.get(key, 0) + 1
            if outcome == "blocked":
                stop_early = True

        # One summary line, easy to grep in `aipager logs`.
        summary = ", ".join(f"{n} {k}" for k, n in sorted(outcomes.items()))
        suffix = ""
        if stop_early:
            suffix = "  (bot blocked — stopping retries)"
        log.info("recovered %d sessions: %s%s", len(targets), summary, suffix)
        self.registry.mark_dirty()

    async def _update_bot_commands(self) -> None:
        """Register bot commands (/ menu) and update persistent keyboard.

        Always runs on the first call after daemon startup, even when
        there are no sessions, so Telegram's server-side command cache
        from a previous run (stale ``/jim`` / ``/john`` entries) is
        cleared and the persistent keyboard appears immediately. On
        subsequent calls it short-circuits when nothing changed.
        """
        if not self._app:
            return

        # Collect live session labels
        labels: set[str] = set()
        for name, sess in self.registry.all_sessions().items():
            if sess.status != Status.GONE and sess.label:
                labels.add(sess.label)

        first_run = self._registered_labels is None
        if not first_run and labels == self._registered_labels:
            return  # no change

        # Build command list: static + dynamic session labels
        commands = [
            BotCommand("status", "Show all sessions"),
            BotCommand("stop", "Stop active session"),
            BotCommand("kill", "Kill a session (destroy)"),
            BotCommand("new", "Launch new session"),
            BotCommand("clearqueue", "Drop pending queued prompts"),
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
            # Still mark as synced so we don't retry every poll cycle.
            if first_run:
                self._registered_labels = labels

        # Send/update persistent keyboard (always main — first run or
        # session list changed).
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
                    CHAT_ID, e, bot_user, bot_user,
                )
            else:
                log.warning("Failed to send keyboard: %s", e)
        except Exception as e:
            if _is_bot_blocked(e):
                _log_blocked_once(e)
            else:
                log.warning("Failed to send keyboard", exc_info=True)

    async def _react(self, update: Update, emoji: str) -> None:
        """React to the user's message with an emoji."""
        try:
            await self._app.bot.set_message_reaction(
                update.effective_chat.id, update.message.message_id, emoji,
            )
        except Exception:
            pass  # reaction API may not be available in all contexts

    async def _install_voice_extra(self, query) -> None:
        """Run the install subprocess and edit the prompt message with
        progress, ending in success or failure.

        Triggered by the `__voice__:install` inline-keyboard tap when
        the user sends a voice message without the voice extra. Lets
        users on the road install the extra without SSH access.
        """
        from aipager import updater

        installer = updater._detect_installer()
        cmd = updater.install_extra_cmd(installer, "voice")
        if cmd is None:
            # Should be unreachable for "voice" today — install_extra_cmd
            # only returns None for an extra it doesn't know about. Keep
            # a defensive fallback so a future extra can't strand the user.
            await self._safe_edit_callback(
                query,
                "⚠️ This aipager build has no installer recipe for the\n"
                "voice extra. Update aipager and try again.",
            )
            return

        await self._safe_edit_callback(
            query, "📦 Installing voice extra…",
        )

        # Launch the install. Capture stdout/stderr so a failure message
        # can show the user what went wrong.
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, asyncio.SubprocessError) as e:
            await self._safe_edit_callback(
                query, f"❌ Couldn't start installer: {html_mod.escape(str(e))}",
            )
            return

        # Edit the message every few seconds so the user has a
        # heartbeat that the install is alive.
        started = time.monotonic()
        last_edit_at = started
        while True:
            try:
                # Wait briefly for the proc to finish; if it doesn't,
                # edit the message and loop.
                await asyncio.wait_for(proc.wait(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                elapsed = int(time.monotonic() - started)
                if time.monotonic() - last_edit_at >= 5.0:
                    await self._safe_edit_callback(
                        query,
                        f"📦 Installing voice extra — still working… ({elapsed}s)",
                    )
                    last_edit_at = time.monotonic()

        stdout = (await proc.stdout.read()).decode("utf-8", errors="replace")
        if proc.returncode == 0:
            # Only brew genuinely wipes the extra on upgrade (its
            # formula venv is rebuilt from scratch). Editable /
            # pip-user / venv installs keep faster-whisper indefinitely
            # — the warning would just be noise there.
            footer = ""
            if installer == "brew":
                footer = (
                    "\n\n<i>Note: a future `brew upgrade aipager` may "
                    "rebuild the formula venv and drop faster-whisper. "
                    "If voice stops working, tap Install again.</i>"
                )

            # Always offer the restart button — service units use
            # systemctl / launchctl; other installs spawn a detached
            # replacement and SIGTERM ourselves. Either way the user
            # doesn't need terminal access.
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔄 Restart daemon now",
                    callback_data="__voice__:restart",
                ),
            ]])
            await self._safe_edit_callback(
                query,
                "✅ Installed. Voice features need a daemon restart "
                "to load the new module." + footer,
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            tail = stdout.strip()[-500:]
            await self._safe_edit_callback(
                query,
                f"❌ Install failed (exit {proc.returncode}):\n"
                f"<pre>{html_mod.escape(tail)}</pre>",
                parse_mode="HTML",
            )

    async def _restart_daemon(self, query) -> None:
        """Trigger a clean restart.

        Service-managed daemons (systemd-user / launchd) go through
        their respective managers. Foreground / editable daemons spawn
        a detached replacement that waits for us to die, then we
        SIGTERM ourselves — so users on their phone don't need
        terminal access to pick up a code change.
        """
        import platform
        from aipager.service import (
            LINUX_UNIT_PATH, MACOS_LABEL, MACOS_PLIST_PATH, _run,
        )

        sysname = platform.system().lower()
        if sysname == "linux" and LINUX_UNIT_PATH.exists():
            await self._safe_edit_callback(
                query, "🔄 Restarting via systemctl --user…",
            )
            # systemctl will kill us; the unit's Restart=on-failure /
            # the service definition handles relaunch.
            _run(["systemctl", "--user", "restart", "aipager.service"])
            return
        if sysname == "darwin" and MACOS_PLIST_PATH.exists():
            await self._safe_edit_callback(
                query, "🔄 Restarting via launchctl kickstart…",
            )
            _run(["launchctl", "kickstart", "-k",
                  f"gui/{os.getuid()}/{MACOS_LABEL}"])
            return

        # No service unit — spawn a detached replacement and self-kill.
        # The shell wrapper polls our PID via `kill -0`; once we die
        # (HookReceiver.stop unlinks /tmp/aipager.sock as part of
        # cli.py's SIGTERM handler) the wrapper execs aipager start.
        parent_pid = os.getpid()
        log_path = "/tmp/aipager.log"
        try:
            log_fd = os.open(
                log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644,
            )
        except OSError as e:
            await self._safe_edit_callback(
                query,
                f"⚠️ Couldn't open {log_path}: {html_mod.escape(str(e))}\n"
                "Please restart manually.",
            )
            return

        shell_cmd = (
            f"while kill -0 {parent_pid} 2>/dev/null; do sleep 0.2; done; "
            f"exec {shlex.quote(sys.executable)} -m aipager.cli start"
        )
        try:
            proc = subprocess.Popen(
                ["sh", "-c", shell_cmd],
                stdout=log_fd, stderr=log_fd, stdin=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
        except OSError as e:
            os.close(log_fd)
            await self._safe_edit_callback(
                query,
                f"⚠️ Couldn't spawn replacement: {html_mod.escape(str(e))}\n"
                "Please restart manually.",
            )
            return
        os.close(log_fd)

        # If the shell wrapper died inside 300 ms, the spawn failed —
        # don't kill ourselves and surface the failure.
        await asyncio.sleep(0.3)
        if proc.poll() is not None:
            await self._safe_edit_callback(
                query,
                "⚠️ Replacement daemon failed to start. "
                "Please restart manually.",
            )
            return

        # Tell the user before we die. The HTTP edit needs a beat to
        # flush before SIGTERM tears the event loop down.
        await self._safe_edit_callback(
            query,
            "🔄 Restarting — send your voice message again in a few seconds.",
        )
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    async def _safe_edit_callback(
        self, query, text: str, *,
        parse_mode: str | None = None,
        reply_markup=None,
    ) -> None:
        """Edit the message tied to a callback query, swallowing
        edit-failed errors (message gone, identical content, etc.)."""
        try:
            await query.edit_message_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup,
            )
        except Exception:
            log.debug("callback edit failed (probably no-op)", exc_info=True)

    async def _send_diff_preview(
        self, sess: TrackedSession, tool_name: str, tool_input: dict,
    ) -> None:
        """Render a Write/Edit diff and post it as a chat reply.

        Best-effort: any failure (Telegram error, malformed input, etc.)
        is swallowed. The user always still has the busy-message
        tool_history entry as a fallback summary.
        """
        try:
            built = _build_diff_block(tool_name, tool_input)
            if not built:
                return
            header, diff = built
            if not diff.strip():
                # No textual change (e.g., Edit where old == new). Skip the
                # send to avoid an empty preview message.
                return
            body, dropped = _truncate_diff(diff.splitlines())
            footer = (f"\n<i>…and {dropped} more line{'s' if dropped != 1 else ''}</i>"
                      if dropped else "")
            # ``<code class="language-diff">`` triggers Telegram's
            # syntax highlighting: `+` lines render green, `-` lines red,
            # `@@` hunks in cyan. Supported on desktop and recent mobile
            # clients; older clients fall back to plain monospace.
            text = (
                f"{header}\n"
                f"<pre><code class=\"language-diff\">"
                f"{html_mod.escape(body)}"
                f"</code></pre>"
                f"{footer}"
            )
            await self._app.bot.send_message(
                CHAT_ID, text, parse_mode="HTML",
                reply_to_message_id=(sess.busy_msg_id
                                     if sess.busy_msg_id and sess.busy_msg_id > 0
                                     else None),
            )
        except Exception:
            log.debug("[%s] diff preview send failed", sess.label, exc_info=True)

    def _build_pinned_text(self, active_name: str) -> str:
        """Compose the pinned-message text (item 4.3).

        Top line is the currently-active session with full context;
        remaining lines are other live sessions (status, model, cost)
        so the user has an at-a-glance dashboard right at the top of
        the chat. Limits to the active session if there are no other
        live ones.
        """
        active = self.registry.get(active_name)
        if not active:
            return ""

        def _state_word(sess: TrackedSession) -> str:
            if sess.status == Status.BUSY:
                return "busy"
            if sess.status == Status.INTERACTIVE:
                return "waiting"
            if sess.status == Status.IDLE:
                return "idle"
            return ""

        def _session_line(sess: TrackedSession, prefix: str) -> str:
            parts = [f"<b>{html_mod.escape(sess.label)}</b>"]
            if sess.model_name:
                parts.append(html_mod.escape(sess.model_name))
            state = _state_word(sess)
            if state and sess.name != active_name:
                parts.append(state)
            if sess.last_token_pct:
                parts.append(f"{int(sess.last_token_pct)}% ctx")
            if sess.last_cost_usd > 0:
                parts.append(f"${sess.last_cost_usd:.2f}")
            return f"{prefix} {' · '.join(parts)}"

        lines = [_session_line(active, "📌")]
        # Other live sessions, alphabetical
        others = sorted(
            (s for s in self.registry.all_sessions().values()
             if s.name != active_name and s.status != Status.GONE and s.label),
            key=lambda s: s.label,
        )
        for s in others:
            lines.append(_session_line(s, "  •"))
        return "\n".join(lines)

    async def _maybe_update_bot_name(self, session_name: str) -> None:
        """Update the pinned status message (compatibility alias).

        Name kept for back-compat with the many call sites scattered
        through the file. Behaviour now: builds a multi-line summary
        of all live sessions with the named one as the header.

        Skipped entirely in team mode — group chats don't want a
        status dashboard cluttering scroll-back (and the bot usually
        can't pin in groups anyway without admin perms).
        """
        if not self._app:
            return
        if self.team is not None:
            return  # team mode: no pinned dashboard
        text = self._build_pinned_text(session_name)
        if not text or text == self._last_pinned_text:
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
                # Store the message id IMMEDIATELY — before attempting
                # to pin. In groups where the bot isn't an admin (no
                # pin permission), the pin call fails but we still
                # want to edit this message in place on every refresh
                # rather than send a fresh status line each time
                # (which spammed the chat with repeated "📌 …" notices).
                self.registry.pinned_msg_id = msg.message_id
                self.registry.mark_dirty()
                try:
                    await self._app.bot.pin_chat_message(
                        chat, msg.message_id,
                        disable_notification=True,
                    )
                except Exception as e:
                    log.info(
                        "Couldn't pin the status dashboard (bot likely "
                        "isn't a group admin): %s — will edit in place "
                        "instead.", e,
                    )
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
        # Live cost delta this turn (item 4.6) + subagent count (item 4.5).
        # Only shown when there's a positive delta — sessions that haven't
        # cost anything yet don't get a misleading "$0.00".
        if sess.cost_baseline is not None and sess.last_cost_usd > 0:
            cost_delta = sess.last_cost_usd - sess.cost_baseline
            if cost_delta > 0.001:
                n_agents = sess.subagent_count_this_turn
                plural = "" if n_agents == 1 else "s"
                agent_note = (f" ({n_agents} agent{plural})" if n_agents > 0 else "")
                text += f" · 💰 ${cost_delta:.2f}{agent_note}"
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

        Serializes concurrent callers via ``sess.animate_lock`` so two
        coroutines (e.g. ``_handle_message`` and a ``UserPromptSubmit``
        hook arriving micro-seconds apart) cannot both observe
        ``busy_msg_id is None`` and both send. The synchronous-sentinel
        pattern below ``-1 claim then None on failure`` is kept as a
        secondary defence inside the lock.
        """
        async with sess.animate_lock:
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
            # Cost + subagent count baselines (items 4.5, 4.6) — reset so
            # busy-message numbers reflect THIS turn, not lifetime.
            sess.cost_baseline = None
            sess.subagent_count_this_turn = 0
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
                # Track the busy message so replies to it route back to this session,
                # even hours later or after a daemon restart.
                self.registry.track_message(msg_id, sess.name)
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
            tool_input_full = context.get("tool_input_full")
            # Update tool history — mark previous as done, append new
            if tool_summary:
                # Append new tool as in-progress (PostToolUse marks it done)
                sess.record_tool(tool_summary, False)
                sess.last_tool_summary = tool_summary
            # Item 4.4: send a separate diff-preview message for Write/Edit.
            # Best-effort and opt-out via AIPAGER_DIFF_VIEW=0. Fire-and-forget
            # so it doesn't slow the busy-message edit cadence.
            if (tool_name in ("Write", "Edit") and tool_input_full
                    and _diff_view_enabled()):
                asyncio.create_task(
                    self._send_diff_preview(sess, tool_name, tool_input_full)
                )
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
            # Count this subagent for the "(N agents)" rollup (item 4.5).
            sess.subagent_count_this_turn += 1
            # Append to tool_history SYNCHRONOUSLY before any await.
            # record_tool returns the (post-trim) index so the subagent
            # bookkeeping below references the correct entry even after
            # the history is trimmed.
            summary = f"\U0001f916 {agent_type}"
            history_idx = sess.record_tool(summary, False)
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
                sess.record_tool(done_summary, True)
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
            # No hook has fired for STALE_BUSY_TIMEOUT seconds — claude
            # is either silently retrying an API call (exhausted
            # subscription, network), in a long-running extended-think
            # /tool call (legitimate), or wedged. Surface the most
            # likely causes so the user can decide whether to wait or
            # tap Stop.
            minutes = context.get("minutes", 2)
            stale_text = (
                f"⚠️ <b>{html_mod.escape(label)}</b> · Stuck on "
                f"<i>Working</i> for {minutes}+ min with no claude activity.\n"
                "\n"
                "<b>Most likely causes</b> (in order):\n"
                "  • Anthropic subscription / credit balance ran out\n"
                "    — check your dashboard at https://console.anthropic.com\n"
                "  • Rate limit hit (transient — claude is retrying)\n"
                "  • Long-running tool call (WebSearch, large fetch)\n"
                "  • Claude crashed or network is wedged\n"
                "\n"
                "<i>Tap Stop to interrupt, or wait it out.</i>"
            )
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
            error_detection = _detect_api_error(error_source)
            if error_detection:
                friendly_error, _retry_after = error_detection
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
            # When the response was big enough to spill into a file
            # attachment, tell the reader explicitly so they don't miss
            # the .txt that lands below the inline preview.
            if send_file:
                text += "\n\n📎 <i>Full response attached below ↓</i>"
            log.debug("[%s] Sending IDLE notification (%d chars)", label, len(text))
            try:
                msg = await _send_with_retry(
                    bot, chat_id=CHAT_ID, text=text, parse_mode="HTML",
                    reply_to_message_id=sess.trigger_msg_id,
                )
            except TruncationFailed:
                # Truncation attempts exhausted — fall back to sending the
                # response as a plain-text document attachment.
                log.warning(
                    "[%s] IDLE summary too long after %d truncations — "
                    "falling back to document send",
                    label, _MAX_TRUNCATIONS,
                )
                fallback_text = f"📨 <b>{html_mod.escape(label)}</b> · Finished (response sent as attachment)"
                msg = await bot.send_message(
                    CHAT_ID, fallback_text, parse_mode="HTML",
                    reply_to_message_id=sess.trigger_msg_id,
                )
                send_file = True  # ensure the document send below fires
            sess.trigger_msg_id = None  # reply cycle complete
            self.registry.mark_dirty()
            self.registry.track_message(msg.message_id, sess.name)
            await self._maybe_update_bot_name(sess.name)
            # Send full response as file for long messages
            file_content = raw_md or (summary if send_file else "")
            if send_file and file_content:
                content_bytes = file_content.encode("utf-8")
                if len(content_bytes) > TELEGRAM_MAX_DOC_BYTES:
                    mb = len(content_bytes) / (1024 * 1024)
                    log.warning(
                        "[%s] Response too large for Telegram (%.1f MB) — sent summary only",
                        label, mb,
                    )
                    file_content = ""  # also skip the observer-broadcast path below
                else:
                    try:
                        tmp = Path(tempfile.mktemp(suffix=".txt", prefix=f"{label}_"))
                        tmp.write_text(file_content, encoding="utf-8")
                        with open(tmp, "rb") as f:
                            await bot.send_document(
                                CHAT_ID, document=f, filename=f"{label}_response.txt",
                                reply_to_message_id=msg.message_id,
                            )
                        tmp.unlink(missing_ok=True)
                    except Forbidden as e:
                        _log_blocked_once(e)
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
                queued_text, queued_trigger, _queued_at = sess.pending_queue.pop(0)
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

            # Team-mode rule check: auto-deny tools listed in
            # ``team.yaml`` ``rules.deny_tools`` (unless the session's
            # last driver is an admin, who bypass rules). Side-steps the
            # permission prompt entirely — claude sees a Deny via the
            # same key-injection path the buttons use, the chat sees
            # a one-line "⛔ Auto-denied" notice, and an audit record
            # is written.
            if (tool_info and self.team is not None
                    and self.team.rules.deny_tools):
                triggerer = self._driver_user(sess)
                if self.team.rules.tool_is_denied(
                    tool_info.get("name", ""), triggerer,
                ):
                    await self._auto_deny(sess, tool_info, triggerer)
                    return

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

    @staticmethod
    async def _safe_answer(query, text: str | None = None) -> None:
        """Call ``query.answer(text)`` swallowing any "query is too old"
        or "already answered" errors.

        Used everywhere we want to set a toast text after the eager
        ack at the top of ``_handle_callback`` — Telegram refuses a
        second answer for the same query, so without this wrapper the
        whole handler would crash on the second ``answer`` call.
        """
        try:
            await query.answer(text)
        except Exception:
            pass

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button tap.

        Acknowledges the callback query immediately (with no toast text)
        so Telegram clears the button-spinner within a few hundred
        milliseconds even when the handler does long work. Subsequent
        toast calls go through :py:meth:`_safe_answer` which swallows
        the resulting error — toasts are a nice-to-have here; the
        actual outcome (message edits, status updates) is what the
        user really sees.
        """
        query = update.callback_query
        # Team mode: reject taps from users not on the allow-list with a
        # toast. Personal mode passes through unchanged (sentinel value).
        member = await self._authorize_callback(query)
        if member is None:
            return
        cb_data = query.data or ""
        original_text = query.message.text or "" if query.message else ""

        # Eager ack — clear the spinner before any slow work.
        await self._safe_answer(query)

        if ":" not in cb_data:
            await self._safe_answer(query, "Invalid callback")
            return

        session_name, action = cb_data.split(":", 1)

        if action == "stop":
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, "Session not found")
                return
            await self._stop_session(sess, query=query)
            return

        if action == "kill":
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name
            await self._safe_answer(query, f"Killing {label}...")
            await self._kill_session_by_label(query, label)
            return

        if action == "kill-confirm":
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name.removeprefix("claude-")
            await self._safe_answer(query, f"Killing {label}...")
            await self._kill_session_by_label(query, label)
            return

        if action == "kill-cancel":
            try:
                await query.edit_message_text(
                    "↩️ Cancelled (no session killed).",
                )
            except Exception:
                pass
            return

        # ---- Voice-extra remote-install flow (item 5.3 follow-up) ----
        if session_name == "__voice__":
            if action == "cancel":
                try:
                    await query.edit_message_text(
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
            if not await inject.is_alive(session_name):
                await self._safe_answer(query, f"Session '{session_name}' not alive")
                return
            # Re-inject the last prompt (last_prompt stays set for retry-of-retry)
            prompt = sess.last_prompt
            ok = await inject.send_text_and_enter(session_name, prompt)
            if ok:
                await self._safe_answer(query, f"Retrying [{sess.label}]")
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
            ok = await inject.send_text_and_enter(session_name, "/compact")
            if ok:
                await self._safe_answer(query, f"Compacting [{sess.label}]")
                try:
                    await self._app.bot.delete_message(
                        chat_id=CHAT_ID,
                        message_id=query.message.message_id,
                    )
                except Exception:
                    pass
                log.info("[%s] Compact triggered by user", sess.label)
            else:
                await self._safe_answer(query, "Failed to send /compact")
            return

        if action == "clear_gone":
            # Remove all dead sessions from registry
            removed = []
            for name, sess in list(self.registry.all_sessions().items()):
                if not await inject.is_alive(name):
                    removed.append(sess.label)
                    self.registry.remove(name)
            if removed:
                await self._safe_answer(query, f"Cleared {len(removed)} session(s)")
                try:
                    await query.edit_message_text(
                        f"Cleared: {', '.join(removed)}", parse_mode="HTML",
                    )
                except Exception:
                    pass
                log.info("Cleared gone sessions: %s", removed)
            else:
                await self._safe_answer(query, "No gone sessions to clear")
            return

        # ---- /resume picker callbacks ---------------------------------
        if action == "resume" and session_name and session_name != "_":
            label = session_name.removeprefix("claude-")
            # Edit the picker message into a "Resuming…" stub so the user
            # sees feedback even before launch_session returns.
            try:
                await query.edit_message_text(
                    f"♻️ Resuming <b>{html_mod.escape(label)}</b>…",
                    parse_mode="HTML",
                )
            except Exception:
                pass

            async def _reply(text, **kw):
                # The picker message is editable but only once — subsequent
                # edits on the same message work fine. Fall back to a fresh
                # message if Telegram rejects the edit (e.g. picker was
                # deleted).
                try:
                    await query.edit_message_text(text, **kw)
                except Exception:
                    await self._app.bot.send_message(
                        chat_id=CHAT_ID, text=text, **kw,
                    )

            # Reuse the same code path as /resume <name>. No update object
            # here, so driver attribution falls back to leaving the
            # last_driver field unchanged (still set from prior session).
            await self._do_resume(label=label, reply_fn=_reply)
            return

        if action.startswith("resume_page:"):
            try:
                page = int(action.split(":", 1)[1])
            except (IndexError, ValueError):
                page = 0
            text, kb = self._render_resume_picker(page=page)
            try:
                await query.edit_message_text(
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
            pending = self._new_conflict_pending.pop(session_name, None)
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name.removeprefix("claude-")

            if action == "new_cancel":
                try:
                    await query.edit_message_text(
                        "↩️ Cancelled — no session changed.",
                    )
                except Exception:
                    pass
                return

            prompt = (pending or {}).get("prompt", "")
            skip_perms = (pending or {}).get("skip_perms", False)

            if action == "new_resume":
                # Live session → switch to it; GONE session → /resume flow.
                if sess and sess.status != Status.GONE:
                    self.registry.last_active_session = session_name
                    self.registry.mark_dirty()
                    asyncio.create_task(
                        self._maybe_update_bot_name(session_name)
                    )
                    if prompt and sess.queue_prompt(prompt,
                                                    pending.get("msg_id", 0)):
                        self.registry.mark_dirty()
                    try:
                        await query.edit_message_text(
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
                        await query.edit_message_text(text, **kw)
                    except Exception:
                        await self._app.bot.send_message(
                            chat_id=CHAT_ID, text=text, **kw,
                        )

                await self._do_resume(label=label, reply_fn=_reply)
                # Queue the prompt into the freshly-resumed session.
                if prompt:
                    resumed = self.registry.get(session_name)
                    if resumed and resumed.queue_prompt(
                        prompt, pending.get("msg_id", 0),
                    ):
                        self.registry.mark_dirty()
                return

            if action == "new_replace":
                # Kill alive socket first, then launch fresh (no resume_id).
                if sess and sess.status != Status.GONE:
                    await inject.kill_session(session_name)
                    # Wait briefly for socket to disappear so the next
                    # launch_session's "already exists" check passes.
                    sock = f"{inject.SOCK_PREFIX}{label}.sock"
                    from pathlib import Path as _Path
                    for _ in range(10):
                        await asyncio.sleep(0.2)
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
                    await query.edit_message_text(
                        f"🚀 Launching <b>{html_mod.escape(label)}</b> "
                        f"(fresh)…",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

                ok, err = await inject.launch_session(label, skip_perms=skip_perms)
                if not ok:
                    try:
                        await self._app.bot.send_message(
                            chat_id=CHAT_ID,
                            text=f"❌ {html_mod.escape(err)}",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    return

                new_sess = self.registry.get_or_create(session_name)
                if new_sess.status in (Status.GONE, Status.UNKNOWN):
                    self.registry.transition(session_name, Status.IDLE)
                self.registry.last_active_session = session_name
                self.registry.mark_dirty()
                asyncio.create_task(self._maybe_update_bot_name(session_name))
                asyncio.create_task(self._update_bot_commands())
                if prompt and new_sess.queue_prompt(
                    prompt, pending.get("msg_id", 0),
                ):
                    self.registry.mark_dirty()

                try:
                    await self._app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            f"✅ <b>{html_mod.escape(label)}</b> launched"
                            + ("\n📝 Prompt queued" if prompt else "")
                        ),
                        parse_mode="HTML",
                    )
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

        if not await inject.is_alive(session_name):
            await self._safe_answer(query, f"Session '{session_name}' not found")
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
                await self._safe_answer(query, f"{toggled} {opt_label}")

                # Rebuild keyboard with updated checkmarks
                keyboard = self._build_inline_ask_keyboard(
                    session_name, perm["options"],
                    multi_select=True, selected=selected)
                text = self._build_busy_text(sess.label, "Waiting", sess)
                await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
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
                await asyncio.sleep(0.15)
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
                actor = member if self.team is not None and member.id != 0 else None
                audit_mod.append(
                    session=sess.name, label=sess.label, action="Answered",
                    tool="AskUserQuestion",
                    summary=f"{question_text[:120]} → {verb[:80]}",
                    user_id=actor.id if actor else None,
                    username=actor.label if actor else "",
                )
                by_attr = f" by {attribution_label(actor)}" if actor else ""
                try:
                    await self._app.bot.send_message(
                        CHAT_ID,
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
            await self._safe_answer(query, f"{verb} [{sess.label}]")

            if sess.pending_permission:
                # Collapse current question into tool_history
                perm = sess.pending_permission
                if perm.get("ask_question"):
                    audit_detail = perm["question"][:80]
                    collapsed = f"❓ {audit_detail[:40]} → {verb}"
                    audit_tool_name = "AskUserQuestion"
                else:
                    audit_detail = perm.get("tool_summary", "Permission")[:80]
                    collapsed = f"🔑 {audit_detail[:60]} → {verb}"
                    audit_tool_name = (perm.get("tool_info") or {}).get("name", "")
                sess.record_tool(collapsed, True)

                # Persistent audit trail to disk (jsonl).
                from aipager import audit as audit_mod
                actor = (
                    member if self.team is not None and member.id != 0 else None
                )
                audit_mod.append(
                    session=sess.name, label=sess.label, action=verb,
                    tool=audit_tool_name, summary=audit_detail,
                    user_id=actor.id if actor else None,
                    username=actor.label if actor else "",
                )

                # Audit reply in chat — persistent record of the decision
                # the user just made. Threaded under the busy message so
                # the scrollback reads as a conversation.
                audit_icon = {
                    "Allowed": "✅",
                    "Denied": "🚫",
                    "Continue": "▶️",
                }.get(verb, "·")
                by_attr = f" by {attribution_label(actor)}" if actor else ""
                try:
                    await self._app.bot.send_message(
                        CHAT_ID,
                        f"{audit_icon} <b>{html_mod.escape(sess.label)}</b> · "
                        f"{verb}{html_mod.escape(by_attr)} · "
                        f"{html_mod.escape(audit_detail)}",
                        parse_mode="HTML",
                        reply_to_message_id=(sess.busy_msg_id
                                             if sess.busy_msg_id and sess.busy_msg_id > 0
                                             else None),
                    )
                except Exception:
                    log.debug("[%s] audit message send failed", sess.label,
                              exc_info=True)

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
            await self._safe_answer(query, f"Failed to send to {session_name}")

    async def _handle_start_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start and /help — friendly welcome with current state."""
        if not await self._authorize(update, allow_read_only=True):
            return
        sessions = sorted(
            (sess.label, sess.status.name.lower())
            for sess in self.registry.all_sessions().values()
            if sess.status != Status.GONE and sess.label
        )
        if sessions:
            session_block = "\n".join(f"  • <b>{lbl}</b> · {status}"
                                     for lbl, status in sessions)
        else:
            session_block = "  <i>(no sessions yet)</i>"

        text = (
            "\U0001f44b <b>aipager</b> — Telegram remote for Claude Code\n\n"
            "Talk to your local Claude sessions from this chat. The daemon "
            "is running and mirroring sessions to you live.\n\n"
            "<b>Tracked sessions</b>\n"
            f"{session_block}\n\n"
            "<b>How to use</b>\n"
            "  • Tap a session name on the keyboard below to switch to it.\n"
            "  • Send a plain message — it goes to the active session.\n"
            "  • Reply to a session's message to pin your prompt to that session.\n\n"
            "<b>Open a new session on your computer</b>\n"
            "  <code>aipager session &lt;name&gt;</code>\n\n"
            "<b>Commands</b>\n"
            "  /status — per-session dashboard\n"
            "  /stop — interrupt the active session\n"
            "  /kill — terminate a session\n"
            "  /new — launch a new session (alias for `aipager session`)\n"
        )
        try:
            await self._app.bot.send_message(
                update.effective_chat.id, text, parse_mode="HTML",
            )
        except Exception:
            log.warning("Failed to send /start welcome", exc_info=True)
        # Make sure the persistent keyboard is showing.
        await self._send_keyboard(level="main")

    async def _handle_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command — rich per-session dashboard."""
        if not await self._authorize(update, allow_read_only=True):
            return
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
        if not await self._authorize(update):
            return
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

    async def _handle_clearqueue_cmd(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /clearqueue — drop pending queued prompts for the last
        active session without interrupting the running task.

        `/stop` already drains the queue, but at the cost of interrupting
        the current work. `/clearqueue` is the "just stop QUEUING for
        the next thing" surgical tool: the current prompt keeps running,
        the queue empties.
        """
        if not await self._authorize(update):
            return
        name = self.registry.last_active_session
        if not name:
            await update.message.reply_text(
                "No active session — switch to one with /<label> first.",
            )
            return
        sess = self.registry.get(name)
        if not sess:
            await update.message.reply_text(f"Session '{name}' not found.")
            return
        dropped = len(sess.pending_queue)
        if dropped == 0:
            await update.message.reply_text(
                f"Nothing to clear in [{html_mod.escape(sess.label)}].",
                parse_mode="HTML",
            )
            return
        sess.pending_queue.clear()
        self.registry.mark_dirty()
        plural = "s" if dropped > 1 else ""
        await update.message.reply_text(
            f"🗑️ Cleared {dropped} queued message{plural} for "
            f"[<b>{html_mod.escape(sess.label)}</b>].",
            parse_mode="HTML",
        )
        log.info("[%s] /clearqueue cleared %d entries", sess.label, dropped)

    async def _handle_kill_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /kill <label> — destroy a session entirely."""
        if not await self._authorize(update):
            return
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
        # Two-tap confirmation: show inline [💀 Kill] [Cancel] instead of
        # destroying immediately. One mistype on a phone shouldn't wipe a
        # session; the user explicitly confirms here.
        sessions = self.registry.all_sessions()
        target_name = None
        for name, sess in sessions.items():
            if sess.label == target_label and sess.status != Status.GONE:
                target_name = name
                break
        if target_name is None:
            await update.message.reply_text(
                f"⚠️ Unknown or already-gone session: {target_label}",
            )
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "💀 Kill", callback_data=f"{target_name}:kill-confirm"),
            InlineKeyboardButton(
                "Cancel", callback_data=f"{target_name}:kill-cancel"),
        ]])
        await update.message.reply_text(
            f"⚠️ Kill session [<b>{html_mod.escape(target_label)}</b>]? "
            "This will terminate the running claude process.",
            reply_markup=keyboard,
            parse_mode="HTML",
        )

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
        """Handle /new <name> [prompt] — launch a new Claude Code session.

        Prefix the name with ``!`` to launch with
        ``--dangerously-skip-permissions`` (e.g. ``/new !dev fix the bug``).
        Without the prefix, claude runs with its default safety checks.
        """
        if not await self._authorize(update):
            return
        text = update.message.text.strip()
        parts = text.split(maxsplit=2)  # /new <name> [prompt...]
        if len(parts) < 2:
            await update.message.reply_text(
                "Usage: /new &lt;name&gt; [initial prompt]\n"
                "Prefix the name with <code>!</code> to skip permission checks "
                "(<code>--dangerously-skip-permissions</code>).\n"
                "Example: /new dev fix the auth bug\n"
                "Example: /new !dev fix the auth bug",
                parse_mode="HTML",
            )
            return

        raw_name = parts[1].strip()
        skip_perms = raw_name.startswith("!")
        name = raw_name.lstrip("!").strip()
        prompt = parts[2].strip() if len(parts) > 2 else ""

        if not name:
            await update.message.reply_text(
                "⚠️ Session name is empty after stripping <code>!</code>.",
                parse_mode="HTML",
            )
            return

        # Name-conflict check — both alive sessions and entries in the
        # GONE history get the same Resume / Replace / Cancel prompt so
        # the user doesn't accidentally throw away a conversation by
        # typing /new with a familiar name. The button callbacks land
        # in _handle_callback under the new_resume / new_replace /
        # new_cancel actions defined below.
        session_name_for_check = f"claude-{name}"
        existing = self.registry.get(session_name_for_check)
        if existing is not None and (
            existing.status != Status.GONE
            or existing.claude_session_id
        ):
            await self._send_new_conflict_prompt(
                update=update,
                existing=existing,
                prompt=prompt,
                skip_perms=skip_perms,
            )
            return

        status_msg = await update.message.reply_text(
            f"🚀 Launching <b>{html_mod.escape(name)}</b>"
            + (" <code>(unsafe)</code>" if skip_perms else "") + "...",
            parse_mode="HTML",
        )

        ok, err = await inject.launch_session(name, skip_perms=skip_perms)
        if not ok:
            await status_msg.edit_text(f"❌ {html_mod.escape(err)}")
            return

        session_name = f"claude-{name}"

        # Switch active session to the new one
        sess = self.registry.get_or_create(session_name)
        # Recover from GONE/UNKNOWN — we just verified the socket is alive
        if sess.status in (Status.GONE, Status.UNKNOWN):
            self.registry.transition(session_name, Status.IDLE)
        # Team-mode attribution: record the creator (and current driver).
        self._mark_driver(sess, update)
        self.registry.last_active_session = session_name
        self.registry.mark_dirty()
        asyncio.create_task(self._maybe_update_bot_name(session_name))
        asyncio.create_task(self._update_bot_commands())

        # Queue the initial prompt if given — it'll drain on first IDLE
        if prompt:
            # Flatten newlines (lesson: newlines cause premature Enter)
            prompt = prompt.replace("\n", " — ")
            if sess.queue_prompt(prompt, update.message.message_id):
                self.registry.mark_dirty()

        await status_msg.edit_text(
            f"✅ <b>{html_mod.escape(name)}</b> launched"
            + ("\n📝 Prompt queued" if prompt else ""),
            parse_mode="HTML",
        )
        log.info("Launched session %s (prompt=%s)", name, bool(prompt))

    # ---- /new name-conflict prompt --------------------------------------

    async def _send_new_conflict_prompt(self, *, update: Update,
                                          existing: TrackedSession,
                                          prompt: str,
                                          skip_perms: bool) -> None:
        """Render the Resume / Replace / Cancel buttons when /new hits a known name.

        ``existing.status`` tells us whether the conflict is with a live
        session (`!= GONE`) or a history entry (`GONE` with a stashed
        claude_session_id). Both flows share the same buttons; the
        callback distinguishes via the session's current status.
        """
        label = existing.label
        alive = existing.status != Status.GONE

        self._new_conflict_pending[existing.name] = {
            "prompt": prompt,
            "skip_perms": skip_perms,
            "user_id": (update.effective_user.id
                        if update.effective_user else 0),
            "msg_id": update.message.message_id,
        }

        if alive:
            header = (
                f"⚠️ <b>{html_mod.escape(label)}</b> is already running.\n"
                f"What would you like to do?"
            )
            resume_label = "↩️ Switch to it"
        else:
            preview = existing.last_assistant_preview or ""
            header = (
                f"♻️ <b>{html_mod.escape(label)}</b> was previously used.\n"
            )
            if preview:
                header += (
                    f"<i>Last response:</i>\n"
                    f"<blockquote>{html_mod.escape(preview)}</blockquote>\n"
                )
            header += "What would you like to do?"
            resume_label = "♻️ Resume"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(resume_label,
                                     callback_data=f"{existing.name}:new_resume"),
                InlineKeyboardButton("🆕 Replace (fresh)",
                                     callback_data=f"{existing.name}:new_replace"),
            ],
            [
                InlineKeyboardButton("↩️ Cancel",
                                     callback_data=f"{existing.name}:new_cancel"),
            ],
        ])
        await update.message.reply_text(
            header, parse_mode="HTML", reply_markup=keyboard,
        )

    # ---- /resume — bring back a previously-gone session by name ----------

    _RESUME_PAGE_SIZE = 10

    def _gone_sessions_sorted(self) -> list[TrackedSession]:
        """Return GONE sessions, newest-first by gone_at, for /resume listings."""
        gone = [
            s for s in self.registry.all_sessions().values()
            if s.status == Status.GONE
        ]
        gone.sort(key=lambda s: s.gone_at or 0.0, reverse=True)
        return gone

    @staticmethod
    def _fmt_gone_ago(gone_at: float | None) -> str:
        """Short relative timestamp for picker rows ('2h ago', 'just now')."""
        if not gone_at:
            return "?"
        delta = max(0, int(time.time() - gone_at))
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        if delta < 86400:
            return f"{delta // 3600}h ago"
        return f"{delta // 86400}d ago"

    def _render_resume_picker(self, page: int = 0) -> tuple[str, InlineKeyboardMarkup | None]:
        """Render the paginated /resume picker. Returns (text, keyboard or None)."""
        gone = self._gone_sessions_sorted()
        if not gone:
            return "📭 No previous sessions to resume.", None

        page_size = self._RESUME_PAGE_SIZE
        total_pages = (len(gone) + page_size - 1) // page_size
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        chunk = gone[start:start + page_size]

        rows: list[list[InlineKeyboardButton]] = []
        for s in chunk:
            label = f"{s.label} — {self._fmt_gone_ago(s.gone_at)}"
            rows.append([InlineKeyboardButton(
                label, callback_data=f"{s.name}:resume",
            )])

        # Pagination row only when there's more than one page
        if total_pages > 1:
            nav: list[InlineKeyboardButton] = []
            if page > 0:
                nav.append(InlineKeyboardButton(
                    "« Prev", callback_data=f"_:resume_page:{page - 1}",
                ))
            nav.append(InlineKeyboardButton(
                f"Page {page + 1}/{total_pages}",
                callback_data="_:resume_noop",
            ))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(
                    "Next »", callback_data=f"_:resume_page:{page + 1}",
                ))
            rows.append(nav)

        text = (
            f"📚 <b>Previous sessions</b> ({len(gone)} total)\n"
            f"Tap a name to resume:"
        )
        return text, InlineKeyboardMarkup(rows)

    async def _handle_resume_cmd(self, update: Update,
                                  ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /resume [<name>] — resume a previous session, or open picker."""
        if not await self._authorize(update):
            return
        parts = (update.message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            text, kb = self._render_resume_picker(page=0)
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=kb,
            )
            return
        name = parts[1].strip().lstrip("@/").lower()
        # Reuse direct-resume helper so the /resume command and the picker
        # button take exactly the same code path (and produce the same
        # errors / dashboard reply).
        await self._do_resume(
            label=name,
            reply_fn=update.message.reply_text,
            update=update,
        )

    async def _do_resume(self, *, label: str, reply_fn,
                          update: Update | None = None,
                          query=None) -> None:
        """Shared resume logic for /resume <name> and picker callbacks.

        ``reply_fn`` is the async-callable used to send the result back
        (``update.message.reply_text`` for command, ``query.edit_message_text``
        for callbacks). ``update`` is used to attribute the driver in team
        mode when available.
        """
        session_name = f"claude-{label}"
        sess = self.registry.get(session_name)

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
        preview = sess.last_assistant_preview or ""
        body = dashboard
        if preview:
            body = (
                f"♻️ Resumed <b>{html_mod.escape(label)}</b>\n\n"
                f"{dashboard}\n\n"
                f"<i>Last response:</i>\n"
                f"<blockquote>{html_mod.escape(preview)}</blockquote>"
            )
        else:
            body = f"♻️ Resumed <b>{html_mod.escape(label)}</b>\n\n{dashboard}"

        await reply_fn(body, parse_mode="HTML")
        log.info("[%s] Resumed (claude_session_id=%s, cwd=%s)",
                 label, resume_id, cwd or "<daemon>")

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
        if not await self._authorize(update):
            return
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

        # Routing precedence:
        #   1. reply_to.message_id → exact match in the msg_map
        #   2. reply_to.message_id → any session whose last_msg_id matches
        #      (catches replies to messages too old for the capped map)
        #   3. parse the reply_to text for a known session label
        #      (catches replies to OLD messages from before this daemon run,
        #       e.g. busy messages sent before track_message covered them)
        #   4. last_active_session (the session the user most recently addressed)
        #   5. Error: nothing to route to → tell the user instead of silently dropping
        reply_to = update.message.reply_to_message
        sess = None
        fallback_reason = ""
        if reply_to:
            sess = self.registry.get_session_by_msg(reply_to.message_id)
            if not sess:
                for cand in self.registry.all_sessions().values():
                    if cand.last_msg_id == reply_to.message_id:
                        sess = cand
                        break
            if not sess:
                sess = self._guess_session_from_text(
                    reply_to.text or reply_to.caption or ""
                )
                if sess:
                    fallback_reason = (
                        f"reply target msg {reply_to.message_id} not tracked — "
                        f"recovered session [{sess.label}] from message text"
                    )
            if not sess:
                fallback_reason = (
                    f"reply target msg {reply_to.message_id} unknown — "
                    "routed by last_active fallback"
                )
        if not sess:
            name = self.registry.last_active_session
            sess = self.registry.get(name) if name else None

        if not sess:
            log.warning("Dropped text %r — no session to route to", text[:80])
            await update.message.reply_text(
                "⚠️ I don't know which session this is for. Pick one with "
                "/<label> or the keyboard."
            )
            return

        if fallback_reason:
            log.info("[%s] %s", sess.label, fallback_reason)

        self.registry.last_active_session = sess.name  # user is talking to this session now
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if session is busy — inject when it goes IDLE
        if sess.status == Status.BUSY:
            if not sess.queue_prompt(text, update.message.message_id):
                await update.message.reply_text(
                    f"⚠️ Queue is full ({QUEUE_CAP} pending) for "
                    f"[{html_mod.escape(sess.label)}]. Tap stop or wait "
                    "for the current task to finish.",
                    parse_mode="HTML",
                )
                return
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] Queued (busy): %s", sess.label, text[:80])
            return

        sess.trigger_msg_id = update.message.message_id
        sess.last_prompt = text
        self._mark_driver(sess, update)
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, text)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Sent text: %s", sess.label, text[:80])
        else:
            await update.message.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _handle_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle voice messages — transcribe via local Whisper, inject as prompt.

        Requires the optional ``aipager[voice]`` extra. When unavailable
        we tell the user once and bail out. On success we send back the
        transcript (so the user can verify what we heard) and inject it
        into the active session like any other text prompt.
        """
        if not await self._authorize(update):
            return
        msg = update.message
        if not msg.voice:
            return

        # Voice extra installed?
        from aipager import voice
        if not voice.is_available():
            # Offer to install it remotely — user might be away from
            # their terminal. The callback handler does the actual work.
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📦 Install voice", callback_data="__voice__:install"),
                InlineKeyboardButton(
                    "Cancel", callback_data="__voice__:cancel"),
            ]])
            await msg.reply_text(
                "⚠️ Voice messages need the optional voice extra "
                "(~200 MB install · ~74 MB model on first use).",
                reply_markup=keyboard,
            )
            return

        # Pre-size check (Telegram bot file-download limit, ~20 MB).
        size = msg.voice.file_size or 0
        if size > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES:
            mb = size / (1024 * 1024)
            limit_mb = TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES // (1024 * 1024)
            await msg.reply_text(
                f"⚠️ Voice message is {mb:.1f} MB; Telegram bots are capped at {limit_mb} MB.",
            )
            return

        # Download the .ogg file
        try:
            FILE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            save_path = FILE_DOWNLOAD_DIR / f"{ts}_voice.ogg"
            tg_file = await msg.voice.get_file()
            await tg_file.download_to_drive(custom_path=str(save_path))
        except Exception:
            log.warning("Voice download failed", exc_info=True)
            await msg.reply_text("❌ Failed to download voice message.")
            return

        # Acknowledge so the user knows we're working
        ack_msg = await msg.reply_text(
            "🎙️ <i>Transcribing…</i>", parse_mode="HTML",
        )

        try:
            text = await voice.transcribe(str(save_path))
        except voice.VoiceUnavailable as e:
            await ack_msg.edit_text(f"⚠️ {e}")
            return
        except Exception as e:
            log.warning("transcription failed", exc_info=True)
            await ack_msg.edit_text(f"❌ Transcription failed: {e}")
            return
        finally:
            # Clean up the audio file once we've transcribed it
            save_path.unlink(missing_ok=True)

        if not text:
            await ack_msg.edit_text("⚠️ Couldn't make out any speech in that.")
            return

        # Show the transcript to the user
        await ack_msg.edit_text(
            f"🎙️ <i>Heard:</i> {html_mod.escape(text)}",
            parse_mode="HTML",
        )

        # Inject the transcript as if it were a regular text reply.
        # Route to the same session that bare text would (last_active or
        # reply target), reusing _handle_message's logic. Build a
        # lightweight shim Update so we don't duplicate routing code.
        # Simpler: write the text into the existing message and dispatch.
        # Cleanest: invoke the same logic inline.
        await self._dispatch_voice_transcript(update, text)

    async def _dispatch_voice_transcript(
        self, update: Update, transcript: str,
    ) -> None:
        """Inject a voice-transcript into the active session as a prompt.

        Mirrors the routing precedence of ``_handle_message`` (reply
        target → last_active_session) so the user's voice behaves like
        their typed text would.
        """
        reply_to = update.message.reply_to_message
        sess = None
        if reply_to:
            sess = self.registry.get_session_by_msg(reply_to.message_id)
            if not sess:
                for cand in self.registry.all_sessions().values():
                    if cand.last_msg_id == reply_to.message_id:
                        sess = cand
                        break
            if not sess:
                sess = self._guess_session_from_text(
                    reply_to.text or reply_to.caption or ""
                )
        if not sess:
            name = self.registry.last_active_session
            sess = self.registry.get(name) if name else None
        if not sess:
            await update.message.reply_text(
                "⚠️ Voice transcribed but no active session to send it to. "
                "Pick one with /<label> first."
            )
            return

        self.registry.last_active_session = sess.name
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if busy
        if sess.status == Status.BUSY:
            if not sess.queue_prompt(transcript, update.message.message_id):
                await update.message.reply_text(
                    f"⚠️ Queue full for [{html_mod.escape(sess.label)}].",
                    parse_mode="HTML",
                )
                return
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] Voice queued (busy): %r", sess.label, transcript[:80])
            return

        sess.trigger_msg_id = update.message.message_id
        sess.last_prompt = transcript
        self._mark_driver(sess, update)
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, transcript)
        if ok:
            await self._react(update, "🎙️")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Voice injected: %r", sess.label, transcript[:80])
        else:
            await update.message.reply_text(
                f"❌ Failed to inject transcript into [{sess.label}]",
            )

    async def _handle_file(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photo/document messages — download file and inject prompt."""
        if not await self._authorize(update):
            return
        msg = update.message

        # Up-front size check — Telegram's bot API tops out at 20 MB on
        # download, so we'd rather reject with a clear message than try
        # and fail with a vague "Failed to download" later.
        file_size = None
        if msg.document:
            file_size = msg.document.file_size
        elif msg.photo:
            # Largest size is last; treat its file_size as the cap
            file_size = msg.photo[-1].file_size if msg.photo[-1] else None
        if file_size and file_size > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES:
            mb = file_size / (1024 * 1024)
            limit_mb = TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES // (1024 * 1024)
            await msg.reply_text(
                f"⚠️ File is {mb:.1f} MB. The Telegram bot API caps file "
                f"downloads at {limit_mb} MB. Try splitting it, or paste the "
                "content as text.",
            )
            return

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

        # Resolve target session (same routing precedence as _handle_message)
        reply_to = msg.reply_to_message
        sess = None
        if reply_to:
            sess = self.registry.get_session_by_msg(reply_to.message_id)
            if not sess:
                for cand in self.registry.all_sessions().values():
                    if cand.last_msg_id == reply_to.message_id:
                        sess = cand
                        break
            if not sess:
                sess = self._guess_session_from_text(
                    reply_to.text or reply_to.caption or ""
                )
        if not sess:
            name = self.registry.last_active_session
            sess = self.registry.get(name) if name else None

        if not sess:
            await msg.reply_text(
                "⚠️ I don't know which session this file is for. Pick one with "
                "/<label> or the keyboard."
            )
            return

        self.registry.last_active_session = sess.name
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await msg.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if busy
        if sess.status == Status.BUSY:
            if not sess.queue_prompt(prompt, msg.message_id):
                await msg.reply_text(
                    f"⚠️ Queue is full ({QUEUE_CAP} pending) for "
                    f"[{html_mod.escape(sess.label)}]. File not queued.",
                    parse_mode="HTML",
                )
                return
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] File queued (busy): %s", sess.label, filename)
            return

        sess.trigger_msg_id = msg.message_id
        sess.last_prompt = prompt
        self._mark_driver(sess, update)
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
            if not sess.queue_prompt(prompt_text, update.message.message_id):
                await update.message.reply_text(
                    f"⚠️ Queue is full ({QUEUE_CAP} pending) for "
                    f"[{html_mod.escape(sess.label)}]. Tap stop or wait "
                    "for the current task to finish.",
                    parse_mode="HTML",
                )
                return
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
