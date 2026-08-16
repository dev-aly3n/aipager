"""Pure, I/O-free session-shaping helpers for the Mini App's read routes.

Everything in this module takes a :class:`~aipager.state.TrackedSession`
(and, where relevant, a caller-supplied monotonic timestamp) and returns
plain dicts/lists — no registry access, no subprocess, no network. That
keeps it trivially unit-testable and keeps ``server.py``'s handlers thin:
auth + scope resolution + registry lookup live in ``server.py``; shaping
the JSON lives here.

See design.md Decision 1 for why "waiting on permission" is derived here
rather than becoming a new ``Status`` enum member, and Decision 3 for why
the timeline is a new, simpler function rather than a reuse of
``bot/animation.py``'s Telegram-markdown renderer.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from aipager.state import Status

if TYPE_CHECKING:
    from aipager.state import TrackedSession

# Wire contract (entrypoints.md): the externally-visible status name for
# "blocked on a human" is always "waiting" — the Status enum's own
# "interactive" member name never appears in a stage-2 response body.
_WAITING_STATUS = "waiting"


def _derive_status(sess: "TrackedSession") -> tuple[str, str | None, str | None]:
    """Return ``(status, waiting_kind, waiting_summary)``.

    ``Status.INTERACTIVE`` alone means waiting — every hook path that
    reaches it (PermissionRequest, the ``permission_prompt`` fallback,
    AskUserQuestion) is entered only via a permission-request or
    question path, so it is blocked on a human by construction, whether
    or not ``pending_permission`` happens to be populated (it is
    ``None`` on the "fell back to a separate Telegram message" path —
    ``bot/notify.py:982,988`` — while status still reads INTERACTIVE).
    ``pending_permission`` is therefore only consulted here to *enrich*
    ``waiting_kind``/``waiting_summary``, never to gate ``"waiting"``
    itself.
    """
    if sess.status != Status.INTERACTIVE:
        return sess.status.name.lower(), None, None

    perm = sess.pending_permission
    if not perm:
        return _WAITING_STATUS, None, None
    if perm.get("ask_question"):
        return _WAITING_STATUS, "question", perm.get("question")
    return _WAITING_STATUS, "permission", perm.get("tool_summary")


# Display order for the grid (design §2: "ordered by last activity", with
# gone sorted last regardless of recency so the grid never becomes a
# graveyard — the failure observed live, 14 dead sessions padding the view).
# Computed server-side rather than in JavaScript so it is a pure function
# pytest can exercise directly; the client renders the order it is given.
_STATUS_RANK = {"waiting": 0, "busy": 1, "idle": 2, "unknown": 3, "gone": 4}
_UNKNOWN_RANK = _STATUS_RANK["unknown"]


def _order_key(row: dict[str, Any]) -> tuple[int, float, str]:
    """Sort key: status group, then most-recently-active, then label.

    ``last_active_seconds_ago`` is ``None`` for a session with no recorded
    activity — those sort LAST within their group rather than being read as
    "0 seconds ago" (which would push never-used sessions to the top).
    Label is the final tiebreaker so the order is stable across polls
    instead of shuffling on every refresh.
    """
    rank = _STATUS_RANK.get(row.get("status", ""), _UNKNOWN_RANK)
    age = row.get("last_active_seconds_ago")
    return (rank, float("inf") if age is None else age, row.get("label", ""))


def sort_for_display(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order grid rows for the two-column Sessions tab."""
    return sorted(rows, key=_order_key)


def grid_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Header stats derived from the same rows the grid renders.

    Computed here, not client-side, so the numbers the operator reads are
    covered by the same tests as the rows themselves.
    """
    live = [r for r in rows if r.get("status") != "gone"]
    return {
        "total": len(rows),
        "live": len(live),
        "gone": len(rows) - len(live),
        "waiting": sum(1 for r in rows if r.get("status") == "waiting"),
        # Spend is meaningful across every session that ever ran, including
        # finished ones — that is what the run actually cost.
        "cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in rows), 4),
    }


def session_summary(sess: "TrackedSession", now: float) -> dict[str, Any]:
    """Shape one grid-row entry for ``GET /api/sessions``.

    Deliberately excludes ``cwd`` (never sent to the polled grid — see
    design.md Decision 5) and ``waiting_summary`` (a drill-down-only
    detail field). ``now`` is a caller-supplied ``time.monotonic()``
    reading so every row in one response is measured against the same
    instant, and so this stays a pure function for testing.
    """
    status, waiting_kind, _summary = _derive_status(sess)
    last_active = round(now - sess.last_hook_at) if sess.last_hook_at else None
    return {
        "label": sess.label,
        "status": status,
        "waiting_kind": waiting_kind,
        "model": sess.model_name or "",
        "context_pct": sess.last_token_pct or 0,
        "cost_usd": round(sess.last_cost_usd or 0.0, 4),
        "last_active_seconds_ago": last_active,
        "project": os.path.basename(sess.cwd) if sess.cwd else "",
    }


def session_detail(sess: "TrackedSession", now: float) -> dict[str, Any]:
    """Shape the drill-down payload for ``GET /api/sessions/{label}``.

    ``busy_elapsed_seconds`` is populated whenever a turn is actually in
    flight — BUSY, or INTERACTIVE while that turn's clock is still
    running (``busy_started_at`` is shifted forward across a permission
    wait per its docstring in state.py, so it stays meaningful there
    too); ``None`` for IDLE/GONE/UNKNOWN, where it would just be stale.
    """
    status, waiting_kind, waiting_summary = _derive_status(sess)
    last_active = round(now - sess.last_hook_at) if sess.last_hook_at else None
    busy_elapsed = None
    if sess.busy_started_at and sess.status in (Status.BUSY, Status.INTERACTIVE):
        busy_elapsed = round(now - sess.busy_started_at)
    detail = {
        "label": sess.label,
        "status": status,
        "waiting_kind": waiting_kind,
        "waiting_summary": waiting_summary,
        "model": sess.model_name or "",
        "context_pct": sess.last_token_pct or 0,
        "cost_usd": round(sess.last_cost_usd or 0.0, 4),
        "cwd": sess.cwd or "",
        "last_active_seconds_ago": last_active,
        "busy_elapsed_seconds": busy_elapsed,
        # The page's headline content. Unlike `timeline`, this survives a
        # daemon restart (`last_assistant_preview` is in state.py's
        # _PERSIST_FIELDS, tool_history/stream_commentary are not), so it
        # is the one thing that reliably has content for an older session.
        "last_message": preview_lines(sess.last_assistant_preview),
        "timeline": build_timeline(sess),
    }
    detail["facts"] = display_facts(detail)
    return detail


# Roughly three phone-width lines. `last_assistant_preview` is already a
# ~200-char extract upstream; this only guards against a longer one
# arriving and pushing everything below it off the screen.
_PREVIEW_MAX_CHARS = 240


def preview_lines(preview: str | None) -> str:
    """Normalise the stored assistant preview for display.

    Collapses runs of blank lines (the stored extract can carry markdown
    paragraph breaks that waste half the visible box on a phone) and caps
    the length on a word boundary so the section stays about three lines.
    Returns "" for anything empty, so the client renders its explicit
    "nothing captured" state rather than an empty box.
    """
    if not preview or not preview.strip():
        return ""
    lines = [line.strip() for line in preview.strip().splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) <= _PREVIEW_MAX_CHARS:
        return text
    clipped = text[:_PREVIEW_MAX_CHARS]
    # Prefer cutting at the last space so the tail is not a half-word.
    cut = clipped.rfind(" ")
    if cut > _PREVIEW_MAX_CHARS // 2:
        clipped = clipped[:cut]
    return clipped.rstrip() + "…"


def display_facts(detail: dict[str, Any]) -> list[dict[str, str]]:
    """The info line as ordered ``{label, value}`` pairs, omitting what
    would be noise.

    A finished session legitimately reports ``model: ""``, ``cost 0`` and
    ``context 0``; rendering those as "0% ctx · $0.00" tells the operator
    something false-looking about a session that simply never recorded
    them. Built here rather than in JavaScript so the omission rules are
    pinned by pytest.
    """
    facts: list[dict[str, str]] = []
    if detail.get("model"):
        facts.append({"label": "Model", "value": str(detail["model"])})
    if detail.get("context_pct"):
        facts.append({"label": "Context", "value": f"{detail['context_pct']}%"})
    if detail.get("cost_usd"):
        facts.append({"label": "Cost", "value": f"${detail['cost_usd']:.2f}"})
    if detail.get("busy_elapsed_seconds"):
        facts.append({
            "label": "Working for",
            "value": _short_duration(detail["busy_elapsed_seconds"]),
        })
    age = detail.get("last_active_seconds_ago")
    if age is not None:
        facts.append({"label": "Last active", "value": _short_duration(age) + " ago"})
    if detail.get("cwd"):
        facts.append({"label": "Directory", "value": str(detail["cwd"])})
    return facts


def _short_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{max(0, seconds)}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def build_timeline(sess: "TrackedSession") -> list[dict[str, Any]]:
    """Interleave ``tool_history`` and ``stream_commentary`` in
    chronological order, uncapped — a scrollable webview has no 32 768
    byte ceiling and no live-batch-holding to honor (design.md
    Decision 3). Naturally bounded by ``TOOL_HISTORY_CAP`` (state.py) —
    no separate cap needed here.

    Each row is ``{"kind": "commentary", "text": str}`` or
    ``{"kind": "tool", "text": str, "state": "done"|"failed"|"running",
    "elapsed_seconds": int|None}``.
    """
    pending: dict[int, list[str]] = defaultdict(list)
    for anchor, text in sess.stream_commentary:
        pending[min(anchor, len(sess.tool_history))].append(text)

    subagent_started = {
        info["history_idx"]: info["started_at"]
        for info in sess.active_subagents.values()
        if info.get("history_idx") is not None
    }

    rows: list[dict[str, Any]] = []
    for idx, (summary, done) in enumerate(sess.tool_history):
        for text in pending.pop(idx, ()):
            rows.append({"kind": "commentary", "text": text})
        state = "failed" if done == "failed" else ("done" if done else "running")
        elapsed = None
        if state == "running" and idx in subagent_started:
            elapsed = round(time.monotonic() - subagent_started[idx])
        rows.append({
            "kind": "tool", "text": summary, "state": state,
            "elapsed_seconds": elapsed,
        })
    for idx in sorted(pending):
        for text in pending[idx]:
            rows.append({"kind": "commentary", "text": text})
    return rows


__all__ = [
    "build_timeline",
    "display_facts",
    "preview_lines",
    "grid_totals",
    "session_detail",
    "session_summary",
    "sort_for_display",
]
