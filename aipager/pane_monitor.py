"""Async tmux pane monitor — fallback for missed hooks.

Runs every PANE_POLL_INTERVAL seconds, scrapes all claude-* tmux sessions,
classifies them as BUSY/IDLE/INTERACTIVE, and calls registry.transition().
If the hook already handled the transition, transition() returns None (no-op).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from aipager import tmux_inject
from aipager.config import PANE_POLL_INTERVAL, RICH_SUMMARIES
from aipager.md_to_tg import markdown_to_telegram_html
from aipager.state import SessionRegistry, Status
from aipager.transcript import extract_last_response, find_transcript

log = logging.getLogger(__name__)


# Claude Code spinner detection. The spinner is a single non-letter character
# followed by a space and a word ending in … (ellipsis). The spinner character
# rotates through many shapes (·, ✽, ✶, ✳, *, •, ◆, ⧫, ⎿, etc.) so we match
# broadly: any single non-alphanumeric, non-whitespace char + space + word + …
_SPINNER = re.compile(r'^[^\w\s❯●] \S+…')


def classify_pane(lines: list[str]) -> tuple[Status, str, dict | None]:
    """Classify pane content into a status.

    Returns (status, summary_text, selector_context).
    - IDLE: bare ❯ in input box, no spinner anywhere in visible pane
    - INTERACTIVE: selector visible ("Enter to select")
    - BUSY: spinner visible or no bare ❯ prompt

    Key insight: Claude Code's input box at the bottom ALWAYS shows a bare ❯,
    even while Claude is thinking. The spinner appears ABOVE the input box in
    the conversation area. So we must check for spinners in the entire visible
    pane, not just below the ❯ prompt.
    """
    if not lines:
        return Status.BUSY, "", None

    # Check for selector (permission/question prompt) — highest priority
    tail = lines[-10:]
    has_selector = any("Enter to select" in l and "navigate" in l for l in tail)
    if has_selector:
        text, options = _extract_selector(lines)
        return Status.INTERACTIVE, text, {"selector_text": text, "selector_options": options}

    # Check for spinner ANYWHERE in the last 15 non-decoration lines.
    # If a spinner is visible, Claude is busy regardless of ❯.
    for l in lines[-15:]:
        stripped = l.strip()
        if _SPINNER.match(stripped):
            return Status.BUSY, "", None

    # No spinner — check for bare ❯ (input box prompt)
    has_bare_prompt = False
    for l in lines[-8:]:
        if l.strip() == "❯":
            has_bare_prompt = True
            break

    if not has_bare_prompt:
        return Status.BUSY, "", None

    summary = _extract_pane_summary(lines)
    return Status.IDLE, summary, None


def _extract_pane_summary(pane_lines: list[str]) -> str:
    """Extract Claude's last response from pane.

    The pane layout is:
        ● Claude's response text           ← we want this
        ────────────────────────            ← input box top border
        ❯                                   ← input box (always present)
        ────────────────────────            ← input box bottom border
        Opus 4.6 | Context: 87%            ← status bar

    We find the input box border (───) and look ABOVE it for ● content.
    """
    # Find the input box top border — it's the ─── line just above the bare ❯
    input_box_top = -1
    for i in range(len(pane_lines) - 1, max(len(pane_lines) - 10, -1), -1):
        if pane_lines[i].strip().startswith("───"):
            # Check if the next non-blank line after this is bare ❯
            for j in range(i + 1, min(i + 3, len(pane_lines))):
                if pane_lines[j].strip() == "❯":
                    input_box_top = i
                    break
            if input_box_top >= 0:
                break

    # Boundary: everything above the input box is conversation
    boundary = input_box_top if input_box_top > 0 else len(pane_lines)

    # Find the last ● (assistant response marker) before the boundary.
    # Search ALL lines above the boundary (scrollback captured with -S -100).
    start_idx = -1
    for i in range(boundary - 1, -1, -1):
        if pane_lines[i].strip().startswith("●"):
            start_idx = i
            break

    if start_idx < 0:
        # Fallback: no ● found even in scrollback. Grab recent content lines
        # above the input box (skip decoration, prompts, blanks).
        return _fallback_summary(pane_lines, boundary)

    out = []
    for i in range(start_idx, boundary):
        stripped = pane_lines[i].strip()
        if not stripped or stripped.startswith("───") or stripped.startswith("✻"):
            continue
        if stripped.startswith("● "):
            stripped = stripped[2:]
        elif stripped == "●":
            continue
        # Skip old user input prompts that might be in the conversation area
        if stripped.startswith("❯"):
            continue
        out.append(stripped)

    summary = "\n".join(out).strip()
    return summary


def _fallback_summary(pane_lines: list[str], boundary: int) -> str:
    """Fallback when ● marker not found — grab last content lines above input box."""
    skip_prefixes = ("───", "✻", "❯", "●")
    out = []
    for i in range(boundary - 1, max(boundary - 15, -1), -1):
        stripped = pane_lines[i].strip()
        if not stripped or any(stripped.startswith(p) for p in skip_prefixes):
            continue
        # Skip status bar lines
        if "Context:" in stripped or "Opus" in stripped or "Sonnet" in stripped:
            continue
        out.append(stripped)
        if len(out) >= 5:
            break
    out.reverse()
    return "\n".join(out).strip()


def _extract_selector(pane_lines: list[str]) -> tuple[str, list[tuple[int, str]]]:
    """Extract question and numbered options from Claude Code selector UI.

    Returns (question_text, [(option_num, label), ...]).
    """
    selector_end = -1
    selector_start = -1
    for i, l in enumerate(pane_lines):
        if "Enter to select" in l and "navigate" in l:
            selector_end = i
        if "☐" in l:
            selector_start = i

    if selector_end < 0:
        return "", []

    # Extract question text
    question = ""
    if selector_start >= 0:
        for i in range(selector_start + 1, selector_end):
            stripped = pane_lines[i].strip()
            if re.match(r'^[❯\s]*\d+\.', stripped):
                break
            if stripped and not stripped.startswith("───"):
                question = stripped
                break

    # Extract numbered options
    options = []
    for i in range(max(selector_start, 0), selector_end):
        stripped = pane_lines[i].strip()
        m = re.match(r'^[❯\s]*(\d+)\.\s+(.+)$', stripped)
        if m:
            num = int(m.group(1))
            label = m.group(2).strip()
            if label.lower() not in ("type something.", "chat about this"):
                options.append((num, label))

    return question, options


class PaneMonitor:
    """Periodically scrapes tmux panes as fallback for missed hooks."""

    def __init__(self, registry: SessionRegistry, notify_fn):
        self.registry = registry
        self.notify_fn = notify_fn
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("Pane monitor started (every %.1fs)", PANE_POLL_INTERVAL)

    async def _loop(self) -> None:
        while True:
            try:
                await self._scan()
            except Exception:
                log.exception("Pane monitor error")
            await asyncio.sleep(PANE_POLL_INTERVAL)

    async def _scan(self) -> None:
        sessions = await tmux_inject.list_sessions()

        # Mark disappeared sessions as GONE
        for name, sess in list(self.registry.all_sessions().items()):
            if name not in sessions and sess.status != Status.GONE:
                self.registry.transition(name, Status.GONE)

        for name in sessions:
            lines = await tmux_inject.capture_pane(name)
            status, summary, context = classify_pane(lines)

            # Track previous status before transition (needed for transcript logic)
            prev = self.registry.get(name)
            prev_status = prev.status if prev else Status.UNKNOWN

            sess = self.registry.transition(name, status, summary=summary)
            if sess is None:
                continue  # no state change — hook already handled it

            if status == Status.IDLE:
                notify_ctx = {"summary": summary}
                # Try transcript-based markdown summary only on BUSY→IDLE
                # (not UNKNOWN→IDLE on startup, which would misattribute transcripts)
                tracked = self.registry.get(name)
                tp = None
                if RICH_SUMMARIES:
                    tp = (tracked.transcript_path if tracked else "") or (
                        find_transcript(name) if prev_status == Status.BUSY else None)
                if tp:
                    try:
                        md = extract_last_response(tp)
                        if md and "```" in md:
                            # Only use rich HTML when response has code blocks;
                            # plain text responses look better in blockquotes.
                            html_summary = markdown_to_telegram_html(md)
                            notify_ctx = {
                                "summary": html_summary,
                                "html_summary": True,
                                "raw_md": md,
                            }
                            log.info("[%s] Using transcript (%d chars HTML)", name, len(html_summary))
                    except Exception:
                        log.info("[%s] Transcript failed, pane fallback", name)
                await self.notify_fn(sess, "idle_prompt", notify_ctx)
            elif status == Status.INTERACTIVE:
                ctx = context or {}
                await self.notify_fn(sess, "permission_prompt", ctx)
            # BUSY transitions don't send notifications (handled by telegram_bot edit)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
