"""aipager's hooks and statusLine go where the daemon's sessions read
them (design success criteria 1, 2, 7, 10; amendment A3; probe P1, P2).

Entry points: ``bootstrap_claude_settings`` (daemon start),
``ensure_session_hooks`` (before each launch), ``hooks_wired``,
``settings_patch.plan_settings``/``apply_settings`` (``aipager config``),
``plan_unpatch``/``apply_unpatch`` and ``aipager uninstall``, and the log
lines of logger ``aipager.claude_bootstrap``.
"""

from __future__ import annotations

import argparse
import logging

import pytest

from aipager import claude_bootstrap, claude_paths, updater
from aipager.wizard import settings_patch

from _cfgdir_kit import has_hooks, rjson, wire, wjson

CCD = "CLAUDE_CONFIG_DIR"
LOGGER = "aipager.claude_bootstrap"
NO_HOOKS = "sessions will start without aipager's hooks"
READS_FROM = "sessions read Claude Code's config from"
NOT_ABSOLUTE = "CLAUDE_CONFIG_DIR is not an absolute path"


def _start(cc):
    claude_bootstrap.bootstrap_claude_settings(str(cc.work))


def _logged(caplog, text, level=None) -> bool:
    return any(text in r.getMessage() and (level is None or r.levelno == level)
               for r in caplog.records if r.name == LOGGER)


# ---- daemon start ---------------------------------------------------------------

def test_start_wires_hooks_into_the_daemon_env_config_dir(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert has_hooks(cc.x / "settings.json")


def test_start_wires_the_status_line_into_the_config_dir(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert "statusLine" in rjson(cc.x / "settings.json")


def test_start_wires_hooks_into_the_process_env_config_dir(cc, monkeypatch):
    monkeypatch.setenv(CCD, str(cc.x))
    _start(cc)
    assert has_hooks(cc.x / "settings.json")


def test_start_with_a_config_dir_leaves_the_default_settings_absent(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert not cc.home_settings.exists()


def test_start_with_a_config_dir_leaves_existing_default_settings_untouched(cc):
    raw = '{"model": "opus"}'
    cc.home_settings.write_text(raw)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert cc.home_settings.read_text() == raw


def test_start_without_a_config_dir_wires_the_default_file(cc):
    _start(cc)
    assert has_hooks(cc.home_settings)


def test_start_wires_the_file_a_settings_env_value_moves_to(cc):
    """P2: hooks from the moved file fire, the start file's do not."""
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    _start(cc)
    assert has_hooks(cc.x / "settings.json")


def test_start_does_not_wire_the_start_file_after_a_settings_move(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    _start(cc)
    assert not has_hooks(cc.home_settings)


def test_start_keeps_the_users_own_settings_in_the_config_dir(cc):
    wjson(cc.x / "settings.json", {"model": "opus", "env": {"A": "1"}})
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert rjson(cc.x / "settings.json")["model"] == "opus"


# ---- log lines ----------------------------------------------------------------

def test_start_logs_where_sessions_read_config_with_a_config_dir(cc, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert _logged(caplog, READS_FROM, logging.INFO)


def test_start_does_not_log_a_config_home_without_a_config_dir(cc, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    _start(cc)
    assert not _logged(caplog, READS_FROM)


def test_start_warns_about_a_relative_config_dir(cc, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    monkeypatch.chdir(cc.work)
    monkeypatch.setenv(CCD, "relcfg")
    _start(cc)
    assert _logged(caplog, NOT_ABSOLUTE, logging.WARNING)


def test_start_does_not_warn_about_an_absolute_config_dir(cc, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert not _logged(caplog, NOT_ABSOLUTE)


@pytest.mark.parametrize("content", [b"{not json", b"[1]"], ids=["invalid-json", "list"])
def test_start_says_when_sessions_have_no_hooks(cc, caplog, content):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    (cc.x / "settings.json").write_bytes(content)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert _logged(caplog, NO_HOOKS, logging.WARNING)


def test_start_never_rewrites_a_malformed_settings_file(cc):
    (cc.x / "settings.json").write_bytes(b"{not json")
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert (cc.x / "settings.json").read_bytes() == b"{not json"


def test_start_does_not_say_hookless_when_wiring_worked(cc, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _start(cc)
    assert not _logged(caplog, NO_HOOKS)


# ---- ensure_session_hooks -----------------------------------------------------

def test_ensure_session_hooks_wires_the_paths_settings(cc):
    claude_bootstrap.ensure_session_hooks(claude_paths.locate({CCD: str(cc.x)}))
    assert has_hooks(cc.x / "settings.json")


def test_ensure_session_hooks_reports_success(cc):
    assert claude_bootstrap.ensure_session_hooks(
        claude_paths.locate({CCD: str(cc.x)})) is True


def test_ensure_session_hooks_leaves_the_default_file_alone(cc):
    claude_bootstrap.ensure_session_hooks(claude_paths.locate({CCD: str(cc.x)}))
    assert not cc.home_settings.exists()


def test_ensure_session_hooks_reports_failure_for_a_malformed_file(cc):
    (cc.x / "settings.json").write_text("{bad")
    assert claude_bootstrap.ensure_session_hooks(
        claude_paths.locate({CCD: str(cc.x)})) is False


def test_ensure_session_hooks_warns_for_a_malformed_file(cc, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    (cc.x / "settings.json").write_text("{bad")
    claude_bootstrap.ensure_session_hooks(claude_paths.locate({CCD: str(cc.x)}))
    assert _logged(caplog, NO_HOOKS, logging.WARNING)


def test_a_changed_config_dir_is_wired_before_the_next_launch(cc):
    """design: a CLAUDE_CONFIG_DIR changed in daemon.env since start is
    wired at the next launch, without a restart."""
    _start(cc)
    y = cc.tmp / "elsewhere" / "y"
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(y))
    claude_bootstrap.ensure_session_hooks(claude_paths.for_sessions(cwd=str(cc.work)))
    assert has_hooks(y / "settings.json")


def test_ensure_session_hooks_is_a_no_op_for_a_file_already_wired(cc):
    """A file this process wired is not rewritten on every launch."""
    paths = claude_paths.locate({CCD: str(cc.x)})
    claude_bootstrap.ensure_session_hooks(paths)
    marker = '{"user": "edited after wiring"}'
    (cc.x / "settings.json").write_text(marker)
    claude_bootstrap.ensure_session_hooks(paths)
    assert (cc.x / "settings.json").read_text() == marker


def test_hooks_wired_is_false_for_a_missing_file(cc):
    assert claude_bootstrap.hooks_wired(cc.x / "settings.json") is False


def test_hooks_wired_is_false_for_a_file_without_aipager(cc):
    wjson(cc.x / "settings.json", {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "/usr/bin/true"}]}]}})
    assert claude_bootstrap.hooks_wired(cc.x / "settings.json") is False


def test_hooks_wired_is_false_for_an_invalid_file(cc):
    (cc.x / "settings.json").write_text("{bad")
    assert claude_bootstrap.hooks_wired(cc.x / "settings.json") is False


def test_hooks_wired_is_true_after_aipager_config(cc):
    wire(cc.x / "settings.json")
    assert claude_bootstrap.hooks_wired(cc.x / "settings.json") is True


# ---- aipager config: plan_settings / apply_settings ---------------------------------

def test_plan_settings_defaults_to_the_daemon_env_config_dir(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    assert settings_patch.plan_settings().path == cc.x / "settings.json"


def test_plan_settings_defaults_to_the_shell_config_dir(cc, monkeypatch):
    monkeypatch.setenv(CCD, str(cc.x))
    assert settings_patch.plan_settings().path == cc.x / "settings.json"


def test_plan_settings_defaults_to_the_home_without_a_config_dir(cc):
    assert settings_patch.plan_settings().path == cc.home_settings


def test_plan_settings_follows_a_settings_env_move(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    assert settings_patch.plan_settings().path == cc.x / "settings.json"


def test_aipager_config_patches_the_config_dirs_file(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    settings_patch.apply_settings(settings_patch.plan_settings())
    assert has_hooks(cc.x / "settings.json")


def test_aipager_config_leaves_the_default_file_alone(cc):
    raw = '{"model": "opus"}'
    cc.home_settings.write_text(raw)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    settings_patch.apply_settings(settings_patch.plan_settings())
    assert cc.home_settings.read_text() == raw


def test_plan_settings_with_an_explicit_path_uses_it(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    other = cc.tmp / "explicit" / "settings.json"
    assert settings_patch.plan_settings(other).path == other


def test_plan_unpatch_defaults_to_the_sessions_file(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    assert settings_patch.plan_unpatch().path == cc.x / "settings.json"


# ---- aipager uninstall: both files ---------------------------------------------------

def _uninstall(monkeypatch):
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    monkeypatch.setattr(updater, "_stop_daemon", lambda: None)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    return updater.cmd_uninstall(argparse.Namespace(force=True))


def test_uninstall_cleans_the_config_dirs_file(cc, monkeypatch, capsys):
    wire(cc.x / "settings.json")
    wire(cc.home_settings)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _uninstall(monkeypatch)
    assert not has_hooks(cc.x / "settings.json")


def test_uninstall_cleans_the_default_file_too(cc, monkeypatch, capsys):
    wire(cc.x / "settings.json")
    wire(cc.home_settings)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _uninstall(monkeypatch)
    assert not has_hooks(cc.home_settings)


def test_uninstall_names_both_files(cc, monkeypatch, capsys):
    wire(cc.x / "settings.json")
    wire(cc.home_settings)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _uninstall(monkeypatch)
    out = capsys.readouterr().out.replace("\n", "")
    assert str(cc.x / "settings.json") in out and str(cc.home_settings) in out


def test_uninstall_cleans_a_settings_moved_file(cc, monkeypatch, capsys):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    wire(cc.x / "settings.json")
    _uninstall(monkeypatch)
    assert not has_hooks(cc.x / "settings.json")


def test_uninstall_keeps_the_users_own_settings_in_the_config_dir(cc, monkeypatch, capsys):
    wjson(cc.x / "settings.json", {"model": "opus"})
    wire(cc.x / "settings.json")
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _uninstall(monkeypatch)
    assert rjson(cc.x / "settings.json").get("model") == "opus"
