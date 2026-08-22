#!/usr/bin/env python3
"""Claude Code notification hook — fire-and-forget UDP datagram to daemon.

Reads JSON from stdin, detects session name from CLAUDE_DTACH_SESSION env var,
sends datagram to the daemon control socket (see SOCKET_PATH below).
No HTTP calls, <5ms.

Also reads the statusLine JSON file (written by the statusLine hook) to
piggyback accurate token data on every PreToolUse event. The statusLine
fires right before PreToolUse, so the file is always current.
"""

import json
import os
import resource
import socket
import sys
from pathlib import Path

# Same precedence as aipager.config._default_socket_path() (kept
# inlined, stdlib-only here rather than importing aipager.config — this
# hook must stay <5ms and importing config transitively pulls in yaml,
# team.py, policy.py, and does I/O). If that function's precedence ever
# changes, mirror the change here and in statusline_notify.py.
# NOTE: bind the *stripped* runtime dir once and use that same value.
# Reading os.environ["XDG_RUNTIME_DIR"] unstripped while guarding on the
# stripped copy meant a padded value produced a path the daemon never
# bound, silently dropping every hook event.
_XDG_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "").strip()
SOCKET_PATH = (
    os.environ.get("AIPAGER_SOCKET_PATH", "").strip()
    or (os.path.join(_XDG_RUNTIME_DIR, "aipager.sock") if _XDG_RUNTIME_DIR else "")
    or "/tmp/aipager.sock"
)

# Address-space cap for the hook subprocess. Baseline is ~34 MB VmSize
# and realistic post-streaming-rewrite max is ~100 MB (recent-transcript
# read + JSON parsing overhead). 1 GB is 10× that — no legitimate hook
# ever approaches it. Its job is to catch true runaways (dmesg has shown
# 1.3 GB and 5.2 GB in the past) and die with MemoryError instead of
# eating gigabytes of host RAM. On a 2 GB VPS/container this still means
# a runaway can't eat more than half the box before self-terminating.
_MEMORY_CAP_BYTES = 1024 * 1024 * 1024

_DEBUG = os.environ.get("AIPAGER_DEBUG") == "1"


def _debug(msg: str) -> None:
    """Print a diagnostic line to stderr when AIPAGER_DEBUG=1.

    Silent by default so we never inject noise into Claude Code's UI.
    """
    if _DEBUG:
        print(f"[aipager-hook] {msg}", file=sys.stderr)


def _read_statusline_tokens(session: str) -> dict | None:
    """Read token data from the statusLine JSON file for this session."""
    status_file = Path(f"/tmp/claude-status-{session}.json")
    try:
        sl = json.loads(status_file.read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError) as e:
        _debug(f"statusline read failed: {type(e).__name__}: {e}")
        return None
    ctx = sl.get("context_window", {})
    cur = ctx.get("current_usage") or {}
    cost = sl.get("cost", {})
    return {
        "context_pct": ctx.get("used_percentage", 0),
        "total_output": ctx.get("total_output_tokens", 0),
        "total_input": ctx.get("total_input_tokens", 0),
        "current_output": cur.get("output_tokens", 0),
        "lines_added": cost.get("total_lines_added", 0),
        "lines_removed": cost.get("total_lines_removed", 0),
    }


def _note_wire(note: dict) -> dict:
    """Minimal, JSON-safe shape of a note for the ``queue_pickup``
    datagram — only what the daemon side (hook_receiver.py) needs."""
    return {
        "msg_id": note.get("msg_id"),
        "chat_id": note.get("chat_id"),
        "raw_text": note.get("raw_text", ""),
    }


def _match_and_promote(session: str, prompt_text: str) -> tuple[list[dict], list[dict]]:
    """Consume the longest PREFIX run of outstanding notes matching
    ``prompt_text``, always leaving the canonical policy snapshot
    correctly overwritten before returning.

    Lists the session's outstanding notes oldest-first and walks them in
    order, searching for each note's ``body`` as a substring of
    ``prompt_text`` starting where the previous match left off — so a
    match requires every consumed note's text to appear, in order, as
    the batching format is deliberately NOT assumed (intent.md's
    confirmed unknown: whether/how Claude concatenates several queued
    messages into one prompt). The first note whose body can't be found
    stops the run; everything before it is "consumed", everything from
    it onward stays outstanding.

    Whatever happens — a full match, a partial run, or no match at all —
    this ALWAYS computes a merged snapshot and overwrites the canonical
    ``/tmp/claude-policy-<session>.json`` before returning (design.md):

    - Matched (``consumed`` non-empty): merge from ONLY the consumed
      notes — this turn is attributable, so only its actual contributors
      restrict it — and delete them (confirmed picked up).
    - Unmatched but notes exist (the "all-outstanding fallback"): merge
      from EVERY outstanding note. This turn's origin can't be
      attributed to any subset, so the safe answer is "as restrictive as
      the most restrictive thing still waiting" — nothing is deleted, so
      an unmatched note keeps feeding future merges too, never widening
      anything (design.md "Why the fallback is safe").
    - No notes outstanding at all: merges to :data:`FLOOR_SNAPSHOT`
      exactly (the "empty floor" path) — never assumed unrestricted,
      and never labelled terminal origin (that would be
      ``enforce.decide()`` returning ``None``, which this never does).

    Returns ``(consumed, expired)`` — the notes matched (in order) and
    any notes dropped for exceeding the pick-up TTL as a side effect of
    listing, for the caller's daemon-side reactions/notice.
    """
    from aipager.policy_snapshot import (
        delete_notes,
        list_outstanding_notes,
        merge_snapshots,
        write_merged_snapshot,
    )

    expired: list[dict] = []
    outstanding = list_outstanding_notes(session, expired_out=expired)

    consumed: list[dict] = []
    cursor = 0
    for note in outstanding:
        body = note.get("body") or ""
        if not body:
            break
        idx = prompt_text.find(body, cursor)
        if idx == -1:
            break
        consumed.append(note)
        cursor = idx + len(body)

    if consumed:
        delete_notes(session, consumed)
        merged = merge_snapshots(consumed)
    else:
        merged = merge_snapshots(outstanding)  # all-outstanding fallback, or floor
    write_merged_snapshot(session, merged)
    return consumed, expired


def _prepare_cap_notifier(session: str) -> tuple[socket.socket | None, bytes]:
    """Pre-open the daemon socket + pre-serialize the cap-hit payload.

    MUST be called BEFORE ``resource.setrlimit`` so the allocations here
    (socket object + JSON bytes) can't themselves trigger the cap. Any
    failure — including a MemoryError from an already-tight parent
    address space — returns ``(None, b"")`` so the cap-hit path silently
    gives up on notifying rather than crash the hook.
    """
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        payload = json.dumps({
            "type": "hook_memory_cap_hit",
            "session": session,
            "hook": "aipager-hook",
        }).encode()
        return sock, payload
    except (OSError, MemoryError):
        return None, b""


def main():
    # Read the session env var first — the cap-hit notifier needs it in
    # its pre-serialized payload.
    session = os.environ.get("CLAUDE_DTACH_SESSION", "")

    # Pre-allocate everything the cap-hit notifier needs BEFORE the cap
    # is set. At MemoryError time no new allocations are possible, so we
    # must hold a live socket and pre-encoded bytes ready for a raw
    # sendto().
    cap_sock, cap_payload = _prepare_cap_notifier(session)

    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (_MEMORY_CAP_BYTES, _MEMORY_CAP_BYTES),
        )
    except (ValueError, OSError):
        pass  # some kernels/containers reject rlimit tightening; never wedge claude

    # Single-element list acts as a zero-allocation swap slot: ``_run``
    # can replace ``cap_slot[0]`` with an enriched payload (e.g. one
    # tagged with the current tool_name) once it knows more. The except
    # handler below reads ``cap_slot[0]`` without allocating, so it
    # picks up whatever the most recent successful swap left behind.
    cap_slot = [cap_payload]

    try:
        _run(session, cap_slot)
    except MemoryError:
        # Cap tripped mid-work. Fire the pre-baked datagram (best-effort,
        # never raises), then exit non-zero so Claude sees the failure.
        if cap_sock is not None:
            try:
                cap_sock.sendto(cap_slot[0], SOCKET_PATH)
            except OSError:
                pass
        sys.exit(1)


def _run(session: str, cap_slot: list[bytes]) -> None:
    """Main hook body — separated so ``main()`` can wrap it in a single
    ``try/except MemoryError``. Any allocation inside here that pushes
    the process past the cap will trip that handler.

    ``cap_slot`` is a one-element list holding the pre-serialized
    cap-hit payload; we mutate ``cap_slot[0]`` in place to enrich it
    (e.g. with the current tool name) as we learn more. Best-effort:
    any failure to serialize the richer payload silently keeps the
    fallback bytes, so the notification path never crashes the hook.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    # Enrich the cap-hit payload with the tool name now that we know it.
    # If the balloon fires later (typically inside the enforce path),
    # the notification will read "cap hit during Bash" instead of the
    # bare "cap hit". If serialization itself trips the cap or the
    # tool_name is pathological, we silently keep the fallback bytes.
    tool_name = data.get("tool_name", "")
    if tool_name:
        try:
            cap_slot[0] = json.dumps({
                "type": "hook_memory_cap_hit",
                "session": session,
                "hook": "aipager-hook",
                "tool": tool_name,
            }).encode()
        except (MemoryError, ValueError, TypeError):
            pass

    if session:
        data["session"] = session

    # Piggyback statusLine token data on hook events. Skipped for
    # MessageDisplay: it runs synchronously inside Claude Code's display
    # path — a slow hook stalls Claude's own rendering — it fires several
    # times per assistant message, and the card reads token counts from the
    # tool events anyway.
    if session and data.get("hook_event_name") != "MessageDisplay":
        tokens = _read_statusline_tokens(session)
        if tokens:
            data["sl_tokens"] = tokens

    # Fire-and-forget UDP datagram
    def _udp(payload: dict) -> None:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            s.sendto(json.dumps(payload).encode(), SOCKET_PATH)
            s.close()
        except OSError as e:
            _debug(f"daemon socket {SOCKET_PATH} unreachable: {e}")
            # daemon not running — session_monitor catches it

    _udp(data)

    # Phase E: PreToolUse safety enforcement. The daemon notify above is
    # fire-and-forget; here we may additionally BLOCK the tool by emitting
    # a Claude Code deny decision on stdout. Best-effort — any error falls
    # through to "allow" so the hook never wedges a session.
    if data.get("hook_event_name") == "PreToolUse":
        try:
            from aipager.dtach.enforce import decide, deny_decision_json
            block = decide(data)
            if block:
                _udp({
                    "hook_event_name": "safety_blocked",
                    "session": data.get("session", ""),
                    "tool": block["tool"],
                    "reason": block["reason"],
                })
                print(deny_decision_json(block["reason"]))
        except MemoryError:
            raise  # let main() handle it uniformly
        except Exception as e:  # never wedge claude on enforcement bugs
            _debug(f"enforcement error (allowing): {e}")

    # /settings reply-style injection (item 6.2). The daemon precomputes
    # `style_text` on the session's policy snapshot at prompt-injection
    # time (aipager.preferences.style_text); this hook's job is trivial
    # by design — a dict lookup and one print — so it stays fast and
    # can't itself get the instruction text wrong. This is the FIRST
    # event this hook ever writes to stdout on, so the shape mirrors the
    # PreToolUse branch above (try/except Exception, never wedge claude)
    # and prints nothing at all — not even an empty line — when there's
    # nothing to say, matching every other event's existing silence.
    elif data.get("hook_event_name") == "UserPromptSubmit":
        # Queue-handoff (design.md): match this pick-up against the
        # session's outstanding per-message notes and rewrite the
        # canonical policy snapshot from the merge BEFORE the style/
        # reply-context read below — that read must see THIS turn's
        # merged snapshot, not a stale one from whenever `_inject_prompt`
        # last wrote it (it no longer writes it at all).
        submit_session = data.get("session", "")
        if submit_session:
            try:
                consumed, expired = _match_and_promote(
                    submit_session, data.get("prompt", "") or "",
                )
                if consumed or expired:
                    _udp({
                        "hook_event_name": "queue_pickup",
                        "session": submit_session,
                        "consumed": [_note_wire(n) for n in consumed],
                        "expired": [_note_wire(n) for n in expired],
                    })
            except MemoryError:
                raise
            except Exception as e:
                # Never leave a stale (possibly broader) snapshot in
                # place on an unexpected failure — fail closed to the
                # floor rather than fail open to whatever was written
                # for some earlier turn.
                _debug(f"queue pickup matching error (falling back to "
                       f"floor): {e}")
                try:
                    from aipager.policy_snapshot import (
                        merge_snapshots, write_merged_snapshot,
                    )
                    write_merged_snapshot(submit_session, merge_snapshots([]))
                except Exception:
                    pass
        try:
            from aipager.policy_snapshot import read_snapshot
            snap = read_snapshot(data.get("session", "")) or {}
            style = snap.get("style_text", "")
            reply_context = snap.get("reply_context", "")
            # A missing, corrupt, or old-shape snapshot must not crash
            # the hook — filter out anything that isn't actually a
            # string (e.g. a stray int from manual editing) rather than
            # letting a non-string slip into the join below.
            parts = [p for p in (style, reply_context) if isinstance(p, str) and p]
            combined = "\n\n".join(parts)
            if combined:
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": combined,
                    },
                }))
        except MemoryError:
            raise
        except Exception as e:  # never wedge claude on a style-lookup bug
            _debug(f"style injection error (skipping): {e}")


if __name__ == "__main__":
    main()
