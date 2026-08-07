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

# ---- v2 multi-scope config ----
#
# ``aipager.yaml`` (the "who") + ``policy.yaml`` (the "what") are the
# v2 config surface, authoritative when present. The bot loads scopes
# fresh at init (see bot/core.py) — these module-level values are the
# import-time snapshot used by preflight/status/state-backfill.
#
# A malformed v2 file is recorded in ``CONFIG_ERROR`` rather than raised
# here. Raising at import time took down every command that imports this
# module, including `aipager doctor` — the one the error message tells
# users to run. Diagnostics must survive a broken config to be able to
# report it.
#
# This does NOT soften the daemon: ``bot/core.py`` re-loads scopes and
# policy itself and still raises, and ``preflight.require_config``
# refuses to start when ``CONFIG_ERROR`` is set. Both matter, because
# ``SCOPES = None`` means "legacy personal mode", under which
# ``auth._is_admin`` returns True for everyone — degrading to it on a
# parse error would be a fail-open. ``TEAM`` above is deliberately still
# loaded eagerly-and-loudly for the same reason: ``bot/core.py`` reads
# the snapshot rather than reloading, so it has no second line of
# defence.
from aipager.scope import ScopeConfigError as _ScopeConfigError  # noqa: E402
from aipager.scope import load_scopes as _load_scopes  # noqa: E402
from aipager.policy import PolicyError as _PolicyError  # noqa: E402
from aipager.policy import load_policy as _load_policy  # noqa: E402

CONFIG_ERROR: str | None = None

try:
    _v2 = _load_scopes()
except _ScopeConfigError as e:
    CONFIG_ERROR = str(e)
    _v2 = None
SCOPES = _v2[0] if _v2 else None

try:
    POLICY = _load_policy()
except _PolicyError as e:
    if CONFIG_ERROR is None:
        CONFIG_ERROR = str(e)
    # Built-in defaults = the restrictive safety floor. `load_policy`
    # documents "missing files → built-in defaults only", so pointing it
    # at a path that cannot exist yields that floor without the
    # unparseable layer.
    POLICY = _load_policy(Path("/nonexistent/aipager"),
                          Path("/nonexistent/aipager.d"))

if _v2 and _v2[1]:
    # v2 is authoritative for the bot token when aipager.yaml is present
    # — this is what lets config.env be retired (Phase C).
    BOT_TOKEN = _v2[1]
del _load_scopes, _load_policy, _v2, _ScopeConfigError, _PolicyError

from aipager.scope import load_default_mode as _load_dm  # noqa: E402
DEFAULT_MODE: str = _load_dm()
del _load_dm


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


# NOTE (multi-scope / R10): observer bots are a GLOBAL firehose — they
# receive a copy of events from EVERY scope, with no per-scope filtering.
# Treat an observer chat as trusted to see all scopes' activity. Per-scope
# observer routing is a documented non-goal (see researches multi-scope §R10).
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

# Seconds between busy-message edits while the turn is producing new content.
# Also the minimum gap between any two busy-message edits (debounce floor).
STREAM_EDIT_INTERVAL: float = float(os.environ.get("STREAM_EDIT_INTERVAL", "0.9"))

# Maximum characters of commentary kept in the card. Whole blocks are dropped
# oldest-first once the budget is spent, so a long turn stays glanceable
# instead of growing into a wall of prose.
STREAM_BODY_CHARS: int = int(os.environ.get("STREAM_BODY_CHARS", "600"))

# Keep the busy card in the chat when the turn ends, re-rendered once as a
# finished timeline, instead of deleting it. The card is the only record of
# which tools ran and what Claude said between them, so throwing it away at
# the moment it becomes readable loses the turn's whole shape. Set to 0 for
# the old behaviour where the card disappears as the answer arrives.
KEEP_FINISHED_CARD: bool = os.environ.get(
    "KEEP_FINISHED_CARD", "1",
) not in ("0", "false", "no")

# Seconds a session can stay BUSY with no hook activity before the bot
# posts an informational "still working" note in chat. Nothing is wrong
# when this fires — a session running a long tool call, generating
# against a big context, or compacting emits no hooks and looks
# identical to a wedged one. The note exists only so a genuinely stuck
# session (exhausted subscription, hung network, crashed claude) is
# discoverable at all, since no Stop / PostToolUse hook will ever
# arrive to reveal it.
#
# Was 120s, which fired constantly on healthy sessions and trained
# users to read it as an error. 600s (10 min) sits above the duration
# of nearly every legitimate quiet stretch, so the note becomes rare
# enough to be worth reading. Override via the env var.
STALE_BUSY_TIMEOUT: float = float(os.environ.get("STALE_BUSY_TIMEOUT", "600"))

# Upper bound on how long a single tool call may run before the stale
# busy detector fires anyway. When a PreToolUse hook has fired without a
# matching PostToolUse, the session is legitimately "quiet" — no hooks
# emit mid-tool — so the STALE_BUSY_TIMEOUT is suppressed. This cap
# guards against a genuinely wedged tool (network hang, subprocess
# spinning) never surfacing. 15 minutes is long enough for real work
# (large git clones, deep WebSearches, multi-minute relay round-trips)
# and short enough that a wedged session still surfaces the same day.
TOOL_INFLIGHT_MAX_SECONDS: float = float(
    os.environ.get("TOOL_INFLIGHT_MAX_SECONDS", "900")
)

# Upper bound on how long a compaction may run before the stale-busy
# detector fires anyway. Between PreCompact and post-compact
# SessionStart, no hooks fire — a large transcript can take multiple
# minutes to compact. Observed ~3 min on a 40 MB transcript in
# production; 30 min gives 10x buffer for larger transcripts / slower
# model days while still surfacing a genuinely wedged compact.
COMPACT_INFLIGHT_MAX_SECONDS: float = float(
    os.environ.get("COMPACT_INFLIGHT_MAX_SECONDS", "1800")
)

# A session's statusLine file being modified within this window counts
# as a liveness heartbeat and suppresses the stale-busy warning. The
# Claude Code statusLine hook fires on many small state changes during
# active work (streaming, tool cycles, etc.), so a fresh mtime is a
# reliable "session is doing something" signal even when no
# aipager-tracked hook (PreToolUse, PreCompact, …) has fired recently.
STATUSLINE_ALIVE_SECONDS: float = float(
    os.environ.get("STATUSLINE_ALIVE_SECONDS", "60")
)

# Upper bound on how long we're willing to `asyncio.sleep` for a single
# Telegram `RetryAfter` (429). Telegram can return retry_after values in
# the multi-hour range for aggressive flood-control — obeying that
# blindly wedges the daemon on one `asyncio.sleep` and blocks every
# subsequent send. When the reported retry_after exceeds this cap we
# log + give up on the message, letting the caller propagate the
# failure and letting the user see a visible signal via reaction.
TELEGRAM_MAX_RETRY_AFTER: float = float(
    os.environ.get("TELEGRAM_MAX_RETRY_AFTER", "90")
)

# When two hook events arrive with identical (session, event_name,
# payload-hash) within this window, drop the second. Belt-and-braces
# against double-wiring scenarios (e.g. a wrapper script whose name
# doesn't match the bootstrap detector, so the standard hook gets
# appended alongside → every event fires twice). 3 s is long enough
# to catch back-to-back duplicates from a single Claude Code stop
# event; short enough that legitimately-identical events (rare, but
# e.g. the same tool called twice in quick succession with the same
# input) mostly aren't collapsed.
HOOK_DEDUP_WINDOW_SECONDS: float = float(
    os.environ.get("HOOK_DEDUP_WINDOW_SECONDS", "3")
)

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
