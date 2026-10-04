"""Per-session policy snapshot (Phase E).

The daemon resolves a session driver's *effective* safety rules and
writes them to ``/tmp/claude-policy-<session>.json`` on each Telegram
prompt. The PreToolUse hook (which can't see daemon memory) reads this
snapshot to decide whether to block a tool call. Origin is determined
separately by the hook (from the transcript marker); the snapshot just
carries the resolved rule sets + the owner bypass flag.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from pathlib import Path

from aipager import safety

log = logging.getLogger(__name__)


def snapshot_path(session_name: str) -> Path:
    return Path(f"/tmp/claude-policy-{session_name}.json")


# ---------------------------------------------------------------------------
# Queue-handoff (design.md "Hand Telegram's message queue over to Claude").
#
# One JSON note per Telegram-originated message, written at send time
# (``write_note``, called from ``session_ops._inject_prompt``) and consumed
# at pick-up by the ``UserPromptSubmit`` hook's matcher
# (``notify_hook._match_and_promote``). ``merge_snapshots`` is the single,
# pure implementation of "most restrictive of N notes" — the only thing the
# safety-invariant test needs to reason about, regardless of how the
# (heuristic, occasionally-wrong) matcher grouped them.
# ---------------------------------------------------------------------------


def note_driver_id(note: dict) -> int | None:
    """The Telegram user id that actually wrote a note's prompt, or
    ``None`` when unknown.

    Read ONLY from ``author_user_id`` (review rev-iter4-002, 2026-09-27):
    ``sender_key`` falls back to whoever last drove the session when a
    prompt is sent with no explicit sender (a Retry of someone else's
    prompt, ``/compact``). Recorded as the prompt's author, that fallback
    made an owner's SECOND Retry of a member's prompt run it with the
    owner's rights. A note without the field (written before this fix)
    has no known author, which sends a Retry of it to the floor."""
    if not isinstance(note, dict) or "author_user_id" not in note:
        return None
    uid = note.get("author_user_id")
    if isinstance(uid, int) and not isinstance(uid, bool) and uid:
        return uid
    return None


def notes_dir(session_name: str) -> Path:
    """Directory holding a session's not-yet-confirmed-picked-up notes.

    A plain function, like :func:`snapshot_path` — tests monkeypatch this
    name to redirect writes to ``tmp_path`` rather than real ``/tmp``.
    """
    return Path(f"/tmp/claude-notes-{session_name}")


# ---------------------------------------------------------------------------
# Hook-side turn state (roadmap 8.77, design F3).
#
# Claude Code fires ``UserPromptSubmit`` when it QUEUES a message sent while
# a turn runs, not when that message runs. The hook therefore cannot tell
# "a new turn starts" from "a message joins the running turn" by the event
# alone. This file says a turn is open: written at every UserPromptSubmit
# (``<task-notification>`` wake-ups included), removed at Stop, at
# SessionStart, and by the daemon when it stops, kills, restarts or launches
# the session (an interrupt may fire no Stop). While it is present a pick-up
# can only narrow the running turn (``snapshot_for_prompt``).
#
# Fail closed: a clear that never happens only makes the NEXT fresh turn
# merge with the previous turn's rules, i.e. stricter for one turn (its own
# Stop clears it), never wider. It lives in the notes dir, under the
# protected ``/tmp/claude-notes-*`` floor path, so a restricted turn cannot
# remove it itself.
# ---------------------------------------------------------------------------

#: The turn-open file's name inside :func:`notes_dir`. Not a note: every
#: note reader only looks at ``.json`` files.
TURN_OPEN_FILE = "turn-open"


def turn_open_path(session_name: str) -> Path:
    return notes_dir(session_name) / TURN_OPEN_FILE


def mark_turn_open(session_name: str) -> None:
    """Record that a turn is running in *session_name* (best-effort)."""
    d = notes_dir(session_name)
    path = d / TURN_OPEN_FILE
    try:
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(repr(time.time()), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.debug("could not mark turn open %s", path, exc_info=True)


def clear_turn_open(session_name: str) -> None:
    """Record that no turn is running in *session_name* (best-effort)."""
    try:
        turn_open_path(session_name).unlink(missing_ok=True)
    except OSError:
        pass


def turn_is_open(session_name: str) -> bool:
    """True while a turn is running (the turn-open file exists). An error
    other than "not there" reads as open: the stricter answer."""
    try:
        os.stat(turn_open_path(session_name))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


# A non-empty ``allow_tools`` is a whitelist; an EMPTY one means "no
# restriction from this axis" (``safety.tool_violation``: ``if allow_tools
# and tool_name not in allow_tools``). A literal set-intersection of allow
# lists is therefore wrong: empty ∩ non-empty = empty = unrestricted, which
# is WIDER than the non-empty contributor. When every non-empty contributor's
# allow list has been intersected down to nothing (two notes whitelisting
# disjoint tools), the merge must not fall back to "empty = unrestricted" —
# it substitutes this sentinel instead, which cannot equal any real tool
# name, so ``tool_violation`` denies every tool rather than allowing all of
# them.
_ALLOW_TOOLS_SENTINEL: tuple[str, ...] = ("\x00none",)

# The built-in safety floor: no bypass, no tool restriction beyond the
# hard-coded path/bash denies. Extracted here so BOTH fallback sites —
# ``enforce.decide()``'s "no snapshot on disk" branch and
# ``merge_snapshots([])``'s "no outstanding notes" branch — read the exact
# same object rather than two hand-maintained copies that could drift.
# Callers must treat this as read-only; a caller that wants a mutable copy
# should ``dict(FLOOR_SNAPSHOT)`` rather than mutate it in place.
# It is the answer for a turn nobody can be held to, so it is at least as
# strict as the built-in ``user`` role: no code-running tools, writes
# confined (2026-09-27 — it used to leave Bash on).
FLOOR_SNAPSHOT: dict = {
    "bypass_safety": False,
    "confine_writes": True,
    "deny_tools": list(safety.RESTRICTED_DENY_TOOLS),
    "allow_tools": [],
    "deny_paths_no_access": list(safety.DENY_PATHS_NO_ACCESS),
    "deny_paths_no_write": list(safety.DENY_PATHS_NO_WRITE),
    "deny_bash_patterns": list(safety.DENY_BASH_PATTERNS),
}


def resolve_snapshot(role, scope, member, style_text: str = "",
                     reply_context: str = "") -> dict:
    """Compute the effective rule sets for a driver (pure).

    ``role`` is a policy.Role (or None), ``scope`` a scope.Scope (or
    None), ``member`` a scope.Member (or None). Deny lists union across
    scope + role + member; the safety floor (paths + bash) always
    applies on top of any role/member additions.

    ``style_text`` is unrelated to safety — it's the precomputed
    `/settings` reply-style instruction block (see
    ``aipager.preferences.style_text``) for the ``UserPromptSubmit``
    hook to print verbatim. Carried on this snapshot rather than a
    second file because it's the same "what does the hook need to know
    about this turn" write, already firing at the right time.

    ``reply_context`` is likewise unrelated to safety — the rendered
    reply-pointer wording (design.md "reply context" feature) for the
    same hook to print alongside ``style_text``. Defaulted to ``""`` so
    a caller that forgets to pass it clears any stale value from a
    prior turn rather than leaking it (the staleness guard — see
    ``write_snapshot`` below and ``session_ops._inject_prompt``).
    """
    bypass_safety = bool(role and role.bypass_safety)
    bypass_role_denies = bool(role and role.bypass_role_denies)

    deny_tools: set[str] = set()
    allow_tools: set[str] = set()
    no_access: set[str] = set(safety.DENY_PATHS_NO_ACCESS)
    no_write: set[str] = set(safety.DENY_PATHS_NO_WRITE)
    bash: set[str] = set(safety.DENY_BASH_PATTERNS)

    if role is None:
        # No role: a sender aipager cannot attribute. Held to the floor's
        # tools, like a turn with no note at all (2026-09-27).
        deny_tools |= set(FLOOR_SNAPSHOT["deny_tools"])
    if not bypass_role_denies:
        if scope:
            deny_tools |= set(scope.deny_tools)
        if role:
            deny_tools |= set(role.deny_tools)
            allow_tools |= set(role.allow_tools)
            no_access |= set(role.deny_paths_no_access)
            no_write |= set(role.deny_paths_no_write)
            bash |= set(role.deny_bash_patterns)
        if member:
            deny_tools |= set(getattr(member, "deny_tools", ()))
            allow_tools |= set(getattr(member, "allow_tools", ()))

    return {
        "origin": "telegram",
        "bypass_safety": bypass_safety,
        # Writes confined to the session's folder + scratchpad (roadmap
        # 8.50) for every sender without the owner's bypass or an
        # admin's role-deny bypass — an unknown sender (no role) too.
        "confine_writes": not (bypass_safety or bypass_role_denies),
        "deny_tools": sorted(deny_tools),
        "allow_tools": sorted(allow_tools),
        "deny_paths_no_access": sorted(no_access),
        "deny_paths_no_write": sorted(no_write),
        "deny_bash_patterns": sorted(bash),
        "style_text": style_text,
        "reply_context": reply_context,
    }


def write_snapshot(session_name: str, role, scope, member,
                   style_text: str = "", reply_context: str = "") -> None:
    """Atomic-write the resolved snapshot for a session (best-effort).

    ``reply_context`` defaults to ``""`` — every one of the (12+)
    ``_inject_prompt`` call sites unrelated to a reply passes nothing,
    so the snapshot is overwritten with an explicit "not a reply" on
    every turn rather than silently keeping a prior turn's value.
    """
    data = resolve_snapshot(role, scope, member, style_text, reply_context)
    path = snapshot_path(session_name)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        log.debug("could not write policy snapshot %s", path, exc_info=True)


def read_snapshot(session_name: str) -> dict | None:
    """Read a session's snapshot, or None if absent/unreadable."""
    try:
        return json.loads(snapshot_path(session_name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_merged_snapshot(session_name: str, snap: dict) -> None:
    """Atomic-write an already-computed snapshot dict (best-effort).

    Used by the ``UserPromptSubmit`` hook to persist the output of
    :func:`merge_snapshots` — the sole writer of the canonical
    ``/tmp/claude-policy-<session>.json`` now that ``_inject_prompt``
    writes a per-message note instead of touching it directly (design.md
    "Chosen approach"). Shares :func:`write_snapshot`'s atomic-replace +
    0600 pattern; kept separate so :func:`write_snapshot`'s own
    role/scope/member-based signature stays unchanged.
    """
    path = snapshot_path(session_name)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(snap), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        log.debug("could not write merged policy snapshot %s", path,
                  exc_info=True)


# ---- Per-message notes -----------------------------------------------------

# Default for write_note's author_user_id: derive it from sender_key.
_FROM_SENDER_KEY = object()


def write_note(
    session_name: str, role, scope, member, *,
    msg_id: int | None, chat_id: int | None,
    sender_key: tuple[int, int] | None,
    body: str, raw_text: str,
    style_text: str = "", reply_context: str = "",
    author_user_id=_FROM_SENDER_KEY,
    scope_mode: bool = False,
) -> Path | None:
    """Write one per-message policy note (design.md "queue handoff").

    ``scope_mode`` marks a note written by a daemon running with scopes
    (``aipager.yaml``). Only such notes make a slash-command record count
    as a Telegram prompt at the hook (roadmap 8.74,
    ``enforce._command_from_telegram``): in personal and legacy team mode
    every Telegram prompt is the operator's and stays unrestricted, as
    before.

    Carries the same resolved permission fields ``write_snapshot`` would
    have written (via :func:`resolve_snapshot`), plus everything the
    pick-up matcher and the daemon need: the originating ``msg_id`` /
    ``chat_id``, ``sender_key`` (``(scope_chat_id, driver_user_id)``, for
    the mixed-sender hold), ``body`` (the literal text sent to the pty,
    marker included — what the matcher searches for) and ``raw_text``
    (before the marker, so Retry does not double it), and ``queued_at``
    (wall-clock, for the TTL prune in :func:`list_outstanding_notes`).

    Best-effort, mirroring :func:`write_snapshot`: any I/O failure is
    logged and swallowed rather than raised, so a full ``/tmp`` or a
    permissions problem never blocks sending a prompt. Returns the
    written path, or ``None`` on failure.
    """
    note = resolve_snapshot(role, scope, member, style_text, reply_context)
    note["msg_id"] = msg_id
    note["chat_id"] = chat_id
    note["sender_key"] = list(sender_key) if sender_key is not None else None
    # The prompt's real author (see note_driver_id). Callers that know
    # the sender was only a fallback pass author_user_id=None explicitly.
    if author_user_id is _FROM_SENDER_KEY:
        author_user_id = sender_key[1] if sender_key else None
    note["author_user_id"] = author_user_id if author_user_id else None
    note["body"] = body
    note["raw_text"] = raw_text
    note["queued_at"] = time.time()
    note["scope_mode"] = scope_mode is True

    d = notes_dir(session_name)
    try:
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    except OSError:
        log.debug("could not create notes dir %s", d, exc_info=True)
        return None

    fname = f"{int(note['queued_at'] * 1_000_000)}-{secrets.token_hex(2)}.json"
    path = d / fname
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(note), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        log.debug("could not write note %s", path, exc_info=True)
        return None
    return path


def list_outstanding_notes(
    session_name: str, *, now: float | None = None,
    expired_out: list | None = None,
) -> list[dict]:
    """Every not-yet-consumed note for a session, oldest first.

    TTL-prunes as a side effect: any note whose ``queued_at`` is older
    than ``QUEUE_MAX_AGE_SECONDS`` is unlinked from disk (best-effort)
    and excluded from the returned list — a note this old will never
    plausibly still be the thing a live pick-up is matching against, and
    dropping it only ever makes a later fallback merge MORE restrictive
    (never less), so nothing on the safety side is lost by bounding the
    directory this way. Pass ``expired_out`` (a list) to also collect the
    pruned notes themselves — callers that need to raise a best-effort
    notice about them (e.g. the ``UserPromptSubmit`` hook) pass a list;
    everyone else leaves it ``None`` and pays nothing extra.

    Each returned dict carries an internal ``"_path"`` key (its file's
    ``Path``) for :func:`delete_notes` — not part of the JSON on disk,
    and not meaningful to callers beyond passing the dict back in.
    ``now`` is injectable for tests; real ``time.time()`` otherwise.
    """
    # Imported here, not at module level: every hook event imports this
    # module, and aipager.state pulls in the config, scope and yaml modules.
    from aipager.state import QUEUE_MAX_AGE_SECONDS

    now = now if now is not None else time.time()
    cutoff = now - QUEUE_MAX_AGE_SECONDS
    d = notes_dir(session_name)
    try:
        entries = sorted(d.iterdir())
    except OSError:
        return []

    out: list[dict] = []
    for p in entries:
        if p.suffix == _RAN_SUFFIX and not p.with_suffix(".json").exists():
            # Its note is gone (consumed, swept): the mark means nothing.
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        if p.suffix != ".json":
            continue
        try:
            note = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(note, dict):
            continue
        try:
            queued_at_f = float(note.get("queued_at"))
        except (TypeError, ValueError):
            queued_at_f = now
        note["_path"] = p
        if _ran_long_enough(p, now):
            # A command that already ran (roadmap 8.37): not a message
            # anyone is waiting on, so not reported as expired either.
            _unlink_note(p)
            continue
        if queued_at_f < cutoff:
            _unlink_note(p)
            if expired_out is not None:
                expired_out.append(note)
            continue
        out.append(note)

    out.sort(key=lambda n: (n.get("queued_at") if isinstance(
        n.get("queued_at"), (int, float)) else now, str(n.get("_path"))))
    return out


#: How long a slash command's note outlives the moment it ran (roadmap
#: 8.37). A local command (``/model``) fires no prompt hook, so nothing
#: would ever consume its note; a prompt-type one fires UserPromptSubmit
#: within a fraction of a second of the Enter and needs its note there to
#: be matched.
RAN_COMMAND_NOTE_GRACE_SECONDS: float = 10.0


#: The mark :func:`mark_command_notes_ran` leaves beside a note. A file of
#: its own, never a rewrite of the note: the pick-up hook may be deleting
#: that note at the same moment, and a rewrite could bring it back.
_RAN_SUFFIX = ".ran"


def _is_command_note(note: dict) -> bool:
    return str(note.get("raw_text") or "").lstrip().startswith("/")


def _unlink_note(path: Path) -> None:
    """Remove a note file and its ran mark (best-effort)."""
    for p in (path, path.with_suffix(_RAN_SUFFIX)):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def _ran_long_enough(path: Path, now: float) -> bool:
    try:
        ran_at = float(path.with_suffix(_RAN_SUFFIX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return now - ran_at >= RAN_COMMAND_NOTE_GRACE_SECONDS


def mark_command_notes_ran(
    session_name: str, notes: list[dict] | None = None, *,
    msg_id: int | None = None, chat_id: int | None = None,
    now: float | None = None,
) -> int:
    """Record that slash commands have run, so their notes expire
    :data:`RAN_COMMAND_NOTE_GRACE_SECONDS` later instead of lingering as
    "outstanding" for a day (roadmap 8.37). A lingering one read as a
    queued message to ``/clearqueue`` and, at the head of the queue,
    stopped every later pick-up from matching.

    Marks *notes* (from :func:`list_outstanding_notes`), or, when that is
    ``None``, the outstanding notes of message *msg_id* in chat *chat_id*.
    Only a command's note (its ``raw_text`` starts with ``/``) is ever
    marked, and a mark already made is kept. The note itself is never
    rewritten. Best-effort; returns how many notes were marked.
    """
    if notes is None:
        if msg_id is None:
            return 0
        notes = [n for n in list_outstanding_notes(session_name)
                 if n.get("msg_id") == msg_id and n.get("chat_id") == chat_id]
    stamp = time.time() if now is None else now
    marked = 0
    for note in notes:
        path = note.get("_path")
        if path is None or not _is_command_note(note):
            continue
        mark = Path(path).with_suffix(_RAN_SUFFIX)
        if mark.exists():
            continue
        tmp = mark.with_name(mark.name + ".tmp")
        try:
            tmp.write_text(repr(stamp), encoding="utf-8")
            os.replace(tmp, mark)
        except OSError:
            log.debug("could not mark note %s ran", path, exc_info=True)
            continue
        marked += 1
    return marked


def delete_notes(session_name: str, notes: list[dict]) -> None:
    """Best-effort unlink of a specific set of notes (matched or expired).

    Never touches any note NOT in ``notes`` — an unmatched note stays
    outstanding and feeds the next merge (design.md's soundness
    argument: an over- or under-eager match can only make a later turn
    MORE restrictive, never less).
    """
    for note in notes:
        path = note.get("_path")
        if path is None:
            continue
        _unlink_note(Path(path))


def match_notes_prefix_run(outstanding: list[dict], content: str) -> list[dict]:
    """The longest PREFIX run of *outstanding* (oldest-first) whose
    ``body`` appears, in order, as a substring of *content*.

    Pure — no I/O, no TTL pruning, no snapshot writes. This is the exact
    matching loop ``notify_hook._match_and_promote`` has always used
    (design.md "turn anchor follows consumption"), extracted so it can
    also drive absorption detection (:func:`consume_notes_matching`)
    without duplicating the substring-run rule. Walks *outstanding* in
    order, searching for each note's ``body`` starting where the
    previous match left off; the first note whose body can't be found
    stops the run — everything before it is "matched", everything from
    it onward is not.
    """
    matched: list[dict] = []
    cursor = 0
    for note in outstanding:
        body = note.get("body") or ""
        if not body:
            break
        idx = content.find(body, cursor)
        if idx == -1:
            break
        matched.append(note)
        cursor = idx + len(body)
    return matched


def _first_token(text) -> str:
    parts = str(text or "").split(None, 1)
    return parts[0] if parts else ""


def match_notes_for_prompt(outstanding: list[dict], prompt: str) -> list[dict]:
    """The notes a ``UserPromptSubmit`` with *prompt* consumes:
    :func:`match_notes_prefix_run`, plus, for a slash command, the oldest
    command note that sent it when the run missed it (roadmap 8.74).

    A slash command is never batched with other messages, but a note
    still outstanding ahead of it (a local command such as ``/model`` or
    ``/clear``, which fires no prompt hook and lingers for
    :data:`RAN_COMMAND_NOTE_GRACE_SECONDS`) stops the prefix run at once.
    Unconsumed, the command's turn would keep the previous turn's
    snapshot and provenance, and the hook would read it as a terminal
    prompt: no rules at all. So for a prompt whose first token is ``/x``
    the oldest outstanding note whose body is in the prompt and whose own
    first token is ``/x`` is consumed too. Taking it can only hold the
    turn to that note's sender (fail closed). A command typed in the
    terminal at the same moment can take a Telegram note of the same text;
    the Telegram command, picked up later with no note, is still named by
    the bodies :func:`snapshot_for_prompt` carries forward. Pure.
    """
    matched = match_notes_prefix_run(outstanding, prompt)
    name = _first_token(prompt)
    if not name.startswith("/"):
        return matched
    if any(_first_token(n.get("body")) == name for n in matched):
        return matched
    for note in outstanding:
        if note in matched:
            continue
        body = note.get("body") or ""
        if body and _first_token(body) == name and body.strip() in prompt:
            return [*matched, note]
    return matched


def consume_notes_matching(session_name: str, content: str) -> list[dict]:
    """Match *content* (a transcript ``queue-operation`` line's own
    ``content`` field) against *session_name*'s outstanding notes and
    delete whichever ones matched — the absorption/pick-up-adjacent half
    of design.md "turn anchor follows consumption".

    Returns the matched notes (oldest first, :func:`match_notes_prefix_run`'s
    own order) so the caller can react/track/re-anchor on them. Returns
    ``[]`` when nothing outstanding matches — including a note the 5s
    Stop-triggered sweep (:func:`expire_notes_after_turn_end`) already
    swept: :func:`list_outstanding_notes` simply won't find it (R7 — the
    sweep stays the safety net, this function never resurrects anything
    it already dropped).

    Deliberately does NOT touch the merged policy snapshot
    (``merge_snapshots``/``write_merged_snapshot``) — unlike
    ``notify_hook._match_and_promote``, this path has no PreToolUse
    enforcement to feed: a message absorbed mid-turn never gets its own
    ``UserPromptSubmit``, so its safety fields were never merged in
    either, before or after this feature (spec.md: the notes' TTL and
    queue safety semantics are out of scope here). This function's only
    job is telling the reply-target machinery which notes were consumed,
    in order.

    Do NOT make ``notify_hook._match_and_promote`` call this function —
    that would call :func:`list_outstanding_notes` TWICE per pick-up
    (once here, once for ``_match_and_promote``'s own
    ``expired_out=expired`` collection), and ``list_outstanding_notes``
    TTL-prunes as a side effect: the first call would silently swallow
    the truly-expired notes before the second call ever sees them,
    under-reporting ``expired`` on the datagram. ``_match_and_promote``
    instead calls :func:`match_notes_prefix_run` directly, reusing ONLY
    the pure matcher.
    """
    outstanding = list_outstanding_notes(session_name)
    consumed = match_notes_prefix_run(outstanding, content)
    if consumed:
        delete_notes(session_name, consumed)
    return consumed


# Grace window for the Stop-triggered sweep below. A message injected
# into an already-BUSY session goes straight into Claude Code's own
# input handling; whether it becomes a fresh UserPromptSubmit (the
# normal `_match_and_promote` pick-up path deletes its note right
# then) or gets silently folded into the turn that was already running
# (no fresh UserPromptSubmit ever fires for it — the leak this fixes)
# is Claude Code's call, not ours, and both outcomes look identical
# from here until one of them happens. A genuine pick-up — including
# Claude Code auto-resubmitting queued input the instant a turn's Stop
# fires — completes within low single-digit milliseconds in practice
# (a hook round-trip plus one UDP datagram); 5s is generously larger
# than that while being negligible next to how long a real turn runs,
# so the sweep below essentially never races a legitimate pick-up.
MID_TURN_NOTE_GRACE_SECONDS: float = 5.0


async def expire_notes_after_turn_end(
    session_name: str, pre_notes: list[dict] | None = None, *,
    grace: float | None = None,
) -> int:
    """Sweep away notes that were outstanding when a session's turn
    ended and are STILL outstanding after a short grace period.

    Call once per Stop/StopFailure (turn end) event, with ``pre_notes``
    captured (:func:`list_outstanding_notes`) BEFORE any turn-end
    side effect (notably ``_drain_next_queued``, which may inject a
    brand-new prompt and write a brand-new note) has had a chance to
    run — that ordering is what keeps this sweep from ever touching a
    note some LATER turn wrote, even if that note happens to still be
    outstanding when the grace period elapses. If ``pre_notes`` is
    omitted, it's captured right here instead (fine for direct/test
    callers that don't need that ordering guarantee).

    After ``grace`` seconds (:data:`MID_TURN_NOTE_GRACE_SECONDS` by
    default), any of ``pre_notes`` still present on disk is deleted —
    the turn they belonged to has already ended, so if the normal
    pick-up path (a fresh ``UserPromptSubmit`` matching and deleting
    the note) hasn't happened by now, it isn't going to: the message
    was silently absorbed into the turn that just finished. Deleting
    the note makes a LATER merge AT LEAST as restrictive in the common
    case (the same soundness argument :func:`list_outstanding_notes`'s
    TTL prune relies on) — with one narrow, deliberately-accepted
    exception: if a genuine (not absorbed) resubmit takes longer than
    ``grace`` and the swept note was the note's session's ONLY
    outstanding one, the next merge falls back to
    :data:`FLOOR_SNAPSHOT` instead of that note's own resolved rules.
    For the common ``bypass_safety`` case this is still safe (the floor
    denies more, never less); for a team/scope-mode member whose ROLE
    adds ``deny_tools``/``deny_paths`` beyond the hardcoded floor, that
    turn would then run under fewer restrictions than the role
    configures until the next note re-establishes them. Review
    rev-iter1-001: accepted because it requires the intersection of
    team/scope mode + a role with extra denies + a resubmit slower than
    ``MID_TURN_NOTE_GRACE_SECONDS`` + no other outstanding note — far
    narrower than the 24h-TTL status quo this replaces for the common
    case. Notes written AFTER this snapshot (e.g. by a queued message
    the same turn-end drains into a fresh BUSY turn) are never touched,
    matched or not.

    Best-effort and safe to call with nothing outstanding (returns 0
    immediately, no sleep). Returns the number of notes removed.
    """
    if pre_notes is None:
        pre_notes = list_outstanding_notes(session_name)
    if not pre_notes:
        return 0
    await asyncio.sleep(MID_TURN_NOTE_GRACE_SECONDS if grace is None else grace)
    still_paths = {n.get("_path") for n in list_outstanding_notes(session_name)}
    survivors = [n for n in pre_notes if n.get("_path") in still_paths]
    if survivors:
        delete_notes(session_name, survivors)
    return len(survivors)


def clear_notes_dir(session_name: str) -> int:
    """Delete every outstanding note for a session (best-effort).

    Called by Stop, ``/clearqueue`` (and the Mini App's equivalent
    route), and session GONE/kill cleanup — so nothing lingers to
    restrict an unrelated future turn for the rest of its TTL. Returns
    the number of note files actually removed. The turn-open file is not
    a note and stays (:func:`clear_turn_open` removes it).
    """
    d = notes_dir(session_name)
    try:
        entries = list(d.iterdir())
    except OSError:
        return 0
    removed = 0
    for p in entries:
        if p.name == TURN_OPEN_FILE:
            # Not a note: clearing the queue mid-turn must not make the
            # running turn look over (roadmap 8.77). Session teardown
            # clears it with clear_turn_open.
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass
    return removed


def outstanding_sender_keys(
    session_name: str, *, max_age: float | None = None, now: float | None = None,
) -> set[tuple[int, int]]:
    """``{sender_key, ...}`` for outstanding notes younger than ``max_age``.

    Used by the mixed-sender hold (design.md): an inbound prompt from a
    Telegram user different from whoever already has a note outstanding
    must be held rather than merged in blind — this is what keeps two
    different humans' permissions from silently combining into one turn.

    ``max_age`` defaults to :data:`aipager.state.MIXED_SENDER_HOLD_WINDOW_SECONDS`
    — deliberately much shorter than :func:`list_outstanding_notes`'s own
    (24h) TTL. That longer TTL bounds a note's contribution to
    ``merge_snapshots``' safety-floor computation and must stay generous;
    THIS bound only decides how long a note may keep holding a
    different-looking sender's messages, which is a much narrower
    window in practice (see the constant's own comment). ``now`` is
    injectable for tests, exactly like :func:`list_outstanding_notes`.
    """
    from aipager.state import MIXED_SENDER_HOLD_WINDOW_SECONDS

    now = now if now is not None else time.time()
    cutoff_age = MIXED_SENDER_HOLD_WINDOW_SECONDS if max_age is None else max_age
    out: set[tuple[int, int]] = set()
    for note in list_outstanding_notes(session_name, now=now):
        try:
            age = now - float(note.get("queued_at"))
        except (TypeError, ValueError):
            age = 0.0  # malformed/missing timestamp: treat as fresh, don't hide it
        if age > cutoff_age:
            continue
        key = note.get("sender_key")
        if isinstance(key, list) and len(key) == 2:
            out.add((key[0], key[1]))
    return out


def queue_depth_parts(sess) -> tuple[int, int]:
    """``(queued, outstanding)`` — the two components summed by
    :func:`combined_queue_depth`.

    Split out so a display surface can show BOTH counts instead of one
    opaque total: a pile of stale/orphaned notes and a real held
    message are indistinguishable in a bare integer (the live incident
    this fixes: "Queue 16 pending" was 15 stale notes and one real
    message — intent.md).
    """
    return len(sess.pending_queue), len(list_outstanding_notes(sess.name))


def combined_queue_depth(sess) -> int:
    """``len(pending_queue) + len(outstanding notes)`` for a session.

    THE single place both chat (Stop's ack, ``/clearqueue``) and the
    Mini App (``queue_depth``, the clearqueue route) compute "how many
    prompts are behind this session's current turn", so the two
    surfaces can never disagree about the count (design.md file plan).
    Takes the whole session object (duck-typed: needs only ``.name`` and
    ``.pending_queue``) rather than a bare name, mirroring
    :func:`clear_session_files`'s shape.
    """
    queued, outstanding = queue_depth_parts(sess)
    return queued + outstanding


def merge_snapshots(notes: list[dict]) -> dict:
    """The single, pure implementation of "most restrictive of N notes".

    ``notes`` is any list of note dicts (as returned by
    :func:`list_outstanding_notes`, or a subset of them) — this function
    never touches disk and never mutates its input. Order of ``notes``
    does not affect the safety fields (they are ANDed/unioned/
    intersected, all order-independent); the LAST note by ``queued_at``
    supplies ``style_text``/``reply_context`` (those aren't safety
    fields — "what should the hook print for this turn" is naturally
    "whatever the most recent contributor asked for").

    At ``notes == []`` returns :data:`FLOOR_SNAPSHOT` exactly (the
    "empty floor" promotion path) — the most restrictive answer when
    there is nothing to reason from at all.

    The merge never widens:

    - ``bypass_safety`` is True only if EVERY note's is True (vacuously
      False at n=0) — ANDed, not ORed.
    - Every ``deny_*`` list is the UNION across notes — a superset of
      every contributor's own list.
    - ``allow_tools`` is the tricky one. A non-empty ``allow_tools`` is a
      whitelist; an EMPTY one means "no restriction from this axis" —
      see ``safety.tool_violation``. A literal set-intersection of allow
      lists is therefore WRONG in the dangerous direction: empty ∩
      non-empty = empty = unrestricted, i.e. WIDER than the non-empty
      contributor. So this merge intersects only the NON-EMPTY allow
      lists (ignoring contributors that impose no restriction on this
      axis at all); if every contributor's allow_tools is empty, the
      merge's is empty too (no note restricts, so nothing should be
      restricted). If the intersection of the non-empty lists is itself
      empty (two notes whitelisting disjoint tools), the merge
      substitutes :data:`_ALLOW_TOOLS_SENTINEL` — a value that can never
      equal a real tool name — so ``tool_violation`` denies every tool
      rather than reading the empty result as "unrestricted".
    """
    if not notes:
        return dict(FLOOR_SNAPSHOT)

    ordered = sorted(notes, key=lambda n: n.get("queued_at") or 0)

    bypass_safety = all(bool(n.get("bypass_safety")) for n in ordered)
    # ORed: confined if any contributor is. A note without the field
    # (written before it existed) counts as confined unless it bypasses.
    confine_writes = any(
        n.get("confine_writes") is not False and not n.get("bypass_safety")
        for n in ordered
    )

    deny_tools: set[str] = set()
    deny_paths_no_access: set[str] = set()
    deny_paths_no_write: set[str] = set()
    deny_bash_patterns: set[str] = set()
    non_empty_allow_lists: list[set[str]] = []

    for n in ordered:
        deny_tools |= set(n.get("deny_tools") or ())
        deny_paths_no_access |= set(n.get("deny_paths_no_access") or ())
        deny_paths_no_write |= set(n.get("deny_paths_no_write") or ())
        deny_bash_patterns |= set(n.get("deny_bash_patterns") or ())
        allow = n.get("allow_tools") or ()
        if allow:
            non_empty_allow_lists.append(set(allow))

    if not non_empty_allow_lists:
        allow_tools: list[str] = []
    else:
        intersected = set.intersection(*non_empty_allow_lists)
        allow_tools = sorted(intersected) if intersected else list(
            _ALLOW_TOOLS_SENTINEL)

    last = ordered[-1]
    return {
        "origin": "telegram",
        "bypass_safety": bypass_safety,
        "confine_writes": confine_writes,
        "deny_tools": sorted(deny_tools),
        "allow_tools": allow_tools,
        "deny_paths_no_access": sorted(deny_paths_no_access),
        "deny_paths_no_write": sorted(deny_paths_no_write),
        "deny_bash_patterns": sorted(deny_bash_patterns),
        "style_text": last.get("style_text") or "",
        "reply_context": last.get("reply_context") or "",
    }


# The line ``session_ops._inject_prompt`` prepends to every Telegram
# message in team/scope mode — the same test ``enforce._origin_from_transcript``
# uses to call a prompt Telegram-originated.
_TELEGRAM_MARKER = "[via Telegram"

# The safety fields a snapshot carries (everything ``merge_snapshots``
# reasons about). ``note_bodies`` is provenance, not a rule.
_LIST_FIELDS = ("deny_tools", "allow_tools", "deny_paths_no_access",
                "deny_paths_no_write", "deny_bash_patterns")


def _has_telegram_marker(text: str) -> bool:
    return any(line.lstrip().startswith(_TELEGRAM_MARKER)
               for line in text.split("\n"))


def snapshot_for_unattributed_prompt(
    current: dict | None, outstanding: list[dict], prompt_text: str,
) -> dict | None:
    """The snapshot a ``UserPromptSubmit`` with NO matching note should
    leave behind, or ``None`` to fall back to the old answer
    (``merge_snapshots(outstanding)``, i.e. the floor when nothing waits).

    Roadmap 8.49. Claude Code fires ``UserPromptSubmit`` for prompts no
    Telegram note accounts for while a Telegram turn is still running:
    a message typed in the terminal and queued behind the turn (the hook
    fires at ENQUEUE — measured 2026-09-26, and on record since 2.1.259),
    a message from another local Claude session, the same Telegram
    message delivered a second time after a compact. Rebuilding the
    snapshot from "no notes" wrote the floor, demoting an owner's running
    turn mid-flight. Such a prompt adds no Telegram sender to the turn, so
    the turn keeps the snapshot it is running under — the last one written
    for this session — narrowed by every note still waiting
    (most-restrictive-wins, never wider than ``current`` on any axis).

    Kept only when the prompt carries no UNATTRIBUTED Telegram text: after
    removing every message body already merged into ``current``
    (``note_bodies``), no line may start with the Telegram marker. Anything
    else — a marked prompt no note matches and ``current`` was not built
    from — still cannot be attributed and falls back, fail closed.

    Returns ``None`` (fall back) as well when ``current`` is missing or not
    a well-formed snapshot. ``style_text``/``reply_context`` come from the
    newest outstanding note, else are blank — the kept snapshot must not
    re-inject the previous message's reply pointer into this prompt.
    """
    if not isinstance(current, dict):
        return None
    bodies = current.get("note_bodies") or []
    if not isinstance(bodies, list):
        return None
    bodies = [b for b in bodies if isinstance(b, str) and b]
    rest = prompt_text or ""
    for body in bodies:
        rest = rest.replace(body, "")
    if _has_telegram_marker(rest):
        return None
    carried = carried_snapshot(current)
    if carried is None:
        return None
    merged = merge_snapshots([carried, *outstanding])
    merged["note_bodies"] = bodies
    merged["scope_mode"] = current.get("scope_mode") is True
    return merged


def carried_snapshot(current) -> dict | None:
    """*current* (the snapshot a running turn is held to) as a note
    :func:`merge_snapshots` can merge, or ``None`` when it is missing or
    not a well-formed snapshot.

    The built-in floor lists are re-applied (a hand-edited snapshot
    missing its deny lists is not carried without them), the bypass is
    kept only if *current* had it, writes stay confined unless *current*
    explicitly was not, and it is the oldest contributor
    (``queued_at = -inf``), so it never supplies ``style_text`` or
    ``reply_context``. Shared by :func:`snapshot_for_unattributed_prompt`
    (roadmap 8.49) and the running-turn merge in :func:`snapshot_for_prompt`
    (roadmap 8.77). Pure.
    """
    if not isinstance(current, dict):
        return None
    for f in _LIST_FIELDS:
        v = current.get(f)
        if v is not None and not (
            isinstance(v, list) and all(isinstance(x, str) for x in v)
        ):
            return None
    carried = {f: list(current.get(f) or []) for f in _LIST_FIELDS}
    for f in ("deny_paths_no_access", "deny_paths_no_write",
              "deny_bash_patterns"):
        carried[f] = sorted(set(carried[f]) | set(FLOOR_SNAPSHOT[f]))
    carried["bypass_safety"] = current.get("bypass_safety") is True
    carried["confine_writes"] = current.get("confine_writes") is not False
    carried["queued_at"] = float("-inf")  # oldest: never supplies style/reply
    carried["style_text"] = ""
    carried["reply_context"] = ""
    return carried


#: How many earlier Telegram slash-command bodies a snapshot keeps
#: (roadmap 8.74). Enough for every command queued behind a running turn.
CARRIED_COMMAND_BODIES = 16


def _carried_command_bodies(current, fresh: list[str]) -> list[str]:
    """The slash-command bodies of *current* (the snapshot being replaced)
    to keep beside *fresh* (roadmap 8.74).

    Claude Code fires ``UserPromptSubmit`` when a message is queued, not
    when it runs, so a message sent while a Telegram slash command's turn
    runs (or a second command queued behind it) replaces the snapshot
    before that command is done or has even started. The command's turn
    is still governed by its ``<command-name>`` record, and the hook can
    only tell it came from Telegram while some body still names it. Kept
    newest first, without duplicates, at most
    :data:`CARRIED_COMMAND_BODIES`. Keeping one longer can only make a
    later prompt of the same command Telegram (held to the snapshot's
    rules), never unrestricted.
    """
    if not isinstance(current, dict):
        return []
    old = current.get("note_bodies")
    if not isinstance(old, list):
        return []
    out: list[str] = []
    for body in old:
        if (isinstance(body, str) and body.lstrip().startswith("/")
                and body not in fresh and body not in out):
            out.append(body)
    return out[:CARRIED_COMMAND_BODIES]


def snapshot_for_prompt(
    session_name: str, prompt_text: str,
    outstanding: list[dict], consumed: list[dict], *,
    turn_open: bool = False,
) -> dict:
    """The canonical snapshot a ``UserPromptSubmit`` writes
    (``notify_hook._match_and_promote``).

    A fresh turn (``turn_open`` False):

    - Notes matched (``consumed``): their merge, exactly as before — this
      is how a different, less-privileged sender's message lowers the turn.
      The merged bodies are recorded as ``note_bodies``.
    - No note matched: :func:`snapshot_for_unattributed_prompt` keeps the
      current snapshot when the prompt adds no unattributed Telegram text.
    - Otherwise: the old answer — every outstanding note's merge, or
      :data:`FLOOR_SNAPSHOT` when none is waiting.

    It also records the turn's origin (``turn_origin``, see
    :func:`_fresh_turn_origin`).

    While a turn is running (``turn_open``, roadmap 8.77) the prompt is a
    message joining it: Claude Code fires this hook when it queues the
    message, and the running turn goes on under whatever is written here.
    So the answer above is merged, strictest wins, with the running turn's
    own snapshot (:func:`_join_running_turn`): a message from someone with
    more rights never widens it.
    """
    current = read_snapshot(session_name)
    answer = _fresh_turn_snapshot(current, prompt_text, outstanding, consumed)
    if turn_open:
        return _join_running_turn(current, answer, consumed, prompt_text)
    answer["turn_origin"] = _fresh_turn_origin(prompt_text, consumed, answer)
    return answer


def snapshot_after_failure(
    session_name: str, prompt_text: str, *, turn_open: bool,
) -> dict:
    """What the UserPromptSubmit hook writes when its pick-up failed.

    A fresh turn: :data:`FLOOR_SNAPSHOT`, as before. A running turn
    (roadmap 8.77): the floor joined to the running turn as any other
    answer is (:func:`_join_running_turn`), so the turn keeps its own
    rules where they are stricter (a ``read_only`` turn's ``allow_tools``)
    and Telegram text joining a terminal turn is still enforced. If even
    that fails: the floor, read as a Telegram turn.
    """
    floor = dict(FLOOR_SNAPSHOT)
    if not turn_open:
        return floor
    try:
        return _join_running_turn(read_snapshot(session_name), floor, [],
                                  prompt_text)
    except Exception:
        log.debug("running-turn floor failed", exc_info=True)
        floor["joined_from_telegram"] = True
        return floor


def _fresh_turn_snapshot(
    current, prompt_text: str, outstanding: list[dict], consumed: list[dict],
) -> dict:
    """:func:`snapshot_for_prompt`'s answer for a prompt that starts a turn
    (the behaviour before roadmap 8.77, unchanged)."""
    if consumed:
        merged = merge_snapshots(consumed)
        bodies = [
            n["body"] for n in consumed
            if isinstance(n.get("body"), str) and n["body"]
        ]
        carried = _carried_command_bodies(current, bodies)
        merged["note_bodies"] = bodies + carried
        # Any scope-mode contributor makes the bodies count at the hook
        # (roadmap 8.74): stricter, never looser.
        merged["scope_mode"] = any(n.get("scope_mode") is True for n in consumed)
        return merged
    kept = snapshot_for_unattributed_prompt(current, outstanding, prompt_text)
    if kept is not None:
        # The kept snapshot keeps its bodies whole, the carried commands
        # included (roadmap 8.74). Dropping one here because this prompt
        # names it (the operator typing the same command) cannot be told
        # apart from Claude Code delivering a running Telegram command a
        # second time after a compact, and that turn would then run with
        # no rules. Kept, a terminal command Telegram also sent in this
        # session runs under the turn's rules instead: fail closed. The
        # snapshot, carried bodies and all, goes when the session ends.
        return kept
    merged = merge_snapshots(outstanding)  # all-outstanding fallback, or floor
    carried = _carried_command_bodies(current, [])
    if carried:
        # Telegram text no note matched: keep the commands still queued
        # or running named, as the consumed branch does (8.74).
        merged["note_bodies"] = carried
        merged["scope_mode"] = current.get("scope_mode") is True
    return merged


def _fresh_turn_origin(prompt_text: str, consumed: list[dict], snap) -> str:
    """``"terminal"`` when a turn starts from a prompt no Telegram message
    accounts for, else ``"telegram"`` (roadmap 8.77).

    Telegram when a note was consumed, when the prompt carries the
    Telegram marker on any line, or, in scope mode, when it is a slash
    command (or a whole body) that *snap*'s ``note_bodies`` name: *snap*
    is the snapshot being written, so this is the evidence
    ``enforce._command_from_telegram`` will read. Only a
    terminal turn's own snapshot is left out of a later join
    (:func:`_join_running_turn`), and the turn really is the operator's
    then: the hook runs a terminal prompt with no rules. Every doubt
    reads as Telegram, which keeps the snapshot in a join (stricter).
    """
    if consumed or _has_telegram_marker(prompt_text or ""):
        return "telegram"
    if isinstance(snap, dict) and snap.get("scope_mode") is True:
        bodies = snap.get("note_bodies")
        if isinstance(bodies, list):
            whole = (prompt_text or "").strip()
            name = _first_token(prompt_text)
            for body in bodies:
                if not isinstance(body, str) or not body.strip():
                    continue
                if body.strip() == whole or (
                        name.startswith("/") and _first_token(body) == name):
                    return "telegram"
    return "terminal"


def _join_running_turn(
    current, answer: dict, consumed: list[dict], prompt_text: str,
) -> dict:
    """The snapshot after a message joins the running turn (roadmap 8.77):
    the strictest of the turn's own snapshot (*current*, carried as
    :func:`carried_snapshot` carries it) and *answer* (what the message
    alone would have written).

    - A turn that started in the terminal and nothing from Telegram has
      joined yet (``turn_origin == "terminal"``) has no rules of its own
      (the hook allows a terminal prompt everything), so *answer* alone
      is its snapshot: the joining message's rules.
    - Any other turn: *current* is carried. Missing or malformed, the
      floor is carried instead (fail closed).
    - ``note_bodies`` accumulate (*answer*'s first, then *current*'s),
      ``scope_mode`` is set if either had it, and ``turn_origin`` stays the
      running turn's.
    - ``joined_from_telegram`` is set once a Telegram message (a consumed
      scope-mode note, or text carrying the marker) joins, and kept until
      the next fresh turn: a message absorbed into a running turn is not
      a prompt of its own in the transcript, so ``enforce`` would keep
      reading a terminal turn as terminal and run the message's text with
      no rules. The flag makes it read the turn as Telegram.
    """
    cur = current if isinstance(current, dict) else {}
    pure_terminal = (cur.get("turn_origin") == "terminal"
                     and cur.get("joined_from_telegram") is not True)
    if pure_terminal:
        merged = dict(answer)
    else:
        base = carried_snapshot(current)
        if base is None:
            base = dict(FLOOR_SNAPSHOT)
            base["queued_at"] = float("-inf")
            base["style_text"] = ""
            base["reply_context"] = ""
        merged = merge_snapshots([base, answer])
    bodies = [b for b in (answer.get("note_bodies") or [])
              if isinstance(b, str) and b]
    old = cur.get("note_bodies")
    if isinstance(old, list):
        bodies += [b for b in old
                   if isinstance(b, str) and b and b not in bodies]
    merged["note_bodies"] = bodies
    merged["scope_mode"] = (answer.get("scope_mode") is True
                            or cur.get("scope_mode") is True)
    origin = cur.get("turn_origin")
    merged["turn_origin"] = origin if origin in ("terminal", "telegram") \
        else "telegram"
    merged["joined_from_telegram"] = (
        cur.get("joined_from_telegram") is True
        or any(n.get("scope_mode") is True for n in consumed)
        or _has_telegram_marker(prompt_text or ""))
    return merged


def clear_snapshot(session_name: str) -> None:
    try:
        snapshot_path(session_name).unlink(missing_ok=True)
    except OSError:
        pass


def reply_context_path(session_name: str) -> Path:
    return Path(f"/tmp/claude-reply-{session_name}.txt")


def write_reply_context_file(session_name: str, header: str, full_text: str) -> None:
    """Atomic-write the full text of a replied-to message (best-effort).

    Written only for an immediate (not queued), whole-message (not
    highlighted), reply to an older (not latest) message — see
    ``aipager.bot.session_ops._build_reply_context``. Capped at 4000
    chars (Telegram's own message ceiling is 4096) even if the caller
    already truncated — defense in depth, mirrors ``write_snapshot``'s
    atomic-replace + 0600 pattern.
    """
    content = f"{header}\n\n{full_text[:4000]}" if header else full_text[:4000]
    path = reply_context_path(session_name)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        log.debug("could not write reply context file %s", path, exc_info=True)


def clear_reply_context_file(session_name: str) -> None:
    try:
        reply_context_path(session_name).unlink(missing_ok=True)
    except OSError:
        pass


def clear_session_files(session_name: str) -> None:
    """Best-effort removal of every /tmp file or dir this feature writes.

    Called on session GONE (crash / socket-vanish / SessionEnd hook, via
    ``SessionRegistry.transition``) and on explicit ``/kill`` (via
    ``SessionRegistry.remove``) — design.md Part 5. Never called for a
    resumed session: the reply-context file is fully overwritten on the
    next ``_inject_prompt`` regardless. The policy snapshot is NOT
    overwritten by ``_inject_prompt`` any more (queue-handoff design.md
    moved that write to the ``UserPromptSubmit`` hook's merge, at
    pick-up) — correcting a claim this docstring used to make — so a
    resumed session's first turn genuinely starts from a clean/absent
    snapshot until that hook fires, same as any other never-yet-prompted
    session.
    """
    clear_snapshot(session_name)
    clear_reply_context_file(session_name)
    clear_notes_dir(session_name)
    clear_turn_open(session_name)
    try:
        notes_dir(session_name).rmdir()
    except OSError:
        pass
