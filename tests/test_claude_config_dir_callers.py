"""Every aipager spot that reads or writes one of Claude Code's own files
follows ``CLAUDE_CONFIG_DIR`` (aipager.claude_paths), here set the way an
operator sets it for the daemon: in aipager's daemon.env.

The config dir is a tmp folder OUTSIDE the tmp home, so a caller still
reading ``~/.claude`` fails these tests instead of passing by accident.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import (
    claude_bootstrap, claude_paths, claude_resolve, daemon_secrets, self_update, setup_cmd,
    updater,
)
from aipager.dtach import inject
from aipager.wizard import settings_patch

HOOK = "/opt/aipager/bin/aipager-hook"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """CLAUDE_CONFIG_DIR=<tmp>/claude-config in daemon.env; the default
    home (the conftest's tmp home) stays where it is."""
    for var in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "CREDENTIALS_DIRECTORY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    folder = tmp_path / "claude-config"
    folder.mkdir()
    _set_daemon_env(folder)
    return folder


def _set_daemon_env(folder: Path) -> None:
    daemon_secrets.DAEMON_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    daemon_secrets.DAEMON_ENV_PATH.write_text(f"CLAUDE_CONFIG_DIR={folder}\n")


def _default() -> Path:
    return claude_paths.DEFAULT_CONFIG_HOME


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def _hooks_on_path(monkeypatch, found: bool = True) -> None:
    monkeypatch.setattr(claude_bootstrap, "_resolve",
                        lambda cmd: f"/opt/aipager/bin/{cmd}" if found else None)


def _has_our_hook(path: Path) -> bool:
    return claude_bootstrap.hooks_wired(path)


# ---- daemon start and launches wire the sessions' settings file ----------------------

def test_start_wires_the_config_dirs_settings(cfg, monkeypatch, caplog):
    _hooks_on_path(monkeypatch)
    caplog.set_level(logging.INFO, logger="aipager.claude_bootstrap")

    claude_bootstrap.bootstrap_claude_settings(str(cfg.parent))

    assert _has_our_hook(cfg / "settings.json")
    settings = json.loads((cfg / "settings.json").read_text())
    assert settings["skipDangerousModePermissionPrompt"] is True
    assert settings["statusLine"]["command"].endswith("aipager-statusline")
    assert not (_default() / "settings.json").exists()
    assert "sessions read Claude Code's config from" in caplog.text
    assert "sessions will start without aipager's hooks" not in caplog.text


def test_start_says_nothing_about_a_config_dir_when_none_is_set(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    _hooks_on_path(monkeypatch)
    caplog.set_level(logging.INFO, logger="aipager.claude_bootstrap")

    claude_bootstrap.bootstrap_claude_settings(str(tmp_path))

    assert _has_our_hook(_default() / "settings.json")
    assert "sessions read Claude Code's config from" not in caplog.text


@pytest.mark.parametrize("why", ["hook-not-on-path", "file-not-json"])
def test_start_says_when_sessions_have_no_hooks(cfg, monkeypatch, caplog, why):
    _hooks_on_path(monkeypatch, found=why != "hook-not-on-path")
    if why == "file-not-json":
        _write(cfg / "settings.json", "{not json")

    claude_bootstrap.bootstrap_claude_settings(str(cfg.parent))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("sessions will start without aipager's hooks" in w
               and str(cfg / "settings.json") in w for w in warnings), warnings


def test_start_with_a_relative_config_dir_writes_nothing_and_says_so(
        tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    daemon_secrets.DAEMON_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    daemon_secrets.DAEMON_ENV_PATH.write_text("CLAUDE_CONFIG_DIR=rel\n")
    _hooks_on_path(monkeypatch)
    work = tmp_path / "work"
    work.mkdir()

    claude_bootstrap.bootstrap_claude_settings(str(work))

    assert not (work / "rel").exists()
    assert "CLAUDE_CONFIG_DIR is not an absolute path" in caplog.text
    assert "sessions will start without aipager's hooks" in caplog.text


def test_a_config_dir_set_in_settings_wires_the_moved_file(tmp_path, monkeypatch):
    """Measured with 2.1.303 (P2): a CLAUDE_CONFIG_DIR in ~/.claude/settings.json's
    env moves the settings file the sessions read hooks from."""
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    moved = tmp_path / "moved"
    _write(_default() / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(moved)}})
    _hooks_on_path(monkeypatch)

    claude_bootstrap.bootstrap_claude_settings(str(tmp_path))

    assert _has_our_hook(moved / "settings.json")
    assert not _has_our_hook(_default() / "settings.json")


def test_a_changed_config_dir_is_wired_before_the_next_launch(cfg, tmp_path, monkeypatch):
    _hooks_on_path(monkeypatch)
    claude_bootstrap.bootstrap_claude_settings(str(tmp_path))
    first = claude_paths.for_sessions()
    assert _has_our_hook(first.user_settings)

    # A file this process wired is not written again at each launch.
    _write(first.user_settings, {"theirs": True})
    assert claude_bootstrap.ensure_session_hooks(first) is True
    assert json.loads(first.user_settings.read_text()) == {"theirs": True}

    # daemon.env now names another folder: wired at the next launch.
    other = tmp_path / "other-config"
    _set_daemon_env(other)
    second = claude_paths.for_sessions()
    assert second.user_settings == other / "settings.json"
    assert claude_bootstrap.ensure_session_hooks(second) is True
    assert _has_our_hook(other / "settings.json")


def test_a_launch_whose_wiring_fails_says_so(cfg, monkeypatch, caplog):
    _hooks_on_path(monkeypatch, found=False)
    assert claude_bootstrap.ensure_session_hooks(claude_paths.for_sessions()) is False
    assert "sessions will start without aipager's hooks" in caplog.text


# ---- launch_session ----------------------------------------------------------------

def _spawn_recorder(monkeypatch, seen: dict):
    async def _fake_exec(*args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs.get("env")
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    monkeypatch.setattr(inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(inject.Path, "is_socket", _is_socket)


def _creds(expires_in_s: float) -> dict:
    return {"claudeAiOauth": {"accessToken": "fake-access", "refreshToken": "fake-refresh",
                              "expiresAt": int((time.time() + expires_in_s) * 1000)}}


def test_launch_reads_the_config_dirs_credentials(cfg, tmp_path, monkeypatch, run_async):
    _hooks_on_path(monkeypatch)
    _write(cfg / ".credentials.json", _creds(3600))
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen)

    ok, err = run_async(inject.launch_session("cd1", mode="ask", cwd=str(tmp_path)))

    assert ok, err
    assert "unset CLAUDE_CODE_OAUTH_TOKEN" in seen["args"][-1]
    assert _has_our_hook(cfg / "settings.json")


def test_launch_ignores_the_default_homes_credentials(cfg, tmp_path, monkeypatch, run_async):
    _hooks_on_path(monkeypatch)
    _write(_default() / ".credentials.json", _creds(3600))
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen)

    ok, err = run_async(inject.launch_session("cd2", mode="ask", cwd=str(tmp_path)))

    assert ok, err
    assert "unset CLAUDE_CODE_OAUTH_TOKEN" not in seen["args"][-1]


def test_launch_stashes_only_the_config_dirs_expired_credentials(
        cfg, tmp_path, monkeypatch, run_async):
    """The gate stays on the daemon's own CLAUDE_CODE_OAUTH_TOKEN."""
    _hooks_on_path(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-env-token")
    _write(cfg / ".credentials.json", _creds(-3600))
    default_creds = _write(_default() / ".credentials.json", _creds(-3600))
    _spawn_recorder(monkeypatch, {})

    ok, err = run_async(inject.launch_session("cd3", mode="ask", cwd=str(tmp_path)))

    assert ok, err
    assert (cfg / ".credentials.json.stale").exists()
    assert not (cfg / ".credentials.json").exists()
    assert default_creds.exists()


@pytest.mark.parametrize("where,kept", [("config-dir", True), ("default-home", False)])
def test_resume_looks_in_the_config_dirs_projects(cfg, tmp_path, monkeypatch, run_async,
                                                   where, kept):
    _hooks_on_path(monkeypatch)
    sid = "0b8c3a1e-1111-2222-3333-444455556666"
    root = cfg if where == "config-dir" else _default()
    _write(root / "projects" / "-some-folder" / f"{sid}.jsonl", "{}\n")
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen)

    ok, err = run_async(inject.launch_session("cd4", mode="ask", resume_id=sid,
                                              cwd=str(tmp_path)))

    assert ok, err
    assert (f"--resume {sid}" in seen["args"][-1]) is kept


def test_launch_answers_in_the_config_dirs_global_config(cfg, tmp_path, monkeypatch, run_async):
    _hooks_on_path(monkeypatch)
    folder = tmp_path / "proj"
    folder.mkdir()
    _spawn_recorder(monkeypatch, {})

    ok, err = run_async(inject.launch_session("cd5", mode="ask", cwd=str(folder)))

    assert ok, err
    data = json.loads((cfg / ".claude.json").read_text())
    assert data["projects"][str(folder.resolve())]["hasTrustDialogAccepted"] is True
    assert not (claude_paths.DEFAULT_GLOBAL_DIR / ".claude.json").exists()


# ---- the credential probe's transcript cleanup ---------------------------------------

def test_probe_cleanup_never_leaves_the_resolved_projects_folder(tmp_path, monkeypatch):
    cfg = tmp_path / "probe-config"
    seen = {}

    def _fake(claude_path, env, cwd, timeout):
        seen["cwd"] = cwd
        slug = claude_resolve._probe_project_dir(cwd).name
        _write(cfg / "projects" / slug / "s.jsonl", "{}")
        # A same-named folder in the default home is not this probe's.
        _write(_default() / "projects" / slug / "s.jsonl", "precious")
        return 0, "ok"

    monkeypatch.setattr(claude_resolve, "_run_probe", _fake)

    assert claude_resolve.validate_credential(
        "/x/claude", {"CLAUDE_CONFIG_DIR": str(cfg)}).state == "valid"

    slug = claude_resolve._probe_project_dir(seen["cwd"]).name
    assert not (cfg / "projects" / slug).exists()
    assert (_default() / "projects" / slug / "s.jsonl").read_text() == "precious"


# ---- start-up auth, update channel ---------------------------------------------------

def test_recover_auth_finds_the_config_dirs_stored_login(tmp_path, monkeypatch):
    import aipager.service as service
    monkeypatch.setattr(service, "_discover_token_via_login_shell", lambda: None)
    monkeypatch.setattr(daemon_secrets, "DAEMON_ENV_PATH", tmp_path / "absent.env")
    cfg = tmp_path / "claude-config"
    _write(cfg / ".credentials.json", _creds(3600))

    _, found = claude_bootstrap._recover_auth("/x/claude", "2.1.303",
                                              {"CLAUDE_CONFIG_DIR": str(cfg)})
    assert "stored login" in found
    _, found = claude_bootstrap._recover_auth("/x/claude", "2.1.303", {})
    assert "stored login" not in found


def test_auth_source_is_the_config_dirs_file(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_resolve.platform, "system", lambda: "Linux")
    cfg = tmp_path / "claude-config"
    _write(cfg / ".credentials.json", _creds(3600))
    assert claude_resolve._attribute_source({"CLAUDE_CONFIG_DIR": str(cfg)}) == "file"
    assert claude_resolve._attribute_source({}) == "unknown"


def test_claude_channel_info_reads_the_config_dir(cfg):
    _write(cfg / "settings.json", {"autoUpdatesChannel": "stable"})
    _write(cfg / ".claude.json", {"installMethod": "native"})
    _write(_default() / "settings.json", {"autoUpdatesChannel": "rc"})

    assert self_update.claude_channel_info() == ("native", "stable")


# ---- the wizard, setup and uninstall ---------------------------------------------------

def test_plan_settings_and_setup_name_the_config_dirs_file(cfg):
    plan = settings_patch.plan_settings()
    assert plan.path == cfg / "settings.json"
    settings_patch.apply_settings(plan)
    assert (cfg / "settings.json").exists()
    assert not (_default() / "settings.json").exists()

    doc = setup_cmd._base_doc("setup", argparse.Namespace())
    assert doc["settings_json"]["path"] == str(cfg / "settings.json")


@pytest.mark.parametrize("value", ["rel", "", " "], ids=["relative", "empty", "space"])
def test_the_wizard_writes_no_hooks_into_a_relative_config_dir(
        tmp_path, monkeypatch, capsys, value):
    """A relative CLAUDE_CONFIG_DIR (an empty one is the folder itself)
    would put aipager's hooks into a settings.json inside the folder
    `aipager config` runs from: the wizard writes nothing and says why,
    as the daemon does."""
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", value)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    with pytest.raises(ValueError, match="not an absolute path"):
        settings_patch.plan_settings()
    settings_patch._step_settings()

    assert sorted(work.rglob("*")) == []
    assert not (_default() / "settings.json").exists()
    out = " ".join(capsys.readouterr().err.split())
    assert "not an absolute path" in out
    assert "Set an absolute CLAUDE_CONFIG_DIR" in out


def test_the_wizard_still_writes_an_absolute_config_dir(cfg, capsys):
    """Control for the test above: an absolute value is patched."""
    settings_patch._step_settings()
    assert _has_our_hook(cfg / "settings.json")


def test_uninstall_cleans_both_settings_files(cfg, monkeypatch, capsys):
    ours = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": HOOK}]}]},
            "model": "opus"}
    _write(cfg / "settings.json", ours)
    _write(_default() / "settings.json", ours)
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    monkeypatch.setattr(updater, "_stop_daemon", lambda: None)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)

    assert updater.cmd_uninstall(argparse.Namespace(force=True)) == 0

    for path in (cfg / "settings.json", _default() / "settings.json"):
        assert json.loads(path.read_text()) == {"model": "opus"}, path
