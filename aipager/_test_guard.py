"""Refuse writes under the operator's real home while pytest runs.

Roadmap 8.97: a probe placed outside ``tests/`` (so none of
``tests/conftest.py``'s redirects applied) ran the real ``aipager setup``
and overwrote the operator's ``~/.config/aipager/aipager.yaml`` and
``~/.claude/settings.json``. The conftest redirects are a list of known
constants and only apply inside ``tests/``; this check sits in the
writers themselves, so it holds wherever the code runs under pytest.

Every function that writes (or deletes) a file under the operator's home
calls :func:`check_write` with the target first. Under pytest (``pytest``
imported, or ``PYTEST_CURRENT_TEST`` set, which a test's subprocesses
inherit) a target whose resolved path is inside the REAL home of the OS
user raises :class:`RealHomeWriteError` (a ``RuntimeError``), unless it
is inside an allowed test root (pytest's base temp dir, the system temp
dir, or one registered with :func:`allow_root`). The real home comes
from the password database, not ``$HOME`` or ``Path.home()``, which
tests redirect. Each refusal is also recorded in :data:`refusals`, so
the conftest can fail a test whose refusal a broad ``except`` swallowed.
A refusal inside a subprocess of a test is refused all the same, but it
is recorded in that process only, never in the test's.
Outside pytest the check is one dict lookup and one environment lookup.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading

# HOME as it was when this module was first imported: the fallback when
# the password database has no entry for this uid (some containers).
_HOME_AT_IMPORT = os.environ.get("HOME", "")

_allowed_roots: list[str] = []

# Every refusal, in order. A writer inside a broad ``except`` (the daemon
# logs and carries on after a failed save) would hide one, so the
# conftest fails any test that leaves a refusal here it did not expect.
refusals: list[str] = []

# What aipager and Claude Code keep under the home: an allowed root that
# holds one of these (``TMPDIR=~/.config``, say) does not count.
_PROTECTED = (
    ".config/aipager", ".config/systemd", ".claude", ".claude.json",
    ".local/share/aipager", ".local/state/aipager",
    "Library/LaunchAgents", "Library/Logs",
)


class RealHomeWriteError(RuntimeError):
    """A write under the operator's real home was refused under pytest."""


def _pytest_imported() -> bool:
    return "pytest" in sys.modules


def under_pytest() -> bool:
    return _pytest_imported() or "PYTEST_CURRENT_TEST" in os.environ


def _real_home() -> str:
    """The OS user's home from the password database (``$HOME`` only
    when there is no entry). Tests replace this to point the guard at a
    fake home; nothing may aim it at the real one."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError):
        return _HOME_AT_IMPORT


def allow_root(path) -> None:
    """Allow writes inside *path* even when it lies under the real home
    (a pytest ``--basetemp`` there, say). Registered by the conftest."""
    resolved = os.path.realpath(os.fspath(path))
    if resolved not in _allowed_roots:
        _allowed_roots.append(resolved)


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


class LiveSocketSendError(RuntimeError):
    """A datagram to the live daemon's control socket was refused under
    pytest."""


def _live_control_sockets() -> set[str]:
    """Where the operator's own daemon listens (``instance``'s defaults,
    from the uid rather than ``$XDG_RUNTIME_DIR``, which tests redirect).
    A daemon on ``AIPAGER_SOCKET_PATH`` or in an ``AIPAGER_INSTANCE_DIR``
    is not covered here; the conftest redirect of every sender is."""
    return {"/tmp/aipager.sock", f"/run/user/{os.getuid()}/aipager.sock"}


def check_send(path) -> None:
    """Raise :class:`LiveSocketSendError` when running under pytest and
    *path* is the live daemon's control socket (roadmap 8.112: a test's
    made-up crash must never reach the operator's report store). A no-op
    outside pytest."""
    if not under_pytest():
        return
    target = os.path.realpath(os.fspath(path))
    if target not in {os.path.realpath(p) for p in _live_control_sockets()}:
        return
    message = (f"refusing to send to the live daemon socket {target} while pytest "
               "is running (roadmap 8.112): point the sender at a tmp path")
    refusals.append(
        f"{message} [test: {os.environ.get('PYTEST_CURRENT_TEST', '?')}; "
        f"thread: {threading.current_thread().name}]")
    raise LiveSocketSendError(message)


class LiveNetworkError(RuntimeError):
    """A real network request was refused under pytest."""


def check_network(what: str) -> None:
    """Raise :class:`LiveNetworkError` when running under pytest: a
    problem report (roadmap 8.112) sent by a test would reach the
    maintainer's Sentry and spend the month's quota, and the key file
    fetch would make the suite depend on GitHub. The sender takes an
    ``httpx`` transport; a test injects one (``httpx.MockTransport``)
    and only an un-injected client reaches this. A no-op outside
    pytest."""
    if not under_pytest():
        return
    message = (f"refusing real network access ({what}) while pytest is running "
               "(roadmap 8.112): inject an httpx transport")
    refusals.append(
        f"{message} [test: {os.environ.get('PYTEST_CURRENT_TEST', '?')}; "
        f"thread: {threading.current_thread().name}]")
    raise LiveNetworkError(message)


def check_write(path) -> None:
    """Raise :class:`RealHomeWriteError` (a :class:`RuntimeError`) when
    running under pytest and *path* resolves inside the operator's real
    home outside every allowed test root. A no-op outside pytest."""
    if not under_pytest():
        return
    home = _real_home()
    if not home:
        return
    home = os.path.realpath(home)
    if home == os.sep:
        return
    target = os.path.realpath(os.fspath(path))
    if not _inside(target, home):
        return
    for root in (tempfile.gettempdir(), *_allowed_roots):
        root = os.path.realpath(root)
        # A root that holds a folder the operator's config lives in (the
        # home itself with TMPDIR=$HOME, or TMPDIR=~/.config) does not
        # count.
        if any(_inside(os.path.join(home, p), root) for p in _PROTECTED):
            continue
        if _inside(target, root):
            return
    message = (
        f"refusing to write {target} under the real home {home} while "
        "pytest is running (roadmap 8.97): point this writer at a tmp "
        "path (tests/conftest.py redirects the known ones)"
    )
    # Who refused, so a refusal from a thread that outlived its test can
    # be traced to the code that made it.
    refusals.append(
        f"{message} [test: {os.environ.get('PYTEST_CURRENT_TEST', '?')}; "
        f"thread: {threading.current_thread().name}]")
    raise RealHomeWriteError(message)
