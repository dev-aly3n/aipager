"""The rules a group scope is checked against, with no prompts: one code
path for ``aipager config`` (the wizard asks again when a rule refuses)
and ``aipager setup group`` (it reports the refusal and exits).

Each ``*_problem`` function returns a :class:`Problem` (a stable code
for logic and the English text to show), or ``None`` when the value is
fine. Callers compare the code, never the text. English only, like
every wizard prompt; nothing here imports questionary or rich.
"""

from __future__ import annotations

import os
from typing import NamedTuple


class Problem(NamedTuple):
    """Why a value is refused: ``code`` for logic, ``message`` to show."""

    code: str
    message: str


#: Roadmap 8.91e: what the wizard says to a shared sender id
#: (``team.SHARED_SENDER_IDS``), typed or auto-detected.
SHARED_SENDER_REFUSAL = (
    "That id belongs to Telegram's anonymous admin or channel sender, which "
    "every anonymous admin and channel post shares. Ask the person to post "
    "as themselves, then try again.")

#: Shown wherever owner is granted in a group (roadmap 8.83).
OWNER_WARNING = ("owner has full control of this machine, including "
                 "aipager's config, the bot token and Claude's credentials. "
                 "Give it only to yourself.")

GROUP_ID_NEGATIVE = "Group IDs are negative integers."
MEMBER_ID_POSITIVE = "Telegram user ids are positive whole numbers."
LABEL_EMPTY = "Label must be non-empty."
LABEL_CONTROL = "Labels cannot contain control characters."

#: The built-in roles, in the order every role list shows them.
BUILTIN_ORDER = ("owner", "admin", "user", "read_only")

#: Folders whose files the system runs or loads as code: a member's turn
#: may write in its root, so a root here (or inside one) lets a member put
#: a program the operator's account then runs.
CODE_LOADING = ("~/bin", "~/.local/bin", "~/.config/systemd",
                "~/.config/autostart")


# ----- ids -----

def group_chat_id_problem(cid: int) -> Problem | None:
    """A group's chat id must be negative."""
    if cid >= 0:
        return Problem("bad_chat_id", GROUP_ID_NEGATIVE)
    return None


def member_id_problem(uid: int, existing_ids) -> Problem | None:
    """A member id must be positive, not a shared sender id, and not
    already among *existing_ids*."""
    from aipager.team import is_shared_sender_id
    if uid <= 0:
        return Problem("bad_member_id", MEMBER_ID_POSITIVE)
    if is_shared_sender_id(uid):
        return Problem("shared_sender_id", SHARED_SENDER_REFUSAL)
    if uid in existing_ids:
        return Problem("duplicate_member",
                       f"User id {uid} is already on the allow-list.")
    return None


# ----- labels -----

def label_problem(label: str, existing_labels) -> Problem | None:
    """A label must be non-empty after trimming, hold no control
    characters, and not be among *existing_labels*."""
    if not label.strip():
        return Problem("bad_label", LABEL_EMPTY)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in label):
        return Problem("bad_label", LABEL_CONTROL)
    if label in existing_labels:
        return Problem("duplicate_label",
                       f"Label {label!r} is already in use - try a different one.")
    return None


def default_group_label(cid: int) -> str:
    """The label a new group gets when none is given."""
    return f"group-{abs(cid)}"


# ----- roles -----

def load_roles_strict():
    """The merged policy (built-ins, ``policy.yaml``, ``policy.d``), with
    the paths read now (tests redirect them). Raises
    :class:`aipager.policy.PolicyError` instead of falling back."""
    from aipager import policy
    return policy.load_policy(policy.POLICY_PATH, policy.POLICY_D_DIR)


def role_names(policy) -> list[str]:
    """Every role of *policy*: built-ins in their usual order, then the
    custom ones in the order the policy lists them."""
    names = list(policy.roles)
    builtin = [n for n in BUILTIN_ORDER if n in names]
    return builtin + [n for n in names if n not in BUILTIN_ORDER]


def role_problem(role: str, names) -> Problem | None:
    """*role* must be one of *names*."""
    if role in names:
        return None
    available = ", ".join(names) or "(none)"
    return Problem("unknown_role",
                   f"Unknown role {role!r}. Available roles: {available}.")


def owner_level(role_name: str, policy) -> bool:
    """*role_name* skips the safety floor (``bypass_safety``): it has
    full control of the machine, like ``owner``."""
    role = policy.get_role(role_name)
    return role is not None and role.bypass_safety is True


def can_manage(role_name: str, policy) -> bool:
    """*role_name* may use the admin features (``can_manage``)."""
    role = policy.get_role(role_name)
    return role is not None and role.can_manage is True


# ----- the members' root folder -----

def code_loading_folder(path: str) -> str | None:
    """The folder of :data:`CODE_LOADING` that *path* is or lies inside
    (as written and with symlinks resolved), ``~``-shortened, or ``None``."""
    from aipager import member_root
    forms = {os.path.abspath(path), os.path.realpath(path)}
    for spot in CODE_LOADING:
        folder = os.path.expanduser(spot)
        for f in {os.path.abspath(folder), os.path.realpath(folder)}:
            if any(member_root.inside(form, f) for form in forms):
                return spot
    return None


def root_overlap(path: str, scope, scopes):
    """Another scope whose root is *path*, holds it or lies inside it."""
    from aipager import member_root
    real = os.path.realpath(path)
    for other in scopes:
        if other.chat_id == scope.chat_id:
            continue
        theirs = os.path.realpath(member_root.effective_root(other, scopes))
        if member_root.inside(real, theirs) or member_root.inside(theirs, real):
            return other
    return None


def root_checks(raw: str, scope, scopes):
    """Check a typed root folder for *scope*: ``(problem, path, loader,
    overlap)``. *problem* is the hard refusal (plain English, from
    :func:`aipager.member_root.root_problem`), and then the rest are
    ``None``. Otherwise *path* is the absolute folder, *loader* the
    :data:`CODE_LOADING` folder it is in (or ``None``) and *overlap* the
    other scope whose root nests with it (or ``None``): the two cases the
    wizard asks to confirm and ``aipager setup group`` needs ``--force``
    for."""
    from aipager import member_root
    problem = member_root.root_problem(raw)
    if problem:
        return problem, None, None, None
    path = os.path.abspath(os.path.expanduser(raw.strip()))
    return (None, path, code_loading_folder(path),
            root_overlap(path, scope, scopes))
