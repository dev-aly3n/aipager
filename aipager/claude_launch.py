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


# -- why a session ended (the exit record) -----------------------------------
# Both launch paths run claude under a shell that writes claude's exit code
# and its start and end times (wall clock, seconds) to a per-session exit
# file once claude exits: ``{"code": N, "start": S, "end": E}``. The monitor
# reads it when a session's socket vanishes (aipager/session_monitor.py),
# so the notice can say why. claude is never the shell's last command, so
# the shell cannot exec it away; aipager's kill (SIGTERM to the dtach
# master, a pty hang-up) still ends claude, and a shell that dies with it
# writes nothing, which reads as today's notice.
#
# The fragments are built from space-free tokens, so no English-looking
# literal joins the i18n ratchet. The record is written to ``<file>.tmp``
# with mode 0600 under noclobber (a planted symlink there makes the write
# fail rather than follow it) and moved into place.

def _sh(*tokens: str) -> str:
    return " ".join(tokens)


def exit_record(path_var: str) -> tuple[str, str]:
    """``(prefix, suffix)``: the shell around claude that records its exit
    in the file named by the shell variable *path_var*. The prefix removes
    an earlier launch's record and stamps the start; the suffix, run right
    after claude, keeps claude's status in ``rc``, writes the record and
    ends the shell with that status."""
    p = f'"${path_var}"'
    tmp = f'"${path_var}.tmp"'
    prefix = _sh("rm", "-f", "--", p + ";", "s=$(date", "+%s);")
    suffix = _sh(
        "rc=$?;", "rm", "-f", "--", tmp + ";", "umask", "077;", "set", "-C;",
        "printf", "'{\"code\":%d,\"start\":%d,\"end\":%d}\\n'",
        '"$rc"', '"${s:-0}"', '"$(date', "+%s)\"",
        "2>/dev/null", ">" + tmp, "&&", "mv", "-f", "--", tmp, p + ";",
        "exit", "$rc")
    return prefix, suffix


def exit_wrapper_script() -> str:
    """The ``sh -c`` script the terminal's ``aipager session`` runs under
    dtach: ``sh -c SCRIPT sh <exit file> <command...>``. The command stays
    positional (``"$@"``), so no argument is ever read by a shell again."""
    prefix, suffix = exit_record("p")
    return _sh("p=$1;", "shift;", prefix, '"$@";', suffix)
