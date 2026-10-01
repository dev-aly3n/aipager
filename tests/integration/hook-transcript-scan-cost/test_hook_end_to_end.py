"""``aipager-hook`` end to end, in process (design.md success criterion:
"The hook's stdout on a long current turn is byte-identical to the old
scans' for halted, denied, owner and terminal cases"; entrypoints.md
"CLI commands").

Each case runs ``notify_hook._run`` twice on the same stdin payload:
once as shipped, once with ``enforce.decide`` replaced by the frozen
166a3f6 ``decide``. The two stdouts must be byte-identical. Anchors pin
what that stdout is, so equal-but-wrong cannot pass.

The daemon socket is a non-existent path under ``tmp_path``; snapshots
go through the root conftest's redirected ``snapshot_path``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from aipager import policy, policy_snapshot as ps
from aipager.dtach import enforce, notify_hook


def _kit():
    name = "_scancost_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / "_scan_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _kit()
cc = kit.cc
SESSION = kit.SESSION

TG = cc(kit.tg_prompt("please look"))
TERM = cc(kit.term_prompt("typed here"))
TR_BLOCK = cc(kit.tool_result(kit.BLOCK_TEXT, is_error=True))


def _builtin(name):
    pol = policy.load_policy(Path("/nonexistent/p.yaml"),
                             Path("/nonexistent/p.d"))
    return pol.get_role(name)


def _role(snap_file, role):
    p = snap_file(SESSION)
    if role is None:
        if p.exists():
            p.unlink()
        return
    ps.write_merged_snapshot(SESSION,
                             ps.resolve_snapshot(_builtin(role), None, None))


def _long_turn(rng_seed=3):
    """About 5 MB of current-turn tool results and assistant lines."""
    import random
    rng = random.Random(rng_seed)
    out = []
    size = 0
    while size < 5 << 20:
        if rng.random() < 0.5:
            ln = cc(kit.tool_result("".join(
                rng.choice("abcdef \n") for _ in range(rng.randint(10, 400)))
                + "x" * rng.randint(0, 40_000), rng=rng))
        else:
            ln = cc(kit.assistant_tool_use(
                {"command": "cat src/x.py"}, rng=rng))
        out.append(ln)
        size += len(ln)
    return out


_TURN = None


def _turn():
    global _TURN
    if _TURN is None:
        _TURN = _long_turn()
    return _TURN


def _transcript(tmp_path, kind):
    turn = _turn()
    lines = {
        "halted": [TERM, TG, TR_BLOCK] + turn,
        "plain": [TERM, TG] + turn,
        "terminal": [TG, TR_BLOCK, TERM] + turn,
        "raising": [TERM, TG] + turn + [b"[1]"],
        "continuation_halted": [TG, TR_BLOCK] + turn[:50] + [cc(kit.envelope(
            "user", kit.prompt_msg(kit.TASK_NOTE)))] + turn[50:],
    }[kind]
    return kit.write_lines(tmp_path / f"{kind}.jsonl", lines)


def _run_hook(monkeypatch, capsys, tmp_path, payload, decide=None):
    capsys.readouterr()
    with monkeypatch.context() as m:
        m.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
        m.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        if decide is not None:
            m.setattr(enforce, "decide", decide)
        notify_hook._run(SESSION, [b""])
    return capsys.readouterr().out


def _payload(tx, project, tool, tool_input):
    return {"hook_event_name": "PreToolUse", "session_id": kit.CLAUDE_SID,
            "cwd": str(project), "tool_name": tool, "tool_input": tool_input,
            "transcript_path": str(tx)}


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "work" / "proj"
    p.mkdir(parents=True)
    return p


CASES = {
    # name: (role, transcript, tool, input-maker)
    "halted": ("user", "halted", "Read", lambda p: {"file_path": str(p / "a")}),
    "denied_protected_path": ("user", "plain", "Read",
                              lambda p: {"file_path": "~/.claude/x"}),
    "denied_bash": ("user", "plain", "Bash", lambda p: {"command": "ls"}),
    "allowed_in_project": ("user", "plain", "Read",
                           lambda p: {"file_path": str(p / "a")}),
    "owner_on_halted": ("owner", "halted", "Bash",
                        lambda p: {"command": "ls"}),
    "owner_on_raising": ("owner", "raising", "Bash",
                         lambda p: {"command": "ls"}),
    "terminal": ("user", "terminal", "Bash", lambda p: {"command": "ls"}),
    "no_snapshot_floor": (None, "plain", "Bash", lambda p: {"command": "ls"}),
    "raising_fail_closed": ("user", "raising", "Read",
                            lambda p: {"file_path": str(p / "a")}),
    "continuation_keeps_halt": ("user", "continuation_halted", "Read",
                                lambda p: {"file_path": str(p / "a")}),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_hook_stdout_is_byte_identical_to_166a3f6(tmp_path, monkeypatch,
                                                  capsys, old, snap_file,
                                                  project, name):
    role, tx_kind, tool, mk_input = CASES[name]
    _role(snap_file, role)
    tx = _transcript(tmp_path, tx_kind)
    payload = _payload(tx, project, tool, mk_input(project))
    new = _run_hook(monkeypatch, capsys, tmp_path, payload)
    ref = _run_hook(monkeypatch, capsys, tmp_path, payload, old.decide)
    assert new == ref


def _deny_reason(out):
    first = out.strip().splitlines()[0]
    return json.loads(first)["hookSpecificOutput"]["permissionDecisionReason"]


ANCHORS = {
    "halted": "aipager safety policy: " + kit.HALT_REASON,
    "continuation_keeps_halt": "aipager safety policy: " + kit.HALT_REASON,
    "raising_fail_closed": "aipager safety policy: " + kit.FAIL_CLOSED_REASON,
}


@pytest.mark.parametrize("name", sorted(ANCHORS))
def test_hook_deny_reason_anchor(tmp_path, monkeypatch, capsys, snap_file,
                                 project, name):
    role, tx_kind, tool, mk_input = CASES[name]
    _role(snap_file, role)
    tx = _transcript(tmp_path, tx_kind)
    out = _run_hook(monkeypatch, capsys, tmp_path,
                    _payload(tx, project, tool, mk_input(project)))
    assert _deny_reason(out) == ANCHORS[name]


@pytest.mark.parametrize("name", ["denied_protected_path", "denied_bash",
                                  "no_snapshot_floor"])
def test_hook_policy_deny_anchor(tmp_path, monkeypatch, capsys, snap_file,
                                 project, name):
    role, tx_kind, tool, mk_input = CASES[name]
    _role(snap_file, role)
    tx = _transcript(tmp_path, tx_kind)
    out = _run_hook(monkeypatch, capsys, tmp_path,
                    _payload(tx, project, tool, mk_input(project)))
    assert json.loads(out.strip().splitlines()[0]) == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": _deny_reason(out)}}


@pytest.mark.parametrize("name", ["owner_on_halted", "owner_on_raising",
                                  "terminal", "allowed_in_project"])
def test_hook_allow_prints_nothing(tmp_path, monkeypatch, capsys, snap_file,
                                   project, name):
    role, tx_kind, tool, mk_input = CASES[name]
    _role(snap_file, role)
    tx = _transcript(tmp_path, tx_kind)
    out = _run_hook(monkeypatch, capsys, tmp_path,
                    _payload(tx, project, tool, mk_input(project)))
    assert out == ""


def test_hook_owner_turn_reads_no_transcript(tmp_path, monkeypatch, capsys,
                                             snap_file, project):
    import builtins
    _role(snap_file, "owner")
    tx = _transcript(tmp_path, "halted")
    opened = []
    real_open = builtins.open

    def spy(path, *a, **k):
        if str(path) == str(tx):
            opened.append(path)
        return real_open(path, *a, **k)
    monkeypatch.setattr(builtins, "open", spy)
    _run_hook(monkeypatch, capsys, tmp_path,
              _payload(tx, project, "Bash", {"command": "ls"}))
    assert opened == []
