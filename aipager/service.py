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
import sys
from pathlib import Path

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


def _run(cmd: list[str], check: bool = False) -> int:
    return subprocess.run(cmd, check=check).returncode


def _install_linux() -> int:
    LINUX_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LINUX_UNIT_PATH.write_text(_render_linux_unit())
    print(f"  ✓ wrote {LINUX_UNIT_PATH}")
    _run(["systemctl", "--user", "daemon-reload"])
    rc = _run(["systemctl", "--user", "enable", "--now", "aipager.service"])
    if rc != 0:
        print("  ✗ failed to enable/start the service", file=sys.stderr)
        return rc
    print("  ✓ enabled and started")
    print()
    print("  status:  systemctl --user status aipager")
    print("  logs:    journalctl --user -u aipager -f")
    print("  stop:    aipager service stop")
    return 0


def _install_macos() -> int:
    MACOS_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MACOS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    MACOS_PLIST_PATH.write_text(_render_macos_plist())
    print(f"  ✓ wrote {MACOS_PLIST_PATH}")
    domain = f"gui/{os.getuid()}"
    # bootstrap is idempotent if we bootout first
    _run(["launchctl", "bootout", f"{domain}/{MACOS_LABEL}"])
    rc = _run(["launchctl", "bootstrap", domain, str(MACOS_PLIST_PATH)])
    if rc != 0:
        print("  ✗ failed to bootstrap the launch agent", file=sys.stderr)
        return rc
    _run(["launchctl", "kickstart", f"{domain}/{MACOS_LABEL}"])
    print("  ✓ loaded and started")
    print()
    print(f"  status:  launchctl print {domain}/{MACOS_LABEL}")
    print(f"  logs:    tail -f {MACOS_LOG_PATH}")
    print("  stop:    aipager service stop")
    return 0


def _start_linux() -> int:
    return _run(["systemctl", "--user", "start", "aipager.service"])


def _start_macos() -> int:
    return _run(["launchctl", "kickstart", f"gui/{os.getuid()}/{MACOS_LABEL}"])


def _stop_linux() -> int:
    return _run(["systemctl", "--user", "stop", "aipager.service"])


def _stop_macos() -> int:
    return _run(["launchctl", "kill", "TERM", f"gui/{os.getuid()}/{MACOS_LABEL}"])


def _status_linux() -> int:
    return _run(["systemctl", "--user", "status", "aipager.service"])


def _status_macos() -> int:
    return _run(["launchctl", "print", f"gui/{os.getuid()}/{MACOS_LABEL}"])


def _logs_linux() -> int:
    return _run(["journalctl", "--user", "-u", "aipager.service", "-f"])


def _logs_macos() -> int:
    return _run(["tail", "-f", str(MACOS_LOG_PATH)])


def _uninstall_linux() -> int:
    _run(["systemctl", "--user", "disable", "--now", "aipager.service"])
    LINUX_UNIT_PATH.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    print(f"  ✓ removed {LINUX_UNIT_PATH}")
    return 0


def _uninstall_macos() -> int:
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{MACOS_LABEL}"])
    MACOS_PLIST_PATH.unlink(missing_ok=True)
    print(f"  ✓ removed {MACOS_PLIST_PATH}")
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
        print(f"Unsupported platform: {plat}", file=sys.stderr)
        print("Fallback: run `aipager start` under screen, tmux, or nohup.",
              file=sys.stderr)
        return 1
    sub = getattr(args, "service_cmd", None)
    handler = _DISPATCH[plat].get(sub)
    if not handler:
        print(f"Unknown service subcommand: {sub}", file=sys.stderr)
        return 1
    # install needs config because the unit will fail to boot without it.
    # start/stop/status/logs/uninstall are pure service-manager wrappers
    # and don't care.
    if sub == "install":
        from aipager.preflight import require_config
        require_config()
    return handler()
