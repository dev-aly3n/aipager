"""Claude Code's 2-minute countdown on its risky-removal prompt (roadmap 8.128).

In Full access (``bypassPermissions``) and Auto, Claude Code 2.1.287+ asks
before a dangerous ``rm`` / ``rmdir`` (its ``dangerousRemoval`` safety
check) and refuses the call by itself when nobody answers within 2 minutes.
No hook fires at that refusal: the tool call's result in the transcript is
the only trace. aipager leaves the countdown on, so the prompt card says
about when Claude Code refuses (the clock), and the prompt closes as
refused once the transcript shows the call came back with an error.

This module holds the facts and the rules, nothing else:

- :func:`runs_removal`: whether a Bash command runs ``rm`` or ``rmdir`` the
  way Claude Code's check reads it (behind wrappers such as ``sudo`` and
  ``xargs``, in ``find -exec``, in a ``sh -c`` script). Any doubt is False.
- :func:`clock_eligible`: whether a PermissionRequest is that prompt.
- :func:`make_clock` / :func:`clock_of`: the ``rm_clock`` fact kept on the
  prompt's ``tool_info`` (when Claude Code refuses it, and which command).
- :func:`refusal_window_open` / :func:`next_check_at`: when a tap, a closer
  or the monitor treats the prompt as refused or about to be.
- :func:`disproof_due` / :func:`disprove`: a clock the transcript proved
  wrong (roadmap 8.152). Some prompts read as risky removals carry no
  countdown at all: an ask rule matching part of the command (say
  ``Bash(sleep:*)`` on a ``$(sleep ...)`` inside the ``rm``) turns
  Claude Code's dialog into its plain ask, and a server may set a longer
  countdown. Once the shown time plus :data:`DISPROVE_AFTER_SECONDS` has
  passed with no result for the call, the clock is marked disproven: no
  clock line and no tap block any more, while the monitor still closes
  the prompt as refused if the refusal does come later.

Stdlib only, no aipager imports (it is never in the hook's import closure,
and must not grow one), and no function raises: a bad input means "no
clock" / "not open".
"""

from __future__ import annotations

import hashlib
import math
import re
import time

#: Claude Code's default ``timeoutMs`` for the risky-removal prompt
#: (``tengu_splendid_horizon``). A server may set another value, which
#: aipager cannot read: the card says "about".
REFUSE_AFTER_SECONDS = 120.0
#: The refusal window opens this long before the shown time: aipager's own
#: handling of the PermissionRequest can lag the dialog by several seconds,
#: so the real refusal may come before the time shown.
WINDOW_LEAD_SECONDS = 15.0
#: The deadline timer looks this long after the shown time.
CHECK_GRACE_SECONDS = 3.0
#: A clock is disproven when the call has no result in the transcript this
#: long after the shown time. The shown time is already later than Claude
#: Code's own (aipager's handling lags the dialog), and its refusal is in
#: the transcript within a second (live: 0.4-0.5 s after it), so 20 s
#: leaves a wide margin before the tap block lifts.
DISPROVE_AFTER_SECONDS = 20.0
#: The permission modes in which Claude Code puts the countdown on it.
COUNTDOWN_MODES = frozenset({"bypassPermissions", "auto"})

#: Longer commands are not read (a bounded scan on the event loop).
MAX_COMMAND_CHARS = 16 * 1024
#: How deep a ``sh -c`` / ``eval`` script is read again (Claude Code's own
#: limit is 2).
MAX_SCRIPT_DEPTH = 2

#: The wall clock: the prompt's arrival stamp, the window checks and the
#: tap gate. A seam: tests replace THIS, never ``time.time``.
_now_wall = time.time

_REMOVALS = frozenset({"rm", "rmdir"})
#: Words Claude Code skips before the command word (its ``l1e`` list).
_WRAPPERS = frozenset({
    "sudo", "doas", "exec", "command", "env", "nice", "nohup", "busybox", "time",
    "timeout", "stdbuf", "setsid", "ionice", "pkexec", "runuser", "eval", "xargs"})
#: ``time`` is not here: it is read as a wrapper, so ``time -p rm x``
#: skips its option.
_KEYWORDS = frozenset({"do", "then", "else", "elif", "if", "while", "until", "!",
                       "coproc"})
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh"})
_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_REDIRECT = re.compile(r"[0-9&]*[<>]")
_DASH_C = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_CLOCK_KEYS = frozenset({"refuse_at", "command_sha"})
#: Set (to True) once the clock was disproven; never another value.
DISPROVEN = "disproven"


# ── reading a command ─────────────────────────────────────────────────

class _Doubt(Exception):
    """A command this reader cannot split the way the shell does."""


def _segments(script: str) -> list[list[str]]:
    """*script* split into simple commands, each a list of its words with
    quotes removed. Splits at unquoted ``;`` ``&`` ``|`` newline ``(``
    ``)`` backtick and ``$(``, and at ``{`` / ``}`` standing as words. A
    quoted string stays one word whatever it holds. ``#`` starting a word
    comments out the rest of the line. Escapes are honoured outside single
    quotes.

    A heredoc's body and a here-string's operand are data the shell does
    not run as commands (Claude Code's parser reads them so, roadmap
    8.151): they are left out, and the rest of the command is read as
    usual, so ``rm -rf x <<'EOF'`` is a removal and a body that only names
    rm is not. An unquoted ``<<WORD`` / ``<<-WORD`` / ``<<'WORD'`` takes
    the lines after its own, up to the line that is WORD (leading tabs
    stripped for ``<<-``), or to the end. Doubt (:class:`_Doubt`): a
    delimiter that is not a plain name, and a body or operand that would
    run a command substitution (``$(`` or a backtick outside single
    quotes) when the shell expands it."""
    segments: list[list[str]] = []
    words: list[str] = []
    word: list[str] = []
    in_word = False
    i, n = 0, len(script)
    #: Heredocs opened on the current line: ``(delimiter, strip_tabs,
    #: expands)``.
    pending: list[tuple[str, bool, bool]] = []

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            text = "".join(word)
            words.append(text)
        word.clear()
        in_word = False

    def end_segment() -> None:
        end_word()
        if words:
            segments.append(list(words))
        words.clear()

    while i < n:
        c = script[i]
        if c == "\\":
            if i + 1 < n:
                word.append(script[i + 1])
            in_word = True
            i += 2
            continue
        if c == "'":
            j = script.find("'", i + 1)
            if j < 0:
                j = n
            word.append(script[i + 1:j])
            in_word = True
            i = j + 1
            continue
        if c == '"':
            i += 1
            while i < n and script[i] != '"':
                if script[i] == "\\" and i + 1 < n and script[i + 1] in '"\\$`\n':
                    word.append(script[i + 1])
                    i += 2
                    continue
                word.append(script[i])
                i += 1
            in_word = True
            i += 1
            continue
        if c == "<" and i + 1 < n and script[i + 1] == "<":
            end_word()
            if script.startswith("<<<", i):
                operand, i = _heredoc_word(script, i + 3)
                if _expands(operand):
                    raise _Doubt("herestring")
                continue
            strip_tabs = script.startswith("<<-", i)
            raw, i = _heredoc_word(script, i + (3 if strip_tabs else 2))
            delimiter = _unquote(raw)
            if not _DELIMITER.fullmatch(delimiter):
                raise _Doubt("delimiter")
            # A quoted delimiter keeps the body as it is; a plain one
            # expands it, so a substitution in it would run.
            pending.append((delimiter, strip_tabs, raw == delimiter))
            continue
        if c == "#" and not in_word:
            j = script.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in " \t":
            end_word()
            i += 1
            continue
        if c == "&" and in_word and word and word[-1] in ("<", ">"):
            word.append(c)  # >&2, <&0: a redirection, not a background job
            i += 1
            continue
        if c == "&" and i + 1 < n and script[i + 1] == ">":
            end_word()
            word.append("&>")  # &> file
            in_word = True
            i += 2
            continue
        if c == "$" and i + 1 < n and script[i + 1] == "(":
            end_segment()
            i += 2
            continue
        if c in ";&|\n()`":
            end_segment()
            i += 1
            if c == "\n" and pending:
                i = _skip_bodies(script, i, pending)
                pending.clear()
            continue
        word.append(c)
        in_word = True
        i += 1
    end_segment()
    out: list[list[str]] = []
    for seg in segments:
        # ``{`` / ``}`` standing as words group commands: they split too.
        cur: list[str] = []
        for w in seg:
            if w in ("{", "}"):
                if cur:
                    out.append(cur)
                cur = []
            else:
                cur.append(w)
        if cur:
            out.append(cur)
    return out


_DELIMITER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _heredoc_word(script: str, i: int) -> tuple[str, int]:
    """The word starting at *i* after a ``<<`` / ``<<-`` / ``<<<`` (blanks
    before it skipped), as written (quotes kept), and the index after it.
    It ends at an unquoted blank or at a character that ends a command."""
    n = len(script)
    while i < n and script[i] in " \t":
        i += 1
    start = i
    while i < n and script[i] not in " \t;&|\n()<>":
        c = script[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            j = script.find(c, i + 1)
            if j < 0:
                raise _Doubt("quote")
            i = j + 1
            continue
        if c == "$" and i + 1 < n and script[i + 1] == "(":
            raise _Doubt("substitution")
        i += 1
    return script[start:min(i, n)], min(i, n)


def _unquote(word: str) -> str:
    """*word* with its quotes and backslashes removed (a heredoc's
    delimiter: ``'EOF'``, ``"EOF"``, ``\\EOF`` and ``E"O"F`` are all EOF)."""
    out, i, n = [], 0, len(word)
    while i < n:
        c = word[i]
        if c == "\\":
            out.append(word[i + 1:i + 2])
            i += 2
        elif c in "'\"":
            j = word.find(c, i + 1)
            j = n if j < 0 else j
            out.append(word[i + 1:j])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _expands(text: str, *, quotes: bool = True) -> bool:
    """Whether *text* (a here-string operand, or with *quotes* False a
    plain heredoc's body, where a single quote is a plain character) holds
    a command substitution the shell would run: ``$(`` or a backtick
    outside single quotes."""
    quoted = False
    for k, c in enumerate(text):
        if c == "'" and quotes:
            quoted = not quoted
        elif not quoted and (c == "`" or (c == "$" and text[k + 1:k + 2] == "(")):
            return True
    return False


def _skip_bodies(script: str, i: int,
                 pending: list[tuple[str, bool, bool]]) -> int:
    """The index after the bodies of the heredocs *pending* opened on the
    line that ended just before *i*, each up to its delimiter line (or to
    the end). A plain delimiter's body that would run a substitution is
    doubt (:func:`_expands`)."""
    n = len(script)
    for delimiter, strip_tabs, expands in pending:
        body: list[str] = []
        while i < n:
            j = script.find("\n", i)
            end = n if j < 0 else j
            line = script[i:end]
            i = n if j < 0 else j + 1
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                break
            body.append(line)
        if expands and _expands("\n".join(body), quotes=False):
            raise _Doubt("substitution")
    return i


def _base(word: str) -> str:
    """The command name *word* runs: a leading backslash and any path
    prefix stripped, lower-cased."""
    return word.lstrip("\\").rsplit("/", 1)[-1].lower()


def _segment_removes(words: list[str], depth: int) -> bool:
    for k, w in enumerate(words[:-1]):
        if w in _EXEC_FLAGS and _base(words[k + 1]) in _REMOVALS:
            return True
    i, n = 0, len(words)
    after_wrapper = False
    while i < n:
        w = words[i]
        base = _base(w)
        if _ASSIGNMENT.match(w):
            i += 1
            continue
        if _REDIRECT.match(w) or w.startswith("&>"):
            # ``2>`` alone takes the next word as its target.
            i += 2 if _REDIRECT.fullmatch(w) or w == "&>" else 1
            continue
        if not after_wrapper and w in _KEYWORDS:
            i += 1
            continue
        if base in _WRAPPERS:
            if base == "eval":
                return _script_removes(" ".join(words[i + 1:]), depth + 1)
            if base == "command" and i + 1 < n and words[i + 1] in ("-v", "-V"):
                return False  # only says where a command is
            after_wrapper = True
            i += 1
            continue
        if after_wrapper and (w.startswith("-") or w[:1].isdigit()):
            # A wrapper's option, and the one value word after it unless
            # that word is itself a removal or a wrapper.
            i += 1
            if (w.startswith("-") and i < n
                    and _base(words[i]) not in _REMOVALS | _WRAPPERS
                    and not words[i].startswith("-")):
                i += 1
            continue
        if base in _REMOVALS:
            return True
        if base in _SHELLS:
            for k in range(i + 1, n - 1):
                if _DASH_C.fullmatch(words[k]) and not words[k].startswith("--"):
                    return _script_removes(words[k + 1], depth + 1)
        return False
    return False


def _script_removes(script: str, depth: int) -> bool:
    if depth > MAX_SCRIPT_DEPTH:
        return False
    return any(_segment_removes(seg, depth) for seg in _segments(script))


def runs_removal(command: object) -> bool:
    """Whether the Bash *command* runs ``rm`` or ``rmdir`` (any case, any
    path prefix, a leading ``\\``), as its own command, behind a wrapper
    (``sudo``, ``env``, ``timeout 5``, ``xargs -0`` ...), in
    ``find ... -exec rm``, or inside a ``sh -c`` / ``bash -lc`` / ``eval``
    script up to two levels deep. False for anything that is not a str,
    a command over :data:`MAX_COMMAND_CHARS`, a removal only named inside
    quoted text or a heredoc's body or a here-string, and on any doubt.
    Never raises."""
    try:
        if not isinstance(command, str) or len(command) > MAX_COMMAND_CHARS:
            return False
        return _script_removes(command.replace("\\\n", ""), 0)
    except Exception:  # noqa: BLE001 - never raises: no clock
        return False


# ── the clock ─────────────────────────────────────────────────────────

def clock_eligible(*, tool_name: object, mode: object, suggestions: object,
                   agent_id: object, tool_input: object) -> bool:
    """Whether a PermissionRequest is Claude Code's risky-removal prompt
    with a countdown: Claude's own (no agent), Bash, in Full access or
    Auto, with no permission suggestions (that ask never has one), for a
    command that runs a removal. Never raises."""
    try:
        if agent_id:
            return False
        if tool_name != "Bash":
            return False
        if not (isinstance(mode, str) and mode in COUNTDOWN_MODES):
            return False
        if not (isinstance(suggestions, list) and not suggestions):
            return False
        if not isinstance(tool_input, dict):
            return False
        return runs_removal(tool_input.get("command"))
    except Exception:  # noqa: BLE001 - never raises: no clock
        return False


def command_sha(command: object) -> str:
    """The sha256 hex digest of *command* ("" for a non-str). Never
    raises."""
    try:
        if not isinstance(command, str):
            return ""
        return hashlib.sha256(command.encode("utf-8", "surrogatepass")).hexdigest()
    except Exception:  # noqa: BLE001 - never raises
        return ""


def make_clock(command: object, arrived_wall: object) -> dict | None:
    """The ``rm_clock`` fact for a prompt that arrived at *arrived_wall*
    (epoch seconds): ``{"refuse_at", "command_sha"}``. None when either
    cannot be made. Never raises."""
    try:
        if isinstance(arrived_wall, bool) or not isinstance(arrived_wall, (int, float)):
            return None
        refuse_at = float(arrived_wall) + REFUSE_AFTER_SECONDS
        sha = command_sha(command)
        if not (math.isfinite(refuse_at) and refuse_at > 0 and sha):
            return None
        return {"refuse_at": refuse_at, "command_sha": sha}
    except Exception:  # noqa: BLE001 - never raises
        return None


def valid_clock(clock: object) -> bool:
    """Whether *clock* is a well-formed ``rm_clock``: exactly its two
    keys, a finite positive ``refuse_at`` (not a bool) and a 64-character
    lowercase hex ``command_sha``, plus :data:`DISPROVEN` only as True.
    Never raises."""
    try:
        if not isinstance(clock, dict):
            return False
        keys = set(clock)
        if keys == _CLOCK_KEYS | {DISPROVEN}:
            if clock[DISPROVEN] is not True:
                return False
        elif keys != _CLOCK_KEYS:
            return False
        at = clock["refuse_at"]
        sha = clock["command_sha"]
        return (type(at) in (int, float) and math.isfinite(at) and at > 0
                and type(sha) is str and _SHA_RE.fullmatch(sha) is not None)
    except Exception:  # noqa: BLE001 - never raises
        return False


def clock_of(holder: object) -> dict | None:
    """The valid ``rm_clock`` of a prompt's ``tool_info``, or of an open
    prompt (its ``tool_info``'s); None otherwise. Never raises."""
    try:
        if not isinstance(holder, dict):
            return None
        clock = holder.get("rm_clock")
        if clock is None:
            info = holder.get("tool_info")
            clock = info.get("rm_clock") if isinstance(info, dict) else None
        return clock if valid_clock(clock) else None
    except Exception:  # noqa: BLE001 - never raises
        return None


def clock_disproven(holder: object) -> bool:
    """Whether the valid ``rm_clock`` of *holder* (a prompt or its
    ``tool_info``) was disproven (:func:`disprove`). Never raises."""
    clock = clock_of(holder)
    return clock is not None and clock.get(DISPROVEN) is True


def shown_clock(holder: object) -> dict | None:
    """The ``rm_clock`` the chat shows for *holder*: its valid clock
    unless disproven, else None. Never raises."""
    clock = clock_of(holder)
    if clock is None or clock.get(DISPROVEN) is True:
        return None
    return clock


def _past(perm: object, now_wall: object, offset: float) -> bool:
    """Whether *perm* has a valid clock and *now_wall* is at or after its
    shown time plus *offset*. Never raises."""
    try:
        clock = clock_of(perm)
        if clock is None:
            return False
        if isinstance(now_wall, bool) or not isinstance(now_wall, (int, float)):
            return False
        return now_wall >= clock["refuse_at"] + offset
    except Exception:  # noqa: BLE001 - never raises
        return False


def refusal_window_open(perm: object, now_wall: object) -> bool:
    """Whether Claude Code may have refused open prompt *perm* by itself
    by *now_wall*: it carries a clock that was not disproven and the time
    is at least :data:`WINDOW_LEAD_SECONDS` before the shown one. False
    for a prompt with no clock or a disproven one: taps, auto-denies and
    the hook closers then treat it as any other prompt. Never raises."""
    return (not clock_disproven(perm)
            and _past(perm, now_wall, -WINDOW_LEAD_SECONDS))


def result_check_open(perm: object, now_wall: object) -> bool:
    """Whether the monitor looks for *perm*'s call result in the
    transcript: from :data:`WINDOW_LEAD_SECONDS` before the shown time,
    a disproven clock too (a refusal that does come later, after a longer
    server-set countdown, still closes it as refused). Never raises."""
    return _past(perm, now_wall, -WINDOW_LEAD_SECONDS)


def disproof_due(perm: object, now_wall: object) -> bool:
    """Whether *perm*'s clock may be disproven at *now_wall*: it is valid,
    not disproven yet, and the shown time plus
    :data:`DISPROVE_AFTER_SECONDS` has passed. The caller still needs the
    transcript's evidence (no result for the call). Never raises."""
    return (not clock_disproven(perm)
            and _past(perm, now_wall, DISPROVE_AFTER_SECONDS))


def disprove(holder: object) -> bool:
    """Mark the valid ``rm_clock`` of *holder* (a prompt or its
    ``tool_info``) disproven, in place, so every holder of the same
    ``tool_info`` sees it. True when it changed. Never raises."""
    try:
        clock = clock_of(holder)
        if clock is None or clock.get(DISPROVEN) is True:
            return False
        clock[DISPROVEN] = True
        return True
    except Exception:  # noqa: BLE001 - never raises
        return False


def next_check_at(perm: object) -> float | None:
    """When the deadline timer looks at *perm* (the shown time plus
    :data:`CHECK_GRACE_SECONDS`), or None for a prompt with no clock."""
    clock = clock_of(perm)
    if clock is None:
        return None
    return float(clock["refuse_at"]) + CHECK_GRACE_SECONDS
