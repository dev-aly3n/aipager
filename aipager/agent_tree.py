"""Which background agent started which, and what of the running agents a
restart keeps.

Pure functions over Claude Code's hook payloads and aipager's own state
file record: nothing here sends, renders, reads a file or reads a clock
(the clocks are passed in). Standard library only: ``state.py`` imports
this module, so it is in the ``aipager-hook`` import closure, which must
never load ``aipager.i18n`` (tests/test_i18n_hook_isolation.py).
"""

from __future__ import annotations

import math
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


# ── walking the links ───────────────────────────────────────────────────────

#: Joins two agent names where a row names the agent that started another
#: ("pipeline-runner › ship-tester"). Two names and a glyph, not a
#: sentence: never translated.
LINEAGE_SEP = " › "


def nearest_listed(parents: dict, agent_id: str, listed) -> str:
    """The first ancestor of *agent_id* (its parent, then that one's parent,
    and so on) that is in *listed*; "" when none is. *parents* maps a child
    to its parent. Ends on a cycle (an id seen twice)."""
    seen = {agent_id}
    current = parents.get(agent_id)
    while current and current not in seen:
        if current in listed:
            return current
        seen.add(current)
        current = parents.get(current)
    return ""


def top_ancestor(parents: dict, agent_id: str) -> str:
    """The last id on *agent_id*'s chain of parents (the agent at its top),
    or "" when it has no parent. Ends on a cycle (an id seen twice), with
    the last id before it repeated."""
    seen = {agent_id}
    top = ""
    current = parents.get(agent_id)
    while current and current not in seen:
        top = current
        seen.add(current)
        current = parents.get(current)
    return top


# ── the state file record ───────────────────────────────────────────────────

AGENTS_RECORD_VERSION = 1
#: How many entries each list of the record holds at most (the state's
#: ACTIVE_SUBAGENTS_CAP, passed in by the caller).
DEFAULT_RECORD_CAP = 100

# String caps, in characters.
_CAPS = {
    "label": 80, "type": 80, "desc": 200, "tool_arg": 200, "activity": 200,
    "prose": 300, "prose_msg": 100, "tool": 80,
}
_RUNNING_STRINGS = ("desc", "prose", "prose_msg", "tool", "tool_arg")


def _cap(key: str, value: str) -> str:
    return value[:_CAPS[key]]


def encode_agents(running: list[dict], job: list[dict], now_wall: float, *,
                  cap: int = DEFAULT_RECORD_CAP) -> dict | None:
    """The ``"agents"`` record the state file keeps for a session, or None
    when there is nothing to keep. Each entry already carries its
    ``started_wall``; strings are capped here and each list holds at most
    *cap* entries (the first ones)."""
    if not running and not job:
        return None
    out_running = []
    for entry in running[:cap]:
        out_running.append({
            "id": entry["id"],
            "label": _cap("label", entry.get("label") or ""),
            "started_wall": float(entry["started_wall"]),
            "waiting": bool(entry.get("waiting")),
            "parent": entry.get("parent") or "",
            **{k: _cap(k, entry.get(k) or "") for k in _RUNNING_STRINGS},
        })
    out_job = []
    for entry in job[:cap]:
        out_job.append({
            "id": entry["id"],
            "type": _cap("type", entry.get("type") or ""),
            "started_wall": float(entry["started_wall"]),
            "tool_count": int(entry.get("tool_count") or 0),
            "activity": _cap("activity", entry.get("activity") or ""),
        })
    return {"v": AGENTS_RECORD_VERSION, "saved_wall": float(now_wall),
            "running": out_running, "job": out_job}


def _wall(value: object, now_wall: float) -> float | None:
    """A usable wall-clock stamp: a finite number, not in the future."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value) or value <= 0.0 or value > now_wall:
        return None
    return value


def _place(started_wall: float, now_mono: float,
           now_wall: float) -> tuple[float, bool]:
    """This boot's monotonic stamp for a wall-clock start, and whether the
    clock had to restart from *now_mono* (the card's own guard,
    lifecycle._restore_card_clock): on a boot clock younger than the
    agent's age a stamp in the past would be negative."""
    elapsed = now_wall - started_wall
    if 0.0 <= elapsed < now_mono:
        return now_mono - elapsed, False
    return now_mono, True


def _strings(raw: dict, keys: tuple[str, ...]) -> dict | None:
    out = {}
    for key in keys:
        value = raw.get(key, "")
        if not isinstance(value, str):
            return None
        out[key] = _cap(key, value)
    return out


def _running_entry(raw: object, now_mono: float,
                   now_wall: float) -> dict | None:
    if not isinstance(raw, dict) or not _is_agent_id(raw.get("id")):
        return None
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        return None
    started_wall = _wall(raw.get("started_wall"), now_wall)
    if started_wall is None:
        return None
    waiting = raw.get("waiting", False)
    if not isinstance(waiting, bool):
        return None
    parent = raw.get("parent", "")
    if not isinstance(parent, str) or (
            parent and (not _is_agent_id(parent) or parent == raw["id"])):
        return None
    strings = _strings(raw, _RUNNING_STRINGS)
    if strings is None:
        return None
    started, restarted = _place(started_wall, now_mono, now_wall)
    return {"id": raw["id"], "label": _cap("label", label.strip()),
            "started": started, "clock_restarted": restarted,
            "waiting": waiting, "parent": parent, **strings}


def _job_entry(raw: object, now_mono: float, now_wall: float) -> dict | None:
    if not isinstance(raw, dict) or not _is_agent_id(raw.get("id")):
        return None
    agent_type = raw.get("type")
    if not isinstance(agent_type, str) or not agent_type.strip():
        return None
    started_wall = _wall(raw.get("started_wall"), now_wall)
    if started_wall is None:
        return None
    tool_count = raw.get("tool_count", 0)
    if (isinstance(tool_count, bool) or not isinstance(tool_count, int)
            or tool_count < 0):
        return None
    activity = raw.get("activity", "")
    if not isinstance(activity, str):
        return None
    started, restarted = _place(started_wall, now_mono, now_wall)
    return {"id": raw["id"], "type": _cap("type", agent_type.strip()),
            "started": started, "clock_restarted": restarted,
            "tool_count": tool_count,
            "activity": _cap("activity", activity)}


def _entries(raw: object, build, now_mono: float, now_wall: float,
             cap: int) -> list[dict] | None:
    if not isinstance(raw, list):
        return None
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if len(out) >= cap:
            break
        entry = build(item, now_mono, now_wall)
        if entry is None or entry["id"] in seen:
            continue
        seen.add(entry["id"])
        out.append(entry)
    return out


def validate_agents(raw: object, now_mono: float, now_wall: float, *,
                    max_gap: float, cap: int = DEFAULT_RECORD_CAP,
                    ) -> dict | None:
    """The saved ``"agents"`` record, checked and placed on this boot's
    monotonic clock: ``{"running": [...], "job": [...]}``, each entry with
    a monotonic ``started`` and a ``clock_restarted`` flag. None when the
    record as a whole is unusable: not a dict, another version, lists of
    the wrong type, or a ``saved_wall`` that is not within the last
    *max_gap* seconds (agents that ended while the daemon was down that
    long would otherwise read as running). A bad entry is dropped on its
    own; a repeated id keeps its first entry. Never raises."""
    try:
        if not isinstance(raw, dict) or raw.get("v") != AGENTS_RECORD_VERSION:
            return None
        saved_wall = _wall(raw.get("saved_wall"), now_wall)
        if saved_wall is None or not now_wall - saved_wall < max_gap:
            return None
        running = _entries(raw.get("running", []), _running_entry,
                           now_mono, now_wall, cap)
        job = _entries(raw.get("job", []), _job_entry, now_mono, now_wall,
                       cap)
        if running is None or job is None:
            return None
        return {"running": running, "job": job}
    except Exception:  # noqa: BLE001 - a bad record never stops the start
        return None
