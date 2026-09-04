"""Tests for the notify_hook.py ``PermissionRequest`` reply-channel branch
(design.md "answer PermissionRequest hooks with a decision instead of
keystrokes"). Kept separate from ``test_dtach_notify_hook_reply_context.py``,
which is about the unrelated ``UserPromptSubmit`` reply-context feature.

Because ``notify_hook.main()`` blocks synchronously until it gives up or a
verdict arrives, exercising the "verdict arrives in time" branch requires
running it on a background thread (entrypoints.md's documented harness):
monkeypatch ``SOCKET_PATH`` to a real path this test itself binds, start
``main()`` in a thread, ``recvfrom()`` the forwarded datagram from the
main thread, ``sendto()`` a reply, join, and read stdout via ``capsys``.

Socket paths here use a short, flat, test-owned temp directory (never
pytest's own ``tmp_path``, whose deep nesting plus this feature's longer
``aipager-reply-<32 hex>.sock`` filename routinely exceeds AF_UNIX's
~108-byte ``sun_path`` limit — see ``tests/test_hook_reply.py``'s module
docstring for the full reasoning) and never the production socket path.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

import pytest

from aipager.dtach import notify_hook


@pytest.fixture
def short_dir():
    d = tempfile.mkdtemp(prefix="nhpr-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _set_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


_PERMISSION_REQUEST_PAYLOAD = json.dumps({
    "hook_event_name": "PermissionRequest",
    "tool_name": "Bash",
    "tool_input": {"command": "ls"},
    "permission_suggestions": [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ls"}],
         "behavior": "allow", "destination": "localSettings"},
    ],
})


def test_verdict_arrives_in_time_prints_wrapped_envelope(monkeypatch, short_dir, capsys):
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5.0)
    _set_stdin(monkeypatch, _PERMISSION_REQUEST_PAYLOAD)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-perm")

    thread = threading.Thread(target=notify_hook.main)
    try:
        thread.start()
        raw, _addr = srv.recvfrom(65536)
        forwarded = json.loads(raw.decode())
        reply_addr = forwarded["aipager_reply_addr"]
        request_id = forwarded["aipager_request_id"]
        assert forwarded["session"] == "claude-perm"
        assert len(request_id) == 32

        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        client.sendto(json.dumps({
            "v": 1, "request_id": request_id,
            "decision": {"behavior": "allow"},
        }).encode(), reply_addr)
        client.close()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
    finally:
        srv.close()

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        },
    }
    # The reply socket must be cleaned up on the success path too.
    assert not os.path.exists(reply_addr)


def test_verdict_with_updated_permissions_is_wrapped_verbatim(monkeypatch, short_dir, capsys):
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5.0)
    _set_stdin(monkeypatch, _PERMISSION_REQUEST_PAYLOAD)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-perm")

    decision = {
        "behavior": "allow",
        "updatedPermissions": [
            {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ls"}],
             "behavior": "allow", "destination": "localSettings"},
        ],
    }

    thread = threading.Thread(target=notify_hook.main)
    try:
        thread.start()
        raw, _addr = srv.recvfrom(65536)
        forwarded = json.loads(raw.decode())
        reply_addr = forwarded["aipager_reply_addr"]
        request_id = forwarded["aipager_request_id"]

        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        client.sendto(json.dumps({
            "v": 1, "request_id": request_id, "decision": decision,
        }).encode(), reply_addr)
        client.close()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
    finally:
        srv.close()

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["decision"] == decision


def test_timeout_prints_nothing_and_is_fast(monkeypatch, short_dir, capsys):
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    monkeypatch.setattr(notify_hook, "_PERMISSION_REPLY_DEADLINE_SECONDS", 0.05)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5.0)
    _set_stdin(monkeypatch, _PERMISSION_REQUEST_PAYLOAD)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-perm")

    start = time.monotonic()
    try:
        thread = threading.Thread(target=notify_hook.main)
        thread.start()
        srv.recvfrom(65536)  # drain the forward, never reply
        thread.join(timeout=5.0)
        assert not thread.is_alive()
    finally:
        srv.close()
    elapsed = time.monotonic() - start
    assert elapsed < 2.0
    assert capsys.readouterr().out == ""


def test_timeout_fires_permission_reply_timeout_notify(monkeypatch, short_dir, capsys):
    """Best-effort notify to the daemon that this specific reply channel
    is abandoned — used by hook_receiver to clear a stale ``hook_reply``."""
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    monkeypatch.setattr(notify_hook, "_PERMISSION_REPLY_DEADLINE_SECONDS", 0.05)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5.0)
    _set_stdin(monkeypatch, _PERMISSION_REQUEST_PAYLOAD)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-perm")

    try:
        thread = threading.Thread(target=notify_hook.main)
        thread.start()
        raw1, _addr = srv.recvfrom(65536)
        forwarded = json.loads(raw1.decode())
        request_id = forwarded["aipager_request_id"]
        raw2, _addr = srv.recvfrom(65536)
        thread.join(timeout=5.0)
        assert not thread.is_alive()
    finally:
        srv.close()

    timeout_msg = json.loads(raw2.decode())
    assert timeout_msg["hook_event_name"] == "permission_reply_timeout"
    assert timeout_msg["aipager_request_id"] == request_id
    assert timeout_msg["session"] == "claude-perm"
    assert capsys.readouterr().out == ""


def test_daemon_down_is_fast_and_prints_nothing(monkeypatch, short_dir, capsys):
    """Nothing bound at SOCKET_PATH at all — the forward sendto() itself
    fails, so the reply wait must be skipped entirely rather than
    blocking for the (real, non-monkeypatched) 20s deadline."""
    sock_path = os.path.join(short_dir, "nonexistent.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    _set_stdin(monkeypatch, _PERMISSION_REQUEST_PAYLOAD)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-perm")

    start = time.monotonic()
    notify_hook.main()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0
    assert capsys.readouterr().out == ""


def test_malformed_verdict_prints_nothing_and_ignores_second_valid_reply(
        monkeypatch, short_dir, capsys):
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5.0)
    _set_stdin(monkeypatch, _PERMISSION_REQUEST_PAYLOAD)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-perm")

    thread = threading.Thread(target=notify_hook.main)
    try:
        thread.start()
        raw, _addr = srv.recvfrom(65536)
        forwarded = json.loads(raw.decode())
        reply_addr = forwarded["aipager_reply_addr"]
        request_id = forwarded["aipager_request_id"]

        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        # Malformed: behavior not in {"allow", "deny"}.
        client.sendto(json.dumps({
            "v": 1, "request_id": request_id,
            "decision": {"behavior": "widen"},
        }).encode(), reply_addr)
        thread.join(timeout=5.0)
        assert not thread.is_alive()
        # A second, VALID reply sent after the hook already gave up on a
        # single-shot recvfrom — proves it is never consumed. Sending it
        # after the hook unlinked its socket may itself raise; either
        # way, no crash and nothing printed.
        try:
            client.sendto(json.dumps({
                "v": 1, "request_id": request_id,
                "decision": {"behavior": "allow"},
            }).encode(), reply_addr)
        except OSError:
            pass
        client.close()
    finally:
        srv.close()

    assert capsys.readouterr().out == ""


def test_open_reply_socket_raising_still_prints_nothing(monkeypatch, short_dir, capsys):
    """Mutation-style guard: even if the reply-socket setup machinery
    itself raises, the broad except around the whole PermissionRequest
    branch must still leave stdout empty and never crash the hook."""
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5.0)
    _set_stdin(monkeypatch, _PERMISSION_REQUEST_PAYLOAD)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-perm")

    from aipager.dtach import hook_reply

    def _boom(_runtime_dir):
        raise RuntimeError("boom")

    monkeypatch.setattr(hook_reply, "open_reply_socket", _boom)
    try:
        notify_hook.main()  # must not raise
    finally:
        srv.close()
    assert capsys.readouterr().out == ""


def test_reply_socket_setup_failure_still_forwards_without_reply_keys(
        monkeypatch, short_dir, capsys):
    """When the reply socket can't be bound, the event still forwards
    exactly like a pre-this-ship hook — no reply keys, no wait."""
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5.0)
    _set_stdin(monkeypatch, _PERMISSION_REQUEST_PAYLOAD)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-perm")

    from aipager.dtach import hook_reply
    monkeypatch.setattr(hook_reply, "open_reply_socket", lambda _rd: None)

    start = time.monotonic()
    try:
        notify_hook.main()
        raw, _addr = srv.recvfrom(65536)
    finally:
        srv.close()
    elapsed = time.monotonic() - start

    forwarded = json.loads(raw.decode())
    assert "aipager_reply_addr" not in forwarded
    assert "aipager_request_id" not in forwarded
    assert elapsed < 1.0  # no wait attempted at all
    assert capsys.readouterr().out == ""
