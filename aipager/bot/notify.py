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

from aipager.bot.rich_message import (
    RichMessageBlocked,
    RichMessageFallbackRequired,
    detect_rtl,
    send_rich_message,
)


from aipager.config import (
    KEEP_FINISHED_CARD,
    STALE_BUSY_TIMEOUT,
    STREAM_EDIT_INTERVAL,
)
from aipager.state import Status, TrackedSession
from aipager.bot.animation import FINAL_VERB

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
    resolve_chat_id,
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

        if event == "hook_memory_cap_hit":
            hook_name = context.get("hook", "aipager-hook")
            tool_name = context.get("tool", "")
            tool_suffix = (
                f" during <code>{html_mod.escape(tool_name)}</code>"
                if tool_name else ""
            )
            text = (
                f"⚠️ <b>{html_mod.escape(label)}</b> · memory cap hit"
                f"{tool_suffix}\n"
                "\n"
                f"<code>{html_mod.escape(hook_name)}</code> exceeded its "
                "1 GB limit — one event was dropped. The session is still "
                "running; the tool call that triggered this proceeded "
                "normally.\n"
                "\n"
                "<i>If this repeats, aipager is compensating for a runaway "
                "allocation somewhere in the hook path — please report.</i>"
            )
            try:
                await bot.send_message(
                    resolve_chat_id(sess), text, parse_mode="HTML",
                )
            except Exception:
                log.debug("hook_memory_cap_hit notify failed", exc_info=True)
            return

        if event == "safety_blocked":
            tool = context.get("tool", "?")
            reason = context.get("reason", "")
            # On the FIRST block of a turn, cleanly halt the session
            # (interrupt Claude + cancel the spinner + back to IDLE) so
            # "thinking" stops automatically and doesn't hang. Sticky
            # repeats ("session halted …" — the hook denying every later
            # tool this turn) are audited only: no extra halt, no 🛑 spam.
            sticky = reason.startswith("session halted")
            if not sticky:
                await self._halt_for_safety(sess, reason)
            try:
                from aipager import audit as audit_mod
                driver = self._driver_user(sess)
                audit_mod.append(
                    session=sess.name, label=label, action="Blocked",
                    tool=tool, summary=reason,
                    user_id=driver.id if driver else None,
                    username=driver.label if driver else "",
                    scope_label=self._scope_label(sess.scope_chat_id),
                    scope_chat_id=sess.scope_chat_id or None,
                    denied=True, reason=reason,
                )
            except Exception:
                log.debug("safety_blocked audit failed", exc_info=True)
            return

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
            sess.stream_dirty = True
            now = time.monotonic()
            if now - sess.last_tool_edit_at >= STREAM_EDIT_INTERVAL:
                if await self._edit_busy_rich(sess, "Working") is None:
                    self._stop_animation(sess)
            return

        if event == "assistant_text":
            # A chunk of Claude's prose, straight from the display path.
            # Latching this flag turns the transcript fallback off for good:
            # both sources would otherwise deliver the same sentences.
            sess.stream_hook_live = True
            delta = context.get("delta", "")
            msg_id = context.get("message_id", "")
            if not delta:
                return
            # One assistant message is one block that grows, not a row per
            # chunk. A new message_id starts a new block at the floor: the
            # tool rows recorded since the last block are the ones this
            # message introduced, because a short preamble only reaches the
            # hook once the message — tool calls included — is complete.
            # Everything known is then attributed, so the floor moves up.
            if msg_id and msg_id == sess.stream_msg_id and sess.stream_commentary:
                anchor, text = sess.stream_commentary[-1]
                sess.stream_commentary[-1] = (anchor, text + delta)
            else:
                sess.stream_msg_id = msg_id
                sess.stream_commentary.append((sess.stream_anchor_floor, delta))
                sess.stream_anchor_floor = len(sess.tool_history)
                # The batch now has its sentence; the card can draw both.
                sess.stream_batch_since = None
            if not sess.busy_msg_id or sess.busy_msg_id < 0:
                return
            sess.stream_dirty = True
            if time.monotonic() - sess.last_tool_edit_at >= STREAM_EDIT_INTERVAL:
                if await self._edit_busy_rich(sess, "Working") is None:
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
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                sess.stream_dirty = True
                if time.monotonic() - sess.last_tool_edit_at >= STREAM_EDIT_INTERVAL:
                    if await self._edit_busy_rich(sess, "Working") is None:
                        self._stop_animation(sess)
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
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                sess.stream_dirty = True
                if time.monotonic() - sess.last_tool_edit_at >= STREAM_EDIT_INTERVAL:
                    if await self._edit_busy_rich(sess, "Working") is None:
                        self._stop_animation(sess)
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
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                sess.stream_dirty = True
                if time.monotonic() - sess.last_tool_edit_at >= STREAM_EDIT_INTERVAL:
                    if await self._edit_busy_rich(sess, "Working") is None:
                        self._stop_animation(sess)
            return

        if event == "compacting":
            # Context compaction started — show dot animation
            self._stop_animation(sess)
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                text = f"🔄 <b>{html_mod.escape(label)}</b> · Compacting"
                await self._edit_busy_raw(sess.busy_msg_id, text, chat_id=resolve_chat_id(sess))
            else:
                # No busy message — send a new one
                try:
                    text = f"🔄 <b>{html_mod.escape(label)}</b> · Compacting"
                    msg = await bot.send_message(
                        resolve_chat_id(sess), text, parse_mode="HTML",
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
                await bot.send_message(resolve_chat_id(sess), warn_text, parse_mode="HTML",
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
            # /tool call (legitimate), or wedged.
            #
            # The legitimate cases dominate, so this reads as a status
            # note, not an alert: hourglass rather than ⚠️, and the
            # diagnostic causes collapsed into an expandable blockquote
            # that only opens if the user taps it. Users were reading
            # the old warning-triangle-plus-bullet-wall as a failure
            # report and interrupting healthy sessions.
            # max(1, …) so a sub-60s STALE_BUSY_TIMEOUT override (ops
            # testing) never renders "quiet for 0 min".
            minutes = context.get("minutes", max(1, int(STALE_BUSY_TIMEOUT / 60)))
            stale_text = (
                f"⏳ <b>{html_mod.escape(label)}</b> · still working — "
                f"quiet for {minutes} min\n"
                "\n"
                "No status updates yet. This is usually normal.\n"
                "\n"
                "<blockquote expandable>What could be happening\n"
                "  • Long-running tool call (Bash, WebSearch, large fetch)\n"
                "  • Heavy generation on a very large context\n"
                "  • Compaction in progress\n"
                "  • Rate-limit backoff or subscription limit\n"
                "  • Network wedge or claude crash</blockquote>"
            )
            try:
                keyboard = self._build_stop_keyboard(sess.name)
                await bot.send_message(resolve_chat_id(sess), stale_text, parse_mode="HTML",
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
                result = await self._edit_busy_raw(sess.busy_msg_id, text, chat_id=resolve_chat_id(sess))
                if result is None:
                    sess.busy_msg_id = None
            else:
                try:
                    msg = await bot.send_message(
                        resolve_chat_id(sess), text, parse_mode="HTML",
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
                    await bot.delete_message(chat_id=resolve_chat_id(sess), message_id=sess.busy_msg_id)
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
                await bot.send_message(resolve_chat_id(sess), text, parse_mode="HTML")
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
            card_kept = False
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                if KEEP_FINISHED_CARD:
                    # Leave the timeline in the chat: which tools ran, in what
                    # order, and what Claude said between them is the record of
                    # how this answer was reached. Rendered here — before the
                    # streaming state is reset below and before the answer goes
                    # out — so scrollback reads card, header, body.
                    try:
                        card_kept = await self._edit_busy_rich(
                            sess, FINAL_VERB, final=True,
                        ) is True
                    except Exception:
                        log.debug("Final busy-card render failed", exc_info=True)
                else:
                    try:
                        await bot.delete_message(
                            chat_id=resolve_chat_id(sess),
                            message_id=sess.busy_msg_id,
                        )
                    except Exception:
                        pass
                sess.busy_msg_id = None

            summary = context.get("summary", sess.summary)
            raw_md = context.get("raw_md", "")

            # ── content-selection (design §1, named rule) ──────────────────
            # raw_md takes precedence; fall through to summary, then the
            # session's cached summary, then empty string.
            #
            # `no_response` short-circuits the sess.summary step. It is set
            # only when a producer positively established that the turn
            # emitted Claude Code's no-response placeholder, i.e. produced no
            # text at all. sess.summary holds the PREVIOUS turn's answer, so
            # reaching for it here would publish stale text as the reply to
            # the current prompt — plausible enough to be believed, and so
            # worse than sending no body. An empty content sends the header
            # alone, which is honest.
            content = raw_md or context.get("summary", "")
            if not content and not context.get("no_response"):
                content = sess.summary or ""

            # Reset streaming state — the turn is over.
            sess.stream_commentary = []
            sess.stream_tool_cursor = 0
            sess.stream_msg_id = ""
            sess.stream_anchor_floor = 0
            sess.stream_batch_since = None
            sess.stream_dirty = False
            sess.stream_last_rendered = ""
            sess.stream_offset = 0
            sess.stream_transcript_path = ""

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
                        resolve_chat_id(sess), text, parse_mode="HTML",
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
            header_text = f"✅ <b>{html_mod.escape(label)}</b> · Finished{suffix}"

            # ── Overflow detection ─────────────────────────────────────────
            send_file = False
            body_content = content  # may be truncated below
            if content:
                content_utf8 = content.encode("utf-8")
                if len(content_utf8) > 32768:
                    # Truncate at the last markdown-safe boundary under 32768.
                    bounds = _md_safe_boundaries(content)
                    cut = 0
                    for b in bounds:
                        b_bytes = len(content[:b].encode("utf-8"))
                        if b_bytes <= 32768:
                            cut = b
                    if cut:
                        body_content = content[:cut]
                    else:
                        # No safe boundary found — truncate at byte limit.
                        body_content = content_utf8[:32768].decode("utf-8", errors="ignore")
                    send_file = True

            # ── Send the header (HTML, via PTB) ────────────────────────────
            # Skipped when the finished card is already sitting right above the
            # answer saying the same thing — ✅, label, elapsed. The body then
            # carries the reply link and the tracked message_id in its place.
            # The overflow case keeps the header: the "attached below" note and
            # the document's reply target both live on it.
            skip_header = card_kept and bool(body_content) and not send_file
            msg_id = 0
            if not skip_header:
                if send_file:
                    header_text += "\n\n📎 <i>Full response attached below ↓</i>"
                log.debug("[%s] Sending IDLE notification (%d chars header)",
                          label, len(header_text))
                try:
                    msg = await bot.send_message(
                        resolve_chat_id(sess), header_text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                    msg_id = msg.message_id
                except Exception:
                    log.warning("[%s] Failed to send IDLE header", label, exc_info=True)
                    # We still try to send the body below; msg_id stays 0 so
                    # nothing downstream tracks or replies to a message that
                    # was never sent.

            # ── Send the body via sendRichMessage ──────────────────────────
            if body_content:
                is_rtl = detect_rtl(body_content)
                log.info("[%s] sendRichMessage: %d chars, rtl=%s, overflow=%s",
                         label, len(body_content), is_rtl, send_file)
                reply_to = sess.trigger_msg_id if skip_header else None
                try:
                    sent = await send_rich_message(
                        int(resolve_chat_id(sess)),
                        body_content,
                        is_rtl=is_rtl,
                        reply_to_message_id=reply_to,
                    )
                    if skip_header and isinstance(sent, dict):
                        msg_id = sent.get("message_id") or 0
                except RichMessageBlocked:
                    _log_blocked_once(Exception("sendRichMessage 403"))
                except (RichMessageFallbackRequired, Exception):
                    # Plain-text fallback — split into ≤4096-char chunks at
                    # markdown-safe boundaries so the send cannot fail to parse.
                    log.warning("[%s] sendRichMessage failed — falling back to plain text",
                                label, exc_info=True)
                    bounds = _md_safe_boundaries(body_content)
                    chunks: list[str] = []
                    prev = 0
                    for b in bounds:
                        chunk = body_content[prev:b]
                        if len(chunk.encode("utf-8")) > 4096:
                            # Safety: hard-cut at 4096 bytes if a single segment
                            # exceeds the limit (very long paragraph, no breaks).
                            encoded = chunk.encode("utf-8")
                            pos = 0
                            while pos < len(encoded):
                                piece = encoded[pos:pos + 4096].decode("utf-8", errors="ignore")
                                if piece:
                                    chunks.append(piece)
                                pos += 4096
                        elif chunk:
                            chunks.append(chunk)
                        prev = b
                    tail = body_content[prev:]
                    if tail:
                        encoded = tail.encode("utf-8")
                        pos = 0
                        while pos < len(encoded):
                            piece = encoded[pos:pos + 4096].decode("utf-8", errors="ignore")
                            if piece:
                                chunks.append(piece)
                            pos += 4096
                    if not chunks:
                        chunks = [body_content[:4096]]
                    for chunk in chunks:
                        try:
                            fallback = await bot.send_message(
                                resolve_chat_id(sess), chunk,
                                # No parse_mode → Telegram cannot raise a parse
                                # error; this is the "never lose a reply" safety net.
                                reply_to_message_id=(
                                    reply_to if not msg_id else None
                                ),
                            )
                            # With no header, the first chunk that lands takes
                            # over as the tracked message for this reply.
                            if skip_header and not msg_id:
                                msg_id = fallback.message_id
                        except Exception:
                            log.warning("[%s] plain-text fallback chunk send failed",
                                        label, exc_info=True)

            sess.trigger_msg_id = None  # reply cycle complete
            self.registry.mark_dirty()
            if msg_id:
                self.registry.track_message(msg_id, sess.name)
            await self._maybe_update_bot_name(sess.name)

            # ── Send full response as .txt attachment for overflow ─────────
            file_content = content if send_file else ""
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
                                resolve_chat_id(sess), document=f, filename=f"{label}_response.txt",
                                reply_to_message_id=msg_id or None,
                            )
                        tmp.unlink(missing_ok=True)
                    except Forbidden as e:
                        _log_blocked_once(e)
                    except Exception:
                        log.warning("Failed to send full response file", exc_info=True)

            # Broadcast to observer bots (header only — rich messages are not
            # observable via the same channel; send the header as summary).
            if self.observers:
                obs_text = header_text
                if send_file and file_content:
                    doc_bytes = file_content.encode("utf-8")
                    asyncio.create_task(self.observers.broadcast_document(
                        obs_text, doc_bytes, f"{label}_response.txt"))
                else:
                    asyncio.create_task(self.observers.broadcast(obs_text))

            # Flush next queued message (one at a time, rest flush on next IDLE)
            if sess.pending_queue:
                queued_text, queued_trigger, _queued_at = sess.pending_queue.pop(0)
                sess.trigger_msg_id = queued_trigger
                sess.last_prompt = queued_text
                self.registry.mark_dirty()
                ok = await self._inject_prompt(sess, queued_text)
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
            if tool_info:
                tool_name = tool_info.get("name", "")
                if self.scopes is not None:
                    # v2: deny set from scope + role + per-user (owner/
                    # admin bypass). See AuthMixin._tool_auto_denied.
                    if self._tool_auto_denied(sess, tool_name):
                        triggerer = self._driver_user(sess)
                        await self._auto_deny(sess, tool_info, triggerer)
                        return
                elif self.team is not None and self.team.rules.deny_tools:
                    triggerer = self._driver_user(sess)
                    if self.team.rules.tool_is_denied(tool_name, triggerer):
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
                result = await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard, chat_id=resolve_chat_id(sess))
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
                    resolve_chat_id(sess), text, reply_markup=keyboard, parse_mode="HTML",
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
                        chat_id=resolve_chat_id(sess),
                        message_id=sess.last_msg_id,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass  # message may be too old or already edited
