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

from telegram.error import RetryAfter

from aipager import flood_policy
from aipager.config import (
    BUSY_EDIT_INTERVAL, CARD_CADENCE_FLOOR_GROUP, CARD_CADENCE_FLOOR_PRIVATE,
    CARD_RETRY_WAKE, CARD_STATE_BYPASS_MIN_GAP,
    CARD_STARVATION_BLOCK_TIMEOUT, CHAT_ID, COMPACT_ANIMATE_INTERVAL_SECONDS,
    COMPACT_ANIMATE_MAX_TICKS, SPINNER_VERBS,
    STREAM_EDIT_INTERVAL, TELEGRAM_MAX_RETRY_AFTER,
    TYPING_INDICATOR_INTERVAL,
)
from aipager.bot.flood import MUTE, FloodMuted, _key as _chat_key
from aipager.bot.flood_budget import (
    PRIORITY_ESSENTIAL,
    PRIORITY_ORNAMENT,
    FloodSkipped,
    rate_limit_args as _rl_args,
    _retry_after_seconds as _retry_after_secs,
    card_interval,
    is_group_chat,
)
from aipager.bot.rich_message import (
    detect_rtl,
    edit_message_text_rich,
    get_rate_limiter,
    RichMessageBlocked,
    RichMessageFallbackRequired,
    RichMessageFloodBanned,
    RichMessageGone,
)
from aipager import policy_snapshot
from aipager.transcript import read_turn_blocks, read_turn_stream
from aipager.state import Status, TrackedSession

# Pure-function helpers and constants live in aipager.bot.transport
# now. Re-export the names this module uses internally so the
# TelegramBot class body below (and any external consumers like the
# tests) keeps working without changes.
from aipager.bot.transport import (  # noqa: F401
    ACTION_VERBS,
    edit_text,
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
    _PERM_DETAIL_CHARS,
    _extract_retry_after,
    _is_bot_blocked,
    _log_blocked_once,
    _MAX_TRUNCATIONS,
    _md_safe_boundaries,
    _message_chat_id,
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

_RICH_LIMIT = 32_768  # UTF-8 byte ceiling for rich messages
# Whole-card CHARACTER ceiling — not bytes, a distinct unit from
# _RICH_LIMIT above, and specifically Python's own len() (code points),
# confirmed against emoji-dense content: three live cards all measured
# 8,600 Python-len() characters but diverging UTF-16-unit/byte counts
# (8,600/8,600/8,605 -> 8,600/8,742/9,601 -> 8,600/9,508/11,329) all
# rendered WHOLE — Telegram's fold counts code points specifically, not
# UTF-16 units or bytes, so this budget must be (and is) checked as
# plain len(card), never an encoded length. Operator-pinned at 8,800
# after live cards at 8,002 / 8,600 / 8,800 characters rendered whole and
# 8,952 / 9,102 folded (measured cliff ~8,900-8,950). Folding never buys
# capacity — hidden text counts identically whether visible or behind a
# tap — it only buys readability. Both this and _RICH_LIMIT are checked
# against the REAL assembled string in the same pass (see _fit_sections);
# whichever binds first wins (at 8,800 characters of dense multi-byte
# content the byte ceiling can bind first, ~35 KB, so both are real).
#
# Block STRUCTURE, independently of total size, is what governs how much
# of a folding message shows before Telegram's own fold: the same
# ~8,900-character content folded almost immediately as one unbroken
# paragraph; folded just as early when rows were joined by single
# newlines (Telegram silently merges those into a handful of blocks); but
# showed roughly 50 rows before folding when rows were separated by a
# blank line, each becoming its own paragraph block. That is the
# live-measured reason every row — inside a <details> block or in the
# visible flow — must be its own blank-line-separated block (_ROW_SEP,
# below), never joined by a bare newline.
_CARD_CHAR_BUDGET = 8_800
# Fewer than this many rows in a foldable section never folds — a tap for
# two lines is worse than showing them.
_FOLD_MIN_ROWS = 3
# Rows are separated by a blank line: Telegram's rich markdown collapses a
# single newline into a space, which would run the whole timeline together.
# The SAME separator is used for rows inside a <details> block — a bare
# newline there would let Telegram silently merge them into far fewer
# blocks (live-confirmed — see _CARD_CHAR_BUDGET's own comment above for
# the measured block-structure evidence).
_ROW_SEP = "\n\n"
# How long a batch of tool rows waits for the sentence that introduced it.
# The MessageDisplay hook flushes a short preamble at message end, measured
# 20-515 ms after the batch's first PreToolUse, so this is generous. It only
# runs out when the message called tools without saying anything.
_BATCH_HOLD_SECS = 1.5
# Claude's prose renders as a blockquote; tool rows render monospace. The
# quote marker is also how the shedding pass tells the two apart.
_QUOTE_MARK = "> "
# Prefix of the row SubagentStart adds for a Task — see _match_tool_row.
_SUBAGENT_MARK = "\U0001f916 "
# Header verb for the one last render after the turn ends.
FINAL_VERB = "Done"
# Delay before the animate task's first tick: short so a fresh card gets
# its first real render quickly, before the regular stream cadence.
FIRST_TICK_DELAY = 1.5

# The sentinel `stream_last_rendered` carries while a chat is in minimal
# mode (8.27 R3). It is deliberately NOT the rendered text: the text
# contains the session label, so comparing against the text would re-send
# the line whenever a session is renamed, and a chat at 0.05 calls/s
# cannot afford an edit it does not need. Any real render replaces it, so
# leaving minimal mode re-animates normally on the next tick.
_PAUSED_CARD_TEXT = "\x00minimal-mode-paused"
# Ceiling on the session monitor's forced stale-card refresh. The refresh
# runs on the monitor's own loop, under the per-session edit lock; the
# HTTP call beneath is bounded (15s httpx timeout, one 429 retry) so this
# only matters if something upstream wedges — and then the monitor must
# get its 2s cadence back rather than hang with the card.
CARD_REFRESH_TIMEOUT = 20.0

# The one chat action this daemon sends (roadmap 8.24). Telegram's
# documented contract is that the status "is set for 5 seconds or less
# (when a message arrives from your bot, Telegram clients clear its typing
# status)", so it has to be re-sent every TYPING_INDICATOR_INTERVAL to
# stay lit, which `_animate_typing` does unconditionally — the schedule
# does NOT assume anything about what else clears the status. Whether a
# card EDIT clears it is not established: the 2026-09-12 probe saw the
# bubble lit while edits were being refused, never while one landed. The
# unconditional resend is what makes that question moot.
TYPING_ACTION = "typing"
# A 429 on the chat action gets ONE INFO line per chat per hour (R4). The
# action is off-budget, so its refusal says nothing about the chat's send
# budget — and must not become a log line per refresh either.
_TYPING_429_LOG_INTERVAL = 3600.0
# chat id -> monotonic stamp of the last such line. Module level, like
# `transport._LAST_BLOCKED_LOG_TS`, and LOG-ONLY state: a stale entry can
# cost at most one suppressed INFO line, never a suppressed call. Keyed
# through `flood._key`, so the same chat reached as an int (a scoped
# session) and as a str (the legacy CHAT_ID fallback) earns one line an
# hour between them rather than one each.
_TYPING_429_LOGGED: dict = {}


def _log_typing_429_once(chat, label: str, exc: Exception, *,
                         ban: bool = False) -> None:
    """One INFO line per chat per hour for a 429 on the typing action.

    INFO, not WARNING: the indicator is an ornament on top of the card,
    the card itself is unaffected (a chat action and a card edit are
    metered separately — that is the whole premise of 8.24), and there is
    nothing for the operator to do. The action is skipped; nothing arms
    the mute, bumps the card backoff or defers the chat.

    ``ban`` distinguishes a ``retry_after`` past
    ``TELEGRAM_MAX_RETRY_AFTER`` — a ban rather than a rate limit, which
    the limiter re-raises untouched. It still does not arm the mute from
    here (R4: the mute is the 8.17 owners' to arm, from the paths that
    carry real content), but a chat action refused for ten minutes and one
    refused for five seconds must not read identically in the log.
    """
    key = _chat_key(chat)
    now = time.monotonic()
    last = _TYPING_429_LOGGED.get(key)
    if last is not None and (now - last) < _TYPING_429_LOG_INTERVAL:
        return
    _TYPING_429_LOGGED[key] = now
    log.info(
        "[%s] Telegram %s the typing indicator for chat %s (%s) — skipping "
        "it; the busy card's own cadence is unaffected",
        label, "BANNED" if ban else "rate-limited", chat, exc,
    )


def card_age_decay_enabled(sess: TrackedSession) -> bool:
    """Does this session's busy card decay with turn age (8.30 R4)?

    The ONE place the answer comes from, so the animator's edit gate, the
    card's elapsed unit and the session monitor's stale-card watchdog can
    never disagree about a card's tier.
    """
    return True


def card_age(sess: TrackedSession, now: float) -> float:
    """Seconds this card's turn has been running at *now*, or ``0.0``.

    Anchored on ``busy_started_at`` — the elapsed counter's own anchor,
    shifted forward while a permission prompt waits — so the tier a card
    is in and the counter it shows are measured from the same instant.
    """
    started = sess.busy_started_at
    if not started:
        return 0.0
    return max(now - started, 0.0)


# ── Module-level pure helpers ────────────────────────────────────────────────

def _md_escape(text: str) -> str:
    """Neutralise markdown metacharacters so a path or glob can't reformat the card."""
    return re.sub(r"([*_`\[\]])", r"\\\1", text)


def _mono(text: str) -> str:
    """Wrap *text* in a code span, sized so its own backticks can't break out.

    A code span is delimited by a run of backticks longer than any run inside
    it, and content that starts or ends with a backtick needs a space of
    padding. Backslash escapes do not apply inside a span, so the text goes in
    verbatim — which is the point: a tool summary is full of globs and paths.
    """
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _quote(text: str) -> str:
    """Render *text* as a blockquote, marking every line.

    Lazy continuation would carry an unmarked line into the quote anyway, but
    only until the first blank line — marking each one keeps a multi-paragraph
    block whole.
    """
    return "\n".join(f"{_QUOTE_MARK}{line}" for line in text.split("\n"))


def _find_tool_row(sess: TrackedSession, tool_name: str, cursor: int) -> int | None:
    """Index of the first ``tool_history`` row at or after *cursor* that a
    transcript ``tool_use`` block named *tool_name* produced, or ``None``.

    Rows are ``"Bash: ..."``-shaped for the tools that get a summary and the
    bare tool name for the rest, so a prefix test identifies both. One
    matcher for the transcript fallback's cursor walk and the exact-anchor
    scan, so the two can never disagree about which row a block is.
    """
    history = sess.tool_history
    for i in range(cursor, len(history)):
        summary = history[i][0]
        if summary == tool_name or summary.startswith(f"{tool_name}:"):
            return i
    return None


def _advance_tool_cursor(
    sess: TrackedSession, tool_name: str, cursor: int,
) -> int:
    """Walk *cursor* past the ``tool_history`` row produced by *tool_name*.

    A Task also grows a "🤖 agent" row from SubagentStart, which belongs to
    the Task's tool_use block rather than to one of its own — so the cursor
    steps over those too, keeping later prose below the agent it followed.

    Returns *cursor* unchanged when the row is not there yet, so a transcript
    that has run ahead of the hooks can never walk the cursor off the end.
    """
    history = sess.tool_history
    i = _find_tool_row(sess, tool_name, cursor)
    if i is None:
        return cursor
    i += 1
    while i < len(history) and history[i][0].startswith(_SUBAGENT_MARK):
        i += 1
    return i


def _pop_queued_target(sess: TrackedSession, content: str) -> dict | None:
    """Remove and return the queued target a transcript ``queue-operation``
    line is about, or ``None``.

    The line's ``content`` carries the injected text verbatim (the
    ``[via Telegram · @owner]`` prefix and any reply-context framing
    included), so the user's own text is a substring of it. Among the
    targets whose text appears in *content* the LONGEST wins, oldest
    first on a tie: a short message ("ok") is a substring of a longer
    one ("ok let's go with plan B") and a bare first-match would pop
    the wrong target and strand the real one (review rev-iter1-001).
    Pure apart from the pop."""
    best: int | None = None
    best_len = 0
    for i, target in enumerate(sess.queued_targets):
        text = target.get("raw_text") or ""
        if text and text in content and len(text) > best_len:
            best, best_len = i, len(text)
    return sess.queued_targets.pop(best) if best is not None else None


def _exact_anchors_available(sess: TrackedSession) -> bool:
    """Whether the transcript can place this session's hook-delivered
    sentences exactly: the hook is live (so the transcript is not being
    read for TEXT — see :func:`_read_stream_text`'s duplication guard) and
    the turn pinned a transcript path to scan."""
    return bool(sess.stream_hook_live and sess.stream_transcript_path)


def _expire_tool_batch(sess: TrackedSession) -> None:
    """Give up waiting for prose that is not coming, and stop waiting again.

    A message can call tools without saying anything. Its rows would otherwise
    stay held, and worse, the next message's prose would anchor above them and
    claim tools it never introduced. Advancing the floor here settles them
    where they are, so the next block lands below.

    A FALLBACK only, for a session whose transcript cannot be scanned. When
    it can, :func:`_sync_anchors_from_transcript` settles a silent message
    the moment its round is readable and never a moment sooner — the clock
    cannot tell a silent message from a multi-tool message whose sentence
    arrives at message END, seconds after its first row, and guessing wrong
    put every such sentence below (or in the middle of) its own rows
    ("transcript-exact-sentence-anchors"). The hold in
    :func:`_build_sections` still stops withholding rows on its own clock,
    so the live card keeps drawing them promptly either way.
    """
    if sess.stream_batch_since is None:
        return
    if _exact_anchors_available(sess):
        return
    if time.monotonic() - sess.stream_batch_since < _BATCH_HOLD_SECS:
        return
    sess.stream_anchor_floor = len(sess.tool_history)
    sess.stream_batch_since = None


def _sync_anchors_from_transcript(sess: TrackedSession) -> bool:
    """Place hook-delivered sentences exactly, from the transcript's own
    message structure ("transcript-exact-sentence-anchors").

    Claude Code flushes an assistant message's lines — its text block and
    its tool_use blocks, all sharing ``message.id`` — together, once that
    message's tool-result round ends. That is the ground truth for "which
    rows did this sentence introduce": the MessageDisplay hook delivers
    the sentence (with the same ``message_id``) only at message end, after
    the rows have landed, so arrival order alone cannot say.

    Walks the newly flushed lines from ``stream_offset``. A text line
    marks its message as pending; that message's first tool_use line is
    matched to a row (name order, from ``stream_tool_cursor`` — the same
    rule the transcript fallback has always used) and that row index
    becomes the message's exact anchor: recorded in
    ``stream_exact_anchor`` for a sentence not yet delivered, and written
    into the block ``stream_block_index`` names when it already is. Every
    matched tool line advances the cursor (stepping over the 🤖 rows a
    SubagentStart adds after an Agent row); an unmatched one is skipped —
    the transcript cannot legitimately run ahead of PreToolUse. Afterwards
    the floor is at least the cursor, so the next sentence cannot claim a
    flushed message's rows, and the batch is settled once every row is
    attributed (left open otherwise — the rows past the cursor are the
    next message's, mid-hold).

    Returns True when an existing block moved (the card needs a redraw).
    Never reads text into the card: while the hook is live the transcript
    is read only for STRUCTURE. Inert when the hook is not live (the
    fallback owns the cursor and offset then) or no transcript is pinned.
    """
    if not _exact_anchors_available(sess):
        return False
    items, sess.stream_offset = read_turn_blocks(
        sess.stream_transcript_path, sess.stream_offset,
    )
    if not items:
        return False
    changed = False
    cursor = sess.stream_tool_cursor
    pending: str | None = None
    for kind, value, mid in items:
        if kind == "queue":
            # design.md "turn anchor follows consumption" R4: a message
            # absorbed mid-turn, or delivered to a running background
            # agent, is the ONLY thing that moves the reply target here —
            # `enqueue`/`dequeue`/bare `remove` (discarded)/`popAll` are
            # ignored on purpose (R7: a discard is handled by the stop
            # path; a `dequeue` — the message popped as the NEXT turn —
            # fires no hook at all, so the finish path starts that turn
            # itself from the still-queued target, R8, and the line is
            # confirmation only).
            # Placed FIRST in the loop so a queue item never reaches
            # `_find_tool_row` (which would otherwise stringify its
            # 4-tuple `value` into a bogus tool-name match) and never
            # disturbs `pending`/`cursor` state.
            operation, reason, content, _ts = value
            if operation == "remove" and reason in (
                "absorbed_mid_turn", "delivered_to_agent",
            ):
                # A message queued while this turn ran was recorded as a
                # queued target at its submit-time pick-up (the hook
                # already deleted its note then); this line is the moment
                # Claude actually consumed it
                # ("anchor-on-transcript-consumption"). The on-disk note
                # match stays as the fallback for a note no pick-up ever
                # consumed.
                target = _pop_queued_target(sess, content or "")
                if target is not None:
                    sess.stream_consumed_notes.append(target)
                else:
                    consumed = policy_snapshot.consume_notes_matching(
                        sess.name, content or "",
                    )
                    if consumed:
                        sess.stream_consumed_notes.extend(consumed)
            continue
        if kind == "text":
            pending = mid if mid and mid not in sess.stream_exact_anchor else None
            continue
        if pending is not None and mid != pending:
            # A text-only message (the answer, or one whose tool lines all
            # went unmatched) never borrows the NEXT message's first row.
            pending = None
        row = _find_tool_row(sess, value, cursor)
        if row is None:
            continue
        if pending is not None:
            sess.stream_exact_anchor[pending] = row
            idx = sess.stream_block_index.get(pending)
            if idx is not None and 0 <= idx < len(sess.stream_commentary):
                anchor, text = sess.stream_commentary[idx]
                if anchor != row:
                    sess.stream_commentary[idx] = (row, text)
                    changed = True
            pending = None
        cursor = _advance_tool_cursor(sess, value, cursor)
    sess.stream_tool_cursor = cursor
    if cursor > sess.stream_anchor_floor:
        sess.stream_anchor_floor = cursor
    if cursor >= len(sess.tool_history):
        sess.stream_batch_since = None
    return changed


def _read_stream_text(sess: TrackedSession) -> bool:
    """Append new assistant text blocks to ``sess.stream_commentary``.

    Each block is anchored to the tool row that follows it in the transcript,
    which is what places it between the right tool rows when the card is
    rendered. The anchor comes from the transcript's byte order rather than
    from ``len(sess.tool_history)`` at read time, because Claude Code fires
    PreToolUse *before* it flushes the assistant entry: by the time the prose
    is readable the row it introduces has usually already been appended, and
    anchoring on the live length pushed every comment one row too late.

    Returns True when new text arrived, False otherwise.

    # Thinking blocks stay out of the card: read_turn_stream collects only
    # type="text" content, and thinking is verbose internal reasoning that
    # was never written for a reader. Only prose commentary streams.
    """
    if sess.stream_hook_live:
        # The MessageDisplay hook is wired for this session and is already
        # delivering the same prose, sooner. Reading here too would print
        # every sentence twice. Duplication is impossible in the other
        # direction: a message's transcript entry is not flushed until its
        # tool-result round ends, seconds after the hook has streamed it, so
        # the flag is always latched before the text becomes readable here.
        return False
    if not sess.stream_transcript_path:
        return False
    items, sess.stream_offset = read_turn_stream(
        sess.stream_transcript_path, sess.stream_offset,
    )
    added = False
    for kind, value in items:
        if kind == "tool":
            sess.stream_tool_cursor = _advance_tool_cursor(
                sess, value, sess.stream_tool_cursor,
            )
            continue
        # A text block can hold several paragraphs; split them so each becomes
        # its own row rather than one run-on paragraph.
        for block in value.split("\n\n"):
            block = block.strip()
            if block:
                sess.stream_commentary.append((sess.stream_tool_cursor, block))
                added = True
    return added


def _build_sections(
    sess: TrackedSession, *, final: bool = False,
) -> list[tuple[str, list[str], list[str] | None]]:
    """The timeline as chronological SECTIONS: ``("prose", [quote rows],
    None)``, ``("run", [tool rows], None)``, ``("agent-run", [a single
    LIVE agent row], tools)`` and ``("agent-settled", [a single settled
    agent row], tools)`` alternating ("collapse-busy-card-timeline").
    Sections — not a flat row list — because per-section folding treats
    each independently: commentary never folds, an older run's own tool
    calls fold into that run's own `<details>` block, and an agent's row
    (live or settled) is never itself wrapped or dropped while its
    ``tools`` list — sourced live from ``active_subagents[id]["tools"]``
    or, once settled, from the matching ``finished_subagents`` entry via
    its ``history_idx`` — feeds that agent's OWN nested fold once it has
    3+ calls (:func:`_fit_sections`'s job, not this function's).

    ``agent_tools`` is ``None`` for ``"prose"``/``"run"`` sections (not
    applicable) and the agent's own raw tool-call summary list (verbatim
    strings, possibly empty) for ``"agent-run"``/``"agent-settled"``.

    Keeps the batch-hold (a run whose introducing sentence hasn't arrived
    yet is withheld) and the answer-filter (a hook-streamed block with no
    tool row at or after its anchor is the final answer, which belongs in
    the answer message) exactly as before. The fixed visible-tools window
    and the commentary character budget are gone: what fits is decided by
    :func:`_fit_sections`'s dual char/byte budget.
    """
    history = sess.tool_history
    if (not final and sess.stream_hook_live
            and sess.stream_batch_since is not None
            and time.monotonic() - sess.stream_batch_since < _BATCH_HOLD_SECS):
        history = history[:sess.stream_anchor_floor]

    commentary = sess.stream_commentary
    if sess.stream_hook_live:
        commentary = [(a, t) for a, t in commentary if a < len(history)]

    by_anchor: dict[int, list[str]] = {}
    for anchor, text in commentary:
        slot = min(max(anchor, 0), len(history))
        by_anchor.setdefault(slot, []).append(text)

    # idx -> the full active_subagents info dict (not just started_at) so
    # the renderer has type/activity/tool_count/tools available too
    # ("agent activity rows on the busy card").
    subagent_live: dict[int, dict] = {}
    for info in sess.active_subagents.values():
        idx = info.get("history_idx")
        if idx is not None:
            subagent_live[idx] = info

    # idx -> the finished_subagents entry whose archived row lives at that
    # tool_history index — the new "history_idx" link (this feature's one
    # state addition) lets a SETTLED row find its own nested-fold tools.
    finished_by_idx: dict[int, dict] = {}
    for entry in sess.finished_subagents:
        idx = entry.get("history_idx")
        if idx is not None:
            finished_by_idx[idx] = entry

    sections: list[tuple[str, list[str], list[str] | None]] = []

    def _push(kind: str, row: str, agent_tools: list[str] | None = None) -> None:
        # Agent sections (live or settled) NEVER merge with the previous
        # section, even when it is the same kind — two adjacent agents
        # must not share one tools list.
        if (sections and sections[-1][0] == kind
                and kind not in ("agent-run", "agent-settled")):
            sections[-1][1].append(row)
        else:
            sections.append((kind, [row], agent_tools))

    for i, (summary, done) in enumerate(history):
        for text in by_anchor.pop(i, ()):
            _push("prose", _quote(text))
        if done == "failed":
            _push("run", f"❌ {_mono(summary)}")
        elif done:
            settled = finished_by_idx.get(i)
            if summary.startswith(_SUBAGENT_MARK) and settled is not None:
                # A SETTLED agent row — its own section kind
                # ("agent-settled", not "run") so it is never merged into
                # a plain run section and is protected exactly like a
                # live agent's section. An aged-out entry (past
                # FINISHED_SUBAGENTS_CAP, no match here) falls through to
                # the plain "run" branch below — "same section rules as
                # any other run" per design.
                _push("agent-settled", f"✅ {_mono(summary)}",
                      list(settled.get("tools", [])))
            else:
                _push("run", f"✅ {_mono(summary)}")
        else:
            info = subagent_live.get(i)
            if info is not None:
                # A LIVE agent row — its own section kind ("agent-run", not
                # "run") so it is never itself folded or dropped while the
                # agent it represents is still active. Once settled it is
                # pushed as "agent-settled" above (the `elif done:` branch),
                # not merged into an ordinary run.
                _push("agent-run",
                      f"⏳ {_mono(_agent_live_row(info, _card_unit(sess, final)))}",
                      list(info.get("tools", [])))
            else:
                _push("run", f"⏳ {_mono(summary)}")
    for slot in sorted(by_anchor):
        for text in by_anchor[slot]:
            _push("prose", _quote(text))
    return sections


def _tool_fold_summary(n: int) -> str:
    """A `<details>` block's `<summary>` text — ``"▸ N tool call(s)"``.
    ONE number: commentary never folds, so there is nothing else to
    count. Used for both an older run's own fold and an agent's nested
    fold — same shape either way. Pure."""
    plural = "s" if n != 1 else ""
    return f"▸ {n} tool call{plural}"


_DETAILS_PREFIX = "<details><summary>"
_DETAILS_MID = "</summary>"
_DETAILS_SUFFIX = "</details>"


def _build_details_block(summary: str, rows: list[str]) -> str:
    """Wrap *rows* (verbatim, chronological oldest-first) behind one
    tappable block: ``<details><summary>`` + *summary* + ``</summary>`` +
    a blank line + rows joined by blank lines + a blank line +
    ``</details>``. Every row is its OWN blank-line-separated block —
    never joined by a bare newline — matching ``_ROW_SEP``'s row-
    separation convention at the top level of the card (live-confirmed:
    rows separated by a blank line render one per line inside a block,
    once expanded). Never emits an ``open`` attribute: `<details>` always
    renders collapsed on this client regardless of the flag, and an
    expanded block survives a later ``editMessageText`` edit (live-
    verified), so no live-vs-settled distinction is needed. Pure."""
    return (
        f"{_DETAILS_PREFIX}{summary}{_DETAILS_MID}{_ROW_SEP}"
        f"{_ROW_SEP.join(rows)}{_ROW_SEP}{_DETAILS_SUFFIX}"
    )


_DETAILS_BLOCK_RE = re.compile(
    r"<details><summary>(.*?)</summary>.*?</details>", re.DOTALL,
)


def _strip_details_tags(markdown: str) -> str:
    """Replace EVERY `<details>...</details>` span with just its own
    captured `<summary>` text — tags gone, collapsed content gone, only
    each summary line (e.g. ``"▸ 8 tool calls"``) survives as a plain
    line, in its original chronological position. A card can carry
    several independent blocks (one per folded section, plus any agent
    nested folds); ``re.sub`` replaces every non-overlapping match by
    default, so all of them are stripped, not just the first. Used only
    by the plain-text degradation arm of
    :meth:`AnimationMixin._edit_busy_rich` — belt-and-suspenders against
    ever emitting raw `<details>`/`<summary>` markup into a message
    Telegram will render as literal, unrendered text. Pure."""
    return _DETAILS_BLOCK_RE.sub(r"\1", markdown)


def _render_agent_tool_rows(tools: list[str]) -> list[str]:
    """An agent's own attributed tool-call summaries, formatted the same
    way an ordinary tool row is (a checkmark plus a code span) — these
    are historical, already-attributed calls; no per-call done/failed
    state is tracked for them (``record_agent_tool`` appends on
    PreToolUse, with no separate completion event), so every row gets the
    same settled-looking mark. Pure."""
    return [f"✅ {_mono(t)}" for t in tools]


def _render_section(
    kind: str, rows: list[str], agent_tools: list[str] | None, *,
    is_newest_run: bool,
) -> str:
    """Step A: a section's rendered form, decided once, independent of
    the card's budget (design.md's per-section fold rules):

    - ``"prose"`` never folds — commentary is the narrative.
    - ``"run"``: the newest run is never wrapped regardless of size
      (rule 3); otherwise folds behind its own `<details>` block once it
      has ``_FOLD_MIN_ROWS`` or more rows (rule 4), else stays plain.
    - ``"agent-run"``/``"agent-settled"``: the agent's own row (``rows[0]``)
      always stays plain and visible. Its own tool calls, if any, render
      directly beneath it — folded behind their own nested `<details>`
      block once there are ``_FOLD_MIN_ROWS`` or more (design.md rule 5),
      else shown plain inline (rule 4's "a tap for two lines is worse
      than showing them" is stated as a general threshold rule, not
      scoped to ordinary run sections only — so fewer than three of an
      agent's own tool calls are shown plain rather than silently
      dropped).

    Pure."""
    if kind == "prose":
        return _ROW_SEP.join(rows)
    if kind == "run":
        if is_newest_run or len(rows) < _FOLD_MIN_ROWS:
            return _ROW_SEP.join(rows)
        return _build_details_block(_tool_fold_summary(len(rows)), rows)
    # "agent-run" / "agent-settled"
    agent_row = rows[0]
    tools = agent_tools or []
    if not tools:
        return agent_row
    tool_rows = _render_agent_tool_rows(tools)
    if len(tool_rows) >= _FOLD_MIN_ROWS:
        nested = _build_details_block(_tool_fold_summary(len(tool_rows)), tool_rows)
    else:
        nested = _ROW_SEP.join(tool_rows)
    return f"{agent_row}{_ROW_SEP}{nested}"


def _chop_to_fit(body: str, char_budget: int, byte_budget: int) -> str:
    """Chop *body*'s head to fit BOTH budgets, keeping the tail — the
    pre-feature byte backstop's own recency-keeping shape, just checking
    both units. The direct slice below is a fast, single-shot computation
    (never a per-character loop over the whole string); the trailing
    ``while`` is a bounded, defensive re-verification against the REAL,
    measured string — this function never returns a value without having
    actually checked it against both real bounds, rather than trusting
    the slicing arithmetic in isolation."""
    if char_budget <= 0 or byte_budget <= 0:
        return ""
    if len(body) > char_budget:
        body = "…" + body[-(char_budget - 1):] if char_budget > 1 else body[-1:]
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > byte_budget:
        skeleton_bytes = len("…".encode("utf-8"))
        tail_budget = byte_budget - skeleton_bytes
        if tail_budget > 0:
            kept_tail = body_bytes[-tail_budget:].decode("utf-8", errors="ignore")
            body = "…" + kept_tail
        else:
            body = body_bytes[-byte_budget:].decode("utf-8", errors="ignore")
    # Defensive final verification against the REAL measured string. The
    # slicing above should already satisfy both bounds; this loop is the
    # actual guarantee — it removes one head character at a time and
    # re-measures for real, so no code path here can exit having only
    # trusted an estimate. Bounded: each iteration strictly shrinks the
    # string, and in practice this never iterates more than a handful of
    # times (correcting the "…" marker's own contribution, if anything).
    while body and (len(body) > char_budget or len(body.encode("utf-8")) > byte_budget):
        body = body[1:]
    if len(body) > char_budget or len(body.encode("utf-8")) > byte_budget:
        return ""
    return body


def _fit_sections(
    sections: list[tuple[str, list[str], list[str] | None]],
    reserve_chars: int, reserve_bytes: int,
) -> tuple[str, bool]:
    """Fit the timeline under BOTH the character and byte budgets,
    per-section (design.md's "Algorithm"):

    - **Step A** — render each section's form once, via
      :func:`_render_section`, independent of the budget; cache each
      section's string so it is produced exactly once per section.
    - **Step B** — assemble the FULL candidate body string and measure
      it for real (``len()`` for characters — Telegram's fold is
      confirmed to count Python `len()` code points specifically, not
      UTF-16 units or bytes, live-measured against emoji-dense content;
      ``len(body.encode("utf-8"))`` for bytes). If both real, measured
      bounds already hold, done: this is the common case (a short turn
      renders exactly as before, no dropped content at all). Otherwise
      walk sections oldest-first: skip (never stop at) an agent section
      (live or settled) — a still-live agent can sit anywhere in the
      timeline, and skipping past it must not stop shedding newer
      sections if the budget still demands it; stop at the newest prose
      section, the newest run, or the physically last section (review
      rev-iter1-001: a trailing prose section after the newest run must
      not make the run eligible); otherwise drop the whole section
      (commentary included), REASSEMBLE the candidate body from the
      remaining sections, and measure THAT real string again — never
      inferred from a cached-length running sum, which is exactly the
      class of bug ("measured the wrong quantity while believing itself
      safe") this design replaces. Cached per-section lengths from Step A
      are used only to build each candidate string quickly, never as the
      GO/NO-GO signal on their own; the actual pass/fail decision always
      comes from measuring the real candidate. At most one reassemble+
      measure per SECTION dropped (not per row), so this stays cheap.
    - If the protected floor that remains still exceeds either real,
      measured bound, fall straight to the raw tail-keeping chop. The
      newest run and the newest prose are NEVER dropped wholesale to make
      room (design.md rule 7): a card that keeps its agent row but shows
      no tool rows at all, while its own status line still counts them,
      is worse than one whose head was cut.


    ``dropped`` is True only when a whole section was actually removed or
    the raw chop fired — never merely because some section's own content
    got wrapped in a `<details>` fold. Folding is presentation, not loss.

    Pure and deterministic. Returns ``(body, dropped)`` — the fully
    assembled body string, every fold already in its own chronological
    position, verified to satisfy both real, measured bounds (or empty,
    if even that is impossible at the given reserve sizes).
    """
    char_budget = _CARD_CHAR_BUDGET - reserve_chars
    byte_budget = _RICH_LIMIT - reserve_bytes

    kinds = [k for k, _r, _a in sections]
    all_runs = [i for i, k in enumerate(kinds) if k == "run"]
    newest_run = all_runs[-1] if all_runs else -1
    last_prose = max((i for i, k in enumerate(kinds) if k == "prose"), default=-1)

    # Step A — render each section's own form exactly once.
    texts: list[str] = [
        _render_section(kind, rows, agent_tools, is_newest_run=(i == newest_run))
        for i, (kind, rows, agent_tools) in enumerate(sections)
    ]

    n = len(sections)
    if n == 0:
        return "", False

    def _body(indices: list[int]) -> str:
        return _ROW_SEP.join(texts[i] for i in indices)

    def _fits(body: str) -> bool:
        return len(body) <= char_budget and len(body.encode("utf-8")) <= byte_budget

    kept = list(range(n))
    body = _body(kept)
    if _fits(body):
        return body, False

    # Step B — drop whole oldest sections, reassembling and measuring the
    # REAL candidate string after each drop (never a running-sum estimate).
    kept_flags = [True] * n
    dropped_any = False
    start = 0
    while start < n:
        if kinds[start] in ("agent-run", "agent-settled"):
            start += 1
            continue
        if start == last_prose or start == newest_run or start >= n - 1:
            # Step PAST a protected section, never stop at it (review
            # rev-iter3-001). Stopping here left later, genuinely
            # droppable sections in place — and the raw chop then landed
            # inside one of their own <details> spans, emitting a
            # dangling </details>. The protected sections themselves are
            # still never dropped; they are simply not a wall.
            start += 1
            continue
        kept_flags[start] = False
        dropped_any = True
        start += 1
        body = _body([i for i in range(n) if kept_flags[i]])
        if _fits(body):
            return body, True

    # The protected floor alone still exceeds a real, measured bound —
    # the raw chop always runs here; no path above returns without this
    # final string having actually been verified.
    body = _body([i for i in range(n) if kept_flags[i]])
    if not _fits(body):
        dropped_any = True
        # An agent row sits chronologically BEFORE the newest prose/run, so
        # a plain tail-keeping chop deletes it outright — the operator would
        # lose the only sign that a background agent is running, which is
        # exactly what the agent rows exist to show (review rev-iter1-006).
        # Keep those rows (never their nested folds, which are the bulky
        # part) and chop the NON-agent remainder — never the whole body,
        # which would still carry the agent's own section and duplicate it.
        agent_idx = [i for i in range(n) if kept_flags[i]
                     and kinds[i] in ("agent-run", "agent-settled")]
        agent_rows = [texts[i].split(_ROW_SEP, 1)[0] for i in agent_idx]
        head = _ROW_SEP.join(agent_rows)
        if agent_rows and _fits(head):
            # Assemble from the NON-agent sections only (review
            # rev-iter2-001): including the agent's own section here would
            # repeat the row already kept above it.
            #
            # No textual "repair" of the chopped result is attempted. Once
            # Step B steps past protected sections rather than stopping at
            # them (review rev-iter3-001), the only non-agent sections that
            # can survive to here are the newest prose and the newest run —
            # and neither is ever wrapped in a fold, so this cut cannot land
            # inside one. A scan for a "dangling" close tag was tried and
            # removed: it could not tell markup from a tool row whose own
            # text merely contained `</details>`, and destroyed most of the
            # card when one did (review rev-iter4-001).
            reserve = len(head) + len(_ROW_SEP)
            reserve_b = len(head.encode("utf-8")) + len(_ROW_SEP.encode("utf-8"))
            rest = _body([i for i in range(n)
                          if kept_flags[i] and i not in set(agent_idx)])
            tail = _chop_to_fit(
                rest, char_budget - reserve, byte_budget - reserve_b,
            )
            body = f"{head}{_ROW_SEP}{tail}" if tail else head
        else:
            # Fallback: even the bare agent rows do not fit, so the whole
            # body is cut. This is the ONE path where the cut can still
            # land inside a fold and leave a `</details>` without its
            # opener — reachable only with ~155+ simultaneously kept agent
            # sections carrying long type names, which is past both
            # ACTIVE_SUBAGENTS_CAP and FINISHED_SUBAGENTS_CAP combined and
            # is what state.py calls a runaway. Accepted as the same
            # severity class design.md already carries for the raw chop
            # (review rev-iter5): the alternative needs offset tracking
            # through the cut, and two rounds proved a textual repair
            # cannot tell markup from row content.
            body = _chop_to_fit(body, char_budget, byte_budget)
    return body, dropped_any


def _assemble_card(body: str, status: str) -> str:
    """Join the card's parts, status LAST ("status-line-at-card-bottom").
    A blank line between them: Telegram's rich markdown collapses a single
    newline into a space."""
    return f"{body}\n\n{status}" if body else status


def _elapsed_str(started_at: float, unit: str = "s") -> str:
    """``"45s"`` under a minute, else ``"2m 5s"`` — or, in a decayed
    card's unit (8.30), ``"23m"`` / ``"1h 23m"``. One formatter
    (``flood_policy.format_elapsed``) so every card surface agrees on how
    elapsed time reads."""
    return flood_policy.format_elapsed(time.monotonic() - started_at, unit)


def _agent_live_row(info: dict, unit: str = "s") -> str:
    """``"🤖 <type> · <activity or 'starting'> · <elapsed>"`` — the LIVE
    agent row text ("agent activity rows on the busy card"), shared
    verbatim by both card renderers. Each caller applies its OWN
    destination escaping to the whole returned string exactly as it
    already does for every other tool summary: the rich card wraps it in
    :func:`_mono` (a code span — `_md_escape` would show literal
    backslashes inside one, not protect anything), the legacy card wraps
    it in ``html.escape``. Pure — no I/O, no mutation.

    ``unit`` is the card's elapsed unit (8.30 "other counters"): at a 30 s
    or 60 s tier an agent row counting seconds would look frozen for most
    of every refresh, exactly like the status line would.
    """
    agent_type = info.get("type") or "agent"
    activity = info.get("activity") or "starting"
    elapsed = _elapsed_str(info.get("started_at", time.monotonic()), unit)
    return f"{_SUBAGENT_MARK}{agent_type} · {activity} · {elapsed}"


def _agent_phrase(sess: TrackedSession) -> str:
    """Shared "N agent(s) (types)" fragment of the waiting status line. During the continuation-grace
    window the table is legitimately EMPTY (the agent finished, the
    wake-up hasn't arrived) — "0 agents still working" would read as
    broken (review rev-iter1-004), so that state says "finishing up"
    instead."""
    n = len(sess.active_subagents)
    if n == 0:
        return "finishing up"
    plural = "" if n == 1 else "s"
    phrase = f"{n} agent{plural}"
    types = sorted({info.get("type", "") for info in sess.active_subagents.values()
                    if info.get("type")})
    if 1 <= len(types) <= 3:
        phrase += f" ({', '.join(_md_escape(t) for t in types)})"
    return phrase


def _card_unit(sess: TrackedSession, final: bool) -> str:
    """The unit this render's live counters use: the card's current tier
    unit, or seconds for the settled final card, whose format is as it
    always was."""
    if final:
        return "s"
    unit = getattr(sess, "card_elapsed_unit", "s")
    return unit if unit in ("s", "m", "h") else "s"


def _status_line(
    sess: TrackedSession, verb: str, *, final: bool = False,
    waiting: bool = False,
) -> str:
    """The card's LAST line — its only always-visible position.

    Telegram renders a long message fully expanded and parks the viewport
    at its END, so a status at the TOP scrolls out of sight exactly when a
    turn grows long enough to need it ("status-line-at-card-bottom"). One
    helper for all three frames — ordinary busy, background-waiting, and
    the settled final card — so the waiting variant and the busy variant
    can never drift apart the way a separate header and footer did.

    Never shed: the caller appends it after :func:`_fit_sections` has
    already fitted the timeline around a reserve computed from this exact
    string, and the byte backstop truncates the body's HEAD, keeping the
    tail this line sits at the end of.
    """
    label = _md_escape(sess.label)
    # The card's tier unit (8.30): seconds for the first ten minutes of a
    # turn, then minutes, then hours — matching how often the card is now
    # refreshed, so a slow card still shows a counter that MOVES.
    unit = _card_unit(sess, final)
    if waiting and not final:
        phrase = _agent_phrase(sess)
        line = (f"🔄 **{label}** · {phrase}" if phrase == "finishing up"
                else f"🔄 **{label}** · {phrase} still working")
        if sess.busy_started_at:
            line += f" · {_elapsed_str(sess.busy_started_at, unit)}"
        return line

    # ── Segments: state, label, verb, then the turn's live stats ──
    parts: list[str] = []
    if sess.busy_started_at:
        # Shown from the first second. Hiding it below 2s left the card
        # reading "⏳" with no number, then jumping straight to "5s".
        parts.append(_elapsed_str(sess.busy_started_at, unit))

    if sess.cost_baseline is not None and sess.last_cost_usd > 0:
        delta = sess.last_cost_usd - sess.cost_baseline
        if delta > 0.001:
            parts.append(f"${delta:.2f}")

    if sess.tool_history:
        tally: dict[str, int] = {}
        for summary, _done in sess.tool_history:
            # Agent rows (live "🤖 <type>" or settled "🤖 <type> · N tool
            # calls · elapsed") never contribute a "🤖 ×N" tally segment —
            # their tool calls are counted on the agent's own row, not the
            # parent's tally ("agent activity rows on the busy card"
            # requirement 3). The parent's own Task/Agent tool call is a
            # different summary shape ("Task: <description>", no 🤖
            # prefix) and keeps tallying normally.
            if summary.startswith(_SUBAGENT_MARK):
                continue
            # Summaries are "Read: /path", "Grep: pat in dir" or "🤖 agent-type".
            # Everything before the colon is the tool name; without one, the
            # first word stands in.
            head = summary.split(":", 1)[0] if ":" in summary else summary
            words = head.split()
            name = words[0][:20] if words else ""
            if not name:
                continue
            tally[name] = tally.get(name, 0) + 1
        parts.extend(f"{n} ×{c}" for n, c in tally.items())

    mark = "✅" if final else "⏳"
    line = f"{mark} **{label}** · {verb}"
    if parts:
        line += " · " + " · ".join(parts)
    return line


def build_stream_card_ex(
    sess: TrackedSession, verb: str, *, final: bool = False,
    waiting: bool = False,
) -> tuple[str, bool]:
    """Build the streaming busy-card markdown. Pure: no I/O, no mutation.

    ``final`` builds the finished card left behind after the turn: uncapped
    rows, a settled ✅ in the header, and tool rows shed oldest-first if the
    card is somehow still over the ceiling.

    ``waiting`` (design.md "model Claude Code background-agent jobs")
    swaps ONLY the status line — mark, verb and stats replaced by the
    background-job waiting presentation (agent count/type(s), elapsed) —
    while reusing the unchanged timeline assembly. Mutually exclusive with
    ``final`` in practice (a waiting job is never the settled finished
    card); ``final`` wins if both are somehow passed, since the settled
    card is the more truthful state once composed together.

    The card reads: prose and per-section `<details>` folds interleaved in
    chronological order → blank line → status line
    ("status-line-at-card-bottom").

    Returns ``(card, dropped)`` — the card string (always ≤ 8,800
    characters AND ≤ 32,768 UTF-8 bytes, whichever binds first) and
    whether a whole section was genuinely dropped or the raw chop fired
    ("collapse-busy-card-timeline": the renderer reports truncation,
    callers never re-derive it; folding a section's own content behind a
    tap is presentation, not loss, and never sets this). The plain
    :func:`build_stream_card` wrapper keeps the historical str-only
    signature for every existing call site and test.
    """
    status = _status_line(sess, verb, final=final, waiting=waiting)

    # Reserve: the status line plus the "\n\n" join above it, so the
    # fitter's budget is exactly what the BODY may use — both units,
    # since _fit_sections enforces the char and byte ceilings in the
    # same pass (no separate outer byte pass here).
    reserve_chars = len(status) + len(_ROW_SEP)
    reserve_bytes = len(status.encode("utf-8")) + len(_ROW_SEP.encode("utf-8"))

    sections = _build_sections(sess, final=final)
    body, dropped = _fit_sections(sections, reserve_chars, reserve_bytes)

    raw = _assemble_card(body, status)
    return raw, dropped



def build_stream_card(
    sess: TrackedSession, verb: str, *, final: bool = False,
    waiting: bool = False,
) -> str:
    """Historical str-returning wrapper over :func:`build_stream_card_ex`."""
    card, _ = build_stream_card_ex(sess, verb, final=final, waiting=waiting)
    return card


def _fmt_duration(seconds: float) -> str:
    """``"0s"`` / ``"Xs"`` / ``"Xm Ys"`` — the same three-branch elapsed
    rule as :func:`_elapsed_str`, but over an already-computed duration
    (an agent's own frozen ``elapsed``, not "now minus started_at") for
    :func:`build_full_log`'s AGENTS section."""
    secs = max(0, int(seconds))
    if secs >= 60:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs}s"


def build_full_log(
    label: str,
    tool_history: list,
    commentary: list,
    answer: str,
    *,
    agents: list[dict] | None = None,
) -> str:
    """The complete plain-text play-by-play for the full-log attachment
    ("layered-card-shedding" requirement 2): every commentary block and
    every tool row still held in memory, chronological, then the full
    answer. Pure — operates on snapshots the caller captured BEFORE the
    close path reset the streaming state.

    ``agents`` (NEW, trailing keyword — merge-friendly signature change,
    "agent activity rows on the busy card"): a list of ``{type, elapsed,
    tool_count, tools}`` dicts in chronological (start) order, every agent
    seen this turn (active and finished). Appends an AGENTS section after
    the tool-row list and before FINAL ANSWER; omitted entirely when
    ``None``/empty, so every existing call site's output is unchanged.
    """
    by_anchor: dict[int, list[str]] = {}
    for anchor, text in commentary:
        slot = min(max(anchor, 0), len(tool_history))
        by_anchor.setdefault(slot, []).append(text)
    lines: list[str] = [
        f"{label} — complete play-by-play",
        f"(memory holds the most recent {len(tool_history)} tool rows; "
        "older rows of very long turns may already be gone)",
        "",
    ]
    for i, (summary, done) in enumerate(tool_history):
        for text in by_anchor.pop(i, ()):
            lines.append("")
            lines.append(f"> {text}")
            lines.append("")
        mark = "x" if done == "failed" else ("v" if done else "…")
        lines.append(f"[{mark}] {summary}")
    for slot in sorted(by_anchor):
        for text in by_anchor[slot]:
            lines.append("")
            lines.append(f"> {text}")
    if agents:
        lines += ["", "=" * 40, "AGENTS", "=" * 40]
        for agent in agents:
            agent_type = agent.get("type", "agent")
            elapsed_str = _fmt_duration(agent.get("elapsed", 0.0))
            tool_count = agent.get("tool_count", 0)
            plural = "" if tool_count == 1 else "s"
            lines.append("")
            lines.append(
                f"\U0001f916 {agent_type} — {elapsed_str} — "
                f"{tool_count} tool call{plural}"
            )
            for tool_summary in agent.get("tools", []):
                lines.append(f"  - {tool_summary}")
    if answer:
        lines += ["", "=" * 40, "FINAL ANSWER", "=" * 40, "", answer]
    return "\n".join(lines)


class AnimationMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    async def _safe_edit_callback(
        self, query, text: str, *,
        parse_mode: str | None = None,
        reply_markup=None,
    ) -> None:
        """Edit the message tied to a callback query, swallowing
        edit-failed errors (message gone, identical content, etc.).

        Routed through ``transport.edit_text`` rather than calling
        ``query.edit_message_text`` directly (8.26 R2). A call on a PTB
        UPDATE OBJECT is not gated by anything: ``query`` is not the
        daemon's limiter-bound ``ExtBot``, so the chokepoint underneath
        never sees it, and the static sweep names it an offender for
        exactly that reason. The seam resolves the chat, returns the
        falsy ``MUTED`` sentinel while it is banned, and passes the call
        through untouched otherwise — which is also what deletes the
        hand-written mute check this function used to carry.

        This is the busy card's callback edit: a permission answer or a
        multi-select toggle, the most common tap on this product.
        """
        try:
            await edit_text(
                query, text, parse_mode=parse_mode, reply_markup=reply_markup,
            )
        except Exception:
            log.debug("callback edit failed (probably no-op)", exc_info=True)

    # ── the "typing…" indicator (roadmap 8.24) ──────────────────────────

    def _typing_chat(self, sess: TrackedSession):
        """The chat to light a "typing…" bubble in, or ``None``.

        Pure gate — no stamps, no side effects, nothing a cadence decision
        could read (R3). In order: the feature is on
        (``TYPING_INDICATOR_INTERVAL > 0`` — 0, or a negative value,
        disables it outright), there is an application to send through, the
        session is genuinely BUSY (never IDLE/INTERACTIVE/GONE, so a
        background job's waiting card shows no phantom bubble — R5), the
        chat resolves, and the chat is not flood-MUTED (a mute is a real
        ban; poking it is a fresh violation — R5).

        The once-per-interval bound is NOT here: it belongs to
        :meth:`_animate_typing`, which is the only caller and whose sleep
        IS the schedule. A second bound here would be worse than
        redundant — the task wakes exactly one interval after it last sent,
        so a gate comparing "elapsed < interval" would refuse that wake by
        a few microseconds and halve the real refresh rate.
        """
        if TYPING_INDICATOR_INTERVAL <= 0:
            return None
        if not self._app or sess.status is not Status.BUSY:
            return None
        chat = resolve_chat_id(sess)
        if not chat or MUTE.is_muted(chat):
            return None
        return chat

    async def _send_typing(self, sess: TrackedSession, chat) -> None:
        """Send the chat action. Never raises, never blocks on the chat's
        send budget.

        ``sendChatAction`` is exempt from the per-chat budget in
        ``flood_budget`` (§12/R1): it meets the 30/s overall bucket and
        nothing else, so it cannot take a token a card edit was going to
        spend and cannot be refused because a card just spent one. No
        ``rate_limit_args`` is passed — the exemption is keyed on the
        endpoint, and the skip class this call used to belong to before
        8.21 removed it is exactly what it must NOT rejoin.

        The BUSY status and the mute are re-checked HERE, at send time, and
        not only in :meth:`_typing_chat`: between the gate and the await,
        the turn can end and — more to the point — another coroutine's
        answer can arm the flood mute, and every call into a live ban is a
        fresh violation that extends it (R5). Fail-closed, and it costs a
        dict lookup.

        A 429 is logged once per chat per hour and dropped (R4). Anything
        else goes to debug: the indicator is an ornament, and its caller is
        a background task whose only job is this call.
        """
        if sess.status is not Status.BUSY or MUTE.is_muted(chat):
            return
        try:
            await self._app.bot.send_chat_action(
                chat_id=chat, action=TYPING_ACTION,
                # ORNAMENT (8.26 R3): the bubble is the most disposable
                # thing this daemon sends. Still budget-EXEMPT by
                # endpoint — the class suspends it in minimal mode, it
                # does not start charging it a chat token.
                rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
            )
        except RetryAfter as exc:
            _log_typing_429_once(
                chat, sess.label, exc,
                ban=_retry_after_secs(exc) > TELEGRAM_MAX_RETRY_AFTER,
            )
        except Exception:
            log.debug("[%s] typing indicator failed", sess.label, exc_info=True)

    async def _animate_typing(self, sess: TrackedSession) -> None:
        """Background task: keep the chat's "typing…" bubble lit for as
        long as this session's busy card is live (roadmap 8.24).

        Its OWN task and its OWN clock, deliberately not the card's. The
        first version of 8.24 offered the indicator from inside
        ``_animate_tick``, which cost it twice: the send could only happen
        on the animator's wake grid, so a 4.5 s interval realized as
        ``ceil(4.5 / wake) * wake`` — 5.28 s with one session in a DM,
        6.6 s with two or in a group, i.e. ABOVE Telegram's 5 s expiry in
        every measured configuration, so the bubble blinked off exactly
        where it was supposed to be reassuring; and the card-creation send
        had to be awaited inside ``send_busy``, which is also
        ``_reanchor_busy_card``'s send path and therefore runs inside a
        tick, putting a Telegram round trip in front of a card edit and
        holding ``animate_lock`` across it (review rev-iter1-001/003).

        Here the period is exactly ``TYPING_INDICATOR_INTERVAL``,
        measured from the START of each refresh so the send's own latency
        does not stretch it, and nothing on any card path ever waits for
        it.

        Liveness is ``busy_card_should_animate()`` — the SAME rule the
        animator, the watchdog and the resume path share, so a compacting
        or INTERACTIVE card (whose message is not the animator's to poke)
        and the ``-1`` claim sentinel are all excluded by construction.
        The task deliberately outlives an interim IDLE while a background
        job is open, exactly as ``_animate_busy`` does: the send gate
        (BUSY-only) keeps the waiting card dark, and the continuation's
        BUSY phase gets its bubble back without anything having to restart
        the task.
        """
        try:
            while sess.busy_card_should_animate():
                started = time.monotonic()
                chat = self._typing_chat(sess)
                if chat is not None:
                    await self._send_typing(sess, chat)
                # Sleep the REMAINDER of the interval, so a slow round trip
                # (or a mute check) cannot push the next refresh past
                # Telegram's 5 s expiry. Never negative: a send that took
                # longer than the whole interval yields and goes again, and
                # it cannot spin, because the send itself awaits.
                await asyncio.sleep(max(
                    TYPING_INDICATOR_INTERVAL - (time.monotonic() - started),
                    0.0,
                ))
        except asyncio.CancelledError:
            pass

    def _start_typing(self, sess: TrackedSession) -> None:
        """Start this session's typing task, replacing any existing one.

        Called from :meth:`_start_animation` and nowhere else: that is the
        one place a busy card starts ticking — the fresh card in
        ``_send_busy_and_animate`` (immediately after ``send_busy``
        returns, so a new card still lights its bubble at once), the
        watchdog's restart and the resume path. Starting it here rather
        than in ``send_busy`` also keeps a re-anchor from restarting the
        schedule: the re-anchor reuses ``send_busy`` for its send, and the
        card's existing task is already ticking through it.
        """
        self._stop_typing(sess)
        if TYPING_INDICATOR_INTERVAL <= 0:
            return
        sess.typing_task = asyncio.create_task(self._animate_typing(sess))

    def _stop_typing(self, sess: TrackedSession) -> None:
        """Cancel the typing task if running. Mirrors
        :meth:`_stop_animation`, and is called from it — so every path that
        takes a busy card down (the turn ending, /stop, a kill, a reclaim)
        takes the bubble down with it rather than leaving a session that is
        no longer working looking as though it were.
        """
        if sess.typing_task and not sess.typing_task.done():
            sess.typing_task.cancel()
        sess.typing_task = None

    # ── Notification methods (called by hook_receiver and session_monitor) ──

    async def send_busy(
        self, sess: TrackedSession, *,
        reply_to: int | None = None, disable_notification: bool = False,
    ) -> int | None:
        """Send initial 'Working...' message and start animation. Returns message_id.

        Sends the card and nothing else — in particular no chat action:
        the "typing…" bubble is :meth:`_animate_typing`'s, started by
        :meth:`_start_animation` right after this returns, because this
        function is also a CARD path (see the note at the end of the body).

        ``reply_to``/``disable_notification`` (design.md "turn anchor
        follows consumption") let :meth:`_reanchor_busy_card` reuse this
        same send for a re-anchor: ``reply_to`` defaults to
        ``sess.trigger_msg_id`` (today's behaviour, unchanged for every
        existing call site), and a successful send records
        ``sess.busy_card_trigger`` — which message THIS card currently
        replies to — so a later mismatch against ``trigger_msg_id`` (R3)
        can be detected.
        """
        if not self._app:
            return None
        target = reply_to if reply_to is not None else sess.trigger_msg_id
        text = f"⚙️ <b>{html_mod.escape(sess.label)}</b> · Thinking…"
        try:
            msg = await self._app.bot.send_message(
                resolve_chat_id(sess), text, parse_mode="HTML",
                reply_to_message_id=target,
                reply_markup=self._build_stop_keyboard(sess),
                disable_notification=disable_notification,
                # ORNAMENT (8.26 R3): the card's creation. It is the
                # single largest consumer of a chat's budget (~95 % of
                # outbound volume against the answer's ~5 %), so it is
                # the first thing pressure sheds — and `_reanchor_busy_card`
                # inherits the class through this call.
                rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
            )
            sess.busy_card_trigger = target
            # Read INSIDE the try, as this always has been: a send that
            # answers something without a `message_id` (a stub, a mocked
            # transport, a future API change) is a FAILED send, and the
            # ``except`` below is what turns it into ``None`` for the
            # caller rather than an AttributeError out of the card path.
            msg_id = msg.message_id
        except FloodMuted:
            # The gate refused it (8.26 R1, row A): the chat is banned and
            # this card is an ornament. No card, no crash, no traceback —
            # ABOVE the arm below, which would log a WARNING with a stack
            # trace at card cadence for a refusal that is entirely
            # expected. `None` is the caller's existing "no card" value,
            # so nothing downstream changes. This is the site whose
            # missing mute check cost 9.5 hours on 2026-09-15.
            log.debug("[%s] busy card not sent — chat flood-muted", sess.label)
            return None
        except Exception:
            log.warning("Failed to send busy message", exc_info=True)
            return None
        # NO CHAT ACTION HERE, and that is a fix rather than an omission
        # (review rev-iter1-001). The "typing…" bubble a fresh card earns
        # is sent by `_animate_typing`, whose task `_start_animation`
        # begins the moment this function returns — see `_start_typing`.
        # Sending it from here would put a Telegram round trip on a CARD
        # path: this function is also `_reanchor_busy_card`'s send, which
        # runs inside `_animate_tick` and holds `sess.animate_lock`, so an
        # awaited action here delays the re-anchor's own edit, the old
        # card's delete and `track_message` behind it — exactly what R2
        # and R3 forbid. (The ordering that mattered is preserved for
        # free: the task's first refresh happens after this send, and
        # Telegram clears a typing status when a message from the bot
        # arrives, so an action sent BEFORE the card would be cancelled by
        # the card itself.)
        return msg_id

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
            now = time.monotonic()
            secs = int(now - sess.busy_started_at)
            unit = self._card_elapsed_unit(sess, now)
            if unit != "s":
                # A decayed card (8.30): the same tier unit as the rich card.
                elapsed = f" {flood_policy.format_elapsed(secs, unit)}"
            elif secs >= 2:
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
        # Build a map of history_idx → the full info dict for live agent
        # rows (same widening as _build_sections's subagent_live — "agent
        # activity rows on the busy card").
        _subagent_live: dict[int, dict] = {}
        for info in sess.active_subagents.values():
            idx = info.get("history_idx")
            if idx is not None:
                _subagent_live[idx] = info
        # Compute offset into tool_history for visible slice indices
        _vis_offset = len(history) - len(visible)
        for i, (summary, done) in enumerate(visible):
            if done == "failed":
                text += f"\n❌ <code>{html_mod.escape(summary)}</code>"
            elif done:
                text += f"\n✅ <code>{html_mod.escape(summary)}</code>"
            else:
                info = _subagent_live.get(_vis_offset + i)
                display = (_agent_live_row(info, self._card_elapsed_unit(sess))
                           if info is not None else summary)
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
                # The real command / path (hook_receiver's ``detail``), not
                # only Claude's own description of it: approving a shell
                # command on the model's summary of itself is a weak check.
                # Skipped when the summary already spells it out (a short
                # command with no description, a bare file path).
                text += _format_perm_detail(
                    tool_summary, (perm.get("tool_info") or {}).get("detail") or "")
        return text

    async def _edit_busy_raw(self, msg_id: int, text: str,
                             reply_markup=None, chat_id=None, *,
                             kind: str = "blocking",
                             priority: str = PRIORITY_ORNAMENT) -> bool | None:
        """Edit busy message with pre-built text.

        ``chat_id`` is the chat the busy message lives in; defaults to
        the global ``CHAT_ID`` for callers that don't route per scope.
        Sess-aware callers in the notify path pass
        ``chat_id=resolve_chat_id(sess)``.

        ``kind="skip"`` makes the edit skippable by the per-chat budget
        (roadmap 8.21): it returns False without touching Telegram when the
        chat cannot afford the call right now. No caller passes it today;
        the parameter exists so the two sibling card editors behave
        identically and a future skippable caller cannot land here by
        accident with the wrong semantics.

        Returns True on success, False on transient error,
        None on permanent failure (message gone).
        """
        if not self._app:
            return False
        # Flood-muted (roadmap 8.17b): skip the attempt. False, never
        # None — the mute is transient, while None means "message gone"
        # and makes every caller that inspects the result drop
        # ``busy_msg_id`` and lose the card for good.
        if MUTE.is_muted(chat_id or CHAT_ID):
            return False
        # ORNAMENT (8.26 R3) BY DEFAULT: a card edit is a card edit.
        # `kind` says "may this be refused when the budget is momentarily
        # short"; `class` says "how much is it worth". The animator
        # escalates kind skip -> blocking after two refusals so a card is
        # slow but never frozen, and the class is what keeps that
        # escalated edit from taking an answer's last token.
        #
        # `priority` is overridable for exactly one caller (8.29 T3):
        # `_render_paused_card`, whose whole job is to explain that
        # ornaments have been suspended. Sent as an ornament it is refused
        # by the suspension it exists to announce, and the user is left
        # with a card frozen mid-animation and no reason — which reads as
        # a hung bot, the state the operator paged about.
        extra = {"rate_limit_args": _rl_args(kind=kind, priority=priority)}
        try:
            await self._app.bot.edit_message_text(
                text, chat_id=chat_id or CHAT_ID, message_id=msg_id,
                parse_mode="HTML", reply_markup=reply_markup, **extra,
            )
            return True
        except FloodSkipped:
            # Before the generic arm below, whose string-matching ladder
            # would classify it by accident. False, never None: the budget
            # is transient, while None means "message gone" and costs the
            # card for good.
            return False
        except FloodMuted:
            # The gate refused the edit (8.26 R1). FALSE, NEVER NONE, for
            # exactly the reason above: a mute is transient and the card
            # must survive it. Returning None here would make every caller
            # that inspects the result drop ``busy_msg_id`` and lose the
            # card for good — over a ban that lifts by itself.
            return False
        except Exception as e:
            err = str(e).lower()
            if "message is not modified" in err:
                return True
            if "message to edit not found" in err:
                return None  # permanent: message deleted
            log.debug("Edit busy failed: %s", e)
            return False  # transient: rate-limit, network, etc.

    async def _edit_busy_rich(
        self, sess: TrackedSession, verb: str, *, final: bool = False,
        waiting: bool = False, kind: str = "blocking",
        priority: str = PRIORITY_ORNAMENT,
    ) -> bool | None:
        """Edit the busy message with the streaming card.

        ``final`` renders the settled card the turn leaves behind: uncapped
        rows and no Stop button.

        ``waiting`` (design.md "model Claude Code background-agent jobs")
        forwards to :func:`build_stream_card_ex` — the status line becomes
        the background-job waiting presentation while the Stop button and the
        rest of the edit logic stay exactly as they are for an ordinary
        busy card, since the session genuinely can still be interrupted.

        ``kind="skip"`` (roadmap 8.21) offers the edit to the per-chat
        budget as skippable: if the chat cannot afford a call right now
        the edit is refused with no Telegram call at all, and this returns
        False with every card stamp left untouched, so the next tick
        re-renders exactly the same content. The animation tick passes
        "skip" for ordinary ticks and "blocking" once a card has been
        refused for two intervals, so a card can be slow but never frozen.

        Returns
        -------
        True   — success; ``last_tool_edit_at`` and ``stream_last_rendered``
                 updated, ``stream_dirty`` cleared.
        False  — transient failure, or a skipped edit; caller should retry
                 on the next tick.
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
            # The tier unit every live counter renders in (8.30), set
            # BEFORE the build so the renderer itself stays pure.
            sess.card_elapsed_unit = (
                "s" if final else self._card_elapsed_unit(sess))
            markdown, hid = build_stream_card_ex(
                sess, verb, final=final, waiting=waiting,
            )
            # The close path reads this to decide the full-log attachment
            # ("layered-card-shedding" requirement 2) — the renderer
            # reports truncation, callers never re-derive it.
            sess.last_card_truncated = hid
            # Dedupe: skip the POST when nothing changed since the last render.
            # Primary guard against the "message is not modified" 400. The
            # final render is exempt — it has to go out even if the text
            # matches, or the Stop button would stay attached to a done turn.
            if not final and markdown == sess.stream_last_rendered:
                return True
            is_rtl = detect_rtl(" ".join(t for _a, t in sess.stream_commentary))
            # Omitting reply_markup on editMessageText clears the keyboard,
            # which is how the Stop button comes off the finished card.
            reply_markup = (
                None if final else self._build_stop_keyboard(sess).to_dict()
            )
            try:
                result = await edit_message_text_rich(
                    int(resolve_chat_id(sess)),
                    int(sess.busy_msg_id),
                    markdown,
                    is_rtl=is_rtl,
                    reply_markup=reply_markup,
                    kind=kind,
                    # 8.26 R3: the card is an ORNAMENT — except for the
                    # ONE edit that brings it back from minimal mode
                    # (8.29 T3), which is the card telling the truth about
                    # its own state and must not be shed.
                    priority=priority,
                )
            except FloodSkipped:
                # The chat's budget was short and this edit was skippable,
                # so NOTHING was sent. Transient: False, never None. Every
                # stamp is deliberately left alone — `last_tool_edit_at`
                # so the debounce still lets the next tick through,
                # `stream_last_rendered` / `stream_dirty` so the dedupe
                # below does not suppress the re-render (same reasoning as
                # the RichMessageFallbackRequired arm). `card_skipped_since`
                # marks the START of this run of refusals, not the latest
                # one, so the starvation guard measures the whole run.
                if not sess.card_skipped_since:
                    sess.card_skipped_since = time.monotonic()
                log.debug("[%s] busy-card edit skipped — chat budget short",
                          sess.label)
                return False
            except RichMessageBlocked:
                log.warning("[%s] editMessageText blocked — stopping animation", sess.label)
                return None
            except RichMessageGone:
                log.debug("[%s] editMessageText: message gone — clearing busy_msg_id",
                          sess.label)
                sess.busy_msg_id = 0
                return None
            except RichMessageFloodBanned:
                # Telegram has flood-banned this chat, or still has it
                # muted: stop exactly as on a block. Every further edit
                # is a skipped send and a plain-text degrade would be a
                # fresh violation. Debug only — flood.py logs the mute
                # once, never per skipped edit.
                log.debug("[%s] editMessageText flood-banned — stopping animation",
                          sess.label)
                return None
            except RichMessageFallbackRequired:
                # Defensive: edit_message_text_rich structurally cannot
                # raise this today (research.md gotcha ~53/54) — it only
                # ever raises RichMessageBlocked/RichMessageGone or
                # returns None. This arm pins spec.md's mandatory
                # requirement 4 in case the transport layer changes later
                # ("currently unreachable is not the same as impossible"),
                # proven with a monkeypatch-raise test rather than
                # requiring the unreachable path to fire for real.
                # stream_last_rendered is deliberately left untouched so
                # the dedupe above does not suppress the NEXT tick's rich
                # attempt — the animation loop keeps trying rich again.
                log.warning(
                    "[%s] editMessageText raised RichMessageFallbackRequired "
                    "— degrading to a plain-text edit", sess.label,
                )
                try:
                    await self._app.bot.edit_message_text(
                        _strip_details_tags(markdown),
                        chat_id=int(resolve_chat_id(sess)),
                        message_id=int(sess.busy_msg_id),
                        reply_markup=reply_markup,
                        rate_limit_args=_rl_args(
                            kind="skip", priority=PRIORITY_ORNAMENT),
                    )
                    return True
                except FloodSkipped:
                    # Same contract as the rich arm above: nothing sent,
                    # nothing stamped, try again next tick.
                    if not sess.card_skipped_since:
                        sess.card_skipped_since = time.monotonic()
                    return False
                except Exception:
                    log.debug("[%s] plain-text degrade edit failed", sess.label,
                              exc_info=True)
                    return False
            if result is None:
                # Transient failure (timeout, network, 429 exhausted, etc.)
                return False
            # Success
            sess.last_tool_edit_at = time.monotonic()
            sess.stream_last_rendered = markdown
            sess.stream_dirty = False
            # An edit landed, so the run of refusals (if any) is over.
            sess.card_skipped_since = 0.0
            # The frame state this edit showed: the next STATE change is
            # measured against it (8.30 Q2, `_card_edit_due`).
            sess.card_frame_state = self._card_frame_state(sess)
            return True

    async def _reanchor_busy_card(
        self, sess: TrackedSession, target_msg_id: int, *, final: bool,
    ) -> None:
        """R3/R5 (design.md "turn anchor follows consumption"): the live
        or about-to-be-finalised card is anchored to a stale message —
        Claude consumed a DIFFERENT one for this turn. Re-send the SAME
        timeline under ``target_msg_id`` instead: send new, then delete
        the old one (best-effort) — never the other order.

        Ordering rationale (send-then-delete, not delete-then-send): B8
        ("delete of the old card fails: new card still sent; old one
        left behind") is the observable contract. Sending FIRST
        guarantees the session always has a live card even if the delete
        fails, races, or the process is killed between the two calls —
        deleting first risks a window with NO live card at all if the
        send then fails.

        Never touches any of the per-turn-reset fields
        ``_send_busy_and_animate`` clears (``tool_history``,
        ``stream_commentary``, etc.) — the whole point of a re-anchor is
        the SAME timeline, under a different reply target.

        Takes ``sess.animate_lock`` for its whole body: this serialises
        against ``_send_busy_and_animate`` and against a second
        concurrent re-anchor call, which is also the B9 mechanism — see
        design.md's own discussion of the finish path's ``final=True``
        call always running strictly after ``_stop_animation``.
        """
        async with sess.animate_lock:
            old_msg_id = sess.busy_msg_id
            if not old_msg_id or old_msg_id <= 0:
                # Nothing live (already gone, or a -1 claim from a
                # concurrently-interrupted send) — the caller's own
                # layout fallback (a standalone answer under the now-
                # correct trigger_msg_id) covers this degraded case.
                return
            new_msg_id = await self.send_busy(
                sess, reply_to=target_msg_id, disable_notification=True,
            )
            if not new_msg_id:
                log.warning("[%s] re-anchor send failed — keeping the stale card",
                            sess.label)
                return
            sess.busy_msg_id = new_msg_id  # mutates the SAME "busy" stack entry
            self.registry.track_message(
                new_msg_id, sess.name, resolve_chat_id_int(sess) or 0,
            )
            verb = FINAL_VERB if final else "Working"
            waiting = (sess.status != Status.BUSY) if not final else False
            try:
                if await self._edit_busy_rich(
                    sess, verb, final=final, waiting=waiting,
                ) is None:
                    self._stop_animation(sess)
            except Exception:
                log.debug("[%s] re-anchor timeline render failed", sess.label,
                          exc_info=True)
            try:
                await self._app.bot.delete_message(
                    chat_id=resolve_chat_id(sess), message_id=old_msg_id,
                    # ORNAMENT (8.26 R3): card housekeeping. Leaving a
                    # stale card behind is cosmetic; taking an answer's
                    # token to remove it is not.
                    rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
                )
            except Exception:
                log.debug("[%s] re-anchor: old card delete failed (left behind)",
                          sess.label, exc_info=True)

    @staticmethod
    def _card_elapsed_unit(sess: TrackedSession, now: float | None = None) -> str:
        """The elapsed unit this card's tier renders in right now (8.30)."""
        if now is None:
            now = time.monotonic()
        return flood_policy.elapsed_unit(card_age(sess, now),
                                         card_age_decay_enabled(sess))

    @staticmethod
    def _card_frame_state(sess: TrackedSession) -> tuple:
        """What a card's frame SAYS, as opposed to what it shows: the
        session's status, whether it is waiting on a background job, and
        whether a prompt is open. A change here is a STATE change — the
        one kind of event allowed to bypass the age decay (8.30 Q2)."""
        waiting = sess.status is not Status.BUSY and sess.job_background_open()
        return (sess.status, bool(waiting), bool(sess.pending_permission))

    def _card_edit_due(
        self, sess: TrackedSession, now: float | None = None, *,
        base_gap: float,
    ) -> bool:
        """THE busy-card edit gate (8.30 R4/Q2/Q3): may this card be edited
        at *now*? Every card edit path asks it — the animator's tick with
        its chat interval, notify's hook-driven edits with
        ``STREAM_EDIT_INTERVAL``, the job interim and continuation with 0.

        The required gap is ``max(base_gap, age floor)``, where the floor
        grows with the turn's age (0 / 10 / 30 / 60 s from 0 / 2 / 10 /
        60 min, ``flood_policy.card_age_floor``). Below two minutes it is
        0 and every path paces exactly as it did before 8.30.

        One exception, and only one: a STATE change — the card's frame
        state differs from the one its last landed edit showed — may go
        out before the floor, at most once per
        ``CARD_STATE_BYPASS_MIN_GAP``, and never faster than
        ``base_gap``. A new tool row, prose or a subagent event is NOT a
        state change: it marks the card dirty and rides the next due
        edit. On vm3 676 phantom SubagentStops reached this path in three
        hours; a bypass for events would have kept a four-hour card at a
        10 s cadence for its whole life.

        Stamps ``card_bypass_at`` when it grants a bypass, so the caller
        must call it only when it is about to edit.
        """
        if now is None:
            now = time.monotonic()
        floor = flood_policy.card_age_floor(card_age(sess, now),
                                            card_age_decay_enabled(sess))
        since = now - sess.last_tool_edit_at
        if since >= max(base_gap, floor):
            return True
        if floor <= base_gap or since < base_gap:
            # The age decay is not what is holding this edit back.
            return False
        stamped = sess.card_frame_state
        if stamped is None or stamped == self._card_frame_state(sess):
            return False
        if now - sess.card_bypass_at < CARD_STATE_BYPASS_MIN_GAP:
            return False
        sess.card_bypass_at = now
        return True

    def _card_interval(self, sess: TrackedSession, *, streaming: bool) -> float:
        """Seconds this session's busy card may take per edit (design §4.3).

        ``max(BASE, N x floor) x MARGIN x backoff``, where N is the number
        of BUSY sessions resolving to the SAME chat. The cards of a chat
        share the chat's 1/s budget instead of each assuming it owns one:
        two sessions streaming into one DM at 0.9 s apiece is what earned
        the 2026-09-11 ban.

        N is counted over ``all_sessions()`` filtered by
        ``resolve_chat_id_int`` — NOT the convenient ``all_sessions(chat)``,
        which filters on the RAW ``scope_chat_id`` and treats 0 as matching
        every scope, so every legacy session would be counted into every
        chat (research D9).

        The pacing comes from the live limiter, read late on every call
        and never cached: caching a reference is how two budgets drift
        apart (the 8.17 failure). Since 8.27 that is the chat's EARNED
        rate and its sustained gap as well as the 429 backoff — one
        `pacing_for` read rather than three, so the card cannot pace
        itself against a rate it took one tick and a backoff it took the
        next.

        Telling the cadence about the earned rate is what makes a
        penalised chat animate SLOWLY instead of having every edit
        refused: without it a chat at 0.1 calls/s would ask for an edit
        every 1.3 s and be turned down twelve times out of thirteen.
        """
        chat = resolve_chat_id_int(sess)
        base = STREAM_EDIT_INTERVAL if streaming else BUSY_EDIT_INTERVAL
        if chat is None:
            # Unresolvable chat: pace it as a lone private session rather
            # than not at all.
            busy, group = 1, False
        else:
            busy = sum(
                1 for s in self.registry.all_sessions().values()
                if resolve_chat_id_int(s) == chat and s.status is Status.BUSY
            )
            group = is_group_chat(chat)
        limiter = get_rate_limiter()
        pacing = limiter.pacing_for(chat) if limiter is not None else {}
        return card_interval(
            base=base, busy_sessions=busy, is_group=group,
            backoff=pacing.get("backoff", 1.0),
            chat_rate=pacing.get("rate"),
            sustained_min_gap=pacing.get("sustained_min_gap"),
        )

    def _first_tick_delay(self, sess: TrackedSession) -> float:
        """Delay before the animate task's first tick.

        A fresh card should render quickly, but never faster than the
        chat's own floor — in a group that is 3 s, and a 1.5 s first tick
        would spend a token the card is about to need again.
        """
        floor = (CARD_CADENCE_FLOOR_GROUP
                 if is_group_chat(resolve_chat_id_int(sess))
                 else CARD_CADENCE_FLOOR_PRIVATE)
        return max(FIRST_TICK_DELAY, floor)

    async def _animate_busy(self, sess: TrackedSession) -> None:
        """Background task: stream transcript text while session is BUSY.

        The loop condition widens from ``status == BUSY`` to ``status ==
        BUSY or job_background_open()`` (design.md "model Claude Code
        background-agent jobs"): the SAME task that has been ticking since
        the original prompt keeps ticking straight through the interim
        Stop, through however many phantom/PreToolUse blips land, through
        the ``<task-notification>`` continuation, until the real final
        Stop. Nothing new is started or stopped in between — this is what
        makes the duration anchoring and the Stop-button continuity fall
        out for free rather than needing bespoke bookkeeping.

        Each tick computes ``waiting = status != BUSY`` to pick the render
        frame and to skip the transcript-prose read / typing indicator
        while genuinely idle — a session sitting on an open background job
        isn't generating anything right now, so there is no new prose to
        stream and no "typing" to signal.
        """
        verbs = list(SPINNER_VERBS)
        random.shuffle(verbs)
        idx = 0
        first_tick = True
        tick_failures = 0

        def _alive() -> bool:
            return bool(sess.busy_msg_id) and (
                sess.status == Status.BUSY or sess.job_background_open()
            )

        try:
            while _alive():
                # First tick early for a quick initial render, then the
                # chat's stream cadence. The loop wake also drives the
                # transcript read (`_read_stream_text`,
                # `_sync_anchors_from_transcript`) — a local file read that
                # costs no Telegram call now that nothing but the edit
                # itself is sent from here — so the loop keeps the
                # STREAMING interval while the edits themselves are paced
                # by `_animate_tick`'s gate. Both derive from the same
                # `_card_interval`, so they cannot drift; the TURN-AGE
                # floor (8.30) is applied at the gate only, so a card in
                # its 60 s tier still wakes every few seconds to read the
                # transcript, sync anchors and notice a state flip — none
                # of which is a Telegram call.
                #
                # A card whose last edit was REFUSED by the budget wakes
                # sooner (`CARD_RETRY_WAKE`) instead of sitting out a whole
                # interval. With N cards started by one burst of prompts
                # they tick in phase for ever, and the chat's burst is 3
                # against a 2-token skip reserve — so the third card of
                # every cluster is refused, and if its retry is a full
                # interval away it loses the next cluster too. Measured
                # with three sessions in one DM: 43 edits a minute, 14
                # refused and gaps up to 9.9 s, against 3.3 s flat once the
                # refusal is retried on the stream cadence. It cannot make
                # any card FASTER than its interval: `_animate_tick`'s
                # debounce is the gate, and it is unchanged.
                await asyncio.sleep(
                    self._first_tick_delay(sess) if first_tick
                    else min(self._card_interval(sess, streaming=True),
                             CARD_RETRY_WAKE) if sess.card_skipped_since
                    else self._card_interval(sess, streaming=True),
                )
                first_tick = False
                if not _alive():
                    break
                waiting = sess.status != Status.BUSY
                # One tick that raises must not end the task: the card would
                # sit frozen with its elapsed counter stopped, indistinguishable
                # from a wedged session. Logged with the traceback once per
                # task, then at debug so a persistently failing tick cannot
                # flood the log at stream cadence. CancelledError is a
                # BaseException and passes straight through to the outer
                # handler, so stopping the animation is unaffected.
                try:
                    result = await self._animate_tick(
                        sess, verbs[idx % len(verbs)], waiting,
                    )
                except Exception:
                    tick_failures += 1
                    if tick_failures == 1:
                        log.warning(
                            "[%s] busy-card tick raised — animation continues",
                            sess.label, exc_info=True,
                        )
                    else:
                        log.debug(
                            "[%s] busy-card tick raised again (%d)",
                            sess.label, tick_failures, exc_info=True,
                        )
                    continue
                if result is None:
                    break  # permanent failure
                if result:
                    idx += 1
        except asyncio.CancelledError:
            pass

    async def _render_paused_card(self, sess: TrackedSession) -> bool:
        """While the chat is in minimal mode, show ONE static line and
        stop animating. Returns True when minimal mode is in force.

        The line is the promise the card was making — "this session is
        working" — kept without the animation that was making it. It is
        sent **ESSENTIAL, not ORNAMENT** (8.29 T3 / ruling 1): minimal
        mode suspends ornaments, so an ornament here is refused by the
        very suspension it exists to explain. That is not theory — it is
        what shipped in iteration 1 and what two independent testers
        measured: ten ticks at ``FLOOD_MIN_RATE`` put ZERO requests on the
        wire, leaving a card frozen on its last animated frame with no
        explanation, indistinguishable from a hung bot.

        Sent ONCE per entry into minimal mode, not once per tick, and the
        dedupe is `sess.stream_last_rendered` — the same mechanism the
        streaming card already uses to avoid "message is not modified"
        400s. A chat at 0.05 calls/s cannot afford a repeated edit, and a
        static line repeated is not static.

        AND ONCE MORE WHEN MINIMAL MODE LIFTS, if the card is still live:
        the user was told the card was paused, so they have to be told it
        is not. That render is `sess.card_resume_due`, honoured by
        :meth:`_animate_tick` as one non-debounced ESSENTIAL edit of the
        ordinary animated frame — the card coming back to life IS the
        message, so there is no second line to write and nothing to
        dedupe against.
        """
        limiter = get_rate_limiter()
        if limiter is None:
            return False
        chat = resolve_chat_id_int(sess)
        if chat is None or not limiter.minimal_mode(chat):
            if sess.stream_last_rendered == _PAUSED_CARD_TEXT:
                # Cleared FIRST: whatever happens to the resume edit, this
                # session is no longer showing the paused line, and a
                # marker left behind would suppress the next entry's line.
                sess.stream_last_rendered = ""
                sess.card_resume_due = True
            return False
        if not sess.busy_msg_id or sess.busy_msg_id <= 0:
            return True          # nothing to edit; still minimal
        if sess.stream_last_rendered == _PAUSED_CARD_TEXT:
            return True          # already shown for this entry
        try:
            sent = await self._edit_busy_raw(
                sess.busy_msg_id,
                f"⏳ <b>{html_mod.escape(sess.label)}</b> · working — "
                "updates paused",
                chat_id=resolve_chat_id(sess),
                priority=PRIORITY_ESSENTIAL,
            )
        except Exception:
            log.debug("[%s] paused-card line failed", sess.label, exc_info=True)
            return True
        if sent:
            sess.stream_last_rendered = _PAUSED_CARD_TEXT
        return True

    async def _animate_tick(
        self, sess: TrackedSession, verb: str, waiting: bool,
    ) -> bool | None:
        """One tick of :meth:`_animate_busy`, the part that can raise.

        Returns ``None`` on a permanent edit failure (the loop must stop),
        ``True`` when an edit was ATTEMPTED — landed or refused by the
        per-chat budget alike, since a refusal is transient and the verb
        may as well move on — and ``False`` only when the tick was
        debounced and no Telegram call was made at all.
        """
        if MUTE.is_muted(resolve_chat_id(sess)):
            # R3: the edit and the indicators below are all sends into a
            # flood ban, so this tick makes no Telegram call at all.
            #
            # FALSE, NEVER NONE (8.29 R7). Returning None ended
            # `_animate_busy`'s loop and the task died; the busy-card
            # watchdog then saw a BUSY session with no animate task and
            # restarted it every 20 s for the length of the ban — 348
            # restarts in 54 minutes on 2026-09-15, two log lines each,
            # against six actual HTTP refusals all day. Silencing only the
            # watchdog would not have been enough either: `notify.py`'s
            # tool_use path calls `_resume_animation_if_dead` as well.
            #
            # `False` means "no call made, no stamps touched": the loop
            # stays alive, keeps sleeping at the chat's cadence, and
            # resumes rendering by itself the moment the mute lifts. The
            # card stays as last rendered in the meantime.
            return False
        if await self._render_paused_card(sess):
            # MINIMAL MODE (8.27 R3): this chat's earned rate has fallen
            # under `FLOOD_MINIMAL_MODE_RATE_FLOOR`, so it can no longer
            # afford an animation — but it can still afford the ANSWER,
            # which is the whole point of shedding pixels first.
            #
            # FALSE, NEVER NONE. `None` ends `_animate_busy`'s loop and
            # kills the task; the watchdog then restarts it every 20 s for
            # as long as the condition lasts (348 restarts in 54 minutes,
            # measured 2026-09-15). `False` means "no Telegram call, no
            # stamps touched" — the loop stays alive, keeps sleeping at
            # the chat's new, much slower cadence, and resumes animating
            # by itself the moment the rate recovers.
            return False
        if not waiting:
            # A batch whose message said nothing settles here, so the
            # card stops holding it and the next prose lands below it.
            _expire_tool_batch(sess)
            # Read transcript on every tick regardless of debounce. New
            # prose is a real change, so it earns the fast cadence.
            if _read_stream_text(sess):
                sess.stream_dirty = True
        # Hook live instead: the transcript is read for STRUCTURE only —
        # a round that flushed since the last tick places (or corrects)
        # the sentence it belongs to, exactly. Run this — and the
        # absorption-consumption check right after it — EVEN WHILE
        # ``waiting`` (design.md "turn anchor follows consumption" R4/B5):
        # a background job's waiting frame must still re-anchor when a
        # message is delivered to the running agent, though nothing new
        # is being generated by the parent turn to stream. Both are cheap
        # no-ops when there's nothing new (`_sync_anchors_from_transcript`
        # returns early on an empty read).
        if _sync_anchors_from_transcript(sess):
            sess.stream_dirty = True
        await self._consume_and_reanchor(sess)
        # Choose the required minimum gap: the chat's own interval, which
        # scales with how many BUSY sessions share it and with any 429
        # backoff.
        interval = self._card_interval(sess, streaming=sess.stream_dirty)
        now = time.monotonic()
        # MINIMAL MODE HAS JUST LIFTED and this card's last render was the
        # static "updates paused" line (8.29 T3 / ruling 1). One edit, not
        # debounced and not skippable: the user was told the card was
        # paused, so they are owed the card coming back. Debounced it
        # could sit out an interval the chat no longer needs to take, and
        # as an ORNAMENT it could be refused outright by a budget that is
        # still tight seconds after the rate crossed back over the floor —
        # leaving the paused line on screen for a chat that is no longer
        # paused, which is worse than never having explained.
        #
        # Cleared BEFORE the attempt: one resume edit per lift, whether or
        # not it lands. A retry loop here would be an ornament-grade cost
        # on the exact chat that has just been in trouble.
        if sess.card_resume_due:
            sess.card_resume_due = False
            result = await self._edit_busy_rich(
                sess, verb, waiting=waiting, kind="blocking",
                priority=PRIORITY_ESSENTIAL,
            )
            return None if result is None else True
        if not self._card_edit_due(sess, now, base_gap=interval):
            # Debounced — by the chat's interval, or by the turn-age tier
            # on top of it (8.30) — and that means NO Telegram call at
            # all, of any kind. Until 8.21 this branch still fired a `sendChatAction`,
            # once per loop wake per BUSY session, which the debounce never
            # suppressed: at 0.9 s wakes it roughly DOUBLED the daemon's
            # real call rate into the chat and was the single most frequent
            # chat-scoped call in the incident.
            #
            # 8.24 brought the indicator back but deliberately NOT here:
            # it has its own task and its own 4.5 s clock
            # (`_animate_typing`), so the card loop neither sends it nor
            # paces it, and a wake is not a send in any branch.
            return False
        # An ordinary tick is skippable: a card edit is worth making only
        # if the chat can afford it now, and an answer must always find a
        # token left. But a card that is refused forever is a frozen card,
        # so once this run of refusals is two intervals old, make ONE
        # blocking attempt.
        starved = bool(sess.card_skipped_since) and (
            now - sess.card_skipped_since) >= 2 * interval
        if starved:
            # Bounded: this runs inside `sess._stream_edit_lock`, and the
            # stale-card watchdog RESTARTS a task that holds that lock for
            # CARD_REFRESH_TIMEOUT (20 s). Five seconds is generous against
            # a 1 token/s refill and only bites during a 429 deferral,
            # where not editing is the right answer anyway.
            try:
                result = await asyncio.wait_for(
                    self._edit_busy_rich(sess, verb, waiting=waiting,
                                         kind="blocking"),
                    timeout=CARD_STARVATION_BLOCK_TIMEOUT,
                )
            except asyncio.TimeoutError:
                # Treat as a skip and leave `card_skipped_since` set, so
                # the next tick blocks again rather than dropping back to
                # skipping forever.
                log.debug("[%s] starvation-guard card edit timed out after %.0fs",
                          sess.label, CARD_STARVATION_BLOCK_TIMEOUT)
                result = False
        else:
            result = await self._edit_busy_rich(sess, verb, waiting=waiting,
                                                kind="skip")
        if result is None:
            return None
        # NO TYPING INDICATOR ON THIS PATH EITHER (Case M, as amended by
        # roadmap 8.24): the bubble is real again, but it belongs to
        # `_animate_typing`'s own task, not to the card loop. The card's
        # cadence is the promise and the indicator is the ornament, so the
        # ornament gets neither a slot on the wake grid (which would
        # quantize its 4.5 s to 5.28-6.6 s, past Telegram's 5 s expiry) nor
        # a millisecond of the tick's own time.
        #
        # 8.21 removed this call from every branch on the grounds that it
        # was half of the chat's budget: measured over a simulated minute
        # with two sessions streaming into one DM, the BUDGETED indicator
        # cost 57 calls of which 49% of the card edits were refused, with
        # the cards running 2.2–6.6 s apart; without it, 56 calls, nothing
        # refused, every gap exactly the promised 2.2 s. In a group it was
        # worse (11 edits a minute with ten-second freezes against 18
        # evenly spaced). Those numbers were real, but the cause was not
        # the call — it was charging the call to the chat's SEND budget.
        # Live probes on 2026-09-12 (design §12) settled it: during a real
        # `retry_after=10` window that refused every edit into the chat,
        # all eleven `sendChatAction typing` calls returned 200 and the
        # bubble stayed lit on the phone. Telegram does not meter chat
        # actions with messages. So the indicator comes back exempt from
        # the per-chat budget (`flood_budget.CHAT_ACTION_ENDPOINT`), at
        # most once per TYPING_INDICATOR_INTERVAL per session, and the
        # 8.21 acceptance numbers above are reproduced WITH it on — the
        # edits, the skips and every gap are unchanged, because no card
        # edit ever waits on it or loses a token to it.
        #
        # Why it is worth a call at all: the bubble is the one signal the
        # operator sees in the chat LIST without opening the chat.
        return True

    def _start_animation(self, sess: TrackedSession) -> None:
        """Start the spinner animation task, cancelling any existing one.

        Also (re)starts the session's "typing…" task (roadmap 8.24): the
        bubble is lit for as long as the card is, and this is the one place
        a card starts ticking — so the two cannot get out of step, and
        nothing else has to remember to start it.
        """
        self._stop_animation(sess)
        sess.animate_task = asyncio.create_task(self._animate_busy(sess))
        self._start_typing(sess)

    def _resume_animation_if_dead(
        self, sess: TrackedSession, *, reason: str,
    ) -> bool:
        """Restart the busy-card animation when the card is live but no task
        is ticking it. Returns True when a task was started.

        The primary caller is the ``tool_use`` path in notify.py: a
        permission answered in the terminal (rather than by a Telegram
        button) is invisible to aipager until the next PreToolUse moves
        the session INTERACTIVE → BUSY, and that transition used to leave
        the animation the prompt had stopped dead for the rest of the turn
        — the card repainted only on hook events, its elapsed counter
        frozen in between. The session monitor's watchdog calls this too,
        as the backstop for every other way the task can vanish.

        Same gate as the watchdog (``busy_card_should_animate`` plus the
        ``animate_lock`` check), so neither can restart a task that
        ``_send_busy_and_animate`` is stopping on purpose mid-send, or
        paint a busy frame over a permission prompt or a compaction card.
        """
        if not sess.busy_card_should_animate():
            return False
        if sess.animate_lock.locked() or sess.animation_running():
            return False
        log.info("[%s] busy-card animation not running — resuming (%s)",
                 sess.label, reason)
        self._start_animation(sess)
        return True

    async def _watchdog_busy_card(
        self, sess: TrackedSession, action: str, since: float,
    ) -> None:
        """Carry out one session-monitor watchdog decision
        (:func:`aipager.session_monitor.busy_card_watchdog_action`).

        ``"restart"`` re-arms the animation through the ordinary start
        path. ``"refresh"`` forces one :meth:`_edit_busy_rich` — the same
        lock, dedupe and rate stamps as every other edit, so Telegram's
        edit budget is respected and an unchanged card costs no POST — and
        reports at INFO only when an edit actually landed. A refresh that
        cannot even get through in ``CARD_REFRESH_TIMEOUT`` means the
        task holding the edit lock is wedged: it is replaced.

        The forced refresh is a SKIP caller (roadmap 8.21). A card that is
        stale because its chat is over budget must not be "fixed" by
        spending the budget: a skipped refresh returns False, leaves
        ``last_tool_edit_at`` alone, takes the "made no edit" branch below,
        and the session monitor simply re-arms in 20 s.
        """
        if action == "restart":
            self._resume_animation_if_dead(sess, reason="no animate task while BUSY")
            return
        if action != "refresh":
            log.debug("[%s] unknown busy_card_watchdog action %r", sess.label, action)
            return
        if not sess.busy_card_should_animate() or sess.animate_lock.locked():
            return
        before = sess.last_tool_edit_at
        waiting = sess.status != Status.BUSY
        try:
            result = await asyncio.wait_for(
                self._edit_busy_rich(sess, "Working", waiting=waiting,
                                     kind="skip"),
                timeout=CARD_REFRESH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log.warning(
                "[%s] forced stale-card refresh did not complete in %.0fs — "
                "restarting the busy-card animation", sess.label,
                CARD_REFRESH_TIMEOUT,
            )
            self._start_animation(sess)
            return
        if result is None:
            self._stop_animation(sess)
            return
        if result and sess.last_tool_edit_at != before:
            log.info("[%s] forced stale-card refresh (%.0fs since last edit)",
                     sess.label, since)
        else:
            log.debug("[%s] forced stale-card refresh made no edit (result=%s, "
                      "%.0fs since last edit)", sess.label, result, since)

    async def _animate_compact(self, sess: TrackedSession) -> None:
        """Dot animation while compacting: . → .. → ... → loop.

        Bounded three ways, because an unbounded edit loop is exactly the
        failure this feature exists to prevent:

        1. ``stack_top_kind()`` — the loop is tied to the stack that owns
           it, so whatever pops the compacting entry (the confirming hook,
           or the monitor's deadline sweeper) also ends the animation.
        2. ``COMPACT_ANIMATE_MAX_TICKS`` — a hard iteration ceiling, sized
           to comfortably outlast the deadline sweeper. This is the guard
           that holds even when ``asyncio.sleep`` has been neutralised, as
           much of the test suite does by patching the shared ``asyncio``
           module object. Without it a leaked task spins free and grows an
           AsyncMock's ``mock_calls`` until the machine OOMs.
        3. The pre-existing ``busy_msg_id`` checks below, unchanged.
        """
        dots = [".", "..", "..."]
        idx = 0
        ticks = 0
        try:
            while sess.busy_msg_id and sess.busy_msg_id > 0:
                if sess.stack_top_kind() != "compacting":
                    break
                ticks += 1
                if ticks > COMPACT_ANIMATE_MAX_TICKS:
                    log.warning(
                        "[%s] compact animation exceeded %d ticks — stopping",
                        sess.label, COMPACT_ANIMATE_MAX_TICKS,
                    )
                    break
                await asyncio.sleep(COMPACT_ANIMATE_INTERVAL_SECONDS)
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
        """Cancel the animation task if running — and the typing task with
        it (roadmap 8.24), so a card that stops ticking stops claiming the
        session is working."""
        if sess.animate_task and not sess.animate_task.done():
            sess.animate_task.cancel()
        sess.animate_task = None
        self._stop_typing(sess)

    async def _close_superseded_card(self, sess: TrackedSession) -> None:
        """Settle the waiting card a genuinely new prompt is reclaiming.

        The reclaim branch of :meth:`_send_busy_and_animate` used to only
        forget the old card, which left it frozen in the chat reading
        "N agents still working" under a live Stop button — a status that
        was false the moment the new turn began, and a button whose tap
        would now interrupt the NEW turn. The old job's stream state (tool
        rows, commentary, start time, cost) is still intact here — the
        fresh-send reset runs only after the caller clears ``busy_msg_id``
        — so the card is rendered in its final form in place, the same
        ``FINAL_VERB, final=True`` edit the idle close makes in notify.py.

        Order matters: the old animate task is stopped FIRST (a cancel
        only — it touches none of the state rendered below). Left running,
        it could wake during the final edit's POST, still see the old
        ``busy_msg_id``, and re-arm the Stop button over the settled card.

        Nothing else goes out for the superseded job — its interim text is
        already inside the card (transition() clears the interim buffer
        without a flush on supersede, deliberately) — except the full-log
        attachment, threaded under the old card, when the final render had
        to hide anything: the same ``last_card_truncated`` rule as the idle
        close ("layered-card-shedding" requirement 2).

        Best-effort on every step. A failed or refused edit, or a failed
        attachment, is logged and the caller proceeds to the new turn's
        card regardless.
        """
        old_msg_id = sess.busy_msg_id
        if not old_msg_id or old_msg_id < 0:
            return
        self._stop_animation(sess)
        try:
            kept = await self._edit_busy_rich(sess, FINAL_VERB, final=True)
        except Exception:
            log.info("[%s] superseded card %s: final render failed",
                     sess.label, old_msg_id, exc_info=True)
            return
        if kept is None:
            # Blocked, or the message is gone — nothing left to thread an
            # attachment under.
            log.debug("[%s] superseded card %s: final edit refused",
                      sess.label, old_msg_id)
            return
        if kept is False:
            log.info("[%s] superseded card %s: final edit failed — left as "
                     "last rendered", sess.label, old_msg_id)
        if not sess.last_card_truncated or not self._app:
            return
        label = sess.label
        try:
            content_bytes = build_full_log(
                label, list(sess.tool_history), list(sess.stream_commentary), "",
            ).encode("utf-8")
            if len(content_bytes) > TELEGRAM_MAX_DOC_BYTES:
                log.warning(
                    "[%s] superseded card %s: full log too large for Telegram "
                    "(%.1f MB) — not attached",
                    label, old_msg_id, len(content_bytes) / (1024 * 1024),
                )
                return
            await self._app.bot.send_document(
                resolve_chat_id(sess), document=content_bytes,
                filename=f"{label}_full_log.txt",
                reply_to_message_id=old_msg_id,
                # ORNAMENT (8.26 R3): card housekeeping again — the
                # superseded card's hidden rows. The turn's own answer and
                # its full-log attachment are ESSENTIAL and go out
                # separately; this one compensates a card that is already
                # being replaced.
                rate_limit_args=_rl_args(priority=PRIORITY_ORNAMENT),
            )
        except Exception:
            log.info("[%s] superseded card %s: full-log attachment failed",
                     label, old_msg_id, exc_info=True)

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
            # Stale-reset/bail decision, keyed on the stack's TOP KIND
            # (design.md Decision 8) rather than raw task liveness — this
            # is the actual fix for "one stuck compacting card suppresses
            # every later busy card on this session forever".
            top_kind = sess.stack_top_kind()
            if top_kind == "busy":
                if sess.job_reclaim_pending:
                    # A genuinely new prompt is starting while a PREVIOUS
                    # job's background agents are still open (design.md
                    # "model Claude Code background-agent jobs", Decision
                    # 9). Checked BEFORE the "already showing busy" race
                    # guard below on purpose: that guard's animate task is
                    # the same one still ticking the waiting card (its loop
                    # condition now also holds on job_background_open()),
                    # so without this check it would look "already showing
                    # busy" and swallow this genuinely new prompt entirely
                    # — exactly what requirement 3 forbids. Reclaim instead:
                    # settle the old card in place (final status line, Stop
                    # button off — see _close_superseded_card), clear
                    # busy_msg_id and fall into the normal fresh-send reset
                    # below, same as the stale-animation branch. The
                    # old job's active_subagents tracking is intentionally
                    # lost here (design.md Risks: its eventual SubagentStop
                    # or TTL expiry lands in the already-tolerated "no
                    # matching start" / phantom path).
                    # The flag is set only by transition()'s genuine-new-
                    # turn branch — the one place the BEFORE-state is
                    # visible (review rev-iter2). A message arriving
                    # mid-continuation is a same-state BUSY→BUSY no-op
                    # there, never sets the flag, and falls into the
                    # "already showing busy" no-op below — the same
                    # treatment any ordinary busy turn gives it. A message
                    # arriving over a live waiting card (interim idle or
                    # grace window) DID transition IDLE→BUSY, set the
                    # flag, and reclaims here.
                    sess.job_reclaim_pending = False
                    log.warning(
                        "[%s] new turn starting while a previous job is "
                        "still open (%d agents, continuation=%s, grace=%s) "
                        "— reclaiming the waiting card",
                        sess.label, len(sess.active_subagents),
                        sess.job_continuation_active,
                        bool(sess.job_grace_until),
                    )
                    await self._close_superseded_card(sess)
                    sess.busy_msg_id = None
                elif sess.animate_task and not sess.animate_task.done():
                    return  # already showing busy — original race guard, unchanged
                else:
                    # Clear stale busy state from a previous lifecycle (e.g.
                    # GONE → BUSY). The task is dead, so the previous cycle
                    # ended abnormally — reset so we can send a fresh card.
                    log.debug("[%s] Clearing stale busy_msg_id=%s (animation dead)",
                              sess.label, sess.busy_msg_id)
                    sess.busy_msg_id = None
            elif top_kind == "compacting":
                # A compacting card is reclaimed ONLY once its deadline has
                # passed. An earlier version of this branch reclaimed any
                # compacting top unconditionally, on the argument that this
                # function only runs when sess.status != Status.BUSY, so a
                # compacting top here must be desynced. That argument is
                # FALSE: _direct_send (handlers.py, the `/label <text>`
                # path) and the retry callback (callbacks.py) both call
                # registry.transition(name, Status.BUSY) — which writes
                # sess.status synchronously — on the line immediately
                # before calling this function. So a genuinely in-progress
                # compaction is reachable here, and tearing it down would
                # orphan its card and misdirect the eventual compact_done
                # edit onto the replacement card.
                #
                # The entry's own deadline is the honest test of staleness,
                # and it is the same signal the monitor's sweeper uses.
                if not sess.compacting_is_overdue(time.monotonic()):
                    return  # genuine compaction in flight — leave it alone
                entry = sess.pop_compacting()
                elapsed = (time.monotonic() - entry.created_at
                           if entry is not None else 0.0)
                log.warning(
                    "[%s] Reclaiming abandoned compacting card (msg_id=%s, "
                    "live %.0fs) to send a fresh busy card",
                    sess.label, entry.msg_id if entry is not None else None,
                    elapsed,
                )
                sess.busy_msg_id = None  # also stops the old animate task
                                          # via the normal task-replacement
                                          # path below.
            # top_kind is None (empty stack) → proceed as today.
            sess.busy_msg_id = -1  # sentinel: claim slot before async yield
            self._stop_animation(sess)
            sess.last_tool_summary = ""
            sess.tool_history.clear()
            sess.active_subagents.clear()
            sess.finished_subagents.clear()
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
            # A genuinely NEW job starts here (design.md "model Claude Code
            # background-agent jobs") — the dedup hash from any PREVIOUS
            # job must not suppress this one's first interim/final answer
            # just because the two happen to share text.
            sess.last_idle_summary_hash = ""
            sess.job_interim_seen = False
            sess.job_continuation_active = False
            sess.job_grace_until = 0.0
            sess.job_reclaim_pending = False
            sess.job_interim_buffer.clear()
            sess.last_card_truncated = False
            sess.busy_started_at = time.monotonic()
            # A new turn is a new card: tier 0, seconds, no frame stamped.
            sess.card_elapsed_unit = "s"
            sess.card_frame_state = None
            sess.card_bypass_at = 0.0
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
            # design.md "turn anchor follows consumption": a fresh turn
            # starts with no stale re-anchor state. send_busy re-seeds
            # busy_card_trigger correctly a few lines below regardless —
            # this reset only matters for the window before that call.
            sess.busy_card_trigger = None
            sess.stream_consumed_notes = []
            tp = sess.transcript_path
            if tp:
                sess.stream_transcript_path = tp
                try:
                    sess.stream_offset = os.path.getsize(tp)
                except OSError:
                    sess.stream_offset = 0
            msg_id = await self.send_busy(sess)
            if msg_id:
                sess.busy_msg_id = msg_id
                sess.last_tool_edit_at = 0.0
                sess.last_tool_name = ""
                # Track the busy message so replies to it route back to this session,
                # even hours later or after a daemon restart.
                self.registry.track_message(msg_id, sess.name, resolve_chat_id_int(sess) or 0)
                self._start_animation(sess)
                log.info("[%s] Busy message sent (msg_id=%d, trigger=%s)",
                         sess.label, msg_id, sess.trigger_msg_id)
            else:
                sess.busy_msg_id = None  # release slot on failure
