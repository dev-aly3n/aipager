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
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import Forbidden

from aipager.dtach import inject

from aipager.config import (
    BUSY_EDIT_INTERVAL, CHAT_ID,
)
from aipager.state import Status, TrackedSession

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





class NotifyMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

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
