"""Wizard ↔ ``aipager.yaml`` glue (see :mod:`aipager.wizard`).

Thin helpers the wizard uses to read the current scope config and to
commit scopes one at a time. Every write goes through
:func:`aipager.scope.dump_scopes` (atomic temp + ``os.replace``, mode
0600), so ``aipager.yaml`` is never left half-written and the daemon
can always start. See architecture §3.0b.
"""

from __future__ import annotations

from dataclasses import replace

from aipager import scope as _scope
from aipager.scope import Member, Scope


def config_exists() -> bool:
    """True iff ``aipager.yaml`` is on disk."""
    return _scope.CONFIG_PATH.exists()


def read_config() -> tuple[list[Scope], str]:
    """Return ``(scopes, bot_token)`` from ``aipager.yaml``.

    ``([], "")`` when the file is absent. A malformed file raises
    :class:`aipager.scope.ScopeConfigError` (callers fail loud).
    """
    # Read ``CONFIG_PATH`` at call time (not the def-time-bound default)
    # so tests can redirect it via ``monkeypatch.setattr``.
    loaded = _scope.load_scopes(_scope.CONFIG_PATH)
    if loaded is None:
        return [], ""
    scopes, token = loaded
    return scopes, token


def replace_scopes(scopes: list[Scope], token: str) -> None:
    """Overwrite ``aipager.yaml`` with the given scopes + token."""
    _scope.dump_scopes(scopes, token, _scope.CONFIG_PATH)


def commit_scope(new: Scope, token: str) -> None:
    """Add or update ``new`` in ``aipager.yaml`` (matched by chat_id).

    Loads the current scopes, replaces any scope with the same
    ``chat_id`` (or appends), and atomically rewrites the file.
    """
    scopes, existing_token = read_config()
    token = token or existing_token
    out = [s for s in scopes if s.chat_id != new.chat_id]
    out.append(new)
    _scope.dump_scopes(out, token, _scope.CONFIG_PATH)


def remove_scope(chat_id: int) -> bool:
    """Drop the scope with ``chat_id``. Returns True iff one was removed.

    Refuses to write an empty scope list (``aipager.yaml`` requires at
    least one scope) — returns False instead.
    """
    scopes, token = read_config()
    out = [s for s in scopes if s.chat_id != chat_id]
    if len(out) == len(scopes):
        return False
    if not out:
        return False
    _scope.dump_scopes(out, token, _scope.CONFIG_PATH)
    return True


def configured_scope_for(chat_id: int, scopes: list[Scope] | None = None
                         ) -> Scope | None:
    """The scope already set up for *chat_id*, or ``None``. A group Telegram
    upgraded to a supergroup counts under either id (roadmap 8.87): its
    old id names the same group."""
    if scopes is None:
        scopes, _ = read_config()
    moved = _scope.load_chat_migrations(_scope.CONFIG_PATH)
    ids = {chat_id, moved.get(chat_id, chat_id)}
    ids |= {old for old, new in moved.items() if new == chat_id}
    return next((s for s in scopes if s.chat_id in ids), None)


def configured_chat_ids() -> frozenset[int]:
    """Every chat already set up, with the other id of each group
    Telegram upgraded (roadmap 8.87): what group auto-detect looks past
    (roadmap 8.88). Empty when ``aipager.yaml`` is absent or unreadable
    (the commit still refuses a duplicate)."""
    try:
        scopes, _ = read_config()
    except Exception:
        return frozenset()
    ids = {s.chat_id for s in scopes}
    for old, new in _scope.load_chat_migrations(_scope.CONFIG_PATH).items():
        if old in ids or new in ids:
            ids |= {old, new}
    return frozenset(ids)


def add_new_scope(new: Scope, token: str) -> bool:
    """Append ``new`` to ``aipager.yaml`` unless a scope for its chat is
    already there. Returns True iff written. Never replaces a scope: that
    would drop its members (roadmap 8.88), so the caller tells the
    operator to add members to it instead."""
    scopes, existing_token = read_config()
    if configured_scope_for(new.chat_id, scopes) is not None:
        return False
    _scope.dump_scopes([*scopes, new], token or existing_token,
                       _scope.CONFIG_PATH)
    return True


def append_member(chat_id: int, member: Member, token: str) -> str | None:
    """Add *member* to the scope of *chat_id* as it is on disk NOW (other
    members, roles and rules kept). Returns ``None`` when written, else
    why not (no such scope, already a member, label taken)."""
    scopes, existing_token = read_config()
    current = next((s for s in scopes if s.chat_id == chat_id), None)
    if current is None:
        return "That scope is no longer in aipager.yaml."
    if any(m.id == member.id for m in current.members):
        return f"User id {member.id} is already a member of {current.label}."
    if any(m.label == member.label for m in current.members):
        return (f"Label {member.label!r} is already used in "
                f"{current.label}.")
    out = [replace(s, members=(*s.members, member))
           if s.chat_id == chat_id else s for s in scopes]
    _scope.dump_scopes(out, token or existing_token, _scope.CONFIG_PATH)
    return None
