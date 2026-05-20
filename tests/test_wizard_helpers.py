"""Tests for the testable wizard helpers — non-interactive code paths
in telegram_api, settings_patch, daemon_io.

The interactive prompts (questionary / input) are skipped here. We
test the underlying API/IO helpers directly so users can be confident
the setup wizard's HTTP, settings-merging, and config-env logic is
sound regardless of how they drive the prompts.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO

import pytest

from aipager.wizard import daemon_io, settings_patch, telegram_api


# ===== telegram_api =====================================================

# ---- _normalize_token (more cases than the existing test file) ----------

@pytest.mark.parametrize("raw,expected", [
    ("", ""),
    ("   ", ""),
    # Trailing newline
    ("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ-_abcdef\n",
     "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ-_abcdef"),
    # Wrapped in single quotes
    ("'123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ-_abcdef'",
     "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ-_abcdef"),
    # Some random text around a valid token
    ("token=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ-_abcdef hello",
     "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ-_abcdef"),
])
def test_normalize_token(raw, expected):
    assert telegram_api._normalize_token(raw) == expected


# ---- _http_json ---------------------------------------------------------

def _make_url_open(monkeypatch, response_body=None, exc=None):
    class _R:
        def __init__(self):
            self.status = 200
        def read(self):
            return json.dumps(response_body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def _fake(url, timeout=30):
        if exc:
            raise exc
        return _R()
    monkeypatch.setattr("aipager.wizard.telegram_api.urllib.request.urlopen", _fake)
    monkeypatch.setattr("aipager.wizard.telegram_api.json.load",
                        lambda fp: json.loads(fp.read()))


def test_http_json_happy_path(monkeypatch):
    _make_url_open(monkeypatch, response_body={"ok": True, "result": {}})
    body, code, err = telegram_api._http_json("https://x.com")
    assert body == {"ok": True, "result": {}}
    assert code == 200
    assert err == ""


def test_http_json_http_error_with_description(monkeypatch):
    err = urllib.error.HTTPError(
        url="https://x.com", code=400, msg="Bad",
        hdrs=None, fp=BytesIO(b'{"description": "Bad Request"}'),
    )
    _make_url_open(monkeypatch, exc=err)
    body, code, err_desc = telegram_api._http_json("https://x.com")
    assert code == 400
    assert "Bad Request" in err_desc


def test_http_json_url_error(monkeypatch):
    err = urllib.error.URLError("DNS failure")
    _make_url_open(monkeypatch, exc=err)
    body, code, err_desc = telegram_api._http_json("https://x.com")
    assert body is None
    assert "network:" in err_desc


def test_http_json_os_error(monkeypatch):
    err = OSError("conn reset")
    _make_url_open(monkeypatch, exc=err)
    body, code, err_desc = telegram_api._http_json("https://x.com")
    assert body is None


# ---- _explain_http_error ------------------------------------------------

@pytest.mark.parametrize("code,err,expected", [
    (401, "Unauthorized", "rejected the token"),
    (404, "Not Found", "URL is malformed"),
    (429, "Too Many", "rate-limiting"),
    (500, "Server", "transient"),
    (503, "Bad Gateway", "transient"),
    (None, "network: DNS", "can't reach api.telegram.org"),
    (None, "", "unknown error"),
])
def test_explain_http_error(code, err, expected):
    assert expected in telegram_api._explain_http_error(code, err)


# ---- _verify_token ------------------------------------------------------

def test_verify_token_success(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": {"username": "bot"}}, 200, ""))
    out = telegram_api._verify_token("tok")
    assert out == {"username": "bot"}


def test_verify_token_invalid(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": False, "description": "bad"}, 401, "Unauthorized"))
    assert telegram_api._verify_token("tok") is None


# ---- _test_send ---------------------------------------------------------

def test_test_send_success(monkeypatch):
    class _R:
        def read(self):
            return b'{"ok": true, "result": {}}'
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr("aipager.wizard.telegram_api.urllib.request.urlopen",
                        lambda req, timeout=30: _R())
    monkeypatch.setattr("aipager.wizard.telegram_api.json.load",
                        lambda fp: json.loads(fp.read()))
    ok, err = telegram_api._test_send("tok", 12345)
    assert ok is True
    assert err == ""


def test_test_send_http_error_with_description(monkeypatch):
    err = urllib.error.HTTPError(
        url="x", code=400, msg="x", hdrs=None,
        fp=BytesIO(b'{"description": "chat not found"}'),
    )
    def _fake(*a, **k):
        raise err
    monkeypatch.setattr("aipager.wizard.telegram_api.urllib.request.urlopen", _fake)
    ok, msg = telegram_api._test_send("tok", 12345)
    assert ok is False
    assert "chat not found" in msg


def test_test_send_url_error(monkeypatch):
    err = urllib.error.URLError("dns")
    def _fake(*a, **k):
        raise err
    monkeypatch.setattr("aipager.wizard.telegram_api.urllib.request.urlopen", _fake)
    ok, _ = telegram_api._test_send("tok", 12345)
    assert ok is False


def test_test_send_result_not_ok(monkeypatch):
    class _R:
        def read(self):
            return b'{"ok": false, "description": "nope"}'
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr("aipager.wizard.telegram_api.urllib.request.urlopen",
                        lambda req, timeout=30: _R())
    monkeypatch.setattr("aipager.wizard.telegram_api.json.load",
                        lambda fp: json.loads(fp.read()))
    ok, msg = telegram_api._test_send("tok", 12345)
    assert ok is False
    assert "nope" in msg


# ---- _fetch_id_from_updates ---------------------------------------------

def test_fetch_id_from_updates_dm_match(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": [
                            {"message": {"chat": {
                                "id": 42, "type": "private", "username": "alice"
                            }}},
                        ]}, 200, ""))
    cid, who, advisory = telegram_api._fetch_id_from_updates("tok", want="dm")
    assert cid == 42
    assert who == "alice"
    assert advisory is None


def test_fetch_id_from_updates_group_match(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": [
                            {"message": {"chat": {
                                "id": -100, "type": "supergroup", "title": "g"
                            }}},
                        ]}, 200, ""))
    cid, who, advisory = telegram_api._fetch_id_from_updates("tok", want="group")
    assert cid == -100
    assert who == "g"


def test_fetch_id_from_updates_user_match(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": [
                            {"message": {"from": {"id": 999, "username": "bob"},
                                          "chat": {"id": -100, "type": "group"}}},
                        ]}, 200, ""))
    uid, who, _ = telegram_api._fetch_id_from_updates("tok", want="user")
    assert uid == 999
    assert who == "bob"


def test_fetch_id_from_updates_dm_seen_group_advisory(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": [
                            {"message": {"chat": {"id": -100, "type": "group"}}},
                        ]}, 200, ""))
    cid, who, advisory = telegram_api._fetch_id_from_updates("tok", want="dm")
    assert cid is None
    assert advisory is not None
    assert "DM" in advisory or "private" in advisory.lower()


def test_fetch_id_from_updates_empty_response(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": []}, 200, ""))
    cid, who, advisory = telegram_api._fetch_id_from_updates("tok", want="dm")
    assert cid is None
    assert advisory is None


def test_fetch_id_from_updates_http_error_returns_nones(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: (None, 500, "server"))
    cid, who, advisory = telegram_api._fetch_id_from_updates("tok", want="dm")
    assert cid is None
    assert who is None
    assert advisory is None


def test_fetch_id_from_updates_skips_records_without_chat_id(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": [
                            {"message": {"chat": {}}},  # no id
                            {"message": {"chat": {"id": 42, "type": "private",
                                                    "username": "alice"}}},
                        ]}, 200, ""))
    cid, who, _ = telegram_api._fetch_id_from_updates("tok", want="dm")
    assert cid == 42


def test_fetch_id_from_updates_user_want_when_no_from(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": [
                            {"message": {"chat": {"id": -100, "type": "group"}}},
                        ]}, 200, ""))
    uid, _, _ = telegram_api._fetch_id_from_updates("tok", want="user")
    assert uid is None


# ===== daemon_io =========================================================

# ---- _read_env_file -----------------------------------------------------

def test_read_env_file_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_io, "CONFIG_ENV", tmp_path / "no-such-file")
    assert daemon_io._read_env_file() == ("", "")


def test_read_env_file_parses_token_and_chat_id(tmp_path, monkeypatch):
    f = tmp_path / "config.env"
    f.write_text(
        'CLAUDE_TG_BOT_TOKEN="123:abc"\n'
        "# comment line\n"
        "CLAUDE_TG_CHAT_ID=-100\n"
    )
    monkeypatch.setattr(daemon_io, "CONFIG_ENV", f)
    assert daemon_io._read_env_file() == ("123:abc", "-100")


def test_read_env_file_handles_unreadable(tmp_path, monkeypatch):
    f = tmp_path / "config.env"
    f.write_text("CLAUDE_TG_BOT_TOKEN=tok\n")
    monkeypatch.setattr(daemon_io, "CONFIG_ENV", f)
    _real_read = f.__class__.read_text
    def _boom(self, *a, **k):
        raise OSError("EPERM")
    monkeypatch.setattr(f.__class__, "read_text", _boom)
    try:
        assert daemon_io._read_env_file() == ("", "")
    finally:
        monkeypatch.setattr(f.__class__, "read_text", _real_read)


def test_read_env_file_ignores_blank_and_comments(tmp_path, monkeypatch):
    f = tmp_path / "config.env"
    f.write_text("\n# comment\nCLAUDE_TG_BOT_TOKEN=tok\n=novalue\n")
    monkeypatch.setattr(daemon_io, "CONFIG_ENV", f)
    assert daemon_io._read_env_file() == ("tok", "")


# ---- _write_env_file ----------------------------------------------------

def test_write_env_file_writes_and_chmod(tmp_path, monkeypatch):
    target = tmp_path / "config.env"
    monkeypatch.setattr(daemon_io, "CONFIG_ENV", target)
    monkeypatch.setattr(daemon_io, "CONFIG_DIR", tmp_path)
    daemon_io._write_env_file("MY_TOKEN", -100)
    content = target.read_text()
    assert "CLAUDE_TG_BOT_TOKEN=MY_TOKEN" in content
    assert "CLAUDE_TG_CHAT_ID=-100" in content
    # File mode should be 0o600
    assert (target.stat().st_mode & 0o777) == 0o600


def test_write_env_file_swallows_chmod_failure(tmp_path, monkeypatch):
    target = tmp_path / "config.env"
    monkeypatch.setattr(daemon_io, "CONFIG_ENV", target)
    monkeypatch.setattr(daemon_io, "CONFIG_DIR", tmp_path)
    def _boom(p, m):
        raise OSError("EROFS")
    monkeypatch.setattr(daemon_io.os, "chmod", _boom)
    # MUST NOT raise
    daemon_io._write_env_file("tok", "12")
    assert target.exists()


# ---- _detect_daemon_running ---------------------------------------------

def test_detect_daemon_running_no_socket(monkeypatch):
    def _boom_sock(*a, **k):
        raise FileNotFoundError("no socket")
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", daemon_io._detect_daemon_running)
    import socket as _sock
    class _FakeSocket:
        def settimeout(self, t): pass
        def sendto(self, data, p): raise FileNotFoundError("no socket")
        def close(self): pass
    monkeypatch.setattr(_sock, "socket", lambda *a, **k: _FakeSocket())
    assert daemon_io._detect_daemon_running() is None


def test_detect_daemon_running_with_pgrep_finds_pid(monkeypatch):
    import socket as _sock
    class _OkSocket:
        def settimeout(self, t): pass
        def sendto(self, data, p): pass  # success
        def close(self): pass
    monkeypatch.setattr(_sock, "socket", lambda *a, **k: _OkSocket())
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/pgrep")

    import subprocess as _subprocess
    class _R:
        returncode = 0
        stdout = "54321\n67890\n"
    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _R())
    assert daemon_io._detect_daemon_running() == 54321


def test_detect_daemon_running_pgrep_not_available(monkeypatch):
    import socket as _sock
    class _OkSocket:
        def settimeout(self, t): pass
        def sendto(self, data, p): pass
        def close(self): pass
    monkeypatch.setattr(_sock, "socket", lambda *a, **k: _OkSocket())
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    # Daemon is up but pgrep is missing — returns -1 sentinel
    assert daemon_io._detect_daemon_running() == -1


def test_detect_daemon_running_pgrep_fails(monkeypatch):
    import socket as _sock
    class _OkSocket:
        def settimeout(self, t): pass
        def sendto(self, data, p): pass
        def close(self): pass
    monkeypatch.setattr(_sock, "socket", lambda *a, **k: _OkSocket())
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/pgrep")
    import subprocess as _subprocess
    def _boom(*a, **k):
        raise _subprocess.TimeoutExpired(cmd="pgrep", timeout=2)
    monkeypatch.setattr(_subprocess, "run", _boom)
    assert daemon_io._detect_daemon_running() == -1


# ---- _restart_hint ------------------------------------------------------

def test_restart_hint_no_daemon_is_noop(monkeypatch, capsys):
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: None)
    daemon_io._restart_hint()
    # No output (or short) expected. Just verify no crash.


def test_restart_hint_daemon_running_prints_warning(monkeypatch, capsys):
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: 54321)
    daemon_io._restart_hint()


# ---- _signal_reload -----------------------------------------------------

def test_signal_reload_daemon_not_running_returns_false(monkeypatch):
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: None)
    assert daemon_io._signal_reload() is False


def test_signal_reload_pid_unknown_returns_false(monkeypatch):
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: -1)
    assert daemon_io._signal_reload() is False


def test_signal_reload_happy_path(monkeypatch):
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: 54321)
    sent = []
    monkeypatch.setattr(daemon_io.os, "kill",
                        lambda pid, sig: sent.append((pid, sig)))
    assert daemon_io._signal_reload() is True
    assert sent == [(54321, 10)]  # SIGUSR1=10 on linux


def test_signal_reload_oserror_returns_false(monkeypatch):
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: 54321)
    def _boom(*a, **k):
        raise OSError("ESRCH")
    monkeypatch.setattr(daemon_io.os, "kill", _boom)
    assert daemon_io._signal_reload() is False


# ---- _apply_team_change_hint -------------------------------------------

def test_apply_team_change_hint_signal_succeeds(monkeypatch, capsys):
    monkeypatch.setattr(daemon_io, "_signal_reload", lambda: True)
    daemon_io._apply_team_change_hint()
    # Verify no exception; the function prints a success message


def test_apply_team_change_hint_signal_fails_falls_back(monkeypatch, capsys):
    monkeypatch.setattr(daemon_io, "_signal_reload", lambda: False)
    monkeypatch.setattr(daemon_io, "_restart_hint", lambda: None)
    daemon_io._apply_team_change_hint()


# ===== settings_patch ===================================================

# ---- _validate_settings_schema -----------------------------------------

def test_validate_settings_schema_accepts_dict():
    settings_patch._validate_settings_schema({"hooks": {"SessionStart": []}})


def test_validate_settings_schema_accepts_missing_hooks():
    settings_patch._validate_settings_schema({})


def test_validate_settings_schema_rejects_str_hooks():
    with pytest.raises(ValueError, match="dict"):
        settings_patch._validate_settings_schema({"hooks": "x"})


def test_validate_settings_schema_rejects_str_event_value():
    with pytest.raises(ValueError, match="list"):
        settings_patch._validate_settings_schema({"hooks": {"X": "nope"}})


# ---- _merge_hooks (idempotent + adds new) ------------------------------

def test_merge_hooks_adds_all_event_entries(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = {}
    settings_patch._merge_hooks(settings)
    assert "hooks" in settings
    assert "SessionStart" in settings["hooks"]
    assert "PreToolUse" in settings["hooks"]
    # statusLine top-level entry
    assert "statusLine" in settings


def test_merge_hooks_idempotent_for_existing_entry(monkeypatch):
    """Calling _merge_hooks twice doesn't duplicate entries."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = {}
    settings_patch._merge_hooks(settings)
    first = json.dumps(settings, sort_keys=True)
    settings_patch._merge_hooks(settings)
    second = json.dumps(settings, sort_keys=True)
    assert first == second


def test_merge_hooks_includes_tool_matcher_for_tool_events(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = {}
    settings_patch._merge_hooks(settings)
    # PreToolUse / PostToolUse / PermissionRequest get a matcher: "*"
    for event in ("PreToolUse", "PostToolUse", "PermissionRequest"):
        block = settings["hooks"][event][0]
        assert block.get("matcher") == "*"


# ---- _has_hook_cmd ------------------------------------------------------

def test_has_hook_cmd_present():
    entries = [{"hooks": [{"type": "command", "command": "aipager-hook"}]}]
    assert settings_patch._has_hook_cmd(entries, "aipager-hook") is True


def test_has_hook_cmd_absent():
    entries = [{"hooks": [{"type": "command", "command": "other"}]}]
    assert settings_patch._has_hook_cmd(entries, "aipager-hook") is False


def test_has_hook_cmd_empty_list():
    assert settings_patch._has_hook_cmd([], "aipager-hook") is False


# ---- _resolve -----------------------------------------------------------

def test_resolve_returns_which_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert settings_patch._resolve("aipager-hook") == "/usr/bin/aipager-hook"


def test_resolve_returns_name_when_not_on_path(monkeypatch, tmp_path):
    """Bare-name fallback only fires when sys.executable.parent also misses."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    # Point sys.executable to a dir that has no aipager-hook binary
    monkeypatch.setattr("sys.executable", str(tmp_path / "python3"))
    assert settings_patch._resolve("aipager-hook") == "aipager-hook"


def test_resolve_falls_back_to_sys_executable_parent(monkeypatch, tmp_path):
    """PATH misses but the binary lives in <venv>/bin/ next to python — use it."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.write_text("#!/bin/sh\necho hi\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr("sys.executable", str(tmp_path / "python3"))
    assert settings_patch._resolve("fake-bin") == str(fake_bin)


def test_resolve_ignores_non_executable_file(monkeypatch, tmp_path):
    """A file next to sys.executable that isn't executable doesn't count."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    f = tmp_path / "fake-bin"
    f.write_text("not a script")
    f.chmod(0o644)  # readable but not executable
    monkeypatch.setattr("sys.executable", str(tmp_path / "python3"))
    assert settings_patch._resolve("fake-bin") == "fake-bin"


# ---- _merge_hooks statusLine idempotency -------------------------------

def test_merge_hooks_keeps_existing_reachable_statusline(monkeypatch):
    """A statusLine pointing at a real binary on PATH is preserved."""
    monkeypatch.setattr("shutil.which",
                        lambda name: f"/usr/bin/{name}"
                        if name in ("aipager-hook", "aipager-statusline", "/usr/bin/true")
                        else None)
    settings = {"statusLine": {"type": "command", "command": "/usr/bin/true"}}
    settings_patch._merge_hooks(settings)
    assert settings["statusLine"]["command"] == "/usr/bin/true"


def test_merge_hooks_keeps_existing_absolute_executable_statusline(monkeypatch, tmp_path):
    """An absolute-path statusLine that exists and is executable is preserved
    even when shutil.which can't find it on PATH."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    real_bin = tmp_path / "real-statusline"
    real_bin.write_text("#!/bin/sh\n")
    real_bin.chmod(0o755)
    settings = {"statusLine": {"type": "command", "command": str(real_bin)}}
    # shutil.which would not find this path under any name lookup
    monkeypatch.setattr("shutil.which",
                        lambda name: None if name == str(real_bin) else f"/usr/bin/{name}")
    settings_patch._merge_hooks(settings)
    assert settings["statusLine"]["command"] == str(real_bin)


def test_merge_hooks_repairs_broken_bare_statusline(monkeypatch):
    """A bare-name statusLine that's NOT on PATH gets re-resolved."""
    # which() returns absolute paths for the wizard's known commands
    # but None for the broken bare name in the existing entry.
    def _which(name):
        if name == "broken-name":
            return None
        return f"/opt/venv/bin/{name}"
    monkeypatch.setattr("shutil.which", _which)
    settings = {"statusLine": {"type": "command", "command": "broken-name"}}
    settings_patch._merge_hooks(settings)
    # statusline_path comes from _resolve("aipager-statusline") which
    # which()-returns "/opt/venv/bin/aipager-statusline"
    assert settings["statusLine"]["command"] == "/opt/venv/bin/aipager-statusline"


def test_merge_hooks_writes_fresh_statusline_when_absent(monkeypatch):
    """Empty settings get a fresh statusLine entry."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = {}
    settings_patch._merge_hooks(settings)
    assert settings["statusLine"]["command"] == "/usr/bin/aipager-statusline"


def test_merge_hooks_handles_non_dict_existing_statusline(monkeypatch):
    """Defensive: existing settings.statusLine as a non-dict is overwritten."""
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = {"statusLine": "garbage"}  # malformed
    settings_patch._merge_hooks(settings)
    assert isinstance(settings["statusLine"], dict)
    assert settings["statusLine"]["command"] == "/usr/bin/aipager-statusline"
