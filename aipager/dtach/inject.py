"""Async dtach wrappers — inject keystrokes and check session liveness.

Uses `dtach -p <socket>` to send raw bytes to the session's PTY via stdin.

Socket naming: session "claude-dev" → /tmp/claude-dtach-dev.sock
"""

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _credentials_file_is_fresh() -> bool:
    """Return True iff ~/.claude/.credentials.json holds an unexpired token.

    Used by launch_session to decide whether to strip
    ``CLAUDE_CODE_OAUTH_TOKEN`` from the environment of spawned claude
    sessions. Two deployment shapes need different behaviour:

    - **Interactive login** (`claude auth login`): the credentials
      file is written and refreshed by Claude Code. A leftover
      ``CLAUDE_CODE_OAUTH_TOKEN`` from an earlier setup-token now
      overrides those fresh credentials for the whole process tree
      and kills each session on first API call. Strip it.

    - **Headless / setup-token** (`claude setup-token` +
      ``export CLAUDE_CODE_OAUTH_TOKEN=…`` in profile): there is no
      credentials file, or the one on disk is stale — the env var
      IS the credential. Stripping it kills the only working auth.
      Keep it.

    Fail-open: any exception path (missing file, permission error,
    malformed JSON, unexpected schema, wrong type on ``expiresAt``,
    …) returns False so we keep the env token. A false negative
    reintroduces the original stale-env-pin bug for interactive
    users, but only when their credentials file is unreadable —
    which is itself a broken state where they'd need to re-login
    anyway.
    """
    path = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(path.read_text())
        expires_at = data["claudeAiOauth"]["expiresAt"]
        # Claude Code stores expiresAt as unix milliseconds.
        return float(expires_at) / 1000.0 > time.time()
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _credentials_file_has_token() -> bool:
    """Return True iff ~/.claude/.credentials.json holds a real,
    non-empty ``claudeAiOauth.accessToken``.

    Guards :func:`_stash_expired_credentials_file` against renaming
    files that only hold account-level metadata (empty tokens,
    ``expiresAt=0``) — observed on Max-plan containers where Claude
    Code manages auth via a non-file path (device token / account UUID
    / server-side session). Such files LOOK expired to
    :func:`_credentials_file_is_fresh` but are actually load-bearing
    for those setups. Renaming them silently breaks auth.

    Fail-open (returns False on any exception path) so we err toward
    not-stashing: false-negative merely reproduces the pre-0.4.18
    behavior (401 on interactive if env token is shadowed), while
    false-positive would DELETE a working config.
    """
    path = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(path.read_text())
        token = data["claudeAiOauth"]["accessToken"]
        return isinstance(token, str) and bool(token)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _credentials_file_is_dead_placeholder() -> bool:
    """Return True iff ~/.claude/.credentials.json has BOTH
    ``claudeAiOauth.accessToken`` and ``claudeAiOauth.refreshToken`` as
    empty strings.

    Observed on containers where a Max-plan account's credentials file
    was cleared (both token strings blanked, only account metadata
    remaining) but the file itself wasn't removed. Claude Code sees the
    file, tries to refresh via the empty refresh token, and fails with
    ``OAuth session expired and could not be refreshed`` — even when a
    valid ``CLAUDE_CODE_OAUTH_TOKEN`` env var is present, because the
    file's presence shadows the env token in interactive mode.

    Fail-open (returns False on any exception path OR any of {missing
    file, malformed JSON, missing keys, non-string tokens, either token
    non-empty}) — refresh-token-only files stay put so Claude Code's
    internal refresh path can try. Only the "both empty" case is
    definitively dead-on-arrival.
    """
    path = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(path.read_text())
        oauth = data["claudeAiOauth"]
        access = oauth["accessToken"]
        refresh = oauth["refreshToken"]
        return (isinstance(access, str) and access == ""
                and isinstance(refresh, str) and refresh == "")
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _stash_expired_credentials_file() -> Path | None:
    """Rename an expired ~/.claude/.credentials.json aside so claude
    falls back to CLAUDE_CODE_OAUTH_TOKEN.

    Claude Code's INTERACTIVE mode prefers the credentials file over
    the env token even when the file's ``expiresAt`` is in the past,
    yielding a 401 that shadows a perfectly-valid env token. (``claude
    -p`` uses a different code path and reads env first, which is why
    it authenticates fine while an interactive session does not.)

    Only triggered when ``CLAUDE_CODE_OAUTH_TOKEN`` is set and the
    credentials file is definitely dead — one of:

    (a) **Traditional expired**: non-empty ``accessToken`` whose
        ``expiresAt`` is in the past (the 0.4.18 case).
    (b) **Dead placeholder**: BOTH ``accessToken`` and ``refreshToken``
        are empty strings — no token material to authenticate with, no
        refresh path (observed on cleared Max-plan files that would
        otherwise sit shadowing the env token forever).

    Refresh-token-only files (empty ``accessToken`` but non-empty
    ``refreshToken``) are left alone: Claude Code may still refresh
    successfully, and aipager doesn't do live API validation.

    Returns the stash path on success, ``None`` otherwise. Idempotent:
    a follow-up call with no file present is a no-op. Reversible: the
    user can ``mv`` the ``.stale`` file back if they later refresh
    credentials via ``claude auth login``. Never raises — a file-op
    failure just returns ``None`` and the existing token-strip logic
    handles it as best it can.
    """
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return None
    creds = Path.home() / ".claude" / ".credentials.json"
    if not creds.exists():
        return None
    dead_placeholder = _credentials_file_is_dead_placeholder()
    traditionally_expired = (
        _credentials_file_has_token() and not _credentials_file_is_fresh()
    )
    if not (dead_placeholder or traditionally_expired):
        return None
    stash = creds.with_suffix(creds.suffix + ".stale")
    try:
        creds.replace(stash)  # atomic overwrite of any prior .stale
        return stash
    except OSError as e:
        log.warning(
            "could not stash expired credentials file (%s → %s): %s — "
            "interactive claude may 401 on this session",
            creds, stash.name, e,
        )
        return None


SOCK_PREFIX = "/tmp/claude-dtach-"


def _resolve_dtach() -> str:
    """Return an absolute path to the `dtach` binary.

    Prefer the bundled binary shipped by `dtach-bin` (correct in pipx /
    uv-tool / brew-venv layouts where the venv's bin/ isn't on PATH),
    fall back to a PATH lookup for users who installed dtach via brew
    or apt.
    """
    try:
        from dtach_bin import path
        return path()
    except (ImportError, FileNotFoundError):
        pass
    return shutil.which("dtach") or "dtach"


_DTACH = _resolve_dtach()

# Logical key names → ANSI escape sequences
KEYS = {
    "Enter": "\r",
    "Down": "\x1b[B",
    "Up": "\x1b[A",
    "Right": "\x1b[C",
    "Left": "\x1b[D",
    "Tab": "\t",
    "Escape": "\x1b",
}


async def _run(args: list[str], stdin: bytes = b"",
               timeout: float = 5) -> tuple[bool, str]:
    """Run subprocess, optionally piping stdin, return (success, stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin or None), timeout=timeout,
        )
        if proc.returncode == 0:
            return True, stdout.decode()
        log.error("dtach cmd failed: %s — %s", args, stderr.decode().strip())
        return False, ""
    except asyncio.TimeoutError:
        log.error("dtach cmd timed out: %s", args)
        return False, ""
    except FileNotFoundError:
        log.error("dtach not found")
        return False, ""


def _sock_path(session: str) -> str:
    """Convert session name 'claude-dev' to socket path '/tmp/claude-dtach-dev.sock'."""
    name = session.removeprefix("claude-")
    return f"{SOCK_PREFIX}{name}.sock"


async def send_keys(session: str, keys: str) -> bool:
    """Send a key sequence to the dtach session.

    `keys` can be a logical name ("Enter", "Down") or raw text.
    """
    seq = KEYS.get(keys, keys)
    sock = _sock_path(session)
    ok, _ = await _run([_DTACH, "-p", sock], stdin=seq.encode())
    if ok:
        log.info("Sent keys %r → %s", keys, session)
    return ok


async def send_text_and_enter(session: str, text: str) -> bool:
    """Send literal text followed by Enter.

    Text and Enter must be separate dtach -p calls — Claude Code's TUI
    treats a single chunk (text + CR) as all-text input. A separate CR
    write is needed to trigger the submit keypress event.
    """
    sock = _sock_path(session)
    ok, _ = await _run([_DTACH, "-p", sock], stdin=text.encode())
    if not ok:
        return False
    # Claude Code's Ink TUI needs time to process text input before
    # Enter is recognized as "submit". Too short → \r is swallowed.
    # Scale with text length: longer text = more rendering time needed.
    delay = max(0.15, min(0.5, len(text) * 0.003))
    await asyncio.sleep(delay)
    ok, _ = await _run([_DTACH, "-p", sock], stdin=b"\r")
    if ok:
        log.info("Sent text %r + Enter → %s", text[:50], session)
    return ok


async def kill_session(session: str) -> bool:
    """Kill a dtach session by finding its host PID and terminating it."""
    sock = _sock_path(session)
    sock_path = Path(sock)
    if not sock_path.is_socket():
        return False

    # Find the dtach host process (dtach -n <sock> ...)
    try:
        proc = await asyncio.create_subprocess_exec(
            "fuser", sock,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        pids = stdout.decode().split()
        for pid_str in pids:
            pid_str = pid_str.strip()
            if pid_str.isdigit():
                import os
                import signal
                os.kill(int(pid_str), signal.SIGTERM)
                log.info("Killed dtach PID %s for %s", pid_str, session)
    except Exception:
        log.warning("Failed to find/kill dtach PID for %s", session, exc_info=True)

    # Remove socket as fallback (dtach should clean up, but ensure it)
    try:
        sock_path.unlink(missing_ok=True)
    except OSError:
        pass
    return True


async def is_alive(session: str) -> bool:
    """Check if a dtach session socket exists and is connectable."""
    sock = _sock_path(session)
    return Path(sock).is_socket()


_VALID_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_RESERVED = {"status", "stop", "kill", "new", "help", "start", "settings"}
_PROJECT_DIR = os.environ.get("AIPAGER_WORK_DIR", os.getcwd())
_CLAUDE_BIN = shutil.which("claude") or "claude"


async def launch_session(
    name: str,
    skip_perms: bool = False,
    *,
    resume_id: str | None = None,
    cwd: str | None = None,
    system_prompt_extra: str | None = None,
) -> tuple[bool, str]:
    """Launch a new Claude Code session inside dtach.

    Returns (success, error_message). The session_monitor will auto-discover
    the new session within 2 seconds.

    Pass ``resume_id`` to invoke ``claude --resume <id>`` so the new
    session inherits the conversation history of a previous one. The
    resume id is what Claude Code stores as the JSONL filename
    (``Path(transcript_path).stem``). Pass ``cwd`` to launch from a
    specific directory — required for resume because Claude organizes
    transcripts by encoded cwd. Both default to None (fresh session,
    daemon's working dir).
    """
    if not name or not _VALID_NAME.match(name):
        return False, "Invalid name (use letters, numbers, hyphens)"
    if name.lower() in _RESERVED:
        return False, f"'{name}' is a reserved command name"
    # The internal name may carry a scope disambiguator suffix
    # (e.g. "jim__d256113222"), so the cap is generous; the
    # user-facing label is validated separately at the /new layer.
    if len(name) > 64:
        return False, "Name too long (max 64 chars)"

    sock = f"{SOCK_PREFIX}{name}.sock"
    if Path(sock).is_socket():
        return False, f"Session '{name}' already exists"

    launch_cwd = cwd or _PROJECT_DIR
    if cwd and not Path(cwd).is_dir():
        return False, f"original project dir is gone: {cwd}"

    # Build the bash -c command — wraps claude with env vars and prompt
    perms = "--dangerously-skip-permissions" if skip_perms else ""
    resume = f"--resume {shlex.quote(resume_id)}" if resume_id else ""
    sys_prompt = (f'Your session name is "{name}". '
                  f'When users address you by this name, respond naturally '
                  f'-- it is your name in this session.')
    if system_prompt_extra:
        # SESSION.md roster + rules (Phase D) appended so claude knows
        # who can address it and what's blocked from Telegram.
        sys_prompt = f"{sys_prompt}\n\n{system_prompt_extra}"
    # `unset CLAUDECODE`: Claude Code sets this env var when running, and
    # the binary refuses to launch a second time if it sees it ("already
    # inside a Claude Code session"). Strip it so /new sessions can launch
    # cleanly from inside a parent Claude.
    #
    # `unset CLAUDE_CODE_OAUTH_TOKEN`: only stripped when a fresh
    # ~/.claude/.credentials.json is on disk. See
    # _credentials_file_is_fresh() for the rationale — briefly:
    # stripping unbreaks interactive users who did `claude auth login`
    # (fresh credentials, stale env token) but breaks headless users
    # who deployed with `claude setup-token` (env token is the only
    # credential). The daemon inherits its environment from whatever
    # started it, so headless setups need the token exported in the
    # process that launches aipager (systemd unit, docker run -e, or
    # the shell that runs `aipager start`).
    # If the credentials file is present but expired and we DO have an
    # env token available, move the file aside — Claude Code's
    # interactive mode otherwise picks the expired file over the env
    # token and 401s on first API call. See
    # _stash_expired_credentials_file() for the full rationale.
    stashed = _stash_expired_credentials_file()
    if stashed is not None:
        log.info("[%s] stashed expired credentials.json → %s "
                 "(env token will be used instead)", name, stashed.name)
    unset_token = ("unset CLAUDE_CODE_OAUTH_TOKEN; "
                   if _credentials_file_is_fresh() else "")
    log.info(
        "[%s] launch: %s CLAUDE_CODE_OAUTH_TOKEN (credentials file %s)",
        name,
        "stripping" if unset_token else "keeping",
        "fresh" if unset_token else "missing/expired",
    )
    bash_cmd = (
        f"unset CLAUDECODE; "
        f"{unset_token}"
        f"export CLAUDE_DTACH_SESSION=claude-{name}; "
        f"{_CLAUDE_BIN} {perms} {resume} "
        f"--append-system-prompt {shlex.quote(sys_prompt)}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            _DTACH, "-n", sock, "-Ez", "bash", "-c", bash_cmd,
            cwd=launch_cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return False, f"dtach failed: {stderr.decode().strip()}"
    except FileNotFoundError:
        return False, "dtach not installed"
    except asyncio.TimeoutError:
        return False, "dtach launch timed out"

    # Wait for socket to appear (dtach creates it asynchronously)
    for _ in range(10):
        await asyncio.sleep(0.3)
        if Path(sock).is_socket():
            log.info("Launched session claude-%s (socket: %s)", name, sock)
            return True, ""
    return False, "Socket never appeared after launch"


async def list_sessions() -> list[str]:
    """Return names of all active claude-dtach sessions.

    Scans /tmp for claude-dtach-*.sock files that are Unix sockets.
    """
    results = []
    for sock_file in Path("/tmp").glob("claude-dtach-*.sock"):
        if not sock_file.is_socket():
            continue
        name = "claude-" + sock_file.stem.removeprefix("claude-dtach-")
        results.append(name)
    return results
