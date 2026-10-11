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
from aipager import safety
from aipager.dtach import inject
from aipager.dtach.inject import _RESERVED, _VALID_NAME, normalize_session_name
from aipager.i18n import Command, Msg
from aipager.miniapp.phrase import Phrase

# Every refusal below is a str equal to its English (a Msg, or a Phrase
# with its parameters), so the chat's callers keep testing ``if err:``
# and showing it as they always did, while the Mini App renders it in its
# viewer's language (``phrase.say``).

# Roadmap 8.79. A role whose writes are confined to the session's folder
# (``user``, ``read_only``, anyone without the owner's or an admin's
# bypass) may not start a session in ``/``, the home folder or a folder
# above it: there the "project" would be the whole home folder. Those
# folders stay usable as the place to create a new project folder in.
NO_PROJECT_FOLDER = Msg(
    "app.launch.no_project_folder",
    "Pick a project folder: create one with New folder, or ask an admin to start the session.",
    note="A person whose role is confined may not start a session there. "
         "New folder is the Mini App's button that creates a folder")
WIDE_FOLDER_REFUSAL = Msg(
    "app.launch.wide_folder",
    "Your role can't start a session in the home folder or a folder above it. "
    "Pick a project folder: create one with New folder, or ask an admin to start the session.",
    note="New folder is the Mini App's button that creates a folder")
# When there is no folder to create one in either (the daemon runs in `/`
# and the chat has never used a folder).
NO_FOLDER_AT_ALL = Msg(
    "app.launch.no_folder_at_all",
    "There is no project folder to start in yet: ask an admin to start the session.",
    note="Nowhere to start a session and nowhere to create a folder")

_NAME_NOT_TEXT = Msg("app.launch.name_not_text", "Session name must be text.",
                     note="The new session's name was not text")
_NAME_EMPTY = Msg("app.launch.name_empty", "Session name can't be empty.")
_NAME_TOO_LONG = Msg("app.launch.name_too_long",
                     "Session name must be {max} characters or fewer.", eg={"max": 64})
_NAME_BAD_CHAR = Msg("app.launch.name_bad_char", "Session name contains an invalid character.")
_NAME_PATTERN = Msg("app.launch.name_pattern",
                    "Use letters, numbers, hyphens and underscores; start with a letter or "
                    "number.", note="What a session name may hold (Latin letters)")
_NAME_RESERVED = Msg("app.launch.name_reserved",
                     "'{name}' is a reserved command name ({command}). Pick another name.",
                     eg={"name": "status", "command": "/status"},
                     note="The name is a bot command, which a session name may not be")
_CWD_NOT_TEXT = Msg("app.launch.cwd_not_text", "Working directory must be text.")
_CWD_BAD_CHAR = Msg("app.launch.cwd_bad_char",
                    "Working directory contains an invalid character.")
_CWD_NO_ROOTS = Msg("app.launch.cwd_no_roots",
                    "No directory is available yet - start a session from chat first, "
                    "then this picker will offer its project.",
                    note="The Mini App's folder picker has nothing to offer yet")
_CWD_UNRESOLVABLE = Msg("app.launch.cwd_unresolvable", "That directory can't be resolved.")
_CWD_NOT_DIR = Msg("app.launch.cwd_not_dir", "That path isn't a directory.")
_CWD_NOT_ALLOWED = Msg("app.launch.cwd_not_allowed",
                       "That directory isn't one this chat already works in.")
_FOLDER_NOT_TEXT = Msg("app.launch.folder_not_text", "Folder name must be text.")
_FOLDER_EMPTY = Msg("app.launch.folder_empty", "Folder name can't be empty.")
_FOLDER_TOO_LONG = Msg("app.launch.folder_too_long",
                       "Folder name must be {max} characters or fewer.", eg={"max": 64})
_FOLDER_BAD_CHAR = Msg("app.launch.folder_bad_char",
                       "Folder name contains an invalid character.")
_FOLDER_PATTERN = Msg("app.launch.folder_pattern",
                      "Use letters, numbers, dots, hyphens and underscores; start with a "
                      "letter or number.", note="What a new folder's name may hold")
_FOLDER_PARENT = Msg("app.launch.folder_parent",
                     "Pick a working directory to create the folder in.")
_FOLDER_LOADS_CODE = Msg("app.launch.folder_loads_code",
                         "'{name}' can't be a project folder here: other programs load "
                         "code from it. Pick another name.",
                         eg={"name": "node_modules"},
                         note="A folder name programs read code from (node_modules, bin)")
_FOLDER_FAILED = Msg("app.launch.folder_failed", "Couldn't create that folder.")
_FOLDER_EXISTS = Msg("app.launch.folder_exists",
                     "A folder with that name already exists there. Pick a new name.")
_MODEL_NOT_TEXT = Msg("app.launch.model_not_text", "Model must be text.")
_MODEL_TOO_LONG = Msg("app.launch.model_too_long",
                      "Model name must be {max} characters or fewer.", eg={"max": 64})
_MODEL_PATTERN = Msg("app.launch.model_pattern",
                     "Use one of the listed models, or a full model name like {example}.",
                     eg={"example": "claude-opus-5"},
                     note="example is a model's full name, written as it is")
#: The model name the refusal shows as an example (a name: never translated).
_MODEL_EXAMPLE = "claude-opus-5"

# Names a confined person may not create directly inside a wide folder:
# programs the operator runs load code from them without being asked.
# Node looks for packages in every node_modules up to the filesystem root,
# so ~/node_modules is searched by each project under the home folder;
# ~/bin is on PATH (Debian/Ubuntu ~/.profile) once it exists.
_NO_NEW_IN_WIDE = frozenset({"node_modules", "bin"})


def is_wide_folder(path: str) -> bool:
    """``/``, the home folder or a folder holding it (see
    :func:`aipager.safety.is_wide_folder`)."""
    return safety.is_wide_folder(path)


def default_folder_is_wide() -> bool:
    """True when a session started with no folder (the daemon's own
    directory, ``inject._PROJECT_DIR``) would run in a wide folder."""
    return is_wide_folder(getattr(inject, "_PROJECT_DIR", "") or os.sep)


# Folders a new-folder request really created, per chat (in memory, at
# most _MAX_CREATED each). A confined person's new ``~/myproject`` sits in
# the home folder, which is not one of their launch roots, so without this
# the Mini App would refuse to start in the folder it just made. Only a
# folder created here counts, never one that already existed.
_MAX_CREATED = 64
_created: dict[int, list[str]] = {}


def remember_created(scope_chat_id: int, path: str) -> None:
    """Record a folder :func:`create_directory` created for this chat."""
    paths = _created.setdefault(scope_chat_id, [])
    if path not in paths:
        paths.append(path)
        del paths[:-_MAX_CREATED]


def move_created(old_chat_id: int, new_chat_id: int) -> None:
    """Telegram upgraded the group *old_chat_id* to a supergroup with a
    new id (roadmap 8.87): the folders created for it are the new chat's
    now (the new chat's own come last, as the newest), still at most
    _MAX_CREATED."""
    moved = _created.pop(old_chat_id, None)
    if not moved:
        return
    paths = list(moved)
    for path in _created.get(new_chat_id, ()):
        if path in paths:
            paths.remove(path)
        paths.append(path)
    _created[new_chat_id] = paths[-_MAX_CREATED:]


def launch_folder_refusal(cwd: str | None, confined: bool) -> str:
    """Why *cwd* (empty: the daemon's own directory) may not be a
    confined person's session folder, or ``""``."""
    if not confined:
        return ""
    folder = cwd or getattr(inject, "_PROJECT_DIR", "") or os.sep
    return WIDE_FOLDER_REFUSAL if is_wide_folder(folder) else ""

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
# the admin gate on Full access mode. `shlex.quote` does not help with that at
# all — it makes a value shell-safe, not argv-safe.
#
# `:` is allowed for Bedrock/Vertex-style ids (`...-v1:0`). One exact
# trailing `[1m]` is allowed too: it is Claude Code's own 1M-context
# suffix (`claude-opus-5-5[1m]`, `opus[1m]`). Only that literal — `[` and
# `]` are glob characters, so the allow-list stays as narrow as the one
# form Claude Code documents rather than admitting brackets in general.
MAX_MODEL_LENGTH = 64
_VALID_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*(?:\[1m\])?$")


def validate_session_name(name: object) -> tuple[str, str]:
    """Return ``(clean_name, "")`` or ``("", reason)``.

    Applies dtach's own `_VALID_NAME` / `_RESERVED` rules up front so the
    Mini App rejects with a clear 400 instead of letting the launch fail
    deeper in with a less useful message.
    """
    if not isinstance(name, str):
        return "", _NAME_NOT_TEXT
    # Normalised BEFORE every check below, so the reserved-word and
    # duplicate rules see the name the session will really have — and so
    # the caller is handed that name rather than the one typed.
    clean = normalize_session_name(name)
    if not clean:
        return "", _NAME_EMPTY
    if len(clean) > MAX_NAME_LENGTH:
        return "", Phrase(_NAME_TOO_LONG, max=MAX_NAME_LENGTH)
    if "\x00" in clean:
        return "", _NAME_BAD_CHAR
    if not _VALID_NAME.match(clean):
        return "", _NAME_PATTERN
    if clean.lower() in _RESERVED:
        return "", Phrase(_NAME_RESERVED, name=clean, command=Command("/" + clean.lower()))
    return clean, ""


def allowed_roots(registry, scope_chat_id: int, *,
                  confined: bool = False, root: str | None = None) -> list[str]:
    """Directories a new session may be launched in, for this scope.

    ``root`` (a restricted member's root folder,
    ``member_root.restricted_root``): only that folder and its immediate
    subfolders (``member_root.folder_choices``, which creates the root),
    whatever this chat's sessions use; ``[]`` when there is none. The
    containment checks in :func:`validate_cwd` and
    :func:`create_directory` then hold the person to it.

    ``confined`` (the caller's turns are confined to the session's folder,
    roadmap 8.79): ``/``, the home folder and the folders above it are
    left out. Callers still pass the full list (``confined=False``) as
    the parents a new folder may be created in, so such a person can make
    ``~/myproject`` and start there.

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
    if root is not None:
        from aipager import member_root  # local: it imports this module lazily
        return member_root.folder_choices(root)
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
    if confined:
        for path in _created.get(scope_chat_id, ()):
            add(path)
        roots = [r for r in roots if not is_wide_folder(r)]
    return roots


def validate_cwd(candidate: object, roots: list[str], *,
                 confined: bool = False) -> tuple[str, str]:
    """Return ``(real_path, "")`` or ``("", reason)``.

    ``candidate`` may be empty, meaning "the daemon's own directory" —
    the same default `launch_session(cwd=None)` already applies, so an
    operator who ignores the picker gets today's behaviour.

    Anything else must resolve — **after** ``realpath``, so symlinks and
    ``..`` are collapsed before the check rather than after — to a
    directory at or beneath one of ``roots``. The comparison is done on
    path components, not string prefixes: a plain ``startswith`` would
    accept ``/home/aly/aipager-evil`` for the root ``/home/aly/aipager``.

    ``confined``: neither the result nor, for an empty candidate, the
    daemon's own directory may be ``/``, the home folder or a folder above
    it (roadmap 8.79).
    """
    if candidate is None or candidate == "":
        if confined and default_folder_is_wide():
            return "", WIDE_FOLDER_REFUSAL
        return "", ""            # daemon default, same as chat's /new
    if not isinstance(candidate, str):
        return "", _CWD_NOT_TEXT
    if "\x00" in candidate:
        return "", _CWD_BAD_CHAR
    if not roots:
        return "", _CWD_NO_ROOTS

    try:
        real = os.path.realpath(candidate)
    except OSError:
        return "", _CWD_UNRESOLVABLE
    if not os.path.isdir(real):
        return "", _CWD_NOT_DIR
    if confined and is_wide_folder(real):
        return "", WIDE_FOLDER_REFUSAL

    real_parts = real.split(os.sep)
    for root in roots:
        root_parts = root.split(os.sep)
        if real_parts[: len(root_parts)] == root_parts:
            return real, ""
    return "", _CWD_NOT_ALLOWED


def validate_new_dir_name(name: object) -> tuple[str, str]:
    """Return ``(clean_leaf, "")`` or ``("", reason)``.

    One path segment only — see :data:`_VALID_DIR_NAME`. The caller joins
    this onto an already-validated parent, so this function's whole job
    is making sure the join cannot walk anywhere.
    """
    if not isinstance(name, str):
        return "", _FOLDER_NOT_TEXT
    clean = name.strip()
    if not clean:
        return "", _FOLDER_EMPTY
    if len(clean) > MAX_DIR_NAME_LENGTH:
        return "", Phrase(_FOLDER_TOO_LONG, max=MAX_DIR_NAME_LENGTH)
    if "\x00" in clean:
        return "", _FOLDER_BAD_CHAR
    if not _VALID_DIR_NAME.match(clean):
        return "", _FOLDER_PATTERN
    return clean, ""


def create_directory(
    parent: object, name: object, roots: list[str], *,
    confined: bool = False,
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

    ``confined`` (roadmap 8.79): ``roots`` are still the full list, so
    the home folder can be the parent, but the result must not be ``/``,
    the home folder or a folder above it, and a folder that already
    exists directly in such a parent is not reused: it is not a project
    this person made (``~/bin`` is on the operator's PATH).
    """
    real_parent, err = validate_cwd(parent, roots)
    if err:
        return "", False, err
    if not real_parent:
        # validate_cwd's "" means "the daemon's default", which is a
        # launch behaviour, not a directory to create inside.
        return "", False, _FOLDER_PARENT

    clean, err = validate_new_dir_name(name)
    if err:
        return "", False, err

    target = os.path.join(real_parent, clean)
    wide_parent = confined and is_wide_folder(real_parent)
    if wide_parent and clean.lower() in _NO_NEW_IN_WIDE:
        return "", False, Phrase(_FOLDER_LOADS_CODE, name=clean)
    existed = False
    try:
        os.mkdir(target)
    except FileExistsError:
        existed = True
    except OSError:
        return "", False, _FOLDER_FAILED
    if existed and wide_parent:
        return "", False, _FOLDER_EXISTS

    real, err = validate_cwd(target, roots, confined=confined)
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
        return "", _MODEL_NOT_TEXT
    clean = value.strip()
    if not clean:
        return "", ""

    resolved = clean
    for label, command in choices:
        if isinstance(label, str) and clean.lower() == label.strip().lower():
            resolved = str(command).split(None, 1)[-1].strip()
            break

    if len(resolved) > MAX_MODEL_LENGTH:
        return "", Phrase(_MODEL_TOO_LONG, max=MAX_MODEL_LENGTH)
    if not _VALID_MODEL.match(resolved):
        return "", Phrase(_MODEL_PATTERN, example=_MODEL_EXAMPLE)
    return resolved, ""


__all__ = [
    "MAX_DIR_NAME_LENGTH",
    "MAX_MODEL_LENGTH",
    "MAX_NAME_LENGTH",
    "NO_FOLDER_AT_ALL",
    "NO_PROJECT_FOLDER",
    "WIDE_FOLDER_REFUSAL",
    "allowed_roots",
    "create_directory",
    "default_folder_is_wide",
    "is_wide_folder",
    "launch_folder_refusal",
    "validate_cwd",
    "validate_model",
    "validate_new_dir_name",
    "validate_session_name",
]
