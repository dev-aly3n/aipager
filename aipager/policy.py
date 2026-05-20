"""Role permission profiles + the hard-safety policy (Phase A: load only).

Two ownership domains back this module:

- ``policy.yaml`` / ``policy.d/*.yaml`` — **user-owned**. The wizard
  never writes them. They define custom roles, override built-in role
  defaults, and tune the safety floor.
- built-in defaults — live in :mod:`aipager.safety`.

``load_policy`` merges them into a :class:`Policy`. **Role definitions
layer with replace-per-field semantics**; the **safety floor is
union-only** (built-in baseline can never be shrunk by a policy edit).

Phase A only *loads + validates* — nothing here is enforced yet.
See ``researches/multi-scope-mode/02-security-model.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import yaml

from aipager import safety

POLICY_PATH: Path = Path.home() / ".config" / "aipager" / "policy.yaml"
POLICY_D_DIR: Path = Path.home() / ".config" / "aipager" / "policy.d"


class PolicyError(Exception):
    """Raised when a policy file is present but malformed.

    The daemon refuses to start on this rather than silently running
    with a misunderstood permission policy.
    """


@dataclass(frozen=True)
class Role:
    """A named permission profile. See the security-model doc."""

    name: str
    bypass_safety: bool = False
    bypass_role_denies: bool = False
    can_prompt: bool = True
    can_approve: bool = True
    deny_tools: tuple[str, ...] = ()
    allow_tools: tuple[str, ...] = ()
    deny_paths_no_access: tuple[str, ...] = ()
    deny_paths_no_write: tuple[str, ...] = ()
    allow_paths: tuple[str, ...] = ()
    deny_bash_patterns: tuple[str, ...] = ()
    auto_approve: bool = False


# Fields a role mapping in policy.yaml may set (everything except ``name``).
_ROLE_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(Role) if f.name != "name"
)
_ROLE_BOOL_FIELDS: frozenset[str] = frozenset(
    {"bypass_safety", "bypass_role_denies", "can_prompt", "can_approve",
     "auto_approve"}
)
_ROLE_LIST_FIELDS: frozenset[str] = _ROLE_FIELDS - _ROLE_BOOL_FIELDS


@dataclass(frozen=True)
class Policy:
    """In-memory view of the merged role + safety policy."""

    roles: dict[str, Role] = field(default_factory=dict)
    safety_deny_paths_no_access: tuple[str, ...] = ()
    safety_deny_paths_no_write: tuple[str, ...] = ()
    safety_deny_bash_patterns: tuple[str, ...] = ()

    def get_role(self, name: str) -> Role | None:
        return self.roles.get(name)


def _builtin_roles() -> dict[str, Role]:
    """Construct ``Role`` objects from :data:`safety.BUILTIN_ROLE_DEFAULTS`."""
    out: dict[str, Role] = {}
    for name, spec in safety.BUILTIN_ROLE_DEFAULTS.items():
        out[name] = Role(name=name, **spec)
    return out


def _coerce_role_overrides(name: str, spec: dict, source: str) -> dict:
    """Validate + normalize a role mapping from a policy file.

    Returns the kwargs to ``dataclasses.replace`` onto an existing
    ``Role``. Raises :class:`PolicyError` on unknown keys / bad types.
    """
    if not isinstance(spec, dict):
        raise PolicyError(f"{source}: role {name!r} must be a mapping")
    unknown = set(spec) - _ROLE_FIELDS
    if unknown:
        raise PolicyError(
            f"{source}: role {name!r} has unknown key(s): "
            f"{', '.join(sorted(unknown))}"
        )
    out: dict = {}
    for key, val in spec.items():
        if key in _ROLE_BOOL_FIELDS:
            if not isinstance(val, bool):
                raise PolicyError(
                    f"{source}: role {name!r}.{key} must be true/false"
                )
            out[key] = val
        else:  # list field
            if not isinstance(val, list) or not all(
                isinstance(x, str) for x in val
            ):
                raise PolicyError(
                    f"{source}: role {name!r}.{key} must be a list of strings"
                )
            out[key] = tuple(val)
    return out


def _read_yaml_mapping(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PolicyError(f"{path.name}: YAML parse error: {e}") from e
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyError(f"{path.name}: expected a mapping at the top level")
    return raw


def _policy_files(policy_path: Path, policy_d: Path) -> list[Path]:
    """Ordered list of policy files: policy.yaml then policy.d/*.yaml."""
    files: list[Path] = []
    if policy_path.exists():
        files.append(policy_path)
    if policy_d.is_dir():
        files.extend(sorted(policy_d.glob("*.yaml")))
    return files


def _apply_layer(
    raw: dict,
    source: str,
    roles: dict[str, Role],
    safety_paths_na: list[str],
    safety_paths_nw: list[str],
    safety_bash: list[str],
) -> None:
    """Merge one policy file's contents into the accumulators (in place).

    Roles: replace-per-field. Safety: union (floor can only grow).
    """
    roles_raw = raw.get("roles") or {}
    if not isinstance(roles_raw, dict):
        raise PolicyError(f"{source}: `roles` must be a mapping")
    for name, spec in roles_raw.items():
        overrides = _coerce_role_overrides(str(name), spec, source)
        base = roles.get(name) or Role(name=str(name))
        roles[name] = replace(base, **overrides)

    safety_raw = raw.get("safety") or {}
    if not isinstance(safety_raw, dict):
        raise PolicyError(f"{source}: `safety` must be a mapping")
    for key, acc in (
        ("deny_paths_no_access", safety_paths_na),
        ("deny_paths_no_write", safety_paths_nw),
        ("deny_bash_patterns", safety_bash),
    ):
        vals = safety_raw.get(key) or []
        if not isinstance(vals, list) or not all(isinstance(x, str) for x in vals):
            raise PolicyError(
                f"{source}: `safety.{key}` must be a list of strings"
            )
        for v in vals:
            if v not in acc:
                acc.append(v)

    unknown_safety = set(safety_raw) - {
        "deny_paths_no_access", "deny_paths_no_write", "deny_bash_patterns",
    }
    if unknown_safety:
        raise PolicyError(
            f"{source}: `safety` has unknown key(s): "
            f"{', '.join(sorted(unknown_safety))}"
        )


def load_policy(
    policy_path: Path = POLICY_PATH, policy_d: Path = POLICY_D_DIR
) -> Policy:
    """Build the merged :class:`Policy`.

    Starts from built-in role defaults + the built-in safety floor,
    then layers ``policy.yaml`` and ``policy.d/*.yaml`` (lexical).
    Missing files → built-in defaults only (zero-config works).
    """
    roles = _builtin_roles()
    safety_paths_na = list(safety.DENY_PATHS_NO_ACCESS)
    safety_paths_nw = list(safety.DENY_PATHS_NO_WRITE)
    safety_bash = list(safety.DENY_BASH_PATTERNS)

    for path in _policy_files(policy_path, policy_d):
        raw = _read_yaml_mapping(path)
        _apply_layer(raw, path.name, roles,
                     safety_paths_na, safety_paths_nw, safety_bash)

    # Surface bad regexes at load time (union floor + any role patterns).
    for pat in (*safety_bash, *(p for r in roles.values()
                                for p in r.deny_bash_patterns)):
        try:
            re.compile(pat)
        except re.error as e:
            raise PolicyError(f"invalid bash deny pattern {pat!r}: {e}") from e

    return Policy(
        roles=roles,
        safety_deny_paths_no_access=tuple(safety_paths_na),
        safety_deny_paths_no_write=tuple(safety_paths_nw),
        safety_deny_bash_patterns=tuple(safety_bash),
    )


def validate_scopes_against_policy(scopes, policy: Policy) -> None:
    """Raise :class:`PolicyError` if any member references an unknown role."""
    available = ", ".join(sorted(policy.roles)) or "(none)"
    for scope in scopes:
        for member in scope.members:
            if member.role not in policy.roles:
                raise PolicyError(
                    f"scope {scope.label!r} member {member.label!r} "
                    f"references undefined role {member.role!r}. "
                    f"Available roles: {available}"
                )


def validate_policy_files(
    scopes=None,
    policy_path: Path = POLICY_PATH,
    policy_d: Path = POLICY_D_DIR,
) -> list[str]:
    """Pure lint. Returns a list of human-readable problems (empty = OK).

    Mutates nothing. Used by ``aipager policy validate``.
    """
    problems: list[str] = []
    try:
        policy = load_policy(policy_path, policy_d)
    except PolicyError as e:
        return [str(e)]
    if scopes is not None:
        try:
            validate_scopes_against_policy(scopes, policy)
        except PolicyError as e:
            problems.append(str(e))
    return problems
