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
    STREAM_BODY_CHARS, STREAM_EDIT_INTERVAL,
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
# Rows are separated by a blank line: Telegram's rich markdown collapses a
# single newline into a space, which would run the whole timeline together.
_ROW_SEP = "\n\n"
# Oldest tool rows collapse into an "N earlier tools" line beyond this.
_MAX_VISIBLE_TOOLS = 15
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


def _build_timeline_rows(sess: TrackedSession, *, final: bool = False) -> list[str]:
    """Merge commentary and tool rows into one chronological list.

    Each commentary block carries the tool count observed when it was read, so
    a block that arrived between two tools renders between them.

    ``final`` renders the finished card that stays in the chat: it keeps every
    tool row and every commentary block. The live card's caps exist to keep a
    running turn glanceable, but the finished card is the turn's record and is
    read at leisure, so nothing is hidden until the byte ceiling forces it.
    """
    history = sess.tool_history
    if (not final and sess.stream_hook_live
            and sess.stream_batch_since is not None
            and time.monotonic() - sess.stream_batch_since < _BATCH_HOLD_SECS):
        # This batch's introducing sentence has not arrived yet. Drawing the
        # rows now and inserting the quote above them a moment later is the
        # jump the card kept showing, so the batch waits. Released the instant
        # the prose lands — a fraction of a second — or by _expire_tool_batch
        # when the message turns out to have said nothing at all.
        history = history[:sess.stream_anchor_floor]
    if final or len(history) <= _MAX_VISIBLE_TOOLS:
        visible = history
        hidden_done = 0
    else:
        hidden = history[:-_MAX_VISIBLE_TOOLS]
        hidden_done = sum(1 for _, d in hidden if d)
        visible = history[-_MAX_VISIBLE_TOOLS:]
    vis_offset = len(history) - len(visible)

    commentary = sess.stream_commentary
    if sess.stream_hook_live:
        # The hook streams every assistant message, the final answer among
        # them. A block with no tool row at or after its anchor has not been
        # shown to introduce anything: either it is that answer — which
        # belongs in the answer message, not quoted directly above it — or
        # its tools simply have not fired yet, and it appears on the next
        # render. Without this the answer's prose ate the whole commentary
        # budget below, evicting the real commentary from the live card, and
        # then reappeared when the finished card lifted the cap.
        # The transcript fallback keeps its own semantics: there an anchor
        # past the end means the prose was read before its row landed.
        commentary = [(a, t) for a, t in commentary if a < len(history)]

    # Keep the most recent commentary within the character budget, dropping
    # whole blocks oldest-first so a long turn can't grow the card without
    # bound. The newest block always survives, truncated if it alone is huge.
    kept: list[tuple[int, str]] = []
    if final:
        kept = list(commentary)
    else:
        used = 0
        for anchor, text in reversed(commentary):
            if kept and used + len(text) > STREAM_BODY_CHARS:
                break
            if not kept and len(text) > STREAM_BODY_CHARS:
                text = text[:STREAM_BODY_CHARS] + "…"
            kept.append((anchor, text))
            used += len(text)
        kept.reverse()

    # Re-anchor into the visible window rather than dropping: when old tool
    # rows scroll off, their commentary moves to the top of what is shown.
    by_anchor: dict[int, list[str]] = {}
    for anchor, text in kept:
        slot = min(max(anchor, vis_offset), len(history))
        by_anchor.setdefault(slot, []).append(text)

    subagent_started: dict[int, float] = {}
    for info in sess.active_subagents.values():
        idx = info.get("history_idx")
        if idx is not None:
            subagent_started[idx] = info["started_at"]

    rows: list[str] = []
    if hidden_done:
        plural = "s" if hidden_done != 1 else ""
        rows.append(f"✅ _{hidden_done} earlier tool{plural}_")
    for i, (summary, done) in enumerate(visible):
        idx = vis_offset + i
        # Commentary keeps its own markdown — it is Claude's prose and meant to
        # render — inside a blockquote. Tool summaries carry globs and paths, so
        # they go in a code span where none of it can reformat the card.
        rows.extend(_quote(text) for text in by_anchor.pop(idx, ()))
        if done == "failed":
            rows.append(f"❌ {_mono(summary)}")
        elif done:
            rows.append(f"✅ {_mono(summary)}")
        else:
            display = summary
            started_at = subagent_started.get(idx)
            if started_at:
                secs = int(time.monotonic() - started_at)
                if secs >= 60:
                    display = f"{summary} ({secs // 60}m {secs % 60}s)"
                elif secs >= 2:
                    display = f"{summary} ({secs}s)"
            rows.append(f"⏳ {_mono(display)}")
    # Commentary that arrived after the last tool row — the usual place for a
    # turn's opening line, and for whatever Claude says before finishing.
    for slot in sorted(by_anchor):
        rows.extend(_quote(text) for text in by_anchor[slot])
    return rows


def _assemble_card(header: str, body: str) -> str:
    """Join the card's parts. A blank line between them: Telegram's rich
    markdown collapses a single newline into a space."""
    return f"{header}\n\n{body}" if body else header


def _shed_tool_rows(rows: list[str], header: str) -> list[str]:
    """Drop the oldest tool rows until the card fits the byte ceiling.

    Only for the finished card. Commentary is never shed: the prose is the part
    worth re-reading, while tool rows are the part that grows without bound.
    Returns the rows unchanged when they already fit, and returns whatever is
    left when only commentary remains — the caller's byte-level truncation is
    the backstop for that case.
    """
    kept = list(rows)
    shed = 0
    while True:
        # Only tool rows are ever shed, so they all came from at or after the
        # first tool row. Putting the marker there keeps the opening prose
        # above it, where it happened.
        idx = next(
            (i for i, r in enumerate(kept) if not r.startswith(_QUOTE_MARK)),
            len(kept),
        )
        out = list(kept)
        if shed:
            plural = "s" if shed != 1 else ""
            out.insert(idx, f"✅ _{shed} earlier tool{plural}_")
        body = _ROW_SEP.join(out)
        if len(_assemble_card(header, body).encode("utf-8")) <= _RICH_LIMIT:
            return out
        if idx == len(kept):
            return out  # nothing but commentary left
        kept.pop(idx)
        shed += 1


def build_stream_card(sess: TrackedSession, verb: str, *, final: bool = False) -> str:
    """Build the streaming busy-card markdown. Pure: no I/O, no mutation.

    ``final`` builds the finished card left behind after the turn: uncapped
    rows, a settled ✅ in the header, and tool rows shed oldest-first if the
    card is somehow still over the ceiling.

    Returns a string that is always ≤ 32 768 UTF-8 bytes.
    """
    # ── Header segments: state, label, verb, then the turn's live stats ──
    parts: list[str] = []
    if sess.busy_started_at:
        # Shown from the first second. Hiding it below 2s left the card
        # reading "⏳" with no number, then jumping straight to "5s".
        elapsed_s = max(0, int(time.monotonic() - sess.busy_started_at))
        if elapsed_s >= 60:
            parts.append(f"{elapsed_s // 60}m {elapsed_s % 60}s")
        else:
            parts.append(f"{elapsed_s}s")

    if (sess.cost_baseline is not None
            and sess.last_cost_usd > 0):
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
    header = f"{mark} **{_md_escape(sess.label)}** · {verb}"
    if parts:
        header += " · " + " · ".join(parts)

    rows = _build_timeline_rows(sess, final=final)
    if final:
        rows = _shed_tool_rows(rows, header)
    body = _ROW_SEP.join(rows)

    raw = _assemble_card(header, body)

    # ── Truncation: drop the head of the body if over the byte ceiling ──
    encoded = raw.encode("utf-8")
    if len(encoded) <= _RICH_LIMIT:
        return raw

    if body:
        # Compute how many bytes we can spare for the body.
        skeleton = _assemble_card(header, "…")
        skeleton_bytes = len(skeleton.encode("utf-8"))
        body_budget = _RICH_LIMIT - skeleton_bytes
        if body_budget > 0:
            body_bytes = body.encode("utf-8")
            if len(body_bytes) > body_budget:
                # Drop from the head (oldest text).
                truncated = body_bytes[-body_budget:].decode("utf-8", errors="ignore")
                body = "…" + truncated
            raw = _assemble_card(header, body)
        else:
            # Header alone exceeds the limit — drop the body entirely.
            raw = header
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
        self, sess: TrackedSession, verb: str, *, final: bool = False,
    ) -> bool | None:
        """Edit the busy message with the streaming card.

        ``final`` renders the settled card the turn leaves behind: uncapped
        rows and no Stop button.

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
            markdown = build_stream_card(sess, verb, final=final)
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
                None if final else self._build_stop_keyboard(sess.name).to_dict()
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
                    # Debounced — still send typing indicator.
                    try:
                        await self._app.bot.send_chat_action(
                            int(resolve_chat_id(sess)), "typing",
                        )
                    except Exception:
                        pass
                    continue
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
                self.registry.track_message(msg_id, sess.name)
                self._start_animation(sess)
                log.info("[%s] Busy message sent (msg_id=%d, trigger=%s)",
                         sess.label, msg_id, sess.trigger_msg_id)
            else:
                sess.busy_msg_id = None  # release slot on failure
