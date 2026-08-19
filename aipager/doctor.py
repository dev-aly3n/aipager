"""Health checks for `aipager doctor`.

Each ``check_*`` function returns a :class:`CheckResult` so the same
checks can power both the doctor table and (later) targeted preflight
errors. The doctor command runs them all in order and prints a one-line
verdict for each, then summarizes failing checks with concrete fixes.

Doctor is **idempotent**: it never sends a Telegram message, never
mutates configuration, never starts/stops the daemon. It only reads
state. ``aipager doctor --fix`` (``cmd_doctor_fix``) is the one
deliberate exception — interactive, and the only place that writes
``daemon.env`` or ``claude_path`` outside of ``aipager config`` /
``aipager service install``.
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


def check_config_parses() -> CheckResult:
    """Report an unparseable ``aipager.yaml`` / ``policy.yaml``.

    Runs first: when this fails, every later check is reading a config
    that is not the one on disk, so the user needs to see it before
    interpreting anything below.
    """
    from aipager.config import CONFIG_ERROR

    if CONFIG_ERROR:
        return CheckResult(
            FAIL,
            "Config file parses",
            detail=[CONFIG_ERROR],
            fix="aipager config  # or hand-edit the file named above",
        )
    return CheckResult(OK, "Config file parses")


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
    """Resolve fresh (doctor is one-shot) and show every distinct install.

    Delegates entirely to :mod:`aipager.claude_resolve` — the same
    precedence chain and verification every other call site uses, so
    this reports exactly what a session launch would get.
    """
    from aipager import claude_resolve

    try:
        resolved = claude_resolve.resolve_claude_binary(force=True)
    except claude_resolve.ClaudeNotFoundError as e:
        return CheckResult(
            FAIL, "claude CLI",
            detail=str(e).splitlines(),
            fix="install Claude Code: https://docs.anthropic.com/claude/docs/claude-code",
        )
    detail = [f"{resolved.chosen.path} ({resolved.chosen.version})"]
    for other in resolved.others:
        detail.append(f"also: {other.path} ({other.version}) — set claude_path to override")
    return CheckResult(OK, "claude CLI", detail=detail)


def check_claude_auth() -> CheckResult:
    """Probe `claude auth status` against the SAME environment a real
    session launch gets — never the daemon's own bare ``os.environ``.

    **Never FAILs.** Auth here is diagnostic only: the daemon and every
    session launch regardless of what this reports — see the
    non-negotiable "never refuse to launch on no auth" and
    :mod:`aipager.claude_resolve`'s module docstring for why a probe
    failure must never be conflated with "not logged in" (that's
    reported as a WARN with a distinct message, not treated as FAIL).
    """
    from aipager import claude_resolve, daemon_secrets

    try:
        resolved = claude_resolve.resolve_claude_binary(force=True)
    except claude_resolve.ClaudeNotFoundError as e:
        return CheckResult(WARN, "claude auth", detail=[f"can't probe — {e}"])

    env = daemon_secrets.build_session_env()
    auth = claude_resolve.detect_auth(
        resolved.chosen.path, resolved.chosen.version, env,
    )
    line = claude_resolve.format_provenance(resolved, auth)[0]
    # Strip the leading "claude: <path> (<version>) · " — check_claude()
    # above already shows the path/version; this row is auth-only.
    detail_line = line.split("· ", 1)[-1] if "· " in line else line

    if auth.source in ("probe-failed", "version-gated"):
        return CheckResult(WARN, "claude auth", detail=[detail_line])
    if not auth.logged_in:
        return CheckResult(
            WARN, "claude auth", detail=[detail_line],
            fix="claude auth login  # or set an API key / CLAUDE_CODE_OAUTH_TOKEN",
        )

    # `auth status` says we have a credential — but it only checks that
    # one EXISTS. A revoked or expired token still answers
    # `{"loggedIn": true}`, and the operator would see a green row here
    # while every session silently parks on the login screen. So spend
    # one small round-trip and report what actually happens.
    check = claude_resolve.validate_credential(resolved.chosen.path, env)
    if check.state == "rejected":
        return CheckResult(
            WARN, "claude auth",
            detail=[f"{detail_line} — but the API rejected it "
                    "(expired or revoked)"],
            fix="claude auth login  # the stored credential is no longer valid",
        )
    if check.state == "absent":
        return CheckResult(
            WARN, "claude auth",
            detail=[f"{detail_line} — but claude reports no usable credential"],
            fix="claude auth login",
        )
    if check.state == "unknown":
        # Offline, or a probe we could not interpret. Report the cheap
        # check's answer rather than inventing a verdict.
        return CheckResult(
            OK, "claude auth",
            detail=[f"{detail_line} (not re-verified: {check.detail})"],
        )
    return CheckResult(OK, "claude auth", detail=[f"{detail_line} — verified"])


def check_service_unit_path() -> CheckResult:
    """Compare the INSTALLED unit's ``Environment=PATH=`` against the
    resolved binary's directory, parsed as TEXT.

    Doctor runs in the operator's own interactive shell, which cannot
    see systemd's PATH for the unit it manages — the exact gap that let
    a hand-written unit with the wrong PATH report OK before this check
    existed (``check_service_installed`` only stats the file). Non-Linux
    and not-installed are both OK — not applicable, not a problem.
    """
    if platform.system().lower() != "linux":
        return CheckResult(OK, "service unit PATH", detail=["not applicable on this OS"])

    from aipager.service import LINUX_UNIT_PATH
    if not LINUX_UNIT_PATH.exists():
        return CheckResult(OK, "service unit PATH", detail=["service not installed"])

    try:
        text = LINUX_UNIT_PATH.read_text()
    except OSError as e:
        return CheckResult(WARN, "service unit PATH", detail=[str(e)])

    import re
    m = re.search(r"^Environment=PATH=(?P<value>.*)$", text, re.MULTILINE)
    if not m:
        return CheckResult(
            FAIL, "service unit PATH",
            detail=["installed unit has no Environment=PATH="],
            fix="aipager service install --yes  # re-render the unit",
        )
    unit_path_dirs = m.group("value").split(os.pathsep)

    from aipager import claude_resolve
    try:
        resolved = claude_resolve.resolve_claude_binary(force=True)
    except claude_resolve.ClaudeNotFoundError:
        return CheckResult(WARN, "service unit PATH",
                           detail=["can't verify — no claude binary resolves"])
    claude_dir = str(Path(resolved.chosen.path).parent)
    if claude_dir in unit_path_dirs:
        return CheckResult(OK, "service unit PATH", detail=[claude_dir])
    return CheckResult(
        FAIL, "service unit PATH",
        detail=[f"unit's PATH does not include {claude_dir}"],
        fix="aipager service install --yes  # re-render with the current PATH",
    )


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
            fix=f"rm -f {SOCKET_PATH} && aipager start",
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


def check_team() -> CheckResult:
    """Validate team.yaml against the configured CHAT_ID and roster.

    Skipped (OK / personal mode) when team.yaml is absent or doesn't
    declare team mode. Reports common misconfigurations:

    - team.yaml malformed → FAIL
    - group_id doesn't match CLAUDE_TG_CHAT_ID → FAIL (daemon would
      otherwise filter every message away as off-chat)
    - no admin user → WARN (admin is the only role that bypasses
      rules.deny_tools, so an admin-less team can't escape its own
      rules)
    - rules.deny_tools empty → WARN with a suggestion
    """
    from aipager.config import CHAT_ID
    from aipager.team import Role, TEAM_CONFIG_PATH, TeamConfigError, load_team

    if not TEAM_CONFIG_PATH.exists():
        return CheckResult(OK, "team config", detail=["personal mode (no team.yaml)"])

    try:
        team = load_team(TEAM_CONFIG_PATH)
    except TeamConfigError as e:
        return CheckResult(
            FAIL, "team config",
            detail=[f"team.yaml malformed: {e}"],
            fix="edit ~/.config/aipager/team.yaml or re-run `aipager config`",
        )

    if team is None:
        # Present but `mode != team` — treat as personal.
        return CheckResult(
            OK, "team config",
            detail=["team.yaml present but mode != team (personal mode)"],
        )

    issues: list[str] = []
    fixes: list[str] = []

    # 1) Chat id must match group_id, otherwise the daemon's chat filter
    # silently rejects every message — the most confusing failure mode.
    try:
        env_chat = int(CHAT_ID) if CHAT_ID else None
    except ValueError:
        env_chat = None
    if env_chat is None:
        issues.append("CLAUDE_TG_CHAT_ID is unset or non-numeric")
        fixes.append("aipager config  # to set the group chat id")
    elif env_chat != team.group_id:
        issues.append(
            f"CHAT_ID ({env_chat}) != team.yaml group_id ({team.group_id})"
        )
        fixes.append(
            "edit ~/.config/aipager/config.env to set "
            f"CLAUDE_TG_CHAT_ID={team.group_id}"
        )

    # 2) At least one admin.
    if not any(u.role == Role.ADMIN for u in team.users.values()):
        issues.append("no admin user — no one can bypass rules.deny_tools")
        fixes.append(
            "promote a user to admin via `aipager config` → "
            "Change a user's role"
        )

    # 3) Suggest deny_tools if empty.
    suggestions: list[str] = []
    if not team.rules.deny_tools:
        suggestions.append(
            "rules.deny_tools is empty — consider enabling at least "
            "[Write, Edit] to block accidental file changes"
        )

    if issues:
        status = FAIL if any("CHAT_ID" in i for i in issues) else WARN
        return CheckResult(
            status, "team config",
            detail=[
                f"team mode · {len(team.users)} user(s) · "
                f"{team.admin_count()} admin(s)",
                *issues,
                *suggestions,
            ],
            fix="; ".join(fixes) if fixes else None,
        )

    if suggestions:
        return CheckResult(
            WARN, "team config",
            detail=[
                f"team mode · {len(team.users)} user(s) · "
                f"{team.admin_count()} admin(s)",
                *suggestions,
            ],
        )

    return CheckResult(
        OK, "team config",
        detail=[
            f"team mode · {len(team.users)} user(s) · "
            f"{team.admin_count()} admin(s) · "
            f"{len(team.rules.deny_tools)} deny rule(s)",
        ],
    )


def check_miniapp() -> CheckResult:
    """Can the Mini App actually start, if it is switched on?

    **Never FAILs** — same fail-open discipline as
    :func:`check_claude_auth`. The Mini App is optional; a base install
    without it is a perfectly healthy aipager, and nothing about a
    missing extra should stop a session from launching.

    This row exists because its absence hid a real one: an install whose
    Mini App could never start (``aiohttp`` was not present — it was an
    opt-in extra at the time) still reported ``13 ok · 0 warn · 0 fail``, and the
    only clue was a daemon log line nobody reads. The failure surfaced
    to the operator as ``/app`` not working, in Telegram, much later.
    """
    from aipager.config import MINIAPP_ENABLED, MINIAPP_PORT
    from aipager.miniapp.server import (
        miniapp_extra_available, reinstall_with_miniapp_hint,
    )

    if not MINIAPP_ENABLED:
        return CheckResult(OK, "Mini App", detail=["disabled"])
    if not miniapp_extra_available():
        return CheckResult(
            WARN, "Mini App",
            detail=["enabled in config, but aiohttp is missing — the "
                    "server cannot start (incomplete install)"],
            fix=reinstall_with_miniapp_hint(),
        )
    return CheckResult(OK, "Mini App", detail=[f"enabled · port {MINIAPP_PORT}"])


CHECKS: list[Callable[[], CheckResult]] = [
    check_config_parses,
    check_config,
    check_token_valid,
    check_chat_reachable,
    check_team,
    check_claude,
    check_claude_auth,
    check_dtach,
    check_hook_scripts,
    check_settings_json,
    check_daemon,
    check_service_installed,
    check_service_unit_path,
    check_miniapp,
]


def run_all() -> list[CheckResult]:
    return [fn() for fn in CHECKS]


_STATUS_STYLE = {OK: "ok", WARN: "warn", FAIL: "err"}


def _print_results(results: list[CheckResult]) -> None:
    from rich.table import Table
    from aipager.ui import console, is_tty

    if not is_tty():
        # Off-TTY (CI, pipes): emit plain padded text so log scrapers
        # can grep `^  ✗  hook scripts on PATH`-style lines.
        width = max(len(r.title) for r in results) + 2
        for r in results:
            line = f"  {r.marker}  {r.title.ljust(width)}"
            if r.detail:
                line += "  " + " · ".join(r.detail)
            console.print(line)
        return

    t = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2))
    t.add_column(justify="center", width=3)
    t.add_column(no_wrap=True)
    t.add_column(style="hint")
    for r in results:
        style = _STATUS_STYLE[r.status]
        t.add_row(
            f"[{style}]{r.marker}[/{style}]",
            r.title,
            " · ".join(r.detail),
        )
    console.print(t)


def _print_fixes(results: list[CheckResult]) -> None:
    from aipager.ui import console

    fixes = [r for r in results if r.status != OK and r.fix]
    if not fixes:
        return
    console.print()
    console.print("[title]Suggested next steps[/title]")
    for r in fixes:
        console.print(f"  [muted]•[/muted] {r.title}: [hint]{r.fix}[/hint]")


def _print_summary(results: list[CheckResult]) -> None:
    from aipager.ui import console

    counts = {OK: 0, WARN: 0, FAIL: 0}
    for r in results:
        counts[r.status] += 1
    parts = [
        f"[ok]{counts[OK]} ok[/ok]",
        f"[warn]{counts[WARN]} warn[/warn]",
        f"[err]{counts[FAIL]} fail[/err]",
    ]
    console.print(f"\n[muted]{' · '.join(parts)}[/muted]")


def _print_safety_policy() -> None:
    """Render the active safety policy (paths + bash patterns + roles)."""
    from aipager.config import POLICY
    from aipager.ui import console

    console.print("Safety policy (enforced for Telegram-driven sessions):")
    console.print("  Blocked paths (read+write):")
    for p in POLICY.safety_deny_paths_no_access:
        console.print(f"    • {p}")
    if POLICY.safety_deny_paths_no_write:
        console.print("  Blocked paths (write):")
        for p in POLICY.safety_deny_paths_no_write:
            console.print(f"    • {p}")
    console.print("  Blocked bash patterns:")
    for p in POLICY.safety_deny_bash_patterns:
        console.print(f"    • /{p}/")
    console.print("  Roles:")
    for name, role in sorted(POLICY.roles.items()):
        flags = []
        if role.bypass_safety:
            flags.append("bypass_safety")
        if role.bypass_role_denies:
            flags.append("bypass_role_denies")
        if not role.can_prompt:
            flags.append("read-only")
        extra = f" ({', '.join(flags)})" if flags else ""
        console.print(f"    • {name}{extra}")
    console.print()
    console.print(
        "  Note: enforcement is pattern-based; all scopes share one "
        "filesystem.\n  For hard isolation between untrusted users, run "
        "separate daemons\n  per OS account. See the multi-scope docs."
    )


def _fix_daemon_credential() -> None:
    """`--fix` step (a): offer to discover/copy a credential into
    daemon.env when nothing is there yet. Interactive — the CLI-only
    home for this class of question (see the contract's non-negotiable:
    a Telegram-side credential-copy question was dropped from scope)."""
    from aipager.daemon_secrets import DAEMON_ENV_PATH
    from aipager import service as _service
    from aipager.ui import console

    has_content = DAEMON_ENV_PATH.exists() and DAEMON_ENV_PATH.stat().st_size > 0
    if has_content:
        console.print(f"  [ok]✓[/ok]  {DAEMON_ENV_PATH} already has content — leaving it alone")
        return
    console.print(f"  No credential found at {DAEMON_ENV_PATH}.")
    answer = input("  Try to discover one automatically? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        console.print("  [muted]skipped[/muted]")
        return
    _service.ensure_daemon_env()


def _fix_claude_path() -> None:
    """`--fix` step (b): offer to pin `claude_path` when multiple
    installs are ambiguous, following `dump_miniapp`'s surgical
    read-modify-write via `scope.dump_claude_path`."""
    from aipager import claude_resolve
    from aipager import scope as _scope
    from aipager.ui import console

    console.print()
    try:
        resolved = claude_resolve.resolve_claude_binary(force=True)
    except claude_resolve.ClaudeNotFoundError as e:
        console.print(f"  [warn]⚠[/warn]  no claude binary resolves — {e}")
        return
    if not resolved.others:
        console.print("  [ok]✓[/ok]  one claude install found — nothing to disambiguate")
        return

    installs = [resolved.chosen, *resolved.others]
    console.print("  Multiple claude installs found:")
    console.print(f"    0) {installs[0].path} ({installs[0].version}) — current pick")
    for i, install in enumerate(installs[1:], start=1):
        console.print(f"    {i}) {install.path} ({install.version})")
    answer = input(
        "  Pin one via claude_path in aipager.yaml? "
        "Enter a number, or blank to skip: "
    ).strip()
    if not answer:
        console.print("  [muted]skipped[/muted]")
        return
    try:
        idx = int(answer)
    except ValueError:
        console.print("  [warn]⚠[/warn]  not a number — skipped")
        return
    if not (0 <= idx < len(installs)):
        console.print("  [warn]⚠[/warn]  out of range — skipped")
        return
    try:
        # `path=` passed explicitly and read from the module at call
        # time — dump_claude_path's `path: Path = CONFIG_PATH` default
        # is bound once, at aipager.scope's IMPORT time. Relying on the
        # default here would silently target whatever CONFIG_PATH was
        # at import (the operator's real ~/.config/aipager/aipager.yaml)
        # even when a caller has since repointed `_scope.CONFIG_PATH`
        # (as every test in this suite does) — the same late-binding
        # trap `config._load_miniapp()` documents avoiding.
        _scope.dump_claude_path(installs[idx].path, path=_scope.CONFIG_PATH)
        console.print(f"  [ok]✓[/ok]  claude_path set to {installs[idx].path}")
    except _scope.ScopeConfigError as e:
        console.print(f"  [warn]⚠[/warn]  could not write claude_path: {e}")


def cmd_doctor_fix() -> int:
    """`aipager doctor --fix` — interactive, the CLI-only home for the
    two questions the contract keeps out of Telegram entirely:
    (a) discovering/copying a Claude credential into ``daemon.env``,
    (b) pinning ``claude_path`` on a multi-install ambiguity or a
    unit/PATH mismatch. Never runs automatically — every step asks
    first."""
    from aipager.ui import console

    console.print("[title]aipager doctor --fix[/title]")
    console.print()
    _fix_daemon_credential()
    _fix_claude_path()
    return 0


def cmd_doctor(args: argparse.Namespace | None = None) -> int:
    from aipager import __version__
    from aipager.config import SCOPES
    from aipager.ui import console, rule

    if getattr(args, "safety_check", False):
        _print_safety_policy()
        return 0

    if getattr(args, "fix", False):
        return cmd_doctor_fix()

    if console.is_terminal:
        rule(f"aipager {__version__} · {platform.system().lower()} · "
             f"python {sys.version_info.major}.{sys.version_info.minor}")
    else:
        console.print(
            f"aipager {__version__} on {platform.system().lower()} "
            f"(python {sys.version_info.major}.{sys.version_info.minor})"
        )
    console.print()
    results = run_all()
    _print_results(results)
    _print_fixes(results)
    _print_summary(results)
    if SCOPES and len(SCOPES) > 1:
        console.print()
        console.print(
            "ℹ️  Multiple scopes share one filesystem. Telegram-driven "
            "sessions can't read each other's aipager data, but this is "
            "not a hard multi-tenant sandbox — for mutually untrusted "
            "users, run separate per-OS-user daemons. "
            "(`aipager doctor --safety-check` shows the policy.)"
        )
    console.print()
    if any(r.status == FAIL for r in results):
        return 1
    return 0


__all__ = [
    "OK", "WARN", "FAIL", "CheckResult", "run_all", "cmd_doctor",
    "cmd_doctor_fix",
    "check_config", "check_token_valid", "check_chat_reachable",
    "check_team",
    "check_dtach", "check_claude", "check_claude_auth",
    "check_settings_json",
    "check_hook_scripts", "check_daemon", "check_service_installed",
    "check_service_unit_path",
    "CHECKS",
]

# Silence the unused-import warning for shutil/os in environments
# where ruff is strict.
_ = (os, shutil)
