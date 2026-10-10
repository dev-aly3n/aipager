"""A safety block shown in the chat's language (multilingual phase 3a).

The hook denies a tool call with an English reason (``aipager.safety``'s
writers, or enforce's own two) that Claude reads exactly as written, and
sends the daemon that same sentence. Here, in the daemon only, the
sentence is read back into a :class:`Reason` (a code and its parameters)
through the shapes safety.py keeps beside each writer, and shown through a
key in the chat's language. Tool names, paths and globs are shown as they
are. A sentence aipager does not recognise (a newer or older hook, an
operator's own wording) is shown exactly as received.

Nothing here decides what Claude reads, what the audit records or whether
a tool runs: those keep the English. Never imported by safety.py, enforce
or any module the hook loads (``tests/test_i18n_hook_isolation.py``).
"""

from __future__ import annotations

import html
import re
from typing import NamedTuple

from aipager import safety
from aipager.i18n import Command, Msg, Safe

#: enforce's sticky reason: a tool call after a block in the same turn.
HALTED = safety.REASON_HALTED
#: enforce's reason when the check itself could not run.
UNCHECKED = safety.REASON_UNCHECKED


class Reason(NamedTuple):
    """A deny reason read back: its code and the values it named."""

    code: str
    params: dict


#: The key for each code; its English is exactly the writer's sentence.
REASON_MSGS: dict[str, Msg] = {
    safety.REASON_BASH: Msg(
        "safety.reason.bash", "Bash command blocked by safety policy",
        note="Why a shell command was blocked (the rule itself is not shown)"),
    safety.REASON_BASH_SEARCH: Msg(
        "safety.reason.bash_search",
        "Bash command blocked by safety policy: a recursive search or listing of the "
        "whole home folder or / is not allowed; search inside a project folder instead",
        note="A shell command that searched or listed the whole home folder or /"),
    safety.REASON_TOOL_NOT_ALLOWED: Msg(
        "safety.reason.tool_not_allowed", "{tool} not in this role's allow_tools",
        note="The tool is not on the role's allow list; allow_tools is a setting's "
             "name and stays as it is", eg={"tool": "WebSearch"}),
    safety.REASON_TOOL_DENIED: Msg(
        "safety.reason.tool_denied", "{tool} is in this role's deny_tools",
        note="The tool is on the role's deny list; deny_tools is a setting's name and "
             "stays as it is", eg={"tool": "Bash"}),
    safety.REASON_PATH_UNREADABLE: Msg(
        "safety.reason.path_unreadable", "{tool} with an unreadable path",
        note="The tool call's path could not be read, so it was refused", eg={"tool": "Write"}),
    safety.REASON_PATH_PROTECTED: Msg(
        "safety.reason.path_protected", "{tool} on protected path {glob}",
        note="The tool touched a path aipager protects; {glob} is the rule's path",
        eg={"tool": "Write", "glob": "~/.ssh/**"}),
    safety.REASON_PATH_PROTECTED_WRITE: Msg(
        "safety.reason.path_protected_write", "{tool} write to protected path {glob}",
        note="The tool wrote to a path that may only be read; {glob} is the rule's path",
        eg={"tool": "Write", "glob": "~/.ssh/**"}),
    safety.REASON_RUNS_COMMANDS: Msg(
        "safety.reason.runs_commands",
        "{tool} to a file that runs commands (.claude/, .git/, .mcp.json)",
        note="A write to a file that makes Claude Code or git run commands; the names "
             "in brackets stay as they are", eg={"tool": "Write"}),
    safety.REASON_WIDE_CWD_WRITE: Msg(
        "safety.reason.wide_cwd_write",
        "{tool} denied - this session runs in the home folder (or a folder above it), "
        "and this role can only write inside a project folder",
        note="A restricted role's write while the session runs in the home folder",
        eg={"tool": "Write"}),
    safety.REASON_WIDE_CWD_SEARCH: Msg(
        "safety.reason.wide_cwd_search",
        "{tool} denied - this session runs in the home folder (or a folder above it), "
        "and this role can only search inside a project folder",
        note="A restricted role's search while the session runs in the home folder",
        eg={"tool": "Grep"}),
    safety.REASON_OUTSIDE_WRITE: Msg(
        "safety.reason.outside_write",
        "{tool} outside the session's folder - a restricted turn writes only in its "
        "project and scratchpad",
        note="A restricted turn wrote outside the session's folder", eg={"tool": "Write"}),
    safety.REASON_OUTSIDE_SEARCH: Msg(
        "safety.reason.outside_search",
        "{tool} outside the session's folder - a restricted turn searches only its "
        "project and scratchpad",
        note="A restricted turn searched outside the session's folder",
        eg={"tool": "Grep"}),
    safety.REASON_SEARCH_FOLDER_UNREADABLE: Msg(
        "safety.reason.search_folder_unreadable",
        "{tool} with an unreadable search folder",
        note="The search's folder could not be read, so it was refused",
        eg={"tool": "Grep"}),
    safety.REASON_PATTERN_UNREADABLE: Msg(
        "safety.reason.pattern_unreadable", "{tool} with an unreadable pattern",
        note="The search's pattern could not be read, so it was refused",
        eg={"tool": "Glob"}),
    safety.REASON_GLOB_UNREADABLE: Msg(
        "safety.reason.glob_unreadable", "{tool} with an unreadable glob",
        note="The search's file filter (a glob) could not be read", eg={"tool": "Grep"}),
    safety.REASON_GLOB_ESCAPED: Msg(
        "safety.reason.glob_escaped", "{tool} with an escaped glob",
        note="The search's file filter used a backslash escape", eg={"tool": "Grep"}),
    safety.REASON_GLOB_NOT_PLAIN: Msg(
        "safety.reason.glob_not_plain",
        "{tool} with a glob that is not a plain relative pattern",
        note="The search's file filter was absolute or special", eg={"tool": "Grep"}),
    safety.REASON_GLOB_CLIMBS: Msg(
        "safety.reason.glob_climbs", "{tool} with a glob that climbs out with ..",
        note="The search's file filter used .. to leave the folder; .. stays as it is",
        eg={"tool": "Grep"}),
    safety.REASON_SEARCH_COULD_REACH: Msg(
        "safety.reason.search_could_reach", "{tool} search could reach protected path {glob}",
        note="A search that could find a protected path; {glob} is the rule's path",
        eg={"tool": "Grep", "glob": "**/.env"}),
    safety.REASON_SEARCH_REACHES: Msg(
        "safety.reason.search_reaches", "{tool} search reaches protected path {glob}",
        note="A search that reaches a protected path; {glob} is the rule's path",
        eg={"tool": "Grep", "glob": "~/.ssh/**"}),
    safety.REASON_SEARCH_ANYWHERE: Msg(
        "safety.reason.search_anywhere",
        "{tool} denied - the protected path {glob} can be in any folder, so this role "
        "cannot search with Grep or Glob",
        note="A protected path that can be anywhere, so no search is safe",
        eg={"tool": "Grep", "glob": "**/.env"}),
    safety.REASON_SEARCH_PROTECTED_FILES: Msg(
        "safety.reason.search_protected_files",
        "{tool} denied - this search would reach protected files ({shown}); search inside "
        "a project folder instead",
        note="A search that would read protected files; {shown} is their folder",
        eg={"tool": "Grep", "shown": "~/.config/aipager"}),
    HALTED: Msg(
        "safety.reason.halted",
        "session halted - a prior tool call this turn was blocked by safety policy; "
        "start a new request",
        note="Every tool call after a block in the same turn is refused too"),
    UNCHECKED: Msg(
        "safety.reason.unchecked", "this tool call could not be checked, so it was denied",
        note="The safety check itself failed, so the call was refused"),
}

NOTICE = Msg("safety.notice",
             "🛑 <b>{session}</b> · Blocked by safety policy: {reason} (stopped)",
             note="A tool call was blocked and the session's turn stopped; {reason} is "
                  "why", eg={"session": "x1", "reason": "Write on protected path ~/.ssh/**"})

#: safety.py's shapes, in its order (enforce's two first).
_SHAPES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (code, re.compile(shape, re.S)) for code, shape in safety.REASON_SHAPES)

#: A reason shown as received, for each output kind.
_AS_RECEIVED = {"html": html.escape, "text": str}


def parse_reason(reason) -> Reason | None:
    """*reason* read back into its code and parameters, or None for a
    sentence aipager did not write."""
    if not isinstance(reason, str):
        return None
    for code, shape in _SHAPES:
        match = shape.match(reason)
        if match:
            return Reason(code, match.groupdict())
    return None


def is_sticky(reason) -> bool:
    """enforce's repeat refusal after a block in the same turn: audited,
    never a second halt or notice."""
    parsed = parse_reason(reason)
    return parsed is not None and parsed.code == HALTED


def reason_text(tr, reason: str, kind: str = "html") -> str:
    """*reason* in *tr*'s language (``kind`` "html" or "text"): the key of
    its code, with the tool name a whole value and paths as they are. In
    English, and for a sentence aipager did not write, the reason exactly
    as received (HTML-escaped for "html")."""
    parsed = parse_reason(reason) if tr.code != "en" else None
    if parsed is None:
        return _AS_RECEIVED[kind](str(reason))
    params = {name: Command(value) if name == "tool" else value
              for name, value in parsed.params.items()}
    return getattr(tr, kind)(REASON_MSGS[parsed.code], **params)


def notice_html(tr, label: str, reason: str) -> str:
    """`🛑 x1 · Blocked by safety policy: <why> (stopped)` in *tr*'s
    language (HTML). The reason as received when there is no key for it
    (and in English, where the key would say the same): a plain value, so
    it is escaped and, in a right-to-left language, isolated like any
    other."""
    if tr.code == "en" or parse_reason(reason) is None:
        return tr.html(NOTICE, session=label, reason=str(reason))
    return tr.html(NOTICE, session=label, reason=Safe(reason_text(tr, reason)))
