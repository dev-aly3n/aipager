"""Write a small file that is owner-only from its first byte.

The problem report files (roadmap 8.112) used to be written at the
umask's mode and narrowed to 0600 only after the content was in
(roadmap 8.115): for that moment another local user could read them.
Here the temporary file is created 0600 before anything is written, then
renamed over the target, so the target is never wider and never half
written.
"""

from __future__ import annotations

import os
from pathlib import Path

from aipager._test_guard import check_write


def write_private(target: Path, text: str, *, dir_mode: int | None = None) -> None:
    """Atomically replace *target* with *text* (UTF-8), mode 0600.

    The temporary file beside it is created with ``O_EXCL``: a leftover
    from a crash (or anything planted there, a symlink included) is
    removed first and never written through. A missing parent folder is
    created, with *dir_mode* when given. Raises ``OSError`` (and, under
    pytest, :class:`aipager._test_guard.RealHomeWriteError`)."""
    target = Path(target)
    check_write(target)
    if dir_mode is None:
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True, mode=dir_mode)
    tmp = target.with_name(target.name + ".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    data = memoryview(text.encode("utf-8"))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            while data:
                data = data[os.write(fd, data):]
        finally:
            os.close(fd)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
