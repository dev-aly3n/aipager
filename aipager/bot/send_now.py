""""⚡ Send now" for a Telegram message still waiting in Claude Code's queue.

A message sent while a turn runs is injected into Claude at once (aipager
never holds it back) and Claude Code queues it (a *queued target*,
``TrackedSession.queued_targets``) until the running step ends. Each such
message gets its own reply line and button, "⏳ Queued - Claude will read
it after the current step [⚡ Send now]", the moment Claude's transcript
shows it in Claude's queue (usually at the pick-up; a few short re-checks
cover the record landing a moment late). The line goes the moment Claude
takes the message: a per-session queue watcher reads Claude's queue
records every ``QUEUE_WATCH_INTERVAL`` and deletes the line, with the 👍,
whatever the busy card is doing (operator, live test 2026-09-29: no delay
either way).
The button, or ``/now``, presses Claude Code's own send-now keys
(``inject.send_now``: Ctrl+X, Ctrl+S), which sends every queued message at
once: a running command or agent moves to the background, a reply being
written is cut short and restarted with the message.

The line's whole lifecycle lives here. A line exists only for a msg_id in
``queued_targets``: :meth:`SendNowMixin._sync_queued_lines` drops the line
(or its pending timer) of every msg_id that has left that list, whichever
code removed it, and several lines leaving together go in one delete.
Nothing guesses a message's fate. Every line sent is owed a delete on the
registry (``SessionRegistry.queued_line_deletes``) until the delete lands,
and startup deletes what a restart left behind.

Lines and their deletes go at ``flood_budget.PRIORITY_INSTANT``: they never
wait for a chat token and are not suspended by minimal mode; only a
Telegram mute refuses them (a refused delete stays owed). A Send now (tap
or ``/now``) drops the lines of the queue it sent, and none of those
messages gets a new line; a message sent afterwards gets its own.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter

from aipager import config
from aipager.bot import reactions
from aipager.bot.animation import _scan_queue_operations
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_INSTANT,
    FloodSkipped,
    _retry_after_seconds,
    rate_limit_args as _rl_args,
)
from aipager.bot.notify import _BACKGROUND_TASKS
from aipager.bot.session_ops import held_by_claude
from aipager.bot.transport import (
    calling_chat_id,
    edit_text_at,
    MUTED,
    reply_text,
    resolve_chat_id,
    resolve_chat_id_int,
    send_text,
)
from aipager.dtach import inject
from aipager.state import Status, TrackedSession

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

log = logging.getLogger(__name__)


def _small_429_wait(exc: RetryAfter, chat_id: int | None) -> float | None:
    """Seconds to wait before the one retry a line send or delete gets
    after a 429, or None to give up at once. A ban-sized 429 (over
    ``TELEGRAM_MAX_RETRY_AFTER``) has armed the chat's mute, and sleeping
    it out would park the task for hours and fire the retry right at the
    ban's end (review rev-iter2-002)."""
    wait = _retry_after_seconds(exc)
    if wait > config.TELEGRAM_MAX_RETRY_AFTER or (
            chat_id is not None and MUTE.is_muted(chat_id)):
        return None
    return wait

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

# The line timer's and the queue watcher's waits. Module attributes so tests
# replace THESE and never patch asyncio.sleep, which is the global module:
# patching it through a module path has hung this suite twice (see
# CLAUDE.md). Same pattern as animation._lazy_card_sleep.
_queued_line_sleep = asyncio.sleep
_queue_watch_sleep = asyncio.sleep
_line_retry_sleep = asyncio.sleep

# The line goes out the moment Claude's transcript shows the message in its
# queue, which is usually already true at the pick-up; the record can land
# a moment later (live test 2026-09-29: the first and last of four queued
# messages got no line), so the timer re-checks after each of these pauses
# (about 2 s in all) before it gives up. Each re-check reads the transcript
# once.
_QUEUED_LINE_EVIDENCE_PAUSES: tuple[float, ...] = (0.2, 0.3, 0.5, 1.0)

# How often the queue watcher reads Claude's new queue records while a
# message is queued: a line goes, with its 👍, within this of the record
# saying Claude took the message, whatever the card is doing.
QUEUE_WATCH_INTERVAL: float = 0.25

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
        """Messages were queued behind a running turn (the queued-while-busy
        branch of the ``queue_pickup`` handler, their targets already
        recorded): each gets its own line, at once, and the session's queue
        watcher runs so each line goes the moment its message is taken.

        A message a Send now or a taken tap already answered for
        (``line_armed``, see :meth:`_send_now_core` and
        :meth:`_drop_all_queued_lines`) gets none; a duplicate pick-up
        re-arms nothing."""
        armed = False
        for note in queued:
            msg_id = note.get("msg_id")
            if type(msg_id) is not int:
                continue
            if msg_id in sess.queued_line_timers or msg_id in sess.queued_lines:
                continue
            target = next((t for t in sess.queued_targets
                           if t.get("msg_id") == msg_id), None)
            if target is None or target.get("line_armed"):
                continue
            target["line_armed"] = True
            target["line_armed_at"] = asyncio.get_running_loop().time()
            sess.queued_line_timers[msg_id] = asyncio.create_task(
                self._queued_line_timer(sess, msg_id))
            armed = True
        if armed:
            self._ensure_queue_watch(sess)

    async def _queued_line_timer(self, sess: TrackedSession, msg_id: int) -> None:
        """Send the line as soon as Claude's transcript shows the message in
        its queue (:meth:`_await_queue_evidence`: usually at once, at most
        about 2 s of short re-checks), if it is still held. No due delay
        (operator, 2026-09-29: "it must be sent immediately")."""
        me = asyncio.current_task()
        try:
            await self._await_queue_evidence(sess, msg_id)
            # The send runs shielded: cancelled half-way it could land with
            # nobody recording its id, and that line would never be
            # deleted. It re-checks membership itself once sent.
            send = asyncio.create_task(self._send_queued_line(sess, msg_id))
            _BACKGROUND_TASKS.add(send)
            send.add_done_callback(_BACKGROUND_TASKS.discard)
            await asyncio.shield(send)
        finally:
            if sess.queued_line_timers.get(msg_id) is me:
                del sess.queued_line_timers[msg_id]

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
        """Send the line under *msg_id* if every check passes. Never raises.

        A small 429 (Telegram asking aipager to slow down) is waited out
        once and the send tried again, every check included; a ban-sized
        one (the gate has armed the mute) gives up at once."""
        for attempt in (1, 2):
            try:
                await self._send_queued_line_checked(sess, msg_id)
                return
            except RetryAfter as exc:
                wait = _small_429_wait(exc, resolve_chat_id_int(sess))
                if attempt == 2 or wait is None:
                    log.info("[%s] queued line for %s not sent: Telegram "
                             "%s", sess.label, msg_id,
                             "asked to slow down twice" if wait is not None
                             else "muted the chat")
                    return
                await _line_retry_sleep(wait)
            except Exception:
                log.info("[%s] queued line for %s not sent: error",
                         sess.label, msg_id, exc_info=True)
                return

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
        # 2. Still queued, and no line of its own yet.
        target = next((t for t in sess.queued_targets
                       if t.get("msg_id") == msg_id), None)
        if target is None or msg_id in sess.queued_lines:
            log.info("[%s] queued line for %s not sent: %s", sess.label,
                     msg_id, "taken" if target is None else "already up")
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
        # INSTANT (flood_budget.PRIORITY_INSTANT): never waits for a chat
        # token and is not suspended by minimal mode (operator, 2026-09-29:
        # "I dont want the flood manager block the sending of these kind of
        # messages"). Only a Telegram mute refuses it: `send_text` returns
        # MUTED then, and no line goes into an active ban.
        sent = await send_text(
            self._app.bot, chat_id, LINE_TEXT,
            reply_to_message_id=msg_id,
            reply_markup=self._build_send_now_keyboard(sess, msg_id),
            rate_limit_args=_rl_args(priority=PRIORITY_INSTANT),
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
        # was out: drop it now. The transcript is asked again too: a message
        # Claude took during the send is no longer held, though the watcher
        # may not have read that record yet. A line landing after a Send now
        # press never stays up (live test 2026-09-29: three landed after the
        # answer).
        if self.registry.get(sess.name) is not sess:
            why = "session gone"
        elif msg_id not in _target_ids(sess):
            why = f"message {msg_id} taken"
        elif _pressed_since_armed(sess, target):
            why = "send now pressed"
        elif not held_by_claude(sess, [target]):
            why = f"message {msg_id} taken"
        else:
            return
        log.info("[%s] queued line %d dropped: %s while it was sent",
                 sess.label, line_id, why)
        self._drop_queued_line(sess, msg_id)

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
        ``queued_targets`` (several at once go in one delete). Called right
        after each place a target leaves that list, always on the running
        loop: idempotent, synchronous."""
        live = _target_ids(sess)
        self._drop_queued_lines(
            sess, [m for m in set(sess.queued_lines) | set(sess.queued_line_timers)
                   if m not in live])

    def _discard_queued_targets(self, sess: TrackedSession) -> None:
        """Forget every queued target (a teardown: /stop, /clearqueue,
        /kill, a halt, a relaunch, a session end) and drop their lines."""
        sess.queued_targets.clear()
        self._sync_queued_lines(sess)

    def _drop_queued_line(self, sess: TrackedSession, msg_id: int) -> None:
        """Cancel *msg_id*'s timer and delete its line, if it has either."""
        self._drop_queued_lines(sess, [msg_id])

    def _drop_queued_lines(self, sess: TrackedSession, msg_ids) -> None:
        """Cancel each msg_id's timer and delete its line, the lines of one
        chat in one call."""
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        by_chat: dict[int, list[int]] = {}
        for msg_id in msg_ids:
            task = sess.queued_line_timers.pop(msg_id, None)
            if task is not None and not task.done() and task is not current:
                task.cancel()
            rec = sess.queued_lines.pop(msg_id, None)
            if rec is not None:
                by_chat.setdefault(rec[0], []).append(rec[1])
        for chat_id, line_ids in by_chat.items():
            self._delete_queued_lines_later(chat_id, line_ids)

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
        """One line: :meth:`_delete_queued_lines_later`."""
        self._delete_queued_lines_later(chat_id, [line_id])

    def _delete_queued_lines_later(self, chat_id: int,
                                   line_ids: list[int]) -> None:
        """Delete lines in the background, INSTANT (never waiting for a
        chat token): their messages have left Claude's queue or were just
        sent, so a button still showing would be wrong. Several lines of
        one chat go in one ``deleteMessages`` call. Never raises, never
        waits.

        Owed until they land. Skipped outright while the chat is muted,
        and a delete refused by the gate (a mute) or answered with a 429
        stays owed: the next startup deletes it
        (``_delete_owed_queued_lines``). Any other failure means the
        message is gone already, and it is no longer owed."""
        if not self._app or not line_ids:
            return
        for line_id in line_ids:
            self._owe_line_delete(chat_id, line_id)
        if MUTE.is_muted(chat_id):
            return
        bot = self._app.bot
        ids = list(line_ids)

        async def _delete() -> None:
            args = _rl_args(priority=PRIORITY_INSTANT)
            try:
                for attempt in (1, 2):
                    try:
                        if len(ids) == 1:
                            await bot.delete_message(
                                chat_id=chat_id, message_id=ids[0],
                                rate_limit_args=args)
                        else:
                            await bot.delete_messages(
                                chat_id=chat_id, message_ids=ids,
                                rate_limit_args=args)
                        break
                    except RetryAfter as exc:
                        # Telegram asked to slow down: wait it out once and
                        # try again, or the Send now button would stay up
                        # until the next start (review rev-iter1-003). A
                        # ban-sized 429 has armed the mute: give up at
                        # once, the lines stay owed (rev-iter2-002).
                        wait = _small_429_wait(exc, chat_id)
                        if attempt == 2 or wait is None:
                            raise
                        await _line_retry_sleep(wait)
            except (FloodSkipped, FloodMuted, RetryAfter):
                log.info("queued lines %s delete refused by the flood gate "
                         "or Telegram; owed until the next start", ids)
                return
            except Exception:
                log.debug("queued lines %s delete failed (already gone)",
                          ids, exc_info=True)
            else:
                log.info("queued line%s %s deleted",
                         "s" if len(ids) > 1 else "",
                         ", ".join(str(i) for i in ids))
            for line_id in ids:
                self._settle_line_delete(chat_id, line_id)

        task = asyncio.create_task(_delete())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    # ── the queue watcher ───────────────────────────────────────────────

    def _ensure_queue_watch(self, sess: TrackedSession) -> None:
        """Start the session's queue watcher if it is not running. The
        running watchers live on the bot instance (created on first use),
        by session name."""
        watches = self.__dict__.setdefault("_queue_watches", {})
        task = watches.get(sess.name)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._queue_watch(sess))
        watches[sess.name] = task
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    async def _queue_watch(self, sess: TrackedSession) -> None:
        """While messages are queued, read Claude's new queue records every
        ``QUEUE_WATCH_INTERVAL``: a message Claude took leaves
        ``queued_targets`` (:func:`animation._scan_queue_operations`, the
        same reader the card's tick uses, sharing its offset so each record
        is handled once), its line is deleted, and it gets its 👍 at once.
        The card tick is paced by the chat's flood budget; this is not
        (live test 2026-09-29: lines and 👍 lagged the queue by seconds).
        Moving the reply target and the card stays with the tick and the
        finish path, which drain the same staged notes. Ends when nothing
        is queued or the session is gone; the next armed line restarts it.
        Never raises."""
        try:
            while True:
                await _queue_watch_sleep(QUEUE_WATCH_INTERVAL)
                if (self.registry.get(sess.name) is not sess
                        or sess.status == Status.GONE
                        or not sess.queued_targets
                        or not sess.stream_transcript_path
                        or (sess.status is not Status.BUSY
                            and not sess.queued_lines
                            and not sess.queued_line_timers)):
                    # Nothing to watch: no queue, no transcript to read, or
                    # an idle session with no line left (a stale target
                    # must not keep it reading four times a second; review
                    # rev-iter1-006). The next armed line restarts it.
                    return
                staged = len(sess.stream_consumed_notes)
                _scan_queue_operations(sess)
                taken = list(sess.stream_consumed_notes[staged:])
                self._sync_queued_lines(sess)
                if taken:
                    # In the background: a slow reaction round-trip must
                    # not hold the next scan's deletes back.
                    task = asyncio.create_task(reactions.mark_all(
                        self, taken, reactions.TAKEN, resolve_chat_id(sess)))
                    _BACKGROUND_TASKS.add(task)
                    task.add_done_callback(_BACKGROUND_TASKS.discard)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("[%s] queue watcher stopped", sess.label,
                        exc_info=True)

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
        """Drop the lines and pending line timers of the queue a Send now
        (or a tap on a taken message, *mark_queue*) answered for, in one
        delete. Its queued targets stay: they still route the answers and
        reactions, and none of them gets a new line (the press marked them,
        see :meth:`_send_now_core`; *mark_queue* marks them here). A message
        whose line was armed after the press (queued while the chord was
        out) keeps its line or timer: Claude may still hold it (review
        rev-iter2-001), and its own checks drop it if not."""
        if mark_queue:
            for target in sess.queued_targets:
                target["line_armed"] = True
        keep = {t.get("msg_id") for t in sess.queued_targets
                if not mark_queue and sess.send_now_pressed_at > 0.0
                and t.get("line_armed_at", 0.0) > sess.send_now_pressed_at}
        self._drop_queued_lines(
            sess, [m for m in set(sess.queued_lines) | set(sess.queued_line_timers)
                   if m not in keep])

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
                rate_limit_args=_rl_args(priority=PRIORITY_INSTANT),
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
