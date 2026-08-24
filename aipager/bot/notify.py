"""Telegram bot — python-telegram-bot v22 async Application.

Single owner of all Telegram communication. Handles:
- CallbackQuery (button taps) → dtach_inject.send_keys()
- Message replies → dtach_inject.send_text_and_enter()
- /status command → show all sessions
- /<label> <prompt> → direct send to session
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_mod
import logging
import re
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
    RichMessageGone,
    detect_rtl,
    edit_message_text_rich,
    send_rich_message,
)


from aipager import preferences
from aipager.config import (
    COMPACT_CARD_TIMEOUT_SECONDS,
    COMPACT_DONE_PAUSE_SECONDS,
    STALE_BUSY_TIMEOUT,
    STREAM_EDIT_INTERVAL,
)
from aipager.state import Status, TrackedSession
from aipager.bot.animation import (
    FINAL_VERB, _RICH_LIMIT, _expire_tool_batch, build_full_log,
    build_stream_card_ex,
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
    resolve_chat_id,
    resolve_chat_id_int,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# "I'll send it the moment it lands" — a promise to deliver separately,
# which the single-response job model makes false (see
# NotifyMixin._strip_promise_lines). Both patterns must hit for a line to
# be dropped: the future-delivery phrase AND the work it defers to.
_PROMISE_FUTURE_RE = re.compile(
    r"(\b(i'?ll|i will|we'?ll|we will)\b[^.]{0,80}?"
    r"\b(send|share|post|deliver|follow|drop)\b)"
    r"|\b(will follow|coming next|follows? (below|next|shortly))\b",
    re.IGNORECASE,
)
_PROMISE_SUBJECT_RE = re.compile(
    r"\b(agent|analys\w*|briefing|report|background|results?|breakdown|"
    r"summary)\b",
    re.IGNORECASE,
)

# Separator row between the finished timeline and the answer in `merged`
# layout — a literal row, not a second "✅ Finished" header (that would be
# redundant with the card's own header, per the same reasoning the
# header-skip logic below already uses for `card` layout).
_MERGED_SEPARATOR = "―――――――――――――"


def _drop_answer_tail(sess: TrackedSession, answer: str) -> None:
    """Drop trailing commentary blocks that are just the final answer.

    A backstop for the finished card only. The card normally keeps the answer
    out structurally — a block with no tool row after its anchor is not shown —
    but that relies on the anchor being right, and the anchor is inferred from
    hook arrival order. When a message flushes its prose *before* calling its
    tools the inference can slip, and the cost of it slipping is the whole
    answer quoted directly above the message carrying it, left in the chat for
    good. Text is compared rather than trusted arithmetic, and only a trailing
    run is trimmed, so mid-turn commentary that merely resembles the answer
    survives.
    """
    if not answer:
        return
    hay = " ".join(answer.split())
    while sess.stream_commentary:
        block = " ".join(sess.stream_commentary[-1][1].split())
        # A whitespace-only block must not halt the walk, or a real duplicate
        # sitting below one would survive.
        if block and block not in hay:
            return
        sess.stream_commentary.pop()


def _plain_text_chunks(body_content: str) -> list[str]:
    """Split *body_content* into ≤4096-byte chunks at markdown-safe
    boundaries, for a plain-text (no ``parse_mode``) send that Telegram
    cannot fail to parse.

    Shared by the finished-turn fallback and the job-interim delivery path
    (design.md "model Claude Code background-agent jobs") so the two paths
    can never disagree about how an oversized answer gets split. Always
    returns at least one chunk (truncated to 4096 bytes) even for input
    with no markdown-safe boundary at all.
    """
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
    return chunks


class NotifyMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    async def _send_merged_final(self, sess: TrackedSession, answer: str) -> bool:
        """Try to deliver a finished turn as ONE edit: the finished timeline
        (exactly what ``card`` mode renders) with the answer appended below
        a separator, on the existing busy message.

        Returns ``True`` on success — the turn is fully delivered, the
        caller sends nothing else. Returns ``False`` when the caller MUST
        fall back to the ``replace``-style send so the answer is never
        lost: either the combined text exceeds the byte ceiling (checked
        before any network call — no edit is attempted at all) or the
        edit itself failed for any reason. Never raises.
        """
        try:
            card_md, hid = build_stream_card_ex(sess, FINAL_VERB, final=True)
            # This IS the merged layout's final render — the attach_log
            # decision must see ITS truncation state, not a stale interim
            # tick's (review rev-iter1-002).
            sess.last_card_truncated = hid
        except Exception:
            log.debug("[%s] merged: card render failed", sess.label, exc_info=True)
            return False
        combined = f"{card_md}\n\n{_MERGED_SEPARATOR}\n\n{answer}" if answer else card_md
        if len(combined.encode("utf-8")) > _RICH_LIMIT:
            log.info("[%s] merged: combined card+answer over the byte ceiling "
                     "— falling back to replace", sess.label)
            return False
        chat_id = resolve_chat_id_int(sess)
        if chat_id is None:
            # No numeric destination to edit (unscoped session, no global
            # CHAT_ID configured) — the caller's replace-style fallback
            # can't reach this chat either, but it degrades to that
            # existing, already-tolerant path instead of this ``await``
            # raising and aborting the rest of the turn, answer included.
            log.info("[%s] merged: no numeric chat id — falling back to replace",
                     sess.label)
            return False
        # Combined-text RTL detection (not the answer alone): an RTL answer
        # following an LTR toolchain timeline must still be judged
        # majority-RTL by sample, matching how the plain single-message
        # body-send already treats detect_rtl(body_content) above.
        is_rtl = detect_rtl(combined)
        try:
            result = await edit_message_text_rich(
                chat_id, int(sess.busy_msg_id), combined,
                is_rtl=is_rtl, reply_markup=None,
            )
        except RichMessageBlocked:
            log.warning("[%s] merged: editMessageText blocked", sess.label)
            return False
        except RichMessageGone:
            log.debug("[%s] merged: busy message gone", sess.label)
            sess.busy_msg_id = 0
            return False
        return result is not None

    def _strip_promise_lines(self, text: str, label: str) -> str:
        """Drop "I'll send the briefing when it lands"-style lines from an
        interim answer being composed into the job's SINGLE final message
        ("status-line-at-card-bottom"): the thing they promise sits a few
        lines below them in the same message, so they read as broken.

        Line-based, not tail-only: the observed instance sat in the MIDDLE
        of the interim (between the folder-structure section and the
        Canary Islands section), not at its end. Conservative by
        construction — a line must be short AND carry a first-person
        future-delivery phrase AND name the work it defers to. Never
        empties the text: if every line matched, the original is returned
        untouched. Only ever called at composition time, and never on the
        final answer or on a flush that has no final answer to follow it
        (there the promise is simply true).
        """
        lines = text.split("\n")
        kept = [
            ln for ln in lines
            if not (len(ln) <= 240
                    and _PROMISE_FUTURE_RE.search(ln)
                    and _PROMISE_SUBJECT_RE.search(ln))
        ]
        if len(kept) == len(lines):
            return text
        if not any(ln.strip() for ln in kept):
            return text
        dropped = len(lines) - len(kept)
        log.info(
            "[%s] stripped %d orphaned delivery-promise line(s) (%d chars) "
            "from an interim answer", label, dropped,
            sum(len(ln) for ln in lines) - sum(len(ln) for ln in kept),
        )
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

    def _record_job_interim(
        self, sess: TrackedSession, content: str,
    ) -> None:
        """Hold an interim answer for the job's SINGLE final message ("one
        response per background job" requirement 1/3) instead of sending
        it standalone — a standalone interim pushed the card (and its
        waiting status) off-screen and made the chat bottom read as
        finished. The prose still reaches the operator live via the
        card's own timeline; this buffer exists so the content is also
        delivered in full, once, at close. Byte-identical strays are
        deduped by membership; the buffer is bounded (drop-oldest) so a
        pathological stray-idle storm cannot grow it without limit.
        """
        if not content or content in sess.job_interim_buffer:
            return
        sess.job_interim_buffer.append(content)
        while len(sess.job_interim_buffer) > 20:
            dropped = sess.job_interim_buffer.pop(0)
            log.warning(
                "[%s] job interim buffer over cap — dropping oldest "
                "entry (%d chars)", sess.label, len(dropped),
            )

    async def _flush_job_buffer(self, sess: TrackedSession) -> None:
        """Deliver the accumulated interim content as the job's final
        message on the close paths that never reach the Finished
        composition (grace expiry, agents lost, Stop during the wait) —
        the buffer is the only full copy of those answers, so silently
        dropping it would lose work the operator paid for ("one response
        per background job" requirement 4). No-op on an empty buffer;
        dedup against the last DELIVERED content so a stray double-close
        cannot double-send.
        """
        if not sess.job_interim_buffer:
            return
        content = "\n\n———\n\n".join(sess.job_interim_buffer)
        sess.job_interim_buffer.clear()
        digest = hashlib.md5(content.encode("utf-8")).hexdigest()
        if digest == sess.last_idle_summary_hash:
            return
        sess.last_idle_summary_hash = digest
        is_rtl = detect_rtl(content)
        chat_id = resolve_chat_id_int(sess)
        try:
            if chat_id is None:
                raise RichMessageFallbackRequired("no numeric chat id resolved")
            sent = await send_rich_message(
                chat_id, content, is_rtl=is_rtl,
                reply_to_message_id=sess.trigger_msg_id,
            )
            if isinstance(sent, dict) and sent.get("message_id"):
                self.registry.track_message(
                    sent["message_id"], sess.name, chat_id or 0,
                )
        except RichMessageBlocked:
            _log_blocked_once(Exception("sendRichMessage 403"))
        except (RichMessageFallbackRequired, Exception):
            log.warning(
                "[%s] job buffer sendRichMessage failed — falling back to "
                "plain text", sess.label, exc_info=True,
            )
            for chunk in _plain_text_chunks(content):
                try:
                    fallback = await self._app.bot.send_message(
                        resolve_chat_id(sess), chunk,
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                    self.registry.track_message(
                        fallback.message_id, sess.name,
                        resolve_chat_id_int(sess) or 0,
                    )
                except Exception:
                    log.warning(
                        "[%s] job buffer plain-text fallback chunk send "
                        "failed", sess.label, exc_info=True,
                    )

    async def _handle_job_interim(
        self, sess: TrackedSession, context: dict,
    ) -> None:
        """The ``idle_prompt`` path when ``sess.job_background_open()`` is
        True — an interim Stop/Notification/StopFailure while a background
        agent this job launched is still running (design.md "model Claude
        Code background-agent jobs", requirement 1).

        Never produces a "Finished" card and never sends a standalone
        message: records Claude's interim answer for the job's single
        final message (see :meth:`_record_job_interim` — the prose is
        already live in the card's own timeline), re-renders the live
        card to the waiting frame in place (rather than waiting for the
        animator's next natural tick), and drains one queued prompt if
        any is waiting — the exact same
        :meth:`_drain_next_queued` the real Finished path uses, so a
        message queued during the wait is not stranded until the job's
        eventual real end.
        """
        sess.job_interim_seen = True
        raw_md = context.get("raw_md", "")
        content = raw_md or context.get("summary", "")
        if not content and not context.get("no_response"):
            content = sess.summary or ""
        if content:
            self._record_job_interim(sess, content)
        if sess.busy_msg_id and sess.busy_msg_id > 0:
            sess.stream_dirty = True
            if await self._edit_busy_rich(
                sess, "Working", waiting=True,
            ) is None:
                self._stop_animation(sess)
        await self._drain_next_queued(sess)

    async def _drain_next_queued(self, sess: TrackedSession) -> None:
        """Pop and inject the next queued prompt, one at a time.

        Extracted from its original inline spot at the end of the
        Finished-card path (design.md "model Claude Code background-agent
        jobs") so that path and :meth:`_handle_job_interim` share one
        implementation — a message queued while a job's background work is
        still open drains on the very next idle moment (interim OR real)
        rather than waiting specifically for the real Finished. A no-op
        when the queue is empty.
        """
        if not sess.pending_queue:
            return
        (
            queued_text, queued_trigger, _queued_at,
            queued_reply_context, queued_driver_user_id,
        ) = sess.pending_queue.pop(0)
        sess.trigger_msg_id = queued_trigger
        sess.last_prompt = queued_text
        if queued_trigger is not None:
            # Queued messages are never tracked at queue time
            # (Part 1 only covers the immediate-inject branches)
            # — track now so a reply to a queued-then-drained
            # message is routable via levels 1/2 right away,
            # not only once the next bot message re-tracks the
            # session by coincidence (design.md Part 4).
            self.registry.track_message(
                queued_trigger, sess.name, resolve_chat_id_int(sess) or 0,
            )
        self.registry.mark_dirty()
        ok = await self._inject_prompt(
            sess, queued_text, queued_reply_context,
            msg_id=queued_trigger, chat_id=resolve_chat_id_int(sess),
            driver_user_id=queued_driver_user_id,
        )
        if ok:
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Flushed queued: %s", sess.label, queued_text[:80])

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

        if event == "queue_pickup":
            # The UserPromptSubmit hook matched some of this session's
            # outstanding notes (design.md "queue handoff"). 👍 is the
            # signal that distinguishes "sent" (👀, at send time) from
            # "Claude actually started on this one" — set on every
            # consumed message, never just the last. Expired notes keep
            # their 👀 (no reaction change — Claude may still process a
            # TTL-lapsed one after aipager stops watching) and get one
            # best-effort notice instead of per-message noise.
            consumed = context.get("consumed") or []
            expired = context.get("expired") or []
            default_chat_id = resolve_chat_id(sess)
            for note in consumed:
                note_msg_id = note.get("msg_id")
                if note_msg_id is None:
                    continue
                note_chat_id = note.get("chat_id") or default_chat_id
                try:
                    self.registry.track_message(
                        note_msg_id, sess.name, note_chat_id or 0,
                    )
                except Exception:
                    log.debug("queue_pickup track_message failed",
                              exc_info=True)
                try:
                    await bot.set_message_reaction(
                        note_chat_id, note_msg_id, "👍",
                    )
                except Exception:
                    log.debug("queue_pickup reaction failed", exc_info=True)
            if expired:
                try:
                    preview = (expired[0].get("raw_text") or "")[:80]
                    suffix = f': "{html_mod.escape(preview)}"' if preview else ""
                    await bot.send_message(
                        default_chat_id,
                        f"⏳ <b>{html_mod.escape(label)}</b> · a queued "
                        f"message wasn't confirmed picked up in time"
                        f"{suffix} — Claude may still process it.",
                        parse_mode="HTML",
                    )
                except Exception:
                    log.debug("queue_pickup expiry notice failed",
                              exc_info=True)
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
            # Fallback for terminal-initiated prompts only (a Telegram-sent
            # prompt already called _send_busy_and_animate from
            # _handle_message / _direct_send).
            #
            # This used to bail on `if not sess.busy_msg_id`, a blanket
            # truthiness gate that duplicated — badly — the decision
            # _send_busy_and_animate already makes properly. It could not
            # tell a live card from a wedged one, so a stuck compacting
            # card swallowed every terminal-initiated prompt for that
            # session, and the dead-animation stale-reset was unreachable
            # from here too. Delegate instead: that function is the single
            # authority on whether a fresh card is warranted, and it bails
            # on its own when one is genuinely live.
            await self._send_busy_and_animate(sess)
            return

        if event == "job_continuation":
            # A self-triggered <task-notification> continuation — the SAME
            # job waking itself back up, not a new turn (design.md "model
            # Claude Code background-agent jobs"). Deliberately does NOT
            # call _send_busy_and_animate: that resets busy_started_at,
            # tool_history, active_subagents, subagent_count_this_turn,
            # output_baseline and cost_baseline, which is exactly what a
            # continuation of the SAME job must not do — the whole reason
            # this is a distinct event name rather than "user_prompt_submit".
            # The animator (already ticking through this transition — its
            # loop condition holds on job_background_open() too) settles
            # the header frame on its own next tick regardless; this just
            # nudges it immediately so the card doesn't sit showing a stale
            # waiting frame for a full animation interval after the job has
            # actually resumed.
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                sess.stream_dirty = True
                waiting = sess.status != Status.BUSY
                if await self._edit_busy_rich(
                    sess, "Working", waiting=waiting,
                ) is None:
                    self._stop_animation(sess)
            return

        if event == "job_grace_expired":
            # The last background agent stopped, an interim was delivered,
            # but no <task-notification> continuation arrived within the
            # grace window ("close the background-job endgame" requirement
            # 2's fallback) — close the job honestly: the interim answer
            # stands as the result.
            self._stop_animation(sess)
            elapsed_str = ""
            if sess.busy_started_at:
                elapsed_s = int(time.monotonic() - sess.busy_started_at)
                if elapsed_s >= 60:
                    elapsed_str = f"{elapsed_s // 60}m {elapsed_s % 60}s"
                elif elapsed_s > 0:
                    elapsed_str = f"{elapsed_s}s"
            suffix = f" ({elapsed_str})" if elapsed_str else ""
            text = f"✅ <b>{html_mod.escape(label)}</b> · Finished{suffix}"
            # The accumulated interim is the only full copy of what this
            # job produced — deliver it before the card resolves ("one
            # response per background job" requirement 4). Best-effort.
            try:
                await self._flush_job_buffer(sess)
            except Exception:
                log.debug("[%s] buffer flush on grace expiry failed",
                          label, exc_info=True)
            target_msg_id = sess.busy_msg_id
            if target_msg_id and target_msg_id > 0:
                await self._edit_busy_raw(
                    target_msg_id, text, chat_id=resolve_chat_id(sess),
                )
                sess.busy_msg_id = None
            else:
                try:
                    await bot.send_message(
                        resolve_chat_id(sess), text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                except Exception:
                    log.warning(
                        "Failed to send job_grace_expired notification",
                        exc_info=True,
                    )
            sess.trigger_msg_id = None
            self.registry.mark_dirty()
            if self.observers:
                asyncio.create_task(self.observers.broadcast(text))
            return

        if event == "job_agents_lost":
            # The subagent TTL sweep emptied the last open agent for a
            # session sitting IDLE with a job open (design.md "model Claude
            # Code background-agent jobs" requirement 6) — a job cannot
            # wait forever. Produces the terminal "background agent lost"
            # card rather than a normal Finished: nothing new happened,
            # the agent just disappeared without ever reporting back, so
            # there is no answer to deliver. Deliberately does not drain
            # the pending queue (design.md Risks) — a message queued
            # during the wait drains on the next real idle-transition.
            self._stop_animation(sess)
            elapsed_str = ""
            if sess.busy_started_at:
                elapsed_s = int(time.monotonic() - sess.busy_started_at)
                if elapsed_s >= 60:
                    elapsed_str = f"{elapsed_s // 60}m {elapsed_s % 60}s"
                elif elapsed_s > 0:
                    elapsed_str = f"{elapsed_s}s"
            suffix = f" after {elapsed_str}" if elapsed_str else ""
            text = (f"⚠️ <b>{html_mod.escape(label)}</b> · Finished "
                    f"(background agent lost{suffix})")
            # Whatever the job produced before the agent vanished is only
            # in the buffer — deliver it ("one response per background
            # job" requirement 4). Best-effort.
            try:
                await self._flush_job_buffer(sess)
            except Exception:
                log.debug("[%s] buffer flush on agents-lost failed",
                          label, exc_info=True)
            target_msg_id = sess.busy_msg_id
            if target_msg_id and target_msg_id > 0:
                await self._edit_busy_raw(
                    target_msg_id, text, chat_id=resolve_chat_id(sess),
                )
                sess.busy_msg_id = None
            else:
                try:
                    await bot.send_message(
                        resolve_chat_id(sess), text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                except Exception:
                    log.warning("Failed to send job_agents_lost notification",
                                exc_info=True)
            sess.trigger_msg_id = None
            self.registry.mark_dirty()
            if self.observers:
                asyncio.create_task(self.observers.broadcast(text))
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
            # Settle a batch that has been waiting too long to be this
            # message's. A preamble reaches the hook within half a second of
            # its own rows; anything older belongs to the message before it,
            # which flushed its prose early and called tools afterwards.
            # Checked here rather than only on the animation tick so the
            # floor is right whatever the tick happened to be doing.
            _expire_tool_batch(sess)
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
            elif agent_type:
                # No matching start — daemon restart, or the start was
                # evicted by the active_subagents cap — append as done
                # entry. A phantom SubagentStop (unknown id AND empty
                # agent_type — design.md "model Claude Code background-agent
                # jobs" requirement 5) carries no real information to show,
                # so it must not pollute the timeline with a meaningless
                # "🤖 " row.
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
            # Reuse-vs-send-new is unchanged from today; only the stack
            # bookkeeping (push_compacting, below) is new. The freshly-sent
            # branch calls push_compacting directly with the new message's
            # id rather than going through the busy_msg_id setter — going
            # through the setter on an empty stack would push a phantom
            # kind="busy" entry underneath the compacting one, for a busy
            # card that never actually existed (design.md Decision 1's
            # "no phantom entries" rule).
            existing_msg_id = sess.busy_msg_id
            pushed_msg_id: int | None = None
            if existing_msg_id and existing_msg_id > 0:
                text = f"🔄 <b>{html_mod.escape(label)}</b> · Compacting"
                await self._edit_busy_raw(existing_msg_id, text, chat_id=resolve_chat_id(sess))
                pushed_msg_id = existing_msg_id
            else:
                # No busy message — send a new one
                try:
                    text = f"🔄 <b>{html_mod.escape(label)}</b> · Compacting"
                    msg = await bot.send_message(
                        resolve_chat_id(sess), text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                    pushed_msg_id = msg.message_id
                except Exception:
                    log.warning("Failed to send compact message", exc_info=True)
            if pushed_msg_id is not None:
                sess.push_compacting(
                    pushed_msg_id, time.monotonic(), COMPACT_CARD_TIMEOUT_SECONDS,
                )
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
                keyboard = self._build_compact_keyboard(sess)
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
                keyboard = self._build_stop_keyboard(sess)
                await bot.send_message(resolve_chat_id(sess), stale_text, parse_mode="HTML",
                                       reply_markup=keyboard)
            except Exception:
                pass
            if self.observers:
                asyncio.create_task(self.observers.broadcast(stale_text))
            return

        if event == "compact_done":
            # Compaction finished — show delta, then resume busy animation.
            # pop_compacting() reveals whatever was live underneath the
            # compaction (a no-op if the top isn't kind="compacting" — e.g.
            # this event firing directly on a plain busy card, or a
            # duplicate/late fire after SessionEnd already cleared
            # everything). When nothing was live underneath (compacting
            # itself sent the only message — Decision 4's "nothing to
            # restore" case), fall back to the just-popped entry's own
            # msg_id so this still edits that ONE physical message rather
            # than sending a second — the "edits exactly one existing
            # message" invariant holds regardless of which branch of the
            # "compacting" event originally produced it.
            before_pct = context.get("before_pct", 0)
            after_pct = context.get("after_pct", 0)
            self._stop_animation(sess)
            popped = sess.pop_compacting()
            target_msg_id = sess.busy_msg_id
            if not target_msg_id and popped is not None:
                target_msg_id = popped.msg_id
            text = (f"📦 <b>{html_mod.escape(label)}</b> · "
                    f"Compacted: {before_pct}% → {after_pct}%")
            if target_msg_id and target_msg_id > 0:
                result = await self._edit_busy_raw(target_msg_id, text, chat_id=resolve_chat_id(sess))
                if result is None:
                    sess.busy_msg_id = None
                elif sess.busy_msg_id != target_msg_id:
                    # Nothing was tracking this message after the pop above
                    # (the "nothing to restore" case) — re-establish
                    # tracking on the now-resolved message so later lookups
                    # (merged-reply routing, the next turn's stale-reset)
                    # still find it, matching pre-stack behaviour where
                    # busy_msg_id stayed set after compact_done resolved it.
                    sess.busy_msg_id = target_msg_id
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
            await asyncio.sleep(COMPACT_DONE_PAUSE_SECONDS)
            sess.last_token_pct = after_pct
            self._start_animation(sess)
            return

        if event == "compact_timeout":
            # The compacting card's deadline (COMPACT_CARD_TIMEOUT_SECONDS)
            # fired before any confirming hook arrived (design.md Decision
            # 3) — the session_monitor sweeper's synthetic event, fired
            # regardless of sess.status (unlike every other watchdog in
            # this file), so a compacting card desynced from status (the
            # reported bug: observed at status=idle) is still reclaimed.
            #
            # This text must never claim success — unlike compact_done
            # above, a deadline firing means we have NO positive evidence
            # either way, only the absence of one.
            elapsed = context.get("elapsed_seconds", 0.0)
            minutes = max(1, int(elapsed / 60))
            target_msg_id = sess.busy_msg_id
            text = (f"⏱️ <b>{html_mod.escape(label)}</b> · Compaction didn't "
                    f"confirm completion after {minutes} min")
            if target_msg_id and target_msg_id > 0:
                result = await self._edit_busy_raw(target_msg_id, text, chat_id=resolve_chat_id(sess))
                if result is None:
                    sess.busy_msg_id = None
            sess.pop_compacting()
            if sess.status == Status.BUSY and sess.busy_msg_id:
                # A real turn's busy card was live underneath — resume it
                # exactly as compact_done already does today.
                self._start_animation(sess)
            else:
                # Nothing legitimate left to resume (the observed bug's
                # status=idle case, or any other non-BUSY status) — the
                # edited text above is the final state of this message.
                sess.busy_msg_id = None
            if self.observers:
                asyncio.create_task(self.observers.broadcast(text))
            return

        if event == "session_end":
            # Session exited — clean up busy state and alert user. A job's
            # buffered interim answers are the only full copy of its
            # output — deliver them even when the session died out from
            # under the job (review rev-iter1-002). Best-effort: a failed
            # flush must never block the exit handling.
            try:
                await self._flush_job_buffer(sess)
            except Exception:
                log.debug("[%s] buffer flush on session_end failed",
                          sess.label, exc_info=True)
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
            if sess.is_restarting():
                # The user asked for this exit — `/perms` kills the session to
                # relaunch it under the other permission mode. Reporting it as
                # a crash, alongside the switch confirmation, told the user the
                # session was both fine and dead in the same breath.
                log.info("[%s] session_end during a deliberate restart (%s)"
                         " — not alerting", label, source)
                return
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
            if sess.job_continuation_active and not sess.active_subagents:
                # The <task-notification> continuation turn's own Stop —
                # the job's one true Finished ("close the background-job
                # endgame" requirement 2). Clear the endgame state FIRST so
                # the Finished path below runs exactly as a normal close.
                sess.job_continuation_active = False
                sess.job_grace_until = 0.0
                sess.job_interim_seen = False
            elif sess.job_background_open():
                # A background agent this job launched is still running (or
                # the continuation grace window is open) — this
                # idle-transition is an INTERIM Stop, not the job's true
                # end (design.md "model Claude Code background-agent jobs",
                # requirement 1). Never falls through to the Finished-card
                # disposal logic below: that logic's unconditional
                # active_subagents.clear() (removed just below) was itself
                # the bug this feature fixes — it erased the very state
                # job_background_open() needs to keep working.
                if sess.active_subagents:
                    # A continuation turn that spawned NEW background
                    # agents has ended — back to plain waiting; the next
                    # continuation cycle re-arms via SubagentStop + grace.
                    sess.job_continuation_active = False
                await self._handle_job_interim(sess, context)
                return
            # Snapshot the play-by-play FIRST — before the done-marking
            # below coerces every row to True (which would misreport
            # failed rows as successes in the full-log attachment, review
            # rev-iter1-003) and before the streaming reset wipes the
            # commentary.
            log_tools = list(sess.tool_history)
            log_commentary = list(sess.stream_commentary)

            # Mark all tools as done
            sess.tool_history = [(s, True) for s, _ in sess.tool_history]
            # Stop animation and clean up busy message
            self._stop_animation(sess)
            sess.pending_permission = None  # clear stale inline permission if any
            # `layout` resolves per-session first: this session's own override
            # (if any) wins; otherwise falls back to the scope's stored
            # /settings preference; an untouched scope falls back further to
            # the KEEP_FINISHED_CARD seed (aipager.preferences is the sole
            # owner of that resolution — resolve_preferences, not
            # get_preferences, so a session override actually takes effect
            # here rather than only in the prompt-injection path).
            layout = preferences.resolve_preferences(
                sess.scope_chat_id, sess.preference_overrides(),
            ).layout
            card_kept = False
            # True once the busy card has been successfully disposed of —
            # either kept as the finished card (`card_kept`, below) or
            # deleted outright (`replace`, and `merged`'s own delete-on-
            # fallback a little further down). Either way there's nothing
            # left in the chat repeating what the header would say, so the
            # header itself becomes skippable — see `skip_header` below.
            # Per the user's own framing of `replace`: "still we have only
            # one message after user message but busy message gets
            # removed" — one message, not a removed-card-plus-two-sends.
            card_deleted = False
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                if layout in ("card", "merged"):
                    # Leave the timeline in the chat: which tools ran, in what
                    # order, and what Claude said between them is the record of
                    # how this answer was reached. Rendered here — before the
                    # streaming state is reset below and before the answer goes
                    # out — so scrollback reads card, header, body.
                    _drop_answer_tail(
                        sess, context.get("raw_md") or context.get("summary") or "",
                    )
                    if layout == "card":
                        try:
                            card_kept = await self._edit_busy_rich(
                                sess, FINAL_VERB, final=True,
                            ) is True
                        except Exception:
                            log.debug("Final busy-card render failed", exc_info=True)
                        sess.busy_msg_id = None
                    # "merged": busy_msg_id stays live on purpose — the one
                    # combined edit (timeline + answer) happens below, once
                    # the answer text is known, and clears it either way.
                else:
                    # "replace" — delete the busy card, then send the answer
                    # alone (header skipped below — nothing is left in the
                    # chat that repeats what it would say).
                    try:
                        await bot.delete_message(
                            chat_id=resolve_chat_id(sess),
                            message_id=sess.busy_msg_id,
                        )
                        card_deleted = True
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

            # Content-dedup covers the FINAL delivery too ("close the
            # background-job endgame" requirement 3): a stray idle event
            # re-running this path with content identical to the last
            # delivered summary (interim or final) must not re-post it.
            # The card disposal below still runs — the header/card is
            # idempotent to finalize; only the body re-send is the spam.
            # The hash resets where a genuine new turn starts
            # (_send_busy_and_animate), so a legitimately repeated answer
            # across two real turns still delivers.
            if content:
                _digest = hashlib.md5(content.encode("utf-8")).hexdigest()
                if _digest == sess.last_idle_summary_hash:
                    log.info(
                        "[%s] final summary unchanged since the last "
                        "delivery (hash match) — suppressing re-send",
                        label,
                    )
                    content = ""
                else:
                    sess.last_idle_summary_hash = _digest

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
                if layout == "merged" and sess.busy_msg_id and sess.busy_msg_id > 0:
                    # This branch returns before the merged-delivery attempt
                    # below ever runs — clean up here so the busy card isn't
                    # stranded showing "Working…" with a Stop button forever.
                    try:
                        await bot.delete_message(
                            chat_id=resolve_chat_id(sess),
                            message_id=sess.busy_msg_id,
                        )
                    except Exception:
                        pass
                    sess.busy_msg_id = None
                friendly_error, _retry_after = error_detection
                text = (f"⚠️ <b>{html_mod.escape(label)}</b> · {friendly_error}")
                keyboard = (self._build_retry_keyboard(sess)
                            if sess.last_prompt else None)
                try:
                    msg = await bot.send_message(
                        resolve_chat_id(sess), text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                        reply_markup=keyboard,
                    )
                    self.registry.track_message(msg.message_id, sess.name, resolve_chat_id_int(sess) or 0)
                    await self._maybe_update_bot_name(sess.name)
                except Exception:
                    log.warning("Failed to send error notification", exc_info=True)
                if self.observers:
                    asyncio.create_task(self.observers.broadcast(text))
                # The buffered interim answers are real, completed work —
                # an API error on the FINAL turn must not eat them
                # (review rev-iter1-001).
                try:
                    await self._flush_job_buffer(sess)
                except Exception:
                    log.debug("[%s] buffer flush on api-error failed",
                              label, exc_info=True)
                # Don't clear trigger_msg_id — retry needs it
                # Don't flush pending queue — nothing was processed
                return

            # The job's single response carries everything undelivered
            # ("one response per background job" requirement 3): interim
            # answers accumulated during the background window join the
            # final answer, oldest first, separated clearly. An interim
            # byte-identical to the final is skipped. Composed AFTER the
            # API-error branch above (review rev-iter1-001) so an error
            # return path can still flush the untouched buffer. Cleared
            # here so a stray re-entry cannot resend.
            composed_with_interim = False
            if sess.job_interim_buffer:
                _parts = [
                    self._strip_promise_lines(b, label)
                    for b in sess.job_interim_buffer if b and b != content
                ]
                _parts = [b for b in _parts if b]
                sess.job_interim_buffer.clear()
                if _parts:
                    _tail = [content] if content else []
                    content = "\n\n———\n\n".join(_parts + _tail)
                    composed_with_interim = True

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

            # ── merged layout: one combined edit, timeline + answer ────────
            # Attempted here — after the header text exists (used only by the
            # observer broadcast below) but before the per-answer-alone
            # overflow check, since merged has its own combined-text ceiling
            # check inside _send_merged_final. Only attempted when the busy
            # card is still live; if it isn't (e.g. it was already lost) there
            # is nothing to merge into, so the turn falls through to the
            # ordinary send below — the exact same path "replace" uses.
            merged_delivered = False
            if layout == "merged" and sess.busy_msg_id and sess.busy_msg_id > 0:
                pre_merge_busy_msg_id = sess.busy_msg_id
                # `_send_merged_final` EDITS the existing busy message in
                # place rather than sending a new one, so on success no
                # `registry.track_message` call happens here — reply
                # routing for this message_id keeps working only because
                # it was already registered when the busy message was
                # first sent (see animation.py's `track_message` call
                # right after `send_busy`) and that message_id is never
                # reused for anything else. If a future refactor ever
                # made the merged edit target a *different* message_id
                # than the one tracked at send time, replies to it would
                # silently stop resolving to this session.
                merged_delivered = await self._send_merged_final(sess, content)
                if not merged_delivered:
                    # Losing the timeline is acceptable; losing the answer is
                    # never acceptable — fall back to the replace-style send
                    # below by clearing the (now presumed-gone-or-stale) card.
                    # Falling back to "replace" means behaving exactly like
                    # it: one message, header skipped, once the card is gone.
                    if sess.busy_msg_id:
                        # Still live — _send_merged_final's own failure
                        # wasn't a RichMessageGone, so the card needs an
                        # explicit delete here.
                        try:
                            await bot.delete_message(
                                chat_id=resolve_chat_id(sess),
                                message_id=pre_merge_busy_msg_id,
                            )
                            card_deleted = True
                        except Exception:
                            pass
                    else:
                        # _send_merged_final already found the card gone
                        # (RichMessageGone) and cleared busy_msg_id itself —
                        # nothing left in the chat either way.
                        card_deleted = True
                sess.busy_msg_id = None

            # ── Overflow detection ─────────────────────────────────────────
            # Skipped when the merged edit above already delivered the whole
            # turn — its own combined-text ceiling check already covers this
            # turn's answer.
            send_file = False
            body_content = content  # may be truncated below
            if not merged_delivered and content:
                content_utf8 = content.encode("utf-8")
                if len(content_utf8) > 32768:
                    if composed_with_interim:
                        # A composed message puts old interim text FIRST
                        # and the actual answer LAST — head-keeping
                        # truncation would show stale interim and hide
                        # the answer (review rev-iter1-003). Keep the
                        # TAIL instead; the .txt attachment below still
                        # carries the full chronological text.
                        # 32 768 total INCLUDING the 3-byte ellipsis.
                        body_content = ("…" + content_utf8[-(32768 - 3):]
                                        .decode("utf-8", errors="ignore"))
                        send_file = True
                        content_utf8 = b""  # handled; skip the head path
                    # Truncate at the last markdown-safe boundary under 32768.
                    bounds = _md_safe_boundaries(content) if content_utf8 else []
                    cut = 0
                    for b in bounds:
                        b_bytes = len(content[:b].encode("utf-8"))
                        if b_bytes <= 32768:
                            cut = b
                    if cut:
                        body_content = content[:cut]
                    elif content_utf8:
                        # No safe boundary found — truncate at byte limit.
                        body_content = content_utf8[:32768].decode("utf-8", errors="ignore")
                    send_file = True

            # ── Send the header (HTML, via PTB) ────────────────────────────
            # Skipped whenever the busy card has already been disposed of —
            # either kept as the finished card (`card_kept`, so the header
            # would repeat what's already sitting right above the answer:
            # ✅, label, elapsed) or deleted outright (`card_deleted` —
            # `replace`, and `merged` falling back to that same one-message
            # behaviour). Either way the body carries the reply link and the
            # tracked message_id in the header's place.
            # Two cases keep the header regardless: overflow (the "attached
            # below" note and the document's reply target both live on it)
            # and card disposal having failed (nothing else identifies the
            # turn).
            skip_header = (
                (card_kept or card_deleted) and bool(body_content) and not send_file
            )
            msg_id = 0
            if not merged_delivered and not skip_header:
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
            if not merged_delivered and body_content:
                is_rtl = detect_rtl(body_content)
                log.info("[%s] sendRichMessage: %d chars, rtl=%s, overflow=%s",
                         label, len(body_content), is_rtl, send_file)
                reply_to = sess.trigger_msg_id if skip_header else None
                chat_id = resolve_chat_id_int(sess)
                try:
                    if chat_id is None:
                        # Unscoped session, no global CHAT_ID configured —
                        # there's no numeric destination for the rich-message
                        # API call. Go straight to the plain-text fallback
                        # below (it still addresses the chat by whatever
                        # resolve_chat_id(sess) returned) instead of letting
                        # int(None-ish) raise and lose the answer outright.
                        raise RichMessageFallbackRequired(
                            "no numeric chat id resolved",
                        )
                    sent = await send_rich_message(
                        chat_id,
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
                    chunks = _plain_text_chunks(body_content)
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
                self.registry.track_message(msg_id, sess.name, resolve_chat_id_int(sess) or 0)
            await self._maybe_update_bot_name(sess.name)

            # ── Full-log .txt attachment ("layered-card-shedding") ────────
            # Sent when the FINAL card render had to hide anything (the
            # renderer reported it via sess.last_card_truncated) OR the
            # answer body was truncated by the overflow logic above. One
            # file per close, superseding the old answer-only
            # response.txt: complete chronological play-by-play plus the
            # full answer, so hidden history is always recoverable.
            # For layout=card and layout=merged this flag comes from the
            # FINAL render (stashed by _edit_busy_rich / _send_merged_final
            # respectively). For layout=replace no final card is ever
            # rendered — the busy card is deleted outright — so the flag
            # reflects the last interim tick: a deliberate proxy (review
            # rev-iter1-006), since replace leaves no finished card whose
            # hidden rows an attachment would need to compensate for
            # beyond what the interim state already showed.
            attach_log = send_file or sess.last_card_truncated
            file_content = (
                build_full_log(label, log_tools, log_commentary, content)
                if attach_log else ""
            )
            if attach_log and file_content:
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
                                resolve_chat_id(sess), document=f,
                                filename=f"{label}_full_log.txt",
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
                if attach_log and file_content:
                    doc_bytes = file_content.encode("utf-8")
                    asyncio.create_task(self.observers.broadcast_document(
                        obs_text, doc_bytes, f"{label}_response.txt"))
                else:
                    asyncio.create_task(self.observers.broadcast(obs_text))

            # Flush next queued message (one at a time, rest flush on next IDLE)
            await self._drain_next_queued(sess)

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
                            sess, options,
                            multi_select=is_multi)
                    else:
                        # AskUserQuestion detected but no questions data (transcript
                        # not flushed). Degrade to Allow/Deny — Allow sends Enter.
                        sess.pending_permission = {
                            "tool_summary": "AskUserQuestion (loading…)",
                            "tool_info": tool_info,
                            "wait_started_at": time.monotonic(),
                        }
                        keyboard = self._build_permission_keyboard(sess)
                else:
                    tool_summary = tool_info["summary"] if tool_info else "Permission needed"
                    sess.pending_permission = {
                        "tool_summary": tool_summary,
                        "tool_info": tool_info,
                        "wait_started_at": time.monotonic(),
                    }
                    keyboard = self._build_permission_keyboard(sess)

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
                    text, keyboard = self._build_ask_keyboard(sess, label, tool_info["input"])
                elif selector_options:
                    text, keyboard = self._build_selector_keyboard(sess, label,
                                                                    selector_text, selector_options)
                else:
                    tool_summary = tool_info["summary"] if tool_info else ""
                    text = f"🔐 <b>{html_mod.escape(label)}</b> · Permission needed"
                    if tool_summary:
                        text += f"\n<code>{html_mod.escape(tool_summary)}</code>"
                    # Local import, not top-level — avoids an import cycle
                    # with session_parity; mirrors keyboards.py's own
                    # local-import precedent (see
                    # keyboards.py:_build_resume_mode_keyboard).
                    from aipager.bot import session_parity

                    chat_id = resolve_chat_id_int(sess) or 0
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "✅ Allow",
                            callback_data=session_parity.session_cb(self, chat_id, sess, "allow")),
                        InlineKeyboardButton(
                            "❌ Deny",
                            callback_data=session_parity.session_cb(self, chat_id, sess, "deny")),
                    ]])

                msg = await bot.send_message(
                    resolve_chat_id(sess), text, reply_markup=keyboard, parse_mode="HTML",
                    reply_to_message_id=sess.trigger_msg_id,
                )
                self.registry.track_message(msg.message_id, sess.name, resolve_chat_id_int(sess) or 0)
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
