"""The checks a file passes before Claude may send it to Telegram.

Claude Code 2.1.301+ gives a session the SendUserFile tool when aipager
launches it with ``CLAUDE_CODE_ATTACHMENTS_BY_PATH=1``. The tool reads each
file it is given and hands it to the person in the session's chat, so the
PreToolUse hook checks every file first, and the daemon checks it again when
it copies it (the control socket can be written by any local process).

Three layers, in this order:

- **The send floor**, for every turn, the owner's and a terminal's
  included: aipager's own config and state, Claude's credentials (all of
  ``~/.claude`` and ``~/.claude.json``), the credential folders of other
  programs, ``/proc``, ``/sys`` and ``/dev``, and secret-shaped names
  (``.env``, keys, keystores). Matched on the path as spelled and as it
  really leads.
- **File checks**: a regular file once symlinks are resolved, one hard
  link, not empty, within Telegram's 50 MB upload limit.
- **The turn's own rules** run in ``enforce`` (the Read rules of the turn,
  and for a member the strict root bound :func:`root_refusals`).

A refusal is a soft deny: Claude reads the English reason below and tells
the person, and the turn goes on. Everything Claude reads here is pinned in
``tests/test_claude_facing_golden.py``.

Part of the hook's import closure: stdlib and :mod:`aipager.safety` only,
never ``aipager.i18n``.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat as _stat
from dataclasses import dataclass

from aipager import safety

SEND_TOOL = "SendUserFile"

#: Telegram's upload limits for a bot (the Bot API's own numbers).
FILE_MAX_BYTES = 50 * 1024 * 1024
PHOTO_MAX_BYTES = 10 * 1024 * 1024
PHOTO_MAX_SIDE_SUM = 10000
PHOTO_MAX_RATIO = 20

#: Paths no turn may send, the owner's included.
SEND_FLOOR: tuple[str, ...] = (
    safety.DENY_PATHS_NO_ACCESS
    + safety.CREDENTIAL_PATHS
    + ("~/.claude.json*", "/proc/**", "/sys/**", "/dev/**")
)

#: Secret-shaped file names (matched case-insensitively on the base name of
#: the path as spelled and as it really leads).
SECRET_NAMES: tuple[str, ...] = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_dsa*", "id_ecdsa*",
    "id_ed25519*", "*.p12", "*.pfx", "*.kdbx", "*.jks", "*.keystore",
    ".netrc", ".pgpass", ".git-credentials", "credentials",
    "credentials.json", "*credentials*.json",
)

CODE_FLOOR = "floor"
CODE_MISSING = "missing"
CODE_UNREADABLE = "unreadable"
CODE_NOT_FILE = "not_file"
CODE_HARD_LINK = "hard_link"
CODE_EMPTY = "empty"
CODE_TOO_BIG = "too_big"
CODE_OUTSIDE_ROOT = "outside_root"
CODE_UNREADABLE_LIST = "unreadable_list"
CODE_TURN_RULES = "turn_rules"
CODE_UNCHECKED = "unchecked"

# Why a file was refused, as Claude reads it: written once, used by the
# writer and its shape.
_WHY_WORDS: dict[str, tuple[str, ...]] = {
    CODE_FLOOR: ("a", "protected", "file:", "aipager's", "settings,", "Claude's",
                 "credentials,", "keys", "and", "secrets", "are", "never", "sent"),
    CODE_MISSING: ("no", "such", "file"),
    CODE_UNREADABLE: ("it", "cannot", "be", "read"),
    CODE_NOT_FILE: ("not", "a", "regular", "file"),
    CODE_HARD_LINK: ("it", "has", "more", "than", "one", "hard", "link"),
    CODE_EMPTY: ("the", "file", "is", "empty"),
    CODE_TOO_BIG: ("larger", "than", "Telegram's", "50", "MB", "limit"),
    CODE_OUTSIDE_ROOT: ("outside", "this", "person's", "own", "folder;", "they",
                        "may", "send", "only", "files", "inside", "it"),
}
_HEAD_WORDS = (SEND_TOOL, "refused,", "nothing", "was", "sent:")
_TAIL_WORDS = ("Tell", "the", "person", "which", "file", "could", "not", "be",
               "sent", "and", "why.")
_UNREADABLE_LIST_WORDS = (SEND_TOOL, "with", "no", "readable", "file", "list,",
                          "nothing", "was", "sent.", "Tell", "the", "person",
                          "the", "files", "could", "not", "be", "sent.")
_UNCHECKED_WORDS = (SEND_TOOL, "could", "not", "be", "checked,", "so", "nothing",
                    "was", "sent.", "Tell", "the", "person", "the", "files",
                    "could", "not", "be", "sent.")

SEND_UNCHECKED_REASON = " ".join(_UNCHECKED_WORDS)
_UNREADABLE_LIST_REASON = " ".join(_UNREADABLE_LIST_WORDS)


@dataclass(frozen=True)
class Refusal:
    """One file that may not be sent: ``shown`` is the path as given,
    ``code`` one of the ``CODE_*`` values, ``why`` the words Claude reads."""

    shown: str
    code: str
    why: str


def _why(code: str) -> str:
    return " ".join(_WHY_WORDS[code])


def _refusal(shown: str, code: str) -> Refusal:
    return Refusal(shown, code, _why(code) if code in _WHY_WORDS else "")


def _entries(tool_input) -> list[str] | None:
    """The trimmed entries of ``files``, or None when it cannot be read."""
    raw = safety.path_targets(SEND_TOOL, tool_input)
    if raw is None:
        return None
    return [safety._js_trim(e) for e in raw]


def _spelled_and_real(entry: str, cwd: str | None) -> tuple[str, str]:
    spelled = safety._norm(entry, cwd)
    return spelled, safety._realpath(spelled)


def _secret_name(path: str) -> bool:
    name = os.path.basename(path).casefold()
    return any(fnmatch.fnmatchcase(name, pat.casefold()) for pat in SECRET_NAMES)


def _on_floor(spelled: str, real: str) -> bool:
    targets = {spelled, real}
    if any(safety._matches_targets(targets, glob) for glob in SEND_FLOOR):
        return True
    return _secret_name(spelled) or _secret_name(real)


def _stat_checks(real: str) -> str | None:
    """The file-check code for a real path, or None when it may be sent."""
    try:
        st = os.stat(real)
    except FileNotFoundError:
        return CODE_MISSING
    except (OSError, ValueError):
        return CODE_UNREADABLE
    if not _stat.S_ISREG(st.st_mode):
        return CODE_NOT_FILE
    if st.st_nlink > 1:
        return CODE_HARD_LINK
    if st.st_size == 0:
        return CODE_EMPTY
    if st.st_size > FILE_MAX_BYTES:
        return CODE_TOO_BIG
    return None


def check_entry(entry: str, cwd: str | None) -> str | None:
    """The refusal code for one trimmed entry, or None when it passes the
    floor and the file checks."""
    spelled, real = _spelled_and_real(entry, cwd)
    if _on_floor(spelled, real):
        return CODE_FLOOR
    return _stat_checks(real)


def check_files(tool_input, cwd: str | None) -> list[Refusal]:
    """The floor, secret names and file checks for every role: one
    :class:`Refusal` per file that may not be sent (an unreadable list is
    one ``unreadable_list`` refusal). Empty when every file may go."""
    entries = _entries(tool_input)
    if entries is None:
        return [Refusal("", CODE_UNREADABLE_LIST, "")]
    out: list[Refusal] = []
    for entry in entries:
        code = check_entry(entry, cwd)
        if code is not None:
            out.append(_refusal(entry, code))
    return out


def _inside(path: str, roots: set[str]) -> bool:
    return any(safety._under(path, r) for r in roots)


def root_refusals(tool_input, cwd: str | None,
                  allowed_root: str | None) -> list[Refusal]:
    """A member's strict root bound: every file, as spelled AND as it
    really leads, must be inside ``allowed_root`` (whole path components,
    case folded where the disk ignores case), with no exception for paths
    outside the home folder. ``allowed_root`` None allows nothing."""
    entries = _entries(tool_input)
    if entries is None:
        return [Refusal("", CODE_UNREADABLE_LIST, "")]
    roots: set[str] = set()
    if isinstance(allowed_root, str) and allowed_root and "\x00" not in allowed_root:
        n = safety._norm(allowed_root)
        roots = {n, safety._realpath(n)}
    out: list[Refusal] = []
    for entry in entries:
        spelled, real = _spelled_and_real(entry, cwd)
        if not roots or not (_inside(spelled, roots) and _inside(real, roots)):
            out.append(_refusal(entry, CODE_OUTSIDE_ROOT))
    return out


def _item(r: Refusal) -> str:
    return f"{r.shown} ({r.why or _why(r.code)})"


def refusal_reason(refusals: list[Refusal]) -> str:
    """The English sentence Claude reads for these refusals."""
    if any(r.code == CODE_UNREADABLE_LIST for r in refusals):
        return _UNREADABLE_LIST_REASON
    items = "; ".join(_item(r) for r in refusals)
    return " ".join((*_HEAD_WORDS, items + ".", *_TAIL_WORDS))


def turn_rule_reason(reason: str) -> str:
    """A refusal by the turn's own rules (its Read rules, its tools), as
    Claude reads it."""
    return " ".join((*_HEAD_WORDS, f"{reason}.", *_TAIL_WORDS))


def _why_shape(code: str) -> str:
    why = _WHY_WORDS[code]
    if len(why) == 1:
        middle: tuple[str, ...] = (f"({why[0]}).",)
    else:
        middle = (f"({why[0]}", *why[1:-1], f"{why[-1]}).")
    return safety.reason_shape(*_HEAD_WORDS, "{shown}", *middle, *_TAIL_WORDS)


#: (code, anchored shape) of every reason above. A reason naming several
#: files matches the shape of its last file's code. Not part of
#: ``safety.REASON_SHAPES``: those feed the chat's hard-block notice, and a
#: soft refusal is never drawn there.
SEND_REASON_SHAPES: tuple[tuple[str, str], ...] = tuple(
    (code, _why_shape(code)) for code in _WHY_WORDS
) + (
    (CODE_UNREADABLE_LIST, safety.reason_shape(*_UNREADABLE_LIST_WORDS)),
    (CODE_UNCHECKED, safety.reason_shape(*_UNCHECKED_WORDS)),
    # A turn-rules reason wraps a safety reason, which starts with the
    # tool's name and never ends in ")": that keeps it apart from the
    # per-file shapes, whose last item ends "(<why>)".
    (CODE_TURN_RULES,
     r"\A" + re.escape(" ".join(_HEAD_WORDS) + " ")
     + r"(?P<shown>" + re.escape(SEND_TOOL) + r" .*[^)])\. "
     + re.escape(" ".join(_TAIL_WORDS)) + r"\Z"),
)


def reason_code(reason: str) -> str | None:
    """The code of a send reason, or None for any other sentence."""
    for code, shape in SEND_REASON_SHAPES:
        if re.match(shape, reason):
            return code
    return None


def sending_row(n: int) -> str:
    """The busy card's stored English row for a SendUserFile call (drawn
    in the chat's language by ``aipager.bot.card_rows``)."""
    return "📎 Sending 1 file" if n == 1 else f"📎 Sending {n} files"
