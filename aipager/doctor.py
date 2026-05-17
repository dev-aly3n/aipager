"""Health checks for `aipager doctor`.

Each ``check_*`` function returns a :class:`CheckResult` so the same
checks can power both the doctor table and (later) targeted preflight
errors. The doctor command runs them all in order and prints a one-line
verdict for each, then summarizes failing checks with concrete fixes.

Doctor is **idempotent**: it never sends a Telegram message, never
mutates configuration, never starts/stops the daemon. It only reads
state.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARKERS = {OK: "✓", WARN: "⚠", FAIL: "✗"}


@dataclass
class CheckResult:
    status: str          # one of OK / WARN / FAIL
    title: str           # short headline (e.g., "Telegram bot token")
    detail: list[str] = field(default_factory=list)
    fix: str | None = None     # one-liner with the next step

    @property
    def marker(self) -> str:
        return _MARKERS[self.status]


def _http_json(url: str, timeout: float = 10.0) -> tuple[dict | None, str]:
    """Return ``(json_body, error_string)`` — exactly one will be empty."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r), ""
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            return None, f"HTTP {e.code}: {body.get('description', '?')}"
        except Exception:
            return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"network: {e.reason}"
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)


def check_config() -> CheckResult:
    from aipager.config import BOT_TOKEN, CHAT_ID

    missing = []
    if not BOT_TOKEN:
        missing.append("CLAUDE_TG_BOT_TOKEN")
    if not CHAT_ID:
        missing.append("CLAUDE_TG_CHAT_ID")
    if missing:
        return CheckResult(
            FAIL,
            "Telegram config",
            detail=[f"missing {', '.join(missing)}"],
            fix="aipager config",
        )
    return CheckResult(OK, "Telegram config", detail=[f"chat {CHAT_ID}"])


def check_token_valid() -> CheckResult:
    from aipager.config import BOT_TOKEN
    if not BOT_TOKEN:
        return CheckResult(FAIL, "Telegram bot token",
                           detail=["no token configured"],
                           fix="aipager config")
    body, err = _http_json(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
    if err:
        if "401" in err:
            return CheckResult(FAIL, "Telegram bot token",
                               detail=["Telegram rejected the token (HTTP 401)"],
                               fix="aipager config  # then paste a fresh token from @BotFather")
        return CheckResult(WARN, "Telegram bot token",
                           detail=[err],
                           fix="check your network and retry")
    if not (body and body.get("ok")):
        return CheckResult(FAIL, "Telegram bot token",
                           detail=["getMe returned ok=false"],
                           fix="aipager config")
    me = body["result"]
    return CheckResult(OK, "Telegram bot token",
                       detail=[f"@{me.get('username', '?')}"])


def check_chat_reachable() -> CheckResult:
    """Check the bot can address the chat — read-only, no send."""
    from aipager.config import BOT_TOKEN, CHAT_ID
    if not BOT_TOKEN or not CHAT_ID:
        return CheckResult(FAIL, "Telegram chat",
                           detail=["bot token or chat id missing"],
                           fix="aipager config")
    body, err = _http_json(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id={CHAT_ID}"
    )
    if err:
        if "chat not found" in err.lower():
            # We don't know the bot username at this point without another
            # API call; check_token_valid already did one and the doctor
            # prints checks in order, so reference the bot generically.
            return CheckResult(
                FAIL, "Telegram chat",
                detail=[f"chat {CHAT_ID} not reachable — bot may need /start"],
                fix="open your bot in Telegram, tap Start, then retry",
            )
        return CheckResult(WARN, "Telegram chat", detail=[err])
    if not (body and body.get("ok")):
        return CheckResult(WARN, "Telegram chat", detail=["getChat ok=false"])
    return CheckResult(OK, "Telegram chat", detail=[f"chat {CHAT_ID}"])


def _probe_binary(path: str, *args: str, timeout: float = 3.0) -> tuple[bool, str]:
    """Run ``path *args`` and return (success, first_line_of_output)."""
    try:
        r = subprocess.run(
            [path, *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return False, "binary not found"
    except subprocess.TimeoutExpired:
        return False, "probe timed out"
    except OSError as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "non-zero exit").splitlines()[0][:120]
    out = (r.stdout or r.stderr).strip().splitlines()
    return True, (out[0] if out else "")


def check_dtach() -> CheckResult:
    try:
        from dtach_bin import path as _dtach_path
        dtach_p: str | None = _dtach_path()
    except (ImportError, FileNotFoundError):
        dtach_p = shutil.which("dtach")
    if not dtach_p:
        return CheckResult(
            FAIL, "dtach binary",
            detail=["not bundled and not on PATH"],
            fix="uv tool install --reinstall aipager  # or `brew install dtach`",
        )
    # dtach prints usage to stderr and exits 1 on `-V` — just check it runs.
    ok, info = _probe_binary(dtach_p, "-V")
    if not ok:
        # dtach -V isn't standard; try a no-op invocation that exits cleanly.
        ok, info = _probe_binary(dtach_p, "-h")
    if not ok and "binary not found" in info:
        return CheckResult(FAIL, "dtach binary",
                           detail=[f"{dtach_p} fails to exec: {info}"],
                           fix="uv tool install --reinstall aipager")
    return CheckResult(OK, "dtach binary", detail=[dtach_p])


def check_claude() -> CheckResult:
    p = shutil.which("claude")
    if not p:
        return CheckResult(
            FAIL, "claude CLI",
            detail=["not on PATH"],
            fix="install Claude Code: https://docs.anthropic.com/claude/docs/claude-code",
        )
    ok, info = _probe_binary(p, "--version")
    if not ok:
        return CheckResult(WARN, "claude CLI",
                           detail=[f"{p} fails --version: {info}"],
                           fix="run `claude --version` to debug")
    return CheckResult(OK, "claude CLI", detail=[f"{p} ({info})"])


def check_settings_json() -> CheckResult:
    path = Path.home() / ".claude" / "settings.json"
    if not path.exists():
        return CheckResult(
            FAIL, "Claude Code settings.json",
            detail=["not found"],
            fix="aipager config",
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return CheckResult(FAIL, "Claude Code settings.json",
                           detail=[f"invalid JSON: {e}"],
                           fix="restore the auto-backup or re-run `aipager config`")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return CheckResult(FAIL, "Claude Code settings.json",
                           detail=[f"hooks key is {type(hooks).__name__}, expected dict"],
                           fix="back up settings.json and re-run `aipager config`")
    has_aipager_hook = False
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for block in entries:
            for h in (block or {}).get("hooks", []):
                cmd = (h or {}).get("command", "")
                if cmd == "aipager-hook" or cmd.endswith("/aipager-hook"):
                    has_aipager_hook = True
                    break
    if not has_aipager_hook:
        return CheckResult(WARN, "Claude Code settings.json",
                           detail=["no aipager-hook entry found"],
                           fix="aipager config")
    if not data.get("statusLine"):
        return CheckResult(WARN, "Claude Code settings.json",
                           detail=["no statusLine entry"],
                           fix="aipager config")
    return CheckResult(OK, "Claude Code settings.json")


def check_hook_scripts() -> CheckResult:
    missing = [n for n in ("aipager-hook", "aipager-statusline")
               if shutil.which(n) is None]
    if missing:
        return CheckResult(
            FAIL, "hook scripts on PATH",
            detail=[f"missing: {', '.join(missing)}"],
            fix="uv tool install --reinstall aipager",
        )
    return CheckResult(OK, "hook scripts on PATH")


def check_daemon() -> CheckResult:
    """Probe the daemon's hook socket via a dummy datagram."""
    from aipager.config import SOCKET_PATH
    sock_path = Path(SOCKET_PATH)
    if not sock_path.exists():
        return CheckResult(
            FAIL, "aipager daemon",
            detail=[f"socket {SOCKET_PATH} missing"],
            fix="aipager start   # or `aipager service start`",
        )
    # SOCK_DGRAM on AF_UNIX: if nothing's bound on the receiving end,
    # sendto() raises ConnectionRefusedError on Linux. We deliberately
    # send a payload the daemon will silently discard (unknown event).
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.settimeout(1.0)
        s.sendto(json.dumps({"event": "_doctor_ping"}).encode(), SOCKET_PATH)
    except (ConnectionRefusedError, FileNotFoundError):
        return CheckResult(
            FAIL, "aipager daemon",
            detail=[f"socket {SOCKET_PATH} exists but no daemon is listening"],
            fix="rm -f /tmp/aipager.sock && aipager start",
        )
    except OSError as e:
        return CheckResult(WARN, "aipager daemon", detail=[str(e)])
    finally:
        s.close()
    return CheckResult(OK, "aipager daemon", detail=[SOCKET_PATH])


def check_service_installed() -> CheckResult:
    sys_name = platform.system().lower()
    if sys_name == "linux":
        unit = Path.home() / ".config" / "systemd" / "user" / "aipager.service"
        if unit.exists():
            return CheckResult(OK, "service unit",
                               detail=[str(unit)])
        return CheckResult(WARN, "service unit",
                           detail=["not installed"],
                           fix="aipager service install  # optional: persist across logout")
    if sys_name == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.aipager.daemon.plist"
        if plist.exists():
            return CheckResult(OK, "service plist", detail=[str(plist)])
        return CheckResult(WARN, "service plist",
                           detail=["not installed"],
                           fix="aipager service install  # optional: persist across logout")
    return CheckResult(WARN, "service unit",
                       detail=[f"unsupported platform: {sys_name}"])


CHECKS: list[Callable[[], CheckResult]] = [
    check_config,
    check_token_valid,
    check_chat_reachable,
    check_claude,
    check_dtach,
    check_hook_scripts,
    check_settings_json,
    check_daemon,
    check_service_installed,
]


def run_all() -> list[CheckResult]:
    return [fn() for fn in CHECKS]


def _print_results(results: list[CheckResult]) -> None:
    width = max(len(r.title) for r in results) + 2
    for r in results:
        line = f"  {r.marker}  {r.title.ljust(width)}"
        if r.detail:
            line += "  " + " · ".join(r.detail)
        print(line)


def _print_fixes(results: list[CheckResult]) -> None:
    fixes = [r for r in results if r.status != OK and r.fix]
    if not fixes:
        return
    print()
    print("Suggested next steps:")
    for r in fixes:
        print(f"  • {r.title}: {r.fix}")


def cmd_doctor(_args: argparse.Namespace | None = None) -> int:
    from aipager import __version__
    print(f"aipager {__version__} on {platform.system().lower()} "
          f"(python {sys.version_info.major}.{sys.version_info.minor})")
    print()
    results = run_all()
    _print_results(results)
    _print_fixes(results)
    print()
    if any(r.status == FAIL for r in results):
        return 1
    if any(r.status == WARN for r in results):
        return 0
    return 0


__all__ = [
    "OK", "WARN", "FAIL", "CheckResult", "run_all", "cmd_doctor",
    "check_config", "check_token_valid", "check_chat_reachable",
    "check_dtach", "check_claude", "check_settings_json",
    "check_hook_scripts", "check_daemon", "check_service_installed",
    "CHECKS",
]

# Silence the unused-import warning for shutil/os in environments
# where ruff is strict.
_ = (os, shutil)
