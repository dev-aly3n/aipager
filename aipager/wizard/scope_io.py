"""Wizard ↔ ``aipager.yaml`` glue (see :mod:`aipager.wizard`).

Thin helpers the wizard uses to read the current scope config and to
commit scopes one at a time. Every write goes through
:func:`aipager.scope.dump_scopes` (atomic temp + ``os.replace``, mode
0600), so ``aipager.yaml`` is never left half-written and the daemon
can always start. See architecture §3.0b.
"""

from __future__ import annotations

from aipager import scope as _scope
from aipager.scope import Scope


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
