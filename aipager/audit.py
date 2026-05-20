"""Append-only audit log for permission decisions.

Every Allow / Deny / Continue tap and every AskUserQuestion submit
gets one JSONL line here. Useful for post-mortems ("which tool did I
allow at 03:14?") and for any security-conscious user who wants a
local paper trail without trusting Telegram's chat history.

In team mode (see :mod:`aipager.team`) every record also carries the
Telegram identity of the user who took the action — ``user_id``,
``username`` (Telegram @handle, may be empty), and ``display_name``
(first+last name from Telegram). Personal-mode records leave those
fields as ``None`` / empty strings, since there's only one user.

Best-effort: write failures (full disk, perms) log at WARNING and
return silently — never block the UI thread.

Format: one JSON object per line.
``{"ts": "ISO8601", "session": "claude-jim", "label": "jim",
   "action": "Allowed", "tool": "Bash", "summary": "ls /tmp",
   "user_id": 12345, "username": "alice", "display_name": "Alice Smith"}``
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

AUDIT_LOG_PATH = Path.home() / ".claude" / "aipager-audit.jsonl"


def append(*, session: str, label: str, action: str,
           tool: str = "", summary: str = "",
           user_id: int | None = None,
           username: str = "",
           display_name: str = "",
           scope_label: str = "",
           scope_chat_id: int | None = None,
           denied: bool = False,
           reason: str = "",
           bypass_safety: bool = False,
           path: Path | None = None) -> bool:
    """Append a single record. Returns True on success, False on failure.

    ``user_id`` / ``username`` / ``display_name`` identify the Telegram
    user who took the action — populated in team mode, left empty in
    personal mode.

    Multi-scope (Phase H) attribution: ``scope_label`` / ``scope_chat_id``
    say *in which scope* the action happened; ``denied`` (+ ``reason``)
    records authorization/safety rejections; ``bypass_safety`` flags an
    owner acting with the safety boundary bypassed. All default-empty, so
    legacy/personal records keep their original shape.

    ``path`` is overrideable for tests; production callers leave it as
    None so it picks up ``AUDIT_LOG_PATH``.
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": session,
        "label": label,
        "action": action,
        "tool": tool,
        "summary": summary[:500],  # cap to keep the log readable
        "user_id": user_id,
        "username": username,
        "display_name": display_name,
        "scope_label": scope_label,
        "scope_chat_id": scope_chat_id,
        "denied": denied,
        "reason": reason[:200],
        "bypass_safety": bypass_safety,
    }
    target = path or AUDIT_LOG_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        log.warning("audit append failed: %s", e)
        return False
