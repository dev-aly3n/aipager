"""Black-box tests for aipager.dtach.hook_reply's documented daemon-side
functions per entrypoints.md: allow_decision, deny_decision, send_decision.

These are treated strictly as a wire-format API: this file never imports
the module's internal building blocks (open_reply_socket, wait_for_decision,
encode_reply, decode_reply, build_reply_path, new_request_id) — per
entrypoints.md's "NOT exported" list, a black-box test constructs/parses
the documented JSON shapes itself using plain stdlib sockets.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile

import pytest

from aipager.dtach import hook_reply


@pytest.fixture
def short_dir():
    d = tempfile.mkdtemp(prefix="phd-hr-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---- allow_decision / deny_decision: pure builders ------------------------

def test_allow_decision_plain_has_no_updated_permissions_key():
    d = hook_reply.allow_decision()
    assert d == {"behavior": "allow"}


def test_allow_decision_explicit_none_has_no_updated_permissions_key():
    d = hook_reply.allow_decision(None)
    assert "updatedPermissions" not in d


def test_allow_decision_empty_list_has_no_updated_permissions_key():
    """Boundary: an empty list is a 'nothing to offer' case, must never
    surface as updatedPermissions: [] on the wire."""
    d = hook_reply.allow_decision([])
    assert "updatedPermissions" not in d


def test_allow_decision_single_entry_included_verbatim():
    suggestion = {"type": "addRules",
                  "rules": [{"toolName": "Bash", "ruleContent": "ls"}],
                  "behavior": "allow", "destination": "localSettings"}
    d = hook_reply.allow_decision([suggestion])
    assert d == {"behavior": "allow", "updatedPermissions": [suggestion]}


def test_allow_decision_multiple_entries_all_included_verbatim():
    s1 = {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ls"}],
          "behavior": "allow", "destination": "localSettings"}
    s2 = {"type": "addDirectories", "directories": ["/tmp/x"],
          "behavior": "allow", "destination": "localSettings"}
    d = hook_reply.allow_decision([s1, s2])
    assert d["updatedPermissions"] == [s1, s2]


def test_deny_decision_default_interrupt_false():
    d = hook_reply.deny_decision("Denied via aipager")
    assert d == {"behavior": "deny", "message": "Denied via aipager", "interrupt": False}


def test_deny_decision_explicit_interrupt_true():
    d = hook_reply.deny_decision("stop now", interrupt=True)
    assert d["interrupt"] is True
    assert d["behavior"] == "deny"
    assert d["message"] == "stop now"


# ---- send_decision: the actual delivery call ------------------------------

def test_send_decision_returns_false_for_none_hook_reply():
    assert hook_reply.send_decision(None, hook_reply.allow_decision()) is False


@pytest.mark.parametrize("bad", [
    {},
    {"addr": "/tmp/x.sock"},              # missing request_id
    {"request_id": "abc"},                # missing addr
    {"addr": "", "request_id": "abc"},    # empty addr
])
def test_send_decision_returns_false_for_incomplete_hook_reply(bad):
    assert hook_reply.send_decision(bad, hook_reply.allow_decision()) is False


@pytest.mark.parametrize("garbage", ["not-a-dict", 123, [], True])
def test_send_decision_never_raises_for_non_dict_hook_reply(garbage):
    """Error-guessing: garbage types must degrade to False, never raise
    — entrypoints.md states send_decision 'Never raises.'"""
    assert hook_reply.send_decision(garbage, hook_reply.allow_decision()) is False


def test_send_decision_delivers_documented_allow_wire_format(short_dir):
    sock_path = os.path.join(short_dir, "aipager-reply-a.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(sock_path)
    listener.settimeout(5.0)
    try:
        request_id = "deadbeef" * 4
        ok = hook_reply.send_decision(
            {"addr": sock_path, "request_id": request_id},
            hook_reply.allow_decision(),
        )
        assert ok is True
        raw, _addr = listener.recvfrom(65536)
        wire = json.loads(raw.decode())
        assert wire == {"v": 1, "request_id": request_id,
                        "decision": {"behavior": "allow"}}
    finally:
        listener.close()


def test_send_decision_delivers_documented_allow_always_wire_format(short_dir):
    sock_path = os.path.join(short_dir, "aipager-reply-b.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(sock_path)
    listener.settimeout(5.0)
    suggestion = {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ls"}],
                 "behavior": "allow", "destination": "localSettings"}
    try:
        request_id = "beefdead" * 4
        ok = hook_reply.send_decision(
            {"addr": sock_path, "request_id": request_id},
            hook_reply.allow_decision(updated_permissions=[suggestion]),
        )
        assert ok is True
        raw, _addr = listener.recvfrom(65536)
        wire = json.loads(raw.decode())
        assert wire == {"v": 1, "request_id": request_id,
                        "decision": {"behavior": "allow",
                                     "updatedPermissions": [suggestion]}}
    finally:
        listener.close()


def test_send_decision_delivers_documented_deny_wire_format(short_dir):
    sock_path = os.path.join(short_dir, "aipager-reply-c.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(sock_path)
    listener.settimeout(5.0)
    try:
        request_id = "cafebabe" * 4
        ok = hook_reply.send_decision(
            {"addr": sock_path, "request_id": request_id},
            hook_reply.deny_decision("some reason"),
        )
        assert ok is True
        raw, _addr = listener.recvfrom(65536)
        wire = json.loads(raw.decode())
        assert wire == {"v": 1, "request_id": request_id,
                        "decision": {"behavior": "deny", "message": "some reason",
                                     "interrupt": False}}
    finally:
        listener.close()


def test_send_decision_returns_false_when_no_listener_at_addr(short_dir):
    """ENOENT case: the path was never bound (or the hook already
    unlinked it) -- must degrade to False, not raise."""
    sock_path = os.path.join(short_dir, "nobody-home.sock")
    ok = hook_reply.send_decision(
        {"addr": sock_path, "request_id": "abc123"},
        hook_reply.allow_decision(),
    )
    assert ok is False


def test_send_decision_returns_false_after_listener_closes(short_dir):
    """A listener that existed and then went away (closed + unlinked,
    mirroring a hook that gave up) must also degrade to False."""
    sock_path = os.path.join(short_dir, "aipager-reply-d.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(sock_path)
    listener.close()
    os.unlink(sock_path)
    ok = hook_reply.send_decision(
        {"addr": sock_path, "request_id": "abc123"},
        hook_reply.deny_decision("too late"),
    )
    assert ok is False
