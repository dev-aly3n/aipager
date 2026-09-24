"""Implementation of `aipager update` / `aipager uninstall`.

Which installer owns the running aipager (uv tool, pipx, Homebrew, or a
user-owned pip venv) is decided by :mod:`aipager.install_source` from the
running interpreter itself, and every command comes from its one
installer table with an absolute binary path — so this works from a
non-interactive ssh shell or a systemd unit whose PATH lacks
``~/.local/bin``. Every spawn goes through
:func:`aipager.self_update.run_command` (argv lists, a timeout, never a
shell).

Upgrading replaces files under the running venv. This CLI process exits
right after, but a running daemon does NOT have its modules in memory —
it imports lazily on hot paths — so ``aipager update`` prints how to
restart it and never restarts anything itself. ``/update`` in Telegram
does the restart safely.
"""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from aipager.errors import friendly_error, friendly_warn
from aipager.install_source import _EXTRA_PACKAGES  # noqa: F401  (re-export)
from aipager.ui import console, ok as ui_ok


# ---------------------------------------------------------------------------
# Installer detection
# ---------------------------------------------------------------------------

def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def _detect_installer() -> str | None:
    """``"uv"|"pipx"|"brew"|"pip"`` for the install that owns THIS
    interpreter, or None when it is not upgradable from here."""
    from aipager import install_source
    return install_source.installer_kind()


# ---------------------------------------------------------------------------
# `aipager update`
# ---------------------------------------------------------------------------

def cmd_update(_args=None) -> int:
    """Upgrade aipager via the installer that owns the running interpreter.

    0 on success or when already current; 1 on a refused install, a
    missing installer binary, a busy lock, a failure or a timeout. Never
    restarts anything.
    """
    from aipager import install_source, self_update

    source = install_source.detect_install_source()
    if not source.upgradable:
        friendly_error(
            f"can't update this install: {source.reason}.",
            "",
            f"  Install: {source.describe()}",
        )
        return 1
    argv = install_source.upgrade_argv(source)
    if argv is None:
        friendly_error(
            f"could not find `{source.kind}`, which owns this aipager.",
            "",
            "  Searched: " + install_source.augmented_path(),
        )
        return 1

    lock = self_update.UpdateLock()
    if not lock.try_acquire():
        friendly_error(
            "another update is already running (the daemon's /update or a "
            "second `aipager update`). Try again when it finishes."
        )
        return 1
    try:
        running = self_update.running_version()
        console.print(
            f"[step]→[/step] upgrading aipager via [path]{source.describe()}[/path]"
        )
        res = self_update.run_command(
            argv, timeout=self_update.UPGRADE_TIMEOUT_SECONDS,
            env=install_source.upgrade_env(source), capture=False,
        )
        if res.timed_out:
            friendly_error(
                f"the upgrade timed out after {self_update.UPGRADE_TIMEOUT_SECONDS}s "
                "and was stopped."
            )
            return 1
        if res.error:
            friendly_error(f"upgrade failed: {res.error}")
            return 1
        if res.returncode != 0:
            friendly_error(f"upgrade failed (exit {res.returncode}).")
            return 1
        new, importable, err = self_update.probe_installed_version(source.python)
        if not importable:
            friendly_error(
                "the upgrade finished but the new version fails to import.",
                f"  {err}" if err else "",
                "  Reinstall with: " + install_source.reinstall_hint(source.kind),
            )
            return 1
        if new == running:
            ui_ok(f"already at {running}")
            if source.origin == "local":
                console.print(
                    f"  [muted]This install upgrades from the local path "
                    f"{source.origin_detail}, which has the same version.[/muted]"
                )
            return 0
        ui_ok(f"aipager {running} → {new}")
        for line in self_update.cli_restart_instruction():
            console.print(f"  {line}")
        return 0
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# `aipager uninstall`
# ---------------------------------------------------------------------------

# Per-user paths to clean up. (Daemon socket + per-session sockets in /tmp
# are handled separately because they have wildcards.)
_USER_PATHS_TO_REMOVE = [
    Path.home() / ".config" / "aipager",
    Path.home() / ".claude" / "aipager-sessions.json",
]

_MACOS_PATHS_TO_REMOVE = [
    Path.home() / "Library" / "LaunchAgents" / "com.aipager.daemon.plist",
    Path.home() / "Library" / "Logs" / "aipager.log",
]


def _stop_daemon() -> None:
    """Best-effort: stop a running daemon before uninstalling."""
    # Remove the service unit FIRST, in-process.
    #
    # This used to shell out to `sys.executable -m aipager.cli service
    # uninstall`, which cannot work: `aipager.cli` is a package with no
    # __main__.py, so the interpreter exits with "cannot be directly
    # executed" every single time. With capture_output=True and
    # check=False that failure was invisible, so every Linux uninstall
    # silently left an enabled Restart=always unit behind pointing at a
    # binary that was about to be deleted. systemd then retried it every
    # RestartSec seconds forever — the unit sets StartLimitIntervalSec=0,
    # which disables the start limiter that would otherwise give up.
    # Calling the handler directly also drops the assumption that
    # sys.executable can even import aipager.
    try:
        import argparse

        from aipager.service import cmd_service
        cmd_service(argparse.Namespace(service_cmd="uninstall"))
    except Exception:
        pass
    # Belt and braces: also kill any foreground daemon.
    if _has_binary("pkill"):
        from aipager import self_update
        pkill = shutil.which("pkill") or "pkill"
        self_update.run_command([pkill, "-f", "aipager start"], timeout=10)


def _remove_path(path: Path) -> bool:
    """Remove a file or directory tree. Return True if something was removed."""
    if not path.exists() and not path.is_symlink():
        return False
    try:
        if path.is_dir() and not path.is_symlink():
            import shutil as _shutil
            _shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except OSError as e:
        friendly_warn(f"could not remove {path}: {e}")
        return False


def _unlink_quietly(path: Path) -> None:
    """Unlink ``path`` if present, swallowing every OSError.

    ``missing_ok=True`` covers only ``FileNotFoundError``; a path owned
    by another user (or on a read-only mount) raises other ``OSError``
    subtypes that must not abort a best-effort uninstall.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _remove_tmp_sockets() -> None:
    """Remove the daemon control socket, /tmp/claude-dtach-*.sock and
    /tmp/claude-status-*.json — best-effort.

    The control socket moved off /tmp to $XDG_RUNTIME_DIR, so this reads
    ``config.SOCKET_PATH`` rather than hardcoding a path that uninstall
    would clean while the real socket survived. The literal
    ``/tmp/aipager.sock`` is still removed afterwards: an install that
    predates the move leaves one there, and uninstall is exactly when it
    should go.

    ``missing_ok=True`` only suppresses ``FileNotFoundError``, so both
    unlinks still go through ``_unlink_quietly``: /tmp is world-writable
    and the literal path is not namespaced per user, so a ``sudo``-era
    leftover owned by root — or another OS user's socket — raises
    ``PermissionError`` rather than being absent. Uninstall promises
    best-effort cleanup and must not abort partway through on one
    stubborn path.

    Imported locally — ``config`` reads the environment at import time,
    and updater is reachable from cold CLI paths that must not pay that
    cost just to print a help string.
    """
    from aipager.config import SOCKET_PATH

    _unlink_quietly(Path(SOCKET_PATH))
    if str(SOCKET_PATH) != "/tmp/aipager.sock":
        # Runs on every uninstall where the socket has moved (i.e. any
        # host with $XDG_RUNTIME_DIR set), not only when a genuine
        # pre-move leftover exists — it is a no-op when there is none.
        _unlink_quietly(Path("/tmp/aipager.sock"))
    for p in Path("/tmp").glob("claude-dtach-*.sock"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in Path("/tmp").glob("claude-status-*.json"):
        try:
            p.unlink()
        except OSError:
            pass


def _uninstall_binary(installer: str | None) -> int:
    from aipager import install_source, self_update

    cmd = install_source.uninstall_argv(installer)
    if cmd is None:
        return 0  # nothing to do
    console.print(f"[step]→[/step] uninstalling aipager via [path]{installer}[/path]")
    res = self_update.run_command(cmd, timeout=300,
                                  env=install_source.spawn_env(), capture=False)
    if res.error or res.returncode is None:
        friendly_warn(f"binary uninstall failed: {res.error or 'timed out'}")
        return 1
    return res.returncode


def cmd_uninstall(args=None) -> int:
    """Stop the daemon, remove user state, uninstall the binary."""
    force = bool(getattr(args, "force", False))

    is_macos = platform.system() == "Darwin"
    installer = _detect_installer()

    console.print("[title]This will remove:[/title]")
    console.print(f"  • aipager binary ({installer or 'no installer detected'})")
    for p in _USER_PATHS_TO_REMOVE:
        console.print(f"  • [path]{p}[/path]")
    console.print("  • the daemon control socket, /tmp/claude-dtach-*.sock, "
                  "/tmp/claude-status-*.json")
    if is_macos:
        for p in _MACOS_PATHS_TO_REMOVE:
            console.print(f"  • [path]{p}[/path]")
    console.print()
    console.print("[muted]Not touched: your Telegram bot, Claude Code's "
                  "settings.json, and any[/muted]")
    console.print("[muted]                ~/.claude/settings.json.bak.* "
                  "backups.[/muted]")
    console.print()

    if not force:
        # Only when actually prompting — `uninstall -y` must keep working
        # from a script, which is the whole point of that flag.
        from aipager.errors import require_interactive
        require_interactive()
        answer = input("Continue? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            return 0

    # 1. Stop daemon
    _stop_daemon()

    # 2. Remove user state
    for p in _USER_PATHS_TO_REMOVE:
        if _remove_path(p):
            ui_ok(f"removed [path]{p}[/path]")

    # 3. Remove tmp sockets / statusline files
    _remove_tmp_sockets()
    ui_ok("cleaned up /tmp sockets and statusline files")

    # 4. macOS service artifacts
    if is_macos:
        for p in _MACOS_PATHS_TO_REMOVE:
            if _remove_path(p):
                ui_ok(f"removed [path]{p}[/path]")

    # 5. Uninstall the binary itself (last — removes us from PATH)
    _uninstall_binary(installer)

    console.print()
    ui_ok("aipager uninstalled.")
    console.print(
        "  [muted]Want to reinstall? "
        "https://aipager.run/install (or `uv tool install aipager`)[/muted]"
    )
    return 0


# ---------------------------------------------------------------------------
# Install / reinstall with an optional extra (driven from Telegram for 5.3)
# ---------------------------------------------------------------------------

def install_extra_cmd(installer: str | None, extra: str) -> list[str] | None:
    """Build the command to (re)install aipager with an optional extra.

    For uv / pipx we go through the installer so the extra is recorded
    and survives a later ``aipager update``. For brew / pip / editable /
    unknown installs we fall back to installing the extra's packages
    directly into the daemon's Python interpreter — works uniformly
    across brew formulas, project venvs, ``pip --user`` and editable
    installs, at the cost that a future ``brew upgrade aipager`` may
    rebuild the formula's venv and require a re-install.

    Installer binaries are absolute when resolvable (bare otherwise).
    Built from :func:`aipager.install_source.extra_install_argv`, the one
    installer table. Returns ``None`` only for genuinely unsupported
    extras.
    """
    from aipager import install_source
    return install_source.extra_install_argv(installer, extra)


__all__ = ["cmd_update", "cmd_uninstall", "install_extra_cmd"]
