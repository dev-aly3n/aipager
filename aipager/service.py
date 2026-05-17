"""Cross-platform daemon service installer.

Linux:  systemd-user unit at ``~/.config/systemd/user/aipager.service``.
        Managed via ``systemctl --user``.
macOS:  launchd plist at ``~/Library/LaunchAgents/com.aipager.daemon.plist``.
        Managed via ``launchctl``.

The unit/plist always points at the *absolute path* of the ``aipager``
console script (resolved via :func:`shutil.which`), so this works
identically for pipx, brew, and editable-venv installs.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from aipager.errors import friendly_error, friendly_warn
from aipager.ui import console, ok, step

LINUX_UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / "aipager.service"
MACOS_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.aipager.daemon.plist"
MACOS_LABEL = "com.aipager.daemon"
MACOS_LOG_PATH = Path.home() / "Library" / "Logs" / "aipager.log"

LINUX_UNIT_TEMPLATE = """\
[Unit]
Description=AIPager Telegram Bot Daemon
After=network-online.target

[Service]
Type=simple
ExecStartPre=-/bin/rm -f /tmp/aipager.sock
ExecStart={aipager_bin} start
EnvironmentFile=-%h/.config/aipager/config.env
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

MACOS_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{aipager_bin}</string>
        <string>start</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>{home}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


def _platform() -> str:
    s = platform.system().lower()
    if s == "linux":
        return "linux"
    if s == "darwin":
        return "macos"
    return s


def _resolve_aipager_bin() -> str:
    p = shutil.which("aipager")
    if not p:
        raise FileNotFoundError(
            "aipager not on PATH — install via pipx/brew/pip before running "
            "`aipager service install`"
        )
    return p


def _render_linux_unit() -> str:
    return LINUX_UNIT_TEMPLATE.format(aipager_bin=_resolve_aipager_bin())


def _render_macos_plist() -> str:
    return MACOS_PLIST_TEMPLATE.format(
        aipager_bin=_resolve_aipager_bin(),
        label=MACOS_LABEL,
        home=str(Path.home()),
        log_path=str(MACOS_LOG_PATH),
    )


def _run(cmd: list[str], *, capture: bool = True,
         check: bool = False) -> tuple[int, str, str]:
    """Run ``cmd`` returning (returncode, stdout, stderr).

    When the binary is not found, returns ``(127, "", "<bin>: not found")``
    so the caller can decide how to surface the error rather than getting
    a traceback. Use ``capture=False`` for commands whose live output
    must reach the terminal (e.g., journalctl -f).
    """
    try:
        if capture:
            r = subprocess.run(cmd, capture_output=True, text=True, check=check)
            return r.returncode, r.stdout, r.stderr
        r = subprocess.run(cmd, check=check)
        return r.returncode, "", ""
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"


def _systemd_user_available() -> tuple[bool, str]:
    """Return (available, reason) — False on container/WSL1/no-systemd."""
    if shutil.which("systemctl") is None:
        return False, "systemctl not on PATH"
    rc, out, err = _run(["systemctl", "--user", "is-system-running"])
    state = (out or err or "").strip()
    if rc == 127:
        return False, err
    # When systemd-user is fine we get one of: running, degraded, starting.
    # offline/unknown means user instance isn't really there.
    if state in ("offline", "unknown", ""):
        return False, state or "systemctl gave no answer"
    return True, state


def _backup_existing(path: Path) -> None:
    """If *path* exists, copy it aside with a timestamp suffix."""
    if not path.exists():
        return
    backup = path.with_name(f"{path.name}.bak.{int(time.time())}")
    try:
        backup.write_text(path.read_text())
        console.print(
            f"  [muted]• backed up existing {path.name} → {backup.name}[/muted]"
        )
    except OSError as e:
        friendly_warn(f"could not back up {path}: {e}")


def _install_linux() -> int:
    available, reason = _systemd_user_available()
    if not available:
        friendly_error(
            "systemd-user is not available on this machine.",
            f"  Detail: {reason}",
            "",
            "  This is normal in containers, WSL1, and minimal distros.",
            "  Run the daemon directly under tmux/screen/nohup instead:",
            "",
            "      aipager start",
        )
        return 2

    step("Installing aipager.service (systemd-user)")

    LINUX_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _backup_existing(LINUX_UNIT_PATH)
    LINUX_UNIT_PATH.write_text(_render_linux_unit())
    ok(f"wrote [path]{LINUX_UNIT_PATH}[/path]")

    rc, _out, err = _run(["systemctl", "--user", "daemon-reload"])
    if rc != 0:
        friendly_warn(f"systemctl daemon-reload returned {rc}",
                      f"  {err.strip()}")
    else:
        ok("systemctl daemon-reload")

    rc, _out, err = _run(["systemctl", "--user", "enable", "--now",
                          "aipager.service"])
    if rc != 0:
        friendly_error(
            f"systemctl --user enable --now aipager.service failed "
            f"(exit {rc}).",
            f"  {err.strip()}" if err.strip() else "  (no stderr captured)",
        )
        return rc
    ok("enabled and started")
    _check_linger()
    _post_install_probe()
    console.print()
    console.print("  [muted]status:[/muted]  systemctl --user status aipager")
    console.print("  [muted]logs:[/muted]    journalctl --user -u aipager -f")
    console.print("  [muted]stop:[/muted]    aipager service stop")
    return 0


def _check_linger() -> None:
    if shutil.which("loginctl") is None:
        return
    user = os.environ.get("USER", "")
    if not user:
        return
    rc, out, _err = _run(["loginctl", "show-user", user,
                          "--property=Linger"])
    if rc != 0:
        return
    if "Linger=no" in out:
        friendly_warn(
            "Your systemd-user session will end at logout (Linger=no).",
            "  To keep aipager running across logouts, run once:",
            f"      loginctl enable-linger {user}",
        )


def _post_install_probe() -> None:
    """Wait a beat, then ping the socket to confirm the daemon came up."""
    from aipager.doctor import check_daemon, FAIL
    time.sleep(2)
    result = check_daemon()
    if result.status == FAIL:
        friendly_warn(
            "daemon didn't come up within 2s.",
            "  Check the logs with `aipager service logs`.",
        )


def _install_macos() -> int:
    if shutil.which("launchctl") is None:
        friendly_error(
            "launchctl not on PATH — this doesn't look like macOS.",
            "  Run `aipager start` under tmux/screen instead.",
        )
        return 2

    step("Installing aipager (launchd)")
    MACOS_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MACOS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _backup_existing(MACOS_PLIST_PATH)
    MACOS_PLIST_PATH.write_text(_render_macos_plist())
    ok(f"wrote [path]{MACOS_PLIST_PATH}[/path]")
    domain = f"gui/{os.getuid()}"
    # bootstrap is idempotent if we bootout first
    _run(["launchctl", "bootout", f"{domain}/{MACOS_LABEL}"])
    rc, _out, err = _run(["launchctl", "bootstrap", domain,
                          str(MACOS_PLIST_PATH)])
    if rc != 0:
        friendly_error(
            f"launchctl bootstrap failed (exit {rc}).",
            f"  {err.strip()}" if err.strip() else "  (no stderr captured)",
            "",
            "  Common causes: existing plist with mismatched label, SIP",
            "  restrictions, or a syntax error in the rendered plist.",
        )
        return rc
    _run(["launchctl", "kickstart", f"{domain}/{MACOS_LABEL}"])
    ok("loaded and started")
    _post_install_probe()
    console.print()
    console.print(
        f"  [muted]status:[/muted]  launchctl print {domain}/{MACOS_LABEL}"
    )
    console.print(f"  [muted]logs:[/muted]    tail -f {MACOS_LOG_PATH}")
    console.print("  [muted]stop:[/muted]    aipager service stop")
    return 0


def _require_installed_linux() -> bool:
    if LINUX_UNIT_PATH.exists():
        return True
    friendly_error(
        "aipager service isn't installed.",
        f"  {LINUX_UNIT_PATH} doesn't exist.",
        "",
        "  Install it once:",
        "      aipager service install",
    )
    return False


def _require_installed_macos() -> bool:
    if MACOS_PLIST_PATH.exists():
        return True
    friendly_error(
        "aipager service isn't installed.",
        f"  {MACOS_PLIST_PATH} doesn't exist.",
        "",
        "  Install it once:",
        "      aipager service install",
    )
    return False


def _start_linux() -> int:
    if not _require_installed_linux():
        return 2
    rc, _out, err = _run(["systemctl", "--user", "start", "aipager.service"])
    if rc != 0 and err.strip():
        print(err.rstrip())
    return rc


def _start_macos() -> int:
    if not _require_installed_macos():
        return 2
    rc, _out, err = _run(["launchctl", "kickstart",
                          f"gui/{os.getuid()}/{MACOS_LABEL}"])
    if rc != 0 and err.strip():
        print(err.rstrip())
    return rc


def _stop_linux() -> int:
    if not _require_installed_linux():
        return 2
    rc, _out, err = _run(["systemctl", "--user", "stop", "aipager.service"])
    if rc != 0 and err.strip():
        print(err.rstrip())
    return rc


def _stop_macos() -> int:
    if not _require_installed_macos():
        return 2
    rc, _out, err = _run(["launchctl", "kill", "TERM",
                          f"gui/{os.getuid()}/{MACOS_LABEL}"])
    if rc != 0 and err.strip():
        print(err.rstrip())
    return rc


def _status_linux() -> int:
    if not _require_installed_linux():
        return 2
    return _run(["systemctl", "--user", "status", "aipager.service"],
                capture=False)[0]


def _status_macos() -> int:
    if not _require_installed_macos():
        return 2
    return _run(["launchctl", "print", f"gui/{os.getuid()}/{MACOS_LABEL}"],
                capture=False)[0]


def _logs_linux() -> int:
    if not _require_installed_linux():
        return 2
    return _run(["journalctl", "--user", "-u", "aipager.service", "-f"],
                capture=False)[0]


def _logs_macos() -> int:
    if not _require_installed_macos():
        return 2
    return _run(["tail", "-f", str(MACOS_LOG_PATH)], capture=False)[0]


def _uninstall_linux() -> int:
    _run(["systemctl", "--user", "disable", "--now", "aipager.service"])
    LINUX_UNIT_PATH.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    ok(f"removed [path]{LINUX_UNIT_PATH}[/path]")
    return 0


def _uninstall_macos() -> int:
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{MACOS_LABEL}"])
    MACOS_PLIST_PATH.unlink(missing_ok=True)
    ok(f"removed [path]{MACOS_PLIST_PATH}[/path]")
    return 0


_DISPATCH = {
    "linux": {
        "install": _install_linux, "start": _start_linux, "stop": _stop_linux,
        "status": _status_linux, "logs": _logs_linux, "uninstall": _uninstall_linux,
    },
    "macos": {
        "install": _install_macos, "start": _start_macos, "stop": _stop_macos,
        "status": _status_macos, "logs": _logs_macos, "uninstall": _uninstall_macos,
    },
}


def cmd_service(args: argparse.Namespace) -> int:
    plat = _platform()
    if plat not in _DISPATCH:
        friendly_error(
            f"Unsupported platform: {plat}",
            "  Run `aipager start` under screen, tmux, or nohup.",
        )
        return 1
    sub = getattr(args, "service_cmd", None)
    handler = _DISPATCH[plat].get(sub)
    if not handler:
        friendly_error(f"Unknown service subcommand: {sub}")
        return 1
    # install needs config because the unit will fail to boot without it.
    # start/stop/status/logs/uninstall are pure service-manager wrappers
    # and don't care.
    if sub == "install":
        from aipager.preflight import require_config
        require_config()
    return handler()
