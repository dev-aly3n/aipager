""""⚡ Send now" for a Telegram message still waiting in Claude Code's queue.

A message sent while a turn runs is queued by Claude Code (a *queued
target*, ``TrackedSession.queued_targets``) until the running step ends.
If Claude's transcript still shows it held once Claude's current step has
run ``QUEUED_LINE_TOOL_AGE`` seconds, or ``QUEUED_LINE_DELAY`` seconds after
its line was armed (its pick-up, or the moment the line moved to it),
whichever comes first, aipager replies under it with one line and one
button. A session has ONE line at a time, under the oldest message
still waiting: the button sends them all, and a line per message could not
keep up with the chat's flood ceiling (live test 2026-09-29).
The button, or ``/now``, presses Claude Code's own send-now keys
(``inject.send_now``: Ctrl+X, Ctrl+S), which sends every queued message at
once: a running command or agent moves to the background, a reply being
written is cut short and restarted with the message.

The line's whole lifecycle lives here. A line exists only for a msg_id in
``queued_targets``: :meth:`SendNowMixin._sync_queued_lines` drops the line
(or its pending timer) of every msg_id that has left that list, whichever
code removed it, and moves the line on to the next waiting message, so each
place a target leaves the queue only has to call it. Nothing guesses a
message's fate. Every line sent is owed a delete on
the registry (``SessionRegistry.queued_line_deletes``) until the delete
lands, and startup deletes what a restart left behind.

The line is never sent while the chat is muted or in minimal mode
(``/now`` is the fallback then). It WAITS for its token rather than being
skipped whenever a busy card leaves the chat short, at answer priority, as
the operator's only way to send the queue now. The delete goes at answer
priority too, since a button still showing once its message has gone would
be wrong, and a refused delete stays owed. A Send now (tap or ``/now``)
drops the line and ends the lines for the queue it sent; a line send still
waiting for its token then never stays up.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager import config, session_monitor
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_ESSENTIAL,
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args as _rl_args,
)
from aipager.bot.notify import _BACKGROUND_TASKS
from aipager.bot.session_ops import held_by_claude
from aipager.bot.transport import (
    calling_chat_id,
    edit_text_at,
    MUTED,
    reply_text,
    resolve_chat_id_int,
    send_text,
)
from aipager.dtach import inject
from aipager.state import Status, TrackedSession

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

# The line, its button, and what a tap or /now answers (no em dashes: see
# tests/test_no_em_dash_in_ui_text.py).
LINE_TEXT = "⏳ Queued - Claude will read it after the current step"
BUTTON_TEXT = "⚡ Send now"
TOAST_SENT = "Sent to Claude now"
TOAST_TAKEN = "Already taken"
TOAST_INTERACTIVE = "Answer the open question first"
TOAST_TYPING = "Busy typing a prompt, try again in a moment"
TOAST_BUSY = "Busy, try again in a moment"
TOAST_FAILED = "Could not reach Claude, try again"
TOAST_SENDING = "Sending to Claude now"
LINE_TEXT_UNREACHED = ("⏳ Queued - could not reach Claude, tap Send now to "
                       "try again")
TOAST_CANNOT_PROMPT = "You can't send to this session"
TOAST_UNAVAILABLE = "That session is no longer available"
REPLY_SENT = "⚡ Sent to Claude now"
REPLY_NOTHING = "Nothing is waiting in the queue"
REPLY_NO_SESSION = "No active session."
REPLY_OTHER_CHAT = "No active session in this chat."

# The line timer's wait. A module attribute so tests replace THIS and never
# patch asyncio.sleep, which is the global module: patching it through a
# module path has hung this suite twice (see CLAUDE.md). Same pattern as
# animation._lazy_card_sleep.
_queued_line_sleep = asyncio.sleep

# The shortest re-check the line timer makes, so a step whose age sits a
# hair under QUEUED_LINE_TOOL_AGE cannot make it spin.
_QUEUED_LINE_MIN_WAIT: float = 0.05

# A line due at once (behind a long step) is due within a millisecond of
# the pick-up, which can be before Claude has written the message's
# `enqueue` queue record: the evidence check then finds nothing (live test
# 2026-09-29: the first and last of four queued messages got no line). The
# timer re-checks after each of these pauses (about 2 s in all) before it
# gives up; each re-check reads the transcript once.
_QUEUED_LINE_EVIDENCE_PAUSES: tuple[float, ...] = (0.2, 0.3, 0.5, 1.0)


def _queued_line_wait(sess: TrackedSession, waited: float) -> float:
    """Seconds the line timer should still sleep, ``<= 0`` once the line
    is due: ``QUEUED_LINE_DELAY`` after the pick-up (*waited* so far), or as
    soon as the parent's current step (``parent_tool_started_at``, never a
    subagent's) has run ``QUEUED_LINE_TOOL_AGE``. With no step running it
    sleeps at most ``QUEUED_LINE_TOOL_AGE``, so a step that starts later is
    seen before it is that old: at most four wake-ups in all.

    The step's start is stamped with ``time.monotonic()``; the running
    loop's ``time()`` is that same clock (and the virtual one in tests)."""
    left = config.QUEUED_LINE_DELAY - waited
    start = sess.parent_tool_started_at
    if start is None:
        return min(left, config.QUEUED_LINE_TOOL_AGE)
    age = asyncio.get_running_loop().time() - start
    if age >= config.QUEUED_LINE_TOOL_AGE:
        return 0.0
    return min(left, max(config.QUEUED_LINE_TOOL_AGE - age,
                         _QUEUED_LINE_MIN_WAIT))

# How long a tap waits for its chord before it toasts. The dispatcher sends
# the blank ack that clears the tap's spinner after
# ``callbacks.CALLBACK_ACK_BOUND`` (1 s), and a toast sent after it is
# dropped, so the tap toasts below that bound: the outcome if the chord has
# finished, else ``TOAST_SENDING``, and the chord's outcome then shows on
# the line (deleted when sent, ``LINE_TEXT_UNREACHED`` when not).
SEND_NOW_TOAST_WAIT: float = 0.8


@dataclass
class SendNowOutcome:
    """What :meth:`SendNowMixin._send_now_core` did.

    ``result`` is one of ``"sent"`` (the chord was written, or one already
    was being written), ``"taken"`` (a tap's message is no longer held by
    Claude), ``"nothing"`` (``/now`` found nothing held), ``"interactive"``
    (a dialog is open), ``"held"`` (the newest turn's hook events are
    still held, so whether a dialog is open is not known yet), ``"typing"``
    (aipager is typing a prompt into the session) or ``"failed"`` (the
    terminal write failed)."""

    result: str
    label: str


def _target_ids(sess: TrackedSession) -> set:
    return {t.get("msg_id") for t in sess.queued_targets}


def _pressed_since_armed(sess: TrackedSession, target: dict) -> bool:
    """Whether a Send now was pressed (and the session has not gone IDLE
    since) after *target*'s line was armed: that line's send, waiting for
    its token, is stale. The press stamp and the arm time are both the
    monotonic clock (the running loop's ``time()`` is ``time.monotonic``)."""
    pressed = sess.send_now_pressed_at
    return pressed > 0.0 and pressed >= target.get("line_armed_at", 0.0)


class SendNowMixin:
    """Mixin for TelegramBot: the "⏳ Queued" line, its "⚡ Send now" button
    and ``/now``. See the module docstring."""

    # ── the line: timer, send, keyboard ─────────────────────────────────

    def _arm_queued_lines(self, sess: TrackedSession, queued: list[dict]) -> None:
        """A message was queued (the queued-while-busy branch of the
        ``queue_pickup`` handler, its target already recorded): give the
        session its line if it has none (:meth:`_arm_next_queued_line`)."""
        self._arm_next_queued_line(sess)

    def _arm_next_queued_line(self, sess: TrackedSession) -> None:
        """Start the line timer for the oldest queued target that has not
        had a line yet, if the session has no line and no timer.

        ONE line per session (live test 2026-09-29: a line per message came
        12-39 s late at this chat's 0.5/s ceiling, some after the answer):
        its button sends everything Claude holds, so a message queued while
        a line is up or due needs none of its own. Each target is tried at
        most once (``line_armed``), so a line that could not be sent hands
        on to the next message instead of retrying the same one. The
        messages a Send now already answered for are marked by
        :meth:`_drop_all_queued_lines`, so the queue Claude is taking gets
        no new line; a message queued after the press still does."""
        if sess.queued_lines or sess.queued_line_timers:
            return
        for target in sess.queued_targets:
            msg_id = target.get("msg_id")
            if type(msg_id) is not int or target.get("line_armed"):
                continue
            target["line_armed"] = True
            target["line_armed_at"] = asyncio.get_running_loop().time()
            sess.queued_line_timers[msg_id] = asyncio.create_task(
                self._queued_line_timer(sess, msg_id))
            return

    async def _queued_line_timer(self, sess: TrackedSession, msg_id: int) -> None:
        """Wait until the line is due (:func:`_queued_line_wait`), give
        Claude a moment to write the message's queue record if it has not
        yet (:meth:`_await_queue_evidence`), then send the line if the
        message is still held. Nothing more after that: a missed line is
        cosmetic, and ``/now`` is always there."""
        me = asyncio.current_task()
        try:
            waited = 0.0
            while (wait := _queued_line_wait(sess, waited)) > 0:
                await _queued_line_sleep(wait)
                waited += wait
            await self._await_queue_evidence(sess, msg_id)
            # Past the wait, the send runs shielded: cancelled half-way it
            # could land with nobody recording its id, and that line would
            # never be deleted. It re-checks membership itself once sent.
            send = asyncio.create_task(self._send_queued_line(sess, msg_id))
            _BACKGROUND_TASKS.add(send)
            send.add_done_callback(_BACKGROUND_TASKS.discard)
            await asyncio.shield(send)
        finally:
            if sess.queued_line_timers.get(msg_id) is me:
                del sess.queued_line_timers[msg_id]
        # Ran to the end without a line (no evidence, the chat muted, ...):
        # the next waiting message gets its chance. Not after a cancel (the
        # drop that cancelled it arms the next itself).
        if msg_id not in sess.queued_lines:
            self._arm_next_queued_line(sess)

    async def _await_queue_evidence(self, sess: TrackedSession,
                                    msg_id: int) -> None:
        """Return once Claude's transcript shows *msg_id* queued, or it no
        longer needs a line (taken, or one is up), or the re-checks
        (``_QUEUED_LINE_EVIDENCE_PAUSES``) run out. The send that follows
        makes the final check and logs the reason if there is still no
        evidence."""
        for pause in _QUEUED_LINE_EVIDENCE_PAUSES:
            target = next((t for t in sess.queued_targets
                           if t.get("msg_id") == msg_id), None)
            if target is None or msg_id in sess.queued_lines:
                return
            if held_by_claude(sess, [target]):
                return
            await _queued_line_sleep(pause)

    async def _send_queued_line(self, sess: TrackedSession, msg_id: int) -> None:
        """Send the line under *msg_id* if every check passes. Never raises."""
        try:
            await self._send_queued_line_checked(sess, msg_id)
        except Exception:
            log.info("[%s] queued line for %s not sent: error", sess.label,
                     msg_id, exc_info=True)

    async def _send_queued_line_checked(self, sess: TrackedSession,
                                        msg_id: int) -> None:
        if not self._app:
            return
        # Every due line not sent is logged at INFO with its reason, so a
        # live test is diagnosable from the journal.
        # 1. Still this registry's live session.
        if self.registry.get(sess.name) is not sess or sess.status == Status.GONE:
            log.info("[%s] queued line for %s not sent: session gone",
                     sess.label, msg_id)
            return
        # 2. Still queued, and no line yet.
        target = next((t for t in sess.queued_targets
                       if t.get("msg_id") == msg_id), None)
        if target is None or sess.queued_lines:
            log.info("[%s] queued line for %s not sent: %s", sess.label,
                     msg_id, "taken" if target is None
                     else "a line is already up")
            return
        # 2b. Not once Send now was pressed after this line was armed:
        # Claude is taking that queue.
        if _pressed_since_armed(sess, target):
            log.info("[%s] queued line for %s not sent: send now pressed",
                     sess.label, msg_id)
            return
        # 3. Claude's transcript still shows it held. Without that evidence
        # no line: a button over an empty queue invites a pointless tap.
        if not held_by_claude(sess, [target]):
            log.info("[%s] queued line for %s not sent: no queue evidence",
                     sess.label, msg_id)
            return
        # 4. Sent to the session's own chat, where its button resolves.
        chat_id = resolve_chat_id_int(sess)
        target_chat = target.get("chat_id")
        if not chat_id or (target_chat is not None and target_chat != chat_id):
            log.info("[%s] queued line for %s not sent: not the session's "
                     "chat", sess.label, msg_id)
            return
        # 5. Not while the chat is muted or in minimal mode.
        if session_monitor.cards_suppressed(chat_id):
            log.info("[%s] queued line for %s not sent: chat suppressed "
                     "(muted or minimal mode)", sess.label, msg_id)
            return
        # BLOCKING at answer priority, never a skip-kind ornament: a
        # skip-kind send was refused whenever a busy card had left the chat
        # below the reserve (live test 2026-09-27: a queued message never
        # got its line), and a blocking ORNAMENT lost every token to the
        # card's own edits at a low ceiling, landing many seconds late. It
        # is the operator's only way to send the queue now, and there is
        # one per session, so it cannot pile up ahead of answers. The mute
        # and minimal mode are refused above (cards_suppressed), and a
        # message taken while it waited is dropped by the re-check below.
        sent = await send_text(
            self._app.bot, chat_id, LINE_TEXT,
            reply_to_message_id=msg_id,
            reply_markup=self._build_send_now_keyboard(sess, msg_id),
            rate_limit_args=_rl_args(priority=PRIORITY_ESSENTIAL),
        )
        line_id = getattr(sent, "message_id", None) if sent else None
        if type(line_id) is not int or line_id <= 0:
            why = ("chat muted" if sent is MUTED
                   else f"send failed ({sent!r})")
            log.info("[%s] queued line for %s not sent: %s", sess.label,
                     msg_id, why)
            return
        sess.queued_lines[msg_id] = (chat_id, line_id)
        # Owed a delete from now on, persisted: a restart deletes it (D8).
        self._owe_line_delete(chat_id, line_id)
        log.info("[%s] queued line %d sent for message %d", sess.label,
                 line_id, msg_id)
        # Taken (or the session gone, or Send now pressed) while the send
        # was out: drop it now. The send can wait seconds for its token, so
        # the transcript is asked again too: a message Claude took in that
        # wait is no longer held, though no tick may have removed its target
        # yet. A line landing after a Send now press never stays up (live
        # test 2026-09-29: three landed after the answer).
        if self.registry.get(sess.name) is not sess:
            why = "session gone"
        elif msg_id not in _target_ids(sess):
            why = f"message {msg_id} taken"
        elif _pressed_since_armed(sess, target):
            why = "send now pressed"
        elif len(sess.queued_lines) > 1:
            # One line per session, by construction: a send whose timer was
            # cancelled can still land beside the line that moved on.
            why = "another line is up"
        elif not held_by_claude(sess, [target]):
            why = f"message {msg_id} taken"
        else:
            return
        log.info("[%s] queued line %d dropped: %s while it was sent",
                 sess.label, line_id, why)
        # Dropping it cancels this line's own timer (still awaiting this
        # send), so its hand-on never runs: move the line on from here, or
        # the next waiting message never gets one (review rev-iter1-001).
        self._drop_queued_line(sess, msg_id)
        self._arm_next_queued_line(sess)

    def _build_send_now_keyboard(self, sess: TrackedSession,
                                 msg_id: int) -> InlineKeyboardMarkup:
        # Local import, like keyboards._build_stop_keyboard's.
        from aipager.bot import session_parity

        chat_id = resolve_chat_id_int(sess) or 0
        return InlineKeyboardMarkup([[InlineKeyboardButton(
            BUTTON_TEXT,
            callback_data=session_parity.session_cb(
                self, chat_id, sess, f"now:{msg_id}"),
        )]])

    # ── the line: deletion ──────────────────────────────────────────────

    def _sync_queued_lines(self, sess: TrackedSession) -> None:
        """Drop the line, or pending timer, of every msg_id no longer in
        ``queued_targets``, and when one went while other messages still
        wait, move the line on to the next of them
        (:meth:`_arm_next_queued_line`). Called right after each place a
        target leaves that list, always on the running loop: idempotent,
        synchronous."""
        live = _target_ids(sess)
        dropped = False
        for msg_id in list(set(sess.queued_lines) | set(sess.queued_line_timers)):
            if msg_id not in live:
                self._drop_queued_line(sess, msg_id)
                dropped = True
        if dropped:
            self._arm_next_queued_line(sess)

    def _discard_queued_targets(self, sess: TrackedSession) -> None:
        """Forget every queued target (a teardown: /stop, /clearqueue,
        /kill, a halt, a relaunch, a session end) and drop their lines."""
        sess.queued_targets.clear()
        self._sync_queued_lines(sess)

    def _drop_queued_line(self, sess: TrackedSession, msg_id: int) -> None:
        """Cancel *msg_id*'s timer and delete its line, if it has either."""
        task = sess.queued_line_timers.pop(msg_id, None)
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()
        rec = sess.queued_lines.pop(msg_id, None)
        if rec is not None:
            self._delete_queued_line_later(*rec)

    def _owe_line_delete(self, chat_id: int, line_id: int) -> None:
        owed = self.registry.queued_line_deletes
        pair = [chat_id, line_id]
        if pair not in owed:
            owed.append(pair)
            self.registry.mark_dirty()

    def _settle_line_delete(self, chat_id: int, line_id: int) -> None:
        owed = self.registry.queued_line_deletes
        pair = [chat_id, line_id]
        if pair in owed:
            owed.remove(pair)
            self.registry.mark_dirty()

    def _delete_queued_line_later(self, chat_id: int, line_id: int) -> None:
        """Delete a line in the background, at answer (ESSENTIAL) priority:
        its message has left Claude's queue or was just sent, so a button
        still showing would be wrong, and ORNAMENT pacing behind card edits
        kept one up for up to 23 s in a live test. Never raises, never
        waits.

        Owed until it lands. Skipped outright while the chat is muted, and
        a delete the gate refuses (a flood mute that starts while it waits)
        stays owed: the next startup deletes it
        (``_delete_owed_queued_lines``). Any other
        failure means the message is gone already, and it is no longer
        owed."""
        if not self._app:
            return
        self._owe_line_delete(chat_id, line_id)
        if MUTE.is_muted(chat_id):
            return
        bot = self._app.bot

        async def _delete() -> None:
            try:
                await bot.delete_message(
                    chat_id=chat_id, message_id=line_id,
                    rate_limit_args=_rl_args(priority=PRIORITY_ESSENTIAL),
                )
            except (FloodSkipped, FloodMuted):
                log.info("queued line %d delete refused by the flood gate; "
                         "owed until the next start", line_id)
                return
            except Exception:
                log.debug("queued line %d delete failed (already gone)",
                          line_id, exc_info=True)
            else:
                log.info("queued line %d deleted", line_id)
            self._settle_line_delete(chat_id, line_id)

        task = asyncio.create_task(_delete())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    async def _delete_owed_queued_lines(self, bot) -> None:
        """At startup: delete every line a previous run left owed (D8).
        Once each; an entry whose chat is muted stays owed."""
        for chat_id, line_id in list(self.registry.queued_line_deletes):
            if MUTE.is_muted(chat_id):
                continue
            try:
                await bot.delete_message(chat_id=chat_id, message_id=line_id)
                log.info("queued line %d a restart left behind deleted",
                         line_id)
            except FloodMuted:
                continue
            except Exception as e:  # noqa: BLE001 - best effort
                log.info("queued line %d a restart left behind could not be "
                         "deleted: %s", line_id, e)
            self._settle_line_delete(chat_id, line_id)

    # ── send now: the shared core, the tap, /now ────────────────────────

    async def _send_now_core(self, sess: TrackedSession,
                             target_msg_id: int | None) -> SendNowOutcome:
        """Press send-now in *sess* if Claude still holds the tapped message
        (*target_msg_id*), or, for ``/now`` (``None``), any queued message.

        The checks, in order: evidence (D5), no dialog open (D3), no hook
        events held for the newest turn (D3 again), aipager not typing a
        prompt (D4), no chord already going out. There is NO
        await between the typing check and the chord's start, so a prompt
        injection cannot begin in between."""
        if target_msg_id is None:
            candidates = list(sess.queued_targets)
            nothing = "nothing"
        else:
            candidates = [t for t in sess.queued_targets
                          if t.get("msg_id") == target_msg_id]
            nothing = "taken"
        if not candidates or not held_by_claude(sess, candidates):
            return SendNowOutcome(nothing, sess.label)
        if sess.status == Status.INTERACTIVE or sess.pending_permission:
            return SendNowOutcome("interactive", sess.label)
        if sess.turn_state_held():
            # The newest turn's own hook events wait for its card state
            # (roadmap 8.62), a PermissionRequest or AskUserQuestion among
            # them: a dialog may be open in Claude's terminal that the
            # status above does not show yet, and the keys could reach it.
            return SendNowOutcome("held", sess.label)
        if sess.prompt_injecting > 0:
            return SendNowOutcome("typing", sess.label)
        if sess.send_now_inflight:
            return SendNowOutcome("sent", sess.label)
        sess.send_now_inflight = True
        # Stamped BEFORE the keys go out: Claude writes its take's
        # `dequeue` within a fraction of a second of the Ctrl+S, which can be
        # before `inject.send_now` returns (it waits for `dtach -p` to exit),
        # and a tick that read the dequeue with no stamp would pass it for
        # good (roadmap 8.64). A chord that then fails stamps harmlessly: a
        # mid-turn dequeue has no other source, and IDLE clears the stamp.
        sess.mark_send_now_pressed()
        # The press answers for the whole queue as it stands: none of it
        # gets a line from here on, not even one moved while the chord is
        # still out (review rev-iter1-002: a slow chord let a tick move the
        # line under the next message for a moment).
        for queued in sess.queued_targets:
            queued["line_armed"] = True
        try:
            ok = await inject.send_now(sess.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            # `dtach -p` could not even be started (a fork failure on a
            # loaded box): the keys did not arrive, which is a failure to
            # report, never an exception that loses the tap's outcome
            # (review rev-iter3-001).
            log.warning("[%s] send now could not start its writes",
                        sess.label, exc_info=True)
            ok = False
        finally:
            sess.send_now_inflight = False
        log.info("[%s] send now %s", sess.label, "sent" if ok else "failed")
        return SendNowOutcome("sent" if ok else "failed", sess.label)

    async def _handle_send_now_tap(self, update: Update, query,
                                   session_name: str, raw_msg_id: str) -> None:
        """The line's "⚡ Send now" button (callback verb ``now:<msg_id>``,
        already resolved from its chat's short-form index)."""
        try:
            msg_id = int(raw_msg_id)
        except (TypeError, ValueError):
            await self._safe_answer(query, "Invalid callback")
            return
        chat_id = calling_chat_id(update)
        tapped_id = getattr(getattr(query, "message", None), "message_id", None)
        sess = self.registry.get(session_name)
        if sess is None:
            await self._safe_answer(query, "Session not found")
            if chat_id and type(tapped_id) is int:
                self._delete_queued_line_later(chat_id, tapped_id)
            return
        user_id = getattr(getattr(query, "from_user", None), "id", None)
        if not self._can_prompt_user(user_id, chat_id):
            # The line stays: someone else in the chat may tap it (D6).
            await self._safe_answer(query, TOAST_CANNOT_PROMPT)
            return
        if sess.scope_chat_id and chat_id is not None and sess.scope_chat_id != chat_id:
            await self._safe_answer(query, TOAST_UNAVAILABLE)
            return
        # The core in its own task (its checks and the chord's start still
        # run in one step, in that task): the toast cannot wait past the
        # ack bound for a slow chord.
        core = asyncio.create_task(self._send_now_core(sess, msg_id))
        _BACKGROUND_TASKS.add(core)
        core.add_done_callback(_BACKGROUND_TASKS.discard)
        await asyncio.wait({core}, timeout=SEND_NOW_TOAST_WAIT)
        if not core.done():
            await self._safe_answer(query, TOAST_SENDING)
            await asyncio.wait({core})
            outcome = core.result()
            if outcome.result in ("sent", "taken"):
                self._drop_tapped_line(sess, msg_id, chat_id, tapped_id,
                                       taken=outcome.result == "taken")
            else:
                await self._show_line_unreached(sess, msg_id, chat_id,
                                                tapped_id)
            return
        outcome = core.result()
        toast = {
            "sent": TOAST_SENT,
            "taken": TOAST_TAKEN,
            "interactive": TOAST_INTERACTIVE,
            "held": TOAST_BUSY,
            "typing": TOAST_TYPING,
        }.get(outcome.result, TOAST_FAILED)
        await self._safe_answer(query, toast)
        if outcome.result not in ("sent", "taken"):
            return
        self._drop_tapped_line(sess, msg_id, chat_id, tapped_id,
                               taken=outcome.result == "taken")

    def _drop_tapped_line(self, sess: TrackedSession, msg_id: int,
                          chat_id: int | None, tapped_id: object, *,
                          taken: bool) -> None:
        """The tapped line goes at once, and so does every other line of
        the session (the operator's rule, live test 2026-09-28: Send now
        sends everything Claude holds, so no button may stay). A tapped
        line aipager no longer tracks is deleted by its own id. For a tap
        on a message already *taken* (nothing pressed) the queue the tapped
        line stood for is marked here; a press marked it already."""
        tracked = msg_id in sess.queued_lines
        self._drop_all_queued_lines(sess, mark_queue=taken)
        if not tracked and chat_id and type(tapped_id) is int:
            self._delete_queued_line_later(chat_id, tapped_id)

    def _drop_all_queued_lines(self, sess: TrackedSession, *,
                               mark_queue: bool = False) -> None:
        """Drop every line and pending line timer of *sess*. Its queued
        targets stay: they still route the answers and reactions. The queue
        a Send now answered for gets no new line: the press marked it (see
        :meth:`_send_now_core`), and *mark_queue* marks it for a tap that
        pressed nothing. A message queued while the chord was out is not
        marked, so it gets its line now if Claude still holds it (review
        rev-iter2-001)."""
        if mark_queue:
            for target in sess.queued_targets:
                target["line_armed"] = True
        for msg_id in list(set(sess.queued_lines) | set(sess.queued_line_timers)):
            self._drop_queued_line(sess, msg_id)
        self._arm_next_queued_line(sess)

    async def _show_line_unreached(self, sess: TrackedSession, msg_id: int,
                                   chat_id: int | None,
                                   tapped_id: object) -> None:
        """A slow chord failed after the tap was toasted ``TOAST_SENDING``:
        say so on the line, keeping its button. Nothing to edit once the
        line is gone (its message was taken meanwhile). Never raises."""
        if not self._app:
            return
        rec = sess.queued_lines.get(msg_id)
        if rec is None:
            if (msg_id not in _target_ids(sess) or not chat_id
                    or type(tapped_id) is not int):
                return
            rec = (chat_id, tapped_id)
        line_chat, line_id = rec
        try:
            await edit_text_at(
                self._app.bot, text=LINE_TEXT_UNREACHED, chat_id=line_chat,
                message_id=line_id,
                reply_markup=self._build_send_now_keyboard(sess, msg_id),
                rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
            )
        except Exception:
            log.debug("[%s] queued line %d not marked unreached", sess.label,
                      line_id, exc_info=True)

    async def _handle_now_cmd(self, update: Update,
                              ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """``/now``: send whatever Claude is holding in the active session's
        queue, at any time (before the line appears, or while the chat is
        muted or in minimal mode and no line is sent). The session is
        resolved exactly like ``/stop``'s. Once the keys are sent, every
        line of the session goes, as after a tap."""
        if not await self._authorize(update):
            return
        name = self.registry.last_active_session
        sess = self.registry.get(name) if name else None
        if sess is None:
            await reply_text(update.message, REPLY_NO_SESSION)
            return
        chat_id = calling_chat_id(update)
        if sess.scope_chat_id and chat_id is not None and sess.scope_chat_id != chat_id:
            await reply_text(update.message, REPLY_OTHER_CHAT)
            return
        outcome = await self._send_now_core(sess, None)
        if outcome.result == "sent":
            self._drop_all_queued_lines(sess)
        await reply_text(update.message, {
            "sent": REPLY_SENT,
            "nothing": REPLY_NOTHING,
            "interactive": TOAST_INTERACTIVE,
            "held": TOAST_BUSY,
            "typing": TOAST_TYPING,
        }.get(outcome.result, TOAST_FAILED))
