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
    BUSY_EDIT_INTERVAL, CHAT_ID, COMPACT_ANIMATE_INTERVAL_SECONDS,
    COMPACT_ANIMATE_MAX_TICKS, SPINNER_VERBS,
    STREAM_EDIT_INTERVAL,
)
from aipager.bot.rich_message import (
    detect_rtl,
    edit_message_text_rich,
    RichMessageBlocked,
    RichMessageGone,
)
from aipager.transcript import read_turn_stream
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

_RICH_LIMIT = 32_768  # UTF-8 byte ceiling for rich messages
# Rows are separated by a blank line: Telegram's rich markdown collapses a
# single newline into a space, which would run the whole timeline together.
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


def _advance_tool_cursor(
    sess: TrackedSession, tool_name: str, cursor: int,
) -> int:
    """Walk *cursor* past the ``tool_history`` row produced by *tool_name*.

    Rows are ``"Bash: ..."``-shaped for the tools that get a summary and the
    bare tool name for the rest, so a prefix test identifies both. A Task also
    grows a "🤖 agent" row from SubagentStart, which belongs to the Task's
    tool_use block rather than to one of its own — so the cursor steps over
    those too, keeping later prose below the agent it followed.

    Returns *cursor* unchanged when the row is not there yet, so a transcript
    that has run ahead of the hooks can never walk the cursor off the end.
    """
    history = sess.tool_history
    for i in range(cursor, len(history)):
        summary = history[i][0]
        if summary == tool_name or summary.startswith(f"{tool_name}:"):
            i += 1
            while i < len(history) and history[i][0].startswith(_SUBAGENT_MARK):
                i += 1
            return i
    return cursor


def _expire_tool_batch(sess: TrackedSession) -> None:
    """Give up waiting for prose that is not coming, and stop waiting again.

    A message can call tools without saying anything. Its rows would otherwise
    stay held, and worse, the next message's prose would anchor above them and
    claim tools it never introduced. Advancing the floor here settles them
    where they are, so the next block lands below.
    """
    if sess.stream_batch_since is None:
        return
    if time.monotonic() - sess.stream_batch_since < _BATCH_HOLD_SECS:
        return
    sess.stream_anchor_floor = len(sess.tool_history)
    sess.stream_batch_since = None


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
) -> list[tuple[str, list[str]]]:
    """The timeline as chronological SECTIONS: ("prose", [quote rows]) and
    ("run", [tool rows]) alternating ("layered-card-shedding"). Sections —
    not a flat row list — because the shedding policy treats them
    differently: commentary is the narrative and outlives tool rows.

    Keeps the batch-hold (a run whose introducing sentence hasn't arrived
    yet is withheld) and the answer-filter (a hook-streamed block with no
    tool row at or after its anchor is the final answer, which belongs in
    the answer message) exactly as before. The fixed visible-tools window
    and the commentary character budget are gone: what fits is decided by
    the byte ceiling in :func:`_fit_sections`, per the operator's policy
    ("show all toolcall and commentry untill we reach to the cap").
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

    subagent_started: dict[int, float] = {}
    for info in sess.active_subagents.values():
        idx = info.get("history_idx")
        if idx is not None:
            subagent_started[idx] = info["started_at"]

    sections: list[tuple[str, list[str]]] = []

    def _push(kind: str, row: str) -> None:
        if sections and sections[-1][0] == kind:
            sections[-1][1].append(row)
        else:
            sections.append((kind, [row]))

    for i, (summary, done) in enumerate(history):
        for text in by_anchor.pop(i, ()):
            _push("prose", _quote(text))
        if done == "failed":
            _push("run", f"❌ {_mono(summary)}")
        elif done:
            _push("run", f"✅ {_mono(summary)}")
        else:
            display = summary
            started_at = subagent_started.get(i)
            if started_at:
                secs = int(time.monotonic() - started_at)
                if secs >= 60:
                    display = f"{summary} ({secs // 60}m {secs % 60}s)"
                elif secs >= 2:
                    display = f"{summary} ({secs}s)"
            _push("run", f"⏳ {_mono(display)}")
    for slot in sorted(by_anchor):
        for text in by_anchor[slot]:
            _push("prose", _quote(text))
    return sections


def _run_placeholder(n: int) -> str:
    plural = "s" if n != 1 else ""
    return f"▸ _{n} tool call{plural}_"


def _fit_sections(
    sections: list[tuple[str, list[str]]], reserve_bytes: int,
) -> tuple[list[str], bool]:
    """Collapse the timeline until it fits under ``_RICH_LIMIT`` minus
    ``reserve_bytes`` (the status line + its separator), per the operator's
    layered policy ("layered-card-shedding"):

    - Fits → everything renders, untouched.
    - Phase 1: older tool RUNS collapse to one-line in-place placeholders,
      oldest first — commentary stays.
    - Phase 1b: the NEWEST run sheds its own oldest rows into an in-place
      count; it is never fully collapsed (it is the live tail, and for a
      single giant row the byte backstop is the honest tool).
    - Phase 2: whole oldest sections are removed one at a time, replaced
      by a single aggregate marker at the TOP of the timeline, so what was
      removed always leaves a visible trace. The newest prose section and
      the NEWEST RUN are never removed here — guarded by their indices
      (review rev-iter1-001: a trailing prose section after the newest
      run must not make the run eligible), with the caller's byte-level
      tail-keep truncation as the backstop.

    Byte accounting is incremental (review rev-iter1-005): per-row sizes
    are encoded once and totals adjusted arithmetically per step, so a
    render tick never re-joins the whole body per collapse step.

    Pure and deterministic. Returns ``(rows, truncated)``.
    """
    budget = _RICH_LIMIT - reserve_bytes
    sep = len(_ROW_SEP.encode("utf-8"))

    parts = [list(rows) for _, rows in sections]
    kinds = [k for k, _ in sections]
    sizes = [len(rows) for _, rows in sections]  # original row counts
    row_bytes = [[len(r.encode("utf-8")) for r in rows] for rows in parts]

    def _total(active: list[int]) -> int:
        n_rows = sum(len(parts[i]) for i in active)
        if n_rows == 0:
            return 0
        return (sum(b for i in active for b in row_bytes[i])
                + sep * (n_rows - 1))

    active = list(range(len(parts)))
    if _total(active) <= budget:
        return [r for i in active for r in parts[i]], False

    truncated = True
    all_runs = [i for i, k in enumerate(kinds) if k == "run"]
    newest_run = all_runs[-1] if all_runs else -1

    # Phase 1 — fully collapse older runs, oldest first.
    for idx in all_runs[:-1]:
        ph = _run_placeholder(sizes[idx])
        parts[idx] = [ph]
        row_bytes[idx] = [len(ph.encode("utf-8"))]
        if _total(active) <= budget:
            return [r for i in active for r in parts[i]], truncated

    # Phase 1b — the newest run sheds its own oldest rows.
    if all_runs:
        original = parts[newest_run]
        original_bytes = row_bytes[newest_run]
        for k in range(1, len(original)):
            ph = _run_placeholder(k)
            parts[newest_run] = [ph] + original[k:]
            row_bytes[newest_run] = ([len(ph.encode("utf-8"))]
                                     + original_bytes[k:])
            if _total(active) <= budget:
                return [r for i in active for r in parts[i]], truncated
        if len(original) > 1:
            ph = _run_placeholder(len(original) - 1)
            parts[newest_run] = [ph, original[-1]]
            row_bytes[newest_run] = [len(ph.encode("utf-8")),
                                     original_bytes[-1]]
        else:
            parts[newest_run] = original
            row_bytes[newest_run] = original_bytes

    # Phase 2 — remove whole sections oldest-first behind one marker.
    hidden_prose = 0
    hidden_tools = 0
    hidden_sections = 0
    start = 0
    last_prose = max((i for i, k in enumerate(kinds) if k == "prose"),
                     default=-1)
    while start < len(parts):
        # Never remove the newest prose section, the NEWEST RUN, or the
        # physically last section.
        if (start == last_prose or start == newest_run
                or start >= len(parts) - 1):
            break
        if kinds[start] == "prose":
            # Count BLOCKS, not sections: several commentary blocks can
            # share one section (review rev-iter1-004).
            hidden_prose += sizes[start]
        else:
            hidden_tools += sizes[start]
        hidden_sections += 1
        start += 1
        active = list(range(start, len(parts)))
        marker = _phase2_marker(hidden_sections, hidden_prose, hidden_tools)
        marker_b = len(marker.encode("utf-8"))
        if _total(active) + marker_b + (sep if active else 0) <= budget:
            return ([marker] + [r for i in active for r in parts[i]],
                    truncated)
    active = list(range(start, len(parts)))
    marker_rows = ([_phase2_marker(hidden_sections, hidden_prose,
                                   hidden_tools)]
                   if hidden_sections else [])
    return (marker_rows + [r for i in active for r in parts[i]], truncated)


def _phase2_marker(sections_n: int, prose_n: int, tools_n: int) -> str:
    s_pl = "s" if sections_n != 1 else ""
    c_pl = "ies" if prose_n != 1 else "y"
    t_pl = "s" if tools_n != 1 else ""
    return (f"▸ _{sections_n} earlier step{s_pl} hidden — {prose_n} "
            f"commentar{c_pl} · {tools_n} tool call{t_pl}_")


def _assemble_card(body: str, status: str) -> str:
    """Join the card's parts, status LAST ("status-line-at-card-bottom").
    A blank line between them: Telegram's rich markdown collapses a single
    newline into a space."""
    return f"{body}\n\n{status}" if body else status


def _elapsed_str(started_at: float) -> str:
    """``"45s"`` under a minute, else ``"2m 5s"``. One formatter so every
    card surface agrees on how elapsed time reads."""
    elapsed_s = max(0, int(time.monotonic() - started_at))
    if elapsed_s >= 60:
        return f"{elapsed_s // 60}m {elapsed_s % 60}s"
    return f"{elapsed_s}s"


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
    if waiting and not final:
        phrase = _agent_phrase(sess)
        line = (f"🔄 **{label}** · {phrase}" if phrase == "finishing up"
                else f"🔄 **{label}** · {phrase} still working")
        if sess.busy_started_at:
            line += f" · {_elapsed_str(sess.busy_started_at)}"
        return line

    # ── Segments: state, label, verb, then the turn's live stats ──
    parts: list[str] = []
    if sess.busy_started_at:
        # Shown from the first second. Hiding it below 2s left the card
        # reading "⏳" with no number, then jumping straight to "5s".
        parts.append(_elapsed_str(sess.busy_started_at))

    if sess.cost_baseline is not None and sess.last_cost_usd > 0:
        delta = sess.last_cost_usd - sess.cost_baseline
        if delta > 0.001:
            parts.append(f"${delta:.2f}")

    if sess.tool_history:
        tally: dict[str, int] = {}
        for summary, _done in sess.tool_history:
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

    The card reads: [hidden-steps marker] → timeline → blank line →
    status line ("status-line-at-card-bottom").

    Returns ``(card, hid_something)`` — the card string (always ≤ 32 768
    UTF-8 bytes) and whether ANY collapse, removal, or byte truncation was
    applied ("layered-card-shedding" requirement 2: the renderer reports
    truncation; callers never re-derive it). The plain
    :func:`build_stream_card` wrapper keeps the historical str-only
    signature for every existing call site and test.
    """
    status = _status_line(sess, verb, final=final, waiting=waiting)

    # Reserve: the status line plus the "\n\n" join above it, so the
    # fitter's budget is exactly what the BODY may use.
    reserve = len(status.encode("utf-8")) + len(_ROW_SEP.encode("utf-8"))

    sections = _build_sections(sess, final=final)
    rows, hid_something = _fit_sections(sections, reserve)
    body = _ROW_SEP.join(rows)

    raw = _assemble_card(body, status)

    # ── Byte backstop: drop the head of the body if somehow still over ──
    # The status line is appended after this truncation, never inside it,
    # so it survives as the card's last line no matter what.
    if len(raw.encode("utf-8")) > _RICH_LIMIT and body:
        hid_something = True
        skeleton_bytes = len(_assemble_card("…", status).encode("utf-8"))
        body_budget = _RICH_LIMIT - skeleton_bytes
        if body_budget > 0:
            body_bytes = body.encode("utf-8")
            if len(body_bytes) > body_budget:
                # Drop from the head (oldest text).
                kept_tail = body_bytes[-body_budget:].decode("utf-8", errors="ignore")
                body = "…" + kept_tail
            raw = _assemble_card(body, status)
        else:
            # The status line alone fills the ceiling — drop the body.
            raw = status
    return raw, hid_something



def build_stream_card(
    sess: TrackedSession, verb: str, *, final: bool = False,
    waiting: bool = False,
) -> str:
    """Historical str-returning wrapper over :func:`build_stream_card_ex`."""
    card, _ = build_stream_card_ex(sess, verb, final=final, waiting=waiting)
    return card


def build_full_log(
    label: str,
    tool_history: list,
    commentary: list,
    answer: str,
) -> str:
    """The complete plain-text play-by-play for the full-log attachment
    ("layered-card-shedding" requirement 2): every commentary block and
    every tool row still held in memory, chronological, then the full
    answer. Pure — operates on snapshots the caller captured BEFORE the
    close path reset the streaming state."""
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
                reply_markup=self._build_stop_keyboard(sess),
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
        self, sess: TrackedSession, verb: str, *, final: bool = False,
        waiting: bool = False,
    ) -> bool | None:
        """Edit the busy message with the streaming card.

        ``final`` renders the settled card the turn leaves behind: uncapped
        rows and no Stop button.

        ``waiting`` (design.md "model Claude Code background-agent jobs")
        forwards to :func:`build_stream_card_ex` — the status line becomes
        the background-job waiting presentation while the Stop button and the
        rest of the edit logic stay exactly as they are for an ordinary
        busy card, since the session genuinely can still be interrupted.

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

        def _alive() -> bool:
            return bool(sess.busy_msg_id) and (
                sess.status == Status.BUSY or sess.job_background_open()
            )

        try:
            while _alive():
                # First tick at 1.5 s for a quick initial render, then stream cadence.
                await asyncio.sleep(1.5 if first_tick else STREAM_EDIT_INTERVAL)
                first_tick = False
                if not _alive():
                    break
                waiting = sess.status != Status.BUSY
                if not waiting:
                    # A batch whose message said nothing settles here, so the
                    # card stops holding it and the next prose lands below it.
                    _expire_tool_batch(sess)
                    # Read transcript on every tick regardless of debounce. New
                    # prose is a real change, so it earns the fast cadence.
                    if _read_stream_text(sess):
                        sess.stream_dirty = True
                # Choose the required minimum gap.
                gap = STREAM_EDIT_INTERVAL if sess.stream_dirty else BUSY_EDIT_INTERVAL
                now = time.monotonic()
                if now - sess.last_tool_edit_at < gap:
                    # Debounced — still send typing indicator (skipped while
                    # waiting: nothing is being generated to signal).
                    if not waiting:
                        try:
                            await self._app.bot.send_chat_action(
                                int(resolve_chat_id(sess)), "typing",
                            )
                        except Exception:
                            pass
                    continue
                verb = verbs[idx % len(verbs)]
                idx += 1
                result = await self._edit_busy_rich(sess, verb, waiting=waiting)
                if result is None:
                    break  # permanent failure
                if not waiting:
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
        """Cancel the animation task if running."""
        if sess.animate_task and not sess.animate_task.done():
            sess.animate_task.cancel()
        sess.animate_task = None

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
                self.registry.track_message(msg_id, sess.name, resolve_chat_id_int(sess) or 0)
                self._start_animation(sess)
                log.info("[%s] Busy message sent (msg_id=%d, trigger=%s)",
                         sess.label, msg_id, sess.trigger_msg_id)
            else:
                sess.busy_msg_id = None  # release slot on failure
