"""Bounded, read-only ``git`` orchestration for the diff-viewer route.

Security-critical (design.md Decision 2, non-negotiable): every call in
this module takes a ``cwd`` that the caller must have already resolved
from server-side state (``TrackedSession.cwd``, stamped from the
``SessionStart`` hook payload — ``dtach/hook_receiver.py:269-271``).
Nothing here accepts a client-supplied path, and nothing here mutates
the repository — ``git status``/``git diff`` (including ``--no-index``,
which is a pure filesystem comparison, unlike ``git add -N``) are the
only commands run.

Every ``git`` invocation goes through :func:`_run_git`, which uses
``asyncio.create_subprocess_exec`` (never ``run_in_executor`` — see
design.md's "Subprocess choice" note: a hung child must be killable
without occupying a thread-pool slot) and reads stdout in a bounded
64 KB-chunk loop under ``asyncio.wait_for`` (never ``communicate()``,
which buffers the whole output before the caller can bound anything).
``requires-python = ">=3.10"`` rules out ``asyncio.timeout()`` (3.11+).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 5.0
MAX_DIFF_BYTES_PER_FILE = 200_000  # ~200 KB, per file
MAX_TOTAL_DIFF_BYTES = 1_500_000  # ~1.5 MB, whole response
MAX_FILES = 100  # changed-file cap per request

# git status --porcelain=v1 XY codes that need the rename/copy source
# token consumed (but not used — see _parse_porcelain_z).
_RENAME_OR_COPY = frozenset("RC")

_STDERR_DRAIN_CAP = 65_536  # never surfaced to the client; just prevents
# the child blocking on a full stderr pipe while we only read stdout.

# Bounds how long a post-kill() reap is awaited (see _run_git) — belt
# and braces on top of GIT_TIMEOUT_SECONDS so a child that's slow to
# actually die never becomes a second, unbounded wait.
_KILL_REAP_GRACE_SECONDS = 2.0


@dataclass
class _GitResult:
    returncode: int | None
    stdout: bytes
    not_found: bool  # git missing from PATH
    timed_out: bool
    truncated: bool  # stdout hit max_bytes before the child exited


async def _run_git(
    args: list[str], cwd: str, *,
    max_bytes: int | None = None,
    timeout: float | None = None,
) -> _GitResult:
    """Run ``git <args>`` with ``cwd=cwd`` (never ``-C``, so paths a
    prior ``git status`` reported are reusable unmodified in a
    following ``git diff`` call — design.md Decision 2).

    Reads stdout in a bounded loop so a huge or hung diff can never
    grow this process's memory or block the event loop past
    ``timeout``. Draining stderr concurrently prevents the child from
    deadlocking on a full stderr pipe; that text is never parsed or
    surfaced — every failure this function reports collapses to one of
    a small set of generic reason strings.

    ``max_bytes``/``timeout`` default to ``None``, resolved against the
    *current* module-level constants inside the function body rather
    than bound as literal defaults at import time — this is what lets
    a test shrink ``GIT_TIMEOUT_SECONDS`` via monkeypatch and actually
    observe a fast timeout instead of waiting out the real 5s ceiling.
    """
    if max_bytes is None:
        max_bytes = MAX_DIFF_BYTES_PER_FILE
    if timeout is None:
        timeout = GIT_TIMEOUT_SECONDS
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return _GitResult(None, b"", True, False, False)

    stdout = b""
    truncated = False

    async def _read_stdout() -> None:
        nonlocal stdout, truncated
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                return
            stdout += chunk
            if len(stdout) >= max_bytes:
                truncated = True
                return

    async def _drain_stderr() -> None:
        drained = 0
        while True:
            chunk = await proc.stderr.read(65536)
            if not chunk:
                return
            drained += len(chunk)
            if drained >= _STDERR_DRAIN_CAP:
                return

    # Race the two reads instead of gathering them: a plain
    # ``asyncio.gather`` under ``wait_for`` (the original approach) waits
    # for BOTH to finish, but ``_read_stdout`` returns the instant it hits
    # ``max_bytes`` while the child is still alive and blocked writing to
    # that now-unread stdout pipe -- so ``_drain_stderr`` never sees EOF
    # and the whole call sits until the full ``timeout`` elapses. Instead,
    # loop on ``FIRST_COMPLETED`` and stop the moment stdout truncates:
    # killing the child below immediately closes its stderr pipe too, so
    # there's nothing left worth waiting on.
    stdout_task: asyncio.Task[None] = asyncio.ensure_future(_read_stdout())
    stderr_task: asyncio.Task[None] = asyncio.ensure_future(_drain_stderr())
    pending: set[asyncio.Task[None]] = {stdout_task, stderr_task}

    timed_out = False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = True
                break
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                # asyncio.wait's own timeout elapsed without either task
                # completing.
                timed_out = True
                break
            if stdout_task in done and truncated:
                # A capped read is a successful, bounded result -- stop
                # waiting on stderr the instant it happens rather than
                # draining it to completion (which is exactly what
                # deadlocked before).
                break
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # Propagate any exception raised inside either reader task (mirrors
    # the original ``gather``-under-``wait_for`` behaviour for anything
    # other than the timeout it was catching).
    for task in (stdout_task, stderr_task):
        if task.done() and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                raise exc

    if truncated or timed_out:
        # Either way we've stopped reading before the child was done —
        # kill it rather than let it run to completion unobserved.
        # returncode may already be set if the child happened to exit
        # in the tiny window between the read loop finishing and here
        # (e.g. it produced exactly max_bytes and then exited on its
        # own) — killing an already-reaped process raises
        # ProcessLookupError.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _drain_to_eof(stream: asyncio.StreamReader | None) -> None:
        # asyncio.Process.wait() only resolves once every pipe transport
        # has reported EOF (BaseSubprocessTransport._try_finish gates the
        # exit-waiter on ALL pipes being "disconnected"). A pipe whose
        # internal buffer we filled past its high-water mark without
        # draining -- exactly what stopping at truncation leaves behind --
        # is *paused* by asyncio's own flow control and never resumes on
        # its own, so `proc.wait()` would otherwise hang indefinitely even
        # though the child is already dead and killed. Resuming reads
        # (discarding the bytes -- we've already taken what we need)
        # drains the remaining kernel buffer and lets the transport
        # observe EOF quickly, since the killed child has no more writers.
        if stream is None:
            return
        try:
            while not stream.at_eof():
                await stream.read(65536)
        except (ValueError, ConnectionResetError):
            pass

    try:
        # Bounded even here: draining+reaping a just-killed child is
        # normally near-instant, but this must never become the
        # unbounded wait this whole function exists to avoid if a wedged
        # child (e.g. blocked in uninterruptible disk I/O on a stalled
        # network filesystem) is slow to actually die and be reaped.
        await asyncio.wait_for(
            asyncio.gather(_drain_to_eof(proc.stdout), _drain_to_eof(proc.stderr)),
            timeout=_KILL_REAP_GRACE_SECONDS,
        )
        await asyncio.wait_for(proc.wait(), timeout=_KILL_REAP_GRACE_SECONDS)
    except asyncio.TimeoutError:
        log.warning("git subprocess (pid=%s) still not reaped after kill()", proc.pid)

    return _GitResult(proc.returncode, stdout[:max_bytes], False, timed_out, truncated)


def _change_type(xy: str) -> str:
    """Map a porcelain v1 ``XY`` code to the wire ``change_type``.

    Order matches design.md Decision 2 verbatim: ``??`` first, then
    ``D``/``A``/``R``/``C``/``U`` each checked independently (so e.g.
    ``AU`` reads as "added", not "conflicted") — this stage doesn't need
    finer conflict-state granularity than one bucket.
    """
    if xy == "??":
        return "untracked"
    if "D" in xy:
        return "deleted"
    if "A" in xy:
        return "added"
    if "R" in xy:
        return "renamed"
    if "C" in xy:
        return "copied"
    if "U" in xy:
        return "conflicted"
    return "modified"


def _parse_porcelain_z(data: bytes) -> list[tuple[str, str]]:
    """Parse ``git status --porcelain=v1 --untracked-files=all -z``
    output into ``[(xy, path), ...]``.

    ``-z`` NUL-separates records instead of quoting/joining paths with
    ``" -> "`` — a filename containing that literal substring can't
    corrupt parsing. For a rename/copy (``X`` or ``Y`` is ``R``/``C``),
    the record is two consecutive NUL-terminated tokens (new path, then
    old path); the old-path token is consumed here without being used —
    this stage's response schema has no rename-source field.
    """
    tokens = data.split(b"\0")
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        xy = tok[:2].decode("utf-8", "replace")
        path = tok[3:].decode("utf-8", "replace")
        if (set(xy) & _RENAME_OR_COPY) and i < len(tokens):
            i += 1  # rename/copy source path — consumed, not used
        entries.append((xy, path))
    return entries


def _is_binary_diff(patch: bytes) -> bool:
    """git's own diff engine detects binary content and emits a
    ``Binary files a/... and b/... differ`` line instead of raw bytes —
    no bespoke content sniffing needed. That line is the *last* line of
    the diff (after the usual ``diff --git``/mode/index header lines),
    not necessarily the first — checked as a standalone line, not a
    substring, so a text file whose actual added/removed content
    happens to contain that phrase (always prefixed with ``+``/``-``
    there) can't be misidentified as binary."""
    return any(
        line.startswith(b"Binary files ") and line.endswith(b" differ")
        for line in patch.split(b"\n")
    )


async def _diff_one_file(cwd: str, path: str, change_type: str, budget: int) -> dict[str, Any]:
    """Diff a single changed file, bounded to ``budget`` bytes (already
    the smaller of the per-file cap and whatever's left of the
    whole-response cap). Returns a file entry dict; never raises."""
    per_file_cap = min(MAX_DIFF_BYTES_PER_FILE, budget)
    if change_type == "untracked":
        # A pure filesystem comparison — does not touch the index or
        # any repo state (unlike `git add -N`, which would be a
        # forbidden mutation).
        args = ["diff", "--no-color", "--no-index", "--", "/dev/null", path]
    else:
        args = ["diff", "--no-color", "HEAD", "--", path]

    result = await _run_git(args, cwd, max_bytes=per_file_cap)

    if result.not_found or result.timed_out:
        # A genuine timeout (or a missing `git` binary) has no usable
        # content at all -- this is the only case with a null patch.
        return {
            "path": path, "change_type": change_type, "binary": False,
            "patch": None, "truncated": True,
        }

    if _is_binary_diff(result.stdout):
        # git's binary marker line is short and always emitted up front,
        # so this check is meaningful whether or not the read was capped.
        return {
            "path": path, "change_type": change_type, "binary": True,
            "patch": None, "truncated": result.truncated,
        }

    if not result.truncated:
        ok_codes = (0, 1)
        if result.returncode not in ok_codes:
            # Exit >1 (or --no-index's equivalent) is a per-file git
            # error — surfaced as an incomplete entry, never a 500 for
            # the whole request (design.md Decision 2). Skipped when
            # `truncated` is set: a capped read is a SUCCESS with a
            # bounded payload, and `_run_git` kills the child to enforce
            # the cap, which leaves an arbitrary (often negative/signal)
            # returncode that says nothing about whether the diff itself
            # would have succeeded — treating it as an error here would
            # turn every oversized file into a null patch again.
            return {
                "path": path, "change_type": change_type, "binary": False,
                "patch": None, "truncated": True,
            }

    patch = result.stdout.decode("utf-8", "replace") if result.stdout else ""
    return {
        "path": path, "change_type": change_type, "binary": False,
        "patch": patch, "truncated": result.truncated,
    }


async def collect_diff(cwd: str) -> dict[str, Any]:
    """Collect the working-tree diff for a session's ``cwd``.

    Returns ``{"available": True, "files": [...], "files_truncated":
    bool}`` or ``{"available": False, "reason": "<reason>"}`` — never
    raises, never a 500, never hangs past ``GIT_TIMEOUT_SECONDS`` per
    ``git`` call. Reasons: ``not_a_git_repo``, ``git_not_installed``,
    ``cwd_missing``, ``git_error``, ``no_commits_yet``.
    """
    if not cwd or not os.path.isdir(cwd):
        return {"available": False, "reason": "cwd_missing"}

    repo_check = await _run_git(
        ["rev-parse", "--is-inside-work-tree"], cwd, max_bytes=4096,
    )
    if repo_check.not_found:
        return {"available": False, "reason": "git_not_installed"}
    if repo_check.timed_out:
        return {"available": False, "reason": "git_error"}
    if repo_check.returncode != 0:
        return {"available": False, "reason": "not_a_git_repo"}

    head_check = await _run_git(["rev-parse", "--verify", "HEAD"], cwd, max_bytes=4096)
    if head_check.timed_out:
        return {"available": False, "reason": "git_error"}
    if head_check.returncode != 0:
        return {"available": False, "reason": "no_commits_yet"}

    status = await _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all", "-z"],
        cwd, max_bytes=MAX_TOTAL_DIFF_BYTES,
    )
    if status.not_found:
        return {"available": False, "reason": "git_not_installed"}
    if status.timed_out or status.returncode != 0:
        return {"available": False, "reason": "git_error"}

    entries = _parse_porcelain_z(status.stdout)
    files_truncated = len(entries) > MAX_FILES
    listed = entries[:MAX_FILES]

    files: list[dict[str, Any]] = []
    total_bytes = 0
    for xy, path in listed:
        change_type = _change_type(xy)
        if total_bytes >= MAX_TOTAL_DIFF_BYTES:
            files.append({
                "path": path, "change_type": change_type, "binary": False,
                "patch": None, "truncated": True,
            })
            files_truncated = True
            continue

        entry = await _diff_one_file(
            cwd, path, change_type, MAX_TOTAL_DIFF_BYTES - total_bytes,
        )
        if entry.get("patch"):
            total_bytes += len(entry["patch"].encode("utf-8"))
        if entry.get("truncated"):
            files_truncated = True
        files.append(entry)

    return {"available": True, "files": files, "files_truncated": files_truncated}


__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "MAX_DIFF_BYTES_PER_FILE",
    "MAX_FILES",
    "MAX_TOTAL_DIFF_BYTES",
    "collect_diff",
]
