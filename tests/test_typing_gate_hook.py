"""Roadmap 8.143, the hook side: a PreToolUse that lets its tool go on ends
at the typing gate (``typing_gate.pass_gate``), so the tool's permission
dialog is drawn only after a prompt aipager is typing is in. A denied call
draws no dialog and is never held."""

from __future__ import annotations

import io
import json
import os
import socket as socket_mod
import sys

import pytest

from aipager.dtach import notify_hook, typing_gate


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
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "run" / "nope.sock"))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-gate")
    owner = load_policy(tmp_path / "none.yaml", tmp_path / "none.d").get_role("owner")
    ps.write_merged_snapshot("claude-gate", ps.resolve_snapshot(owner, None, None))
    sent: list[dict] = []
    monkeypatch.setattr(socket_mod, "socket",
                        lambda *_a, **_k: _FakeDatagramSocket(sent))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @alice · role:owner]\ndo it"}}) + "\n")
    gates: list[tuple] = []

    def spy(base_dir, session, **_kw):
        gates.append((base_dir, session, list(sent)))
        return "passed"
    monkeypatch.setattr(typing_gate, "pass_gate", spy)

    def run(event="PreToolUse", tool="Bash", tool_input=None):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
            "hook_event_name": event, "tool_name": tool,
            "tool_input": tool_input if tool_input is not None else {"command": "ls"},
            "tool_use_id": "toolu_1", "cwd": str(tmp_path),
            "session_id": "s1", "transcript_path": str(transcript),
        })))
        sent.clear()
        gates.clear()
        notify_hook.main()
        return capsys.readouterr().out, list(gates)
    run.home = home
    run.tmp = tmp_path
    return run


def test_an_allowed_tool_call_ends_at_the_typing_gate(hook):
    out, gates = hook()
    assert "deny" not in out
    assert len(gates) == 1
    base_dir, session, forwarded = gates[0]
    assert base_dir == str(hook.tmp / "run")
    assert session == "claude-gate"
    # Last: the PreToolUse itself was forwarded before the gate.
    assert [d["hook_event_name"] for d in forwarded][-1:] == ["PreToolUse"]


@pytest.mark.parametrize("block", [
    {"tool": "Bash", "reason": "not for this role"},
    {"tool": "Bash", "reason": "peer check running", "soft": True},
])
def test_a_denied_tool_call_is_never_held(hook, monkeypatch, block):
    from aipager.dtach import enforce
    monkeypatch.setattr(enforce, "decide", lambda _data: dict(block))
    out, gates = hook()
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert gates == []


def test_other_events_never_reach_the_gate(hook):
    _out, gates = hook(event="PostToolUse")
    assert gates == []


def test_a_gate_that_raises_lets_the_tool_go_on(hook, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(typing_gate, "pass_gate", broken)
    unhandled = []
    monkeypatch.setattr(notify_hook, "_deny_unhandled_pre_tool_use",
                        lambda *a, **k: unhandled.append(a) or False)
    out, _gates = hook()
    assert "deny" not in out
    assert unhandled == [], "the gate's error escaped the hook's own handling"


def test_the_hook_and_the_daemon_use_the_same_gate_folder(hook, monkeypatch,
                                                           _isolate_typing_gate):
    """The daemon writes its marker beside ``config.SOCKET_PATH``; the hook
    reads beside its own ``SOCKET_PATH``: one folder when the two agree."""
    from aipager import config
    from aipager.dtach import inject
    run_dir = hook.tmp / "run"
    run_dir.mkdir()
    monkeypatch.setattr(config, "SOCKET_PATH", str(run_dir / "nope.sock"))
    monkeypatch.setattr(typing_gate, "gate_dir", _isolate_typing_gate)
    assert inject._gate_base() == os.path.dirname(notify_hook.SOCKET_PATH)
    assert typing_gate.begin(inject._gate_base(), "claude-gate")
    assert (run_dir / "aipager-gate-claude-gate" / "typing").exists()
    typing_gate.end(inject._gate_base(), "claude-gate")
