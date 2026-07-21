#!/usr/bin/env python3
"""Claude Code notification hook — fire-and-forget UDP datagram to daemon.

Reads JSON from stdin, detects session name from CLAUDE_DTACH_SESSION env var,
sends datagram to /tmp/aipager.sock. No HTTP calls, <5ms.

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

SOCKET_PATH = "/tmp/aipager.sock"

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

    # Piggyback statusLine token data on hook events
    if session:
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


if __name__ == "__main__":
    main()
