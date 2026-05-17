"""Tests for aipager.preflight — failure paths exit with code 2."""

import pytest

from aipager import preflight


def test_require_config_missing_both(monkeypatch, capsys):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    with pytest.raises(SystemExit) as exc:
        preflight.require_config()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "CLAUDE_TG_BOT_TOKEN" in err
    assert "CLAUDE_TG_CHAT_ID" in err
    assert "aipager config" in err


def test_require_config_missing_only_token(monkeypatch, capsys):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "1234")
    with pytest.raises(SystemExit) as exc:
        preflight.require_config()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "CLAUDE_TG_BOT_TOKEN" in err
    assert "CLAUDE_TG_CHAT_ID" not in err


def test_require_config_all_set(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "abc")
    monkeypatch.setattr("aipager.config.CHAT_ID", "1234")
    # Should not raise
    preflight.require_config()


def test_require_claude_missing(monkeypatch, capsys):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as exc:
        preflight.require_claude()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Claude Code CLI not found" in err


def test_require_claude_present(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/claude")
    assert preflight.require_claude() == "/usr/bin/claude"


def test_require_daemon_missing(monkeypatch, tmp_path, capsys):
    fake_socket = tmp_path / "nope.sock"
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(fake_socket))
    with pytest.raises(SystemExit) as exc:
        preflight.require_daemon()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "daemon isn't running" in err
    assert "aipager start" in err


def test_require_daemon_present(monkeypatch, tmp_path):
    fake_socket = tmp_path / "exists.sock"
    fake_socket.touch()
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(fake_socket))
    preflight.require_daemon()  # no exit
