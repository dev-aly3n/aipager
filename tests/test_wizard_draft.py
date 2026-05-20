"""F1: wizard draft persistence (.wizard-draft.json)."""

from __future__ import annotations

import stat

import pytest

from aipager.wizard import draft as draft_mod


@pytest.fixture
def draft_at(tmp_path, monkeypatch):
    p = tmp_path / ".wizard-draft.json"
    monkeypatch.setattr(draft_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(draft_mod, "DRAFT_PATH", p)
    return p


def test_load_missing_returns_none(draft_at):
    assert draft_mod.load_draft() is None


def test_save_then_load_round_trips(draft_at):
    d = {"kind": "group", "chat_id": -100, "label": "dev", "members": [{"id": 1}]}
    draft_mod.save_draft(d)
    assert draft_mod.load_draft() == d


def test_save_is_0600(draft_at):
    draft_mod.save_draft({"x": 1})
    mode = stat.S_IMODE(draft_at.stat().st_mode)
    assert mode == 0o600


def test_clear_removes_file(draft_at):
    draft_mod.save_draft({"x": 1})
    assert draft_at.exists()
    draft_mod.clear_draft()
    assert not draft_at.exists()


def test_clear_missing_is_noop(draft_at):
    draft_mod.clear_draft()  # must not raise


def test_load_unparseable_returns_none(draft_at):
    draft_at.write_text("{not json", encoding="utf-8")
    assert draft_mod.load_draft() is None


def test_load_non_dict_returns_none(draft_at):
    draft_at.write_text("[1, 2, 3]", encoding="utf-8")
    assert draft_mod.load_draft() is None


def test_save_creates_config_dir(tmp_path, monkeypatch):
    nested = tmp_path / "sub" / "dir"
    monkeypatch.setattr(draft_mod, "CONFIG_DIR", nested)
    monkeypatch.setattr(draft_mod, "DRAFT_PATH", nested / ".wizard-draft.json")
    draft_mod.save_draft({"ok": True})
    assert draft_mod.load_draft() == {"ok": True}
