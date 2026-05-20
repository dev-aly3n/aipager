"""Tests for wizard.first_run — the interactive setup flow.

We stub ``_ask`` to return canned answers and mock the Telegram-API
helpers + sub-flows so each step can be exercised end-to-end without
a TTY.
"""

from __future__ import annotations


import pytest

from aipager.wizard import first_run


def _stub_ask(monkeypatch, answers):
    """Make wizard.first_run._ask pop the next canned answer."""
    queue = iter(answers)
    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers")
    monkeypatch.setattr(first_run, "_ask", _ask)
    return _ask


# ---- _step_token ---------------------------------------------------------

def test_step_token_happy_path(monkeypatch):
    _stub_ask(monkeypatch, ["123456:tokenABCDEFGHIJKLMNOPQRSTUVWX"])
    monkeypatch.setattr(first_run, "_verify_token",
                        lambda t: {"username": "bot_name"})
    # _spin is a context manager; replace with no-op
    monkeypatch.setattr(first_run, "_spin",
                        lambda msg: __import__("contextlib").nullcontext())
    token, username = first_run._step_token()
    assert username == "bot_name"
    assert token == "123456:tokenABCDEFGHIJKLMNOPQRSTUVWX"


def test_step_token_retries_on_empty(monkeypatch):
    _stub_ask(monkeypatch, ["", "123456:abc-_def-_ghijklmnopqrstuvwxyz"])
    monkeypatch.setattr(first_run, "_verify_token",
                        lambda t: {"username": "bot_name"})
    monkeypatch.setattr(first_run, "_spin",
                        lambda msg: __import__("contextlib").nullcontext())
    token, _ = first_run._step_token()
    assert token


def test_step_token_retries_on_invalid(monkeypatch):
    _stub_ask(monkeypatch, [
        "bad-token", "123456:abc-_def-_ghijklmnopqrstuvwxyz",
    ])
    calls = []
    def _verify(t):
        calls.append(t)
        return None if len(calls) == 1 else {"username": "bot"}
    monkeypatch.setattr(first_run, "_verify_token", _verify)
    monkeypatch.setattr(first_run, "_spin",
                        lambda msg: __import__("contextlib").nullcontext())
    token, _ = first_run._step_token()
    assert len(calls) == 2  # one failure, one success
    assert token


# ---- _step_chat_id -------------------------------------------------------

def test_step_chat_id_manual_personal_success(monkeypatch):
    _stub_ask(monkeypatch, [
        "manual",   # how to find
        "12345",    # chat id
        True,       # "did the test message arrive?"
    ])
    monkeypatch.setattr(first_run, "_test_send",
                        lambda t, c: (True, ""))
    monkeypatch.setattr(first_run, "_spin",
                        lambda msg: __import__("contextlib").nullcontext())
    cid = first_run._step_chat_id("tok", "bot_name", mode="personal")
    assert cid == 12345


def test_step_chat_id_manual_team_negative_required(monkeypatch):
    """In team mode, positive chat ID is rejected with retry."""
    _stub_ask(monkeypatch, [
        "manual", "12345",    # positive — rejected
        "manual", "-100",     # negative — accepted
        True,                 # confirmed
    ])
    monkeypatch.setattr(first_run, "_test_send",
                        lambda t, c: (True, ""))
    monkeypatch.setattr(first_run, "_spin",
                        lambda msg: __import__("contextlib").nullcontext())
    cid = first_run._step_chat_id("tok", "bot_name", mode="team")
    assert cid == -100


def test_step_chat_id_auto_detect_personal(monkeypatch):
    _stub_ask(monkeypatch, [
        "auto",     # method
        True,       # "Sent — continue?"
        True,       # "Did the test message arrive?"
    ])
    monkeypatch.setattr(first_run, "_fetch_id_from_updates",
                        lambda t, *, want: (42, "alice", None))
    monkeypatch.setattr(first_run, "_test_send",
                        lambda t, c: (True, ""))
    monkeypatch.setattr(first_run, "_spin",
                        lambda msg: __import__("contextlib").nullcontext())
    cid = first_run._step_chat_id("tok", "bot_name", mode="personal")
    assert cid == 42


def test_step_chat_id_auto_detect_no_result(monkeypatch):
    """No update found → retry; then manual fallback succeeds."""
    _stub_ask(monkeypatch, [
        "auto", True,           # first attempt
        "manual", "99",         # fall back to manual
        True,                   # confirmed
    ])
    monkeypatch.setattr(first_run, "_fetch_id_from_updates",
                        lambda t, *, want: (None, None, None))
    monkeypatch.setattr(first_run, "_test_send",
                        lambda t, c: (True, ""))
    monkeypatch.setattr(first_run, "_spin",
                        lambda msg: __import__("contextlib").nullcontext())
    cid = first_run._step_chat_id("tok", "bot_name", mode="personal")
    assert cid == 99


def test_step_chat_id_chat_not_found_then_retry(monkeypatch):
    """sendMessage returns 'chat not found' → DM prompt → retry succeeds."""
    _stub_ask(monkeypatch, [
        "manual", "5",  # chat id
        True,           # I've tapped Start — retry
    ])
    calls = []
    def _send(t, c):
        calls.append(c)
        if len(calls) == 1:
            return False, "Bad Request: chat not found"
        return True, ""
    monkeypatch.setattr(first_run, "_test_send", _send)
    monkeypatch.setattr(first_run, "_spin",
                        lambda msg: __import__("contextlib").nullcontext())
    cid = first_run._step_chat_id("tok", "bot_name", mode="personal")
    assert cid == 5


# ---- _step_write_env ----------------------------------------------------

def test_step_write_env_writes_new_file(tmp_path, monkeypatch):
    monkeypatch.setattr(first_run, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(first_run, "CONFIG_ENV", tmp_path / "config.env")
    first_run._step_write_env("tok", 42)
    assert (tmp_path / "config.env").exists()
    content = (tmp_path / "config.env").read_text()
    assert "tok" in content
    assert "42" in content


def test_step_write_env_overwrite_confirmed(tmp_path, monkeypatch):
    target = tmp_path / "config.env"
    target.write_text("CLAUDE_TG_BOT_TOKEN=OLD\nCLAUDE_TG_CHAT_ID=1\n")
    monkeypatch.setattr(first_run, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(first_run, "CONFIG_ENV", target)
    _stub_ask(monkeypatch, [True])  # confirm overwrite
    first_run._step_write_env("NEW", 99)
    assert "NEW" in target.read_text()


def test_step_write_env_overwrite_declined_keeps_old(tmp_path, monkeypatch):
    target = tmp_path / "config.env"
    target.write_text("CLAUDE_TG_BOT_TOKEN=OLD\nCLAUDE_TG_CHAT_ID=1\n")
    monkeypatch.setattr(first_run, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(first_run, "CONFIG_ENV", target)
    _stub_ask(monkeypatch, [False])  # decline overwrite
    first_run._step_write_env("NEW", 99)
    # Old content preserved
    assert "OLD" in target.read_text()


def test_step_write_env_chmod_failure_warns(tmp_path, monkeypatch, capsys):
    target = tmp_path / "config.env"
    monkeypatch.setattr(first_run, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(first_run, "CONFIG_ENV", target)
    monkeypatch.setattr(first_run.os, "chmod",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("EROFS")))
    first_run._step_write_env("tok", 42)
    assert target.exists()


def test_step_write_env_cannot_create_dir_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(first_run, "CONFIG_DIR", tmp_path / "nope")
    monkeypatch.setattr(first_run, "CONFIG_ENV", tmp_path / "nope" / "config.env")
    monkeypatch.setattr(first_run.os, "chmod", lambda *a, **k: None)
    # Force mkdir failure
    real_mkdir = first_run.CONFIG_DIR.__class__.mkdir
    def _boom(self, *a, **k):
        raise OSError("EROFS")
    monkeypatch.setattr(first_run.CONFIG_DIR.__class__, "mkdir", _boom)
    try:
        with pytest.raises(OSError):
            first_run._step_write_env("tok", 42)
    finally:
        monkeypatch.setattr(first_run.CONFIG_DIR.__class__, "mkdir", real_mkdir)


# ---- _step_pick_mode -----------------------------------------------------

def test_step_pick_mode_personal(monkeypatch):
    _stub_ask(monkeypatch, ["personal"])
    assert first_run._step_pick_mode() == "personal"


def test_step_pick_mode_team_confirmed(monkeypatch):
    _stub_ask(monkeypatch, ["team", True])  # accepts trust warning
    # _show_team_warning_panel lives in team_setup module — stub it
    monkeypatch.setattr(first_run, "_show_team_warning_panel", lambda: None)
    assert first_run._step_pick_mode() == "team"


def test_step_pick_mode_team_then_decline_falls_back(monkeypatch):
    _stub_ask(monkeypatch, ["team", False])
    monkeypatch.setattr(first_run, "_show_team_warning_panel", lambda: None)
    assert first_run._step_pick_mode() == "personal"


# ---- _completion_screen --------------------------------------------------

def test_completion_screen_prints(capsys):
    first_run._completion_screen()
    out = capsys.readouterr().out
    assert "Setup complete" in out
    assert "aipager start" in out


# ---- _first_run_flow -----------------------------------------------------

def test_first_run_flow_personal_happy_path(monkeypatch, tmp_path):
    monkeypatch.setattr(first_run, "_step_token",
                        lambda step_label: ("tok", "bot_username"))
    monkeypatch.setattr(first_run, "_step_pick_mode",
                        lambda step_label: "personal")
    monkeypatch.setattr(first_run, "_step_chat_id",
                        lambda *a, **k: 42)
    monkeypatch.setattr(first_run, "_step_write_env",
                        lambda *a, **k: None)
    monkeypatch.setattr(first_run, "_step_deps",
                        lambda step_label: True)
    monkeypatch.setattr(first_run, "_step_settings",
                        lambda step_label: None)
    monkeypatch.setattr(first_run, "_completion_screen", lambda: None)
    rc = first_run._first_run_flow()
    assert rc is None or rc == 0


def test_first_run_flow_team_happy_path(monkeypatch):
    monkeypatch.setattr(first_run, "_step_token",
                        lambda step_label: ("tok", "bot_username"))
    monkeypatch.setattr(first_run, "_step_pick_mode",
                        lambda step_label: "team")
    monkeypatch.setattr(first_run, "_step_chat_id",
                        lambda *a, **k: -100)
    monkeypatch.setattr(first_run, "_step_write_env",
                        lambda *a, **k: None)
    monkeypatch.setattr(first_run, "_step_team_setup",
                        lambda *a, **k: None)
    monkeypatch.setattr(first_run, "_step_deps",
                        lambda step_label: True)
    monkeypatch.setattr(first_run, "_step_settings",
                        lambda step_label: None)
    monkeypatch.setattr(first_run, "_completion_screen", lambda: None)
    rc = first_run._first_run_flow()
    assert rc is None or rc == 0


def test_first_run_flow_deps_missing_user_continues(monkeypatch):
    monkeypatch.setattr(first_run, "_step_token",
                        lambda step_label: ("tok", "bot_username"))
    monkeypatch.setattr(first_run, "_step_pick_mode",
                        lambda step_label: "personal")
    monkeypatch.setattr(first_run, "_step_chat_id",
                        lambda *a, **k: 42)
    monkeypatch.setattr(first_run, "_step_write_env",
                        lambda *a, **k: None)
    monkeypatch.setattr(first_run, "_step_deps",
                        lambda step_label: False)  # deps missing
    _stub_ask(monkeypatch, [True])  # "Continue anyway?" yes
    monkeypatch.setattr(first_run, "_step_settings",
                        lambda step_label: None)
    monkeypatch.setattr(first_run, "_completion_screen", lambda: None)
    rc = first_run._first_run_flow()
    assert rc is None or rc == 0


def test_first_run_flow_deps_missing_user_aborts(monkeypatch):
    monkeypatch.setattr(first_run, "_step_token",
                        lambda step_label: ("tok", "bot_username"))
    monkeypatch.setattr(first_run, "_step_pick_mode",
                        lambda step_label: "personal")
    monkeypatch.setattr(first_run, "_step_chat_id",
                        lambda *a, **k: 42)
    monkeypatch.setattr(first_run, "_step_write_env",
                        lambda *a, **k: None)
    monkeypatch.setattr(first_run, "_step_deps",
                        lambda step_label: False)
    _stub_ask(monkeypatch, [False])  # "Continue anyway?" no
    rc = first_run._first_run_flow()
    assert rc == 2


def test_first_run_flow_keyboard_interrupt_returns_130(monkeypatch):
    def _boom(step_label):
        raise KeyboardInterrupt
    monkeypatch.setattr(first_run, "_step_token", _boom)
    rc = first_run._first_run_flow()
    assert rc == 130


def test_first_run_flow_value_error_returns_1(monkeypatch):
    def _boom(step_label):
        raise ValueError("bad")
    monkeypatch.setattr(first_run, "_step_token", _boom)
    rc = first_run._first_run_flow()
    assert rc == 1


def test_first_run_flow_os_error_returns_1(monkeypatch):
    def _boom(step_label):
        raise OSError("EROFS")
    monkeypatch.setattr(first_run, "_step_token", _boom)
    rc = first_run._first_run_flow()
    assert rc == 1
