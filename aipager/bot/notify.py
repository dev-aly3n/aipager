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
import time
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import Forbidden

from aipager.bot import agents_line as _agents_line
from aipager.bot.divider import (
    DIVIDER,
    DIVIDER_SEP,
    PLAIN_DIVIDER,
    without_dividers,
)
from aipager.bot.footer import footer_block, without_last_footer
from aipager.bot import card_rows, question_form, reactions, rm_refusal
from aipager.bot.sibling_text import shown
from aipager.bot.dashboard import PIN_NEEDS_INPUT, new_prompt_token
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.keyboards import PERM_NEEDED
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
    message_rtl,
    edit_message_text_rich,
    send_rich_message,
)


from aipager import agent_tree as _agent_tree
from aipager import bg_shells as _bg_shells
from aipager import bidi_guard
from aipager import i18n, preferences, rm_countdown
from aipager import transcript as transcript_mod
from aipager.i18n import Msg, Plural, Safe, Span
from aipager.clockfmt import elapsed as clock
from aipager.config import (
    COMPACT_CARD_TIMEOUT_SECONDS,
    COMPACT_DONE_PAUSE_SECONDS,
    FINISH_CARD_GRACE_SECONDS,
    STALE_BUSY_TIMEOUT,
    STREAM_EDIT_INTERVAL,
)
from aipager.state import (
    BG_AGENTS_RETRY_SECONDS,
    BG_AGENTS_SETTLE_SECONDS,
    Status,
    TrackedSession,
)
from aipager.policy_snapshot import clear_turn_open, mark_turn_open, note_driver_id
from aipager.bot.animation import (
    COMPACTING, FINAL_VERB, _CARD_CHAR_BUDGET, _RICH_LIMIT, _card_edit_lock, _exact_anchors_available,
    _expire_tool_batch,
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
    _turn_api_error,
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
    document_upload,
    resolve_chat_id,
    resolve_chat_id_int,
    send_text,
    NEEDS_ADMIN_REPLY,
    PROMPT_DIALOG,
    PROMPT_REFUSED,
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

# Strong references to fire-and-forget housekeeping tasks (a card delete
# scheduled by `_delete_card_later`): the event loop only keeps weak ones, and a
# task collected mid-flight is a delete that silently never happened.
_BACKGROUND_TASKS: set = set()

# Sessions whose held answers are being delivered right now: their files
# wait until that flush is over (it tells the outbox where each landed).
_HELD_FLUSHING: set[str] = set()
#: A turn's copied files that no finish took (their copy ended after the
#: turn's finish had given up waiting): sent without a reply target after
#: this long, by the monitor's flush.
_ORPHAN_FILES_SECONDS = 60.0

# Separator between the finished timeline and the answer in `merged`
# layout: Telegram's own divider line (aipager.bot.divider), not a second
# "✅ Finished" header (that would be redundant with the card's own header,
# per the same reasoning the header-skip logic below already uses for
# `card` layout). A plain-text copy shows PLAIN_DIVIDER in its place.
_MERGED_SEPARATOR = DIVIDER


def _drawn_block(block: str) -> str:
    """The agents block as drawn under an answer: small text, each line a
    footer block (operator, 2026-10-15); nothing when there is none."""
    return footer_block(block) if block else ""


def _with_agents_block(text: str, line: str) -> str:
    """*text* with the agents block *line* under it, a divider between.
    The one way an answer and its block are joined: the send, the record
    of what went out (``_remember_agents_line``) and every later edit
    agree by construction."""
    return f"{text}{DIVIDER_SEP}{line}"

# The result line ("session-name-on-every-message"): the FIRST line of
# every result message, as the status line is the LAST line of every busy
# card — one glyph per message kind, always in the same place, so a reader
# can tell a session's answer from its timeline (and from another
# session's) at a glance. The short form below is for a result that lands
# under a finished card, which already says Done and the turn's stats; the
# stats form (`💬 **label** · Finished (…)`) is built in the finish path
# for a result that is the only message left for its turn.
_RESULT_GLYPH = "💬"


def _result_line_md(label: str, tr=None) -> str:
    """The result line for a rich message drawn by *tr*. In the in-RTL
    twin of a left-to-right language (an English chat, a Persian answer:
    :attr:`~aipager.i18n.core.Tr.ltr_in_rtl`) the line is marked at both
    ends and the name isolated, so the glyph stays first on Telegram
    Desktop and Web; otherwise the bytes are as they always were."""
    if tr is not None and tr.ltr_in_rtl:
        return tr.line(f"{_RESULT_GLYPH} **{tr.isolate(_md_escape(label))}**", kind="md")
    return f"{_RESULT_GLYPH} **{_md_escape(label)}**"


def _result_line_plain(label: str) -> str:
    return f"{_RESULT_GLYPH} {label}"


FINISHED = Msg("fin.finished", "{lead} · Finished ({stats})",
               note="The answer's first line: the session's name, then how long the turn "
                    "ran and the lines it changed",
               eg={"lead": "💬 x1", "stats": "4:10, +10 -5"})
FINISHED_BARE = Msg("fin.finished_bare", "{lead} · Finished",
                    note="The answer's first line when there is nothing to count",
                    eg={"lead": "💬 x1"})
STATS = Msg("fin.stats", "{clock}, +{added} -{removed}",
            note="How long the turn ran, then the lines added and removed",
            eg={"clock": "4:10", "added": 10, "removed": 5})
LINES = Msg("fin.lines", "+{added} -{removed}", note="The lines added and removed in a turn",
            eg={"added": 10, "removed": 5})
AGENT_LOST = Msg("fin.agent_lost", "⚠️ <b>{session}</b> · Finished (background agent lost)",
                 note="A turn closed because its background agent disappeared",
                 eg={"session": "x1"})
AGENT_LOST_AFTER = Msg("fin.agent_lost_after",
                       "⚠️ <b>{session}</b> · Finished (background agent lost after {clock})",
                       note="A turn closed because its background agent disappeared, after "
                            "how long", eg={"session": "x1", "clock": "4:10"})
SHELL_LOST = Msg("fin.shell_lost",
                 "⚠️ <b>{session}</b> · Finished (no end seen for a background shell)",
                 note="A turn closed though aipager never saw its background shell end",
                 eg={"session": "x1"})
SHELL_LOST_AFTER = Msg("fin.shell_lost_after",
                       "⚠️ <b>{session}</b> · Finished (no end seen for a background shell "
                       "after {clock})",
                       note="As fin.shell_lost, after how long",
                       eg={"session": "x1", "clock": "4:10"})
FULL_ATTACHED = Msg("fin.full_attached", "📎 <i>Full response attached below ↓</i>",
                    note="Under the answer's first line: the whole answer is in the file "
                         "that follows")


def finished_line(tr, lead: str, kind: str, *, seconds: int = 0, added: int = 0,
                  removed: int = 0) -> str:
    """``<lead> · Finished (4:10, +10 -5)`` in *tr*'s language: *lead* is
    the result line already written for *kind* (``💬 **x1**``), the clock
    only when *seconds* > 0, the counts only when either is set."""
    render = getattr(tr, kind)
    clock_text = Safe(tr.clock(seconds)) if seconds > 0 else None
    counted = bool(added or removed)
    if clock_text is not None and counted:
        stats = render(STATS, clock=clock_text, added=added, removed=removed)
    elif clock_text is not None:
        stats = clock_text
    elif counted:
        stats = render(LINES, added=added, removed=removed)
    else:
        return render(FINISHED_BARE, lead=Span(lead))
    return render(FINISHED, lead=Span(lead), stats=Safe(stats))


def settled_agent_row(agent_type: str, tool_count: int, elapsed: float) -> str:
    """A finished agent's row as STORED in ``tool_history`` (English; the
    card draws it in the chat's language, ``card_rows``)."""
    plural = "" if tool_count == 1 else "s"
    return (f"\U0001f916 {agent_type} · {tool_count} tool call{plural}"
            f" · {clock(elapsed)}")


def _card_is_empty(sess: TrackedSession) -> bool:
    """Whether a finishing turn's busy card has nothing on it but its
    status line: the turn made no tool call (operator, 2026-10-18).

    Read from the state the card itself is drawn from, all of it reset
    when this turn's card was opened (``_send_busy_and_animate``), so an
    earlier turn's rows never count: no section the finished card would
    keep (``final_card_has_timeline``: a tool row, an agent row, or prose
    left after ``_drop_answer_tail`` trimmed what the answer carries), no
    agent launched (``subagent_count_this_turn``, the agents finished or
    still running), no background shell of the job, and no permission or
    question prompt raised (``turn_prompt_shown``). Call it AFTER the trim.
    """
    return not (
        final_card_has_timeline(sess)
        or sess.subagent_count_this_turn
        or sess.finished_subagents
        or sess.active_subagents
        or sess.job_shell_ids()
        or sess.turn_prompt_shown
    )


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


def _separate_prompt_perm(tool_info: dict | None, summary: str,
                          hook_reply: dict | None) -> dict:
    """What answering a separate-message prompt needs (roadmap 8.99): the
    same fields the inline prompt keeps in ``pending_permission``, kept in
    ``pending_prompt_msg["perm"]`` instead, so an Allow/Deny tapped on it
    answers through the parked PermissionRequest hook and posts the same
    attributed audit line. ``pending_permission`` itself stays None on this
    path: it means "the prompt is shown on the busy card".

    The hook's reply channel rides only on a plain tool permission, as on
    the inline path: never on an AskUserQuestion, whose answer is a
    choice of option rows, not an allow/deny."""
    perm: dict = {"tool_summary": summary or str(PERM_NEEDED),
                  "tool_info": tool_info, "wait_started_at": time.monotonic(),
                  # When it was shown, on the clock a restart can read
                  # (roadmap 8.102).
                  "shown_wall": time.time()}
    if tool_info and tool_info.get("name") == "AskUserQuestion":
        perm["ask_question"] = True
        perm["question"] = summary or "?"
    elif tool_info:
        perm["hook_reply"] = hook_reply
    return perm


def _refused_call_row(sess: TrackedSession, perm: dict, row: str) -> bool:
    """The card's own row of the tool call Claude Code refused by itself
    (roadmap 8.151) becomes the refused *row* (❌, kept at the turn's
    end): the call never ran, so the finished card must not tick it. Its
    first pending row with the call's summary, as a PostToolUseFailure
    would settle it. False when it has none (the caller adds the row)."""
    info = perm.get("tool_info")
    summary = info.get("summary") if isinstance(info, dict) else None
    claimed = {shell.get("history_idx") for shell in sess.bg_shells.values()}
    for i, (text, done) in enumerate(sess.tool_history):
        if text == summary and not done and i not in claimed:
            sess.tool_history[i] = (row, "failed")
            return True
    return False


def _prompt_owner(perm) -> str:
    """The agent id that owns open prompt *perm*, ``""`` for Claude's own
    or one whose tool is not known (roadmap 8.120)."""
    info = perm.get("tool_info") if isinstance(perm, dict) else None
    return str(info.get("agent_id") or "") if isinstance(info, dict) else ""


def _terminal_end_how(sess: TrackedSession, perm) -> str:
    """How the permission dialog of *perm* (Claude's own, shown during the
    wait that just ended at the transcript's interrupt marker) ended at
    the terminal (roadmap 8.152): ``"ran"`` when its call was allowed and
    then stopped while it ran (a PostToolUseFailure with ``is_interrupt``
    marked it ``dialog_gone``, or the marker names the call's message),
    ``"denied"`` when the dialog itself was answered "No" or Escape,
    ``"unknown"`` when nothing says which. Never raises."""
    try:
        perm = perm if isinstance(perm, dict) else {}
        if perm.get("dialog_gone") is True:
            return transcript_mod.END_RAN
        info = perm.get("tool_info")
        tuid = info.get("tool_use_id") if isinstance(info, dict) else None
        return transcript_mod.terminal_end_kind(sess.transcript_path or "",
                                                tuid if isinstance(tuid, str) else "")
    except Exception:  # noqa: BLE001 - the neutral wording then
        return transcript_mod.END_UNKNOWN


#: :meth:`NotifyMixin._end_job_turn`'s answer when the job is still open.
_JOB_STILL_OPEN = object()


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

    def _separate_permission_keyboard(self, sess: TrackedSession):
        """Allow / Deny for a tool permission sent as its own message (the
        separate-message prompt, roadmap 8.99), shared by the send and by
        the restore of that prompt after a restart (8.102).

        This surface keeps its Allow/Deny-only keyboard on purpose (review
        rev-iter1-004): Allow-always was never offered here, and Stop
        stays absent; unifying with ``_build_permission_keyboard`` would
        change this surface's behaviour."""
        # Local import, not top-level — avoids an import cycle with
        # session_parity; mirrors keyboards.py's own local-import precedent
        # (see keyboards.py:_build_resume_mode_keyboard).
        from aipager.bot import session_parity

        from aipager.bot import keyboards
        chat_id = resolve_chat_id_int(sess) or 0
        tr = i18n.for_sess(sess)
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                tr.text(keyboards.ALLOW),
                callback_data=session_parity.session_cb(self, chat_id, sess, "allow")),
            InlineKeyboardButton(
                tr.text(keyboards.DENY),
                callback_data=session_parity.session_cb(self, chat_id, sess, "deny")),
        ]])

    def _pending_prompt_markup(self, sess: TrackedSession):
        """``(text, keyboard)`` of the prompt *sess* is waiting on, as the
        chat was shown it — for the pinned bar's "Answer" button (8.31).
        ``None`` when there is none to show (a prompt that never reached
        the chat and left nothing behind).

        Inline prompts are re-rendered from ``pending_permission`` with the
        same builders the busy card uses; a separate-message prompt is
        re-sent exactly as it was sent."""
        if sess.pending_permission:
            text = self._build_busy_text(sess.label, "Waiting", sess)
            # Telegram counts the text it shows, not the HTML: a question
            # form the card already fitted to that goes out whole.
            if question_form.shown_len(text) > TELEGRAM_MAX_TEXT_LEN:
                text = _safe_truncate(text, TELEGRAM_MAX_TEXT_LEN, True)
            return text, self._pending_prompt_keyboard(sess)
        if sess.pending_prompt_msg:
            return (sess.pending_prompt_msg.get("text", ""),
                    sess.pending_prompt_msg.get("keyboard"))
        return None

    async def _send_prompt_message(self, sess: TrackedSession,
                                   tool_info: dict | None, context: dict,
                                   slot: str) -> dict:
        """Send a prompt as its own message and record it (roadmap 8.99,
        factored out for 8.120). *slot* ``"primary"``: the prompt the wait
        opened with, kept in ``pending_prompt_msg``. ``"extra"``: a prompt
        that opened while another was open, appended to
        ``extra_prompts`` and sent as a reply to the busy card. Returns the
        record."""
        bot = self._app.bot
        label = sess.label
        sess.turn_prompt_shown = True
        selector_text = context.get("selector_text", "")
        selector_options = context.get("selector_options")
        unclocked = None  # its text without a clock line, when it has one
        if tool_info and tool_info["name"] == "AskUserQuestion":
            text, keyboard = self._build_ask_keyboard(sess, label, tool_info["input"])
            questions = (tool_info.get("input") or {}).get("questions") or []
            prompt_summary = (questions[0].get("question", "")
                              if questions else "")
        elif selector_options:
            text, keyboard = self._build_selector_keyboard(sess, label,
                                                            selector_text, selector_options)
            prompt_summary = selector_text or str(PIN_NEEDS_INPUT)
        else:
            from aipager.bot import keyboards
            prompt_tr = i18n.for_sess(sess)
            tool_summary = tool_info["summary"] if tool_info else ""
            text = prompt_tr.html(keyboards.SEPARATE_PERM, session=label)
            if tool_summary:
                text += f"\n<code>{html_mod.escape(tool_summary)}</code>"
                text += _format_perm_detail(
                    tool_summary, (tool_info or {}).get("detail") or "",
                    tr=prompt_tr)
            # When Claude Code refuses a risky removal by itself (8.128).
            clock_line = rm_refusal.clock_line_html(prompt_tr, tool_info)
            if clock_line:
                unclocked = text
                text += clock_line
            # This separate-message prompt keeps its Allow/Deny-only
            # keyboard on purpose (review rev-iter1-004): Allow-always
            # was never offered here. Stop stays absent as before;
            # unifying with _build_permission_keyboard would change
            # this surface's behaviour beyond the fix. Its taps
            # answer through the hook like the inline prompt's
            # (roadmap 8.99, ``pending_prompt_msg["perm"]`` below).
            keyboard = self._separate_permission_keyboard(sess)
            prompt_summary = tool_summary or str(PERM_NEEDED)

        # Kept as SENT, before the send: the pinned bar summarises
        # it and its "Answer" button re-sends it (8.31) — also after
        # a ban that ate this send.
        # Its identity is stamped NOW, while this prompt is the one
        # pending, and kept in a local: the send below can wait in
        # the limiter while the prompt is answered and another one
        # shown, and the message must be bound to THIS prompt.
        prompt_token = new_prompt_token()
        prompt_record = {"text": text, "keyboard": keyboard,
                         "summary": prompt_summary,
                         "prompt_token": prompt_token,
                         "perm": _separate_prompt_perm(
                             tool_info, prompt_summary,
                             context.get("hook_reply"))}
        if unclocked is not None:
            # Shown once the prompt closes other than by Claude Code's
            # own refusal (roadmap 8.151).
            prompt_record["text_unclocked"] = unclocked
        if slot == "primary":
            sess.pending_prompt_msg = prompt_record
            reply_to = sess.trigger_msg_id
        else:
            sess.extra_prompts.append(prompt_record)
            # Under the card it waits beside (8.120), else the turn's
            # message.
            reply_to = (sess.busy_msg_id if sess.busy_msg_id and sess.busy_msg_id > 0
                        else sess.trigger_msg_id)

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
                reply_to_message_id=reply_to,
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
            # Where the message is, for a restart to bind it to the
            # restored prompt (roadmap 8.102). Only on THIS prompt's
            # record: the send may have waited in the limiter while
            # it was answered and another one shown.
            if (sess.pending_prompt_msg is prompt_record
                    or any(r is prompt_record for r in sess.extra_prompts)):
                prompt_record["msg_id"] = msg.message_id
                prompt_record["chat_id"] = resolve_chat_id_int(sess) or 0
                self.registry.mark_dirty()
            await self._maybe_update_bot_name(sess.name)
        return prompt_record

    def _rule_denial(self, sess: TrackedSession, tool_name: str):
        """``(denied, whose)``: whether the running turn's rules deny
        *tool_name* (scope mode's, or legacy team.yaml's), and whose."""
        if self.scopes is not None:
            return self._auto_deny_decision(sess, tool_name)
        if self.team is not None and self.team.rules.deny_tools:
            triggerer = self._driver_user(sess)
            if self.team.rules.tool_is_denied(tool_name, triggerer):
                return True, triggerer
        return False, None

    async def _on_extra_prompt(self, sess: TrackedSession, context: dict) -> None:
        """A prompt that opened while another is open (roadmap 8.120): its
        own message with its own buttons, under the card."""
        if sess.status != Status.INTERACTIVE and not sess.prompt_wait_carry:
            log.info("[%s] a second prompt arrived after the wait ended - "
                     "not shown", sess.label)
            return
        tool_info = context.get("tool_info")
        if tool_info and tool_info.get("name") == "AskUserQuestion":
            tool_info = {**tool_info,
                         "input": bidi_guard.reveal_questions(tool_info.get("input"))}
        if tool_info:
            denied, whose = self._rule_denial(sess, tool_info.get("name", ""))
            if denied:
                await self._auto_deny_extra(sess, tool_info, whose, context)
                return
        await self._send_prompt_message(sess, tool_info, context, "extra")

    async def _after_prompts_changed(self, sess: TrackedSession,
                                     context: dict) -> None:
        """Some open prompts closed and others are still open (roadmap
        8.120): when the card's own prompt went, the card shows the
        Waiting frame with no answer buttons (the others keep theirs), and
        a refusal that waited for its dialog to be on top is typed once it
        is."""
        if sess.status != Status.INTERACTIVE:
            return
        card = sess.busy_msg_id
        if (context.get("inline_closed") and sess.pending_permission is None
                and card and card > 0):
            self._stop_animation(sess)
            text = self._build_busy_text(sess.label, "Waiting", sess)
            await self._edit_busy_raw(card, text,
                                      reply_markup=self._build_stop_keyboard(sess),
                                      chat_id=resolve_chat_id(sess))
        await self._run_due_auto_denies(sess)

    async def _on_rm_refused(self, sess: TrackedSession, context: dict) -> None:
        """Claude Code's risky-removal prompts that came back with no
        answer from the chat (roadmap 8.128), ``context["perms"]``:
        ``outcome`` ``"refused"`` (its countdown ran out and Claude Code
        refused it by itself) or ``"ran"`` (answered in the terminal).

        ``close`` True (the session monitor read the result): each prompt
        still open is closed here, and the session goes on as after an
        answer. False (a hook closer already closed them): only the
        records; ``context["records"]`` then says where each was shown,
        ``(where, record)`` as :meth:`TrackedSession.prompt_record_of`.

        A refused prompt's surface is marked refused (a later tap says
        so), the card's history row says "Refused by Claude Code" (❌,
        never ✅), the audit records it as a refusal with no person, and
        its own message, if it has one, gets the refused line and loses
        its buttons. Nobody answered, so no answer line is posted. Each
        prompt is handled once (``perm["rm_refused"]``)."""
        perms = context.get("perms") or []
        records = context.get("records") or []
        close = context.get("close") is True
        refused = context.get("outcome") == "refused"
        closed_any = inline_closed = False
        edits = []
        for i, perm in enumerate(perms):
            if not isinstance(perm, dict) or perm.get("rm_refused"):
                continue
            if rm_countdown.clock_of(perm) is None:
                continue  # its call came back meanwhile: it ran
            if close:
                where, rec = sess.prompt_record_of(perm)
                if where is None:
                    continue  # answered or closed meanwhile
            elif i < len(records) and isinstance(records[i], tuple):
                where, rec = records[i]
            else:
                where, rec = sess.prompt_record_of(perm)
            perm["rm_refused"] = True
            if close:
                closed, inl = sess.close_prompts(lambda p, perm=perm: p is perm)
                closed_any = closed_any or bool(closed)
                inline_closed = inline_closed or inl
            if not refused:
                continue
            if where == "inline":
                sess.mark_prompt_refused(sess.busy_msg_id, perm.get("prompt_token"))
            elif isinstance(rec, dict):
                sess.mark_prompt_refused(rec.get("msg_id"), rec.get("prompt_token"))
                # Its refused line is shown below: a Stop that read the
                # record before its Escapes leaves the message to it.
                rec["final_shown"] = True
                edits.append(rec)
            detail = (perm.get("tool_summary") or "Permission")[:80]
            row = f"🔑 {detail[:60]} → {card_rows.REFUSED_BY_CLAUDE}"
            if not _refused_call_row(sess, perm, row):
                sess.record_tool(row, "failed")
            from aipager import audit as audit_mod
            audit_mod.append(
                session=sess.name, label=sess.label,
                action=str(card_rows.REFUSED_BY_CLAUDE),
                tool=str((perm.get("tool_info") or {}).get("name") or "Bash"),
                summary=detail, user_id=None, username="",
                scope_label=self._scope_label(sess.scope_chat_id),
                scope_chat_id=sess.scope_chat_id or None,
                denied=True, via="claude_code_timeout")
            log.info("[%s] prompt refused by Claude Code (nobody answered in "
                     "time): %s", sess.label, detail)
        if closed_any or edits:
            self.registry.mark_dirty()
        if self._app is not None:
            tr = i18n.for_sess(sess)
            for rec in edits:
                await self._show_rm_refused(sess, rec, tr)
        if not (close and closed_any):
            return
        if sess.open_prompts():
            await self._after_prompts_changed(sess, {"inline_closed": inline_closed})
            return
        if sess.status != Status.INTERACTIVE:
            return
        if sess.resumed_after_stop:
            # Carried over Claude's Stop (8.120): no turn of Claude's runs.
            self.registry.transition(sess.name, Status.IDLE)
            return
        self.registry.transition(sess.name, Status.BUSY,
                                 preserve_job_state=sess.job_work_running())
        card = sess.busy_msg_id
        if self._app is None or not (card and card > 0):
            return
        try:
            await self._edit_busy_raw(
                card, self._build_busy_text(sess.label, "Working", sess),
                reply_markup=self._build_stop_keyboard(sess),
                chat_id=resolve_chat_id(sess))
        except Exception:  # noqa: BLE001 - the animation repaints it
            log.debug("[%s] card repaint after a refused prompt failed",
                      sess.label, exc_info=True)
        self._start_animation(sess)

    async def _on_rm_clock_disproven(self, sess: TrackedSession,
                                     context: dict) -> None:
        """Risky-removal prompts whose clock the transcript disproved
        (roadmap 8.152, ``context["perms"]``): each one still open loses
        its clock line and keeps its answer buttons. Its own message is
        edited to its text without the line; the busy card's inline
        prompt is drawn again (its text no longer carries the line). The
        bar's later copies are sent from the same, now unclocked, text.
        Never raises."""
        if self._app is None:
            return
        for perm in context.get("perms") or ():
            if not isinstance(perm, dict) or not rm_countdown.clock_disproven(perm):
                continue
            where, rec = sess.prompt_record_of(perm)
            try:
                if where == "inline" and sess.pending_permission is perm:
                    card = sess.busy_msg_id
                    if card and card > 0:
                        await self._edit_busy_raw(
                            card, self._build_busy_text(sess.label, "Waiting", sess),
                            reply_markup=self._pending_prompt_keyboard(sess),
                            chat_id=resolve_chat_id(sess))
                elif where in ("separate", "extra") and isinstance(rec, dict):
                    base = rm_refusal.take_unclocked_text(rec)
                    if base is not None:
                        self.registry.mark_dirty()
                        await self._show_unclocked(sess, rec, base,
                                                   keyboard=rec.get("keyboard"))
            except Exception:  # noqa: BLE001 - cosmetic: the line stays
                log.debug("[%s] clock line not taken off a disproven prompt",
                          sess.label, exc_info=True)

    async def _drop_clock_lines(self, sess: TrackedSession, records) -> None:
        """Prompt messages whose prompt closed other than by Claude Code's
        own refusal (a tap, an answer in the terminal, its call ran or
        failed; roadmap 8.151): each loses its clock line ("If nobody
        answers ..."), once, and shows no answer buttons, since its
        prompt is closed (roadmap 8.152). A record with no clock line is
        left alone (a refused prompt is closed before any caller reads its
        record). Never raises."""
        for rec in records or ():
            base = rm_refusal.take_unclocked_text(rec)
            if base is not None:  # none, or taken off already
                await self._show_unclocked(sess, rec, base)

    async def _show_prompts_closed(self, sess: TrackedSession,
                                   records) -> set[int]:
        """Prompt messages whose prompt a Stop's Escapes closed (Claude's
        own, an agent's: every prompt goes with the turn) show no answer
        buttons, as a prompt answered in the terminal: one with a clock
        line loses it (:meth:`_drop_clock_lines`, Claude Code did not
        refuse it), any other loses its buttons alone. A clocked message
        whose text cannot be edited still loses its buttons (as
        :meth:`_show_rm_refused`). Their surfaces are marked closed
        already, so a late tap types nothing. The busy card's inline
        prompt goes with the card's own settle. Returns the ids of the
        messages whose buttons went. Never raises."""
        stripped: set[int] = set()
        if self._app is None:
            return stripped
        for rec in records or ():
            if not isinstance(rec, dict):
                continue
            msg_id = rec.get("msg_id")
            base = rm_refusal.take_unclocked_text(rec)
            if base is not None and await self._show_unclocked(sess, rec, base):
                stripped.add(msg_id)
                continue
            if not (isinstance(msg_id, int) and msg_id > 0):
                continue  # never sent (a muted chat): nothing to edit
            try:
                await self._app.bot.edit_message_reply_markup(
                    chat_id=rec.get("chat_id") or resolve_chat_id(sess),
                    message_id=msg_id, reply_markup=None)
                stripped.add(msg_id)
            except Exception:  # noqa: BLE001 - its taps type nothing anyway
                log.debug("[%s] closed prompt message's buttons not removed",
                          sess.label, exc_info=True)
        return stripped

    async def _show_unclocked(self, sess: TrackedSession, rec: dict,
                              base: str, keyboard=None) -> bool:
        """Prompt message record *rec* shows *base*, its text without the
        clock line (see :meth:`_drop_clock_lines`). *keyboard*: the
        buttons the message keeps, None for a closed prompt. An edit of
        the text sets the message's buttons too, so a closed prompt's
        edit sends none: its old Allow/Deny never come back, also after a
        tap took them off (roadmap 8.152). True when the message was
        edited. Never raises."""
        rec["text"] = base
        chat_id, msg_id = rec.get("chat_id"), rec.get("msg_id")
        if not (isinstance(msg_id, int) and msg_id > 0):
            return False  # never sent (a muted chat): nothing to edit
        try:
            await self._app.bot.edit_message_text(
                chat_id=chat_id or resolve_chat_id(sess), message_id=msg_id,
                text=base, parse_mode="HTML", reply_markup=keyboard)
        except Exception:  # noqa: BLE001 - cosmetic: the line stays
            log.debug("[%s] clock line not taken off a closed prompt",
                      sess.label, exc_info=True)
            return False
        return True

    async def _show_rm_refused(self, sess: TrackedSession, rec: dict, tr) -> None:
        """A refused prompt's own message: its text as sent, the refused
        line under it, and no buttons. Its buttons go even when the text
        cannot be edited."""
        chat_id, msg_id = rec.get("chat_id"), rec.get("msg_id")
        if not (isinstance(msg_id, int) and msg_id > 0):
            return
        chat = chat_id or resolve_chat_id(sess)
        bot = self._app.bot
        try:
            await bot.edit_message_text(
                chat_id=chat, message_id=msg_id,
                text=(rec.get("text") or "") + "\n\n" + tr.html(rm_refusal.RM_REFUSED),
                parse_mode="HTML", reply_markup=None)
            return
        except FloodMuted:
            log.info("[%s] refused prompt message not edited - chat flood-muted",
                     sess.label)
        except Exception:  # noqa: BLE001 - fall back to the buttons alone
            log.debug("[%s] refused prompt message edit failed", sess.label,
                      exc_info=True)
        try:
            await bot.edit_message_reply_markup(chat_id=chat, message_id=msg_id,
                                                reply_markup=None)
        except Exception:  # noqa: BLE001 - nothing more to do
            log.debug("[%s] refused prompt message's buttons not removed",
                      sess.label, exc_info=True)

    async def _show_agents_stopped(self, sess: TrackedSession, records) -> None:
        """Every closed agent prompt's own message in *records* (open
        prompt records, see ``TrackedSession.open_prompt_records``) says
        its agent was stopped, with no buttons (roadmap 8.121 F5). The
        card's inline prompt goes with the card's own settle."""
        if self._app is None:
            return
        tr = i18n.for_sess(sess)
        # Marked before the first edit: a Stop that read a record before
        # its Escapes leaves the message to the stopped line.
        for rec in records:
            if isinstance(rec, dict):
                rec["final_shown"] = True
        for rec in records:
            if isinstance(rec, dict):
                await self._show_agent_stopped(sess, rec, tr)

    async def _show_agent_stopped(self, sess: TrackedSession, rec: dict, tr) -> None:
        """A stopped agent's prompt message: its text as sent, the stopped
        line under it, and no buttons. Its buttons go even when the text
        cannot be edited (as :meth:`_show_rm_refused`)."""
        from aipager.bot.stop_text import PROMPT_AGENT_STOPPED
        chat_id, msg_id = rec.get("chat_id"), rec.get("msg_id")
        if not (isinstance(msg_id, int) and msg_id > 0):
            return
        chat = chat_id or resolve_chat_id(sess)
        bot = self._app.bot
        try:
            await bot.edit_message_text(
                chat_id=chat, message_id=msg_id,
                # Without its countdown, if it had one (roadmap 8.151):
                # the prompt did not close by Claude Code's own refusal.
                text=((rec.get("text_unclocked") or rec.get("text") or "")
                      + "\n\n" + tr.html(PROMPT_AGENT_STOPPED)),
                parse_mode="HTML", reply_markup=None)
            return
        except FloodMuted:
            log.info("[%s] stopped agent's prompt message not edited - chat "
                     "flood-muted", sess.label)
        except Exception:  # noqa: BLE001 - fall back to the buttons alone
            log.debug("[%s] stopped agent's prompt message edit failed", sess.label,
                      exc_info=True)
        try:
            await bot.edit_message_reply_markup(chat_id=chat, message_id=msg_id,
                                                reply_markup=None)
        except Exception:  # noqa: BLE001 - nothing more to do
            log.debug("[%s] stopped agent's prompt message's buttons not removed",
                      sess.label, exc_info=True)

    async def _on_prompts_carried(self, sess: TrackedSession) -> None:
        """Claude's turn ended with an agent's prompt still open (roadmap
        8.120): the answer is out, and the session waits on that prompt
        again. The card's inline prompt is sent again as its own message
        (the card now shows the answer); a prompt with its own message
        keeps it."""
        carried = sess.carried_inline_prompt
        sess.carried_inline_prompt = None
        if carried is None and sess.pending_permission is not None:
            # The job's interim path keeps the card: its prompt moves off it.
            carried = sess.pending_permission
            sess.mark_prompt_closed(sess.busy_msg_id)
            sess.pending_permission = None
        if carried is None and not sess.open_prompts():
            # Every carried prompt closed during the carry (its agent's
            # tool ended, the agent stopped): nothing is left to wait on,
            # and Claude's turn is over. The session stays IDLE, and what
            # was held behind the dialog goes out.
            log.info("[%s] carried prompts closed during the Stop - not "
                     "waiting", sess.label)
            if sess.dialog_hold is not None:
                sess.dialog_hold["closed"] = "answered"
            self.registry.mark_dirty()
            return
        if self.registry.resume_wait(sess.name) is None:
            return
        log.info("[%s] waiting on %d open prompts after the turn ended",
                 sess.label, len(sess.open_prompts()) + (1 if carried else 0))
        if carried is not None and self._app is not None:
            await self._send_prompt_message(
                sess, carried.get("tool_info"),
                {"hook_reply": carried.get("hook_reply")}, "primary")
        self.registry.mark_dirty()

    async def _send_merged_final(
        self, sess: TrackedSession, answer: str, *,
        send_as_new: bool = False, reply_to: int | None = None,
        agents: dict | None = None, rtl: bool | None = None,
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

        The caller holds the card-edit lock (``_card_edit_lock``) across
        this and its clean-up, so nothing in here may call
        ``_edit_busy_rich``.

        One direction for the whole message (multilingual design 5.7,
        rule 5): *rtl*, the turn's (:meth:`_answer_rtl`), worked out from
        *answer* when the caller does not pass it.
        """
        if rtl is None:
            rtl = self._answer_rtl(sess, answer)
        try:
            card_md, hid = build_stream_card_ex(sess, FINAL_VERB, final=True, rtl=rtl)
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
        line_tr = i18n.for_sess(sess).directed(rtl)

        def _merged(card: str) -> str:
            return (f"{card}\n\n{_MERGED_SEPARATOR}\n\n"
                    f"{_result_line_md(sess.label, line_tr)}\n\n{answer}" if answer else card)

        combined = _merged(card_md)
        # The held copy (a mute below) never carries the ⏳ line: it goes
        # out late, and a line frozen into it could not be settled. Its
        # plain-text copy (the last resort once rich delivery keeps
        # failing) shows a line of its own where a divider is.
        held = combined
        # Only the card's own status line is a footer to strip: the answer
        # is Claude's, a "<footer>" in it stays as written.
        held_plain = without_dividers(_merged(without_last_footer(card_md)), PLAIN_DIVIDER)
        if agents:
            # Room for the block's largest form, so no later edit of it can
            # push this message past the ceiling.
            if len(combined.encode("utf-8")) + agents["reserve"] > _RICH_LIMIT:
                log.info("[%s] merged: combined card+answer plus the agents "
                         "block over the byte ceiling — falling back to "
                         "replace", sess.label)
                return False
            combined = _with_agents_block(combined, agents["line"])
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
        # The message's one direction, the turn's: Claude's words decide,
        # never aipager's own lines in the timeline (rule 5).
        is_rtl = rtl
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
                self._hold_answer(sess, held, held_plain, reply_to, is_rtl=rtl)
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
            self._hold_answer(sess, held, held_plain, reply_to, is_rtl=rtl)
            return False
        if result is not None and agents:
            self._remember_agents_line(
                sess, chat_id, int(sess.busy_msg_id), combined, agents,
                is_rtl)
        return result is not None

    # ── roadmap 8.41: background agents still running ─────────────────────

    def _answer_rtl(self, sess: TrackedSession, answer: str,
                    commentary: str = "") -> bool:
        """The ONE direction of a message carrying Claude's answer
        (multilingual design 5.7, rule 5): the answer's, else what Claude
        said during the turn, else the prompt the turn answers, else the
        chat's language. Its header, its Finished line and its agents
        block are drawn in it (``Tr.directed``)."""
        return message_rtl(i18n.for_sess(sess).language_rtl, answer, commentary,
                           sess.turn_prompt)

    def _agents_snapshot(self, sess: TrackedSession,
                         rtl: bool | None = None) -> dict | None:
        """The background work running as an answer goes out (roadmap
        8.41) — ONE snapshot, taken once, so the block shown and the items
        it keeps current describe the same agents and shells — or ``None``
        with none. The agents are all this session's running background
        agents; the shells only this job's own (``in_job``): a dev server
        an earlier task left up is not repeated under every answer (the
        pinned bar still counts it). Each agent keeps its nearest ancestor
        the answer lists (``"parent"``, "" for a top one): the links
        themselves go when an agent stops."""
        now = time.monotonic()
        listed = set(sess.bg_agents)
        items = [{"id": agent_id, "kind": "agent", "label": info["label"],
                  "started": info.get("started"),
                  "parent": _agent_tree.nearest_listed(
                      sess.agent_parents, agent_id, listed)}
                 for agent_id, info in sess.bg_agents.items()]
        items += [{"id": task_id, "kind": "shell",
                   "label": sess.bg_shells[task_id]["label"],
                   "started": sess.bg_shells[task_id].get("started")}
                  for task_id in sess.job_shell_ids()]
        if not items:
            return None
        # In the direction of the answer it ends (`_answer_rtl`).
        tr = i18n.for_sess(sess).directed(rtl)
        rec = {"items": items, "ids": [item["id"] for item in items],
               "ends": {}, "sent_at": now}
        return {"line": _drawn_block(_agents_line.render_block(
                    self._agents_view(sess, rec, now), tr)),
                "items": items, "ids": rec["ids"], "sent_at": now,
                "reserve": _agents_line.reserve_bytes(tr)}

    def _agents_view(self, sess: TrackedSession, rec: dict,
                     now: float) -> _agents_line.BlockView:
        """What an answer's block shows at *now*: its own items, each
        ended only once its end is BG_AGENTS_SETTLE_SECONDS old (an agent
        the still-running note brings back stays working), the activity
        and the waiting of the agents still working read live from
        ``bg_agents``, each agent under the one its item names."""
        ends = rec.get("ends") or {}
        sent_at = rec["sent_at"]
        row_of = {}
        for item in rec["items"]:
            if item["kind"] != "shell":
                row_of[item["id"]] = len(row_of)

        def settled(item_id: str) -> dict | None:
            end = ends.get(item_id)
            if end is not None and now - end["at"] >= BG_AGENTS_SETTLE_SECONDS:
                return end
            return None

        rows = []
        agent_ends = []
        stopped_ends = []
        done_ends = []
        shells = {"running": 0, "finished": [], "stopped": []}
        for item in rec["items"]:
            end = settled(item["id"])
            started = item.get("started")
            if item["kind"] == "shell":
                if end is None:
                    shells["running"] += 1
                else:
                    ran = end["at"] - started if started is not None else end["at"] - sent_at
                    shells["stopped" if end["outcome"] == "stopped"
                           else "finished"].append(ran)
                continue
            parent = row_of.get(item.get("parent") or "", -1)
            if end is not None:
                agent_ends.append(end["at"])
                # Roadmap 8.121 F5: an agent a person's Stop ended reads
                # "stopped", never "done".
                stopped = end.get("outcome") == "stopped"
                (stopped_ends if stopped else done_ends).append(end["at"])
                ran = end["at"] - (started if started is not None else sent_at)
                rows.append(_agents_line.AgentRow(item["label"], True, ran,
                                                  parent=parent, stopped=stopped))
                continue
            info = sess.bg_agents.get(item["id"]) or {}
            rows.append(_agents_line.AgentRow(
                item["label"], False, prose=info.get("prose", ""),
                tool=info.get("tool", ""), tool_arg=info.get("tool_arg", ""),
                description=info.get("desc", ""),
                waiting=bool(info.get("waiting")), parent=parent))
        all_done = bool(rows) and len(agent_ends) == len(rows)
        return _agents_line.BlockView(
            agents=tuple(rows),
            agents_done_seconds=(max(done_ends or agent_ends) - sent_at)
            if all_done else None,
            agents_stopped_seconds=(max(stopped_ends) - sent_at) if stopped_ends else 0.0,
            shells_running=shells["running"],
            shells_finished=len(shells["finished"]),
            shells_finished_seconds=max(shells["finished"], default=0.0),
            shells_stopped=len(shells["stopped"]),
            shells_stopped_seconds=max(shells["stopped"], default=0.0))

    def _remember_agents_line(
        self, sess: TrackedSession, chat_id: int, msg_id: int, text: str,
        agents: dict, is_rtl: bool,
    ) -> None:
        """An answer carrying the status block landed as *msg_id*: keep
        exactly what went out before the block, so every later edit is
        that text plus the block, never a re-render of the answer."""
        tail = _with_agents_block("", agents["line"])
        if not text.endswith(tail):
            log.warning("[%s] agents block: the sent text does not end with "
                        "it - not kept current", sess.label)
            return
        sent_at = agents.get("sent_at") or time.monotonic()
        sess.add_agents_line({
            "chat_id": chat_id, "msg_id": msg_id,
            "body": text[:-len(tail)], "shown": agents["line"],
            "items": [dict(item) for item in agents["items"]],
            "ids": list(agents["ids"]), "ends": {}, "is_rtl": is_rtl,
            "sent_at": sent_at, "last_try": sent_at, "refused": "",
            "final": False, "attempts": 0, "next_try": 0.0, "gone_at": 0.0,
        })

    async def _tick_agents_lines(self, sess: TrackedSession,
                                 now: float) -> None:
        """Keep each due answer's status block current (roadmap 8.41).

        An ORNAMENT, skip-class edit, and only when the block's visible
        text changed: the flood gate may refuse it, never queue an answer,
        a prompt or a card behind it. A live edit that is refused (budget
        short, muted, minimal mode) is not tried again for the same text;
        the next change tries. The final one (everything ended and
        settled) gets two attempts at most: a refused or muted one is
        followed by ONE more, after the mute lifts when there is one, and
        then the block is left as last shown. A deleted message (or a
        blocked bot) ends it at once. A gone session has none: the GONE
        transition clears them (SessionRegistry.transition), and /kill
        drops the whole entry."""
        for rec in sess.agents_lines_due(now):
            if await self._tick_agents_line(sess, rec, now):
                sess.agents_lines = [
                    r for r in sess.agents_lines if r is not rec]

    async def _tick_agents_line(self, sess: TrackedSession, rec: dict,
                                now: float) -> bool:
        """One look at one answer's block, and at most one edit. True when
        that answer is finished with (settled, gone, or given up)."""
        final = bool(rec.get("final"))
        if sess.busy_msg_id and rec["msg_id"] == sess.busy_msg_id:
            # The message is the live card again: an edit here would
            # replace the card with the old answer. Never; wait.
            return False
        # The answer's direction, kept with it (`_remember_agents_line`).
        block = _drawn_block(_agents_line.render_block(
            self._agents_view(sess, rec, now),
            i18n.for_sess(sess).directed(rec.get("is_rtl"))))
        if block == rec["shown"]:
            return final
        if not final and block == rec.get("refused"):
            return False
        text = _with_agents_block(rec["body"], block)
        shown = _with_agents_block(rec["body"], rec["shown"])
        if (len(text.encode("utf-8")) > _RICH_LIMIT
                or (len(text) > _CARD_CHAR_BUDGET and len(text) > len(shown))):
            # Never past the ceiling, never longer past Telegram's fold:
            # an ornament that does not fit is skipped. The block is
            # measured as sent, its footer tags and entities included
            # (more than Telegram shows): it can only be skipped sooner.
            log.info("[%s] agents block: the edit would not fit - skipped",
                     sess.label)
            if final:
                return True
            rec["refused"] = block
            return False
        chat_id = rec["chat_id"]
        rec["last_try"] = now
        if final:
            rec["attempts"] = rec.get("attempts", 0) + 1
        try:
            result = await edit_message_text_rich(
                chat_id, rec["msg_id"], text, is_rtl=rec["is_rtl"],
                kind="skip", priority=PRIORITY_ORNAMENT,
            )
        except (RichMessageGone, RichMessageBlocked):
            log.info("[%s] agents block: the answer is gone — not edited",
                     sess.label)
            return True
        except (FloodSkipped, FloodMuted):
            # RichMessageFloodBanned is a FloodMuted: the chat is muted.
            result = None
        except Exception:
            log.debug("[%s] agents block edit failed", sess.label,
                      exc_info=True)
            result = None
        if result is not None:
            rec["shown"] = block
            rec["refused"] = ""
            if final:
                log.info("[%s] agents line settled", sess.label)
            return final
        if not final:
            log.debug("[%s] agents block edit refused - waits for the next "
                      "change", sess.label)
            rec["refused"] = block
            return False
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
        """Delete a finished turn's busy card once its answer is out. Unused
        by the finish path since 2026-09-27 (8.32 R1's tool-less delete
        after the answer was reverted; since 2026-10-18 an empty card is
        deleted BEFORE the answer, by ``_drop_empty_card``). Never raises,
        never waits.

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
        # Owed until it lands, and persisted: a restart that kills this
        # task deletes the card at the next startup instead (roadmap 8.55).
        if msg_id not in sess.pending_card_deletes:
            sess.pending_card_deletes.append(msg_id)
            self.registry.mark_dirty()

        async def _delete() -> None:
            try:
                await bot.delete_message(
                    chat_id=chat_id, message_id=msg_id,
                    rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
                )
            except Exception:
                log.debug("[%s] tool-less card delete failed (left behind)",
                          label, exc_info=True)
            if msg_id in sess.pending_card_deletes:
                sess.pending_card_deletes.remove(msg_id)
                self.registry.mark_dirty()

        task = asyncio.create_task(_delete())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    async def _drop_empty_card(self, sess: TrackedSession) -> bool:
        """Delete a finishing turn's busy card that has nothing on it
        (``_card_is_empty``), as it stands: still "Working", never edited
        into its Done form first (operator, 2026-10-18). True once the card
        is gone, False when the delete failed and the card is still there
        (the caller then finishes it in place, so it never stays on
        "Working").

        The same call `replace` makes for its card: ESSENTIAL and sent
        before the answer. Not attempted while the gate refuses the card
        (``_card_refused_now``: the chat is flood-muted, or in minimal mode,
        where the delete would take the chat's scarce token and hold the
        answer back behind it): False, and the final edit the caller then
        tries is refused before any request, so nothing goes out for the
        card, as before this change. Under the card-edit lock, like the
        merged finish: an edit already waiting in that lock lands first,
        and one that gets the lock afterwards finds ``busy_msg_id`` retired
        and sends nothing."""
        if self._card_refused_now(sess):
            return False
        async with _card_edit_lock(sess):
            msg_id = sess.busy_msg_id
            if not msg_id or msg_id <= 0:
                # The edit this waited behind found the card gone.
                sess.busy_msg_id = None
                return True
            try:
                await self._app.bot.delete_message(
                    chat_id=resolve_chat_id(sess), message_id=msg_id,
                )
            except Exception:
                log.info("[%s] empty card %s could not be deleted — it is "
                         "finished in place instead", sess.label, msg_id,
                         exc_info=True)
                return False
            sess.busy_msg_id = None
            return True

    async def _apply_consumption(
        self, sess: TrackedSession, consumed: list[dict], *,
        move_target: bool = True,
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
        # The target first, with no wait in between: a Stop landing during
        # the 👍 round trips must find the target already moved (review
        # rev-iter1-001 of instant-card-move-and-helper-questions).
        self._take_consumed(sess, consumed, move_target=move_target)
        await reactions.mark_all(self, consumed, reactions.TAKEN,
                                 resolve_chat_id(sess))

    def _take_consumed(self, sess: TrackedSession, consumed: list[dict], *,
                       move_target: bool = True) -> None:
        """:meth:`_apply_consumption` without the 👍: track the consumed
        messages and move the reply target to the last one. Synchronous,
        so it cannot interleave with a Stop."""
        self._track_consumed(sess, consumed)
        last = consumed[-1]
        last_msg_id = last.get("msg_id")
        # `move_target` is False only for a finish whose turn a newer one
        # has already replaced, or whose Stop popped the next message: the
        # target is that newer turn's (or that message's) by now.
        if last_msg_id is not None and move_target:
            sess.trigger_msg_id = last_msg_id
            sess.last_prompt = last.get("raw_text", "") or ""
            sess.turn_prompt = sess.last_prompt
            sess.last_prompt_driver_user_id = note_driver_id(last)
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
        from aipager.policy_snapshot import (
            list_outstanding_notes, mark_command_notes_ran,
        )
        ran = [n for n in list_outstanding_notes(sess.name)
               if reactions.is_slash_command(n.get("raw_text"))]
        await reactions.mark_all(self, ran, reactions.ACK,
                                 resolve_chat_id(sess))
        # Done, so no longer "queued": the notes expire (roadmap 8.37).
        mark_command_notes_ran(sess.name, ran)

    def _take_next_prompt(
        self, sess: TrackedSession,
    ) -> tuple[dict, bool] | None:
        """The oldest message Claude queued during the run that just ended
        and never absorbed is running NOW: Claude Code pops its queue the
        moment a run ends — an interim one inside a background job
        included — and fires NO hook for the prompt it submits
        ("anchor-on-transcript-consumption", R8).

        Synchronous, and called the moment the Stop is handled (roadmap
        8.57): the pop moves the reply target and enters BUSY right away,
        so the popped turn's own hooks — its first PreToolUse, its Stop —
        land on a session that already knows it is running, instead of
        this happening seconds later behind a flood-delayed answer, when
        that turn may already have ended (a BUSY that nothing would ever
        end, and a card for a finished turn). Inside an open job
        (roadmap 8.56) the entry preserves the job. Returns ``(target,
        in_job)``, or ``None`` with nothing queued. The card and the 👍 go
        out later, from :meth:`_open_popped_turn`.

        Accepted residual risk (review rev-iter1-005): if the operator
        clears Claude's queue from the terminal (Escape) in the fraction
        of a second between this turn's Stop and the pop, this card
        shows a turn that never runs. No hook can confirm the pop; the
        card sits until /stop, /kill or the stale-BUSY warning.
        """
        if not sess.queued_targets:
            return None
        nxt = sess.queued_targets.pop(0)
        # "Inside the job" exactly when this Stop is an interim one: the
        # continuation turn's own Stop closes the job (the finish below),
        # so a message popped there starts a turn of its own.
        in_job = (sess.job_background_open()
                  and not (sess.job_continuation_active
                           and not sess.job_work_running()))
        sess.trigger_msg_id = nxt.get("msg_id")
        sess.last_prompt = nxt.get("raw_text") or ""
        sess.turn_prompt = sess.last_prompt
        sess.last_prompt_driver_user_id = nxt.get("driver_user_id")
        self.registry.mark_dirty()
        self.registry.transition(sess.name, Status.BUSY,
                                 preserve_job_state=in_job)
        # A prompt the daemon typed itself (its note was picked up while
        # the last turn ran) is this turn's: files Claude sends in it are
        # from a turn a prompt opened (state.py files_turn_resumed).
        if sess.files_turn_resumed(time.time()):
            log.info("[%s] files turn resumed for queued message %s",
                     sess.label, nxt.get("msg_id"))
        # Claude Code fires no hook for this turn: its sender is the popped
        # message's author (roadmap 8.77), None when not known.
        sess.turn_sender_id = nxt.get("driver_user_id")
        if not in_job:
            # A new turn whose card state (and card) waits for this finish:
            # its own hooks are held until then (roadmap 8.62). Inside the
            # job the turn goes on with the state it has; nothing to wait
            # for.
            sess.hold_turn_state(sess.turn_seq)
        return nxt, in_job

    async def _open_popped_turn(
        self, sess: TrackedSession, nxt: dict, in_job: bool, turn: int,
    ) -> None:
        """The popped message's 👍 and card (see :meth:`_take_next_prompt`).

        The card comes from the turn-card step for the popped turn: nothing
        if that turn has already finished, the card it already has if
        something else opened one (moved under this message, if it is not
        there yet), a new one otherwise (roadmap 8.57). Inside an open job
        the popped turn IS the job's turn, so that step finds the job's one
        card and moves it down to this message — one re-anchor, never a
        second card, no reset of the job's agent tracking (roadmap 8.56)."""
        try:
            # Claude popped it as this turn's prompt: taken (R2).
            await reactions.mark_all(self, [nxt], reactions.TAKEN,
                                     resolve_chat_id(sess))
            await self._send_busy_and_animate(sess, turn=turn)
        finally:
            # The turn-card step released the popped turn's held hooks as
            # soon as its card state existed. A request it dropped (the
            # turn already finished or was replaced), or a failure, must
            # not leave them waiting (roadmap 8.62).
            sess.release_turn_state(turn)
        log.info("[%s] %s for queued message %s (turn %d)", sess.label,
                 "taken inside the open job" if in_job else "next turn started",
                 nxt.get("msg_id"), turn)

    async def _start_queued_turn(self, sess: TrackedSession) -> None:
        """:meth:`_take_next_prompt` then :meth:`_open_popped_turn`, in one
        step — for a caller that has nothing to deliver in between."""
        taken = self._take_next_prompt(sess)
        if taken is not None:
            await self._open_popped_turn(sess, *taken, turn=sess.turn_seq)

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
        when the MessageDisplay hook is known live
        (``_exact_anchors_available``) and no background job is waiting.
        The queue scan itself runs without the hook since roadmap 8.47;
        the hook gate is kept deliberately as it was (a teardown that
        guesses wrong sends an Escape), and so is the job gate (an interim
        Stop now takes the next target as it pops, roadmap 8.56, but a
        teardown still does not guess while agents run). An
        untrusted target gets no 🤷 here. Whether a teardown types its
        queue wipe is decided by the transcript's evidence instead
        (``session_ops.held_by_claude``, roadmap 8.37)."""
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
                     selected_wall: float = 0.0,
                     is_rtl: bool | None = None) -> None:
        """Keep an answer a flood mute refused (8.29 R6). Never raises.

        ``is_rtl`` is the message's direction (`_answer_rtl`), kept with it
        so the late send reads as the answer was built.

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
                selected_wall=selected_wall, is_rtl=is_rtl,
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
        from aipager.bot.held import HELD

        chat_id = resolve_chat_id_int(sess) or resolve_chat_id(sess)
        if not chat_id or MUTE.is_muted(chat_id):
            return 0
        entries = [e for e in HELD.pending(chat_id) if e.session == sess.name]
        if not entries:
            return 0
        _HELD_FLUSHING.add(sess.name)
        try:
            delivered = await self._flush_held_entries(sess, chat_id, entries)
        finally:
            _HELD_FLUSHING.discard(sess.name)
        # The files that waited behind those answers follow them now.
        await self.flush_turn_files(sess)
        return delivered

    async def _flush_held_entries(self, sess: TrackedSession, chat_id,
                                  entries) -> int:
        """:meth:`flush_held_answers`'s deliveries, oldest first."""
        from aipager.bot import held_message
        from aipager.bot.held import HELD, HELD_ANSWER_MAX_ATTEMPTS
        from aipager.bot.outbox import OUTBOX

        # The marker in the chat's language (held.py stays import-free).
        tr = i18n.for_sess(sess)
        delivered = 0
        for entry in entries:
            # The marker heads the answer, so it takes the answer's
            # direction (multilingual design 5.7, rule 5), the one it was
            # built in; judged from the text only when that is not known.
            is_rtl = getattr(entry, "is_rtl", None)
            if is_rtl is None:
                is_rtl = detect_rtl(entry.rich_text)
            marker = held_message.late_marker_text(tr.directed(is_rtl),
                                                   entry.held_seconds())
            # The plain fallback is not a rich message: no is_rtl there,
            # so an English marker needs no marks (rich=False).
            plain_marker = held_message.late_marker_text(
                tr.directed(is_rtl, rich=False), entry.held_seconds())
            try:
                sent = await send_rich_message(
                    int(chat_id), f"{marker}\n\n{entry.rich_text}",
                    is_rtl=is_rtl,
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
                # Its files reply to the prompt (a chunked plain text has
                # no one message that is the answer).
                OUTBOX.answer_gone(entry.key)
                if await self._deliver_held_as_plain_text(entry, plain_marker):
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
                OUTBOX.answer_landed(entry.key, sent["message_id"])
                self.registry.track_message(
                    sent["message_id"], sess.name,
                    resolve_chat_id_int(sess) or 0,
                )
            else:
                OUTBOX.answer_gone(entry.key)
        if delivered:
            log.info("[%s] delivered %d held answer(s) after the mute lifted",
                     sess.label, delivered)
        return delivered

    # ── files Claude sends (SendUserFile, aipager/bot/outbox.py) ──────────

    @staticmethod
    def _files_chat(sess: TrackedSession):
        """The chat a session's files go to and are keyed under: the same
        key ``_hold_answer`` files its answers under."""
        return resolve_chat_id_int(sess) or resolve_chat_id(sess)

    def _held_key_now(self, sess: TrackedSession) -> str | None:
        try:
            from aipager.bot.held import HELD
            return HELD.newest_key(self._files_chat(sess), sess.name)
        except Exception:  # pragma: no cover - defensive
            return None

    def _file_audit_ctx(self, sess: TrackedSession) -> dict:
        """Who and where, for each file's audit record."""
        try:
            driver = self._driver_user(sess)
        except Exception:
            driver = None
        try:
            scope_label = self._scope_label(sess.scope_chat_id)
        except Exception:
            scope_label = ""
        return {"session": sess.name, "label": sess.label,
                "user_id": driver.id if driver else None,
                "username": driver.label if driver else "",
                "scope_label": scope_label,
                "scope_chat_id": sess.scope_chat_id or None}

    def _audit_refused_files(self, sess: TrackedSession, context: dict) -> None:
        """The hook refused files Claude asked to send (and told Claude
        why): one ``FileRefused`` record per file. Nothing in the chat."""
        from aipager.bot import outbox
        outbox.OUTBOX.take_request(sess.name, context.get("tool_use_id"))
        ctx = self._file_audit_ctx(sess)
        for f in context.get("files") or ():
            if not isinstance(f, dict):
                continue
            path = f.get("path") if isinstance(f.get("path"), str) else ""
            code = f.get("code") if isinstance(f.get("code"), str) else ""
            reason = f.get("reason") if isinstance(f.get("reason"), str) else ""
            name = outbox.shown_name(path) if path else "?"
            log.info("[%s] turn file refused: %s (%s)", sess.label, name, code)
            outbox.audit_file(ctx, outbox.ACTION_REFUSED, name, None,
                              f"{code}: {reason}" if reason else code)

    def _start_file_copy(self, sess: TrackedSession, context: dict) -> None:
        """A SendUserFile ran: copy its files now (in a thread), so a later
        change to them changes nothing that is sent. The finish path waits
        for the copy before it delivers."""
        from aipager.bot import outbox
        req = outbox.OUTBOX.files_for_ran(
            sess.name, context.get("tool_use_id"), context.get("tool_input"),
            context.get("tool_response"), context.get("cwd") or sess.cwd)
        if req is None or not req.files:
            log.warning("[%s] turn files: no readable file list", sess.label)
            return
        if self._files_turn_live(sess):
            self._track_file_copy(sess, self._checked_file_copy(sess, req, context))
            return
        if self._files_hookless_candidate(sess):
            # The last turn ended and no prompt opened another, yet Claude
            # works: a turn Claude Code began with no hook (a queued
            # message it dequeued, a Stop hook's feedback). Nothing is
            # read until its transcript shows that start.
            self._track_file_copy(sess, self._copy_in_hookless_turn(
                sess, req, context))
            return
        # No turn opened by a prompt (or its job) runs: not a call of
        # Claude's (a forged datagram, a replay). Nothing is read.
        log.warning("[%s] turn files: a send with no turn running was "
                    "dropped", sess.label)
        self._audit_unverified(sess, req.files)

    def _audit_unverified(self, sess: TrackedSession, files) -> None:
        from aipager.bot import outbox
        ctx = self._file_audit_ctx(sess)
        for entry in list(files)[:100]:
            outbox.audit_file(ctx, outbox.ACTION_FAILED,
                              outbox.shown_name(entry), None,
                              outbox.CAUSE_UNVERIFIED)

    def _track_file_copy(self, sess: TrackedSession, coro) -> None:
        """Run a copy (a coroutine that returns its batch, or None) as a
        tracked task: the finish path waits for it."""
        from aipager.bot import outbox
        if coro is None:
            return
        task = asyncio.create_task(coro)
        outbox.OUTBOX.track(sess.name, task)
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    def _checked_file_copy(self, sess: TrackedSession, req, context: dict,
                           turn_wall: float | None = None,
                           transcript: str | None = None):
        """The copy of a call made in a live files turn (or in the hookless
        turn begun after *turn_wall*, shown in *transcript*), as a
        coroutine, or None when it is not copied (an id already copied or
        none, no chat). The id is recorded here, with no await before
        it."""
        from aipager import policy_snapshot
        from aipager.bot import outbox
        if not sess.files_call_used(req.tool_use_id):
            # This call was copied already (a replayed datagram), or has no
            # id to find it by.
            log.warning("[%s] turn files: a call with no id or one already "
                        "copied was dropped", sess.label)
            self._audit_unverified(sess, req.files)
            return None
        self.registry.mark_dirty()
        chat_id = self._files_chat(sess)
        if not chat_id:
            log.warning("[%s] turn files: no chat to send them to", sess.label)
            return None
        try:
            snap = policy_snapshot.read_snapshot(sess.name)
        except Exception:
            snap = None
        bound, allowed = outbox.allowed_root_for(snap, req.cwd)
        ctx = self._file_audit_ctx(sess)

        # The transcript and the turn start the daemon recorded when the
        # turn opened (state.py files_turn_opened), never this datagram's.
        transcript_path = (sess.files_transcript if transcript is None
                           else transcript) or ""
        if turn_wall is None:
            turn_wall = sess.files_turn_wall
        prompt_id = sess.trigger_msg_id or None

        async def _copy():
            batch = await asyncio.to_thread(
                outbox.copy_batch, sess.name, chat_id, req.files, req.cwd,
                req.caption, req.display, req.agent_id or (
                    context.get("agent_id") or ""),
                bound=bound, allowed_root=allowed,
                tool_use_id=req.tool_use_id, transcript_path=transcript_path,
                prompt_id=prompt_id, turn_wall=turn_wall)
            for failure in batch.failures:
                outbox.audit_file(ctx, outbox.ACTION_FAILED, failure.name,
                                  failure.size, failure.cause)
            outbox.OUTBOX.finish_copy(batch)
            return batch
        return _copy()

    @staticmethod
    def _files_hookless_candidate(sess: TrackedSession,
                                  working: bool = True) -> bool:
        """No files turn is live (checked first), a turn of the
        conversation it looked in has ended (``files_turn_closed_wall``),
        and Claude is working:
        possibly a turn Claude Code started with no hook. Only a candidate
        (a forged PreToolUse makes an idle session BUSY too): the
        transcript must show the start (:meth:`_copy_in_hookless_turn`).
        Not *working*: the request a PreToolUse brings, before that hook
        moved the idle session to working (it is only kept, never read)."""
        return (sess.files_turn_closed_wall > 0 and bool(sess.files_transcript)
                and (not working
                     or sess.status in (Status.BUSY, Status.INTERACTIVE)))

    async def _copy_in_hookless_turn(self, sess: TrackedSession, req,
                                     context: dict):
        """A call that may be from a turn Claude Code began with no hook:
        copied only once the transcript noted
        for the conversation shows the start after the last turn ended
        (outbox.hookless_turn_started; waits for it at most
        ``outbox.TRANSCRIPT_WAIT_SECONDS``). Otherwise nothing is read
        and each file is audited ``unverified``; when Claude's own
        transcript shows this very call, the chat gets a line per file it
        lists (names from that line, never from the datagram), so nobody
        is left believing it was sent. Returns the batch, or None."""
        from aipager.bot import outbox
        since = sess.files_turn_closed_wall
        transcript = sess.files_transcript
        # One wait per session at a time (forged reports join it; None
        # when too many have: dropped like a failed wait, with no scan).
        started = await outbox.OUTBOX.wait_hookless(sess.name, transcript,
                                                    since)
        if started:
            # This call is from the turn that start began, even if that
            # turn has ended by now (its Stop can come right after); it is
            # looked for in that same transcript, written after *since*.
            log.info("[%s] turn files: the transcript shows a turn begun "
                     "with no prompt hook", sess.label)
            coro = self._checked_file_copy(sess, req, context, turn_wall=since,
                                           transcript=transcript)
            return await coro if coro is not None else None
        log.warning("[%s] turn files: no start of a turn after the last one "
                    "ended, a send was dropped", sess.label)
        self._audit_unverified(sess, req.files)
        if started is None:
            return None
        names = await asyncio.to_thread(
            outbox.transcript_call_files, transcript,
            req.agent_id or (context.get("agent_id") or ""),
            req.tool_use_id, since)
        if not names or not sess.files_call_used(req.tool_use_id):
            return None
        self.registry.mark_dirty()
        batch = outbox.unconfirmed_batch(sess.name, names,
                                         prompt_id=sess.trigger_msg_id or None,
                                         tool_use_id=req.tool_use_id)
        outbox.OUTBOX.finish_copy(batch)
        return batch

    @staticmethod
    def _files_turn_live(sess: TrackedSession) -> bool:
        """A turn opened by prompt evidence (a prompt the daemon typed, a
        UserPromptSubmit) still runs, or its background job does: the only
        time Claude can be calling SendUserFile. A tool call's hook alone
        never counts: a forged PreToolUse moves an idle session to BUSY by
        itself (review 2026-10-18, rev-iter2-001)."""
        return sess.files_turn_open and (
            sess.status in (Status.BUSY, Status.INTERACTIVE)
            or sess.job_background_open())

    @staticmethod
    def _files_work_running(sess: TrackedSession) -> bool:
        """Work of this session still runs (a background job, a tool call):
        a call it made may not be in its transcript yet (Claude Code
        writes it lazily)."""
        try:
            return bool(sess.job_background_open()
                        or sess.work_in_flight(time.monotonic()))
        except Exception:  # pragma: no cover - defensive
            return False

    def _deliver_turn_files(self, sess: TrackedSession, anchor,
                            prompt_id, held_before: str | None):
        """Send the files Claude sent this turn, after its answer (*anchor*:
        the answer's message, else the prompt), in a background task: the
        finish never waits for an upload (review 2026-10-17, rev-iter1-003).

        What belongs to this turn is taken NOW (its batches and the copies
        still running), and so is whether its answer was held, so a later
        turn's files and answers change nothing. The task runs after the
        session's earlier deliveries (one at a time, in order), waits for
        the copies (at most 30 s), checks each call is in the transcript,
        then sends, or queues behind the held answer or a mute. Returns the
        task (None when there is nothing to send). Never raises."""
        try:
            from aipager.bot import outbox
            box = outbox.OUTBOX
            # The answer this turn's files reply to: a batch of this turn
            # kept back and found at a later finish replies to it too.
            box.note_answer(sess.name, prompt_id, anchor)
            if not box.has_batches(sess.name):
                return None
            batches = box.take_batches(sess.name)
            copies = box.claim_copies(sess.name)
            held_now = self._held_key_now(sess)
            held = held_now if held_now and held_now != held_before else None
            return self._start_delivery(sess, batches, copies, anchor,
                                        prompt_id, held)
        except Exception:
            log.warning("[%s] turn files: delivery not started", sess.label,
                        exc_info=True)
            return None

    def _start_delivery(self, sess: TrackedSession, batches, copies, anchor,
                        prompt_id, held: str | None):
        """Run :meth:`_turn_files_now` for these batches in the session's
        delivery task chain (after its earlier deliveries)."""
        from aipager.bot import outbox
        audit = self._file_audit_ctx(sess)

        async def _run() -> None:
            await self._turn_files_now(sess, batches, copies, anchor,
                                       prompt_id, held, audit)
        return outbox.OUTBOX.run_delivery(sess.name, _run, _BACKGROUND_TASKS)

    async def _turn_files_now(self, sess: TrackedSession, batches, copies,
                              anchor, prompt_id, held: str | None,
                              audit: dict) -> None:
        """:meth:`_deliver_turn_files`'s task. Never raises."""
        try:
            from aipager.bot import outbox
            box = outbox.OUTBOX
            try:
                if copies:
                    try:
                        await box.wait_tasks(copies)
                    finally:
                        # One still running goes into the store when it ends.
                        box.unclaim([t for t in copies if not t.done()])
                for task in copies:
                    batch = outbox.task_batch(task)
                    if batch is not None and (batch.items or batch.failures):
                        batches.append(batch)
                    box.unclaim([task])
            finally:
                # Cancelled while waiting: no copy stays claimed for good.
                box.unclaim(copies)
            batches = await self._verified_batches(sess, batches, audit)
            if not batches:
                return
            chat_id = self._files_chat(sess)
            own_prompt = prompt_id or None
            # A batch kept back from an earlier turn of this session (its
            # call was not in the transcript yet while work ran) replies to
            # that turn's answer when it is known, else to its prompt: one
            # delivery per earlier turn, in order, before this turn's own.
            groups: list[tuple[int | None, list]] = []
            own: list = []
            for batch in batches:
                if batch.prompt_id is None or batch.prompt_id == own_prompt:
                    own.append(batch)
                elif groups and groups[-1][0] == batch.prompt_id:
                    groups[-1][1].append(batch)
                else:
                    groups.append((batch.prompt_id, [batch]))
            pendings = [(outbox.Pending(
                session=sess.name, chat_id=chat_id,
                anchor=box.answer_for(sess.name, pid) or pid, held_key=None,
                prompt_id=pid, batches=group, created=time.monotonic(),
                audit=audit), None) for pid, group in groups]
            if own:
                pendings.append((outbox.Pending(
                    session=sess.name, chat_id=chat_id,
                    anchor=(anchor or None), held_key=None,
                    prompt_id=own_prompt, batches=own,
                    created=time.monotonic(), audit=audit), held))
            for pending, held_key in pendings:
                if held_key:
                    pending.held_key, pending.anchor = held_key, None
                    box.queue(pending)
                    log.info("[%s] turn files held behind the held answer",
                             sess.label)
                elif MUTE.is_muted(chat_id):
                    box.queue(pending)
                    log.info("[%s] turn files held: the chat is flood-muted",
                             sess.label)
                elif box.has_pending(sess.name):
                    # Older files of this session still wait (a mute that
                    # just lifted, a held answer not landed yet): these go
                    # after them, never before (review 2026-10-18,
                    # rev-iter2-003).
                    box.queue(pending)
                    log.info("[%s] turn files wait behind earlier ones",
                             sess.label)
                    await self.flush_turn_files(sess)
                else:
                    await self._send_turn_files(sess, pending)
        except Exception:
            log.warning("[%s] turn files: delivery failed", sess.label,
                        exc_info=True)

    async def _verified_batches(self, sess: TrackedSession, batches,
                                audit: dict) -> list:
        """The batches whose call is in the session's transcript (each
        waits for it, at most ``outbox.TRANSCRIPT_WAIT_SECONDS``). One not
        found while the session's work still runs (a background agent's
        call, whose transcript Claude Code writes lazily) goes back into
        the store, to be looked for again at the job's next finish or as
        an orphan once nothing runs (review 2026-10-18, rev-iter2-002).
        Any other is dropped: its copies deleted, a FileFailed record per
        file (cause ``unverified``), nothing in the chat."""
        from aipager.bot import outbox
        ordered = sorted(batches, key=lambda b: b.created)
        found = await asyncio.gather(*(
            outbox.verify_batch(b) for b in ordered if not b.unconfirmed))
        found_iter = iter(found)
        out = []
        for batch in ordered:
            # A batch of chat lines only (Claude's own transcript showed the
            # call): looked for already, nothing in it to send.
            if batch.unconfirmed or next(found_iter):
                out.append(batch)
                continue
            if self._files_work_running(sess):
                log.info("[%s] turn files: call %s not in the transcript yet, "
                         "kept while work runs", sess.label,
                         batch.tool_use_id or "?")
                outbox.OUTBOX.add_batch(batch)
                continue
            log.warning("[%s] turn files: call %s not in the transcript, "
                        "nothing sent", sess.label, batch.tool_use_id or "?")
            for item in batch.items:
                outbox.audit_file(audit, outbox.ACTION_FAILED, item.name,
                                  item.size, outbox.CAUSE_UNVERIFIED)
                outbox.delete_copy(item)
            # Claude's own transcript shows this call in the turn (its
            # folder or file list did not match): a line per file that
            # line lists, so nobody is left believing it went. A forged
            # call cannot add such a line, and the names are Claude's.
            names = await asyncio.to_thread(
                outbox.transcript_call_files, batch.transcript_path,
                batch.agent_id, batch.tool_use_id, batch.turn_wall)
            if names:
                out.append(outbox.unconfirmed_batch(
                    sess.name, names, prompt_id=batch.prompt_id,
                    tool_use_id=batch.tool_use_id))
        return out

    async def _send_turn_files(self, sess: TrackedSession, pending) -> bool:
        from aipager.bot import outbox
        if not self._app:
            outbox.OUTBOX.requeue_front(pending)
            return False
        done = await outbox.deliver(self._app.bot, pending, sess.label,
                                    i18n.for_sess(sess))
        if not done:
            outbox.OUTBOX.requeue_front(pending)
        return done

    async def flush_turn_files(self, sess: TrackedSession) -> int:
        """Deliver this session's files that waited (for a mute to lift, or
        for the held answer they follow to land), oldest first, then any
        orphan copies: in the session's delivery task (after the one that
        runs, if any), so neither the monitor's tick nor a held answer's
        flush waits for an upload.
        Driven by the session monitor's tick and by
        :meth:`flush_held_answers`. Returns how many waiting deliveries
        were handed to the task."""
        from aipager.bot import outbox
        from aipager.bot.held import HELD
        box = outbox.OUTBOX
        chat_id = self._files_chat(sess)
        if not chat_id or MUTE.is_muted(chat_id) or sess.name in _HELD_FLUSHING:
            return 0
        # A held answer that is no longer held and never landed (dropped by
        # a cap, expired): its files reply to the prompt.
        for p in list(box.pending.get(chat_id) or ()):
            if (p.session == sess.name and p.held_key
                    and not HELD.has_key(chat_id, p.held_key)):
                box.answer_gone(p.held_key)
        due = box.due(chat_id, sess.name)
        if due:
            async def _send_due() -> None:
                for i, pending in enumerate(due):
                    try:
                        done = await self._send_turn_files(sess, pending)
                    except Exception:
                        log.warning("[%s] turn files: delivery failed",
                                    sess.label, exc_info=True)
                        for batch in pending.batches:
                            for item in batch.items:
                                outbox.delete_copy(item)
                        done = True
                    if not done:
                        # Kept in order: the one that failed went back to
                        # the front, the rest follow it.
                        box.requeue_after(pending, due[i + 1:])
                        return
            box.run_delivery(sess.name, _send_due, _BACKGROUND_TASKS)
        self._adopt_orphan_files(sess, chat_id)
        return len(due)

    def _adopt_orphan_files(self, sess: TrackedSession, chat_id) -> None:
        """Copied files no finish took (the copy ended after the finish had
        stopped waiting, or a background agent's call the transcript had
        not caught up with): delivered once the session is idle with no
        finish out and no job or work still running, each turn's batches
        as replies to the prompt of the turn they were copied in, and only
        once their call is found in the transcript."""
        from aipager.bot import outbox
        box = outbox.OUTBOX
        if (sess.status is not Status.IDLE or box.in_flight.get(sess.name)
                or not box.batches.get(sess.name)):
            return
        if sess.job_background_open() or sess.work_in_flight(time.monotonic()):
            return
        gate = sess.finish_gate
        if isinstance(gate, asyncio.Event) and not gate.is_set():
            return
        now = time.monotonic()
        if any(now - b.created < _ORPHAN_FILES_SECONDS
               for b in box.batches[sess.name]):
            return
        # One delivery per turn, in order, each replying to its own prompt.
        # No answer of theirs is held any more (their turns ended earlier).
        groups: list[tuple[int | None, list]] = []
        for batch in box.take_batches(sess.name):
            if groups and groups[-1][0] == batch.prompt_id:
                groups[-1][1].append(batch)
            else:
                groups.append((batch.prompt_id, [batch]))
        for prompt_id, batches in groups:
            try:
                self._start_delivery(sess, batches, [], prompt_id, prompt_id,
                                     None)
            except Exception:
                log.warning("[%s] turn files: delivery not started", sess.label,
                            exc_info=True)

    async def _deliver_job_interim(
        self, sess: TrackedSession, content: str, turn_end_wall: float,
        *, reply_to=None,
    ) -> int | None:
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

        Returns the id of the message that landed (the files Claude sent
        this turn reply to it), or None.
        """
        if not content:
            return None
        label = sess.label
        digest = hashlib.md5(content.encode("utf-8")).hexdigest()
        if sess.was_delivered(digest):
            log.info("[%s] job interim answer already delivered — not "
                     "re-sending", label)
            return None
        sess.remember_delivered(digest, pending=True)
        is_rtl = self._answer_rtl(sess, content)
        agents = self._agents_snapshot(sess, is_rtl)
        lead_md = _result_line_md(label, i18n.for_sess(sess).directed(is_rtl))
        lead_plain = _result_line_plain(label)
        # The same byte budget as the finish path: an answer over the
        # ceiling keeps its head at a markdown-safe cut, with the ⏳ line
        # still last, and the full text follows as an attachment.
        body_limit = _RICH_LIMIT - len(f"{lead_md}\n\n".encode("utf-8"))
        if agents:
            body_limit -= agents["reserve"]
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
            rich_text = _with_agents_block(rich_text, agents["line"])
        plain_text = f"{lead_plain}\n\n{content}"
        chat_id = resolve_chat_id_int(sess)
        # The interim turn's own message: the queued pick-up at this Stop
        # has already moved `sess.trigger_msg_id` to the next one.
        if reply_to is None:
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
            return None
        except RichMessageFloodBanned:
            # HELD (8.29 R6); confirmed by flush_held_answers once it lands.
            self._hold_answer(sess, held_rich, plain_text, reply_to,
                              digests=[digest], selected_wall=turn_end_wall,
                              is_rtl=is_rtl)
            return None
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
                    if not landed:
                        msg_id = fallback.message_id
                    landed = True
                    self.registry.track_message(
                        fallback.message_id, sess.name,
                        resolve_chat_id_int(sess) or 0,
                    )
                except Exception:
                    log.warning("[%s] job interim plain-text chunk failed",
                                label, exc_info=True)
        if not landed:
            return None
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
                filename = f"{label}_answer.md"
                await self._app.bot.send_document(
                    resolve_chat_id(sess),
                    document=document_upload(content.encode("utf-8"), filename),
                    filename=filename,
                    reply_to_message_id=msg_id or None,
                )
            except Exception:
                log.warning("[%s] job interim attachment failed", label,
                            exc_info=True)
        return msg_id or None

    async def _handle_job_interim(
        self, sess: TrackedSession, context: dict, turn_end_wall: float,
        *, gate=None, reply_to=None,
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
        # The hook closed the turn at this Stop, but the job goes on: its
        # agent's tool calls and its continuation run under the job's
        # rules, so a message sent meanwhile must still only narrow them
        # (roadmap 8.77). Before the drain below, which may send one.
        mark_turn_open(sess.name)
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
        prompt_id = reply_to if reply_to is not None else sess.trigger_msg_id
        held_before = self._held_key_now(sess)
        landed = await self._deliver_job_interim(sess, content, turn_end_wall,
                                                 reply_to=reply_to)
        if gate is not None:
            self._release_finish_gate(sess, gate)
        # This interim turn's files, after its answer (in the background).
        self._deliver_turn_files(sess, landed or prompt_id, prompt_id,
                                 held_before)
        await self._drain_next_queued(sess)

    def _end_job_turn(self, sess: TrackedSession):
        """A background job closed with no Stop of its own (its grace ran
        out, or its agent went silent): its turn is over (roadmap 8.77).
        Called before the closing branch awaits anything, so a turn that
        starts meanwhile keeps its own turn state: the hook's turn-open
        file (``_handle_job_interim`` reopened it) is cleared here, at
        once. Returns the job's sender for :meth:`_drain_after_job`, or
        :data:`_JOB_STILL_OPEN` when the job is not closed after all."""
        if sess.job_background_open():
            return _JOB_STILL_OPEN
        clear_turn_open(sess.name)
        job_sender, sess.turn_sender_id = sess.turn_sender_id, None
        return job_sender

    async def _drain_after_job(self, sess: TrackedSession, job_sender) -> None:
        """Send the message the turn-sender hold kept back behind the job
        that just closed (:meth:`_end_job_turn`), as its own turn, as the
        job's real end would have. Only that message: other held messages
        keep waiting for the next real turn end, as before (a "lost" agent
        may still be alive). Nothing is sent once another turn has started
        while the job's closing notice went out."""
        if (job_sender is _JOB_STILL_OPEN or job_sender is None
                or sess.status is not Status.IDLE or not sess.pending_queue):
            return
        head = sess.pending_queue[0][4]
        if head is None or not self._sender_differs_from(sess, job_sender, head):
            return
        await self._drain_next_queued(sess)

    async def _notify_parked(self, sess: TrackedSession, job: str) -> None:
        """The park notice (P6): once per job, persisted
        (``park_notified_job``), so neither the next tick nor a daemon
        restart repeats it; with its "Bring it back now" button."""
        if not job or sess.parked_job != job or sess.park_notified_job == job:
            return
        if not self._app:
            return
        from aipager.bg_text import BG_PARK_NOTICE
        from aipager.bot import session_parity
        from aipager.bot.transport import resolve_chat_id, resolve_chat_id_int
        sess.park_notified_job = job  # claimed before the send's await
        self.registry.mark_dirty()
        log.info("[%s] parked on background job %s: notice sent", sess.label, job)
        tr = i18n.for_sess(sess)
        try:
            await send_text(
                self._app.bot, chat_id=resolve_chat_id(sess),
                text=tr.html(BG_PARK_NOTICE, session=sess.label), parse_mode="HTML",
                reply_markup=session_parity.bg_park_keyboard(
                    self, resolve_chat_id_int(sess) or 0, sess, tr))
        except Exception:
            log.warning("[%s] park notice failed", sess.label, exc_info=True)

    async def _release_held(self, sess: TrackedSession) -> None:
        """A bring-back from Claude Code's background is done and its
        relaunched claude is up: the first message held for the park goes
        out (the rest drain at turn ends, as ever). Behind every release
        site (the bring-back itself, the relaunched claude's SessionStart,
        the monitor's backstop): ``claim_bg_release`` checks and clears the
        release with no await in between, so it happens once."""
        if not sess.claim_bg_release():
            return
        log.info("[%s] back from the background: releasing %d held message(s)",
                 sess.label, len(sess.pending_queue))
        await self._drain_next_queued(sess)

    async def _drain_next_queued(self, sess: TrackedSession) -> None:
        """Pop and inject the next queued prompt, one at a time.

        Extracted from its original inline spot at the end of the
        Finished-card path (design.md "model Claude Code background-agent
        jobs") so that path and :meth:`_handle_job_interim` share one
        implementation — a message queued while a job's background work is
        still open drains on the very next idle moment (interim OR real)
        rather than waiting specifically for the real Finished. A no-op
        when the queue is empty, and while a typed ``/model`` may still be
        checking its model (the queue then goes on with
        :meth:`_settle_model_hold`).
        """
        if not sess.pending_queue:
            return
        if sess.parked_job:
            # Parked on a Claude Code background job (a linked job's turn
            # end comes here): typed now, the message would go to the
            # parked client. It stays held until the session is back.
            return
        if sess.model_check_running():
            # A typed /model may still be checking its model, and text typed
            # now is lost (a turn whose answer went out after the /model).
            # The queue goes on once the check is over (_settle_model_hold).
            sess.model_hold = True
            return
        if sess.prompt_wait_carry:
            # A turn's end with an agent's prompt still open (roadmap
            # 8.120): typed now, the message would land in that dialog.
            # It goes out once the last prompt is answered.
            return
        if self._turn_sender_differs(sess, sess.pending_queue[0][4]):
            # Roadmap 8.77 (D-H): the finish path has just started a turn
            # for a message Claude had queued, and it is someone else's.
            # Sent now, this one would join that turn; it stays held and
            # drains at that turn's end, as its own turn.
            return
        if sess.bg_release_pending and not sess.claim_bg_release():
            # A bring-back's release is pending and a safety halt waits out
            # that bring-back (``bg_release_blocked``): the halt drops what
            # is held, so nothing goes out meanwhile.
            return
        # A drain while a bring-back's release is still pending (its
        # relaunched claude's SessionStart never came, and a turn typed in
        # the terminal ended) claims that release above: one site ever
        # releases, so a later own SessionStart or the backstop adds
        # nothing to the turn this message starts.
        entry = sess.pending_queue.pop(0)
        (
            queued_text, queued_trigger, _queued_at,
            queued_reply_context, queued_driver_user_id,
        ) = entry
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
            sess.turn_prompt = queued_text
            sess.last_prompt_driver_user_id = queued_driver_user_id
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
        if ok is PROMPT_DIALOG:
            # It met a dialog while it was typed (roadmap 8.143): back at
            # the head, it goes out once that dialog is answered (or at the
            # turn's end, when the dialog is not on record yet).
            sess.pending_queue.insert(0, entry)
            if sess.any_dialog_open():
                from aipager.bot.handlers import _claim_dialog_hold
                _claim_dialog_hold(sess)
            self.registry.mark_dirty()
            log.info("[%s] Held again (a prompt opened while it was typed): %s",
                     sess.label, queued_text[:80])
            return
        if ok and starts_turn:
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
        if ok:
            log.info("[%s] Flushed queued: %s", sess.label, queued_text[:80])
        else:
            # Popped and not sent: this held message is gone (R3).
            await self._mark_not_delivered(sess, [{"msg_id": queued_trigger}])
            if ok is PROMPT_REFUSED:
                # A slash command its sender may not send (roadmap 8.74),
                # held before the check could run (a /new conflict card)
                # or from a sender removed while it waited.
                await self._reply_needs_admin_queued(sess, queued_trigger)

    async def _settle_dialog_hold(self, sess: TrackedSession) -> None:
        """The messages held while a dialog was open (``dialog_hold``),
        once no dialog is open: when it was answered, they go to Claude now,
        in the order they were sent, through :meth:`_drain_next_queued`;
        when its turn or its session ended instead, the queue is left to
        the paths that handle that (the turn end's drain, /stop, the
        session end). Either way the "answer it first" notices are deleted
        (they were only about the wait) and the hold ends. A no-op while a
        dialog is open, or while another settle of the same hold runs.

        Stops releasing at a held message that may not join the running
        turn (another person's, roadmap 8.77): it and those after it wait
        for the turn's end, as before. A message arriving while this runs
        is held behind the others (``dialog_release_pending``) and goes out
        in this same pass.

        A terminal dialog's hold (roadmap 8.127) is left alone while it
        settles after the dialog's close (``"settling"``): the session
        monitor marks it answered once the session has settled."""
        hold = sess.dialog_hold
        if (hold is None or sess.dialog_settling or sess.any_dialog_open()
                or hold.get("settling")):
            return
        sess.dialog_settling = True
        try:
            if hold.get("closed") == "answered" and sess.status is not Status.GONE:
                while sess.pending_queue and not sess.any_dialog_open():
                    before = len(sess.pending_queue)
                    await self._drain_next_queued(sess)
                    if len(sess.pending_queue) >= before:
                        break  # the head may not join this turn
        finally:
            sess.dialog_settling = False
            notices = list(hold.get("notices") or [])
            if (sess.dialog_hold is hold and sess.any_dialog_open()
                    and sess.pending_queue):
                # Another dialog opened while they went out: the rest are
                # held for that one now, and its first new message gets a
                # notice of its own.
                hold.pop("closed", None)
                hold["entered_at"] = sess.dialog_wait_stamp()
                hold["terminal"] = not sess.dialog_is_open()
                hold["told"] = set()
                hold["notices"] = []
            elif sess.dialog_hold is hold:
                sess.dialog_hold = None
            await self._delete_hold_notices(sess, notices)

    async def _settle_model_hold(self, sess: TrackedSession) -> None:
        """The messages held while a typed ``/model`` checked its model
        (``model_hold``), once that is over: the first goes to Claude now as
        its own turn, the rest at that turn's end, as any queued message
        does (:meth:`_drain_next_queued`, which also keeps them while an
        agent's prompt is still open). Nothing is sent while a dialog is
        open or a turn runs (the session is not idle): the paths that handle
        those drain the queue."""
        if not sess.model_hold or sess.model_check_running():
            return
        sess.model_hold = False
        if sess.status is Status.IDLE and sess.pending_queue:
            await self._drain_next_queued(sess)

    async def _drop_dialog_hold(self, sess: TrackedSession) -> None:
        """The messages held for a dialog were dropped (/clearqueue): its
        notices no longer say anything true, and the next message held for
        the dialog gets a notice of its own."""
        hold, sess.dialog_hold = sess.dialog_hold, None
        if hold is not None:
            await self._delete_hold_notices(sess, list(hold.get("notices") or []))

    async def _delete_hold_notices(self, sess: TrackedSession, notices) -> None:
        """Delete the "answer it first" notices ``[(chat_id, msg_id)]``.
        Best-effort."""
        if not notices or self._app is None:
            return
        for chat_id, msg_id in notices:
            try:
                await self._app.bot.delete_message(chat_id=chat_id,
                                                   message_id=msg_id)
            except Exception:
                log.debug("[%s] could not delete the held-message "
                          "notice %s", sess.label, msg_id, exc_info=True)

    async def _reply_needs_admin_queued(
        self, sess: TrackedSession, msg_id: int | None,
    ) -> None:
        """Say why a held slash command was dropped, as a reply to it in
        the session's own chat. Best-effort."""
        chat_id = resolve_chat_id_int(sess)
        if not chat_id or self._app is None:
            return
        kwargs = {"chat_id": chat_id,
                  "text": shown(i18n.for_chat(chat_id), NEEDS_ADMIN_REPLY)}
        if msg_id:
            kwargs["reply_to_message_id"] = msg_id
        try:
            await send_text(self._app.bot, **kwargs)
        except Exception:
            log.debug("[%s] could not send the needs-admin reply",
                      sess.label, exc_info=True)

    def _release_finish_gate(self, sess: TrackedSession, gate) -> None:
        """Let newer turns' cards through (roadmap 8.57). Idempotent; only
        the finish that owns the session's current gate clears it."""
        gate.set()
        if sess.finish_gate is gate:
            sess.finish_gate = None
            sess.finishing_turn = None

    async def notify(self, sess: TrackedSession, event: str, context: dict) -> None:
        """Send appropriate Telegram notification for a state change.

        Counts itself in ``sess.notify_in_flight`` while it runs: the
        session monitor's orphan-card sweep never settles a card while
        any event for its session is still being handled (roadmap 8.57).
        """
        sess.notify_in_flight += 1
        try:
            await self._notify_event(sess, event, context)
            # A hook that closed a dialog messages were held for (or the
            # session monitor's backstop, ``dialog_hold_settle``): send
            # them on now (:meth:`_settle_dialog_hold`).
            if sess.dialog_hold is not None and not sess.any_dialog_open():
                try:
                    await self._settle_dialog_hold(sess)
                except Exception:
                    log.warning("[%s] releasing the messages held for a "
                                "dialog failed", sess.label, exc_info=True)
            # The cards of terminal dialogs that closed (roadmap 8.127),
            # wherever the close was found: shown closed now.
            if sess.term_dialog_closing:
                try:
                    from aipager.bot import resume_dialog  # local: import cycle
                    await resume_dialog.close_cards(self, sess)
                except Exception:
                    log.warning("[%s] closing a terminal dialog's card failed",
                                sess.label, exc_info=True)
            # The statusline reported a typed /model's switch (or the
            # session monitor's backstop, ``model_hold_settle``, saw its
            # check's time run out): send on what was held for it. A no-op
            # while the check may still run.
            if sess.model_hold:
                try:
                    await self._settle_model_hold(sess)
                except Exception:
                    log.warning("[%s] releasing the messages held for a "
                                "model switch failed", sess.label, exc_info=True)
        finally:
            sess.notify_in_flight -= 1

    async def _notify_event(
        self, sess: TrackedSession, event: str, context: dict,
    ) -> None:
        """:meth:`notify`'s body."""
        # ── settle a dialog hold (the session monitor's backstop) ──
        # The work is done by `notify` itself, after every event.
        if event in ("dialog_hold_settle", "model_hold_settle"):
            return

        # ── a session moved into a Claude Code background job ──
        # The monitor saw the park: one notice per job, with "Bring it
        # back now" (P6). Never reaches the status branches below.
        if event == "bg_parked":
            await self._notify_parked(sess, context.get("job") or "")
            return
        # ── a dialog in Claude Code's terminal no hook tells of ──
        # The monitor saw it open, close, or its card fall due (roadmap
        # 8.127). Cards to close are closed by `notify` itself, after
        # every event.
        if event == "term_dialog":
            from aipager.bot import resume_dialog  # local: import cycle
            await resume_dialog.on_opened(self, sess)
            return
        # ── a session brought back from a background job ──
        # Its relaunched claude's first SessionStart (or the monitor's
        # backstop): the messages held for the park may go out now.
        if event == "bg_release":
            if not sess.bg_release_pending:
                return
            if sess.parked_job or sess.relaunch_in_flight:
                # The bring-back is still running (or the session parked
                # again): it releases them itself when it is done.
                sess.bg_start_seen = True
                return
            await self._release_held(sess)
            return

        # ── the mode a hook reported, while a mode switch waits on it ──
        # Before the app check: the switch's state is decided whatever
        # can be shown (aipager/bot/mode_switch.py).
        if event == "mode_reported":
            from aipager.bot import mode_switch  # local: import cycle
            await mode_switch.on_reported(self, sess, context)
            return

        # ── files Claude sends (SendUserFile) ──
        # Before the app check: recording, copying and auditing need no bot.
        if event == "send_files_requested":
            from aipager.bot.outbox import OUTBOX
            if not (self._files_turn_live(sess)
                    or self._files_hookless_candidate(sess, working=False)):
                # No turn or job runs: nothing Claude does can be asking.
                log.warning("[%s] turn files: a request with no turn running "
                            "was ignored", sess.label)
                return
            OUTBOX.record_request(sess.name, context.get("tool_use_id"),
                                  context.get("tool_input"),
                                  context.get("cwd") or sess.cwd,
                                  context.get("agent_id") or "")
            return
        if event == "send_files_ran":
            self._start_file_copy(sess, context)
            return
        if event == "send_files_refused":
            self._audit_refused_files(sess, context)
            return

        # ── Claude Code refused its risky-removal prompt (8.128) ──
        # Before the app check: the records need no bot.
        if event == "clock_lines_drop":
            await self._drop_clock_lines(sess, context.get("records"))
            return
        if event == "rm_refused":
            await self._on_rm_refused(sess, context)
            return
        if event == "rm_clock_disproven":
            await self._on_rm_clock_disproven(sess, context)
            return

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

        # ── roadmap 8.120: more than one open prompt ──
        if event == "extra_prompt":
            await self._on_extra_prompt(sess, context)
            return
        if event == "prompt_closed":
            await self._after_prompts_changed(sess, context)
            return
        if event == "prompts_carried":
            await self._on_prompts_carried(sess)
            return
        if event == "agent_prompts_stopped":
            # Roadmap 8.121 F5: the agents whose prompts these were were
            # stopped (Claude Code's kill chord): no hook closes them.
            await self._show_agents_stopped(sess, context.get("records") or ())
            return

        # ── A mute lifted and this session has an answer waiting (8.29) ──
        # Dispatched by `SessionMonitor._scan` on the 2 s tick it already
        # runs, so a held answer lands within one tick of the ban lifting
        # with no busy-wait, no timer and no new task.
        if event == "held_answer_flush":
            await self.flush_held_answers(sess)
            return

        # ── a turn's files waiting for a mute or a held answer ──
        if event == "turn_files_flush":
            await self.flush_turn_files(sess)
            return

        # ── the card a flood mute refused, owed while the turn runs (8.17c) ──
        # Dispatched by the same tick, AFTER this chat's held answers are
        # all out, and only once the chat can afford the ornament again.
        if event == "owed_card_flush":
            await self._send_owed_card(sess, reason="mute lifted")
            return

        # ── the answer's status block, kept current (roadmap 8.41) ──
        # Dispatched by `SessionMonitor._scan` whenever a block's edit
        # interval has passed; it is edited only when what it shows changed.
        if event == "agents_line_done":
            await self._tick_agents_lines(
                sess, context.get("now") or time.monotonic())
            return

        if event == "hook_memory_cap_hit":
            hook_name = context.get("hook", "aipager-hook")
            tool_name = context.get("tool", "")
            # "Report this" (roadmap 8.112): opens the problem report
            # preview in the owner's DM; only the owner's tap does
            # anything (report_flow re-checks it).
            from aipager.bot import report_flow  # local: import cycle
            has_owner = report_flow.resolve_owner(self) is not None
            tr = i18n.for_sess(sess)
            head = (tr.html(MEMCAP_HEAD_TOOL, session=label, tool=tool_name) if tool_name
                    else tr.html(MEMCAP_HEAD, session=label))
            text = f"{head}\n\n" + memory_cap_body(hook_name, has_owner, tr)
            extra = ({"reply_markup": report_flow.report_button_markup(
                report_flow.REPORT_THIS_BUTTON, tr=tr)} if has_owner else {})
            try:
                await bot.send_message(
                    resolve_chat_id(sess), text, parse_mode="HTML", **extra,
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
                    "driver_user_id": note_driver_id(n),
                } for n in queued)
                log.info("[%s] queued while busy: %s", label,
                         [n.get("msg_id") for n in queued])
                # Still waiting when its line is due: the "⏳ Queued" line
                # and its "⚡ Send now" button (bot/send_now.py).
                self._arm_queued_lines(sess, queued)
            await self._apply_consumption(sess, own)
            if expired:
                try:
                    preview = (expired[0].get("raw_text") or "")[:80]
                    tr = i18n.for_sess(sess)
                    await bot.send_message(
                        default_chat_id,
                        tr.html(QUEUE_TIMEOUT_PREVIEW, session=label, preview=preview)
                        if preview else tr.html(QUEUE_TIMEOUT, session=label),
                        parse_mode="HTML",
                    )
                except Exception:
                    log.debug("queue_pickup expiry notice failed",
                              exc_info=True)
            return

        if event == "peer_resolved":
            # Who sent the message that started this turn: the card's top
            # line shows it at its next render.
            sess.stream_dirty = True
            log.info("[%s] message from another session: %s (%s)", label,
                     context.get("status"), context.get("sender_session") or "-")
            return

        if event == "safety_blocked":
            tool = context.get("tool", "?")
            reason = context.get("reason", "")
            # On the FIRST block of a turn, cleanly halt the session
            # (interrupt Claude + cancel the spinner + back to IDLE) so
            # "thinking" stops automatically and doesn't hang. Sticky
            # repeats ("session halted …" — the hook denying every later
            # tool this turn) are audited only: no extra halt, no 🛑 spam.
            from aipager.bot import safety_notice
            sticky = safety_notice.is_sticky(reason)
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
            job_sender = self._end_job_turn(sess)
            sess.close_turn()  # no card for this turn from here (8.57)
            self._stop_animation(sess)
            elapsed_s = 0
            if sess.busy_started_at:
                elapsed_s = int(time.monotonic() - sess.busy_started_at)
            # Two forms of the same line ("session-name-on-every-message"):
            # the busy card settles to ITS glyph (✅ — the card's terminal
            # state), while a standalone notice, sent because no card is
            # left to settle, is the turn's result message and opens with
            # the result glyph like the idle path's standalone header.
            # aipager's own line, carrying no text of Claude's (it replaces
            # the card whole): the chat's direction.
            name = html_mod.escape(label)
            tr = i18n.for_sess(sess)
            text = finished_line(tr, f"✅ <b>{name}</b>", "html", seconds=elapsed_s)
            text_alone = finished_line(tr, f"{_RESULT_GLYPH} <b>{name}</b>", "html",
                                       seconds=elapsed_s)
            # The card's last word, written and retired under the
            # card-edit lock (`_settle_card_text`).
            if not await self._settle_card_text(
                    sess, text):
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
            await self._drain_after_job(sess, job_sender)
            return

        if event == "job_agents_lost":
            # The subagent TTL sweep emptied the last open agent for a
            # session sitting IDLE with a job open (design.md "model Claude
            # Code background-agent jobs" requirement 6) — a job cannot
            # wait forever. Produces the terminal "background agent lost"
            # card rather than a normal Finished: nothing new happened,
            # the agent just disappeared without ever reporting back, so
            # there is no answer to deliver. The job's turn is over, so the
            # message the turn-sender hold kept behind it drains now
            # (``_drain_after_job``, roadmap 8.77); anything else held
            # drains on the next real idle-transition, as before (design.md
            # Risks: a "lost" agent may still be alive).
            job_sender = self._end_job_turn(sess)
            sess.close_turn()  # no card for this turn from here (8.57)
            self._stop_animation(sess)
            elapsed_s = 0
            if sess.busy_started_at:
                elapsed_s = int(time.monotonic() - sess.busy_started_at)
            # aipager's own notice, no text of Claude's: the chat's direction.
            tr = i18n.for_sess(sess)
            shell = context.get("what", "agent") == "shell"
            if elapsed_s > 0:
                text = tr.html(SHELL_LOST_AFTER if shell else AGENT_LOST_AFTER,
                               session=label, clock=Safe(tr.clock(elapsed_s)))
            else:
                text = tr.html(SHELL_LOST if shell else AGENT_LOST, session=label)
            # Written and retired under the card-edit lock, as above.
            if not await self._settle_card_text(
                    sess, text):
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
            await self._drain_after_job(sess, job_sender)
            return

        if event == "tool_use":
            tool_summary = context.get("tool_summary", "")
            tool_name = context.get("tool_name", "")
            tool_input_full = context.get("tool_input_full")
            agent_id = context.get("agent_id", "")
            if agent_id and agent_id not in sess.active_subagents:
                # A tool call inside a subagent this turn does not own — a
                # background agent a previous turn launched, whose row the
                # new turn's reset dropped (roadmap 8.58). It is not this
                # turn's work: no parent row, no card earned, no edit.
                log.debug("[%s] tool call of detached agent %s — not on "
                          "this turn's card", sess.label, agent_id)
                return
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

        if event == "dialog_ended_in_terminal":
            # Roadmap 8.113: "No" chosen in Claude Code's own dialog at the
            # terminal (or Escape on a question there) ended the turn like
            # an interrupt, and no hook fires for that; the session monitor
            # saw the transcript's interrupt marker. End the turn the way
            # aipager's own typed Deny does, unless something moved on in
            # the meantime: a Telegram answer, another dialog, a hook, or
            # keys aipager typed into the dialog (their own path ends it).
            #
            # Two things are left as they are. What Claude's own queue
            # held is in the terminal's input box now (popAll): the person
            # at the terminal can send or edit it, as after any Escape
            # there, so it is not wiped; a message aipager held while the
            # dialog was open still goes out below, typed after it, as at
            # any turn end. An Escape while a tool allowed at the
            # terminal runs (no hook says the dialog closed) ends the turn
            # the same way; the card tells the two apart from the marker
            # (roadmap 8.152, transcript.terminal_end_kind).
            from aipager.bot.session_ops import _terminal_card_text
            from aipager.dtach import inject as dtach_inject
            entered = context.get("entered_at")

            def carried_on() -> bool:
                return (self.registry.get(sess.name) is not sess
                        or sess.status != Status.INTERACTIVE
                        or sess.interactive_entered_at != entered
                        or dtach_inject.last_write_at(sess.name) >= entered)

            # carried_on is checked once the card's lock is held, so a
            # Telegram answer that lands first is left alone. A prompt sent
            # as its own message keeps its fields there (8.99), and only
            # one shown during this wait is this dialog.
            perm = sess.pending_permission
            if not perm:
                sent = (sess.pending_prompt_msg or {}).get("perm") or {}
                if (sent.get("wait_started_at") or 0) >= sess.interactive_entered_at:
                    perm = sent
            refused = not (perm or {}).get("ask_question")
            # Roadmap 8.120: the interrupt ended Claude's own dialogs. A
            # running BACKGROUND agent's prompt is still open in the
            # terminal: the session keeps waiting on it, and the turn's end
            # is left to Claude's own hooks. A foreground agent (or one
            # whose launch is not known) dies with the interrupted turn,
            # its dialog with it: the turn ends below, every prompt closed.
            running = [p for p in sess.open_prompts()
                       if sess.agent_survives_interrupt(_prompt_owner(p))]
            if running:
                # Prompt by prompt: Claude's own and every agent's that
                # died with the turn close, so a dead foreground agent's
                # buttons can never type into a background agent's dialog.
                closed, inline_closed = sess.close_prompts(
                    lambda p: not sess.agent_survives_interrupt(
                        _prompt_owner(p)))
                log.info("[%s] prompt closed (answered in the terminal), %d "
                         "still open", sess.label, len(sess.open_prompts()))
                self.registry.mark_dirty()
                if closed:
                    await self._after_prompts_changed(
                        sess, {"inline_closed": inline_closed})
                return
            # Prompt messages sent with a clock line: answered in the
            # terminal, the line goes (roadmap 8.151).
            clocked = [rec for _where, _perm, rec in sess.open_prompt_records()
                       if isinstance(rec, dict) and rec.get("text_unclocked")]
            how = _terminal_end_how(sess, perm) if refused else ""
            if await self._end_turn_ended_at_dialog(
                    sess, refused=refused, by="", confirmed=True,
                    carried_on=carried_on,
                    card_text=_terminal_card_text(sess.label, refused=refused,
                                                  how=how, tr=i18n.for_sess(sess))):
                await self._drop_clock_lines(sess, clocked)
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
            tr = i18n.for_sess(sess)
            reminder = tr.html(WAITING, session=label)
            if summary:
                reminder += "\n" + tr.html(
                    WAITING_QUESTION if kind == "question" else WAITING_PERMISSION,
                    summary=str(summary)[:120])
            reply_to = (sess.busy_msg_id
                        if sess.busy_msg_id and sess.busy_msg_id > 0 else None)
            try:
                await bot.send_message(
                    resolve_chat_id(sess), reminder, parse_mode="HTML",
                    reply_to_message_id=reply_to,
                )
            except Exception:
                log.debug("[%s] waiting reminder failed", label, exc_info=True)
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
            sess.close_turn()  # no card for this turn from here (8.57)
            self._stop_animation(sess)
            tr = i18n.for_sess(sess)
            warn_text = tr.html(NOT_TAKEN, session=label, grace=tr.num(f"{grace:.0f}"))
            # Written and retired under the card-edit lock
            # (`_settle_card_text`): a merely late hook's card edit must
            # not land after the warning and put "Working" back.
            if not await self._settle_card_text(
                    sess, warn_text):
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
            # A message held behind the rejected one (the mixed-sender
            # hold in _hold_for_open_dialog parks it in pending_queue)
            # would otherwise sit there until some later turn's idle
            # moment drained it — the session is idle NOW, so give it
            # its turn the way the idle path does (review rev-iter1-002).
            # That send is a from-idle send and gets its own deadline.
            await self._drain_next_queued(sess)
            return

        if event == "orphan_card":
            # The session monitor found a live card no turn owns any more
            # (roadmap 8.55/8.57) — see TrackedSession.card_orphaned.
            await self._settle_orphan_card(sess)
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
            if sess.status is Status.BUSY:
                # A dialog answered in the terminal leaves INTERACTIVE at its
                # tool's end (hook_receiver), with the animation still
                # stopped from the prompt: bring it back.
                self._resume_animation_if_dead(sess, reason="tool_done")
            tool_summary = context.get("tool_summary", "")
            agent_id = context.get("agent_id", "")
            mark = "failed" if event == "tool_failed" else True
            # An attributed tool (agent_id matches a LIVE active_subagents
            # entry) never created a parent tool_history row to settle —
            # the tool_use handler routed it to record_agent_tool instead
            # ("agent activity rows on the busy card"). Skip the
            # parent-row search entirely so it can never mark an unrelated
            # in-flight row done via the "no exact match" fallback below.
            if agent_id and agent_id not in sess.active_subagents:
                # A detached background agent's tool (roadmap 8.58): not
                # this turn's row, and nothing on this card changed.
                return
            attributed = bool(agent_id and agent_id in sess.active_subagents)
            shell = sess.bg_shells.get(context.get("bg_shell_id") or "")
            # A live shell's row stays pending until the shell ends, so a
            # later call with the same summary (a second shell, or a plain
            # Bash) would find it first: rows a running shell holds are
            # never another call's to take.
            claimed = {info["history_idx"] for info in sess.bg_shells.values()
                       if info.get("history_idx") is not None}
            settled = context.get("bg_shell_row")
            if settled is not None:
                # A background shell's launch handled after its end: its
                # own row settles at once to the outcome, as the end would
                # have settled it.
                for i, (s, done) in enumerate(sess.tool_history):
                    if s == tool_summary and not done and i not in claimed:
                        sess.tool_history[i] = tuple(settled)
                        break
                else:
                    sess.record_tool(*settled)
            elif shell is not None:
                # A background shell's launch: the call returned, the shell
                # runs on. Its row stays live until the shell's end is
                # seen. Only its own row: never the "last undone" fallback
                # below, which would take another tool's pending row.
                for i, (s, done) in enumerate(sess.tool_history):
                    if s == tool_summary and not done and i not in claimed:
                        shell["history_idx"] = i
                        break
                else:
                    shell["history_idx"] = sess.record_tool(tool_summary,
                                                            False)
            elif tool_summary and not attributed:
                for i, (s, done) in enumerate(sess.tool_history):
                    if s == tool_summary and not done and i not in claimed:
                        sess.tool_history[i] = (s, mark)
                        break
                else:
                    # No exact match — mark the last undone tool
                    for i in range(len(sess.tool_history) - 1, -1, -1):
                        if not sess.tool_history[i][1] and i not in claimed:
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
            if not context.get("resumed"):
                # Count this subagent for the "(N agents)" rollup (item
                # 4.5). A waiting agent resuming already has its count
                # and its running row.
                sess.subagent_count_this_turn += 1
                # A helper names the agent at the top of its chain
                # ("pipeline-runner › ship-tester"), fixed now: the card's
                # rows stay in the order things started.
                card_label = (sess.agent_card_label(agent_id, agent_type)
                              if agent_id else agent_type)
                # Append to tool_history SYNCHRONOUSLY before any await.
                # record_tool returns the (post-trim) index so the
                # subagent bookkeeping below references the correct entry
                # even after the history is trimmed.
                summary = f"\U0001f916 {card_label}"
                history_idx = sess.record_tool(summary, False)
                # Store index in active_subagents so SubagentStop can find it
                if agent_id and agent_id in sess.active_subagents:
                    sess.active_subagents[agent_id]["history_idx"] = history_idx
                    sess.active_subagents[agent_id]["card_label"] = card_label
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
            # The clock in true seconds, "0:00" rather than "" below 1s
            # (was blank before this feature) so the settled row's
            # three-segment shape ("type · N tool calls · elapsed") is
            # always fixed ("agent activity rows on the busy card").
            done_summary = settled_agent_row(
                context.get("card_label") or agent_type, tool_count, elapsed)
            mark: object = True
            if context.get("stopped"):
                # A kill chord stopped it (roadmap 8.121 F5): its row reads
                # stopped, never done.
                done_summary = card_rows.stopped_agent_row(
                    context.get("card_label") or agent_type, elapsed)
                mark = _bg_shells.STOPPED
            # Mark the matching tool_history entry as done SYNCHRONOUSLY
            if history_idx is not None and 0 <= history_idx < len(sess.tool_history):
                sess.tool_history[history_idx] = (done_summary, mark)
            elif agent_type:
                # No matching start — daemon restart, or the start was
                # evicted by the active_subagents cap — append as done
                # entry. A phantom SubagentStop (unknown id AND empty
                # agent_type — design.md "model Claude Code background-agent
                # jobs" requirement 5) carries no real information to show,
                # so it must not pollute the timeline with a meaningless
                # "🤖 " row.
                sess.record_tool(done_summary, mark)
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
            compacting = i18n.for_sess(sess).html(COMPACTING, session=label, dots="")
            if existing_msg_id and existing_msg_id > 0:
                text = compacting
                await self._edit_busy_raw(existing_msg_id, text, chat_id=resolve_chat_id(sess))
                pushed_msg_id = existing_msg_id
            else:
                # No busy message — send a new one
                try:
                    text = compacting
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
            return

        if event == "context_warning":
            ctx_pct = context.get("context_pct", 0)
            tr = i18n.for_sess(sess)
            warn_text = tr.html(CONTEXT_WARNING, session=label, percent=tr.num(f"{ctx_pct}%"))
            try:
                keyboard = self._build_compact_keyboard(sess)
                await bot.send_message(resolve_chat_id(sess), warn_text, parse_mode="HTML",
                                       reply_markup=keyboard)
            except Exception:
                pass
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
            stale_text = i18n.for_sess(sess).n(QUIET_TURN, int(minutes), session=label)
            try:
                keyboard = self._build_stop_keyboard(sess)
                await bot.send_message(resolve_chat_id(sess), stale_text, parse_mode="HTML",
                                       reply_markup=keyboard)
            except Exception:
                pass
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
            # Nothing live underneath and no turn left to own the message:
            # Claude Code compacted on its own, or ran a /compact, while the
            # session sat idle. The "Compacted" text is then this message's
            # last word. Tracked as the busy card, it would be an orphan the
            # sweeper settles by painting the last finished turn's card into
            # it: that turn's done card a second time.
            standalone = not target_msg_id and not (
                sess.status in (Status.BUSY, Status.INTERACTIVE)
                or sess.job_background_open()
                or sess.finish_gate is not None
            )
            if not target_msg_id and popped is not None:
                target_msg_id = popped.msg_id
            text = _compacted_text(i18n.for_sess(sess), label, before_pct, after_pct)
            if standalone:
                # Not tracked, so a turn that starts meanwhile opens a card
                # of its own, and nothing here may touch that card. Under
                # the card-edit lock, as at compact_timeout: a card edit
                # already out for this message (a phantom SubagentStop's
                # repaint) lands first, and one taking the lock later finds
                # the stack popped and sends nothing.
                chat_id = resolve_chat_id(sess)
                conversation = sess.claude_session_id
                shown_id = None
                try:
                    async with _card_edit_lock(sess):
                        if target_msg_id and target_msg_id > 0:
                            # Not gone (None): a failed edit still leaves a
                            # message the size can complete later.
                            if await self._edit_busy_raw(target_msg_id, text,
                                                         chat_id=chat_id) is not None:
                                shown_id = target_msg_id
                        else:
                            sent = await bot.send_message(
                                chat_id, text, parse_mode="HTML",
                                reply_to_message_id=sess.trigger_msg_id,
                            )
                            shown_id = sent.message_id
                except Exception:
                    log.warning("Failed to show compact_done", exc_info=True)
                sess.last_token_pct = after_pct
                # The size after it is not measured yet: the first real
                # status line reading completes this message ("compact_size").
                # Not when, during the edit, another compaction began, the
                # conversation changed (/clear) or the session ended.
                if (shown_id and before_pct > 0 and after_pct <= 0
                        and not sess.pre_compact_pct and sess.compact_started_at is None
                        and sess.claude_session_id == conversation
                        and sess.status != Status.GONE):
                    sess.compacted_size_pending = (shown_id, chat_id, before_pct, conversation)
                return
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
            # Brief pause so user can read the delta, then resume busy animation
            await asyncio.sleep(COMPACT_DONE_PAUSE_SECONDS)
            sess.last_token_pct = after_pct
            self._start_animation(sess)
            return

        if event == "compact_size":
            # The first real status line reading after a compaction between
            # turns: its "Compacted from N%" message gets the size after it.
            # The hook receiver took the record off the session. Not under
            # the card-edit lock: that message is no card, and nothing else
            # edits it, while a turn's live card would wait behind this edit.
            pending = context.get("pending")
            after_pct = context.get("after_pct", 0)
            if not pending or after_pct <= 0:
                return
            msg_id, chat_id, before_pct, conversation = pending
            reading_conversation = context.get("session_id") or ""
            if conversation and reading_conversation and reading_conversation != conversation:
                return  # another conversation's size
            if sess.pre_compact_pct or sess.compact_started_at is not None:
                return  # a newer compaction is on
            if msg_id == sess.busy_msg_id:
                return  # defensive: never a live card (nothing tracks it today)
            text = _compacted_text(i18n.for_sess(sess), label, before_pct, after_pct)
            try:
                await self._edit_busy_raw(msg_id, text, chat_id=chat_id)
            except Exception:
                log.warning("Failed to add the size after a compaction", exc_info=True)
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
            # The compaction dots (`_animate_compact`, still ticking at the
            # deadline) stop first, as at compact_done: a dot frame queued
            # behind the text below would land after it and leave the card
            # on "Compacting." for good. A BUSY card is resumed below.
            self._stop_animation(sess)
            text = i18n.for_sess(sess).n(COMPACT_TIMEOUT, minutes, session=label)
            # Under the card-edit lock: when nothing is resumed, the text
            # below is this message's last word, and a busy-card edit that
            # took the lock while it was out would land after it (both are
            # ORNAMENTs, served in order) and put "Working" back. Taken
            # here, that edit finds `busy_msg_id` retired and sends nothing.
            async with _card_edit_lock(sess):
                # Read under the lock: the edit it waited behind may have
                # found the card gone.
                target_msg_id = sess.busy_msg_id
                if target_msg_id and target_msg_id > 0:
                    result = await self._edit_busy_raw(target_msg_id, text, chat_id=resolve_chat_id(sess))
                    if result is None:
                        sess.busy_msg_id = None
                sess.pop_compacting()
                resume = bool(sess.status == Status.BUSY and sess.busy_msg_id)
                if not resume:
                    # Nothing legitimate left to resume (the observed bug's
                    # status=idle case, or any other non-BUSY status) — the
                    # edited text above is the final state of this message.
                    sess.busy_msg_id = None
            if resume:
                # A real turn's busy card was live underneath — resume it
                # exactly as compact_done already does today.
                self._start_animation(sess)
            return

        if event == "session_end":
            # Session exited — clean up busy state and alert user.
            # A deferred card whose send is in flight lands first, under
            # the card lock, and is then deleted below like any card.
            async with sess.animate_lock:
                self._cancel_lazy_card(sess)
                sess.close_turn()  # no card for this turn from here (8.57)
            # Claude Code's input queue dies with the process, restart
            # included (the relaunch starts empty): nothing still queued
            # there will be taken (R3). `/clear` and `/resume` also fire
            # SessionEnd, but the process lives on — leave its queue alone.
            if context.get("source") not in ("clear", "resume"):
                lost = await self._settle_queued_targets(sess, reanchor=False)
                self._discard_queued_targets(sess)
                # `notes`: what was still outstanding just before the GONE
                # transition deleted the notes dir (captured by the caller)
                # — a command or message Claude never got to run.
                await self._mark_not_delivered(
                    sess, list(context.get("notes") or []) + lost)
                if sess.dialog_hold is not None and not sess.is_restarting():
                    # Claude Code ended with a dialog open that messages
                    # were held for: nothing will take them now, and no
                    # turn end will ever drain them. Dropped, with a 🤷.
                    held = reactions.held_entries(sess.pending_queue)
                    sess.pending_queue.clear()
                    self.registry.mark_dirty()
                    await self._mark_not_delivered(sess, held)
                    log.info("[%s] session ended with a dialog open: %d "
                             "held message(s) dropped", label, len(held))
                if sess.parked_job and not sess.is_restarting():
                    # Its conversation was in a Claude Code background job
                    # and the parked client ended: nothing will bring the
                    # held messages to it now. Dropped, with a 🤷.
                    held = reactions.held_entries(sess.pending_queue)
                    sess.pending_queue.clear()
                    log.info("[%s] session ended while parked on background "
                             "job %s: %d held message(s) dropped", label,
                             sess.parked_job, len(held))
                    sess.forget_park()
                    self.registry.mark_dirty()
                    await self._mark_not_delivered(sess, held)
            self._stop_animation(sess)
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                try:
                    await bot.delete_message(chat_id=resolve_chat_id(sess), message_id=sess.busy_msg_id)
                except Exception:
                    pass
                sess.busy_msg_id = None
            source = context.get("source", "unknown")
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
            # By the hook's source id, or what the exit record says, one
            # whole line each.
            reason, params = session_end_reason(context)
            tr = i18n.for_sess(sess)
            text = tr.html(SESSION_END_MSGS[reason], session=label, **params)
            markup = None
            if reason == "start_failed_bg":
                # Its conversation is in a Claude Code background job: the
                # button resumes it here, stopping the job first (R2).
                from aipager.bot import session_parity
                markup = session_parity.bg_park_keyboard(
                    self, resolve_chat_id_int(sess) or 0, sess, tr)
            try:
                if markup is None:
                    await bot.send_message(resolve_chat_id(sess), text, parse_mode="HTML")
                else:
                    await bot.send_message(resolve_chat_id(sess), text, parse_mode="HTML",
                                           reply_markup=markup)
            except Exception:
                log.warning("Failed to send session_end notification", exc_info=True)
            return

        if sess.status == Status.IDLE:
            # The turn that ended, read before any await: a newer turn can
            # begin while this finish is still out (a flood-delayed answer),
            # and nothing below may take that turn's card, reply target or
            # stream state for this one's (roadmap 8.57).
            finishing_turn = sess.turn_seq
            # This turn's reply target, read before any await (roadmap
            # 8.57): the queued pick-up below, or a Telegram prompt that
            # starts the NEXT turn while this finish is out, moves
            # `sess.trigger_msg_id`; this turn's answer and card still
            # belong under this turn's message.
            finish_trigger = sess.trigger_msg_id
            # A previous turn's finish may still be out (a flood-delayed
            # answer) when this turn has already ended too. The two must
            # not dispose of the same card: this one waits for that one
            # below, after the synchronous captures here.
            prev_gate = sess.finish_gate
            gate = asyncio.Event()
            sess.finish_gate = gate
            sess.finishing_turn = finishing_turn
            sess.finishing_answer = str(
                context.get("raw_md") or context.get("summary") or "")
            taken = None
            popped_turn = finishing_turn
            # Everything from here on is inside the `try`, so its `finally`
            # releases the gate even when a transcript read below raises
            # (review rev-iter1-004): a gate left set would hold every later
            # card for FINISH_GATE_TIMEOUT and keep the orphan sweep off
            # this session.
            try:
                # An absorption flushed with the run's last round is first
                # visible here; read it before deciding what is still queued.
                _sync_anchors_from_transcript(sess)
                # Those absorptions are THIS turn's, taken now: left staged,
                # the old card's still-running animation would apply them to
                # whatever turn is current by the time it ticks.
                finish_consumed = sess.stream_consumed_notes
                sess.stream_consumed_notes = []
                # An API error ends the run with nothing processed: the queue
                # is left for the next finish, as before.
                taken = (None if _turn_api_error(context, sess=sess)
                         else self._take_next_prompt(sess))
                popped_turn = sess.turn_seq
                # What the sync above absorbed (or popAll dropped) and what
                # the pop just took have left Claude's queue: their "⏳
                # Queued" lines go now, before any wait below, so before the
                # popped turn's card and its 👍.
                self._sync_queued_lines(sess)
                if isinstance(prev_gate, asyncio.Event):
                    await self._wait_finish_gate(sess, prev_gate)
                # The delivery stamp's moment (roadmap 8.39 R2), taken on
                # arrival — before the awaited final card render, which the
                # outbound gate can pace by seconds. A next turn's answer
                # written during that render must not look older than it.
                turn_end_wall = time.time()
                # Every turn end, before the job-interim and API-error returns
                # below: Claude Code has just run whatever it had queued.
                await self._mark_ran_commands(sess)
                if taken is not None:
                    # A message popped at this Stop's arrival settled then
                    # whether this Stop is the job's interim one (review
                    # rev-iter1-003): taken inside the open job, the popped
                    # turn IS the job's turn, which this finish must not
                    # close; taken outside it, the popped turn is a new one
                    # and this finish is the old turn's plain close. What
                    # the job does during the waits above changes neither.
                    job_interim = taken[1]
                elif (sess.job_continuation_active
                      and not sess.job_work_running()):
                    # The <task-notification> continuation turn's own Stop —
                    # the job's one true Finished ("close the background-job
                    # endgame" requirement 2). Clear the endgame state FIRST so
                    # the Finished path below runs exactly as a normal close.
                    sess.job_continuation_active = False
                    sess.job_grace_until = 0.0
                    sess.job_interim_seen = False
                    job_interim = False
                else:
                    job_interim = sess.job_background_open()
                if not job_interim and sess.status is Status.IDLE:
                    # The turn, or the job, is over: the IDLE transition
                    # kept its sender while the job still looked open
                    # (roadmap 8.77). A popped turn (BUSY) or one that
                    # started during the awaits above keeps its own.
                    sess.turn_sender_id = None
                if job_interim:
                    # A background agent this job launched is still running (or
                    # the continuation grace window is open) — this
                    # idle-transition is an INTERIM Stop, not the job's true
                    # end (design.md "model Claude Code background-agent jobs",
                    # requirement 1). Never falls through to the Finished-card
                    # disposal logic below: that logic's unconditional
                    # active_subagents.clear() (removed just below) was itself
                    # the bug this feature fixes — it erased the very state
                    # job_background_open() needs to keep working.
                    if sess.job_work_running():
                        # A continuation turn that spawned NEW background
                        # agents or shells has ended — back to plain
                        # waiting; the next continuation cycle re-arms via
                        # their end + grace.
                        sess.job_continuation_active = False
                    if taken is not None:
                        # The message popped at this Stop now owns the reply
                        # target and the job's card (roadmap 8.56). What this
                        # run absorbed is taken (👍, tracked) but must not
                        # pull the target back to itself: staged for the
                        # animator, it would, and the popped turn's card and
                        # answer would land under the absorbed message
                        # (review rev-iter1-002).
                        await self._apply_consumption(
                            sess, finish_consumed, move_target=False)
                        run_consumed = finish_consumed
                    else:
                        # An interim Stop leaves absorptions to the animator,
                        # which moves the still-live card to them.
                        sess.stream_consumed_notes = (
                            finish_consumed + sess.stream_consumed_notes)
                        run_consumed = sess.stream_consumed_notes
                    # This run's answer goes under the last message it took,
                    # exactly as the final path's does (roadmap 8.63):
                    # otherwise a message absorbed inside a job shows 👍 and
                    # never gets an answer under it.
                    interim_reply_to = finish_trigger
                    if run_consumed:
                        last_consumed = run_consumed[-1].get("msg_id")
                        if last_consumed is not None:
                            interim_reply_to = last_consumed
                    await self._handle_job_interim(sess, context, turn_end_wall,
                                                   gate=gate,
                                                   reply_to=interim_reply_to)
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
                # An absorption first visible here (flushed while this finish
                # waited) takes its "⏳ Queued" line with it.
                self._sync_queued_lines(sess)
                consumed = finish_consumed + sess.stream_consumed_notes
                sess.stream_consumed_notes = []
                if consumed:
                    # The target moves only while this is still the current turn;
                    # either way this turn's answer follows its last consumed
                    # message.
                    await self._apply_consumption(
                        sess, consumed, move_target=sess.turn_seq == finishing_turn)
                    last_consumed = consumed[-1].get("msg_id")
                    if last_consumed is not None:
                        finish_trigger = last_consumed
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

                # Mark all tools as done — except a background shell's
                # settled failed/stopped row, which keeps its own outcome,
                # and a prompt Claude Code refused by itself (8.128), which
                # stays ❌: nobody answered and the command never ran.
                sess.tool_history = [
                    (s, d if ((d in ("failed", _bg_shells.STOPPED)
                               and s.startswith(_bg_shells.SHELL_ROW_PREFIX))
                              or (d == _bg_shells.STOPPED
                                  and card_rows.stopped_agent(s))
                              or (d == "failed" and card_rows.refused_by_claude(s)))
                     else True)
                    for s, d in sess.tool_history]
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
                # it is replacing. Settling the old id instead would strand the
                # live card on "Working" for good (tests: the in-flight re-anchor
                # and in-flight send tests in test_quiet_toolless_turns.py).
                async with sess.animate_lock:
                    self._cancel_lazy_card(sess)
                    # From here no card is opened for this turn (roadmap 8.57 R2):
                    # a request for it that is still waiting on the lock finds it
                    # finished and sends nothing.
                    sess.close_turn(finishing_turn)
                # Stop animation and clean up busy message
                self._stop_animation(sess)
                if sess.prompt_wait_carry and sess.pending_permission:
                    # An agent's prompt outlives Claude's turn (roadmap
                    # 8.120): kept, and sent again as its own message once
                    # this answer is out (``prompts_carried``).
                    sess.carried_inline_prompt = sess.pending_permission
                sess.pending_permission = None  # clear stale inline permission if any
                # The turn is over: no prompt on its card is open any more,
                # so an old answer button on it types nothing (8.102).
                sess.mark_prompt_closed(sess.busy_msg_id)
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
                    and sess.busy_card_trigger != finish_trigger
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
                # ── a card with nothing on it goes before it says Done ─────────
                # In the `card` layout a turn that ran no tool keeps no card:
                # it is deleted here, still showing "Working", and never
                # edited into "✅ label · Done · 0:03" first; the answer then
                # opens with the stats, as in `replace` (operator,
                # 2026-10-18: "these Done empty messages are still bad", and
                # "delete it before it get its final looks"). This reverses
                # 2026-09-27's "every turn follows the layout", which kept
                # such a card because the chat looked random next to tool
                # turns; an empty Done card above the answer only repeats
                # its first line. Turns that ran a tool, and the `merged`
                # and `replace` layouts, are unchanged.
                #
                # Empty is `_card_is_empty`: nothing on the card's own
                # timeline (read after the trim above, so streamed text the
                # answer carries does not count), no agent, no background
                # shell and no prompt this turn. Only with a fresh answer to
                # carry the stats: a turn with nothing to deliver keeps its
                # card as the one record that it ended (8.32 R1's rule).
                # The id read here is the settled one: the `animate_lock`
                # above waited out any re-anchor in flight.
                drop_empty_card = False
                if (layout == "card" and sess.busy_msg_id and sess.busy_msg_id > 0
                        and _card_is_empty(sess)):
                    _candidate = (context.get("raw_md") or context.get("summary")
                                  or "")
                    # An API error is not an answer: its notice goes out
                    # without the stats, so the card stays the record of
                    # the turn, as before.
                    if _candidate and not _turn_api_error(context, sess=sess):
                        _cand_digest = hashlib.md5(
                            _candidate.encode("utf-8")).hexdigest()
                        drop_empty_card = not (
                            _cand_digest == sess.last_idle_summary_hash
                            or sess.was_delivered(_cand_digest))
                if drop_empty_card:
                    # No re-anchor either: a final card under the new target
                    # is exactly what must not appear. The answer replies to
                    # the turn's current target on its own.
                    reanchor_needed = False
                card_already_final = False
                merged_send_as_new = False
                if reanchor_needed and layout == "card":
                    await self._reanchor_busy_card(sess, finish_trigger, final=True)
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
                            if drop_empty_card and await self._drop_empty_card(sess):
                                # Gone (or found gone): no finished card, so
                                # no head start for the answer, and the
                                # answer's first line carries the stats.
                                log.info("[%s] the turn ran no tool — its card "
                                         "is removed, the answer carries the "
                                         "stats", label)
                                # Nothing hidden: no card is left whose rows
                                # a full log would make up for (a live
                                # frame's cut must not attach one).
                                sess.last_card_truncated = False
                            elif card_already_final:
                                # _reanchor_busy_card already rendered the
                                # exact same final content under the new
                                # message a moment ago — a second POST here
                                # would be redundant, harmless-but-wasteful.
                                card_kept = True
                            else:
                                # Also a toolless card whose delete failed:
                                # it must still end finished, never stay on
                                # "Working".
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
                error_tr = i18n.for_sess(sess)
                error_detection = _turn_api_error(context, sess=sess, tr=error_tr)
                if error_detection:
                    # Never silent (roadmap 8.145): the answer is not shown.
                    log.warning("[%s] The turn ended in an API error (%s): an error "
                                "notice goes in its place: %r", label,
                                context.get("api_error"), error_source[:120])
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
                    # In the session chat's language; the line starts with a
                    # name, so a right-to-left one is marked at both ends.
                    text = error_tr.line(f"⚠️ <b>{html_mod.escape(label)}</b> · "
                                         f"{html_mod.escape(friendly_error, quote=False)}",
                                         kind="html")
                    keyboard = (self._build_retry_keyboard(sess)
                                if sess.last_prompt else None)
                    try:
                        msg = await bot.send_message(
                            resolve_chat_id(sess), text, parse_mode="HTML",
                            reply_to_message_id=finish_trigger,
                            reply_markup=keyboard,
                        )
                        self.registry.track_message(msg.message_id, sess.name, resolve_chat_id_int(sess) or 0)
                        await self._maybe_update_bot_name(sess.name)
                    except Exception:
                        log.warning("Failed to send error notification", exc_info=True)
                    # Files Claude sent before the error: under the prompt
                    # (in the background).
                    self._deliver_turn_files(
                        sess, finish_trigger, finish_trigger,
                        self._held_key_now(sess))
                    # Don't clear trigger_msg_id — retry needs it
                    # Don't flush pending queue — nothing was processed
                    return

                # Compute elapsed time since BUSY started
                elapsed_s = 0
                if sess.busy_started_at:
                    elapsed_s = int(time.monotonic() - sess.busy_started_at)
                elif sess.busy_started_wall:
                    # busy_started_at is stamped by the card sender; a turn
                    # that never got a card still has the transition's own
                    # stamp, so the header can report how long it ran.
                    elapsed_s = int(time.time() - sess.busy_started_wall)
                # The result line in its STATS form — a result that is the only
                # message left for its turn says Finished and how long it took
                # and the lines it changed ("session-name-on-every-message").
                # HTML for the standalone header message, the rich-message
                # dialect (bold = **) for a composed send, bare for the
                # plain-text fallback.
                # The answer message's one direction (rule 5): the answer's,
                # else the turn's commentary, else the prompt, else the
                # chat's language. Decided before the dedup above could
                # empty `content`, so a header-only message still follows
                # the turn.
                turn_rtl = self._answer_rtl(
                    sess, raw_md or summary,
                    " ".join(t for _a, t in log_commentary))
                header_tr = i18n.for_sess(sess).directed(turn_rtl)
                # The HTML header and the plain copies are not rich
                # messages (no is_rtl): an English chat's lines there need
                # no marks.
                plain_tr = i18n.for_sess(sess).directed(turn_rtl, rich=False)
                stats = {"seconds": elapsed_s, "added": sess.last_lines_added,
                         "removed": sess.last_lines_removed}
                header_text = finished_line(
                    plain_tr, f"{_RESULT_GLYPH} <b>{html_mod.escape(label)}</b>", "html",
                    **stats)
                header_md = finished_line(header_tr, _result_line_md(label, header_tr), "md",
                                          **stats)
                header_plain = finished_line(plain_tr, _result_line_plain(label), "text",
                                             **stats)

                # ── merged layout: one combined edit, timeline + answer ────────
                # Attempted here — after the header text exists (the ordinary
                # answer send below uses it too) but before the per-answer-alone
                # overflow check, since merged has its own combined-text ceiling
                # check inside _send_merged_final. Only attempted when the busy
                # card is still live; if it isn't (e.g. it was already lost) there
                # is nothing to merge into, so the turn falls through to the
                # ordinary send below — the exact same path "replace" uses.
                # Roadmap 8.41: agents this turn launched in the background are
                # still running — the answer ends by saying so, in every layout.
                agents = self._agents_snapshot(sess, turn_rtl)
                merged_delivered = False
                # Files Claude sent this turn follow its answer: a held key
                # that is new after the answer's send says it was held.
                files_held_before = self._held_key_now(sess)
                files_anchor = 0
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
                    # Under the card-edit lock, like every busy-card edit
                    # (`_edit_busy_rich`): the final is ESSENTIAL and the
                    # outbound gate lets it overtake an ORNAMENT, so a card
                    # edit already waiting in that lock for a chat token
                    # (a hook's, the monitor's refresh: the animate task is
                    # cancelled above, these are not) would otherwise land
                    # AFTER the answer and replace it with a stale
                    # "Working" frame (seen live 2026-10-02). Holding it
                    # makes that edit land first; one that gets the lock
                    # afterwards finds `busy_msg_id` retired below, still
                    # under the lock, and sends nothing. Nothing in here
                    # takes this lock again or waits on the animator.
                    async with _card_edit_lock(sess):
                        if not (sess.busy_msg_id and sess.busy_msg_id > 0):
                            # The edit this waited behind found the card
                            # gone (`busy_msg_id` 0): nothing to merge
                            # into, so straight to the replace-style send.
                            merged_delivered = False
                        elif merged_send_as_new:
                            merged_delivered = await self._send_merged_final(
                                sess, content, send_as_new=True,
                                reply_to=finish_trigger, agents=agents,
                                rtl=turn_rtl,
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
                                sess, content, agents=agents, rtl=turn_rtl)
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
                        elif sess.busy_msg_id and sess.busy_msg_id > 0:
                            # The merged message IS the answer.
                            files_anchor = sess.busy_msg_id
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
                lead_md = _result_line_md(label, header_tr) if card_kept else header_md
                lead_plain = _result_line_plain(label) if card_kept else header_plain
                body_limit = _RICH_LIMIT - len(f"{lead_md}\n\n".encode("utf-8"))
                if agents:
                    body_limit -= agents["reserve"]
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
                #                           the elapsed time.
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
                        header_text += "\n\n" + plain_tr.html(FULL_ATTACHED)
                    log.debug("[%s] Sending IDLE notification (%d chars header)",
                              label, len(header_text))
                    try:
                        msg = await bot.send_message(
                            resolve_chat_id(sess), header_text, parse_mode="HTML",
                            reply_to_message_id=finish_trigger,
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
                        rich_text = _with_agents_block(rich_text, agents["line"])
                    is_rtl = turn_rtl
                    log.info("[%s] sendRichMessage: %d chars, rtl=%s, overflow=%s, "
                             "lead=%s",
                             label, len(rich_text), is_rtl, send_file,
                             "standalone" if standalone_header
                             else ("short" if card_kept else "stats"))
                    # Without a standalone header the body IS the turn's message:
                    # it carries the reply link and becomes the tracked message.
                    body_is_the_message = not standalone_header
                    reply_to = finish_trigger if body_is_the_message else None
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
                                          selected_wall=turn_end_wall,
                                          is_rtl=turn_rtl)
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

                if (sess.turn_seq == finishing_turn
                        and sess.trigger_msg_id == finish_trigger):
                    # Reply cycle complete — unless a newer turn has begun, or a
                    # popped message has taken the target (roadmap 8.57).
                    sess.trigger_msg_id = None
                    sess.busy_card_trigger = None
                self.registry.mark_dirty()
                if msg_id:
                    self.registry.track_message(msg_id, sess.name, resolve_chat_id_int(sess) or 0)
                await self._maybe_update_bot_name(sess.name)

                # ── Full-log .md attachment ("layered-card-shedding") ────────
                # Sent when the FINAL card render had to hide anything (the
                # renderer reported it via sess.last_card_truncated) OR the
                # answer body was truncated by the overflow logic above. One
                # file per close, superseding the old answer-only
                # response file: complete chronological play-by-play plus the
                # full answer, so hidden history is always recoverable.
                # For layout=card (except a dropped empty card, which resets
                # it: nothing is left hidden) and layout=merged this flag
                # comes from the FINAL render (stashed by _edit_busy_rich / _send_merged_final
                # respectively). For layout=replace no final card is ever
                # rendered — the busy card is deleted outright — so the flag
                # reflects the last interim tick: a deliberate proxy (review
                # rev-iter1-006), since replace leaves no finished card whose
                # hidden rows an attachment would need to compensate for
                # beyond what the interim state already showed.
                attach_log = send_file or sess.last_card_truncated
                file_content = (
                    build_full_log(label, log_tools, log_commentary, content,
                                    agents=log_agents, tr=i18n.for_sess(sess))
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
                    elif MUTE.is_muted(resolve_chat_id(sess)):
                        # R3: a document is a send.
                        log.info("[%s] full-log attachment skipped — chat "
                                 "flood-muted", label)
                    else:
                        try:
                            filename = f"{label}_full_log.md"
                            await bot.send_document(
                                resolve_chat_id(sess),
                                document=document_upload(content_bytes, filename),
                                filename=filename,
                                reply_to_message_id=msg_id or None,
                            )
                            # The one positive trace of the attachment (the
                            # live e2e suite's oracle): a held or refused
                            # send raises instead of returning.
                            log.info("[%s] full-log attachment sent: %s (%d bytes)",
                                     label, filename, len(content_bytes))
                        except Forbidden as e:
                            _log_blocked_once(e)
                        except Exception:
                            log.warning("Failed to send full response file", exc_info=True)

                # This turn's card and answer are settled: the next turn's
                # card may go up now. (A message Claude had queued and did not
                # absorb was taken as the next turn at this Stop's arrival,
                # `_take_next_prompt` (R8, "anchor-on-transcript-consumption"),
                # and its card follows once this returns.)
                self._release_finish_gate(sess, gate)

                # ── Files Claude sent this turn (SendUserFile) ──────────────
                # After the answer, as replies to it (the prompt when there is
                # none), or queued behind a held answer or a mute: in the
                # background, so nothing here waits for an upload.
                self._deliver_turn_files(
                    sess, files_anchor or msg_id or finish_trigger,
                    finish_trigger, files_held_before)

                # Flush next queued message (one at a time, rest flush on next IDLE)
                await self._drain_next_queued(sess)
            finally:
                self._release_finish_gate(sess, gate)
                if taken is not None:
                    # The popped message's 👍 and card, now that this
                    # turn's card and answer are settled (and whichever
                    # way this finish ended).
                    try:
                        await self._open_popped_turn(sess, *taken,
                                                     turn=popped_turn)
                    except Exception:
                        log.warning("[%s] the popped turn's card failed",
                                    sess.label, exc_info=True)
        elif sess.status == Status.INTERACTIVE:
            self._stop_animation(sess)
            # The turn asked something: its card is not an empty one, and
            # the finish keeps it (`_card_is_empty`).
            sess.turn_prompt_shown = True
            tool_info = context.get("tool_info")
            if tool_info and tool_info.get("name") == "AskUserQuestion":
                # Whatever found it (the hook, or the transcript fallback),
                # a question and its options are shown with any direction
                # control as a visible marker. Answers go by position.
                tool_info = {**tool_info,
                             "input": bidi_guard.reveal_questions(tool_info.get("input"))}

            # Rule check: a tool the running turn's rules deny is answered
            # Deny here, with no prompt shown. Claude sees a Deny through
            # the same key-injection path the buttons use, the chat gets a
            # one-line "⛔ x1 · Edit blocked for @bob (role user)" notice
            # naming whose rules blocked it, and an audit record is written.
            if tool_info:
                tool_name = tool_info.get("name", "")
                if self.scopes is not None:
                    # Scope mode: the running turn's rules (a terminal turn
                    # is never denied by a Telegram member's rules; owner
                    # and admin roles bypass). See
                    # AuthMixin._auto_deny_decision.
                    denied, whose = self._auto_deny_decision(sess, tool_name)
                    if denied:
                        await self._auto_deny(sess, tool_info, whose,
                                              context.get("hook_reply"))
                        return
                elif self.team is not None and self.team.rules.deny_tools:
                    # Legacy team.yaml mode: its ``rules.deny_tools``, for
                    # the session's last Telegram sender (admins bypass).
                    triggerer = self._driver_user(sess)
                    if self.team.rules.tool_is_denied(tool_name, triggerer):
                        await self._auto_deny(sess, tool_info, triggerer,
                                              context.get("hook_reply"))
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
                            "shown_wall": time.time(),
                        }
                    else:
                        # AskUserQuestion detected but no questions data (transcript
                        # not flushed). Degrade to Allow/Deny — Allow sends Enter.
                        sess.pending_permission = {
                            "tool_summary": "AskUserQuestion (loading…)",
                            "tool_info": tool_info,
                            "wait_started_at": time.monotonic(),
                            "shown_wall": time.time(),
                        }
                else:
                    tool_summary = tool_info["summary"] if tool_info else str(PERM_NEEDED)
                    sess.pending_permission = {
                        "tool_summary": tool_summary,
                        "tool_info": tool_info,
                        "wait_started_at": time.monotonic(),
                        # The wall clock a restart reads (roadmap 8.102).
                        "shown_wall": time.time(),
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
                # The card holds an open prompt again: its buttons answer
                # it, whatever an earlier prompt on it was (8.102).
                sess.reopen_prompt_surface(sess.busy_msg_id)
                # The state file keeps the open prompt (roadmap 8.102).
                self.registry.mark_dirty()

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
                await self._send_prompt_message(sess, tool_info, context, "primary")

        elif sess.status == Status.BUSY:
            # Session went back to working — edit the last idle/interactive message
            if sess.last_msg_id:
                try:
                    from aipager.bot.animation import LEGACY_HEAD, _verb
                    working_tr = i18n.for_sess(sess)
                    await bot.edit_message_text(
                        working_tr.html(LEGACY_HEAD, session=label,
                                        verb=Safe(_verb(working_tr, "Working", "html"))),
                        chat_id=resolve_chat_id(sess),
                        message_id=sess.last_msg_id,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass  # message may be too old or already edited


# The notices about a session (HTML, sent to the session's chat).
MEMCAP_HEAD = Msg("notice.memcap_head", "⚠️ <b>{session}</b> · memory cap hit",
                  note="A helper of aipager ran out of its memory limit", eg={"session": "x1"})
MEMCAP_HEAD_TOOL = Msg("notice.memcap_head_tool",
                       "⚠️ <b>{session}</b> · memory cap hit during <code>{tool}</code>",
                       note="As notice.memcap_head, while checking that tool",
                       eg={"session": "x1", "tool": "Bash"})
MEMCAP_STATUSLINE = Msg("notice.memcap_statusline",
                        "<code>{hook}</code> ran out of its {limit} GB memory limit, so one "
                        "status line update was skipped. The session keeps running.",
                        note="The status line helper hit its memory limit; {hook} is its name",
                        eg={"hook": "aipager-statusline", "limit": 1})
MEMCAP_HOOK = Msg("notice.memcap_hook",
                  "<code>{hook}</code> ran out of its {limit} GB memory limit and stopped. If "
                  "it was checking a tool call, Claude Code most likely refused that call and "
                  "Claude can try it again; otherwise only that one event was skipped. The "
                  "session keeps running.",
                  note="The hook hit its memory limit; {hook} is its name",
                  eg={"hook": "aipager-hook", "limit": 1})
MEMCAP_NUDGE_OWNER = Msg("notice.memcap_nudge_owner",
                         "<i>If this keeps happening, the owner can tap Report this to send "
                         "the maintainer a report.</i>",
                         note="Report this is the button under the notice (use its "
                              "translation)")
MEMCAP_NUDGE_CLI = Msg("notice.memcap_nudge_cli",
                       "<i>If this keeps happening, run {command} in a terminal to send the "
                       "maintainer a report.</i>",
                       note="{command} is the command that sends a problem report",
                       eg={"command": "aipager report"})
QUEUE_TIMEOUT = Msg("notice.queue_timeout",
                    "⏳ <b>{session}</b> · a queued message wasn't confirmed picked up in time - "
                    "Claude may still process it.",
                    note="A message Claude Code held was not seen taken", eg={"session": "x1"})
QUEUE_TIMEOUT_PREVIEW = Msg("notice.queue_timeout_preview",
                            "⏳ <b>{session}</b> · a queued message wasn't confirmed picked up "
                            "in time: \"{preview}\" - Claude may still process it.",
                            note="As notice.queue_timeout, with the start of the message",
                            eg={"session": "x1", "preview": "fix the tests"})
WAITING = Msg("notice.waiting", "⬆️ <b>{session}</b> · still waiting for your answer above",
              note="A reminder: a question or permission prompt above waits",
              eg={"session": "x1"})
WAITING_QUESTION = Msg("notice.waiting_question", "Question: {summary}",
                       note="Under notice.waiting: the question", eg={"summary": "Which one?"})
WAITING_PERMISSION = Msg("notice.waiting_permission", "Permission: {summary}",
                         note="Under notice.waiting: what wants permission",
                         eg={"summary": "Bash: ls"})
NOT_TAKEN = Msg("notice.not_taken",
                "⚠️ <b>{session}</b> · Not taken by Claude Code\n\nNo hook arrived within "
                "{grace} s of the send. A message that starts with \"/\" is read as a slash "
                "command - check the terminal.",
                note="A message sent to Claude Code that it never took; {grace} is seconds",
                eg={"session": "x1", "grace": "45"})
CONTEXT_WARNING = Msg("notice.context",
                      "⚠️ <b>{session}</b> · Context at {percent} - auto-compact soon",
                      note="The session's context is nearly full", eg={"session": "x1",
                                                                         "percent": "92%"})
QUIET_TURN = Plural(
    "notice.quiet_turn",
    one="⏳ <b>{session}</b> · still working (quiet for {n} min)\n\nNo status updates yet. "
        "This is usually normal.\n\n<blockquote expandable>What could be happening\n"
        "  • Long-running tool call (Bash, WebSearch, large fetch)\n"
        "  • Heavy generation on a very large context\n"
        "  • Compaction in progress\n"
        "  • Rate-limit backoff or subscription limit\n"
        "  • Network wedge or claude crash</blockquote>",
    other="⏳ <b>{session}</b> · still working (quiet for {n} min)\n\nNo status updates yet. "
          "This is usually normal.\n\n<blockquote expandable>What could be happening\n"
          "  • Long-running tool call (Bash, WebSearch, large fetch)\n"
          "  • Heavy generation on a very large context\n"
          "  • Compaction in progress\n"
          "  • Rate-limit backoff or subscription limit\n"
          "  • Network wedge or claude crash</blockquote>",
    note="A turn with no news for {n} minutes; the list opens on a tap",
    eg={"session": "x1"})
COMPACTED = Msg("notice.compacted", "📦 <b>{session}</b> · Compacted: {before} → {after}",
                note="The context was compacted: how full before and after; RTL catalogs use ←",
                eg={"session": "x1", "before": "88%", "after": "12%"})
COMPACTED_FROM = Msg("notice.compacted_from", "📦 <b>{session}</b> · Compacted from {before}",
                     note="The context was compacted from {before} full; the size after it "
                          "is not known yet",
                     eg={"session": "x1", "before": "88%"})
COMPACTED_PLAIN = Msg("notice.compacted_plain", "📦 <b>{session}</b> · Compacted",
                      note="The context was compacted; how full it was is not known",
                      eg={"session": "x1"})


def _compacted_text(tr, label: str, before_pct: int, after_pct: int) -> str:
    """The Compacted notice. Right after a compaction Claude Code's status
    line reads 0 until the next message is sent, so an after of 0 is "not
    measured yet", never the new size: the notice then says what it was
    compacted from. A before of 0 is PreCompact having measured nothing."""
    if before_pct <= 0:
        return tr.html(COMPACTED_PLAIN, session=label)
    before = tr.num(f"{before_pct}%")
    if after_pct <= 0:
        return tr.html(COMPACTED_FROM, session=label, before=before)
    return tr.html(COMPACTED, session=label, before=before, after=tr.num(f"{after_pct}%"))


COMPACT_TIMEOUT = Plural(
    "notice.compact_timeout",
    one="⏱️ <b>{session}</b> · Compaction didn't confirm completion after {n} min",
    other="⏱️ <b>{session}</b> · Compaction didn't confirm completion after {n} min",
    note="Compaction gave no sign of finishing within {n} minutes", eg={"session": "x1"})
#: The session ended, by the hook's ``source`` id (an unknown one: exited).
SESSION_END_MSGS: dict[str, Msg] = {
    "clear": Msg("notice.session_end.clear", "🔴 <b>{session}</b> · Session cleared",
                 note="The session's conversation was cleared (/clear)", eg={"session": "x1"}),
    "resume": Msg("notice.session_end.resume",
                  "🔴 <b>{session}</b> · Session switched to another conversation",
                  note="The session moved to another conversation (/resume in Claude Code)",
                  eg={"session": "x1"}),
    "logout": Msg("notice.session_end.logout", "🔴 <b>{session}</b> · Session logged out",
                  note="Claude Code logged out", eg={"session": "x1"}),
    "exited": Msg("notice.session_end.exited", "🔴 <b>{session}</b> · Session exited",
                  note="Claude Code exited", eg={"session": "x1"}),
    "permissions": Msg("notice.session_end.permissions",
                       "🔴 <b>{session}</b> · Session permissions error",
                       note="Claude Code stopped on a permissions error", eg={"session": "x1"}),
    "crashed": Msg("notice.session_end.crashed", "🔴 <b>{session}</b> · Session crashed or killed",
                   note="The session's process vanished", eg={"session": "x1"}),
    # Why a vanished session ended, from the exit code its launch shell
    # recorded (aipager.claude_launch.exit_record).
    "start_failed": Msg(
        "notice.session_end.start_failed",
        "🔴 <b>{session}</b> · Claude Code stopped right after starting (exit code {code})",
        note="Claude Code quit within seconds of starting, with this exit code",
        eg={"session": "x1", "code": "1"}),
    "start_failed_bg": Msg(
        "notice.session_end.start_failed_bg",
        "🔴 <b>{session}</b> · Claude Code stopped right after starting because its "
        "conversation is running in Claude Code's background",
        note="Claude Code quit at start because a background job of Claude Code holds "
             "the conversation; a Bring it back button follows",
        eg={"session": "x1"}),
    "signal": Msg(
        "notice.session_end.signal", "🔴 <b>{session}</b> · Session killed by signal {signal}",
        note="Claude Code was killed by an operating-system signal; {signal} is its "
             "name, such as SIGKILL, kept as it is",
        eg={"session": "x1", "signal": "SIGKILL"}),
    "exit_code": Msg(
        "notice.session_end.exit_code",
        "🔴 <b>{session}</b> · Session exited with error code {code}",
        note="Claude Code exited with this non-zero exit code", eg={"session": "x1", "code": "2"}),
}
_SESSION_END_SOURCES = {
    "clear": "clear", "resume": "resume", "logout": "logout",
    "prompt_input_exit": "exited", "bypass_permissions_disabled": "permissions",
    "disappeared": "crashed",
    # Claude Code's default reason — a signal among others — not evidence
    # of a crash ("disappeared" is the one with evidence: the socket
    # vanished).
    "other": "exited", "unknown": "exited",
}


def session_end_reason(context: dict) -> tuple[str, dict]:
    """The ``SESSION_END_MSGS`` key for a ``session_end`` *context*, and
    its extra parameters. A vanished session (``disappeared``) with the
    exit record its launch shell left (``exit``, added by the monitor)
    says why: 0 exited; 128+N a signal, by name; another code within
    ``START_WINDOW_SECONDS`` of the start stopped right after starting
    (``start_failed_bg`` when a Claude Code background job holds the
    conversation, ``bg_holder``); any other code its error code. Without
    a record: as before."""
    from aipager import claude_jobs
    source = context.get("source", "unknown")
    record = context.get("exit") if source == "disappeared" else None
    if not isinstance(record, dict) or not isinstance(record.get("code"), int):
        return _SESSION_END_SOURCES.get(source, "exited"), {}
    code = record["code"]
    if code == 0:
        return "exited", {}
    name = claude_jobs.exit_signal(code)
    if name:
        return "signal", {"signal": name}
    if claude_jobs.is_start_failure(record):
        if context.get("bg_holder"):
            return "start_failed_bg", {}
        return "start_failed", {"code": code}
    return "exit_code", {"code": code}


def memory_cap_body(hook_name: str, has_owner: bool, tr=None) -> str:
    """What a hook's memory-cap hit did (roadmap 8.109). The hook stops
    with exit 2 on a PreToolUse, which Claude Code reads as a refusal of
    that tool call, and with exit 1 on any other event, which skips only
    that event; the cap-hit message does not say which event it was, so
    the text says both ("most likely": a cap hit while the hook was still
    reading a very large request exits 1 even on a PreToolUse, and the
    tool runs). The status line helper only skips an update. In *tr*'s
    language (English when None)."""
    tr = tr or i18n.get("en")
    what = tr.html(MEMCAP_STATUSLINE if hook_name == "aipager-statusline" else MEMCAP_HOOK,
                   hook=hook_name, limit=1)
    # The notice may go to a group: only the owner's tap does anything.
    nudge = (tr.html(MEMCAP_NUDGE_OWNER) if has_owner else
             tr.html(MEMCAP_NUDGE_CLI, command=Span("<code>aipager report</code>")))
    return what + "\n\n" + nudge
