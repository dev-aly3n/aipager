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
import time
from typing import TYPE_CHECKING


import random

from aipager.config import (
    BUSY_EDIT_INTERVAL, CHAT_ID, SPINNER_VERBS,
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
