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
    return {
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
        "timeline": build_timeline(sess),
    }


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


__all__ = ["build_timeline", "session_detail", "session_summary"]
