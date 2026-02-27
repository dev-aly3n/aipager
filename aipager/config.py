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


def _parse_observer_bots(raw: str) -> list[tuple[str, str]]:
    """Parse 'token1:chatid1,token2:chatid2' into [(token, chatid), ...].

    Uses rsplit(":", 1) to split at the LAST colon, since bot tokens
    contain an internal colon (format NNNNN:XXXXXX).
    """
    if not raw.strip():
        return []
    result = []
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        parts = entry.rsplit(":", 1)
        if len(parts) != 2:
            continue
        token, chat_id = parts[0].strip(), parts[1].strip()
        if token and chat_id:
            result.append((token, chat_id))
    return result


OBSERVER_BOTS: list[tuple[str, str]] = _parse_observer_bots(
    os.environ.get("OBSERVER_BOTS", "")
)

# Unix datagram socket for hook → daemon communication
SOCKET_PATH: str = "/tmp/claude-remote.sock"

# Pane monitor interval (seconds)
PANE_POLL_INTERVAL: float = 2.0

# Use transcript JSONL for rich markdown→HTML summaries in Telegram notifications.
# When False, uses pane-scraped plain text in expandable blockquotes (old behavior).
RICH_SUMMARIES: bool = os.environ.get("CLAUDE_RICH_SUMMARIES", "1") not in ("0", "false", "no")

# Session state persistence (survives daemon restarts)
SESSION_STATE_FILE = Path.home() / ".claude" / "claude-remote-sessions.json"

# Minimum seconds between busy-message edits (rate-limit for Telegram API)
BUSY_EDIT_INTERVAL: float = 3.0

# Spinner verbs for animated busy messages (curated from Claude Code's terminal spinner)
SPINNER_VERBS: list[str] = [
    "Thinking", "Reasoning", "Pondering", "Considering", "Analyzing",
    "Processing", "Synthesizing", "Deliberating", "Evaluating", "Mulling",
    "Contemplating", "Inferring", "Cogitating", "Puzzling", "Calculating",
    "Deciphering", "Formulating", "Examining", "Investigating", "Brewing",
    "Cooking", "Crafting", "Forging", "Conjuring", "Noodling",
    "Percolating", "Simmering", "Ruminating", "Musing", "Tinkering",
]

# Quick template buttons for Telegram persistent keyboard
TEMPLATES_BUTTON = "Templates"
BACK_BUTTON = "\u00ab Back"
QUICK_TEMPLATES: list[tuple[str, str]] = [
    ("Continue", "Continue"),
    ("Run tests", "Run the tests"),
    ("Commit", "Commit the changes with a descriptive message"),
    ("/compact", "/compact"),
    ("LGTM ship it", "LGTM, ship it"),
    ("Show diff", "Show me the git diff of all changes"),
]

# Claude Code slash commands — instant commands (no BUSY transition)
# Only commands that CHANGE BEHAVIOR belong here. Commands that just
# display info in the terminal (cost, context, stats, doctor) are useless
# remotely since the user can't see the terminal output in Telegram.
COMMANDS_BUTTON = "Commands"
QUICK_COMMANDS: list[tuple[str, str]] = [
    ("Compact", "/compact"),
    ("Clear", "/clear"),
    ("Plan mode", "/plan"),
]

# Model submenu — accessible from Commands → Model
MODELS_BUTTON = "Model \u203a"
MODEL_CHOICES: list[tuple[str, str]] = [
    ("Sonnet", "/model sonnet"),
    ("Opus", "/model opus"),
    ("Haiku", "/model haiku"),
    ("OpusPlan", "/model opusplan"),
]

# Parent level for each keyboard level (for context-aware Back button)
KEYBOARD_PARENTS: dict[str, str] = {
    "templates": "main",
    "commands": "main",
    "models": "commands",
}
