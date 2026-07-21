"""Tests for the two console-script hook stubs: ``aipager-hook`` and
``aipager-statusline`` (``aipager.dtach.notify_hook`` /
``aipager.dtach.statusline_notify``).

These run as subprocesses spawned by Claude Code on every event. They
must:
- Never crash (any failure swallowed; Claude Code's UI must keep working).
- Be cheap (no HTTP, ~5ms).
- Be no-ops when stdin is empty or malformed.
- Forward enriched payloads to the daemon's UDP socket when reachable.
"""

from __future__ import annotations

import io
import json
import resource
import socket
import sys

import pytest

from aipager.dtach import notify_hook, statusline_notify


# ---- notify_hook ---------------------------------------------------------

def _set_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_notify_hook_empty_stdin_exits_cleanly(monkeypatch):
    _set_stdin(monkeypatch, "")
    with pytest.raises(SystemExit) as exc:
        notify_hook.main()
    assert exc.value.code == 0


def test_notify_hook_whitespace_stdin_exits_cleanly(monkeypatch):
    _set_stdin(monkeypatch, "   \n   ")
    with pytest.raises(SystemExit) as exc:
        notify_hook.main()
    assert exc.value.code == 0


def test_notify_hook_malformed_json_exits_cleanly(monkeypatch):
    _set_stdin(monkeypatch, "{not json")
    with pytest.raises(SystemExit) as exc:
        notify_hook.main()
    assert exc.value.code == 0


def test_notify_hook_forwards_payload_to_daemon(monkeypatch, tmp_path):
    sock_path = tmp_path / "aipager.sock"
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock_path))

    # Listen on the socket so the hook's sendto is observable
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(str(sock_path))
    srv.settimeout(1.0)
    try:
        _set_stdin(monkeypatch, '{"hook_event_name":"PreToolUse","tool_name":"Bash"}')
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-test")
        notify_hook.main()
        data, _ = srv.recvfrom(4096)
        payload = json.loads(data.decode())
        assert payload["hook_event_name"] == "PreToolUse"
        assert payload["session"] == "claude-test"
    finally:
        srv.close()


def test_notify_hook_piggybacks_statusline_tokens(monkeypatch, tmp_path):
    sock_path = tmp_path / "aipager.sock"
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock_path))

    # Write a statusLine JSON file the hook will read
    status = {
        "context_window": {
            "used_percentage": 42,
            "total_output_tokens": 1234,
            "total_input_tokens": 4321,
            "current_usage": {"output_tokens": 100},
        },
        "cost": {"total_lines_added": 5, "total_lines_removed": 2},
    }
    (tmp_path / "claude-status-claude-jim.json").write_text(json.dumps(status))
    # Redirect statusLine file lookup. Capture the real read_text *before*
    # patching so the replacement doesn't recurse.
    _real_read_text = notify_hook.Path.read_text
    monkeypatch.setattr(
        notify_hook.Path, "read_text",
        lambda self: _real_read_text(tmp_path / self.name),
    )

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(str(sock_path))
    srv.settimeout(1.0)
    try:
        _set_stdin(monkeypatch, '{"hook_event_name":"PreToolUse"}')
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-jim")
        notify_hook.main()
        data, _ = srv.recvfrom(4096)
        payload = json.loads(data.decode())
        assert payload["sl_tokens"]["context_pct"] == 42
        assert payload["sl_tokens"]["total_output"] == 1234
        assert payload["sl_tokens"]["lines_added"] == 5
    finally:
        srv.close()


def test_notify_hook_swallows_unreachable_daemon(monkeypatch, tmp_path):
    # SOCKET_PATH doesn't exist → sendto raises FileNotFoundError → hook
    # must NOT propagate the exception.
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _set_stdin(monkeypatch, '{"hook_event_name":"PreToolUse"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-x")
    notify_hook.main()  # must not raise


def test_notify_hook_debug_prints(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    monkeypatch.setattr(notify_hook, "_DEBUG", True)
    _set_stdin(monkeypatch, '{"hook_event_name":"X"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-x")
    notify_hook.main()
    err = capsys.readouterr().err
    assert "aipager-hook" in err
    assert "unreachable" in err


# ---- statusline_notify ---------------------------------------------------

def test_statusline_empty_stdin_writes_empty_stdout(monkeypatch, capsys):
    _set_stdin(monkeypatch, "")
    monkeypatch.delenv("CLAUDE_DTACH_SESSION", raising=False)
    statusline_notify.main()
    out = capsys.readouterr().out
    # No input → falls through to the formatted-line branch with empty data
    assert out == ""


def test_statusline_writes_status_file_and_emits_line(monkeypatch, tmp_path, capsys):
    sock_path = tmp_path / "aipager.sock"
    status_file = tmp_path / "claude-status-claude-jim.json"
    monkeypatch.setattr(statusline_notify, "SOCKET_PATH", str(sock_path))

    # Redirect Path so the hook writes into tmp_path. Capture the real
    # write_text *before* patching so the replacement doesn't recurse.
    _real_write = statusline_notify.Path.write_text

    def _write_to_tmp(self, content):
        return _real_write(tmp_path / self.name, content)

    monkeypatch.setattr(statusline_notify.Path, "write_text", _write_to_tmp)

    payload = json.dumps({
        "model": {"display_name": "Opus 4.7"},
        "context_window": {
            "used_percentage": 25,
            "total_output_tokens": 500,
            "total_input_tokens": 800,
            "current_usage": {"output_tokens": 50},
        },
        "cost": {
            "total_cost_usd": 0.12,
            "total_lines_added": 3,
            "total_lines_removed": 1,
        },
    })

    # Bind so we can verify the UDP forward
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(str(sock_path))
    srv.settimeout(1.0)
    try:
        _set_stdin(monkeypatch, payload)
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-jim")
        statusline_notify.main()
        out = capsys.readouterr().out
        assert "[jim]" in out
        assert "Opus 4.7" in out
        assert "25%" in out
        assert "$0.12" in out
        # Status file written
        assert status_file.exists()
        # UDP datagram forwarded
        data, _ = srv.recvfrom(4096)
        msg = json.loads(data.decode())
        assert msg["type"] == "statusline"
        assert msg["session"] == "claude-jim"
        assert msg["context_pct"] == 25
        assert msg["model_name"] == "Opus 4.7"
    finally:
        srv.close()


def test_statusline_swallows_unreachable_daemon(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(statusline_notify, "SOCKET_PATH",
                        str(tmp_path / "nowhere.sock"))
    # Redirect Path write to a tmp location. Capture the real write_text
    # before patching so the replacement doesn't recurse.
    _real_write = statusline_notify.Path.write_text
    monkeypatch.setattr(
        statusline_notify.Path, "write_text",
        lambda self, c: _real_write(tmp_path / self.name, c),
    )
    _set_stdin(monkeypatch, '{"model":{"display_name":"M"},"context_window":{"used_percentage":5},"cost":{"total_cost_usd":0.01}}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-zz")
    statusline_notify.main()  # must not raise
    out = capsys.readouterr().out
    assert "[zz]" in out


def test_statusline_malformed_json_emits_empty(monkeypatch, capsys):
    _set_stdin(monkeypatch, "not json")
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-x")
    statusline_notify.main()
    assert capsys.readouterr().out == ""


def test_statusline_no_session_env_still_works(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_DTACH_SESSION", raising=False)
    _set_stdin(monkeypatch, '{"model":{"display_name":"M"},"context_window":{"used_percentage":1},"cost":{"total_cost_usd":0}}')
    statusline_notify.main()
    out = capsys.readouterr().out
    # No session → no [label] prefix
    assert not out.startswith("[")
    assert "M" in out


def test_statusline_debug_logs_write_failure(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(statusline_notify, "_DEBUG", True)
    monkeypatch.setattr(statusline_notify, "SOCKET_PATH",
                        str(tmp_path / "nope.sock"))

    def _boom(self, content):
        raise OSError("read-only fs")

    monkeypatch.setattr(statusline_notify.Path, "write_text", _boom)
    _set_stdin(monkeypatch, '{"model":{"display_name":"M"},"context_window":{},"cost":{}}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-r")
    statusline_notify.main()
    err = capsys.readouterr().err
    assert "could not write" in err


# ---- RLIMIT_AS memory cap (defense-in-depth against future leaks) --------
#
# We capture the setrlimit call arguments rather than actually clamping this
# test process's own address space (which would break subsequent tests). The
# hook subprocesses call setrlimit at the top of main() before any real I/O,
# so intercepting the call is enough to verify the contract.

def _capture_setrlimit(monkeypatch, module):
    calls = []
    def fake(res, limits):
        calls.append((res, limits))
    monkeypatch.setattr(module.resource, "setrlimit", fake)
    return calls


def test_notify_hook_sets_memory_rlimit(monkeypatch):
    calls = _capture_setrlimit(monkeypatch, notify_hook)
    _set_stdin(monkeypatch, "")
    with pytest.raises(SystemExit):
        notify_hook.main()
    assert calls, "notify_hook.main() must call resource.setrlimit"
    res, (soft, hard) = calls[0]
    assert res == resource.RLIMIT_AS
    assert soft == notify_hook._MEMORY_CAP_BYTES
    assert hard == notify_hook._MEMORY_CAP_BYTES


def test_notify_hook_survives_rlimit_rejection(monkeypatch, tmp_path):
    # Some kernels/containers refuse to tighten RLIMIT_AS from unprivileged
    # users. Hook must swallow the error and continue — never wedge claude.
    def _boom(res, limits):
        raise ValueError("kernel refuses this tightening")
    monkeypatch.setattr(notify_hook.resource, "setrlimit", _boom)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _set_stdin(monkeypatch, '{"hook_event_name":"PreToolUse"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-x")
    notify_hook.main()  # must not raise


def test_notify_hook_survives_rlimit_oserror(monkeypatch, tmp_path):
    def _boom(res, limits):
        raise OSError("EPERM")
    monkeypatch.setattr(notify_hook.resource, "setrlimit", _boom)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _set_stdin(monkeypatch, '{"hook_event_name":"PreToolUse"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-x")
    notify_hook.main()  # must not raise


def test_statusline_sets_memory_rlimit(monkeypatch):
    calls = _capture_setrlimit(monkeypatch, statusline_notify)
    _set_stdin(monkeypatch, "")
    monkeypatch.delenv("CLAUDE_DTACH_SESSION", raising=False)
    statusline_notify.main()
    assert calls, "statusline_notify.main() must call resource.setrlimit"
    res, (soft, hard) = calls[0]
    assert res == resource.RLIMIT_AS
    assert soft == statusline_notify._MEMORY_CAP_BYTES
    assert hard == statusline_notify._MEMORY_CAP_BYTES


def test_statusline_survives_rlimit_rejection(monkeypatch, tmp_path, capsys):
    def _boom(res, limits):
        raise ValueError("kernel refuses this tightening")
    monkeypatch.setattr(statusline_notify.resource, "setrlimit", _boom)
    monkeypatch.setattr(statusline_notify, "SOCKET_PATH",
                        str(tmp_path / "nope.sock"))
    _set_stdin(monkeypatch, "")
    monkeypatch.delenv("CLAUDE_DTACH_SESSION", raising=False)
    statusline_notify.main()  # must not raise


def test_statusline_survives_rlimit_oserror(monkeypatch, tmp_path):
    def _boom(res, limits):
        raise OSError("EPERM")
    monkeypatch.setattr(statusline_notify.resource, "setrlimit", _boom)
    monkeypatch.setattr(statusline_notify, "SOCKET_PATH",
                        str(tmp_path / "nope.sock"))
    _set_stdin(monkeypatch, "")
    monkeypatch.delenv("CLAUDE_DTACH_SESSION", raising=False)
    statusline_notify.main()  # must not raise


# ---- cap-hit MemoryError notification (pre-allocated socket + payload) ---

def test_notify_hook_sends_cap_hit_on_memory_error(monkeypatch, tmp_path):
    sock_path = tmp_path / "aipager.sock"
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock_path))

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(str(sock_path))
    srv.settimeout(1.0)
    try:
        # Any MemoryError from anywhere inside _run() must be caught by
        # main(). We simulate one at sys.stdin.read() to be realistic —
        # a huge stdin blob is one of the actual real-world triggers.
        def _boom():
            raise MemoryError("simulated cap hit")
        monkeypatch.setattr(sys.stdin, "read", _boom)
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-vic")
        with pytest.raises(SystemExit) as exc:
            notify_hook.main()
        assert exc.value.code == 1
        data, _ = srv.recvfrom(4096)
        payload = json.loads(data.decode())
        assert payload == {
            "type": "hook_memory_cap_hit",
            "session": "claude-vic",
            "hook": "aipager-hook",
        }
    finally:
        srv.close()


def test_notify_hook_cap_hit_daemon_down_still_exits(monkeypatch, tmp_path):
    # SOCKET_PATH points to a non-existent socket → sendto raises OSError,
    # which the cap-hit handler must swallow. Hook still exits 1.
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))

    def _boom():
        raise MemoryError("simulated cap hit")
    monkeypatch.setattr(sys.stdin, "read", _boom)
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-vic")
    with pytest.raises(SystemExit) as exc:
        notify_hook.main()
    assert exc.value.code == 1  # exited even though notify path failed


def test_notify_hook_survives_socket_preopen_failure(monkeypatch, tmp_path):
    # If socket.socket() itself fails at pre-open time (e.g. EMFILE), the
    # hook must still run its normal body — not crash. Empty stdin → exit 0.
    def _boom(*a, **kw):
        raise OSError("EMFILE")
    monkeypatch.setattr(notify_hook.socket, "socket", _boom)
    _set_stdin(monkeypatch, "")
    with pytest.raises(SystemExit) as exc:
        notify_hook.main()
    assert exc.value.code == 0


def test_notify_hook_enforce_memory_error_reraises_to_main(monkeypatch, tmp_path):
    """A MemoryError raised inside the PreToolUse enforce path must
    propagate to main() (not get swallowed by the broad ``except
    Exception``), so the user is notified. Regression guard for a subtle
    ordering bug."""
    sock_path = tmp_path / "aipager.sock"
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock_path))
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(str(sock_path))
    srv.settimeout(1.0)
    try:
        # Feed a PreToolUse payload so the enforce path runs
        _set_stdin(monkeypatch,
                   '{"hook_event_name":"PreToolUse","tool_name":"Bash"}')
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-en")

        # Stub the enforce import to raise MemoryError
        import aipager.dtach.enforce as enforce_mod
        def _boom(_data):
            raise MemoryError("simulated in-enforce cap hit")
        monkeypatch.setattr(enforce_mod, "decide", _boom)

        with pytest.raises(SystemExit) as exc:
            notify_hook.main()
        assert exc.value.code == 1
        # Skip past any other in-flight datagrams (the pre-enforce _udp
        # fires an ordinary datagram first).
        while True:
            data, _ = srv.recvfrom(4096)
            payload = json.loads(data.decode())
            if payload.get("type") == "hook_memory_cap_hit":
                break
        assert payload["session"] == "claude-en"
        assert payload["hook"] == "aipager-hook"
    finally:
        srv.close()


def test_statusline_sends_cap_hit_on_memory_error(monkeypatch, tmp_path):
    sock_path = tmp_path / "aipager.sock"
    monkeypatch.setattr(statusline_notify, "SOCKET_PATH", str(sock_path))
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(str(sock_path))
    srv.settimeout(1.0)
    try:
        def _boom():
            raise MemoryError("simulated cap hit")
        monkeypatch.setattr(sys.stdin, "read", _boom)
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-sl")
        with pytest.raises(SystemExit) as exc:
            statusline_notify.main()
        assert exc.value.code == 1
        data, _ = srv.recvfrom(4096)
        payload = json.loads(data.decode())
        assert payload == {
            "type": "hook_memory_cap_hit",
            "session": "claude-sl",
            "hook": "aipager-statusline",
        }
    finally:
        srv.close()


def test_statusline_survives_socket_preopen_failure(monkeypatch, tmp_path):
    def _boom(*a, **kw):
        raise OSError("EMFILE")
    monkeypatch.setattr(statusline_notify.socket, "socket", _boom)
    _set_stdin(monkeypatch, "")
    monkeypatch.delenv("CLAUDE_DTACH_SESSION", raising=False)
    statusline_notify.main()  # must not raise, no SystemExit either


def test_notify_hook_cap_notifier_ordering(monkeypatch):
    """The pre-open MUST happen BEFORE setrlimit is called; otherwise a
    tight cap could kill the socket allocation itself. Verify the call
    order by recording both."""
    events: list[str] = []
    real_socket = socket.socket

    def spy_socket(*a, **kw):
        events.append("pre_open_socket")
        return real_socket(*a, **kw)
    def spy_setrlimit(res, limits):
        events.append("setrlimit")
    monkeypatch.setattr(notify_hook.socket, "socket", spy_socket)
    monkeypatch.setattr(notify_hook.resource, "setrlimit", spy_setrlimit)
    _set_stdin(monkeypatch, "")
    with pytest.raises(SystemExit):
        notify_hook.main()
    assert events[:2] == ["pre_open_socket", "setrlimit"], events


def test_statusline_cap_notifier_ordering(monkeypatch):
    """Mirror of the notify_hook ordering test: statusline's pre-open
    also MUST happen before setrlimit."""
    events: list[str] = []
    real_socket = socket.socket

    def spy_socket(*a, **kw):
        events.append("pre_open_socket")
        return real_socket(*a, **kw)
    def spy_setrlimit(res, limits):
        events.append("setrlimit")
    monkeypatch.setattr(statusline_notify.socket, "socket", spy_socket)
    monkeypatch.setattr(statusline_notify.resource, "setrlimit", spy_setrlimit)
    _set_stdin(monkeypatch, "")
    monkeypatch.delenv("CLAUDE_DTACH_SESSION", raising=False)
    statusline_notify.main()
    assert events[:2] == ["pre_open_socket", "setrlimit"], events
