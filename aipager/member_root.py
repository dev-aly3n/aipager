"""Members' root folders: where a restricted member's sessions live.

A member whose role in the chat they act in lacks ``can_manage`` (the
built-in ``user`` and ``read_only``, any custom role without it) works
only inside the root folder the owner gave that chat: a DM scope's root
is that person's, a group scope's root is shared by the group's
restricted members. The owner sets it in ``aipager config`` (the scope's
``root`` in ``aipager.yaml``); without one the default is
``~/aipager-users/<label>`` (``group-<label>`` for a group), created
(mode 0700) the first time the member opens a folder surface.

Two halves:

- launch side (this module): which folders a restricted member may pick,
  create and start in (the root and its immediate subfolders), and the
  refusals for starting, resuming, restarting or switching a session
  outside the root. Owner and admin, personal mode and legacy team mode
  are untouched.
- hook side: :func:`snapshot_bound` gives ``policy_snapshot`` the root
  and every other scope's root to deny, and the hook enforces them
  (``aipager.safety``, ``aipager.dtach.enforce``). The hook never imports
  this module.

Helpers take the bot as an argument (``scopes``, ``policy``,
``_scope_for``, ``_member_in_scope``), like ``new_flow``'s.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import NamedTuple

from aipager import root_rule, safety
from aipager._test_guard import check_write
from aipager.i18n import Msg
from aipager.miniapp.phrase import Phrase

#: A restricted person with no root to work in here (no scope for the
#: chat, not a member, or an unknown role): nowhere is inside it.
NOWHERE = "\x00nowhere"

#: The folder every default root lives in, under the home folder.
USERS_DIR = "aipager-users"

#: How many subfolders of the root the folder list shows.
FOLDER_CAP = 24

OUTSIDE_ROOT = Msg(
    "app.launch.outside_root",
    "Your role can only start sessions in your folder {folder} or a folder inside it.",
    note="A restricted member picked a folder outside the folder the owner gave them; "
         "{folder} is that folder's path", eg={"folder": "~/aipager-users/sara"})
SESSION_OUTSIDE_ROOT = Msg(
    "app.launch.session_outside_root",
    "This session works outside your folder {folder}, so your role can't resume, "
    "restart or switch it. Ask an admin.",
    note="A restricted member tried to resume, restart or change the mode of a session "
         "in another folder; {folder} is their own folder's path",
    eg={"folder": "~/aipager-users/sara"})
ROOT_UNAVAILABLE = Msg(
    "app.launch.root_unavailable",
    "Your folder {folder} could not be prepared. Ask the owner to check it in aipager config.",
    note="The member's folder could not be created; aipager config is a command and stays "
         "as it is", eg={"folder": "~/aipager-users/sara"})
NO_ROOT = Msg(
    "app.launch.no_root",
    "You have no folder to work in here. Ask the owner to add you in aipager config.",
    note="Someone who is not a member of this chat has no folder; aipager config is a "
         "command and stays as it is")


class RootBound(NamedTuple):
    """What a restricted turn's snapshot is held to: its ``root`` (real
    path) and the other scopes' roots it may never touch (``deny``)."""

    root: str
    deny: tuple[str, ...]


# ---- defaults ----------------------------------------------------------

_UNSAFE = re.compile(r"[^a-z0-9._-]")


def sanitise_label(label, fallback: str = "member") -> str:
    """*label* as one safe folder name: lowercase, anything outside
    ``a-z 0-9 . _ -`` becomes ``-``, runs of ``-`` collapse, no leading
    ``.``, ``-`` or ``_`` and no trailing ``-``, at most 48 characters.
    *fallback* when nothing is left."""
    s = _UNSAFE.sub("-", str(label or "").lower())
    s = re.sub(r"-+", "-", s).lstrip(".-_").rstrip("-")
    s = s[:48].rstrip("-")
    return s or fallback


def _idtag(chat_id: int) -> str:
    return str(chat_id) if chat_id >= 0 else f"n{abs(chat_id)}"


def _base_name(scope) -> str:
    if scope.kind == "dm" and scope.members:
        return sanitise_label(scope.members[0].label, "member")
    return "group-" + sanitise_label(scope.label, "group")


def users_dir() -> str:
    """``~/aipager-users``: where every default root lives. The home
    folder is read at call time, never at import."""
    return os.path.join(os.path.expanduser("~"), USERS_DIR)


def default_roots(scopes) -> dict[int, str]:
    """``{chat_id: default root}`` for every scope with no root set.

    A DM's is ``~/aipager-users/<member label>``, a group's
    ``~/aipager-users/group-<scope label>``, sanitised. When two of them
    would be the same folder (names that differ only in case count), or
    one would be a root another scope has set, each of those gets its
    chat id added (``<name>-<id>``, a negative id as ``n<digits>``), so
    two scopes never share a default. The home folder is read now, never
    at import."""
    scopes = list(scopes or ())
    users = users_dir()
    configured = {os.path.normpath(s.root) for s in scopes if s.root}
    base = {s.chat_id: _base_name(s) for s in scopes if not s.root}
    counts = Counter(b.casefold() for b in base.values())
    out: dict[int, str] = {}
    for chat_id, name in base.items():
        path = os.path.join(users, name)
        if counts[name.casefold()] > 1 or os.path.normpath(path) in configured:
            path = os.path.join(users, f"{name}-{_idtag(chat_id)}")
        out[chat_id] = path
    return out


def effective_root(scope, scopes) -> str:
    """*scope*'s root: the one set in aipager.yaml, else its default
    (not created here)."""
    if scope.root:
        return scope.root
    scopes = list(scopes or ())
    if all(s.chat_id != scope.chat_id for s in scopes):
        scopes.append(scope)
    return default_roots(scopes)[scope.chat_id]


def root_problem(path) -> str | None:
    """Why *path* cannot be a root (plain English), or ``None``. The rule
    lives in :func:`aipager.root_rule.root_problem`, which the
    aipager.yaml parser shares."""
    return root_rule.root_problem(path)


# ---- who is held to a root ---------------------------------------------

def is_restricted(role) -> bool:
    """A role without ``can_manage`` (no role at all counts)."""
    return role is None or getattr(role, "can_manage", False) is not True


def restricted_root(bot, user_id, chat_id) -> str | None:
    """The root *user_id* works in when acting in *chat_id*, as a real
    path; :data:`NOWHERE` when they are held to a root but have none here
    (no scope, not a member, an unknown role); ``None`` when they are not
    held to one (personal or legacy team mode, or a role with
    ``can_manage``)."""
    scopes = getattr(bot, "scopes", None)
    if scopes is None:
        return None
    scope = bot._scope_for(chat_id)
    member = (bot._member_in_scope(scope, user_id)
              if scope is not None and user_id is not None else None)
    role = bot.policy.get_role(member.role) if member is not None else None
    if not is_restricted(role):
        return None
    if scope is None or member is None or role is None:
        return NOWHERE
    return os.path.realpath(effective_root(scope, scopes))


def inside(path, root) -> bool:
    """*path* is *root* or inside it (symlinks resolved, whole
    components). Never true for :data:`NOWHERE` or an empty value."""
    if not isinstance(path, str) or not path or not isinstance(root, str) \
            or not root or "\x00" in path or "\x00" in root:
        return False
    return safety.under_root(path, root)


def short(path: str) -> str:
    """*path* with the home folder written ``~``."""
    home = os.path.expanduser("~").rstrip("/")
    if home and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


# ---- the folder itself -------------------------------------------------

def ensure_root(root) -> bool:
    """Create *root* (and the folders above it) if missing: mode 0700.
    True when it is a directory afterwards."""
    if not isinstance(root, str) or not root or root == NOWHERE or "\x00" in root:
        return False
    if root_problem(root):
        return False
    if os.path.isdir(root):
        return True
    check_write(root)   # never the operator's real home under pytest
    try:
        os.makedirs(root, mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        return False
    else:
        try:
            os.chmod(root, 0o700)   # the leaf, whatever the umask did
        except OSError:
            pass
    return os.path.isdir(root)


def folder_choices(root, cap: int = FOLDER_CAP) -> list[str]:
    """The folders a restricted member may pick: *root* (created first)
    and its real, non-hidden immediate subfolders that stay inside it
    (a link out of the root is left out), sorted by name ignoring case,
    at most *cap* of them. ``[]`` when there is no root to offer."""
    if not ensure_root(root):
        return []
    real_root = os.path.realpath(root)
    out = [real_root]
    try:
        entries = sorted(os.scandir(real_root), key=lambda e: e.name.casefold())
    except OSError:
        return out
    for entry in entries:
        if len(out) > cap:
            break
        if entry.name.startswith("."):
            continue
        try:
            real = os.path.realpath(entry.path)
        except OSError:
            continue
        if (real != real_root and real not in out and os.path.isdir(real)
                and inside(real, real_root)):
            out.append(real)
    return out


# ---- refusals ----------------------------------------------------------

def _folder(cwd) -> str:
    from aipager.dtach import inject  # local: the dtach side loads late
    return cwd or getattr(inject, "_PROJECT_DIR", "") or os.sep


def launch_refusal(bot, user_id, chat_id, cwd) -> str:
    """Why *user_id* may not START a session in *cwd* (empty: the daemon's
    own folder) from *chat_id*, or ``""``. A restricted person: only
    inside their root (created here if missing). Anyone else: the home
    folder rule as before (roadmap 8.79)."""
    root = restricted_root(bot, user_id, chat_id)
    if root is None:
        from aipager.miniapp import launch  # local: launch imports dtach
        return launch.launch_folder_refusal(
            cwd, bot._is_confined_user(user_id, chat_id))
    if root == NOWHERE:
        return NO_ROOT
    if not ensure_root(root):
        return Phrase(ROOT_UNAVAILABLE, folder=short(root))
    if inside(_folder(cwd), root):
        return ""
    return Phrase(OUTSIDE_ROOT, folder=short(root))


def relaunch_refusal(bot, user_id, chat_id, cwd) -> str:
    """Why *user_id* may not resume, restart or switch the mode of a
    session working in *cwd* (empty: the daemon's own folder), acting
    from *chat_id*, or ``""``. Only the root rule: for anyone not held to
    a root this is always ``""``."""
    root = restricted_root(bot, user_id, chat_id)
    if root is None:
        return ""
    if root == NOWHERE:
        return NO_ROOT
    if inside(_folder(cwd), root):
        return ""
    return Phrase(SESSION_OUTSIDE_ROOT, folder=short(root))


# ---- the hook's bound --------------------------------------------------

def snapshot_bound(role, scope, scopes) -> RootBound | None:
    """What ``policy_snapshot.resolve_snapshot`` holds a turn to, for a
    sender whose *role* comes from *scope*: ``None`` unless the role lacks
    ``can_manage`` and there is a scope. ``deny`` is every other scope's
    root that neither holds nor lies inside this one, as written and with
    symlinks resolved."""
    if scopes is None or scope is None or role is None or not is_restricted(role):
        return None
    scopes = list(scopes)
    root = os.path.realpath(effective_root(scope, scopes))
    defaults = default_roots(scopes)
    deny: set[str] = set()
    for other in scopes:
        if other.chat_id == scope.chat_id:
            continue
        path = other.root or defaults[other.chat_id]
        real = os.path.realpath(path)
        if safety._under(real, root) or safety._under(root, real):
            continue   # the same folder, or nested: given on purpose
        deny |= {os.path.normpath(path), real}
    return RootBound(root, tuple(sorted(deny)))


def as_html(tr, refusal) -> str:
    """A refusal from here (a :class:`Msg` or a :class:`Phrase`) as HTML
    in *tr*'s language, its folder escaped."""
    if isinstance(refusal, Phrase):
        return tr.html(refusal.msg, **refusal.params)
    if isinstance(refusal, Msg):
        return tr.html(refusal)
    import html  # local: rarely needed
    return html.escape(str(refusal))


def folder_refusal(root, path) -> str:
    """Why *path* is not a folder inside *root* (from
    :func:`restricted_root`), or ``""``."""
    if root == NOWHERE:
        return NO_ROOT
    if isinstance(path, str) and inside(path, root):
        return ""
    return Phrase(OUTSIDE_ROOT, folder=short(root))
