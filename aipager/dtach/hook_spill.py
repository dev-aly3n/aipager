"""Hook events too big for one datagram travel through a private file.

``aipager-hook`` sends each event to the daemon as one Unix datagram. A
datagram has a size limit: about 208 KiB on Linux and 2 KiB by default on
macOS, where the daemon's receive buffer (4 KiB by default) can also
refuse one. When the send fails for its size, the hook writes the whole
event into a file in a folder beside the control socket and sends a
small pointer datagram naming the file instead. The daemon reads the file
at once, deletes it, and handles the event as if it had come inline.

The control socket is writable by every local user, so the daemon takes
nothing from a pointer but a name, checks it against its own pattern and
opens it only through the folder's handle, never following a link. The
folder must be a directory of this user with no group or other access;
files are created 0600 with 128 random bits in their name.

Stdlib-only and cheap: the hook imports this only when an event spills,
and nothing here may import ``aipager.i18n`` or ``aipager.config``
(``tests/test_i18n_hook_isolation.py``).
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

#: The ``type`` of a pointer datagram.
POINTER_TYPE = "aipager_spill"
#: The only file names ever read, written or deleted in the folder.
NAME_RE = re.compile(r"^ev-[0-9a-f]{32}\.json$")
#: Bigger events are not written, and bigger files are refused.
SPILL_MAX_BYTES = 4 * 1024 * 1024
#: The daemon's start-up sweep removes files older than this.
SWEEP_AGE_SECONDS = 300.0
#: A pointer datagram is about 70 bytes; anything longer is not one.
_POINTER_MAX_BYTES = 512

# Seams: tests simulate a foreign owner without patching the global ``os``.
_getuid = os.getuid
_fstat_file = os.fstat
# Seam: tests make a write fail without patching the global ``os``.
_write = os.write

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _new_name() -> str:
    """The next spill file name (a seam for tests)."""
    return f"ev-{secrets.token_hex(16)}.json"


def _valid_name(name) -> bool:
    return isinstance(name, str) and NAME_RE.fullmatch(name) is not None


def spill_dir(base_dir: str) -> Path:
    """The spill folder beside the control socket in *base_dir*."""
    return Path(base_dir) / f"aipager-spill-{os.getuid()}"


def _errname(e: OSError) -> str:
    return errno.errorcode.get(e.errno or 0, str(e.errno))


def _open_dir(base_dir: str) -> tuple[int | None, str]:
    """A handle on the spill folder, never through a symlink."""
    try:
        return os.open(spill_dir(base_dir), _DIR_FLAGS), ""
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            return None, "symlink-or-not-dir"
        return None, f"open-failed:{_errname(e)}"


def _open_own_dir(base_dir: str) -> tuple[int | None, str]:
    """A handle on the spill folder when it is a directory of this user
    with no group or other access, else ``(None, reason)``. Reasons are
    short codes for the log, never shown to anyone."""
    fd, why = _open_dir(base_dir)
    if fd is None:
        return None, why
    try:
        st = os.fstat(fd)
    except OSError as e:
        os.close(fd)
        return None, f"stat-failed:{_errname(e)}"
    if not stat.S_ISDIR(st.st_mode):
        why = "not-a-dir"
    elif st.st_uid != _getuid():
        why = "not-ours"
    elif st.st_mode & 0o077:
        why = "open-to-others"
    if why:
        os.close(fd)
        return None, why
    return fd, ""


def _unlink_at(dir_fd: int, name: str) -> bool:
    """Remove *name* from the folder; False when that failed."""
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        return False
    return True


def ensure_dir(base_dir: str) -> str:
    """Daemon side: create the folder 0700, or tighten an existing one of
    ours to 0700. ``""`` when it is usable, else a short reason."""
    try:
        os.mkdir(spill_dir(base_dir), 0o700)
    except FileExistsError:
        pass
    except OSError as e:
        return f"create-failed:{_errname(e)}"
    fd, why = _open_dir(base_dir)
    if fd is None:
        return why
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            return "not-a-dir"
        if st.st_uid != _getuid():
            return "not-ours"
        if st.st_mode & 0o077:
            os.fchmod(fd, 0o700)
        return ""
    except OSError as e:
        return f"check-failed:{_errname(e)}"
    finally:
        os.close(fd)


def write_spill(base_dir: str, data: bytes) -> str | None:
    """Hook side: write *data* to a new 0600 file in the folder and return
    its name; None (and no file left) when it cannot. Never creates the
    folder. Raises only what is not an ``OSError`` (``MemoryError``),
    after removing its own file."""
    if len(data) > SPILL_MAX_BYTES:
        return None
    dfd, _why = _open_own_dir(base_dir)
    if dfd is None:
        return None
    try:
        name = _new_name()
        try:
            # O_CREAT|O_EXCL never follows a symlink at the name (POSIX).
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                         0o600, dir_fd=dfd)
        except OSError:
            # EEXIST included: 128 random bits never collide by chance, so
            # something planted the name; never write through it.
            return None
        done = False
        try:
            view = memoryview(data)
            while view:
                n = _write(fd, view)
                view = view[n:]
            done = True
        except OSError:
            pass
        finally:
            os.close(fd)
            if not done:
                _unlink_at(dfd, name)
        return name if done else None
    finally:
        os.close(dfd)


def remove_spill(base_dir: str, name: str) -> None:
    """Delete the spill file *name*; anything else is left alone."""
    if not _valid_name(name):
        return
    dfd, _why = _open_own_dir(base_dir)
    if dfd is None:
        return
    try:
        _unlink_at(dfd, name)
    finally:
        os.close(dfd)


def pointer(name: str) -> bytes:
    """The datagram that tells the daemon where an event waits."""
    return json.dumps({"type": POINTER_TYPE, "name": name}).encode()


def parse_pointer(data: bytes):
    """The ``name`` a pointer datagram carries (unchecked, any JSON value;
    ``""`` when it has none), or None when *data* is not a pointer."""
    if len(data) > _POINTER_MAX_BYTES or POINTER_TYPE.encode() not in data:
        return None
    try:
        msg = json.loads(data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(msg, dict) or msg.get("type") != POINTER_TYPE:
        return None
    name = msg.get("name")
    return "" if name is None else name


def _read_bounded(fd: int) -> bytes | None:
    """At most ``SPILL_MAX_BYTES`` from *fd*; None when there is more."""
    limit = SPILL_MAX_BYTES + 1
    chunks = []
    total = 0
    while total < limit:
        chunk = os.read(fd, min(1024 * 1024, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > SPILL_MAX_BYTES:
        return None
    return b"".join(chunks)


def read_spill(base_dir: str, name) -> tuple[bytes | None, str]:
    """Daemon side: the contents of the spill file *name*, which is then
    deleted, as ``(contents, "")``; else ``(None, reason)``. A name that
    is not one of ours, or a folder that is not ours, is never touched;
    any other refused file in the folder is deleted. Never follows a
    symlink, never reads outside the folder, never raises an OSError."""
    if not _valid_name(name):
        return None, "bad-name"
    dfd, why = _open_own_dir(base_dir)
    if dfd is None:
        return None, f"folder-{why}"
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=dfd)
        except FileNotFoundError:
            return None, "missing"
        except OSError as e:
            # Unlinking a symlink removes the link, never its target.
            _unlink_at(dfd, name)
            if e.errno == errno.ELOOP:
                return None, "symlink"
            return None, f"open-failed:{_errname(e)}"
        try:
            # Gone from the folder before anything else: a crash mid-read
            # leaves nothing behind.
            _unlink_at(dfd, name)
            st = _fstat_file(fd)
            if not stat.S_ISREG(st.st_mode):
                return None, "not-regular"
            if st.st_uid != _getuid():
                return None, "not-ours"
            body = _read_bounded(fd)
            if body is None:
                return None, "too-big"
            return body, ""
        except OSError as e:
            return None, f"read-failed:{_errname(e)}"
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def sweep(base_dir: str, now: float | None = None) -> int:
    """Daemon side, at start: delete spill files older than
    ``SWEEP_AGE_SECONDS`` and spill names that are not regular files.
    Fresh files and other names stay. Returns how many went."""
    dfd, _why = _open_own_dir(base_dir)
    if dfd is None:
        return 0
    now = time.time() if now is None else now
    removed = 0
    try:
        try:
            names = os.listdir(dfd)
        except OSError:
            return 0
        for name in names:
            if not _valid_name(name):
                continue
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or now - st.st_mtime > SWEEP_AGE_SECONDS:
                if _unlink_at(dfd, name):
                    removed += 1
    finally:
        os.close(dfd)
    return removed
