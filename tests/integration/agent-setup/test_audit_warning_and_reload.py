"""Iteration 4:

* ``audit_write_failed``: the owner grant was written but its
  ``grant-owner`` audit record could not be appended. Best effort: exit 0,
  the warning is in JSON ``warnings`` and on plain stderr (entrypoints.md;
  docs/commands.md ``warnings``).
* A live reload that raises when no daemon was detected reports
  ``reload: not_needed`` (fixes-4 behaviour list; docs/commands.md
  ``daemon.reload`` lists ``not_needed``), and ``restart_needed`` stays
  false because no daemon runs.
"""

from __future__ import annotations

import os

import pytest

import aipager.wizard.daemon_io as _daemon_io
from agent_setup_support import CHAT, OTHER_TOKEN, TOKEN, assert_no_secret

NEW_CHAT = 777000111
CODE = "audit_write_failed"


def _codes(r):
    return [w["code"] for w in r.json["warnings"]]


@pytest.fixture
def ro_audit(env, monkeypatch):
    """The audit log lives in a directory that cannot be written."""
    import aipager.audit as audit_mod
    ro = env.tmp / "ro-audit"
    ro.mkdir()
    monkeypatch.setattr(audit_mod, "AUDIT_LOG_PATH", ro / "audit.jsonl")
    os.chmod(ro, 0o500)
    yield env
    os.chmod(ro, 0o700)


def _lock_audit(env, monkeypatch):
    """Lock the audit log after a first, normal install."""
    import aipager.audit as audit_mod
    ro = env.tmp / "ro-audit-later"
    ro.mkdir()
    monkeypatch.setattr(audit_mod, "AUDIT_LOG_PATH", ro / "audit.jsonl")
    os.chmod(ro, 0o500)
    return ro


# ── JSON: fresh owner install with an unwritable audit log ────────────

def test_audit_fail_json_exit_0(ro_audit):
    assert ro_audit.setup().code == 0


def test_audit_fail_json_status_installed(ro_audit):
    assert ro_audit.setup().json["status"] == "installed"


def test_audit_fail_json_ok_true(ro_audit):
    assert ro_audit.setup().json["ok"] is True


def test_audit_fail_json_exit_code_field_0(ro_audit):
    assert ro_audit.setup().json["exit_code"] == 0


def test_audit_fail_json_warning_code(ro_audit):
    assert _codes(ro_audit.setup()) == [CODE]


def test_audit_fail_json_warning_has_a_message(ro_audit):
    w = ro_audit.setup().json["warnings"][0]
    assert isinstance(w["message"], str) and w["message"].strip()


def test_audit_fail_json_changed_is_the_full_install(ro_audit):
    assert ro_audit.setup().json["changed"] == [
        "bot_token", "owner_dm", "settings_json"]


def test_audit_fail_owner_is_on_disk(ro_audit):
    ro_audit.setup()
    roles = [m["role"] for s in ro_audit.config()["scopes"]
             if s["chat_id"] == CHAT for m in s["members"]]
    assert roles == ["owner"]


def test_audit_fail_settings_written(ro_audit):
    ro_audit.setup()
    assert ro_audit.settings_path.exists()


def test_audit_fail_no_audit_file(ro_audit):
    ro_audit.setup()
    assert not ro_audit.audit_path.exists()


def test_audit_fail_json_leaks_no_token(ro_audit):
    r = ro_audit.setup()
    assert_no_secret(r.out + r.err + r.log, "audit_write_failed output")


def test_audit_fail_json_stdout_is_one_object(ro_audit):
    assert isinstance(ro_audit.setup().json, dict)


# ── plain mode ────────────────────────────────────────────────────────

def test_audit_fail_plain_exit_0(ro_audit):
    assert ro_audit.setup(json_out=False).code == 0


def test_audit_fail_plain_stderr_mentions_audit(ro_audit):
    assert "audit" in ro_audit.setup(json_out=False).err.lower()


def test_audit_fail_plain_has_no_already_written_line(ro_audit):
    """Not an error, so no error-after-write line."""
    r = ro_audit.setup(json_out=False)
    assert "Already written" not in r.err + r.out


def test_audit_fail_plain_leaks_no_token(ro_audit):
    r = ro_audit.setup(json_out=False)
    assert_no_secret(r.out + r.err + r.log, "plain audit warning output")


# ── other owner grants ────────────────────────────────────────────────

def test_audit_fail_on_force_chat_change_warns(env, monkeypatch):
    """A new owner DM is a new owner grant."""
    assert env.setup().code == 0
    try:
        ro = _lock_audit(env, monkeypatch)
        r = env.setup("--force", chat=NEW_CHAT)
    finally:
        os.chmod(ro, 0o700)
    assert (r.code, _codes(r)) == (0, [CODE])


def test_audit_fail_on_role_admin_to_owner_warns(env, monkeypatch):
    assert env.setup("--role", "admin").code == 0
    try:
        ro = _lock_audit(env, monkeypatch)
        r = env.setup("--force")
    finally:
        os.chmod(ro, 0o700)
    assert (r.code, _codes(r)) == (0, [CODE])


# ── no grant, no warning ──────────────────────────────────────────────

def test_audit_ok_fresh_install_has_no_warning(env):
    assert CODE not in _codes(env.setup())


def test_audit_locked_admin_install_has_no_warning(ro_audit):
    """--role admin grants no owner, so nothing is audited."""
    assert CODE not in _codes(ro_audit.setup("--role", "admin"))


def test_audit_locked_unchanged_rerun_has_no_warning(env, monkeypatch):
    assert env.setup().code == 0
    try:
        ro = _lock_audit(env, monkeypatch)
        r = env.setup()
    finally:
        os.chmod(ro, 0o700)
    assert (r.json["status"], _codes(r)) == ("unchanged", [])


def test_audit_locked_token_change_has_no_warning(env, monkeypatch):
    """A token swap does not grant the owner again."""
    assert env.setup().code == 0
    try:
        ro = _lock_audit(env, monkeypatch)
        r = env.setup("--force", token_file=env.token_file(
            OTHER_TOKEN, name="o"))
    finally:
        os.chmod(ro, 0o700)
    assert CODE not in _codes(r)


def test_audit_locked_dry_run_has_no_warning(ro_audit):
    r = ro_audit.setup("--dry-run")
    assert (r.code, CODE in _codes(r)) == (0, False)


# ── reload raises while no daemon was detected ────────────────────────

@pytest.fixture
def raising_reload_no_daemon(env, monkeypatch):
    assert env.setup().code == 0
    env.daemon_pid = None

    def _boom():
        env.reloads.append(1)
        raise OSError(f"reload broke near {TOKEN}")
    monkeypatch.setattr(_daemon_io, "_live_reload", _boom)
    return env


def _role_change(env, **kw):
    return env.setup("--force", "--role", "admin", **kw)


def test_no_daemon_reload_raising_exits_0(raising_reload_no_daemon):
    assert _role_change(raising_reload_no_daemon).code == 0


def test_no_daemon_reload_raising_reload_not_needed(raising_reload_no_daemon):
    assert _role_change(raising_reload_no_daemon).json["daemon"][
        "reload"] == "not_needed"


def test_no_daemon_reload_raising_running_false(raising_reload_no_daemon):
    assert _role_change(raising_reload_no_daemon).json["daemon"][
        "running"] is False


def test_no_daemon_reload_raising_restart_not_needed(
        raising_reload_no_daemon):
    assert _role_change(raising_reload_no_daemon).json["daemon"][
        "restart_needed"] is False


def test_no_daemon_reload_raising_status_updated(raising_reload_no_daemon):
    assert _role_change(raising_reload_no_daemon).json["status"] == "updated"


def test_no_daemon_reload_raising_changed_is_role(raising_reload_no_daemon):
    assert _role_change(raising_reload_no_daemon).json["changed"] == ["role"]


def test_no_daemon_reload_raising_on_chat_change_not_needed(
        raising_reload_no_daemon):
    r = raising_reload_no_daemon.setup("--force", chat=NEW_CHAT)
    assert (r.code, r.json["daemon"]["reload"]) == (0, "not_needed")


def test_no_daemon_reload_raising_leaks_no_token(raising_reload_no_daemon):
    r = _role_change(raising_reload_no_daemon)
    assert_no_secret(r.out + r.err + r.log, "no-daemon reload output")


def test_no_daemon_reload_raising_plain_exit_0(raising_reload_no_daemon):
    assert _role_change(raising_reload_no_daemon, json_out=False).code == 0


def test_no_daemon_reload_raising_plain_no_restart_advice(
        raising_reload_no_daemon):
    """No daemon, nothing to restart."""
    r = _role_change(raising_reload_no_daemon, json_out=False)
    assert "aipager service stop" not in r.out + r.err


def test_no_daemon_reload_raising_next_step_no_restart(
        raising_reload_no_daemon):
    assert "aipager service stop" not in (
        _role_change(raising_reload_no_daemon).json["next_step"] or "")
