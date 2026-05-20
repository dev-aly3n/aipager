"""Tests for wizard.__init__ entry points and wizard.display helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager.team import Role, Rules, Team, TeamConfigError, User as TeamUser
from aipager.wizard import display


# ===== wizard.__init__ =====

def test_wizard_run_routes_to_first_run_when_no_config(monkeypatch, tmp_path):
    """No config.env → first_run_flow."""
    from aipager import wizard
    monkeypatch.setattr("aipager.wizard.CONFIG_ENV", tmp_path / "nope")
    monkeypatch.setattr("aipager.wizard._first_run_flow", lambda: 7)
    assert wizard.run() == 7


def test_wizard_run_routes_to_edit_when_config_exists(monkeypatch, tmp_path):
    from aipager import wizard
    target = tmp_path / "config.env"
    target.write_text("CLAUDE_TG_BOT_TOKEN=x\nCLAUDE_TG_CHAT_ID=1\n")
    monkeypatch.setattr("aipager.wizard.CONFIG_ENV", target)
    monkeypatch.setattr("aipager.wizard._edit_flow", lambda: 11)
    assert wizard.run() == 11


def test_wizard_main_calls_sys_exit(monkeypatch):
    from aipager import wizard
    monkeypatch.setattr("aipager.wizard.run", lambda: 5)
    with pytest.raises(SystemExit) as exc:
        wizard.main()
    assert exc.value.code == 5


# ===== display._ask =====

def test_ask_returns_answer():
    prompt = MagicMock()
    prompt.ask.return_value = "the answer"
    assert display._ask(prompt) == "the answer"


def test_ask_none_raises_keyboard_interrupt():
    prompt = MagicMock()
    prompt.ask.return_value = None  # Ctrl-C
    with pytest.raises(KeyboardInterrupt):
        display._ask(prompt)


# ===== display._spin =====

def test_spin_off_tty_returns_null_ctx(monkeypatch, capsys):
    # `is_terminal` is a read-only property; mock the console object instead.
    fake_console = MagicMock()
    fake_console.is_terminal = False
    monkeypatch.setattr(display, "console", fake_console)
    ctx = display._spin("hello")
    with ctx:
        pass
    # Off-TTY branch calls console.print
    fake_console.print.assert_called_once()


def test_spin_on_tty_returns_console_status(monkeypatch):
    fake_console = MagicMock()
    fake_console.is_terminal = True
    sentinel_status = MagicMock()
    fake_console.status.return_value = sentinel_status
    monkeypatch.setattr(display, "console", fake_console)
    ctx = display._spin("hello")
    assert ctx is sentinel_status


# ===== display._NullCtx =====

def test_null_ctx_is_a_proper_context_manager():
    ctx = display._NullCtx()
    assert ctx.__enter__() is ctx
    assert ctx.__exit__(None, None, None) is False


# ===== display._show_current_config =====

def test_show_current_config_team_mode(monkeypatch, capsys):
    monkeypatch.setattr(display, "_read_env_file",
                        lambda: ("token-123:abc", "12345"))
    team = Team(
        group_id=-100,
        users={1: TeamUser(id=1, label="admin", role=Role.ADMIN)},
        rules=Rules(deny_tools=("Bash",)),
    )
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: team)
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "Team" in out
    assert "admin" in out
    assert "Bash" in out


def test_show_current_config_personal_mode(monkeypatch, capsys):
    """No team.yaml → personal mode rendering."""
    monkeypatch.setattr(display, "_read_env_file",
                        lambda: ("tok", "12345"))
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: None)
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "Personal" in out


def test_show_current_config_malformed_team_yaml(monkeypatch, capsys):
    monkeypatch.setattr(display, "_read_env_file",
                        lambda: ("tok", "12345"))
    def _raise(p=None):
        raise TeamConfigError("malformed")
    monkeypatch.setattr("aipager.team.load_team", _raise)
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "malformed" in out


def test_show_current_config_no_token(monkeypatch, capsys):
    """When config.env has no token, show missing marker."""
    monkeypatch.setattr(display, "_read_env_file",
                        lambda: ("", "12345"))
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: None)
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "missing" in out.lower()


def test_show_current_config_daemon_running(monkeypatch, capsys):
    monkeypatch.setattr(display, "_read_env_file", lambda: ("tok", "12345"))
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: None)
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: 54321)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "54321" in out


def test_show_current_config_daemon_running_pid_unknown(monkeypatch, capsys):
    monkeypatch.setattr(display, "_read_env_file", lambda: ("tok", "12345"))
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: None)
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: -1)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "up" in out.lower()


def test_show_current_config_off_tty(monkeypatch):
    monkeypatch.setattr(display, "_read_env_file", lambda: ("tok", "12345"))
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: None)
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    fake_console = MagicMock()
    fake_console.is_terminal = False
    monkeypatch.setattr(display, "console", fake_console)
    display._show_current_config()
    # In off-TTY mode, it calls console.print with "Current config" + body
    calls = [str(c) for c in fake_console.print.call_args_list]
    assert any("Current config" in c for c in calls)
