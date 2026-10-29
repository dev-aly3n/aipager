"""Async dtach wrappers — inject keystrokes and check session liveness.

Uses `dtach -p <socket>` to send raw bytes to the session's PTY via stdin.

Socket naming: session "claude-dev" → /tmp/claude-dtach-dev.sock (under
$AIPAGER_INSTANCE_DIR instead of /tmp for an isolated instance, see
aipager.instance).
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
from collections.abc import Awaitable, Callable
from pathlib import Path

from aipager import claude_launch, claude_resolve, daemon_secrets, instance, perm_modes
from aipager.dtach import bg_jobs, typing_gate

log = logging.getLogger(__name__)

# How long a signalled dtach gets to exit before the signal is escalated,
# and how often its liveness is rechecked. A clean dtach shutdown is a few
# milliseconds; the ceiling only matters for a wedged one.
_KILL_TIMEOUT: float = 3.0
_KILL_POLL_INTERVAL: float = 0.02

# Seams: tests replace THESE, never the ``asyncio`` attributes themselves
# (``inject.asyncio`` IS the global module; patching it reaches every
# coroutine in the process, CLAUDE.md). Both default to the real functions.
# ``_key_gap_sleep`` is the pause between two writes of one key sequence
# (``send_text_and_enter``'s gaps between the pieces of a long prompt and
# its text-to-Enter gap, ``discard_queued_input``'s Escape-to-Ctrl+U gap
# and the gaps between its Ctrl+U writes).
_create_subprocess_exec = asyncio.create_subprocess_exec
_key_gap_sleep = asyncio.sleep
# The clock press_kill_agents reads for its two presses (tests replace this
# attribute, never ``time``).
_kill_clock = time.monotonic


def _credentials_path(path: Path | None) -> Path:
    """*path*, or the credentials file of the sessions' environment
    (``~/.claude/.credentials.json``, or ``$CLAUDE_CONFIG_DIR``'s)."""
    if path is not None:
        return path
    from aipager import claude_paths
    return claude_paths.for_sessions().credentials


def _credentials_file_is_fresh(path: Path | None = None) -> bool:
    """Return True iff Claude Code's credentials file (*path*, default the
    sessions' ``~/.claude/.credentials.json`` or ``$CLAUDE_CONFIG_DIR``'s)
    holds an unexpired token.

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
    path = _credentials_path(path)
    try:
        data = json.loads(path.read_text())
        expires_at = data["claudeAiOauth"]["expiresAt"]
        # Claude Code stores expiresAt as unix milliseconds.
        return float(expires_at) / 1000.0 > time.time()
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _credentials_file_has_token(path: Path | None = None) -> bool:
    """Return True iff Claude Code's credentials file holds a real,
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
    path = _credentials_path(path)
    try:
        data = json.loads(path.read_text())
        token = data["claudeAiOauth"]["accessToken"]
        return isinstance(token, str) and bool(token)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _credentials_file_is_dead_placeholder(path: Path | None = None) -> bool:
    """Return True iff Claude Code's credentials file has BOTH
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
    path = _credentials_path(path)
    try:
        data = json.loads(path.read_text())
        oauth = data["claudeAiOauth"]
        access = oauth["accessToken"]
        refresh = oauth["refreshToken"]
        return (isinstance(access, str) and access == ""
                and isinstance(refresh, str) and refresh == "")
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _stash_expired_credentials_file(path: Path | None = None) -> Path | None:
    """Rename an expired credentials file (*path*, default the sessions'
    ``~/.claude/.credentials.json`` or ``$CLAUDE_CONFIG_DIR``'s) aside so
    claude falls back to CLAUDE_CODE_OAUTH_TOKEN.

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
    creds = _credentials_path(path)
    if not creds.exists():
        return None
    dead_placeholder = _credentials_file_is_dead_placeholder(creds)
    traditionally_expired = (
        _credentials_file_has_token(creds) and not _credentials_file_is_fresh(creds)
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


# Computed once at import (the daemon env is fixed for its lifetime).
SOCK_PREFIX = instance.dtach_sock_prefix()

#: The launch shell's variable holding the session's exit record path
#: (:func:`aipager.claude_launch.exit_record`).
_EXIT_VAR = "p"


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
    # Ctrl+U. In Claude Code's input box one press deletes back to the
    # start of the current screen row, or one line break, not the whole
    # input (2.1.296), so discard_queued_input below presses it many times
    # to wipe text an Escape surfaced there without submitting it.
    "KillLine": "\x15",
    # Shift+Tab: Claude Code cycles its permission mode on it
    # (press_mode_cycle below).
    "BackTab": "\x1b[Z",
    # Claude Code's ``chat:killAgents`` (ctrl+x ctrl+k): pressed twice
    # within 3 s it stops every background agent of the session (2.1.312,
    # live). One write per press. Only press_kill_agents below writes it:
    # send_keys and press_key_in_dialog refuse it, so a lone press is never
    # sent from anywhere else.
    "KillAgents": "\x18\x0b",
}
# Key names only press_kill_agents may write (see KEYS["KillAgents"]).
_GATED_KEYS: frozenset[str] = frozenset({"KillAgents"})


def _count_dtach_failure() -> None:
    """Problem reports (8.112): one more dtach failure. Imported here, on a
    failure only: the hook imports this package and must not load the
    report modules on every tool call."""
    try:
        from aipager.report import store as report_store
        report_store.record_counter("dtach_failed")
    except Exception:  # noqa: BLE001 - a counter never fails a dtach call
        pass


async def _run(args: list[str], stdin: bytes = b"",
               timeout: float = 5) -> tuple[bool, str]:
    """Run subprocess, optionally piping stdin, return (success, stdout)."""
    try:
        proc = await _create_subprocess_exec(
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
        _count_dtach_failure()
        return False, ""
    except asyncio.TimeoutError:
        log.error("dtach cmd timed out: %s", args)
        _count_dtach_failure()
        return False, ""
    except FileNotFoundError:
        log.error("dtach not found")
        return False, ""


def _sock_path(session: str) -> str:
    """Convert session name 'claude-dev' to socket path
    '<SOCK_PREFIX>dev.sock' ('/tmp/claude-dtach-dev.sock' by default)."""
    name = session.removeprefix("claude-")
    return f"{SOCK_PREFIX}{name}.sock"


# Claude Code's send-now chord (``chat:sendNow``, bound to ``ctrl+x
# ctrl+s``): Ctrl+X then Ctrl+S, written as TWO separate ``dtach -p``
# writes this many seconds apart. One chunk is read as text by Claude
# Code's input (see send_text_and_enter), and a lone Ctrl+S is
# ``chat:stash``, so the second byte is never written without the first.
SEND_NOW_CHORD_GAP: float = 0.1
_CTRL_X = b"\x18"
_CTRL_S = b"\x13"
# Sessions with a send-now chord being written right now. Every other
# write first waits for it (``_await_chord``), so in normal timing nothing
# lands in the chord's gap and the chord goes through: a byte there would
# break it, and the chord would then be dropped (see send_now).
_CHORD_IN_FLIGHT: set[str] = set()
# How long another write waits for a chord in flight, and how often it looks.
_CHORD_WAIT_LIMIT: float = 0.5
_CHORD_POLL: float = 0.02
# Every write to a session's terminal, counted as its lock is taken
# (``_note_write``): once per lock hold, which is what the chord compares.
_WRITES: dict[str, int] = {}
# When aipager last wrote to each session's terminal (``time.monotonic()``),
# stamped with the count, before the bytes go out (``last_write_at``).
_LAST_WRITE_AT: dict[str, float] = {}
# The per-session terminal write lock. Every ``dtach -p`` write holds its
# session's lock until the write has returned (``dtach -p`` exits only
# after writing), so writes are delivered one at a time and in order, not
# merely started in order:
# - ``send_keys``: one hold per key write;
# - ``send_text_and_enter``: ONE hold across the text (its pieces and the
#   gaps between them, for a long prompt), its settle delay and the Enter,
#   so nothing can land between them (and, first, the typing gate's second
#   look, normally nothing to wait for, and the wipe of a prompt a dialog
#   stopped, roadmap 8.143);
# - ``discard_queued_input``: ONE hold across its Escape, the settle gap and
#   every Ctrl+U write (about 0.3 s for a short queue, one more write per
#   40 characters queued, at most 512 writes), so no prompt's text lands in a
#   half-wiped input box and goes out glued to what is left there;
# - ``send_now``: one hold for the Ctrl+X; released for the gap; one hold
#   from its "was anything written since my Ctrl+X?" check through the
#   Ctrl+S write, so a foreign write falls before the check (and the
#   Ctrl+S is skipped) or after the Ctrl+S, never between them.
# - ``press_mode_cycle``: ONE hold across its Shift+Tab presses and the
#   gaps between them (at most 3 writes + 2 x ``MODE_PRESS_GAP``, 0.3 s,
#   plus the typing gate's look before each press, bounded as a prompt's
#   are), so no prompt's text lands between two presses.
# - ``press_key_in_dialog``: ONE hold across its "is the dialog still
#   open?" check (one small file read off the event loop, roadmap 8.127)
#   and its one key write, so no key lands in a dialog that closed.
# - ``press_kill_agents``: ONE hold across both presses of the kill
#   chord and the 1 s gap between them (``KILL_AGENTS_PRESS_GAP``), so no
#   prompt's text lands between the presses.
# Waiting is FIFO. The bound on any writer's wait (Stop's Escape
# included): at most ``_CHORD_WAIT_LIMIT`` (0.5 s) for a chord in flight,
# then the lock hold in progress: one ``dtach -p`` write, a prompt's
# text (for a long one its pieces and their gaps: a full 4,096-character
# Telegram message is 21 writes and 0.6 s of gaps) + its settle delay (at
# most 0.5 s) + Enter, or a discard's Escape
# and Ctrl+U writes (about 0.3 s for a short queue, longer only for a long
# one, see above). So one writer ahead costs at most ~0.5 s plus the
# writes and gaps in flight (about 1.2 s behind a full-length message), or
# a long discard, or a kill chord's two presses and its gap (about 1.1 s,
# after the gate's look before the first press). A prompt or a mode switch
# (or a kill chord) also looks at the typing gate
# under the lock before each piece, Enter or press; that look waits only
# when a tool call passed its hook while the marker was up (one held past
# its hook's cap), at most ``GATE_WAIT_HARD_LIMIT`` per look, so such a
# hold can grow by a few seconds in that rare case. (A chord never starts
# while a prompt is typed: bot/send_now.py refuses then. Several writers
# queued at once on one session would add up, but Telegram updates are
# handled one at a time.) Each ``dtach -p`` is capped by ``_run``'s
# timeout, so no hold is unbounded.
_WRITE_LOCKS: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}


def _write_lock(session: str) -> asyncio.Lock:
    """*session*'s terminal write lock, for the running event loop (a lock
    is bound to the loop it first waited in; tests run many loops)."""
    loop = asyncio.get_running_loop()
    entry = _WRITE_LOCKS.get(session)
    if entry is None or entry[0] is not loop:
        entry = (loop, asyncio.Lock())
        _WRITE_LOCKS[session] = entry
    return entry[1]


def _note_write(session: str) -> int:
    """Count one write to *session*'s terminal and stamp its time
    (:func:`last_write_at`); return the new count."""
    count = _WRITES.get(session, 0) + 1
    _WRITES[session] = count
    _LAST_WRITE_AT[session] = time.monotonic()
    return count


def last_write_at(session: str) -> float:
    """When aipager last wrote to *session*'s terminal (``time.monotonic()``,
    taken under the write lock before the bytes went out), or 0.0 when it
    has not in this process. Whatever Claude Code writes in answer to
    aipager's keys (an interrupt marker after a typed Deny or /stop's
    Escape, roadmap 8.113) comes after this stamp."""
    return _LAST_WRITE_AT.get(session, 0.0)


def _never_parked(_session: str) -> bool:
    return False


# Claude Code background jobs: a session whose own claude moved its
# conversation into a background job (``←``) is parked, and nothing may be
# typed into it (text typed there can start a stray background job). The
# daemon sets this probe to read its session registry (cli/daemon.py), so
# the registry is the only source of truth and a park loaded from disk is
# honoured at once; this package never imports the bot or the registry.
_parked_probe: Callable[[str], object] = _never_parked
# Session -> the park a refused write was already logged for (what the
# probe answered for it): one warning per park, again for the next one.
_PARK_WARNED: dict[str, object] = {}


def set_parked_probe(fn: Callable[[str], object] | None) -> None:
    """Install *fn* (session name -> parked?) as the parked probe; None
    puts back the default, which says no session is parked. A truthy
    answer means parked; when it names the park (the daemon's answers
    ``(job, park number)``), a new park is told apart from the last one
    for the once-per-park warning, even with no write in between."""
    global _parked_probe
    _parked_probe = fn if fn is not None else _never_parked


def is_parked(session: str) -> bool:
    """Whether *session* is parked on a Claude Code background job, as the
    probe says. A probe that raises counts as parked: refusing a write is
    safe, typing into a parked session is not."""
    return bool(_park_of(session))


def _park_of(session: str) -> object:
    """The probe's answer for *session* (falsy: not parked). A probe that
    raises answers True."""
    try:
        park = _parked_probe(session)
    except Exception:  # noqa: BLE001 - fail closed
        log.warning("parked probe failed for %s", session, exc_info=True)
        park = True
    if not park:
        _PARK_WARNED.pop(session, None)
    return park


def _refuse_parked(session: str) -> bool:
    """True (and one warning per park) when *session* is parked: the
    calling writer then returns False without writing anything."""
    park = _park_of(session)
    if not park:
        return False
    if _PARK_WARNED.get(session) != park:
        _PARK_WARNED[session] = park
        log.warning("Not typing into %s: it is parked on a Claude Code "
                    "background job", session)
    return True


def _never_in_dialog(_session: str) -> bool:
    return False


# Whether a dialog (a permission prompt, a question) is open in a session,
# as the daemon's registry says (cli/daemon.py installs it, like the parked
# probe): a prompt is never typed into one (roadmap 8.143).
_dialog_probe: Callable[[str], object] = _never_in_dialog


def set_dialog_probe(fn: Callable[[str], object] | None) -> None:
    """Install *fn* (session name -> a dialog is open?) as the dialog
    probe; None puts back the default, which says no dialog is open."""
    global _dialog_probe
    _dialog_probe = fn if fn is not None else _never_in_dialog


def dialog_open(session: str) -> bool:
    """Whether the probe says a dialog is open in *session*. A probe that
    raises counts as open: holding a message back is safe, typing it into
    a dialog is not."""
    try:
        return bool(_dialog_probe(session))
    except Exception:  # noqa: BLE001 - fail closed
        log.warning("dialog probe failed for %s", session, exc_info=True)
        return True


async def _await_chord(session: str) -> None:
    """Wait (at most ``_CHORD_WAIT_LIMIT``) while a send-now chord is being
    written to *session*, so no other write lands inside it."""
    if session not in _CHORD_IN_FLIGHT:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _CHORD_WAIT_LIMIT
    while session in _CHORD_IN_FLIGHT and loop.time() < deadline:
        await asyncio.sleep(_CHORD_POLL)


async def send_now(session: str) -> bool:
    """Press Claude Code's send-now chord (Ctrl+X, then Ctrl+S) in
    *session*: it sends every message Claude has queued at once. With
    nothing queued Claude Code does nothing.

    Two writes ``SEND_NOW_CHORD_GAP`` apart, never one chunk. If the Ctrl+X
    write fails, Ctrl+S is not written: alone it would stash the input box.
    Nor is it written when any other write reached the session after the
    Ctrl+X (a writer that gave up waiting for this chord): the chord is
    broken then, and a Ctrl+S after it would stash the input, while a lone
    Ctrl+X is harmless. Returns True when both writes succeeded.

    Cancelled during the gap (daemon shutdown), or with a Ctrl+X write that
    timed out after its byte was delivered, a lone Ctrl+X may be left
    pending in Claude Code's input; the next key then ends that chord."""
    if _refuse_parked(session):
        return False
    if _PENDING_WIPE.get(session):
        # A prompt a dialog stopped left text in the input box (roadmap
        # 8.143): the chord would send it, though it was never sent whole.
        log.warning("Send-now chord → %s not pressed: a stopped prompt's text "
                    "is still in the input box", session)
        return False
    # Marked before the first await, so a write another path starts from
    # here on waits for the chord instead of landing inside it.
    _CHORD_IN_FLIGHT.add(session)
    try:
        sock = _sock_path(session)
        lock = _write_lock(session)
        async with lock:
            # Again under the lock: a park noticed while this waited.
            if _refuse_parked(session):
                return False
            mark = _note_write(session)
            ok, _ = await _run([_DTACH, "-p", sock], stdin=_CTRL_X)
        if not ok:
            return False
        await asyncio.sleep(SEND_NOW_CHORD_GAP)
        # The check and the Ctrl+S under ONE hold: a write that got the
        # lock during the gap has counted itself by now, and no write can
        # start between this check and the Ctrl+S.
        async with lock:
            if _refuse_parked(session):
                return False
            if _WRITES.get(session) != mark:
                log.warning("Send-now chord → %s broken by another write; "
                            "Ctrl+S not written", session)
                return False
            _note_write(session)
            ok, _ = await _run([_DTACH, "-p", sock], stdin=_CTRL_S)
        if ok:
            log.info("Sent send-now chord → %s", session)
        return ok
    finally:
        _CHORD_IN_FLIGHT.discard(session)


async def send_keys(session: str, keys: str) -> bool:
    """Send one key to the dtach session.

    `keys` is a logical name from :data:`KEYS` ("Enter", "Down"). Anything
    else is refused (logged, False, nothing written): a name missing from
    KEYS used to be typed as text, and "C-c" put the three characters
    C, -, c into Claude Code's input box, where they joined the next
    prompt (roadmap 8.137). Text goes through :func:`send_text_and_enter`.
    """
    seq = KEYS.get(keys)
    if seq is None:
        log.warning("send_keys: %r is not a key name; nothing sent to %s",
                    keys, session)
        return False
    if keys in _GATED_KEYS:
        log.warning("send_keys: %r is pressed only through its own writer; "
                    "nothing sent to %s", keys, session)
        return False
    if _refuse_parked(session):
        return False
    await _await_chord(session)
    sock = _sock_path(session)
    async with _write_lock(session):
        if _refuse_parked(session):
            return False
        _note_write(session)
        ok, _ = await _run([_DTACH, "-p", sock], stdin=seq.encode())
    if ok:
        log.info("Sent keys %r → %s", keys, session)
    return ok


# A long prompt is typed in pieces (roadmap 8.123). Claude Code takes a
# burst of input as a paste: one write of a few thousand characters lost
# words (with its paste marking on, the first ~900 characters, aipager's
# "[via Telegram" line among them) and the rest came wrapped in
# <pasted_content> tags. Pieces of TYPE_CHUNK_CHARS characters,
# TYPE_CHUNK_GAP_SECONDS apart, arrive as typed text, exactly as sent
# (2.1.310/2.1.311: 6,000 characters, 40 lines, Persian, idle and while
# Claude works; 64 to 512 characters with 20 to 50 ms gaps all held). The
# margin assumes Claude Code reads its input within about 100 ms: four
# pieces read together reach the ~800 characters it takes as a paste.
TYPE_CHUNK_CHARS: int = 200
TYPE_CHUNK_GAP_SECONDS: float = 0.03

# No dialog takes a prompt's keys (roadmap 8.143). Claude Code draws a
# permission dialog the moment its tool's PreToolUse hooks return and runs
# the PermissionRequest hooks beside it, so the dialog takes keys before
# aipager hears of it (about 0.075 s later): typed into it, a prompt's
# Enter, or a lone digit, picks a "Yes" row (2.1.311, live: the command
# ran). So, around every prompt typed (aipager/dtach/typing_gate.py):
# - the typing marker, written first, holds every tool call that has not
#   passed its PreToolUse hook yet until the Enter is in, and
#   GATE_AFTER_ENTER_SECONDS more so Claude Code reads that Enter first;
# - a call that passed before the marker left a stamp: the typing waits
#   until every stamp is GATE_FRESH_SECONDS old (its dialog, if any, is
#   then known), at most GATE_WAIT_LIMIT_SECONDS, or GATE_WAIT_HARD_LIMIT
#   while one passed less than GATE_MIN_PASS_AGE ago (a call held at the
#   marker past its hook's cap goes on with a fresh stamp: this is looked
#   at again before every piece and the Enter, renewing the marker);
# - the dialog probe is asked before every piece and before the Enter. A
#   dialog open before anything is typed: nothing is typed
#   (DIALOG_BEFORE_TYPING). One that opened after some pieces (a slower
#   hook of the user's own, say): the rest and the Enter are not typed,
#   and what was typed is wiped (Ctrl+U) before the next prompt goes in
#   (DIALOG_WHILE_TYPING). Either way the caller holds the message for the
#   dialog. No piece is a single character: a lone digit picks a row.
GATE_FRESH_SECONDS: float = 1.0
GATE_WAIT_LIMIT_SECONDS: float = 1.5
GATE_MIN_PASS_AGE: float = 0.3
GATE_WAIT_HARD_LIMIT: float = 2.5
GATE_POLL_SECONDS: float = 0.05
GATE_AFTER_ENTER_SECONDS: float = 0.15
# Seam for the gate's waits (tests replace this, never asyncio's own).
_gate_sleep = asyncio.sleep
# Sends holding each session's typing marker (two prompts can overlap: the
# marker goes only when the last one is done).
_GATE_HOLDS: dict[str, int] = {}
# Characters a prompt stopped by a dialog left in the input box, wiped
# before the next prompt is typed there.
_PENDING_WIPE: dict[str, int] = {}
#: Sessions whose pending wipe is only a draft a terminal dialog's Escape
#: left (:func:`request_wipe`), no stopped prompt: for the log's wording.
#: Read only while a wipe is pending; both writers keep it right then.
_WIPE_DRAFT_ONLY: set[str] = set()


class _SendStopped:
    """A falsy :func:`send_text_and_enter` result: the prompt was not
    sent because a dialog is open. Compared with ``is``."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return self._name


DIALOG_BEFORE_TYPING = _SendStopped("DIALOG_BEFORE_TYPING")
DIALOG_WHILE_TYPING = _SendStopped("DIALOG_WHILE_TYPING")


def _gate_base() -> str:
    from aipager import config
    return os.path.dirname(config.SOCKET_PATH) or "/tmp"


def _gate_begin(session: str) -> None:
    _GATE_HOLDS[session] = _GATE_HOLDS.get(session, 0) + 1
    try:
        written = typing_gate.begin(_gate_base(), session)
    except Exception:  # noqa: BLE001 - the probe checks still guard
        written = False
    if not written:
        log.warning("Typing marker for %s could not be written: tool calls "
                    "are not held while it is typed", session)


def _gate_end(session: str) -> None:
    left = _GATE_HOLDS.get(session, 0) - 1
    if left > 0:
        _GATE_HOLDS[session] = left
        return
    _GATE_HOLDS.pop(session, None)
    typing_gate.end(_gate_base(), session)


def _gate_finish(session: str, ok: bool) -> None:
    """End this send's hold on the typing marker. After an Enter that went
    in, a moment later: Claude Code reads the Enter before a tool call held
    at the gate draws its dialog."""
    if ok:
        asyncio.get_running_loop().call_later(GATE_AFTER_ENTER_SECONDS, _gate_end, session)
    else:
        _gate_end(session)


async def _await_quiet_gate(session: str, *, renew: bool = False,
                            blocked: Callable[[str], bool] | None = None) -> bool:
    """Wait until no tool call passed its PreToolUse hook in the last
    :data:`GATE_FRESH_SECONDS` (at most :data:`GATE_WAIT_LIMIT_SECONDS`;
    then on, unless one passed in the last :data:`GATE_MIN_PASS_AGE`, its
    dialog perhaps drawing right now, up to :data:`GATE_WAIT_HARD_LIMIT`).
    False as soon as a dialog is open. *renew*: the typing marker's expiry
    starts again first (a send that holds the lock long stays gated).
    *blocked*, when given, is asked in place of the dialog probe (the kill
    chord's own check: an agent's dialog does not stop it)."""
    is_blocked = dialog_open if blocked is None else blocked
    if renew:
        try:
            typing_gate.begin(_gate_base(), session)
        except Exception:  # noqa: BLE001 - the probe checks still guard
            log.debug("typing marker for %s not renewed", session, exc_info=True)
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + GATE_WAIT_LIMIT_SECONDS
    hard_deadline = started + GATE_WAIT_HARD_LIMIT
    while True:
        if is_blocked(session):
            return False
        fresh = _fresh_passes(session, GATE_FRESH_SECONDS)
        if not fresh:
            return True
        if loop.time() >= deadline and (loop.time() >= hard_deadline
                                         or not _fresh_passes(session, GATE_MIN_PASS_AGE)):
            log.info("Typing into %s: a tool call still passing its hook after "
                     "%.1f s, typing anyway", session, loop.time() - started)
            return True
        await _gate_sleep(GATE_POLL_SECONDS)


def _fresh_passes(session: str, window: float) -> int:
    try:
        return typing_gate.fresh_passes(_gate_base(), session, window)
    except Exception:  # noqa: BLE001 - the probe checks still guard
        log.debug("typing gate stamps unreadable for %s", session, exc_info=True)
        return 0


def _type_pieces(text: str) -> list[str]:
    """*text* in pieces of :data:`TYPE_CHUNK_CHARS`, never a lone
    character (it joins the piece before)."""
    pieces = [text[i:i + TYPE_CHUNK_CHARS]
              for i in range(0, max(len(text), 1), TYPE_CHUNK_CHARS)]
    if len(pieces) > 1 and len(pieces[-1]) == 1:
        tail = pieces.pop()
        pieces[-1] += tail
    return pieces


def _stopped_while_typing(session: str, typed: int) -> _SendStopped:
    _PENDING_WIPE[session] = _PENDING_WIPE.get(session, 0) + typed
    _WIPE_DRAFT_ONLY.discard(session)
    log.warning("A dialog opened in %s while a prompt was typed: stopped after "
                "%d characters, no Enter", session, typed)
    return DIALOG_WHILE_TYPING


async def _wipe_stopped_text(session: str, sock: str) -> bool:
    """Wipe what a prompt stopped by a dialog left in the input box
    (:data:`_PENDING_WIPE`): Ctrl+U as :func:`discard_queued_input` presses
    it, never an Escape (one would interrupt a running turn). Under the
    caller's lock hold. False when a write failed (the wipe stays due)."""
    chars = _PENDING_WIPE.get(session, 0)
    if not chars:
        return True
    kills = KEYS["KillLine"].encode() * DISCARD_KILL_BATCH
    writes = discard_presses(chars) // DISCARD_KILL_BATCH
    for i in range(writes):
        if i:
            await _key_gap_sleep(DISCARD_KILL_GAP)
        ok, _ = await _run([_DTACH, "-p", sock], stdin=kills)
        if not ok:
            log.warning("Wiping a stopped prompt in %s: Ctrl+U write %d/%d failed",
                        session, i + 1, writes)
            return False
    _PENDING_WIPE.pop(session, None)
    if session in _WIPE_DRAFT_ONLY:
        log.info("Wiped the input box (up to %d characters of a draft left behind "
                 "a terminal dialog) in %s", chars, session)
    else:
        log.info("Wiped %d characters of a stopped prompt in %s", chars, session)
    return True


async def send_text_and_enter(session: str, text: str) -> bool | _SendStopped:
    """Send literal text followed by Enter.

    Text and Enter must be separate dtach -p calls — Claude Code's TUI
    treats a single chunk (text + CR) as all-text input. A separate CR
    write is needed to trigger the submit keypress event. Text longer than
    :data:`TYPE_CHUNK_CHARS` goes in pieces (see the note above). All of it
    is written under one hold of the session's write lock, so no other
    write (a Stop's Escape, a send-now chord's check and Ctrl+S) can land
    between the pieces or before the Enter; it is counted once, for the
    whole. A piece that fails stops the send: no Enter.

    Never into a dialog (roadmap 8.143, the gate note above): returns
    :data:`DIALOG_BEFORE_TYPING` when one is open before anything is
    typed, :data:`DIALOG_WHILE_TYPING` when one opened after some pieces
    (no Enter; their text is wiped before the next prompt).
    """
    if _refuse_parked(session):
        return False
    await _await_chord(session)
    sock = _sock_path(session)
    _gate_begin(session)
    ok = False
    try:
        if not await _await_quiet_gate(session):
            log.info("Not typing into %s: a dialog is open", session)
            return DIALOG_BEFORE_TYPING
        async with _write_lock(session):
            if _refuse_parked(session):
                return False
            # Again: the lock may have waited behind other writes (the
            # marker is renewed for as long again, at every look below too).
            if not await _await_quiet_gate(session, renew=True):
                log.info("Not typing into %s: a dialog is open", session)
                return DIALOG_BEFORE_TYPING
            _note_write(session)
            if not await _wipe_stopped_text(session, sock):
                return False
            typed = 0
            for i, piece in enumerate(_type_pieces(text)):
                if i:
                    await _key_gap_sleep(TYPE_CHUNK_GAP_SECONDS)
                # The whole gate again, not only the probe: a tool call held
                # at the marker longer than its hook's cap went on with a
                # fresh stamp, and its dialog may be drawing right now.
                if not await _await_quiet_gate(session, renew=True):
                    ok = False
                    if not i:
                        log.info("Not typing into %s: a dialog is open", session)
                        return DIALOG_BEFORE_TYPING
                    return _stopped_while_typing(session, typed)
                ok, _ = await _run([_DTACH, "-p", sock], stdin=piece.encode())
                if not ok:
                    return False
                typed += len(piece)
            # Claude Code's Ink TUI needs time to process text input before
            # Enter is recognized as "submit". Too short → \r is swallowed.
            # Scale with text length: longer text = more rendering time needed.
            delay = max(0.15, min(0.5, len(text) * 0.003))
            await _key_gap_sleep(delay)
            if not await _await_quiet_gate(session, renew=True):
                ok = False
                return _stopped_while_typing(session, len(text))
            ok, _ = await _run([_DTACH, "-p", sock], stdin=b"\r")
    finally:
        _gate_finish(session, ok)
    if ok:
        log.info("Sent text %r + Enter → %s", text[:50], session)
    return ok


async def press_enter_gated(session: str) -> bool | _SendStopped:
    """One Enter typed as a prompt's is (the second Enter of a message
    Claude Code held back, roadmap 8.124): through the typing gate, never
    into a dialog (:data:`DIALOG_BEFORE_TYPING`, nothing written)."""
    if _refuse_parked(session):
        return False
    await _await_chord(session)
    sock = _sock_path(session)
    _gate_begin(session)
    ok = False
    try:
        if not await _await_quiet_gate(session):
            return DIALOG_BEFORE_TYPING
        async with _write_lock(session):
            if _refuse_parked(session):
                return False
            if not await _await_quiet_gate(session, renew=True):
                return DIALOG_BEFORE_TYPING
            _note_write(session)
            ok, _ = await _run([_DTACH, "-p", sock], stdin=b"\r")
    finally:
        _gate_finish(session, ok)
    if ok:
        log.info("Sent keys 'Enter' → %s", session)
    return ok


async def press_key_in_dialog(session: str, key: str,
                              still_open: Callable[[], Awaitable[bool]]) -> bool:
    """Answer a dialog open in Claude Code's terminal that no hook tells of
    (roadmap 8.127, a resume question) with one key from :data:`KEYS`.

    Only while it is still open: *still_open* is awaited under the
    session's write lock, right before the write, and False (or raising)
    writes nothing; so a key never lands in a dialog that closed, a newer
    one, or a turn. Not through the dialog probe (the dialog is the
    target). Refused for a parked session. True once the key went out."""
    seq = KEYS.get(key)
    if seq is None or key in _GATED_KEYS:
        log.warning("press_key_in_dialog: %r is not a dialog key; nothing sent to %s",
                    key, session)
        return False
    if _refuse_parked(session):
        return False
    await _await_chord(session)
    sock = _sock_path(session)
    async with _write_lock(session):
        if _refuse_parked(session):
            return False
        try:
            still = bool(await still_open())
        except Exception:  # noqa: BLE001 - fail closed: no key
            log.warning("dialog check failed for %s: no key sent", session,
                        exc_info=True)
            still = False
        if not still:
            log.info("Not pressing %s in %s: the dialog is no longer open",
                     key, session)
            return False
        _note_write(session)
        ok, _ = await _run([_DTACH, "-p", sock], stdin=seq.encode())
    if ok:
        log.info("Sent keys %r → %s (dialog)", key, session)
    return ok


def request_wipe(session: str, chars: int) -> None:
    """Have the next prompt typed into *session* first wipe its input box
    (Ctrl+U, as for a prompt a dialog stopped: :data:`_PENDING_WIPE`) of
    at least *chars* characters. For a draft a terminal dialog's Escape
    left there (roadmap 8.127)."""
    if chars > 0:
        if not _PENDING_WIPE.get(session):
            _WIPE_DRAFT_ONLY.add(session)
        _PENDING_WIPE[session] = max(_PENDING_WIPE.get(session, 0), int(chars))


# discard_queued_input's wipe. Claude Code's Ctrl+U deletes, per press, one
# line break or the text from the start of the current screen row back to
# the cursor; on an empty box it deletes nothing (2.1.296; live, one press
# took a 665-character input to 586). So every press on a non-empty box
# deletes at least one character, and as many presses as there are
# characters wipe the box whatever its line lengths or the terminal width.
# The caller passes how many characters Claude's queue holds (its
# transcript knows the text); DISCARD_MIN_PRESSES more cover what it cannot
# count (a message whose enqueue line is not written yet, text typed at the
# terminal): about four screens of a 40-column terminal. Capped at
# DISCARD_MAX_PRESSES (five full Telegram messages) so one wipe never holds
# the write lock for more than 512 writes (about 8 s on a fast machine). Written DISCARD_KILL_BATCH to a
# write (live, one write of about 40 cleared that 665-character input), far
# under the 800 characters above which Claude Code takes typed text as a
# paste (in 2.1.296's code a Ctrl key is never taken as pasted text).
DISCARD_KILL_BATCH: int = 40
DISCARD_MIN_PRESSES: int = 480
DISCARD_MAX_PRESSES: int = 20_480
DISCARD_KILL_GAP: float = 0.01
# From the Escape to the first Ctrl+U: Claude Code moves its queue into the
# input box on the Escape.
DISCARD_ESCAPE_SETTLE: float = 0.15


def discard_presses(chars: int) -> int:
    """How many Ctrl+U presses wipe *chars* characters surfaced into the
    input box, plus the margin, in whole batches (see the note above)."""
    presses = min(DISCARD_MAX_PRESSES, DISCARD_MIN_PRESSES + max(0, chars))
    return -(-presses // DISCARD_KILL_BATCH) * DISCARD_KILL_BATCH


async def discard_queued_input(session: str, chars: int = 0) -> bool:
    """Wipe text Claude's own not-yet-picked-up queue surfaced into the
    input box, without touching whatever turn is (or isn't) currently
    running.

    Upstream Claude Code (2.1.0+) moves queued-but-not-yet-picked-up
    prompts into the visible input box on Escape rather than discarding
    them — proven interrupt behaviour this project does not change (see
    design.md "Stop and /clearqueue on the same primitive"). Left alone,
    that surfaced text would concatenate onto the next prompt sent. One
    more Escape (in case a fresh dialog opened underneath in the
    meantime), then Ctrl+U (``KillLine``) pressed
    ``discard_presses(chars)`` times clears whatever landed in the input
    box: one press deletes only one screen row or one line break there, so
    a single press left all but the last row of a long or multi-message
    queue behind. *chars*: how many characters Claude's queue holds, as
    far as the caller knows (see the sizing note above).

    The one function the version-fragile choreography above is isolated
    behind — every caller (Stop, ``/clearqueue``, the Mini App's
    clearqueue route) goes through this rather than reproducing the key
    sequence itself, so a future Claude Code release that changes the
    mechanism only needs one call site fixed. Sends exactly one Escape,
    first, then only Ctrl+U — callers that also need the two-Escape
    interrupt pair send those themselves, first (Stop), or not at all
    (``/clearqueue`` — it must never send a second Escape, which would
    risk interrupting the running turn it explicitly leaves alone).

    Every write under ONE hold of the session's write lock, counted once,
    so no prompt's text lands in a half-wiped box. Stops at the first
    failed write and returns False; True once every write went out.
    """
    if _refuse_parked(session):
        return False
    await _await_chord(session)
    sock = _sock_path(session)
    kills = KEYS["KillLine"].encode() * DISCARD_KILL_BATCH
    presses = discard_presses(chars)
    if DISCARD_MIN_PRESSES + chars > DISCARD_MAX_PRESSES:
        log.warning("Discard → %s: %d characters queued, wiping at most %d",
                    session, chars, DISCARD_MAX_PRESSES - DISCARD_MIN_PRESSES)
    writes = presses // DISCARD_KILL_BATCH
    async with _write_lock(session):
        if _refuse_parked(session):
            return False
        _note_write(session)
        ok, _ = await _run([_DTACH, "-p", sock], stdin=KEYS["Escape"].encode())
        if not ok:
            log.warning("Discard → %s: the Escape write failed", session)
            return False
        await _key_gap_sleep(DISCARD_ESCAPE_SETTLE)
        for i in range(writes):
            if i:
                await _key_gap_sleep(DISCARD_KILL_GAP)
            ok, _ = await _run([_DTACH, "-p", sock], stdin=kills)
            if not ok:
                log.warning("Discard → %s: Ctrl+U write %d/%d failed",
                            session, i + 1, writes)
                return False
    # The input box is empty now: a stopped prompt's text went with it.
    _PENDING_WIPE.pop(session, None)
    log.info("Discarded queued input → %s (Escape, %d x Ctrl+U)",
             session, presses)
    return True


async def press_mode_cycle(session: str, presses: int, *,
                           still_wanted: Callable[[], bool] | None = None,
                           on_press: Callable[[], None] | None = None,
                           ) -> bool | _SendStopped:
    """Press Shift+Tab *presses* times in *session*: Claude Code moves one
    step along its permission-mode cycle per press (an Ask<->Auto switch
    without a restart, aipager/bot/session_ops.py ``_mode_switch_core``).

    One ``dtach -p`` write per press, ``MODE_PRESS_GAP`` apart, never one
    chunk: Ink reads a chunk of several escape sequences unpredictably (the
    same reason ``send_text_and_enter`` splits text and Enter), and a
    partly read chunk would land in a mode nobody can count. All presses
    under one hold of the write lock, counted once. Stops at the first
    failed write and returns False; ``presses <= 0`` writes nothing.

    Through the typing gate, as a prompt is (roadmap 8.146; the gate note
    above): in Claude Code's file-edit permission dialog Shift+Tab allows
    the edit and every edit after it for the session (2.1.311, live). The
    whole gate is looked at before every press: a dialog open before the
    first returns :data:`DIALOG_BEFORE_TYPING` (nothing pressed); one that
    opened after some presses stops the rest and returns
    :data:`DIALOG_WHILE_TYPING` (the mode is then somewhere along the
    cycle). *on_press* is called after each press that went in, so the
    caller knows how far. *still_wanted*, asked under the lock before each
    press: False (a relaunch or the session's end began while the gate
    waited) stops the presses and returns False."""
    if presses <= 0:
        return True
    if _refuse_parked(session):
        return False
    await _await_chord(session)
    sock = _sock_path(session)
    seq = KEYS["BackTab"].encode()
    _gate_begin(session)
    ok = False
    try:
        if not await _await_quiet_gate(session):
            log.info("Not pressing Shift+Tab in %s: a dialog is open", session)
            return DIALOG_BEFORE_TYPING
        async with _write_lock(session):
            if _refuse_parked(session):
                return False
            for i in range(presses):
                if i:
                    await _key_gap_sleep(perm_modes.MODE_PRESS_GAP)
                # The whole gate before every press (renewing the marker):
                # a tool call held at the marker past its hook's cap goes
                # on with a fresh stamp, and its dialog may be drawing now.
                # The first one is also the look again after the lock wait.
                if not await _await_quiet_gate(session, renew=True):
                    ok = False
                    if not i:
                        log.info("Not pressing Shift+Tab in %s: a dialog is open", session)
                        return DIALOG_BEFORE_TYPING
                    log.warning("A dialog opened in %s during a mode switch: stopped "
                                "after %d of %d Shift+Tab presses", session, i, presses)
                    return DIALOG_WHILE_TYPING
                if still_wanted is not None and not still_wanted():
                    ok = False
                    log.info("Mode switch in %s no longer wanted after %d of %d "
                             "Shift+Tab presses", session, i, presses)
                    return False
                if not i:
                    _note_write(session)
                ok, _ = await _run([_DTACH, "-p", sock], stdin=seq)
                if not ok:
                    log.warning("Shift+Tab %d/%d → %s failed", i + 1, presses, session)
                    return False
                if on_press is not None:
                    on_press()
    finally:
        _gate_finish(session, ok)
    log.info("Pressed Shift+Tab x%d → %s", presses, session)
    return True


# Claude Code's kill chord (KEYS["KillAgents"]) counts only when its second
# press comes within 3 s of the first (2.1.312, ``i$e=3000``); a later one
# is a new first press. 1 s apart is safe (probe G1, G3: 2.5 s still
# counted, 3.2 s did not); the second press is not written after
# KILL_AGENTS_MAX_GAP.
KILL_AGENTS_PRESS_GAP: float = 1.0
KILL_AGENTS_MAX_GAP: float = 2.5


async def press_kill_agents(session: str, may_press: Callable[[], bool],
                            before_second_press: Callable[[], None] | None = None,
                            ) -> bool | _SendStopped:
    """Press Claude Code's ``chat:killAgents`` chord twice in *session*,
    ``KILL_AGENTS_PRESS_GAP`` apart: it stops every background agent of the
    session (an agent's open dialog goes with its agent), types nothing
    into a draft and answers no dialog of Claude's own (2.1.312, live).

    Through the typing gate, as a prompt is, with *may_press* in place of
    the dialog probe: an agent's dialog does not stop the chord, a dialog
    of Claude's own (or one nobody can name yet) does, and *may_press* says
    which. Asked before the first press (False: nothing is pressed,
    :data:`DIALOG_BEFORE_TYPING`) and again before the second (False: the
    second press is not written and this returns False; a lone first press
    only shows Claude Code's confirm line for 3 s). Both presses and the
    gap under ONE hold of the write lock, counted once. True only when
    both writes went out, the second within ``KILL_AGENTS_MAX_GAP``.

    *before_second_press* runs once every check before the second press
    passed, right before it is written, with no await in between: the
    turn Claude Code starts after the kill cannot come before that press
    (one press stops nothing), so a prompt typed at the terminal any
    earlier is never mistaken for it."""
    if _refuse_parked(session):
        return False
    await _await_chord(session)
    sock = _sock_path(session)
    seq = KEYS["KillAgents"].encode()

    def blocked(_s: str) -> bool:
        return not may_press()

    _gate_begin(session)
    ok = False
    try:
        if not await _await_quiet_gate(session, blocked=blocked):
            log.info("Kill-agents chord not pressed in %s: a dialog is open", session)
            return DIALOG_BEFORE_TYPING
        async with _write_lock(session):
            if _refuse_parked(session):
                return False
            if not await _await_quiet_gate(session, renew=True, blocked=blocked):
                log.info("Kill-agents chord not pressed in %s: a dialog is open",
                         session)
                return DIALOG_BEFORE_TYPING
            _note_write(session)
            first, _ = await _run([_DTACH, "-p", sock], stdin=seq)
            if not first:
                log.warning("Kill-agents chord → %s: the first press failed", session)
                return False
            t1 = _kill_clock()
            await _key_gap_sleep(KILL_AGENTS_PRESS_GAP)
            if _refuse_parked(session):
                return False
            if not may_press():
                log.info("Kill-agents chord → %s: second press not written, "
                         "the session changed during the gap", session)
                return False
            if _kill_clock() - t1 > KILL_AGENTS_MAX_GAP:
                log.warning("Kill-agents chord → %s: second press not written, "
                            "%.1f s after the first", session, _kill_clock() - t1)
                return False
            if before_second_press is not None:
                before_second_press()
            ok, _ = await _run([_DTACH, "-p", sock], stdin=seq)
    finally:
        _gate_finish(session, ok)
    log.info("Sent kill-agents chord → %s (%s)", session, "ok" if ok else "failed")
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
        proc = await _create_subprocess_exec(
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
    # No input box to wipe any more (roadmap 8.143).
    _PENDING_WIPE.pop(session, None)
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
    except OSError as e:
        # Swallowing this silently left a socket the monitor's next 2 s
        # scan re-adopted as a "new" session under a derived label
        # (roadmap 8.6). The kill itself succeeded, so still True — but
        # say so, so the re-adoption has an explanation in the journal.
        log.warning("[%s] killed, but could not remove socket %s: %s — "
                    "the next scan may re-adopt it", session, sock, e)
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
    "status", "stop", "kill", "new", "help", "start", "settings", "language",
    "restart", "rename", "delete", "diff",
    "app", "clearqueue", "perms", "mode", "resume", "whoami", "update", "now",
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


def _conversation_exists(session_id: str, projects: Path | None = None) -> bool:
    """True when Claude Code has a transcript it could resume for *session_id*.

    Transcripts live at ``<projects>/<cwd-slug>/<session-id>.jsonl``, where
    *projects* is the sessions' ``~/.claude/projects`` (or
    ``$CLAUDE_CONFIG_DIR``'s: :mod:`aipager.claude_paths`).
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
        if projects is None:
            from aipager import claude_paths
            projects = claude_paths.for_sessions().projects
        return any(projects.glob(f"*/{session_id}.jsonl"))
    except OSError:
        log.debug("conversation lookup failed for %s", session_id, exc_info=True)
        return False


# (substring, phrase) — checked in order, case-insensitively, against
# dtach's stderr. The phrases are what chat sees (roadmap 8.7): every one
# of dtach's own messages carries its binary path and the socket path
# (session name, /tmp), and the exec failure carries the command path with
# the operator's home directory, so the raw text must not reach a
# Telegram message; it goes to the daemon log instead. Measured
# 2026-09-06 against the bundled dtach: "<sock>: Address already in use",
# "could not execute <cmd>: No such file or directory", "<sock>: No such
# file or directory", "<sock>: File name too long"; "Could not find a
# pty." and "Permission denied" come from its source. "could not execute"
# is checked before "No such file or directory" because the exec failure
# ends with the latter.
_DTACH_FAILURE_PHRASES: tuple[tuple[str, str], ...] = (
    ("address already in use",
     "a session socket with this name already exists - kill it or pick another name"),
    ("could not execute",
     "dtach could not start the shell (bash missing or not executable?)"),
    ("could not find a pty", "no pseudo-terminal available on this machine"),
    ("file name too long",
     "the session name makes the socket path too long - pick a shorter name"),
    ("permission denied", "no permission to create the session socket"),
    ("no such file or directory", "the socket directory does not exist"),
)
_DTACH_STDERR_LOG_MAX = 300  # chars of dtach's first stderr line kept in the log


def _describe_dtach_failure(returncode: int, stderr: str) -> str:
    """The fixed, path-free phrase for a failed ``dtach -n`` — see
    ``_DTACH_FAILURE_PHRASES``. Anything unrecognised (an empty stderr
    included) names only the exit status and points at the log."""
    lowered = stderr.lower()
    for needle, phrase in _DTACH_FAILURE_PHRASES:
        if needle in lowered:
            return phrase
    return f"dtach exited with status {returncode} (see `aipager logs`)"


async def launch_session(
    name: str,
    *,
    mode: str,
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

    ``mode`` (required, never defaulted) is ``"ask"``, ``"auto"`` or
    ``"full"`` (aipager/perm_modes.py); its Claude Code flag is always
    passed explicitly, so Claude Code's own starting mode never decides.
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
    # (e.g. "api__d222333444"), so the cap is generous; the
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
    # Exactly one of the three ids, never a bool or "": a caller still
    # passing the old skip_perms boolean must fail closed, not launch in a
    # mode nobody chose.
    if not perm_modes.is_mode(mode):
        return False, perm_modes.INVALID_MODE_ERROR

    sock = f"{SOCK_PREFIX}{name}.sock"
    if Path(sock).is_socket():
        return False, f"Session '{name}' already exists"
    # A new claude starts with an empty input box (roadmap 8.143); only
    # past the check above, which keeps a live session's own.
    _PENDING_WIPE.pop(f"claude-{name}", None)

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

    # The environment the session gets, and where Claude Code keeps its
    # files for it (CLAUDE_CONFIG_DIR in daemon.env or the daemon's env, or
    # a settings env block, moves them): read once, used for every check
    # below and for the spawn.
    from aipager import claude_paths
    session_env = daemon_secrets.build_session_env()
    paths = claude_paths.locate(session_env, cwd=launch_cwd)

    # Build the bash -c command — wraps claude with env vars and prompt
    perms = perm_modes.launch_flag(mode)
    if resume_id and not _conversation_exists(resume_id, paths.projects):
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
    # Lazy resolution — see the comment above _PROJECT_DIR. On no
    # candidate resolving, fall back to the literal "claude", exactly
    # preserving today's `shutil.which("claude") or "claude"` behaviour:
    # launch has never been gated on resolution and stays that way.
    resolved = claude_resolve.try_resolve_claude_binary()
    claude_bin = resolved.chosen.path if resolved else "claude"
    claude_version = getattr(resolved.chosen, "version", None) if resolved else None
    if resolved is not None and os.path.realpath(claude_bin) != getattr(
            resolved.chosen, "realpath", None):
        # The path leads to another file than the one whose version the
        # daemon read: Claude Code updated itself (its installer points the
        # link at the new version's file) or was replaced. That path's
        # version is read again; when that fails it is not known.
        try:
            fresh = await asyncio.to_thread(claude_resolve.reprobe_chosen)
        except Exception:  # noqa: BLE001 - unknown is the safe answer
            log.debug("[%s] probing claude again failed", name, exc_info=True)
            fresh = None
        same = fresh is not None and fresh.realpath == os.path.realpath(claude_bin)
        claude_version = fresh.version if same else None
    # The session's name, and (for a Claude Code with SendUserFile, or one
    # whose version is unknown) that Claude can send files to the chat.
    sys_prompt = claude_launch.base_system_prompt(name, claude_version)
    # The prompt appended below, not the one this conversation recorded on
    # its first request (Claude Code 2.1.267+ replays that on --resume);
    # never for a version not known (an older Claude Code refuses the flag
    # and the session would not start).
    snapshot_flag = " ".join(claude_launch.snapshot_off_args(claude_version))
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
    # credentials file (the session's: paths.credentials) is on disk. See
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
    stashed = _stash_expired_credentials_file(paths.credentials)
    if stashed is not None:
        log.info("[%s] stashed expired credentials.json → %s "
                 "(env token will be used instead)", name, stashed.name)
    unset_token = ("unset CLAUDE_CODE_OAUTH_TOKEN; "
                   if _credentials_file_is_fresh(paths.credentials) else "")
    log.info(
        "[%s] launch: %s CLAUDE_CODE_OAUTH_TOKEN (credentials file %s)",
        name,
        "stripping" if unset_token else "keeping",
        "fresh" if unset_token else "missing/expired",
    )
    exit_prefix, exit_suffix = claude_launch.exit_record(_EXIT_VAR)
    bash_cmd = (
        f"unset CLAUDECODE; "
        f"{unset_token}"
        f"export CLAUDE_DTACH_SESSION=claude-{name}; "
        # Claude Code's SendUserFile (2.1.301+): read once, at start; the
        # hook checks every file and the daemon delivers it to the chat.
        f"export {claude_launch.ATTACHMENTS_ENV}=1; "
        # Why the session ends (step 4 of the background-jobs design): the
        # previous exit record is removed before claude starts, and claude's
        # exit code is recorded after it. claude is therefore not the last
        # command, so bash cannot exec it away.
        f"{_EXIT_VAR}={shlex.quote(str(bg_jobs.exit_path(f'claude-{name}')))}; "
        f"{exit_prefix} "
        # Every sibling value on this line is shlex.quote()d; the binary
        # was the lone exception, safe only because it came verbatim from
        # shutil.which(). Once it can come from config (claude_path) or
        # AIPAGER_CLAUDE_BIN, an unquoted value is a shell-injection sink
        # into this `bash -c` string.
        f"{shlex.quote(claude_bin)} {perms} {resume} {model_flag} {snapshot_flag} "
        f"--append-system-prompt {shlex.quote(sys_prompt)} ; "
        f"{exit_suffix}"
    )

    # Claude Code asks "Do you trust this folder?" in a folder it has never
    # trusted, and the first message sent to the session would answer it:
    # Enter on "No, exit", and the session ends at once. No one on Telegram
    # sees that dialog, so the folder is trusted here, right before a
    # session starts in it (restricted members cannot write the files a
    # trusted folder runs: .claude/, .git/, .mcp.json). Never blocks the
    # launch: an untrusted folder is no worse off than before.
    # Claude Code applies a folder's trust to every folder under it that is
    # not inside a git repository; a repository still asks (2.1.301). The
    # home folder is marked too: a yes given there is kept only for that one
    # session, and a session started from Telegram cannot give it. Never /
    # or a folder above home, which would reach every such folder on the
    # machine.
    # The same goes for Claude Code's "use this API key?" question and its
    # first-run screens: they are answered in the same write, from the very
    # environment the session gets (claude_bootstrap.prepare_session_start).
    # aipager's hooks too: wired at daemon start, and again here when the
    # session's settings file is another one (CLAUDE_CONFIG_DIR changed).
    from aipager import claude_bootstrap, safety
    try:
        claude_bootstrap.ensure_session_hooks(paths)
        trust = launch_cwd
        if safety.holds_home_folder(launch_cwd):
            trust = None
            log.info("[%s] the session's folder is / or a folder above the home folder "
                     "- not marked trusted for Claude Code", name)
        for change in claude_bootstrap.prepare_session_start(trust, session_env,
                                                             cwd=launch_cwd):
            if change == "trusted":
                log.info("[%s] marked the session's folder trusted for Claude Code", name)
            elif change == "key-approved":
                log.info("[%s] told Claude Code to use the session's ANTHROPIC_API_KEY", name)
            elif change == "key-declined":
                log.info("[%s] told Claude Code not to use the ANTHROPIC_API_KEY aipager "
                         "inherited from its environment: sessions use the other login (to "
                         "use the key, turn it on with /config, \"Use custom API key\", in a "
                         "terminal session that has it)", name)
            elif change == "onboarded":
                log.info("[%s] marked Claude Code's first-run screens done", name)
    except Exception:
        log.warning("[%s] could not prepare Claude Code's start-up answers", name,
                    exc_info=True)

    try:
        proc = await _create_subprocess_exec(
            _DTACH, "-n", sock, "-Ez", "bash", "-c", bash_cmd,
            cwd=launch_cwd,
            env=session_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            raw = stderr.decode(errors="replace").strip()
            first = (raw.splitlines() or [""])[0][:_DTACH_STDERR_LOG_MAX]
            log.warning("[%s] dtach launch failed (rc=%d): %s",
                        name, proc.returncode, first)
            _count_dtach_failure()
            return False, f"dtach failed: {_describe_dtach_failure(proc.returncode, raw)}"
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


def _scan_session_sockets() -> list[str]:
    """Names of the claude-dtach sessions whose socket exists: every
    ``claude-dtach-*.sock`` in the runtime folder (``/tmp``, or the
    instance folder; see :func:`aipager.instance.runtime_tmp_dir`) that is
    a Unix socket."""
    results = []
    for sock_file in Path(instance.runtime_tmp_dir()).glob("claude-dtach-*.sock"):
        if not sock_file.is_socket():
            continue
        name = "claude-" + sock_file.stem.removeprefix("claude-dtach-")
        results.append(name)
    return results


def live_session_names() -> set[str]:
    """The sessions alive right now, synchronously: what
    :func:`list_sessions` returns, as a set. For ``SessionRegistry.load``,
    which runs before the event loop's first scan and must tell a running
    session from an orphan the same way the scan will."""
    return set(_scan_session_sockets())


async def list_sessions() -> list[str]:
    """Return names of all active claude-dtach sessions (see
    :func:`_scan_session_sockets`)."""
    return _scan_session_sockets()
