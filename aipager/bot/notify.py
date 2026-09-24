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
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import Forbidden

from aipager.bot import agents_line as _agents_line
from aipager.bot import reactions
from aipager.bot.dashboard import new_prompt_token
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import (
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args as _rl_args,
)
from aipager.bot.rich_message import (
    RichMessageBlocked,
    RichMessageFallbackRequired,
    RichMessageFloodBanned,
    RichMessageGone,
    detect_rtl,
    edit_message_text_rich,
    send_rich_message,
)


from aipager import preferences
from aipager.config import (
    COMPACT_CARD_TIMEOUT_SECONDS,
    COMPACT_DONE_PAUSE_SECONDS,
    FINISH_CARD_GRACE_SECONDS,
    STALE_BUSY_TIMEOUT,
    STREAM_EDIT_INTERVAL,
)
from aipager.state import BG_AGENTS_RETRY_SECONDS, Status, TrackedSession
from aipager.bot.animation import (
    FINAL_VERB, _RICH_LIMIT, _exact_anchors_available, _expire_tool_batch,
    _md_escape, _read_stream_text, _sync_anchors_from_transcript,
    build_full_log,
    build_stream_card_ex, final_card_has_timeline,
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
    _ERROR_PATTERNS,
    _format_perm_detail,
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

# The ONLY sleep in the finish path: the head start the finished card gets
# before the answer is sent (FINISH_CARD_GRACE_SECONDS, roadmap 8.23). A
# module attribute so tests shorten THIS and never patch asyncio.sleep,
# which is the global module — patching it through a module path has hung
# this suite twice (see CLAUDE.md). Same pattern as rich_message._sleep.
_finish_sleep = asyncio.sleep

# Strong references to fire-and-forget housekeeping tasks (the tool-less
# card's delete, roadmap 8.32): the event loop only keeps weak ones, and a
# task collected mid-flight is a delete that silently never happened.
_BACKGROUND_TASKS: set = set()

# Separator row between the finished timeline and the answer in `merged`
# layout — a literal row, not a second "✅ Finished" header (that would be
# redundant with the card's own header, per the same reasoning the
# header-skip logic below already uses for `card` layout).
_MERGED_SEPARATOR = "―――――――――――――"

# The result line ("session-name-on-every-message"): the FIRST line of
# every result message, as the status line is the LAST line of every busy
# card — one glyph per message kind, always in the same place, so a reader
# can tell a session's answer from its timeline (and from another
# session's) at a glance. The short form below is for a result that lands
# under a finished card, which already says Done and the turn's stats; the
# stats form (`💬 **label** · Finished (…)`) is built in the finish path
# for a result that is the only message left for its turn.
_RESULT_GLYPH = "💬"


def _result_line_md(label: str) -> str:
    return f"{_RESULT_GLYPH} **{_md_escape(label)}**"


def _result_line_plain(label: str) -> str:
    return f"{_RESULT_GLYPH} {label}"


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

    Paragraphs are PACKED: consecutive segments share one chunk while
    they fit, and a new chunk starts only at a boundary that would push
    it over. Splitting at every boundary sent one message per paragraph,
    and would have made the result line — the first paragraph of every
    plain-text fallback ("session-name-on-every-message") — a message of
    its own. A single segment over 4096 bytes (a long paragraph with no
    break) is still hard-cut.
    """
    bounds = _md_safe_boundaries(body_content)
    segments: list[str] = []
    prev = 0
    for b in bounds:
        segments.append(body_content[prev:b])
        prev = b
    segments.append(body_content[prev:])

    chunks: list[str] = []
    current = ""
    for segment in segments:
        if not segment:
            continue
        if len((current + segment).encode("utf-8")) <= 4096:
            current += segment
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(segment.encode("utf-8")) <= 4096:
            current = segment
            continue
        # Safety: hard-cut at 4096 bytes if a single segment exceeds the
        # limit (very long paragraph, no breaks).
        encoded = segment.encode("utf-8")
        pos = 0
        while pos < len(encoded):
            piece = encoded[pos:pos + 4096].decode("utf-8", errors="ignore")
            if piece:
                chunks.append(piece)
            pos += 4096
    if current:
        chunks.append(current)
    if not chunks:
        chunks = [body_content[:4096]]
    return chunks


class NotifyMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    def _pending_prompt_keyboard(self, sess: TrackedSession):
        """The answer keyboard for *sess*'s inline prompt, from
        ``pending_permission``: the option buttons of an AskUserQuestion
        (with any multi-select ticks), else Allow/Deny."""
        perm = sess.pending_permission or {}
        if perm.get("ask_question"):
            return self._build_inline_ask_keyboard(
                sess, perm.get("options", []),
                multi_select=perm.get("multi_select", False),
                selected=perm.get("selected") or None)
        return self._build_permission_keyboard(sess)

    def _pending_prompt_markup(self, sess: TrackedSession):
        """``(text, keyboard)`` of the prompt *sess* is waiting on, as the
        chat was shown it — for the pinned bar's "Answer" button (8.31).
        ``None`` when there is none to show (a prompt that never reached
        the chat and left nothing behind).

        Inline prompts are re-rendered from ``pending_permission`` with the
        same builders the busy card uses; a separate-message prompt is
        re-sent exactly as it was sent."""
        if sess.pending_permission:
            text = _safe_truncate(
                self._build_busy_text(sess.label, "Waiting", sess),
                TELEGRAM_MAX_TEXT_LEN, True)
            return text, self._pending_prompt_keyboard(sess)
        if sess.pending_prompt_msg:
            return (sess.pending_prompt_msg.get("text", ""),
                    sess.pending_prompt_msg.get("keyboard"))
        return None

    async def _send_merged_final(
        self, sess: TrackedSession, answer: str, *,
        send_as_new: bool = False, reply_to: int | None = None,
        agents: dict | None = None,
    ) -> bool:
        """Try to deliver a finished turn as ONE message: the finished
        timeline (exactly what ``card`` mode renders) with the answer
        appended below a separator — normally an EDIT of the existing
        busy message, or, when ``send_as_new`` is set (design.md "turn
        anchor follows consumption" R5/B6), a fresh SEND under
        ``reply_to`` instead, because the busy message is anchored to a
        message this turn did not actually consume and Telegram cannot
        move an existing message's reply target.

        Returns ``True`` on success — the turn is fully delivered, the
        caller sends nothing else. Returns ``False`` when the caller MUST
        fall back to the ``replace``-style send so the answer is never
        lost: either the combined text exceeds the byte ceiling (checked
        before any network call — no edit is attempted at all) or the
        edit/send itself failed for any reason. Never raises.

        ``send_as_new`` deliberately does NOT reuse ``_reanchor_busy_card``
        (which always sends ``disable_notification=True``) — this send
        IS the turn's one and only user-facing message, so it must ping
        normally, unlike a re-anchor that's always followed by a
        separately-notified answer.
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
        # Order: timeline → status line → separator → result line → answer.
        # The status line therefore sits MID-message here, and deliberately
        # so (review rev-iter1-002): this is the settled delivery, and what
        # should be on screen when it lands is the ANSWER, not a status
        # that has stopped changing. "Status last" exists for the LIVE
        # card, where the status is the only thing still moving. Each
        # section keeps exactly the line it would carry unmerged — the
        # card's status line at its end, the answer's result line at its
        # start ("session-name-on-every-message") — so the merged shape
        # reads the same as the two-message one.
        combined = (
            f"{card_md}\n\n{_MERGED_SEPARATOR}\n\n"
            f"{_result_line_md(sess.label)}\n\n{answer}"
            if answer else card_md
        )
        # The held copy (a mute below) never carries the ⏳ line: it goes
        # out late, and a line frozen into it could not be settled.
        held = combined
        if agents:
            combined = f"{combined}\n\n{agents['line']}"
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
        if send_as_new:
            try:
                sent = await send_rich_message(
                    chat_id, combined, is_rtl=is_rtl,
                    reply_to_message_id=reply_to,
                )
            except RichMessageBlocked:
                log.warning("[%s] merged: sendRichMessage blocked", sess.label)
                return False
            except RichMessageFloodBanned:
                # HELD, not dropped (8.29 R6). The chat is flood-muted, so
                # this attempt and the caller's replace-style fallback are
                # both refused — but the answer is still worth reading once
                # the ban lifts. `return False` is unchanged, so the
                # caller's control flow is exactly as before.
                self._hold_answer(sess, held, held, reply_to)
                return False
            except (RichMessageFallbackRequired, Exception):
                log.debug("[%s] merged: send-as-new failed", sess.label,
                          exc_info=True)
                return False
            if isinstance(sent, dict) and sent.get("message_id"):
                sess.busy_msg_id = sent["message_id"]
                sess.busy_card_trigger = reply_to
                self.registry.track_message(
                    sent["message_id"], sess.name, chat_id or 0,
                )
                if agents:
                    self._remember_agents_line(
                        sess, chat_id, sent["message_id"], combined,
                        agents, is_rtl)
                return True
            return False
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
        except RichMessageFloodBanned:
            # HELD, not dropped (8.29 R6) — see the send-as-new arm above.
            self._hold_answer(sess, held, held, reply_to)
            return False
        if result is not None and agents:
            self._remember_agents_line(
                sess, chat_id, int(sess.busy_msg_id), combined, agents,
                is_rtl)
        return result is not None

    # ── roadmap 8.41: background agents still running ─────────────────────

    def _agents_snapshot(self, sess: TrackedSession) -> dict | None:
        """The agents running as an answer goes out — ONE snapshot, taken
        once, so the line shown, the ids it waits for and the labels the ✅
        repeats all describe the same agents — or ``None`` with none."""
        labels = sess.bg_agent_labels()
        if not labels:
            return None
        return {"line": _agents_line.running_line(labels, markdown=True),
                "ids": list(sess.bg_agents), "labels": labels}

    def _remember_agents_line(
        self, sess: TrackedSession, chat_id: int, msg_id: int, text: str,
        agents: dict, is_rtl: bool,
    ) -> None:
        """An answer carrying the ⏳ line landed as *msg_id*: keep exactly
        what went out, so the ✅ edit rebuilds it rather than re-rendering."""
        sess.add_agents_line({
            "chat_id": chat_id, "msg_id": msg_id, "text": text,
            "line": agents["line"], "ids": list(agents["ids"]),
            "labels": list(agents["labels"]), "is_rtl": is_rtl,
            "sent_wall": time.time(), "attempts": 0, "next_try": 0.0,
            "gone_at": 0.0,
        })

    async def _settle_agents_lines(self, sess: TrackedSession,
                                   now: float) -> None:
        """Edit each due answer's ⏳ line to ✅, once (roadmap 8.41 R3).

        An ORNAMENT, skip-class edit: the flood gate may refuse it, never
        queue an answer behind it. Each answer gets two attempts at most:
        a refused or muted one (a muted try makes no call) is followed by
        ONE more — after the mute lifts when there is one — and then the
        line is left as sent: a courtesy, never worth more pressure. A
        deleted message (or a blocked bot) ends it at once. A gone session
        has none to settle: the GONE transition clears them
        (SessionRegistry.transition), and /kill drops the whole entry.
        """
        for rec in sess.agents_lines_due(now):
            if await self._settle_agents_line(sess, rec, now):
                sess.agents_lines = [
                    r for r in sess.agents_lines if r is not rec]

    async def _settle_agents_line(self, sess: TrackedSession, rec: dict,
                                  now: float) -> bool:
        """One attempt at one answer's ✅ edit. True when that answer is
        finished with (settled, gone, or given up)."""
        done = _agents_line.done_line(
            rec["labels"], time.time() - rec["sent_wall"], markdown=True)
        text = rec["text"][: -len(rec["line"])] + done
        rec["attempts"] += 1
        chat_id = rec["chat_id"]
        try:
            result = await edit_message_text_rich(
                chat_id, rec["msg_id"], text, is_rtl=rec["is_rtl"],
                kind="skip", priority=PRIORITY_ORNAMENT,
            )
        except (RichMessageGone, RichMessageBlocked):
            log.info("[%s] agents line: the answer is gone — not edited",
                     sess.label)
            return True
        except (FloodSkipped, FloodMuted):
            # RichMessageFloodBanned is a FloodMuted: the chat is muted.
            result = None
        except Exception:
            log.debug("[%s] agents line edit failed", sess.label,
                      exc_info=True)
            result = None
        if result is not None:
            log.info("[%s] agents line settled: %s", sess.label, done)
            return True
        if rec["attempts"] >= 2:
            log.info("[%s] agents line left as sent — the edit was refused "
                     "twice", sess.label)
            return True
        wait = BG_AGENTS_RETRY_SECONDS
        if MUTE.is_muted(chat_id):
            wait = max(wait, MUTE.remaining(chat_id) + 1.0)
        rec["next_try"] = now + wait
        return False

    def _delete_card_later(self, sess: TrackedSession, msg_id: int) -> None:
        """Delete a finished turn's tool-less busy card once its answer is
        out (roadmap 8.32 R1). Never raises, never waits.

        Card housekeeping, so an ORNAMENT, like the re-anchor's own delete:
        leaving a stale card behind is cosmetic, taking an answer's token to
        remove it is not. A background task because a blocking ornament
        waits for the chat's reserve, and nothing after the answer — the
        attachment, the next queued prompt — should wait with it.

        Skipped outright while the chat is flood-muted: the gate would
        refuse it anyway, and a refused call is still counted against the
        chat. In minimal mode the limiter refuses it (ornaments are
        suspended) and the card stays as last rendered — the paused line —
        which is what that card showed before this change too.
        """
        if not self._app or MUTE.is_muted(resolve_chat_id(sess)):
            return
        bot = self._app.bot
        chat_id = resolve_chat_id(sess)
        label = sess.label

        async def _delete() -> None:
            try:
                await bot.delete_message(
                    chat_id=chat_id, message_id=msg_id,
                    rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
                )
            except Exception:
                log.debug("[%s] tool-less card delete failed (left behind)",
                          label, exc_info=True)

        task = asyncio.create_task(_delete())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    async def _apply_consumption(
        self, sess: TrackedSession, consumed: list[dict],
    ) -> None:
        """R2 (design.md "turn anchor follows consumption"): a message
        was actually consumed by Claude — picked up as the next turn, or
        absorbed mid-turn. Reacts 👍 on each consumed message, tracks it
        so a later reply routes back here, and moves the reply target to
        the LAST consumed message.

        ``consumed`` is either the hook's minimal wire shape
        (``notify_hook._note_wire``: ``msg_id``/``chat_id``/``raw_text``)
        or a full on-disk note dict
        (:func:`aipager.policy_snapshot.consume_notes_matching`'s
        return) — only those three keys are read here, so both shapes
        work unchanged.

        Does NOT delete notes — deletion already happened upstream (the
        hook's ``_match_and_promote`` for pick-up;
        ``consume_notes_matching`` for absorption) before this function
        ever sees the list.
        """
        if not consumed:
            return
        self._track_consumed(sess, consumed)
        await reactions.mark_all(self, consumed, reactions.TAKEN,
                                 resolve_chat_id(sess))
        last = consumed[-1]
        last_msg_id = last.get("msg_id")
        if last_msg_id is not None:
            sess.trigger_msg_id = last_msg_id
            sess.last_prompt = last.get("raw_text", "") or ""
            self.registry.mark_dirty()

    def _track_consumed(
        self, sess: TrackedSession, notes: list[dict],
    ) -> None:
        """``track_message`` for each message the hook's pick-up named,
        so a later reply to it routes back here. Shared by
        ``_apply_consumption`` (a message Claude took) and the
        queued-while-busy branch of the ``queue_pickup`` handler (a
        message Claude only QUEUED — its fate is not known yet, so it
        gets no 👍 there; see :mod:`aipager.bot.reactions`)."""
        default_chat_id = resolve_chat_id(sess)
        for note in notes:
            note_msg_id = note.get("msg_id")
            if note_msg_id is None:
                continue
            note_chat_id = note.get("chat_id") or default_chat_id
            try:
                self.registry.track_message(
                    note_msg_id, sess.name, note_chat_id or 0,
                )
            except Exception:
                log.debug("consumption track_message failed", exc_info=True)

    async def _mark_not_delivered(
        self, sess: TrackedSession, entries: list[dict],
    ) -> None:
        """🤷 on each message that will never be taken — the moment its
        fate is known (queue cleared, session gone, input refused).
        ``entries`` are ``{msg_id, chat_id?}`` dicts; see
        :func:`aipager.bot.reactions.held_entries` for held messages.

        At most the newest :data:`aipager.bot.reactions.BULK_CAP` are
        marked (a teardown can name 50+ held messages)."""
        await reactions.mark_all(self, entries, reactions.NOT_DELIVERED,
                                 resolve_chat_id(sess),
                                 cap=reactions.BULK_CAP)

    async def _mark_ran_commands(self, sess: TrackedSession) -> None:
        """👌 on every slash command still outstanding when a turn ends —
        any turn end: the normal finish, a background job's interim Stop,
        an API error. Claude Code runs its queue as soon as a run ends,
        and a local command (``/model``) fires no hook when it runs —
        nothing else would ever move it off 👀. A prompt-type command
        queued the same way was named by its pick-up and is a queued
        target instead. (A teardown before the turn ends drops it: 🤷.)"""
        from aipager.policy_snapshot import list_outstanding_notes
        ran = [n for n in list_outstanding_notes(sess.name)
               if reactions.is_slash_command(n.get("raw_text"))]
        await reactions.mark_all(self, ran, reactions.ACK,
                                 resolve_chat_id(sess))

    async def _start_queued_turn(self, sess: TrackedSession) -> None:
        """Start the turn for the oldest message Claude queued during the
        turn that just ended and never absorbed: Claude Code pops its
        queue the moment a run ends and fires NO hook for the prompt it
        submits, so that turn would otherwise have no card, no reply
        target and — its Stop landing inside the IDLE debounce — no
        delivered answer ("anchor-on-transcript-consumption", R8).

        Accepted residual risk (review rev-iter1-005): if the operator
        clears Claude's queue from the terminal (Escape) in the fraction
        of a second between this turn's Stop and the pop, this card
        shows a turn that never runs. No hook can confirm the pop; the
        card sits until /stop, /kill or the stale-BUSY warning.
        """
        nxt = sess.queued_targets.pop(0)
        # Claude popped it as this turn's prompt: taken (R2).
        await reactions.mark_all(self, [nxt], reactions.TAKEN,
                                 resolve_chat_id(sess))
        sess.trigger_msg_id = nxt.get("msg_id")
        sess.last_prompt = nxt.get("raw_text") or ""
        self.registry.mark_dirty()
        self.registry.transition(sess.name, Status.BUSY)
        await self._send_busy_and_animate(sess)
        log.info("[%s] next turn started for queued message %s",
                 sess.label, nxt.get("msg_id"))

    async def _settle_queued_targets(
        self, sess: TrackedSession, *, reanchor: bool = True,
    ) -> list[dict]:
        """The messages Claude Code really still holds in its queue, for a
        teardown (``/stop``, ``/clearqueue``, ``/kill``, a session ending)
        about to discard them — or ``[]`` when that cannot be known.

        ``sess.queued_targets`` only loses an absorbed message when the
        transcript scan reads its ``absorbed_mid_turn`` line. So first run
        that scan (an absorption since the last tick is applied — 👍 and,
        with ``reanchor``, the card follows it). Then trust the list only
        when the scan is live (``_exact_anchors_available``) and no
        background job is waiting: without the hook the scan never runs,
        and in a job's waiting window the targets are kept on purpose
        while Claude Code has already started the next prompt. An
        untrusted target gets no 🤷 and no discard keystroke — an Escape
        into an empty Claude Code queue interrupts the running turn."""
        if not sess.queued_targets:
            return []
        if not _exact_anchors_available(sess) or sess.job_background_open():
            return []
        _sync_anchors_from_transcript(sess)
        if reanchor:
            await self._consume_and_reanchor(sess)
        else:
            consumed = sess.stream_consumed_notes
            sess.stream_consumed_notes = []
            if consumed:
                await self._apply_consumption(sess, consumed)
        return list(sess.queued_targets)

    async def _consume_and_reanchor(self, sess: TrackedSession) -> None:
        """Drain ``sess.stream_consumed_notes`` (staged by
        ``_sync_anchors_from_transcript``'s absorption detection) and, if
        it moved the reply target off a still-live card, re-anchor that
        card (R3). Called from two sites: ``animation._animate_tick``
        (every tick, waiting or not) and the ``assistant_text`` notify
        handler — the whole of R3 for the "live" case.

        Draining the WHOLE batch before making exactly one ``if``-gated
        re-anchor call is what makes "at most one re-anchor per
        detection batch" (design.md B3) structural, never one per
        queue-operation line.

        The mismatch check below is a bare ``!=``, deliberately with no
        ``busy_card_trigger is not None`` carve-out (review
        rev-iter1-002): every production call site that establishes a
        live card now records ``busy_card_trigger`` alongside it, so a
        missing record is never silent — it is a real (if narrow, restart
        or hand-built-session) case, and the fix belongs at the site that
        forgot to seed it, not in a guard here that would also swallow a
        genuine post-compaction absorption.
        """
        consumed = sess.stream_consumed_notes
        if not consumed:
            return
        sess.stream_consumed_notes = []
        await self._apply_consumption(sess, consumed)
        if (sess.busy_msg_id and sess.busy_msg_id > 0
                and sess.busy_card_trigger != sess.trigger_msg_id):
            await self._reanchor_busy_card(sess, sess.trigger_msg_id, final=False)

    @staticmethod
    def _diff_preview_enabled(sess) -> bool:
        """The resolved "Diff previews" preference for ``sess``: the
        scope's /settings value with this session's own override applied
        (``resolve_preferences``, never ``get_preferences``, so a
        per-session choice actually takes effect). Off by default.
        """
        return preferences.resolve_preferences(
            sess.scope_chat_id or 0, sess.preference_overrides(),
        ).diff_preview

    def _hold_answer(self, sess: TrackedSession, rich_text: str,
                     plain_text: str, reply_to: int | None, *,
                     digests: list[str] | None = None,
                     selected_wall: float = 0.0) -> None:
        """Keep an answer a flood mute refused (8.29 R6). Never raises.

        ``digests`` / ``selected_wall`` are the finish path's pending
        delivery record (roadmap 8.39): ``flush_held_answers`` confirms
        them once the held answer finally lands, so a restart after that
        does not re-post it.

        One INFO where "not delivered, no fallback" used to be — the
        operator is told the answer is coming, not that it is gone.

        Never raises, because every call site is inside a turn that must
        finish: a failure to BUFFER an answer must not also abort the rest
        of the turn.
        """
        try:
            from aipager.bot.held import HELD

            HELD.hold(
                chat_id=resolve_chat_id_int(sess) or resolve_chat_id(sess),
                session=sess.name, label=sess.label,
                rich_text=rich_text, plain_text=plain_text,
                reply_to=reply_to, digests=tuple(digests or ()),
                selected_wall=selected_wall,
            )
            log.info(
                "[%s] answer held — the chat is flood-muted; it will be "
                "delivered when the mute lifts", sess.label,
            )
        except Exception:  # pragma: no cover - defensive
            log.warning("[%s] could not hold the refused answer", sess.label,
                        exc_info=True)

    async def _deliver_held_as_plain_text(self, entry, marker: str) -> bool:
        """Last resort for one held answer: the stored plain text.

        Returns whether ANYTHING landed. Chunked at markdown-safe
        boundaries by the same helper the live answer path uses, with no
        ``parse_mode``, so Telegram cannot refuse it for parsing — which
        is the most likely way a rich delivery fails at this point, the
        chat having just proved it accepts sends.

        NEVER called on a mute (`RichMessageFloodBanned` is handled above
        and returns): a second attempt into a ban is a fresh violation,
        and that rule is untouched by this fallback.
        """
        bot = self._app.bot if self._app else None
        if bot is None or not entry.plain_text:
            return False
        landed = False
        for index, chunk in enumerate(_plain_text_chunks(
                f"{marker}\n\n{entry.plain_text}")):
            try:
                await bot.send_message(
                    entry.chat_id, chunk,
                    reply_to_message_id=entry.reply_to if index == 0 else None,
                )
                landed = True
            except Exception:
                log.warning("[%s] held answer: plain-text chunk failed",
                            entry.label, exc_info=True)
        return landed

    def _confirm_held(self, sess: TrackedSession, entry) -> None:
        """A held answer landed: its digests become persistable and the
        delivery stamp moves (roadmap 8.39) — the finish path left both
        pending when the mute refused it."""
        if entry.digests:
            sess.confirm_delivered(entry.digests, entry.selected_wall)
            self.registry.mark_dirty()

    async def flush_held_answers(self, sess: TrackedSession) -> int:
        """Deliver everything held for this session's chat. Returns how
        many went out.

        ``0`` while the chat is still muted or with nothing held — the
        cheap early return, because the session monitor calls this on its
        2 s tick for every session that has something held.

        Each delivery is a BLOCKING, ESSENTIAL ``send_rich_message`` with
        the late marker as its first line, oldest first — two turns of one
        session inside one ban are two entries (8.29 T4) and they read as
        nonsense the other way round.

        An entry whose rich delivery fails for a reason OTHER than the
        mute keeps its place and counts an attempt. On the LAST attempt —
        the point at which it used to be dropped and lost — the stored
        plain text is sent instead (8.29 ruling 7). That is what
        ``HeldAnswer.plain_text`` is for: the chat is demonstrably not
        muted (the check above passed and the rich attempt reached
        Telegram), so a plain send is not a request into a ban, it is the
        same "never lose a reply" net every other answer path already has.
        An answer that survives a 9.5-hour ban and is then lost to a
        markdown parse error is the exact failure this buffer exists to
        prevent, arriving hours later.

        If even that fails the entry is dropped with one WARNING naming
        the session and its size — not silently, which is the whole point
        of R6.
        """
        from aipager.bot.held import HELD, HELD_ANSWER_MAX_ATTEMPTS, late_marker

        chat_id = resolve_chat_id_int(sess) or resolve_chat_id(sess)
        if not chat_id or MUTE.is_muted(chat_id):
            return 0
        entries = [e for e in HELD.pending(chat_id) if e.session == sess.name]
        if not entries:
            return 0

        delivered = 0
        for entry in entries:
            marker = late_marker(entry.held_seconds())
            try:
                sent = await send_rich_message(
                    int(chat_id), f"{marker}\n\n{entry.rich_text}",
                    is_rtl=detect_rtl(entry.rich_text),
                    reply_to_message_id=entry.reply_to,
                )
            except RichMessageFloodBanned:
                # Re-muted between the check above and the send. Keep it:
                # this is not a failed delivery, it is a postponed one,
                # and it must not count against the attempt budget.
                return delivered
            except Exception:
                entry.attempts += 1
                if entry.attempts < HELD_ANSWER_MAX_ATTEMPTS:
                    log.debug("[%s] held answer delivery failed (attempt %d)",
                              entry.label, entry.attempts, exc_info=True)
                    continue
                # OUT OF RICH ATTEMPTS — the point at which this answer
                # used to be dropped and lost. The plain text has been
                # stored beside it the whole time; spend it here rather
                # than losing an answer that survived a 9.5-hour ban to a
                # markdown parse error hours later (8.29 ruling 7).
                #
                # At the cap rather than on the first failure, deliberately:
                # a transient failure deserves another RICH try, and the
                # fallback is for the loss, not for the failure.
                log.warning(
                    "[%s] held answer: %d rich deliveries failed — falling "
                    "back to the stored plain text",
                    entry.label, entry.attempts, exc_info=True,
                )
                HELD.drop(entry.chat_id, entry.key)
                if await self._deliver_held_as_plain_text(entry, marker):
                    delivered += 1
                    self._confirm_held(sess, entry)
                    log.info(
                        "[%s] held answer delivered as plain text after %d "
                        "failed rich attempts", entry.label, entry.attempts,
                    )
                else:
                    log.warning(
                        "[%s] held answer dropped after %d failed delivery "
                        "attempts, plain text included (%d chars) — it is lost",
                        entry.label, entry.attempts, len(entry.rich_text),
                    )
                continue
            HELD.drop(entry.chat_id, entry.key)
            delivered += 1
            self._confirm_held(sess, entry)
            if isinstance(sent, dict) and sent.get("message_id"):
                self.registry.track_message(
                    sent["message_id"], sess.name,
                    resolve_chat_id_int(sess) or 0,
                )
        if delivered:
            log.info("[%s] delivered %d held answer(s) after the mute lifted",
                     sess.label, delivered)
        return delivered

    async def _deliver_job_interim(
        self, sess: TrackedSession, content: str, turn_end_wall: float,
    ) -> None:
        """Send a background job's interim answer NOW, as its own message
        (roadmap 8.42, operator decision 2026-09-24).

        Until 8.42 an interim answer was held in a buffer and merged into
        the job's final message, so for the minutes an agent ran the
        terminal showed Claude's answer and Telegram showed only the
        waiting card. Now it goes out like any answer: the result line,
        the text, threaded to the prompt, notifying — and ending with
        8.41's "⏳ N agents still running" line, which that feature settles
        to ✅ once the agents it named have stopped.

        The waiting card is NOT touched: it stays the job's live status
        and carries its Stop button, in every layout — merged and replace
        included, where a normal finish would consume the card.

        Exactly once: the digest of the bare text goes into the delivered
        ring (8.39) before the send, pending, and is confirmed — persisted,
        with the delivery stamp moved to this turn's end — only once the
        send landed. A stray idle event re-running this with the same text
        is refused here; the job's continuation reading this text back from
        the transcript is refused by the finish path's check of the same
        ring, and a restarted daemon's idle Notification by the hook
        receiver's (ring + delivery stamp) — this confirm is what feeds
        both.

        A flood mute HOLDS the answer (8.29 R6) without the ⏳ line: a held
        copy goes out late, and a line frozen into it could not be settled.
        Neither does the plain-text fallback carry it (it is chunked).
        """
        if not content:
            return
        label = sess.label
        digest = hashlib.md5(content.encode("utf-8")).hexdigest()
        if sess.was_delivered(digest):
            log.info("[%s] job interim answer already delivered — not "
                     "re-sending", label)
            return
        sess.remember_delivered(digest, pending=True)
        agents = self._agents_snapshot(sess)
        lead_md = _result_line_md(label)
        lead_plain = _result_line_plain(label)
        # The same byte budget as the finish path: an answer over the
        # ceiling keeps its head at a markdown-safe cut, with the ⏳ line
        # still last, and the full text follows as an attachment.
        body_limit = _RICH_LIMIT - len(f"{lead_md}\n\n".encode("utf-8"))
        if agents:
            body_limit -= len(f"\n\n{agents['line']}".encode("utf-8"))
        body = content
        overflow = len(content.encode("utf-8")) > body_limit
        if overflow:
            cut = 0
            for b in _md_safe_boundaries(content):
                if len(content[:b].encode("utf-8")) <= body_limit:
                    cut = b
            body = (content[:cut] if cut else content.encode("utf-8")[
                :body_limit].decode("utf-8", errors="ignore"))
        held_rich = f"{lead_md}\n\n{body}"
        rich_text = held_rich
        if agents:
            rich_text = f"{rich_text}\n\n{agents['line']}"
        plain_text = f"{lead_plain}\n\n{content}"
        is_rtl = detect_rtl(content)
        chat_id = resolve_chat_id_int(sess)
        reply_to = sess.trigger_msg_id
        landed = False
        msg_id = 0
        sent = None
        try:
            if chat_id is None:
                # An unscoped session on an install with no CHAT_ID: no
                # numeric destination for the rich API — straight to the
                # plain-text fallback, which addresses the chat as it can.
                raise RichMessageFallbackRequired("no numeric chat id resolved")
            sent = await send_rich_message(
                chat_id, rich_text, is_rtl=is_rtl,
                reply_to_message_id=reply_to,
            )
            landed = True
        except RichMessageBlocked:
            _log_blocked_once(Exception("sendRichMessage 403"))
            return
        except RichMessageFloodBanned:
            # HELD (8.29 R6); confirmed by flush_held_answers once it lands.
            self._hold_answer(sess, held_rich, plain_text, reply_to,
                              digests=[digest], selected_wall=turn_end_wall)
            return
        except (RichMessageFallbackRequired, Exception):
            log.warning("[%s] job interim sendRichMessage failed — falling "
                        "back to plain text", label, exc_info=True)
            overflow = False  # the plain chunks carry the whole text
            for chunk in _plain_text_chunks(plain_text):
                try:
                    fallback = await self._app.bot.send_message(
                        resolve_chat_id(sess), chunk,
                        reply_to_message_id=reply_to if not landed else None,
                    )
                    landed = True
                    self.registry.track_message(
                        fallback.message_id, sess.name,
                        resolve_chat_id_int(sess) or 0,
                    )
                except Exception:
                    log.warning("[%s] job interim plain-text chunk failed",
                                label, exc_info=True)
        if not landed:
            return
        # Bookkeeping only once the send is settled, outside the try: a
        # failure here must never fall into the plain-text fallback and
        # send the same answer a second time.
        sess.confirm_delivered([digest], turn_end_wall)
        self.registry.mark_dirty()
        if isinstance(sent, dict) and sent.get("message_id"):
            msg_id = sent["message_id"]
            self.registry.track_message(msg_id, sess.name, chat_id or 0)
            if agents:
                self._remember_agents_line(
                    sess, chat_id, msg_id, rich_text, agents, is_rtl)
        log.info("[%s] job interim answer delivered (%d chars, agents=%d)",
                 label, len(content), len(agents["ids"]) if agents else 0)
        if overflow:
            # The rich send just landed, so the chat is not muted; a text
            # a hook carried is far below Telegram's document limit.
            try:
                tmp = Path(tempfile.mktemp(suffix=".txt", prefix=f"{label}_"))
                tmp.write_text(content, encoding="utf-8")
                with open(tmp, "rb") as f:
                    await self._app.bot.send_document(
                        resolve_chat_id(sess), document=f,
                        filename=f"{label}_answer.txt",
                        reply_to_message_id=msg_id or None,
                    )
                tmp.unlink(missing_ok=True)
            except Exception:
                log.warning("[%s] job interim attachment failed", label,
                            exc_info=True)

    async def _handle_job_interim(
        self, sess: TrackedSession, context: dict, turn_end_wall: float,
    ) -> None:
        """The ``idle_prompt`` path when ``sess.job_background_open()`` is
        True — an interim Stop/Notification/StopFailure while a background
        agent this job launched is still running (design.md "model Claude
        Code background-agent jobs", requirement 1).

        Never produces a "Finished" card: re-renders the live card to the
        waiting frame in place (rather than waiting for the animator's next
        natural tick), sends Claude's interim answer now (roadmap 8.42 —
        :meth:`_deliver_job_interim`), and drains one queued prompt if any
        is waiting — the exact same :meth:`_drain_next_queued` the real
        Finished path uses, so a message queued during the wait is not
        stranded until the job's eventual real end.
        """
        sess.job_interim_seen = True
        raw_md = context.get("raw_md", "")
        # Only what THIS interim turn produced — never sess.summary, which
        # is the previous answer (see the idle branch's content selection).
        content = raw_md or context.get("summary", "") or ""
        # The answer now goes out as its own message (roadmap 8.42), so
        # the card must not quote it too (review rev-iter1-001). Read the
        # transcript fallback's text now (a no-op while the MessageDisplay
        # hook is live) and trim the trailing commentary that is just this
        # answer: otherwise the continuation turn's first tick would read
        # it onto the card, and the job's finished card would quote it.
        # The trim compares text, so where anchors place the prose does
        # not matter to it.
        _read_stream_text(sess)
        _drop_answer_tail(sess, content)
        if sess.busy_msg_id and sess.busy_msg_id > 0:
            sess.stream_dirty = True
            # A STATE change (busy -> waiting): the gate lets it out at
            # once in a young turn, and in an old one takes the one
            # debounced bypass of the age decay (8.30 Q2). Refused, the
            # card is dirty and the animator's next due tick shows it.
            if self._card_edit_due(sess, time.monotonic(), base_gap=0.0):
                if await self._edit_busy_rich(
                    sess, "Working", waiting=True,
                ) is None:
                    self._stop_animation(sess)
        await self._deliver_job_interim(sess, content, turn_end_wall)
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
        # A drain at an idle moment starts the turn and owns its target.
        # When a turn is already running (the finish path just started
        # one for a message Claude had queued — R8), the drained message
        # is a mid-turn send instead: injected as usual, it becomes a
        # queued target through the hook's submit-time pick-up and must
        # not move the running turn's target or send a second card.
        starts_turn = sess.status != Status.BUSY
        if starts_turn:
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
        if ok and starts_turn:
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
        if ok:
            log.info("[%s] Flushed queued: %s", sess.label, queued_text[:80])
        else:
            # Popped and not sent: this held message is gone (R3).
            await self._mark_not_delivered(sess, [{"msg_id": queued_trigger}])

    async def notify(self, sess: TrackedSession, event: str, context: dict) -> None:
        """Send appropriate Telegram notification for a state change."""
        if not self._app:
            return

        # No pinned-bar refresh here (8.31). This ran on EVERY hook, and a
        # hook is not a state change: the bar is refreshed at transitions
        # and on the session monitor's tick instead (dashboard.py).

        bot = self._app.bot
        label = sess.label

        # ── The model changed (hook_receiver's statusline) ──
        # The bar no longer shows the model (8.31 R6): nothing to do.
        if event == "pinned_update":
            return

        # ── A mute lifted and this session has an answer waiting (8.29) ──
        # Dispatched by `SessionMonitor._scan` on the 2 s tick it already
        # runs, so a held answer lands within one tick of the ban lifting
        # with no busy-wait, no timer and no new task.
        if event == "held_answer_flush":
            await self.flush_held_answers(sess)
            return

        # ── the answer's ⏳ line is owed its ✅ (roadmap 8.41) ──
        # Dispatched by `SessionMonitor._scan` once every agent the line
        # named has been gone for the settle window.
        if event == "agents_line_done":
            await self._settle_agents_lines(
                sess, context.get("now") or time.monotonic())
            return

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
            # message that started this turn, never just the last. Expired notes keep
            # their 👀 (no reaction change — Claude may still process a
            # TTL-lapsed one after aipager stops watching) and get one
            # best-effort notice instead of per-message noise.
            #
            # R2 (design.md "turn anchor follows consumption"):
            # _apply_consumption also moves trigger_msg_id/last_prompt to
            # the LAST consumed message — hook_receiver.py's own
            # _on_datagram already does this too before calling here, so
            # this is a harmless re-affirmation for that path, and the
            # ONLY place it happens for a caller that reaches this event
            # some other way (e.g. a unit test driving notify() directly).
            consumed = context.get("consumed") or []
            expired = context.get("expired") or []
            default_chat_id = resolve_chat_id(sess)
            # Claude Code fires this pick-up at SUBMIT time for a message
            # typed while a turn runs — before its fate is known
            # (measured 2026-09-05, "anchor-on-transcript-consumption").
            # Such a message is "queued, fate unknown": it gets routing
            # but keeps its 👀 (Claude Code shows it grey), the reply
            # target does not move and no
            # card re-anchors until the transcript says the turn absorbed
            # it (_sync_anchors_from_transcript) — or, if it is still
            # queued when this turn ends, it becomes the next turn's
            # prompt (the finish path's _start_queued_turn). Either is
            # the moment it turns 👍.
            if sess.status == Status.BUSY:
                queued = [n for n in consumed
                          if n.get("msg_id") is not None
                          and n.get("msg_id") != sess.trigger_msg_id]
            else:
                queued = []
            own = [n for n in consumed if n not in queued]
            if queued:
                self._track_consumed(sess, queued)
                sess.queued_targets.extend({
                    "msg_id": n.get("msg_id"),
                    "chat_id": n.get("chat_id"),
                    "raw_text": n.get("raw_text", "") or "",
                } for n in queued)
                log.info("[%s] queued while busy: %s", label,
                         [n.get("msg_id") for n in queued])
            await self._apply_consumption(sess, own)
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
            #
            # A turn Claude woke ITSELF for (hook_receiver's fresh
            # <task-notification> path, context["self_woken"]) defers its
            # card until it is earned — the first tool use or
            # SELF_WOKEN_CARD_DELAY (roadmap 8.32). A human prompt, from
            # Telegram or the terminal, keeps the immediate card.
            if context.get("self_woken"):
                await self._send_busy_and_animate(sess, lazy=True)
            else:
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
                # A state change (waiting -> busy): same gate and same
                # single bypass as the interim above (8.30 Q2).
                if self._card_edit_due(sess, time.monotonic(), base_gap=0.0):
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
            # Two forms of the same line ("session-name-on-every-message"):
            # the busy card settles to ITS glyph (✅ — the card's terminal
            # state), while a standalone notice, sent because no card is
            # left to settle, is the turn's result message and opens with
            # the result glyph like the idle path's standalone header.
            name = html_mod.escape(label)
            text = f"✅ <b>{name}</b> · Finished{suffix}"
            text_alone = f"{_RESULT_GLYPH} <b>{name}</b> · Finished{suffix}"
            target_msg_id = sess.busy_msg_id
            if target_msg_id and target_msg_id > 0:
                await self._edit_busy_raw(
                    target_msg_id, text, chat_id=resolve_chat_id(sess),
                )
                sess.busy_msg_id = None
            else:
                text = text_alone
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
            agent_id = context.get("agent_id", "")
            # Update tool history — mark previous as done, append new
            if tool_summary:
                # A tool call made INSIDE a subagent (agent_id matches a
                # LIVE active_subagents entry) is attributed to that
                # agent's own row instead of the parent's tool_history
                # ("agent activity rows on the busy card") — folds an
                # agent's tool calls under its row rather than flooding
                # the parent's timeline. Empty, unknown, or a
                # stopped/evicted agent_id falls through to exactly
                # today's behaviour — a tool event is never dropped.
                if agent_id and agent_id in sess.active_subagents:
                    sess.record_agent_tool(agent_id, tool_summary)
                else:
                    # Append new tool as in-progress (PostToolUse marks it done)
                    sess.record_tool(tool_summary, False)
                sess.last_tool_summary = tool_summary
            # A self-woken turn's deferred card is earned by its first
            # tool use (roadmap 8.32). Sent after the row is recorded, so
            # the card's first animation tick already carries it — no
            # edit straight after the send (below), which would be a
            # second call for the same row.
            card_just_sent = False
            if sess.lazy_card_at:
                card_before = sess.busy_msg_id
                await self._send_lazy_card(sess, reason="first tool use")
                card_just_sent = bool(
                    sess.busy_msg_id and sess.busy_msg_id > 0
                    and sess.busy_msg_id != card_before
                )
            # Item 4.4: a separate diff-preview message for Write/Edit —
            # OFF by default, on via the /settings "Diff previews" toggle
            # (the scope value, or this session's own override). Resolved
            # per event, never cached, so a toggle flipped mid-turn takes
            # effect on the next edit. Fire-and-forget so it doesn't slow
            # the busy-message edit cadence.
            if (tool_name in ("Write", "Edit") and tool_input_full
                    and self._diff_preview_enabled(sess)):
                asyncio.create_task(
                    self._send_diff_preview(sess, tool_name, tool_input_full)
                )
            # Skip edit if busy msg not ready yet (animation will pick up cached stats)
            if (not sess.busy_msg_id or sess.busy_msg_id < 0 or not tool_summary
                    or card_just_sent):
                return
            sess.stream_dirty = True
            now = time.monotonic()
            # Every hook-driven edit goes through the card's one gate
            # (8.30 Q3): the 1.2 s debounce in a young turn, the turn-age
            # tier in an old one. A tool row is not a state change, so it
            # never bypasses the tier — it rides the next due edit.
            if self._card_edit_due(sess, now, base_gap=STREAM_EDIT_INTERVAL):
                if await self._edit_busy_rich(sess, "Working") is None:
                    self._stop_animation(sess)
                    return
            # A permission answered in the terminal reaches BUSY through
            # this event (hook_receiver's PreToolUse transition), with the
            # animation still stopped from the prompt. Bring it back so the
            # card keeps ticking between hooks rather than only on them.
            self._resume_animation_if_dead(sess, reason="tool_use while BUSY")
            return

        if event == "waiting_reminder":
            # Claude Code nudged about idle input while this session is
            # blocked on a permission prompt or a question (roadmap 8.4).
            # The state is right — INTERACTIVE — and stays; the chat gets
            # one line pointing at the prompt, threaded under the inline
            # card when there is one. No keyboard: the buttons are on the
            # prompt itself.
            kind = context.get("kind")
            summary = context.get("summary")
            reminder = (f"⬆️ <b>{html_mod.escape(label)}</b> · still waiting "
                        "for your answer above")
            if summary:
                head = "Question" if kind == "question" else "Permission"
                reminder += f"\n{head}: {html_mod.escape(str(summary)[:120])}"
            reply_to = (sess.busy_msg_id
                        if sess.busy_msg_id and sess.busy_msg_id > 0 else None)
            try:
                await bot.send_message(
                    resolve_chat_id(sess), reminder, parse_mode="HTML",
                    reply_to_message_id=reply_to,
                )
            except Exception:
                log.debug("[%s] waiting reminder failed", label, exc_info=True)
            if self.observers:
                asyncio.create_task(self.observers.broadcast(reminder))
            return

        if event == "prompt_not_taken":
            # The session monitor saw a turn-starting Telegram send go
            # PROMPT_HOOK_GRACE_SECONDS without any hook (roadmap 8.11):
            # Claude Code refused the input outright — an unknown slash
            # command, a built-in that only opens a dialog — and printed
            # its error in the terminal where nobody in chat can see it.
            # Modelled on _stop_session_core: settle the card into a
            # warning, drop the message's own note (it would otherwise
            # be matched by a later pick-up), and put the session back
            # to IDLE directly — no idle notification, there was no turn.
            # If the hook is merely late, UserPromptSubmit flips the
            # session BUSY again and sends a fresh card on its own.
            grace = float(context.get("grace", 0.0))
            msg_ref = context.get("msg") or sess.prompt_sent_msg
            msg_id, msg_chat = msg_ref if msg_ref else (None, None)
            log.warning(
                "[%s] prompt not taken by Claude Code: no hook within %.0fs "
                "of the send (msg_id=%s)", label, grace, msg_id,
            )
            self._stop_animation(sess)
            warn_text = (
                f"⚠️ <b>{html_mod.escape(label)}</b> · Not taken by Claude Code\n"
                "\n"
                f"No hook arrived within {grace:.0f} s of the send. A message "
                "that starts with \"/\" is read as a slash command — check "
                "the terminal."
            )
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                await self._edit_busy_raw(
                    sess.busy_msg_id, warn_text, chat_id=resolve_chat_id(sess),
                )
            else:
                try:
                    await bot.send_message(
                        resolve_chat_id(sess), warn_text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                except Exception:
                    log.debug("[%s] prompt_not_taken notice failed", label,
                              exc_info=True)
            if msg_id is not None:
                own: list[dict] = []
                try:
                    from aipager.policy_snapshot import (
                        delete_notes, list_outstanding_notes,
                    )
                    # Both halves, like track_message: a message id is
                    # only unique within its chat.
                    own = [n for n in list_outstanding_notes(sess.name)
                           if n.get("msg_id") == msg_id
                           and n.get("chat_id") == msg_chat]
                    if own:
                        delete_notes(sess.name, own)
                except Exception:
                    log.debug("[%s] note drop failed", label, exc_info=True)
                text = own[0].get("raw_text") if own else sess.last_prompt
                # Claude Code refused a prompt outright: never taken (R3),
                # and final — the note is gone, so even a merely late hook
                # cannot name it. A slash command is different: a
                # built-in that opens a dialog or runs locally fires no
                # hook either, so silence is not a refusal — 👌, never 🤷.
                await reactions.mark_all(
                    self, [{"msg_id": msg_id, "chat_id": msg_chat}],
                    reactions.ACK if reactions.is_slash_command(text)
                    else reactions.NOT_DELIVERED,
                    resolve_chat_id(sess))
            sess.busy_msg_id = None
            sess.status = Status.IDLE
            sess.trigger_msg_id = None
            sess.busy_card_trigger = None
            sess.prompt_sent_msg = None
            sess.last_idle_at = time.monotonic()
            self.registry.mark_dirty()
            if self.observers:
                asyncio.create_task(self.observers.broadcast(warn_text))
            # A message held behind the rejected one (the mixed-sender
            # hold in _hold_for_open_dialog parks it in pending_queue)
            # would otherwise sit there until some later turn's idle
            # moment drained it — the session is idle NOW, so give it
            # its turn the way the idle path does (review rev-iter1-002).
            # That send is a from-idle send and gets its own deadline.
            await self._drain_next_queued(sess)
            return

        if event == "busy_card_watchdog":
            # The session monitor found the live card frozen (no animate
            # task, or no successful edit in CARD_STALE_SECONDS) — see
            # session_monitor.busy_card_watchdog_action.
            await self._watchdog_busy_card(
                sess, str(context.get("action", "")),
                float(context.get("since", 0.0)),
            )
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
                # Settle every round the transcript has flushed BEFORE
                # placing this sentence ("transcript-exact-sentence-
                # anchors"): a silent message's rows then sit below the
                # floor, and a message whose own round beat its hook text
                # here (its last tool was quick, and the NEXT message's
                # first row may already have landed) has its exact anchor
                # waiting.
                _sync_anchors_from_transcript(sess)
                # design.md "turn anchor follows consumption" R3/R4: this
                # scan may have just staged an absorption/pick-up match —
                # drain it and re-anchor the live card before placing
                # THIS sentence, so a mid-turn re-anchor never straddles
                # the block this delta is about to append.
                await self._consume_and_reanchor(sess)
                sess.stream_msg_id = msg_id
                exact = sess.stream_exact_anchor.get(msg_id) if msg_id else None
                if exact is None:
                    # Unflushed round: the rows since the last block are
                    # this message's — the sentence goes above them, and
                    # everything known is now attributed, so the floor
                    # moves up. The batch has its sentence; the card can
                    # draw both.
                    anchor = sess.stream_anchor_floor
                    sess.stream_anchor_floor = len(sess.tool_history)
                    sess.stream_batch_since = None
                else:
                    # Flushed round: the scan above already put the floor
                    # past this message's rows and left any rows beyond it
                    # to the next message (batch untouched).
                    anchor = exact
                sess.stream_commentary.append((anchor, delta))
                if msg_id:
                    sess.stream_block_index[msg_id] = len(sess.stream_commentary) - 1
            if not sess.busy_msg_id or sess.busy_msg_id < 0:
                return
            sess.stream_dirty = True
            if self._card_edit_due(sess, time.monotonic(),
                                   base_gap=STREAM_EDIT_INTERVAL):
                if await self._edit_busy_rich(sess, "Working") is None:
                    self._stop_animation(sess)
            return

        if event in ("tool_done", "tool_failed"):
            # PostToolUse / PostToolUseFailure — mark tool as done or failed
            tool_summary = context.get("tool_summary", "")
            agent_id = context.get("agent_id", "")
            mark = "failed" if event == "tool_failed" else True
            # An attributed tool (agent_id matches a LIVE active_subagents
            # entry) never created a parent tool_history row to settle —
            # the tool_use handler routed it to record_agent_tool instead
            # ("agent activity rows on the busy card"). Skip the
            # parent-row search entirely so it can never mark an unrelated
            # in-flight row done via the "no exact match" fallback below.
            attributed = bool(agent_id and agent_id in sess.active_subagents)
            if tool_summary and not attributed:
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
                if self._card_edit_due(sess, time.monotonic(),
                                       base_gap=STREAM_EDIT_INTERVAL):
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
            # An agent is work too: a self-woken turn's deferred card goes
            # up now if its Agent call's own PreToolUse did not already
            # send it (roadmap 8.32).
            if sess.lazy_card_at:
                await self._send_lazy_card(sess, reason="subagent start")
            # Edit busy message if ready (debounced)
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                sess.stream_dirty = True
                if self._card_edit_due(sess, time.monotonic(),
                                       base_gap=STREAM_EDIT_INTERVAL):
                    if await self._edit_busy_rich(sess, "Working") is None:
                        self._stop_animation(sess)
            return

        if event == "subagent_stop":
            agent_type = context.get("agent_type", "agent")
            elapsed = context.get("elapsed", 0.0)
            history_idx = context.get("history_idx")
            tool_count = context.get("tool_count", 0)
            # Format elapsed time — floored to "0s" rather than "" below
            # 1s (was blank before this feature) so the settled row's
            # three-segment shape ("type · N tool calls · elapsed") is
            # always fixed ("agent activity rows on the busy card").
            if elapsed >= 60:
                elapsed_str = f"{int(elapsed) // 60}m {int(elapsed) % 60}s"
            else:
                elapsed_str = f"{int(elapsed)}s"
            plural = "" if tool_count == 1 else "s"
            done_summary = (
                f"\U0001f916 {agent_type} · {tool_count} tool call{plural}"
                f" · {elapsed_str}"
            )
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
                if self._card_edit_due(sess, time.monotonic(),
                                       base_gap=STREAM_EDIT_INTERVAL):
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
                    # review rev-iter1-002 (design.md "turn anchor follows
                    # consumption"): this bypasses send_busy, so it must
                    # record busy_card_trigger itself — otherwise this
                    # genuinely live, correctly-anchored card reads as
                    # "never seeded" against the very next re-anchor
                    # decision, and a real absorption after this point
                    # would go undetected.
                    sess.busy_card_trigger = sess.trigger_msg_id
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
                    # review rev-iter1-002: this message is already live
                    # and already reply-anchored to whatever trigger_msg_id
                    # was at the moment it was sent (either by the
                    # "compacting" branch's own fresh send above, or by
                    # this session's original send_busy before compaction
                    # started) — current trigger_msg_id is the best
                    # available record of that if it hasn't moved since,
                    # matching the same best-effort reasoning as the
                    # restart seed in state.py's load(). Without this a
                    # later absorption's mismatch check reads a stale/None
                    # busy_card_trigger against a genuinely live card and
                    # never re-anchors it.
                    sess.busy_card_trigger = sess.trigger_msg_id
            else:
                try:
                    msg = await bot.send_message(
                        resolve_chat_id(sess), text, parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                    sess.busy_msg_id = msg.message_id
                    sess.busy_card_trigger = sess.trigger_msg_id  # review rev-iter1-002
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
            # Session exited — clean up busy state and alert user.
            # A deferred card whose send is in flight lands first, under
            # the card lock, and is then deleted below like any card.
            async with sess.animate_lock:
                self._cancel_lazy_card(sess)
            # Claude Code's input queue dies with the process, restart
            # included (the relaunch starts empty): nothing still queued
            # there will be taken (R3). `/clear` and `/resume` also fire
            # SessionEnd, but the process lives on — leave its queue alone.
            if context.get("source") not in ("clear", "resume"):
                lost = await self._settle_queued_targets(sess, reanchor=False)
                sess.queued_targets.clear()
                # `notes`: what was still outstanding just before the GONE
                # transition deleted the notes dir (captured by the caller)
                # — a command or message Claude never got to run.
                await self._mark_not_delivered(
                    sess, list(context.get("notes") or []) + lost)
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
                "resume": "switched to another conversation",
                "logout": "logged out",
                "prompt_input_exit": "exited",
                "bypass_permissions_disabled": "permissions error",
                "disappeared": "crashed or killed",
                # Claude Code's default reason — a signal among others —
                # not evidence of a crash ("disappeared" is the one with
                # evidence: the socket vanished).
                "other": "exited",
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
            from aipager.bot.session_ops import KILL_NOTICE_QUIET_SECONDS
            ended_sid = context.get("session_id") or ""
            killed_sessions = self.registry.killed_sessions
            now = time.monotonic()
            for stale in [k for k, t in killed_sessions.items()
                          if now - t >= KILL_NOTICE_QUIET_SECONDS]:
                del killed_sessions[stale]
            if ended_sid and ended_sid in killed_sessions:
                # This very process was ended by aipager's own /kill moments
                # ago; the operator already has "💀 Killed" — a second
                # notice is noise.
                log.info("[%s] session_end right after /kill (%s) — not "
                         "alerting", label, source)
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
            # The delivery stamp's moment (roadmap 8.39 R2), taken on
            # arrival — before the awaited final card render, which the
            # outbound gate can pace by seconds. A next turn's answer
            # written during that render must not look older than it.
            turn_end_wall = time.time()
            # Every turn end, before the job-interim and API-error returns
            # below: Claude Code has just run whatever it had queued.
            await self._mark_ran_commands(sess)
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
                await self._handle_job_interim(sess, context, turn_end_wall)
                return
            # The last round flushes right before Stop; no tick may have
            # run in between. Place its sentence exactly before anything
            # below snapshots or renders the timeline
            # ("transcript-exact-sentence-anchors").
            _sync_anchors_from_transcript(sess)
            # R2 (design.md "turn anchor follows consumption", B6): the
            # absorption line can be first visible only right here — no
            # tick ran between it and Stop. Apply the consumption (move
            # the target, react, track) BEFORE anything below snapshots
            # or renders the timeline; the layout-aware re-anchor DECISION
            # itself needs `layout`, resolved a few lines down, so it is
            # not folded into `_consume_and_reanchor` here.
            consumed = sess.stream_consumed_notes
            sess.stream_consumed_notes = []
            if consumed:
                await self._apply_consumption(sess, consumed)
            # Snapshot the play-by-play FIRST — before the done-marking
            # below coerces every row to True (which would misreport
            # failed rows as successes in the full-log attachment, review
            # rev-iter1-003) and before the streaming reset wipes the
            # commentary.
            log_tools = list(sess.tool_history)
            log_commentary = list(sess.stream_commentary)
            # Snapshot every agent seen this turn — finished (already a
            # {type, started_at, elapsed, tool_count, tools} snapshot) plus
            # still-active (defensive: by the time a genuine Finished close
            # runs, active_subagents should already be empty per
            # job_background_open()'s own invariant, but this costs
            # nothing and covers any edge path) — for build_full_log's
            # AGENTS section ("agent activity rows on the busy card").
            # Chronological (start order), matching the section's own
            # "start order" contract.
            log_agents = sorted(
                list(sess.finished_subagents)
                + [
                    {
                        "type": info.get("type", "agent"),
                        "started_at": info.get("started_at", 0.0),
                        "elapsed": (
                            time.monotonic()
                            - info.get("started_at", time.monotonic())
                        ),
                        "tool_count": info.get("tool_count", 0),
                        "tools": list(info.get("tools", [])),
                    }
                    for info in sess.active_subagents.values()
                ],
                key=lambda a: a.get("started_at", 0.0),
            )

            # Mark all tools as done
            sess.tool_history = [(s, True) for s, _ in sess.tool_history]
            # A self-woken turn that ends before earning its deferred card
            # never gets one: only the answer goes out, and with no card
            # its first line carries the stats (roadmap 8.32). Under the
            # card lock, and before the animation is stopped: a deferred
            # card whose send is already in flight lands first and is then
            # finished like any other card, instead of arriving after this
            # path and being stranded mid-animation. The same wait covers a
            # live re-anchor in flight (a tick's `_consume_and_reanchor`),
            # which swaps `busy_msg_id` to its new card under this lock:
            # everything below reads the card it leaves, never the old one
            # it is deleting — for a tool-less card (8.32 R1) that is the
            # difference between deleting the live card and stranding it on
            # "Working" for good.
            async with sess.animate_lock:
                self._cancel_lazy_card(sess)
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
            # R3/R5 (design.md "turn anchor follows consumption"): decide
            # HERE, once layout is known, whether the about-to-be-
            # finalised card needs to move. `card` re-anchors immediately
            # (the finished render happens inside `_reanchor_busy_card`
            # itself); `merged` only flags it — the old card is still
            # needed for `_drop_answer_tail` below and the answer text
            # isn't known yet, so the actual delete+resend happens once
            # `_send_merged_final` is called, further down. `replace` is
            # unchanged: the delete-then-answer-under-`trigger_msg_id`
            # path already reads the now-correct target from insertion
            # point 1 above.
            #
            # A real mismatch between `busy_card_trigger` and
            # `trigger_msg_id` is the ONLY signal design.md defines for
            # "consumption moved the target" (R3) — every production call
            # site that establishes a live card now also records
            # `busy_card_trigger` (`send_busy`, both `_reanchor_busy_card`
            # sends, `_send_merged_final(send_as_new=True)`, the
            # `compacting`/`compact_done` bypass sends, and the `load()`
            # restart seed), so a bare `!= ` check — no `is not None`
            # carve-out — cannot silently disable a genuine re-anchor
            # after one of those paths runs (review rev-iter1-002: a
            # `busy_card_trigger is not None` guard here used to survive
            # exactly the compaction-bypass gap those sites had before
            # this fix, degrading a real absorption to pre-feature
            # behaviour with no signal it had happened). A hand-built
            # `TrackedSession` that skips every production call site (as
            # several pre-existing unit tests do) must seed
            # `busy_card_trigger` itself, same as design.md's own contract
            # already required.
            reanchor_needed = bool(
                sess.busy_msg_id and sess.busy_msg_id > 0
                and sess.busy_card_trigger != sess.trigger_msg_id
            )
            # review rev-iter1-001: trim any trailing commentary that just
            # duplicates the incoming answer BEFORE either final-render
            # path below — the immediate `card`-layout re-anchor a few
            # lines down renders the finished card RIGHT HERE via
            # `_reanchor_busy_card`, and `card_already_final` then skips
            # the second (correctly-ordered) render that used to catch
            # this. Running the trim first means both the immediate
            # re-anchor render and the ordinary in-place final render see
            # the already-trimmed `stream_commentary`, so there is only
            # ever one place this decision has to be made.
            if sess.busy_msg_id and sess.busy_msg_id > 0 and layout in ("card", "merged"):
                _drop_answer_tail(
                    sess, context.get("raw_md") or context.get("summary") or "",
                )
            # ── a card with nothing on it (roadmap 8.32 R1) ────────────────
            # In `card` layout the kept card is the turn's timeline record.
            # A turn with NO timeline content — `final_card_has_timeline`
            # names exactly what counts: tool rows, subagent rows, and prose
            # the card would keep — would leave only "✅ label · Done · Ns"
            # above an answer that is about to say the same. Such a turn is
            # delivered as ONE message instead: the answer, opening with
            # the STATS result line, the way `replace` delivers every turn.
            #
            # Not an edit of the card into the answer (what `merged` does):
            # an edit is silent in Telegram, and a card-layout answer has
            # always notified. The answer is a fresh send under the turn's
            # target, and the card is deleted AFTER it (`_delete_card_later`)
            # — two calls, where the kept card cost three, and the answer
            # never waits behind card housekeeping.
            #
            # Only with something to deliver: a turn with no answer keeps
            # its card as the one record that it ended, exactly as today.
            # Checked after the `_drop_answer_tail` trim above, so a prose
            # block that merely repeats the answer does not count as
            # timeline. A re-anchor is moot for such a card — the answer
            # replies to the turn's current target — so none is made.
            # (With `busy_msg_id` cleared here, the `card` re-anchor just
            # below finds no card and does nothing.)
            #
            # The id read here is the settled one: the `animate_lock` taken
            # above waited out any re-anchor still in flight, and nothing
            # between there and here yields.
            #
            # "Something to deliver" is judged the way the content
            # selection below will judge it, read-only: an answer whose
            # digest already went out is suppressed there (a turn with no
            # text of its own reads an EARLIER turn's from the transcript),
            # and such a turn has nothing to deliver — it keeps its card
            # rather than trading it for a bare header, or, on the
            # recovered path, for nothing at all.
            toolless_card_msg_id = 0
            _candidate = context.get("raw_md") or context.get("summary") or ""
            _fresh_answer = False
            if _candidate:
                _cand_digest = hashlib.md5(_candidate.encode("utf-8")).hexdigest()
                _fresh_answer = not (
                    _cand_digest == sess.last_idle_summary_hash
                    or sess.was_delivered(_cand_digest)
                )
            if (layout == "card" and sess.busy_msg_id and sess.busy_msg_id > 0
                    and _fresh_answer
                    and not final_card_has_timeline(sess)):
                toolless_card_msg_id = sess.busy_msg_id
                sess.busy_msg_id = None
                log.info("[%s] finished card has no timeline — the answer "
                         "goes out alone, with the stats", label)
            card_already_final = False
            merged_send_as_new = False
            if reanchor_needed and layout == "card":
                await self._reanchor_busy_card(sess, sess.trigger_msg_id, final=True)
                card_already_final = True
            elif reanchor_needed and layout == "merged":
                merged_send_as_new = True
            card_kept = False
            # When the finished card went out, on the monotonic clock: the
            # answer send below owes it FINISH_CARD_GRACE_SECONDS measured
            # from that moment (roadmap 8.23) — the clock starts where
            # this is STAMPED, a few lines down, not here. 0.0 means no
            # finished card was rendered, and so nothing to wait for.
            finish_card_at = 0.0
            # True once the busy card has been successfully disposed of —
            # either kept as the finished card (`card_kept`, below) or
            # deleted outright (`replace`, and `merged`'s own delete-on-
            # fallback a little further down). A kept card already says
            # Done and the turn's stats, so the answer under it opens with
            # the SHORT result line; with the card gone the answer's first
            # line carries the stats instead — still ONE message, per the
            # user's own framing of `replace`: "still we have only one
            # message after user message but busy message gets removed".
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                if layout in ("card", "merged"):
                    # Leave the timeline in the chat: which tools ran, in what
                    # order, and what Claude said between them is the record of
                    # how this answer was reached. Rendered here — before the
                    # streaming state is reset below and before the answer goes
                    # out — so scrollback reads card, header, body.
                    # (the trim already ran above, before any final render.)
                    if layout == "card":
                        if card_already_final:
                            # _reanchor_busy_card already rendered the
                            # exact same final content under the new
                            # message a moment ago — a second POST here
                            # would be redundant, harmless-but-wasteful.
                            card_kept = True
                        else:
                            try:
                                card_kept = await self._edit_busy_rich(
                                    sess, FINAL_VERB, final=True,
                                ) is True
                            except Exception:
                                log.debug("Final busy-card render failed", exc_info=True)
                        if card_kept:
                            # Stamped for BOTH branches above, and only
                            # once the card is really out: the re-anchor
                            # rendered it a few lines up, the edit right
                            # here. A failed final render (`card_kept`
                            # False) leaves this 0.0 — there is no
                            # finished card for the answer to follow, so
                            # the answer must not be held back for one.
                            finish_card_at = time.monotonic()
                        sess.busy_msg_id = None
                    # "merged": busy_msg_id stays live on purpose — the one
                    # combined edit (timeline + answer) happens below, once
                    # the answer text is known, and clears it either way.
                else:
                    # "replace" — delete the busy card, then send the answer
                    # as the turn's one message (its first line carries the
                    # stats the card would have shown).
                    try:
                        await bot.delete_message(
                            chat_id=resolve_chat_id(sess),
                            message_id=sess.busy_msg_id,
                        )
                    except Exception:
                        pass
                    sess.busy_msg_id = None

            summary = context.get("summary", "") or ""
            raw_md = context.get("raw_md", "")
            # Set only by session_monitor.py's idle-recovery fallback (a
            # missed-Stop-hook guess, never a real Stop/Notification hook).
            # When that guess turns up nothing new to say, the standalone
            # "Finished" header below is suppressed entirely — see its use
            # near `standalone_header`.
            recovered = bool(context.get("recovered"))

            # ── content-selection (design §1, named rule) ──────────────────
            # raw_md takes precedence, then the producer's summary, then
            # nothing. Deliberately NOT sess.summary: that is the PREVIOUS
            # turn's answer, and a turn that produced no text of its own
            # (it ended on tool calls, or was a background-job re-entry)
            # used to fall through to it whenever the producer had not set
            # `no_response` — the operator then read the last answer again
            # as the reply to the new prompt, plausible enough to be
            # believed and so worse than no body at all (three copies of
            # one answer were seen live). An empty content sends no body;
            # what goes out instead depends on whether a card exists — see
            # the header placement below.
            content = raw_md or summary

            # Content-dedup covers the FINAL delivery too ("close the
            # background-job endgame" requirement 3): a stray idle event
            # re-running this path with content identical to the last
            # delivered summary (interim or final) must not re-post it.
            # The card disposal below still runs — the header/card is
            # idempotent to finalize; only the body re-send is the spam.
            # The single hash resets where a genuine new turn starts; the
            # delivered-digest ring does not, so a body that already went
            # out is never re-posted however the turns were counted — the
            # transcript's newest text is an EARLIER turn's exactly when
            # this one produced none, and that is the case a per-turn
            # reset cannot see.
            #
            # Recorded PENDING (roadmap 8.39): refused in this process from
            # here on, but written to the state file only once the send
            # below is known to have landed — see `_answer_landed`.
            _answer_digests: list[str] = []
            _answer_landed = False
            if content:
                _digest = hashlib.md5(content.encode("utf-8")).hexdigest()
                if (_digest == sess.last_idle_summary_hash
                        or sess.was_delivered(_digest)):
                    log.info(
                        "[%s] final summary identical to a body already "
                        "delivered this session — suppressing re-send",
                        label,
                    )
                    content = ""
                else:
                    sess.last_idle_summary_hash = _digest
                    sess.remember_delivered(_digest, pending=True)
                    _answer_digests.append(_digest)

            # Reset streaming state — the turn is over.
            sess.stream_commentary = []
            sess.stream_tool_cursor = 0
            sess.stream_msg_id = ""
            sess.stream_anchor_floor = 0
            sess.stream_batch_since = None
            sess.stream_block_index = {}
            sess.stream_exact_anchor = {}
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
                if toolless_card_msg_id:
                    self._delete_card_later(sess, toolless_card_msg_id)
                # Don't clear trigger_msg_id — retry needs it
                # Don't flush pending queue — nothing was processed
                return

            # Compute elapsed time since BUSY started
            elapsed_str = ""
            elapsed_s = 0
            if sess.busy_started_at:
                elapsed_s = int(time.monotonic() - sess.busy_started_at)
            elif sess.busy_started_wall:
                # busy_started_at is stamped by the card sender; a turn
                # that never got a card still has the transition's own
                # stamp, so the header can report how long it ran.
                elapsed_s = int(time.time() - sess.busy_started_wall)
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
            # The result line in its STATS form — a result that is the only
            # message left for its turn says Finished and how long it took
            # ("session-name-on-every-message"). HTML for the standalone
            # header message, the rich-message dialect (bold = **) for a
            # composed send, bare for the plain-text fallback.
            header_text = (
                f"{_RESULT_GLYPH} <b>{html_mod.escape(label)}</b> · Finished{suffix}"
            )
            header_md = f"{_result_line_md(label)} · Finished{suffix}"
            header_plain = f"{_result_line_plain(label)} · Finished{suffix}"

            # ── merged layout: one combined edit, timeline + answer ────────
            # Attempted here — after the header text exists (used only by the
            # observer broadcast below) but before the per-answer-alone
            # overflow check, since merged has its own combined-text ceiling
            # check inside _send_merged_final. Only attempted when the busy
            # card is still live; if it isn't (e.g. it was already lost) there
            # is nothing to merge into, so the turn falls through to the
            # ordinary send below — the exact same path "replace" uses.
            # Roadmap 8.41: agents this turn launched in the background are
            # still running — the answer ends by saying so, in every layout.
            agents = self._agents_snapshot(sess)
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
                #
                # R5/B6 (design.md "turn anchor follows consumption"):
                # `merged_send_as_new` (set once, layout-aware, right
                # after `layout` was resolved above) means this card is
                # anchored to a message this turn did NOT consume —
                # Telegram cannot move an existing message's reply
                # target, so the combined card+answer must be a fresh
                # SEND under the new target instead of an edit in place.
                if merged_send_as_new:
                    merged_delivered = await self._send_merged_final(
                        sess, content, send_as_new=True,
                        reply_to=sess.trigger_msg_id, agents=agents,
                    )
                    if merged_delivered and pre_merge_busy_msg_id and pre_merge_busy_msg_id > 0:
                        try:
                            await bot.delete_message(
                                chat_id=resolve_chat_id(sess),
                                message_id=pre_merge_busy_msg_id,
                            )
                        except Exception:
                            log.debug("[%s] stale merged card delete failed",
                                      sess.label, exc_info=True)
                else:
                    merged_delivered = await self._send_merged_final(
                        sess, content, agents=agents)
                if not merged_delivered:
                    # Losing the timeline is acceptable; losing the answer is
                    # never acceptable — fall back to the replace-style send
                    # below by clearing the (now presumed-gone-or-stale) card.
                    # Falling back to "replace" means behaving exactly like
                    # it: one message opening with the stats result line,
                    # once the card is gone.
                    if sess.busy_msg_id and not MUTE.is_muted(resolve_chat_id(sess)):
                        # Still live — _send_merged_final's own failure
                        # wasn't a RichMessageGone, so the card needs an
                        # explicit delete here (a send too: skipped while
                        # the chat is flood-muted).
                        try:
                            await bot.delete_message(
                                chat_id=resolve_chat_id(sess),
                                message_id=pre_merge_busy_msg_id,
                            )
                        except Exception:
                            pass
                    # Else _send_merged_final already found the card gone
                    # (RichMessageGone) and cleared busy_msg_id itself —
                    # nothing left in the chat either way.
                sess.busy_msg_id = None

            # ── Overflow detection ─────────────────────────────────────────
            # Skipped when the merged edit above already delivered the whole
            # turn — its own combined-text ceiling check already covers this
            # turn's answer.
            send_file = False
            body_content = content  # may be truncated below
            # The first line the body carries when it goes out as ONE rich
            # message: the short result line under a kept card (the card
            # already says Done + stats), the stats form when no card
            # remains (`replace`, `merged`'s fallback, a turn that never
            # had a card). It counts against the byte ceiling, so the
            # overflow check below leaves room for it.
            lead_md = _result_line_md(label) if card_kept else header_md
            lead_plain = _result_line_plain(label) if card_kept else header_plain
            body_limit = _RICH_LIMIT - len(f"{lead_md}\n\n".encode("utf-8"))
            if agents:
                body_limit -= len(f"\n\n{agents['line']}".encode("utf-8"))
            if not merged_delivered and content:
                content_utf8 = content.encode("utf-8")
                if len(content_utf8) > body_limit:
                    # Truncate at the last markdown-safe boundary under the limit.
                    bounds = _md_safe_boundaries(content)
                    cut = 0
                    for b in bounds:
                        b_bytes = len(content[:b].encode("utf-8"))
                        if b_bytes <= body_limit:
                            cut = b
                    if cut:
                        body_content = content[:cut]
                    else:
                        # No safe boundary found — truncate at byte limit.
                        body_content = content_utf8[:body_limit].decode("utf-8", errors="ignore")
                    send_file = True

            # ── Result-line placement: one message per turn, never a bare
            # one, and every result opens with the session's name
            # ("session-name-on-every-message") ──
            # card kept + body        → ONE rich message — the SHORT result
            #                           line (`💬 label`), blank line, body —
            #                           threaded to the prompt. The card
            #                           right above already reads
            #                           ✅ label · Done · stats, so nothing
            #                           of that is repeated.
            # card kept + no body     → nothing. The card's ✅ status line IS
            #                           the record; a bare "Finished" under it
            #                           only ever repeated it.
            # card deleted + body     → ONE rich message — the STATS result
            #                           line (`💬 label · Finished (…)`),
            #                           blank line, body (`replace`, and
            #                           `merged` falling back to it): the
            #                           card is gone, so this line carries
            #                           the elapsed time. A `card`-layout
            #                           card with no timeline (roadmap 8.32)
            #                           takes this row too: it is deleted
            #                           once this message is out.
            # no card + body          → the same stats-line message. "No
            #                           card" covers a turn that never had
            #                           one and a final render that failed.
            # no card / deleted card, → one header message carrying the
            #   no body                 elapsed time: nothing else in the chat
            #                           says the turn ended.
            # Overflow keeps the standalone header regardless: the "attached
            # below" note and the document's reply target both live on it,
            # and the body then follows it bare.
            #
            # EXCEPT: a recovery-originated idle (session_monitor.py's
            # missed-Stop-hook guess) with no body — content was empty, or
            # its digest was already delivered — has nothing to report.
            # `send_file` can't be true here (it requires non-empty
            # `content`), so this only ever silences the "no card, no body"
            # row above: no bare "Finished (46m 46s)" for a turn that, as
            # far as the operator can tell, never actually ended.
            standalone_header = (
                not merged_delivered
                and not (recovered and not body_content)
                and (send_file or (not card_kept and not body_content))
            )
            msg_id = 0
            if standalone_header and MUTE.is_muted(resolve_chat_id(sess)):
                # R3: the header is a send too. The body below raises
                # RichMessageFloodBanned before any HTTP and nothing
                # falls back, so the whole turn costs zero attempts.
                log.info("[%s] IDLE header skipped — chat flood-muted", label)
            elif standalone_header:
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
                if standalone_header:
                    # The header message above already opens the turn's
                    # result with the session's name; the body follows it.
                    rich_text = body_content
                    plain_text = body_content
                else:
                    # One message — its FIRST line is the result line
                    # ("session-name-on-every-message"), in whichever form
                    # `lead_md` picked above.
                    rich_text = f"{lead_md}\n\n{body_content}"
                    plain_text = f"{lead_plain}\n\n{body_content}"
                # Only the rich send carries the ⏳ line: it is the one whose
                # exact text is kept to settle it. A held copy goes out late
                # and a plain-text fallback is chunked — a line frozen into
                # either could never be settled, so neither carries it.
                held_rich = rich_text
                if agents:
                    rich_text = f"{rich_text}\n\n{agents['line']}"
                is_rtl = detect_rtl(body_content)
                log.info("[%s] sendRichMessage: %d chars, rtl=%s, overflow=%s, "
                         "lead=%s",
                         label, len(rich_text), is_rtl, send_file,
                         "standalone" if standalone_header
                         else ("short" if card_kept else "stats"))
                # Without a standalone header the body IS the turn's message:
                # it carries the reply link and becomes the tracked message.
                body_is_the_message = not standalone_header
                reply_to = sess.trigger_msg_id if body_is_the_message else None
                chat_id = resolve_chat_id_int(sess)
                # ── the finished card's head start (roadmap 8.23) ───────
                # Wire order is already right; this makes it the VISIBLE
                # order. Only the REMAINDER of the grace is slept — the
                # answer-text building since the card edit counts toward
                # it — and only in the `card` layout with a finished card
                # really out, which is exactly what `finish_card_at`
                # records (`merged` edits the card into the answer, so
                # there is no ordering to fix; `replace` deleted it).
                # This is the single grace site in the finish path: the
                # plain-text fallbacks below follow a FAILED send, by
                # which point the head start has long since elapsed.
                #
                # NOT "the card is always seen first", though — two sends
                # can still precede it with a stamped card, and both are
                # deliberate: the standalone overflow header just above
                # (reachable with a kept card only when `send_file` is
                # set, i.e. an answer over the byte ceiling going out as
                # an attachment), and the API-error notice that returns
                # early, upstream of here. Neither is the ANSWER — the
                # message that actually competes with the card edit for
                # the operator's eye — and the answer still follows the
                # card by the grace in both cases, so the card is never
                # the last thing to render. Covering those two wants its
                # own decision, not a second grace site here: moving the
                # wait above the header would also delay header-only
                # turns and would have to learn about the flood mute,
                # which skips that header entirely.
                # A grace of 0 disables the wait through `owed` alone, so
                # there is no separate (untestable) branch for it.
                if finish_card_at:
                    owed = FINISH_CARD_GRACE_SECONDS - (
                        time.monotonic() - finish_card_at
                    )
                    if owed > 0:
                        await _finish_sleep(owed)
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
                        rich_text,
                        is_rtl=is_rtl,
                        reply_to_message_id=reply_to,
                    )
                    _answer_landed = True
                    if body_is_the_message and isinstance(sent, dict):
                        msg_id = sent.get("message_id") or 0
                    if (agents and isinstance(sent, dict)
                            and sent.get("message_id")):
                        self._remember_agents_line(
                            sess, chat_id, sent["message_id"], rich_text,
                            agents, is_rtl)
                except RichMessageBlocked:
                    _log_blocked_once(Exception("sendRichMessage 403"))
                except RichMessageFloodBanned:
                    # HELD (8.29 R6). This is THE drop path the incident
                    # log named five times on 2026-09-15: the answer lived
                    # in a local of this method and went out of scope with
                    # it, leaving one INFO line and nothing else.
                    #
                    # Still no plain-text fallback — that would be a fresh
                    # violation extending the ban, and R2 is unchanged.
                    # What changes is that "cannot send now" stops meaning
                    # "cannot send": the text is kept and delivered once
                    # the mute lifts, with an honest late marker.
                    self._hold_answer(sess, held_rich, plain_text, reply_to,
                                      digests=_answer_digests,
                                      selected_wall=turn_end_wall)
                except (RichMessageFallbackRequired, Exception):
                    # Plain-text fallback — split into ≤4096-char chunks at
                    # markdown-safe boundaries so the send cannot fail to parse.
                    log.warning("[%s] sendRichMessage failed — falling back to plain text",
                                label, exc_info=True)
                    chunks = _plain_text_chunks(plain_text)
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
                            _answer_landed = True
                            # With no header, the first chunk that lands takes
                            # over as the tracked message for this reply.
                            if body_is_the_message and not msg_id:
                                msg_id = fallback.message_id
                        except Exception:
                            log.warning("[%s] plain-text fallback chunk send failed",
                                        label, exc_info=True)

            # Roadmap 8.39: only an answer that reached the chat is
            # remembered ACROSS a restart. A held one (not persisted), a
            # blocked or failed one, or one a shutdown cut off stays out of
            # the state file, so the next daemon can still deliver it.
            if _answer_digests and (_answer_landed or merged_delivered):
                sess.confirm_delivered(_answer_digests, turn_end_wall)

            # The tool-less card goes only now that the answer is out (or
            # held) — roadmap 8.32 R1.
            if toolless_card_msg_id:
                self._delete_card_later(sess, toolless_card_msg_id)

            sess.trigger_msg_id = None  # reply cycle complete
            sess.busy_card_trigger = None
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
                build_full_log(label, log_tools, log_commentary, content,
                                agents=log_agents)
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
                elif MUTE.is_muted(resolve_chat_id(sess)):
                    # R3: a document is a send. Observers (own bots, own
                    # budgets) still get theirs below.
                    log.info("[%s] full-log attachment skipped — chat "
                             "flood-muted", label)
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

            # A message Claude queued during this turn and did not absorb
            # is the NEXT turn's prompt — Claude Code pops it as soon as
            # the run ends, silently. Give that turn its card and target
            # now (R8, "anchor-on-transcript-consumption"); a background
            # job's waiting window keeps the queue for the job's close.
            if sess.queued_targets and not sess.job_background_open():
                await self._start_queued_turn(sess)

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
                    else:
                        # AskUserQuestion detected but no questions data (transcript
                        # not flushed). Degrade to Allow/Deny — Allow sends Enter.
                        sess.pending_permission = {
                            "tool_summary": "AskUserQuestion (loading…)",
                            "tool_info": tool_info,
                            "wait_started_at": time.monotonic(),
                        }
                else:
                    tool_summary = tool_info["summary"] if tool_info else "Permission needed"
                    sess.pending_permission = {
                        "tool_summary": tool_summary,
                        "tool_info": tool_info,
                        "wait_started_at": time.monotonic(),
                        # design.md "answer PermissionRequest hooks with
                        # a decision instead of keystrokes": deliberately
                        # NOT added to either AskUserQuestion-flavored
                        # shape above — belt-and-suspenders alongside
                        # callbacks.py's own tool-name guard.
                        "hook_reply": context.get("hook_reply"),
                    }
                # The one inline-prompt keyboard builder, shared with the
                # pinned bar's "Answer" re-send (8.31).
                keyboard = self._pending_prompt_keyboard(sess)
                sess.pending_prompt_msg = None

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
                    questions = (tool_info.get("input") or {}).get("questions") or []
                    prompt_summary = (questions[0].get("question", "")
                                      if questions else "")
                elif selector_options:
                    text, keyboard = self._build_selector_keyboard(sess, label,
                                                                    selector_text, selector_options)
                    prompt_summary = selector_text or "Needs input"
                else:
                    tool_summary = tool_info["summary"] if tool_info else ""
                    text = f"🔐 <b>{html_mod.escape(label)}</b> · Permission needed"
                    if tool_summary:
                        text += f"\n<code>{html_mod.escape(tool_summary)}</code>"
                        text += _format_perm_detail(
                            tool_summary, (tool_info or {}).get("detail") or "")
                    # This separate-message prompt keeps its Allow/Deny-only
                    # keyboard on purpose (review rev-iter1-004): with no
                    # ``pending_permission`` on this path there is no
                    # always_available flag, so Allow-always must not be
                    # offered — and it never was here. Stop stays absent as
                    # before; unifying with _build_permission_keyboard would
                    # change this surface's behaviour beyond the fix.
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
                    prompt_summary = tool_summary or "Permission needed"

                # Kept as SENT, before the send: the pinned bar summarises
                # it and its "Answer" button re-sends it (8.31) — also after
                # a ban that ate this send.
                # Its identity is stamped NOW, while this prompt is the one
                # pending, and kept in a local: the send below can wait in
                # the limiter while the prompt is answered and another one
                # shown, and the message must be bound to THIS prompt.
                prompt_token = new_prompt_token()
                sess.pending_prompt_msg = {"text": text, "keyboard": keyboard,
                                           "summary": prompt_summary,
                                           "prompt_token": prompt_token}

                # Wrapped for the gate (8.26 R2, the tolerance sweep). This
                # is a DIRECT send inside `notify`, a 1726-line method: an
                # unhandled `FloodMuted` here would propagate out of the
                # whole notify call and abort everything after it —
                # including the answer the gate exists to protect. The
                # prompt itself is genuinely lost while the chat is banned
                # (there is nowhere to put a permission dialog), but the
                # turn survives, and Claude's own terminal prompt is still
                # answerable.
                try:
                    msg = await bot.send_message(
                        resolve_chat_id(sess), text, reply_markup=keyboard,
                        parse_mode="HTML",
                        reply_to_message_id=sess.trigger_msg_id,
                    )
                except FloodMuted:
                    log.info("[%s] permission prompt not sent — chat "
                             "flood-muted", label)
                else:
                    self.registry.track_message(
                        msg.message_id, sess.name, resolve_chat_id_int(sess) or 0)
                    # Bound to THIS prompt: a tap on it after the prompt is
                    # answered elsewhere is refused (8.31, callbacks.py).
                    self.register_prompt_surface(
                        resolve_chat_id_int(sess) or 0, msg.message_id, sess,
                        prompt_token)
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
