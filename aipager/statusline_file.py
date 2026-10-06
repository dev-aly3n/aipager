"""The one reader of Claude Code's per-session status-line file.

Claude Code pipes its status-line JSON to ``aipager-statusline`` on every
update, which writes it verbatim to ``/tmp/claude-status-<session>.json``
(``dtach/statusline_notify.py``) and then sends the daemon a ``statusline``
datagram. The daemon's in-memory copy of those numbers
(``TrackedSession.last_token_pct`` / ``last_cost_usd``) is not persisted
and is partly reset at every turn's start, so anything that shows a
session's latest context/cost reads the file, the same source the terminal
uses. Every reader in the daemon and the CLI goes through here so they
cannot disagree about how the file is parsed.

``STATUS_DIR`` is read at call time so tests can point it at a tmp dir.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aipager import instance
from aipager.state import Status

if TYPE_CHECKING:
    from aipager.state import TrackedSession

#: ``/tmp``, or the instance folder (:func:`aipager.instance.runtime_tmp_dir`).
#: The writer, ``dtach/statusline_notify.py``, inlines the same rule.
STATUS_DIR = instance.runtime_tmp_dir()


def status_file_path(session_name: str) -> Path:
    return Path(STATUS_DIR) / f"claude-status-{session_name}.json"


def read_raw(session_name: str) -> dict[str, Any] | None:
    """The file's parsed JSON object, or ``None`` when it is missing,
    unreadable, not JSON, or not a JSON object."""
    try:
        data = json.loads(status_file_path(session_name).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _num(value: Any) -> float | None:
    """A real number, or ``None`` (bools, strings, nulls and NaN are not)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    return float(value)


def _obj(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class StatusLine:
    """The fields aipager shows, parsed defensively from one file read."""

    context_pct: int
    cost_usd: float
    model: str
    total_input: int
    total_output: int
    mtime: float  # the file's modification time (wall clock)


def parse(data: dict[str, Any], mtime: float = 0.0) -> StatusLine:
    ctx = _obj(data, "context_window")
    used = _num(ctx.get("used_percentage"))
    remaining = _num(ctx.get("remaining_percentage"))
    if used is not None:
        context_pct = round(used)
    elif remaining is not None:
        context_pct = round(100 - remaining)
    else:
        context_pct = 0
    cost = _num(_obj(data, "cost").get("total_cost_usd"))
    model = _obj(data, "model").get("display_name")
    return StatusLine(
        context_pct=context_pct,
        cost_usd=cost if cost is not None else 0.0,
        model=model if isinstance(model, str) else "",
        total_input=int(_num(ctx.get("total_input_tokens")) or 0),
        total_output=int(_num(ctx.get("total_output_tokens")) or 0),
        mtime=mtime,
    )


def read_status_line(session_name: str) -> StatusLine | None:
    """Parse the session's status-line file, or ``None`` when there is
    no usable file (missing, unreadable, malformed)."""
    path = status_file_path(session_name)
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    data = read_raw(session_name)
    if data is None:
        return None
    return parse(data, mtime)


def latest_stats(sess: "TrackedSession") -> dict[str, Any]:
    """A session's latest known context %, cost and model, for display.

    A live session reads its status-line file once: the file wins unless
    the daemon applied a status-line update after the file was last
    written (then the in-memory numbers are the newer ones), and the
    in-memory numbers stand in when there is no usable file. So a daemon
    restart (nothing in memory yet) and a turn's start (which zeroes
    ``last_token_pct`` for the busy card) both show the real numbers.

    A GONE session is not read: it keeps showing what the daemon itself
    saw, so a long-ended session never surfaces a leftover file.
    """
    ctx = sess.last_token_pct or 0
    cost = sess.last_cost_usd or 0.0
    model = sess.model_name or ""
    if sess.status == Status.GONE:
        return {"context_pct": ctx, "cost_usd": round(cost, 4), "model": model}
    sl = read_status_line(sess.name)
    if sl is not None and sl.mtime >= sess.statusline_at:
        ctx, cost = sl.context_pct, sl.cost_usd
    elif sess.statusline_at:
        ctx, cost = sess.statusline_ctx_pct, sess.last_cost_usd or 0.0
    return {
        "context_pct": ctx,
        "cost_usd": round(cost, 4),
        "model": model or (sl.model if sl is not None else ""),
    }
