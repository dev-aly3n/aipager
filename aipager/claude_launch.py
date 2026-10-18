"""What every Claude Code session aipager launches starts with.

Claude Code 2.1.301+ has a built-in SendUserFile tool that delivers files
to the person; in a plain local session it is offered only when the
process starts with ``CLAUDE_CODE_ATTACHMENTS_BY_PATH`` set (read once, at
start), which also makes it a tool Claude sees from the first turn rather
than one it must load first. aipager sets it on every launch (the daemon's
``inject.launch_session`` and the terminal's ``aipager session``), checks
every file in the PreToolUse hook (``aipager.send_check``) and delivers it
to the session's chat (``aipager.bot.outbox``).

The system prompt every launch appends starts with the session's name and,
for a Claude Code that has the tool (or one whose version is unknown),
tells Claude it can send files. Claude-facing English, pinned in
``tests/test_claude_facing_golden.py``.

Daemon and launcher only.
"""

from __future__ import annotations

import re

#: The switch that gives a plain local session SendUserFile.
ATTACHMENTS_ENV = "CLAUDE_CODE_ATTACHMENTS_BY_PATH"

#: The first Claude Code release with SendUserFile behind that switch.
MIN_SEND_VERSION = (2, 1, 301)

#: What Claude reads about the tool (no em dashes; English only).
SEND_FILES_NOTE = (
    "You can send files and images (screenshots, PDFs, videos, anything) to the "
    "person in Telegram with the SendUserFile tool; if it is not in your tool list, "
    "load it with ToolSearch. Never say you cannot send files."
)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _name_prompt(name: str) -> str:
    return (f'Your session name is "{name}". '
            f'When users address you by this name, respond naturally '
            f'-- it is your name in this session.')


def has_send_tool(claude_version: str | None) -> bool:
    """Whether a Claude Code of *claude_version* offers SendUserFile.
    Unknown or unreadable: assumed yes (the note is harmless there)."""
    m = _VERSION_RE.match(claude_version or "")
    if not m:
        return True
    return tuple(int(g) for g in m.groups()) >= MIN_SEND_VERSION


def base_system_prompt(name: str, claude_version: str | None) -> str:
    """The prompt every launch appends: the session's name, then the send
    note when this Claude Code has the tool."""
    prompt = _name_prompt(name)
    if has_send_tool(claude_version):
        prompt = f"{prompt}\n\n{SEND_FILES_NOTE}"
    return prompt
