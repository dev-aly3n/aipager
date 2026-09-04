"""Unit tests for aipager.dtach.hook_reply — the daemon<->hook reply
transport (design.md "answer PermissionRequest hooks with a decision
instead of keystrokes").

All sockets here are bound under a freshly-created, test-owned temp
directory — never the production ``SOCKET_PATH``/``XDG_RUNTIME_DIR``,
satisfying the "no real socket under /tmp" constraint (that's about the
*shared production* socket, not a test's own isolated temp dir).

Deliberately NOT pytest's own ``tmp_path`` fixture: pytest nests it under
a per-test directory name that, combined with this module's
``aipager-reply-<32 hex chars>.sock`` filename (51 chars), routinely
pushes the full path past AF_UNIX's ~108-byte ``sun_path`` limit — the
exact hazard design.md's "no session name in the socket filename"
reasoning exists to avoid, just triggered here by pytest's own directory
naming instead of a long session label. ``tempfile.mkdtemp()`` gives a
short, flat directory under the system temp dir that mirrors production
(``/run/user/<uid>`` or ``/tmp`` directly, never deeply nested) far more
faithfully than pytest's own tree, and is still fully test-isolated:
freshly created per test, removed after.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import time

import pytest

from aipager.dtach import hook_reply


@pytest.fixture
def short_dir():
    d = tempfile.mkdtemp(prefix="hr-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---- open_reply_socket / close_reply_socket ---------------------------

def test_open_close_round_trip(short_dir):
    opened = hook_reply.open_reply_socket(short_dir)
    assert opened is not None
    sock, path, request_id = opened
    assert os.path.exists(path)
    assert path == hook_reply.build_reply_path(short_dir, request_id)
    assert isinstance(request_id, str) and len(request_id) == 32
    hook_reply.close_reply_socket(sock, path)
    assert not os.path.exists(path)


def test_open_reply_socket_returns_none_on_bad_runtime_dir():
    # A path component that cannot possibly exist as a directory.
    opened = hook_reply.open_reply_socket("/nonexistent/deeply/nested/dir")
    assert opened is None


def test_close_reply_socket_is_safe_to_call_twice(short_dir):
    """Never raises, even if the file/socket is already gone."""
    opened = hook_reply.open_reply_socket(short_dir)
    assert opened is not None
    sock, path, _rid = opened
    hook_reply.close_reply_socket(sock, path)
    hook_reply.close_reply_socket(sock, path)  # must not raise


def test_two_requests_never_collide_on_path(short_dir):
    a = hook_reply.open_reply_socket(short_dir)
    b = hook_reply.open_reply_socket(short_dir)
    assert a is not None and b is not None
    assert a[1] != b[1]
    assert a[2] != b[2]
    hook_reply.close_reply_socket(a[0], a[1])
    hook_reply.close_reply_socket(b[0], b[1])


# ---- wait_for_decision --------------------------------------------------

def test_wait_for_decision_timeout_returns_none_fast(short_dir):
    opened = hook_reply.open_reply_socket(short_dir)
    assert opened is not None
    sock, path, request_id = opened
    try:
        start = time.monotonic()
        result = hook_reply.wait_for_decision(sock, request_id, 0.01)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 1.0
    finally:
        hook_reply.close_reply_socket(sock, path)


def test_wait_for_decision_success_path(short_dir):
    opened = hook_reply.open_reply_socket(short_dir)
    assert opened is not None
    sock, path, request_id = opened
    sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        decision = {"behavior": "allow"}
        sender.sendto(hook_reply.encode_reply(request_id, decision), path)
        result = hook_reply.wait_for_decision(sock, request_id, 5.0)
        assert result == decision
    finally:
        sender.close()
        hook_reply.close_reply_socket(sock, path)


def test_wait_for_decision_reads_at_most_one_datagram(short_dir):
    """A second, later, valid datagram must never be consumed by a call
    that already returned (single-shot recvfrom)."""
    opened = hook_reply.open_reply_socket(short_dir)
    assert opened is not None
    sock, path, request_id = opened
    sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sender.sendto(hook_reply.encode_reply(request_id, {"behavior": "deny", "message": "x"}), path)
        sender.sendto(hook_reply.encode_reply(request_id, {"behavior": "allow"}), path)
        first = hook_reply.wait_for_decision(sock, request_id, 5.0)
        assert first == {"behavior": "deny", "message": "x"}
        # Second datagram is still sitting in the kernel queue, unread —
        # a fresh, independent recv would see it; THIS test only proves
        # the first call didn't loop internally to drain both.
        sock.settimeout(0.5)
        raw, _addr = sock.recvfrom(8192)
        second = hook_reply.decode_reply(raw, request_id)
        assert second == {"behavior": "allow"}
    finally:
        sender.close()
        hook_reply.close_reply_socket(sock, path)


# ---- decode_reply malformed-input matrix --------------------------------

@pytest.mark.parametrize("raw,request_id", [
    (b"not json", "abc"),
    (b"[1, 2, 3]", "abc"),  # non-dict top level
    (json.dumps({"v": 1, "request_id": "other", "decision": {"behavior": "allow"}}).encode(), "abc"),
    (json.dumps({"v": 1, "request_id": "abc", "decision": {"behavior": "widen"}}).encode(), "abc"),
    (json.dumps({"v": 1, "request_id": "abc", "decision": "allow"}).encode(), "abc"),
    (json.dumps({"v": 1, "request_id": "abc"}).encode(), "abc"),
    (json.dumps({"v": 1, "request_id": "abc",
                 "decision": {"behavior": "allow", "updatedPermissions": "nope"}}).encode(), "abc"),
    (json.dumps({"v": 1, "request_id": "abc",
                 "decision": {"behavior": "deny", "message": 123}}).encode(), "abc"),
])
def test_decode_reply_malformed_matrix_returns_none(raw, request_id):
    assert hook_reply.decode_reply(raw, request_id) is None


def test_decode_reply_valid_allow():
    raw = hook_reply.encode_reply("abc", {"behavior": "allow"})
    assert hook_reply.decode_reply(raw, "abc") == {"behavior": "allow"}


def test_decode_reply_valid_deny():
    decision = {"behavior": "deny", "message": "no", "interrupt": False}
    raw = hook_reply.encode_reply("abc", decision)
    assert hook_reply.decode_reply(raw, "abc") == decision


# ---- allow_decision / deny_decision --------------------------------------

def test_allow_decision_plain_has_no_updated_permissions_key():
    d = hook_reply.allow_decision()
    assert d == {"behavior": "allow"}
    assert "updatedPermissions" not in d


def test_allow_decision_none_or_empty_never_include_the_key():
    assert "updatedPermissions" not in hook_reply.allow_decision(None)
    assert "updatedPermissions" not in hook_reply.allow_decision([])


def test_allow_decision_with_suggestion_includes_it_verbatim():
    suggestion = {"type": "addRules", "rules": [{"toolName": "Bash"}],
                  "behavior": "allow", "destination": "localSettings"}
    d = hook_reply.allow_decision(updated_permissions=[suggestion])
    assert d == {"behavior": "allow", "updatedPermissions": [suggestion]}


def test_deny_decision_defaults_interrupt_false():
    d = hook_reply.deny_decision("nope")
    assert d == {"behavior": "deny", "message": "nope", "interrupt": False}


def test_deny_decision_interrupt_can_be_overridden():
    d = hook_reply.deny_decision("nope", interrupt=True)
    assert d["interrupt"] is True


# ---- send_decision --------------------------------------------------------

def test_send_decision_delivers_to_a_real_listener(short_dir):
    opened = hook_reply.open_reply_socket(short_dir)
    assert opened is not None
    sock, path, request_id = opened
    try:
        ok = hook_reply.send_decision(
            {"addr": path, "request_id": request_id}, {"behavior": "allow"})
        assert ok is True
        sock.settimeout(2.0)
        raw, _addr = sock.recvfrom(8192)
        assert hook_reply.decode_reply(raw, request_id) == {"behavior": "allow"}
    finally:
        hook_reply.close_reply_socket(sock, path)


def test_send_decision_false_on_missing_hook_reply():
    assert hook_reply.send_decision(None, {"behavior": "allow"}) is False


@pytest.mark.parametrize("bad", [
    {},
    {"addr": ""},
    {"addr": "/x", "request_id": ""},
    {"addr": 5, "request_id": "abc"},
    {"addr": "/x", "request_id": 5},
])
def test_send_decision_false_on_malformed_hook_reply(bad):
    assert hook_reply.send_decision(bad, {"behavior": "allow"}) is False


def test_send_decision_false_when_socket_gone(short_dir):
    """ENOENT (hook already unlinked its socket) is a normal, expected
    failure mode, not an exception."""
    path = os.path.join(short_dir, "gone.sock")
    assert hook_reply.send_decision(
        {"addr": path, "request_id": "abc"}, {"behavior": "allow"}) is False


# ---- new_request_id / build_reply_path -----------------------------------

def test_new_request_id_is_32_hex_chars_and_unique():
    a = hook_reply.new_request_id()
    b = hook_reply.new_request_id()
    assert len(a) == 32 and len(b) == 32
    assert a != b
    int(a, 16)  # must be valid hex


def test_build_reply_path_shape(short_dir):
    path = hook_reply.build_reply_path(short_dir, "deadbeef")
    assert path == os.path.join(short_dir, "aipager-reply-deadbeef.sock")
