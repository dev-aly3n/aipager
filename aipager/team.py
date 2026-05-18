"""Group / team mode — allow-list of Telegram users + role-based rules.

When ``~/.config/aipager/team.yaml`` is present and contains ``mode:
team``, the daemon flips from personal mode (one DM, one implicit
user) to team mode: only enumerated users can interact, and
optional :class:`Rules` clamp dangerous tool calls automatically.

The :class:`Team` dataclass is loaded at startup by ``config.py``
and consulted by every handler in ``telegram_bot.py``. Personal-mode
installs are unaffected (``TEAM`` stays ``None``).

Trust model:

- ``admin``     — full control; bypasses ``deny_tools`` rules.
- ``developer`` — can prompt + approve, but ``deny_tools`` applies.
- ``read_only`` — can use ``/status``; all other input is ignored.

Unauthorized users (not in the allow-list) get one polite reply
per daemon run telling them they're not on the list, then silence.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Canonical location for the team config. ``aipager config`` writes
# it; users can hand-edit it later to add / remove members.
TEAM_CONFIG_PATH: Path = Path.home() / ".config" / "aipager" / "team.yaml"


class Role(str, Enum):
    """Membership tier within a team."""

    ADMIN = "admin"
    DEVELOPER = "developer"
    READ_ONLY = "read_only"


@dataclass(frozen=True)
class User:
    """A single member of the team."""

    id: int
    label: str
    role: Role

    @property
    def can_prompt(self) -> bool:
        return self.role != Role.READ_ONLY

    @property
    def can_approve(self) -> bool:
        return self.role != Role.READ_ONLY

    @property
    def bypasses_rules(self) -> bool:
        return self.role == Role.ADMIN


@dataclass
class Rules:
    """Declarative restrictions layered on top of role permissions.

    Empty / missing sections impose no extra constraint.
    """

    # Tools whose ``PreToolUse: Ask`` prompts auto-deny (unless the
    # triggering user is an admin). Case-sensitive — matches the
    # exact ``tool_name`` Claude Code emits.
    deny_tools: tuple[str, ...] = ()

    def tool_is_denied(self, tool_name: str, user: User | None) -> bool:
        """Return True iff ``tool_name`` must be auto-denied for ``user``.

        Admins bypass rules. ``None`` (synthetic / unknown user) is
        treated as non-admin for safety — if we can't identify the
        triggerer, default to the most restrictive interpretation.
        """
        if user is not None and user.bypasses_rules:
            return False
        return tool_name in self.deny_tools


@dataclass
class Team:
    """In-memory view of ``team.yaml``."""

    group_id: int
    users: dict[int, User] = field(default_factory=dict)
    rules: Rules = field(default_factory=Rules)

    def __post_init__(self) -> None:
        # Sanity: at least one admin so the team is recoverable if
        # rules.yaml gets misconfigured (an admin can always hand-edit).
        if not any(u.role == Role.ADMIN for u in self.users.values()):
            log.warning(
                "team.yaml has no admin user — only an admin can bypass "
                "rules.deny_tools. Consider promoting one user.",
            )

    def get(self, user_id: int) -> User | None:
        return self.users.get(user_id)

    def find_by_label(self, label: str) -> User | None:
        for u in self.users.values():
            if u.label == label:
                return u
        return None

    def admin_count(self) -> int:
        return sum(1 for u in self.users.values() if u.role == Role.ADMIN)

    def is_authorized(self, user_id: int | None) -> bool:
        """True iff ``user_id`` is on the allow-list."""
        return user_id is not None and user_id in self.users

    # --- Immutable-style mutations -----------------------------------
    #
    # Each returns a NEW :class:`Team`. The wizard sub-flows validate
    # invariants (no duplicate label, at least one admin) at the call
    # site and only persist if the new team is acceptable.

    def with_user(self, user: User) -> Team:
        """Return a copy with ``user`` added. Raises on duplicate id.

        Duplicate-label is also a hard error — labels are how users
        are addressed in chat (``@alice``) so they must stay unique.
        """
        if user.id in self.users:
            raise ValueError(f"user id {user.id} already in team")
        if self.find_by_label(user.label) is not None:
            raise ValueError(f"label {user.label!r} already taken")
        new_users = dict(self.users)
        new_users[user.id] = user
        return replace(self, users=new_users)

    def without_user(self, user_id: int) -> Team:
        """Return a copy with ``user_id`` removed. Raises if absent."""
        if user_id not in self.users:
            raise ValueError(f"user id {user_id} not in team")
        new_users = {k: v for k, v in self.users.items() if k != user_id}
        return replace(self, users=new_users)

    def with_role(self, user_id: int, role: Role) -> Team:
        """Return a copy with ``user_id`` set to ``role``."""
        if user_id not in self.users:
            raise ValueError(f"user id {user_id} not in team")
        old = self.users[user_id]
        new_users = dict(self.users)
        new_users[user_id] = User(id=old.id, label=old.label, role=role)
        return replace(self, users=new_users)

    def with_deny_tools(self, tools: Sequence[str]) -> Team:
        """Return a copy whose ``rules.deny_tools`` is ``tools``."""
        return replace(self, rules=Rules(deny_tools=tuple(tools)))


def load_team(path: Path = TEAM_CONFIG_PATH) -> Team | None:
    """Load and validate ``team.yaml``.

    Returns ``None`` if the file is absent or doesn't declare team
    mode — the daemon should run in personal mode in that case.
    Raises :class:`TeamConfigError` on malformed content so the
    daemon can refuse to start rather than silently fall through to
    a less-safe mode.
    """
    if not path.exists():
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TeamConfigError(f"team.yaml parse error: {e}") from e

    if not isinstance(raw, dict):
        raise TeamConfigError(f"team.yaml: expected a mapping, got {type(raw).__name__}")

    if raw.get("mode") != "team":
        # File exists but isn't asserting team mode — treat as personal.
        return None

    group_id = raw.get("group_id")
    if not isinstance(group_id, int):
        raise TeamConfigError("team.yaml: `group_id` must be an integer (Telegram group ID)")

    users_raw = raw.get("users", [])
    if not isinstance(users_raw, list) or not users_raw:
        raise TeamConfigError("team.yaml: `users` must be a non-empty list")

    users: dict[int, User] = {}
    for i, entry in enumerate(users_raw):
        if not isinstance(entry, dict):
            raise TeamConfigError(f"team.yaml: users[{i}] must be a mapping")
        try:
            uid = int(entry["id"])
            label = str(entry["label"]).strip()
            role = Role(str(entry["role"]).strip())
        except KeyError as e:
            raise TeamConfigError(f"team.yaml: users[{i}] missing field {e}") from e
        except ValueError as e:
            raise TeamConfigError(f"team.yaml: users[{i}] invalid role: {e}") from e
        if not label:
            raise TeamConfigError(f"team.yaml: users[{i}].label must be non-empty")
        if uid in users:
            raise TeamConfigError(f"team.yaml: duplicate user id {uid}")
        users[uid] = User(id=uid, label=label, role=role)

    rules_raw = raw.get("rules") or {}
    if not isinstance(rules_raw, dict):
        raise TeamConfigError("team.yaml: `rules` must be a mapping if present")

    deny_tools_raw = rules_raw.get("deny_tools") or []
    if not isinstance(deny_tools_raw, list) or not all(
        isinstance(t, str) for t in deny_tools_raw
    ):
        raise TeamConfigError("team.yaml: `rules.deny_tools` must be a list of strings")

    return Team(
        group_id=group_id,
        users=users,
        rules=Rules(deny_tools=tuple(deny_tools_raw)),
    )


class TeamConfigError(Exception):
    """Raised when ``team.yaml`` is present but malformed.

    The daemon refuses to start on this; a half-loaded team would be
    less safe than the explicit personal-mode default.
    """


# Canonical header for team.yaml — explains the file at a glance and
# tells the user how to apply changes. Re-emitted on every write.
_TEAM_YAML_HEADER = """\
# aipager team mode — managed by `aipager config`.
# Edit by hand to add / remove users. Restart the daemon after
# changes (`aipager service restart`, or kill the foreground daemon
# and re-run `aipager start`).
"""


def dump_team(team: Team, path: Path = TEAM_CONFIG_PATH) -> None:
    """Serialize ``team`` to ``path`` (atomic write, mode 0600).

    Writes via a ``.tmp`` sibling then ``os.rename`` so a crash
    mid-write can't corrupt the existing file.
    """
    data: dict = {
        "mode": "team",
        "group_id": team.group_id,
        "users": [
            {"id": u.id, "label": u.label, "role": u.role.value}
            for u in team.users.values()
        ],
    }
    if team.rules.deny_tools:
        data["rules"] = {"deny_tools": list(team.rules.deny_tools)}

    body = (
        _TEAM_YAML_HEADER
        + yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        log.debug("could not chmod %s", tmp, exc_info=True)
    os.replace(tmp, path)


def archive_team(path: Path = TEAM_CONFIG_PATH) -> Path | None:
    """Rename ``path`` → ``path.bak.<unix-ts>``.

    Used by the wizard's Switch-to-Personal flow. Returns the
    backup path on success, ``None`` if there was nothing to
    archive.
    """
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    os.replace(path, backup)
    return backup


# ---------------------------------------------------------------------------
# One-shot "you're not on the allow-list" tracker
# ---------------------------------------------------------------------------
#
# Telegram users who message the bot but aren't on the allow-list get one
# polite reply, then silence. The tracker resets on daemon restart, which
# is fine — restarts are infrequent.

_UNAUTHORIZED_SEEN: set[int] = set()


def remember_unauthorized(user_id: int) -> bool:
    """Returns True iff we've already replied to ``user_id``.

    Caller pattern::

        if not remember_unauthorized(uid):
            await msg.reply_text("you're not on the allow-list…")
    """
    if user_id in _UNAUTHORIZED_SEEN:
        return True
    _UNAUTHORIZED_SEEN.add(user_id)
    return False


def reset_unauthorized_seen() -> None:
    """Test seam — clear the in-memory unauthorized cache."""
    _UNAUTHORIZED_SEEN.clear()


# ---------------------------------------------------------------------------
# Telegram-friendly attribution helpers
# ---------------------------------------------------------------------------


def attribution_label(user: User | None) -> str:
    """``@alice`` for a known user, ``@unknown`` otherwise.

    Always returned with an at-sign so it reads naturally inside the
    chat — even though Telegram won't mention the user (no notification),
    the visual treatment matches.
    """
    return f"@{user.label}" if user is not None else "@unknown"


__all__ = [
    "Role",
    "Rules",
    "Team",
    "TeamConfigError",
    "User",
    "TEAM_CONFIG_PATH",
    "archive_team",
    "attribution_label",
    "dump_team",
    "load_team",
    "remember_unauthorized",
    "reset_unauthorized_seen",
]
