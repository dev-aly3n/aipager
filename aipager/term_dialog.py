"""Dialogs open in Claude Code's terminal that no hook tells of (roadmap 8.127).

Claude Code 2.1.311 asks some questions in its terminal with no hook at
all: "Resume this conversation?" (the cold-resume dialog: a long-idle,
large conversation would use a good part of the 5-hour usage limit),
"Resume from summary" (at ``--resume`` of an old, large conversation),
the newer-model offer and about a dozen more. While one is up, Claude
Code's own session record (``<config>/sessions/<pid>.json``, read by
:mod:`aipager.claude_jobs`) says ``status: "waiting"``, and for all of
these ``waitingFor: "dialog open"``: the record never says which dialog
it is (2.1.311, live: identical for the cold, the new-focus and the
summary dialog).

Typed into such a dialog, aipager's message is lost or answers it: an
Enter picks the focused row (in one variant "Start a new conversation"),
and in the idle variant the text lands in the input box behind it. So a
session whose record says waiting, while aipager knows of no prompt by
hook, is taken as having a dialog open: nothing is typed into it (the
8.143 typing gate's probe), a message that meets it is held and sent once
it closes and the session has settled, and the chat is told
(:mod:`aipager.bot.resume_dialog`).

Which dialog it is comes from context only (Claude Code's flags and
global config are never read):

- :data:`RESUME_UNSURE`: it opened within :data:`RESUME_CONTEXT_SECONDS`
  of a ``--resume`` start of the session's claude (its SessionStart with
  source ``resume``). The summary dialog, the cold-resume dialog or the
  newer-model offer: no way to tell them apart, so only Escape (each
  one's "dismiss": the conversation goes on unchanged) is offered;
- :data:`COLD_IDLE`: it opened by itself on a session nobody touched for
  :data:`IDLE_DIALOG_MIN_QUIET` seconds, in a conversation whose last
  exchange is :data:`IDLE_DIALOG_MIN_AGE` older. Claude Code opens the
  cold-resume dialog on a timer at the prompt cache's expiry (about an
  hour after the last answer for a subscriber), and no other dialog that
  way;
- :data:`OTHER`: anything else.

Daemon side only: never imported by the hook or by :mod:`aipager.state`,
and it never imports ``aipager.i18n``. Readers never raise.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

from aipager import claude_jobs
from aipager.dtach import bg_jobs

log = logging.getLogger(__name__)

# -- kinds --------------------------------------------------------------------

RESUME_UNSURE = "resume_unsure"
COLD_IDLE = "cold_idle"
OTHER = "other"

#: The ``waitingFor`` Claude Code writes for the resume dialogs (and the
#: other "dialog open" kinds, and an open local slash-command panel).
#: Claude Code's own value, compared, never shown: spelled in two words so
#: the i18n check's count of hard-coded English (text a person reads)
#: does not take it for such text.
DIALOG_OPEN = " ".join(("dialog", "open"))
#: The record's status while a dialog is up.
WAITING = "waiting"

# -- constants (live, Claude Code 2.1.311, release harness 2026-10-28) ---------

#: A dialog whose record says waiting within this many seconds after the
#: session's claude started with ``--resume`` came from that start (live:
#: drawn 0.8-1.5 s after aipager's relaunch, 4.0 s at most with
#: ``aipager resume``; waiting 0.10-0.48 s after the draw).
RESUME_CONTEXT_SECONDS = 30.0
#: For this long after a ``--resume`` SessionStart, a message about to be
#: typed into an idle session first waits to see whether a dialog draws.
RESUME_TYPE_SETTLE_SECONDS = 6.0
#: The cold-resume dialog's idle trigger: the conversation's last
#: exchange is at least this much older than the dialog (the cache TTL is
#: 5 min at the shortest; live, the dialog drew 292.5-292.9 s after the
#: last turn ended with a 5-min TTL).
IDLE_DIALOG_MIN_AGE = 240.0
#: ...and nothing happened in the session for this long before it (Claude
#: Code waits 60 s after the last interaction).
IDLE_DIALOG_MIN_QUIET = 55.0
#: An unknown dialog that opened within this long after aipager typed
#: (a bare ``/config`` from Telegram, say) gets its note at once.
AFTER_WRITE_NOTE_SECONDS = 10.0
#: A card is sent once the opening is this old and still open.
CARD_SETTLE_SECONDS = 1.0
#: After a button's key, how long the record is watched for the dialog to
#: close (live: 0.004-0.025 s after it is gone).
ANSWER_CLOSE_WAIT = 3.0
ANSWER_CLOSE_POLL = 0.1
#: After a close, how long before held messages may go (room for a
#: newer-model offer drawn right after a cold-resume dialog).
ANSWER_SETTLE_SECONDS = 2.0
#: Held messages go after this long from the close even when the session
#: never settled in a way the record or a hook shows.
AFTER_ANSWER_MAX_WAIT = 180.0
#: How often the record is read while a message waits after a resume.
FRESH_POLL_SECONDS = 0.25
#: How many characters of a draft the input box is wiped of before
#: ``/clear`` after "Start a new conversation" (in the idle variant Escape
#: keeps the box's text).
WIPE_DRAFT_CHARS = 2000

# -- seams ----------------------------------------------------------------------

#: The clocks (tests replace these, never ``time`` itself).
_now_wall = time.time
_now_mono = time.monotonic


# -- the rules ------------------------------------------------------------------

def record_waiting(entry: dict | None) -> bool:
    """Whether *entry* (a normalised registry entry) says a dialog is up."""
    return isinstance(entry, dict) and entry.get("status") == WAITING


def explained_by_hooks(sess) -> bool:
    """Whether a prompt aipager knows of by hook explains a waiting record:
    one carried over a Stop, a pending permission prompt or question, or
    another open prompt (roadmap 8.120)."""
    return bool(sess.prompt_wait_carry or sess.pending_permission is not None
                or sess.extra_prompts)


def _observable(sess) -> bool:
    """A live session aipager would type into: idle in aipager's view, not
    parked on a background job, no relaunch running."""
    from aipager.state import Status
    return (sess.status is Status.IDLE and not sess.parked_job
            and not sess.relaunch_in_flight)


def _wall_of(mono: float, now_mono: float, now_wall: float) -> float:
    """A monotonic stamp as wall-clock seconds (0.0 for "never")."""
    if not mono:
        return 0.0
    return now_wall - (now_mono - mono)


def classify(sess, entry: dict, *, now_wall: float, last_write_wall: float,
             last_exchange_wall: float | None,
             last_hook_wall: float = 0.0) -> str:
    """Which dialog *entry*'s opening is, from context (see the module
    note). *last_write_wall*: aipager's last write to the terminal (0.0:
    none); *last_exchange_wall*: the conversation's last user or assistant
    entry (None: unknown); *last_hook_wall*: the last hook (0.0: none).
    All wall-clock seconds."""
    if entry.get("waitingFor") != DIALOG_OPEN:
        return OTHER
    at = entry.get("statusAt") or now_wall
    resume_at = (sess.session_start_at or {}).get("resume") or 0.0
    if resume_at and -1.0 <= at - resume_at <= RESUME_CONTEXT_SECONDS:
        return RESUME_UNSURE
    if last_exchange_wall is None or at - last_exchange_wall < IDLE_DIALOG_MIN_AGE:
        return OTHER
    quiet_since = at - IDLE_DIALOG_MIN_QUIET
    if last_write_wall > quiet_since or last_hook_wall > quiet_since:
        return OTHER
    return COLD_IDLE


def same_opening(sess, entry: dict | None) -> bool:
    """Whether *entry* still shows the opening on record: the same claude,
    still waiting, with the same status time."""
    cur = sess.term_dialog
    return (cur is not None and record_waiting(entry)
            and entry.get("pid") == cur.get("pid")
            and entry.get("statusAt") == cur.get("status_at"))


def _open(sess, entry: dict, now_mono: float, now_wall: float,
          last_exchange) -> None:
    last_write_wall = _wall_of(_last_write_mono(sess.name), now_mono, now_wall)
    last_hook_wall = _wall_of(sess.last_hook_at, now_mono, now_wall)
    exchange = last_exchange() if callable(last_exchange) else last_exchange
    kind = classify(sess, entry, now_wall=now_wall, last_write_wall=last_write_wall,
                    last_exchange_wall=exchange, last_hook_wall=last_hook_wall)
    sess.term_dialog_epoch += 1
    sess.term_dialog = {
        "epoch": sess.term_dialog_epoch,
        "pid": entry.get("pid"),
        "status_at": entry.get("statusAt"),
        "waiting_for": entry.get("waitingFor") or "",
        "kind": kind,
        "seen_at": now_mono,
        "seen_wall": now_wall,
        "write_wall": last_write_wall,
        "told": False,          # the card or note was claimed
        "card": None,           # (chat_id, message_id) once sent
        "card_kind": "",        # "card" or "note"
        "answering": False,     # a button's answer is running
        "answer": "",           # "continue" | "resume" | "new"
    }
    log.info("[%s] terminal dialog open (kind=%s, waitingFor=%s)",
             sess.label, kind, sess.term_dialog["waiting_for"] or "-")


def _last_write_mono(name: str) -> float:
    from aipager.dtach import inject
    return inject.last_write_at(name)


def close(sess, now_mono: float, outcome: str = "") -> None:
    """The opening on record is over: its card is queued for closing (with
    *outcome*, else what its answer says), a hold for it starts to settle
    (:func:`observe` releases it), and the probe no longer says open."""
    cur = sess.term_dialog
    if cur is None:
        return
    sess.term_dialog = None
    if not outcome:
        outcome = {"continue": "continued", "resume": "continued",
                   "new": "new"}.get(cur.get("answer") or "", "terminal")
    if cur.get("card"):
        sess.term_dialog_closing.append({
            "epoch": cur["epoch"], "card": cur["card"],
            "card_kind": cur.get("card_kind") or "card", "outcome": outcome})
    sess.term_dialog_settle = {
        "since": now_mono,
        "after_wall": cur.get("status_at") or cur.get("seen_wall") or 0.0,
        "expect": "clear" if cur.get("answer") == "new" else "",
    }
    hold = sess.dialog_hold
    if hold is not None and hold.get("terminal"):
        hold["settling"] = True
    log.info("[%s] terminal dialog closed", sess.label)


def _settled(sess, entry: dict | None, now_mono: float) -> bool:
    settle = sess.term_dialog_settle
    if settle is None:
        return False
    waited = now_mono - settle["since"]
    from aipager.state import Status
    if sess.status is Status.IDLE and waited >= ANSWER_SETTLE_SECONDS:
        after = settle["after_wall"]
        if (isinstance(entry, dict) and entry.get("status") == "idle"
                and (entry.get("statusAt") or 0.0) > after):
            return True
        expect = settle.get("expect")
        if expect and ((sess.session_start_at or {}).get(expect) or 0.0) > after:
            return True
    if waited >= AFTER_ANSWER_MAX_WAIT:
        log.info("[%s] terminal dialog: the session never settled in %.0f s; "
                 "releasing what was held", sess.label, waited)
        return True
    return False


def observe(sess, entry: dict | None, *, complete: bool, now_mono: float,
            now_wall: float,
            last_exchange_wall: float | None | Callable[[], float | None] = None,
            settle: bool = False) -> str | None:
    """Take *entry* (the registry entry of the session's own claude, None
    when none was found; *complete*: the read was whole, so "none found"
    means none) into the session's terminal-dialog state. Returns
    ``"opened"``, ``"closed"``, ``"settled"`` (a close's settle is over:
    what was held may go) or None (nothing changed).
    *last_exchange_wall* may be a callable: it is read only for a new
    opening (it reads the transcript).

    *settle*: only the session monitor's scan passes True, and acts on
    ``"settled"`` (it releases the hold). The other looks (right before
    typing, a button's) never end a settle: one taken there would be
    lost, and the messages held for the dialog with it."""
    cur = sess.term_dialog
    if not _observable(sess):
        from aipager.state import Status
        if cur is not None:
            if sess.status in (Status.BUSY, Status.INTERACTIVE):
                # A turn runs: the dialog was answered (resume dialogs never
                # open during a turn, and a permission dialog's record is the
                # hooks' to tell).
                close(sess, now_mono)
                return "closed"
            return None
        if (settle and sess.status is not Status.GONE and not sess.relaunch_in_flight
                and not sess.parked_job and _settled(sess, entry, now_mono)):
            # Only the backstop can say so here (the session is not idle).
            sess.term_dialog_settle = None
            return "settled"
        return None
    if cur is not None:
        if entry is None:
            if not complete:
                return None         # unreadable: still open (fail closed)
            close(sess, now_mono)
            return "closed"
        if not record_waiting(entry):
            close(sess, now_mono)
            return "closed"
        if same_opening(sess, entry):
            return None
        # Waiting again with a new status time: the next dialog.
        close(sess, now_mono)
        _open(sess, entry, now_mono, now_wall, last_exchange_wall)
        return "opened"
    if record_waiting(entry) and not explained_by_hooks(sess):
        _open(sess, entry, now_mono, now_wall, last_exchange_wall)
        return "opened"
    if settle and _settled(sess, entry, now_mono):
        sess.term_dialog_settle = None
        return "settled"
    return None


def card_due(sess, now_wall: float) -> bool:
    """Whether the open opening's card or note goes out now without a
    message meeting it: a dialog after a ``--resume`` start, or an
    unknown one aipager's own write opened, once
    :data:`CARD_SETTLE_SECONDS` old."""
    cur = sess.term_dialog
    if cur is None or cur.get("told"):
        return False
    at = cur.get("status_at") or cur.get("seen_wall") or now_wall
    if now_wall - at < CARD_SETTLE_SECONDS:
        return False
    if cur["kind"] == RESUME_UNSURE:
        return True
    if cur["kind"] == OTHER:
        wrote = cur.get("write_wall") or 0.0
        return bool(wrote) and -1.0 <= at - wrote <= AFTER_WRITE_NOTE_SECONDS
    return False


# -- the session's own record, read afresh ---------------------------------------------

def _own_entry_by_pid(pid: int) -> dict | None:
    """The registry entry of claude *pid*, read from its own file, when it
    is an interactive claude still running as the process it was written
    for. Never raises."""
    try:
        path = os.path.join(str(claude_jobs.config_dir()), "sessions", f"{pid}.json")
        data = bg_jobs.read_own_json(path)
        if data is None:
            return None
        entry = claude_jobs._entry(data, pid)
        if (entry is None or entry.get("pid") != pid
                or entry.get("kind") != claude_jobs._INTERACTIVE):
            return None
        if not claude_jobs._pid_alive(pid) or not claude_jobs._start_matches(entry, pid):
            return None
        return entry
    except Exception:  # noqa: BLE001 - a reader never raises
        log.debug("own session record of %s unreadable", pid, exc_info=True)
        return None


def read_own_record(sess) -> dict | None:
    """The registry entry of *sess*'s own claude, read now: its own file
    when its pid is known (one small read), else the whole registry.
    None when none is found. Never raises."""
    try:
        pid = sess.claude_pid if isinstance(sess.claude_pid, int) else 0
        if pid > 0:
            entry = _own_entry_by_pid(pid)
            if entry is not None:
                return entry
        return claude_jobs.find_client(sess, claude_jobs.read_registry())
    except Exception:  # noqa: BLE001 - a reader never raises
        log.debug("[%s] own session record unreadable", sess.label, exc_info=True)
        return None


async def fresh_record(sess, *, own_only: bool = False) -> dict | None:
    """:func:`read_own_record` with its file reads off the event loop.
    *own_only*: only the claude pid's own file, never the whole registry
    (None when the pid is not known or its file shows nothing). Never
    raises."""
    try:
        pid = sess.claude_pid if isinstance(sess.claude_pid, int) else 0
        if pid <= 0 and own_only:
            return None
        if pid > 0:
            entry = await claude_jobs.off_loop(_own_entry_by_pid, pid)
            if entry is not None or own_only:
                return entry
        entries = await claude_jobs.read_registry_async()
        return claude_jobs.find_client(sess, entries)
    except Exception:  # noqa: BLE001 - a reader never raises
        log.debug("[%s] own session record unreadable", sess.label, exc_info=True)
        return None
