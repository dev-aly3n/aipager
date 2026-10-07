"""A PreToolUse the hook cannot decode or handle is denied (roadmap 8.107).

Claude Code reads a PreToolUse hook's exit 1 as a NON-blocking error and
runs the tool. The hook used to exit 1 (an uncaught exception) for a
payload nested past the JSON decoder's limit, a huge integer, a payload
that is not an object, or a crash on its way to the decision, and 0 for
an undecodable one: either way the tool ran with ``enforce`` (the
role's denied tools, the safety floor) never asked.

Each row runs the real hook entry in its own process, the way Claude
Code runs it, against an instance folder of its own (no daemon socket,
no snapshot outside ``tmp_path``).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aipager.dtach import notify_hook

REPO = Path(__file__).resolve().parents[1]
SESSION = "claude-failclosed-8107"


def _hook(tmp_path: Path, payload: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "AIPAGER"))}
    env.update(PYTHONPATH=str(REPO), AIPAGER_INSTANCE_DIR=str(tmp_path),
               CLAUDE_DTACH_SESSION=SESSION, HOME=str(tmp_path / "home"))
    return subprocess.run(
        [sys.executable, "-c",
         "from aipager.dtach.notify_hook import main; main()"],
        input=payload, capture_output=True, text=True, env=env, timeout=60)


def _denied(proc: subprocess.CompletedProcess) -> bool:
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        return False
    decision = json.loads(out)["hookSpecificOutput"]
    return (decision["hookEventName"] == "PreToolUse"
            and decision["permissionDecision"] == "deny")


def _pre_tool_use(tool_input: str) -> str:
    return ('{"hook_event_name":"PreToolUse","tool_name":"mcp__x__y",'
            f'"tool_input":{tool_input}}}')


DEEP = "[" * 100_000 + "]" * 100_000          # past every decoder's limit
HUGE_INT = "9" * 5_000                         # past the int digit limit

UNHANDLED = {
    "nested past the decoder": _pre_tool_use(f'{{"a":{DEEP}}}'),
    "huge integer": _pre_tool_use(f'{{"n":{HUGE_INT}}}'),
    "not an object": '[{"hook_event_name":"PreToolUse","tool_name":"Bash"}]',
    "not json": '{"hook_event_name":"PreToolUse","tool_name":"Bash", garbage',
}


@pytest.mark.parametrize("case", sorted(UNHANDLED))
def test_a_pre_tool_use_the_hook_cannot_handle_is_denied(tmp_path, case):
    proc = _hook(tmp_path, UNHANDLED[case])
    assert _denied(proc), (proc.returncode, proc.stdout[-300:], proc.stderr[-300:])


def test_the_owners_bypass_still_lets_it_through(tmp_path):
    """The same answer as ``enforce.fail_closed``: an owner acting with the
    safety boundary bypassed is not denied."""
    (tmp_path / f"claude-policy-{SESSION}.json").write_text(
        json.dumps({"bypass_safety": True}))
    proc = _hook(tmp_path, UNHANDLED["nested past the decoder"])
    assert proc.returncode == 0 and proc.stdout.strip() == "", proc


def test_an_ordinary_pre_tool_use_is_still_allowed(tmp_path):
    """The control: the fix denies only what it cannot read."""
    proc = _hook(tmp_path, _pre_tool_use('{"command":"ls"}'))
    assert proc.returncode == 0 and proc.stdout.strip() == "", proc


@pytest.mark.parametrize("event", ["Stop", "UserPromptSubmit", "PostToolUse"])
def test_other_events_are_never_blocked(tmp_path, event):
    """Exit 2 means "block" for these (Stop: keep going; a prompt: refuse
    it), so an unreadable one must never answer that way, nor print a
    PreToolUse deny."""
    for payload in (
            f'{{"hook_event_name":"{event}","x":{DEEP}}}',
            f'{{"hook_event_name":"{event}", garbage',
            f'[{{"hook_event_name":"{event}"}}]'):
        proc = _hook(tmp_path, payload)
        assert proc.returncode != 2, (event, proc)
        assert "permissionDecision" not in proc.stdout, (event, proc)


def _crash_before_the_decision(monkeypatch, tmp_path, event: str):
    """A step on the hook's way to the decision raises (here the
    statusline read every event does first). *event* is put in the JSON
    as given, so an escaped name is possible."""
    def _boom(session):
        raise RuntimeError("a step before the decision broke")

    monkeypatch.setattr(notify_hook, "_read_statusline_tokens", _boom)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "none.sock"))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        f'{{"hook_event_name":"{event}","tool_name":"Bash",'
        '"tool_input":{"command":"ls"}}'))


@pytest.mark.parametrize("event", ["PreToolUse", "\\u0050reToolUse"])
def test_a_crash_before_the_decision_denies_a_pre_tool_use(
        monkeypatch, tmp_path, capsys, event):
    """Also when the event name is written escaped, which the raw match
    before decoding does not see."""
    _crash_before_the_decision(monkeypatch, tmp_path, event)
    notify_hook.main()  # answers, never raises
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"


def test_a_crash_before_the_decision_leaves_other_events_as_they_were(
        monkeypatch, tmp_path, capsys):
    _crash_before_the_decision(monkeypatch, tmp_path, "Stop")
    with pytest.raises(RuntimeError):
        notify_hook.main()  # a non-zero exit there never blocks Claude
    assert "permissionDecision" not in capsys.readouterr().out


def test_a_nested_pre_tool_use_text_does_not_make_another_event_one(
        monkeypatch, tmp_path, capsys):
    """The raw match before decoding finds the text anywhere; once the
    payload decodes, its own event name decides. A PostToolUse whose tool
    input holds the text, crashing on its way, is no PreToolUse."""
    def _boom(session):
        raise RuntimeError("a step before the decision broke")

    monkeypatch.setattr(notify_hook, "_read_statusline_tokens", _boom)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "none.sock"))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PostToolUse", "tool_name": "mcp__x__y",
        "tool_input": {"hook_event_name": "PreToolUse"}}, separators=(",", ":"))))
    with pytest.raises(RuntimeError):
        notify_hook.main()
    assert "permissionDecision" not in capsys.readouterr().out


def test_an_answer_that_cannot_be_written_still_denies(monkeypatch, tmp_path):
    """The last resort: exit 2, Claude Code's deny for a PreToolUse."""
    _crash_before_the_decision(monkeypatch, tmp_path, "PreToolUse")

    def _cannot_write(session, error):
        raise MemoryError

    monkeypatch.setattr(notify_hook, "_deny_unhandled_pre_tool_use", _cannot_write)
    with pytest.raises(SystemExit) as exc:
        notify_hook.main()
    assert exc.value.code == 2
