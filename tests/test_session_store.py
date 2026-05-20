"""Phase D: session folder + SESSION.md generation."""

from __future__ import annotations

import json

from aipager import session_store as ss
from aipager.policy import load_policy
from aipager.scope import Member, Scope


def _scope():
    return Scope(
        chat_id=-4152307515, kind="group", label="dev",
        members=(
            Member(id=1, label="aly", role="owner"),
            Member(id=2, label="bob", role="user", deny_tools=("Bash",)),
            Member(id=3, label="ro", role="read_only"),
        ),
    )


def test_session_folder_path():
    p = ss.session_folder(-4152307515, "group", "jim")
    assert p.name == "jim"
    assert p.parent.name == "group-4152307515"


def test_build_session_md_roster():
    md = ss.build_session_md(_scope(), load_policy(), "jim")
    assert "# Session: jim" in md
    assert "**aly** (owner — can call any tool)" in md
    assert "**bob** (user — denied: Bash)" in md
    assert "**ro** (read_only — observer; cannot drive prompts)" in md
    # routing-hint + blocked-paths notes present
    assert "[via Telegram · @X · role:Y]" in md
    assert "~/.config/aipager/**" in md


def test_write_session_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "SESSIONS_ROOT", tmp_path)
    body = ss.write_session_files(_scope(), load_policy(), "jim")
    folder = tmp_path / "group-4152307515" / "jim"
    assert (folder / "SESSION.md").read_text() == body
    meta = json.loads((folder / ".aipager" / "meta.json").read_text())
    assert meta["scope_chat_id"] == -4152307515
    assert meta["scope_kind"] == "group"
    assert meta["members"] == [1, 2, 3]


def test_dm_scope_folder():
    p = ss.session_folder(256113222, "dm", "t1")
    assert p.parent.name == "dm-256113222"
