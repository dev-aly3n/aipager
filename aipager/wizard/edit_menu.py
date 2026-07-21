"""See :mod:`aipager.wizard` for the package overview.

The edit menu is the re-run face of ``aipager config``: a list-and-edit
interface over the scopes in ``aipager.yaml`` (flows §6). It reads the
policy to enumerate role choices and to *show* the active policy, but
it **never writes** the user-owned policy files (security §3.7d / R15).
"""

from __future__ import annotations

from dataclasses import replace

import questionary

from aipager.errors import friendly_error, friendly_warn
from aipager.scope import ScopeConfigError
from aipager.ui import console, ok, rule, step
from aipager.wizard._constants import _PROMPT_STYLE
from aipager.wizard.daemon_io import _restart_hint
from aipager.wizard.display import _ask, _show_current_config
from aipager.wizard.scope_flows import _pick_role, add_dm_scope, add_group_scope
from aipager.wizard.scope_io import commit_scope, read_config, remove_scope, replace_scopes
from aipager.wizard.settings_patch import _step_settings
from aipager.wizard.telegram_api import _normalize_token, _test_send, _verify_token

_COMMON_TOOLS = (
    "Bash", "Write", "Edit", "WebFetch", "Read", "Glob", "Grep", "Task",
)


def _bot_username(token: str) -> str:
    if not token:
        return "your_bot"
    info = _verify_token(token)
    return (info or {}).get("username", "your_bot")


def _pick_scope(scopes, prompt: str = "Which scope?"):
    choices = [
        questionary.Choice(
            f'{s.kind} "{s.label}" ({len(s.members)} member'
            f'{"s" if len(s.members) != 1 else ""})',
            value=s.chat_id,
        )
        for s in scopes
    ]
    choices.append(questionary.Choice("Cancel", value=None))
    cid = _ask(questionary.select(
        prompt, choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    if cid is None:
        return None
    return next(s for s in scopes if s.chat_id == cid)


def _toggle_tools(current: tuple[str, ...]) -> tuple[str, ...]:
    """Checkbox + free-text editor for a deny_tools list."""
    cur = set(current)
    choices = [
        questionary.Choice(t, value=t, checked=(t in cur))
        for t in _COMMON_TOOLS
    ]
    picked = _ask(questionary.checkbox(
        "Toggle deny_tools (space to select, Enter to confirm):",
        choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    extras_raw = _ask(questionary.text(
        "Other tools to deny (comma-separated, blank for none):",
        default=", ".join(sorted(cur - set(_COMMON_TOOLS))),
        qmark="?", style=_PROMPT_STYLE,
    )).strip()
    extras = [t.strip() for t in extras_raw.split(",") if t.strip()]
    return tuple(sorted(set(picked) | set(extras)))


def _edit_scope(scope, token: str) -> bool:
    """Rename / edit deny_tools / remove a scope. Returns True if changed."""
    action = _ask(questionary.select(
        f'Edit {scope.kind} "{scope.label}":',
        choices=[
            questionary.Choice("Rename", value="rename"),
            questionary.Choice("Edit scope deny_tools", value="deny"),
            questionary.Choice("Remove this scope", value="remove"),
            questionary.Choice("Cancel", value="cancel"),
        ],
        qmark="?", style=_PROMPT_STYLE,
    ))
    if action == "cancel":
        return False
    if action == "rename":
        new = _ask(questionary.text(
            "New label:", default=scope.label,
            qmark="?", style=_PROMPT_STYLE,
        )).strip()
        if not new or new == scope.label:
            friendly_warn("No change.")
            return False
        commit_scope(replace(scope, label=new), token)
        ok(f"Renamed → {new}")
        return True
    if action == "deny":
        new_tools = _toggle_tools(scope.deny_tools)
        if new_tools == scope.deny_tools:
            friendly_warn("No change.")
            return False
        commit_scope(replace(scope, deny_tools=new_tools), token)
        ok(f"scope deny_tools = {list(new_tools) or '(none)'}")
        return True
    # remove
    confirm = _ask(questionary.confirm(
        f'Remove {scope.kind} "{scope.label}"?',
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not confirm:
        return False
    if not remove_scope(scope.chat_id):
        friendly_warn(
            "Can't remove the only scope — at least one must remain. "
            "Add another scope first, or re-run setup.",
        )
        return False
    ok(f"Removed {scope.label}.")
    return True


def _edit_member(scope, token: str) -> bool:
    """Set role / edit per-user deny_tools / remove a member."""
    choices = [
        questionary.Choice(f"{m.label} — {m.role}", value=m.id)
        for m in scope.members
    ]
    choices.append(questionary.Choice("Cancel", value=None))
    mid = _ask(questionary.select(
        "Which member?", choices=choices, qmark="?", style=_PROMPT_STYLE,
    ))
    if mid is None:
        return False
    member = next(m for m in scope.members if m.id == mid)

    action = _ask(questionary.select(
        f"Edit @{member.label} ({member.role}):",
        choices=[
            questionary.Choice("Set role", value="role"),
            questionary.Choice("Edit member deny_tools", value="deny"),
            questionary.Choice("Remove member", value="remove"),
            questionary.Choice("Cancel", value="cancel"),
        ],
        qmark="?", style=_PROMPT_STYLE,
    ))
    if action == "cancel":
        return False

    def _replace_member(new_member) -> None:
        members = tuple(new_member if m.id == mid else m for m in scope.members)
        commit_scope(replace(scope, members=members), token)

    if action == "role":
        role = _pick_role(f"New role for @{member.label}:",
                          default=member.role)
        if role == member.role:
            friendly_warn("No change.")
            return False
        _replace_member(replace(member, role=role))
        ok(f"@{member.label}: → {role}")
        return True
    if action == "deny":
        new_tools = _toggle_tools(member.deny_tools)
        if new_tools == member.deny_tools:
            friendly_warn("No change.")
            return False
        _replace_member(replace(member, deny_tools=new_tools))
        ok(f"@{member.label} deny_tools = {list(new_tools) or '(none)'}")
        return True
    # remove
    if scope.kind == "dm":
        friendly_warn(
            "A DM scope is one person — remove the whole scope instead "
            "(Edit a scope → Remove this scope).",
        )
        return False
    if len(scope.members) == 1:
        friendly_warn(
            "That's the last member — remove the whole scope instead.",
        )
        return False
    confirm = _ask(questionary.confirm(
        f"Remove @{member.label} from {scope.label}?",
        default=False, qmark="?", style=_PROMPT_STYLE,
    ))
    if not confirm:
        return False
    members = tuple(m for m in scope.members if m.id != mid)
    commit_scope(replace(scope, members=members), token)
    ok(f"Removed @{member.label}.")
    return True


def _test_reachability(scope, token: str) -> None:
    step(f'[~]  Testing "{scope.label}"')
    sent, err = _test_send(token, scope.chat_id)
    if sent:
        ok(f"Test message delivered to {scope.label}.")
    else:
        friendly_warn(f"Could not reach {scope.label}: {err}")


def _view_policy() -> None:
    """Read-only policy view. NEVER opens the policy files for writing."""
    from aipager.policy import POLICY_D_DIR, POLICY_PATH, load_policy

    console.print()
    console.print(
        "[title]Policy (user-owned — this wizard never writes it):[/title]"
    )
    state = "exists" if POLICY_PATH.exists() else \
        "absent — built-in defaults in effect"
    console.print(f"  [path]{POLICY_PATH}[/path]  [muted]({state})[/muted]")
    console.print(f"  [path]{POLICY_D_DIR}/[/path]")
    try:
        # Pass the (call-time) paths so a monkeypatched POLICY_PATH is
        # honored — load_policy's defaults bind at import time.
        pol = load_policy(POLICY_PATH, POLICY_D_DIR)
    except Exception as e:  # noqa: BLE001 — surface any policy error
        console.print(f"  [err]policy error: {e}[/err]")
        return
    console.print(f"[title]Roles:[/title] {', '.join(sorted(pol.roles))}")
    console.print(
        "[muted]  Edit the file directly to add custom roles / tune "
        "safety — nothing here overwrites it.[/muted]"
    )
    console.print("[muted]  Lint:   aipager policy validate[/muted]")
    console.print("[muted]  Render: aipager doctor --safety-check[/muted]")


def _refresh_token(scopes) -> str | None:
    """Re-prompt for a token, verify, rewrite aipager.yaml. Returns the
    new token iff it changed."""
    step("[~]  Refresh bot token")
    while True:
        raw = _ask(questionary.text(
            "Paste your bot token:", qmark="?", style=_PROMPT_STYLE,
        ))
        token = _normalize_token(raw)
        if not token:
            friendly_warn("Empty — try again or Ctrl-C to cancel.")
            continue
        info = _verify_token(token)
        if info is None:
            friendly_warn("Telegram rejected the token. Try again or Ctrl-C.")
            continue
        replace_scopes(scopes, token)
        ok(f"Wrote new token for @{info.get('username', '?')}.")
        return token


def _menu_choices(has_error: bool) -> list[questionary.Choice]:
    """The edit-menu actions. A malformed ``aipager.yaml`` collapses to
    just token-refresh + exit (nothing scope-based is safe to offer)."""
    if has_error:
        return [
            questionary.Choice("Refresh bot token", value="refresh_token"),
            questionary.Choice("Exit", value="exit"),
        ]
    return [
        questionary.Choice("Add a group scope", value="add_group"),
        questionary.Choice("Add a DM scope", value="add_dm"),
        questionary.Choice("Edit a scope", value="edit_scope"),
        questionary.Choice("Edit a member", value="edit_member"),
        questionary.Choice("Change default mode (Ask / Auto)", value="default_mode"),
        questionary.Choice("Test bot reachability", value="test"),
        questionary.Choice("View policy", value="view_policy"),
        questionary.Choice("Re-install Claude Code hooks",
                           value="reinstall_hooks"),
        questionary.Choice("Refresh bot token", value="refresh_token"),
        questionary.Choice("Exit", value="exit"),
    ]


def _edit_flow() -> int:
    """Show the scope list and offer a menu of edits (loops until Exit)."""
    rule("aipager config")
    while True:
        _show_current_config()

        try:
            scopes, token = read_config()
            cfg_err: str | None = None
        except ScopeConfigError as e:
            scopes, token, cfg_err = [], "", str(e)

        choices = _menu_choices(cfg_err is not None)

        try:
            choice = _ask(questionary.select(
                "What would you like to do?",
                choices=choices, qmark="?", style=_PROMPT_STYLE,
            ))
        except KeyboardInterrupt:
            return 130

        changed = False
        try:
            if choice == "exit":
                return 0
            if choice == "refresh_token":
                if _refresh_token(scopes):
                    changed = True
            elif choice == "reinstall_hooks":
                _step_settings(step_label="[~]")
                # Hooks live in ~/.claude/settings.json (read by Claude
                # Code itself) — no daemon restart needed.
            elif choice == "view_policy":
                _view_policy()
            elif choice == "add_group":
                if add_group_scope(token, _bot_username(token)):
                    changed = True
            elif choice == "add_dm":
                if add_dm_scope(token, _bot_username(token)):
                    changed = True
            elif choice == "test":
                sc = _pick_scope(scopes, "Test which scope?")
                if sc is not None:
                    _test_reachability(sc, token)
            elif choice == "edit_scope":
                sc = _pick_scope(scopes, "Edit which scope?")
                if sc is not None and _edit_scope(sc, token):
                    changed = True
            elif choice == "edit_member":
                sc = _pick_scope(scopes, "Member of which scope?")
                if sc is not None and _edit_member(sc, token):
                    changed = True
            elif choice == "default_mode":
                from aipager.wizard.first_run import (
                    _commit_default_mode, _step_default_mode,
                )
                mode = _step_default_mode(step_label="[~]")
                _commit_default_mode(mode)
                changed = True
        except KeyboardInterrupt:
            friendly_warn("Cancelled this action.")
            continue
        except ValueError as e:
            friendly_error(str(e))
            continue
        except OSError as e:
            friendly_error(f"Write failed: {e}")
            continue

        if changed:
            _restart_hint()
