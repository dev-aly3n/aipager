"""Configuration for aipager — loads from .env at project root."""

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

BOT_TOKEN: str = os.environ.get("CLAUDE_TG_BOT_TOKEN", "")
CHAT_ID: str = os.environ.get("CLAUDE_TG_CHAT_ID", "")
PROXY: str = ""

# Unix datagram socket for hook → daemon communication
SOCKET_PATH: str = "/tmp/claude-remote.sock"

# Pane monitor interval (seconds)
PANE_POLL_INTERVAL: float = 2.0

# Use transcript JSONL for rich markdown→HTML summaries in Telegram notifications.
# When False, uses pane-scraped plain text in expandable blockquotes (old behavior).
RICH_SUMMARIES: bool = os.environ.get("CLAUDE_RICH_SUMMARIES", "1") not in ("0", "false", "no")
