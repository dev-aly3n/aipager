"""Append-only audit log for permission decisions.

Every Allow / Deny / Continue tap and every AskUserQuestion submit
gets one JSONL line here. Useful for post-mortems ("which tool did I
allow at 03:14?") and for any security-conscious user who wants a
local paper trail without trusting Telegram's chat history.

Best-effort: write failures (full disk, perms) log at WARNING and
return silently — never block the UI thread.

Format: one JSON object per line.
``{"ts": "ISO8601", "session": "claude-jim", "label": "jim",
   "action": "Allowed", "tool": "Bash", "summary": "ls /tmp"}``
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
           path: Path | None = None) -> bool:
    """Append a single record. Returns True on success, False on failure.

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
