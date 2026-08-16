"""Validation for Mini App session creation.

Pure functions, no I/O beyond the filesystem checks a directory
validation inherently needs. Split out from ``server.py`` so the rules
that decide *what may be executed and where* are unit-testable without a
running server — this is the only place in aipager where an HTTP request
can lead to a process spawn, so the rules deserve their own tests rather
than living inside a request handler.

The two functions here are deliberately conservative: both return a
``(value, error)`` pair and never raise, so a handler cannot accidentally
turn a validation bug into a 500 that leaks a stack trace.
"""

from __future__ import annotations

import os

# Reuse dtach's own name rules rather than inventing a second, looser
# set: the name becomes a dtach socket filename and a registry key, and
# `launch_session` will reject anything these don't allow anyway. A
# second regex here could only ever disagree with the one that matters.
from aipager.dtach.inject import _RESERVED, _VALID_NAME

# A label longer than this is not a real workstream name; it is someone
# probing for a buffer to overflow or a filename to blow up.
MAX_NAME_LENGTH = 64


def validate_session_name(name: object) -> tuple[str, str]:
    """Return ``(clean_name, "")`` or ``("", reason)``.

    Applies dtach's own `_VALID_NAME` / `_RESERVED` rules up front so the
    Mini App rejects with a clear 400 instead of letting the launch fail
    deeper in with a less useful message.
    """
    if not isinstance(name, str):
        return "", "Session name must be text."
    clean = name.strip()
    if not clean:
        return "", "Session name can't be empty."
    if len(clean) > MAX_NAME_LENGTH:
        return "", f"Session name must be {MAX_NAME_LENGTH} characters or fewer."
    if "\x00" in clean:
        return "", "Session name contains an invalid character."
    if not _VALID_NAME.match(clean):
        return "", "Use letters, numbers, hyphens and underscores; start with a letter or number."
    if clean.lower() in _RESERVED:
        return "", f"'{clean}' is a reserved command name."
    return clean, ""


def allowed_roots(registry, scope_chat_id: int) -> list[str]:
    """Directories a new session may be launched in, for this scope.

    Seeded from the working directories sessions in this scope already
    use — the operator has demonstrably run Claude there, so it is not a
    new grant of reach. Deliberately NOT "anywhere on the filesystem":
    this list is the allow-list the tunnel-reachable create route is
    checked against, and a free-text path box would make the Mini App a
    remote "run a process in any directory" primitive.

    GONE sessions still count: their directory is a project the operator
    works in, and excluding them would mean the picker forgets a project
    the moment its last session is cleaned up.
    """
    roots: list[str] = []
    seen: set[str] = set()
    for sess in registry.all_sessions(scope_chat_id).values():
        cwd = getattr(sess, "cwd", "") or ""
        if not cwd:
            continue
        try:
            real = os.path.realpath(cwd)
        except OSError:
            continue
        if real not in seen and os.path.isdir(real):
            seen.add(real)
            roots.append(real)
    return roots


def validate_cwd(candidate: object, roots: list[str]) -> tuple[str, str]:
    """Return ``(real_path, "")`` or ``("", reason)``.

    ``candidate`` may be empty, meaning "the daemon's own directory" —
    the same default `launch_session(cwd=None)` already applies, so an
    operator who ignores the picker gets today's behaviour.

    Anything else must resolve — **after** ``realpath``, so symlinks and
    ``..`` are collapsed before the check rather than after — to a
    directory at or beneath one of ``roots``. The comparison is done on
    path components, not string prefixes: a plain ``startswith`` would
    accept ``/home/aly/aipager-evil`` for the root ``/home/aly/aipager``.
    """
    if candidate is None or candidate == "":
        return "", ""            # daemon default, same as chat's /new
    if not isinstance(candidate, str):
        return "", "Working directory must be text."
    if "\x00" in candidate:
        return "", "Working directory contains an invalid character."
    if not roots:
        return "", (
            "No directory is available yet — start a session from chat first, "
            "then this picker will offer its project."
        )

    try:
        real = os.path.realpath(candidate)
    except OSError:
        return "", "That directory can't be resolved."
    if not os.path.isdir(real):
        return "", "That path isn't a directory."

    real_parts = real.split(os.sep)
    for root in roots:
        root_parts = root.split(os.sep)
        if real_parts[: len(root_parts)] == root_parts:
            return real, ""
    return "", "That directory isn't one this chat already works in."


__all__ = [
    "MAX_NAME_LENGTH",
    "allowed_roots",
    "validate_cwd",
    "validate_session_name",
]
