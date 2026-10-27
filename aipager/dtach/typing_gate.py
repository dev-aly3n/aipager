"""No permission dialog opens while aipager types into a session (roadmap 8.143).

Claude Code (2.1.311) draws a tool's permission dialog for the main agent
at once and runs the PermissionRequest hooks beside it, so the dialog takes
keys while aipager's hook still waits for a Telegram answer. Typed into it,
the Enter of a message (or a lone digit) picks a "Yes" row: live, the
command ran. The dialog comes only after the tool's PreToolUse hooks have
all returned, about 0.1 s after they start, and the daemon hears of it about
0.075 s later.

So the two sides meet here, in files beside the daemon's control socket
(``$XDG_RUNTIME_DIR`` normally, per user, mode 0700), one folder per dtach
session:

- the daemon writes a ``typing`` marker (with an expiry) before it types
  into the session and removes it once the Enter is in;
- every PreToolUse hook leaves a ``pass-*`` stamp FIRST and only then reads
  the marker (:func:`pass_gate`). While the marker is there it takes its
  stamp back and waits, so its dialog comes after the typing;
- the daemon, after writing the marker, waits until no stamp is younger
  than a short window (:func:`fresh_passes`): a call that read the marker
  before it was written left its stamp, and its dialog (if any) is known
  by the time that stamp is old.

Each side writes before it reads, so at least one sees the other.

Stdlib only and import-light: ``aipager-hook`` imports this on every
PreToolUse and must stay fast (nothing here may import ``aipager.i18n``).
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path

# How long one PreToolUse hook waits for a typing to end before it goes on
# regardless (with a fresh stamp, so the daemon still sees it). Typing a
# full Telegram message takes about 1.4 s; a send queued behind a long
# queue wipe can hold the marker longer.
HOOK_WAIT_CAP_SECONDS = 5.0
HOOK_POLL_SECONDS = 0.02
# A marker a crashed daemon left behind stops gating after this.
MARKER_TTL_SECONDS = 15.0
# Stamps older than this are tidied away by the daemon's next look.
STALE_PASS_SECONDS = 60.0
# A stamp dated further ahead than this was written before the clock
# stepped back.
_CLOCK_SLACK_SECONDS = 1.0
_MAX_MARKER_BYTES = 256
# dtach session names are already restricted to this (inject._VALID_NAME);
# checked again because the name becomes part of a path.
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_PASS = re.compile(r"^pass-(\d{1,20})-(\d{1,10})$")

# Seam: tests simulate a foreign owner without patching the global ``os``.
_getuid = os.getuid


def gate_dir(base_dir: str, session: str) -> Path:
    return Path(base_dir) / f"aipager-gate-{session}"


def _own_dir(base_dir: str, session: str, *, create: bool) -> Path | None:
    """The session's gate folder when it is a real directory of ours
    (created 0700 when *create*), else None."""
    if not _SAFE_SESSION.match(session or ""):
        return None
    path = gate_dir(base_dir, session)
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError:
            return None
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != _getuid():
        return None
    return path


def _write_stamp(folder: Path, now_ns: int) -> Path:
    path = folder / f"pass-{now_ns}-{os.getpid()}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    return path


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _tidy(folder: Path, now: float) -> None:
    """Remove stamps older than :data:`STALE_PASS_SECONDS`: every tool call
    leaves one, and the daemon looks only when it types."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        m = _PASS.match(name)
        if m and now - int(m.group(1)) / 1e9 > STALE_PASS_SECONDS:
            _unlink(folder / name)


def _marker_live(folder: Path, now: float) -> bool:
    """A marker of ours, readable and not expired. Anything else (none, a
    foreign or broken file) does not gate: the hook never stalls Claude
    Code on something the daemon did not write (it writes atomically)."""
    try:
        fd = os.open(folder / "typing", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        if os.fstat(fd).st_uid != _getuid():
            return False
        raw = os.read(fd, _MAX_MARKER_BYTES)
    except OSError:
        return False
    finally:
        os.close(fd)
    try:
        expires_at = float(json.loads(raw)["expires_at"])
    except (ValueError, TypeError, KeyError):
        return False
    return expires_at > now


def pass_gate(base_dir: str, session: str, *,
              cap: float = HOOK_WAIT_CAP_SECONDS,
              clock=time.time, monotonic=time.monotonic,
              sleep=time.sleep) -> str:
    """Hook side, at the end of a PreToolUse that lets its tool go on:
    leave a stamp, then wait while aipager types into *session*.

    Returns ``"passed"`` (no typing: the stamp stays), ``"waited"`` (it
    waited for a typing to end, then passed with a fresh stamp),
    ``"capped"`` (it waited *cap* seconds and passed anyway, stamped) or
    ``"error"`` (it could not take part: it goes on at once). Never raises.
    """
    try:
        folder = _own_dir(base_dir, session, create=True)
        if folder is None:
            return "error"
        _tidy(folder, clock())
        deadline = None
        while True:
            stamp = _write_stamp(folder, int(clock() * 1_000_000_000))
            if not _marker_live(folder, clock()):
                return "passed" if deadline is None else "waited"
            if deadline is None:
                deadline = monotonic() + cap
            elif monotonic() >= deadline:
                return "capped"
            _unlink(stamp)
            sleep(HOOK_POLL_SECONDS)
    except Exception:  # noqa: BLE001 - never wedge Claude Code
        return "error"


def begin(base_dir: str, session: str, *, ttl: float = MARKER_TTL_SECONDS,
          now: float | None = None) -> bool:
    """Daemon side: aipager is about to type into *session*. Writes (or
    renews) the marker atomically. False when it could not."""
    folder = _own_dir(base_dir, session, create=True)
    if folder is None:
        return False
    now = time.time() if now is None else now
    path = folder / "typing"
    tmp = folder / f"typing.tmp.{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, json.dumps({"expires_at": now + ttl}).encode())
        finally:
            os.close(fd)
        os.replace(tmp, path)
        return True
    except OSError:
        _unlink(tmp)
        return False


def end(base_dir: str, session: str) -> None:
    """Daemon side: the typing is over."""
    folder = _own_dir(base_dir, session, create=False)
    if folder is not None:
        _unlink(folder / "typing")


def fresh_passes(base_dir: str, session: str, window: float, *,
                 now: float | None = None) -> int:
    """Daemon side: how many PreToolUse calls passed the gate less than
    *window* seconds ago (their dialogs may still be on the way). Stamps
    older than :data:`STALE_PASS_SECONDS` are removed."""
    folder = _own_dir(base_dir, session, create=False)
    if folder is None:
        return 0
    now = time.time() if now is None else now
    fresh = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for name in names:
        m = _PASS.match(name)
        if not m:
            continue
        age = now - int(m.group(1)) / 1e9
        if age < -_CLOCK_SLACK_SECONDS:
            # Dated well in the future: the clock stepped back since it was
            # written. Not a pass to wait for (it would look fresh for as
            # long as the step), and nothing to keep.
            _unlink(folder / name)
        elif age < window:
            fresh += 1
        elif age > STALE_PASS_SECONDS:
            _unlink(folder / name)
    return fresh
