"""Tests for aipager.preflight — failure paths exit with code 2."""

import pytest

from aipager import claude_resolve, preflight


def test_require_config_missing_both(monkeypatch, capsys):
    monkeypatch.setattr("aipager.config.SCOPES", None, raising=False)
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
    monkeypatch.setattr("aipager.config.SCOPES", None, raising=False)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "1234")
    with pytest.raises(SystemExit) as exc:
        preflight.require_config()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "CLAUDE_TG_BOT_TOKEN" in err
    assert "CLAUDE_TG_CHAT_ID" not in err


def test_require_config_all_set(monkeypatch):
    monkeypatch.setattr("aipager.config.SCOPES", None, raising=False)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "abc")
    monkeypatch.setattr("aipager.config.CHAT_ID", "1234")
    # Should not raise
    preflight.require_config()


def test_require_config_v2_scopes_satisfy_without_chat_id(monkeypatch):
    """v2: scopes + token pass even when CHAT_ID is empty (config.env retired)."""
    monkeypatch.setattr("aipager.config.SCOPES", [object()], raising=False)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "abc")
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    preflight.require_config()  # should not raise


def test_require_config_v2_scopes_but_no_token_fails(monkeypatch, capsys):
    monkeypatch.setattr("aipager.config.SCOPES", [object()], raising=False)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    with pytest.raises(SystemExit) as exc:
        preflight.require_config()
    assert exc.value.code == 2
    assert "CLAUDE_TG_BOT_TOKEN" in capsys.readouterr().err


def test_require_claude_missing(monkeypatch, capsys):
    # claude_resolve's discovery is patched to no-candidates by the
    # autouse `_no_real_claude_candidates` fixture — require_claude()
    # must therefore already exit(2) with no further mocking needed.
    with pytest.raises(SystemExit) as exc:
        preflight.require_claude()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Claude Code CLI not found" in err


def test_require_claude_present(monkeypatch):
    def _fake_candidates():
        return [("/usr/bin/claude", 4)]

    def _fake_verify(path):
        return (
            claude_resolve.ClaudeInstall(path=path, realpath=path, version="2.1.235"),
            "",
        )

    monkeypatch.setattr(claude_resolve, "_candidate_paths", _fake_candidates)
    monkeypatch.setattr(claude_resolve, "_verify_candidate", _fake_verify)
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
