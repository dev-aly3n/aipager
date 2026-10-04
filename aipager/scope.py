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
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

CONFIG_PATH: Path = Path.home() / ".config" / "aipager" / "aipager.yaml"

SCHEMA_VERSION = 3
# v2 files load unchanged: v3 only ADDS the optional `miniapp:` block, so
# every v2 document is a valid v3 document minus that key. Accepting both
# is what keeps an existing install from dying with ScopeConfigError the
# moment it upgrades — the version is rewritten to 3 lazily, the next
# time anything calls dump_scopes()/dump_miniapp().
_READABLE_SCHEMA_VERSIONS = (2, 3)
_KINDS = ("dm", "group")

# Mini App settings live here rather than in config.env because
# `migrate.retire_v1()` renames config.env away on every daemon start once
# aipager.yaml is authoritative — a setting written there survives exactly
# one restart. aipager.yaml is never retired.
_MINIAPP_KEY = "miniapp"
_MINIAPP_DEFAULTS: dict = {"enabled": True, "port": 8765, "public_url": ""}

# claude_path override — see claude_resolve.py's precedence chain (tier 1).
# Same rebuild-vs-surgical-write split as miniapp: dump_claude_path() is the
# surgical read-modify-write, and this key must ALSO join dump_scopes()'s
# preservation list below, or the next `aipager config` silently wipes it.
_CLAUDE_PATH_KEY = "claude_path"

# Roadmap 8.87: groups Telegram upgraded to a supergroup, ``{old id: new
# id}``. Written by :func:`migrate_scope_chat_id` (the daemon follows the
# upgrade by itself) and read by :func:`load_chat_migrations`, so a session
# whose internal name still carries the old id (``__g<old>``, names are
# internal and never renamed) is placed in the new chat, even after the
# session registry was lost. In aipager.yaml rather than the registry for
# exactly that reason. Same preservation rule as the keys above:
# dump_scopes() must re-emit it.
_CHAT_MIGRATIONS_KEY = "chat_migrations"


def scope_suffix(chat_id: int, kind: str) -> str:
    """Internal socket/name suffix that disambiguates same-labeled
    sessions across scopes: ``<kind[0]><abs(chat_id)>`` (e.g. the DM
    chat 256113222 → ``d256113222``; group -4152307515 → ``g4152307515``).
    The leading ``-`` of a group id is encoded by the ``g`` prefix, so
    the result is filesystem-safe. The full chat_id (not a truncation)
    is used so the suffix is collision-free.
    """
    return f"{kind[0]}{abs(chat_id)}"


# Inverse of the suffix `scope_suffix` appends. Anchored to the END and
# requiring digits, because a label may legitimately contain "__"
# (`inject._VALID_NAME` allows underscores) — a naive split would turn
# "my__thing" into "my". `[dg]` are the only first characters
# `scope_suffix` can emit, from kind "dm" / "group".
_SCOPE_SUFFIX_RE = re.compile(r"__[dg]\d+$")


def strip_scope_suffix(name: str) -> str:
    """Undo :func:`scope_suffix`'s contribution to a disambiguated name.

    ``"Jkhk__d256113222"`` → ``"Jkhk"``; ``"my__thing"`` (no suffix) and
    ``"my__dabc"`` (not digits) are returned unchanged. Only a trailing
    suffix is removed, so ``"my__thing__d123"`` → ``"my__thing"``.
    """
    return _SCOPE_SUFFIX_RE.sub("", name)


def chat_from_suffix(name: str) -> tuple[int, str] | None:
    """The chat a scope suffix names, or ``None`` for a name without one.

    The inverse of :func:`scope_suffix` on a whole session name:
    ``"claude-api__d555"`` → ``(555, "dm")``; ``"claude-x__g1001"`` →
    ``(-1001, "group")`` (the ``g`` prefix encodes the group id's sign).
    Same anchored pattern as :func:`strip_scope_suffix`, so
    ``"my__thing"`` and ``"my__dabc"`` have no suffix.
    """
    m = _SCOPE_SUFFIX_RE.search(name)
    if m is None:
        return None
    tag = m.group(0)[2:]
    n = int(tag[1:])
    if n == 0:
        # No chat has id 0 (0 means "unstamped" in the registry).
        return None
    if tag[0] == "g":
        return -n, "group"
    return n, "dm"


def home_scope(scopes, policy) -> Scope | None:
    """The chat a session with no chat of its own belongs to (roadmap 8.82).

    In order: the first DM scope (yaml order) with a member whose role has
    ``bypass_safety`` (the owner's own DM); else the first DM scope; else
    the first scope. ``None`` for no scopes. A lone scope is therefore
    always its own home (it is the first DM or the first scope). Never
    "prefer the group": a session started in the terminal is the owner's
    private work, and a group is shared with other people. *policy* may be
    ``None`` (no role can then qualify). Pure.
    """
    if not scopes:
        return None
    if policy is not None:
        for s in scopes:
            if s.kind != "dm":
                continue
            for m in s.members:
                role = policy.get_role(m.role)
                if role is not None and role.bypass_safety:
                    return s
    for s in scopes:
        if s.kind == "dm":
            return s
    return scopes[0]


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
# aipager - multi-scope config (the "who"). Managed by `aipager config`.
# For custom roles + safety rules, edit policy.yaml; that file is
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
    """Return the file's ``default_mode`` key: ``"ask"`` or ``"auto"``.

    Kept only so an older ``aipager.yaml`` round-trips through
    :func:`dump_scopes` (roadmap 8.89). Nothing acts on the key: the
    daemon ignores it, and the wizard no longer asks for it. New sessions
    take their mode from ``/settings → New sessions``
    (``new_flow.resolve_new_session_settings``). When the file is absent,
    unreadable, or the key is missing, returns ``"ask"``.
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


def _raw_yaml(path: Path) -> dict:
    """Best-effort read of the whole document as a plain dict.

    Never raises: the Mini App accessors below must work from a cold CLI
    process against a file that may be absent or half-written, the same
    tolerance :func:`load_default_mode` already applies.
    """
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _atomic_write_yaml(data: dict, path: Path) -> None:
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


def load_miniapp(path: Path = CONFIG_PATH) -> dict:
    """Return the Mini App block: ``{enabled: bool, port: int, public_url: str}``.

    Missing file, missing key, or a malformed value each fall back to the
    built-in default for that field — **on**, 8765, no override.

    That default used to be *off*, and this docstring used to say a
    damaged config "can never turn the listener ON by accident". That is
    no longer true and pretending otherwise would be worse than the
    change itself: a missing or unreadable config now starts the Mini App
    and, with it, a public tunnel. The trade was made deliberately — an
    opt-in nobody discovers is a feature that does not exist — but it is
    a real widening of what a broken config can do.

    What is preserved is the strictness for **explicit** values: the
    ``is True`` test below means a hand-edited ``enabled: "no"`` (or
    ``"false"``, or ``"off"``) is still a string, still not ``True``, and
    still reads as OFF. Anyone who has deliberately turned this off keeps
    it off; only the absent-or-corrupt case flipped.
    """
    block = _raw_yaml(path).get(_MINIAPP_KEY)
    out = dict(_MINIAPP_DEFAULTS)
    if not isinstance(block, dict):
        return out
    # `is True`, not bool(): a hand-edited `enabled: "no"` (or "false",
    # or "off") is a non-empty string, and bool() would read all three as
    # True — opening a listening socket for a config that plainly says
    # not to. Only a real YAML boolean enables the server. YAML's own
    # `yes`/`on` already parse to True before reaching here.
    out["enabled"] = block.get("enabled", out["enabled"]) is True
    try:
        out["port"] = int(block.get("port", out["port"]))
    except (TypeError, ValueError):
        pass
    url = block.get("public_url", out["public_url"])
    out["public_url"] = str(url).strip() if url else ""
    return out


def dump_miniapp(settings: dict, path: Path = CONFIG_PATH) -> None:
    """Persist only the ``miniapp:`` block, leaving the rest of the
    document byte-identical.

    Deliberately NOT built on :func:`dump_scopes`, which rebuilds the file
    from a parsed scope list — round-tripping the whole config just to
    flip a boolean would risk dropping any key that function doesn't know
    about. Same atomic-write + 0600 discipline as dump_scopes.
    """
    raw = _raw_yaml(path)
    if not raw:
        raise ScopeConfigError(
            "aipager.yaml is missing or unreadable - run `aipager config` first"
        )
    raw[_MINIAPP_KEY] = {
        "enabled": settings.get("enabled", False) is True,
        "port": int(settings.get("port", _MINIAPP_DEFAULTS["port"])),
        "public_url": str(settings.get("public_url", "") or ""),
    }
    # Writing through this path also lands the current SCHEMA_VERSION, so a
    # v2 file becomes a v3 file the first time the Mini App is configured.
    raw["schema_version"] = SCHEMA_VERSION
    _atomic_write_yaml(raw, path)


def load_claude_path(path: Path = CONFIG_PATH) -> str:
    """Return the configured ``claude_path`` override, or ``""`` if unset
    or the file is absent/unreadable. Never raises."""
    val = _raw_yaml(path).get(_CLAUDE_PATH_KEY)
    return val.strip() if isinstance(val, str) and val.strip() else ""


def dump_claude_path(value: str, path: Path = CONFIG_PATH) -> None:
    """Persist (or clear, with ``value=""``) the ``claude_path`` override.

    Surgical read-modify-write, following :func:`dump_miniapp`'s pattern
    rather than :func:`dump_scopes`'s rebuild-from-scratch — this never
    touches ``scopes``/``bot_token``/anything else in the document.
    """
    raw = _raw_yaml(path)
    if not raw:
        raise ScopeConfigError(
            "aipager.yaml is missing or unreadable - run `aipager config` first"
        )
    value = value.strip()
    if value:
        raw[_CLAUDE_PATH_KEY] = value
    else:
        raw.pop(_CLAUDE_PATH_KEY, None)
    raw["schema_version"] = SCHEMA_VERSION
    _atomic_write_yaml(raw, path)


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
    if version not in _READABLE_SCHEMA_VERSIONS:
        readable = " or ".join(str(v) for v in _READABLE_SCHEMA_VERSIONS)
        raise ScopeConfigError(
            f"aipager.yaml: schema_version must be {readable}, got {version!r}"
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
    about the mode key don't accidentally wipe it. The key is a leftover
    the daemon ignores (roadmap 8.89); it is kept only so an older file
    round-trips unchanged.
    """
    # Preserve an existing default_mode when the caller didn't pass one,
    # and only when the file has one: a file without the key stays
    # without it (nothing reads it any more, roadmap 8.89).
    if not default_mode and "default_mode" in _raw_yaml(path):
        default_mode = load_default_mode(path)
    # Same reasoning for the Mini App block: this function rebuilds the
    # document from scratch, so anything not re-emitted here is silently
    # dropped. Without this, `aipager config` adding a scope would wipe the
    # Mini App settings — the exact silent-loss bug that moving them out of
    # config.env exists to fix.
    existing_miniapp = _raw_yaml(path).get(_MINIAPP_KEY)
    # Same reasoning for claude_path: dump_claude_path() writes it
    # surgically, but this function rebuilds the whole document, so a
    # value it doesn't re-emit is silently dropped on the next
    # `aipager config` run — the exact bug this preservation list exists
    # to prevent (see the miniapp precedent above).
    existing_claude_path = _raw_yaml(path).get(_CLAUDE_PATH_KEY)
    # And the record of groups Telegram upgraded (8.87): without it a
    # session named after a group's old id lands in the home chat again.
    existing_migrations = _raw_yaml(path).get(_CHAT_MIGRATIONS_KEY)
    data: dict = {
        "schema_version": SCHEMA_VERSION,
        "bot_token": bot_token,
        "scopes": [],
    }
    # A group the daemon followed to its supergroup while `aipager config`
    # held the old id in memory (roadmap 8.87): writing the old id back
    # would move the scope back to a chat that no longer exists.
    # load_chat_migrations keeps only group -> group records, so a damaged
    # record never moves a private chat.
    moved = load_chat_migrations(path)
    taken = {s.chat_id for s in scopes}
    for s in scopes:
        chat_id = s.chat_id
        new_id = moved.get(chat_id)
        if new_id is not None and new_id not in taken:
            log.info("aipager.yaml: group %s was upgraded to %s; writing the "
                     "new id", chat_id, new_id)
            chat_id = new_id
        sd: dict = {
            "kind": s.kind,
            "chat_id": chat_id,
            "label": s.label,
            "members": [_member_to_dict(m) for m in s.members],
        }
        if s.deny_tools:
            sd["deny_tools"] = list(s.deny_tools)
        data["scopes"].append(sd)

    # A leftover key, re-emitted so an older file round-trips; the
    # daemon ignores it (roadmap 8.89).
    if default_mode:
        data["default_mode"] = default_mode

    if isinstance(existing_miniapp, dict):
        data[_MINIAPP_KEY] = existing_miniapp

    if isinstance(existing_claude_path, str) and existing_claude_path.strip():
        data[_CLAUDE_PATH_KEY] = existing_claude_path

    if isinstance(existing_migrations, dict) and existing_migrations:
        data[_CHAT_MIGRATIONS_KEY] = existing_migrations

    _atomic_write_yaml(data, path)


# ---- groups Telegram upgraded to a supergroup (roadmap 8.87) ----------------

_migrations_cache: tuple[tuple, dict[int, int]] | None = None


def _as_chat_id(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out or None


def load_chat_migrations(path: Path | None = None) -> dict[int, int]:
    """``{old chat id: new chat id}`` for every group Telegram upgraded to
    a supergroup and the daemon followed (:func:`migrate_scope_chat_id`).
    Empty when the file or the key is absent, or unreadable; a malformed
    entry is skipped. Never raises. Cached on the file's inode, mtime and
    size, since a session discovered by the socket scan asks for it."""
    global _migrations_cache
    path = CONFIG_PATH if path is None else path
    try:
        st = path.stat()
        stamp = (str(path), st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if _migrations_cache is not None and _migrations_cache[0] == stamp:
        return dict(_migrations_cache[1])
    block = _raw_yaml(path).get(_CHAT_MIGRATIONS_KEY)
    out: dict[int, int] = {}
    if isinstance(block, dict):
        for k, v in block.items():
            old, new = _as_chat_id(k), _as_chat_id(v)
            # Only a group moves, and only to a group: a hand-edited or
            # damaged record must never move a private chat's sessions.
            if (old is not None and new is not None and old < 0 and new < 0
                    and old != new):
                out[old] = new
    _migrations_cache = (stamp, out)
    return dict(out)


def follow_chat_migrations(chat_id: int, migrations: dict[int, int]) -> int:
    """Where *chat_id* lives now: follows ``old -> new`` hops. At most one
    hop per record, so a hand-edited loop ends (somewhere on the loop)
    instead of hanging the caller. Pure."""
    for _ in range(len(migrations)):
        if chat_id not in migrations:
            break
        chat_id = migrations[chat_id]
    return chat_id


_CHAT_ID_LINE_RE = re.compile(
    r"^(?P<lead>[ \t]*(?:-[ \t]+)?chat_id:[ \t]*)(?P<q>['\"]?)"
    r"(?P<id>-?\d+)(?P=q)(?P<tail>[ \t]*(?:#.*)?)$",
    re.MULTILINE)


def migrate_scope_chat_id(old: int, new: int, path: Path | None = None) -> None:
    """Move the group scope *old* to *new* in ``aipager.yaml`` (roadmap
    8.87: Telegram upgraded the group to a supergroup) and record
    ``old -> new`` under ``chat_migrations``.

    SURGICAL: only that scope's ``chat_id`` line changes and the record is
    added (a new ``chat_migrations`` block at the end, or one line in the
    block an earlier upgrade wrote), so every other byte of the document
    (comments, key order, the other scopes, the token) stays as it was.
    Only when the text cannot be edited line by line does it fall back to
    the read-modify-write of :func:`dump_miniapp`, which keeps every key
    and value but not the comments: a shape the wizard and this function
    never write (a flow-style scope list, a ``chat_migrations`` mapping
    spread over several flow lines or written twice, the id on two
    lines). A line edit there cannot be checked to have changed exactly
    the one value, and a wrong edit would move the wrong chat; the
    rewrite is exact. Either way the result is re-parsed and checked
    before it is written; atomic, mode 0600.

    Raises :class:`ScopeConfigError` when the file is missing or
    unreadable, when *old* is not a group scope, or when *new* already is
    a scope.
    """
    path = CONFIG_PATH if path is None else path
    try:
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as e:
        raise ScopeConfigError(f"aipager.yaml is unreadable: {e}") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("scopes"), list):
        raise ScopeConfigError("aipager.yaml is missing or has no scopes")
    scopes = raw["scopes"]
    ids = [_as_chat_id(s.get("chat_id")) if isinstance(s, dict) else None
           for s in scopes]
    if new in ids:
        raise ScopeConfigError(f"chat {new} is already a scope")
    hits = [i for i, cid in enumerate(ids) if cid == old]
    if len(hits) != 1 or str(scopes[hits[0]].get("kind", "")).strip() != "group":
        raise ScopeConfigError(f"chat {old} is not a group scope")

    expected = dict(raw)
    expected["scopes"] = [dict(s) if isinstance(s, dict) else s for s in scopes]
    expected["scopes"][hits[0]]["chat_id"] = new
    migrations = raw.get(_CHAT_MIGRATIONS_KEY)
    migrations = dict(migrations) if isinstance(migrations, dict) else {}
    migrations[old] = new
    expected[_CHAT_MIGRATIONS_KEY] = migrations

    body = _surgical_migration_text(text, old, new, raw)
    if body is not None:
        try:
            ok = yaml.safe_load(body) == expected
        except yaml.YAMLError:
            ok = False
        if ok:
            _atomic_write_text(body, path)
            return
    log.warning("aipager.yaml: rewrote the whole document to move scope %s "
                "to %s (the lines could not be edited in place); its "
                "comments were not kept", old, new)
    _atomic_write_yaml(expected, path)


def _surgical_migration_text(text: str, old: int, new: int,
                             raw: dict) -> str | None:
    """*text* with the one ``chat_id: <old>`` line rewritten to *new* and
    ``old: new`` added to ``chat_migrations``, or ``None`` when that edit
    cannot be made safely (the caller then rewrites the document)."""
    matches = [m for m in _CHAT_ID_LINE_RE.finditer(text)
               if int(m.group("id")) == old]
    if not matches:
        # Not on a line of its own (flow style, say): rewrite instead.
        return None
    # A second line with the same id elsewhere: the caller's check of the
    # parsed result refuses an edit of the wrong one.
    m = matches[0]
    out = text[:m.start("id")] + str(new) + text[m.end("id"):]
    if _CHAT_MIGRATIONS_KEY in raw:
        # A second upgrade (another group, after an earlier one wrote the
        # block): one line into that block, so the comments stay.
        return _add_migration_line(out, old, new, raw[_CHAT_MIGRATIONS_KEY])
    if not out.endswith("\n"):
        out += "\n"
    return out + f"{_CHAT_MIGRATIONS_KEY}:\n  {old}: {new}\n"


# The top-level ``chat_migrations:`` key line: its value on the same line
# (empty for a block mapping), then an optional comment.
_MIGRATIONS_KEY_RE = re.compile(
    rf"^{_CHAT_MIGRATIONS_KEY}:(?P<value>[^#\n]*?)[ \t]*(?P<tail>#[^\n]*)?$",
    re.MULTILINE)


def _add_migration_line(text: str, old: int, new: int, current) -> str | None:
    """*text* with ``old: new`` added to its existing ``chat_migrations``
    mapping, edited in place, or ``None`` when its layout is not one of
    the line shapes handled here: a block mapping (what this module
    writes), an empty value (``chat_migrations:`` / ``~`` / ``null``), or
    a one-line flow mapping (``{-1: -2}``). The caller re-parses the
    result and refuses anything that is not exactly the expected
    document."""
    if isinstance(current, dict) and old in current:
        return None     # a key written twice would be the wrong record
    keys = list(_MIGRATIONS_KEY_RE.finditer(text))
    if len(keys) != 1:
        return None
    k = keys[0]
    value = k.group("value").strip()
    entry = f"{old}: {new}"
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        flow = "{" + (f"{inner}, {entry}" if inner else entry) + "}"
        start = k.start("value")
        return text[:start] + " " + flow + text[k.end("value"):]
    if value not in ("", "~", "null", "Null", "NULL"):
        return None
    # The block's lines: everything after the key line that is indented,
    # blank, or a comment, up to the next top-level line.
    lines = text[k.end():].split("\n")
    pos = k.end()       # just before the key line's newline
    last_entry_end = None
    indent = None
    offset = pos
    for i, line in enumerate(lines):
        if i == 0:
            offset += len(line)         # the rest of the key line ("")
            continue
        offset += 1                     # the newline before this line
        stripped = line.strip()
        if stripped and not line[:1].isspace() and not stripped.startswith("#"):
            break                       # the next top-level key
        if stripped and not stripped.startswith("#"):
            if indent is None:
                indent = line[:len(line) - len(line.lstrip())]
            last_entry_end = offset + len(line)
        offset += len(line)
    if last_entry_end is None:
        if value:
            # ``chat_migrations: ~``: the value becomes the block.
            return (text[:k.start("value")] + text[k.end("value"):k.end()]
                    + f"\n  {entry}" + text[k.end():])
        last_entry_end, indent = k.end(), "  "
    return text[:last_entry_end] + f"\n{indent}{entry}" + text[last_entry_end:]


def _atomic_write_text(body: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        log.debug("could not chmod %s", tmp, exc_info=True)
    os.replace(tmp, path)
