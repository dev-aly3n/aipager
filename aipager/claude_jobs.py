"""Claude Code's background jobs, as the daemon reads them.

Claude Code can move a conversation into a background job (``←`` at an
empty prompt, ``claude daemon``). The session's own claude then stays in
its terminal, parked on the job: its entry in Claude's session registry
(``<config>/sessions/<pid>.json``) gains ``parkedJobId``, and the
conversation's transcript ends with a ``continued-in`` record naming the
job's new conversation. aipager must never type into such a session, and
must know which job holds a conversation before it resumes it.

This module reads Claude's own files for that, under :func:`config_dir`
(the folder ``claude_paths.for_sessions()`` resolves for the sessions'
environment; never a folder a hook reported, since any local process can
write to the daemon's socket):

- :func:`read_registry`: the registry, each file parsed once per change;
- :func:`find_client`: the session's own claude in it;
- :func:`transcript_continued_in`, :func:`find_transcript`,
  :func:`follow_continued`: where a conversation moved;
- :func:`live_holder`: the background process holding a conversation;
- :func:`job_state`: whether a job is busy, idle or gone;
- :func:`run_claude` / :func:`run_claude_sync`: ``claude stop`` and
  ``claude agents --json``, through module seams.
- :func:`read_exit`, :func:`exit_signal`, :func:`is_start_failure`: the
  exit record the launch shell writes (``aipager.claude_launch.exit_record``)
  and what it says about why a session ended.

Daemon side only (it may use asyncio); never imported by the hook, and it
never imports ``aipager.i18n``. Every reader is bounded and never raises:
an unreadable folder or file reads as "nothing known", which leaves
aipager's behaviour as it was before this module existed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from aipager.dtach import bg_jobs

log = logging.getLogger(__name__)

# -- constants --------------------------------------------------------------

#: A claude that exits within this many seconds of its start "stopped right
#: after starting" (step 4's end notices).
START_WINDOW_SECONDS = 10
#: An exit record older than this belongs to an earlier launch.
EXIT_FRESH_SECONDS = 120
#: How long ``claude stop <job>`` may take.
STOP_TIMEOUT = 10
#: How long a bring-back waits for a stopped job's process to go away.
HOLDER_WAIT = 5
#: How long ``claude agents --json`` may take.
AGENTS_TIMEOUT = 5
#: After a bring-back, the held messages go out at the latest this late.
RELEASE_BACKSTOP = 10
#: A park younger than this may hide a job about to continue a moved turn
#: (a just-forked job reads "idle" for a moment before "busy"): an
#: unforced bring-back waits it out or asks, never stops the job.
PARK_SETTLE_SECONDS = 3.0

#: How many registry files one read parses, at most (files already
#: parsed and unchanged cost no parse).
REGISTRY_SCAN_LIMIT = 512
#: How many registry file names one read looks at, at most (each only
#: stat'ed).
REGISTRY_NAMES_LIMIT = 4096
#: How much of a transcript's end the ``continued-in`` check reads.
TAIL_READ_BYTES = 256 * 1024
#: How many ``continued-in`` hops :func:`follow_continued` takes at most.
MAX_HOPS = 3
#: How many levels of parents the dtach-ancestry fallback walks.
ANCESTRY_LEVELS = 8
#: How long :func:`config_dir` keeps its answer (it reads settings files).
CONFIG_DIR_TTL = 30.0

#: Claude Code's conversation ids (UUIDs today), kept to a safe file-name
#: alphabet because they become part of a path.
_SESSION_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{0,63}$")
_REGISTRY_FILE = re.compile(r"^([0-9]{1,10})\.json$")

# -- seams ------------------------------------------------------------------
# Tests replace THESE module attributes, never asyncio, subprocess, time or
# os themselves (CLAUDE.md).
_create_subprocess_exec = asyncio.create_subprocess_exec
_subprocess_run = subprocess.run
#: Runs a blocking read in a worker thread (:func:`off_loop`).
_to_thread = asyncio.to_thread


def _glob_transcripts(projects: Path, session_id: str) -> list[Path]:
    """Every ``<projects>/*/<session_id>.jsonl`` (it lists every project
    folder: the slow part of :func:`find_transcript`). A seam, so a test
    can tell which thread runs it."""
    return sorted(projects.glob(f"*/{session_id}.jsonl"))
#: Opens a transcript for the tail read (a test counts the bytes read).
_open_binary = open


def _proc_start(pid: int) -> int | None:
    """Field 22 of ``/proc/<pid>/stat``, or None (off Linux, unreadable)."""
    from aipager import peer_auth
    return peer_auth.proc_start(pid)


def _ppid(pid: int) -> int | None:
    """*pid*'s parent, from ``/proc/<pid>/stat``; None when unknown."""
    from aipager import peer_auth
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8",
                                                       errors="replace")
    except (OSError, ValueError):
        return None
    return peer_auth._ppid_from_stat(raw)


def _dtach_pids(session: str) -> list[int]:
    """The dtach processes holding *session*'s socket (its master)."""
    from aipager.dtach import inject
    try:
        return inject._proc_socket_pids(inject._sock_path(session))
    except Exception:  # noqa: BLE001 - a lookup never fails the caller
        return []


def _pid_alive(pid: int) -> bool:
    """True while *pid* exists (signal 0 checks without delivering) and,
    where ``/proc`` tells, is not a zombie: a killed claude its parent has
    not reaped yet still answers signal 0."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError):
        return False
    return _proc_state(pid) not in ("Z", "X")


def _proc_state(pid: int) -> str:
    """The state letter in ``/proc/<pid>/stat`` (``Z`` for a zombie), or
    ``""`` when it cannot be read (off Linux, gone)."""
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as f:
            raw = f.read(4096)
    except (OSError, ValueError):
        return ""
    # The name in brackets may hold spaces and brackets: the state is the
    # first field after the last ")".
    rest = raw[raw.rfind(b")") + 1:].split()
    return rest[0].decode("ascii", "replace") if rest else ""


def _pid_tagged(pid: int, session: str) -> bool:
    """Whether *pid* runs with ``CLAUDE_DTACH_SESSION=<session>`` in its
    environment: the session's own claude does (it inherits the launch
    shell's), another session's does not. Where ``/proc`` is missing
    (macOS) nothing can be read, and the pid is taken as reported."""
    if not os.path.isdir("/proc"):
        return True
    from aipager import peer_auth
    return peer_auth._environ_has(int(pid), b"CLAUDE_DTACH_SESSION",
                                  session.encode())


# -- where Claude's files are -------------------------------------------------

_config_cache: tuple[float, Path] | None = None
#: The registry and transcript caches are also used from worker threads
#: (:func:`off_loop`): every change to them is made under this lock.
_REGISTRY_LOCK = threading.Lock()


def config_dir() -> Path:
    """The Claude config folder aipager's sessions use: the folder that
    holds ``projects/`` for the sessions' environment (where Claude Code's
    final ``CLAUDE_CONFIG_DIR`` points), with ``sessions/`` and ``jobs/``
    beside it. Kept for :data:`CONFIG_DIR_TTL` seconds. A test points
    this function at a folder under its tmp_path."""
    global _config_cache
    now = time.monotonic()
    if _config_cache is not None and now - _config_cache[0] < CONFIG_DIR_TTL:
        return _config_cache[1]
    from aipager import claude_paths
    path = claude_paths.for_sessions().projects.parent
    _config_cache = (now, path)
    return path


def valid_session_id(value) -> bool:
    """Whether *value* looks like one of Claude Code's conversation ids."""
    return isinstance(value, str) and bool(_SESSION_ID.match(value))


def valid_job(value) -> bool:
    """Whether *value* is a job id aipager can name a file after."""
    return (isinstance(value, str) and bool(bg_jobs._JOB_ID.match(value))
            and value not in bg_jobs._RESERVED_JOBS)


# -- the registry -------------------------------------------------------------

_INTERACTIVE = "interactive"

#: file path -> (mtime_ns, size, parsed entry or None, read whole)
_REGISTRY_CACHE: dict[str, tuple[int, int, dict | None, bool]] = {}


class Registry(list):
    """What :func:`read_registry` returns: the entries, and ``complete``,
    False when a file was left out (too many names, the parse budget
    spent, a file that could not be read or parsed). Only a complete
    registry may say a job has no entry, so is gone. A plain list (a
    caller's own) counts as complete."""

    complete: bool = True


def _as_int(value) -> int | None:
    return bg_jobs._as_int(value)


def _entry(data: dict, file_pid: int) -> dict | None:
    """One registry file's fields aipager reads, normalised; None when it
    names no process."""
    pid = _as_int(data.get("pid"))
    if pid is None:
        pid = file_pid
    if pid <= 0:
        return None

    def text(key: str) -> str:
        value = data.get(key)
        return value if isinstance(value, str) else ""

    return {
        "pid": pid,
        "sessionId": text("sessionId"),
        "kind": text("kind"),
        "jobId": text("jobId"),
        "parkedJobId": text("parkedJobId"),
        "procStart": _as_int(data.get("procStart")),
        "status": text("status"),
        "spare": data.get("spare") is True,
    }


def read_registry(config: Path | None = None) -> Registry | None:
    """Claude's session registry: one normalised entry per readable file
    of ``<config>/sessions/<pid>.json``, each read owner-checked and
    parsed again only when its (mtime, size) changed. Every file name is
    looked at (at most :data:`REGISTRY_NAMES_LIMIT`), and at most
    :data:`REGISTRY_SCAN_LIMIT` new parses are made, newest files first.
    A file that cannot be read or parsed is left out, and then the
    result's ``complete`` is False. None when the folder cannot be read.
    Safe to call from a worker thread."""
    folder = os.path.join(str(config if config is not None else config_dir()),
                          "sessions")
    found: list[tuple[str, int]] = []
    complete = True
    try:
        with os.scandir(folder) as it:
            for item in it:
                m = _REGISTRY_FILE.match(item.name)
                if m:
                    found.append((item.name, int(m.group(1))))
                    if len(found) >= REGISTRY_NAMES_LIMIT:
                        complete = False
                        break
    except OSError:
        return None
    stats: list[tuple[str, int, os.stat_result]] = []
    for name, file_pid in found:
        try:
            stats.append((name, file_pid,
                          os.stat(os.path.join(folder, name), follow_symlinks=False)))
        except OSError:
            continue                    # gone since the listing
    with _REGISTRY_LOCK:
        parsed: dict[str, tuple[dict | None, bool]] = {}
        budget = REGISTRY_SCAN_LIMIT
        for name, file_pid, st in sorted(stats, key=lambda x: -x[2].st_mtime_ns):
            path = os.path.join(folder, name)
            cached = _REGISTRY_CACHE.get(path)
            if cached is not None and cached[:2] == (st.st_mtime_ns, st.st_size):
                parsed[name] = (cached[2], cached[3])
                continue
            if budget <= 0:
                continue                # left out: it reads as not whole below
            budget -= 1
            data = bg_jobs.read_own_json(path)
            entry = _entry(data, file_pid) if data is not None else None
            _REGISTRY_CACHE[path] = (st.st_mtime_ns, st.st_size, entry, data is not None)
            parsed[name] = (entry, data is not None)
        seen = {os.path.join(folder, name) for name, _pid, _st in stats}
        for stale in [p for p in _REGISTRY_CACHE
                      if p not in seen and os.path.dirname(p) == folder]:
            del _REGISTRY_CACHE[stale]
    entries = Registry()
    for name, _pid, _st in sorted(stats):
        entry, whole = parsed.get(name, (None, False))
        if not whole:
            complete = False
        if entry is not None:
            entries.append(dict(entry))
    entries.complete = complete
    return entries


async def off_loop(fn, *args, **kwargs):
    """``fn(*args, **kwargs)`` in a worker thread: the registry reads
    (a stat of every file) and the transcript lookups (a glob over every
    project folder) never block the event loop on the hot paths (the
    message gate, the hook receiver, the bring-back, the monitor's scan).
    *fn* must only read: every cache it touches is thread-safe."""
    return await _to_thread(fn, *args, **kwargs)


async def read_registry_async(config: Path | None = None) -> Registry | None:
    """:func:`read_registry` off the event loop."""
    return await off_loop(read_registry, config)


def prefetch_transcripts(session_ids) -> None:
    """Look up the transcript of each of *session_ids* and read where it
    moved, so the same lookups made next on the event loop find them in
    the caches (a miss is remembered for :data:`FIND_MISS_TTL`). For a
    worker thread (:func:`off_loop`)."""
    for sid in dict.fromkeys(i for i in session_ids if valid_session_id(i)):
        path = find_transcript(sid, cache_misses=True)
        if path is not None:
            transcript_continued_in(path)


def _unique(candidates: list[dict]) -> tuple[bool, dict | None]:
    """``(decided, entry)``: one candidate is the answer, none means look
    further, more than one means no answer at all (S7)."""
    if not candidates:
        return False, None
    if len(candidates) == 1:
        return True, candidates[0]
    return True, None


def _descends(pid: int, ancestors: set[int]) -> bool:
    """Whether one of *pid*'s first :data:`ANCESTRY_LEVELS` parents is in
    *ancestors* (the launch shell sits between dtach and claude)."""
    current = pid
    for _ in range(ANCESTRY_LEVELS):
        parent = _ppid(current)
        if parent is None or parent <= 1:
            return False
        if parent in ancestors:
            return True
        current = parent
    return False


def _on_linux() -> bool:
    """Whether a process's start time is readable here (``/proc``): a
    module seam, so tests can take either side."""
    return sys.platform.startswith("linux")


def _start_matches(entry: dict, pid: int) -> bool:
    """Whether *pid* is the process *entry* was written for: its
    ``procStart`` equals the process's. An entry without one matches.
    On Linux a start that cannot be read (the process is gone, or the pid
    is someone else's) is no match; elsewhere nothing can be read, and
    the pid is taken as it is."""
    recorded = entry.get("procStart")
    if recorded is None:
        return True
    actual = _proc_start(pid)
    if actual is None:
        return not _on_linux()
    return actual == recorded


def _start_differs(entry: dict, pid: int) -> bool:
    """Whether *pid* is known to be another process than the one *entry*
    was written for: both start times are readable and differ (the pid
    was reused). Unreadable is no proof either way."""
    recorded = entry.get("procStart")
    if recorded is None:
        return False
    actual = _proc_start(pid)
    return actual is not None and actual != recorded


def find_client(sess, entries: list[dict] | None) -> dict | None:
    """The registry entry of *sess*'s own claude (an ``interactive`` one),
    or None. Tried in order, the first level with an answer wins:

    1. the pid its own hooks reported (``sess.claude_pid``), when that
       process is alive and the entry's ``procStart`` equals its start
       (:func:`_start_matches`): Claude leaves a dead claude's file
       behind, and a pid can be reused;
    2. only when no pid is known: an entry whose pid descends from the
       session's dtach master;
    3. an entry parked on the job named by the first 8 characters of the
       session's conversation id (the job's own id, adopted from its hooks);
    4. an entry holding the session's conversation id.

    Levels 3 and 4 take only entries whose process is alive (not a
    zombie) and, where both start times are readable, the one the entry
    was written for.

    Two candidates on one level are no answer. The pid found is kept on
    the session (``claude_pid``)."""
    if not entries:
        return None
    interactive = [e for e in entries if e.get("kind") == _INTERACTIVE]
    if not interactive:
        return None
    found: dict | None = None
    decided = False
    pid = sess.claude_pid if isinstance(sess.claude_pid, int) else 0
    if pid > 0:
        own = [e for e in interactive if e["pid"] == pid and _pid_alive(pid)
               and _start_matches(e, pid)]
        decided, found = _unique(own)
    if not decided and pid <= 0:
        masters = set(_dtach_pids(sess.name))
        if masters:
            decided, found = _unique(
                [e for e in interactive if _descends(e["pid"], masters)])
    sid = sess.claude_session_id
    if not decided and valid_session_id(sid):
        # By id alone, only a live process counts: Claude Code leaves the
        # file of a killed claude behind (it checks the pid itself), and
        # after a bring-back the old client's stale entry is parked on the
        # very job the session's new id starts with.
        alive = [e for e in interactive
                 if _pid_alive(e["pid"]) and not _start_differs(e, e["pid"])]
        decided, found = _unique(
            [e for e in alive if e.get("parkedJobId") == sid[:8]])
        if not decided:
            decided, found = _unique(
                [e for e in alive if e.get("sessionId") == sid])
    if found is not None:
        if sess.claude_pid != found["pid"]:
            sess.claude_pid = found["pid"]
            sess.claude_pid_start = found.get("procStart") or 0
    return found


def job_entry(job: str, entries: list[dict] | None) -> dict | None:
    """The registry entry of background job *job* itself, or None."""
    for e in entries or ():
        if e.get("kind") != _INTERACTIVE and e.get("jobId") == job:
            return e
    return None


# -- transcripts --------------------------------------------------------------

#: path -> (size, mtime_ns, continued-in target or "")
_TAIL_CACHE: dict[str, tuple[int, int, str]] = {}
_TAIL_CACHE_MAX = 256


def _last_complete_line(path: str) -> bytes | None:
    """The last newline-terminated, non-blank line within the last
    :data:`TAIL_READ_BYTES` of *path*; None when there is none. A first
    line cut by the seek never counts."""
    with _open_binary(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - TAIL_READ_BYTES)
        f.seek(start)
        data = f.read(TAIL_READ_BYTES)
    end = data.rfind(b"\n")
    if end == -1:
        return None
    body = data[:end]
    while True:
        cut = body.rfind(b"\n")
        line = body[cut + 1:]
        if line.strip():
            if cut == -1 and start > 0:
                return None  # the seek cut this line: not a whole record
            return line
        if cut == -1:
            return None
        body = body[:cut]


def transcript_continued_in(path) -> str:
    """The conversation the transcript at *path* moved to: the
    ``continuedInSessionId`` of its LAST complete record when that record
    is ``{"type": "continued-in"}``; ``""`` otherwise (records after a
    ``continued-in`` mean the old copy was resumed on purpose). Reads at
    most :data:`TAIL_READ_BYTES`, cached by (size, mtime). Never raises."""
    if not path:
        return ""
    path = str(path)
    try:
        st = os.stat(path)
    except OSError:
        return ""
    cached = _TAIL_CACHE.get(path)
    if cached is not None and cached[:2] == (st.st_size, st.st_mtime_ns):
        return cached[2]
    target = ""
    try:
        line = _last_complete_line(path)
        record = json.loads(line) if line else None
        if (isinstance(record, dict) and record.get("type") == "continued-in"
                and valid_session_id(record.get("continuedInSessionId"))):
            target = record["continuedInSessionId"]
    except (OSError, ValueError, RecursionError):
        target = ""
    with _REGISTRY_LOCK:
        if len(_TAIL_CACHE) >= _TAIL_CACHE_MAX:
            _TAIL_CACHE.clear()
        _TAIL_CACHE[path] = (st.st_size, st.st_mtime_ns, target)
    return target


#: "<projects>\0<id>" -> (monotonic time of the search, path found or "")
_FIND_CACHE: dict[str, tuple[float, str]] = {}
_FIND_CACHE_MAX = 1024
#: How long a search that found nothing is believed, for a caller that
#: asks for it (the monitor's per-tick fallback).
FIND_MISS_TTL = 30.0


def find_transcript(session_id: str, *, cache_misses: bool = False) -> Path | None:
    """The transcript of conversation *session_id* under
    ``<config>/projects/*/``, or None. A path found before is checked
    again, never searched again; with *cache_misses* a search that found
    nothing is not repeated for :data:`FIND_MISS_TTL` seconds (it lists
    every project folder)."""
    if not valid_session_id(session_id):
        return None
    projects = Path(config_dir()) / "projects"
    key = f"{projects}\0{session_id}"
    cached = _FIND_CACHE.get(key)
    if cached is not None:
        at, found = cached
        if found and os.path.isfile(found):
            return Path(found)
        if not found and cache_misses and time.monotonic() - at < FIND_MISS_TTL:
            return None
    result: Path | None = None
    try:
        matches = _glob_transcripts(projects, session_id)
    except OSError:
        matches = []
    for match in matches:
        try:
            if match.is_file():
                result = match
                break
        except OSError:
            continue
    with _REGISTRY_LOCK:
        if len(_FIND_CACHE) >= _FIND_CACHE_MAX:
            _FIND_CACHE.clear()
        _FIND_CACHE[key] = (time.monotonic(), str(result) if result is not None else "")
    return result


def live_holder(session_id: str, entries: list[dict] | None = None) -> dict | None:
    """The background process holding conversation *session_id*: a
    registry entry with that ``sessionId`` that is not interactive, whose
    pid is alive and whose ``procStart`` matches the process's where both
    are known. None when there is none, or the registry cannot be read."""
    if not valid_session_id(session_id):
        return None
    if entries is None:
        entries = read_registry()
    for e in entries or ():
        if (e.get("sessionId") == session_id and e.get("kind") != _INTERACTIVE
                and _pid_alive(e["pid"]) and _start_matches(e, e["pid"])):
            return e
    return None


def follow_continued(session_id: str, entries: list[dict] | None = None) -> str:
    """Where conversation *session_id* went: each ``continued-in`` hop is
    taken while the target has a transcript or a live holder, at most
    :data:`MAX_HOPS`, never through a conversation seen already. The id
    itself when it did not move."""
    current = session_id
    seen = {session_id}
    for _ in range(MAX_HOPS):
        path = find_transcript(current)
        target = transcript_continued_in(path) if path is not None else ""
        if not target or target in seen:
            break
        if find_transcript(target) is None and live_holder(target, entries) is None:
            break
        log.info("conversation %s continued in %s", current, target)
        seen.add(target)
        current = target
    return current


# -- running claude -----------------------------------------------------------

@dataclass
class ClaudeRun:
    """What one ``claude`` command did. ``rc`` is None when it did not
    finish: ``error`` is then ``"timeout"`` or ``"no_claude"``."""

    rc: int | None
    out: str = ""
    err: str = ""
    error: str = ""


def _claude_command(args: list[str]) -> tuple[list[str], dict[str, str]]:
    from aipager import claude_resolve, daemon_secrets
    resolved = claude_resolve.try_resolve_claude_binary()
    binary = resolved.chosen.path if resolved else "claude"
    return [str(binary), *args], daemon_secrets.build_session_env()


async def run_claude(args: list[str], timeout: float) -> ClaudeRun:
    """Run ``claude <args>`` (the resolved binary, the sessions'
    environment) and wait at most *timeout* seconds. Never raises."""
    try:
        argv, env = _claude_command(args)
        proc = await _create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env)
    except (FileNotFoundError, PermissionError):
        return ClaudeRun(None, error="no_claude")
    except Exception:  # noqa: BLE001 - a runner never raises
        log.warning("claude %s could not start", args[:1], exc_info=True)
        return ClaudeRun(None, error="no_claude")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except Exception:  # noqa: BLE001
            log.debug("killing a timed-out claude failed", exc_info=True)
        return ClaudeRun(None, error="timeout")
    return ClaudeRun(proc.returncode, (out or b"").decode(errors="replace"),
                     (err or b"").decode(errors="replace"))


def run_claude_sync(args: list[str], timeout: float) -> ClaudeRun:
    """:func:`run_claude` for the command line (no event loop)."""
    try:
        argv, env = _claude_command(args)
        done = _subprocess_run(argv, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return ClaudeRun(None, error="timeout")
    except (FileNotFoundError, PermissionError):
        return ClaudeRun(None, error="no_claude")
    except Exception:  # noqa: BLE001 - a runner never raises
        log.warning("claude %s could not run", args[:1], exc_info=True)
        return ClaudeRun(None, error="no_claude")

    def text(raw) -> str:
        return raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw or "")

    return ClaudeRun(done.returncode, text(done.stdout), text(done.stderr))


# -- a job's state ------------------------------------------------------------

BUSY = "busy"
IDLE = "idle"
GONE = "gone"
UNKNOWN = "unknown"


def _state_of(status: str) -> str:
    return {"busy": BUSY, "idle": IDLE}.get(status, UNKNOWN)


def entry_state(entry: dict) -> str:
    """A job's state from its own registry entry."""
    return _state_of(entry.get("status") or "")


#: From the most permissive answer to the strictest.
_STRICTNESS = (GONE, IDLE, UNKNOWN, BUSY)


def stricter_state(a: str, b: str) -> str:
    """The stricter of two answers about one job (busy over unknown over
    idle over gone)."""
    rank = {s: i for i, s in enumerate(_STRICTNESS)}
    return a if rank.get(a, 2) >= rank.get(b, 2) else b


def _agents_state(job: str, run: ClaudeRun) -> str:
    """*job*'s state from ``claude agents --json``'s output: listed means
    its status, not listed means gone. Unknown when the run failed."""
    if run.rc != 0:
        return UNKNOWN
    try:
        listing = json.loads(run.out or "[]")
    except ValueError:
        return UNKNOWN
    if isinstance(listing, dict):
        listing = listing.get("agents") or listing.get("jobs") or []
    if not isinstance(listing, list):
        return UNKNOWN
    for item in listing:
        # By id only: once Claude's daemon runs, the listing also holds
        # the interactive sessions (kind "interactive", no "id"), so being
        # listed does not make an item a job (live check 1, finding 3).
        if isinstance(item, dict) and item.get("id") == job:
            status = item.get("status")
            return _state_of(status if isinstance(status, str) else "")
    return GONE


async def job_state(job: str, entries: list[dict] | None = None) -> str:
    """Whether background job *job* is :data:`BUSY`, :data:`IDLE` or
    :data:`GONE`; :data:`UNKNOWN` when neither the registry nor
    ``claude agents --json`` can tell. The job's own registry entry
    decides first (a parked client's ``status`` is never read: it is
    unreliable while parked); without one, the listing does. When the
    listing fails, no entry means gone only in a registry read whole
    (``Registry.complete``): a file left out may be the job's. Without
    *entries* the registry is read off the event loop."""
    if entries is None:
        entries = await read_registry_async()
    entry = job_entry(job, entries)
    if entry is not None:
        return _state_of(entry.get("status") or "")
    listed = _agents_state(job, await run_claude(["agents", "--json"], AGENTS_TIMEOUT))
    if listed == UNKNOWN and _read_whole(entries):
        return GONE  # the registry was read whole and has no entry for it
    return listed


def _read_whole(entries) -> bool:
    """Whether *entries* is a registry read completely (a plain list
    counts as one): only then does a missing entry mean a job is gone."""
    return entries is not None and getattr(entries, "complete", True) is True


def job_state_sync(job: str, entries: list[dict] | None = None) -> str:
    """:func:`job_state` for the command line (no event loop)."""
    if entries is None:
        entries = read_registry()
    entry = job_entry(job, entries)
    if entry is not None:
        return _state_of(entry.get("status") or "")
    listed = _agents_state(job, run_claude_sync(["agents", "--json"], AGENTS_TIMEOUT))
    if listed == UNKNOWN and _read_whole(entries):
        return GONE
    return listed


# -- resuming from the command line (R3) --------------------------------------

#: How often a stopped job's process is looked for again.
HOLDER_POLL = 0.25
#: The command line's wait between two looks (a test makes it instant).
_sync_sleep = time.sleep


def stop_error(run: ClaudeRun) -> tuple[str, dict]:
    """Why ``claude stop`` failed, as ``(code, params)`` (an error code
    the callers turn into words), or ``("", {})`` when it worked."""
    if run.error == "timeout":
        return "stop_timeout", {}
    if run.error:
        return "no_claude", {}
    if run.rc != 0:
        return "stop_exit", {"code": run.rc}
    return "", {}


@dataclass
class ResumePlan:
    """What :func:`prepare_resume_sync` found and did for one resume.

    ``session_id`` is the conversation to resume; ``moved`` is True when
    it is not the one asked for (``continued-in`` was followed).
    ``action`` is ``""`` (nothing held it), ``"stopped"`` (an idle
    background job held it and was stopped), ``"busy"`` (a busy job holds
    it: nothing was done) or ``"stop_failed"`` (``error``/``params`` say
    why). ``job`` names the holding job."""

    session_id: str
    moved: bool = False
    action: str = ""
    job: str = ""
    error: str = ""
    params: dict | None = None


def prepare_resume_sync(session_id: str) -> ResumePlan:
    """Before the command line resumes conversation *session_id*: follow
    ``continued-in`` (R1), then look for a live background holder of the
    conversation (R2). An idle one is stopped (``claude stop <job>``) and
    waited for, a busy one is left alone. Unreadable Claude files change
    nothing: the id is resumed as asked. Never raises."""
    try:
        return _prepare_resume_sync(session_id)
    except Exception:  # noqa: BLE001 - the command line resumes as before
        log.warning("resume preparation failed", exc_info=True)
        return ResumePlan(session_id)


def _prepare_resume_sync(session_id: str) -> ResumePlan:
    if not valid_session_id(session_id):
        return ResumePlan(session_id)
    entries = read_registry()
    target = follow_continued(session_id, entries)
    plan = ResumePlan(target, moved=target != session_id)
    if entries is None:
        return plan
    holder = live_holder(target, entries)
    if holder is None:
        return plan
    job = holder.get("jobId") or ""
    if not valid_job(job):
        plan.action, plan.error, plan.params = "stop_failed", "still_running", {}
        return plan
    plan.job = job
    state = job_state_sync(job, entries)
    if state in (BUSY, UNKNOWN):
        plan.action = "busy"
        return plan
    if state != GONE:
        code, params = stop_error(run_claude_sync(["stop", job], STOP_TIMEOUT))
        if code:
            plan.action, plan.error, plan.params = "stop_failed", code, params
            return plan
    for _ in range(int(HOLDER_WAIT / HOLDER_POLL)):
        if live_holder(target) is None:
            break
        _sync_sleep(HOLDER_POLL)
    else:
        plan.action, plan.error, plan.params = "stop_failed", "still_running", {}
        return plan
    plan.action = "stopped"
    log.info("stopped background job %s, which held conversation %s", job, target)
    return plan


# -- the exit record ------------------------------------------------------------

def read_exit(session: str, now: float) -> dict | None:
    """The exit record of *session*'s launch shell, ``{"code", "start",
    "end"}`` (ints; wall-clock seconds), when it is ours (owner-checked, a
    symlink ignored), well formed, and fresh: its ``end`` is within
    :data:`EXIT_FRESH_SECONDS` of *now* (``time.time()``). A launch
    removes the previous record, so a fresh one belongs to this launch.
    None otherwise: no record reads as today's "crashed or killed"."""
    data = bg_jobs.read_own_json(bg_jobs.exit_path(session))
    if data is None:
        return None
    values = [data.get(k) for k in ("code", "start", "end")]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return None
    code, start, end = values
    if not 0 <= code <= 255 or end < start:
        return None
    if abs(now - end) > EXIT_FRESH_SECONDS:
        return None
    return {"code": code, "start": start, "end": end}


def exit_signal(code: int) -> str:
    """The signal's name (``SIGKILL``) when a shell's exit status *code*
    is 128+N for a known signal N; "" otherwise."""
    if not isinstance(code, int) or code <= 128:
        return ""
    try:
        return signal.Signals(code - 128).name
    except ValueError:
        return ""


def is_start_failure(record: dict | None) -> bool:
    """Whether *record* says claude stopped right after starting: a
    non-zero code that is not a signal, within
    :data:`START_WINDOW_SECONDS` of the start."""
    if not record:
        return False
    code = record.get("code")
    return (bool(code) and not exit_signal(code)
            and record["end"] - record["start"] <= START_WINDOW_SECONDS)
