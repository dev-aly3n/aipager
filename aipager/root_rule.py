"""What may be a members' root folder (``aipager.yaml``'s per-scope
``root``; see :mod:`aipager.member_root`).

Shared by the ``aipager.yaml`` parser (:mod:`aipager.scope`) and the
wizard, so the file and ``aipager config`` refuse the same folders. In
the hook's import closure through ``scope``: imports nothing but
:mod:`aipager.safety`, and nothing here is translated (the wizard and the
parser speak English).
"""

from __future__ import annotations

import os

from aipager import safety

_MAGIC = set("*?[]{}")

#: What ``aipager uninstall`` says ``~/aipager-users`` holds when it lists
#: the folders it keeps (English, like the rest of the command line).
USERS_KEPT = "members' root folders, with their work"


def _claude_temp_globs() -> tuple[str, ...]:
    """Claude Code's per-user temp folders (``<temp dir>/claude-<uid>``,
    every session's scratchpad inside): ``/tmp/claude-*``, and the same
    under ``$CLAUDE_CODE_TMPDIR`` and ``$TMPDIR`` when they are set. A
    root there would put a member's work beside every session's scratch
    files, and the hook lets a turn write in its scratchpad."""
    out = ["/tmp/claude-*"]
    for var in ("CLAUDE_CODE_TMPDIR", "TMPDIR"):
        base = (os.environ.get(var) or "").rstrip("/")
        if base and os.path.isabs(base) and "\x00" not in base \
                and not (_MAGIC & set(base)):
            out.append(f"{base}/claude-*")
    return tuple(dict.fromkeys(out))


def root_problem(path) -> str | None:
    """Why *path* cannot be a members' root folder, or ``None``. A folder
    that does not exist yet is fine.

    Refused: not text, empty, a NUL byte, not absolute after ``~``,
    wildcard characters, ``/``, the home folder or a folder above it, and
    a folder that is, lies inside or holds one of aipager's protected
    paths or the credential files (``~/.claude``, ``~/.config/aipager``,
    ``~/.ssh``...): a member's turn may write in its root. Claude Code's
    temp folder (``/tmp/claude-*``) too: it holds every session's
    scratchpad."""
    if not isinstance(path, str) or not path.strip():
        return "it is empty"
    if "\x00" in path:
        return "it holds a NUL byte"
    expanded = os.path.expanduser(path.strip())
    if not os.path.isabs(expanded):
        return "it is not an absolute path (start it with / or ~/)"
    if _MAGIC & set(expanded):
        return "it holds a wildcard character (* ? [ ] { })"
    spelled = os.path.abspath(expanded)
    if safety.is_wide_folder(spelled):
        return "it is /, the home folder or a folder above the home folder"
    forms = {spelled, safety._realpath(spelled)}
    for form in forms:
        for glob in (*safety.DENY_PATHS_NO_ACCESS, *safety.CREDENTIAL_PATHS):
            spots = safety._protected_spots(glob)
            if spots is None:
                continue
            if any(safety._reaches(form, folder, name) for folder, name in spots):
                shown = glob[:-3] if glob.endswith("/**") else glob
                return f"it is, holds or lies inside the protected path {shown}"
    for form in forms:
        for glob in _claude_temp_globs():
            spots = safety._protected_spots(glob) or ()
            if any(safety._reaches(form, folder, name) for folder, name in spots):
                return ("it is, holds or lies inside Claude Code's temp "
                        f"folder {glob}, which holds every session's scratchpad")
    return None


def parse_scope_root(value, index: int, error):
    """``scopes[index].root`` as read from ``aipager.yaml``: ``None`` when
    absent, else the folder ``~``-expanded and absolute. Raises *error*
    (the parser's own exception class) for anything :func:`root_problem`
    refuses, naming the field."""
    if value is None:
        return None
    where = f"aipager.yaml: scopes[{index}].root"
    if not isinstance(value, str):
        raise error(f"{where} must be text")
    problem = root_problem(value)
    if problem:
        raise error(f"{where}: {problem}")
    return os.path.abspath(os.path.expanduser(value.strip()))
