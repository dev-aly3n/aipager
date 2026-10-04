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
from aipager.ui import console, ok, step, warn
from aipager.wizard import daemon_io
from aipager.wizard._constants import _PROMPT_STYLE
from aipager.wizard.display import _ask
from aipager.wizard.draft import clear_draft, load_draft, save_draft
from aipager.wizard.scope_io import add_new_scope, configured_scope_for

_ROLE_GLOSS = {
    "owner": "unrestricted; bypasses safety + all deny rules",
    "admin": "Auto, settings, updates; not members, roles or the safety floor",
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
        choices.append(questionary.Choice(f"{n} - {gloss}", value=n))
    return choices


#: "Add a group scope" for a chat that already is one (roadmap 8.88): it
#: used to replace the scope, dropping every member but the new ones.
GROUP_ALREADY_SET_UP = ("This group is already set up. Use Edit a scope → "
                        "Add a member.")


#: "Add a DM scope" for a person who already has one (roadmap 8.94c): it
#: used to replace their DM scope, dropping its role and deny lists.
DM_ALREADY_SET_UP = ("This person already has a DM set up. Use Edit a "
                     "member to change their role.")

#: "Add a DM scope" for an id that is a group's chat (Telegram never gives
#: a person a group's id, but a pasted id is not trusted).
DM_ID_IS_A_GROUP = ("That id is a group that is already set up, not a "
                    "person. Nothing was changed.")


def _dm_refusal(chat_id: int) -> str | None:
    """Why a DM scope for *chat_id* must not be added (a scope for that
    chat is already set up), or ``None``."""
    existing = configured_scope_for(chat_id)
    if existing is None:
        return None
    return DM_ALREADY_SET_UP if existing.kind == "dm" else DM_ID_IS_A_GROUP


# Shown above a group's role picker, where owner is offered (roadmap 8.83).
OWNER_WARNING = ("owner has full control of this machine, including "
                 "aipager's config, the bot token and Claude's credentials. "
                 "Give it only to yourself.")


def _pick_role(prompt: str, *, default: str = "user",
               include_owner: bool = True,
               warn_owner: bool = False) -> str:
    choices = _role_choices(include_owner=include_owner)
    values = [c.value for c in choices]
    if warn_owner and "owner" in values:
        warn(OWNER_WARNING)
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
        friendly_warn("Cancelled - no DM scope added.")
        return False
    refusal = _dm_refusal(captured["id"])
    if refusal is not None:
        friendly_warn(refusal)
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
    # Never commit_scope here: it replaces a scope with the same chat, and
    # that would wipe this person's role and deny lists (roadmap 8.94c).
    if not add_new_scope(scope, token):
        friendly_warn(_dm_refusal(captured["id"]) or DM_ALREADY_SET_UP)
        return False
    ok(f"Added DM scope for @{captured['label']} ({role}).")
    return True


def _operator_member() -> Member | None:
    """The operator: the member of the owner's DM scope (a DM scope whose
    member's role has ``bypass_safety``), or ``None`` when there is no
    such scope. Offered as a new group's first member (roadmap 8.83)."""
    from aipager.wizard.scope_io import read_config
    try:
        from aipager.policy import load_policy
        scopes, _ = read_config()
        policy = load_policy()
    except Exception:
        return None
    for s in scopes:
        if s.kind != "dm":
            continue
        for m in s.members:
            role = policy.get_role(m.role)
            if role is not None and role.bypass_safety is True:
                return m
    return None


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
        if configured_scope_for(chat_id) is not None:
            friendly_warn(GROUP_ALREADY_SET_UP,
                          "The unfinished draft for it was discarded.")
            clear_draft()
            return False
        label = str(resume.get("label") or f"group-{abs(chat_id)}")
        members: list[dict] = list(resume.get("members", []))
        console.print(
            f"Resuming group '{label}' "
            f"({len(members)} member(s) so far)."
        )
    else:
        chat_id = _step_chat_id(token, bot_username, mode="team",
                                step_label="[~]")
        if configured_scope_for(chat_id) is not None:
            friendly_warn(GROUP_ALREADY_SET_UP)
            return False
        label = _ask(questionary.text(
            "Label for this group (shown in status):",
            default=f"group-{abs(chat_id)}", qmark="?", style=_PROMPT_STYLE,
        )).strip() or f"group-{abs(chat_id)}"
        members = []

    def _persist() -> None:
        save_draft({"kind": "group", "chat_id": chat_id, "label": label,
                    "members": members})

    _persist()

    ask_first = True
    if not resume:
        # The operator first, as owner (skippable): without someone who
        # can manage it, nobody in the group could use Auto, /settings
        # or /update.
        op = _operator_member()
        if op is not None and _ask(questionary.confirm(
            f"Add yourself (@{op.label}, from your own DM) as the first "
            f"member, with the owner role?",
            default=True, qmark="?", style=_PROMPT_STYLE,
        )):
            members.append({"id": op.id, "label": op.label, "role": "owner"})
            _persist()
            ok(f"Added @{op.label} (owner) - 1 member(s) drafted.")
            ask_first = bool(_ask(questionary.confirm(
                "Add another member?", default=True,
                qmark="?", style=_PROMPT_STYLE,
            )))

    while ask_first:
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
                          default="user", warn_owner=True)
        members.append({**captured, "role": role})
        _persist()
        ok(f"Added @{captured['label']} ({role}) - "
           f"{len(members)} member(s) drafted.")
        more = _ask(questionary.confirm(
            "Add another member?", default=False,
            qmark="?", style=_PROMPT_STYLE,
        ))
        if not more:
            break

    if not members:
        friendly_warn("No members added - group scope discarded.")
        clear_draft()
        return False

    deny_tools = _collect_deny_tools()
    scope = Scope(
        chat_id=chat_id, kind="group", label=label,
        members=tuple(Member(id=m["id"], label=m["label"], role=m["role"])
                      for m in members),
        deny_tools=tuple(deny_tools),
    )
    # Never commit_scope here: it replaces a scope with the same chat,
    # and someone may have set this group up meanwhile (roadmap 8.88).
    if not add_new_scope(scope, token):
        friendly_warn(GROUP_ALREADY_SET_UP,
                      "Nothing was changed; the members drafted here were "
                      "not added.")
        clear_draft()
        return False
    clear_draft()
    ok(f"Added group '{label}' with {len(members)} member(s).")
    return True


def offer_expansion(token: str, bot_username: str) -> None:
    """Post-bootstrap additive offer (default = done).

    Whatever was added is applied like an edit-menu change (roadmap
    8.94b): one live reload (or the restart hint) when the operator
    leaves, however many scopes were added. Nothing added, nothing sent.
    """
    changed = False
    try:
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
                    added = add_group_scope(token, bot_username)
                else:
                    added = add_dm_scope(token, bot_username)
                if added:
                    changed = True
            except KeyboardInterrupt:
                friendly_warn("Cancelled this action.")
    finally:
        # Also on a Ctrl-C at the menu: what was added is on disk.
        if changed:
            daemon_io._apply_team_change_hint()


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
        added = add_group_scope(token, bot_username, resume=draft)
    except KeyboardInterrupt:
        friendly_warn("Paused again - draft kept.")
        return
    if added:
        # Applied like an edit-menu change (roadmap 8.94b).
        daemon_io._apply_team_change_hint()
