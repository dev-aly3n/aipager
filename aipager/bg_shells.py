"""Background shells a session's main Claude loop started.

A ``Bash`` call Claude Code moves to the background (``run_in_background``,
Ctrl+B, its timeout, a queued message) reports a ``backgroundTaskId`` in its
PostToolUse ``tool_response`` and keeps running after the tool call ends.
Its end reaches Claude as a ``<task-notification>`` with a ``<status>``, or
not at all when Claude stops it with ``TaskStop``.

Pure text here, shared by the hook receiver, the session state and the busy
card so all three agree on one wording: the row label, the notification
parser, and the live and settled row texts. No I/O, no imports from the bot
package (``state`` imports this module).
"""

from __future__ import annotations

import re

#: Every shell row on the card starts with this, so the finished-card
#: coercion can tell a settled shell row from any other tool row.
SHELL_ROW_PREFIX = "shell: "
#: A label longer than this is cut, with an ellipsis.
LABEL_MAX = 60
#: The ``tool_history`` mark of a shell that was stopped (a ``killed``
#: notification, TaskStop/KillShell) or whose end was never seen.
STOPPED = "stopped"

_NOTIFICATION = "<task-notification>"
_RESULT = "<result>"
_TASK_ID_RE = re.compile(r"<task-id>\s*([A-Za-z0-9_-]+)\s*</task-id>")
_STATUS_RE = re.compile(r"<status>\s*([A-Za-z_]+)\s*</status>")
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_EXIT_RE = re.compile(r"exit code (\d+)")
#: The statuses that end a shell. A Monitor event carries no status at all,
#: so it never matches.
_END_STATUSES = frozenset({"completed", "failed", "killed"})


def duration(seconds: float) -> str:
    """``45s``, ``6m``, ``1h 3m``: how long something ran, in the answer
    line's and the settled rows' words."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def shell_label(tool_input: object) -> str:
    """The shell's name on the card: its ``description``, else the first
    line of its command, whitespace collapsed and cut at LABEL_MAX."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    text = inp.get("description") or ""
    if not isinstance(text, str) or not text.strip():
        command = inp.get("command") or ""
        text = command.strip().split("\n", 1)[0] if isinstance(
            command, str) else ""
    text = " ".join(text.split())
    if not text:
        return "shell"
    if len(text) > LABEL_MAX:
        text = text[:LABEL_MAX - 1].rstrip() + "…"
    return text


def parse_shell_ends(text: str) -> list[tuple[str, str, int | None]]:
    """``(task_id, status, exit_code)`` for every notification in *text*
    that says a task ended (status completed, failed or killed).

    One prompt can carry several notifications, so each is read on its
    own, and only its tags BEFORE any ``<result>``: an agent's result is
    free text and may quote another notification. A block with no status
    (a Monitor event) ends nothing. Whether the id is one of this
    session's shells is the caller's question."""
    ends: list[tuple[str, str, int | None]] = []
    if not text or _NOTIFICATION not in text:
        return ends
    for block in text.split(_NOTIFICATION)[1:]:
        cut = block.find(_RESULT)
        if cut >= 0:
            block = block[:cut]
        task = _TASK_ID_RE.search(block)
        status = _STATUS_RE.search(block)
        if task is None or status is None:
            continue
        state = status.group(1).lower()
        if state not in _END_STATUSES:
            continue
        exit_code = None
        summary = _SUMMARY_RE.search(block)
        if summary is not None:
            code = _EXIT_RE.search(summary.group(1))
            if code is not None:
                exit_code = int(code.group(1))
        ends.append((task.group(1), state, exit_code))
    return ends


def live_row(label: str, elapsed: str) -> str:
    """``shell: <label> (<elapsed>)``: a running shell on a live card."""
    return f"{SHELL_ROW_PREFIX}{label} ({elapsed})"


def final_live_row(label: str) -> str:
    """A running shell on a card settled while it still runs: no counter
    that would sit frozen under the settled status."""
    return f"{SHELL_ROW_PREFIX}{label} (still running)"


def settled_row(label: str, outcome: str, seconds: float,
                exit_code: int | None = None) -> tuple[str, object]:
    """The row text and ``tool_history`` mark of a shell that ended.

    *outcome* is a notification status (``completed``, ``failed``,
    ``killed``), ``stopped`` (TaskStop/KillShell) or ``lost`` (its end was
    never seen within the max age)."""
    dur = duration(seconds)
    if outcome == "completed":
        return f"{SHELL_ROW_PREFIX}{label} - done ({dur})", True
    if outcome == "failed":
        what = f"exit {exit_code}" if exit_code is not None else dur
        return f"{SHELL_ROW_PREFIX}{label} - failed ({what})", "failed"
    if outcome == "lost":
        return f"{SHELL_ROW_PREFIX}{label} - no end seen ({dur})", STOPPED
    return f"{SHELL_ROW_PREFIX}{label} - stopped ({dur})", STOPPED
