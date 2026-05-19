"""Configuration for aipager — loads env from XDG path or project root."""

import json
import os
from pathlib import Path

_XDG_CONFIG = Path.home() / ".config" / "aipager" / "config.env"
_PROJECT_DOTENV = Path(__file__).parent.parent / ".env"


def _load_env_file() -> None:
    """Load environment variables.

    Source priority (first existing file wins):
      1. ~/.config/aipager/config.env (XDG, written by `aipager config`)
      2. <project-root>/.env (legacy / development checkouts)
    """
    for candidate in (_XDG_CONFIG, _PROJECT_DOTENV):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = value
            return


_load_env_file()

BOT_TOKEN: str = os.environ.get("CLAUDE_TG_BOT_TOKEN", "")
CHAT_ID: str = os.environ.get("CLAUDE_TG_CHAT_ID", "")

# Optional group / team mode (see ``aipager.team``).
# ``TEAM`` is ``None`` for personal-mode installs (no team.yaml on disk),
# which preserves the existing one-user-one-DM behaviour.
# If team.yaml exists but is malformed, ``load_team`` raises
# ``TeamConfigError`` so the daemon fails loudly on startup rather
# than silently degrading to a less-safe mode.
from aipager.team import load_team as _load_team  # noqa: E402

TEAM = _load_team()
del _load_team


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
SOCKET_PATH: str = "/tmp/aipager.sock"

# Pane monitor interval (seconds)
PANE_POLL_INTERVAL: float = 2.0

# Use transcript JSONL for rich markdown→HTML summaries in Telegram notifications.
# When False, uses pane-scraped plain text in expandable blockquotes (old behavior).
RICH_SUMMARIES: bool = os.environ.get("CLAUDE_RICH_SUMMARIES", "1") not in ("0", "false", "no")

# Session state persistence (survives daemon restarts)
SESSION_STATE_FILE = Path.home() / ".claude" / "aipager-sessions.json"

# Minimum seconds between busy-message edits (rate-limit for Telegram API)
BUSY_EDIT_INTERVAL: float = 3.0

# Stale busy session threshold (seconds) — alert if BUSY with no hooks for this long
# Seconds a session can stay BUSY with no hook activity before the
# bot surfaces a "stuck" alert in chat. The common stuck causes — an
# exhausted Anthropic subscription, a wedged tool call, or a hung
# network request — are otherwise invisible to the user because no
# Stop / PostToolUse hook ever fires. 120s (2 min) catches these
# quickly without false-positiving on legitimate long operations
# (extended thinking, big WebSearch). Override via the env var.
STALE_BUSY_TIMEOUT: float = float(os.environ.get("STALE_BUSY_TIMEOUT", "120"))

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
_DEFAULT_TEMPLATES: list[tuple[str, str]] = [
    ("Continue", "Continue"),
    ("Run tests", "Run the tests"),
    ("Write tests", "Write tests for the changes"),
    ("Commit", "Commit the changes with a descriptive message"),
    ("LGTM ship it", "LGTM, ship it"),
    ("Show diff", "Show me the git diff of all changes"),
    ("Explain plan", "Explain your plan before making changes"),
    ("Update memory", "Update CLAUDE.md with what you learned"),
]

# Claude Code slash commands — instant commands (no BUSY transition)
# Only commands that CHANGE BEHAVIOR belong here. Commands that just
# display info in the terminal (cost, context, stats, doctor) are useless
# remotely since the user can't see the terminal output in Telegram.
COMMANDS_BUTTON = "Commands"
_DEFAULT_COMMANDS: list[tuple[str, str]] = [
    ("Compact", "/compact"),
    ("Clear", "/clear"),
    ("Plan mode", "/plan"),
    ("Init", "/init"),
    ("Security review", "/security-review"),
]

# Model submenu — accessible from Commands → Model
MODELS_BUTTON = "Model \u203a"
_DEFAULT_MODELS: list[tuple[str, str]] = [
    ("Sonnet", "/model sonnet"),
    ("Opus", "/model opus"),
    ("Haiku", "/model haiku"),
    ("OpusPlan", "/model opusplan"),
]

# ---- Customizable keyboard layout (item 4.1) -------------------------
#
# Optional override at ``~/.config/aipager/keyboard.json``. Any missing
# section falls back to the hardcoded defaults above; an unparseable
# file logs a warning and uses defaults. Changes require a daemon
# restart (the bot rebuilds the keyboard on every render but the
# constants are imported once at startup).
#
# Schema:
#   {
#     "templates": [{"label": "Continue", "prompt": "Continue"}, ...],
#     "commands":  [{"label": "Compact",  "send":   "/compact"},  ...],
#     "models":    [{"label": "Sonnet",   "send":   "/model sonnet"}, ...]
#   }

_KEYBOARD_CONFIG_PATH = Path.home() / ".config" / "aipager" / "keyboard.json"


def _coerce_pair_list(items, *, payload_key, fallback):
    """Turn a list-of-dicts spec into ``[(label, payload), ...]`` tuples."""
    out: list[tuple[str, str]] = []
    if isinstance(items, list):
        for entry in items:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            payload = entry.get(payload_key)
            if isinstance(label, str) and isinstance(payload, str):
                out.append((label.strip(), payload))
    return out or fallback


def _load_keyboard_overrides():
    """Return (templates, commands, models) honoring keyboard.json."""
    import logging
    _log = logging.getLogger(__name__)
    if not _KEYBOARD_CONFIG_PATH.exists():
        return _DEFAULT_TEMPLATES, _DEFAULT_COMMANDS, _DEFAULT_MODELS
    try:
        data = json.loads(_KEYBOARD_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("keyboard.json could not be loaded (%s); using defaults", e)
        return _DEFAULT_TEMPLATES, _DEFAULT_COMMANDS, _DEFAULT_MODELS
    if not isinstance(data, dict):
        _log.warning("keyboard.json root must be an object; using defaults")
        return _DEFAULT_TEMPLATES, _DEFAULT_COMMANDS, _DEFAULT_MODELS
    return (
        _coerce_pair_list(data.get("templates", []),
                          payload_key="prompt",
                          fallback=_DEFAULT_TEMPLATES),
        _coerce_pair_list(data.get("commands", []),
                          payload_key="send",
                          fallback=_DEFAULT_COMMANDS),
        _coerce_pair_list(data.get("models", []),
                          payload_key="send",
                          fallback=_DEFAULT_MODELS),
    )


QUICK_TEMPLATES, QUICK_COMMANDS, MODEL_CHOICES = _load_keyboard_overrides()

# Parent level for each keyboard level (for context-aware Back button)
KEYBOARD_PARENTS: dict[str, str] = {
    "templates": "main",
    "commands": "main",
    "models": "commands",
}

# Directory for files downloaded from Telegram (photos, documents)
FILE_DOWNLOAD_DIR = Path("/tmp/aipager-files")
