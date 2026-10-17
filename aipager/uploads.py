"""Where files sent from Telegram are kept: one private folder per chat.

``<download dir>/<chat key>/``, where the download dir is
``config.FILE_DOWNLOAD_DIR`` (``/tmp/aipager-files``, or
``<instance>/aipager-files``) and the key is an HMAC of the chat id under
the bot token: the same chat always gets the same folder, and the name
does not give the chat id away. Both folders are ``0700`` and every file
``0600``, so no other local user can read an upload, and a restricted
turn is kept off every other chat's folder by its policy snapshot
(``member_root.snapshot_bound``).

A key always starts with a letter, and files from before per-chat
folders (``<unix time>_<name>``, left where they were) always start with
a digit, so the glob :func:`legacy_glob` covers the old files and never
a chat's folder.

The folders are made without trusting what is already there: a symlink
in the place of either folder, something that is not a folder, or a
folder another user owns is refused (:class:`UnsafeUploadFolder`), and a
folder of ours with wider modes (``0775`` from an older aipager) is
narrowed to ``0700``. Nothing here deletes or moves a file.

Daemon only (the bot's downloads and ``member_root.snapshot_bound``),
and free of ``aipager.i18n`` and of the bot.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import os
import stat
from pathlib import Path

#: A chat key: ``c`` and 24 hex digits (96 bits of the HMAC).
KEY_PREFIX = "c"
_KEY_HEX = 24


#: Who must own an upload folder: this process's user. A seam, so a test
#: can play a folder owned by someone else (never patch ``os`` itself).
_owner_uid = os.geteuid


class UnsafeUploadFolder(Exception):
    """An upload folder could not be made safely. ``args``: a code
    (``symlink``, ``not-a-folder``, ``foreign-owner``, ``no-chat``) and
    the path. Nothing is written there."""


def chat_key(chat_id: int | str, secret: str | None = None) -> str:
    """The folder name of *chat_id*'s uploads. *secret* defaults to the
    bot token, read at call time."""
    if secret is None:
        from aipager import config
        secret = config.BOT_TOKEN or ""
    mac = hmac.new(secret.encode("utf-8"),
                   b"aipager-uploads\x00" + str(int(chat_id)).encode("ascii"),
                   hashlib.sha256)
    return KEY_PREFIX + mac.hexdigest()[:_KEY_HEX]


def chat_folder(base: Path | str, chat_id: int | str,
                secret: str | None = None) -> Path:
    """``<base>/<chat key>``, as a path only (nothing is made)."""
    return Path(base) / chat_key(chat_id, secret)


def legacy_glob(base: Path | str) -> str:
    """The deny glob for the files every chat shared before per-chat
    folders: ``<base>/[0-9]*``. A chat key starts with a letter, so no
    chat folder matches."""
    return os.path.join(os.fspath(base), "[0-9]*")


def _private_dir(path: str) -> None:
    """Make *path* a ``0700`` folder of ours, or raise
    :class:`UnsafeUploadFolder`. The parent must exist."""
    try:
        # 0700 from the start (the fchmod below only narrows a folder that
        # was already there), so a new folder is never briefly open.
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise UnsafeUploadFolder("symlink", path) from exc
        if exc.errno == errno.ENOTDIR:
            raise UnsafeUploadFolder("not-a-folder", path) from exc
        raise
    try:
        st = os.fstat(fd)   # a folder: O_DIRECTORY refused anything else
        if st.st_uid != _owner_uid():
            raise UnsafeUploadFolder("foreign-owner", path)
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def secure_base(base: Path | str) -> str:
    """Make the download folder a ``0700`` folder of ours (narrowing one
    an older aipager left open, its files untouched) and return it as an
    absolute path. The daemon calls it at start, so the files every chat
    shared before are closed to other users without waiting for an
    upload. A missing parent is made as well (``mkdir -p``)."""
    base = os.path.abspath(os.fspath(base))
    parent = os.path.dirname(base)
    if not os.path.isdir(parent):
        os.makedirs(parent, 0o700, exist_ok=True)
    _private_dir(base)
    return base


def ensure_chat_folder(base: Path | str, chat_id: int | str,
                       secret: str | None = None) -> Path:
    """Make ``<base>`` and ``<base>/<chat key>`` safely (both ``0700``)
    and return the chat's folder. Folders above *base* are made as
    needed, ``0700``, as before (``mkdir -p``)."""
    base = secure_base(base)
    folder = os.path.join(base, chat_key(chat_id, secret))
    _private_dir(folder)
    return Path(folder)


def reserve_file(folder: Path, filename: str) -> Path:
    """Create an empty ``0600`` file for an upload in *folder* and return
    its path: ``filename``, else ``stem-2.ext``, ``stem-3.ext`` and so on.
    Created exclusively and without following a symlink, so two uploads
    never share a file and nothing already there is reused."""
    stem, suffix = Path(filename).stem, Path(filename).suffix
    path = folder / filename
    n = 2
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600)
        except FileExistsError:
            path = folder / f"{stem}-{n}{suffix}"
            n += 1
            continue
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        return path


def seal_file(path: Path) -> None:
    """Put a finished download back to ``0600`` (a download writes into
    the reserved file and keeps its mode; this is the check)."""
    os.chmod(path, 0o600)
