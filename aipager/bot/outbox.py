"""Files Claude sends to the session's chat (SendUserFile): copied, sorted
and captioned here, then delivered after the turn's answer.

The PreToolUse hook already checked every file (``aipager.send_check``,
``enforce``). The daemon trusts nothing it is told over the control
socket, which any local process can write, so here each file is:

1. **taken from the PreToolUse request** for that ``tool_use_id`` (the
   input that was checked), or from the PostToolUse ``tool_input`` when
   that request was missed; never from Claude Code's ``tool_response``;
2. **checked again**: the send floor and file checks, and the root of the
   session's current snapshot for a member, all on one resolution of the
   path, which is the one opened;
3. **copied** into ``<outbox>/<chat key>/`` (``config.OUTBOX_DIR``, folder
   ``0700``, file ``0600``, the per-chat helpers of ``aipager.uploads``),
   from a file opened without following a link and found to be the very
   file that was checked, so a later change to the original changes
   nothing that is sent;
4. **sorted** into Telegram sends (``plan_sends``): photos and videos in
   albums of 2-10, documents with documents, audio with audio, an
   animation on its own, each album at most 50 MiB;
5. **delivered** after the answer (``notify``), and its copy deleted.

Copies and waiting deliveries live in memory, like held answers: a
restart loses them, and the day sweep deletes what is left on disk.

Daemon only.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from telegram import (
    InputFile, InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo,
)
from telegram.error import BadRequest, Forbidden

from aipager import audit as audit_mod
from aipager import bidi_guard, media_kind, safety, send_check, uploads
from aipager.bot.flood import FloodMuted
from aipager.bot.transport import utf16_len
from aipager.i18n import Msg

log = logging.getLogger(__name__)

#: How long a PreToolUse request waits for its PostToolUse.
REQUEST_TTL_SECONDS = 30 * 60
#: How long a copy or a waiting delivery is kept before it is given up.
KEEP_SECONDS = 24 * 3600
#: Telegram's album and caption limits.
ALBUM_MAX_ITEMS = 10
ALBUM_MAX_BYTES = send_check.FILE_MAX_BYTES
CAPTION_MAX = 1024
CAPTION_MARK = "💬"
ELLIPSIS = "…"

#: Album classes: photos and videos share an album; documents and audio
#: each only go with their own kind; an animation always goes alone.
_ALBUM_CLASS = {
    media_kind.METHOD_PHOTO: "visual",
    media_kind.METHOD_VIDEO: "visual",
    media_kind.METHOD_AUDIO: "audio",
    media_kind.METHOD_DOCUMENT: "document",
}
METHOD_MEDIA_GROUP = "media_group"

#: Why a file could not be sent, after the check (each has a line in the
#: chat, ``notify``).
CAUSE_TOO_BIG = "too_big"
CAUSE_GONE = "gone"
CAUSE_REFUSED = "refused"
CAUSE_NOT_ALLOWED = "not_allowed"
CAUSE_OTHER = "other"
CAUSE_EXPIRED = "expired"

#: The upload's own timeouts: PTB's default write timeout (about 20 s) is
#: too short for a 50 MB file on a slow line.
FILE_SEND_WRITE_TIMEOUT = 300.0
FILE_SEND_READ_TIMEOUT = 60.0

#: The one line a file that could not be sent gets in the chat, by cause.
FAILED_LINES: dict[str, Msg] = {
    CAUSE_TOO_BIG: Msg(
        "outbox.failed.too_big", "⚠️ Couldn't send {name}: it is too big for Telegram.",
        note="A file Claude sent to this chat could not be delivered: Telegram's size "
             "limit. {name} is the file's name",
        eg={"name": "report.pdf"}),
    CAUSE_GONE: Msg(
        "outbox.failed.gone",
        "⚠️ Couldn't send {name}: the file was gone or changed before aipager could "
        "copy it.",
        note="A file Claude sent: it was deleted, moved or changed between Claude's "
             "request and aipager's copy of it",
        eg={"name": "report.pdf"}),
    CAUSE_REFUSED: Msg(
        "outbox.failed.refused", "⚠️ Couldn't send {name}: Telegram refused it.",
        note="A file Claude sent: Telegram answered with an error",
        eg={"name": "report.pdf"}),
    CAUSE_NOT_ALLOWED: Msg(
        "outbox.failed.not_allowed",
        "⚠️ Couldn't send {name}: it is no longer allowed to be sent.",
        note="A file Claude sent: aipager's second check, at the copy, refused it "
             "(it became a protected file, or is outside the person's folder)",
        eg={"name": "report.pdf"}),
    CAUSE_OTHER: Msg(
        "outbox.failed.other", "⚠️ Couldn't send {name}.",
        note="A file Claude sent could not be delivered, for another reason",
        eg={"name": "report.pdf"}),
}


def outbox_dir() -> Path:
    """Where the copies live (read late, so a test's redirect applies)."""
    from aipager import config
    return Path(config.OUTBOX_DIR)


class CopyFailed(Exception):
    """A file could not be copied: ``cause`` is one of the ``CAUSE_*``
    values, the message a short code for the log."""

    def __init__(self, cause: str, detail: str = "") -> None:
        super().__init__(f"{cause}: {detail}" if detail else cause)
        self.cause = cause


@dataclass
class Request:
    """A SendUserFile call as its PreToolUse showed it (what was checked)."""

    session: str
    tool_use_id: str
    files: list[str]
    caption: str | None
    display: str | None
    cwd: str | None
    agent_id: str
    created: float


@dataclass
class Item:
    """One copied file, ready to send."""

    name: str
    path: Path
    size: int
    kind: str
    width: int | None
    height: int | None
    method: str


@dataclass
class Failure:
    """A file that could not be copied or sent."""

    name: str
    size: int | None
    cause: str


@dataclass
class Batch:
    """One SendUserFile call's copied files."""

    session: str
    caption: str | None
    display: str | None
    items: list[Item]
    agent_id: str
    created: float
    failures: list[Failure] = field(default_factory=list)


@dataclass
class Planned:
    """One Telegram send: a single method, or an album."""

    method: str
    items: list[Item]
    caption: str


@dataclass
class Pending:
    """A turn's files waiting to be delivered: for a mute to lift, or for
    the held answer they follow (``held_key``) to land."""

    session: str
    chat_id: int
    anchor: int | None
    held_key: str | None
    prompt_id: int | None
    batches: list[Batch]
    created: float
    audit: dict = field(default_factory=dict)


# ---- the store -------------------------------------------------------------

class Outbox:
    """In-memory, like ``bot.held.HELD``."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self.requests: dict[tuple[str, str], Request] = {}
        self.in_flight: dict[str, set[asyncio.Task]] = {}
        self.batches: dict[str, list[Batch]] = {}
        self.pending: dict[int, list[Pending]] = {}

    def clear(self) -> None:
        self.requests.clear()
        self.in_flight.clear()
        self.batches.clear()
        self.pending.clear()

    # requests -------------------------------------------------------------

    def record_request(self, session: str, tool_use_id: str, tool_input,
                       cwd: str | None, agent_id: str = "") -> Request | None:
        """Keep a PreToolUse request until its PostToolUse comes."""
        if not isinstance(tool_use_id, str) or not tool_use_id:
            return None
        files = safety.path_targets(send_check.SEND_TOOL, tool_input or {})
        if files is None:
            return None
        inp = tool_input if isinstance(tool_input, dict) else {}
        caption = inp.get("caption")
        display = inp.get("display")
        req = Request(session=session, tool_use_id=tool_use_id, files=list(files),
                      caption=caption if isinstance(caption, str) and caption else None,
                      display=display if isinstance(display, str) else None,
                      cwd=cwd if isinstance(cwd, str) and cwd else None,
                      agent_id=agent_id or "", created=self._clock())
        self.requests[(session, tool_use_id)] = req
        self._drop_old_requests()
        return req

    def take_request(self, session: str, tool_use_id) -> Request | None:
        if not isinstance(tool_use_id, str):
            return None
        return self.requests.pop((session, tool_use_id), None)

    def files_for_ran(self, session: str, tool_use_id, tool_input,
                      tool_response, cwd: str | None) -> Request | None:
        """What a PostToolUse for SendUserFile is to copy: its PreToolUse
        request (the input that was checked), else its own ``tool_input``
        (a restart or a lost datagram in between). ``tool_response`` is
        Claude Code's output, never the authority: its paths are not
        used. None when there is no readable file list."""
        del tool_response  # never trusted (see the docstring)
        req = self.take_request(session, tool_use_id)
        if req is not None:
            return req
        files = safety.path_targets(send_check.SEND_TOOL, tool_input or {})
        if files is None:
            return None
        inp = tool_input if isinstance(tool_input, dict) else {}
        caption = inp.get("caption")
        display = inp.get("display")
        return Request(session=session, tool_use_id=str(tool_use_id or ""),
                       files=list(files),
                       caption=caption if isinstance(caption, str) and caption else None,
                       display=display if isinstance(display, str) else None,
                       cwd=cwd if isinstance(cwd, str) and cwd else None,
                       agent_id="", created=self._clock())

    def _drop_old_requests(self) -> None:
        now = self._clock()
        for key in [k for k, r in self.requests.items()
                    if now - r.created > REQUEST_TTL_SECONDS]:
            del self.requests[key]

    # copies in flight ------------------------------------------------------

    def track(self, session: str, task: asyncio.Task) -> None:
        tasks = self.in_flight.setdefault(session, set())
        tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            tasks.discard(t)
            if not tasks and self.in_flight.get(session) is tasks:
                del self.in_flight[session]
        task.add_done_callback(_done)

    async def wait_in_flight(self, session: str, timeout: float = 30.0) -> None:
        """Wait (at most *timeout* seconds) for this session's copies."""
        tasks = list(self.in_flight.get(session, ()))
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            log.warning("turn files: %d copies still running after %.0fs",
                        len(pending), timeout)

    # batches ---------------------------------------------------------------

    def add_batch(self, batch: Batch) -> None:
        if batch.items or batch.failures:
            self.batches.setdefault(batch.session, []).append(batch)

    def take_batches(self, session: str) -> list[Batch]:
        return self.batches.pop(session, [])

    def has_batches(self, session: str) -> bool:
        return bool(self.batches.get(session)) or bool(self.in_flight.get(session))

    # waiting deliveries ----------------------------------------------------

    def queue(self, pending: Pending) -> None:
        self.pending.setdefault(pending.chat_id, []).append(pending)

    def answer_landed(self, held_key: str, message_id: int) -> None:
        """The held answer *held_key* was sent as *message_id*: the files
        that wait for it reply to it."""
        for items in self.pending.values():
            for p in items:
                if p.held_key == held_key:
                    p.held_key = None
                    p.anchor = message_id

    def answer_gone(self, held_key: str) -> None:
        """The held answer *held_key* will never be sent: its files reply
        to the prompt."""
        for items in self.pending.values():
            for p in items:
                if p.held_key == held_key:
                    p.held_key = None
                    p.anchor = p.prompt_id

    def due(self, chat_id: int, session: str | None = None) -> list[Pending]:
        """The deliveries of *chat_id* (of one session when given) whose
        anchor is known, oldest first, taken out of the store."""
        items = self.pending.get(chat_id) or []
        out = [p for p in items if p.held_key is None
               and (session is None or p.session == session)]
        rest = [p for p in items if p not in out]
        if rest:
            self.pending[chat_id] = rest
        else:
            self.pending.pop(chat_id, None)
        return out

    def has_pending(self, session: str) -> bool:
        return any(p.session == session for items in self.pending.values()
                   for p in items)

    def requeue_front(self, pending: Pending) -> None:
        self.pending.setdefault(pending.chat_id, []).insert(0, pending)

    # the day sweep ---------------------------------------------------------

    def sweep(self, now: float | None = None) -> list[Pending]:
        """Give up deliveries and batches older than a day (returned, so
        the caller audits them) and delete copies older than a day."""
        now = self._clock() if now is None else now
        expired: list[Pending] = []
        for chat_id in list(self.pending):
            keep = []
            for p in self.pending[chat_id]:
                (expired if now - p.created > KEEP_SECONDS else keep).append(p)
            if keep:
                self.pending[chat_id] = keep
            else:
                del self.pending[chat_id]
        for session in list(self.batches):
            keep_b = []
            for b in self.batches[session]:
                if now - b.created > KEEP_SECONDS:
                    expired.append(Pending(session=session, chat_id=0, anchor=None,
                                           held_key=None, prompt_id=None,
                                           batches=[b], created=b.created))
                else:
                    keep_b.append(b)
            if keep_b:
                self.batches[session] = keep_b
            else:
                del self.batches[session]
        self._drop_old_requests()
        for p in expired:
            for b in p.batches:
                for item in b.items:
                    delete_copy(item)
        sweep_disk()
        return expired


OUTBOX = Outbox()


def sweep_disk(now: float | None = None) -> int:
    """Delete outbox files and empty chat folders older than a day (wall
    clock, from their mtime). Never follows a link. Returns how many files
    went."""
    base = outbox_dir()
    now = time.time() if now is None else now
    removed = 0
    try:
        folders = list(os.scandir(base))
    except OSError:
        return 0
    for folder in folders:
        try:
            if not folder.is_dir(follow_symlinks=False):
                continue
            entries = list(os.scandir(folder.path))
        except OSError:
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
                if now - st.st_mtime > KEEP_SECONDS:
                    os.unlink(entry.path)
                    removed += 1
            except OSError:
                continue
        try:
            if not os.listdir(folder.path) and \
                    now - folder.stat(follow_symlinks=False).st_mtime > KEEP_SECONDS:
                os.rmdir(folder.path)
        except OSError:
            pass
    return removed


def delete_copy(item: Item) -> None:
    try:
        os.unlink(item.path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("turn file copy not deleted: %s", e)


# ---- the copy ----------------------------------------------------------------

def shown_name(entry: str) -> str:
    """The name a file goes to the chat under: its base name as Claude
    gave it, any direction control shown as a visible marker."""
    name = os.path.basename(str(entry).rstrip("/")) or "file"
    return bidi_guard.reveal(name)[:200]


def allowed_root_for(snapshot, cwd: str | None) -> tuple[bool, str | None]:
    """``(bound, root)``: whether the session's snapshot holds it to a
    members' root, and the one folder it may send from (None: nothing)."""
    root = snapshot.get("root") if isinstance(snapshot, dict) else None
    if root is None:
        return False, None
    from aipager.dtach import enforce
    return True, enforce._send_root(cwd, root, False, False)


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino, a.st_size) == (b.st_dev, b.st_ino, b.st_size)


def _opened_path(fd: int) -> str | None:
    """Where an open file really is (Linux's ``/proc/self/fd``, macOS's
    ``F_GETPATH``), or None when unknown."""
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        pass
    try:
        import fcntl
        getpath = getattr(fcntl, "F_GETPATH", None)
        if getpath is None:
            return None
        raw = fcntl.fcntl(fd, getpath, b"\0" * 1024)
        return os.fsdecode(raw.split(b"\0", 1)[0]) or None
    except (OSError, ImportError, ValueError):
        return None


def _copy_failed_for(code: str) -> CopyFailed:
    if code == send_check.CODE_TOO_BIG:
        return CopyFailed(CAUSE_TOO_BIG, code)
    if code in (send_check.CODE_MISSING, send_check.CODE_UNREADABLE):
        return CopyFailed(CAUSE_GONE, code)
    return CopyFailed(CAUSE_NOT_ALLOWED, code)


def copy_one(entry: str, cwd: str | None, chat_id: int, display: str | None,
             *, bound: bool = False, allowed_root: str | None = None) -> Item:
    """Check one file again and copy it into the chat's outbox folder.
    Raises :class:`CopyFailed`. Blocking: run it in a thread.

    The path is resolved ONCE. The floor, the file checks and the root are
    judged on that one resolution (``real``), ``real`` is what is opened
    (without following a link), and the open file must be the one found
    there (``fstat``: same file, regular, one link, size) and still be at
    ``real`` (``/proc/self/fd``), so a link or folder swapped at any point
    after the resolution sends nothing (review 2026-10-17, rev-iter1-001)."""
    entry = safety._js_trim(entry)
    if not isinstance(entry, str) or not entry or "\x00" in entry:
        raise CopyFailed(CAUSE_NOT_ALLOWED, "unreadable-path")
    spelled = safety._norm(entry, cwd)
    real = safety._realpath(spelled)
    try:
        before = os.lstat(real)
    except FileNotFoundError as exc:
        raise CopyFailed(CAUSE_GONE, "no-such-file") from exc
    except OSError as exc:
        raise CopyFailed(CAUSE_GONE, str(exc)) from exc
    code = send_check.check_resolved(spelled, real)
    if code is not None:
        raise _copy_failed_for(code)
    if bound and not send_check.inside_root_resolved(
            spelled, real, send_check.root_set(allowed_root)):
        raise CopyFailed(CAUSE_NOT_ALLOWED, "outside-root")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(real, flags)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise CopyFailed(CAUSE_NOT_ALLOWED, "became-a-link") from exc
        raise CopyFailed(CAUSE_GONE, str(exc)) from exc
    dest: Path | None = None
    try:
        st = os.fstat(fd)
        code = send_check.stat_code(st)
        if code is not None:
            raise _copy_failed_for(code)
        if not _same_file(st, before):
            raise CopyFailed(CAUSE_GONE, "changed-after-check")
        # Opened where it was checked: a folder on the way swapped for a
        # link after the resolution would lead elsewhere.
        opened = _opened_path(fd)
        if opened is not None and safety._fold(opened) != safety._fold(real):
            raise CopyFailed(CAUSE_GONE, "moved-after-check")
        folder = uploads.ensure_chat_folder(outbox_dir(), chat_id)
        name = shown_name(entry)
        dest = uploads.reserve_file(folder, name)
        out = os.open(dest, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
                      | getattr(os, "O_CLOEXEC", 0))
        copied = 0
        try:
            with os.fdopen(fd, "rb", closefd=False) as src, \
                    os.fdopen(out, "wb", closefd=True) as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > st.st_size:
                        break
                    dst.write(chunk)
        except OSError as exc:
            raise CopyFailed(CAUSE_GONE, str(exc)) from exc
        if copied != st.st_size:
            raise CopyFailed(CAUSE_GONE, "changed-while-copied")
        uploads.seal_file(dest)
    except CopyFailed:
        if dest is not None:
            _unlink_quiet(dest)
        raise
    except uploads.UnsafeUploadFolder as exc:
        raise CopyFailed(CAUSE_OTHER, f"unsafe-outbox-folder:{exc.args}") from exc
    except OSError as exc:
        if dest is not None:
            _unlink_quiet(dest)
        raise CopyFailed(CAUSE_OTHER, str(exc)) from exc
    finally:
        os.close(fd)
    kind, w, h = media_kind.probe(str(dest))
    method = media_kind.choose_method(kind, w, h, st.st_size, display)
    return Item(name=name, path=dest, size=st.st_size, kind=kind, width=w,
                height=h, method=method)


def _unlink_quiet(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _entry_size(entry: str, cwd: str | None) -> int | None:
    try:
        return os.stat(safety._realpath(safety._norm(entry, cwd))).st_size
    except (OSError, ValueError, TypeError):
        return None


def copy_batch(session: str, chat_id: int, files: list[str], cwd: str | None,
               caption: str | None, display: str | None, agent_id: str = "",
               *, bound: bool = False, allowed_root: str | None = None,
               clock=time.monotonic) -> Batch:
    """Copy every file of one call, in order (blocking: run it in a
    thread). A file that fails is a :class:`Failure` of the batch."""
    batch = Batch(session=session, caption=caption, display=display, items=[],
                  agent_id=agent_id, created=clock())
    for entry in files:
        try:
            batch.items.append(copy_one(entry, cwd, chat_id, display, bound=bound,
                                        allowed_root=allowed_root))
            log.info("turn file copied: %s", shown_name(entry))
        except CopyFailed as exc:
            log.warning("turn file failed: %s (%s)", shown_name(entry), exc)
            batch.failures.append(Failure(shown_name(entry), _entry_size(entry, cwd),
                                          exc.cause))
    return batch


# ---- sorting and captions --------------------------------------------------

def caption_for(label: str, caption: str | None, first: bool) -> str:
    """``💬 <label>``, then Claude's caption on the next line for the
    batch's first send; at most 1,024 UTF-16 units, cut at the last
    whitespace with "…". Plain text."""
    text = f"{CAPTION_MARK} {label}"
    if first and caption:
        text += "\n" + caption
    if utf16_len(text) <= CAPTION_MAX:
        return text
    budget = CAPTION_MAX - utf16_len(ELLIPSIS)
    cut = ""
    used = 0
    for ch in text:
        size = utf16_len(ch)
        if used + size > budget:
            break
        cut += ch
        used += size
    space = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
    if space > len(CAPTION_MARK):
        cut = cut[:space]
    return cut.rstrip() + ELLIPSIS


def _chunks(items: list[Item]) -> list[list[Item]]:
    """*items* (one album class) in chunks of at most 10 items and 50 MiB,
    as even as they can be: 11 items are 6 + 5, never 10 + 1."""
    n = len(items)
    k = -(-n // ALBUM_MAX_ITEMS)
    while True:
        base, extra = divmod(n, k)
        out, i = [], 0
        for c in range(k):
            size = base + (1 if c < extra else 0)
            out.append(items[i:i + size])
            i += size
        if k >= n or all(sum(it.size for it in chunk) <= ALBUM_MAX_BYTES
                         for chunk in out):
            return out
        k += 1


def plan_sends(items: list[Item], label: str, caption: str | None) -> list[Planned]:
    """The Telegram sends for one batch's files, in the order Claude gave
    them: albums by class (photo + video, audio, document), classes in the
    order of their first file, an animation always on its own. Each send's
    first item carries the caption; Claude's caption only the first send's."""
    groups: list[list[Item]] = []
    by_class: dict[str, list[Item]] = {}
    for item in items:
        cls = _ALBUM_CLASS.get(item.method)
        if cls is None:            # an animation: alone, where it is
            groups.append([item])
            continue
        if cls not in by_class:
            by_class[cls] = []
            groups.append(by_class[cls])
        by_class[cls].append(item)
    planned: list[Planned] = []
    for group in groups:
        for chunk in _chunks(group):
            method = chunk[0].method if len(chunk) == 1 else METHOD_MEDIA_GROUP
            planned.append(Planned(method=method, items=chunk,
                                   caption=caption_for(label, caption, not planned)))
    return planned


# ---- the audit ----------------------------------------------------------------

ACTION_SENT = "FileSent"
ACTION_REFUSED = "FileRefused"
ACTION_FAILED = "FileFailed"


def audit_file(ctx: dict, action: str, name: str, size: int | None = None,
               reason: str = "") -> None:
    """One audit record for one file. *ctx*: ``session``, ``label``,
    ``user_id``, ``username``, ``scope_label``, ``scope_chat_id``."""
    try:
        audit_mod.append(
            session=ctx.get("session", ""), label=ctx.get("label", ""),
            action=action, tool=send_check.SEND_TOOL, summary=name,
            user_id=ctx.get("user_id"), username=ctx.get("username", ""),
            scope_label=ctx.get("scope_label", ""),
            scope_chat_id=ctx.get("scope_chat_id"),
            denied=action == ACTION_REFUSED, reason=reason,
            file_name=name or "?", file_size=size)
    except Exception:
        log.exception("turn file audit failed")


# ---- the delivery -----------------------------------------------------------

#: Telegram's words for a file over its limit ("Request Entity Too Large",
#: PTB's "File too large").
_TOO_BIG_RE = re.compile(r"too\s+(?:large|big)", re.IGNORECASE)


def failed_line(tr, failure: Failure) -> str:
    """The chat's line for one file that could not be sent."""
    msg = FAILED_LINES.get(failure.cause, FAILED_LINES[CAUSE_OTHER])
    return tr.text(msg, name=failure.name)


def send_cause(exc: BaseException) -> str:
    """The failure cause of a Telegram error on a file send."""
    if _TOO_BIG_RE.search(str(exc)):
        return CAUSE_TOO_BIG
    if isinstance(exc, (BadRequest, Forbidden)):
        return CAUSE_REFUSED
    return CAUSE_OTHER


def _input_file(item: Item) -> InputFile:
    return InputFile(item.path.read_bytes(), filename=item.name)


def _input_media(item: Item, caption: str | None):
    kind = {media_kind.METHOD_PHOTO: InputMediaPhoto,
            media_kind.METHOD_VIDEO: InputMediaVideo,
            media_kind.METHOD_AUDIO: InputMediaAudio}.get(item.method, InputMediaDocument)
    return kind(media=_input_file(item), caption=caption or None)


def send_kwargs(anchor: int | None) -> dict:
    """What every file send carries: silent, a reply to *anchor* that
    still goes when that message is gone, and the upload's timeouts."""
    return {"disable_notification": True, "reply_to_message_id": anchor or None,
            "allow_sending_without_reply": True,
            "write_timeout": FILE_SEND_WRITE_TIMEOUT,
            "read_timeout": FILE_SEND_READ_TIMEOUT}


async def send_planned(bot, chat_id: int, planned: Planned,
                       anchor: int | None) -> bool:
    """One Telegram send. False when the chat is flood-muted (nothing was
    sent: the caller keeps the files); any other error is raised."""
    kw = send_kwargs(anchor)
    item = planned.items[0]
    cap = planned.caption or None
    try:
        if planned.method == METHOD_MEDIA_GROUP:
            media = [_input_media(it, cap if i == 0 else None)
                     for i, it in enumerate(planned.items)]
            await bot.send_media_group(chat_id, media=media, **kw)
        elif planned.method == media_kind.METHOD_PHOTO:
            await bot.send_photo(chat_id, photo=_input_file(item), caption=cap, **kw)
        elif planned.method == media_kind.METHOD_VIDEO:
            await bot.send_video(chat_id, video=_input_file(item), caption=cap, **kw)
        elif planned.method == media_kind.METHOD_ANIMATION:
            await bot.send_animation(chat_id, animation=_input_file(item),
                                     caption=cap, **kw)
        elif planned.method == media_kind.METHOD_AUDIO:
            await bot.send_audio(chat_id, audio=_input_file(item), caption=cap, **kw)
        else:
            await bot.send_document(chat_id, document=_input_file(item),
                                    caption=cap, **kw)
    except FloodMuted:
        return False
    return True


_upload_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    """One upload at a time, daemon-wide: PTB holds each upload in memory
    and keeps a pool connection for as long as it takes."""
    global _upload_lock
    if _upload_lock is None:
        _upload_lock = asyncio.Lock()
    return _upload_lock


async def deliver(bot, pending: Pending, label: str, tr) -> bool:
    """Send a turn's files (*pending*), then one line per file that could
    not be sent. True when everything is done; False when a flood mute
    stopped it, with what is left still in *pending* (the caller queues
    it again). Each file is audited and its copy deleted once settled."""
    async with _lock():
        while pending.batches:
            batch = pending.batches[0]
            while batch.items:
                planned = plan_sends(batch.items, label, batch.caption)[0]
                try:
                    sent = await send_planned(bot, pending.chat_id, planned,
                                              pending.anchor)
                except Exception as exc:
                    cause = send_cause(exc)
                    log.warning("turn file failed: %s (%s: %s)",
                                ", ".join(i.name for i in planned.items), cause, exc)
                    for it in planned.items:
                        batch.failures.append(Failure(it.name, it.size, cause))
                        audit_file(pending.audit, ACTION_FAILED, it.name, it.size, cause)
                else:
                    if not sent:
                        log.info("turn files held: chat %s is flood-muted",
                                 pending.chat_id)
                        return False
                    for it in planned.items:
                        log.info("turn file sent: %s (%s, %d bytes)", it.name,
                                 planned.method, it.size)
                        audit_file(pending.audit, ACTION_SENT, it.name, it.size)
                # Claude's caption goes with the batch's first send only.
                batch.caption = None
                for it in planned.items:
                    batch.items.remove(it)
                    delete_copy(it)
            while batch.failures:
                failure = batch.failures[0]
                try:
                    await bot.send_message(
                        pending.chat_id, failed_line(tr, failure),
                        disable_notification=True,
                        reply_to_message_id=pending.anchor or None,
                        allow_sending_without_reply=True)
                except FloodMuted:
                    return False
                except Exception:
                    log.warning("turn file failure line not sent", exc_info=True)
                batch.failures.pop(0)
            pending.batches.pop(0)
    return True

