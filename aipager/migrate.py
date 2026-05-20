"""One-shot migration from v1 config (config.env / team.yaml) to v2.

Phase A behavior: **generate** ``aipager.yaml`` + seed ``policy.yaml``
from the current install, **without retiring the v1 files**. The
runtime still authorizes via the existing ``TEAM`` / ``CHAT_ID`` in
Phase A, so we keep ``config.env`` / ``team.yaml`` in place and only
*copy* them to timestamped backups. Retirement happens later, when v2
becomes authoritative.

Idempotent: a no-op once ``aipager.yaml`` exists.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from aipager import scope as _scope
from aipager import team as _team

log = logging.getLogger(__name__)

# Old → new role name mapping (the "developer" role was renamed "user").
_ROLE_MAP = {
    "admin": "admin",
    "developer": "user",
    "read_only": "read_only",
}

_POLICY_SEED = """\
# aipager — policy (the "what"). USER-OWNED: `aipager config` never
# writes this file, so anything you put here survives wizard re-runs.
#
# Built-in roles (owner / admin / user / read_only) and the safety
# floor are baked into the daemon; you only need entries here to
# OVERRIDE a built-in role, ADD a custom role, or tighten safety.
#
# Examples (uncomment + edit):
#
# roles:
#   user:
#     deny_tools: [Bash]              # this install's `user` can't Bash
#   reviewer:                         # a custom read-only role
#     allow_tools: [Read, Grep, Glob]
#     can_approve: false
#
# safety:                             # union-only: can tighten, never loosen
#   deny_paths_no_write: ["**/*.lock"]
"""


def _backup(src: Path) -> Path | None:
    """Copy ``src`` to ``src.bak.<ts>`` (leaving the original). No-op if absent."""
    if not src.exists():
        return None
    dst = src.with_suffix(src.suffix + f".bak.{int(time.time())}")
    shutil.copy2(src, dst)
    return dst


def _scopes_from_current() -> tuple[list[_scope.Scope], str] | None:
    """Derive v2 scopes from the live v1 config. None if nothing to migrate."""
    from aipager import config

    bot_token = config.BOT_TOKEN
    if not bot_token:
        return None

    team = _team.load_team(_team.TEAM_CONFIG_PATH)
    if team is not None:
        members = tuple(
            _scope.Member(
                id=u.id,
                label=u.label,
                role=_ROLE_MAP.get(u.role.value, "user"),
            )
            for u in team.users.values()
        )
        sc = _scope.Scope(
            chat_id=team.group_id,
            kind="group",
            label="team",
            members=members,
            deny_tools=tuple(team.rules.deny_tools),
        )
        return [sc], bot_token

    # Personal mode: one DM scope, single admin member (NOT owner —
    # silent migration must not grant god-mode).
    chat_id = config.CHAT_ID
    if not chat_id:
        return None
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return None
    sc = _scope.Scope(
        chat_id=cid,
        kind="dm",
        label="owner DM",
        members=(_scope.Member(id=cid, label="owner", role="admin"),),
    )
    return [sc], bot_token


def migrate_to_v2() -> bool:
    """Generate ``aipager.yaml`` + seed ``policy.yaml`` from v1 config.

    Returns True iff it wrote ``aipager.yaml``. Idempotent: a no-op
    once ``aipager.yaml`` already exists or there's nothing to migrate.
    """
    from aipager import config
    from aipager.policy import POLICY_PATH

    if _scope.CONFIG_PATH.exists():
        return False

    derived = _scopes_from_current()
    if derived is None:
        return False
    scopes, bot_token = derived

    _scope.dump_scopes(scopes, bot_token, _scope.CONFIG_PATH)

    # Back up v1 files (copy, not move — they stay authoritative in Phase A).
    _backup(config._XDG_CONFIG)
    _backup(_team.TEAM_CONFIG_PATH)

    # Seed policy.yaml only if absent (never overwrite a user's file).
    if not POLICY_PATH.exists():
        POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
        POLICY_PATH.write_text(_POLICY_SEED, encoding="utf-8")

    log.info("migrated to aipager.yaml (v2); v1 files backed up + retained")
    return True


def retire_v1() -> bool:
    """Rename the v1 ``config.env`` / ``team.yaml`` to ``*.retired.<ts>``.

    Only acts once v2 is authoritative: ``aipager.yaml`` must load
    cleanly AND carry a bot_token, so a broken/absent v2 can never
    strand the daemon. The Phase-A ``.bak.<ts>`` copies remain
    regardless. Idempotent — a no-op once the v1 files are gone.
    Returns True if it renamed anything.
    """
    from aipager import config

    try:
        loaded = _scope.load_scopes(_scope.CONFIG_PATH)
    except _scope.ScopeConfigError:
        return False
    if not loaded or not loaded[1]:
        return False  # v2 not loadable / no token → keep v1 as-is

    ts = int(time.time())
    renamed = False
    for src in (config._XDG_CONFIG, _team.TEAM_CONFIG_PATH):
        if src.exists():
            src.rename(src.with_suffix(src.suffix + f".retired.{ts}"))
            renamed = True
    if renamed:
        log.info("retired v1 config (config.env/team.yaml → *.retired.%d)", ts)
    return renamed
