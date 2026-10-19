"""aipager writes nothing into a relative Claude Code config home
(entrypoints "Env vars": "aipager writes nothing into a relative config
home"; "Side effects": "Nothing is written into a relative config home
(daemon, wizard, setup)"; review-1 rev-iter1-003; probe P3: ``""`` is the
launch folder itself, whitespace and ``rel`` are folders under it).

Entry points: ``settings_patch.relative_home``, ``plan_settings``,
``aipager setup --json`` (error ``settings_invalid``),
``bootstrap_claude_settings`` and ``ensure_session_hooks``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aipager import claude_bootstrap, claude_paths
from aipager.wizard import settings_patch

from _cfgdir_kit import has_hooks, wjson

CCD = "CLAUDE_CONFIG_DIR"
RELATIVE = ["", "  ", "relcfg"]
RELATIVE_IDS = ["empty", "whitespace", "relative"]


def _target(cc, value: str) -> Path:
    """The settings file Claude Code would read for *value* (P3)."""
    return (cc.work / value / "settings.json") if value else (cc.work / "settings.json")


@pytest.fixture
def in_work(cc, monkeypatch):
    """Run from the launch folder, the way a user runs ``aipager config``
    from a project root."""
    monkeypatch.chdir(cc.work)
    return cc


# ---- relative_home ------------------------------------------------------------------

@pytest.mark.parametrize("value", RELATIVE, ids=RELATIVE_IDS)
def test_relative_home_names_a_problem_for_a_shell_value(in_work, monkeypatch, value):
    monkeypatch.setenv(CCD, value)
    assert settings_patch.relative_home() is not None


@pytest.mark.parametrize("value", RELATIVE, ids=RELATIVE_IDS)
def test_relative_home_gives_a_problem_and_a_fix(in_work, monkeypatch, value):
    monkeypatch.setenv(CCD, value)
    problem, fix = settings_patch.relative_home()
    assert problem.strip() and fix.strip()


def test_relative_home_names_a_problem_for_a_daemon_env_value(in_work):
    in_work.set_daemon_env(CLAUDE_CONFIG_DIR="relcfg")
    assert settings_patch.relative_home() is not None


def test_relative_home_names_a_problem_for_a_settings_env_move(in_work):
    """A relative value in the start home's settings ``env`` moves the
    settings file into the launch folder too (P2 + P3)."""
    wjson(in_work.home_settings, {"env": {CCD: "relcfg"}})
    assert settings_patch.relative_home() is not None


def test_relative_home_is_none_for_an_absolute_value(in_work, monkeypatch):
    monkeypatch.setenv(CCD, str(in_work.x))
    assert settings_patch.relative_home() is None


def test_relative_home_is_none_with_nothing_set(in_work):
    assert settings_patch.relative_home() is None


# ---- plan_settings (aipager config) ------------------------------------------------

@pytest.mark.parametrize("value", RELATIVE, ids=RELATIVE_IDS)
def test_plan_settings_refuses_a_relative_home(in_work, monkeypatch, value):
    monkeypatch.setenv(CCD, value)
    with pytest.raises(ValueError, match="not an absolute path"):
        settings_patch.plan_settings()


@pytest.mark.parametrize("value", RELATIVE, ids=RELATIVE_IDS)
def test_plan_settings_writes_nothing_into_the_launch_folder(in_work, monkeypatch, value):
    monkeypatch.setenv(CCD, value)
    try:
        settings_patch.apply_settings(settings_patch.plan_settings())
    except ValueError:
        pass
    assert not _target(in_work, value).exists()


def test_plan_settings_leaves_a_projects_own_settings_file_alone(in_work, monkeypatch):
    """The review's case: ``CLAUDE_CONFIG_DIR=`` and a project root that
    happens to hold a ``settings.json``."""
    raw = '{"project": "own file"}'
    (in_work.work / "settings.json").write_text(raw)
    monkeypatch.setenv(CCD, "")
    try:
        settings_patch.apply_settings(settings_patch.plan_settings())
    except ValueError:
        pass
    assert (in_work.work / "settings.json").read_text() == raw


def test_plan_settings_with_a_relative_home_writes_no_default_file(in_work, monkeypatch):
    monkeypatch.setenv(CCD, "relcfg")
    try:
        settings_patch.apply_settings(settings_patch.plan_settings())
    except ValueError:
        pass
    assert not in_work.home_settings.exists()


def test_plan_settings_with_an_explicit_path_still_works_for_a_relative_home(in_work, monkeypatch):
    """Only the default (no *path*) refuses."""
    monkeypatch.setenv(CCD, "relcfg")
    other = in_work.tmp / "explicit" / "settings.json"
    assert settings_patch.plan_settings(other).path == other


def test_plan_settings_with_an_absolute_home_does_not_refuse(in_work, monkeypatch):
    monkeypatch.setenv(CCD, str(in_work.x))
    assert settings_patch.plan_settings().path == in_work.x / "settings.json"


# ---- daemon start and ensure_session_hooks ------------------------------------------

@pytest.mark.parametrize("value", RELATIVE, ids=RELATIVE_IDS)
def test_start_writes_no_hooks_into_a_relative_home(in_work, monkeypatch, value):
    monkeypatch.setenv(CCD, value)
    claude_bootstrap.bootstrap_claude_settings(str(in_work.work))
    assert not _target(in_work, value).exists()


def test_start_with_a_relative_home_leaves_a_projects_settings_file_alone(in_work, monkeypatch):
    raw = '{"project": "own file"}'
    (in_work.work / "settings.json").write_text(raw)
    monkeypatch.setenv(CCD, "")
    claude_bootstrap.bootstrap_claude_settings(str(in_work.work))
    assert (in_work.work / "settings.json").read_text() == raw


@pytest.mark.parametrize("value", RELATIVE, ids=RELATIVE_IDS)
def test_ensure_session_hooks_writes_nothing_into_a_relative_home(in_work, value):
    claude_bootstrap.ensure_session_hooks(
        claude_paths.locate({CCD: value}, cwd=str(in_work.work)))
    assert not _target(in_work, value).exists()


def test_ensure_session_hooks_reports_no_hooks_for_a_relative_home(in_work):
    assert claude_bootstrap.ensure_session_hooks(
        claude_paths.locate({CCD: "relcfg"}, cwd=str(in_work.work))) is False


def test_ensure_session_hooks_keeps_a_relative_homes_file_byte_identical(in_work):
    raw = '{"project": "own file"}'
    (in_work.work / "settings.json").write_text(raw)
    claude_bootstrap.ensure_session_hooks(
        claude_paths.locate({CCD: ""}, cwd=str(in_work.work)))
    assert (in_work.work / "settings.json").read_text() == raw


def test_ensure_session_hooks_wires_an_absolute_home_from_the_same_folder(in_work):
    """Control for the relative cases above: the same call does wire."""
    claude_bootstrap.ensure_session_hooks(
        claude_paths.locate({CCD: str(in_work.x)}, cwd=str(in_work.work)))
    assert has_hooks(in_work.x / "settings.json")
