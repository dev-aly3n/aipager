"""Iteration 3: a failure after the first write (settings.json, the audit
record, the service installer, the live reload) is reported, and
``changed`` lists exactly what was written before it (entrypoints.md:
"OSError in a-d -> write_failed, exit 1, changed lists what was written";
``--service`` non-zero -> ``service_failed``, exit 1, "config already on
disk, changed says so"). A reload that cannot be done after a scope
change is not a failure: exit 0, ``reload: not_reloaded``,
``restart_needed: true`` (docs/commands.md, ``daemon.reload``)."""

from __future__ import annotations

import os

import pytest

import aipager.wizard.daemon_io as _daemon_io
from agent_setup_support import CHAT, TOKEN, assert_no_secret

# The real reload seams, saved at import (before the env fixture stubs them).
REAL_LIVE_RELOAD = _daemon_io._live_reload
REAL_SIGNAL_RELOAD = _daemon_io._signal_reload


def _role_of(env, chat=CHAT):
    return [m["role"] for s in env.config()["scopes"]
            if s["chat_id"] == chat for m in s["members"] if m["id"] == chat]


# ── settings.json write fails (OSError after the yaml is written) ─────

@pytest.fixture
def ro_settings_dir(env):
    """~/.claude exists but cannot be written: planning (a read) works,
    the write raises PermissionError."""
    d = env.settings_path.parent
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o500)
    yield env
    os.chmod(d, 0o700)


def test_settings_write_failure_exits_1(ro_settings_dir):
    assert ro_settings_dir.setup().code == 1


def test_settings_write_failure_is_write_failed(ro_settings_dir):
    assert ro_settings_dir.setup().json["error"] == "write_failed"


def test_settings_write_failure_status_is_error(ro_settings_dir):
    assert ro_settings_dir.setup().json["status"] == "error"


def test_settings_write_failure_changed_lists_the_yaml_only(ro_settings_dir):
    """Fresh install: the token and the owner DM were written; settings.json
    was not."""
    assert ro_settings_dir.setup().json["changed"] == ["bot_token",
                                                       "owner_dm"]


def test_settings_write_failure_yaml_is_on_disk(ro_settings_dir):
    ro_settings_dir.setup()
    assert _role_of(ro_settings_dir) == ["owner"]


def test_settings_write_failure_yaml_is_0600(ro_settings_dir):
    ro_settings_dir.setup()
    assert oct(ro_settings_dir.config_path.stat().st_mode & 0o777) == "0o600"


def test_settings_write_failure_wrote_no_settings_file(ro_settings_dir):
    ro_settings_dir.setup()
    assert not ro_settings_dir.settings_path.exists()


def test_settings_write_failure_fix_is_not_null(ro_settings_dir):
    assert (ro_settings_dir.setup().json["fix"] or "").strip() != ""


def test_settings_write_failure_leaks_no_token(ro_settings_dir):
    r = ro_settings_dir.setup()
    assert_no_secret(r.out + r.err + r.log, "write_failed output")


def test_settings_write_failure_plain_exits_1(ro_settings_dir):
    assert ro_settings_dir.setup(json_out=False).code == 1


def test_settings_write_failure_on_role_change_lists_only_role(env):
    """Existing install, role change with --force: only `role` was
    written before settings.json failed (settings.json gets an unrelated
    edit so it has to change again)."""
    assert env.setup().code == 0
    env.settings_path.write_text("{}\n")
    d = env.settings_path.parent
    os.chmod(d, 0o500)
    try:
        r = env.setup("--force", "--role", "admin")
    finally:
        os.chmod(d, 0o700)
    assert (r.code, r.json["error"], r.json["changed"]) == (
        1, "write_failed", ["role"])


def test_settings_write_failure_on_role_change_role_is_on_disk(env):
    assert env.setup().code == 0
    env.settings_path.write_text("{}\n")
    d = env.settings_path.parent
    os.chmod(d, 0o500)
    try:
        env.setup("--force", "--role", "admin")
    finally:
        os.chmod(d, 0o700)
    assert _role_of(env) == ["admin"]


def test_settings_write_failure_then_rerun_converges(ro_settings_dir):
    """"A re-run converges": once the directory is writable again, the
    same command finishes the install and lists only settings.json."""
    ro_settings_dir.setup()
    os.chmod(ro_settings_dir.settings_path.parent, 0o700)
    r = ro_settings_dir.setup()
    assert (r.code, r.json["changed"]) == (0, ["settings_json"])


def test_settings_write_failure_sends_one_test_message(ro_settings_dir):
    """The test send happens before any write; a failed write does not
    add a second one."""
    ro_settings_dir.setup()
    assert len(ro_settings_dir.tg.sent) == 1


# ── audit record write fails (step c) ─────────────────────────────────

def test_audit_write_failure_changed_lists_the_yaml(env, monkeypatch):
    """Error guessing: the grant-owner audit append raises after the yaml
    is written. Whatever the exit code, `changed` names what is on
    disk."""
    import aipager.audit as audit_mod
    ro = env.tmp / "ro-audit"
    ro.mkdir()
    monkeypatch.setattr(audit_mod, "AUDIT_LOG_PATH", ro / "audit.jsonl")
    os.chmod(ro, 0o500)
    try:
        r = env.setup()
    finally:
        os.chmod(ro, 0o700)
    assert r.json["changed"][:2] == ["bot_token", "owner_dm"]


def test_audit_write_failure_exit_code_is_documented(env, monkeypatch):
    """The audit record is best effort (docs/commands.md): exit 0, no
    error, and the warning `audit_write_failed` says the grant has no
    audit record."""
    import aipager.audit as audit_mod
    ro = env.tmp / "ro-audit"
    ro.mkdir()
    monkeypatch.setattr(audit_mod, "AUDIT_LOG_PATH", ro / "audit.jsonl")
    os.chmod(ro, 0o500)
    try:
        r = env.setup()
    finally:
        os.chmod(ro, 0o700)
    assert (r.code, r.json["error"]) == (0, None)
    assert [w["code"] for w in r.json["warnings"]] == ["audit_write_failed"]


# ── service installer fails (step f) ──────────────────────────────────

def test_service_failure_fresh_changed_is_exact(env):
    env.service_rc = 1
    assert env.setup("--service").json["changed"] == [
        "bot_token", "owner_dm", "settings_json"]


def test_service_failure_fresh_settings_on_disk(env):
    env.service_rc = 1
    env.setup("--service")
    assert env.settings_path.exists()


def test_service_failure_on_unchanged_install_exits_1(env):
    assert env.setup().code == 0
    env.service_rc = 1
    assert env.setup("--service").code == 1


def test_service_failure_on_unchanged_install_changed_is_empty(env):
    assert env.setup().code == 0
    env.service_rc = 1
    assert env.setup("--service").json["changed"] == []


def test_service_failure_on_role_change_changed_is_role(env):
    assert env.setup().code == 0
    env.service_rc = 1
    r = env.setup("--service", "--force", "--role", "admin")
    assert (r.code, r.json["error"], r.json["changed"]) == (
        1, "service_failed", ["role"])


def test_service_failure_status_is_error(env):
    env.service_rc = 1
    assert env.setup("--service").json["status"] == "error"


def test_service_failure_plain_exits_1(env):
    env.service_rc = 1
    assert env.setup("--service", json_out=False).code == 1


@pytest.fixture
def raising_installer(env, monkeypatch):
    import aipager.service as svc

    def _boom(*, yes):
        env.installs.append(yes)
        raise OSError(f"cannot write the unit near {TOKEN}")
    monkeypatch.setattr(svc, "install_service", _boom)
    if "linux" in svc._DISPATCH:
        monkeypatch.setitem(svc._DISPATCH["linux"], "install", _boom)
    return env


def test_service_installer_raising_exits_1(raising_installer):
    assert raising_installer.setup("--service").code == 1


def test_service_installer_raising_changed_is_exact(raising_installer):
    assert raising_installer.setup("--service").json["changed"] == [
        "bot_token", "owner_dm", "settings_json"]


def test_service_installer_raising_keeps_the_yaml(raising_installer):
    raising_installer.setup("--service")
    assert _role_of(raising_installer) == ["owner"]


def test_service_installer_raising_leaks_no_token(raising_installer):
    r = raising_installer.setup("--service")
    assert_no_secret(r.out + r.err + r.log, "service error output")


def test_service_installer_raising_stdout_is_one_json(raising_installer):
    r = raising_installer.setup("--service")
    assert isinstance(r.json, dict)


# ── live reload raises after a scope change (step e) ──────────────────

def _installed_then_daemon(env, pid=4242):
    assert env.setup().code == 0
    env.daemon_pid = pid
    env.reloads.clear()
    env.signals.clear()


@pytest.fixture(params=[OSError, RuntimeError, ValueError])
def raising_reload(env, monkeypatch, request):
    exc = request.param

    def _boom():
        env.reloads.append(1)
        raise exc(f"reload broke near {TOKEN}")
    _installed_then_daemon(env)
    monkeypatch.setattr(_daemon_io, "_live_reload", _boom)
    return env


def _role_change(env, **kw):
    return env.setup("--force", "--role", "admin", **kw)


def test_reload_raising_exits_0(raising_reload):
    assert _role_change(raising_reload).code == 0


def test_reload_raising_status_is_updated(raising_reload):
    assert _role_change(raising_reload).json["status"] == "updated"


def test_reload_raising_changed_is_role(raising_reload):
    assert _role_change(raising_reload).json["changed"] == ["role"]


def test_reload_raising_reload_is_not_reloaded(raising_reload):
    assert _role_change(raising_reload).json["daemon"]["reload"] == (
        "not_reloaded")


def test_reload_raising_restart_needed(raising_reload):
    assert _role_change(raising_reload).json["daemon"][
        "restart_needed"] is True


def test_reload_raising_was_tried(raising_reload):
    _role_change(raising_reload)
    assert raising_reload.reloads == [1]


def test_reload_raising_role_is_on_disk(raising_reload):
    _role_change(raising_reload)
    assert _role_of(raising_reload) == ["admin"]


def test_reload_raising_next_step_says_stop_and_start(raising_reload):
    s = _role_change(raising_reload).json["next_step"]
    assert "aipager service stop" in s and "aipager service start" in s


def test_reload_raising_next_step_names_no_bot_token(raising_reload):
    assert "bot token" not in _role_change(
        raising_reload).json["next_step"].lower()


def test_reload_raising_leaks_no_token(raising_reload):
    r = _role_change(raising_reload)
    assert_no_secret(r.out + r.err + r.log, "reload error output")


def test_reload_raising_plain_exits_0(raising_reload):
    assert _role_change(raising_reload, json_out=False).code == 0


def test_reload_raising_on_chat_change_changed_is_owner_dm(raising_reload):
    r = raising_reload.setup("--force", chat=777000111)
    assert (r.code, r.json["changed"]) == (0, ["owner_dm"])


# ── the daemon becomes unreadable inside the REAL reload ──────────────

@pytest.fixture
def vanishing_daemon(env, monkeypatch):
    """The real reload seams; the detector sees pid 4242 until the new
    role is on disk, then raises (the daemon's state became unreadable
    between setup's check and the reload). Signals are recorded."""
    _installed_then_daemon(env)
    kills: list = []
    monkeypatch.setattr(_daemon_io, "_live_reload", REAL_LIVE_RELOAD)
    monkeypatch.setattr(_daemon_io, "_signal_reload", REAL_SIGNAL_RELOAD)
    monkeypatch.setattr(_daemon_io.os, "kill",
                        lambda pid, sig: kills.append((pid, sig)))

    def _detect():
        if _role_of(env) == ["admin"]:
            raise PermissionError("cannot read the daemon socket")
        return 4242
    monkeypatch.setattr(_daemon_io, "_detect_daemon_running", _detect)
    env.kills = kills
    return env


def test_vanishing_daemon_exits_0(vanishing_daemon):
    assert _role_change(vanishing_daemon).code == 0


def test_vanishing_daemon_changed_is_role(vanishing_daemon):
    assert _role_change(vanishing_daemon).json["changed"] == ["role"]


def test_vanishing_daemon_restart_needed(vanishing_daemon):
    assert _role_change(vanishing_daemon).json["daemon"][
        "restart_needed"] is True


def test_vanishing_daemon_reload_not_reloaded(vanishing_daemon):
    assert _role_change(vanishing_daemon).json["daemon"]["reload"] == (
        "not_reloaded")


def test_vanishing_daemon_gets_no_signal(vanishing_daemon):
    _role_change(vanishing_daemon)
    assert vanishing_daemon.kills == []

