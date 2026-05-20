"""F1: wizard ↔ aipager.yaml glue (scope_io)."""

from __future__ import annotations

import stat

import pytest

from aipager import scope as _scope
from aipager.scope import Member, Scope
from aipager.wizard import scope_io


@pytest.fixture
def cfg_at(tmp_path, monkeypatch):
    p = tmp_path / "aipager.yaml"
    monkeypatch.setattr(_scope, "CONFIG_PATH", p)
    return p


def _dm(chat_id, label="owner", role="owner"):
    return Scope(
        chat_id=chat_id, kind="dm", label=f"{label} DM",
        members=(Member(id=chat_id, label=label, role=role),),
    )


def _group(chat_id, label="dev", members=(("bob", "user"),)):
    return Scope(
        chat_id=chat_id, kind="group", label=label,
        members=tuple(Member(id=i + 1, label=lbl, role=r)
                      for i, (lbl, r) in enumerate(members)),
    )


def test_config_exists(cfg_at):
    assert scope_io.config_exists() is False
    cfg_at.write_text("x", encoding="utf-8")
    assert scope_io.config_exists() is True


def test_read_config_absent_returns_empty(cfg_at):
    assert scope_io.read_config() == ([], "")


def test_commit_scope_appends_then_round_trips(cfg_at):
    scope_io.commit_scope(_dm(100), "TOK")
    scopes, token = scope_io.read_config()
    assert token == "TOK"
    assert [s.chat_id for s in scopes] == [100]


def test_commit_scope_replaces_by_chat_id(cfg_at):
    scope_io.commit_scope(_dm(100, label="owner"), "TOK")
    scope_io.commit_scope(_group(-200), "TOK")
    # Re-commit chat 100 with a different label → replace, not duplicate.
    scope_io.commit_scope(_dm(100, label="owner", role="admin"), "TOK")
    scopes, _ = scope_io.read_config()
    assert sorted(s.chat_id for s in scopes) == [-200, 100]
    dm = next(s for s in scopes if s.chat_id == 100)
    assert dm.members[0].role == "admin"


def test_commit_scope_keeps_existing_token_when_blank(cfg_at):
    scope_io.commit_scope(_dm(100), "TOK")
    scope_io.commit_scope(_group(-200), "")  # blank token → keep existing
    _, token = scope_io.read_config()
    assert token == "TOK"


def test_commit_scope_writes_0600(cfg_at):
    scope_io.commit_scope(_dm(100), "TOK")
    assert stat.S_IMODE(cfg_at.stat().st_mode) == 0o600


def test_replace_scopes_overwrites(cfg_at):
    scope_io.commit_scope(_dm(100), "TOK")
    scope_io.replace_scopes([_group(-200)], "TOK2")
    scopes, token = scope_io.read_config()
    assert token == "TOK2"
    assert [s.chat_id for s in scopes] == [-200]


def test_remove_scope(cfg_at):
    scope_io.commit_scope(_dm(100), "TOK")
    scope_io.commit_scope(_group(-200), "TOK")
    assert scope_io.remove_scope(-200) is True
    scopes, _ = scope_io.read_config()
    assert [s.chat_id for s in scopes] == [100]


def test_remove_scope_missing_returns_false(cfg_at):
    scope_io.commit_scope(_dm(100), "TOK")
    assert scope_io.remove_scope(999) is False


def test_remove_last_scope_refused(cfg_at):
    scope_io.commit_scope(_dm(100), "TOK")
    # Removing the only scope would leave aipager.yaml invalid → refuse.
    assert scope_io.remove_scope(100) is False
    assert [s.chat_id for s in scope_io.read_config()[0]] == [100]
