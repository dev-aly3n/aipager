"""Session registry — maps session IDs to tmux targets and message IDs.

Registry file: /tmp/claude-remote-sessions.json
Format:
{
    "short_id": {
        "session_id": "full-uuid",
        "tmux_session": "claude-dev",
        "label": "dev",
        "message_ids": {<telegram_message_id>: "permission_prompt"},
        "last_event": "permission_prompt",
        "updated_at": "2026-02-24T12:00:00"
    }
}
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from aipager.config import SESSION_REGISTRY

log = logging.getLogger(__name__)

_registry_path = Path(SESSION_REGISTRY)


def _load() -> dict:
    try:
        return json.loads(_registry_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    _registry_path.write_text(json.dumps(data, indent=2))


def short_id(session_id: str) -> str:
    """First 8 chars of session_id, used as callback_data prefix."""
    return session_id[:8]


def register_session(session_id: str, tmux_session: str, label: str) -> str:
    """Register or update a session. Returns short_id."""
    data = _load()
    sid = short_id(session_id)
    if sid not in data:
        data[sid] = {
            "session_id": session_id,
            "tmux_session": tmux_session,
            "label": label,
            "message_ids": {},
            "last_event": "",
            "updated_at": "",
        }
    data[sid]["tmux_session"] = tmux_session
    data[sid]["label"] = label
    data[sid]["updated_at"] = datetime.now().isoformat()
    _save(data)
    return sid


def record_message(sid: str, message_id: int, event_type: str) -> None:
    """Record a Telegram message_id associated with this session."""
    data = _load()
    if sid in data:
        data[sid]["message_ids"][str(message_id)] = event_type
        data[sid]["last_event"] = event_type
        data[sid]["updated_at"] = datetime.now().isoformat()
        _save(data)


def get_session_by_short_id(sid: str) -> dict | None:
    """Look up session by short_id."""
    data = _load()
    return data.get(sid)


def get_session_by_message_id(message_id: int) -> tuple[str, dict] | None:
    """Find session that owns a given Telegram message_id."""
    data = _load()
    msg_str = str(message_id)
    for sid, session in data.items():
        if msg_str in session.get("message_ids", {}):
            return sid, session
    return None


def remove_message(sid: str, message_id: int) -> None:
    """Remove a message_id from the session (after buttons are consumed)."""
    data = _load()
    if sid in data:
        data[sid]["message_ids"].pop(str(message_id), None)
        _save(data)


def list_sessions() -> dict:
    """Return all registered sessions."""
    return _load()
