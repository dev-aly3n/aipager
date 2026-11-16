"""The Bash command check with a time limit.

:func:`aipager.safety.bash_violation` reads a command with regular
expressions, and a regex cannot be stopped from inside the process once it
runs: a crafted command a few KB long can hold some rules for minutes. The
hook would then hang the session until Claude Code's own hook timeout ends
it, and a timed-out hook is a non-blocking error, so the command would run
unchecked.

A command longer than :data:`FORK_ABOVE` characters is therefore checked in
a forked child. The parent waits at most :data:`BUDGET_SECONDS`; past that
it kills the child and raises :class:`CheckTimedOut`. A child that dies or
answers anything but a verdict raises :class:`CheckFailed`. enforce reads
both like any other failed check: the call is denied (fail closed).

The child also ends itself :data:`CHILD_LIFE_SECONDS` after it starts,
whatever happens to the hook: a hook killed while it waits (an Escape in
Claude Code) would otherwise leave it running the check. It holds none of
the hook's standard streams.

Shorter commands are checked in this process: every rule is fast on them
(the worst known shape takes a few ms at 1 KB, under 0.1 s on a loaded
machine) and a fork costs about 3 ms. Where fork is not available the
check runs here, as it always did.

The hook runs no threads, so forking it is safe (a child of a threaded
process could inherit a lock held by another thread).

This module is in the hook's import closure: stdlib only, and never
:mod:`aipager.i18n`.
"""
from __future__ import annotations

import json
import os
import select
import signal
import time

from aipager import safety

#: Commands longer than this many characters are checked in a child.
FORK_ABOVE = 1024
#: How long the child may take. An ordinary 1 MB command takes about 0.4 s
#: here, so this leaves room for a loaded or slower machine.
BUDGET_SECONDS = 3.0
#: When the child ends itself (a second past the budget), even if the
#: parent is gone: the default SIGALRM action stops it inside a regex too.
CHILD_LIFE_SECONDS = BUDGET_SECONDS + 1.0

#: The fork call (a seam for tests); None where there is no fork.
_fork = getattr(os, "fork", None)
#: How the parent waits for the child's answer (a seam for tests).
_wait = select.select
_READ_CHUNK = 65536


class CheckTimedOut(Exception):
    """The check did not finish within its time limit."""


class CheckFailed(Exception):
    """The child gave no verdict (it died, or wrote something else)."""


def bash_violation(command: str, patterns: tuple[str, ...]) -> str | None:
    """:func:`aipager.safety.bash_violation`, within :data:`BUDGET_SECONDS`
    for a long command. Raises :class:`CheckTimedOut` or
    :class:`CheckFailed` instead of answering when it cannot (so does a
    *command* that is not text, which enforce then denies)."""
    if len(command) <= FORK_ABOVE or _fork is None:
        return safety.bash_violation(command, patterns)
    read_end, write_end = os.pipe()
    try:
        pid = _fork()
    except OSError:
        os.close(read_end)
        os.close(write_end)
        return safety.bash_violation(command, patterns)
    if pid == 0:
        _child(read_end, write_end, command, patterns)
    os.close(write_end)
    return _verdict(_collect(read_end, pid))


def _child(read_end: int, write_end: int, command: str,
           patterns: tuple[str, ...]) -> None:
    """Check the command, write the verdict, and exit without running any
    of the parent's exit handlers or flushing its buffers."""
    code = 1
    try:
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.setitimer(signal.ITIMER_REAL, CHILD_LIFE_SECONDS)
        os.close(read_end)
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        reason = safety.bash_violation(command, patterns)
        data = memoryview(json.dumps({"reason": reason}).encode())
        while data:
            data = data[os.write(write_end, data):]
        code = 0
    except BaseException:
        pass
    finally:
        os._exit(code)


def _collect(read_end: int, pid: int) -> bytes:
    """Everything the child wrote, read until it closes its end; raise
    :class:`CheckTimedOut` past the deadline. A child that has not
    finished (the deadline passed, or the wait itself raised) is killed,
    and the child is always reaped."""
    deadline = time.monotonic() + BUDGET_SECONDS
    chunks: list[bytes] = []
    done = False
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = _wait([read_end], [], [], left)
            if not ready:
                continue
            chunk = os.read(read_end, _READ_CHUNK)
            if not chunk:
                done = True
                break
            chunks.append(chunk)
    finally:
        os.close(read_end)
        if not done:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass  # already reaped (SIGCHLD ignored): the output decides
    if not done:
        raise CheckTimedOut
    return b"".join(chunks)


def _verdict(raw: bytes) -> str | None:
    """The child's verdict, or :class:`CheckFailed`."""
    try:
        answer = json.loads(raw)
    except ValueError:
        raise CheckFailed from None
    if not isinstance(answer, dict) or "reason" not in answer:
        raise CheckFailed
    reason = answer["reason"]
    if reason is not None and not isinstance(reason, str):
        raise CheckFailed
    return reason
