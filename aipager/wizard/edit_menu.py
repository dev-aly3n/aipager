"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations


import questionary

from aipager.errors import friendly_error, friendly_warn
from aipager.ui import console, ok, rule, step
from aipager.wizard._constants import (
    _PROMPT_STYLE,
)

from aipager.wizard.daemon_io import (
    _apply_team_change_hint,
    _read_env_file,
    _restart_hint,
    _write_env_file,
)
from aipager.wizard.display import (
    _ask,
    _show_current_config,
)
from aipager.wizard.first_run import (
    _first_run_flow,
    _step_chat_id,
)
from aipager.wizard.settings_patch import _step_settings
from aipager.wizard.team_setup import (
    _collect_users,
    _show_team_warning_panel,
    _step_team_setup,
)
from aipager.wizard.telegram_api import (
    _normalize_token,
    _verify_token,
)


def _edit_add_user(team) -> "object | None":
    """Returns the new Team after add, or None if the user cancelled."""
    from aipager.team import Role, User as TeamUser, dump_team

    existing_ids = set(team.users.keys())
    existing_labels = {u.label for u in team.users.values()}
    # Read token so the add flow can offer Telegram auto-detect.
    token, _ = _read_env_file()
    new_entries = _collect_users(
        existing_ids=existing_ids, existing_labels=existing_labels,
        token=token,
    )
    if not new_entries:
        return None
    new_team = team
    for u in new_entries:
        new_team = new_team.with_user(
            TeamUser(id=u["id"], label=u["label"], role=Role(u["role"])),
        )
    confirm = _ask(questionary.confirm(
        f"Add {len(new_entries)} user(s) to team.yaml?",
        default=True, qmark="?", style=_PROMPT_STYLE,
    ))
    if not confirm:
        friendly_warn("Cancelled.")
        return None
    dump_team(new_team)
    ok(f"Added {len(new_entries)} user(s).")
    return new_team


def _edit_review_pending(team) -> "object | None":
    """List pending-users (recorded when unauthorized users tried to
    use the bot) and let the admin add or dismiss them one by one.

    Returns the updated Team if anyone was added (so the caller can
    decide to hot-reload), otherwise ``None``.
    """
    from aipager.team import (
        Role, User as TeamUser, clear_pending_user, dump_team,
        list_pending_users,
    )

    pending = list_pending_users()
    if not pending:
        console.print()
        console.print(
            "[muted]No pending users — nobody has tried the bot from "
            "outside the allow-list since the last reset.[/muted]"
        )
        return None

    existing_ids = set(team.users.keys())
    existing_labels = {u.label for u in team.users.values()}
    changed = False
    new_team = team

    for record in pending:
        uid = int(record.get("user_id", 0))
        handle = record.get("username") or ""
        display = record.get("display_name") or ""
        first_seen = record.get("first_seen") or "?"

        if uid in existing_ids:
            # Stale entry — already on the allow-list.
            clear_pending_user(uid)
            continue

        console.print()
        console.print(
            f"[title]Pending:[/title]  "
            f"@{handle or '(no handle)'} · id={uid} · "
            f"{display or '(no name)'} · first seen {first_seen}"
        )
        action = _ask(questionary.select(
            "What do you want to do with this user?",
            choices=[
                questionary.Choice("Add as developer", value="developer"),
                questionary.Choice("Add as admin",     value="admin"),
                questionary.Choice("Add as read_only", value="read_only"),
                questionary.Choice("Dismiss (remove from pending list)",
                                   value="dismiss"),
                questionary.Choice("Skip (keep in pending, decide later)",
                                   value="skip"),
            ],
            qmark="?", style=_PROMPT_STYLE,
        ))

        if action == "skip":
            continue
        if action == "dismiss":
            clear_pending_user(uid)
            ok(f"Dismissed @{handle or uid}")
            continue

        # Add as the chosen role.
        suggested_label = (handle or display or f"user{uid}").lower()
        if suggested_label in existing_labels:
            friendly_warn(
                f"Label {suggested_label!r} clashes with existing user."
            )
            new_label = _ask(questionary.text(
                "Use which label instead?",
                default=f"{suggested_label}_{uid}",
                qmark="?", style=_PROMPT_STYLE,
            )).strip()
            if not new_label or new_label in existing_labels:
                friendly_warn("Cancelled.")
                continue
            suggested_label = new_label
        try:
            new_team = new_team.with_user(TeamUser(
                id=uid, label=suggested_label, role=Role(action),
            ))
        except ValueError as e:
            friendly_warn(f"Couldn't add: {e}")
            continue
        clear_pending_user(uid)
        existing_ids.add(uid)
        existing_labels.add(suggested_label)
        changed = True
        ok(f"Added @{suggested_label} ({uid}) as {action}")

    if not changed:
        return None
    dump_team(new_team)
    ok(f"Saved → team.yaml now has {len(new_team.users)} users")
    return new_team


def _edit_remove_user(team) -> "object | None":
    from aipager.team import dump_team
    from aipager.team import Role

    if not team.users:
        friendly_warn("No users to remove.")
        return None
    choices = [
        questionary.Choice(f"{u.label} — {u.role.value}", value=u.id)
        for u in team.users.values()
    ]
    choices.append(questionary.Choice("Cancel", value=None))
    pick = _ask(questionary.select(
        "Remove which user?",
        choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    if pick is None:
        return None
    target = team.users[pick]
    # Refuse to leave the team without an admin.
    if (target.role == Role.ADMIN and team.admin_count() == 1):
        friendly_warn(
            f"{target.label} is the only admin — promote another member "
            "first, then come back to remove them.",
        )
        return None
    confirm = _ask(questionary.confirm(
        f"Remove {target.label} ({target.role.value})?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not confirm:
        return None
    new_team = team.without_user(pick)
    dump_team(new_team)
    ok(f"Removed {target.label}.")
    return new_team


def _edit_change_role(team) -> "object | None":
    from aipager.team import Role, dump_team

    if not team.users:
        friendly_warn("No users to update.")
        return None
    choices = [
        questionary.Choice(f"{u.label} — currently {u.role.value}", value=u.id)
        for u in team.users.values()
    ]
    choices.append(questionary.Choice("Cancel", value=None))
    pick = _ask(questionary.select(
        "Change which user's role?",
        choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    if pick is None:
        return None
    current = team.users[pick]
    new_role_str = _ask(questionary.select(
        f"New role for {current.label} (was {current.role.value}):",
        choices=["admin", "developer", "read_only"],
        qmark="?", style=_PROMPT_STYLE,
    ))
    new_role = Role(new_role_str)
    if new_role == current.role:
        friendly_warn("No change.")
        return None
    # Refuse to demote the only admin.
    if (current.role == Role.ADMIN and new_role != Role.ADMIN
            and team.admin_count() == 1):
        friendly_warn(
            f"{current.label} is the only admin — promote another member "
            "first.",
        )
        return None
    new_team = team.with_role(pick, new_role)
    dump_team(new_team)
    ok(f"{current.label}: {current.role.value} → {new_role.value}")
    return new_team


_COMMON_TOOLS = (
    "Bash", "Write", "Edit", "WebFetch", "Read", "Glob", "Grep", "Task",
)


def _edit_deny_tools(team) -> "object | None":
    from aipager.team import dump_team

    current = set(team.rules.deny_tools)
    choices = [
        questionary.Choice(
            t, value=t, checked=(t in current),
        ) for t in _COMMON_TOOLS
    ]
    picked = _ask(questionary.checkbox(
        "Toggle deny_tools (space to select, Enter to confirm):",
        choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    extras_raw = _ask(questionary.text(
        "Other tools to deny (comma-separated, leave blank for none):",
        default=", ".join(sorted(current - set(_COMMON_TOOLS))),
        qmark="?", style=_PROMPT_STYLE,
    )).strip()
    extras = [t.strip() for t in extras_raw.split(",") if t.strip()]
    new_tools = sorted(set(picked) | set(extras))

    if tuple(new_tools) == team.rules.deny_tools:
        friendly_warn("No change.")
        return None
    new_team = team.with_deny_tools(new_tools)
    dump_team(new_team)
    ok(f"deny_tools = {new_tools or '(none)'}")
    return new_team


def _edit_refresh_token() -> bool:
    """Re-prompt for token, verify, rewrite config.env. Returns True
    iff the token was updated."""
    step("[~]  Refresh bot token")
    _, chat_id = _read_env_file()
    while True:
        raw = _ask(questionary.text(
            "Paste your bot token:",
            qmark="?", style=_PROMPT_STYLE,
        ))
        token = _normalize_token(raw)
        if not token:
            friendly_warn("Empty — try again or Ctrl-C to cancel.")
            continue
        info = _verify_token(token)
        if info is None:
            friendly_warn("Telegram rejected the token. Try again or Ctrl-C.")
            continue
        _write_env_file(token, chat_id)
        ok(f"Wrote new token for @{info.get('username', '?')}.")
        return True


def _edit_switch_to_personal(team) -> bool:
    """Archive team.yaml. Returns True iff the switch happened."""
    from aipager.team import TEAM_CONFIG_PATH, archive_team

    console.print()
    console.print(
        "[warn]⚠[/warn]  Switching to personal mode will archive "
        "[path]team.yaml[/path] as a timestamped backup.",
    )
    confirm = _ask(questionary.confirm(
        f"Archive {team.users and f'{len(team.users)} users + ' or ''}rules "
        "and run as a 1:1 DM bot?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not confirm:
        return False
    backup = archive_team(TEAM_CONFIG_PATH)
    if backup is None:
        friendly_warn("No team.yaml found — nothing to archive.")
        return False
    ok(f"Archived team.yaml → {backup.name}")

    # Offer to update CHAT_ID — it's almost certainly still the group id.
    _token, current_chat_id = _read_env_file()
    update_chat = _ask(questionary.confirm(
        f"Current CHAT_ID is {current_chat_id} (the group id). "
        "Update it to a DM chat id now?",
        default=True, qmark="?", style=_PROMPT_STYLE,
    ))
    if update_chat:
        token = _token
        info = _verify_token(token) if token else None
        bot_username = (info or {}).get("username", "your_bot")
        new_chat = _step_chat_id(
            token, bot_username, mode="personal", step_label="[~]",
        )
        _write_env_file(token, new_chat)
        ok(f"Wrote CHAT_ID={new_chat} (DM).")
    return True


def _edit_switch_to_team() -> bool:
    """Run the team setup against the existing token. Returns True
    iff team.yaml was written."""
    token, _ = _read_env_file()
    if not token:
        friendly_warn("No token in config.env — run full setup first.")
        return False
    info = _verify_token(token)
    bot_username = (info or {}).get("username", "your_bot")

    _show_team_warning_panel()
    proceed = _ask(questionary.confirm(
        "Continue with team-mode setup?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not proceed:
        return False

    group_id = _step_chat_id(token, bot_username, mode="team",
                             step_label="[~]")
    _step_team_setup(group_id, step_label="[~]", token=token)
    # CHAT_ID in config.env should now be the group id for the daemon
    # to filter correctly.
    _write_env_file(token, group_id)
    ok(f"Wrote CHAT_ID={group_id} (group).")
    return True


def _edit_flow() -> int:
    """Show the current-config panel and offer a menu of edits.

    Loops until the user picks Exit (or selects "Run full setup",
    which delegates back to :func:`_first_run_flow`).
    """
    from aipager.team import TEAM_CONFIG_PATH, TeamConfigError, load_team

    rule("aipager config")
    while True:
        _show_current_config()

        try:
            team = load_team(TEAM_CONFIG_PATH)
        except TeamConfigError:
            team = None  # malformed; only show "run full setup" action

        if team is not None:
            # Surface pending-user count in the menu label so the
            # admin sees at a glance there are people waiting.
            from aipager.team import list_pending_users
            pending = list_pending_users()
            review_label = "Review pending users"
            if pending:
                review_label = f"Review pending users ({len(pending)} waiting)"
            choices = [
                questionary.Choice("Add a user", value="add"),
                questionary.Choice(review_label, value="review_pending"),
                questionary.Choice("Remove a user", value="remove"),
                questionary.Choice("Change a user's role", value="role"),
                questionary.Choice("Edit deny_tools rules", value="rules"),
                questionary.Choice("Switch to Personal mode", value="to_personal"),
                questionary.Choice("Refresh bot token", value="refresh_token"),
                questionary.Choice("Re-install Claude Code hooks",
                                   value="reinstall_hooks"),
                questionary.Choice("Run full setup (overwrites everything)",
                                   value="full"),
                questionary.Choice("Exit", value="exit"),
            ]
        else:
            choices = [
                questionary.Choice("Switch to Team mode", value="to_team"),
                questionary.Choice("Refresh bot token", value="refresh_token"),
                questionary.Choice("Re-install Claude Code hooks",
                                   value="reinstall_hooks"),
                questionary.Choice("Run full setup (overwrites everything)",
                                   value="full"),
                questionary.Choice("Exit", value="exit"),
            ]

        try:
            choice = _ask(questionary.select(
                "What would you like to do?",
                choices=choices, qmark="?", style=_PROMPT_STYLE,
            ))
        except KeyboardInterrupt:
            return 130

        try:
            if choice == "exit":
                return 0
            # Track what kind of change happened so we can choose the
            # right post-edit hint:
            #   "team"    — only team.yaml touched → hot-reload via SIGUSR1
            #   "restart" — config.env or token changed → full restart
            #   None      — no change / cancelled
            change_kind: str | None = None
            if choice == "full":
                return _first_run_flow()
            if choice == "refresh_token":
                if _edit_refresh_token():
                    change_kind = "restart"
            elif choice == "reinstall_hooks":
                _step_settings(step_label="[~]")
                # Hooks live in ~/.claude/settings.json; they're read
                # by Claude Code itself (not aipager). No daemon
                # restart needed.
            elif choice == "to_personal" and team is not None:
                if _edit_switch_to_personal(team):
                    # Archives team.yaml AND may update config.env
                    # (chat-id swap). Conservatively assume restart.
                    change_kind = "restart"
            elif choice == "to_team":
                if _edit_switch_to_team():
                    # Writes team.yaml AND updates CHAT_ID in
                    # config.env — restart required.
                    change_kind = "restart"
            elif team is not None and choice == "add":
                if _edit_add_user(team) is not None:
                    change_kind = "team"
            elif team is not None and choice == "review_pending":
                if _edit_review_pending(team) is not None:
                    change_kind = "team"
            elif team is not None and choice == "remove":
                if _edit_remove_user(team) is not None:
                    change_kind = "team"
            elif team is not None and choice == "role":
                if _edit_change_role(team) is not None:
                    change_kind = "team"
            elif team is not None and choice == "rules":
                if _edit_deny_tools(team) is not None:
                    change_kind = "team"
        except KeyboardInterrupt:
            friendly_warn("Cancelled this action.")
            continue
        except ValueError as e:
            friendly_error(str(e))
            continue
        except OSError as e:
            friendly_error(f"Write failed: {e}")
            continue

        if change_kind == "team":
            _apply_team_change_hint()
        elif change_kind == "restart":
            _restart_hint()
