"""Scope model + ``aipager.yaml`` loader (the *who* of multi-scope mode).

A **scope** is a Telegram chat the bot serves plus its members. The
wizard-managed ``aipager.yaml`` lists scopes; the user-owned
``policy.yaml`` (see :mod:`aipager.policy`) defines what each role may
do. Phase A only loads + validates; nothing consumes scopes for
authorization yet.

See ``researches/multi-scope-mode/01-architecture.md`` (§3.2, §4).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

CONFIG_PATH: Path = Path.home() / ".config" / "aipager" / "aipager.yaml"

SCHEMA_VERSION = 2
_KINDS = ("dm", "group")


def scope_suffix(chat_id: int, kind: str) -> str:
    """Internal socket/name suffix that disambiguates same-labeled
    sessions across scopes: ``<kind[0]><abs(chat_id)>`` (e.g. the DM
    chat 256113222 → ``d256113222``; group -4152307515 → ``g4152307515``).
    The leading ``-`` of a group id is encoded by the ``g`` prefix, so
    the result is filesystem-safe. The full chat_id (not a truncation)
    is used so the suffix is collision-free.
    """
    return f"{kind[0]}{abs(chat_id)}"


def disambiguated_name(label: str, chat_id: int, kind: str) -> str:
    """Internal session name for a NEW scoped session:
    ``claude-<label>__<suffix>``. The user only ever sees ``label``;
    the suffix lives in the registry key, the dtach socket path, the
    ``CLAUDE_DTACH_SESSION`` env var and the statusline file name.
    """
    return f"claude-{label}__{scope_suffix(chat_id, kind)}"


class ScopeConfigError(Exception):
    """Raised when ``aipager.yaml`` is present but malformed.

    The daemon refuses to start rather than silently degrade — a
    half-understood scope config is less safe than failing loud.
    """


@dataclass(frozen=True)
class Member:
    """A user within a scope.

    ``role`` is a role *name* resolved against the policy
    (:func:`aipager.policy.validate_scopes_against_policy`). The
    optional override fields default to "inherit from the role":
    ``None`` for the bool overrides, empty tuples for the lists.
    """

    id: int
    label: str
    role: str
    deny_tools: tuple[str, ...] = ()
    allow_tools: tuple[str, ...] = ()
    bypass_safety: bool | None = None
    bypass_role_denies: bool | None = None


@dataclass(frozen=True)
class Scope:
    """A Telegram chat the bot serves, plus its members + rules."""

    chat_id: int
    kind: str  # "dm" | "group"
    label: str
    members: tuple[Member, ...] = ()
    deny_tools: tuple[str, ...] = ()


# Canonical header — re-emitted on every write. Mirrors team.py's pattern.
_AIPAGER_YAML_HEADER = """\
# aipager — multi-scope config (the "who"). Managed by `aipager config`.
# For custom roles + safety rules, edit policy.yaml — that file is
# never overwritten. Restart the daemon after changes.
"""


def _str_list(val, where: str) -> tuple[str, ...]:
    if val is None:
        return ()
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise ScopeConfigError(f"aipager.yaml: {where} must be a list of strings")
    return tuple(val)


def _parse_member(entry, where: str) -> Member:
    if not isinstance(entry, dict):
        raise ScopeConfigError(f"aipager.yaml: {where} must be a mapping")
    try:
        uid = int(entry["id"])
        label = str(entry["label"]).strip()
        role = str(entry["role"]).strip()
    except KeyError as e:
        raise ScopeConfigError(f"aipager.yaml: {where} missing field {e}") from e
    except (TypeError, ValueError) as e:
        raise ScopeConfigError(f"aipager.yaml: {where} invalid id: {e}") from e
    if not label:
        raise ScopeConfigError(f"aipager.yaml: {where}.label must be non-empty")
    if not role:
        raise ScopeConfigError(f"aipager.yaml: {where}.role must be non-empty")

    def _opt_bool(key):
        if key not in entry:
            return None
        v = entry[key]
        if not isinstance(v, bool):
            raise ScopeConfigError(f"aipager.yaml: {where}.{key} must be true/false")
        return v

    return Member(
        id=uid,
        label=label,
        role=role,
        deny_tools=_str_list(entry.get("deny_tools"), f"{where}.deny_tools"),
        allow_tools=_str_list(entry.get("allow_tools"), f"{where}.allow_tools"),
        bypass_safety=_opt_bool("bypass_safety"),
        bypass_role_denies=_opt_bool("bypass_role_denies"),
    )


def _parse_scope(entry, i: int) -> Scope:
    if not isinstance(entry, dict):
        raise ScopeConfigError(f"aipager.yaml: scopes[{i}] must be a mapping")
    kind = str(entry.get("kind", "")).strip()
    if kind not in _KINDS:
        raise ScopeConfigError(
            f"aipager.yaml: scopes[{i}].kind must be one of {_KINDS}, got {kind!r}"
        )
    try:
        chat_id = int(entry["chat_id"])
    except KeyError as e:
        raise ScopeConfigError(f"aipager.yaml: scopes[{i}] missing field {e}") from e
    except (TypeError, ValueError) as e:
        raise ScopeConfigError(
            f"aipager.yaml: scopes[{i}].chat_id must be an integer: {e}"
        ) from e
    label = str(entry.get("label", "")).strip() or f"scope-{chat_id}"

    members_raw = entry.get("members") or []
    if not isinstance(members_raw, list) or not members_raw:
        raise ScopeConfigError(
            f"aipager.yaml: scopes[{i}].members must be a non-empty list"
        )
    members = tuple(
        _parse_member(m, f"scopes[{i}].members[{j}]")
        for j, m in enumerate(members_raw)
    )
    if kind == "dm" and len(members) != 1:
        raise ScopeConfigError(
            f"aipager.yaml: scopes[{i}] is a DM scope and must have exactly "
            f"one member (got {len(members)})"
        )

    seen: set[int] = set()
    for m in members:
        if m.id in seen:
            raise ScopeConfigError(
                f"aipager.yaml: scopes[{i}] has duplicate member id {m.id}"
            )
        seen.add(m.id)

    return Scope(
        chat_id=chat_id,
        kind=kind,
        label=label,
        members=members,
        deny_tools=_str_list(entry.get("deny_tools"), f"scopes[{i}].deny_tools"),
    )


def load_default_mode(path: Path = CONFIG_PATH) -> str:
    """Return the configured default session mode: ``"ask"`` or ``"auto"``.

    Reads ``aipager.yaml`` and returns the ``default_mode`` key. When the
    file is absent, unreadable, or the key is missing, returns ``"ask"``
    so the safe default is always explicit.
    """
    if not path.exists():
        return "ask"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return "ask"
    if not isinstance(raw, dict):
        return "ask"
    mode = raw.get("default_mode", "ask")
    if mode not in ("ask", "auto"):
        return "ask"
    return mode


def load_scopes(path: Path = CONFIG_PATH) -> tuple[list[Scope], str] | None:
    """Load ``aipager.yaml``.

    Returns ``(scopes, bot_token)`` or ``None`` if the file is absent.
    Raises :class:`ScopeConfigError` on malformed content. Role names
    are NOT validated here — that's
    :func:`aipager.policy.validate_scopes_against_policy`.
    """
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ScopeConfigError(f"aipager.yaml parse error: {e}") from e
    if not isinstance(raw, dict):
        raise ScopeConfigError("aipager.yaml: expected a mapping at the top level")

    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ScopeConfigError(
            f"aipager.yaml: schema_version must be {SCHEMA_VERSION}, got {version!r}"
        )

    bot_token = str(raw.get("bot_token", "")).strip()
    if not bot_token:
        raise ScopeConfigError("aipager.yaml: `bot_token` must be non-empty")

    scopes_raw = raw.get("scopes")
    if not isinstance(scopes_raw, list) or not scopes_raw:
        raise ScopeConfigError("aipager.yaml: `scopes` must be a non-empty list")
    scopes = [_parse_scope(s, i) for i, s in enumerate(scopes_raw)]

    chat_ids = [s.chat_id for s in scopes]
    if len(set(chat_ids)) != len(chat_ids):
        raise ScopeConfigError("aipager.yaml: duplicate scope chat_id")

    return scopes, bot_token


def _member_to_dict(m: Member) -> dict:
    out: dict = {"id": m.id, "label": m.label, "role": m.role}
    if m.deny_tools:
        out["deny_tools"] = list(m.deny_tools)
    if m.allow_tools:
        out["allow_tools"] = list(m.allow_tools)
    if m.bypass_safety is not None:
        out["bypass_safety"] = m.bypass_safety
    if m.bypass_role_denies is not None:
        out["bypass_role_denies"] = m.bypass_role_denies
    return out


def dump_scopes(
    scopes: list[Scope], bot_token: str, path: Path = CONFIG_PATH,
    *, default_mode: str = "",
) -> None:
    """Serialize scopes to ``aipager.yaml`` (atomic write, mode 0600).

    ``default_mode`` is written as a top-level ``default_mode:`` key when
    non-empty (``"ask"`` or ``"auto"``). Passing ``""`` (the default) leaves
    any existing ``default_mode`` key in the file unchanged — this function
    reads the existing value and re-emits it, so callers that don't care
    about the mode key don't accidentally wipe it.
    """
    # Preserve the existing default_mode when the caller didn't pass one.
    if not default_mode:
        default_mode = load_default_mode(path)
    data: dict = {
        "schema_version": SCHEMA_VERSION,
        "bot_token": bot_token,
        "scopes": [],
    }
    for s in scopes:
        sd: dict = {
            "kind": s.kind,
            "chat_id": s.chat_id,
            "label": s.label,
            "members": [_member_to_dict(m) for m in s.members],
        }
        if s.deny_tools:
            sd["deny_tools"] = list(s.deny_tools)
        data["scopes"].append(sd)

    # Write default_mode when it's non-default (auto) or when explicitly
    # requested — always preserve if already set.
    if default_mode and default_mode != "ask":
        data["default_mode"] = default_mode
    elif default_mode == "ask":
        # Explicitly set to ask — write it so the file is self-documenting.
        data["default_mode"] = default_mode

    body = (
        _AIPAGER_YAML_HEADER
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
