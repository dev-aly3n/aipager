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
import stat
import time
from pathlib import Path

from aipager import instance, safety

log = logging.getLogger(__name__)


def snapshot_path(session_name: str) -> Path:
    return Path(instance.runtime_tmp_dir()) / f"claude-policy-{session_name}.json"


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
    return Path(instance.runtime_tmp_dir()) / f"claude-notes-{session_name}"


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
    # The credential files too (roadmap 8.79), as for the ``user`` role.
    "deny_paths_no_access": [*safety.DENY_PATHS_NO_ACCESS,
                             *safety.CREDENTIAL_PATHS],
    "deny_paths_no_write": list(safety.DENY_PATHS_NO_WRITE),
    "deny_bash_patterns": list(safety.DENY_BASH_PATTERNS),
}

# What every snapshot carries whoever it is for: the built-in path and
# command floor, without the floor role's own additions (the credential
# files are a restricted-role default, which an admin's turn never has).
# The operator's ``safety:`` section of policy.yaml adds to it
# (:func:`safety_floor_lists`, roadmap 8.96).
_BASE_FLOOR_LISTS: dict = {
    "deny_paths_no_access": safety.DENY_PATHS_NO_ACCESS,
    "deny_paths_no_write": safety.DENY_PATHS_NO_WRITE,
    "deny_bash_patterns": safety.DENY_BASH_PATTERNS,
}

#: The three safety-floor lists and the ``Policy`` attributes that extend
#: each (``policy.yaml``'s ``safety:`` section, built-ins first).
_FLOOR_LIST_ATTRS: tuple[tuple[str, str], ...] = (
    ("deny_paths_no_access", "safety_deny_paths_no_access"),
    ("deny_paths_no_write", "safety_deny_paths_no_write"),
    ("deny_bash_patterns", "safety_deny_bash_patterns"),
)

# ---------------------------------------------------------------------------
# The effective safety floor (roadmap 8.96).
#
# Every non-owner turn is held to the built-in floor plus the operator's
# ``safety:`` section of policy.yaml. The daemon knows the policy: it
# registers it here (:func:`set_live_policy`, at start and on every live
# reload) and writes the resulting lists to :func:`floor_path`. The hook
# never parses policy.yaml: when it needs the floor without a snapshot
# to read it from (no snapshot, a failed pick-up, an unattributed turn),
# it reads that small file once per process. A missing, unreadable or
# malformed file gives the built-in floor: never less than the built-ins
# (anything the file adds is only ever added).
# ---------------------------------------------------------------------------

#: The largest floor file read; anything bigger reads as corrupt.
_FLOOR_FILE_MAX_BYTES = 1 << 20

#: The daemon's floor lists, set by :func:`set_live_policy`.
_live_floor_lists: dict | None = None
#: The floor lists read from :func:`floor_path` (a process with no live
#: policy, i.e. the hook), cached for the process.
_file_floor_lists: dict | None = None


def floor_path() -> Path:
    """The daemon-written effective floor. Under the protected
    ``/tmp/claude-policy-*`` path, named so no session's snapshot can
    collide with it (session names never hold a dot), and per OS user, so
    two users' daemons on one host never share it (each hook ignores a
    file it does not own). A plain function, like :func:`snapshot_path`,
    so tests can redirect it."""
    return Path(instance.runtime_tmp_dir()) / f"claude-policy-.floor-{_own_uid()}.json"


def _with_builtins(base, extra) -> tuple[str, ...]:
    """*base* (a built-in list) followed by the strings of *extra* it
    does not already hold, in order. Anything that is not a list or
    tuple of strings adds nothing."""
    out = list(base)
    if isinstance(extra, (list, tuple)):
        for x in extra:
            if isinstance(x, str) and x not in out:
                out.append(x)
    return tuple(out)


def policy_floor_lists(policy) -> dict:
    """The effective floor lists of *policy*: the built-in lists, then
    the policy's ``safety:`` additions. Pure."""
    return {f: _with_builtins(_BASE_FLOOR_LISTS[f],
                              getattr(policy, attr, ()))
            for f, attr in _FLOOR_LIST_ATTRS}


def _own_uid() -> int:
    """This process's user id (a seam for tests)."""
    return os.getuid()


def _read_floor_file() -> dict:
    """The floor lists in :func:`floor_path`, each on top of the
    built-ins; the built-ins alone when the file is missing, not ours
    (another user could have created the name first in ``/tmp``), too
    big, not JSON, not a mapping, or fails in any other way: fail
    closed, never less, and never raises."""
    try:
        with open(floor_path(), "rb") as f:
            if os.fstat(f.fileno()).st_uid != _own_uid():
                raise ValueError("floor file owned by another user")
            raw = f.read(_FLOOR_FILE_MAX_BYTES + 1)
        if len(raw) > _FLOOR_FILE_MAX_BYTES:
            raise ValueError("floor file too big")
        data = json.loads(raw)
        # Not a mapping: ``.get`` raises below, which reads as corrupt.
        return {f: _with_builtins(_BASE_FLOOR_LISTS[f], data.get(f))
                for f, _attr in _FLOOR_LIST_ATTRS}
    except Exception:
        # Anything at all (a parser RecursionError on deeply nested
        # brackets included): the built-in floor.
        return {f: tuple(_BASE_FLOOR_LISTS[f]) for f, _a in _FLOOR_LIST_ATTRS}


def safety_floor_lists(policy=None) -> dict:
    """The floor lists every non-owner turn is held to: *policy*'s when
    given, else the daemon's live policy (:func:`set_live_policy`), else
    (the hook) the daemon-written :func:`floor_path`, read once."""
    global _file_floor_lists
    if policy is not None:
        return policy_floor_lists(policy)
    if _live_floor_lists is not None:
        return _live_floor_lists
    if _file_floor_lists is None:
        _file_floor_lists = _read_floor_file()
    return _file_floor_lists


def write_floor_file(lists: dict) -> None:
    """Atomic-write the effective floor lists for the hook (best-effort,
    0600, like the snapshots)."""
    path = floor_path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({f: list(lists[f])
                                   for f, _a in _FLOOR_LIST_ATTRS}),
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        log.warning("could not write the safety floor %s: the hook's "
                    "fallbacks use the built-in floor", path, exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def set_live_policy(policy) -> None:
    """Make *policy*'s safety section the floor of every snapshot this
    process writes from now on, and hand it to the hook
    (:func:`write_floor_file`). Called by the daemon at start and after
    every live reload."""
    global _live_floor_lists
    _live_floor_lists = policy_floor_lists(policy)
    write_floor_file(_live_floor_lists)


def floor_snapshot(policy=None) -> dict:
    """:data:`FLOOR_SNAPSHOT` with the effective floor lists
    (:func:`safety_floor_lists`) added: the answer for a turn nobody can
    be held to. Equal to :data:`FLOOR_SNAPSHOT`, list order included,
    when policy.yaml adds nothing. A fresh dict each call. Never raises:
    it is the last-resort answer of every failure path (the hook's
    ``snapshot_after_failure`` included), so anything going wrong here
    gives the built-in floor."""
    try:
        lists = safety_floor_lists(policy)
        snap = dict(FLOOR_SNAPSHOT)
        for f, _attr in _FLOOR_LIST_ATTRS:
            snap[f] = list(_with_builtins(FLOOR_SNAPSHOT[f], lists[f]))
        return snap
    except Exception:
        log.debug("effective floor failed, using the built-in one",
                  exc_info=True)
        return {k: list(v) if isinstance(v, list) else v
                for k, v in FLOOR_SNAPSHOT.items()}


def resolve_snapshot(role, scope, member, style_text: str = "",
                     reply_context: str = "", *, policy=None) -> dict:
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

    The safety floor is the built-in one plus policy.yaml's ``safety:``
    section (roadmap 8.96): *policy*'s when given, else the daemon's
    live policy (:func:`safety_floor_lists`).
    """
    floor_lists = safety_floor_lists(policy)
    bypass_safety = bool(role and role.bypass_safety)
    bypass_role_denies = bool(role and role.bypass_role_denies)

    deny_tools: set[str] = set()
    allow_tools: set[str] = set()
    no_access: set[str] = set(floor_lists["deny_paths_no_access"])
    no_write: set[str] = set(floor_lists["deny_paths_no_write"])
    bash: set[str] = set(floor_lists["deny_bash_patterns"])

    if role is None:
        # No role: a sender aipager cannot attribute. Held to the floor's
        # tools, like a turn with no note at all (2026-09-27), and kept off
        # the credential files like the floor (roadmap 8.79).
        deny_tools |= set(FLOOR_SNAPSHOT["deny_tools"])
        no_access |= set(FLOOR_SNAPSHOT["deny_paths_no_access"])
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
                   style_text: str = "", reply_context: str = "", *,
                   policy=None) -> None:
    """Atomic-write the resolved snapshot for a session (best-effort).

    ``reply_context`` defaults to ``""`` — every one of the (12+)
    ``_inject_prompt`` call sites unrelated to a reply passes nothing,
    so the snapshot is overwritten with an explicit "not a reply" on
    every turn rather than silently keeping a prior turn's value.
    """
    data = resolve_snapshot(role, scope, member, style_text, reply_context,
                            policy=policy)
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
    :func:`merge_snapshots`, and by the daemon's live reload to narrow a
    running turn (:func:`narrow_snapshot`, roadmap 8.80): two processes
    write the canonical ``/tmp/claude-policy-<session>.json``, so the
    temporary file is per process (two writers sharing one temp name
    could interleave or lose a write). Shares :func:`write_snapshot`'s
    atomic-replace + 0600 pattern; kept separate so
    :func:`write_snapshot`'s own role/scope/member-based signature stays
    unchanged.
    """
    path = snapshot_path(session_name)
    try:
        tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
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
    policy=None,
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

    ``policy`` supplies the safety floor's policy.yaml additions (the
    daemon's live policy when ``None``, :func:`resolve_snapshot`).
    """
    note = resolve_snapshot(role, scope, member, style_text, reply_context,
                            policy=policy)
    note["msg_id"] = msg_id
    note["chat_id"] = chat_id
    note["sender_key"] = list(sender_key) if sender_key is not None else None
    # The prompt's real author (see note_driver_id). Callers that know
    # the sender was only a fallback pass author_user_id=None explicitly.
    if author_user_id is _FROM_SENDER_KEY:
        author_user_id = sender_key[1] if sender_key else None
    note["author_user_id"] = author_user_id if author_user_id else None
    # Who wrote it, for display only (a receipt names the turn's driver;
    # roadmap "peer message authority"). Never read for a decision.
    note["author_label"] = (str(getattr(member, "label", "") or "")[:64]
                            if member is not None else "")
    note["author_role"] = (str(getattr(member, "role", "") or "")[:32]
                           if member is not None else "")
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


#: Claude Code's local slash commands: they run in the terminal with no
#: prompt (no UserPromptSubmit), so nothing ever consumes their note.
LOCAL_COMMANDS = frozenset({
    "/compact", "/model", "/clear", "/cost", "/context", "/status",
    "/config", "/help", "/memory", "/permissions", "/usage", "/exit",
    "/quit", "/mcp", "/agents", "/hooks", "/doctor", "/export", "/rewind",
    "/resume", "/add-dir", "/theme", "/statusline", "/fast", "/output-style",
    "/release-notes", "/upgrade", "/login", "/logout", "/ide", "/vim",
})


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

    Slash-command notes follow two extra rules ("peer message authority",
    C13):

    - A command is never batched with other text, so its note matches only
      a prompt that STARTS with that command (its first token), never one
      that merely contains it.
    - A LOCAL command's note (:data:`LOCAL_COMMANDS`: ``/compact``,
      ``/model``…) that does not match is passed over, not a stop. A local
      command runs with no prompt of its own and no hook that consumes its
      note, so its note lingers until a turn end marks it ran. Stopping
      there left the next real message unmatched, and its turn fell back
      to the strictest merge of everything waiting: the owner's own
      message ran with the lingering note's rights (Mohamad's VM,
      2026-10-09). Passed over, it stays outstanding: it can still only
      make a fallback merge stricter.
    """
    matched: list[dict] = []
    cursor = 0
    for note in outstanding:
        body = note.get("body") or ""
        if not body:
            break
        if body.lstrip().startswith("/"):
            name = _first_token(body)
            if cursor == 0 and _first_token(content) == name \
                    and body.strip() in content:
                idx = content.find(body.strip())
                matched.append(note)
                cursor = idx + len(body.strip())
                continue
            if name in LOCAL_COMMANDS:
                continue
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
    :func:`floor_snapshot` instead of that note's own resolved rules.
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

    At ``notes == []`` returns :func:`floor_snapshot` (the "empty floor"
    promotion path): the most restrictive answer when there is nothing
    to reason from at all. That is the only case that reads anything
    (in the hook, the daemon-written floor file, once per process).

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
        return floor_snapshot()

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

    The effective path and command floor (:func:`safety_floor_lists`) is
    re-applied (a hand-edited
    snapshot missing its deny lists is not carried without them; the
    credential files only for a confined turn, so an owner's or admin's
    turn does not gain them), the bypass is
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
    carried["bypass_safety"] = current.get("bypass_safety") is True
    carried["confine_writes"] = current.get("confine_writes") is not False
    # A confined turn gets the whole floor back, credential files included;
    # an owner's or admin's only the part every snapshot carries.
    floor = (floor_snapshot() if carried["confine_writes"]
             and not carried["bypass_safety"] else safety_floor_lists())
    for f in ("deny_paths_no_access", "deny_paths_no_write",
              "deny_bash_patterns"):
        carried[f] = sorted(set(carried[f]) | set(floor[f]))
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
    envelope: dict | None = None,
) -> dict:
    """The canonical snapshot a ``UserPromptSubmit`` writes
    (``notify_hook._match_and_promote``).

    A fresh turn (``turn_open`` False):

    - Notes matched (``consumed``): their merge, exactly as before — this
      is how a different, less-privileged sender's message lowers the turn.
      The merged bodies are recorded as ``note_bodies``.
    - No note matched and the prompt is only the bodies the current
      snapshot already ran (Claude Code delivering a Telegram message a
      second time after a compact, 8.49): the current snapshot is kept.
    - Any other prompt no note matched never keeps the previous turn's
      rights ("peer message authority", I3): with no Telegram marker the
      floor (a terminal prompt is still unrestricted: ``enforce`` reads
      its origin), with one the old answer (every outstanding note's
      merge, or the floor). Both are marked ``verify_origin`` so
      ``enforce`` checks what Claude Code recorded as the prompt's origin.
    - A message from another session (*envelope*, parsed by the caller
      with :func:`aipager.peer_auth.parse_envelope`) gets the peer floor
      until ``enforce`` verifies its sender (:func:`_fresh_peer_snapshot`).

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
    if envelope is not None:
        # A message from another session ("peer message authority"): its
        # rights come from its sender, never from this session's previous
        # turn (I3) nor from the text.
        if turn_open:
            return _join_peer(session_name, current, envelope, consumed,
                              prompt_text)
        return _fresh_peer_snapshot(current, envelope, consumed)
    scheduled = None if turn_open else read_trigger(session_name, prompt_text)
    answer = _fresh_turn_snapshot(
        current, prompt_text, outstanding, consumed, fresh=not turn_open,
        scheduled=scheduled)
    if (not turn_open and scheduled is None and answer.get("verify_origin") is True
            and not consumed and not _has_telegram_marker(prompt_text or "")):
        # Unattributed: if it turns out to be a bare /loop firing (its
        # transcript entry says so), ``enforce`` applies the loop's rights.
        loop = read_trigger(session_name, LOOP_SENTINELS[0])
        if loop is not None:
            answer["loop_rules"] = loop
    if turn_open:
        return _join_running_turn(current, answer, consumed, prompt_text)
    answer["turn_origin"] = _fresh_turn_origin(prompt_text, consumed, answer)
    if answer.get("scheduled") is True:
        # Never a pure terminal turn: a message joining it keeps its rules.
        answer["turn_origin"] = "scheduled"
    return answer


def snapshot_after_failure(
    session_name: str, prompt_text: str, *, turn_open: bool,
) -> dict:
    """What the UserPromptSubmit hook writes when its pick-up failed.

    A fresh turn: :func:`floor_snapshot`, as before. A running turn
    (roadmap 8.77): the floor joined to the running turn as any other
    answer is (:func:`_join_running_turn`), so the turn keeps its own
    rules where they are stricter (a ``read_only`` turn's ``allow_tools``)
    and Telegram text joining a terminal turn is still enforced. If even
    that fails: the floor, read as a Telegram turn.
    """
    floor = floor_snapshot()
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
    *, fresh: bool = False, scheduled: dict | None = None,
) -> dict:
    """:func:`snapshot_for_prompt`'s answer for a prompt that starts a turn
    (*fresh*), or what a message joining a running turn would write alone
    (the behaviour before roadmap 8.77). Only a fresh turn applies the
    "no previous rights" rule (I3): a message joining a running turn is
    merged with it, strictest wins, and must not demote it for nothing
    (roadmap 8.49)."""
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
        drivers = _drivers_of(consumed)
        if drivers:
            merged["drivers"] = drivers
        return merged
    if fresh and not _only_note_bodies(current, prompt_text):
        # A fresh turn no Telegram message accounts for ("peer message
        # authority", I3): never the previous turn's rights. Typed in the
        # terminal it is still unrestricted (``enforce`` reads its origin
        # from the transcript); a message from another session, a channel
        # push or anything else Claude Code delivers gets the floor. The
        # all-outstanding fallback below stays for marked Telegram text
        # no note matched (it is the strictest of what waits).
        if not _has_telegram_marker(prompt_text or ""):
            # The floor, and never less than any message still waiting
            # (strictest of them all); the newest waiting message's style,
            # else the chat's current one. A prompt this session scheduled
            # (a /loop, a cron: :func:`read_trigger`) gets the rights of
            # the turn that scheduled it instead of the floor.
            base = floor_snapshot()
            if scheduled is not None:
                base.update(safety_rules({**base, **scheduled}))
            base["queued_at"] = float("-inf")
            base["style_text"] = (current.get("style_text") or ""
                                  if isinstance(current, dict) else "")
            base["reply_context"] = ""
            if outstanding:
                snap = merge_snapshots([base, *outstanding])
            elif scheduled is not None:
                snap = dict(base)
                del snap["queued_at"]
            else:
                snap = floor_snapshot()
                snap["style_text"] = base["style_text"]
                snap["reply_context"] = ""
            if scheduled is not None:
                snap["scheduled"] = True
            snap["verify_origin"] = True
            return snap
        merged = merge_snapshots(outstanding)
        carried = _carried_command_bodies(current, [])
        if carried:
            merged["note_bodies"] = carried
            merged["scope_mode"] = current.get("scope_mode") is True
        merged["verify_origin"] = True
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
                     and cur.get("joined_from_telegram") is not True
                     and "loop_rules" not in cur)  # maybe a bare /loop firing
    if pure_terminal:
        merged = dict(answer)
    else:
        base = carried_snapshot(current)
        if base is None:
            base = floor_snapshot()
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
    merged["turn_origin"] = origin if origin in (
        "terminal", "telegram", "peer", "scheduled") else "telegram"
    merged["joined_from_telegram"] = (
        cur.get("joined_from_telegram") is True
        or any(n.get("scope_mode") is True for n in consumed)
        or _has_telegram_marker(prompt_text or ""))
    # A turn started by a message from another session keeps its peer
    # state through every join ("peer message authority", C2/C3). While
    # its sender is still being checked, each joining message's rules are
    # kept apart too, so the check's result is merged with them: a member
    # whose message joined a pending turn never rides an owner's receipt.
    _carry_peer_state(cur, merged)
    peer = cur.get("peer")
    if isinstance(peer, dict) and peer.get("status") == "pending":
        merged["pending_joins"] = [
            *_rule_list(cur.get("pending_joins")), safety_rules(answer)]
    # A scheduled prompt's turn stays one ("peer message authority").
    for k in SCHEDULED_STATE_FIELDS:
        if k in cur:
            v = cur[k]
            merged[k] = (json.loads(json.dumps(v))
                         if isinstance(v, (dict, list)) else v)
    if "loop_rules" in cur and (consumed or _has_telegram_marker(prompt_text or "")):
        # Telegram text joined before the loop's rights were adopted: they
        # will be merged with its rules. (Text typed in the terminal adds
        # none: its answer is only the running turn's own snapshot.)
        merged["loop_joins"] = [*_rule_list(cur.get("loop_joins")),
                                safety_rules(answer)]
    drivers = _merge_drivers(cur.get("drivers"), answer.get("drivers"))
    if drivers:
        merged["drivers"] = drivers
    return merged


# ---- Messages between sessions ("peer message authority") -----------------
#
# A message one Claude session sends another (Claude Code's SendMessage)
# runs with exactly the rights the sending turn had, proven by a receipt
# (:mod:`aipager.peer_auth`), and with the peer floor until then or when
# that cannot be proven. These helpers write the receiver's side of it at
# UserPromptSubmit; ``enforce`` verifies the sender before any tool runs.

#: Snapshot fields that describe a turn's peer state. Carried through
#: every join and narrowing: losing one would let ``enforce`` read the
#: turn as an ordinary one again.
PEER_STATE_FIELDS = ("peer", "pending_joins", "joined_peers",
                     "joined_from_peer", "verify_origin", "peer_notice")

#: A scheduled prompt's turn ("peer message authority", review 5): kept
#: through every join and narrowing, so the turn is still read from its
#: own bytes and a bare /loop's rights, once adopted, are merged with
#: every message that joined it meanwhile (``loop_joins``).
SCHEDULED_STATE_FIELDS = ("scheduled", "turn_start_size", "loop_rules",
                          "loop_joins", "loop_waited")


def peer_floor_snapshot() -> dict:
    """The floor plus no file changes and no web: the rights of a message
    from another session whose sender could not be proven."""
    from aipager.peer_auth import PEER_EXTRA_DENY_TOOLS
    snap = floor_snapshot()
    snap["deny_tools"] = sorted(set(snap["deny_tools"]) | set(PEER_EXTRA_DENY_TOOLS))
    return snap


def verify_flag_path(session_name: str) -> Path:
    return snapshot_path(session_name).with_suffix(".verify")


def mark_verify_origin(session_name: str) -> None:
    """Flag the running job for an origin check (a continuation woke it:
    Claude Code also delivers another session's text as a task
    notification). Best-effort."""
    if not session_name:
        return
    try:
        fd = os.open(verify_flag_path(session_name),
                     os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
    except (OSError, ValueError):
        log.debug("could not flag %s for an origin check", session_name,
                  exc_info=True)


def clear_verify_origin(session_name: str) -> None:
    try:
        verify_flag_path(session_name).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def verify_origin_flagged(session_name: str) -> bool:
    """True when the flag is set; an error other than "not there" reads as
    set (the stricter answer)."""
    if not session_name:
        return False
    try:
        os.stat(verify_flag_path(session_name))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, ValueError):
        return True
    return True


def marked_for_verification(snap) -> bool:
    """True when ``enforce`` must check the turn's origin before trusting
    *snap*'s rights, the owner's bypass included."""
    if not isinstance(snap, dict):
        return False
    if snap.get("turn_origin") == "peer":
        return True
    return any(snap.get(k) for k in ("peer", "pending_joins", "joined_peers",
                                     "joined_from_peer", "verify_origin"))


def _only_note_bodies(current, prompt_text: str) -> bool:
    """True when *prompt_text* is nothing but bodies the current snapshot
    already ran (a Telegram message delivered a second time after a
    compact, roadmap 8.49)."""
    if not isinstance(current, dict):
        return False
    bodies = current.get("note_bodies")
    if not isinstance(bodies, list):
        return False
    bodies = [b for b in bodies if isinstance(b, str) and b]
    if not bodies:
        return False
    rest = prompt_text or ""
    for body in bodies:
        rest = rest.replace(body, "")
    return not rest.strip()


def _rule_list(v) -> list[dict]:
    return [safety_rules(r) for r in v if isinstance(r, dict)] \
        if isinstance(v, list) else []


def _drivers_of(notes) -> list[dict]:
    """Who wrote *notes* (display only): ``[{user_id, label, role}]``."""
    out: list[dict] = []
    for n in notes or ():
        if not isinstance(n, dict):
            continue
        d = {"user_id": note_driver_id(n),
             "label": str(n.get("author_label") or "")[:64],
             "role": str(n.get("author_role") or "")[:32]}
        if d not in out:
            out.append(d)
    return out[:8]


def _merge_drivers(a, b) -> list[dict]:
    out: list[dict] = []
    for d in [*(a if isinstance(a, list) else []),
              *(b if isinstance(b, list) else [])]:
        if isinstance(d, dict) and d not in out:
            out.append(d)
    return out[:8]


def _carry_peer_state(cur: dict, merged: dict) -> None:
    for k in PEER_STATE_FIELDS:
        if k in cur:
            v = cur[k]
            merged[k] = (json.loads(json.dumps(v))
                         if isinstance(v, (dict, list)) else v)


def _fresh_peer_snapshot(current, envelope: dict, consumed: list[dict]) -> dict:
    """A fresh turn started by a message from another session: the peer
    floor, marked pending, until ``enforce`` verifies its sender before
    the first tool call. Any Telegram note matched OUTSIDE the envelope
    (the caller matched only there, C4) is kept apart as a join, so the
    verified result is merged with it."""
    from aipager.peer_auth import NOTICE_PENDING, NOTICE_UNVERIFIED, body_sha256
    body = envelope.get("body")
    sha = body_sha256(body) if isinstance(body, str) else None
    snap = peer_floor_snapshot()
    joins = [safety_rules(merge_snapshots(consumed))] if consumed else []
    if sha is None and joins:
        snap = merge_snapshots([snap, *joins])
    snap["turn_origin"] = "peer"
    snap["verify_origin"] = True
    snap["peer"] = {
        "status": "pending" if sha else "unverified",
        "body_sha256": sha,
        "name": str(envelope.get("name") or "")[:64],
        "claimed_pid": envelope.get("claimed_pid"),
    }
    snap["pending_joins"] = joins if sha else []
    snap["joined_peers"] = []
    snap["peer_notice"] = NOTICE_PENDING if sha else NOTICE_UNVERIFIED
    snap["note_bodies"] = [n["body"] for n in consumed
                           if isinstance(n.get("body"), str) and n["body"]]
    snap["scope_mode"] = (any(n.get("scope_mode") is True for n in consumed)
                          or (isinstance(current, dict)
                              and current.get("scope_mode") is True))
    snap["style_text"] = (current.get("style_text") or ""
                          if isinstance(current, dict) else "")
    snap["reply_context"] = ""
    return snap


def session_chat(session_name: str):
    """The chat a session name's suffix names, as ``[id, kind]``, or None
    for a name without one (a single-chat install)."""
    try:
        from aipager.scope import chat_from_suffix
        chat = chat_from_suffix(session_name or "")
    except Exception:
        return None
    return list(chat) if chat else None


def receipt_rules(session_name: str, receipts: list[dict]) -> tuple[dict, str]:
    """The rules a set of claimed receipts gives *session_name*: their
    strictest merge, with the peer floor added when any one of them may not
    cross into this session's chat (roles are per chat, 8.73), or when
    there are none. Returns ``(rules, status)``, status ``verified`` or
    ``unverified``."""
    from aipager.peer_auth import receipt_honoured_across_chats
    if not receipts:
        return safety_rules(peer_floor_snapshot()), "unverified"
    mine = session_chat(session_name)
    parts: list[dict] = []
    status = "verified"
    for r in receipts:
        if r.get("sender_chat") != mine and not receipt_honoured_across_chats(r):
            parts.append(peer_floor_snapshot())
            status = "unverified"
            continue
        parts.append(dict(r["rules"]))
    return safety_rules(merge_snapshots(parts)), status


def _join_peer(session_name: str, current, envelope: dict,
               consumed: list[dict], prompt_text: str) -> dict:
    """A message from another session joining a running turn: the turn
    narrows to the strictest of itself and the message's rights. Those
    come from receipts for this body (claimed by body hash: joining can
    only narrow, so no sender process check is needed), else the peer
    floor. The same message delivered again (after a compact, C9) changes
    nothing."""
    from aipager.peer_auth import body_sha256, claim_receipts
    cur = current if isinstance(current, dict) else {}
    body = envelope.get("body")
    sha = body_sha256(body) if isinstance(body, str) else None
    peer = cur.get("peer")
    if sha and isinstance(peer, dict) and peer.get("body_sha256") == sha:
        return dict(cur)
    joined = cur.get("joined_peers") if isinstance(cur.get("joined_peers"), list) else []
    if sha and any(isinstance(j, dict) and j.get("body_sha256") == sha
                   for j in joined):
        return dict(cur)
    receipts = (claim_receipts(sha, exclude_session=session_name)
                if sha else [])
    rules, status = receipt_rules(session_name, receipts)
    answer = dict(rules)
    answer["style_text"] = cur.get("style_text") or ""
    answer["reply_context"] = ""
    if consumed:
        answer = merge_snapshots([answer, *consumed])
    merged = _join_running_turn(current, answer, consumed, prompt_text)
    merged["joined_from_peer"] = True
    merged["joined_peers"] = [*joined, {
        "body_sha256": sha, "status": status,
        "sender_session": receipts[0].get("sender_session") if receipts else None,
    }][-16:]
    return merged


def narrow_for_peer_join(session_name: str, content) -> bool:
    """The daemon's backstop for a message from another session queued
    into a running turn (a transcript ``enqueue`` line whose content is a
    peer envelope): narrow the turn to the message's proven rights, or the
    peer floor, unless a UserPromptSubmit already accounted for it. Returns
    True when it narrowed. Never raises, and never blocks the daemon's
    loop: it takes the snapshot lock only if it is free at once.

    When it cannot narrow (no turn running, so the message will start the
    next one with no UserPromptSubmit; the lock is held by a hook) it
    marks the session for an origin check instead (:func:`mark_verify_origin`),
    so the hook's own check accounts for the message at the next tool call.
    It never writes the snapshot without the lock.
    """
    try:
        from aipager.peer_auth import body_sha256, claim_receipts, parse_envelope
        env = parse_envelope(content)
        if env is None:
            return False
        body = env.get("body")
        sha = body_sha256(body) if isinstance(body, str) else None
        if not turn_is_open(session_name):
            # No turn is running: the message starts a turn of its own,
            # whose UserPromptSubmit (or ``enforce``) checks its sender.
            # Claiming its receipt here would leave that check nothing; the
            # mark ends the owner short-circuit until a fresh prompt.
            mark_verify_origin(session_name)
            return False
        with snapshot_lock(session_name, timeout=0) as held:
            if not held:
                mark_verify_origin(session_name)
                return False
            cur = read_snapshot(session_name)
            if not isinstance(cur, dict):
                return False  # no turn to narrow; enforce holds it to the floor
            peer = cur.get("peer")
            seen = {j.get("body_sha256") for j in cur.get("joined_peers") or ()
                    if isinstance(j, dict)}
            if isinstance(peer, dict):
                seen.add(peer.get("body_sha256"))
            if sha is not None and sha in seen:
                return False
            receipts = (claim_receipts(sha, exclude_session=session_name)
                        if sha else [])
            rules, status = receipt_rules(session_name, receipts)
            new = narrowed_snapshot(cur, [rules])
            new["verify_origin"] = True
            new["joined_from_peer"] = True
            prev = new.get("joined_peers") if isinstance(new.get("joined_peers"), list) else []
            new["joined_peers"] = [*prev, {
                "body_sha256": sha, "status": status, "missed": True,
                "sender_session": receipts[0].get("sender_session") if receipts else None,
            }][-16:]
            write_merged_snapshot(session_name, new)
            return True
    except Exception:
        log.debug("peer join narrowing failed for %s", session_name, exc_info=True)
        return False


# ---- Serialising snapshot writes --------------------------------------------

def lock_path(session_name: str) -> Path:
    """Beside the snapshot (``claude-policy-<session>.lock``), under the
    same protected name and wherever the snapshot itself lives."""
    return snapshot_path(session_name).with_suffix(".lock")


class snapshot_lock:
    """``with snapshot_lock(name, timeout) as held:``: an exclusive
    ``flock`` on the session's lock file around a read-modify-write of its
    snapshot (the hook's pick-up and peer check, the daemon's narrowing),
    so no writer works from a snapshot another one is replacing. *held* is
    False when the lock could not be taken in *timeout* seconds; the
    caller decides (the hook falls back to the stricter answer)."""

    def __init__(self, session_name: str, timeout: float = 5.0):
        self.session_name = session_name
        self.timeout = timeout
        self.fd: int | None = None
        self.held = False

    def __enter__(self) -> bool:
        import fcntl
        try:
            self.fd = os.open(lock_path(self.session_name),
                              os.O_RDWR | os.O_CREAT
                              | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except (OSError, ValueError):
            return False
        deadline = time.monotonic() + max(0.0, self.timeout)
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.held = True
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.01)

    def __exit__(self, *exc) -> None:
        if self.fd is None:
            return
        try:
            if self.held:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None


# ---- Per-agent pins (phase C) -------------------------------------------------
#
# A background agent can outlive the turn that launched it, and there is one
# snapshot per session: the next turn's rights would apply to it. At
# SubagentStart the hook pins the launching turn's rights for that agent;
# ``enforce`` holds the agent's tool calls to the strictest of its pin and
# the session's current rights, so an agent never gains rights from a later
# turn (and a later, stricter turn still restricts it).

PIN_TTL_SECONDS = 24 * 3600.0
_AGENT_ID_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def pins_dir(session_name: str) -> Path:
    return snapshot_path(session_name).with_suffix(".pins")


def _pins_dir_state(d: Path) -> str:
    """``"missing"``, ``"ours"`` (a real directory of this user that no one
    else can write) or ``"foreign"`` (anything else: a symlink, another
    user's directory, a shared one). The pins sit in a shared temp folder."""
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError):
        return "foreign"
    if (stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid()
            and not st.st_mode & 0o077):
        return "ours"
    return "foreign"


def _pin_name(agent_id) -> str | None:
    if not isinstance(agent_id, str) or not 0 < len(agent_id) <= 64:
        return None
    if not set(agent_id) <= _AGENT_ID_OK:
        return None
    return f"{agent_id}.json"


def write_pin(session_name: str, agent_id, pin: dict) -> bool:
    """Pin *pin* (``{"rules": …, "terminal": bool}``) for *agent_id*."""
    name = _pin_name(agent_id)
    if name is None or not session_name:
        return False
    d = pins_dir(session_name)
    try:
        try:
            d.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if _pins_dir_state(d) != "ours":
            return False
        now = time.time()
        for p in d.iterdir():
            try:
                if now - p.stat().st_mtime > PIN_TTL_SECONDS:
                    p.unlink()
            except OSError:
                pass
        path = d / name
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(pin))
        os.replace(tmp, path)
        return True
    except (OSError, ValueError):
        return False


def read_pin(session_name: str, agent_id) -> dict | None:
    name = _pin_name(agent_id)
    if name is None or not session_name:
        return None
    d = pins_dir(session_name)
    state = _pins_dir_state(d)
    if state == "missing":
        return None
    if state == "foreign":
        return {}  # pins no one can trust: the caller fails closed
    try:
        fd = os.open(d / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as fh:
            data = json.loads(fh.read(64 * 1024 + 1))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}  # pinned but unreadable: no rules, so the caller fails closed
    return data if isinstance(data, dict) else {}


def drop_pin(session_name: str, agent_id) -> None:
    name = _pin_name(agent_id)
    if name is None or not session_name:
        return
    try:
        (pins_dir(session_name) / name).unlink(missing_ok=True)
    except OSError:
        pass


def clear_pins(session_name: str) -> None:
    d = pins_dir(session_name)
    try:
        for p in d.iterdir():
            try:
                p.unlink()
            except OSError:
                pass
        d.rmdir()
    except OSError:
        pass


# ---- Scheduled prompts ("peer message authority", review 2) -----------------
#
# A prompt the session scheduled itself (Claude Code's CronCreate, the
# /loop wakeups of ScheduleWakeup) fires later as a fresh prompt no
# Telegram message accounts for. It carries the rights of the turn that
# scheduled it: the hook records them when it allows the scheduling call,
# keyed by the prompt's text, and the pick-up reads them back. Never more:
# the same text scheduled twice keeps the stricter rights.

#: Scheduled prompts recorded per session. A record lasts as long as the
#: session (none expires or is pushed out: a fire that lost its record
#: would fall back to a bare /loop's rights); past this many, scheduling
#: another prompt is refused (``trigger_slot_free``).
TRIGGER_MAX_COUNT = 256

#: Claude Code's tools that schedule a prompt of this session to fire
#: later (a cron, a /loop wakeup).
SCHEDULING_TOOLS = ("CronCreate", "ScheduleWakeup")


def triggers_dir(session_name: str) -> Path:
    return snapshot_path(session_name).with_suffix(".triggers")


#: What a bare /loop (or a loop.md loop) schedules: Claude Code swaps the
#: loop's instructions in when it fires, so its text never matches. One
#: record per session holds the strictest rights of every such loop.
LOOP_SENTINELS = ("<<autonomous-loop-dynamic>>", "<<autonomous-loop>>")
LOOP_RECORD = "loop.json"


def _trigger_name(prompt) -> str | None:
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    if prompt.strip() in LOOP_SENTINELS:
        return LOOP_RECORD
    import hashlib
    return hashlib.sha256(prompt.strip().encode("utf-8", "surrogatepass")).hexdigest() + ".json"


def write_trigger(session_name: str, prompt, rules: dict, authors=()) -> bool:
    """Record that *prompt*, when it fires, runs with *rules* (strictest
    of these and any record already there), scheduled by *authors* (the
    Telegram users whose turn it was: a live reload that takes rights from
    one of them narrows the record too). Never raises."""
    name = _trigger_name(prompt)
    if name is None or not session_name:
        return False
    d = triggers_dir(session_name)
    try:
        try:
            d.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if _pins_dir_state(d) != "ours":
            return False
        old_record = _read_trigger_record(d, name)
        if old_record is None and _trigger_count(d) >= 2 * TRIGGER_MAX_COUNT:
            # Full: never push another record out. The hard limit sits above
            # the one ``enforce`` checks before allowing the call, so two
            # calls allowed at the same moment are both still recorded.
            return False
        old = old_record.get("rules") if old_record else None
        merged = safety_rules(merge_snapshots(
            [rules, old] if isinstance(old, dict) else [rules]))
        who = sorted({a for a in (*(old_record or {}).get("authors", ()), *authors)
                      if isinstance(a, int) and not isinstance(a, bool)})
        _write_trigger_record(d, name, {"rules": merged, "authors": who})
        return True
    except Exception:
        log.debug("could not record a scheduled prompt for %s", session_name,
                  exc_info=True)
        return False


def _trigger_count(d: Path) -> int:
    try:
        return sum(1 for f in d.iterdir() if f.name.endswith(".json"))
    except OSError:
        return 0


def trigger_slot_free(session_name: str, prompt) -> bool:
    """Whether *prompt* can be recorded: already recorded, or room for one
    more. ``enforce`` refuses scheduling a prompt that could not be."""
    name = _trigger_name(prompt)
    if name is None or not session_name:
        return True  # nothing to record (no text): Claude Code refuses it
    d = triggers_dir(session_name)
    state = _pins_dir_state(d)
    if state == "missing":
        return True
    if state != "ours":
        return False
    return (_read_trigger_record(d, name) is not None
            or _trigger_count(d) < TRIGGER_MAX_COUNT)


def _write_trigger_record(d: Path, name: str, record: dict) -> None:
    path = d / name
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(record))
    os.replace(tmp, path)


def _read_trigger_record(d: Path, name: str) -> dict | None:
    """A record as stored (``rules``, ``authors``), None when missing,
    unreadable or not well formed."""
    try:
        fd = os.open(d / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as fh:
            data = json.loads(fh.read(64 * 1024 + 1))
    except (OSError, ValueError):
        return None
    from aipager.peer_auth import well_formed_rules
    if not isinstance(data, dict) or not well_formed_rules(data.get("rules")):
        return None
    authors = data.get("authors")
    return {"rules": data["rules"],
            "authors": [a for a in authors if isinstance(a, int) and not isinstance(a, bool)]
            if isinstance(authors, list) else []}


def narrow_triggers(session_name: str, rules_for) -> int:
    """Live reload: hold every scheduled prompt of *session_name* to what
    its authors may still do. *rules_for(user_id)* gives the rules a user's
    work in flight must now be held to, or None when nothing was taken.
    Returns how many records were narrowed. Never raises."""
    d = triggers_dir(session_name)
    if _pins_dir_state(d) != "ours":
        return 0
    n = 0
    try:
        names = [f.name for f in d.iterdir() if f.name.endswith(".json")]
    except OSError:
        return 0
    for name in names:
        try:
            rec = _read_trigger_record(d, name)
            if rec is None:
                continue
            extra: list[dict] = []
            for uid in rec["authors"]:
                r = rules_for(uid)
                if r:
                    extra += list(r)
            if not extra:
                continue
            merged = safety_rules(merge_snapshots(
                [{**rec["rules"], "queued_at": float("-inf")},
                 *({**r, "queued_at": float("-inf")} for r in extra)]))
            if merged != safety_rules(rec["rules"]):
                _write_trigger_record(d, name, {"rules": merged,
                                                "authors": rec["authors"]})
                n += 1
        except Exception:
            log.debug("could not narrow a scheduled prompt of %s", session_name,
                      exc_info=True)
    return n


def read_trigger(session_name: str, prompt) -> dict | None:
    """The rules recorded for *prompt* (:func:`write_trigger`), or None:
    none recorded, unreadable, or a folder no one can trust."""
    name = _trigger_name(prompt)
    if name is None or not session_name:
        return None
    d = triggers_dir(session_name)
    if _pins_dir_state(d) != "ours":
        return None
    rec = _read_trigger_record(d, name)
    return safety_rules(rec["rules"]) if rec else None


def loop_rights(snap: dict) -> dict | None:
    """A bare /loop's rights for *snap*: the recorded loop rules, never
    wider than anything that joined or narrowed the turn before they were
    adopted (``loop_joins``). None when there are none."""
    loop = snap.get("loop_rules") if isinstance(snap, dict) else None
    from aipager.peer_auth import well_formed_rules
    if not well_formed_rules(loop):
        return None
    joins = _rule_list(snap.get("loop_joins"))
    return safety_rules(merge_snapshots(
        [{**loop, "queued_at": float("-inf")},
         *({**j, "queued_at": float("-inf")} for j in joins)]))


def adopt_loop_rules(session_name: str) -> dict | None:
    """The turn a bare /loop fire started is confirmed (``enforce`` saw its
    scheduled entry): give its snapshot the loop's recorded rights, under
    the lock. Returns the snapshot, or None (no lock, nothing to adopt)."""
    with snapshot_lock(session_name, timeout=5.0) as held:
        if not held:
            return None
        cur = read_snapshot(session_name)
        if not isinstance(cur, dict):
            return None
        rights = loop_rights(cur)
        if rights is None:
            return None
        new = {k: v for k, v in cur.items()
               if k not in ("loop_rules", "loop_joins", "loop_waited")}
        new.update(rights)
        new["scheduled"] = True
        new["turn_origin"] = "scheduled"
        write_merged_snapshot(session_name, new)
        return new


def wait_for_loop(session_name: str, limit: int) -> bool:
    """One more tool call of a possible /loop fire whose entry is not
    written yet: True while it may still wait (a refusal is soft), False
    once it has waited *limit* calls (refusals are real then; the loop's
    rights stay recorded, so a fire written later is still adopted)."""
    with snapshot_lock(session_name, timeout=5.0) as held:
        if not held:
            return True
        cur = read_snapshot(session_name)
        if not isinstance(cur, dict) or "loop_rules" not in cur:
            return False
        waited = cur.get("loop_waited") if isinstance(cur.get("loop_waited"), int) else 0
        if waited >= limit:
            return False
        new = dict(cur)
        new["loop_waited"] = waited + 1
        if waited + 1 >= limit:
            write_merged_snapshot(session_name, new)
            return False
        write_merged_snapshot(session_name, new)
        return True


def clear_triggers(session_name: str) -> None:
    d = triggers_dir(session_name)
    try:
        for f in d.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        d.rmdir()
    except OSError:
        pass


# ---- Live reload (roadmap 8.80) --------------------------------------------

#: The fields a snapshot's rules are made of: what :func:`merge_snapshots`
#: reasons about and the hook enforces. Everything else is provenance.
SAFETY_FIELDS = ("bypass_safety", "confine_writes", *_LIST_FIELDS)

#: What a narrowed snapshot keeps of the one it replaces: everything that
#: is not a rule (the turn's origin and bodies, the style and reply text).
_KEPT_ON_NARROW = ("origin", "style_text", "reply_context", "note_bodies",
                   "scope_mode", "turn_origin", "joined_from_telegram",
                   "drivers", *PEER_STATE_FIELDS, *SCHEDULED_STATE_FIELDS)


def safety_rules(snap) -> dict:
    """The rules part of a snapshot or note (:data:`SAFETY_FIELDS`), in a
    form two of them can be compared by (lists sorted, flags as bools).
    Pure."""
    snap = snap if isinstance(snap, dict) else {}
    out: dict = {
        "bypass_safety": snap.get("bypass_safety") is True,
        "confine_writes": snap.get("confine_writes") is not False,
    }
    for f in _LIST_FIELDS:
        v = snap.get(f)
        out[f] = sorted(x for x in v if isinstance(x, str)) \
            if isinstance(v, list) else []
    return out


def narrowed_snapshot(current, rules: list[dict]) -> dict:
    """*current* (a running turn's snapshot) held to the strictest of
    itself and every one of *rules* (each a :func:`resolve_snapshot`-shaped
    dict, or :func:`floor_snapshot`). Never wider than *current* on any
    axis: *current* is carried as :func:`carried_snapshot` carries it (the
    floor when it is missing or malformed) and :func:`merge_snapshots`
    never widens. Everything that is not a rule is kept from *current*.
    Pure."""
    base = carried_snapshot(current)
    if base is None:
        base = floor_snapshot()
        base["queued_at"] = float("-inf")
    parts = [base]
    for r in rules:
        part = dict(r)
        part["queued_at"] = float("-inf")
        parts.append(part)
    merged = merge_snapshots(parts)
    if isinstance(current, dict):
        for k in _KEPT_ON_NARROW:
            if k in current:
                merged[k] = current[k]
        peer = current.get("peer")
        if isinstance(peer, dict) and peer.get("status") == "pending":
            # Still being checked: the narrowing must outlive the check's
            # result too ("peer message authority", C2).
            merged["pending_joins"] = [
                *_rule_list(current.get("pending_joins")),
                *(safety_rules(r) for r in rules)]
        if "loop_rules" in current:
            # A bare /loop's rights not adopted yet: this narrowing must
            # outlive the adoption too.
            merged["loop_joins"] = [
                *_rule_list(current.get("loop_joins")),
                *(safety_rules(r) for r in rules)]
    return merged


def narrow_snapshot(session_name: str, rules: list[dict]) -> bool:
    """Narrow *session_name*'s canonical snapshot with *rules*
    (:func:`narrowed_snapshot`) and write it. Returns True only when a
    narrower snapshot is on disk afterwards (read back). A session with no
    readable snapshot is left alone: the hook already holds such a turn
    to the floor (``enforce.decide``).

    The hook may write the same file at the same moment (a message
    joining the turn): whichever write lands last wins. The hook's join
    reads the snapshot first, so it keeps this narrowing unless it read
    before this write. The read-back makes such a loss visible: one more
    try, then a WARNING. The read-modify-write runs under the session's
    snapshot lock (:class:`snapshot_lock`), shared with the hook, for at
    most 50 ms (it runs on the daemon's loop; the hook holds the lock for
    milliseconds): past that it goes ahead anyway (a narrowing must not
    wait on a stuck lock), the read-back still checking it."""
    with snapshot_lock(session_name, timeout=0.05):
        return _narrow_snapshot_locked(session_name, rules)


def _narrow_snapshot_locked(session_name: str, rules: list[dict]) -> bool:
    for attempt in range(2):
        current = read_snapshot(session_name)
        if not isinstance(current, dict):
            return False
        new = narrowed_snapshot(current, rules)
        if safety_rules(new) == safety_rules(current):
            # Nothing to narrow; on the retry, the snapshot on disk now
            # carries the narrowing (the hook's write kept it).
            return attempt > 0
        write_merged_snapshot(session_name, new)
        on_disk = read_snapshot(session_name)
        if isinstance(on_disk, dict) and safety_rules(
                narrowed_snapshot(on_disk, rules)) == safety_rules(on_disk):
            return True
    log.warning("could not narrow the policy snapshot of %s: it changed "
                "or could not be written", session_name)
    return False


def clear_snapshot(session_name: str) -> None:
    try:
        snapshot_path(session_name).unlink(missing_ok=True)
    except OSError:
        pass


def reply_context_path(session_name: str) -> Path:
    return Path(instance.runtime_tmp_dir()) / f"claude-reply-{session_name}.txt"


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


def clear_session_files(session_name: str, *, keep_triggers: bool = False) -> None:
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

    *keep_triggers* (session GONE): the session's scheduled prompts stay
    recorded. Claude Code brings them back when the session is resumed
    (session crons, a pending /loop wakeup) and keeps durable ones, so a
    record must outlive the process; only ``/kill`` drops them.
    """
    clear_snapshot(session_name)
    clear_reply_context_file(session_name)
    clear_notes_dir(session_name)
    clear_turn_open(session_name)
    clear_pins(session_name)
    if not keep_triggers:
        clear_triggers(session_name)
    clear_verify_origin(session_name)
    try:
        notes_dir(session_name).rmdir()
    except OSError:
        pass
