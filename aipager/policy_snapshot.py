"""Per-session policy snapshot (Phase E).

The daemon resolves a session driver's *effective* safety rules and
writes them to ``/tmp/claude-policy-<session>.json`` on each Telegram
prompt. The PreToolUse hook (which can't see daemon memory) reads this
snapshot to decide whether to block a tool call. Origin is determined
separately by the hook (from the transcript marker); the snapshot just
carries the resolved rule sets + the owner bypass flag.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path

from aipager import safety
from aipager.state import QUEUE_MAX_AGE_SECONDS

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


def notes_dir(session_name: str) -> Path:
    """Directory holding a session's not-yet-confirmed-picked-up notes.

    A plain function, like :func:`snapshot_path` — tests monkeypatch this
    name to redirect writes to ``tmp_path`` rather than real ``/tmp``.
    """
    return Path(f"/tmp/claude-notes-{session_name}")


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
FLOOR_SNAPSHOT: dict = {
    "bypass_safety": False,
    "deny_tools": [],
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

def write_note(
    session_name: str, role, scope, member, *,
    msg_id: int | None, chat_id: int | None,
    sender_key: tuple[int, int] | None,
    body: str, raw_text: str,
    style_text: str = "", reply_context: str = "",
) -> Path | None:
    """Write one per-message policy note (design.md "queue handoff").

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
    note["body"] = body
    note["raw_text"] = raw_text
    note["queued_at"] = time.time()

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
    now = now if now is not None else time.time()
    cutoff = now - QUEUE_MAX_AGE_SECONDS
    d = notes_dir(session_name)
    try:
        entries = sorted(d.iterdir())
    except OSError:
        return []

    out: list[dict] = []
    for p in entries:
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
        if queued_at_f < cutoff:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
            if expired_out is not None:
                expired_out.append(note)
            continue
        out.append(note)

    out.sort(key=lambda n: (n.get("queued_at") if isinstance(
        n.get("queued_at"), (int, float)) else now, str(n.get("_path"))))
    return out


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
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def clear_notes_dir(session_name: str) -> int:
    """Delete every outstanding note for a session (best-effort).

    Called by Stop, ``/clearqueue`` (and the Mini App's equivalent
    route), and session GONE/kill cleanup — so nothing lingers to
    restrict an unrelated future turn for the rest of its TTL. Returns
    the number of note files actually removed.
    """
    d = notes_dir(session_name)
    try:
        entries = list(d.iterdir())
    except OSError:
        return 0
    removed = 0
    for p in entries:
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


def outstanding_sender_keys(session_name: str) -> set[tuple[int, int]]:
    """``{sender_key, ...}`` for every currently outstanding note.

    Used by the mixed-sender hold (design.md): an inbound prompt from a
    Telegram user different from whoever already has a note outstanding
    must be held rather than merged in blind — this is what keeps two
    different humans' permissions from silently combining into one turn.
    """
    out: set[tuple[int, int]] = set()
    for note in list_outstanding_notes(session_name):
        key = note.get("sender_key")
        if isinstance(key, list) and len(key) == 2:
            out.add((key[0], key[1]))
    return out


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
    return len(sess.pending_queue) + len(list_outstanding_notes(sess.name))


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
        "deny_tools": sorted(deny_tools),
        "allow_tools": allow_tools,
        "deny_paths_no_access": sorted(deny_paths_no_access),
        "deny_paths_no_write": sorted(deny_paths_no_write),
        "deny_bash_patterns": sorted(deny_bash_patterns),
        "style_text": last.get("style_text") or "",
        "reply_context": last.get("reply_context") or "",
    }


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
