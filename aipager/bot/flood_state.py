"""Per-chat flood state that outlives the process (roadmap 8.28, R5).

Until 0.7.12 nothing about a flood penalty survived a restart. The mute
registry was process memory on the monotonic clock, ``flood.py`` said so
in its own docstring, and ``BudgetRateLimiter.initialize`` said "a restart
starts every chat at ×1" and unlinked the backoff file to make it true.

That is the defect this module closes. On 2026-09-15 a daemon restarted
during a penalty regime came back knowing nothing and sent its startup
notice straight into an active ban; three bans that day escalated
``retry_after`` 1283 -> 312 -> 34212 (9.5 h). Telegram's memory is
measured in hours. Ours was measured in process lifetimes.

**This module owns the FILE and nothing else.** It asks ``flood.MUTE``
and the live limiter for their serialised parts, merges them per chat id,
and writes. It holds no state of its own beyond a dirty flag and the
stamp of the last write, so there is no second copy of the truth to drift
out of step with the first.

**The mechanism is reused, not reinvented.** ``mark_dirty()`` sets a flag;
``session_monitor._sweep_flood_backoff()`` calls ``save_if_dirty()`` on
the 2 s tick it already runs. That dirty-flag-plus-one-tick IS the whole
debounce, exactly as ``state.py`` + ``session_monitor.py`` are for the
session registry. No timer of our own to leak.

**Why a third file rather than an existing one.** The two runtime signals
(``FLOOD_MUTE_FILE``, ``FLOOD_BACKOFF_FILE``) live under
``$XDG_RUNTIME_DIR`` on tmpfs, are wiped by a reboot, and are UNLINKED
whenever nothing is muted or backing off — a healthy chat's earned rate
would never be visible in them. And ``aipager-sessions.json`` is
serialised per SESSION by a module that must not import ``flood_budget``,
while this state is per CHAT. Two writers on one path also race, with the
loser's half silently lost.

Nothing here ever raises into a caller: every ``OSError`` is swallowed at
debug. A full disk must not be able to stop a send.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Schema version. A document with any other value is ignored outright
#: rather than guessed at — starting fresh is always safe here, because
#: everything this file holds is re-learned within hours.
SCHEMA_VERSION = 1

_dirty: bool = False
_last_write_at: float = 0.0


def mark_dirty() -> None:
    """Note that something material changed: a rate, a 429, a ban, a mute
    armed / extended / lifted, minimal mode toggling, the first sight of a
    chat. The write itself happens on the next monitor tick."""
    global _dirty
    _dirty = True


def is_dirty() -> bool:
    return _dirty


def _path(path=None) -> Path:
    """Resolve the state file LATE, from ``config``.

    Read through the module rather than imported by value on purpose: it
    is what lets ``tests/conftest.py::_isolate_home_paths`` redirect the
    file with a single entry. A by-value import here would need its own
    entry there, and the day someone adds one without it the suite starts
    writing into the operator's real ``~/.claude/`` — which
    ``_guard_real_home`` deliberately does not watch.
    """
    from aipager import config

    return Path(path or config.FLOOD_STATE_FILE)


def _document() -> dict:
    """The current state of both components, merged per chat id."""
    from aipager.bot.flood import MUTE
    from aipager.bot.flood_budget import BudgetRateLimiter
    from aipager.bot.rich_message import get_rate_limiter

    limiter = get_rate_limiter()
    chats: dict = {}
    if isinstance(limiter, BudgetRateLimiter):
        for entry in limiter.serialise():
            chats[str(entry["chat_id"])] = dict(entry)
    for entry in MUTE.serialise():
        key = str(entry.get("chat_id"))
        merged = chats.setdefault(key, {"chat_id": entry.get("chat_id")})
        merged["muted_until"] = entry.get("until")
        merged["mute_retry_after"] = entry.get("retry_after")
    return {
        "version": SCHEMA_VERSION,
        "written_at": time.time(),
        # An ARRAY, not an object: a JSON object key is always a string,
        # so an int chat id would come back as "-1001234567" and land on a
        # different budget than the one it left.
        "chats": list(chats.values()),
    }


def save_if_dirty(*, path=None, force: bool = False) -> bool:
    """Write the state file when something changed. Returns whether it did.

    Debounced by ``config.FLOOD_STATE_MIN_INTERVAL`` unless *force* —
    ``lifecycle.stop()`` forces, because there is no next tick.

    Atomic: ``tmp.write_text`` then ``os.replace``, the same shape
    ``SessionRegistry.save`` and both signal writers use, so a reader in
    another process never sees a half-written document.
    """
    global _dirty, _last_write_at

    if not _dirty and not force:
        return False
    now = time.monotonic()
    if (not force and _last_write_at
            and (now - _last_write_at) < _min_interval()):
        return False
    target = _path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(_document(), indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        log.debug("could not write the flood state file %s", target,
                  exc_info=True)
        return False
    except Exception:  # pragma: no cover - defensive
        log.debug("could not serialise the flood state", exc_info=True)
        return False
    _dirty = False
    _last_write_at = now
    return True


def _min_interval() -> float:
    from aipager import config

    return config.FLOOD_STATE_MIN_INTERVAL


def read(*, path=None) -> dict:
    """The raw parsed document, or ``{}``.

    ``{}`` for a missing, unreadable, malformed or wrong-``version`` file.
    Never inferred, never guessed: every consumer treats an empty document
    as "start fresh", which is always safe.
    """
    target = _path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return {}
    return data


def load(*, path=None) -> bool:
    """Restore into ``flood.MUTE`` and the live limiter. Never raises.

    Returns whether anything was restored. Also refreshes both runtime
    SIGNAL files so ``aipager status`` agrees with what the daemon now
    believes — the signals are cleared at start as stale-signal hygiene,
    and this is what puts back the part that is not stale.
    """
    from aipager.bot.flood import MUTE
    from aipager.bot.flood_budget import BudgetRateLimiter
    from aipager.bot.rich_message import get_rate_limiter

    data = read(path=path)
    if not data:
        return False
    chats = data.get("chats")
    if not isinstance(chats, list):
        return False

    restored = 0
    try:
        limiter = get_rate_limiter()
        if isinstance(limiter, BudgetRateLimiter):
            restored += limiter.restore(chats)
            limiter.sweep()
        restored += MUTE.restore([
            {"chat_id": c.get("chat_id"),
             "until": c.get("muted_until"),
             "retry_after": c.get("mute_retry_after")}
            for c in chats
            if isinstance(c, dict) and c.get("muted_until")
        ])
    except Exception:  # pragma: no cover - defensive
        log.debug("could not restore the flood state", exc_info=True)
        return False
    if restored:
        log.info("flood state restored for %d chat(s) from %s",
                 restored, _path(path))
    return bool(restored)


def clear(*, path=None) -> None:
    """Unlink the state file and forget the dirty flag. Idempotent."""
    global _dirty, _last_write_at

    _dirty = False
    _last_write_at = 0.0
    try:
        _path(path).unlink(missing_ok=True)
    except OSError:
        log.debug("could not unlink the flood state file", exc_info=True)


__all__ = [
    "SCHEMA_VERSION",
    "clear",
    "is_dirty",
    "load",
    "mark_dirty",
    "read",
    "save_if_dirty",
]
