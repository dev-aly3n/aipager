"""Claude Code background workers, as the hook and the status line see them.

Claude Code can move a conversation into a background job (``claude
daemon``): the session's own claude stays in the terminal, parked on the
job, and a worker process runs the conversation's turns. Workers inherit
the session's environment, ``CLAUDE_DTACH_SESSION`` included, but each
one also carries ``CLAUDE_JOB_DIR=<config>/jobs/<job>``. Their hooks must
never count for the inherited session just because of that tag:

- a worker whose job is *linked* (the daemon found the session's own
  claude parked on it and wrote a link file) counts as that session;
- any other worker is *foreign*: its hooks write, consume and report
  nothing, and only the PreToolUse safety check still runs, read-only.

This module holds what both hook binaries need for that: the job read from
the environment, the runtime file paths, owner-checked bounded reads,
atomic 0600 writes, the bounded scan of Claude's session registry
(``<config>/sessions/<pid>.json``) and the resolution itself.

Stdlib plus :mod:`aipager.instance` only. It is imported lazily, and only
when ``CLAUDE_JOB_DIR`` is set: never ``aipager.i18n``, asyncio, inject
or the bot. Every caller uses the path helpers as module attributes
(``bg_jobs.link_path(job)``), so the test suite can redirect them.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from aipager import instance

JOB_ENV = "CLAUDE_JOB_DIR"

#: A job id Claude Code gives a background job (8 hex characters today),
#: kept to a safe file-name alphabet because it becomes part of a path.
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: The pid map's own name under the same prefix: never a job's link file.
_RESERVED_JOBS = frozenset({"pids"})

#: An aipager session name, as the dtach labels allow (inject._VALID_NAME).
_SESSION_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

#: The longest file any reader here accepts; a longer one decides nothing.
MAX_BYTES = 64 * 1024

#: How many of Claude's registry files one resolution reads at most.
REGISTRY_LIMIT = 64

#: Module seam: tests simulate a file owned by someone else without
#: patching the global ``os`` module.
_getuid = os.getuid

LINKED = "linked"
FOREIGN = "foreign"


def job_from_env(env=None) -> tuple[str, str] | None:
    """``(job, config)`` from ``CLAUDE_JOB_DIR``, or None when it is unset,
    blank or not a job folder: ``job`` is the folder's name, ``config``
    the Claude config folder two levels up (``<config>/jobs/<job>``)."""
    raw = (os.environ if env is None else env).get(JOB_ENV, "")
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    job = os.path.basename(raw)
    if not _JOB_ID.match(job) or job in _RESERVED_JOBS:
        return None
    config = os.path.dirname(os.path.dirname(raw))
    if not config or not os.path.isabs(raw):
        return None
    return job, config


def link_path(job: str) -> Path:
    """The daemon's link of *job* to the aipager session parked on it."""
    return Path(instance.runtime_tmp_dir()) / f"claude-bgjob-{job}.json"


def pidmap_path() -> Path:
    """The daemon's map of each session's own claude pid to the session."""
    return Path(instance.runtime_tmp_dir()) / "claude-bgjob-pids.json"


def exit_path(session: str) -> Path:
    """Where a session's launch shell records claude's exit."""
    return Path(instance.runtime_tmp_dir()) / f"claude-exit-{session}.json"


def read_own_json(path) -> dict | None:
    """The JSON object in *path* when it is a regular file of ours (not a
    symlink, not another user's) of at most :data:`MAX_BYTES`; else None.
    Opened non-blocking, so a FIFO planted there cannot stall a hook.
    Never raises."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, ValueError, TypeError):
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != _getuid():
            return None
        raw = os.read(fd, MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def write_own_json(path, obj: dict) -> bool:
    """Write *obj* to *path* atomically (a temp file, then ``os.replace``),
    mode 0600, never through a symlink. False on any failure."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        body = json.dumps(obj).encode()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                     0o600)
        try:
            os.write(fd, body)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def remove_own(path) -> None:
    """Remove *path* when it is ours (a symlink is removed, never
    followed). Best effort."""
    try:
        st = os.lstat(path)
        if st.st_uid != _getuid():
            return
        os.unlink(path)
    except OSError:
        pass


_REGISTRY_FILE = re.compile(r"^[0-9]{1,10}\.json$")


def scan_registry(config: str, limit: int = REGISTRY_LIMIT) -> list[dict]:
    """The entries of Claude's session registry (``<config>/sessions/
    <pid>.json``), at most *limit* files, each read owner-checked. Empty
    when the folder cannot be read."""
    out: list[dict] = []
    try:
        with os.scandir(os.path.join(config, "sessions")) as it:
            names = []
            for entry in it:
                if _REGISTRY_FILE.match(entry.name):
                    names.append(entry.name)
                    if len(names) >= limit:
                        break
    except OSError:
        return out
    for name in sorted(names):
        data = read_own_json(os.path.join(config, "sessions", name))
        if data is not None:
            out.append(data)
    return out


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip())
    return None


def _session_ok(value) -> bool:
    return isinstance(value, str) and bool(_SESSION_NAME.match(value))


def _worker(role: str, job: str, session: str) -> dict:
    return {"role": role, "job": job, "session": session}


def _from_registry(job: str, config: str) -> str | None:
    """The session whose own claude (an interactive registry entry parked
    on *job*) the daemon's pid map names, or None. The map's start time
    must equal the entry's ``procStart`` when both are known, so a reused
    pid never links. More than one answer is no answer."""
    found: set[str] = set()
    pids = None
    for entry in scan_registry(config):
        if entry.get("kind") != "interactive" or entry.get("parkedJobId") != job:
            continue
        pid = _as_int(entry.get("pid"))
        if pid is None:
            continue
        if pids is None:
            pidmap = read_own_json(pidmap_path()) or {}
            pids = pidmap.get("pids") if isinstance(pidmap.get("pids"), dict) else {}
        rec = pids.get(str(pid))
        if not isinstance(rec, dict) or not _session_ok(rec.get("session")):
            continue
        start, proc_start = _as_int(rec.get("start")), _as_int(entry.get("procStart"))
        if start is not None and proc_start is not None and start != proc_start:
            continue
        found.add(rec["session"])
    return found.pop() if len(found) == 1 else None


def resolve_worker(job: str, config: str, inherited: str) -> dict:
    """Who a worker of *job* counts for: ``{"role", "job", "session"}``.

    1. The daemon's link file for *job* (its ``job`` field must be *job*)
       names the session: linked.
    2. Else Claude's registry: an interactive entry parked on *job* whose
       pid the daemon's pid map names (closes the moment before the link
       file is written): linked.
    3. Else foreign; ``session`` stays the inherited tag, which only the
       read-only safety check uses.
    Any failure is foreign."""
    try:
        link = read_own_json(link_path(job))
        if link is not None and link.get("job") == job and _session_ok(link.get("session")):
            return _worker(LINKED, job, link["session"])
        session = _from_registry(job, config)
        if session is not None:
            return _worker(LINKED, job, session)
    except Exception:
        pass
    return _worker(FOREIGN, job, inherited)


def resolve_from_env(inherited: str, env=None) -> dict:
    """:func:`resolve_worker` for the job in ``CLAUDE_JOB_DIR`` (the caller
    has seen it set). A value that names no job is foreign. Never
    raises."""
    try:
        parsed = job_from_env(env)
        if parsed is None:
            return _worker(FOREIGN, "", inherited)
        job, config = parsed
        return resolve_worker(job, config, inherited)
    except Exception:
        return _worker(FOREIGN, "", inherited)
