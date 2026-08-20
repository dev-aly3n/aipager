"""Implementation of `aipager update` / `aipager uninstall`.

Detects how aipager was installed (uv tool, pipx, or Homebrew) and
runs the matching upgrade / removal command. Falls back to a friendly
error when no known installer is in charge.

The replacement of the running ``aipager`` binary by uv/pipx/brew is
safe: each one writes the new file in place, but the current Python
process already has its modules in memory.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from aipager.errors import friendly_error, friendly_warn
from aipager.ui import console, ok as ui_ok


# ---------------------------------------------------------------------------
# Installer detection
# ---------------------------------------------------------------------------

def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def _uv_has_aipager() -> bool:
    if not _has_binary("uv"):
        return False
    try:
        r = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and "aipager" in r.stdout


def _pipx_has_aipager() -> bool:
    if not _has_binary("pipx"):
        return False
    try:
        r = subprocess.run(
            ["pipx", "list", "--short"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and "aipager" in r.stdout


def _brew_has_aipager() -> bool:
    if not _has_binary("brew"):
        return False
    try:
        r = subprocess.run(
            ["brew", "list", "aipager"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _detect_installer() -> str | None:
    """Return the name of the installer that owns this aipager, or None."""
    if _uv_has_aipager():
        return "uv"
    if _pipx_has_aipager():
        return "pipx"
    if _brew_has_aipager():
        return "brew"
    return None


# ---------------------------------------------------------------------------
# `aipager update`
# ---------------------------------------------------------------------------

def cmd_update(_args=None) -> int:
    """Upgrade aipager via the installer that owns it."""
    installer = _detect_installer()
    if installer is None:
        friendly_error(
            "could not detect how aipager was installed.",
            "",
            "  Tried `uv tool list`, `pipx list`, `brew list aipager` — none",
            "  reported aipager. If you installed via `pip install --user` or",
            "  in a project venv, upgrade that manually:",
            "      pip install --upgrade aipager",
        )
        return 1

    if installer == "uv":
        # --refresh forces uv to bypass its index cache, which has bitten
        # users when a fresh PyPI release was minutes old.
        cmd = ["uv", "tool", "upgrade", "aipager", "--refresh"]
    elif installer == "pipx":
        cmd = ["pipx", "upgrade", "aipager"]
    elif installer == "brew":
        cmd = ["brew", "upgrade", "aipager"]
    else:  # defensive — _detect_installer only returns one of the above
        return 1

    console.print(f"[step]→[/step] upgrading aipager via [path]{installer}[/path]")
    try:
        rc = subprocess.run(cmd).returncode
    except (OSError, subprocess.SubprocessError) as e:
        friendly_error(f"upgrade failed: {e}")
        return 1
    if rc == 0:
        ui_ok("upgrade complete")
    return rc


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
        subprocess.run(["pkill", "-f", "aipager start"],
                       capture_output=True, check=False)


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
    if installer == "uv":
        cmd = ["uv", "tool", "uninstall", "aipager"]
    elif installer == "pipx":
        cmd = ["pipx", "uninstall", "aipager"]
    elif installer == "brew":
        cmd = ["brew", "uninstall", "aipager"]
    else:
        return 0  # nothing to do
    console.print(f"[step]→[/step] uninstalling aipager via [path]{installer}[/path]")
    try:
        return subprocess.run(cmd, check=False).returncode
    except (OSError, subprocess.SubprocessError) as e:
        friendly_warn(f"binary uninstall failed: {e}")
        return 1


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

# Map each optional extra to the concrete packages we need pip to install
# when the installer-aware path isn't available (brew formulas don't
# expose pip extras, editable / unknown installs have no installer to
# go through). Keep in sync with the [project.optional-dependencies]
# table in pyproject.toml.
_EXTRA_PACKAGES: dict[str, list[str]] = {
    "voice": ["faster-whisper>=1.0"],
}


def install_extra_cmd(installer: str | None, extra: str) -> list[str] | None:
    """Build the command to (re)install aipager with an optional extra.

    For uv / pipx we go through the installer so the extra is recorded
    and survives a later ``aipager update``. For brew / editable /
    unknown installs we fall back to installing the extra's packages
    directly into the daemon's Python interpreter — works uniformly
    across brew formulas, project venvs, ``pip --user`` and editable
    installs, at the cost that a future ``brew upgrade aipager`` may
    rebuild the formula's venv and require a re-install.

    Returns ``None`` only for genuinely unsupported extras.
    """
    if installer == "uv":
        return ["uv", "tool", "install", "--reinstall", f"aipager[{extra}]"]
    if installer == "pipx":
        return ["pipx", "install", "--force", f"aipager[{extra}]"]
    packages = _EXTRA_PACKAGES.get(extra)
    if packages is None:
        return None
    return [sys.executable, "-m", "pip", "install", "--upgrade", *packages]


__all__ = ["cmd_update", "cmd_uninstall", "install_extra_cmd"]

# Keep the `os` import referenced; some platforms may need it for future
# Windows additions, but right now it's only used transitively. Silence
# the unused-import lint without weakening it elsewhere.
_ = os
