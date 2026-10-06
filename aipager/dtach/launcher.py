"""Launch Claude Code inside a named dtach session.

The user-facing entry point is ``aipager session <name>`` (wired through
``aipager.cli``), which calls :func:`launch`. If the dtach socket
already exists and a process is listening on it this reattaches;
otherwise it spawns a new session and attaches.

Sets ``CLAUDE_DTACH_SESSION`` inside the spawned session so the aipager
hook scripts can identify which session sent which event.
"""

from __future__ import annotations

import os
import re
import shutil
import socket as _socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from aipager import daemon_secrets, instance
from aipager.dtach import redraw as _dtach_redraw
from aipager.dtach.inject import _RESERVED, normalize_session_name
from aipager.errors import friendly_error, friendly_warn
from aipager.ui import console, ok

_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,50}")
# Reserved because they are subcommand verbs under `aipager session`.
# The positional verbs `aipager session` matches before treating its
# argument as a session name (see cli/session.py). PURELY COSMETIC:
# it selects the wording of the rejection and carries no validation
# authority of its own — names are checked against inject._RESERVED,
# which contains these three as well. Do not re-widen this into a
# second reserved list; that split is the bug this file just fixed.
_SUBCOMMAND_VERBS = frozenset({"ls", "list", "kill"})


def _resolve_dtach() -> str | None:
    """Return absolute path to the dtach binary, or None if unavailable."""
    try:
        from dtach_bin import path
        return path()
    except (ImportError, FileNotFoundError):
        pass
    return shutil.which("dtach")


def _dtach_works(dtach_path: str) -> tuple[bool, str]:
    """Probe the dtach binary by running ``dtach`` with no args.

    dtach exits 1 and prints "dtach - ..." usage on stderr when called
    with no arguments. The point is to confirm the binary is loadable
    (right arch, libc available) — we don't care about its return code.
    """
    try:
        r = subprocess.run([dtach_path], capture_output=True, text=True, timeout=2)
    except FileNotFoundError:
        return False, "binary missing"
    except OSError as e:
        return False, str(e)
    except subprocess.TimeoutExpired:
        return False, "probe hung"
    blob = (r.stdout + r.stderr).lower()
    if "dtach" in blob:
        return True, ""
    return False, blob.strip().splitlines()[0][:120] if blob else "no output"


def _socket_alive(sock: str) -> bool:
    """Return True iff *something* is currently listening on the dtach socket.

    dtach uses AF_UNIX SOCK_STREAM, so we probe via connect(). A stale
    socket left by a dead process raises ConnectionRefusedError.
    """
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(sock)
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False
    finally:
        s.close()


def _set_title(name: str) -> None:
    sys.stderr.write(f"\033]0;{name}\007")
    sys.stderr.flush()


def _keep_title(name: str, stop: threading.Event) -> None:
    """Re-emit terminal title every 3s — Claude Code's TUI overrides it."""
    while not stop.is_set():
        _set_title(name)
        if stop.wait(3.0):
            break


def _force_redraw(name: str) -> None:
    """Bounce PTY size 0.8s after attach to force Ink to redraw."""
    time.sleep(0.8)
    _dtach_redraw.redraw(name)


# Following a relaunch. Telegram's /mode, /restart and Restart kill the
# dtach master and start a new one on the SAME socket about a second later
# (`session_ops._kill_and_relaunch_core`). The attach client ends with the
# old master (`inject.kill_session` SIGTERMs it alongside the master, and it
# exits on the master's EOF otherwise), so without this the terminal fell
# back to a shell while the session ran on unseen. How long to wait for the
# socket to come back: the relaunch waits for the old process to exit,
# polls up to 3 s for its socket to go, then spawns, so a few seconds is
# normal and 15 s is generous. (Wall time can run longer if the probes'
# connects stall, at worst 60 polls x (0.25 s + 0.5 s connect timeout), about
# 45 s, but the poll count keeps it bounded.)
_FOLLOW_WAIT_S = 15.0
_FOLLOW_POLL_S = 0.25
# `kill_session` signals the attach client and the master in the same loop,
# so the client can be gone while the master is still dying and its socket
# still answers. Probe this many more times (x _FOLLOW_POLL_S) before
# deciding the master survived the attach. An alive probe alone cannot tell
# the old master from the relaunched one (the dead gap between them is only
# about 0.2-0.3 s, easily stepped over), so every probe also checks that the
# socket file is the very one the attach began on (`_sock_identity`).
_DETACH_GRACE_PROBES = 4
# A session that keeps coming back and dying (a crash loop under some
# supervisor) must not keep the terminal reattaching forever.
_FOLLOW_MAX_REATTACHES = 5
_FOLLOW_WINDOW_S = 60.0


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _clock() -> float:
    return time.monotonic()


def _stat(path: str) -> os.stat_result:
    return os.stat(path)


def _sock_identity(sock: str) -> tuple[int, int, int] | None:
    """Which socket FILE is at *sock*: a relaunch unlinks the old one and
    the new master binds a new one. None if nothing is there.

    Device, inode and MODIFICATION time. The inode alone is not enough:
    ext4 hands a just-freed inode number straight back to the next bind.
    The change time is wrong: the dtach master chmods its socket (u+x
    while a client is attached, u-x when none is), so ctime moves on every
    attach and detach of the same master. mtime is set when the socket is
    bound and left alone by chmod, connects and `dtach -p` pushes.
    """
    try:
        st = _stat(sock)
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_mtime_ns)


def _master_survived(sock: str, ident: tuple[int, int, int] | None) -> bool:
    """True if the master the attach began on is still running.

    *ident* is the socket's identity taken just before the attach. Each
    probe needs the socket alive AND still that same file: a dead socket
    or a different file means the session ended or was relaunched. The
    grace exists because during a hard-kill relaunch the client is
    SIGTERMed next to the master and can exit first, while the old socket
    still answers. Ctrl-C during the grace means "back to the shell".
    """
    def same_master() -> bool:
        # No identity to compare (the stat failed): alive has to do.
        return _socket_alive(sock) and (
            ident is None or _sock_identity(sock) == ident)

    if not same_master():
        return False
    try:
        for _ in range(_DETACH_GRACE_PROBES):
            _sleep(_FOLLOW_POLL_S)
            if not same_master():
                return False
    except KeyboardInterrupt:
        return True
    return True


def _wait_for_return(sock: str, name: str) -> bool:
    """Wait, bounded, for *sock* to be alive again after its master died.

    True as soon as something listens on it again; False once the bound
    runs out or the user presses Ctrl-C. The bound is a poll COUNT, not a
    clock deadline, so it stays bounded even when sleep is a no-op.
    """
    label = (f"[muted]{name} ended; waiting for it to come back "
             f"(Ctrl-C to stop)[/muted]")
    status = console.status(label, spinner="dots") if console.is_terminal else None
    if status:
        status.__enter__()
    else:
        console.print(f"  {label}")
    try:
        for _ in range(int(_FOLLOW_WAIT_S / _FOLLOW_POLL_S)):
            _sleep(_FOLLOW_POLL_S)
            if _socket_alive(sock):
                return True
        return False
    except KeyboardInterrupt:
        return False
    finally:
        if status:
            status.__exit__(None, None, None)


def _attach_following(dtach: str, sock: str, name: str,
                      *, redraw: bool) -> float:
    """Attach to *sock* and stay attached across relaunches.

    Returns how long the LAST attach lasted, for the caller's
    "exited immediately" check. The one attach loop both branches of
    :func:`launch` use.

    After ``dtach -a`` returns, the socket tells the two endings apart: if
    only the attach ended (the client was signalled) the master is still
    running and this returns within a second; a dead master means the
    session ended or is being relaunched, so wait a bounded time for it
    to come back and reattach.
    """
    stop = threading.Event()
    _set_title(name)
    threading.Thread(target=_keep_title, args=(name, stop), daemon=True).start()
    reattached_at: list[float] = []
    try:
        while True:
            if redraw:
                threading.Thread(target=_force_redraw, args=(name,),
                                 daemon=True).start()
            ident = _sock_identity(sock)
            started = _clock()
            subprocess.run([dtach, "-a", sock, "-r", "winch", "-E"], check=False)
            elapsed = _clock() - started
            if _master_survived(sock, ident):
                return elapsed
            now = _clock()
            reattached_at = [t for t in reattached_at
                             if now - t < _FOLLOW_WINDOW_S]
            if len(reattached_at) >= _FOLLOW_MAX_REATTACHES:
                console.print(
                    f"  [muted]({name} restarted {len(reattached_at)} times "
                    f"in {_FOLLOW_WINDOW_S:.0f}s; no longer following it)"
                    "[/muted]")
                return elapsed
            if not _wait_for_return(sock, name):
                return elapsed
            reattached_at.append(_clock())
            console.print(f"[step]→[/step] [muted]{name} restarted, "
                          "reattaching[/muted]")
            _set_title(name)
            # A relaunched claude has drawn its screen with nobody attached.
            redraw = True
    finally:
        stop.set()


def _validate_name(name: str) -> str | None:
    """Return None if the name is valid, else an error string."""
    if not name:
        return "session name cannot be empty"
    if not _NAME_RE.fullmatch(name):
        return ("session name must be 1-50 chars of [A-Za-z0-9_-]; "
                f"got {name!r}")
    # The canonical set from `inject`, not a second list of our own: the
    # two used to disagree, so a name this layer accepted could shadow a
    # Telegram command, and vice versa.
    if name in _RESERVED:
        if name in _SUBCOMMAND_VERBS:
            return (f"{name!r} is reserved as an `aipager session` subcommand "
                    "(ls / list / kill); pick a different name")
        return (f"{name!r} is reserved as an aipager command name - a session "
                f"called that would shadow /{name} in Telegram")
    return None


def _resolve_launch_name(name: str) -> str:
    """The name this invocation should actually use.

    ``aipager session <name>`` both CREATES and REATTACHES, so it cannot
    simply normalise: sessions made before that rule keep their original
    spelling, and normalising past a live ``HjIo`` would strand it and
    start a second session called ``hjio`` alongside. So an exact-name
    match on a LIVE socket wins; everything else — a new session, or a
    dead socket about to be cleaned up — gets the canonical spelling.
    """
    raw = name.strip()
    sock = f"{instance.dtach_sock_prefix()}{raw}.sock"
    if Path(sock).exists():
        if _socket_alive(sock):
            return raw
        # Dead socket under the old spelling. Say so: we are about to
        # create a session under a DIFFERENT name, and silently leaving
        # `HjIo.sock` behind while starting `hjio` is the kind of thing
        # someone finds months later and cannot explain.
        canonical = normalize_session_name(raw)
        if canonical != raw:
            console.print(
                f"  [muted](session {raw!r} is no longer running; "
                f"starting {canonical!r} - its stale socket remains at "
                f"{sock})[/muted]")
        return canonical
    return normalize_session_name(raw)


def launch(name: str, claude_args: list[str] | None = None,
          *, claude_bin: str | None = None) -> int:
    """Create or reattach a Claude Code session inside dtach.

    All extra args in ``claude_args`` are passed through to claude
    verbatim. To start with permission checks bypassed, pass
    ``--dangerously-skip-permissions`` like you would to claude itself.

    ``claude_bin`` is the resolved absolute path the caller wants used
    (``cli/session.py`` passes ``require_claude()``'s return value).
    Defaults to the literal ``"claude"`` — today's behaviour — when not
    given, so this function stays usable on its own.
    """
    # Validate the RAW name first: _resolve_launch_name stats a path built
    # from it, and Path.exists() does NOT swallow ENAMETOOLONG — a 300-char
    # argument raised OSError out of the CLI as an "unexpected error" bug
    # prompt instead of the clean "1-50 chars" message. Normalisation can
    # never turn an invalid name valid (it only lowercases and maps `-` to
    # `_`, both already inside _NAME_RE, and leaves length untouched), so
    # checking before costs nothing.
    err = _validate_name(name.strip())
    if err:
        friendly_error(err)
        return 2

    name = _resolve_launch_name(name)
    err = _validate_name(name)
    if err:
        # Reachable: `inject._RESERVED` holds lowercase literals, so `LS`
        # passes the check above and only becomes reserved once
        # normalised. Not dead code — do not delete.
        friendly_error(err)
        return 2

    claude_args = list(claude_args) if claude_args else []
    session = f"claude-{name}"
    sock = f"{instance.dtach_sock_prefix()}{name}.sock"

    dtach = _resolve_dtach()
    if not dtach:
        friendly_error(
            "dtach not installed.",
            "",
            "  aipager bundles dtach via the dtach-bin package. Try:",
            "      uv tool install --reinstall aipager",
            "",
            "  Or install system-wide:",
            "      Debian/Ubuntu:  sudo apt install dtach",
            "      macOS:          brew install dtach",
        )
        return 1

    dtach_ok, why = _dtach_works(dtach)
    if not dtach_ok:
        friendly_error(
            f"dtach binary at {dtach} fails to run.",
            f"  Detail: {why}",
            "",
            "  Probably an architecture / libc mismatch. Reinstall aipager:",
            "      uv tool install --reinstall aipager",
        )
        return 1

    sys_prompt = (
        f'Your session name is "{name}". '
        f'When users address you by this name, respond naturally '
        f'-- it is your name in this session.'
    )

    sock_path = Path(sock)

    # Reattach branch — only if the socket is *alive*.
    if sock_path.exists() and _socket_alive(sock):
        console.print(f"[step]→[/step] reattaching to [path]{session}[/path]")
        elapsed = _attach_following(dtach, sock, name, redraw=True)
        if elapsed < 1.0 and not sock_path.exists():
            friendly_warn(
                f"session '{session}' exited immediately ({elapsed:.1f}s).",
                "  The claude process inside dtach may have crashed.",
                "  Try `aipager session <name>` again, or run `claude --version`.",
            )
        return 0

    # Stale-socket cleanup: a socket file exists but nothing's listening.
    if sock_path.exists():
        try:
            sock_path.unlink()
            console.print(f"  [muted](cleaned up stale socket {sock})[/muted]")
        except OSError as e:
            friendly_warn(f"stale socket {sock} could not be removed: {e}")

    console.print(f"[step]→[/step] starting [path]{session}[/path]")
    if console.is_terminal:
        spawn_status = console.status(
            "[muted]spawning dtach + claude…[/muted]", spinner="dots"
        )
    else:
        spawn_status = None
    if spawn_status:
        spawn_status.__enter__()
    try:
        spawn = subprocess.run(
            [dtach, "-n", sock, "-Ez",
             "env", f"CLAUDE_DTACH_SESSION={session}",
             claude_bin or "claude",
             "--append-system-prompt", sys_prompt,
             *claude_args],
            capture_output=True, text=True, check=False,
            env=daemon_secrets.build_session_env(),
        )
    finally:
        if spawn_status:
            spawn_status.__exit__(None, None, None)
    if spawn.returncode != 0:
        friendly_error(
            f"dtach failed to start session (exit {spawn.returncode}).",
            *( [f"  stderr: {spawn.stderr.rstrip()}"] if spawn.stderr.strip() else [] ),
            *( [f"  stdout: {spawn.stdout.rstrip()}"] if spawn.stdout.strip() else [] ),
            "",
            "  Try running `claude` directly to see if it works on its own,",
            "  then re-run `aipager session " + name + "`.",
        )
        return 1

    if console.is_terminal:
        wait_status = console.status(
            "[muted]waiting for socket to appear…[/muted]", spinner="dots"
        )
        wait_status.__enter__()
    else:
        wait_status = None
    try:
        for _ in range(10):
            time.sleep(0.3)
            if sock_path.is_socket():
                break
    finally:
        if wait_status:
            wait_status.__exit__(None, None, None)
    if not sock_path.is_socket():
        diag = _claude_version_diag()
        friendly_error(
            f"dtach socket {sock} never appeared after launch.",
            "  This usually means the `claude` process crashed at startup.",
            *( [f"  `claude --version` said: {diag}"] if diag else [] ),
            "",
            "  Run `claude` directly to see the underlying error.",
        )
        return 1

    ok(f"session [path]{session}[/path] ready")
    elapsed = _attach_following(dtach, sock, name, redraw=False)
    if elapsed < 1.0 and not sock_path.exists():
        friendly_warn(
            f"session '{session}' exited immediately ({elapsed:.1f}s).",
            "  The claude process inside dtach may have crashed.",
            "  Run `claude --version` to check the install.",
        )
    return 0


def _claude_version_diag() -> str:
    """Return "" if a working claude resolves, else why it doesn't.

    Resolution itself already runs (and verifies) `--version` on every
    candidate, so a successful resolve here means claude is fine and
    the dtach-socket-never-appeared failure lies elsewhere.
    """
    from aipager import claude_resolve
    try:
        claude_resolve.resolve_claude_binary()
    except claude_resolve.ClaudeNotFoundError as e:
        return str(e)
    return ""
