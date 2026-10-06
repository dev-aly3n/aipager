"""The wizard's behaviour after its cores were extracted for `aipager
setup`: same texts, same output, same seams."""

from __future__ import annotations

import argparse
import io
import json
import urllib.error
import urllib.parse

import pytest

from aipager.wizard import daemon_io, settings_patch, telegram_api


@pytest.fixture(autouse=True)
def _no_live_daemon_socket(tmp_path, monkeypatch):
    """Never let a check here reach the operator's live daemon socket."""
    no_daemon = str(tmp_path / "no-daemon.sock")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", no_daemon)
    monkeypatch.setattr("aipager.status.SOCKET_PATH", no_daemon)


TOKEN = "123456789:AAHf3kLmQ9zXwV7bN2pR8sT4uY6cE1dG0jK"


class _Resp:
    def __init__(self, body, status=200):
        self._b = json.dumps(body).encode()
        self.status = status

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ----- telegram_api -----

def test_test_send_posts_the_wizard_text(monkeypatch):
    seen = []

    def fake(req, timeout=30):
        seen.append(urllib.parse.parse_qs(req.data.decode()))
        return _Resp({"ok": True, "result": {}})

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", fake)
    assert telegram_api._test_send(TOKEN, 42) == (True, "")
    assert seen == [{"chat_id": ["42"], "text": ["✓ aipager linked to this chat."]}]


def test_send_message_reports_the_http_status(monkeypatch):
    def fake(req, timeout=30):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {},
            io.BytesIO(b'{"ok": false, "description": "Forbidden: bot was '
                       b'blocked by the user"}'))

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", fake)
    ok, desc, code = telegram_api._send_message(TOKEN, 42, "hi")
    assert (ok, code) == (False, 403) and "blocked" in desc
    assert telegram_api._test_send(TOKEN, 42) == (False, desc)


def test_send_message_network_error_has_no_status(monkeypatch):
    def fake(req, timeout=30):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(telegram_api.urllib.request, "urlopen", fake)
    ok, _desc, code = telegram_api._send_message(TOKEN, 42, "hi")
    assert ok is False and code is None


def test_get_me_prints_nothing_and_verify_token_prints_the_error(monkeypatch, capsys):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": False}, 401, "Unauthorized"))
    info, code, explained = telegram_api._get_me(TOKEN)
    assert info is None and code == 401 and "rejected the token" in explained
    assert capsys.readouterr().err == ""
    assert telegram_api._verify_token(TOKEN) is None
    assert "rejected the token" in capsys.readouterr().err


def test_verify_token_returns_the_bot(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": {"username": "b"}}, 200, ""))
    assert telegram_api._verify_token(TOKEN) == {"username": "b"}


# ----- settings_patch -----

@pytest.fixture
def which(monkeypatch):
    monkeypatch.setattr(settings_patch.shutil, "which", lambda n: f"/opt/bin/{n}")


def _capture(monkeypatch):
    lines = []
    monkeypatch.setattr(settings_patch, "step", lambda t: lines.append(("step", t)))
    monkeypatch.setattr(settings_patch, "ok", lambda t: lines.append(("ok", t)))
    monkeypatch.setattr(settings_patch.console, "print",
                        lambda *a, **k: lines.append(("print", a[0] if a else "")))
    return lines


def test_step_settings_new_file(monkeypatch, which):
    lines = _capture(monkeypatch)
    path = settings_patch.CLAUDE_SETTINGS
    settings_patch._step_settings("[4/4]")
    n = len(settings_patch.HOOK_EVENTS)
    assert lines == [("step", "[4/4]  Claude Code integration"),
                     ("ok", f"Patched {path} ({n} hooks + statusLine)")]
    data = json.loads(path.read_text())
    assert set(data["hooks"]) == set(settings_patch.HOOK_EVENTS)
    assert list(path.parent.glob("settings.json.bak.*")) == []


def test_step_settings_up_to_date(monkeypatch, which):
    settings_patch._step_settings()
    path = settings_patch.CLAUDE_SETTINGS
    before = path.read_bytes()
    lines = _capture(monkeypatch)
    settings_patch._step_settings("[4/4]")
    assert lines == [("step", "[4/4]  Claude Code integration"),
                     ("ok", f"{path} already up to date")]
    assert path.read_bytes() == before


def test_step_settings_repoint_backup_order(monkeypatch, which):
    path = settings_patch.CLAUDE_SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    stale = {"hooks": {"Stop": [{"hooks": [{"type": "command",
                                            "command": "/old/bin/aipager-hook"}]}]},
             "other": 1}
    path.write_text(json.dumps(stale, indent=2) + "\n")
    lines = _capture(monkeypatch)
    settings_patch._step_settings("[4/4]")
    kinds = [k for k, _t in lines]
    assert kinds == ["step", "print", "print", "ok"]
    assert "backed up existing settings" in lines[1][1]
    assert "repointed 1 entry from an earlier install" in lines[2][1]
    data = json.loads(path.read_text())
    assert data["other"] == 1
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "/opt/bin/aipager-hook"
    assert len(list(path.parent.glob("settings.json.bak.*"))) == 1


def test_plan_settings_writes_nothing_and_apply_matches_step(monkeypatch, which, tmp_path):
    path = settings_patch.CLAUDE_SETTINGS
    plan = settings_patch.plan_settings()
    assert plan.changed and not plan.exists and not path.exists()
    assert settings_patch.apply_settings(plan) is None
    via_apply = path.read_text()
    path.unlink()
    _capture(monkeypatch)
    settings_patch._step_settings()
    assert path.read_text() == via_apply


def test_plan_settings_rejects_bad_json_like_the_step(monkeypatch, which):
    path = settings_patch.CLAUDE_SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// comment\n{}")
    with pytest.raises(ValueError, match="comments"):
        settings_patch.plan_settings()
    _capture(monkeypatch)
    with pytest.raises(ValueError, match="comments"):
        settings_patch._step_settings()


def test_check_deps_and_step_deps(monkeypatch, capsys):
    import sys

    from aipager import claude_resolve
    monkeypatch.setitem(sys.modules, "dtach_bin", None)  # ImportError -> which
    monkeypatch.setattr(settings_patch.shutil, "which",
                        lambda n: None if n == "aipager-hook" else f"/b/{n}")
    monkeypatch.setattr(claude_resolve, "try_resolve_claude_binary", lambda: None)
    deps = settings_patch.check_deps()
    assert [(d.name, d.path) for d in deps] == [
        ("dtach", "/b/dtach"), ("claude", None), ("aipager-hook", None),
        ("aipager-statusline", "/b/aipager-statusline")]
    assert deps[2].fix == "uv tool install --reinstall aipager"
    assert settings_patch._step_deps("[4/4]") is False
    out = capsys.readouterr().out
    assert "  ✓ dtach  /b/dtach" in out
    assert "  ✗ aipager-hook  uv tool install --reinstall aipager" in out


# ----- first_run -----

def test_commit_owner_dm_unchanged(monkeypatch, tmp_path):
    from aipager import audit, scope
    from aipager.wizard import first_run
    monkeypatch.setattr(first_run, "ok", lambda t: None)
    first_run._commit_owner_dm(TOKEN, 42, "owner")
    scopes, tok = scope.load_scopes(scope.CONFIG_PATH)
    assert tok == TOKEN and scopes == [first_run._owner_dm_scope(42, "owner")]
    lines = [json.loads(x) for x in open(audit.AUDIT_LOG_PATH)]
    assert [(r["action"], r["user_id"]) for r in lines] == [("grant-owner", 42)]


# ----- daemon_io -----

@pytest.mark.parametrize("problem,signalled,outcome", [
    ("broken", True, ("refused", "broken")),
    (None, True, ("reloaded", None)),
    (None, False, ("not_reloaded", None)),
])
def test_live_reload_outcomes(monkeypatch, problem, signalled, outcome):
    sent = []
    monkeypatch.setattr(daemon_io, "_config_problem", lambda: problem)
    monkeypatch.setattr(daemon_io, "_signal_reload",
                        lambda: sent.append(1) or signalled)
    assert daemon_io._live_reload() == outcome
    assert sent == ([] if problem else [1])


def test_apply_team_change_hint_per_outcome(monkeypatch):
    printed, hints = [], []
    monkeypatch.setattr(daemon_io.console, "print",
                        lambda *a, **k: printed.append(a[0] if a else ""))
    monkeypatch.setattr(daemon_io, "_restart_hint", lambda: hints.append(1))
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: 7)

    monkeypatch.setattr(daemon_io, "_live_reload", lambda: ("reloaded", None))
    daemon_io._apply_team_change_hint()
    assert any("Scopes reloaded live" in p for p in printed) and hints == []

    printed.clear()
    monkeypatch.setattr(daemon_io, "_live_reload", lambda: ("refused", "bad yaml"))
    daemon_io._apply_team_change_hint()
    assert any("Not applied: bad yaml" in p for p in printed)
    assert any("keeps its previous config" in p for p in printed)

    printed.clear()
    monkeypatch.setattr(daemon_io, "_live_reload", lambda: ("not_reloaded", None))
    daemon_io._apply_team_change_hint()
    assert hints == [1]


# ----- service -----

def test_cmd_service_install_still_runs_require_config(monkeypatch):
    from aipager import preflight, service
    calls = []
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setattr(service, "install_service",
                        lambda *, yes: calls.append(("install", yes)) or 0)

    def _refuse():
        calls.append("require_config")
        raise SystemExit(2)

    monkeypatch.setattr(preflight, "require_config", _refuse)
    with pytest.raises(SystemExit):
        service.cmd_service(argparse.Namespace(service_cmd="install", yes=True))
    assert calls == ["require_config"]
    monkeypatch.setattr(preflight, "require_config", lambda: calls.append("rc"))
    assert service.cmd_service(argparse.Namespace(service_cmd="install",
                                                  yes=True)) == 0
    assert calls[-2:] == ["rc", ("install", True)]


def test_install_service_dispatches_with_yes(monkeypatch):
    from aipager import service
    seen = []
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    monkeypatch.setitem(service._DISPATCH["linux"], "install",
                        lambda *, yes: seen.append(yes) or 0)
    assert service.install_service(yes=True) == 0 and seen == [True]
    monkeypatch.setattr(service, "_platform", lambda: "windows")
    assert service.install_service(yes=True) == 1


def test_unit_path_per_platform(monkeypatch):
    from aipager import service
    monkeypatch.setattr(service, "_platform", lambda: "linux")
    assert service.unit_path() == service.LINUX_UNIT_PATH
    monkeypatch.setattr(service, "_platform", lambda: "macos")
    assert service.unit_path() == service.MACOS_PLIST_PATH
    monkeypatch.setattr(service, "_platform", lambda: "windows")
    assert service.unit_path() is None


def test_redact_bare_token():
    from aipager.errors import redact_bare_token
    assert TOKEN not in redact_bare_token(f"bad {TOKEN} arg")
    assert redact_bare_token("x 123: y") == "x 123: y"
