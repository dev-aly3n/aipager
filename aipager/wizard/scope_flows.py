"""Additive scope sub-flows for the wizard (see :mod:`aipager.wizard`).

Growth in multi-scope mode is additive, never a mode switch (arch
§3.0): after the solo bootstrap the operator can *add a group* or
*add a person*. Each sub-flow commits its completed scope atomically
and holds the in-progress one in the draft (arch §3.0b), so a crash
or cancel never loses prior work.

Role choices are read from the merged policy (built-ins + any custom
roles in ``policy.yaml``); the wizard never *writes* the policy.
"""

from __future__ import annotations

import questionary

from aipager.errors import friendly_warn
from aipager.scope import Member, Scope
from aipager.ui import console, ok, step
from aipager.wizard._constants import _PROMPT_STYLE
from aipager.wizard.display import _ask
from aipager.wizard.draft import clear_draft, load_draft, save_draft
from aipager.wizard.scope_io import commit_scope

_ROLE_GLOSS = {
    "owner": "unrestricted; bypasses safety + all deny rules",
    "admin": "full control; bypasses deny_tools, not the safety floor",
    "user": "prompt + approve; full safety + deny rules apply",
    "read_only": "/status only; no prompting",
}
_BUILTIN_ORDER = ("owner", "admin", "user", "read_only")


def _role_choices(*, include_owner: bool = True) -> list[questionary.Choice]:
    """Built-in roles + any custom roles from ``policy.yaml``.

    Built-ins first (canonical order), then custom names. Reads the
    merged policy; falls back to the built-in names if it can't load.
    """
    try:
        from aipager.policy import load_policy
        names = list(load_policy().roles)
    except Exception:
        names = list(_BUILTIN_ORDER)
    builtin = [n for n in _BUILTIN_ORDER if n in names]
    custom = [n for n in names if n not in _BUILTIN_ORDER]
    choices: list[questionary.Choice] = []
    for n in (*builtin, *custom):
        if n == "owner" and not include_owner:
            continue
        gloss = _ROLE_GLOSS.get(n, "custom role")
        choices.append(questionary.Choice(f"{n} — {gloss}", value=n))
    return choices


def _pick_role(prompt: str, *, default: str = "user",
               include_owner: bool = True) -> str:
    choices = _role_choices(include_owner=include_owner)
    values = [c.value for c in choices]
    dflt = default if default in values else values[0]
    return _ask(questionary.select(
        prompt, choices=choices, default=dflt,
        qmark="?", style=_PROMPT_STYLE,
    ))


def add_dm_scope(token: str, bot_username: str) -> bool:
    """Add a DM scope for another user. Returns True iff one was written."""
    from aipager.wizard.team_setup import _capture_user_identity

    step("[~]  Add a DM scope")
    captured = _capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token=token,
    )
    if captured is None:
        friendly_warn("Cancelled — no DM scope added.")
        return False
    # A DM scope has exactly one member. Single-tenant deployments (one
    # friend per aipager container, they own everything) legitimately
    # need `owner` for that member — otherwise the safety floor blocks
    # basic work for the container's sole user. Keep the default at
    # "user" so a multi-user daemon isn't tricked into granting bypass;
    # the operator picks owner explicitly when that's the setup.
    role = _pick_role(
        f"Role for @{captured['label']} in their DM:",
        default="user", include_owner=True,
    )
    scope = Scope(
        chat_id=captured["id"], kind="dm", label=f"{captured['label']} DM",
        members=(Member(id=captured["id"], label=captured["label"],
                        role=role),),
    )
    commit_scope(scope, token)
    ok(f"Added DM scope for @{captured['label']} ({role}).")
    return True


def add_group_scope(token: str, bot_username: str,
                    *, resume: dict | None = None) -> bool:
    """Add a group scope, member by member. Returns True iff committed.

    Each captured member is flushed to the draft, so a Ctrl-C mid-add
    leaves a resumable draft (prior committed scopes untouched). On
    confirm the scope is committed atomically and the draft cleared.
    """
    from aipager.wizard.first_run import _step_chat_id
    from aipager.wizard.team_setup import _capture_user_identity, _collect_deny_tools

    step("[~]  Add a group scope")
    if resume:
        chat_id = int(resume["chat_id"])
        label = str(resume.get("label") or f"group-{abs(chat_id)}")
        members: list[dict] = list(resume.get("members", []))
        console.print(
            f"Resuming group '{label}' "
            f"({len(members)} member(s) so far)."
        )
    else:
        chat_id = _step_chat_id(token, bot_username, mode="team",
                                step_label="[~]")
        label = _ask(questionary.text(
            "Label for this group (shown in status):",
            default=f"group-{abs(chat_id)}", qmark="?", style=_PROMPT_STYLE,
        )).strip() or f"group-{abs(chat_id)}"
        members = []

    def _persist() -> None:
        save_draft({"kind": "group", "chat_id": chat_id, "label": label,
                    "members": members})

    _persist()

    while True:
        idx = len(members) + 1
        captured = _capture_user_identity(
            idx,
            existing_ids={m["id"] for m in members},
            existing_labels={m["label"] for m in members},
            token=token,
        )
        if captured is None:
            break
        role = _pick_role(f"Role for @{captured['label']}:",
                          default="user", include_owner=False)
        members.append({**captured, "role": role})
        _persist()
        ok(f"Added @{captured['label']} ({role}) — "
           f"{len(members)} member(s) drafted.")
        more = _ask(questionary.confirm(
            "Add another member?", default=False,
            qmark="?", style=_PROMPT_STYLE,
        ))
        if not more:
            break

    if not members:
        friendly_warn("No members added — group scope discarded.")
        clear_draft()
        return False

    deny_tools = _collect_deny_tools()
    scope = Scope(
        chat_id=chat_id, kind="group", label=label,
        members=tuple(Member(id=m["id"], label=m["label"], role=m["role"])
                      for m in members),
        deny_tools=tuple(deny_tools),
    )
    commit_scope(scope, token)
    clear_draft()
    ok(f"Added group '{label}' with {len(members)} member(s).")
    return True


def offer_expansion(token: str, bot_username: str) -> None:
    """Post-bootstrap additive offer (default = done)."""
    while True:
        choice = _ask(questionary.select(
            "Connected. Add a team group or other people now?",
            choices=[
                questionary.Choice("Add a group", value="group"),
                questionary.Choice("Add a person (DM)", value="dm"),
                questionary.Choice("I'm done", value="done"),
            ],
            default="done", qmark="?", style=_PROMPT_STYLE,
        ))
        if choice == "done":
            return
        try:
            if choice == "group":
                add_group_scope(token, bot_username)
            else:
                add_dm_scope(token, bot_username)
        except KeyboardInterrupt:
            friendly_warn("Cancelled this action.")


def resume_or_discard_draft(token: str, bot_username: str) -> None:
    """If a leftover draft exists, offer to resume or discard it."""
    draft = load_draft()
    if not draft:
        return
    label = draft.get("label", "?")
    n = len(draft.get("members", []))
    console.print()
    choice = _ask(questionary.select(
        f"You were adding group '{label}' ({n} member(s) so far).",
        choices=[
            questionary.Choice("Resume", value="resume"),
            questionary.Choice("Discard", value="discard"),
        ],
        qmark="?", style=_PROMPT_STYLE,
    ))
    if choice == "discard":
        clear_draft()
        ok("Discarded the in-progress draft.")
        return
    try:
        add_group_scope(token, bot_username, resume=draft)
    except KeyboardInterrupt:
        friendly_warn("Paused again — draft kept.")
