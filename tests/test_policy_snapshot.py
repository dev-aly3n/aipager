"""Phase E: per-session policy snapshot."""

from __future__ import annotations

import json

from aipager import policy_snapshot as ps
from aipager import safety
from aipager.policy import load_policy
from aipager.scope import Member, Scope


def _policy():
    return load_policy()


def test_owner_bypass():
    pol = _policy()
    snap = ps.resolve_snapshot(pol.get_role("owner"), None, None)
    assert snap["bypass_safety"] is True


def test_user_gets_floor():
    pol = _policy()
    scope = Scope(chat_id=-1, kind="group", label="g",
                  members=(), deny_tools=("Bash",))
    member = Member(id=2, label="bob", role="user", deny_tools=("WebFetch",))
    snap = ps.resolve_snapshot(pol.get_role("user"), scope, member)
    assert snap["bypass_safety"] is False
    # safety floor present
    assert "~/.claude/**" in snap["deny_paths_no_access"]
    assert r"\bclaude\b" in snap["deny_bash_patterns"]
    # scope + member tool denies unioned
    assert "Bash" in snap["deny_tools"]
    assert "WebFetch" in snap["deny_tools"]


def test_admin_keeps_floor_but_skips_role_denies():
    pol = _policy()
    scope = Scope(chat_id=-1, kind="group", label="g",
                  members=(), deny_tools=("Bash",))
    snap = ps.resolve_snapshot(pol.get_role("admin"), scope, None)
    # admin bypasses role/scope deny_tools …
    assert "Bash" not in snap["deny_tools"]
    # … but the hard floor still applies (admin isn't owner)
    assert "~/.claude/**" in snap["deny_paths_no_access"]
    assert snap["bypass_safety"] is False


def test_write_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None)
    p = tmp_path / "claude-x__d1.json"
    data = json.loads(p.read_text())
    assert data["origin"] == "telegram"
    assert "~/.claude/**" in data["deny_paths_no_access"]
    ps.clear_snapshot("claude-x__d1")
    assert not p.exists()


def test_floor_constants_match():
    snap = ps.resolve_snapshot(None, None, None)
    assert set(safety.DENY_PATHS_NO_ACCESS) <= set(snap["deny_paths_no_access"])
