"""See :mod:`aipager.wizard` for the package overview."""

from __future__ import annotations

import os


from aipager.ui import console
from aipager.wizard._constants import (
    CONFIG_DIR, CONFIG_ENV,
)


def _read_env_file() -> tuple[str, str]:
    """Return ``(token, chat_id)`` from CONFIG_ENV, or ``("", "")``
    if the file is missing or malformed."""
    if not CONFIG_ENV.exists():
        return "", ""
    token = ""
    chat_id = ""
    try:
        for line in CONFIG_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip("\"'")
            if k == "CLAUDE_TG_BOT_TOKEN":
                token = v
            elif k == "CLAUDE_TG_CHAT_ID":
                chat_id = v
    except OSError:
        return "", ""
    return token, chat_id


def _write_env_file(token: str, chat_id: int | str) -> None:
    """Overwrite CONFIG_ENV (mode 0600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_ENV.write_text(
        f"CLAUDE_TG_BOT_TOKEN={token}\nCLAUDE_TG_CHAT_ID={chat_id}\n"
    )
    try:
        os.chmod(CONFIG_ENV, 0o600)
    except OSError:
        pass


def _socket_path():
    """The daemon's control socket, read at call time (a seam the test
    suite points away from the live daemon)."""
    from aipager.config import SOCKET_PATH
    return SOCKET_PATH


def _detect_daemon_running() -> int | None:
    """Probe the daemon's control socket and return its PID if we can
    find one, ``None`` otherwise, ``-1`` when it is up but its PID cannot
    be told for sure. The PID is SIGNALLED by the post-edit live reload
    (:func:`_signal_reload`, roadmap 8.80), so only a PID that is really
    the daemon is returned: the service's ``MainPID`` first, else a
    single ``pgrep`` match whose argv is ``aipager start`` in this PID
    namespace (:func:`_pick_daemon_pid`).

    Uses ``aipager.config.SOCKET_PATH`` rather than a hardcoded
    ``/tmp/aipager.sock`` — under the systemd unit that socket lives at
    ``$XDG_RUNTIME_DIR/aipager.sock``, and probing the wrong path would
    always report "not running" even while the daemon is up.
    """
    import socket as _socket
    p = _socket_path()
    try:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.sendto(b'{"event":"_wizard_probe"}', p)
        s.close()
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return None
    import shutil as _shutil
    import subprocess as _subprocess
    main_pid = _systemd_main_pid()
    if main_pid is not None:
        return main_pid
    # Best-effort PID lookup via pgrep
    if _shutil.which("pgrep"):
        try:
            r = _subprocess.run(
                ["pgrep", "-f", "aipager start"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                pid = _pick_daemon_pid(r.stdout)
                if pid is not None:
                    return pid
        except (OSError, _subprocess.TimeoutExpired):
            pass
    return -1  # daemon up, PID unknown


def _systemd_main_pid() -> int | None:
    """The background service's main PID (``systemctl --user show -p
    MainPID``), when the service runs and that process is the daemon;
    ``None`` otherwise. The service's cgroup also holds every session it
    launched, so only its MAIN process may be signalled."""
    import shutil as _shutil
    import subprocess as _subprocess
    if not _shutil.which("systemctl"):
        return None
    try:
        r = _subprocess.run(
            ["systemctl", "--user", "show", "-p", "MainPID", "--value",
             "aipager.service"],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, _subprocess.TimeoutExpired):
        return None
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out.isdigit() or int(out) <= 0:
        return None
    pid = int(out)
    argv = _read_cmdline(pid)
    if argv is not None and not _is_daemon_argv(argv):
        return None
    return pid


def _same_pid_namespace(pid: int) -> bool:
    """False when *pid* runs in another PID namespace than this process
    (a container's aipager seen from the host). Unknown reads as True."""
    try:
        return os.readlink(f"/proc/{pid}/ns/pid") == os.readlink(
            "/proc/self/ns/pid")
    except OSError:
        return True


def _read_cmdline(pid: int) -> list[str] | None:
    """*pid*'s argv from ``/proc``, or ``None`` where it cannot be read
    (no ``/proc``, the process is gone, another user's)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    return [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]


def _is_daemon_argv(argv: list[str]) -> bool:
    """``aipager start`` as two real arguments (``/path/aipager start``,
    ``python -m aipager start``), not a shell or an editor whose one
    argument merely contains the words."""
    for i, arg in enumerate(argv[:-1]):
        if os.path.basename(arg) == "aipager" and argv[i + 1] == "start":
            return True
    return False


def _pick_daemon_pid(pgrep_out: str) -> int | None:
    """The daemon's PID among ``pgrep -f 'aipager start'``'s matches, or
    ``None`` when it cannot be told for sure.

    The wizard now signals this PID (SIGUSR1, roadmap 8.80), and
    SIGUSR1's default action is to terminate: a wrong guess would kill an
    innocent process (a shell running ``... && aipager start``, an editor
    with the words in a file name). So a match counts only when its argv
    is the daemon's (``/proc``) in this PID namespace (a container's
    aipager is not this one), and anything ambiguous is unknown, which
    falls back to the restart hint."""
    me = os.getpid()
    pids = [int(t) for t in pgrep_out.split() if t.isdigit()]
    pids = [p for p in pids if p != me]
    confirmed: list[int] = []
    unknown: list[int] = []
    for pid in pids:
        argv = _read_cmdline(pid)
        if argv is None:
            unknown.append(pid)
        elif _is_daemon_argv(argv) and _same_pid_namespace(pid):
            confirmed.append(pid)
    if len(confirmed) == 1:
        return confirmed[0]
    if not confirmed and len(unknown) == 1 and len(pids) == 1:
        return unknown[0]  # no /proc (macOS): only an unambiguous match
    return None


def _restart_hint() -> None:
    """Print a one-line reminder that the daemon must be restarted
    for config changes to take effect."""
    pid = _detect_daemon_running()
    if pid is None:
        # Daemon not running — nothing to restart.
        return
    console.print()
    console.print(
        "[warn]⚠[/warn]  [warn]Restart the daemon to apply this change:[/warn]"
    )
    console.print(
        "    [path]aipager service restart[/path]"
        "  [muted](or kill the foreground daemon and re-run `aipager start`)[/muted]"
    )


def _signal_reload() -> bool:
    """Send SIGUSR1 to the running daemon to live-reload its scopes and
    policy (``aipager.yaml``, ``policy.yaml``).

    Returns ``True`` iff a signal was delivered. ``False`` when the
    daemon isn't running, the PID is unknown, or the platform
    doesn't support signals (Windows). Caller handles fallback.
    """
    import signal as _signal

    pid = _detect_daemon_running()
    if pid is None or pid < 0:
        return False
    try:
        os.kill(pid, _signal.SIGUSR1)
        return True
    except (OSError, AttributeError):
        return False


def _apply_team_change_hint() -> None:
    """Post-edit feedback for changes a live reload applies: scopes,
    members, roles, ``deny_tools`` (roadmap 8.80).

    Sends the running daemon a live reload (SIGUSR1) when it can be
    found; otherwise falls back to the restart hint (which says nothing
    when no daemon runs). Use the bare :func:`_restart_hint` for edits a
    reload does not apply (the bot token).
    """
    outcome, problem = _live_reload()
    if outcome == "refused":
        # The daemon would refuse this reload and keep its previous
        # config: never claim the change is live. With no daemon running
        # there is no previous config to keep: say only what is wrong.
        console.print()
        console.print(
            f"[warn]⚠[/warn]  [warn]Not applied: {problem}[/warn]"
        )
        if _detect_daemon_running() is not None:
            console.print(
                "    [muted]The daemon keeps its previous config until this "
                "is fixed.[/muted]"
            )
        return
    if outcome == "reloaded":
        console.print()
        console.print(
            "[ok]✓[/ok]  Scopes reloaded live "
            "[muted](no daemon restart needed)[/muted]"
        )
        return
    _restart_hint()


def _live_reload() -> tuple[str, str | None]:
    """Ask the running daemon to reload the config on disk, without
    printing. ``("refused", problem)`` when the daemon would refuse it
    (:func:`_config_problem`; nothing is sent), ``("reloaded", None)``
    when SIGUSR1 was delivered, ``("not_reloaded", None)`` when no daemon
    PID could be signalled (none running, or its PID unknown)."""
    problem = _config_problem()
    if problem is not None:
        return "refused", problem
    if _signal_reload():
        return "reloaded", None
    return "not_reloaded", None


def _config_problem() -> str | None:
    """Why the daemon would refuse a reload of the config as it is on
    disk now (the same loads ``reload_team`` does), or ``None``."""
    from aipager import policy as policy_mod
    from aipager import scope as scope_mod
    try:
        loaded = scope_mod.load_scopes(scope_mod.CONFIG_PATH)
        policy_mod.load_policy(policy_mod.POLICY_PATH,
                               policy_mod.POLICY_D_DIR)
    except (scope_mod.ScopeConfigError, policy_mod.PolicyError) as e:
        return str(e)
    if loaded is None:
        return "aipager.yaml is missing"
    return None
