"""Black-box tests for aipager.dtach.notify_hook.main()'s PermissionRequest
stdin -> stdout contract, driven purely through entrypoints.md's documented
surface: stdin, the two documented UDP side channels, and env vars.

Uses entrypoints.md's stated harness: bind our own SOCK_DGRAM unix socket,
monkeypatch SOCKET_PATH to it, run main() on a background thread, recvfrom()
the forwarded datagram to learn aipager_reply_addr/aipager_request_id,
sendto() a reply, join, and read stdout via capsys. Sockets live under a
short, flat, test-owned temp dir (never pytest's own tmp_path, whose deep
nesting can push an AF_UNIX path past the ~108-byte sun_path limit once
combined with this feature's own "aipager-reply-<32 hex>.sock" filename —
never the production socket path either way).
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
    d = tempfile.mkdtemp(prefix="phd-nh-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _set_stdin(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))


def _permission_request_payload(**overrides):
    payload = {
        "session_id": "abc123",
        "transcript_path": "/path/to/transcript.jsonl",
        "cwd": "/work/project",
        "permission_mode": "default",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
        "permission_suggestions": [
            {"type": "addRules",
             "rules": [{"toolName": "Bash", "ruleContent": "ls -la"}],
             "behavior": "allow", "destination": "localSettings"},
        ],
    }
    payload.update(overrides)
    return payload


def _run_and_capture(monkeypatch, short_dir, capsys, session_name, payload,
                     reply_fn, *, recv_timeout=5.0, join_timeout=10.0):
    """Bind our own control socket, run main() on a thread, hand
    reply_fn the forwarded reply addr/request_id (reply_fn is
    responsible for sending 0+ datagrams to it), join, and return
    (stdout, elapsed_seconds, forwarded_dict_or_None)."""
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(recv_timeout)
    _set_stdin(monkeypatch, payload)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", session_name)

    thread = threading.Thread(target=notify_hook.main)
    start = time.monotonic()
    forwarded = None
    try:
        thread.start()
        raw, _addr = srv.recvfrom(65536)
        forwarded = json.loads(raw.decode())
        reply_fn(forwarded.get("aipager_reply_addr"),
                 forwarded.get("aipager_request_id"))
    finally:
        thread.join(timeout=join_timeout)
        srv.close()
    elapsed = time.monotonic() - start
    out = capsys.readouterr().out
    return out, elapsed, forwarded


def _send_reply(addr, payload_dict):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        client.sendto(json.dumps(payload_dict).encode(), addr)
    finally:
        client.close()


# ---- verdict arrives in time -------------------------------------------

def test_plain_allow_prints_wrapped_envelope_verbatim(monkeypatch, short_dir, capsys):
    def reply(addr, request_id):
        assert addr and request_id
        _send_reply(addr, {"v": 1, "request_id": request_id,
                           "decision": {"behavior": "allow"}})

    out, _elapsed, forwarded = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t1",
        _permission_request_payload(), reply)

    assert forwarded["aipager_reply_addr"]
    assert len(forwarded["aipager_request_id"]) == 32
    payload = json.loads(out.strip())
    assert payload == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def test_allow_with_updated_permissions_echoed_verbatim(monkeypatch, short_dir, capsys):
    suggestion = {"type": "addRules",
                  "rules": [{"toolName": "Bash", "ruleContent": "ls -la"}],
                  "behavior": "allow", "destination": "localSettings"}

    def reply(addr, request_id):
        _send_reply(addr, {"v": 1, "request_id": request_id,
                           "decision": {"behavior": "allow",
                                        "updatedPermissions": [suggestion]}})

    out, _elapsed, _fwd = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t2",
        _permission_request_payload(), reply)

    payload = json.loads(out.strip())
    decision = payload["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "allow"
    assert decision["updatedPermissions"] == [suggestion]


def test_allow_with_empty_updated_permissions_list_round_trips(monkeypatch, short_dir, capsys):
    """Boundary: an explicitly-empty updatedPermissions list on the wire
    is passed through unaltered — the hook wraps verbatim, it does not
    normalize/drop the key itself (that policy lives daemon-side)."""
    def reply(addr, request_id):
        _send_reply(addr, {"v": 1, "request_id": request_id,
                           "decision": {"behavior": "allow",
                                        "updatedPermissions": []}})

    out, _elapsed, _fwd = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t2b",
        _permission_request_payload(), reply)

    payload = json.loads(out.strip())
    assert payload["hookSpecificOutput"]["decision"]["updatedPermissions"] == []


def test_deny_with_message_prints_wrapped_deny(monkeypatch, short_dir, capsys):
    def reply(addr, request_id):
        _send_reply(addr, {"v": 1, "request_id": request_id,
                           "decision": {"behavior": "deny",
                                        "message": "Denied via aipager",
                                        "interrupt": False}})

    out, _elapsed, _fwd = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t3",
        _permission_request_payload(), reply)

    payload = json.loads(out.strip())
    decision = payload["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny"
    assert decision["message"] == "Denied via aipager"
    assert decision["interrupt"] is False


# ---- timeout -------------------------------------------------------------

def test_timeout_with_no_reply_prints_nothing_and_completes_fast(monkeypatch, short_dir, capsys):
    monkeypatch.setenv("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", "0.05")

    def reply(_addr, _request_id):
        pass  # never reply

    out, elapsed, forwarded = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t4",
        _permission_request_payload(), reply, recv_timeout=5.0)

    assert forwarded is not None  # the forward datagram itself must still arrive
    assert out == ""
    assert elapsed < 2.0, f"deadline override was not honored, took {elapsed}s"


# ---- daemon down -----------------------------------------------------------

def test_daemon_down_prints_nothing_near_instantly(monkeypatch, short_dir, capsys):
    """SOCKET_PATH points at a path nothing is listening on. This must
    resolve near-instantly, not after the (much longer) internal
    deadline — proving the daemon-down fast path, not merely a short
    configured one."""
    nowhere = os.path.join(short_dir, "nobody-home.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", nowhere)
    monkeypatch.delenv("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", raising=False)
    _set_stdin(monkeypatch, _permission_request_payload())
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-t5")

    start = time.monotonic()
    notify_hook.main()
    elapsed = time.monotonic() - start

    assert capsys.readouterr().out == ""
    assert elapsed < 2.0, f"daemon-down fast path did not fire, took {elapsed}s"


# ---- malformed verdict / single-shot recvfrom -----------------------------

@pytest.mark.parametrize("bad_decision", [
    {"behavior": "widen"},                       # invalid behavior
    {"behavior": "allow", "updatedPermissions": "not-a-list"},
    {"behavior": "deny", "message": 12345},       # message not a string
])
def test_malformed_verdict_prints_nothing(monkeypatch, short_dir, capsys, bad_decision):
    monkeypatch.setenv("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", "2")

    def reply(addr, request_id):
        _send_reply(addr, {"v": 1, "request_id": request_id, "decision": bad_decision})

    out, _elapsed, _fwd = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t6",
        _permission_request_payload(), reply)

    assert out == ""


def test_malformed_verdict_never_reads_a_second_valid_reply(monkeypatch, short_dir, capsys):
    """A well-formed reply sent AFTER a bad one must never be read — the
    hook performs at most one recvfrom() per request."""
    monkeypatch.setenv("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", "1.5")

    def reply(addr, request_id):
        _send_reply(addr, {"v": 1, "request_id": request_id,
                           "decision": {"behavior": "widen"}})
        time.sleep(0.3)
        # If the hook were still listening, this would flip the outcome
        # to a printed "allow" envelope.
        try:
            _send_reply(addr, {"v": 1, "request_id": request_id,
                               "decision": {"behavior": "allow"}})
        except OSError:
            pass  # already unlinked -- also proves single-shot

    out, _elapsed, _fwd = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t7",
        _permission_request_payload(), reply, join_timeout=10.0)

    assert out == ""


def test_wrong_request_id_reply_is_treated_as_malformed(monkeypatch, short_dir, capsys):
    monkeypatch.setenv("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", "2")

    def reply(addr, _request_id):
        _send_reply(addr, {"v": 1, "request_id": "not-the-real-id",
                           "decision": {"behavior": "allow"}})

    out, _elapsed, _fwd = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t8",
        _permission_request_payload(), reply)

    assert out == ""


def test_invalid_json_reply_is_treated_as_malformed(monkeypatch, short_dir, capsys):
    monkeypatch.setenv("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", "2")

    def reply(addr, _request_id):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            client.sendto(b"not json at all", addr)
        finally:
            client.close()

    out, _elapsed, _fwd = _run_and_capture(
        monkeypatch, short_dir, capsys, "claude-t9",
        _permission_request_payload(), reply)

    assert out == ""


# ---- non-PermissionRequest events are unaffected --------------------------

def test_pre_tool_use_event_never_enriches_or_waits(monkeypatch, short_dir, capsys):
    """Sanity/equivalence check: only hook_event_name == 'PermissionRequest'
    gets the reply-socket treatment. A PreToolUse for a tool with no
    printable hook output must still print nothing and return immediately
    (no wait attempted at all)."""
    sock_path = os.path.join(short_dir, "aipager.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(5.0)
    _set_stdin(monkeypatch, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                            "tool_input": {"command": "ls"}})
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-t10")

    start = time.monotonic()
    notify_hook.main()
    elapsed = time.monotonic() - start
    srv.close()

    assert capsys.readouterr().out == ""
    assert elapsed < 2.0


# ---- concurrency: distinct request_ids never collide -----------------------

def _spawn_hook_subprocess(env_extra, payload):
    import subprocess

    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from aipager.dtach import notify_hook; notify_hook.main()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, text=True,
    )
    proc.stdin.write(json.dumps(payload))
    proc.stdin.close()
    # communicate() below tries to flush/write to proc.stdin itself if it
    # is still a live pipe object -- since we already closed it, that
    # raises ValueError("I/O operation on closed file"). Clearing the
    # reference (subprocess.Popen tolerates stdin=None on communicate())
    # tells it there is nothing more to write.
    proc.stdin = None
    return proc


def test_two_concurrent_permission_requests_do_not_collide(short_dir):
    """Two sessions prompting at once against the same daemon socket:
    each hook process must only ever honor ITS OWN request_id, even when
    replies are sent out of order. Genuine separate processes (not
    threads) so each has its own real stdin/stdout, driven purely
    through the documented AIPAGER_SOCKET_PATH / CLAUDE_DTACH_SESSION
    env-var contract."""
    sock_path = os.path.join(short_dir, "aipager.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(10.0)

    payload_a = _permission_request_payload(tool_input={"command": "echo a"})
    payload_b = _permission_request_payload(tool_input={"command": "echo b"})

    proc_a = _spawn_hook_subprocess(
        {"AIPAGER_SOCKET_PATH": sock_path, "CLAUDE_DTACH_SESSION": "claude-a",
         "AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS": "10"}, payload_a)
    proc_b = _spawn_hook_subprocess(
        {"AIPAGER_SOCKET_PATH": sock_path, "CLAUDE_DTACH_SESSION": "claude-b",
         "AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS": "10"}, payload_b)

    try:
        forwarded = {}
        for _ in range(2):
            raw, _addr = srv.recvfrom(65536)
            fwd = json.loads(raw.decode())
            forwarded[fwd["session"]] = fwd

        assert (forwarded["claude-a"]["aipager_request_id"]
                != forwarded["claude-b"]["aipager_request_id"])

        # Reply to B first (deny) then A (allow) -- order must not matter,
        # and each process must only ever act on its own id/address.
        _send_reply(forwarded["claude-b"]["aipager_reply_addr"],
                   {"v": 1, "request_id": forwarded["claude-b"]["aipager_request_id"],
                    "decision": {"behavior": "deny", "message": "no", "interrupt": False}})
        _send_reply(forwarded["claude-a"]["aipager_reply_addr"],
                   {"v": 1, "request_id": forwarded["claude-a"]["aipager_request_id"],
                    "decision": {"behavior": "allow"}})

        out_a, _err_a = proc_a.communicate(timeout=15)
        out_b, _err_b = proc_b.communicate(timeout=15)
    finally:
        srv.close()
        for p in (proc_a, proc_b):
            if p.poll() is None:
                p.kill()

    payload_out_a = json.loads(out_a.strip())
    payload_out_b = json.loads(out_b.strip())
    assert payload_out_a["hookSpecificOutput"]["decision"] == {"behavior": "allow"}
    assert payload_out_b["hookSpecificOutput"]["decision"]["behavior"] == "deny"
