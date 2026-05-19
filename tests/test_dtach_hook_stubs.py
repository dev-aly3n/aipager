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
