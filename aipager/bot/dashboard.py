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
import itertools
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.error import BadRequest, Forbidden


from aipager.config import (
    APP_BUTTON,
    CHAT_ID,
    PINNED_MIN_EDIT_GAP,
    PINNED_RECREATE_MIN_INTERVAL,
)
from aipager.bot import session_parity
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_ESSENTIAL,
    PRIORITY_ORNAMENT,
    FloodSkipped,
    is_group_chat,
    rate_limit_args as _rl_args,
)
from aipager.bot.rich_message import get_rate_limiter
from aipager.policy_snapshot import queue_depth_parts
from aipager.state import Status, TrackedSession
from aipager.transcript import last_assistant_preview as _read_preview

# Pure-function helpers and constants live in aipager.bot.transport
# now. Re-export the names this module uses internally so the
# TelegramBot class body below (and any external consumers like the
# tests) keeps working without changes.
from aipager.bot.transport import (  # noqa: F401
    MUTED,
    SKIPPED,
    _message_chat_id,
    edit_text_at,
    send_text,
    ACTION_VERBS,
    TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES,
    TELEGRAM_MAX_DOC_BYTES,
    TELEGRAM_MAX_TEXT_LEN,
    TruncationFailed,
    _build_diff_block,
    _detect_api_error,
    _DIFF_MAX_CHARS,
    _DIFF_MAX_LINES,
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

#: The callback verb of the pinned bar's "Answer <label>" button, sent as
#: ``_:sx:<idx>:pin_answer`` (session_parity's short form, ≤ 64 bytes).
PINNED_ANSWER_VERB = "pin_answer"
PINNED_SLOW_LINE = "🐢 slow mode after a Telegram warning"
PINNED_PAUSED_LINE = "⏸ card updates paused (hourly limit)"
PINNED_PAUSED_RATE_LINE = "⏸ card updates paused (rate limit)"
#: At most this many "Answer" buttons on a bar.
PINNED_MAX_ANSWER_BUTTONS = 3
#: A session's state word on the bar, and the order of the first line's
#: counts when several sessions share the chat.
_PINNED_WORDS = {Status.BUSY: "working", Status.INTERACTIVE: "needs you",
                 Status.IDLE: "idle"}
_PINNED_COUNT_ORDER = ("working", "idle", "starting")
#: The first line's glyph when no session is waiting: that of the first
#: state in _PINNED_GLYPH_ORDER any session is in.
_PINNED_GLYPHS = {"working": "⚙️", "starting": "🔄", "idle": "💤"}
_PINNED_GLYPH_ORDER = ("working", "starting", "idle")
#: The waiting prompt's summary on the first line is cut to this.
PINNED_SUMMARY_MAX = 40
#: A trailing refresh wakes this long after its deadline, so a timer that
#: fires a hair early (asyncio allows up to the clock's resolution) never
#: lands inside the gap it waited out.
_TRAILING_MARGIN = 0.05
#: How soon an edit the budget skipped is tried again. A skip made no
#: call, so it does not open a PINNED_MIN_EDIT_GAP.
_SKIP_RETRY = 5.0
#: How long a transient failure (network, timeout, 5xx) of the bar's
#: first send waits before the trailing refresh tries again.
_TRANSIENT_RETRY = 60.0

_prompt_tokens = itertools.count(1)


def _pinned_word(sess: TrackedSession) -> str:
    return _PINNED_WORDS.get(sess.status, "starting")


def _pinned_agents(sess: TrackedSession) -> str:
    """``, N agent(s) running`` while *sess* has background agents
    running (roadmap 8.41), else ``""``: the tail of the parenthesised
    state, as in ``catfish (working, 3 agents running)``. A count, never
    their names: the bar then moves only when the count does."""
    n = len(sess.bg_agents)
    if not n:
        return ""
    return f", {n} agent{'' if n == 1 else 's'} running"


def _pinned_state(sess: TrackedSession) -> str:
    """``(working)`` / ``(idle, 1 agent running)``: a session's state as
    the bar shows it. Parentheses, never a dash (operator, 2026-09-25)."""
    return f"({_pinned_word(sess)}{_pinned_agents(sess)})"


def _pinned_glyph(words) -> str:
    return next((_PINNED_GLYPHS[w] for w in _PINNED_GLYPH_ORDER if w in words),
                "💤")


def current_prompt_token(sess: TrackedSession, *, create: bool = False):
    """The identity of the prompt *sess* is waiting on right now, or
    ``None``. Stamped lazily on the prompt's own dict (``pending_permission``
    for an inline prompt, ``pending_prompt_msg`` for a separate message):
    every new prompt, and every next question of a multi-question form, is
    a NEW dict, so it can never carry an older prompt's token. Only while
    the session is INTERACTIVE."""
    if sess.status != Status.INTERACTIVE:
        return None
    holder = sess.pending_permission or sess.pending_prompt_msg
    if not holder:
        return None
    token = holder.get("prompt_token")
    if token is None and create:
        token = holder["prompt_token"] = new_prompt_token()
    return token


def new_prompt_token() -> int:
    """A fresh prompt identity. notify stamps one on a separate-message
    prompt the moment it RECORDS it — before the send, which may wait in
    the limiter while the prompt is answered and another one shown."""
    return next(_prompt_tokens)


@dataclass
class PinnedChat:
    """One chat's pinned-bar bookkeeping (8.31), on the event loop's clock.

    ``shown``: ``(text, keyboard signature)`` the bar last showed, and
    ``shown_flood`` the flood lines on it.
    ``attempt_at``: the last edit or send attempt (the gap runs from it).
    ``created_at``: when this daemon last sent and pinned a new bar here.
    ``trailing``: the pending trailing refresh, if any.
    ``disabled``: a group where the pin was refused — no bar until restart.
    ``pin_pending``: the pin was held back by a ban or the budget.
    ``hold_until``: a transient send failure's back-off, on the same clock.
    """

    shown: tuple | None = None
    shown_flood: tuple = ()
    attempt_at: float | None = None
    created_at: float | None = None
    trailing: asyncio.Task | None = None
    in_flight: bool = False
    again: bool = False
    hold_until: float = 0.0
    disabled: bool = False
    pin_pending: bool = False


def _keyboard_sig(keyboard) -> str:
    """A comparable form of an inline keyboard (buttons are objects)."""
    if keyboard is None:
        return ""
    return json.dumps(keyboard.to_dict(), sort_keys=True)





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
            await send_text(self._app.bot,
                CHAT_ID, text, parse_mode="HTML",
                reply_to_message_id=(sess.busy_msg_id
                                     if sess.busy_msg_id and sess.busy_msg_id > 0
                                     else None),
            )
        except Exception:
            log.debug("[%s] diff preview send failed", sess.label, exc_info=True)

    # ---- the pinned "needs you" bar (roadmap 8.31) -----------------------
    #
    # One pinned message per scope chat. Telegram's bar at the top of the
    # chat shows the message from its first line on, lines run together, so
    # that line says what most needs the user; below it come any flood
    # lines and, with several sessions, one line per session not already
    # named above (see `_render_pinned`). It is rendered in
    # full from state every time and edited only when the rendering
    # changed, at most once per PINNED_MIN_EDIT_GAP per chat. Nothing on it
    # moves without a state change: no clock, cost, context % or model,
    # which is what made 8.30's hook-driven dashboard 75 % of all calls.
    #
    # What drives it: `refresh_pinned`, called at state transitions
    # (`_maybe_update_bot_name`'s call sites: a prompt shown or answered, a
    # turn's end, a session launched, stopped or killed) and on the session
    # monitor's 2 s tick, which is what notices every other transition —
    # a status changed by a hook, a session gone, a flood regime entered or
    # left — without a call site of its own. Either way a refresh that
    # renders what is already shown costs nothing.

    def _pinned_chats(self) -> list[int]:
        """Every chat that may carry a bar: each scope's chat on a v2
        install, the one CHAT_ID on a legacy personal install. Legacy team
        mode keeps its opt-out (a group dashboard nobody asked for)."""
        if not self._app:
            return []
        if self.scopes is not None:
            return [s.chat_id for s in self.scopes]
        if self.team is not None:
            return []
        try:
            return [int(CHAT_ID)]
        except (TypeError, ValueError):
            return []

    @staticmethod
    def _pinned_chat_of(sess: TrackedSession) -> int | None:
        """The chat a session's notifications go to — `resolve_chat_id`'s
        rule, without its warning for an unresolvable id (asked every tick)."""
        if sess.scope_chat_id:
            return sess.scope_chat_id
        try:
            return int(CHAT_ID)
        except (TypeError, ValueError):
            return None

    def _pinned_sessions(self, chat: int) -> list[TrackedSession]:
        """The live sessions whose scope resolves to *chat*, by label."""
        return sorted(
            (s for s in self.registry.all_sessions().values()
             if s.status != Status.GONE and s.label
             and self._pinned_chat_of(s) == chat),
            key=lambda s: (s.label, s.name),
        )

    @staticmethod
    def _pinned_summary(sess: TrackedSession) -> str:
        """A one-line, short summary of what *sess* is waiting on."""
        _kind, summary = sess.waiting_on_human()
        if not summary and sess.pending_prompt_msg:
            summary = sess.pending_prompt_msg.get("summary") or ""
        summary = " ".join(str(summary or "").split())
        if len(summary) > PINNED_SUMMARY_MAX:
            summary = summary[:PINNED_SUMMARY_MAX - 1].rstrip() + "…"
        return summary

    @staticmethod
    def _pinned_flood_lines(chat: int) -> list[str]:
        """The bar's flood lines for *chat*, only while they apply: the
        8.30 warning regime, and hourly-minimal mode."""
        limiter = get_rate_limiter()
        if limiter is None:
            return []
        lines = []
        if limiter.warning_remaining(chat) > 0:
            lines.append(PINNED_SLOW_LINE)
        if limiter.hourly_usage(chat).get("minimal"):
            lines.append(PINNED_PAUSED_LINE)
        elif limiter.minimal_mode(chat):
            # Minimal mode's other way in: the earned rate under the floor
            # (after a ban, or a run of 429s). Same effect on the chat, so
            # the bar says so too — and its regime-edge edit is what gets
            # through while every ornament is refused.
            lines.append(PINNED_PAUSED_RATE_LINE)
        return lines

    def _render_pinned(
        self, chat: int, sessions: list[TrackedSession] | None = None,
    ) -> tuple[str, InlineKeyboardMarkup | None]:
        """The bar for *chat*: ``(html text, keyboard or None)``. Pure: a
        function of session state, the chat's flood regime and the Mini App
        URL, and nothing that moves by itself."""
        if sessions is None:
            sessions = self._pinned_sessions(chat)
        esc = html_mod.escape
        waiting = [s for s in sessions if s.status == Status.INTERACTIVE]
        # Each fact once. Telegram's pinned preview runs the lines together,
        # so a first line that names sessions above a list that names them
        # again read "⚙️ 1 working — x • x — working" (2026-09-24). The
        # first line therefore never lists names a session line repeats: it
        # says the one thing that most needs the user (a waiting session,
        # named, whose own line is then left out), else the counts; a lone
        # session is the whole bar, flood lines aside.
        named = None
        if waiting:
            named = waiting[0]
            first = f"⏳ {esc(named.label)} needs you"
            agents = _pinned_agents(named)
            if agents:
                first += f" ({agents[2:]})"
            summary = self._pinned_summary(named)
            if summary:
                first += f" - {esc(summary)}"
            if len(waiting) > 1:
                first += f" (+{len(waiting) - 1} more)"
        elif len(sessions) == 1:
            only = sessions[0]
            word = _pinned_word(only)
            first = (f"{_pinned_glyph((word,))} {esc(only.label)} "
                     f"{_pinned_state(only)}")
            named = only
        elif sessions:
            counts: dict[str, int] = {}
            for s in sessions:
                word = _pinned_word(s)
                counts[word] = counts.get(word, 0) + 1
            first = f"{_pinned_glyph(counts)} " + " · ".join(
                f"{counts[w]} {w}" for w in _PINNED_COUNT_ORDER if w in counts)
        else:
            first = "💤 all idle"
        lines = [first] + self._pinned_flood_lines(chat)
        for s in sessions:
            if s is not named:
                lines.append(f"• <b>{esc(s.label)}</b> {_pinned_state(s)}")

        rows: list[list[InlineKeyboardButton]] = []
        for s in waiting[:PINNED_MAX_ANSWER_BUTTONS]:
            rows.append([InlineKeyboardButton(
                f"Answer {s.label}",
                # A literal verb (== PINNED_ANSWER_VERB): the 64-byte
                # guard in test_callback_data_budget.py proves literals.
                callback_data=session_parity.session_cb(
                    self, chat, s, "pin_answer"),
            )])
        # The Mini App URL goes only where the menu button may go: an
        # explicit list of private chats (a web_app button in a group gets
        # the WHOLE keyboard rejected, and the URL is a credential).
        url = getattr(self, "_miniapp_url", "")
        if url and chat in self._miniapp_button_chats():
            rows.append([InlineKeyboardButton(
                APP_BUTTON, web_app=WebAppInfo(url=url))])
        return "\n".join(lines), (InlineKeyboardMarkup(rows) if rows else None)

    async def _maybe_update_bot_name(self, session_name: str) -> None:
        """A state transition happened: refresh the pinned bars.

        The name is historical (and kept for its many call sites and test
        doubles). *session_name* is no longer used: the bar has no header
        session, and every chat is re-rendered from state. Returns at once:
        the refresh runs as its own task (see :meth:`pinned_tick`), so a
        turn's answer path never waits behind an ornament.
        """
        await self.pinned_tick()

    async def pinned_tick(self) -> None:
        """Start a refresh of every bar as a task, unless one is running
        (it renders at each chat's turn, so it sees this change too, or
        leaves it to its trailing refresh). The session monitor calls this
        every scan; a blocking first send + pin must never hold up the
        scan's own work (held answers, GONE detection)."""
        task = self._pinned_task
        if task is not None and not task.done():
            return
        self._pinned_task = asyncio.get_running_loop().create_task(
            self.refresh_pinned())

    async def refresh_pinned(self) -> None:
        """Bring every chat's bar up to date with the current state — or
        schedule it, when the chat's gap has not passed. Never raises."""
        if not self._app:
            return
        chats = self._pinned_chats()
        # A chat removed from the scopes keeps no bar id: nothing edits
        # that message any more, and forgetting it costs no call. (It stays
        # pinned there, frozen, until someone unpins it; unpinning would be
        # a call into a chat the operator just dropped.)
        stale = [c for c in self.registry.pinned_msg_ids if c not in chats]
        for chat in stale:
            self.registry.pinned_msg_ids.pop(chat, None)
            st = self._pinned.pop(chat, None)
            if st is not None and st.trailing is not None:
                st.trailing.cancel()
            log.info("Forgot the pinned status bar of chat %s — no longer "
                     "a scope", chat)
        if stale:
            self.registry.mark_dirty()
        for chat in chats:
            try:
                await self._refresh_pinned_chat(chat)
            except Exception:
                log.debug("pinned bar refresh failed for %s", chat,
                          exc_info=True)

    async def _refresh_pinned_chat(self, chat: int) -> None:
        if chat not in self._pinned_chats():
            return  # e.g. a trailing refresh for a chat no longer a scope
        st = self._pinned.setdefault(chat, PinnedChat())
        if st.disabled:
            return
        if st.in_flight:
            # One refresh per chat at a time. The one in flight rendered
            # before this change; run once more when it is done (it will
            # usually land in the gap and become the trailing edit).
            st.again = True
            return
        st.in_flight = True
        try:
            await self._refresh_pinned_once(chat, st)
        finally:
            st.in_flight = False
        if st.again:
            st.again = False
            await self._refresh_pinned_chat(chat)

    async def _refresh_pinned_once(self, chat: int, st: "PinnedChat") -> None:
        msg_id = self.registry.pinned_msg_ids.get(chat)
        sessions = self._pinned_sessions(chat)
        if not msg_id and not sessions and st.shown is None:
            return  # nothing to show, and never shown: no bar yet
        text, keyboard = self._render_pinned(chat, sessions)
        sig = (text, _keyboard_sig(keyboard))
        if msg_id and sig == st.shown:
            return  # what the bar already shows: no call
        now = asyncio.get_running_loop().time()
        # MUTE: never a call into a ban. Wake up exactly when it lifts and
        # catch up then, with the state as it is at that moment.
        if MUTE.is_muted(chat):
            self._schedule_pinned_trailing(chat, MUTE.remaining(chat))
            return
        # A transient failure's back-off (see `_pinned_create`).
        if now < st.hold_until:
            self._schedule_pinned_trailing(chat, st.hold_until - now)
            return
        # THE GAP: at most one attempt per PINNED_MIN_EDIT_GAP per chat.
        # A change inside it is coalesced into one trailing refresh at its
        # end, which re-renders from state, so it is never lost.
        if (st.attempt_at is not None
                and now - st.attempt_at < PINNED_MIN_EDIT_GAP):
            self._schedule_pinned_trailing(
                chat, st.attempt_at + PINNED_MIN_EDIT_GAP - now)
            return
        if not msg_id:
            await self._pinned_create(chat, st, text, keyboard, sig, now)
            return
        if st.pin_pending:
            await self._pinned_pin(chat, st, msg_id)
            if st.disabled:
                return
        # ORNAMENT, skip class (8.26 R3, 8.30): the bar is a summary that
        # is always re-derivable, so it counts in the hourly budget as an
        # ornament and is dropped when the budget is tight. A drop is
        # rescheduled below, never lost.
        #
        # EXCEPT the edit that puts a flood line on the bar or takes one
        # off (a chat entering or leaving slow or minimal mode): that one
        # is ESSENTIAL, like the busy card's single "updates paused" line
        # and its single resume edit (8.29 T3). As an ornament it would be
        # refused by the very minimal mode it announces, and the "⏸" line
        # could never be shown, or never taken down. Still gapped and
        # never into a mute; at most one per regime edge.
        flood = tuple(self._pinned_flood_lines(chat))
        regime_edge = st.shown is not None and flood != st.shown_flood
        rl = (_rl_args(priority=PRIORITY_ESSENTIAL) if regime_edge
              else _rl_args(kind="skip", priority=PRIORITY_ORNAMENT))
        previous_attempt, st.attempt_at = st.attempt_at, now
        try:
            edited = await edit_text_at(self._app.bot,
                text, chat, msg_id,
                parse_mode="HTML",
                reply_markup=keyboard,
                rate_limit_args=rl,
            )
        except Forbidden as e:
            # Kicked from the group, or blocked in the DM: every later call
            # would be refused the same way. No bar here until restart.
            self._pinned_disable(chat, st, f"the bot can't post there ({e})")
            return
        except BadRequest as e:
            low = str(e).lower()
            if "not modified" in low:
                st.shown, st.shown_flood = sig, flood
                return
            if "chat not found" in low:
                self._pinned_disable(chat, st, f"the chat is gone ({e})")
                return
            if "message to edit not found" in low or low.endswith(
                    "message not found"):
                # The user deleted the pinned message. Forget it and pin a
                # new one: at most once per PINNED_RECREATE_MIN_INTERVAL.
                log.info("Pinned status bar in chat %s is gone (%s) — "
                         "will pin a new one", chat, e)
                self.registry.pinned_msg_ids.pop(chat, None)
                self.registry.mark_dirty()
                st.attempt_at = None
                await self._refresh_pinned_once(chat, st)
                return
            log.debug("Pinned status bar edit refused in %s: %s", chat, e)
            return
        if edited is SKIPPED:
            # The budget was short for a skip-class call: nothing went out,
            # so it does not open a gap. The trailing refresh retries soon
            # (and the tick may get there first); never dropped.
            st.attempt_at = previous_attempt
            self._schedule_pinned_trailing(chat, _SKIP_RETRY)
            return
        if edited is MUTED:
            # A ban armed under the call: the next refresh sees the mute
            # and waits for the lift.
            self._schedule_pinned_trailing(chat, PINNED_MIN_EDIT_GAP)
            return
        st.shown, st.shown_flood = sig, flood

    async def _pinned_create(self, chat: int, st: "PinnedChat", text: str,
                             keyboard, sig: tuple, now: float) -> None:
        """Send and pin a new bar in *chat* (R5): once per chat, and again
        only when the user deleted it — then at most once an hour."""
        if (st.created_at is not None
                and now - st.created_at < PINNED_RECREATE_MIN_INTERVAL):
            self._schedule_pinned_trailing(
                chat, st.created_at + PINNED_RECREATE_MIN_INTERVAL - now)
            return
        st.attempt_at = now
        try:
            msg = await send_text(self._app.bot,
                chat, text, parse_mode="HTML",
                reply_markup=keyboard,
                # Silent: a status message is not news. (The pin is silent
                # too; this covers the send itself, and a group's message
                # that is deleted again when the pin is refused.)
                disable_notification=True,
                # ORNAMENT, blocking (8.26 R3): a summary, but the FIRST
                # send is what creates the message every later refresh
                # edits.
                rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
            )
        except Forbidden as e:
            self._pinned_disable(chat, st, f"the bot can't post there ({e})")
            return
        except BadRequest as e:
            if "chat not found" in str(e).lower():
                self._pinned_disable(chat, st, f"the chat is gone ({e})")
                return
            # Telegram refused THIS message (not the network): sending the
            # same thing again in a minute would be refused the same way.
            # Back off for the recreate interval.
            st.created_at = now
            log.info("Couldn't send the status bar to chat %s: %s", chat, e)
            return
        except Exception as e:
            # Transient — a network error, a timeout, a 5xx: the next try
            # may well work. A short back-off on the trailing refresh, not
            # the hour (which would leave the chat with no bar for an hour
            # after one blip).
            st.hold_until = now + _TRANSIENT_RETRY
            self._schedule_pinned_trailing(chat, _TRANSIENT_RETRY)
            log.info("Couldn't send the status bar to chat %s (%s) — "
                     "retrying in %.0fs", chat, e, _TRANSIENT_RETRY)
            return
        if msg is MUTED or msg is SKIPPED:
            self._schedule_pinned_trailing(chat, PINNED_MIN_EDIT_GAP)
            return
        # Stamped when the send came BACK: a blocking ornament may have
        # waited in the limiter, and both clocks run from what went out.
        st.created_at = st.attempt_at = asyncio.get_running_loop().time()
        st.shown = sig
        st.shown_flood = tuple(self._pinned_flood_lines(chat))
        self.registry.pinned_msg_ids[chat] = msg.message_id
        self.registry.mark_dirty()
        await self._pinned_pin(chat, st, msg.message_id)

    async def _pinned_pin(self, chat: int, st: "PinnedChat",
                          msg_id: int) -> None:
        """Pin *msg_id* silently. In a GROUP a refused pin (the bot is not
        an admin) deletes the message and turns the bar off for this chat
        for the daemon's lifetime: an unpinned status message would sit in
        the group's scroll-back and be edited there forever. In a DM the
        message stays and is edited in place."""
        st.pin_pending = False
        try:
            await self._app.bot.pin_chat_message(
                chat, msg_id,
                disable_notification=True,
                rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
            )
        except (FloodMuted, FloodSkipped):
            # Not a refusal: the budget or a ban. Pin it on the next
            # refresh that gets through.
            st.pin_pending = True
        except Exception as e:
            if not is_group_chat(chat):
                log.info("Couldn't pin the status bar in chat %s: %s — "
                         "will edit it in place.", chat, e)
                return
            self._pinned_disable(
                chat, st, f"the bot needs admin rights to pin ({e}); "
                "the unpinned message is deleted")
            try:
                await self._app.bot.delete_message(
                    chat_id=chat, message_id=msg_id,
                    rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
                )
            except Exception:
                log.warning("Couldn't delete the unpinned status bar in "
                            "group %s", chat, exc_info=True)

    def _pinned_disable(self, chat: int, st: "PinnedChat", why: str) -> None:
        """No bar in *chat* for the rest of this daemon's life: every call
        to it would be refused the same way. Forgets its message id."""
        log.info("No pinned status bar in chat %s until the daemon "
                 "restarts: %s", chat, why)
        st.disabled = True
        if st.trailing is not None and not st.trailing.done():
            st.trailing.cancel()
        if self.registry.pinned_msg_ids.pop(chat, None) is not None:
            self.registry.mark_dirty()

    def _schedule_pinned_trailing(self, chat: int, delay: float) -> None:
        """One trailing refresh per chat, *delay* seconds from now. One
        already pending stays: when it fires it re-renders and, if it is
        still too early, schedules the next."""
        st = self._pinned.setdefault(chat, PinnedChat())
        if st.trailing is not None and not st.trailing.done():
            return
        st.trailing = asyncio.get_running_loop().create_task(
            self._pinned_trailing(chat, max(delay, 0.0) + _TRAILING_MARGIN))

    async def _pinned_trailing(self, chat: int, delay: float) -> None:
        await asyncio.sleep(delay)
        st = self._pinned.get(chat)
        if st is not None:
            st.trailing = None
        await self._refresh_pinned_chat(chat)

    async def _pinned_answer(self, query, sess: TrackedSession) -> None:
        """The bar's "Answer <label>" button: re-send *sess*'s pending
        prompt, with its answer keyboard, as a fresh message at the bottom
        of the chat the tap came from. "already answered" when it is not
        waiting any more.

        The copy is registered as a prompt surface bound to THIS prompt
        (:meth:`register_prompt_surface`), so a tap on it after the prompt
        was answered — even with the session already waiting on the next
        one — is refused instead of answering a prompt it never showed."""
        message = getattr(query, "message", None)
        chat = _message_chat_id(message)
        if message is None or chat is None:
            await self._safe_answer(query, "Open the chat to answer")
            return
        if sess.status != Status.INTERACTIVE:
            await self._safe_answer(query, "already answered")
            return
        markup = self._pending_prompt_markup(sess)
        if markup is None:
            await self._safe_answer(
                query, "The prompt can't be re-sent — answer it in the terminal")
            return
        token = current_prompt_token(sess, create=True)
        text, keyboard = markup
        msg = await send_text(self._app.bot,
            chat, text, parse_mode="HTML", reply_markup=keyboard,
        )
        if msg is SKIPPED:
            await self._safe_answer(query, "Busy — try again in a moment")
            return
        if msg is MUTED or msg is None:
            return
        msg_id = getattr(msg, "message_id", None)
        if not isinstance(msg_id, int):
            return
        self.registry.track_message(msg_id, sess.name, chat)
        self.register_prompt_surface(chat, msg_id, sess, token)

    def register_prompt_surface(self, chat, msg_id: int,
                                sess: TrackedSession, token: int) -> None:
        """Remember that message *msg_id* in *chat* carries answer buttons
        for *sess*'s prompt *token*: a separate-message prompt, or a copy
        the bar re-sent. `_handle_callback` refuses an answer tapped on it
        once that prompt is no longer the one pending.

        Unbounded on purpose — an entry is added per prompt that could not
        go inline and per "Answer" tap, both human-paced, about 100 bytes
        each, and a restart clears it. Evicting an entry would take the
        guard off a message that still has live-looking buttons."""
        self._resent_prompts[(chat or 0, msg_id)] = (sess.name, token)

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

        # Live subagents — only if any are running right now. This is the
        # current population (capped), not the busy message's per-turn
        # cumulative count; a dashboard answers "what is happening now".
        if sess.active_subagents:
            # `type` arrives from the hook payload unvalidated: coerce to
            # str so an odd payload (None, int) cannot break the sort or
            # the dict key — one bad session must not fail the whole
            # render — and escape it like every other hook-derived string
            # in this function (label, model, tool summaries), because the
            # dashboard is sent with parse_mode="HTML".
            types: dict[str, int] = {}
            for info in sess.active_subagents.values():
                t = str(info.get("type") or "unknown")
                types[t] = types.get(t, 0) + 1
            if len(types) <= 3:
                breakdown = ", ".join(
                    f"{html_mod.escape(t)} ×{n}" if n > 1
                    else html_mod.escape(t)
                    for t, n in sorted(types.items()))
                rows.append(
                    f"  Agents  {len(sess.active_subagents)} ({breakdown})")
            else:
                rows.append(f"  Agents  {len(sess.active_subagents)}")

        # Queue depth — only if non-empty. Combined: held messages plus
        # ones already sent to Claude and awaiting pick-up. Same seam the
        # Mini App and /clearqueue use, so the surfaces cannot disagree.
        queued, notes = queue_depth_parts(sess)
        depth = queued + notes
        if depth:
            # Broken out so a pile of stale/orphaned notes never reads
            # as "N real pending messages" — see handlers.py's /status
            # overview for the same breakdown.
            rows.append(
                f"  Queue   {depth} pending ({queued} queued, {notes} notes)")

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
        all_gone = self._gone_sessions_sorted(scope_chat_id)
        # A session with no `claude_session_id` has nothing to resume FROM —
        # `_do_resume_core` refuses it with `no_transcript` — so listing it
        # promises something that cannot happen. Hidden rather than shown
        # greyed out, because Telegram has no disabled button; the count
        # below keeps the omission honest.
        gone = [s for s in all_gone if s.claude_session_id]
        hidden = len(all_gone) - len(gone)
        if not gone:
            text = "📭 No previous sessions to resume."
            if hidden:
                text += (f"\n\n<i>{hidden} with no saved transcript to "
                         "resume from.</i>")
            return text, None

        page_size = self._RESUME_PAGE_SIZE
        total_pages = (len(gone) + page_size - 1) // page_size
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        chunk = gone[start:start + page_size]

        rows: list[list[InlineKeyboardButton]] = []
        for s in chunk:
            label = f"{s.label} — {self._fmt_gone_ago(s.gone_at)}"
            rows.append([InlineKeyboardButton(
                label, callback_data=session_parity.session_cb(
                    self, scope_chat_id or 0, s, "resume"),
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
        if hidden:
            # The empty-list branch above says this too. It has to be said
            # HERE as well — the common case is a mix of resumable and
            # not, and dropping a session from the list without a word is
            # its own small mystery.
            lines.append(
                f"<i>{hidden} more with no saved transcript to "
                "resume from.</i>")
        text = "\n\n".join(lines)
        return text, InlineKeyboardMarkup(rows)
