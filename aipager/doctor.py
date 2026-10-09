"""Health checks for `aipager doctor`.

Each ``check_*`` function returns a :class:`CheckResult` so the same
checks can power both the doctor table and (later) targeted preflight
errors. The doctor command runs them all in order and prints a one-line
verdict for each, then summarizes failing checks with concrete fixes.

Doctor is **idempotent**: it never sends a Telegram message, never
mutates configuration, never starts/stops the daemon. It only reads
state. ``aipager doctor --json`` prints the same checks as one JSON
object, keyed by check name (:func:`run_all_keyed`), for scripts and
coding agents. ``aipager doctor --fix`` (``cmd_doctor_fix``) is the one
deliberate exception — interactive, and the only place that writes
``daemon.env`` or ``claude_path`` outside of ``aipager config`` /
``aipager service install``.
"""

from __future__ import annotations

import argparse
import json
import logging
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

from aipager import telegram_endpoint

log = logging.getLogger(__name__)

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
    from aipager.errors import redact_token
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r), ""
    # Redacted like the wizard's copy: this URL carries the bot token, and
    # `aipager doctor` prints these strings straight to the terminal.
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            return None, redact_token(
                f"HTTP {e.code}: {body.get('description', '?')}")
        except Exception:
            return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, redact_token(f"network: {e.reason}")
    except (OSError, json.JSONDecodeError) as e:
        return None, redact_token(str(e))


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
    bad_base = telegram_endpoint.check()
    if bad_base:
        return CheckResult(FAIL, "Telegram bot token", detail=bad_base,
                           fix=f"unset {telegram_endpoint.BASE_ENV}")
    body, err = _http_json(telegram_endpoint.method_url(BOT_TOKEN, "getMe"))
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
    bad_base = telegram_endpoint.check()
    if bad_base:
        return CheckResult(FAIL, "Telegram chat", detail=bad_base,
                           fix=f"unset {telegram_endpoint.BASE_ENV}")
    body, err = _http_json(
        f"{telegram_endpoint.method_url(BOT_TOKEN, 'getChat')}?chat_id={CHAT_ID}"
    )
    if err:
        if "chat not found" in err.lower():
            # We don't know the bot username at this point without another
            # API call; check_token_valid already did one and the doctor
            # prints checks in order, so reference the bot generically.
            return CheckResult(
                FAIL, "Telegram chat",
                detail=[f"chat {CHAT_ID} not reachable - bot may need /start"],
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
        detail.append(f"also: {other.path} ({other.version}) - set claude_path to override")
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
        return CheckResult(WARN, "claude auth", detail=[f"can't probe - {e}"])

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
            detail=[f"{detail_line} - but the API rejected it "
                    "(expired or revoked)"],
            fix="claude auth login  # the stored credential is no longer valid",
        )
    if check.state == "absent":
        return CheckResult(
            WARN, "claude auth",
            detail=[f"{detail_line} - but claude reports no usable credential"],
            fix="claude auth login",
        )
    if check.state == "unknown":
        # Offline, or a probe we could not interpret. Report the cheap
        # check's answer rather than inventing a verdict.
        return CheckResult(
            OK, "claude auth",
            detail=[f"{detail_line} (not re-verified: {check.detail})"],
        )
    return CheckResult(OK, "claude auth", detail=[f"{detail_line} (verified)"])


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
                           detail=["can't verify - no claude binary resolves"])
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
    # A live daemon that Telegram has flood-banned in a chat looks dead
    # from the chat. Say so here rather than let the operator restart it
    # into the ban (the mute self-clears; a restart sends one more
    # attempt — see docs/troubleshooting.md).
    #
    # A 429 BACKOFF is the other half of that story and the opposite kind
    # of news: the chat is being paced more slowly on purpose, nothing is
    # muted and no message is lost, so it rides along in `detail` and
    # NEVER changes the row's severity (roadmap 8.21). An operator whose
    # cards feel slow reads this row and sees why, instead of restarting
    # a daemon that is working exactly as designed.
    # MINIMAL MODE is the third state, added by 8.27, and it is the one
    # new thing here that deserves a WARN of its own: a chat whose earned
    # rate has fallen under the floor has stopped animating its cards
    # entirely, which looks broken and is not. A merely REDUCED rate never
    # changes severity, exactly as the backoff does not — it is the system
    # working.
    from aipager.status import (
        flood_backoff_lines,
        flood_chat_lines,
        flood_mute_lines,
        read_flood_backoffs,
        read_flood_chats,
        read_flood_mutes,
    )

    mutes = read_flood_mutes()
    backoff = flood_backoff_lines(read_flood_backoffs())
    chats = read_flood_chats()
    chat_lines = flood_chat_lines(chats)
    minimal = [c for c in chats if c.get("minimal")]
    if mutes:
        return CheckResult(
            WARN, "aipager daemon",
            detail=[SOCKET_PATH, *flood_mute_lines(mutes),
                    "self-clears when the ban lapses - do not restart into it",
                    *backoff, *chat_lines],
        )
    if minimal:
        return CheckResult(
            WARN, "aipager daemon",
            detail=[SOCKET_PATH,
                    "a chat is in minimal mode - busy-card updates are "
                    "paused so answers keep flowing; it lifts as the "
                    "chat's earned rate recovers",
                    *backoff, *chat_lines],
        )
    return CheckResult(OK, "aipager daemon",
                       detail=[SOCKET_PATH, *backoff, *chat_lines])


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


def _scope_mode_line() -> str:
    """On a scope-mode install (``aipager.yaml``, no ``team.yaml``): one
    line naming the scopes, e.g. ``scope mode: 2 scopes (1 private chat,
    1 group)``. ``""`` when no scopes are configured (personal mode)."""
    from aipager.config import SCOPES
    if not SCOPES:
        return ""
    dms = sum(1 for s in SCOPES if s.kind == "dm")
    groups = len(SCOPES) - dms
    parts = []
    if dms:
        parts.append(f"{dms} private chat" + ("s" if dms != 1 else ""))
    if groups:
        parts.append(f"{groups} group" + ("s" if groups != 1 else ""))
    n = len(SCOPES)
    return (f"scope mode: {n} scope" + ("s" if n != 1 else "")
            + f" ({', '.join(parts)}) in aipager.yaml")


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
        scopes_line = _scope_mode_line()
        if scopes_line:
            return CheckResult(OK, "team config", detail=[scopes_line])
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
        issues.append("no admin user - no one can bypass rules.deny_tools")
        fixes.append(
            "promote a user to admin with `aipager config` → "
            "Edit a member → Set role"
        )

    # 3) Suggest deny_tools if empty.
    suggestions: list[str] = []
    if not team.rules.deny_tools:
        suggestions.append(
            "rules.deny_tools is empty - consider enabling at least "
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


def _code_tools_of(role) -> list[str]:
    """The code-running tools (``safety.CODE_EXECUTION_TOOLS``) ``role``
    may use: all of them when it bypasses role denies."""
    from aipager.safety import CODE_EXECUTION_TOOLS, tool_violation

    return [t for t in CODE_EXECUTION_TOOLS
            if role.bypass_role_denies
            or tool_violation(t, role.deny_tools, role.allow_tools) is None]


def check_role_shell_access() -> CheckResult:
    """Warn, once per role in use, when a role without the owner's bypass
    can run Bash (roadmap 8.50). aipager's safety rules for such a turn
    are regex patterns and path globs: the Claude session runs as the
    same OS user that owns aipager's files, so a shell can reach them
    through spellings no pattern anticipates. The built-in ``user`` and
    ``read_only`` roles have no Bash; ``admin`` bypasses role denies and
    keeps it; ``policy.yaml`` can give it back to any role.

    Only roles a scope member actually holds are checked — in personal
    mode there are no restricted senders to warn about."""
    from aipager.config import POLICY, SCOPES

    if not SCOPES:
        return CheckResult(OK, "role shell access",
                           detail=["personal mode (no scopes)"])
    used = sorted({m.role for s in SCOPES for m in s.members})
    warned = []
    for name in used:
        role = POLICY.get_role(name)
        if role is None or role.bypass_safety:
            continue
        tools = _code_tools_of(role)
        if tools:
            what = "Bash" if "Bash" in tools else ", ".join(tools)
            warned.append(
                f"role {name} has {what}: its safety rules are best-effort, "
                "not a boundary")
    if warned:
        return CheckResult(
            WARN, "role shell access", detail=warned,
            fix="give only people you would hand a shell to a role with "
                "Bash (docs/security.md)",
        )
    return CheckResult(OK, "role shell access",
                       detail=["no restricted role has Bash"])


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
            detail=["enabled in config, but aiohttp is missing - the "
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
    check_role_shell_access,
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


_CRASH_DETAIL_MAX = 200  # chars of a crashed check's message kept on its row


def _check_title(fn: Callable[[], CheckResult]) -> str:
    """The row title for a check that never returned one: its function
    name minus the ``check_`` prefix, underscores as spaces
    (``check_claude_auth`` → ``claude auth``)."""
    name = getattr(fn, "__name__", "") or "check"
    for prefix in ("_check_", "check_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace("_", " ") or "check"


def _check_key(fn: Callable[[], CheckResult]) -> str:
    """The stable machine key of a check (``aipager doctor --json``): its
    function name minus the ``check_`` prefix (``check_token_valid`` ->
    ``token_valid``), ``"check"`` for a callable with no name. Titles are
    not keys: they differ by platform and a crashed check has none."""
    name = getattr(fn, "__name__", "") or "check"
    for prefix in ("_check_", "check_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name or "check"


def run_all() -> list[CheckResult]:
    """Every check's result, in order (see :func:`run_all_keyed`)."""
    return [r for _k, r in run_all_keyed()]


def run_all_keyed() -> list[tuple[str, CheckResult]]:
    """Run every check in order. A check that raises becomes one WARN
    row naming the check and the error (roadmap 8.8) instead of taking
    the whole report down: every check is written never to raise, but
    that is fourteen separate promises holding a diagnostic tool up,
    and `doctor` is what the operator runs when something is already
    wrong. ``Exception`` only — Ctrl-C must still stop the command. The
    error text is escaped because both renderers print through a rich
    console that would otherwise read ``[...]`` in a message as markup.
    """
    from rich.markup import escape

    results: list[tuple[str, CheckResult]] = []
    for fn in CHECKS:
        try:
            results.append((_check_key(fn), fn()))
        except Exception as e:
            title = _check_title(fn)
            log.debug("doctor check %s crashed",
                      getattr(fn, "__name__", None) or title, exc_info=True)
            # One line, bounded — a row is one line in both renderers
            # (the off-TTY form is documented as grep-able), the same
            # treatment `_probe_binary` gives a subprocess's stderr.
            first_line = (str(e).splitlines() or [""])[0].strip()[:_CRASH_DETAIL_MAX]
            text = f"check crashed: {type(e).__name__}"
            if first_line:
                text += f": {first_line}"
            results.append((_check_key(fn), CheckResult(
                WARN, title, detail=[escape(text)],
                fix="Re-run `aipager doctor`; if it keeps crashing, report the line above.",
            )))
    return results


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


def _unanchored_safety_paths(policy) -> list[str]:
    """The safety section's no-access paths with no leading ``/`` or
    ``~``: such a rule can match in any folder, so it denies every search
    of every non-owner role (``safety._search_violation`` and
    ``_unconfined_search_violation``, roadmap 8.96)."""
    return [p for p in policy.safety_deny_paths_no_access
            if not os.path.expanduser(p).startswith("/")]


def _print_unanchored_safety_paths(console, policy) -> None:
    from rich.markup import escape

    for p in _unanchored_safety_paths(policy):
        console.print(
            f"  [warn]⚠[/warn]  safety path {escape(p)} in policy.yaml has no leading "
            "/ or ~, so it can match in any folder: every Grep and Glob of "
            "every role except owner is denied while it is there.")


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
    console.print("  These apply to every role except owner (built-in, plus "
                  "the safety: section of policy.yaml).")
    _print_unanchored_safety_paths(console, POLICY)
    console.print("  Roles:")
    for name, role in sorted(POLICY.roles.items()):
        flags = []
        if role.bypass_safety:
            flags.append("bypass_safety")
        if role.bypass_role_denies:
            flags.append("bypass_role_denies")
        if role.can_manage:
            flags.append("can_manage")
        if not role.can_prompt:
            flags.append("read-only")
        if not role.bypass_safety:
            tools = _code_tools_of(role)
            if tools:
                flags.append(f"runs code ({', '.join(tools)}): "
                             "best-effort rules")
            else:
                flags.append("no code tools")
            if not role.bypass_role_denies:
                flags.append("writes confined to the session folder")
        extra = f" ({', '.join(flags)})" if flags else ""
        console.print(f"    • {name}{extra}")
    console.print()
    console.print(
        "  Note: the bash patterns are a filter, not a boundary - a role\n"
        "  with Bash can get around them. All scopes share one filesystem\n"
        "  and OS user; for hard isolation between untrusted users, run\n"
        "  separate daemons per OS account. See docs/security.md."
    )


def _daemon_env_state(path) -> str:
    """``"credential"`` when ``daemon.env`` holds a non-empty Claude
    credential, ``"unreadable"`` when it exists but cannot be read, else
    ``"none"`` (missing, empty, or other lines only)."""
    from aipager import daemon_secrets
    from aipager.service import _TOKEN_KEYS

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "none"
    except (OSError, UnicodeDecodeError):
        return "unreadable"
    values = daemon_secrets._parse_env_file(text)
    return "credential" if any(values.get(k) for k in _TOKEN_KEYS) else "none"


def _fix_daemon_credential() -> None:
    """`--fix` step (a): offer to discover/copy a credential into
    daemon.env when it holds none, even when the file exists (`aipager
    service install` always creates it, often empty). Interactive - the
    CLI-only home for this class of question (see the contract's
    non-negotiable: a Telegram-side credential-copy question was dropped
    from scope). The credential itself is never printed."""
    from aipager import daemon_secrets
    from aipager import service as _service
    from aipager.ui import console

    path = daemon_secrets.DAEMON_ENV_PATH
    state = _daemon_env_state(path)
    if state == "credential":
        console.print(f"  [ok]✓[/ok]  {path} already holds a Claude credential - leaving it alone")
        return
    if state == "unreadable":
        console.print(f"  [warn]⚠[/warn]  {path} could not be read - leaving it alone")
        return
    console.print(f"  No Claude credential in {path}.")
    from aipager.errors import require_interactive
    require_interactive()
    answer = input("  Try to discover one automatically? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        console.print("  [muted]skipped[/muted]")
        return
    found = _service.discover_daemon_credential()
    if found is None:
        console.print(
            "  None found (no legacy config.env, and none in your login shell).\n"
            f"  Add a CLAUDE_CODE_OAUTH_TOKEN=... or ANTHROPIC_API_KEY=... line to {path}\n"
            "  yourself (`claude setup-token` prints a token).")
        return
    line, source = found
    key = line.split("=", 1)[0]
    try:
        _service.add_daemon_credential(line, path)
    except (OSError, UnicodeDecodeError) as e:
        console.print(f"  [warn]⚠[/warn]  could not write {path}: {type(e).__name__}")
        return
    console.print(
        f"  [ok]✓[/ok]  added a {key} line from {source} to {path}\n"
        "  Restart aipager to use it: `aipager service stop`, then `aipager service start`.")


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
        console.print(f"  [warn]⚠[/warn]  no claude binary resolves - {e}")
        return
    if not resolved.others:
        console.print("  [ok]✓[/ok]  one claude install found - nothing to disambiguate")
        return

    installs = [resolved.chosen, *resolved.others]
    console.print("  Multiple claude installs found:")
    console.print(f"    0) {installs[0].path} ({installs[0].version}, current pick)")
    for i, install in enumerate(installs[1:], start=1):
        console.print(f"    {i}) {install.path} ({install.version})")
    from aipager.errors import require_interactive
    require_interactive()
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
        console.print("  [warn]⚠[/warn]  not a number - skipped")
        return
    if not (0 <= idx < len(installs)):
        console.print("  [warn]⚠[/warn]  out of range - skipped")
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


def _plain(text: str | None) -> str | None:
    """Rich markup -> plain text for the JSON report (crash details are
    markup-escaped; a few titles and details carry tags)."""
    if text is None:
        return None
    from rich.errors import MarkupError
    from rich.text import Text
    try:
        return Text.from_markup(str(text)).plain
    except MarkupError:
        return str(text)


def _doctor_json(args) -> int:
    """``aipager doctor --json``: one JSON object on stdout, nothing else.
    Anything a check prints goes to stderr."""
    import contextlib

    from aipager import __version__

    out = sys.stdout
    if getattr(args, "fix", False) or getattr(args, "safety_check", False):
        flag = "--fix" if getattr(args, "fix", False) else "--safety-check"
        out.write(json.dumps({
            "command": "doctor", "status": "error", "ok": False,
            "exit_code": 2, "error": "usage",
            "message": f"--json cannot be combined with {flag}.",
            "fix": f"Run `aipager doctor --json` without {flag}.",
        }, indent=2) + "\n")
        return 2
    with contextlib.redirect_stdout(sys.stderr):
        keyed = run_all_keyed()
    checks = [{
        "key": key,
        "status": r.status,
        "title": _plain(r.title),
        "detail": [_plain(d) for d in r.detail],
        "fix": _plain(r.fix),
    } for key, r in keyed]
    summary = {s: sum(1 for c in checks if c["status"] == s)
               for s in (OK, WARN, FAIL)}
    failed = summary[FAIL] > 0
    from aipager.errors import redact_bare_token
    # The JSON goes straight into an agent's context: scrub anything shaped
    # like a bot token (a crash row carries the exception's first line).
    out.write(redact_bare_token(json.dumps({
        "command": "doctor", "version": __version__, "ok": not failed,
        "summary": summary, "checks": checks,
    }, indent=2)) + "\n")
    out.flush()
    return 1 if failed else 0


def cmd_doctor(args: argparse.Namespace | None = None) -> int:
    if getattr(args, "as_json", False):
        return _doctor_json(args)

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
    try:
        from aipager.config import POLICY
        if _unanchored_safety_paths(POLICY):
            console.print()
            _print_unanchored_safety_paths(console, POLICY)
    except Exception:
        pass  # a display extra: never stops the doctor
    if SCOPES and len(SCOPES) > 1:
        console.print()
        console.print(
            "ℹ️  Multiple scopes share one filesystem. Telegram-driven "
            "sessions can't read each other's aipager data, but this is "
            "not a hard multi-tenant sandbox - for mutually untrusted "
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
    "check_team", "check_role_shell_access",
    "check_dtach", "check_claude", "check_claude_auth",
    "check_settings_json",
    "check_hook_scripts", "check_daemon", "check_service_installed",
    "check_service_unit_path",
    "CHECKS",
]

# Silence the unused-import warning for shutil/os in environments
# where ruff is strict.
_ = (os, shutil)
