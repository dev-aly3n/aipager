"""Hard-safety policy data for multi-scope mode (Phase A: data only).

This module holds the *built-in defaults* for the Telegram-driven
safety boundary and the role permission profiles. It is intentionally
**data-only** in Phase A — nothing here is consulted at enforcement
time yet. The ``PreToolUse`` hook starts using it in a later phase.

See ``researches/multi-scope-mode/02-security-model.md`` for the
rationale behind every entry.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import sys

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# B1 — no-access paths (block READ *and* WRITE from Telegram).
# These hold other users' data + aipager's own bones. A Telegram session
# has no legitimate reason to touch them.
# ---------------------------------------------------------------------------
DENY_PATHS_NO_ACCESS: tuple[str, ...] = (
    "~/.claude/**",                 # transcripts live in projects/
    "~/.config/aipager/**",
    "~/.local/share/aipager/**",
    "~/.local/state/aipager/**",
    # aipager's own control files in /tmp (roadmap 8.50). The policy
    # snapshot decides this turn's restrictions — a turn that could Write
    # or Edit it could grant itself the owner's bypass — and the notes,
    # status, reply and dtach files feed the daemon. These globs keep the
    # FILE tools (Read/Write/Edit/Grep/Glob…) off them; they do nothing
    # against a shell. The same OS user owns these files, so a turn with
    # Bash can reach them through a spelling no pattern anticipates — the
    # built-in restricted roles are protected by having no shell
    # (CODE_EXECUTION_TOOLS), not by DENY_BASH_PATTERNS. The one exception
    # is a session reading its OWN reply file (enforce passes it as
    # ``readable``), which the reply-to-an-older-message prompt names.
    "/tmp/claude-policy-*",
    "/tmp/claude-notes-*",
    "/tmp/claude-status-*",
    "/tmp/claude-reply-*",
    "/tmp/claude-dtach-*",
)

# ---------------------------------------------------------------------------
# Credential files (roadmap 8.79): keys, tokens and passwords other
# programs keep in the home folder. No access for every role without
# ``bypass_role_denies`` (the built-in ``user`` and ``read_only`` roles,
# a custom role ``policy.yaml`` defines, and the floor an unattributed
# turn runs under); owners and admins keep them. A role default, not part
# of :data:`DENY_PATHS_NO_ACCESS`, so ``policy.yaml`` can still set a
# role's ``deny_paths_no_access`` (which replaces this list for it). Every
# entry is ``~``-anchored: an unanchored pattern would deny every Grep
# and Glob of a restricted turn (see ``_search_violation``).
# ---------------------------------------------------------------------------
CREDENTIAL_PATHS: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.gnupg/**",
    "~/.aws/**",
    "~/.config/gh/**",
    "~/.git-credentials",
    "~/.netrc",
    "~/.docker/config.json",
    "~/.kube/**",
    "~/.config/gcloud/**",
    "~/.azure/**",
    "~/.password-store/**",
    "~/.pgpass",
    "~/.npmrc",
    "~/.pypirc",
)

# ---------------------------------------------------------------------------
# B2 — no-write paths (READ ok, block WRITE). Empty by default; reserved
# for operator-declared project paths.
# ---------------------------------------------------------------------------
DENY_PATHS_NO_WRITE: tuple[str, ...] = ()

# ---------------------------------------------------------------------------
# Recursive searches and listings of the whole home folder or above
# (roadmap 8.98). Claude Code builds without a Grep/Glob tool (2.1.289 on
# the operator's box) search with ``grep -rn … $HOME`` through Bash, which
# read ``~/.config/aipager`` for an admin: the path patterns below only see
# a command that NAMES aipager's folders. These deny a search or listing
# command whose folder is the home folder (``~``, ``~user``, ``$HOME``,
# ``${HOME}``, ``"$HOME"``), ``/home``, ``/home/<user>`` (``$USER``
# included), ``/Users``, ``/Users/<user>``, ``/root`` or ``/`` (each with
# or without a trailing ``/``, ``/.`` or ``/*``), or ``~/.config`` /
# ``~/.local`` (``/share``, ``/state``), which hold the protected folders.
# The deny reason says to search inside a project folder instead.
#
# What is a folder: the command must be the command word (first in its
# command, after ``;``, ``&``, ``|``, ``(``, a newline, ``sh -c "`` or a
# wrapper such as ``xargs``, ``env`` or ``timeout 5``; a line ending in
# ``\`` goes on to the next), and for the tools
# whose first word is the text to find (grep, rg, ag, ack, fd) that first
# word is the text, not a folder, unless ``-e``/``-f`` gives the text
# (and then the word right after a lone ``-e``/``-f``/``--regexp``/
# ``--file`` is a text, never a folder; in a cluster such as ``-rne`` it
# is still read as a folder). So
# a project search FOR a home path (``grep -rn "$HOME" src``,
# ``rg -n '/Users/' .``) and ``find . -path '/home/*'`` are not denied.
# Quoted text the shell does not expand (``'~'``, ``'$HOME'``) is never a
# folder. A search inside a project (``grep -r x ~/proj``, ``rg x src/``,
# ``find . -name y``) is not touched.
#
# Best effort, like every Bash pattern: a regex sees words, not what the
# shell will do. Not caught, for example: a search with no folder, or
# ``.`` or ``..``, run from (or below) the home folder
# (``cd ~ && grep -r x .``), a folder spelled through another variable or a
# glob (``H=~; grep -r x $H``), a path written out in full that is not
# under /home, /Users or /root, a wrapper not listed below
# (``sudo -u bob grep …``, ``\grep``). Known false positives: an option
# that takes a separate value before the text (``rg -t py "$HOME" .``,
# ``grep -rn -A 3 "/home/x" src``: ``py`` and ``3`` read as the text),
# and a command quoted inside another command's text (``echo "a; rg x
# ~"``), since a separator inside quotes may start a ``bash -c`` command.
#
# Cost: each pattern starts only at a command word, then reads that one
# command (never past ``;``, ``&``, ``|`` or a bare newline outside
# quotes, a quoted string as one unit). No repetition is ambiguous (each
# repeated unit starts with a different character), so there is no
# exponential backtracking. Real commands take milliseconds (a 270 KB
# heredoc about 0.02 s per pattern); input crafted so that commands start
# inside quotes is quadratic (40 KB about 3 s), which only delays the
# answer. tests/test_bash_search_floor.py times long and adversarial
# commands.
# ---------------------------------------------------------------------------

#: A space between two words of one command (a backslash-newline joins
#: the next line).
_SP = r"(?:[ \t]|\\\n)"
#: One unit of a word: a plain character, an escaped one, or a whole
#: quoted string (so a ``|`` inside ``"a|b"`` does not end the command).
_UNIT = r"""(?:[^\s;&|()<>`"'\\]|\\[^\n]|'[^'\n]*'|"[^"\n]*")"""
#: One unit of a single command: a word unit, a space, or a redirection
#: (which ends a word). A parenthesis or a backquote ends the command.
_SEG = rf"(?:{_UNIT}|{_SP}|[<>])"
#: Where a word ends.
_WORD_END = r"(?=[\s;&|()<>`]|\\\n|$)"
#: A whole word that is not an option (the lookahead keeps it whole).
_WORD = rf"(?!-){_UNIT}+{_WORD_END}"
#: Options before the first word.
_OPTS = rf"(?:{_SP}+-{_UNIT}*)*"

#: Commands that run the next word as a command, with their own options
#: (``xargs -0``, ``timeout 5``; ``env A=1`` is ``env`` then a setting),
#: and shell keywords
#: (``if``, ``then``, ``!``, ``{``).
_WRAP = (r"(?:sudo|doas|xargs|env|nice|nohup|time|command|exec|builtin"
         r"|timeout|stdbuf|ionice|watch|if|then|do|else|elif|while|until|!|\{)")
_WRAP_ARG = rf"(?:-{_UNIT}*|\d{_UNIT}*)"
#: A variable set for the command alone: ``LC_ALL=C grep …``.
_ASSIGN = rf"[A-Za-z_]\w*={_UNIT}*{_SP}+"
#: A shell running a quoted command: ``bash -c "``, ``sh -lc '``.
_SHELL_C = rf"""(?:ba|da|z|k)?sh(?:{_SP}+-[A-Za-z]+)+{_SP}+["']{_SP}*"""
#: Where a command word starts (the start, or after ``;``, ``&``, ``|``,
#: ``(``, a backquote or a newline that does not continue a line), then
#: variables set for it, wrappers (``{ `` is one) and a path
#: (``/usr/bin/``). Outside quotes, only characters that also end a
#: command start one, so no two scans overlap.
_CMD = (r"(?:^|(?<=[;&|(`])|(?<!\\\n)(?<=\n))"
        rf"{_SP}*(?:{_WRAP}(?:{_SP}+{_WRAP_ARG})*{_SP}+|{_SHELL_C}|{_ASSIGN})*"
        r"(?:[\w.~+-]*/)*")


def _command(names: str) -> str:
    """One of ``names`` as the command word (``/usr/bin/find`` counts,
    ``--find-renames``, ``ls-tree``, ``find.py`` and ``find/x`` do not)."""
    return rf"{_CMD}(?:{names})(?![\w./-])"


#: One user name in ``/home/<user>`` or ``/Users/<user>``.
_USER = r"""(?:[^/\s"'`;&|()<>$]+|\$\{?(?:USER|LOGNAME)\}?)"""
#: The home folder as a path: ``/home``, ``/home/<user>``, ``/Users``,
#: ``/Users/<user>`` or ``/root`` (quoted or not, the shell reads it so).
_HOME_PATH = rf"(?:/home(?:/{_USER})?|/Users(?:/{_USER})?|/root)"
#: The home folder as a variable: expanded bare or in double quotes only.
_HOME_VAR = r"(?:\$HOME|\$\{HOME\})"
#: The home folder as a tilde (``~``, ``~user``): expanded bare only.
_HOME_TILDE = r"(?:~(?:[A-Za-z_][\w.-]*)?)"
#: Folders in the home folder that hold aipager's protected folders.
_HOME_PARENTS = r"(?:/\.config|/\.local(?:/share|/state)?)"


def _target_body(*homes: str) -> str:
    """One of ``homes`` (or one of the folders in it that hold the
    protected ones), or ``/``; then an optional ``/``, ``/.`` or ``/*``."""
    return rf"(?:(?:{'|'.join(homes)}){_HOME_PARENTS}?|/)/?[.*]?"


#: A whole-home (or ``/``) folder word: bare, or quoted as a whole the way
#: the shell would still expand it (a quote opened here must close right
#: after it, so ``'/ x'`` is not a folder, and ``'~'`` or ``'$HOME'`` is
#: plain text), then an optional unquoted ``/.config``-style folder and
#: ``/``, ``/.`` or ``/*`` (``"$HOME"/.config``), ending where the shell
#: word ends (or the string around a ``bash -c "…"``).
_TARGET = (
    r"(?:"
    rf"{_target_body(_HOME_TILDE, _HOME_VAR, _HOME_PATH)}"
    rf'|"{_target_body(_HOME_VAR, _HOME_PATH)}"'
    rf"|'{_target_body(_HOME_PATH)}'"
    rf"){_HOME_PARENTS}?/?[.*]?"
    r"""(?=[\s;&|()<>`"']|\\\n|$)""")

#: The text to find is given by an option (``-e x``, ``-f file``,
#: ``--regexp``, ``--file``), so every other word may be a folder.
_TEXT_BY_OPTION = (rf"(?={_SEG}*?{_SP}"
                   r"(?:-[A-Za-z0-9]*[ef]|--regexp\b|--file\b))")
#: After a search tool's name: a folder word anywhere after the text to
#: find (the first word that is not an option, unless an option gave it),
#: never the word right after ``-e``/``-f``/``--regexp``/``--file``.
_FOLDER_AFTER_TEXT = (
    rf"(?:{_TEXT_BY_OPTION}{_SEG}*?|{_OPTS}{_SP}+{_WORD}{_SEG}*?)"
    rf"(?<!-e)(?<!-f)(?<!--regexp)(?<!--file){_SP}{_TARGET}")
#: After a listing tool's name: a folder word anywhere.
_FOLDER_ANYWHERE = rf"{_SEG}*?{_SP}{_TARGET}"

#: rgrep, rg, ag, ack, fd: always recursive, the first word is the text.
SEARCH_TEXT_FIRST = (rf"{_command('rgrep|rg|ag|ack|ack-grep|fd|fdfind')}"
                     rf"{_FOLDER_AFTER_TEXT}")
#: grep / egrep / fgrep with a recursive option anywhere in the command.
SEARCH_GREP_RECURSIVE = (
    rf"{_command('[ef]?grep')}"
    rf"(?={_SEG}*?{_SP}(?:-[A-Za-z0-9]*[rR]|--recursive|--dereference-recursive"
    rf"|-d{_SP}*recurse|--directories(?:=|{_SP}+)recurse))"
    rf"{_FOLDER_AFTER_TEXT}")
#: find: a folder among the start points (before the first ``-``, ``(``
#: or ``!`` word), after find's own ``-H``/``-L``/``-P``/``-D x``/``-O1``.
SEARCH_FIND = (rf"{_command('find')}(?:{_SP}+-[HLP])*"
               rf"(?:{_SP}+-D{_SP}+{_UNIT}+)?(?:{_SP}+-O\d*)?"
               rf"(?:{_SP}+(?![-(!\\]){_WORD})*?{_SP}+{_TARGET}")
#: tree and du: every word that is not an option is a folder.
LIST_TREE_DU = rf"{_command('tree|du')}{_FOLDER_ANYWHERE}"
#: ls -R / ls --recursive (capital R: ``ls -r`` only reverses the order).
LIST_LS_RECURSIVE = (rf"{_command('ls')}"
                     rf"(?={_SEG}*?{_SP}(?:-[A-Za-z0-9]*R|--recursive))"
                     rf"{_FOLDER_ANYWHERE}")
#: rg --files and ack -f: list every file, every word is a folder (rg's
#: own ``-f`` reads a file of texts to find instead).
LIST_FILES = (
    rf"(?:{_command('rg')}(?={_SEG}*?{_SP}--files(?![\w-]))"
    rf"|{_command('ack|ack-grep')}(?={_SEG}*?{_SP}-f(?![\w-])))"
    rf"{_FOLDER_ANYWHERE}")

DENY_BASH_SEARCH_PATTERNS: tuple[str, ...] = (
    SEARCH_TEXT_FIRST,
    SEARCH_GREP_RECURSIVE,
    SEARCH_FIND,
    LIST_TREE_DU,
    LIST_LS_RECURSIVE,
    LIST_FILES,
)

# ---------------------------------------------------------------------------
# Bash command patterns denied from Telegram (case-sensitive regex).
# Covers daemon manipulation, escalation, pipe-to-shell, and — critically —
# nested ``claude`` invocations + privilege flags that would let an
# in-session user craft a privileged claude instance.
# ---------------------------------------------------------------------------
DENY_BASH_PATTERNS: tuple[str, ...] = (
    r"^(sudo|su)\b",
    r"\baipager\s+(service|config|start)\b",
    r"\bsystemctl\b.*\baipager\b",
    r"\brm\b.*(\.config/aipager|\.claude|\.local/share/aipager)",
    # Any command referencing aipager's own dirs — a Telegram session has
    # no legitimate reason to read OR write them. path_violation only
    # guards the Read/Glob/Edit tools, so without these a `cat`/`grep`/`cp`
    # of these paths via Bash would exfiltrate the bot token / scopes.
    # (~/.claude/** is covered by the \bclaude\b pattern below — keep that
    # in mind before ever narrowing it. The one thing it does NOT see is a
    # session's own scratchpad root, which bash_violation normalises away
    # first — see normalize_scratchpad.)
    r"\.config/aipager\b",
    r"\.local/share/aipager\b",
    r"\.local/state/aipager\b",
    # Nested claude invocations + privilege flags (the flag is the
    # smoking gun; catches obfuscated binary names too). Also incidentally
    # blocks reads of ~/.claude/** via Bash.
    r"\bclaude\b",
    r"--append-system-prompt\b",
    r"--system-prompt\b",
    r"--dangerously-skip-permissions\b",
    # Exact flag token only: `--resume`, `--resume=x`, `"--resume"`, but not
    # an unrelated longer flag such as `--resume-from` (`\b` sat before the
    # hyphen). Claude Code's own `--resume-session-at`/`--resume-drops-turn`
    # only act together with `--resume`, which this still blocks.
    r"--resume(?![\w-])",
    r"--mcp-config\b",
    # A recursive search or listing of the whole home folder or / (8.98).
    *DENY_BASH_SEARCH_PATTERNS,
)

# ---------------------------------------------------------------------------
# Tools that run code as the OS user: Claude Code 2.1.283's own set (every
# tool it flags ``enablesCodeExecution``, plus PowerShell, which it adds by
# name). The built-in restricted roles deny all of them (operator decision
# 2026-09-26, "No Bash for restricted"). The Claude session runs as the
# same OS user that owns aipager's /tmp control files and config, so a
# restricted turn with a shell can rewrite its own policy file through a
# glob no pattern anticipates (``sed -i … /tmp/*-policy-*.json``) and grant
# itself the owner's bypass. DENY_BASH_PATTERNS is a best-effort filter,
# not a boundary; removing the shell is. CronCreate and RemoteTrigger
# schedule prompts that would arrive without the Telegram marker, i.e.
# unrestricted. A role that ``policy.yaml`` gives one of these back is
# held to best-effort rules only (``aipager doctor`` says so).
# ---------------------------------------------------------------------------
CODE_EXECUTION_TOOLS: tuple[str, ...] = (
    "Bash",
    "PowerShell",
    "Monitor",
    "REPL",
    "Workflow",
    "CronCreate",
    "RemoteTrigger",
    "AppifactRepl",
    "self_hosted_runner_spawn_local",
    "self_hosted_runner_requeue_session",
)

# What the built-in restricted roles deny: every code-running tool, plus
# three that are not code execution but each gets a turn out of its box.
# SendMessage can deliver a prompt to ANOTHER local Claude session
# (``uds:``/``bridge:`` peers), where it arrives without the Telegram
# marker — terminal origin, unrestricted — unless that session's user
# happens to hold cross-session messages for approval. EnterWorktree and
# ExitWorktree move the session's working directory, which is where a
# restricted turn's writes are confined to (review rev-iter1-003/004).
RESTRICTED_DENY_TOOLS: tuple[str, ...] = (
    *CODE_EXECUTION_TOOLS,
    "SendMessage",
    "EnterWorktree",
    "ExitWorktree",
)

# ---------------------------------------------------------------------------
# Built-in role permission profiles, as plain dicts so ``policy.py`` can
# import them without a circular dependency. ``policy.load_policy`` turns
# these into ``Role`` objects and lets ``policy.yaml`` override any field.
#
# Field meanings (full schema lives on ``policy.Role``):
#   bypass_safety       — skip the §3.7 hard boundary entirely (owner only)
#   bypass_role_denies  — ignore deny_tools / allow_tools (admin-style)
#   can_prompt          — may drive prompts
#   can_approve         — may tap permission buttons
#   can_manage          — may use the admin features (Auto, a group's
#                         /settings, /update); never members or roles
# Unspecified list/bool fields fall back to ``policy.Role`` defaults
# (empty lists, auto_approve=False).
# ---------------------------------------------------------------------------
BUILTIN_ROLE_DEFAULTS: dict[str, dict] = {
    "owner": {
        "bypass_safety": True,
        "bypass_role_denies": True,
        "can_prompt": True,
        "can_approve": True,
        "can_manage": True,
    },
    "admin": {
        "bypass_safety": False,
        "bypass_role_denies": True,
        "can_prompt": True,
        "can_approve": True,
        "can_manage": True,
    },
    "user": {
        "bypass_safety": False,
        "bypass_role_denies": False,
        "can_prompt": True,
        "can_approve": True,
        "can_manage": False,
        "deny_tools": RESTRICTED_DENY_TOOLS,
        "deny_paths_no_access": CREDENTIAL_PATHS,
    },
    "read_only": {
        "bypass_safety": False,
        "bypass_role_denies": False,
        "can_prompt": False,
        "can_approve": False,
        "can_manage": False,
        "deny_tools": RESTRICTED_DENY_TOOLS,
        "deny_paths_no_access": CREDENTIAL_PATHS,
    },
}


# ---------------------------------------------------------------------------
# Pure matchers (Phase E). Used by the PreToolUse hook to decide whether a
# Telegram-driven tool call must be denied. No daemon state — the caller
# supplies the resolved deny lists (from the policy snapshot) and the
# trusted context (the hook payload's cwd). The only I/O is
# ``os.path.realpath``.
# ---------------------------------------------------------------------------

# Tools whose target is a filesystem path, and the input key holding it.
# ``MultiEdit`` is gone from current Claude Code (2.1.283 only maps old
# permission rules for it) but older releases still run it.
_PATH_KEYS = {
    "Read": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "Write": "file_path",
    "NotebookEdit": "notebook_path",
    "LSP": "filePath",
    "Glob": "path",
    "Grep": "path",
}
_WRITE_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
_SEARCH_TOOLS = {"Glob", "Grep"}

# Inside a confined write root, these still may not be written: each one
# makes Claude Code or git run a command as the OS user. ``.claude/``
# holds project settings (hooks, statusLine), ``.mcp.json`` starts MCP
# servers, ``.git/`` holds hooks and ``core.fsmonitor``, which Claude
# Code's own ``git status`` would run.
_NEVER_WRITE_DIRS = (".claude", ".git")
_NEVER_WRITE_FILES = (".mcp.json",)

_GLOB_MAGIC = re.compile(r"[*?\[{]")
# Where a search glob splits into pieces: JavaScript's ``\s`` (what Claude
# Code splits Grep's ``glob`` on — it includes U+FEFF, which Python's
# ``str.split()`` misses), commas, and brace delimiters.
_GLOB_PIECES = re.compile(
    "[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f"
    "\u205f\u3000\ufeff,{}]+")
_DRIVE = re.compile(r"[A-Za-z]:")

# What JavaScript's String.prototype.trim() strips — Claude Code trims a
# tool's path before using it, so a path the checks read with a leading
# space, tab or U+FEFF must be trimmed the same way first, or " ~/.config"
# reads as a folder under the cwd while Claude Code searches ~/.config
# (roadmap 8.52, review 4).
_JS_TRIM = ("\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
            "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")


def _js_trim(value):
    return value.strip(_JS_TRIM) if isinstance(value, str) else value


def _case_insensitive_fs() -> bool:
    """True where the filesystem ignores case (macOS's APFS default), so
    ``/TMP/CLAUDE-POLICY-x`` opens ``/tmp/claude-policy-x`` (roadmap
    8.53). Folding on Linux as well would be safe, but would let
    ``/tmp/X`` match a rule written for ``/tmp/x``, a different file."""
    return sys.platform == "darwin"


def _expand_tool_home(path: str) -> str:
    """Expand ``~`` exactly as Claude Code expands a tool's path, and no
    further: only a bare ``~`` or a leading ``~/`` means the home folder.
    Claude Code does NOT expand ``~name`` (another user's home); it reads
    it as a relative name under the cwd. ``os.path.expanduser`` does
    expand it, so ``~root/../../.config/aipager/config.yaml`` was checked
    as ``/.config/aipager/config.yaml`` while Claude Code opened the one
    under the operator's home: a restricted Read of the bot token (review
    2026-09-27). The check must read a path the way the tool will."""
    # No Unicode normalisation: Claude Code's expandPath returns the path
    # through an identity wrapper, so the file it opens has the exact code
    # points given. Normalising here (tried 2026-09-27) made the check read
    # a different file than the tool opens (review 3).
    if path == "~":
        return os.path.expanduser("~")
    if path.startswith("~/"):
        # Node's path.join keeps the home folder even when the rest starts
        # with "/"; Python's os.path.join would DROP it, turning
        # "~//.config/aipager/x" into "/.config/aipager/x" (review
        # 2026-09-27). Concatenate, then let abspath collapse the "//".
        return os.path.expanduser("~").rstrip("/") + "/" + path[2:]
    return path


def _norm(path: str, base: str | None = None) -> str:
    """Absolute path for glob matching, ``~`` expanded the way Claude Code
    expands a tool's path (:func:`_expand_tool_home`). A relative path is
    taken relative to ``base`` (the session's cwd) when given."""
    p = _expand_tool_home(str(path))
    if base and not os.path.isabs(p):
        p = os.path.join(os.path.expanduser(base), p)
    return os.path.abspath(p)


def _realpath(path: str) -> str:
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return path


def _literal_prefix(glob: str) -> str:
    """The leading directory of an absolute glob up to its first magic
    component (``/tmp/claude-policy-*`` → ``/tmp``; ``~/.claude/**`` →
    ``~/.claude`` expanded)."""
    parts = glob.split("/")
    keep: list[str] = []
    for part in parts[:-1] if not glob.endswith("**") else parts:
        if _GLOB_MAGIC.search(part):
            break
        keep.append(part)
    return "/".join(keep) or "/"


def _glob_variants(glob: str) -> set[str]:
    """``glob`` ~-expanded, and again with its literal anchor resolved
    through ``realpath`` (``/tmp/…`` → ``/private/tmp/…`` on macOS)."""
    g = os.path.expanduser(glob)
    out = {g}
    if g.startswith("/"):
        anchor = _literal_prefix(g)
        real = _realpath(anchor)
        if real != anchor:
            out.add(real.rstrip("/") + g[len(anchor.rstrip("/")):])
    return out


def _fold(s: str) -> str:
    return s.casefold() if _case_insensitive_fs() else s


def _under(path: str, root: str) -> bool:
    p, r = _fold(path), _fold(root.rstrip("/") or "/")
    return p == r or p.startswith(r if r == "/" else r + "/")


def home_folder() -> str:
    """The OS user's home folder, symlinks resolved."""
    return _realpath(os.path.expanduser("~"))


def is_wide_folder(path: str) -> bool:
    """True when ``path``, symlinks resolved, is ``/``, the home folder or
    a folder that holds the home folder (roadmap 8.79). A restricted turn
    whose session runs there would have the whole home folder as its
    project: ``~/.bashrc``, ``~/.ssh/authorized_keys`` and
    ``~/.config/systemd/user`` among its writable files. An unreadable
    path counts as wide (fail closed)."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return True
    real = _realpath(os.path.abspath(path))
    return _under(home_folder(), real)


def _matches_one(target: str, g: str) -> bool:
    target, g = _fold(target), _fold(g)
    if g.startswith("/"):
        if g.endswith("**"):
            base = g[:-2].rstrip("/")
            return target == base or target.startswith(base + "/")
        return fnmatch.fnmatchcase(target, g)
    tail = g[3:] if g.startswith("**/") else g
    return (
        fnmatch.fnmatchcase(target, g)
        or fnmatch.fnmatchcase(os.path.basename(target), tail)
        or fnmatch.fnmatchcase(target, "*/" + tail)
    )


def _matches_targets(targets: set[str], glob: str) -> bool:
    """True if any of ``targets`` matches a deny glob.

    Anchored globs (``~/.claude/**``) match by prefix — ``**`` covers the
    dir itself and everything under it — in their spelled and their
    realpath form. Unanchored globs (``**/*.lock``, ``*.lock``) match the
    basename / any tail. Case is folded where the disk ignores it.
    """
    return any(_matches_one(t, g)
               for g in _glob_variants(glob) for t in targets)


def _glob_problem(glob) -> str | None:
    """Why a Grep ``glob`` / Glob ``pattern`` is not a plain relative
    pattern, or ``None``. Plain means: every piece relative (no leading
    ``/``, ``~``, ``$`` or drive letter), no ``..`` component, not a
    gitignore comment (``#``) or negation (``!``), and no ``\\``
    escape. Anything else is how a glob can lead ripgrep or Claude Code
    out of the folder being searched, so it is refused rather than
    modelled (three review rounds found a new spelling each time)."""
    if not isinstance(glob, str):
        return "an unreadable glob"
    if "\\" in glob:
        return "an escaped glob"
    for piece in _GLOB_PIECES.split(glob):
        if not piece:
            continue
        if piece[0] in "/~$#!" or _DRIVE.match(piece):
            return "a glob that is not a plain relative pattern"
        if ".." in piece.split("/"):
            return "a glob that climbs out with .."
    return None


# The deny reason when a restricted turn's session runs in the home folder
# (or above it), whose folder is then no write or search root (8.79).
def _wide_cwd_reason(tool_name: str, verb: str) -> str:
    return (f"{tool_name} denied - this session runs in the home folder "
            f"(or a folder above it), and this role can only {verb} inside "
            "a project folder")


def _search_violation(
    tool_name: str, tool_input: dict, no_access: tuple[str, ...],
    cwd: str | None, roots: tuple[str, ...], wide_cwd: bool = False,
) -> str | None:
    """A confined turn's Grep/Glob, as an allow-list (roadmap 8.52,
    operator decision 2026-09-27). Allowed only when:

    - the search root (``path``, else the payload ``cwd``), with symlinks
      resolved, is inside one of ``roots`` — the session's folder and
      scratchpad, the same roots writes are confined to;
    - the root is not itself a protected path, and holds no anchored
      protected path (a session started in ``$HOME`` holds
      ``~/.config/aipager``);
    - no protected-path rule is unanchored (an operator's ``**/*.pem``
      can sit anywhere under the root, so no search is safe);
    - every glob is plain and relative (:func:`_glob_problem`).
    """
    root = _js_trim(tool_input.get("path")) or cwd
    if not isinstance(root, str) or not root or "\x00" in root:
        # A NUL byte is never a real path; denied explicitly rather than by
        # relying on something downstream raising on it.
        return f"{tool_name} with an unreadable search folder"
    spelled = _norm(root, cwd)
    real = _realpath(spelled)
    if not any(_under(real, _realpath(_norm(r))) for r in roots):
        if wide_cwd:
            return _wide_cwd_reason(tool_name, "search")
        return (f"{tool_name} outside the session's folder - a restricted "
                "turn searches only its project and scratchpad")
    for glob in no_access:
        if _matches_targets({spelled, real}, glob):
            return f"{tool_name} on protected path {glob}"
        g = os.path.expanduser(glob)
        if not g.startswith("/"):
            return f"{tool_name} search could reach protected path {glob}"
        if any(_under(_literal_prefix(v), t)
               for v in _glob_variants(g) for t in (spelled, real)):
            return f"{tool_name} search reaches protected path {glob}"
    key = "glob" if tool_name == "Grep" else "pattern"
    if key in tool_input and tool_input[key] is not None:
        problem = _glob_problem(tool_input[key])
        if problem:
            return f"{tool_name} with {problem}"
    return None


# What can stand for something other than itself in a Glob tool pattern
# (picomatch: wildcards, classes, braces, extglob and regex groups, and
# the ``\`` escape, which makes ``aipage\r`` name ``aipager``). The
# literal part of a pattern ends at the first component holding one;
# stopping too early only makes the search root wider, so stricter.
_SEARCH_GLOB_MAGIC = re.compile(r"[*?\[\]{}()\\]")


def _pattern_dirs(pattern: str, base: str) -> set[str]:
    """The folder a Glob ``pattern`` searches: its literal leading
    components joined to ``base`` (the search folder) unless the pattern
    is absolute or ``~``-anchored, ``..`` resolved the way Claude Code
    reads it (lexically) and, separately, through the disk (symlinks).
    A pattern with no wildcard at all searches its parent folder: Claude
    Code splits ``/a/b/name`` into the folder ``/a/b`` and the glob
    ``name``, which ripgrep matches at any depth there (so
    ``~/.config/aipager.yaml`` lists ``~/.config/aipager/aipager.yaml``).
    Claude Code itself expands no ``~`` in a pattern and reads a relative
    one as a filter inside ``base``; treating those as folders too only
    makes the check stricter."""
    p = _expand_tool_home(pattern)
    if not os.path.isabs(p):
        p = base.rstrip("/") + "/" + p
    keep: list[str] = []
    parts = p.split("/")
    for part in parts:
        if _SEARCH_GLOB_MAGIC.search(part):
            break
        keep.append(part)
    literal = "/".join(keep) or "/"
    spelled = os.path.abspath(literal)
    if len(keep) == len(parts):
        # No wildcard: the search folder is the parent.
        spelled = os.path.dirname(spelled)
        literal = os.path.dirname(literal.rstrip("/")) or "/"
    return {spelled, _realpath(spelled), _realpath(literal)}


def _protected_spots(glob: str) -> list[tuple[str, str | None]] | None:
    """Where a no-access ``glob`` lives, as ``(folder, name)`` pairs: the
    folder is its literal leading part, ``name`` the first component that
    is a pattern (``None`` when the glob is a literal path, so the folder
    itself is protected). ``~/.config/aipager/**`` is the folder
    ``~/.config/aipager`` (name ``**``, i.e. everything in it);
    ``/tmp/claude-policy-*`` is the entries of ``/tmp`` named
    ``claude-policy-*``. ``None`` for an unanchored glob, which can match
    in any folder."""
    g = os.path.expanduser(glob)
    if not g.startswith("/"):
        return None
    spots: list[tuple[str, str | None]] = []
    for v in _glob_variants(g):
        parts = v.split("/")
        for i, part in enumerate(parts):
            if _GLOB_MAGIC.search(part):
                spots.append((os.path.normpath("/".join(parts[:i]) or "/"),
                              part))
                break
        else:
            spots.append((os.path.normpath(v), None))
    return spots


def _reaches(root: str, folder: str, name: str | None) -> bool:
    """True when a search of ``root`` reads something at the protected
    spot ``(folder, name)``: ``root`` is ``folder`` or holds it, or lies
    inside it (inside an entry matching ``name``, when there is one)."""
    if _under(folder, root):
        return True  # the root is the folder or a folder above it
    if not _under(root, folder):
        return False
    if name is None or "{" in name:
        # A literal protected path, or a brace pattern fnmatch cannot
        # read (``/srv/{a,b}/**``): everything in the folder counts.
        return True
    rel = _fold(root)[len(_fold(folder).rstrip("/")):].lstrip("/")
    return fnmatch.fnmatchcase(rel.split("/")[0], _fold(name))


def _unconfined_search_violation(
    tool_name: str, tool_input: dict, no_access: tuple[str, ...],
    cwd: str | None,
) -> str | None:
    """An unconfined turn's Grep/Glob (an admin, or a custom role with
    ``bypass_role_denies``; roadmap 8.61). Owners never get here
    (``enforce``'s short-circuit). The search may go anywhere except
    into aipager's protected paths: it is denied when a search root is a
    protected folder, lies inside one, or holds one (``Grep ~`` holds
    ``~/.config/aipager``). The roots are ``path`` (else the payload
    ``cwd``) and, for a Glob, the folder its pattern names (Claude Code
    takes the search folder from an absolute pattern; for one with no
    wildcard, that is its parent folder, :func:`_pattern_dirs`). An unanchored protected glob (``**/.env``) can match in any
    folder, so it denies every search. Odd input denies (fail closed)."""
    root = _js_trim(tool_input.get("path")) or cwd
    if not isinstance(root, str) or not root or "\x00" in root:
        return f"{tool_name} with an unreadable search folder"
    base = _norm(root, cwd)
    roots = {base, _realpath(base)}
    if tool_name == "Glob" and tool_input.get("pattern") is not None:
        pattern = tool_input["pattern"]
        if not isinstance(pattern, str) or "\x00" in pattern:
            return f"{tool_name} with an unreadable pattern"
        # The whole pattern, never its brace or comma pieces: Claude Code
        # takes the search folder from the whole pattern, and a piece of
        # ``{src,tests}/*.py`` would read as the folder ``/``.
        if pattern:
            roots |= _pattern_dirs(pattern, base)
    for glob in no_access:
        spots = _protected_spots(glob)
        if spots is None:
            return (f"{tool_name} denied - the protected path {glob} can be "
                    "in any folder, so this role cannot search with Grep "
                    "or Glob")
        if any(_reaches(r, folder, name)
               for r in roots for folder, name in spots):
            shown = glob[:-3] if glob.endswith("/**") else glob
            return (f"{tool_name} denied - this search would reach "
                    f"protected files ({shown}); search inside a project "
                    "folder instead")
    return None


def _confinement_violation(tool_name: str, real: str,
                           write_roots: tuple[str, ...],
                           wide_cwd: bool = False) -> str | None:
    """A write that is not inside one of ``write_roots``, or that lands
    on a file which makes Claude Code or git run a command
    (:data:`_NEVER_WRITE_DIRS`, :data:`_NEVER_WRITE_FILES`). ``real`` is
    where the path really leads, so a symlink in the project pointing
    out of it is outside."""
    for root in write_roots:
        root_real = _realpath(_norm(root))
        if not _under(real, root_real):
            continue
        rel = _fold(real)[len(_fold(root_real).rstrip("/")):].strip("/")
        parts = rel.split("/") if rel else []
        if any(p in _NEVER_WRITE_DIRS for p in parts[:-1]) or (
            parts and (parts[-1] in _NEVER_WRITE_FILES
                       or parts[-1] in _NEVER_WRITE_DIRS)
        ):
            return (f"{tool_name} to a file that runs commands "
                    "(.claude/, .git/, .mcp.json)")
        return None
    if wide_cwd:
        return _wide_cwd_reason(tool_name, "write")
    return (f"{tool_name} outside the session's folder - a restricted turn "
            "writes only in its project and scratchpad")


def path_violation(
    tool_name: str, tool_input: dict,
    no_access: tuple[str, ...], no_write: tuple[str, ...],
    *,
    cwd: str | None = None,
    write_roots: tuple[str, ...] | None = None,
    readable: tuple[str, ...] = (),
    wide_cwd: bool = False,
) -> str | None:
    """Reason string if this tool touches a protected path, else None.

    - ``cwd``: the session's working directory, from the hook payload —
      never from the tool input. Relative paths and a Grep/Glob with no
      ``path`` resolve against it.
    - ``write_roots``: when given, a write tool may only land under one
      of these (the session's cwd and scratchpad) — roadmap 8.50 — and a
      Grep/Glob must pass the allow-list in :func:`_search_violation`.
      ``None`` means unconfined (admin): a Grep/Glob may then search
      anywhere that does not reach a protected path
      (:func:`_unconfined_search_violation`, roadmap 8.61).
    - ``readable``: exact files the Read tool may open even though a
      no-access glob covers them (the session's own reply file).
    - ``wide_cwd``: the session runs in the home folder or above it, so
      ``write_roots`` left its folder out (roadmap 8.79); a write or
      search outside the roots then says so in its deny reason.
    """
    key = _PATH_KEYS.get(tool_name)
    if not key:
        return None
    tool_input = tool_input or {}
    if tool_name in _SEARCH_TOOLS:
        if write_roots is not None:
            return _search_violation(tool_name, tool_input, no_access, cwd,
                                     write_roots, wide_cwd)
        return _unconfined_search_violation(tool_name, tool_input,
                                            no_access, cwd)
    raw = _js_trim(tool_input.get(key))
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or "\x00" in raw:
        return f"{tool_name} with an unreadable path"  # fail closed
    spelled = _norm(raw, cwd)
    real = _realpath(spelled)
    targets = {spelled, real}
    exempt = tool_name == "Read" and any(
        _fold(real) == _fold(_realpath(_norm(r))) for r in readable)
    if not exempt:
        for glob in no_access:
            if _matches_targets(targets, glob):
                return f"{tool_name} on protected path {glob}"
    if tool_name in _WRITE_TOOLS:
        for glob in no_write:
            if _matches_targets(targets, glob):
                return f"{tool_name} write to protected path {glob}"
        if write_roots is not None:
            return _confinement_violation(tool_name, real, write_roots,
                                          wide_cwd)
    return None


# A session's own scratchpad root: Claude Code gives every session a
# directory under ``/tmp/claude-<uid>/<project>/<session>/scratchpad`` and
# points it there, so ordinary commands carry the path. The built-in
# ``\bclaude\b`` pattern matched its ``claude`` and halted them (roadmap
# 8.48). Only this exact root is exempted: ``/tmp/claude-`` + digits, as a
# whole path component (followed by ``/`` or the end of the word). That
# root is the OS user's, not the session's: it holds every session's
# scratchpad and task output for that user, so a turn that has Bash (an
# owner-trusted role; the built-in restricted roles have none) may read
# those, as the Read tool can. A glob
# (``/tmp/claude-1*``), a look-alike (``claude-1000x``, ``claude-dtach-…``,
# aipager's ``claude-policy-…``), another tmp dir, or a relative spelling
# does not qualify and still meets the full pattern set; so does anything
# AFTER the root (``…/../../home/u/.claude`` still contains ``.claude``).
_SCRATCHPAD_ROOT_RE = re.compile(
    r"(?<![\w.~/-])/tmp/claude-\d+(?=/|[\s;&|()<>'\"`]|$)"
)
_SCRATCHPAD_PLACEHOLDER = "/tmp/session-scratchpad"


def normalize_scratchpad(command: str) -> str:
    """``command`` with every scratchpad root replaced by a neutral
    placeholder. Used only for the built-in deny patterns — see
    :func:`bash_violation`."""
    return _SCRATCHPAD_ROOT_RE.sub(_SCRATCHPAD_PLACEHOLDER, command)


#: The deny reason for a Bash command that matched a pattern.
BASH_REASON = "Bash command blocked by safety policy"
#: The same for a recursive search of the whole home folder or ``/``
#: (:data:`DENY_BASH_SEARCH_PATTERNS`): it says what to do instead, and
#: names the rule in words, never the regex.
BASH_SEARCH_REASON = (
    f"{BASH_REASON}: a recursive search or listing of the whole home folder "
    "or / is not allowed; search inside a project folder instead")


def bash_violation(command: str, patterns: tuple[str, ...]) -> str | None:
    """Reason string if a Bash command matches a deny pattern, else None.

    The reason is intentionally generic — it must NOT echo the matched
    regex. Returning the pattern (e.g. ``/\\bclaude\\b/``) hands an agent
    the exact filter to reverse-engineer a dodge (observed: a glob
    ``cla*-code`` read the same file). The matched pattern is still logged
    server-side for the operator.

    The built-in patterns (:data:`DENY_BASH_PATTERNS`) are matched against
    the command with the session's scratchpad root normalised away
    (:func:`normalize_scratchpad`); any other pattern — an operator's own
    ``deny_bash_patterns`` from policy.yaml — sees the command exactly as
    typed, so a rule written against the scratchpad still applies.
    """
    if not command:
        return None
    normalized = normalize_scratchpad(command)
    for pat in patterns:
        subject = normalized if pat in DENY_BASH_PATTERNS else command
        try:
            if re.search(pat, subject):
                log.info("bash_violation: command blocked by pattern %r", pat)
                if pat in DENY_BASH_SEARCH_PATTERNS:
                    return BASH_SEARCH_REASON
                return BASH_REASON
        except re.error:
            continue
    return None


def tool_violation(
    tool_name: str, deny_tools: tuple[str, ...], allow_tools: tuple[str, ...],
) -> str | None:
    """Reason if the tool is denied / not in a non-empty allowlist."""
    if allow_tools and tool_name not in allow_tools:
        return f"{tool_name} not in this role's allow_tools"
    if tool_name in deny_tools:
        return f"{tool_name} is in this role's deny_tools"
    return None
