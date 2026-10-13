"""Which background agent started which.

Pure functions over Claude Code's hook payloads: nothing here sends,
renders, reads a file or reads a clock. Standard library only: ``state.py`` imports
this module, so it is in the ``aipager-hook`` import closure, which must
never load ``aipager.i18n`` (tests/test_i18n_hook_isolation.py).
"""

from __future__ import annotations

import re

#: The shape of a Claude Code agent id (and of a task id in a
#: ``<task-notification>``).
AGENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: The tools Claude launches an agent with (``Task`` is the older name).
_AGENT_TOOLS = ("Agent", "Task")

# ── the task notification's still-running note (roadmap 8.41) ──────────────
#
# A <task-notification> for an agent carries a <note>; this wording
# (Claude Code 2.1.280) marks an agent that stopped while background work of
# its own still runs, and so will resume and notify again.
_TASK_NOTIFICATION_PREFIX = "<task-notification>"
_TASK_ID_RE = re.compile(r"<task-id>\s*([A-Za-z0-9_-]+)\s*</task-id>")
_NOTE_RE = re.compile(r"<note>(.*?)</note>", re.DOTALL)
_STILL_RUNNING_NOTE = "background work of its own still running"


def still_running_task_ids(text: str) -> list[str]:
    """The task ids of the notifications in *text* whose agent stopped
    with background work still running. One text can carry several
    notifications (a background shell's, which has no note, then an
    agent's), so each is read on its own. Only a notification's own <note>
    counts: the agent's <result> after it is free text and may quote
    anything."""
    if not isinstance(text, str):
        return []
    ids = []
    for block in text.split(_TASK_NOTIFICATION_PREFIX)[1:]:
        note = _NOTE_RE.search(block)
        if note is None or _STILL_RUNNING_NOTE not in note.group(1):
            continue
        match = _TASK_ID_RE.search(block[:note.start()])
        if match:
            ids.append(match.group(1))
    return ids


# ── the parent link ─────────────────────────────────────────────────────────

def _is_agent_id(value: object) -> bool:
    return isinstance(value, str) and AGENT_ID_RE.fullmatch(value) is not None


def launched_child(msg: object) -> str:
    """The id of the agent a ``PostToolUse`` payload says was launched
    from INSIDE another agent, or "".

    Claude Code 2.1.296: every hook fired inside a subagent carries that
    agent's ``agent_id``, and the ``Agent`` tool's ``tool_response`` is a
    dict whose ``agentId`` is the launched agent (``{isAsync, status:
    "async_launched", agentId, ...}``). Its ``status`` is not checked: a
    synchronous child has already stopped by then, so its link changes
    nothing. Anything else (the main loop's own call, another tool, a
    response that is not a dict, an id of the wrong shape, a self link)
    links nothing."""
    if not isinstance(msg, dict):
        return ""
    parent = msg.get("agent_id")
    if not _is_agent_id(parent):
        return ""
    if msg.get("tool_name") not in _AGENT_TOOLS:
        return ""
    response = msg.get("tool_response")
    if not isinstance(response, dict):
        return ""
    child = response.get("agentId")
    if not _is_agent_id(child) or child == parent:
        return ""
    return child
