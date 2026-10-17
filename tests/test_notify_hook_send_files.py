"""The hook's answer to a SendUserFile: a soft deny Claude reads (never the
sticky "aipager safety policy" marker, never ``safety_blocked``), then one
``send_files_refused`` datagram naming each refused file for the daemon's
audit; nothing new on allow."""

from __future__ import annotations

import io
import json
import socket as socket_mod
import sys

import pytest

from aipager import send_check
from aipager.dtach import notify_hook


class _FakeDatagramSocket:
    def __init__(self, sink):
        self._sink = sink

    def sendto(self, data, _addr):
        self._sink.append(json.loads(data.decode()))

    def close(self):
        pass


@pytest.fixture
def hook(monkeypatch, tmp_path, capsys):
    from aipager import policy_snapshot as ps
    from aipager.policy import load_policy
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-send")
    owner = load_policy(tmp_path / "none.yaml", tmp_path / "none.d").get_role("owner")
    ps.write_merged_snapshot("claude-send", ps.resolve_snapshot(owner, None, None))
    sent: list[dict] = []
    monkeypatch.setattr(socket_mod, "socket",
                        lambda *_a, **_k: _FakeDatagramSocket(sent))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @alice · role:owner]\nsend it"}}) + "\n")

    def run(files):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
            "hook_event_name": "PreToolUse", "tool_name": "SendUserFile",
            "tool_input": {"files": files, "status": "normal"},
            "tool_use_id": "toolu_9", "cwd": str(tmp_path),
            "session_id": "s1", "transcript_path": str(transcript),
        })))
        sent.clear()
        notify_hook.main()
        return capsys.readouterr().out, list(sent)
    run.home = home
    run.tmp = tmp_path
    return run


def test_refused_send_reports_each_file(hook):
    creds = hook.home / ".claude" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text("{}")
    empty = hook.tmp / "e.txt"
    empty.write_text("")
    out, sent = hook([str(creds), str(empty)])
    payload = json.loads(out)["hookSpecificOutput"]
    assert payload["permissionDecision"] == "deny"
    reason = payload["permissionDecisionReason"]
    assert reason.startswith("SendUserFile refused, nothing was sent: ")
    assert "aipager safety policy" not in out
    names = [d["hook_event_name"] for d in sent]
    assert "safety_blocked" not in names
    refused = [d for d in sent if d["hook_event_name"] == "send_files_refused"]
    assert refused == [{
        "hook_event_name": "send_files_refused", "session": "claude-send",
        "tool_use_id": "toolu_9",
        "files": [
            {"path": str(creds), "code": "floor", "reason": send_check._why("floor")},
            {"path": str(empty), "code": "empty", "reason": send_check._why("empty")},
        ]}]
    # The deny is printed before the report is sent: the forward of the
    # PreToolUse itself comes first, the refusal last.
    assert names[-1] == "send_files_refused"


def test_allowed_send_prints_and_sends_nothing_new(hook):
    pic = hook.tmp / "pic.png"
    pic.write_bytes(b"\x89PNG")
    out, sent = hook([str(pic)])
    assert out.strip() == ""
    assert [d["hook_event_name"] for d in sent] == ["PreToolUse"]
