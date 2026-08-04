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
import os
import random
import re
import time
from typing import TYPE_CHECKING

from aipager.config import (
    BUSY_EDIT_INTERVAL, CHAT_ID, SPINNER_VERBS,
    STREAM_BODY_CHARS, STREAM_EDIT_INTERVAL, STREAM_MAX_REVEAL_STEPS,
    STREAM_REVEAL_CHARS,
)
from aipager.bot.rich_message import (
    detect_rtl,
    edit_message_text_rich,
    RichMessageBlocked,
    RichMessageGone,
)
from aipager.transcript import read_turn_text
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
    resolve_chat_id,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_RICH_LIMIT = 32_768  # UTF-8 byte ceiling for rich messages
_DIVIDER = "────────────────"


# ── Module-level pure helpers ────────────────────────────────────────────────

def _read_stream_text(sess: TrackedSession) -> bool:
    """Read new assistant text from the transcript into ``sess.stream_pending``.

    Returns True when new text arrived, False otherwise.

    # Thinking blocks stay out of the card: read_turn_text collects only
    # type="text" content, and thinking is verbose internal reasoning that
    # was never written for a reader. Only prose commentary streams.
    """
    if not sess.stream_transcript_path:
        return False
    new_text, sess.stream_offset = read_turn_text(
        sess.stream_transcript_path, sess.stream_offset,
    )
    if not new_text:
        return False
    # Separate blocks with a blank line against what is buffered OR already on
    # screen: once pending drains into shown, a bare append glues the next
    # block onto the tail of the previous sentence.
    if sess.stream_pending or sess.stream_shown:
        sess.stream_pending = sess.stream_pending + "\n\n" + new_text
    else:
        sess.stream_pending = new_text
    return True


def _reveal_chunk(sess: TrackedSession) -> bool:
    """Move up to ``STREAM_REVEAL_CHARS`` characters from pending to shown.

    Breaks at the last whitespace within the window so words are never split.
    Returns True when something was revealed, False when the buffer is empty.
    """
    if not sess.stream_pending:
        return False
    # Small steps read as typing; scale up when a big blob is queued so the
    # card never lags a minute behind the turn it is narrating.
    step = max(
        STREAM_REVEAL_CHARS,
        -(-len(sess.stream_pending) // STREAM_MAX_REVEAL_STEPS),
    )
    window = sess.stream_pending[:step]
    if len(sess.stream_pending) > step:
        # Cut at last whitespace so words never split mid-token.
        cut = window.rfind(" ")
        if cut == -1:
            cut = step
        else:
            cut += 1  # include the space in the revealed portion
        chunk = sess.stream_pending[:cut]
        sess.stream_pending = sess.stream_pending[cut:]
    else:
        chunk = sess.stream_pending
        sess.stream_pending = ""
    if sess.stream_shown:
        sess.stream_shown = sess.stream_shown + chunk
    else:
        sess.stream_shown = chunk
    return True


def build_stream_card(sess: TrackedSession, verb: str) -> str:
    """Build the streaming busy-card markdown. Pure: no I/O, no mutation.

    Returns a string that is always ≤ 32 768 UTF-8 bytes.
    """
    # Escape markdown metacharacters in the label so they don't break formatting.
    safe_label = re.sub(r"([*_`\[\]])", r"\\\1", sess.label)
    header = f"🔧 **{safe_label}** · {verb}"

    # ── Footer segments ──
    footer_parts: list[str] = []
    if sess.busy_started_at:
        elapsed_s = int(time.monotonic() - sess.busy_started_at)
        if elapsed_s >= 60:
            footer_parts.append(f"{elapsed_s // 60}m {elapsed_s % 60}s")
        elif elapsed_s >= 2:
            footer_parts.append(f"{elapsed_s}s")

    if (sess.cost_baseline is not None
            and sess.last_cost_usd > 0):
        delta = sess.last_cost_usd - sess.cost_baseline
        if delta > 0.001:
            footer_parts.append(f"${delta:.2f}")

    if sess.tool_history:
        tally: dict[str, int] = {}
        for summary, _done in sess.tool_history:
            # Summaries are "Read: /path", "Grep: pat in dir" or "🤖 agent-type".
            # Everything before the colon is the tool name; without one, the
            # first word stands in.
            head = summary.split(":", 1)[0] if ":" in summary else summary
            parts = head.split()
            name = parts[0][:20] if parts else ""
            if not name:
                continue
            tally[name] = tally.get(name, 0) + 1
        footer_parts.extend(f"{n} ×{c}" for n, c in tally.items())

    footer = "⏳ " + " · ".join(footer_parts) if footer_parts else "⏳"

    body = sess.stream_shown
    if len(body) > STREAM_BODY_CHARS:
        # Keep the card glanceable: show only the most recent commentary,
        # starting at a paragraph break where one is close to the cut.
        tail = body[-STREAM_BODY_CHARS:]
        para = tail.find("\n\n")
        if para != -1 and para < STREAM_BODY_CHARS // 2:
            tail = tail[para + 2:]
        else:
            space = tail.find(" ")
            if space != -1:
                tail = tail[space + 1:]
        body = "… " + tail

    # ── Assemble the card ──
    # The divider and footer need a blank line between them: Telegram's rich
    # markdown collapses a single newline into a space, which would put the
    # footer on the divider's line. With no body there is nothing to divide.
    if body:
        raw = f"{header}\n\n{body}\n\n{_DIVIDER}\n\n{footer}"
    else:
        raw = f"{header}\n\n{footer}"

    # ── Truncation: drop the head of the body if over the byte ceiling ──
    encoded = raw.encode("utf-8")
    if len(encoded) <= _RICH_LIMIT:
        return raw

    if body:
        # Compute how many bytes we can spare for the body.
        skeleton = f"{header}\n\n…\n\n{_DIVIDER}\n\n{footer}"
        skeleton_bytes = len(skeleton.encode("utf-8"))
        body_budget = _RICH_LIMIT - skeleton_bytes
        if body_budget > 0:
            body_bytes = body.encode("utf-8")
            if len(body_bytes) > body_budget:
                # Drop from the head (oldest text).
                truncated = body_bytes[-body_budget:].decode("utf-8", errors="ignore")
                body = "…" + truncated
            raw = f"{header}\n\n{body}\n\n{_DIVIDER}\n\n{footer}"
        else:
            # Skeleton alone exceeds the limit — drop the body entirely.
            raw = f"{header}\n\n{footer}"
    return raw



class AnimationMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

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

    # ── Notification methods (called by hook_receiver and session_monitor) ──

    async def send_busy(self, sess: TrackedSession) -> int | None:
        """Send initial 'Working...' message and start animation. Returns message_id."""
        if not self._app:
            return None
        text = f"⚙️ <b>{html_mod.escape(sess.label)}</b> · Thinking…"
        try:
            msg = await self._app.bot.send_message(
                resolve_chat_id(sess), text, parse_mode="HTML",
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

    async def _edit_busy_raw(self, msg_id: int, text: str,
                             reply_markup=None, chat_id=None) -> bool | None:
        """Edit busy message with pre-built text.

        ``chat_id`` is the chat the busy message lives in; defaults to
        the global ``CHAT_ID`` for callers that don't route per scope.
        Sess-aware callers in the notify path pass
        ``chat_id=resolve_chat_id(sess)``.

        Returns True on success, False on transient error,
        None on permanent failure (message gone).
        """
        if not self._app:
            return False
        try:
            await self._app.bot.edit_message_text(
                text, chat_id=chat_id or CHAT_ID, message_id=msg_id,
                parse_mode="HTML", reply_markup=reply_markup,
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

    async def _edit_busy_rich(
        self, sess: TrackedSession, verb: str,
    ) -> bool | None:
        """Edit the busy message with the streaming card.

        Returns
        -------
        True   — success; ``last_tool_edit_at`` and ``stream_last_rendered``
                 updated, ``stream_dirty`` cleared.
        False  — transient failure; caller should retry on the next tick.
        None   — permanent failure (blocked or message gone); caller must stop
                 animating.
        """
        if not sess.busy_msg_id or sess.busy_msg_id < 0:
            return False
        # Serialise edits per session. The POST below is a suspension point, so
        # without this a hook-driven edit can start while the animation loop's
        # edit is still in flight; Telegram then rejects the first one with
        # 400 "canceled by new edit message request". The waiter re-renders
        # inside the lock, so a burst collapses into the dedupe below instead
        # of racing.
        lock = getattr(sess, "_stream_edit_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            sess._stream_edit_lock = lock

        async with lock:
            markdown = build_stream_card(sess, verb)
            # Dedupe: skip the POST when nothing changed since the last render.
            # Primary guard against the "message is not modified" 400.
            if markdown == sess.stream_last_rendered:
                return True
            is_rtl = detect_rtl(sess.stream_shown or "")
            reply_markup = self._build_stop_keyboard(sess.name).to_dict()
            try:
                result = await edit_message_text_rich(
                    int(resolve_chat_id(sess)),
                    int(sess.busy_msg_id),
                    markdown,
                    is_rtl=is_rtl,
                    reply_markup=reply_markup,
                )
            except RichMessageBlocked:
                log.warning("[%s] editMessageText blocked — stopping animation", sess.label)
                return None
            except RichMessageGone:
                log.debug("[%s] editMessageText: message gone — clearing busy_msg_id",
                          sess.label)
                sess.busy_msg_id = 0
                return None
            if result is None:
                # Transient failure (timeout, network, 429 exhausted, etc.)
                return False
            # Success
            sess.last_tool_edit_at = time.monotonic()
            sess.stream_last_rendered = markdown
            sess.stream_dirty = False
            return True

    async def _animate_busy(self, sess: TrackedSession) -> None:
        """Background task: stream transcript text while session is BUSY."""
        verbs = list(SPINNER_VERBS)
        random.shuffle(verbs)
        idx = 0
        first_tick = True
        try:
            while sess.busy_msg_id and sess.status == Status.BUSY:
                # First tick at 1.5 s for a quick initial render, then stream cadence.
                await asyncio.sleep(1.5 if first_tick else STREAM_EDIT_INTERVAL)
                first_tick = False
                if not sess.busy_msg_id or sess.status != Status.BUSY:
                    break
                # Read transcript on every tick regardless of debounce.
                _read_stream_text(sess)
                # Choose the required minimum gap.
                gap = (STREAM_EDIT_INTERVAL if (sess.stream_pending or sess.stream_dirty)
                       else BUSY_EDIT_INTERVAL)
                now = time.monotonic()
                if now - sess.last_tool_edit_at < gap:
                    # Debounced — still send typing indicator.
                    try:
                        await self._app.bot.send_chat_action(
                            int(resolve_chat_id(sess)), "typing",
                        )
                    except Exception:
                        pass
                    continue
                _reveal_chunk(sess)
                verb = verbs[idx % len(verbs)]
                idx += 1
                result = await self._edit_busy_rich(sess, verb)
                if result is None:
                    break  # permanent failure
                # Send typing AFTER edit (edit cancels the typing indicator).
                try:
                    await self._app.bot.send_chat_action(
                        int(resolve_chat_id(sess)), "typing",
                    )
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
                result = await self._edit_busy_raw(sess.busy_msg_id, text, chat_id=resolve_chat_id(sess))
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
            # Seed streaming state for this turn.  stream_offset is set to
            # the current transcript size so the previous turn's text is
            # never re-streamed as this turn's (analogous to the
            # false-idle-recovery bug fixed in 0.4.26).
            #
            # Only sess.transcript_path is trusted — it is stamped per-session
            # from the hook payload. There is deliberately no fallback: an
            # unstamped session streams nothing rather than risk streaming
            # another session's transcript into this one.
            # The path is pinned on sess.stream_transcript_path for the whole
            # turn so _read_stream_text never resolves a different file mid-turn.
            sess.stream_pending = ""
            sess.stream_shown = ""
            sess.stream_dirty = False
            sess.stream_last_rendered = ""
            sess.stream_offset = 0
            sess.stream_transcript_path = ""
            tp = sess.transcript_path
            if tp:
                sess.stream_transcript_path = tp
                try:
                    sess.stream_offset = os.path.getsize(tp)
                except OSError:
                    sess.stream_offset = 0
            msg_id = await self.send_busy(sess)
            if msg_id:
                # Send typing AFTER the busy message (sending a message cancels typing)
                try:
                    await self._app.bot.send_chat_action(int(resolve_chat_id(sess)), "typing")
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
