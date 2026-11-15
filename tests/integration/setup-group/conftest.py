"""Fixture for the setup-group black-box tests (helpers live in
``setup_group_support``)."""

from __future__ import annotations

import builtins
import getpass
import logging
import socket
import subprocess

import pytest

from setup_group_support import Env, FakeTelegram


@pytest.fixture
def env(tmp_path, monkeypatch, capsys, caplog):
    import aipager.wizard.daemon_io as daemon_io
    import questionary

    caplog.set_level(logging.DEBUG)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    e = Env(tmp=tmp_path, home=home, tg=FakeTelegram(),
            monkeypatch=monkeypatch, capsys=capsys, caplog=caplog)

    # Telegram: the lowest seam, so the real _http_json parses answers.
    monkeypatch.setattr(
        "aipager.wizard.telegram_api.urllib.request.urlopen", e.tg)

    # daemon seams (entrypoints.md)
    def _detect():
        e.detects.append(1)
        if isinstance(e.daemon_pid, BaseException):
            raise e.daemon_pid
        return e.daemon_pid
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", _detect)

    def _live_reload():
        e.reloads.append(1)
        if isinstance(e.reload_outcome, BaseException):
            raise e.reload_outcome
        return e.reload_outcome
    monkeypatch.setattr(daemon_io, "_live_reload", _live_reload)

    # no prompts ever
    def _trap(name):
        def _t(*a, **k):
            raise AssertionError(f"setup group reached a prompt: {name}")
        return _t
    for n in ("text", "password", "select", "confirm", "checkbox",
              "autocomplete", "path", "rawselect", "prompt"):
        monkeypatch.setattr(questionary, n, _trap(f"questionary.{n}"))
    monkeypatch.setattr(builtins, "input", _trap("input"))
    monkeypatch.setattr(getpass, "getpass", _trap("getpass"))

    # nothing real: no socket connects, no subprocess
    def _no_connect(self, *a, **k):
        raise ConnectionRefusedError("sockets are refused in these tests")
    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    monkeypatch.setattr(socket.socket, "connect_ex",
                        lambda self, *a, **k: 111)

    class _NoPopen:
        def __init__(self, args, *a, **k):
            raise FileNotFoundError(f"no subprocess in these tests: {args!r}")
    monkeypatch.setattr(subprocess, "Popen", _NoPopen)

    monkeypatch.delenv("CLAUDE_TG_BOT_TOKEN", raising=False)
    yield e
