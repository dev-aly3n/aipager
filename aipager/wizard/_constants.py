"""Interactive setup wizard for `aipager config`.

UX goals:
- Each question is asked once with arrow-key / Y-n prompts via
  :mod:`questionary`. Long-running Telegram API calls are wrapped in
  ``rich.status`` spinners so the terminal never appears frozen.
- Successful steps print a ``✓`` line; failures use the shared
  ``ui.err_block`` rendering.
- Off-TTY (pytest, scripts), questionary falls back gracefully and the
  spinner is suppressed by rich.

Walks the user through:
  1. Bot token + verify via getMe
  2. Chat ID via getUpdates auto-detect (or manual paste) + test send
  3. Dep check (dtach, claude, hook scripts)
  4. Patch ~/.claude/settings.json with hooks + statusLine (back up first)
  5. Write ~/.config/aipager/config.env (0600)

Idempotent — safe to re-run; existing aipager-hook entries are not
duplicated.
"""

from __future__ import annotations

import re
from pathlib import Path

from questionary import Style


CONFIG_DIR = Path.home() / ".config" / "aipager"
CONFIG_ENV = CONFIG_DIR / "config.env"
TEAM_YAML = CONFIG_DIR / "team.yaml"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

HOOK_CMD = "aipager-hook"
STATUSLINE_CMD = "aipager-statusline"
HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PermissionRequest",
    "Notification", "Stop", "SubagentStop", "PreCompact",
)
TOOL_MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}

_TOKEN_RE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,80}")
_CHAT_NOT_FOUND_RE = re.compile(r"chat\s*[\s_-]*not\s*[\s_-]*found", re.I)

# Restrained Inquirer-style: cyan question mark, green checkmark after
# commit, dim instruction/default text. Matches the rest of aipager.
_PROMPT_STYLE = Style([
    ("qmark", "fg:cyan bold"),
    ("question", "bold"),
    ("answer", "fg:cyan bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:cyan"),
    ("instruction", "fg:#888888"),
    ("text", ""),
    ("disabled", "fg:#888888 italic"),
])


# ----- helpers -----

