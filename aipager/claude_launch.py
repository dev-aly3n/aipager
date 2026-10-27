"""What every Claude Code session aipager launches starts with.

Claude Code 2.1.301+ has a built-in SendUserFile tool that delivers files
to the person; in a plain local session it is offered only when the
process starts with ``CLAUDE_CODE_ATTACHMENTS_BY_PATH`` set (read once, at
start), which also makes it a tool Claude sees from the first turn rather
than one it must load first. aipager sets it on every launch (the daemon's
``inject.launch_session`` and the terminal's ``aipager session``), checks
every file in the PreToolUse hook (``aipager.send_check``) and delivers it
to the session's chat (``aipager.bot.outbox``).

The system prompt every launch appends starts with the session's name,
then a note on how Telegram shows Claude's answers (rich Markdown, native
tables, no drawn ones) that, for a Claude Code that has the tool (or one
whose version is unknown), also tells Claude to send images and files
with it. Claude-facing English, pinned in
``tests/test_claude_facing_golden.py``.

Since 2.1.267 Claude Code records a conversation's system prompt on its first
request and sends that record again on every later request and every
``--resume``, until the conversation is compacted, even when the launch
appends different text. The appended text changes (a new name, a changed
roster, roles or rules, the note after an upgrade), so both launch paths
pass ``--system-prompt-snapshot off``: the prompt is rendered fresh, as it
was before. Its cost is a prompt-cache miss when the text did change.

Daemon and launcher only.
"""

from __future__ import annotations

import re

#: The switch that gives a plain local session SendUserFile.
ATTACHMENTS_ENV = "CLAUDE_CODE_ATTACHMENTS_BY_PATH"

#: The first Claude Code release with SendUserFile behind that switch.
MIN_SEND_VERSION = (2, 1, 301)

#: The first Claude Code release that takes ``--system-prompt-snapshot``
#: (its --help has it in 2.1.259, before the recording that replays an old
#: prompt began; an unknown flag stops a launch).
MIN_SNAPSHOT_FLAG_VERSION = (2, 1, 259)

#: The flag and value that render the system prompt fresh on every request.
SNAPSHOT_OFF_ARGS = ("--system-prompt-snapshot", "off")

#: What Claude reads about Telegram, for a Claude Code that has SendUserFile
#: (no em dashes; English only; never translated).
TELEGRAM_NOTE = (
    "Your answers are shown in Telegram as rich text. Markdown renders (headings, "
    "lists, links, code blocks). When a table helps, use a Markdown table, or an "
    "HTML <table> for a caption or merged cells: Telegram draws it natively and it "
    "scrolls sideways. Never draw tables or diagrams with box characters or spaces "
    "in a code block; Mermaid is not drawn. Images in Markdown are not shown: send "
    "images and files (screenshots, PDFs, anything) with the SendUserFile tool; if "
    "it is not in your tool list, load it with ToolSearch, and never say you cannot "
    "send files. A table over 20 columns or a few hundred rows goes as a CSV file "
    "instead. Side details can fold in <details><summary>\u2026</summary>\u2026</details>."
)

#: The same note for a Claude Code without SendUserFile: no sentence about
#: the tool, and a big table is split instead of sent as a file.
TELEGRAM_NOTE_NO_SEND = (
    "Your answers are shown in Telegram as rich text. Markdown renders (headings, "
    "lists, links, code blocks). When a table helps, use a Markdown table, or an "
    "HTML <table> for a caption or merged cells: Telegram draws it natively and it "
    "scrolls sideways. Never draw tables or diagrams with box characters or spaces "
    "in a code block; Mermaid is not drawn. A table over 20 columns or a few "
    "hundred rows: split it. Side details can fold in "
    "<details><summary>\u2026</summary>\u2026</details>."
)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _name_prompt(name: str) -> str:
    return (f'Your session name is "{name}". '
            f'When users address you by this name, respond naturally '
            f'-- it is your name in this session.')


def has_send_tool(claude_version: str | None) -> bool:
    """Whether a Claude Code of *claude_version* offers SendUserFile.
    Unknown or unreadable: assumed yes (the send sentence is harmless there)."""
    m = _VERSION_RE.match(claude_version or "")
    if not m:
        return True
    return tuple(int(g) for g in m.groups()) >= MIN_SEND_VERSION


def snapshot_off_args(claude_version: str | None) -> tuple[str, ...]:
    """The arguments that keep a Claude Code of *claude_version* from
    replaying an old system prompt: :data:`SNAPSHOT_OFF_ARGS` when the
    version is known and takes the flag, else none (unknown or unreadable:
    an older Claude Code would refuse the flag and the session would not
    start)."""
    m = _VERSION_RE.match(claude_version or "")
    if not m or tuple(int(g) for g in m.groups()) < MIN_SNAPSHOT_FLAG_VERSION:
        return ()
    return SNAPSHOT_OFF_ARGS


def telegram_note(claude_version: str | None) -> str:
    """The Telegram note for a Claude Code of *claude_version*: the one
    that sends files with SendUserFile when it has the tool."""
    return TELEGRAM_NOTE if has_send_tool(claude_version) else TELEGRAM_NOTE_NO_SEND


def base_system_prompt(name: str, claude_version: str | None) -> str:
    """The prompt every launch appends: the session's name, then the
    Telegram note (:func:`telegram_note`)."""
    return f"{_name_prompt(name)}\n\n{telegram_note(claude_version)}"


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
