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
import time
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)


from aipager.config import (
    CHAT_ID,
)
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
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)





class DashboardMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

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

    # ---- /resume — bring back a previously-gone session by name ----------

    _RESUME_PAGE_SIZE = 10

    def _gone_sessions_sorted(
        self, scope_chat_id: int | None = None,
    ) -> list[TrackedSession]:
        """GONE sessions, newest-first by gone_at, for /resume listings.

        ``scope_chat_id`` restricts the list to the calling chat's scope
        so a DM/group only ever sees its own previous sessions.
        """
        gone = [
            s for s in self.registry.all_sessions(scope_chat_id).values()
            if s.status == Status.GONE
        ]
        gone.sort(key=lambda s: s.gone_at or 0.0, reverse=True)
        return gone

    @staticmethod
    def _fmt_gone_ago(gone_at: float | None) -> str:
        """Short relative timestamp for picker rows ('2h ago', 'just now')."""
        if not gone_at:
            return "earlier"
        delta = max(0, int(time.time() - gone_at))
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        if delta < 86400:
            return f"{delta // 3600}h ago"
        return f"{delta // 86400}d ago"

    def _render_resume_picker(
        self, page: int = 0, scope_chat_id: int | None = None,
    ) -> tuple[str, InlineKeyboardMarkup | None]:
        """Render the paginated /resume picker. Returns (text, keyboard or None).

        ``scope_chat_id`` scopes the listing to the calling chat.
        """
        gone = self._gone_sessions_sorted(scope_chat_id)
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

        # Build the message body with per-row previews so the user
        # can pick by content (not just name/timestamp). Cached
        # preview wins when present; fall back to re-reading the
        # transcript on disk for sessions whose SessionEnd hook
        # was dropped. Per-row snippet capped at ~140 chars so the
        # 10-entry page stays well under Telegram's 4096-char limit.
        lines = [f"📚 <b>Previous sessions</b> ({len(gone)} total)"]
        for s in chunk:
            when = self._fmt_gone_ago(s.gone_at)
            snippet = (s.last_assistant_preview or _read_preview(
                s.transcript_path, max_chars=140,
            )).strip()
            if snippet:
                lines.append(
                    f"🔘 <b>{html_mod.escape(s.label)}</b> — "
                    f"<i>{html_mod.escape(when)}</i>\n"
                    f"<blockquote>{html_mod.escape(snippet)}</blockquote>"
                )
            else:
                lines.append(
                    f"🔘 <b>{html_mod.escape(s.label)}</b> — "
                    f"<i>{html_mod.escape(when)}</i>\n"
                    f"<i>(no preview)</i>"
                )
        lines.append("Tap a button below to resume.")
        text = "\n\n".join(lines)
        return text, InlineKeyboardMarkup(rows)
