"""Fixture for the agent-setup black-box tests (helpers live in
``agent_setup_support``)."""

from __future__ import annotations

import builtins
import getpass
import logging
import socket
import subprocess
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_setup_support import BOT, CHAT, Call, Env, FakeTelegram


@pytest.fixture
def env(tmp_path, monkeypatch, capsys, caplog):
    import aipager.errors as errors_mod
    import aipager.service as svc
    import aipager.wizard.daemon_io as daemon_io
    import questionary
    from tests.conftest import REAL_REQUIRE_INTERACTIVE

    caplog.set_level(logging.DEBUG)
    input_dir = tmp_path / "input"
    bin_dir = input_dir / "bin"
    bin_dir.mkdir(parents=True)
    e = Env(tmp=tmp_path, home=tmp_path / "home", input_dir=input_dir,
            bin_dir=bin_dir, tg=FakeTelegram(), monkeypatch=monkeypatch,
            capsys=capsys, caplog=caplog)

    # Telegram
    monkeypatch.setattr(
        "aipager.wizard.telegram_api.urllib.request.urlopen", e.tg)

    def _doctor_http(url, timeout=10.0):
        e.tg.calls.append(Call(urllib.parse.urlsplit(url).path
                               .rpartition("/")[2], url, {}))
        if "/getMe" in url:
            return ({"ok": True, "result": {"username": BOT}}, "")
        if "/getChat" in url:
            return ({"ok": True, "result": {"id": CHAT}}, "")
        return ({"ok": True, "result": {}}, "")
    monkeypatch.setattr("aipager.doctor._http_json", _doctor_http)

    # deps: fake executables
    for name in ("dtach", "claude", "aipager-hook", "aipager-statusline"):
        p = bin_dir / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)

    def _which(name, *a, **k):
        if name in e.missing:
            return None
        p = bin_dir / str(name)
        return str(p) if p.exists() else None
    monkeypatch.setattr("aipager.wizard.settings_patch.shutil.which", _which)

    def _no_dtach_bin():
        raise FileNotFoundError("dtach_bin not in this test")
    try:
        import dtach_bin  # noqa: F401
        monkeypatch.setattr("dtach_bin.path", _no_dtach_bin)
    except ImportError:
        pass

    def _resolve_claude(*a, **k):
        if "claude" in e.missing:
            return None
        return SimpleNamespace(chosen=SimpleNamespace(
            path=str(bin_dir / "claude")))
    monkeypatch.setattr("aipager.claude_resolve.try_resolve_claude_binary",
                        _resolve_claude)

    # daemon
    monkeypatch.setattr(daemon_io, "_detect_daemon_running",
                        lambda: e.daemon_pid)

    def _live_reload():
        e.reloads.append(1)
        return e.reload_outcome
    monkeypatch.setattr(daemon_io, "_live_reload", _live_reload)

    def _signal_reload():
        e.signals.append(1)
        return True
    monkeypatch.setattr(daemon_io, "_signal_reload", _signal_reload)

    # service installer
    def _install(*, yes):
        e.installs.append(yes)
        if e.service_rc == 0:
            up = Path(svc.LINUX_UNIT_PATH)
            up.parent.mkdir(parents=True, exist_ok=True)
            up.write_text("[Unit]\nDescription=aipager (test)\n")
        return e.service_rc
    monkeypatch.setattr(svc, "install_service", _install)
    monkeypatch.setattr(svc, "_platform", lambda: "linux")
    if "linux" in svc._DISPATCH:
        monkeypatch.setitem(svc._DISPATCH["linux"], "install", _install)
    monkeypatch.setattr(svc, "unit_path", lambda: Path(svc.LINUX_UNIT_PATH))

    # no prompts: the real terminal guard, counted; traps on every prompt
    def _require_interactive(*a, **k):
        e.interactive_calls.append(a)
        return REAL_REQUIRE_INTERACTIVE(*a, **k)
    monkeypatch.setattr(errors_mod, "require_interactive",
                        _require_interactive)

    def _trap(name):
        def _t(*a, **k):
            e.traps.append(name)
            raise AssertionError(f"setup reached a prompt: {name}")
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
            e.spawned.append(args)
            raise FileNotFoundError(f"no subprocess in these tests: {args!r}")
    monkeypatch.setattr(subprocess, "Popen", _NoPopen)

    # the token is never an env var source
    monkeypatch.delenv("CLAUDE_TG_BOT_TOKEN", raising=False)
    yield e
