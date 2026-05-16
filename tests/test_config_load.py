"""Tests for aipager.config — env file loader with XDG / cwd fallback."""

import os

from aipager import config as cfg


def _clear(key: str) -> None:
    os.environ.pop(key, None)


def test_xdg_takes_precedence(tmp_path, monkeypatch):
    xdg = tmp_path / "config.env"
    proj = tmp_path / ".env"
    xdg.write_text("TEST_KEY_XDG_PREC=from_xdg\n")
    proj.write_text("TEST_KEY_XDG_PREC=from_project\n")
    monkeypatch.setattr(cfg, "_XDG_CONFIG", xdg)
    monkeypatch.setattr(cfg, "_PROJECT_DOTENV", proj)
    _clear("TEST_KEY_XDG_PREC")
    cfg._load_env_file()
    assert os.environ.get("TEST_KEY_XDG_PREC") == "from_xdg"


def test_falls_back_to_project_env(tmp_path, monkeypatch):
    proj = tmp_path / ".env"
    proj.write_text("TEST_KEY_FALLBACK=from_project\n")
    monkeypatch.setattr(cfg, "_XDG_CONFIG", tmp_path / "missing.env")
    monkeypatch.setattr(cfg, "_PROJECT_DOTENV", proj)
    _clear("TEST_KEY_FALLBACK")
    cfg._load_env_file()
    assert os.environ.get("TEST_KEY_FALLBACK") == "from_project"


def test_no_files_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "_XDG_CONFIG", tmp_path / "absent_xdg")
    monkeypatch.setattr(cfg, "_PROJECT_DOTENV", tmp_path / "absent_proj")
    _clear("TEST_KEY_NONE")
    cfg._load_env_file()
    assert os.environ.get("TEST_KEY_NONE") is None


def test_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    proj = tmp_path / ".env"
    proj.write_text("TEST_KEY_PROTECTED=from_file\n")
    monkeypatch.setattr(cfg, "_XDG_CONFIG", tmp_path / "missing")
    monkeypatch.setattr(cfg, "_PROJECT_DOTENV", proj)
    os.environ["TEST_KEY_PROTECTED"] = "from_env"
    try:
        cfg._load_env_file()
        assert os.environ["TEST_KEY_PROTECTED"] == "from_env"
    finally:
        _clear("TEST_KEY_PROTECTED")


def test_skips_comments_and_blank_lines(tmp_path, monkeypatch):
    proj = tmp_path / ".env"
    proj.write_text("# comment line\n\nTEST_KEY_COMMENT=value\n")
    monkeypatch.setattr(cfg, "_XDG_CONFIG", tmp_path / "missing")
    monkeypatch.setattr(cfg, "_PROJECT_DOTENV", proj)
    _clear("TEST_KEY_COMMENT")
    cfg._load_env_file()
    assert os.environ.get("TEST_KEY_COMMENT") == "value"


def test_strips_quotes(tmp_path, monkeypatch):
    proj = tmp_path / ".env"
    proj.write_text("TEST_KEY_QUOTED=\"quoted value\"\n")
    monkeypatch.setattr(cfg, "_XDG_CONFIG", tmp_path / "missing")
    monkeypatch.setattr(cfg, "_PROJECT_DOTENV", proj)
    _clear("TEST_KEY_QUOTED")
    cfg._load_env_file()
    assert os.environ.get("TEST_KEY_QUOTED") == "quoted value"
