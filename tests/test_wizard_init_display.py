"""Tests for wizard.__init__ entry points and wizard.display helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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

from aipager.scope import Member, Scope, ScopeConfigError  # noqa: E402


def _stub_read_config(monkeypatch, scopes, token):
    monkeypatch.setattr("aipager.wizard.scope_io.read_config",
                        lambda: (scopes, token))


def _dm():
    return Scope(chat_id=1, kind="dm", label="owner DM",
                 members=(Member(id=1, label="owner", role="owner"),))


def _group():
    return Scope(chat_id=-100, kind="group", label="dev-team",
                 members=(Member(id=11, label="ann", role="user"),),
                 deny_tools=("Bash",))


def test_show_current_config_renders_scopes(monkeypatch, capsys):
    _stub_read_config(monkeypatch, [_dm(), _group()], "token-123:abc")
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "owner DM" in out
    assert "dev-team" in out
    assert "ann" in out and "user" in out
    assert "deny rule" in out  # the group's Bash deny


def test_show_current_config_no_scopes(monkeypatch, capsys):
    _stub_read_config(monkeypatch, [], "tok")
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "No scopes" in out


def test_show_current_config_malformed(monkeypatch, capsys):
    def _raise():
        raise ScopeConfigError("bad schema")
    monkeypatch.setattr("aipager.wizard.scope_io.read_config", _raise)
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "malformed" in out


def test_show_current_config_no_token(monkeypatch, capsys):
    _stub_read_config(monkeypatch, [], "")
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "missing" in out.lower()


def test_show_current_config_daemon_running(monkeypatch, capsys):
    _stub_read_config(monkeypatch, [_dm()], "tok")
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: 54321)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "54321" in out


def test_show_current_config_daemon_running_pid_unknown(monkeypatch, capsys):
    _stub_read_config(monkeypatch, [_dm()], "tok")
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: -1)
    display._show_current_config()
    out = capsys.readouterr().out
    assert "up" in out.lower()


def test_show_current_config_off_tty(monkeypatch):
    _stub_read_config(monkeypatch, [_dm()], "tok")
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    fake_console = MagicMock()
    fake_console.is_terminal = False
    monkeypatch.setattr(display, "console", fake_console)
    display._show_current_config()
    calls = [str(c) for c in fake_console.print.call_args_list]
    assert any("Current config" in c for c in calls)
