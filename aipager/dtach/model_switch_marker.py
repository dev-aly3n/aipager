""""aipager typed this ``/model``" — the marker the PreModelSwitch hook checks.

Claude Code (2.1.251+) runs ``PreModelSwitch`` hooks before a model switch.
A hook that answers ``permissionDecision: "allow"`` makes the switch go
ahead WITHOUT Claude Code's "Switch model? … the full history gets re-read"
confirmation. The dialog is invisible from Telegram and the Mini App, so it
would otherwise leave every remote switch "not confirmed" (roadmap 8.35).

The hook must say "allow" ONLY for a switch aipager itself typed, to that
exact model, within a short window. Every other switch (the operator's own
``/model`` in the terminal, the picker, a stale or foreign marker) gets no
decision at all, so Claude Code behaves exactly as it does without aipager.

Channel: one small JSON file per dtach session, in the directory that
already holds the daemon's control socket (``$XDG_RUNTIME_DIR`` normally —
per-user, mode 0700). The daemon writes it atomically just before typing
``/model``; the hook, a separate short-lived process, reads it with no
socket round-trip (nothing to hang on), checks owner, expiry, Claude
session, source and target, and claims it with an atomic rename so it is
used exactly once.

Stdlib-only and import-light: ``aipager-hook`` imports this on the
PreModelSwitch path only, and must stay fast.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# The hook fires as soon as Claude Code reads the typed `/model`, well
# under a second after the daemon wrote the marker. 30 s is generous
# slack for a slow PTY and short enough that a marker Claude Code never
# asked about cannot pre-approve some later, unrelated switch.
MARKER_TTL_SECONDS = 30.0
_MAX_MARKER_BYTES = 4096
# dtach session names are already restricted to this (inject._VALID_NAME);
# checked again because the name becomes part of a path.
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Module seams so tests can simulate a foreign owner or a lost claim race
# without patching the global ``os`` module.
_getuid = os.getuid
_rename = os.rename

ALLOW_REASON = "Model switch requested from aipager (Telegram / Mini App)"


def marker_path(base_dir: str, session: str) -> Path:
    return Path(base_dir) / f"aipager-modelswitch-{session}.json"


def _norm(value) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def write_marker(base_dir: str, session: str, model: str,
                 claude_session_id: str = "", *,
                 ttl: float = MARKER_TTL_SECONDS,
                 now: float | None = None) -> bool:
    """Daemon side: record that ``/model <model>`` is about to be typed
    into ``session``. Atomic (temp file + ``os.replace``), mode 0600.
    Returns False on any failure — the switch then simply falls back to
    Claude Code's own confirmation, as without this feature."""
    if not _SAFE_SESSION.match(session or "") or not _norm(model):
        return False
    now = time.time() if now is None else now
    path = marker_path(base_dir, session)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    body = json.dumps({
        "model": model,
        "claude_session_id": claude_session_id or "",
        "expires_at": now + ttl,
    }).encode()
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                     0o600)
        try:
            os.write(fd, body)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def clear_marker(base_dir: str, session: str) -> None:
    """Daemon side: the switch is settled (sent and failed, or confirmed)."""
    if not _SAFE_SESSION.match(session or ""):
        return
    try:
        os.unlink(marker_path(base_dir, session))
    except OSError:
        pass


def _read_own_file(path: Path) -> bytes | None:
    """Up to ``_MAX_MARKER_BYTES`` of ``path`` if it is owned by us and
    not a symlink; ``None`` otherwise. Opened non-blocking, so a FIFO
    planted at the path cannot stall the hook."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        # Owner check only: a directory, FIFO or device here reads as
        # empty or raises, and either way decides nothing.
        if st.st_uid != _getuid():
            return None
        # Bounded read: anything longer arrives truncated, fails to parse,
        # and so decides nothing.
        return os.read(fd, _MAX_MARKER_BYTES)
    finally:
        os.close(fd)


def decide(base_dir: str, session: str, payload, *,
           now: float | None = None) -> dict | None:
    """Hook side: the ``PreModelSwitch`` answer for ``payload``, or
    ``None`` for "no decision" (Claude Code then does what it always does).
    Any malformed input — payload or marker — ends in ``None``.

    Allows only when ALL hold: the payload is a ``PreModelSwitch`` from a
    typed ``/model`` (``source == "command"``); a marker exists for this
    dtach session, owned by this user, unexpired; its Claude session id
    is known and equals the payload's; its model equals the payload's
    ``requested_model`` or ``to_model``; and this process wins the
    atomic claim. Never raises.
    """
    try:
        return _decide(base_dir, session, payload, now)
    except Exception:
        return None


def _decide(base_dir, session, payload, now):
    if not isinstance(payload, dict):
        return None
    if payload.get("hook_event_name") != "PreModelSwitch":
        return None
    if payload.get("source") != "command":
        return None
    if not isinstance(session, str) or not _SAFE_SESSION.match(session):
        return None
    path = marker_path(base_dir, session)
    try:
        raw = _read_own_file(path)
    except OSError:
        return None
    if raw is None:
        return None
    # A marker that is not an object, or whose expires_at is not a number,
    # raises below (AttributeError / TypeError) and decide() answers None.
    marker = json.loads(raw)
    now = time.time() if now is None else now
    expires_at = marker["expires_at"]
    if expires_at <= now:
        try:
            os.unlink(path)          # stale: tidy up, decide nothing
        except OSError:
            pass
        return None
    want = _norm(marker.get("model"))
    if not want:
        return None
    # Both ids required: a nested `claude` started inside this session
    # inherits CLAUDE_DTACH_SESSION but has its own session id.
    marker_sid = marker.get("claude_session_id")
    if not marker_sid or marker_sid != payload.get("session_id"):
        return None
    if want not in (_norm(payload.get("requested_model")),
                    _norm(payload.get("to_model"))):
        return None
    # Claim: exactly one hook process can rename the marker away.
    claimed = path.with_name(f"{path.name}.claimed.{os.getpid()}")
    try:
        _rename(path, claimed)
    except OSError:
        return None
    try:
        # The file claimed must be the one judged above — not a newer
        # marker the daemon wrote in between.
        if _read_own_file(claimed) != raw:
            return None
    finally:
        try:
            os.unlink(claimed)
        except OSError:
            pass
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreModelSwitch",
            "permissionDecision": "allow",
            "permissionDecisionReason": ALLOW_REASON,
        },
    }


__all__ = [
    "ALLOW_REASON",
    "MARKER_TTL_SECONDS",
    "clear_marker",
    "decide",
    "marker_path",
    "write_marker",
]
