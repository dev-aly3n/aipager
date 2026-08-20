"""Validation for Mini App session creation.

Split out from ``server.py`` so the rules that decide *what may be
executed and where* are unit-testable without a running server — this is
the only place in aipager where an HTTP request can lead to a process
spawn, so the rules deserve their own tests rather than living inside a
request handler.

Everything here is pure apart from the filesystem checks a directory
validation inherently needs, plus the one ``mkdir`` in
:func:`create_directory` — which lives here rather than in the handler
precisely because the check that contains it is here.

Every function is deliberately conservative: each returns a
``(value, ..., error)`` tuple and never raises, so a handler cannot
accidentally turn a validation bug into a 500 that leaks a stack trace.
"""

from __future__ import annotations

import os
import re

# Reuse dtach's own name rules rather than inventing a second, looser
# set: the name becomes a dtach socket filename and a registry key, and
# `launch_session` will reject anything these don't allow anyway. A
# second regex here could only ever disagree with the one that matters.
from aipager.dtach import inject
from aipager.dtach.inject import _RESERVED, _VALID_NAME, normalize_session_name

# A label longer than this is not a real workstream name; it is someone
# probing for a buffer to overflow or a filename to blow up.
MAX_NAME_LENGTH = 64

# A new folder's leaf name. One path segment, nothing else: the regex
# rejects `/`, `\`, `..`, `.`, a leading dash and every shell
# metacharacter by construction, because it is an allow-list rather than
# a list of things to strip. Rejecting beats sanitising here — a folder
# silently created under a different name than the operator typed is a
# worse outcome than an error they can read.
MAX_DIR_NAME_LENGTH = 64
_VALID_DIR_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# `claude --model` takes "an alias for the latest model (e.g. 'fable',
# 'opus', or 'sonnet') or a model's full name (e.g. 'claude-fable-5')",
# so the set cannot be enumerated — but it can be constrained.
#
# The first character MUST be alphanumeric, and that is the load-bearing
# part: the value becomes claude's own argv, where a token starting with
# `-` is parsed as another FLAG rather than as this flag's value.
# `{"model": "--dangerously-skip-permissions"}` would then be a way past
# the admin gate on Auto mode. `shlex.quote` does not help with that at
# all — it makes a value shell-safe, not argv-safe.
#
# `:` is allowed for Bedrock/Vertex-style ids (`...-v1:0`).
MAX_MODEL_LENGTH = 64
_VALID_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def validate_session_name(name: object) -> tuple[str, str]:
    """Return ``(clean_name, "")`` or ``("", reason)``.

    Applies dtach's own `_VALID_NAME` / `_RESERVED` rules up front so the
    Mini App rejects with a clear 400 instead of letting the launch fail
    deeper in with a less useful message.
    """
    if not isinstance(name, str):
        return "", "Session name must be text."
    # Normalised BEFORE every check below, so the reserved-word and
    # duplicate rules see the name the session will really have — and so
    # the caller is handed that name rather than the one typed.
    clean = normalize_session_name(name)
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

    The daemon's own directory (``inject._PROJECT_DIR``) is appended last.
    For *launching*, that is no new reach: it is already where
    ``launch_session(cwd=None)`` puts every session that ignores the
    picker, so this only makes the existing default visible and
    selectable. For *creating a folder* it is a real, deliberate grant —
    that route is new, so a scope that has never run a session in the
    daemon's directory can now make a subdirectory there. The trade is
    worth it: without this, a fresh install has no roots at all, and both
    the picker and the folder button are dead UI on a new machine. The
    grant stays narrow — one mkdir, a name that must match
    :data:`_VALID_DIR_NAME`, behind the same authorization and rate limit
    as starting a session.

    ``/`` is the one exclusion: allow-listing the filesystem root would
    be exactly the free-text path box this list exists to refuse. A
    daemon started from ``$HOME`` does allow-list the home directory,
    which is a real consequence of where the operator started it — and
    still bounded by the permission prompts every non-Auto session asks
    for in chat.
    """
    roots: list[str] = []
    seen: set[str] = set()

    def add(cwd: str) -> None:
        if not cwd:
            return
        try:
            real = os.path.realpath(cwd)
        except OSError:
            return
        if real not in seen and os.path.isdir(real):
            seen.add(real)
            roots.append(real)

    for sess in registry.all_sessions(scope_chat_id).values():
        add(getattr(sess, "cwd", "") or "")

    # Read through the module, not a from-import, so the daemon's own
    # directory stays substitutable in tests.
    project_dir = getattr(inject, "_PROJECT_DIR", "") or ""
    try:
        is_fs_root = os.path.realpath(project_dir) == os.sep
    except OSError:
        is_fs_root = True
    if project_dir and not is_fs_root:
        add(project_dir)
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


def validate_new_dir_name(name: object) -> tuple[str, str]:
    """Return ``(clean_leaf, "")`` or ``("", reason)``.

    One path segment only — see :data:`_VALID_DIR_NAME`. The caller joins
    this onto an already-validated parent, so this function's whole job
    is making sure the join cannot walk anywhere.
    """
    if not isinstance(name, str):
        return "", "Folder name must be text."
    clean = name.strip()
    if not clean:
        return "", "Folder name can't be empty."
    if len(clean) > MAX_DIR_NAME_LENGTH:
        return "", f"Folder name must be {MAX_DIR_NAME_LENGTH} characters or fewer."
    if "\x00" in clean:
        return "", "Folder name contains an invalid character."
    if not _VALID_DIR_NAME.match(clean):
        return "", (
            "Use letters, numbers, dots, hyphens and underscores; "
            "start with a letter or number."
        )
    return clean, ""


def create_directory(
    parent: object, name: object, roots: list[str],
) -> tuple[str, bool, str]:
    """Create ``<parent>/<name>``. Returns ``(real_path, existed, error)``.

    ``parent`` must already be an allowed working directory — it goes
    through the unchanged :func:`validate_cwd`, so the folder can only
    appear at or beneath somewhere this scope already works. There is no
    "create anywhere" mode; that would make the Mini App a remote mkdir.

    An **existing** directory is reused rather than refused. The picker
    only lists *roots*, so a subdirectory that already exists is
    otherwise unreachable from the form — refusing it would leave the
    operator with a folder they can see the name of and no way to select
    it. An existing *file* of that name is still an error, caught by the
    ``isdir`` check inside the re-validation below.

    The created path is re-checked with :func:`validate_cwd` **after**
    the mkdir. That is not belt-and-braces for its own sake: a parent
    swapped for a symlink between the check and the create, or a leaf
    that already exists as a symlink pointing outside, both resolve
    outside the allow-list and are caught only by re-resolving the real
    thing that now exists.
    """
    real_parent, err = validate_cwd(parent, roots)
    if err:
        return "", False, err
    if not real_parent:
        # validate_cwd's "" means "the daemon's default", which is a
        # launch behaviour, not a directory to create inside.
        return "", False, "Pick a working directory to create the folder in."

    clean, err = validate_new_dir_name(name)
    if err:
        return "", False, err

    target = os.path.join(real_parent, clean)
    existed = False
    try:
        os.mkdir(target)
    except FileExistsError:
        existed = True
    except OSError:
        return "", False, "Couldn't create that folder."

    real, err = validate_cwd(target, roots)
    if err:
        return "", False, err
    return real, existed, ""


def validate_model(value: object, choices) -> tuple[str, str]:
    """Return ``(model_for_the_cli, "")`` or ``("", reason)``.

    ``choices`` is ``config.MODEL_CHOICES`` — ``(label, chat_command)``
    pairs. A label match resolves through the **command**, never by
    lowercasing the label: ``keyboard.json`` lets an operator map
    "Claude 4.5 Opus" to ``/model claude-opus-4-5``, and the shipped list
    hides the difference because every default label lowercases into its
    own alias.

    Anything that is not a label is taken as a full model name and
    checked against :data:`_VALID_MODEL`. An unrecognised value is an
    error, not a silent drop — launching with a different model than the
    operator picked is the kind of quiet lie that costs an hour to
    notice.
    """
    if value is None or value == "":
        return "", ""                      # leave the CLI on its own default
    if not isinstance(value, str):
        return "", "Model must be text."
    clean = value.strip()
    if not clean:
        return "", ""

    resolved = clean
    for label, command in choices:
        if isinstance(label, str) and clean.lower() == label.strip().lower():
            resolved = str(command).split(None, 1)[-1].strip()
            break

    if len(resolved) > MAX_MODEL_LENGTH:
        return "", f"Model name must be {MAX_MODEL_LENGTH} characters or fewer."
    if not _VALID_MODEL.match(resolved):
        return "", (
            "Use one of the listed models, or a full model name like "
            "claude-opus-5."
        )
    return resolved, ""


__all__ = [
    "MAX_DIR_NAME_LENGTH",
    "MAX_MODEL_LENGTH",
    "MAX_NAME_LENGTH",
    "allowed_roots",
    "create_directory",
    "validate_cwd",
    "validate_model",
    "validate_new_dir_name",
    "validate_session_name",
]
