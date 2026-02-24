"""Configuration constants and env vars for aipager."""

import os
from pathlib import Path


def _load_env_file():
    """Load .env file from project root if it exists."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

BOT_TOKEN = os.environ.get("CLAUDE_TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("CLAUDE_TG_CHAT_ID", "")
PROXY = ""
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

SESSION_REGISTRY = "/tmp/claude-remote-sessions.json"
POLL_TIMEOUT = 30  # long-polling seconds
POLL_INTERVAL = 1  # seconds between failed polls

# Callback data format: {session_short_id}:{action}
# session_short_id = first 8 chars of session_id (or tmux session name)
# action = "allow", "deny", "continue", "stop"
