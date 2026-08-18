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


# ---- style_text field (item 6.2) -----------------------------------------

def test_resolve_snapshot_style_text_defaults_empty():
    snap = ps.resolve_snapshot(None, None, None)
    assert snap["style_text"] == ""


def test_resolve_snapshot_carries_style_text():
    snap = ps.resolve_snapshot(None, None, None, style_text="Keep it short.")
    assert snap["style_text"] == "Keep it short."


def test_write_snapshot_persists_style_text(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None,
                      style_text="Use simple, everyday words.")
    data = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data["style_text"] == "Use simple, everyday words."


def test_write_snapshot_style_text_defaults_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None)
    data = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data["style_text"] == ""


# ---- reply_context field (design.md "reply context") ----------------------

def test_resolve_snapshot_reply_context_defaults_empty():
    snap = ps.resolve_snapshot(None, None, None)
    assert snap["reply_context"] == ""


def test_resolve_snapshot_carries_reply_context():
    snap = ps.resolve_snapshot(None, None, None, reply_context="pointing at msg X")
    assert snap["reply_context"] == "pointing at msg X"


def test_write_snapshot_persists_reply_context(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None,
                      reply_context="pointing at an older message")
    data = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data["reply_context"] == "pointing at an older message"


def test_write_snapshot_reply_context_defaults_empty_and_clears_a_prior_value(
    tmp_path, monkeypatch,
):
    """The staleness guard (design.md): a caller that omits reply_context
    must overwrite a PRIOR turn's non-empty value with "", never leave
    it in place. Named specifically so it can serve as the mutation
    target for write_snapshot's own default."""
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    pol = _policy()
    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None,
                      reply_context="stale pointer from turn 1")
    data1 = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data1["reply_context"] == "stale pointer from turn 1"

    ps.write_snapshot("claude-x__d1", pol.get_role("user"), None, None)
    data2 = json.loads((tmp_path / "claude-x__d1.json").read_text())
    assert data2["reply_context"] == ""


# ---- reply-context /tmp file (design.md Part 2/5) --------------------------

def test_reply_context_path_uses_the_documented_filename():
    assert ps.reply_context_path("claude-jim") == (
        __import__("pathlib").Path("/tmp/claude-reply-claude-jim.txt")
    )


def test_write_reply_context_file_is_atomic_0600_and_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    ps.write_reply_context_file("claude-x", "Header:", "the full replied-to text")
    p = tmp_path / "claude-x.txt"
    assert p.exists()
    assert oct(p.stat().st_mode)[-3:] == "600"
    content = p.read_text()
    assert "Header:" in content
    assert "the full replied-to text" in content


def test_write_reply_context_file_caps_full_text_at_4000_chars(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    long_text = "x" * 6000
    ps.write_reply_context_file("claude-x", "", long_text)
    content = (tmp_path / "claude-x.txt").read_text()
    assert len(content) == 4000
    assert content == "x" * 4000


def test_clear_reply_context_file_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    ps.write_reply_context_file("claude-x", "h", "full text")
    assert (tmp_path / "claude-x.txt").exists()
    ps.clear_reply_context_file("claude-x")
    assert not (tmp_path / "claude-x.txt").exists()


def test_clear_reply_context_file_is_a_noop_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    ps.clear_reply_context_file("claude-never-existed")  # must not raise


def test_clear_session_files_removes_both_policy_and_reply_context_files(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")
    pol = _policy()
    ps.write_snapshot("claude-x", pol.get_role("user"), None, None)
    ps.write_reply_context_file("claude-x", "h", "full text")
    assert (tmp_path / "claude-x.policy.json").exists()
    assert (tmp_path / "claude-x.reply.txt").exists()

    ps.clear_session_files("claude-x")

    assert not (tmp_path / "claude-x.policy.json").exists()
    assert not (tmp_path / "claude-x.reply.txt").exists()
