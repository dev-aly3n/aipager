"""Idempotent first-run setup for Claude Code's user files.

Three things block a fresh container/headless aipager install from
working over Telegram, because the wizard (``aipager config``) is
normally what writes them:

1. ``~/.claude/settings.json`` — without ``skipDangerousModePermissionPrompt``,
   claude shows a "WARNING: Bypass Permissions mode" picker every time
   it's launched with ``--dangerously-skip-permissions`` (which aipager
   uses for sessions started as ``/new !name``). The picker defaults to
   "No, exit" — the user's first prompt over Telegram lands as Enter
   on that, and claude exits before responding.

2. ``~/.claude.json`` — without ``hasTrustDialogAccepted`` for the
   working directory, claude shows a "Do you trust this folder?" picker
   on launch in any new cwd. Same Telegram failure mode.

3. ``~/.claude/settings.json`` ``hooks`` + ``statusLine`` — without the
   ``aipager-hook`` wired into ``UserPromptSubmit``/``Stop``/etc.,
   the daemon never learns each session's transcript path and
   ``claude_session_id``, so ``/resume`` has nothing to resume to and
   safety/policy enforcement (PreToolUse) doesn't run.

Run on every ``aipager start`` because these are all user-state that
the wizard sets when the user accepts the prompts interactively — a
Telegram-only user (containerized friend deploy, SSH-less host) never
sees those prompts, so aipager has to write the acceptance for them.

Best-effort: failures are logged at DEBUG and skipped so a missing
``~/.claude`` directory or non-writable filesystem never blocks daemon
startup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingAuthCheck:
    """Everything :func:`recover_auth_or_notice` needs, captured before
    ``bot.start()`` so the slow part can run after it."""

    claude_path: str
    version: str
    session_env: dict[str, str]


@dataclass(frozen=True)
class ProvenanceInfo:
    """Returned by :func:`bootstrap_claude_settings` on success.

    ``.lines`` is exactly :func:`aipager.claude_resolve.format_provenance`'s
    output — the main "claude: ... auth: ..." line, then zero or more
    "also found: ..." lines. Those lines name absolute paths and versions
    and are for the **local journal only**.

    ``.pending`` is set only when Claude reported itself logged out, and
    carries what :func:`recover_auth_or_notice` needs to go looking for a
    credential that works. Whatever text that eventually produces is
    built by :func:`aipager.claude_resolve.format_auth_notice`, which is
    deliberately machine-free — see its docstring for why it and
    ``.lines`` are not the same string.
    """

    lines: list[str]
    auth_ok: bool
    pending: "PendingAuthCheck | None" = None

    def __post_init__(self) -> None:
        # The old invariant here said a healthy-looking start must carry
        # no pending work. That was exactly backwards once we learned
        # that `claude auth status` reports a credential's *presence*
        # and not its *validity*: a revoked token answers
        # ``{"loggedIn": true}``, so the "healthy" case is precisely the
        # one that still needs checking for real.
        #
        # Replaced with the invariant that actually protects something:
        # a resolved binary ALWAYS carries deferred work, so no future
        # edit can quietly skip validation and reintroduce the silent
        # expired-token hang. ``auth_ok`` survives as the cheap check's
        # opinion, used for the log line and doctor — never as a reason
        # to skip the real one.
        if self.pending is None:
            raise ValueError(
                "every resolved binary must carry a PendingAuthCheck — "
                "the cheap auth-status check cannot see an expired token"
            )


_SETTINGS = Path.home() / ".claude" / "settings.json"
_CLAUDE_JSON = Path.home() / ".claude.json"

# Mirror the wizard's hook surface so containerized deploys get the
# same coverage as `aipager config`. Kept in sync with
# ``aipager.wizard._constants.HOOK_EVENTS`` (the wizard is the canonical
# source for users who run it; this module is the fallback for users who
# don't). Both tuples must list events in the same order. They are kept
# separate rather than consolidated because ``_constants`` imports
# ``questionary`` at module level — pulling it in here would drag
# ``questionary``/``prompt_toolkit`` into every daemon start.
_HOOK_CMD = "aipager-hook"
_STATUSLINE_CMD = "aipager-statusline"
_HOOK_EVENTS = (
    "SessionStart", "SessionEnd", "UserPromptSubmit",
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
    "Notification", "Stop", "StopFailure", "SubagentStart", "SubagentStop",
    "PreCompact", "PostCompact", "MessageDisplay",
)
_TOOL_MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PermissionRequest"}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600 if path.name.startswith(".") else 0o644)
    except OSError:
        pass
    os.replace(tmp, path)


def _ensure_bypass_accepted() -> bool:
    """Return True if the settings file was modified."""
    settings = _load(_SETTINGS)
    if settings.get("skipDangerousModePermissionPrompt") is True:
        return False
    settings["skipDangerousModePermissionPrompt"] = True
    _atomic_write(_SETTINGS, settings)
    return True


def _ensure_workdir_trusted(workdir: str) -> bool:
    """Return True if .claude.json was modified."""
    data = _load(_CLAUDE_JSON)
    projects = data.setdefault("projects", {})
    if not isinstance(projects, dict):
        return False
    entry = projects.get(workdir)
    if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
        return False
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("allowedTools", [])
    entry.setdefault("mcpContextUris", [])
    entry.setdefault("mcpServers", {})
    entry.setdefault("hasClaudeMdExternalIncludesWarningShown", False)
    entry["hasTrustDialogAccepted"] = True
    projects[workdir] = entry
    _atomic_write(_CLAUDE_JSON, data)
    return True


def _resolve(cmd: str) -> str | None:
    """Resolve an aipager helper script to an absolute path.

    Tries PATH, then the bin dir next to the running Python interpreter
    (true for pip / uv tool / pipx / Docker installs). Returns None if
    we can't find it — Claude Code does NOT augment PATH when running
    hook commands, so a bare name silently breaks the hook.
    """
    found = shutil.which(cmd)
    if found:
        return found
    candidate = Path(sys.executable).parent / cmd
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def _has_hook_cmd(entries: list, bare_name: str) -> bool:
    """Detect whether the aipager hook (or a user's wrapper around it)
    is already wired for this event.

    Matches any of:
    - Command is literally ``bare_name`` (e.g. ``aipager-hook``).
    - Command's basename starts with ``bare_name`` — catches wrapper
      scripts like ``aipager-hook-capped.sh`` /
      ``aipager-hook.wrapped`` that users deploy for rate-limits,
      memory caps, logging, etc. Documented convention: name your
      wrapper ``aipager-hook*`` and aipager will honor it instead
      of injecting a duplicate entry.
    """
    if not bare_name:
        return False
    for block in entries:
        for hook in (block or {}).get("hooks", []):
            cmd = (hook or {}).get("command", "") or ""
            if not cmd:
                continue
            if cmd == bare_name:
                return True
            basename = Path(cmd).name
            if basename.startswith(bare_name):
                return True
    return False


def _ensure_hooks_and_statusline() -> bool:
    """Wire aipager-hook into every hook event + statusLine. Idempotent."""
    hook_path = _resolve(_HOOK_CMD)
    statusline_path = _resolve(_STATUSLINE_CMD)
    if not hook_path:
        log.debug("claude bootstrap: %s not on PATH; skipping hook wiring", _HOOK_CMD)
        return False

    settings = _load(_SETTINGS)
    changed = False

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks
        changed = True
    entry = {"type": "command", "command": hook_path}
    for event in _HOOK_EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
            hooks[event] = entries
            changed = True
        if _has_hook_cmd(entries, _HOOK_CMD):
            continue
        if event in _TOOL_MATCHER_EVENTS:
            entries.append({"matcher": "*", "hooks": [entry]})
        else:
            entries.append({"hooks": [entry]})
        changed = True

    # statusLine — keep an existing working entry; otherwise install ours.
    existing_sl = settings.get("statusLine") or {}
    existing_cmd = (existing_sl.get("command", "")
                    if isinstance(existing_sl, dict) else "")
    sl_good = bool(
        existing_cmd and (
            shutil.which(existing_cmd)
            or (os.path.isabs(existing_cmd)
                and os.path.exists(existing_cmd)
                and os.access(existing_cmd, os.X_OK))
        )
    )
    if not sl_good and statusline_path:
        settings["statusLine"] = {"type": "command", "command": statusline_path}
        changed = True

    if changed:
        _atomic_write(_SETTINGS, settings)
    return changed


def bootstrap_claude_settings(workdir: str | None = None) -> ProvenanceInfo | None:
    """Write the acceptance flags + hooks that claude-code's wizard
    would normally configure interactively, then resolve the claude
    binary + auth shape for the startup provenance line. Idempotent;
    safe to call on every daemon start. Never raises.

    ``workdir`` defaults to the daemon's cwd (which is also the default
    cwd for spawned sessions — see ``dtach/inject.py:_PROJECT_DIR``).

    Returns ``None`` if binary resolution failed (nothing to report);
    otherwise a :class:`ProvenanceInfo` whose ``.lines`` is exactly
    :func:`aipager.claude_resolve.format_provenance`'s output. One log
    line is emitted per line, at the point resolution happens — this
    function is called once per daemon lifetime, before ``bot.start()``,
    so it never sends Telegram itself; the caller (``cli/daemon.py``)
    does that after the bot is up.
    """
    if workdir is None:
        workdir = os.environ.get("AIPAGER_WORK_DIR", os.getcwd())
    try:
        if _ensure_bypass_accepted():
            log.info("claude bootstrap: set skipDangerousModePermissionPrompt=true in %s", _SETTINGS)
    except Exception:
        log.debug("claude bootstrap: failed to patch settings.json", exc_info=True)
    try:
        if _ensure_workdir_trusted(workdir):
            log.info("claude bootstrap: trusted %s in %s", workdir, _CLAUDE_JSON)
    except Exception:
        log.debug("claude bootstrap: failed to patch .claude.json", exc_info=True)
    try:
        if _ensure_hooks_and_statusline():
            log.info("claude bootstrap: wired %s hooks + statusLine into %s",
                     _HOOK_CMD, _SETTINGS)
    except Exception:
        log.debug("claude bootstrap: failed to wire hooks", exc_info=True)

    from aipager import claude_resolve, daemon_secrets

    try:
        resolved = claude_resolve.resolve_claude_binary()
    except claude_resolve.ClaudeNotFoundError as e:
        log.warning("claude bootstrap: could not resolve a claude binary: %s", e)
        return None

    try:
        # Auth is probed exactly here — once per daemon lifecycle — using
        # the SAME environment a real session launch gets
        # (daemon_secrets.build_session_env()), never the daemon's own
        # bare os.environ. Under systemd's LoadCredential=, the token
        # never enters the daemon's own environ, so probing against it
        # would falsely report "not logged in" while sessions
        # authenticate fine. See claude_resolve's module docstring.
        session_env = daemon_secrets.build_session_env()
        auth = claude_resolve.detect_auth(
            resolved.chosen.path, resolved.chosen.version, session_env,
        )
        lines = claude_resolve.format_provenance(resolved, auth)
    except Exception:
        log.debug("claude bootstrap: provenance formatting failed", exc_info=True)
        return None

    for line in lines:
        log.info("claude bootstrap: %s", line)

    if auth.source in ("probe-failed", "version-gated"):
        log.info("claude bootstrap: cheap auth check undetermined (%s)",
                 auth.source)

    # Deferred unconditionally — including when auth status claims we are
    # logged in, which is exactly the case it gets wrong. The real
    # validation costs a network round-trip and the recovery sweep can
    # spend ~20s in blocking subprocesses, and this function runs BEFORE
    # bot.start(); doing either here would delay the daemon reaching
    # Telegram in precisely the situation where remote control matters
    # most. recover_auth_or_notice() does the work, off the startup path,
    # once the bot is already live.
    return ProvenanceInfo(
        lines=lines, auth_ok=auth.logged_in,
        pending=PendingAuthCheck(
            claude_path=resolved.chosen.path,
            version=resolved.chosen.version,
            session_env=session_env,
        ),
    )


def recover_auth_or_notice(pending: PendingAuthCheck) -> str | None:
    """Run the recovery sweep; return the Telegram text, or ``None``.

    Blocking (subprocesses) — call it via ``asyncio.to_thread`` so it
    never stalls the event loop. ``None`` means a working credential was
    found and the operator needs to hear nothing. Never raises except
    ``AssertionError``, which only the test-suite safety net produces.
    """
    from aipager import claude_resolve

    check = claude_resolve.validate_credential(
        pending.claude_path, pending.session_env)

    if check.state == "valid":
        return None
    if check.state == "unknown":
        # Offline, a timeout, or something Claude said that we do not
        # recognise. We have not learned that the credential is bad, only
        # that we could not tell — and warning on every restart behind a
        # flaky network would train the operator to ignore this message.
        log.info("claude bootstrap: credential validation inconclusive (%s); "
                 "staying quiet", check.detail)
        return None

    log.warning("claude bootstrap: credential did not work (%s)", check.state)
    recovered, found_kinds = _recover_auth(
        pending.claude_path, pending.version, pending.session_env,
    )
    if recovered:
        log.info("claude bootstrap: recovered a working credential; staying quiet")
        return None
    return claude_resolve.format_auth_notice(
        found_kinds, rejected=check.state == "rejected")


# Mirrors claude_resolve._ENV_AUTH_VARS, which is what attributes a
# *working* auth to "env". Reporting a narrower set here would tell a
# Bedrock/Vertex operator "couldn't find a credential anywhere" while a
# perfectly real, credential-shaped variable was sitting in the
# environment being rejected.
_ENV_KINDS: tuple[tuple[str, str], ...] = (
    ("CLAUDE_CODE_OAUTH_TOKEN", "setup token"),
    ("ANTHROPIC_AUTH_TOKEN", "setup token"),
    ("ANTHROPIC_API_KEY", "API key"),
    ("AWS_BEARER_TOKEN_BEDROCK", "Bedrock token"),
    ("CLAUDE_CODE_USE_BEDROCK", "Bedrock setting"),
)
_AUTH_ENV_KEYS = frozenset(k for k, _ in _ENV_KINDS)
_BOOLEAN_AUTH_VARS = frozenset({"CLAUDE_CODE_USE_BEDROCK"})


def _kinds_in(env: dict[str, str]) -> list[str]:
    """Human-readable credential kinds present in *env*. Never values."""
    kinds: list[str] = []
    for var, kind in _ENV_KINDS:
        value = (env.get(var) or "").strip()
        if not value:
            continue
        # Only the Bedrock switch is a boolean; "0"/"false"/"no" there
        # means deliberately OFF, and reporting it as a credential would
        # send the operator to fix something they turned off on purpose.
        # The others are opaque secrets — a token whose literal value is
        # "0" is still a token, and second-guessing its content here
        # would be wrong.
        if var in _BOOLEAN_AUTH_VARS and value.lower() in ("0", "false", "no"):
            continue
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def _recover_auth(
    claude_path: str, version: str, failing_env: dict[str, str],
) -> tuple[bool, list[str]]:
    """Look for a credential that actually authenticates. Never raises.

    Returns ``(recovered, found_kinds)``. Strictly **read-only**: it
    probes candidates and, on success, records the winning overlay in
    memory via :func:`aipager.daemon_secrets.set_recovered_credential`
    so real session launches get it too. It never writes, renames, or
    repairs a credential file — in particular it must never reach for
    ``inject._stash_expired_credentials_file``, which renames the user's
    own credentials file and exists only as legacy compensation for a
    Claude Code bug fixed in 2.1.225.

    *failing_env* is the environment that was just probed and rejected,
    so anything already in it is recorded as "found" but not re-probed.
    """
    from aipager import claude_resolve, daemon_secrets

    found: list[str] = list(_kinds_in(failing_env))

    def _remember(kind: str) -> None:
        if kind not in found:
            found.append(kind)

    # A stored login is claude's own credentials file. Claude reads it
    # itself, so there is no overlay to try — its presence is only worth
    # reporting, and only as a kind.
    try:
        if (Path.home() / ".claude" / ".credentials.json").exists():
            _remember("stored login")
    except OSError:
        pass

    candidates: list[tuple[str, dict[str, str]]] = []

    # daemon.env read directly. Under systemd, $CREDENTIALS_DIRECTORY
    # shadows it entirely, so a stale credential materialised into the
    # unit's private directory can lose to a good one sitting in the
    # config file the operator actually edits.
    try:
        direct = daemon_secrets._parse_env_file(
            daemon_secrets.DAEMON_ENV_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        direct = {}
    if direct:
        for kind in _kinds_in(direct):
            _remember(kind)
        # Only an *auth* key differing is worth a fresh ~5s probe. An
        # unrelated KEY=VALUE line in daemon.env changing nothing about
        # the credential would otherwise buy a redundant re-probe of a
        # combination already known to fail.
        if any(direct.get(k) != failing_env.get(k)
               for k in direct if k in _AUTH_ENV_KEYS):
            candidates.append(("aipager config", direct))

    # The login shell. This is the shape the whole runtime-environment
    # work exists for: a token exported from ~/.bashrc, invisible to a
    # systemd unit because units never source shell startup files.
    try:
        from aipager.service import _discover_token_via_login_shell
        shell_token = _discover_token_via_login_shell()
    except AssertionError:
        # tests/conftest.py's _no_real_login_shell_probe raises this on
        # purpose, so a test that reaches a real login-shell spawn fails
        # loudly. Swallowing it here would quietly disarm that safety
        # net. Production never raises AssertionError from this call, so
        # re-raising costs the daemon nothing.
        raise
    except Exception:
        shell_token = None
    if shell_token:
        _remember("setup token")
        if shell_token != failing_env.get("CLAUDE_CODE_OAUTH_TOKEN"):
            candidates.append(
                ("login shell", {"CLAUDE_CODE_OAUTH_TOKEN": shell_token}))

    for origin, overlay in candidates:
        try:
            probe_env = dict(failing_env)
            probe_env.update(overlay)
            # validate_credential, NOT detect_auth: the latter only sees
            # that a token exists, so the sweep would cheerfully
            # "recover" onto a second dead credential and go quiet.
            status = claude_resolve.validate_credential(claude_path, probe_env)
        except AssertionError:
            # Same carve-out as the login-shell call above: the suite's
            # _no_real_credential_probe guard raises this so an unmocked
            # test fails loudly instead of quietly spending the
            # operator's money. `except Exception` swallowed it, which
            # disarmed the guard on this path exactly as it would have
            # on that one.
            raise
        except Exception:
            continue
        if status.state == "valid":
            log.info("claude bootstrap: credential from %s authenticates; "
                     "using it for sessions", origin)
            try:
                daemon_secrets.set_recovered_credential(overlay)
            except Exception:
                log.debug("claude bootstrap: could not record the recovered "
                          "credential", exc_info=True)
                return False, found
            return True, found

    return False, found
