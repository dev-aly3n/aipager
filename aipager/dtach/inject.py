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
import signal
import time
from pathlib import Path

from aipager import claude_resolve, daemon_secrets

log = logging.getLogger(__name__)

# How long a signalled dtach gets to exit before the signal is escalated,
# and how often its liveness is rechecked. A clean dtach shutdown is a few
# milliseconds; the ceiling only matters for a wedged one.
_KILL_TIMEOUT: float = 3.0
_KILL_POLL_INTERVAL: float = 0.02


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


def _proc_socket_pids(sock: str) -> list[int]:
    """PIDs of dtach processes holding ``sock``, via a /proc scan.

    Only matches processes whose argv[0] is a dtach binary, so an
    unrelated process that merely mentions the socket path on its
    command line is never signalled.
    """
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return []
    needle = sock.encode()
    pids: list[int] = []
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue  # process exited mid-scan, or not ours to read
        if not argv or b"dtach" not in os.path.basename(argv[0]):
            continue
        if any(needle in arg for arg in argv[1:]):
            pids.append(pid)
    return pids


async def _fuser_socket_pids(sock: str) -> list[int]:
    """PIDs holding ``sock`` according to ``fuser``.

    Fallback for platforms without /proc. ``fuser`` is not declared as
    a dependency anywhere and is absent from slim container images, so
    a missing binary is an expected outcome, not an error.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "fuser", sock,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception:
        log.warning("fuser lookup failed for %s", sock, exc_info=True)
        return []
    return [int(tok) for tok in stdout.decode().split() if tok.strip().isdigit()]


def _pid_alive(pid: int) -> bool:
    """True while *pid* still exists. Signal 0 checks without delivering."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours to signal
    return True


async def _await_exit(pids: list[int], session: str) -> None:
    """Block until every pid in *pids* is gone, escalating if it lingers.

    SIGTERM is a request, not an event: dtach removes its own socket as it
    shuts down, so returning while it is still dying lets it delete the
    socket of whatever session is created next. `/perms` relaunches within
    milliseconds, and that is exactly what happened — the restarted session
    was reported alive, then vanished two seconds later when the corpse of
    its predecessor finished cleaning up.
    """
    deadline = time.monotonic() + _KILL_TIMEOUT
    escalated = False
    while True:
        alive = [pid for pid in pids if _pid_alive(pid)]
        if not alive:
            return
        if not escalated and time.monotonic() >= deadline:
            # Ignoring SIGTERM for this long means it is not coming down
            # politely. SIGKILL cannot be caught, so the socket is ours to
            # reclaim right after.
            escalated = True
            for pid in alive:
                try:
                    os.kill(pid, signal.SIGKILL)
                    log.warning("SIGKILLed dtach PID %s for %s after %.0fs",
                                pid, session, _KILL_TIMEOUT)
                except OSError:
                    pass
            deadline = time.monotonic() + _KILL_TIMEOUT
        elif escalated and time.monotonic() >= deadline:
            # Unkillable (uninterruptible sleep, or not ours). Give up
            # waiting rather than hang the caller forever.
            log.warning("PIDs %s for %s outlived SIGKILL — proceeding",
                        alive, session)
            return
        await asyncio.sleep(_KILL_POLL_INTERVAL)


async def kill_session(session: str) -> bool:
    """Kill a dtach session by finding its host PID and terminating it.

    Waits for the process to actually exit before unlinking, so a caller
    that relaunches immediately (``/perms``) cannot have its new socket
    deleted by the previous session's shutdown.

    Returns False — leaving the socket in place — when no process could
    be signalled. The socket is the only handle aipager has on a running
    session: unlinking it after a failed kill strands a live claude that
    the monitor then reports as gone, and the orphan later deletes the
    socket of whatever session has since taken its place.
    """
    sock = _sock_path(session)
    sock_path = Path(sock)
    if not sock_path.is_socket():
        return False

    pids = _proc_socket_pids(sock) or await _fuser_socket_pids(sock)

    signalled: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            log.warning("Could not SIGTERM PID %s for %s", pid, session,
                        exc_info=True)
            continue
        signalled.append(pid)
        log.info("Killed dtach PID %s for %s", pid, session)

    if not signalled:
        log.warning("No dtach process signalled for %s — keeping socket %s",
                    session, sock)
        return False

    await _await_exit(signalled, session)

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
# Names a session may not take, because something else already answers to
# them. ONE canonical set, deliberately: there used to be a second list in
# `dtach/launcher.py` covering only the CLI's own verbs, and the two drifted
# apart twice — `app`/`clearqueue`/`perms`/`resume`/`whoami` shipped as bot
# commands without ever being reserved, while `ls`/`list` were reserved by the
# CLI and not by chat. Either way round the result is the same: a session
# named after a command shadows it on one surface, or is unreachable on the
# other.
#
# Lives HERE, in the low-level module, so `dtach/launcher.py` and
# `miniapp/launch.py` can both import it — `dtach` must never import from
# `bot`. `tests/test_reserved_name_reconciliation.py` fails if a bot command
# is ever registered without appearing below, which is what stops this from
# drifting a third time; adding a command means adding it here too.
_RESERVED = {
    # Telegram bot commands (see bot/lifecycle.py::_command_list)
    "status", "stop", "kill", "new", "help", "start", "settings",
    "restart", "rename", "delete", "diff",
    "app", "clearqueue", "perms", "resume", "whoami",
    # `aipager session` subcommand verbs (see cli/session.py) — these are
    # matched before the name is treated as a session at all.
    "ls", "list",
}


def normalize_session_name(name: str) -> str:
    """The canonical spelling of a session name the user just typed.

    Lowercased, with ``-`` mapped to ``_``. Both changes exist for one
    reason: a Telegram bot command must match ``[a-z0-9_]{1,32}``, while
    ``_VALID_NAME`` below deliberately allows uppercase, hyphens and up
    to 64 characters. That mismatch meant a perfectly legal session name
    could be an illegal command — which, before the ``_label_command``
    filter, took a whole chat's command menu down with it. Normalising on
    the way in removes the mismatch at source instead of compensating for
    it afterwards.

    Applied at the INPUT layer only — where a human's typing enters the
    system — never inside :func:`launch_session`. By the time a name
    reaches the launcher it is the internal, scope-suffixed one, and its
    caller has already recorded ``sess.label`` separately; rewriting it
    there would leave the socket path and the registry entry disagreeing
    about what the session is called. The launcher also has to stay
    permissive so sessions created before this rule, and anything
    ``cli/resume.py`` re-launches, still start.

    Length is deliberately NOT clamped. Commands cap at 32 characters and
    names at 64, but truncating would make two different long names
    collide; an over-long name keeps working and simply gets no
    ``/shortcut`` (see ``lifecycle._label_command``).
    """
    return name.strip().lower().replace("-", "_")


_PROJECT_DIR = os.environ.get("AIPAGER_WORK_DIR", os.getcwd())
# Resolved lazily inside launch_session() — NOT at import time. Import-time
# resolution would spawn a `--version` subprocess for every unrelated CLI
# that transitively imports this module, and would be unmockable before
# import. See aipager.claude_resolve for the shared six-call-site resolver.


def _conversation_exists(session_id: str) -> bool:
    """True when Claude Code has a transcript it could resume for *session_id*.

    Transcripts live at ``~/.claude/projects/<cwd-slug>/<session-id>.jsonl``.
    The slug depends on the directory the session ran in, which the caller
    does not always know, so match on the id across every project — a session
    id is a UUID, so a hit in the wrong project is not a realistic collision.
    Returns False if the lookup itself fails: dropping the resume costs a
    session its history, but keeping it risks the session exiting on launch,
    and a live session with no history beats a dead one.
    """
    if not session_id:
        return False
    try:
        projects = Path.home() / ".claude" / "projects"
        return any(projects.glob(f"*/{session_id}.jsonl"))
    except OSError:
        log.debug("conversation lookup failed for %s", session_id, exc_info=True)
        return False


async def launch_session(
    name: str,
    skip_perms: bool = False,
    *,
    resume_id: str | None = None,
    cwd: str | None = None,
    system_prompt_extra: str | None = None,
    model: str | None = None,
    is_relaunch: bool = False,
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
    # Creation only. Every relaunch of an ALREADY-EXISTING session comes
    # through here too — /restart, /perms, /resume, and the replace-on-name-
    # conflict flow all kill and re-launch under the session's own name — so
    # gating them on the reserved set would retroactively strand any session
    # created before a word joined it. That is not hypothetical: `ls` became
    # reserved after a live `claude-ls` session already existed, and its next
    # /restart would have failed. Names are validated where a human types
    # them (bot/handlers.py, miniapp/launch.py, dtach/launcher.py); this stays
    # as the creation-time backstop for a caller that forgets.
    if not is_relaunch and name.lower() in _RESERVED:
        return False, f"'{name}' is a reserved command name"
    # The internal name may carry a scope disambiguator suffix
    # (e.g. "jim__d256113222"), so the cap is generous; the
    # user-facing label is validated separately at the /new layer.
    if len(name) > 64:
        return False, "Name too long (max 64 chars)"
    # `shlex.quote` below makes the model shell-safe, but it cannot make
    # it argv-safe: a value starting with `-` is quoted to itself and
    # then read by claude's own parser as another FLAG rather than as
    # this flag's value. `--model --dangerously-skip-permissions` is a
    # permission bypass, not a typo. The Mini App validates this far more
    # strictly before it gets here; this is the layer that holds if a
    # future caller forgets to.
    if model and model.startswith("-"):
        return False, "Invalid model name"

    sock = f"{SOCK_PREFIX}{name}.sock"
    if Path(sock).is_socket():
        return False, f"Session '{name}' already exists"

    launch_cwd = cwd or _PROJECT_DIR
    if cwd and not Path(cwd).is_dir():
        # The path goes to the journal, not into the returned string:
        # this string is rendered straight into a Telegram message, and
        # in a group scope every member of that group would see the
        # operator's absolute project path — hence their home directory
        # and OS username. Same rule as the startup auth notice.
        log.warning("launch refused for %s: original project dir is gone: %s",
                    name, cwd)
        return False, "original project dir is gone"

    # Build the bash -c command — wraps claude with env vars and prompt
    perms = "--dangerously-skip-permissions" if skip_perms else ""
    if resume_id and not _conversation_exists(resume_id):
        # Claude Code exits 1 with "No conversation found with session ID"
        # when asked to resume something it cannot find, and a session that
        # has not taken a turn yet has no conversation on disk. `/perms`
        # relaunches with the id it was given, so switching mode on a
        # freshly-created session killed it outright. Nothing is lost by
        # dropping the flag here: an id with no conversation has no history
        # to preserve.
        log.info("[%s] no conversation for %s — launching fresh instead of "
                 "resuming", name, resume_id)
        resume_id = None
    resume = f"--resume {shlex.quote(resume_id)}" if resume_id else ""
    # `--model` at launch, never a queued `/model` prompt. Queuing it made
    # the model command drain on the session's FIRST IDLE — which is after
    # the operator's first real message has been answered — so choosing a
    # model in the new-session form produced a spurious second turn, a
    # second busy card, and a duplicate of the previous answer (the slash
    # command yields no new assistant text, so the idle notification
    # re-sent the last one). Setting it on the command line means the
    # session simply starts on that model.
    model_flag = f"--model {shlex.quote(model)}" if model else ""
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
    # Lazy resolution — see the comment above _PROJECT_DIR. On no
    # candidate resolving, fall back to the literal "claude", exactly
    # preserving today's `shutil.which("claude") or "claude"` behaviour:
    # launch has never been gated on resolution and stays that way.
    resolved = claude_resolve.try_resolve_claude_binary()
    claude_bin = resolved.chosen.path if resolved else "claude"
    bash_cmd = (
        f"unset CLAUDECODE; "
        f"{unset_token}"
        f"export CLAUDE_DTACH_SESSION=claude-{name}; "
        # Every sibling value on this line is shlex.quote()d; the binary
        # was the lone exception, safe only because it came verbatim from
        # shutil.which(). Once it can come from config (claude_path) or
        # AIPAGER_CLAUDE_BIN, an unquoted value is a shell-injection sink
        # into this `bash -c` string.
        f"{shlex.quote(claude_bin)} {perms} {resume} {model_flag} "
        f"--append-system-prompt {shlex.quote(sys_prompt)}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            _DTACH, "-n", sock, "-Ez", "bash", "-c", bash_cmd,
            cwd=launch_cwd,
            env=daemon_secrets.build_session_env(),
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
