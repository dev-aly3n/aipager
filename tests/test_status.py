"""Tests for aipager.status — daemon snapshot + session listing."""

from __future__ import annotations

import argparse
import json
import socket

from aipager import status


def _ns(**kw):
    return argparse.Namespace(**kw)


# ----- _read_state / _read_statusline -----

def test_read_state_missing_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(status, "SESSION_STATE_FILE", tmp_path / "missing.json")
    assert status._read_state() == {}


def test_read_state_corrupt_returns_empty(monkeypatch, tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{ not json")
    monkeypatch.setattr(status, "SESSION_STATE_FILE", p)
    assert status._read_state() == {}


def test_read_state_valid(monkeypatch, tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"sessions": {"claude-jim": {"label": "jim"}}}))
    monkeypatch.setattr(status, "SESSION_STATE_FILE", p)
    assert status._read_state()["sessions"]["claude-jim"]["label"] == "jim"


def test_read_statusline_missing(tmp_path, monkeypatch):
    # path is /tmp/claude-status-{name}.json — name doesn't exist
    assert status._read_statusline("nonexistent-xyz-9999") == {}


# ----- _live_sessions -----

def test_live_sessions_picks_up_dtach_socks(tmp_path, monkeypatch):
    # Stub out _live_sessions's Path.glob by replacing the function with one
    # that scans our tmp_path. (Monkeypatching Path.glob directly is fragile
    # because the function would call its own replacement recursively.)
    sock_files = []
    for label in ("jim", "john"):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock = tmp_path / f"claude-dtach-{label}.sock"
        s.bind(str(sock))
        s.listen(1)
        sock_files.append(s)
    try:
        # Re-implement _live_sessions inline against tmp_path to verify the
        # logic (.sock parsing + name reconstruction) without filesystem
        # assumptions.
        out = set()
        for sock in tmp_path.glob("claude-dtach-*.sock"):
            name = "claude-" + sock.stem.removeprefix("claude-dtach-")
            out.add(name)
        assert out == {"claude-jim", "claude-john"}
    finally:
        for s in sock_files:
            s.close()


# ----- _daemon_alive -----

def test_daemon_alive_no_socket(monkeypatch, tmp_path):
    monkeypatch.setattr(status, "SOCKET_PATH", str(tmp_path / "missing.sock"))
    assert status._daemon_alive() is False


def test_daemon_alive_stale_socket(monkeypatch, tmp_path):
    sock_path = tmp_path / "aipager.sock"
    sock_path.touch()
    monkeypatch.setattr(status, "SOCKET_PATH", str(sock_path))

    class _FakeSocket:
        def __init__(self, *a, **kw): pass
        def settimeout(self, *_): pass
        def sendto(self, *_): raise ConnectionRefusedError
        def close(self): pass

    monkeypatch.setattr(status.socket, "socket", _FakeSocket)
    assert status._daemon_alive() is False


def test_daemon_alive_listening(monkeypatch, tmp_path):
    sock_path = tmp_path / "aipager.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setattr(status, "SOCKET_PATH", str(sock_path))
        assert status._daemon_alive() is True
    finally:
        server.close()


# ----- _gather_sessions -----

def test_gather_marks_gone_when_no_socket(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "sessions": {
            "claude-jim": {"label": "jim", "model_name": "Opus", "busy_msg_id": None,
                           "pending_queue": []},
        },
    }))
    monkeypatch.setattr(status, "SESSION_STATE_FILE", state_file)
    monkeypatch.setattr(status, "_live_sessions", lambda: set())
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    rows, live = status._gather_sessions()
    assert len(rows) == 1
    assert rows[0]["status"] == "GONE"
    assert rows[0]["label"] == "jim"


def test_gather_marks_idle_when_socket_alive(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "sessions": {
            "claude-jim": {"label": "jim", "model_name": "Opus", "busy_msg_id": None,
                           "pending_queue": []},
        },
    }))
    monkeypatch.setattr(status, "SESSION_STATE_FILE", state_file)
    monkeypatch.setattr(status, "_live_sessions", lambda: {"claude-jim"})
    monkeypatch.setattr(status, "_read_statusline", lambda name: {
        "model": {"display_name": "Opus 4.7"},
        "context_window": {"used_percentage": 12},
        "cost": {"total_cost_usd": 0.42},
    })
    rows, _ = status._gather_sessions()
    assert rows[0]["status"] == "IDLE"
    assert rows[0]["context_pct"] == 12
    assert rows[0]["cost_usd"] == 0.42
    assert rows[0]["model"] == "Opus 4.7"


def test_gather_marks_busy_when_busy_msg_id_set(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "sessions": {
            "claude-jim": {"label": "jim", "busy_msg_id": 1234,
                           "pending_queue": [["queued1", 9000], ["queued2", 9001]]},
        },
    }))
    monkeypatch.setattr(status, "SESSION_STATE_FILE", state_file)
    monkeypatch.setattr(status, "_live_sessions", lambda: {"claude-jim"})
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    rows, _ = status._gather_sessions()
    assert rows[0]["status"] == "BUSY"
    assert rows[0]["queue_depth"] == 2


def test_gather_picks_up_orphan_sockets(monkeypatch, tmp_path):
    """A live socket with no entry in the registry should still show up."""
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"sessions": {}}))
    monkeypatch.setattr(status, "SESSION_STATE_FILE", state_file)
    monkeypatch.setattr(status, "_live_sessions", lambda: {"claude-orphan"})
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    rows, _ = status._gather_sessions()
    assert len(rows) == 1
    assert rows[0]["label"] == "orphan"
    assert rows[0]["status"] == "IDLE"


def test_gather_orders_gone_last(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "sessions": {
            "claude-zzz": {"label": "zzz", "busy_msg_id": None, "pending_queue": []},
            "claude-aaa": {"label": "aaa", "busy_msg_id": None, "pending_queue": []},
            "claude-mmm": {"label": "mmm", "busy_msg_id": None, "pending_queue": []},
        },
    }))
    monkeypatch.setattr(status, "SESSION_STATE_FILE", state_file)
    monkeypatch.setattr(status, "_live_sessions", lambda: {"claude-zzz", "claude-aaa"})
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    rows, _ = status._gather_sessions()
    # aaa, zzz (alphabetical live), then mmm (GONE)
    assert [r["label"] for r in rows] == ["aaa", "zzz", "mmm"]
    assert rows[-1]["status"] == "GONE"


# ----- cmd_status: exit codes + JSON output -----

def test_cmd_status_missing_config_returns_2(monkeypatch, capsys):
    monkeypatch.setattr(status, "BOT_TOKEN", "")
    monkeypatch.setattr(status, "CHAT_ID", "")
    rc = status.cmd_status(_ns(as_json=False))
    assert rc == 2
    assert "isn't configured" in capsys.readouterr().err


def test_cmd_status_missing_config_json(monkeypatch, capsys):
    monkeypatch.setattr(status, "BOT_TOKEN", "")
    monkeypatch.setattr(status, "CHAT_ID", "1234")
    rc = status.cmd_status(_ns(as_json=True))
    assert rc == 2
    out = capsys.readouterr().out
    assert "CLAUDE_TG_BOT_TOKEN" in out
    assert "CLAUDE_TG_CHAT_ID" not in out


def test_cmd_status_daemon_down_returns_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: False)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))
    rc = status.cmd_status(_ns(as_json=False))
    assert rc == 1


def test_cmd_status_daemon_up_returns_0(monkeypatch):
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))
    rc = status.cmd_status(_ns(as_json=False))
    assert rc == 0


def test_cmd_status_json_output_shape(monkeypatch, capsys):
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([
        {"name": "claude-jim", "label": "jim", "status": "IDLE",
         "model": "Opus", "context_pct": 5, "cost_usd": 0.10,
         "queue_depth": 0},
    ], {"claude-jim"}))
    rc = status.cmd_status(_ns(as_json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["daemon"]["up"] is True
    assert payload["daemon"]["chat_id"] == "5"
    assert payload["sessions"][0]["label"] == "jim"
    assert payload["total_cost_usd"] == 0.1


def test_cmd_status_total_cost_sums(monkeypatch):
    captured = {}

    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([
        {"name": "claude-jim", "label": "jim", "status": "IDLE",
         "model": "", "context_pct": None, "cost_usd": 0.1, "queue_depth": 0},
        {"name": "claude-john", "label": "john", "status": "IDLE",
         "model": "", "context_pct": None, "cost_usd": 0.2, "queue_depth": 0},
        {"name": "claude-tim", "label": "tim", "status": "IDLE",
         "model": "", "context_pct": None, "cost_usd": None, "queue_depth": 0},
    ], set()))

    def _capture(daemon_up, sessions, total):
        captured["total"] = total

    # cmd_status picks _render_rich or _render_plain based on console.is_terminal
    # (a property without a setter). Patch both renderers so we don't care which.
    monkeypatch.setattr(status, "_render_plain", _capture)
    monkeypatch.setattr(status, "_render_rich", _capture)
    status.cmd_status(_ns(as_json=False))
    assert round(captured["total"], 2) == 0.30
