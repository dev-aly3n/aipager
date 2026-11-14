"""`aipager setup group`: set up one Telegram group (its chat id, label,
members and their roles, the members' root folder) with no prompts, for
scripts and coding agents.

One code path with ``aipager config``: the same checks
(:mod:`aipager.wizard.group_rules`), the same atomic 0600 writer
(:func:`aipager.wizard.scope_io.put_group_scope`, which pins other
scopes' default roots like every wizard write) and the same live reload
(:func:`aipager.setup_cmd._reload_after_write`). The output, exit codes
and ``--json`` document follow ``aipager setup`` (docs/commands.md).

The bot token is read only from the install's ``aipager.yaml``, never
from the command line, and never printed (every line goes through
``setup_cmd._scrub``). The daemon is never restarted. Nothing is ever
sent to the group: the Telegram checks (getMe, getChat, getChatMember
for the bot) are read-only, and what they find is a warning, never an
exit code.

Order: check the flags, read the install, load the roles, plan (pure,
:func:`plan_group`), the Telegram checks, daemon detection, the first
refusal in priority order, ``--dry-run``, the write, the audit record
of each owner grant, the reload.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field, replace

from aipager.setup_cmd import (
    EXIT_EXISTING_INSTALL, EXIT_FAILURE, EXIT_NOT_SET_UP, EXIT_OK, EXIT_USAGE,
    SetupError, _Run, _START_HOW, _daemon_running, _given, _reload_after_write,
    _restart_or_refused_step,
)

#: `changed` values, in the order they are reported.
GROUP_CHANGE_ORDER = ("group", "label", "root", "deny_tools", "member_added",
                      "member_relabeled", "role", "member_removed")
#: Force reasons, in the order they are reported.
FORCE_ORDER = ("owner_granted", "role_changed", "owner_demoted",
               "owner_removed", "root_code_loading", "root_overlap")
#: What ``--block-edits`` adds to the group's ``deny_tools``.
EDIT_TOOLS = ("Write", "Edit")
#: Telegram answers that mean the person must act in the group.
PERSON_ONLY_WARNINGS = ("bot_not_admin", "bot_cannot_pin", "bot_not_in_group",
                        "group_not_found")
PERSON_ONLY_STEP = (
    "Ask the person to add the bot to the group and make it an admin with "
    "the right to pin messages (only a group admin can do this, in "
    "Telegram), then check with `aipager doctor --json`.")

_GROUP_ID_RE = re.compile(r"-?\d{1,15}")
_USER_ID_RE = re.compile(r"-?\d{1,15}")

_MEMBER_FIX = ("Pass each member as --member ID:LABEL:ROLE, for example "
               "--member 111:alice:owner (the numeric Telegram user id, the "
               "name shown in chat, the role).")


@dataclass(frozen=True)
class MemberSpec:
    """One ``--member ID:LABEL:ROLE``."""

    id: int
    label: str
    role: str


@dataclass
class GroupRequest:
    """The parsed group flags."""

    chat_id: int
    members: tuple[MemberSpec, ...] = ()
    remove: tuple[int, ...] = ()
    label: str | None = None
    root: str | None = None
    block_edits: bool = False


@dataclass
class GroupPlan:
    """What :func:`plan_group` decided. ``scope`` is the group as it
    would be written; ``existing`` the group as it is now (``None`` for
    a new group). ``errors`` maps an error code to its refusal."""

    scope: object | None
    existing: object | None
    changes: list[str] = field(default_factory=list)
    member_changes: list[dict] = field(default_factory=list)
    force_reasons: list[str] = field(default_factory=list)
    force_details: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    owner_grants: list[int] = field(default_factory=list)
    pinned_roots: list[dict] = field(default_factory=list)
    errors: dict = field(default_factory=dict)


# ----- the document -----

def _null_telegram() -> dict:
    return {"checked": False, "chat_type": None, "chat_title": None,
            "bot_status": None, "bot_can_pin": None, "privacy_mode": None}


def base_doc(args: argparse.Namespace) -> dict:
    """``aipager setup group --json``: every key, ``null`` until known."""
    return {
        "command": "group", "status": None, "ok": False, "exit_code": None,
        "error": None, "message": None, "fix": None,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "force": bool(getattr(args, "force", False)),
        "bot_username": None, "chat_id": None, "scope_chat_id": None,
        "label": None, "root": None, "block_edits": False, "members": None,
        "changed": [], "member_changes": [], "force_reasons": [],
        "pinned_roots": [], "telegram": _null_telegram(), "daemon": None,
        "warnings": [], "next_step": None,
    }


# ----- flags -----

def _install_token_refusal(command: str, flags: list[str]) -> SetupError:
    return SetupError(
        EXIT_USAGE, "usage",
        f"`aipager setup {command}` uses the bot token aipager is already "
        f"set up with, so {', '.join(flags)} cannot be used.",
        f"Remove {', '.join(flags)}.")


def validate_group_flags(args: argparse.Namespace, run: _Run) -> None:
    """The flag checks of both group forms (exit 2), before any file or
    network read. For ``group`` the parsed request goes to
    ``run.request`` and the chat id to the document."""
    token_flags = _given(args, (("--token-file", "token_file"),
                                ("--token-stdin", "token_stdin")))
    if run.command == "detect-group":
        if token_flags:
            raise _install_token_refusal("detect-group", token_flags)
        from aipager.setup_cmd import GROUP_ONLY_FLAGS
        wrong = _given(args, (("--chat-id", "chat_id"), ("--role", "role"),
                              ("--service", "service"), ("--force", "force"),
                              ("--dry-run", "dry_run"), *GROUP_ONLY_FLAGS))
        if wrong:
            raise SetupError(
                EXIT_USAGE, "usage",
                f"{', '.join(wrong)} cannot be used with `aipager setup "
                "detect-group`.",
                f"Remove {', '.join(wrong)}; detect-group only takes "
                "--timeout SECONDS and --json.")
        return
    if token_flags:
        raise _install_token_refusal("group", token_flags)
    wrong = _given(args, (("--role", "role"), ("--service", "service"),
                          ("--timeout", "timeout")))
    if wrong:
        raise SetupError(
            EXIT_USAGE, "usage",
            f"{', '.join(wrong)} cannot be used with `aipager setup group`.",
            f"Remove {', '.join(wrong)}; give each member's role in "
            "--member ID:LABEL:ROLE.")
    run.request = _parse_request(args)
    run.doc["chat_id"] = run.request.chat_id


def _parse_request(args: argparse.Namespace) -> GroupRequest:
    from aipager.wizard import group_rules

    raw_chat = getattr(args, "chat_id", None)
    if raw_chat is None:
        raise SetupError(
            EXIT_USAGE, "usage", "No group chat id given.",
            "Add --chat-id N with the group's id (a negative number; "
            "`aipager setup detect-group` finds it).")
    raw_chat = raw_chat.strip()
    if not _GROUP_ID_RE.fullmatch(raw_chat):
        raise SetupError(
            EXIT_USAGE, "bad_chat_id",
            "--chat-id must be a group's id: a negative whole number.",
            "Pass --chat-id N with the group's id (`aipager setup "
            "detect-group` finds it).")
    chat_id = int(raw_chat)
    problem = group_rules.group_chat_id_problem(chat_id)
    if problem is not None:
        raise SetupError(
            EXIT_USAGE, problem.code,
            f"--chat-id {chat_id} is not a group: {problem.message}",
            "A person's own chat is set up with `aipager setup --chat-id N`; "
            "for a group pass its negative id (`aipager setup detect-group` "
            "finds it).")

    members: list[MemberSpec] = []
    seen_ids: set[int] = set()
    seen_labels: set[str] = set()
    for raw in getattr(args, "member", None) or []:
        head, sep1, rest = raw.partition(":")
        label, sep2, role = rest.rpartition(":")
        head, label, role = head.strip(), label.strip(), role.strip()
        if not sep1 or not sep2 or not head or not label or not role:
            raise SetupError(
                EXIT_USAGE, "bad_member",
                f"--member {raw!r} is not ID:LABEL:ROLE.", _MEMBER_FIX)
        if not _USER_ID_RE.fullmatch(head):
            raise SetupError(
                EXIT_USAGE, "bad_member_id",
                f"--member {raw!r}: the id must be a whole number.",
                _MEMBER_FIX)
        uid = int(head)
        problem = group_rules.member_id_problem(uid, seen_ids)
        if problem is None:
            problem = group_rules.label_problem(label, seen_labels)
        if problem is not None:
            raise SetupError(EXIT_USAGE, problem.code,
                             f"--member {raw!r}: {problem.message}",
                             _MEMBER_FIX)
        seen_ids.add(uid)
        seen_labels.add(label)
        members.append(MemberSpec(uid, label, role))

    remove: list[int] = []
    for raw in getattr(args, "remove_member", None) or []:
        raw = raw.strip()
        if not _USER_ID_RE.fullmatch(raw) or int(raw) <= 0:
            raise SetupError(
                EXIT_USAGE, "bad_member_id",
                f"--remove-member {raw!r} must be a person's numeric "
                "Telegram user id (a positive whole number).",
                "Pass --remove-member ID with the member's numeric id.")
        uid = int(raw)
        if uid in seen_ids:
            raise SetupError(
                EXIT_USAGE, "usage",
                f"User id {uid} is given both to --member and to "
                "--remove-member.",
                "Keep only one of them for this id.")
        if uid not in remove:
            remove.append(uid)

    label = getattr(args, "label", None)
    if label is not None:
        label = label.strip()
        problem = group_rules.label_problem(label, ())
        if problem is not None:
            raise SetupError(EXIT_USAGE, problem.code,
                             f"--label: {problem.message}",
                             "Pass --label NAME with a short name for the group.")

    root = getattr(args, "root", None)
    if root is not None:
        from aipager import member_root
        problem_text = member_root.root_problem(root)
        if problem_text:
            raise SetupError(
                EXIT_USAGE, "bad_root",
                f"--root {root!r} can't be a members' root folder: {problem_text}.",
                "Pass --root PATH with a folder under your home folder, for "
                "example ~/aipager-users/team (it is created when a member "
                "first needs it).")
        root = os.path.abspath(os.path.expanduser(root.strip()))

    return GroupRequest(chat_id=chat_id, members=tuple(members),
                        remove=tuple(remove), label=label, root=root,
                        block_edits=bool(getattr(args, "block_edits", False)))


# ----- the install -----

def load_install() -> tuple[list, str]:
    """``(scopes, bot_token)`` of the install's ``aipager.yaml``. Raises
    ``SetupError``: ``not_set_up`` (exit 11) when there is no
    ``aipager.yaml`` (an old ``config.env``-only install included),
    ``config_malformed`` (exit 1) when it cannot be read."""
    from aipager import scope as scope_mod
    from aipager.wizard import _constants, scope_io

    try:
        scopes, token = scope_io.read_config()
    except scope_mod.ScopeConfigError as e:
        raise SetupError(
            EXIT_FAILURE, "config_malformed",
            f"aipager.yaml cannot be read: {e}",
            "Fix the file by hand or run `aipager config`; aipager setup "
            "never overwrites a file it cannot read.")
    if scopes and token:
        return scopes, token
    if _constants.CONFIG_ENV.exists():
        raise SetupError(
            EXIT_NOT_SET_UP, "not_set_up",
            "This aipager install still uses the old config.env, and "
            "groups live in aipager.yaml.",
            "Have the person run `aipager config` once in a terminal (it "
            "moves the old config to aipager.yaml), then run this again.")
    raise SetupError(
        EXIT_NOT_SET_UP, "not_set_up",
        "aipager is not set up yet: there is no aipager.yaml with a bot token.",
        "Set aipager up first (`aipager setup --token-file PATH --chat-id N`, "
        "or `aipager config` in a terminal), then run this again.")


# ----- planning (pure) -----

def _owner_ids(scopes, policy) -> set[int]:
    """Every member id holding an owner-level role in any scope."""
    from aipager.wizard import group_rules
    return {m.id for s in scopes for m in s.members
            if group_rules.owner_level(m.role, policy)}


def _member_doc(m) -> dict:
    return {"id": m.id, "label": m.label, "role": m.role}


def _change(member_id: int, change: str, label: str, role: str,
            previous_role: str | None = None) -> dict:
    return {"id": member_id, "change": change, "label": label, "role": role,
            "previous_role": previous_role}


def _warn(plan: GroupPlan, code: str, message: str) -> None:
    if all(w["code"] != code or w["message"] != message for w in plan.warnings):
        plan.warnings.append({"code": code, "message": message})


def _force(plan: GroupPlan, reason: str, detail: str) -> None:
    if reason not in plan.force_reasons:
        plan.force_reasons.append(reason)
    if detail not in plan.force_details:
        plan.force_details.append(detail)


def plan_group(existing_scopes: list, moved: dict, request: GroupRequest,
               policy) -> GroupPlan:
    """Decide the group scope to write and what changes. Pure apart from
    reading the home folder for default roots: nothing is written or
    sent. *moved* is the recorded supergroup moves (``{old: new}``)."""
    from aipager import member_root
    from aipager.scope import Member, Scope
    from aipager.wizard import group_rules, scope_io

    scopes = list(existing_scopes)
    target = scope_io.scope_for_ids(request.chat_id, scopes, moved)
    plan = GroupPlan(scope=None, existing=target)
    if target is not None and target.kind != "group":
        plan.errors["not_a_group_scope"] = SetupError(
            EXIT_FAILURE, "not_a_group_scope",
            f"Chat {request.chat_id} is set up as a person's DM in "
            "aipager.yaml, not as a group.",
            "Check --chat-id: a group's id is a negative number "
            "(`aipager setup detect-group` finds it).")
        return plan
    if target is not None and target.chat_id != request.chat_id:
        _warn(plan, "group_moved",
              f"Telegram moved chat {request.chat_id} to a supergroup; this "
              f"group is set up under chat {target.chat_id}, and that is the "
              "scope this command changes.")
    owners_now = _owner_ids(scopes, policy)

    def _grant(spec_id: int, label: str, role: str, *, new_member: bool) -> None:
        plan.owner_grants.append(spec_id)
        _warn(plan, "owner_granted",
              f"@{label} gets the {role} role: {group_rules.OWNER_WARNING}")
        if new_member and spec_id not in owners_now:
            _force(plan, "owner_granted",
                   f"give @{label} ({spec_id}) the owner-level role {role}")

    if target is None:
        for rid in request.remove:
            _warn(plan, "not_a_member",
                  f"User id {rid} is not a member of this group, so there is "
                  "nothing to remove.")
        if not request.members:
            plan.errors["usage"] = SetupError(
                EXIT_USAGE, "usage",
                f"Chat {request.chat_id} is not set up yet, and a new group "
                "needs at least one member.",
                "Add --member ID:LABEL:ROLE for each person (for example "
                "--member 111:alice:owner).")
            return plan
        members = tuple(Member(id=m.id, label=m.label, role=m.role)
                        for m in request.members)
        for m in members:
            plan.member_changes.append(_change(m.id, "added", m.label, m.role))
            if group_rules.owner_level(m.role, policy):
                _grant(m.id, m.label, m.role, new_member=True)
        draft = Scope(chat_id=request.chat_id, kind="group",
                      label=request.label or group_rules.default_group_label(
                          request.chat_id),
                      members=members,
                      deny_tools=EDIT_TOOLS if request.block_edits else ())
        root = request.root or member_root.effective_root(draft, scopes)
        new = replace(draft, root=root)
        plan.changes = ["group"]
        _check_root(plan, root, new, scopes)
    else:
        members = list(target.members)
        index = {m.id: i for i, m in enumerate(members)}
        changes: set[str] = set()
        for spec in request.members:
            if spec.id not in index:
                members.append(Member(id=spec.id, label=spec.label, role=spec.role))
                index[spec.id] = len(members) - 1
                changes.add("member_added")
                plan.member_changes.append(
                    _change(spec.id, "added", spec.label, spec.role))
                if group_rules.owner_level(spec.role, policy):
                    _grant(spec.id, spec.label, spec.role, new_member=True)
                continue
            old = members[index[spec.id]]
            if old.label != spec.label:
                changes.add("member_relabeled")
                plan.member_changes.append(
                    _change(spec.id, "relabeled", spec.label, spec.role))
            if old.role != spec.role:
                changes.add("role")
                plan.member_changes.append(
                    _change(spec.id, "role", spec.label, spec.role, old.role))
                _force(plan, "role_changed",
                       f"change @{old.label}'s role from {old.role} to {spec.role}")
                was_owner = group_rules.owner_level(old.role, policy)
                now_owner = group_rules.owner_level(spec.role, policy)
                if was_owner and not now_owner:
                    _force(plan, "owner_demoted",
                           f"take the owner-level role {old.role} from @{old.label}")
                if now_owner and not was_owner:
                    _grant(spec.id, spec.label, spec.role, new_member=False)
            # Per-member rules (deny_tools, allow_tools, bypass flags) stay.
            members[index[spec.id]] = replace(old, label=spec.label, role=spec.role)
        for rid in request.remove:
            if rid not in index:
                _warn(plan, "not_a_member",
                      f"User id {rid} is not a member of {target.label}, so "
                      "there is nothing to remove.")
                continue
            old = next(m for m in members if m.id == rid)
            members = [m for m in members if m.id != rid]
            changes.add("member_removed")
            plan.member_changes.append(_change(rid, "removed", old.label, old.role))
            if group_rules.owner_level(old.role, policy):
                _force(plan, "owner_removed",
                       f"remove @{old.label}, who has the owner-level role {old.role}")
        if not members:
            plan.errors["last_member"] = SetupError(
                EXIT_FAILURE, "last_member",
                f"This would remove the last member of {target.label}, and a "
                "group needs at least one member (even with --force).",
                "To stop serving this group, have the person remove the whole "
                "group in `aipager config` (Edit a scope).")
        label = target.label
        root = target.root
        if request.label is not None and request.label != target.label:
            changes.add("label")
            label = request.label
            # A default root follows the label: keep the folder the members
            # already work in by writing it down (as a wizard rename does).
            root = target.root or member_root.effective_root(target, scopes)
        if request.root is not None and (
                target.root is None
                or os.path.normpath(request.root) != os.path.normpath(target.root)):
            changes.add("root")
            root = request.root
        deny_tools = tuple(target.deny_tools)
        if request.block_edits:
            missing = tuple(t for t in EDIT_TOOLS if t not in deny_tools)
            if missing:
                changes.add("deny_tools")
                deny_tools += missing
        new = replace(target, label=label, members=tuple(members), root=root,
                      deny_tools=deny_tools)
        plan.changes = [c for c in GROUP_CHANGE_ORDER if c in changes]
        if "root" in changes:
            _check_root(plan, root, new, scopes)
        if new == target:
            plan.changes = []

    plan.scope = new
    labels = [m.label for m in new.members]
    clash = next((x for x in labels if labels.count(x) > 1), None)
    if clash is not None:
        plan.errors["duplicate_label"] = SetupError(
            EXIT_USAGE, "duplicate_label",
            f"Label {clash!r} would be used by two members of this group.",
            "Give each member a different label in --member ID:LABEL:ROLE.")
    if new.members and not any(group_rules.can_manage(m.role, policy)
                               for m in new.members):
        _warn(plan, "no_manager",
              "No member of this group has a role that can manage it (owner, "
              "admin or a custom role with can_manage), so nobody there can "
              "use Full access, /settings or /update.")
    plan.force_reasons = [r for r in FORCE_ORDER if r in plan.force_reasons]
    if plan.changes:
        after = ([new if s is target else s for s in scopes] if target is not None
                 else [*scopes, new])
        pinned = member_root.pin_moved_defaults(scopes, after)
        for before_s, after_s in zip(after, pinned):
            if (after_s.chat_id != new.chat_id and after_s.root
                    and after_s.root != before_s.root):
                plan.pinned_roots.append({"chat_id": after_s.chat_id,
                                          "root": after_s.root})
    return plan


def _check_root(plan: GroupPlan, root: str, scope, scopes) -> None:
    """The wizard's two root confirms, as force reasons."""
    from aipager import member_root
    from aipager.wizard import group_rules
    _problem, path, loader, other = group_rules.root_checks(root, scope, scopes)
    if loader is not None:
        _force(plan, "root_code_loading",
               f"use the root folder {member_root.short(path)}, which is in "
               f"{loader}, whose files the system runs as programs")
    if other is not None:
        _force(plan, "root_overlap",
               f"use the root folder {member_root.short(path)}, which is the "
               f"same as, or nests with, the root of \"{other.label}\" (their "
               "members would share files)")


def _force_error(plan: GroupPlan) -> SetupError:
    return SetupError(
        EXIT_EXISTING_INSTALL, "needs_force",
        "This change needs --force, and aipager setup group never makes it "
        f"silently ({', '.join(plan.force_reasons)}).",
        "Ask the person first. If they agree, run the same command with "
        f"--force to {'; and '.join(plan.force_details)}.")


# ----- Telegram (read-only, invisible) -----

def _bot_checks(token: str, chat_id: int) -> tuple[dict, list[dict]]:
    """getMe, getChat and getChatMember for the bot: ``(telegram_doc,
    warnings)``. Nothing is sent to the group. The document may carry a
    ``bot_username`` key, which the caller moves to the top level. After
    a network failure (or 429, 5xx, an unreadable answer) the remaining
    calls are not made, so an offline machine waits for one timeout."""
    from aipager.wizard import telegram_api
    from aipager.wizard._constants import _CHAT_NOT_FOUND_RE

    doc = _null_telegram()
    warnings: list[dict] = []

    def skipped(why: str) -> tuple[dict, list[dict]]:
        warnings.append({
            "code": "telegram_checks_skipped",
            "message": ("The read-only Telegram checks were skipped "
                        f"({why or 'no answer'}). The group is still set up; "
                        "check it later with `aipager doctor --json`."),
        })
        out = _null_telegram()
        if "bot_username" in doc:
            out["bot_username"] = doc["bot_username"]
        return out, warnings

    def unreachable(code) -> bool:
        return code is None or code == 429 or code >= 500

    info, code, explained = telegram_api._get_me(token)
    if info is None:
        if code in (401, 404):
            warnings.append({
                "code": "token_rejected",
                "message": ("Telegram rejected the bot token in aipager.yaml, "
                            "so the daemon cannot use it either. Have the "
                            "person check the token in `aipager config`."),
            })
            return doc, warnings
        return skipped(explained)
    username = info.get("username")
    doc["bot_username"] = username if isinstance(username, str) and username else None
    bot_id = info.get("id")
    if not isinstance(bot_id, int) or isinstance(bot_id, bool):
        return skipped("getMe gave no bot id")
    can_read_all = info.get("can_read_all_group_messages")

    chat = telegram_api._get_chat(token, chat_id)
    if chat.result is None:
        if chat.migrate_to_chat_id is not None:
            doc["checked"] = True
            warnings.append({
                "code": "group_upgraded",
                "message": (f"Telegram upgraded this group to a supergroup, and "
                            f"its new id is {chat.migrate_to_chat_id}. The "
                            "aipager daemon moves the group there by itself "
                            "when it starts or sees the upgrade."),
            })
            return doc, warnings
        if unreachable(chat.status):
            return skipped(chat.description)
        doc["checked"] = True
        if chat.status == 403:
            warnings.append(_not_in_group(chat_id))
        elif _CHAT_NOT_FOUND_RE.search(chat.description or ""):
            warnings.append({
                "code": "group_not_found",
                "message": (f"Telegram does not know chat {chat_id} for this "
                            "bot: check the id, and that the bot was added to "
                            "the group."),
            })
        else:
            return skipped(chat.description)
        return doc, warnings
    ctype = chat.result.get("type")
    title = chat.result.get("title")
    doc.update(checked=True, chat_type=ctype if isinstance(ctype, str) else None,
               chat_title=title if isinstance(title, str) else None,
               privacy_mode=not bool(can_read_all))
    if ctype not in ("group", "supergroup"):
        warnings.append({
            "code": "chat_not_a_group",
            "message": (f"Chat {chat_id} is a {ctype or 'chat'}, not a group or "
                        "supergroup."),
        })
        return doc, warnings

    member = telegram_api._get_chat_member(token, chat_id, bot_id)
    if member.result is None:
        if unreachable(member.status):
            return skipped(member.description)
        if member.status == 403 or _CHAT_NOT_FOUND_RE.search(member.description or ""):
            warnings.append(_not_in_group(chat_id))
            return doc, warnings
        return skipped(member.description)
    status = member.result.get("status")
    status = status if isinstance(status, str) else None
    can_pin = (status == "creator"
               or (status == "administrator"
                   and member.result.get("can_pin_messages") is True))
    doc.update(bot_status=status, bot_can_pin=can_pin)
    if status in ("left", "kicked"):
        warnings.append(_not_in_group(chat_id))
    elif status in ("member", "restricted"):
        warnings.append({
            "code": "bot_not_admin",
            "message": ("The bot is in the group but is not an admin, so it "
                        "cannot pin the status bar there."),
        })
    elif status == "administrator" and not can_pin:
        warnings.append({
            "code": "bot_cannot_pin",
            "message": ("The bot is an admin of the group but may not pin "
                        "messages, so it cannot pin the status bar there."),
        })
    return doc, warnings


def _not_in_group(chat_id: int) -> dict:
    return {"code": "bot_not_in_group",
            "message": (f"The bot is not a member of chat {chat_id}, so it "
                        "cannot read or answer messages there.")}


# ----- the run -----

def _outcome(daemon: dict, wrote: bool) -> str:
    if not wrote:
        return "not_needed"
    if daemon.get("reload") == "refused":
        return "not_applied"
    if not daemon.get("running"):
        return "no_daemon"
    if daemon.get("reload") == "reloaded":
        return "reloaded_live"
    return "restart_needed"


def _next_step(doc: dict) -> str:
    shared = _restart_or_refused_step(doc)
    if shared is not None:
        return shared
    codes = {w.get("code") for w in doc["warnings"]}
    if codes & set(PERSON_ONLY_WARNINGS):
        return PERSON_ONLY_STEP
    if not (doc.get("daemon") or {}).get("running"):
        return (f"Start aipager with {_START_HOW}, then check it with "
                "`aipager doctor --json`.")
    return "Check the group with `aipager doctor --json`."


def _members_text(members) -> str:
    return ", ".join(f"{m['label']} ({m['id']}, {m['role']})" for m in members)


def cmd_group(args: argparse.Namespace, run: _Run) -> int:
    """Run ``aipager setup group`` on an invocation whose flags passed
    :func:`validate_group_flags`. Returns the exit code or raises
    ``SetupError``; ``setup_cmd.cmd_setup`` reports both."""
    from aipager import member_root, policy as policy_mod
    from aipager import scope as scope_mod
    from aipager.wizard import group_rules

    doc = run.doc
    request: GroupRequest = run.request
    dry_run = doc["dry_run"]
    force = doc["force"]

    scopes, run.token = load_install()

    errors: dict[str, SetupError] = {}
    plan: GroupPlan | None = None
    try:
        policy = group_rules.load_roles_strict()
    except policy_mod.PolicyError as e:
        errors["policy_malformed"] = SetupError(
            EXIT_FAILURE, "policy_malformed",
            f"The roles cannot be read (policy.yaml): {e}",
            "Fix policy.yaml, check it with `aipager policy validate`, then "
            "run this again.")
        policy = None
    if policy is not None:
        names = group_rules.role_names(policy)
        for spec in request.members:
            problem = group_rules.role_problem(spec.role, names)
            if problem is not None:
                errors["unknown_role"] = SetupError(
                    EXIT_USAGE, problem.code,
                    f"--member {spec.id}:{spec.label}:{spec.role}: {problem.message}",
                    "Use one of these roles, or define the role in policy.yaml "
                    "first (`aipager policy validate` checks it).")
                break
    if not errors:
        moved = scope_mod.load_chat_migrations(scope_mod.CONFIG_PATH)
        plan = plan_group(scopes, moved, request, policy)
        errors.update(plan.errors)
        if plan.force_reasons and not force and plan.changes:
            errors["needs_force"] = _force_error(plan)
        doc["warnings"].extend(plan.warnings)
        new = plan.scope
        if new is not None:
            doc.update(scope_chat_id=new.chat_id, label=new.label,
                       root=new.root or member_root.effective_root(new, scopes),
                       block_edits=all(t in new.deny_tools for t in EDIT_TOOLS),
                       members=[_member_doc(m) for m in new.members])
        doc["member_changes"] = plan.member_changes
        doc["force_reasons"] = plan.force_reasons
        doc["pinned_roots"] = plan.pinned_roots

    check_id = doc["scope_chat_id"] or request.chat_id
    telegram, tg_warnings = _bot_checks(run.token, check_id)
    telegram = dict(telegram)
    doc["bot_username"] = telegram.pop("bot_username", None)
    doc["telegram"] = {**_null_telegram(), **telegram}
    doc["warnings"].extend(tg_warnings)

    detected = _daemon_running()
    running = detected is not False
    doc["daemon"] = {"running": running, "reload": "not_needed",
                     "restart_needed": False, "outcome": "not_needed"}
    if detected is None:
        doc["warnings"].append({
            "code": "daemon_unknown",
            "message": ("Could not tell whether an aipager daemon is running, "
                        "so no live reload is tried and a restart is asked "
                        "for instead. Check with `aipager doctor --json`."),
        })

    first_error = next((errors[k] for k in (
        "policy_malformed", "unknown_role", "usage", "duplicate_label",
        "not_a_group_scope", "last_member", "needs_force") if k in errors), None)
    changes = list(plan.changes) if plan is not None else []

    if dry_run:
        doc["changed"] = changes
        if first_error is not None:
            raise first_error
        doc["status"] = "dry_run"
        doc["message"] = ("Dry run: nothing was written. "
                          + (f"Would change: {', '.join(changes)}."
                             if changes else "Nothing would change."))
        doc["next_step"] = "Run the same command without --dry-run to apply it."
        _plain_lines(run)
        return EXIT_OK
    if first_error is not None:
        raise first_error
    assert plan is not None and plan.scope is not None

    from aipager import audit
    from aipager._test_guard import check_write
    for target in (scope_mod.CONFIG_PATH, audit.AUDIT_LOG_PATH):
        check_write(target)

    label, cid = plan.scope.label, plan.scope.chat_id
    if not changes:
        doc["status"] = "unchanged"
        doc["message"] = (f"Nothing to change: group {label} (chat {cid}) is "
                          "already set up like this.")
        doc["pinned_roots"] = []
        doc["next_step"] = _next_step(doc)
        _plain_lines(run)
        return EXIT_OK

    from aipager.wizard import scope_io
    try:
        refusal = scope_io.put_group_scope(plan.scope, run.token,
                                           expected=plan.existing)
    except OSError as e:
        raise SetupError(EXIT_FAILURE, "write_failed", f"Writing failed: {e}",
                         "Fix the cause above, then run this again.")
    if refusal is not None:
        raise SetupError(EXIT_FAILURE, "config_changed", refusal,
                         "Run this again: it plans from the file as it is now.")
    doc["changed"] = changes

    from aipager.wizard.first_run import _record_owner_grant
    for member_id in plan.owner_grants:
        if not _record_owner_grant(member_id):
            doc["warnings"].append({
                "code": "audit_write_failed",
                "message": (f"The owner grant for user {member_id} is written, "
                            "but its audit record could not be added to "
                            f"{audit.AUDIT_LOG_PATH}. Check that this file "
                            "can be written."),
            })

    _reload_after_write(run, running, unknown=detected is None)
    doc["daemon"]["outcome"] = _outcome(doc["daemon"], True)

    n = len(plan.scope.members)
    count = "1 member" if n == 1 else f"{n} members"
    if plan.existing is None:
        doc["status"] = "created"
        doc["message"] = f"Group {label} (chat {cid}) is set up with {count}."
    else:
        doc["status"] = "updated"
        doc["message"] = (f"Group {label} (chat {cid}) is updated. Changed: "
                          f"{', '.join(changes)}.")
    doc["next_step"] = _next_step(doc)
    _plain_lines(run, wrote=True)
    return EXIT_OK


def _plain_lines(run: _Run, *, wrote: bool = False) -> None:
    """The indented lines plain mode prints under the result."""
    from aipager import member_root
    doc = run.doc
    if doc.get("label") is not None:
        run.plain_out.append(f"  group:    {doc['label']} (chat {doc['scope_chat_id']})")
    if doc.get("members"):
        run.plain_out.append(f"  members:  {_members_text(doc['members'])}")
    if doc.get("root"):
        run.plain_out.append(f"  root:     {member_root.short(doc['root'])}")
    if not wrote:
        return
    outcome = doc["daemon"]["outcome"]
    if outcome == "reloaded_live":
        line = "reloaded live"
    elif outcome == "not_applied":
        problem = next((w["message"] for w in doc["warnings"]
                        if w["code"] == "reload_refused"), "")
        line = f"not applied: {problem}" if problem else "not applied"
    elif outcome == "restart_needed":
        line = "restart needed"
    else:
        line = "no daemon running"
    run.plain_out.append(f"  daemon:   {line}")


__all__ = ["cmd_group", "plan_group", "load_install", "validate_group_flags",
           "base_doc"]
