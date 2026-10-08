"""The marker files of problem reports (roadmap 8.112, design section 4).

- ``install.json`` ``{"first_start": <unix seconds>}``: the first daemon
  start on this machine. Written once, by the first start that finds no
  valid file, and never rewritten: an update or a reinstall keeps it, so
  the 48 hours without automatic offers run from the FIRST install only.
- ``running.json`` ``{"pid", "started", "boot", "service"}``: written when
  the daemon starts, removed when it stops (cleanly, or on an error it
  saw and recorded). One left behind means the last daemon was killed
  (SIGKILL, out of memory, a hang past the stop timeout) or went down with
  the machine; the boot id tells which.

Both live under ``~/.local/share/aipager`` (paths read LATE from config,
so the test redirect is one entry each) and both are written atomically
behind ``_test_guard.check_write``. Nothing here raises into a caller.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from aipager._test_guard import check_write
from aipager.private_file import write_private

log = logging.getLogger(__name__)

#: Linux's id of this boot: a leftover marker from another boot went down
#: with the machine, not on its own. Compared, never reported.
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
BOOT_ID_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
#: Unix seconds a marker may hold (2001 to 2096).
TS_MIN, TS_MAX = 1_000_000_000, 4_000_000_000
#: How far ahead of the clock a first-install time may be before it is
#: taken as a broken clock and started again.
FUTURE_SLACK = 86400

_started_at: float | None = None


def _install_path() -> Path:
    from aipager import config

    return Path(config.REPORT_INSTALL_FILE)


def _running_path() -> Path:
    from aipager import config

    return Path(config.REPORT_RUNNING_FILE)


def _is_ts(value) -> bool:
    return type(value) is int and TS_MIN <= value <= TS_MAX


def _write(target: Path, document: dict) -> bool:
    try:
        check_write(target)
        write_private(target, json.dumps(document))  # owner-only from the first byte
        return True
    except Exception:  # noqa: BLE001 - a marker must not stop the daemon
        log.debug("could not write the marker %s", target.name, exc_info=True)
        return False


def _read(marker_path: Path):
    """The marker's JSON object, or None when it is missing or not ours."""
    try:
        if not marker_path.is_file() or marker_path.stat().st_size > 4096:
            return None
        document = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return document if isinstance(document, dict) else None


def _uptime() -> float:
    """Seconds since boot, counting time asleep: ``CLOCK_BOOTTIME`` on
    Linux, ``CLOCK_MONOTONIC`` on macOS (which counts sleep there), the
    monotonic clock elsewhere (which may not, so it errs towards
    "reboot")."""
    try:
        return time.clock_gettime(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))
    except (AttributeError, OSError):
        return time.monotonic()


def _boot_id() -> str | None:
    try:
        text = BOOT_ID.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return None
    return text if BOOT_ID_RE.fullmatch(text) else None


# ---- the first install -------------------------------------------------------

def first_start(now=None) -> int:
    """When the daemon first started on this machine (unix seconds). The
    first call that finds no valid marker writes *now*; a valid marker is
    never rewritten. Never raises."""
    ts = int(time.time() if now is None else now)
    try:
        marker_path = _install_path()
        document = _read(marker_path)
        value = document.get("first_start") if document is not None else None
        if _is_ts(value) and value <= ts + FUTURE_SLACK:
            return value
        _write(marker_path, {"first_start": ts})
    except Exception:  # noqa: BLE001 - errs quiet: the clock starts now
        pass
    return ts


def read_first_start(now=None) -> int | None:
    """The install marker's first start, or None when there is no valid
    one. Read only: unlike :func:`first_start` it never writes, so a
    periodic check (the automatic offer) cannot start the clock itself.
    Never raises."""
    ts = int(time.time() if now is None else now)
    try:
        document = _read(_install_path())
        value = document.get("first_start") if document is not None else None
        if _is_ts(value) and value <= ts + FUTURE_SLACK:
            return value
    except Exception:  # noqa: BLE001 - unknown: no offer
        pass
    return None


# ---- the running marker ------------------------------------------------------

def previous_exit(now=None) -> tuple[str, bool] | None:
    """How the last daemon ended, from the running marker it left:
    ``("crash", ran_as_service)`` when it died on this boot,
    ``("reboot", ...)`` when the machine went down under it,
    ``("unknown", False)`` for a marker that is not ours; None when there is
    no marker (a clean stop, or a first start). Never raises."""
    try:
        marker_path = _running_path()
        if not marker_path.exists():
            return None
        document = _read(marker_path)
        if document is None or not _is_ts(document.get("started")):
            return "unknown", False
        service = document.get("service") is True
        boot_then, boot_now = document.get("boot"), _boot_id()
        if isinstance(boot_then, str) and BOOT_ID_RE.fullmatch(boot_then) and boot_now is not None:
            same_boot = boot_then == boot_now
        else:
            # No boot id (macOS): the boot time from the uptime. A clock
            # that missed some sleep makes the boot look later, so a
            # doubtful marker reads as a reboot: quiet.
            ts = time.time() if now is None else now
            same_boot = document["started"] >= ts - _uptime()
        return ("crash" if same_boot else "reboot"), service
    except Exception:  # noqa: BLE001
        return "unknown", False


def write_running(*, service: bool, now=None) -> bool:
    """Mark this daemon as running (and note when it started)."""
    ts = time.time() if now is None else now
    set_started_at(ts)
    return _write(_running_path(), {"pid": os.getpid(), "started": int(ts),
                                    "boot": _boot_id() or "", "service": service is True})


def clear_running() -> None:
    """The daemon stopped and said so: no marker to find next time."""
    try:
        target = _running_path()
        check_write(target)
        target.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        log.debug("could not remove the running marker", exc_info=True)


def started_at() -> float | None:
    """When this daemon started (unix seconds), or None outside one."""
    return _started_at


def set_started_at(ts: float | None) -> None:
    """Set when this daemon started (unix seconds), in memory only;
    None forgets it (the test suite resets it around every test)."""
    global _started_at
    _started_at = ts
