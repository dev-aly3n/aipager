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


def _stale_busy_timeout_in_a_fresh_process(tmp_path, extra_env=None) -> float:
    """``config.STALE_BUSY_TIMEOUT`` as a fresh daemon reads it: a new
    interpreter with a throwaway HOME and cwd and no AIPAGER_/CLAUDE_TG_
    or STALE_BUSY_TIMEOUT in its environment (never the real home)."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_TG_", "AIPAGER_")) and k != "STALE_BUSY_TIMEOUT"}
    env.update(HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / ".config"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(repo))
    env.update(extra_env or {})
    out = subprocess.run(
        [sys.executable, "-B", "-c",
         "from aipager import config; print(config.STALE_BUSY_TIMEOUT)"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return float(out.stdout.strip())


def test_the_still_working_note_waits_two_hours_by_default(tmp_path):
    """Operator, 2026-10-12: the "still working (quiet for N min)" note
    after 10 minutes was noise; it waits 2 hours of silence now."""
    assert _stale_busy_timeout_in_a_fresh_process(tmp_path) == 7200.0


def test_the_environment_still_overrides_the_wait(tmp_path):
    assert _stale_busy_timeout_in_a_fresh_process(
        tmp_path, {"STALE_BUSY_TIMEOUT": "900"}) == 900.0


# ----- _default_socket_path -----

def test_socket_path_env_override_wins(monkeypatch):
    monkeypatch.setenv("AIPAGER_SOCKET_PATH", "/custom/aipager.sock")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert cfg._default_socket_path() == "/custom/aipager.sock"


def test_socket_path_uses_xdg_runtime_dir_when_set(monkeypatch):
    monkeypatch.delenv("AIPAGER_SOCKET_PATH", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert cfg._default_socket_path() == "/run/user/1000/aipager.sock"


def test_socket_path_falls_back_to_tmp_when_unset(monkeypatch):
    monkeypatch.delenv("AIPAGER_SOCKET_PATH", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert cfg._default_socket_path() == "/tmp/aipager.sock"
