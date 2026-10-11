"""The members' root folder in ``aipager config`` (see
:mod:`aipager.member_root`): asked for when a group, or a DM for a role
without ``can_manage``, is added; set from Edit a scope; shown in the
current config. The wizard writes the folder, never creates it. English
only, like every wizard prompt. Every prompt goes through the calling
menu's own ``_ask`` (passed as *ask*), so one stub answers them all.
"""

from __future__ import annotations

import os
from dataclasses import replace

import questionary

from aipager.errors import friendly_warn
from aipager.ui import console, ok
from aipager.wizard._constants import _PROMPT_STYLE
from aipager.wizard.scope_io import commit_scope, read_config

ROOT_MENU_LABEL = "Set the members' root folder"

ROOT_QUESTION = ("Members' root folder (members without can_manage, such "
                 "as user and read_only, start and work only inside it):")


def _root_overlap(path: str, scope, scopes):
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


def ask_root(scope, scopes, ask) -> str:
    """Ask for *scope*'s members' root folder, prefilled with its current
    one (the default when none is set). Re-asks while the answer is
    refused (:func:`aipager.member_root.root_problem`) and asks to confirm
    one that is, holds or lies inside another scope's root. Returns the
    absolute path. The folder is not created here. *ask*: the prompt
    runner (the calling menu's own, so one stub answers every prompt)."""
    from aipager import member_root
    current = member_root.effective_root(scope, scopes)
    while True:
        raw = str(ask(questionary.text(
            ROOT_QUESTION, default=member_root.short(current),
            qmark="?", style=_PROMPT_STYLE,
        )) or "").strip() or member_root.short(current)
        problem = member_root.root_problem(raw)
        if problem:
            friendly_warn(f"That folder can't be a root: {problem}.")
            continue
        path = os.path.abspath(os.path.expanduser(raw))
        other = _root_overlap(path, scope, scopes)
        if other is not None and not ask(questionary.confirm(
            f"{member_root.short(path)} is the same as, or nests with, the "
            f"root of \"{other.label}\", so their members would share files. "
            "Use it anyway?",
            default=False, qmark="?", style=_PROMPT_STYLE,
        )):
            continue
        return path


def role_can_manage(role: str) -> bool:
    """*role* (a name) has ``can_manage`` in the policy as loaded now."""
    try:
        from aipager.policy import load_policy
        found = load_policy().get_role(role)
    except Exception:
        found = None
    return found is not None and found.can_manage is True


def effective_root(scope) -> str:
    """*scope*'s root folder, with the scopes on disk now."""
    from aipager import member_root
    return member_root.effective_root(scope, read_config()[0])


def set_root(scope, token: str, ask) -> bool:
    """Edit a scope → Set the members' root folder. Returns True iff
    written (the caller then reloads the daemon live)."""
    from aipager import member_root
    scopes = read_config()[0]
    current = member_root.effective_root(scope, scopes)
    console.print(f"  Current root: {member_root.short(current)}"
                  + ("" if scope.root else " (default)"))
    new = ask_root(scope, scopes, ask)
    if scope.root and new == scope.root:
        friendly_warn("No change.")
        return False
    commit_scope(replace(scope, root=new), token)
    ok(f"Members' root folder = {member_root.short(new)}")
    return True


def root_label(scope, scopes) -> str:
    """The current config's ``root: <folder>`` for *scope* (rich markup),
    ``(default)`` when none is set."""
    from rich.markup import escape

    from aipager import member_root
    root = member_root.short(member_root.effective_root(scope, scopes))
    return (f" · root: [path]{escape(root)}[/path]"
            + ("" if scope.root else " (default)"))
