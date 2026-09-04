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
    RichMessageFallbackRequired,
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
    _format_perm_detail,
    _PERM_DETAIL_CHARS,
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
# Ceiling on the session monitor's forced stale-card refresh. The refresh
# runs on the monitor's own loop, under the per-session edit lock; the
# HTTP call beneath is bounded (15s httpx timeout, one 429 retry) so this
# only matters if something upstream wedges — and then the monitor must
# get its 2s cadence back rather than hang with the card.
CARD_REFRESH_TIMEOUT = 20.0


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
                _push("agent-run", f"⏳ {_mono(_agent_live_row(info))}",
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
      measured bound, a second wave (still skipping every agent section,
      no other exceptions this time) drops the newest-prose/newest-run/
      last-section survivors too, oldest-first, reassembling and
      re-measuring after each — an agent section (live or settled) must
      never be the section a chop targets first (entrypoints.md), so
      this whole-section drop is preferred over ever touching an agent
      row's own text. Only once nothing non-agent is left to drop does
      :func:`_chop_to_fit` — a raw head-chop, characters first, then
      bytes — become unavoidable; same accepted "rare, can cut mid-block"
      severity class documented in design.md's Risks, now reached only
      when even every non-agent section combined can't make room.

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
            break
        kept_flags[start] = False
        dropped_any = True
        start += 1
        body = _body([i for i in range(n) if kept_flags[i]])
        if _fits(body):
            return body, True

    # Second wave, last resort before ever touching an agent section's own
    # text: the newest-prose/newest-run/last-section protection Step B
    # gave everything above was about ORDINARY shedding, not about "what
    # survives when literally nothing else can be dropped." An agent
    # section (live or settled) must never be the section a chop targets
    # first (entrypoints.md) — so, ONLY when an agent section is actually
    # present to protect, drop whatever non-agent sections remain,
    # oldest-first, with no further exceptions, before falling to a
    # character-level chop that could otherwise corrupt the agent's own
    # row. Without an agent section present, there is nothing to protect
    # from the chop, and dropping the sole remaining (newest-run/prose)
    # section WHOLESALE here would throw away MORE than the ordinary
    # tail-keeping chop does for no benefit — so this wave is skipped
    # entirely in that case, same as before this refinement.
    if any(kept_flags[i] and kinds[i] in ("agent-run", "agent-settled")
           for i in range(n)):
        start = 0
        while start < n:
            if not kept_flags[start] or kinds[start] in ("agent-run", "agent-settled"):
                start += 1
                continue
            kept_flags[start] = False
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
        body = _chop_to_fit(body, char_budget, byte_budget)
    return body, dropped_any


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


def _agent_live_row(info: dict) -> str:
    """``"🤖 <type> · <activity or 'starting'> · <elapsed>"`` — the LIVE
    agent row text ("agent activity rows on the busy card"), shared
    verbatim by both card renderers. Each caller applies its OWN
    destination escaping to the whole returned string exactly as it
    already does for every other tool summary: the rich card wraps it in
    :func:`_mono` (a code span — `_md_escape` would show literal
    backslashes inside one, not protect anything), the legacy card wraps
    it in ``html.escape``. Pure — no I/O, no mutation.
    """
    agent_type = info.get("type") or "agent"
    activity = info.get("activity") or "starting"
    elapsed = _elapsed_str(info.get("started_at", time.monotonic()))
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
                display = _agent_live_row(info) if info is not None else summary
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
                    )
                    return True
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
        tick_failures = 0

        def _alive() -> bool:
            return bool(sess.busy_msg_id) and (
                sess.status == Status.BUSY or sess.job_background_open()
            )

        try:
            while _alive():
                # First tick early for a quick initial render, then stream cadence.
                await asyncio.sleep(
                    FIRST_TICK_DELAY if first_tick else STREAM_EDIT_INTERVAL,
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

    async def _animate_tick(
        self, sess: TrackedSession, verb: str, waiting: bool,
    ) -> bool | None:
        """One tick of :meth:`_animate_busy`, the part that can raise.

        Returns ``None`` on a permanent edit failure (the loop must stop),
        ``True`` when an edit was attempted (the loop rotates the verb),
        ``False`` when the tick was debounced.
        """
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
            return False
        result = await self._edit_busy_rich(sess, verb, waiting=waiting)
        if result is None:
            return None
        if not waiting:
            # Send typing AFTER edit (edit cancels the typing indicator).
            try:
                await self._app.bot.send_chat_action(
                    int(resolve_chat_id(sess)), "typing",
                )
            except Exception:
                pass
        return True

    def _start_animation(self, sess: TrackedSession) -> None:
        """Start the spinner animation task, cancelling any existing one."""
        self._stop_animation(sess)
        sess.animate_task = asyncio.create_task(self._animate_busy(sess))

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
                self._edit_busy_rich(sess, "Working", waiting=waiting),
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
