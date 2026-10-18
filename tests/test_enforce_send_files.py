"""The PreToolUse decision for a file Claude sends (SendUserFile): the send
floor holds for every turn, the owner's and a terminal's included; a
member sends only from inside its root; every refusal is soft (Claude reads
the reason and the turn goes on), except in a turn already halted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipager import member_root, peer_auth, policy, send_check
from aipager import policy_snapshot as ps
from aipager.dtach import enforce

SESSION = "claude-proj"
SID = "5b1d7363-aaaa-bbbb-cccc-0000000000ab"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home" / "op"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    return h


@pytest.fixture
def root(home):
    r = home / "aipager-users" / "sara"
    (r / "proj").mkdir(parents=True)
    (r / "proj" / "chart.png").write_bytes(b"\x89PNG....")
    return r


def _pol(tmp_path=None, text=None):
    if text is None:
        return policy.load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))
    f = tmp_path / "policy.yaml"
    f.write_text(text)
    return policy.load_policy(f, tmp_path / "none.d")


def _use(role, root=None):
    bound = member_root.RootBound(str(root), ()) if root is not None else None
    ps.write_merged_snapshot(SESSION, ps.resolve_snapshot(role, None, None,
                                                          root_bound=bound))


def _transcript(tmp_path, terminal=False, results=()):
    t = tmp_path / "t.jsonl"
    lines = [{"type": "user", "message": {
        "content": "do it" if terminal
        else "[via Telegram · @sara · role:user]\ndo it"}}]
    for text in results:
        lines.append({"type": "user", "message": {"content": [{
            "type": "tool_result", "content": text}]}})
    t.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return str(t)


def _data(tmp_path, files, cwd, *, tool="SendUserFile", terminal=False,
          results=(), tool_input=None):
    return {
        "hook_event_name": "PreToolUse", "session": SESSION, "session_id": SID,
        "cwd": str(cwd), "tool_name": tool,
        "tool_input": tool_input if tool_input is not None
        else {"files": files, "status": "normal"},
        "tool_use_id": "toolu_1",
        "transcript_path": _transcript(tmp_path, terminal, results)}


def _file(path, data=b"data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _hook_output(block) -> str | None:
    """What the hook prints for a block (notify_hook's PreToolUse branch)."""
    if not block:
        return None
    if block.get("soft"):
        return enforce.soft_deny_json(block["reason"])
    return enforce.deny_decision_json(block["reason"])


def _shown_reason(block) -> str:
    out = json.loads(_hook_output(block))
    return out["hookSpecificOutput"]["permissionDecisionReason"]


# ---- the floor for everyone ----------------------------------------------

def test_owner_cannot_send_claude_credentials(tmp_path, home):
    creds = _file(home / ".claude" / ".credentials.json", b"{}")
    _use(_pol().get_role("owner"))
    block, rules, info = enforce.decide_full(_data(tmp_path, [creds], home))
    assert block == {"tool": "SendUserFile", "soft": True,
                     "reason": send_check.refusal_reason(
                         send_check.check_files({"files": [creds]}, None))}
    assert rules is None
    assert info["send_refusals"] == [{"path": creds, "code": "floor",
                                      "reason": send_check._why("floor")}]
    assert "aipager safety policy" not in _hook_output(block)


def test_owner_cannot_send_claude_json(tmp_path, home):
    _file(home / ".claude.json", b"{}")
    _use(_pol().get_role("owner"))
    block = enforce.decide(_data(tmp_path, "~/.claude.json", home))
    assert block["soft"] is True and "(a protected file:" in block["reason"]


def test_terminal_turn_cannot_send_an_ssh_key(tmp_path, home):
    key = _file(home / ".ssh" / "config")
    _use(_pol().get_role("user"))
    block = enforce.decide(_data(tmp_path, [key], home, terminal=True))
    assert block and block["soft"] is True
    assert send_check.reason_code(block["reason"]) == "floor"


def test_terminal_and_owner_may_send_any_other_file(tmp_path, home):
    pic = _file(tmp_path / "elsewhere" / "pic.png")
    _use(_pol().get_role("user"))
    assert enforce.decide(_data(tmp_path, [pic], home, terminal=True)) is None
    _use(_pol().get_role("owner"))
    assert enforce.decide(_data(tmp_path, [pic, "/etc/hostname"], home)) is None


def test_admin_may_send_any_file_outside_the_floor(tmp_path, home):
    pic = _file(home / "notes" / "pic.png")
    _use(_pol().get_role("admin"))
    assert enforce.decide(_data(tmp_path, [pic], home / "notes")) is None
    creds = _file(home / ".claude" / "x.json")
    assert enforce.decide(_data(tmp_path, [creds], home))["soft"] is True


def test_file_checks_apply_to_the_owner(tmp_path, home):
    _use(_pol().get_role("owner"))
    empty = _file(tmp_path / "e.txt", b"")
    block = enforce.decide(_data(tmp_path, [empty, str(tmp_path / "gone")], home))
    assert block["soft"] is True
    assert "(the file is empty)" in block["reason"]
    assert "(no such file)" in block["reason"]


# ---- the member's root --------------------------------------------------

def test_member_inside_root_is_allowed(tmp_path, home, root):
    _use(_pol().get_role("user"), root)
    assert enforce.decide(_data(tmp_path, ["chart.png"], root / "proj")) is None


def test_member_cannot_send_outside_root_even_outside_home(tmp_path, home, root):
    outside = _file(tmp_path / "x.png")
    _use(_pol().get_role("user"), root)
    block, _rules, info = enforce.decide_full(
        _data(tmp_path, [outside], root / "proj"))
    assert block["soft"] is True
    assert send_check.reason_code(block["reason"]) == "outside_root"
    assert [f["code"] for f in info["send_refusals"]] == ["outside_root"]


def test_member_cannot_send_from_home_outside_root(tmp_path, home, root):
    other = _file(home / "elsewhere.png")
    _use(_pol().get_role("user"), root)
    block = enforce.decide(_data(tmp_path, [other], root / "proj"))
    assert send_check.reason_code(block["reason"]) == "outside_root"


def test_member_link_in_root_to_outside_is_refused(tmp_path, home, root):
    target = _file(tmp_path / "x.png")
    (root / "proj" / "link.png").symlink_to(target)
    _use(_pol().get_role("user"), root)
    block = enforce.decide(_data(tmp_path, ["link.png"], root / "proj"))
    assert send_check.reason_code(block["reason"]) == "outside_root"


_BYPASS_NO_MANAGE = "roles:\n  boss:\n    bypass_safety: true\n"


def test_root_bound_bypass_role_cannot_send_outside_root(tmp_path, home, root):
    outside = _file(tmp_path / "x.png")
    _use(_pol(tmp_path, _BYPASS_NO_MANAGE).get_role("boss"), root)
    block = enforce.decide(_data(tmp_path, [outside], root / "proj"))
    assert block and block["soft"] is True
    assert send_check.reason_code(block["reason"]) == "outside_root"
    assert enforce.decide(_data(tmp_path, ["chart.png"], root / "proj")) is None


# ---- soft, never halting -------------------------------------------------

def test_refusal_is_soft_and_unmarked(tmp_path, home):
    """A refusal by the turn's own rules (here its tools) is turned soft,
    worded for Claude to pass on, with each file reported."""
    pic = _file(home / "proj" / "a.png")
    role = _pol(tmp_path, "roles:\n  user:\n    deny_tools: [SendUserFile]\n"
                ).get_role("user")
    _use(role)
    block, _rules, info = enforce.decide_full(_data(tmp_path, [pic], home / "proj"))
    assert block["soft"] is True
    assert block["reason"] == send_check.turn_rule_reason(
        "SendUserFile is in this role's deny_tools")
    assert send_check.reason_code(block["reason"]) == "turn_rules"
    assert "aipager safety policy" not in _hook_output(block)
    assert info["send_refusals"] == [{"path": pic, "code": "turn_rules",
                                      "reason": "SendUserFile is in this role's deny_tools"}]


def test_halted_turn_still_denies_hard(tmp_path, home, root):
    _use(_pol().get_role("user"), root)
    data = _data(tmp_path, ["chart.png"], root / "proj",
                 results=["aipager safety policy: Write outside the root"])
    block = enforce.decide(data)
    assert block.get("soft") is None
    assert block["reason"].startswith("session halted")


def test_later_call_after_a_send_refusal_is_allowed(tmp_path, home, root):
    outside = _file(tmp_path / "x.png")
    _use(_pol().get_role("user"), root)
    block = enforce.decide(_data(tmp_path, [outside], root / "proj"))
    shown = _shown_reason(block)
    # The refusal is in the transcript as the call's result; the next
    # call of the same turn is decided normally.
    nxt = _data(tmp_path, ["chart.png"], root / "proj", results=[shown])
    assert enforce.decide(nxt) is None
    read = _data(tmp_path, None, root / "proj", tool="Read", results=[shown],
                 tool_input={"file_path": str(root / "proj" / "chart.png")})
    assert enforce.decide(read) is None


def test_send_check_error_denies_owner(tmp_path, home, monkeypatch):
    _use(_pol().get_role("owner"))
    pic = _file(home / "a.png")

    def boom(*a, **k):
        raise RuntimeError("check failed")
    monkeypatch.setattr(send_check, "check_files", boom)
    block, rules, info = enforce.decide_full(_data(tmp_path, [pic], home))
    assert block == {"tool": "SendUserFile", "soft": True,
                     "reason": send_check.SEND_UNCHECKED_REASON}
    assert rules is None
    assert [f["code"] for f in info["send_refusals"]] == ["unchecked"]
    assert isinstance(enforce.take_decide_error(), RuntimeError)


def test_fail_closed_denies_send_user_file_for_owner(tmp_path, home):
    _use(_pol().get_role("owner"))
    data = {"session": SESSION, "tool_name": "SendUserFile",
            "tool_input": {"files": ["/etc/hostname"]}}
    assert enforce.fail_closed(data) == {
        "tool": "SendUserFile", "soft": True,
        "reason": send_check.SEND_UNCHECKED_REASON}
    # Other tools keep the owner's pass-through.
    assert enforce.fail_closed({**data, "tool_name": "Read"}) is None


def test_decide_error_after_the_pre_gate_fails_closed_softly(tmp_path, home, monkeypatch):
    _use(_pol().get_role("owner"))
    pic = _file(home / "a.png")

    def boom(data):
        raise RuntimeError("decide failed")
    monkeypatch.setattr(enforce, "_decide", boom)
    block, rules, _info = enforce.decide_full(_data(tmp_path, [pic], home))
    assert block["soft"] is True
    assert block["reason"] == send_check.SEND_UNCHECKED_REASON
    assert rules is None


def test_peer_soft_answer_is_passed_on():
    enforce._cur = {"rules": None, "info": {}}
    block = {"tool": "SendUserFile", "reason": peer_auth.SOFT_DENY_REASON,
             "soft": True}
    assert enforce._send_block({"tool_input": {"files": ["a"]}}, block) == block
    assert "send_refusals" not in enforce._cur["info"]


def test_unreadable_list_is_refused_for_the_owner(tmp_path, home):
    _use(_pol().get_role("owner"))
    block = enforce.decide(_data(tmp_path, [], home))
    assert block["soft"] is True
    assert send_check.reason_code(block["reason"]) == "unreadable_list"


def test_other_tools_are_unchanged_for_the_owner(tmp_path, home):
    _use(_pol().get_role("owner"))
    creds = _file(home / ".claude" / ".credentials.json", b"{}")
    data = _data(tmp_path, None, home, tool="Read",
                 tool_input={"file_path": creds})
    assert enforce.decide(data) is None
