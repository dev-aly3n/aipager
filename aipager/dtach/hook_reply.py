"""Wire format + transport for the daemon <-> hook ``PermissionRequest``
reply channel (design.md "answer PermissionRequest hooks with a decision
instead of keystrokes").

The hook (``notify_hook.py``) already has a one-way, fire-and-forget
channel to the daemon over ``SOCKET_PATH``. This module adds the reverse
leg: a per-request ``SOCK_DGRAM`` unix socket, bound by the hook to a
**filesystem path** (never the Linux-only abstract namespace — macOS is a
supported install target), that the daemon can ``sendto()`` a verdict to
while the hook is still parked waiting for one.

Stdlib-only top-level imports by design — this module is imported
*lazily* by ``notify_hook.py`` (only on the ``PermissionRequest`` branch,
to preserve that hook's <5ms budget for every other event) and *eagerly*
by ``aipager/bot/callbacks.py`` (the daemon side, where import cost is
irrelevant).

Wire format:

Hook -> daemon (piggybacked onto the existing forward datagram, two new
keys added to that payload):
    ``aipager_reply_addr``: the filesystem path of the reply socket.
    ``aipager_request_id``: a fresh ``uuid4().hex``, embedded in the
    reply socket's filename too (deliberately not the session name — a
    long session label risks AF_UNIX's ~108-byte ``sun_path`` limit).

Daemon -> hook (sent to ``aipager_reply_addr``):
    ``{"v": 1, "request_id": "<echo>", "decision": {...}}``
    where ``decision`` is exactly Claude Code's documented
    ``PermissionRequest`` decision object
    (``{"behavior": "allow", "updatedPermissions": [...]}`` or
    ``{"behavior": "deny", "message": "...", "interrupt": false}``).
"""

from __future__ import annotations

import json
import os
import socket
import uuid

PROTOCOL_VERSION = 1

_VALID_BEHAVIORS = frozenset({"allow", "deny"})


def new_request_id() -> str:
    """A fresh, unpredictable correlation id — the sole source of
    filename/path uniqueness for a reply socket (see module docstring:
    deliberately not the session name)."""
    return uuid.uuid4().hex


def build_reply_path(runtime_dir: str, request_id: str) -> str:
    """The reply socket's filesystem path for *request_id* under
    *runtime_dir* (the same directory as the daemon's own control
    socket)."""
    return os.path.join(runtime_dir, f"aipager-reply-{request_id}.sock")


def open_reply_socket(runtime_dir: str) -> tuple[socket.socket, str, str] | None:
    """Bind a fresh, per-request ``SOCK_DGRAM`` unix socket under
    *runtime_dir*. Returns ``(sock, path, request_id)`` on success, or
    ``None`` on any failure (permission denied, path too long, no space,
    or any other ``OSError`` — including on a single unlink-and-retry
    attempt, in case a hard-killed previous hook left a stale file at
    this exact path, astronomically unlikely as a fresh ``uuid4()``
    collision is).

    Never raises.
    """
    request_id = new_request_id()
    path = build_reply_path(runtime_dir, request_id)
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(path)
        return sock, path, request_id
    except OSError:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        try:
            os.unlink(path)
        except OSError:
            pass
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.bind(path)
            return sock, path, request_id
        except OSError:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            return None


def close_reply_socket(sock: socket.socket, path: str) -> None:
    """Close *sock* and unlink *path*. Best-effort — never raises, safe
    to call even if either half already failed/vanished."""
    try:
        sock.close()
    except OSError:
        pass
    try:
        os.unlink(path)
    except OSError:
        pass


def encode_reply(request_id: str, decision: dict) -> bytes:
    """Serialize a daemon -> hook reply datagram."""
    return json.dumps({
        "v": PROTOCOL_VERSION,
        "request_id": request_id,
        "decision": decision,
    }).encode()


def decode_reply(raw: bytes, expected_request_id: str) -> dict | None:
    """Parse+validate a reply datagram. Returns the ``decision`` dict on
    a well-formed, matching reply, else ``None``.

    Rejects: invalid JSON, a non-dict top level, a ``request_id`` that
    doesn't match *expected_request_id*, a missing/non-dict ``decision``,
    a ``behavior`` outside ``{"allow", "deny"}``, an ``updatedPermissions``
    present but not a list, or a ``message`` present but not a string.
    Never raises.
    """
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("request_id") != expected_request_id:
        return None
    decision = obj.get("decision")
    if not isinstance(decision, dict):
        return None
    if decision.get("behavior") not in _VALID_BEHAVIORS:
        return None
    updated_permissions = decision.get("updatedPermissions")
    if updated_permissions is not None and not isinstance(updated_permissions, list):
        return None
    message = decision.get("message")
    if message is not None and not isinstance(message, str):
        return None
    return decision


def wait_for_decision(sock: socket.socket, request_id: str,
                       deadline_seconds: float, *,
                       bufsize: int = 8192) -> dict | None:
    """Block on *sock* for at most *deadline_seconds*, reading **at most
    one** datagram. Returns the decoded ``decision`` dict, or ``None`` on
    a timeout, a socket error, or a malformed/mismatched reply — this is
    a single-shot read; a second, later datagram (even a valid one) is
    never consumed by this call.

    Never raises.
    """
    try:
        sock.settimeout(deadline_seconds)
        raw, _addr = sock.recvfrom(bufsize)
    except OSError:
        return None
    return decode_reply(raw, request_id)


def allow_decision(updated_permissions: list[dict] | None = None) -> dict:
    """Build an ``allow`` decision object. ``updatedPermissions`` is
    included only when *updated_permissions* is a non-empty list —
    never synthesized, never present on a plain allow."""
    decision: dict = {"behavior": "allow"}
    if updated_permissions:
        decision["updatedPermissions"] = updated_permissions
    return decision


def deny_decision(message: str, *, interrupt: bool = False) -> dict:
    """Build a ``deny`` decision object. ``interrupt`` is ``False``
    unless explicitly overridden — a deny here means "no to this one
    tool call", never "abort the whole turn"."""
    return {"behavior": "deny", "message": message, "interrupt": interrupt}


def send_decision(hook_reply: dict | None, decision: dict) -> bool:
    """Deliver *decision* to the hook described by *hook_reply*
    (``{"addr": str, "request_id": str}``, or ``None``/incomplete).

    Returns ``True`` iff the kernel accepted the datagram (a listener
    existed at that path) — not delivery/processing confirmation.
    ``False`` on a missing/malformed *hook_reply* or any send failure.
    Never raises.
    """
    if not isinstance(hook_reply, dict):
        return False
    addr = hook_reply.get("addr")
    request_id = hook_reply.get("request_id")
    if not isinstance(addr, str) or not addr:
        return False
    if not isinstance(request_id, str) or not request_id:
        return False
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(encode_reply(request_id, decision), addr)
        return True
    except Exception:
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
