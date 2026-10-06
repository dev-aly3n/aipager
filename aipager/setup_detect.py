"""`aipager setup detect-chat`: find the person's Telegram id for
`aipager setup --chat-id`, and save nothing.

The person opens the bot and presses Start (which sends ``/start``);
this command reports the newest private message to the bot dated at
most :data:`DETECT_LOOKBACK_SECONDS` before it started, so the person
can press Start before or after the agent runs it while a stranger's
day-old message is ignored. The agent must show the candidate to the
person and run ``aipager setup --chat-id`` only once they confirm.

Source: ``getUpdates`` polled read-only (never an ``offset``, which
would confirm and so delete the ``/start`` for the wizard or a later
daemon). While a daemon for the same bot runs, it holds the bot's
updates, so the daemon-aware source the wizard uses is asked instead;
that source sees no DMs today and the command says so (exit 9).

Pacing goes through the module seams :func:`_now`, :func:`_monotonic`
and :func:`_sleep`, which tests patch (never ``time`` itself).
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from aipager.setup_cmd import (
    EXIT_DAEMON_RUNNING, EXIT_DETECT_TIMEOUT, EXIT_OK, EXIT_TELEGRAM_UNREACHABLE,
    EXIT_TOKEN_REJECTED, EXIT_UPDATES_CONFLICT, EXIT_USAGE, SetupError, _Run,
    _get_me_or_raise, _bot_link, _scrub,
)

#: Private messages older than this (seconds before the command started)
#: are ignored.
DETECT_LOOKBACK_SECONDS = 600
#: Seconds between two getUpdates polls.
DETECT_POLL_SECONDS = 2
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 3600

#: The daemon-aware source cannot see a DM (it never records one).
AGENT_DAEMON_DM_ADVISORY = (
    "A running aipager daemon for this bot reads its messages, and it does "
    "not note private messages from people who are not set up yet, so "
    "detect-chat cannot see the person's /start.")
AGENT_DAEMON_DM_FIX = (
    "Stop the daemon (`aipager service stop`, or stop a foreground "
    "`aipager start`) and run this again, or ask the person for their "
    "numeric Telegram id and pass it to `aipager setup --chat-id`.")

#: Telegram returned a full batch of old updates, hiding newer ones.
DETECT_BACKLOG_ADVISORY = (
    "The bot has 100 or more unread updates from the last 24 hours, and "
    "Telegram shows detect-chat only the oldest 100 of them (detect-chat "
    "never confirms updates, so it cannot page past them), so a new /start "
    "cannot be seen.")

_TIMEOUT_RE = re.compile(r"\d{1,6}")


def _now() -> float:
    return time.time()


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _parse_timeout(raw: str | None) -> int:
    if raw is None:
        return DEFAULT_TIMEOUT
    raw = raw.strip()
    if not _TIMEOUT_RE.fullmatch(raw) or int(raw) > MAX_TIMEOUT:
        raise SetupError(
            EXIT_USAGE, "bad_timeout",
            f"--timeout must be a whole number of seconds from 0 to {MAX_TIMEOUT}.",
            f"Pass --timeout SECONDS (0 to {MAX_TIMEOUT}; the default is "
            f"{DEFAULT_TIMEOUT}).")
    return int(raw)


def _configured_token() -> str | None:
    """The token of the install on disk (``aipager.yaml``, else the v1
    ``config.env``), or ``None`` when it cannot be told."""
    from aipager.wizard import daemon_io, scope_io
    try:
        _scopes, token = scope_io.read_config()
    except Exception:
        return None
    if token:
        return token
    try:
        token = daemon_io._read_env_file()[0]
    except Exception:
        return None
    return token or None


def _choose_source(token: str) -> str:
    """``"daemon"`` when a daemon runs AND it serves this bot (or which
    bot it serves cannot be told), else ``"telegram"``."""
    from aipager.wizard import telegram_api
    if telegram_api._watch_source() != "daemon":
        return "telegram"
    configured = _configured_token()
    if configured is not None and configured != token:
        # The running daemon polls another bot: this one's updates are free.
        return "telegram"
    return "daemon"


def _name(candidate: dict) -> str:
    parts = [p for p in (candidate.get("first_name"), candidate.get("last_name"))
             if p]
    name = " ".join(parts) or "(no name)"
    if candidate.get("username"):
        name += f" (@{candidate['username']})"
    return name


def _found(run: _Run, candidate: dict, others: int) -> int:
    doc = run.doc
    doc["status"] = "found"
    doc["candidate"] = candidate
    doc["other_candidates"] = others
    msg = f"Found {_name(candidate)}, Telegram id {candidate['id']}."
    if others:
        msg += (f" {others} other {'person' if others == 1 else 'people'} also "
                "messaged the bot in the last "
                f"{DETECT_LOOKBACK_SECONDS // 60} minutes: confirm it is the "
                "right person.")
    doc["message"] = msg
    doc["next_step"] = (
        "Show this person to the user; once they confirm, run: aipager setup "
        f"{run.token_hint} --chat-id {candidate['id']}")
    run.plain_out.append(f"  id:       {candidate['id']}")
    run.plain_out.append(f"  name:     {_name(candidate)}")
    run.plain_out.append(f"  source:   {doc['source']}")
    return EXIT_OK


def cmd_detect_chat(args: argparse.Namespace, run: _Run) -> int:
    """Run detect-chat on an already-validated invocation (*run* holds the
    token read by :mod:`aipager.setup_cmd`). Writes nothing."""
    from aipager.wizard import telegram_api

    doc = run.doc
    timeout = _parse_timeout(getattr(args, "timeout", None))
    username = _get_me_or_raise(run)
    source = _choose_source(run.token)
    doc["source"] = source
    link = _bot_link(username)

    if source == "daemon":
        cid, who, _advisory = telegram_api._detect_id(
            run.token, want="dm", source="daemon")
        if cid is not None:
            is_name_only = isinstance(who, telegram_api.NameOnly)
            return _found(run, {
                "id": cid,
                "first_name": (str(who) if is_name_only and who else None),
                "last_name": None,
                "username": (str(who) if who and not is_name_only else None),
                "date": None,
            }, 0)
        raise SetupError(EXIT_DAEMON_RUNNING, "daemon_running",
                         AGENT_DAEMON_DM_ADVISORY, AGENT_DAEMON_DM_FIX)

    sys.stderr.write(_scrub(
        f"Watching Telegram directly for a private message to "
        f"{'@' + username if username else 'the bot'} (up to {timeout} s). "
        f"Ask the person to open {link} and press Start.\n", run.token))
    sys.stderr.flush()
    start = _now()
    not_before = int(start) - DETECT_LOOKBACK_SECONDS
    deadline = _monotonic() + timeout
    last_failed = False
    backlog = False
    while True:
        candidate, others, code, _err, full = telegram_api._newest_private_chat(
            run.token, not_before=not_before)
        if candidate is not None:
            return _found(run, candidate, others)
        if code == 409:
            raise SetupError(
                EXIT_UPDATES_CONFLICT, "updates_conflict",
                telegram_api.UPDATES_CONFLICT_ADVISORY,
                "Stop the other program reading this bot's messages, or ask "
                "the person for their numeric Telegram id and pass it to "
                "`aipager setup --chat-id`.")
        if code in (401, 404):
            raise SetupError(
                EXIT_TOKEN_REJECTED, "token_rejected",
                "Telegram rejected the bot token.",
                "Check the token (copy it again from @BotFather), then run "
                "this again.")
        last_failed = code is None or code == 429 or code >= 500
        if not last_failed:
            backlog = full
        remaining = deadline - _monotonic()
        if remaining <= 0:
            break
        _sleep(min(DETECT_POLL_SECONDS, remaining))
    if last_failed:
        raise SetupError(
            EXIT_TELEGRAM_UNREACHABLE, "telegram_unreachable",
            "Could not reach Telegram to look for the person's message.",
            "Check the network connection, then run this again.")
    if backlog:
        # Without an offset Telegram shows only the oldest 100 unread
        # updates, so pressing Start again cannot help: say so.
        doc["warnings"].append({
            "code": "updates_backlog",
            "message": DETECT_BACKLOG_ADVISORY,
        })
        raise SetupError(
            EXIT_DETECT_TIMEOUT, "detect_timeout",
            f"No private message to the bot was found within {timeout} "
            f"seconds. {DETECT_BACKLOG_ADVISORY}",
            "Ask the person for their numeric Telegram id and pass it to "
            "`aipager setup --chat-id`.")
    raise SetupError(
        EXIT_DETECT_TIMEOUT, "detect_timeout",
        f"No private message to the bot arrived within {timeout} seconds.",
        f"Ask the person to open {link} and press Start (or send any "
        "message), then run this again, with a longer --timeout if needed.")


__all__ = ["cmd_detect_chat"]
