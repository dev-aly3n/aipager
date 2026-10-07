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
import re
import resource
import socket
import sys
from pathlib import Path

# Same precedence as aipager.instance.control_socket_path() (kept
# inlined, stdlib-only here rather than importing aipager.config — this
# hook must stay <5ms and importing config transitively pulls in yaml,
# team.py, policy.py, and does I/O). If that function's precedence ever
# changes, mirror the change here and in statusline_notify.py.
# An isolated instance (AIPAGER_INSTANCE_DIR) wins first: its sessions'
# hooks must reach that instance's daemon, never the operator's.
# NOTE: bind the *stripped* runtime dir once and use that same value.
# Reading os.environ["XDG_RUNTIME_DIR"] unstripped while guarding on the
# stripped copy meant a padded value produced a path the daemon never
# bound, silently dropping every hook event.
_INSTANCE_DIR = os.environ.get("AIPAGER_INSTANCE_DIR", "").strip()
_INSTANCE_DIR = os.path.normpath(_INSTANCE_DIR) if _INSTANCE_DIR else ""
_XDG_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "").strip()
SOCKET_PATH = (
    (os.path.join(_INSTANCE_DIR, "aipager.sock") if _INSTANCE_DIR else "")
    or os.environ.get("AIPAGER_SOCKET_PATH", "").strip()
    or (os.path.join(_XDG_RUNTIME_DIR, "aipager.sock") if _XDG_RUNTIME_DIR else "")
    or "/tmp/aipager.sock"
)
# Where aipager-statusline writes the per-session status file
# (aipager.instance.runtime_tmp_dir(), inlined for the same reason).
_STATUS_DIR = _INSTANCE_DIR or "/tmp"

# Address-space cap for the hook subprocess. Baseline is ~34 MB VmSize
# and realistic post-streaming-rewrite max is ~100 MB (recent-transcript
# read + JSON parsing overhead). 1 GB is 10× that — no legitimate hook
# ever approaches it. Its job is to catch true runaways (dmesg has shown
# 1.3 GB and 5.2 GB in the past) and die with MemoryError instead of
# eating gigabytes of host RAM. On a 2 GB VPS/container this still means
# a runaway can't eat more than half the box before self-terminating.
_MEMORY_CAP_BYTES = 1024 * 1024 * 1024

_DEBUG = os.environ.get("AIPAGER_DEBUG") == "1"

# A self-triggered continuation turn's prompt (design.md "model Claude
# Code background-agent jobs") — Claude Code wakes a session back up when a
# background agent it launched finishes, with a synthetic UserPromptSubmit
# whose prompt carries this prefix instead of any human-typed text. This
# hook must not consume notes or overwrite the pinned policy snapshot for
# it (spec.md's documented safety leak: the all-outstanding fallback would
# otherwise widen the job's restrictions to the floor mid-job). A private
# module constant, deliberately not imported from aipager.dtach.hook_receiver
# / aipager.dtach.enforce's own copies — this hook stays stdlib-only to
# hold its <5ms budget (see the SOCKET_PATH comment above).
#
# Scope (review-1#rev-iter1-002): this is a raw prefix match, not a
# signed/correlated check. Safe by construction in scoped/team mode
# (`session_ops._inject_prompt` always prepends the Telegram marker
# first, so a spoofed user prompt can never win the match here); in
# personal mode (no team configured) a user-typed message literally
# starting with this string is indistinguishable from a real
# continuation — accepted, since personal mode has no role/snapshot
# separation to leak. See enforce.py's own copy of this note for the
# full reasoning.
_TASK_NOTIFICATION_PREFIX = "<task-notification>"

#: A PreToolUse payload, recognised from its raw text before it is decoded
#: (roadmap 8.107): one the hook then cannot decode or handle is denied,
#: never let through by an exit Claude Code reads as non-blocking. An
#: escaped event name in a payload that also cannot be decoded is missed;
#: Claude Code's serializer never escapes ASCII.
_PRE_TOOL_USE_RAW = re.compile(r'"hook_event_name"\s*:\s*"PreToolUse"')

# design.md "answer PermissionRequest hooks with a decision instead of
# keystrokes": how long THIS hook process itself waits, after a
# successful forward send, for the daemon to deliver a verdict over the
# reply socket before giving up and printing nothing (falling through to
# Claude Code's own interactive dialog). 20s leaves a huge margin under
# Claude Code's own external hook timeout for this event (30s once
# settings_patch.py's PermissionRequest entry carries one, 600s
# otherwise) while still giving a genuinely-away-from-desk Telegram user
# a real chance to tap a button. Overridable for tests (and only tests —
# there is no supported production reason to change this) via
# AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS; any non-positive or
# unparseable value silently falls back to this default rather than
# ever raising or hanging the hook.
_PERMISSION_REPLY_DEADLINE_SECONDS = 20


def _permission_reply_deadline_seconds() -> float:
    raw = os.environ.get("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", "").strip()
    if raw:
        try:
            val = float(raw)
        except ValueError:
            val = None
        if val is not None and val > 0:
            return val
    return _PERMISSION_REPLY_DEADLINE_SECONDS


def _debug(msg: str) -> None:
    """Print a diagnostic line to stderr when AIPAGER_DEBUG=1.

    Silent by default so we never inject noise into Claude Code's UI.
    """
    if _DEBUG:
        print(f"[aipager-hook] {msg}", file=sys.stderr)


def _read_statusline_tokens(session: str) -> dict | None:
    """Read token data from the statusLine JSON file for this session."""
    status_file = Path(_STATUS_DIR) / f"claude-status-{session}.json"
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
    datagram — only what the daemon side (hook_receiver.py) needs.

    Deliberately an allow-list, not a passthrough of ``note``: a future
    field added to the note shape (e.g. a permission field, or
    ``sender_key``) must not be forwarded here by accident just because
    it exists on the dict. Add new keys explicitly, one at a time.
    """
    return {
        "msg_id": note.get("msg_id"),
        "chat_id": note.get("chat_id"),
        "raw_text": note.get("raw_text", ""),
        # Who sent it — so the daemon can record the author of the turn's
        # prompt, which decides whether a Retry may run as the tapper
        # (roadmap 8.50). Identity only; no permission field travels.
        "sender_key": note.get("sender_key"),
        # The prompt's real author, never the sender_key fallback (review
        # rev-iter4-002); what note_driver_id reads on the daemon side.
        "author_user_id": note.get("author_user_id"),
    }


def _match_and_promote(
    session: str, prompt_text: str, *, report: dict | None = None,
) -> tuple[list[dict], list[dict]]:
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
    - Unmatched, and the prompt carries no Telegram text the current
      snapshot was not already built from (a message typed in the
      terminal and queued behind the turn, another local session's
      message, a redelivery): the running turn keeps its snapshot,
      narrowed by any outstanding note — never widened
      (``policy_snapshot.snapshot_for_unattributed_prompt``, roadmap 8.49).
    - Otherwise, unmatched but notes exist (the "all-outstanding fallback"): merge
      from EVERY outstanding note. This turn's origin can't be
      attributed to any subset, so the safe answer is "as restrictive as
      the most restrictive thing still waiting" — nothing is deleted, so
      an unmatched note keeps feeding future merges too, never widening
      anything (design.md "Why the fallback is safe").
    - Otherwise, no notes outstanding at all: merges to the floor
      (``policy_snapshot.floor_snapshot``, built-in plus policy.yaml's
      ``safety:`` section; the "empty floor" path) — never assumed
      unrestricted,
      and never labelled terminal origin (that would be
      ``enforce.decide()`` returning ``None``, which this never does).

    While a turn is already running (the session's turn-open file,
    roadmap 8.77) the prompt is a message joining it, and whichever answer
    the rules above give is merged, strictest wins, with the running
    turn's snapshot (``policy_snapshot.snapshot_for_prompt``). The
    turn-open file is written here, after the snapshot, whatever happens.

    Returns ``(consumed, expired)`` — the notes matched (in order) and
    any notes dropped for exceeding the pick-up TTL as a side effect of
    listing, for the caller's daemon-side reactions/notice. A ``report``
    dict is filled with what the daemon needs to know about the turn
    (roadmap 8.77): ``fresh`` (this prompt started a turn: none was
    open), ``origin`` (the turn's origin as written) and ``authors`` (the
    consumed notes' authors, ``None`` where unknown).
    """
    from aipager.policy_snapshot import (
        delete_notes,
        list_outstanding_notes,
        mark_turn_open,
        match_notes_for_prompt,
        note_driver_id,
        snapshot_for_prompt,
        turn_is_open,
        write_merged_snapshot,
    )

    turn_open = turn_is_open(session)
    if report is not None:
        # Known before anything can fail: the caller's failure path needs
        # it (``snapshot_after_failure``).
        report["fresh"] = not turn_open
    try:
        expired: list[dict] = []
        outstanding = list_outstanding_notes(session, expired_out=expired)

        # Reuse ONLY the pure matcher — never `consume_notes_matching`,
        # which calls `list_outstanding_notes` itself. Doing that here
        # would call it TWICE per pick-up (once inside
        # `consume_notes_matching`, once for the `expired_out=expired`
        # collection just above), and `list_outstanding_notes` TTL-prunes
        # as a side effect: the first call would silently swallow the
        # truly-expired notes before the `expired_out` collection above
        # ever sees them, under-reporting `expired` on the datagram
        # (double-prune trap — see `policy_snapshot.consume_notes_matching`'s
        # own docstring). The prefix run, plus the note that sent a slash
        # command when a lingering note ahead of it stopped the run
        # (roadmap 8.74).
        consumed = match_notes_for_prompt(outstanding, prompt_text)

        if consumed:
            delete_notes(session, consumed)
        # Matched → the consumed notes' merge; unmatched → keep the running
        # turn's snapshot unless the prompt carries unattributed Telegram
        # text (roadmap 8.49); else the all-outstanding fallback, or the
        # floor. A turn already running is never widened (8.77).
        merged = snapshot_for_prompt(session, prompt_text, outstanding,
                                     consumed, turn_open=turn_open)
        write_merged_snapshot(session, merged)
    finally:
        # After the snapshot: this prompt's own pick-up must see the turn
        # as it was before it. Marked even when the pick-up failed (the
        # caller then writes the floor).
        mark_turn_open(session)
    if report is not None:
        report.update({
            "fresh": not turn_open,
            "origin": merged.get("turn_origin"),
            "authors": [note_driver_id(n) for n in consumed],
        })
    return consumed, expired


#: SessionStart sources that begin a new conversation in the process. Not
#: ``compact``: an auto-compact can run in the middle of a turn, and
#: clearing the turn state there would let a message joining the rest of
#: that turn widen it (roadmap 8.77).
_NEW_CONVERSATION_SOURCES = ("startup", "resume", "clear")


def _end_turn(session: str, data: dict) -> None:
    """Clear the session's turn-open file at a turn end (roadmap 8.77).

    - Stop / StopFailure: the turn is over, unless the transcript shows a
      message Claude Code still holds in its queue. That message becomes
      the next turn at once, with no UserPromptSubmit (measured
      2026-09-05), and it already joined the turn's snapshot when it was
      queued, so the turn is still open as far as the snapshot goes.
      Missing that evidence is the old behaviour (a fresh turn at the next
      prompt); the daemon's hold covers a different sender there.
    - SessionStart for a new conversation (not a compact).

    Never raises: a missed clear only makes the next fresh turn stricter.
    """
    if not session:
        return
    try:
        from aipager.policy_snapshot import clear_turn_open, turn_is_open

        if not turn_is_open(session):
            return  # nothing to clear: skip the transcript read
        if data.get("hook_event_name") == "SessionStart":
            if data.get("source") not in _NEW_CONVERSATION_SOURCES:
                return
        else:
            path = data.get("transcript_path")
            if isinstance(path, str) and path:
                from aipager.transcript import read_still_queued
                if read_still_queued(path):
                    return
        clear_turn_open(session)
    except MemoryError:
        raise
    except Exception as e:
        _debug(f"turn-open clear error (kept): {e}")


def _answer_model_switch(session: str, data: dict) -> None:
    """PreModelSwitch (roadmap 8.35): allow — skipping Claude Code's
    "Switch model?" cache confirmation — only a switch aipager itself
    typed into this session, to that exact model, moments ago. Anything
    else gets no output at all, so Claude Code does exactly what it does
    without aipager.

    Local file checks only (``model_switch_marker``): no socket, no wait,
    nothing forwarded to the daemon — a switch is not turn evidence, and
    this hook runs inside Claude Code's model-switch path. Never raises.
    """
    try:
        from aipager.dtach import model_switch_marker

        decision = model_switch_marker.decide(
            os.path.dirname(SOCKET_PATH) or "/tmp", session, data,
        )
        if decision is not None:
            print(json.dumps(decision))
    except MemoryError:
        raise
    except Exception as e:  # never wedge claude — no decision is the safe answer
        _debug(f"model switch marker error (no decision): {e}")


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
    # Slot 1 flips to True once ``_run`` knows this is a PreToolUse
    # event: a cap hit while deciding one must deny the tool, not let it
    # through unchecked.
    cap_slot = [cap_payload, False]

    try:
        _run(session, cap_slot)
    except MemoryError:
        # Cap tripped mid-work. Fire the pre-baked datagram (best-effort,
        # never raises), then exit non-zero so Claude sees the failure —
        # 2 for a PreToolUse event, which Claude Code reads as "deny".
        if cap_sock is not None:
            try:
                cap_sock.sendto(cap_slot[0], SOCKET_PATH)
            except OSError:
                pass
        sys.exit(2 if cap_slot[1] else 1)
    except Exception as e:
        # Roadmap 8.107: anything else escaping a PreToolUse (a payload
        # nested past the decoder's limit, a non-object, a crash before
        # the decision) used to exit 1, which Claude Code reads as a
        # NON-blocking error: the tool ran unchecked. Give enforce's own
        # fail-closed answer instead. Any other event keeps its old exit:
        # a non-zero one there never blocks Claude.
        if not cap_slot[1]:
            raise
        try:
            _deny_unhandled_pre_tool_use(session, e)
        except BaseException:
            # Even the answer could not be written: exit 2, which Claude
            # Code reads as a deny for PreToolUse.
            sys.exit(2)


def _deny_unhandled_pre_tool_use(session: str, error: Exception) -> None:
    """Answer a PreToolUse the hook could not handle as ``enforce`` answers
    a decision that failed: deny, unless the session's snapshot grants
    the owner's bypass (``enforce.fail_closed``). Called only for an
    error that escaped ``_run`` before any decision was printed (the
    decision branch catches its own errors)."""
    try:
        _debug(f"PreToolUse could not be handled (denying unless owner): {error!r}")
        from aipager.dtach import enforce
        # The session is the hook's own (the payload's "session" is set
        # from it), so the owner's bypass is found without the payload.
        block = enforce.fail_closed({"session": session, "tool_name": ""})
        if block is not None:
            print(enforce.deny_decision_json(block["reason"]))
    except Exception:
        # The enforcer itself is unavailable: nothing can read the
        # snapshot either, so deny.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason":
                "aipager safety policy: the safety check could not run",
        }}))
    sys.stdout.flush()


def _run(session: str, cap_slot: list) -> None:
    """Main hook body — separated so ``main()`` can wrap it in a single
    ``try/except MemoryError``. Any allocation inside here that pushes
    the process past the cap will trip that handler.

    ``cap_slot`` holds the pre-serialized cap-hit payload at index 0
    and, when ``main()`` passes it, a PreToolUse flag at index 1 (set
    below, read by main's MemoryError handler); we mutate ``cap_slot[0]`` in place to enrich it
    (e.g. with the current tool name) as we learn more. Best-effort:
    any failure to serialize the richer payload silently keeps the
    fallback bytes, so the notification path never crashes the hook.
    """
    # (A read that fails here, at hundreds of MB or on invalid UTF-8, is
    # still the old non-blocking exit: neither comes from Claude Code.)
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    # A PreToolUse from here on denies on any failure (main()), the
    # decode itself included (roadmap 8.107).
    pre_tool_use = len(cap_slot) > 1 and bool(_PRE_TOOL_USE_RAW.search(raw))
    if pre_tool_use:
        cap_slot[1] = True

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if pre_tool_use:
            raise
        sys.exit(0)
    if not isinstance(data, dict):
        if pre_tool_use:
            raise TypeError("hook payload is not a JSON object")
        sys.exit(0)
    if len(cap_slot) > 1:
        # Decoded, the event's own name decides (an escaped one the raw
        # match missed, or a nested "PreToolUse" text the raw match took
        # for it): only a PreToolUse may ever answer with a deny.
        cap_slot[1] = data.get("hook_event_name") == "PreToolUse"

    if data.get("hook_event_name") == "PreModelSwitch":
        _answer_model_switch(session, data)
        return

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

    # Fire-and-forget UDP datagram. Returns whether the sendto() itself
    # succeeded (a listener existed at SOCKET_PATH) — NOT delivery/
    # processing confirmation. Every pre-existing call site below ignores
    # this; the new PermissionRequest branch is the only caller that
    # reads it, to skip the reply wait outright when the daemon is
    # provably down rather than blocking for the full deadline.
    def _udp(payload: dict) -> bool:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            s.sendto(json.dumps(payload).encode(), SOCKET_PATH)
            s.close()
            return True
        except OSError as e:
            _debug(f"daemon socket {SOCKET_PATH} unreachable: {e}")
            # daemon not running — session_monitor catches it
            return False

    hook_event_name = data.get("hook_event_name")

    if hook_event_name == "PermissionRequest":
        # design.md "answer PermissionRequest hooks with a decision
        # instead of keystrokes": the reply-socket address/id must be
        # embedded in `data` BEFORE it is forwarded, so the daemon can
        # reply to THIS specific, still-parked invocation. Lazy import —
        # this is the only branch of this file that pays any cost beyond
        # the stdlib; every other event (including the plain _udp(data)
        # forward in the `else` below) never imports this module.
        sock = None
        reply_path = None
        request_id = None
        try:
            from aipager.dtach import hook_reply

            runtime_dir = os.path.dirname(SOCKET_PATH) or "/tmp"
            opened = hook_reply.open_reply_socket(runtime_dir)
            if opened is not None:
                sock, reply_path, request_id = opened
                data["aipager_reply_addr"] = reply_path
                data["aipager_request_id"] = request_id

            forward_ok = _udp(data)

            if opened is not None and forward_ok:
                decision = hook_reply.wait_for_decision(
                    sock, request_id, _permission_reply_deadline_seconds(),
                )
                if decision is not None:
                    print(json.dumps({
                        "hookSpecificOutput": {
                            "hookEventName": "PermissionRequest",
                            "decision": decision,
                        },
                    }))
                else:
                    # No (or no well-formed, on-time) verdict — best-
                    # effort tell the daemon this specific reply channel
                    # is abandoned, shrinking the window where a stale
                    # Telegram tap thinks it can still reach this hook.
                    # Diagnostic only: its own failure is swallowed.
                    try:
                        _udp({
                            "hook_event_name": "permission_reply_timeout",
                            "session": data.get("session", ""),
                            "aipager_request_id": request_id,
                        })
                    except Exception:
                        pass
        except MemoryError:
            raise  # let main() handle it uniformly
        except Exception as e:  # never wedge claude — fall through to the dialog
            _debug(f"permission reply error (falling through to dialog): {e}")
        finally:
            if sock is not None:
                try:
                    hook_reply.close_reply_socket(sock, reply_path)
                except Exception:
                    pass
    elif hook_event_name == "UserPromptSubmit":
        # design.md "turn anchor follows consumption" R6: the queue-handoff
        # pick-up match + its `queue_pickup` datagram must reach the daemon
        # BEFORE the forwarded UserPromptSubmit does, so the receiver's
        # IDLE→BUSY card for the new turn is built from the ALREADY-updated
        # `trigger_msg_id` — one send, not a send-then-correct. The UDS
        # datagram socket preserves order for one sender, so emission order
        # here is delivery order there. `prompt_text` is a function-scope
        # local (not block-scope) — the SECOND `elif hook_event_name ==
        # "UserPromptSubmit":` branch further down (style/reply-context
        # injection) reads this exact same variable; both branches only
        # ever run when hook_event_name == "UserPromptSubmit", so it is
        # always already bound by the time that branch reads it.
        prompt_text = data.get("prompt", "") or ""
        submit_session = data.get("session", "")
        if submit_session and not prompt_text.startswith(_TASK_NOTIFICATION_PREFIX):
            # Continuation turn: the SAME job waking itself up, not a new
            # human prompt (design.md "model Claude Code background-agent
            # jobs") — skip the queue-handoff match entirely so it never
            # consumes notes meant for a real prompt, exactly as the
            # style/reply-context branch below already skips for the same
            # reason.
            turn: dict = {}
            try:
                consumed, expired = _match_and_promote(
                    submit_session, prompt_text, report=turn)
                if turn:
                    # Who runs the turn this prompt started, for the
                    # daemon's hold of a different sender's message
                    # (roadmap 8.77, D-H). Rides the forwarded event
                    # below; identity only.
                    data["aipager_turn"] = turn
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
                        snapshot_after_failure, write_merged_snapshot,
                    )
                    # A running turn keeps its own stricter rules too
                    # (roadmap 8.77). Unknown whether one runs: assume so.
                    write_merged_snapshot(submit_session, snapshot_after_failure(
                        submit_session, prompt_text,
                        turn_open=turn.get("fresh") is not True))
                except Exception:
                    pass
        elif submit_session:
            # A continuation wakes the job's turn back up (roadmap 8.77):
            # it is open again, and its snapshot stays as it is.
            try:
                from aipager.policy_snapshot import mark_turn_open
                mark_turn_open(submit_session)
            except MemoryError:
                raise
            except Exception as e:
                _debug(f"turn-open mark error: {e}")
        _udp(data)
    elif hook_event_name in ("Stop", "StopFailure", "SessionStart"):
        # Before the event reaches the daemon: its turn end may send the
        # next message at once, and that one starts a turn of its own.
        _end_turn(data.get("session", ""), data)
        _udp(data)
    else:
        _udp(data)

    # Phase E: PreToolUse safety enforcement. The daemon notify above is
    # fire-and-forget; here we may additionally BLOCK the tool by emitting
    # a Claude Code deny decision on stdout. Best-effort — any error falls
    # through to "allow" so the hook never wedges a session.
    if hook_event_name == "PreToolUse":
        # A MemoryError from here on exits 2, which Claude Code treats as
        # a deny for PreToolUse (main()); the slot write allocates nothing.
        if len(cap_slot) > 1:
            cap_slot[1] = True
        try:
            from aipager.dtach import enforce
            try:
                block = enforce.decide(data)
            except MemoryError:
                raise
            except Exception as e:
                # decide() already fails closed; this covers a bug around
                # it. Deny unless the snapshot grants the owner's bypass.
                _debug(f"enforcement error (denying unless owner): {e}")
                block = enforce.fail_closed(data)
            if block:
                # Deny first: a failure reporting it must not undo it.
                print(enforce.deny_decision_json(block["reason"]))
                sys.stdout.flush()
                try:
                    _udp({
                        "hook_event_name": "safety_blocked",
                        "session": data.get("session", ""),
                        "tool": block["tool"],
                        "reason": block["reason"],
                    })
                except Exception as e:
                    _debug(f"safety_blocked notify failed: {e}")
        except MemoryError:
            raise  # let main() handle it uniformly
        except Exception as e:
            # The enforcer could not even be imported. Nothing can read
            # the snapshot either, so deny.
            _debug(f"enforcement unavailable (denying): {e}")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    "aipager safety policy: the safety check could not run",
            }}))

    # /settings reply-style injection (item 6.2). The daemon precomputes
    # `style_text` on the session's policy snapshot at prompt-injection
    # time (aipager.preferences.style_text); this hook's job is trivial
    # by design — a dict lookup and one print — so it stays fast and
    # can't itself get the instruction text wrong. This is the FIRST
    # event this hook ever writes to stdout on, so the shape mirrors the
    # PreToolUse branch above (try/except Exception, never wedge claude)
    # and prints nothing at all — not even an empty line — when there's
    # nothing to say, matching every other event's existing silence.
    elif hook_event_name == "UserPromptSubmit":
        # The queue-handoff match itself (and its `queue_pickup` emission)
        # already ran in the FIRST `if/elif` chain above, before this
        # event was forwarded (R6) — `prompt_text` is the SAME function-
        # scope local set there, reused rather than re-fetched. Only the
        # continuation early-return and the style/reply-context injection
        # are left here.
        if prompt_text.startswith(_TASK_NOTIFICATION_PREFIX):
            # Continuation turn: the SAME job waking itself up, not a new
            # human prompt (design.md "model Claude Code background-agent
            # jobs"). The style/reply-context additionalContext print
            # (already injected on the real prompt that started this job)
            # is skipped entirely for it, same as the queue-handoff match
            # already was above.
            return
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
