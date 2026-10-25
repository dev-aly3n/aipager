"""The managed Mini App tunnel's state, and why it is not working.

:class:`~aipager.miniapp.tunnel_manager.TunnelManager` is the only
writer. It keeps one :class:`TunnelStatus` here (``starting``,
``working``, ``retrying`` or ``slow``, with a reason id), mirrors it to
``config.TUNNEL_STATUS_FILE`` for ``aipager doctor`` (a separate
process), and attaches the hook ``/app`` uses to ask for an attempt
during the slow phase.

Reasons are ids, compared as ids: the text a person reads is chosen from
the id (``/app`` in :mod:`aipager.bot.handlers`, doctor's lines below).
cloudflared's own error text is read from its ``--logfile`` and from its
stderr file, and goes to the daemon's log only (:func:`raw_cause`): never
into chat, never into the status file.

This module imports nothing from the bot and never imports
``aipager.i18n``: doctor's text is English, like the rest of the CLI.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# ---- ids -------------------------------------------------------------------

STARTING = "starting"
WORKING = "working"
RETRYING = "retrying"
SLOW = "slow"
PHASES = (STARTING, WORKING, RETRYING, SLOW)

DNS_INCOMPLETE = "dns_incomplete"
DNS_LOOKUP_FAILED = "dns_lookup_failed"
QUICK_TUNNEL = "quick_tunnel"
EDGE_UNREACHABLE = "edge_unreachable"
BINARY_MISSING = "binary_missing"
UNREACHABLE = "unreachable"
OTHER = "other"
REASONS = (DNS_INCOMPLETE, DNS_LOOKUP_FAILED, QUICK_TUNNEL, EDGE_UNREACHABLE,
           BINARY_MISSING, UNREACHABLE, OTHER)

# cloudflared's own error strings (format strings in its source at the
# pinned tag 2026.8.2), matched as substrings in this order; the first
# match wins.
#   edgediscovery/allregions/regions.go: ResolveEdge, a DNS server that
#     returns one SRV record (the doubled "Regions regions" is cloudflared's)
#   edgediscovery/allregions/discovery.go: the SRV lookup failing outright
#   cmd/cloudflared/tunnel/quick_tunnel.go: the quick-tunnel request
#     failing (printed on stderr, never in the --logfile)
_PATTERNS = (
    ("expected at least 2 Cloudflare Regions regions", DNS_INCOMPLETE),
    ("Could not lookup srv records", DNS_LOOKUP_FAILED),
    ("failed to request quick Tunnel", QUICK_TUNNEL),
)
# connection/errors.go: a quick tunnel forces QUIC, and a blocked UDP 7844
# repeats this while cloudflared keeps running.
_EDGE_DIAL_PREFIX = "failed to dial to edge"

# Never read more of either file than this (the tail holds the answer).
_TAIL_BYTES = 64 * 1024
# What of cloudflared's text reaches the daemon's log.
_RAW_CAP = 300

_STATUS_VERSION = 1


# ---- reading cloudflared's files -------------------------------------------

def _tail_lines(path: Path) -> list[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            data = fh.read(_TAIL_BYTES)
    except (OSError, ValueError):
        return []
    return data.decode("utf-8", "replace").splitlines()


def _log_candidates(log_path: Path) -> tuple[str, str]:
    """``(shutdown_error, last_error)`` from cloudflared's JSON log: the
    ``"error"`` of the last ``Initiating shutdown`` line, and of the last
    line carrying a string ``"error"`` (either may be "")."""
    shutdown = last = ""
    for line in _tail_lines(log_path):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        err = entry.get("error")
        if not isinstance(err, str) or not err:
            continue
        last = err
        if entry.get("message") == "Initiating shutdown":
            shutdown = err
    return shutdown, last


def _last_stderr_line(err_path: Path) -> str:
    for line in reversed(_tail_lines(err_path)):
        if line.strip():
            return line.strip()
    return ""


def _match(text: str) -> str | None:
    for needle, reason in _PATTERNS:
        if needle in text:
            return reason
    return None


def _pick(log_path: Path, err_path: Path) -> tuple[str, str]:
    """``(reason, raw)``: the first recognised candidate, else ``other``
    with the first non-empty candidate as its raw text."""
    try:
        shutdown, last = _log_candidates(Path(log_path))
        stderr = _last_stderr_line(Path(err_path))
    except Exception:           # never raises: a garbage file is "other"
        log.debug("cloudflared: could not read its log", exc_info=True)
        return OTHER, ""
    candidates = [c for c in (shutdown, last, stderr) if c]
    for text in candidates:
        reason = _match(text)
        if reason is not None:
            return reason, text
    return OTHER, (candidates[0] if candidates else "")


def classify(log_path: Path, err_path: Path) -> str:
    """The reason id for a cloudflared that exited or failed to launch,
    from its ``--logfile`` and its stderr file. ``other`` when nothing
    is recognised, a file is missing or holds garbage. Never raises."""
    return _pick(log_path, err_path)[0]


def classify_unreachable(log_path: Path) -> str:
    """The reason id for a cloudflared still running whose link never
    answered: ``edge_unreachable`` when its log's last error is the edge
    dial failing, else ``unreachable``. Never raises."""
    try:
        _shutdown, last = _log_candidates(Path(log_path))
    except Exception:
        return UNREACHABLE
    return EDGE_UNREACHABLE if last.startswith(_EDGE_DIAL_PREFIX) else UNREACHABLE


def raw_cause(log_path: Path, err_path: Path, *, unreachable: bool = False) -> str:
    """cloudflared's own text behind the reason, capped, for the daemon's
    log only (never chat, never the status file). Never raises."""
    try:
        if unreachable:
            text = _log_candidates(Path(log_path))[1]
        else:
            text = _pick(log_path, err_path)[1]
    except Exception:
        return ""
    text = " ".join(text.split())
    return text[:_RAW_CAP]


# ---- the state ---------------------------------------------------------------

@dataclass(frozen=True)
class TunnelStatus:
    """The managed tunnel's state. ``reason`` is "" while starting or
    working; ``attempt`` counts the failed attempts of the current fast
    budget; ``next_try_at`` is the wall clock of the next try (0 while
    an attempt runs); ``since`` is when this phase began."""

    phase: str
    reason: str = ""
    attempt: int = 0
    next_try_at: float = 0.0
    since: float = 0.0


_status: TunnelStatus | None = None
_attempt_hook: Callable[[], bool] | None = None


def publish_status(status: TunnelStatus | None) -> None:
    """Set (or clear, with None) the running daemon's tunnel state.
    The tunnel manager is the only caller."""
    global _status
    _status = status


def current_status() -> TunnelStatus | None:
    """The running daemon's tunnel state; None when no managed tunnel
    exists (disabled, an override, no server)."""
    return _status


def set_attempt_hook(fn: Callable[[], bool] | None) -> None:
    """Attach (or detach) the manager's "try once now" hook."""
    global _attempt_hook
    _attempt_hook = fn


def request_attempt() -> bool:
    """Ask the tunnel manager for one attempt right away: True when one
    was started (only in the slow phase, at most once a minute). False
    otherwise, and when there is no manager. Never raises."""
    hook = _attempt_hook
    if hook is None:
        return False
    try:
        return bool(hook())
    except Exception:
        log.warning("cloudflared: could not start a tunnel attempt", exc_info=True)
        return False


# ---- the state file, for aipager doctor -------------------------------------

def _status_path() -> Path:
    from aipager import config
    return Path(config.TUNNEL_STATUS_FILE)


def write_status_file(status: TunnelStatus) -> bool:
    """Write *status* for ``aipager doctor``, atomically, readable by this
    user only. Never the URL or the host. True when it is on disk. Never
    raises (except the test guard's refusal of the operator's real home)."""
    from aipager._test_guard import check_write
    from aipager.miniapp import tunnel_manager

    path = _status_path()
    check_write(path)
    data = {"v": _STATUS_VERSION, "pid": os.getpid(), "boot_id": tunnel_manager._boot_id(),
            "phase": status.phase, "reason": status.reason, "attempt": status.attempt,
            "next_try_at": status.next_try_at, "since": status.since}
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)                # also a stale .tmp a crash left
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        log.warning("cloudflared: could not save the tunnel state", exc_info=True)
        return False
    return True


def delete_status_file() -> None:
    """Remove the state file. Never raises."""
    try:
        _status_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.warning("cloudflared: could not remove the tunnel state", exc_info=True)


def _daemon_alive(pid: int) -> bool:
    """Whether *pid* is a running process (a seam tests replace)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_status_file() -> TunnelStatus | None:
    """The running daemon's tunnel state, from ``config.TUNNEL_STATUS_FILE``.
    None when the file is missing, unreadable or malformed, from another
    boot, or its pid is not a running aipager (a crash left it): a state
    is never inferred. Never raises."""
    from aipager.miniapp import tunnel_manager

    try:
        raw = json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("v") != _STATUS_VERSION:
        return None
    try:
        pid = int(raw["pid"])
        boot_id = str(raw["boot_id"])
        phase = str(raw["phase"])
        reason = str(raw.get("reason") or "")
        attempt = int(raw.get("attempt") or 0)
        next_try_at = float(raw.get("next_try_at") or 0.0)
        since = float(raw.get("since") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    if pid <= 1 or phase not in PHASES or (reason and reason not in REASONS):
        return None
    try:
        if boot_id != tunnel_manager._boot_id():
            return None
        if not _daemon_alive(pid) or not tunnel_manager._is_aipager(pid):
            return None
    except Exception:
        return None
    return TunnelStatus(phase=phase, reason=reason, attempt=attempt,
                        next_try_at=next_try_at, since=since)


# ---- aipager doctor (command line: English) -----------------------------------

_DOCTOR_REASON = {
    DNS_INCOMPLETE: "this machine's DNS server returns only one Cloudflare region record",
    DNS_LOOKUP_FAILED: "this machine cannot look up Cloudflare's servers in DNS",
    QUICK_TUNNEL: "cloudflared could not get a link from api.trycloudflare.com",
    EDGE_UNREACHABLE: "cloudflared cannot reach Cloudflare's servers on port 7844 (UDP)",
    BINARY_MISSING: "cloudflared could not be downloaded or verified",
    UNREACHABLE: "cloudflared runs but its link never answered",
    OTHER: "cloudflared stopped for a reason aipager does not recognise",
}

_DOCTOR_FIX = {
    DNS_INCOMPLETE: ("point this machine at another DNS server (for example 1.1.1.1); "
                     "`resolvectl query -t SRV _v2-origintunneld._tcp.argotunnel.com` "
                     "should list two records - see docs/troubleshooting.md"),
    DNS_LOOKUP_FAILED: ("check this machine's DNS server and internet connection - "
                        "see docs/troubleshooting.md"),
    QUICK_TUNNEL: ("check that this machine can open https://api.trycloudflare.com - "
                   "see docs/troubleshooting.md"),
    EDGE_UNREACHABLE: ("allow outgoing UDP port 7844 in the firewall or network - "
                       "see docs/troubleshooting.md"),
    BINARY_MISSING: "check the internet connection; `aipager logs` names the failed step",
    UNREACHABLE: "check this machine's network; aipager restarts the tunnel by itself",
    OTHER: "`aipager logs` shows cloudflared's own message",
}


def doctor_lines(status: TunnelStatus, *, now: float | None = None,
                 ) -> tuple[list[str], list[str], str | None]:
    """``(detail, warnings, fix)`` for doctor's Mini App row. Warnings
    (and a fix) only while the tunnel is not working."""
    if status.phase == WORKING:
        return ["tunnel: working"], [], None
    if status.phase == STARTING:
        return ["tunnel: starting"], [], None
    reason = status.reason if status.reason in _DOCTOR_REASON else OTHER
    now = time.time() if now is None else now
    word = "attempt" if status.attempt == 1 else "attempts"
    if status.next_try_at <= 0 or status.next_try_at <= now:
        when = "trying now"
    else:
        minutes = max(1, round((status.next_try_at - now) / 60))
        when = f"next try in about {minutes} min"
    warn = [f"{status.attempt} failed {word}; {when}"]
    if status.phase == SLOW:
        warn.append("retrying slowly")
    return ([f"tunnel: not working - {_DOCTOR_REASON[reason]}"], warn, _DOCTOR_FIX[reason])


__all__ = [
    "STARTING", "WORKING", "RETRYING", "SLOW",
    "DNS_INCOMPLETE", "DNS_LOOKUP_FAILED", "QUICK_TUNNEL", "EDGE_UNREACHABLE",
    "BINARY_MISSING", "UNREACHABLE", "OTHER",
    "TunnelStatus", "classify", "classify_unreachable", "current_status",
    "request_attempt", "read_status_file", "doctor_lines",
]
