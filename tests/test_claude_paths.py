"""Where Claude Code keeps its config for an environment (aipager.claude_paths).

Every rule here was measured with Claude Code 2.1.303 in a throwaway home
against a fake provider (probe notes P1-P7): hooks firing from a settings
file, where the transcript landed, which global config `claude mcp list`
read and which one got Claude Code's own start-up keys.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from aipager import claude_paths

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The default config home and global dir in tmp (conftest does the
    same; named here so the tests can write into them)."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", h / ".claude")
    monkeypatch.setattr(claude_paths, "DEFAULT_GLOBAL_DIR", h)
    return h


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def _managed(name: str, data) -> Path:
    return _write(claude_paths.MANAGED_SETTINGS_DIR / name, data)


# ---- the config dir value ----------------------------------------------------------

def test_no_config_dir_means_the_default_home(home):
    p = claude_paths.locate({})
    assert p.config_home == p.start_home == home / ".claude"
    assert p.global_config == home / ".claude.json"
    assert p.user_settings == home / ".claude" / "settings.json"
    assert p.credentials == home / ".claude" / ".credentials.json"
    assert p.projects == home / ".claude" / "projects"
    assert not (p.from_env or p.relative or p.global_relative or p.moved_by_settings
                or p.legacy)


def test_an_absolute_config_dir_moves_every_file(home, tmp_path):
    cfg = tmp_path / "cfg"
    p = claude_paths.locate({"CLAUDE_CONFIG_DIR": str(cfg)})
    assert p.config_home == cfg and p.from_env and not p.relative
    assert p.global_config == cfg / ".claude.json"
    assert p.user_settings == cfg / "settings.json"
    assert p.credentials == cfg / ".credentials.json"
    assert p.projects == cfg / "projects"


def test_a_blank_config_dir_counts_as_unset():
    """Only a missing key is unset: Claude Code reads `??` there."""
    assert claude_paths.config_dir_value({}) is None
    assert claude_paths.config_dir_value({"CLAUDE_CONFIG_DIR": ""}) == ""
    assert claude_paths.config_dir_value({"CLAUDE_CONFIG_DIR": "  "}) == "  "
    assert claude_paths.config_dir_value({"CLAUDE_CONFIG_DIR": 7}) is None


def test_an_empty_config_dir_is_the_launch_folder_but_the_global_config_stays_home(
        home, tmp_path):
    """`""`: settings and transcripts in the session's folder itself
    (`??`), global config in the home folder (`||`). Measured with 2.1.303."""
    folder = tmp_path / "proj"
    p = claude_paths.locate({"CLAUDE_CONFIG_DIR": ""}, cwd=str(folder))
    assert p.config_home == folder and p.relative and p.from_env
    assert p.user_settings == folder / "settings.json"
    assert p.projects == folder / "projects"
    assert p.global_config == home / ".claude.json" and not p.global_relative


def test_a_whitespace_config_dir_is_a_folder_of_that_name(home, tmp_path):
    folder = tmp_path / "proj"
    p = claude_paths.locate({"CLAUDE_CONFIG_DIR": "  "}, cwd=str(folder))
    assert p.config_home == folder / "  " and p.relative
    assert p.global_config == folder / "  " / ".claude.json" and p.global_relative


@pytest.mark.parametrize("value", ["rel", "~/cfg", "./a/b"], ids=["relative", "tilde", "dot"])
def test_a_relative_value_is_taken_against_the_folder(home, tmp_path, value):
    """No `~` expansion: `~/cfg` is a folder named `~` (measured)."""
    folder = tmp_path / "proj"
    p = claude_paths.locate({"CLAUDE_CONFIG_DIR": value}, cwd=str(folder))
    assert p.config_home == Path(os.path.join(str(folder), value))
    assert p.relative and p.global_relative


def test_the_custom_oauth_url_names_another_file(home, tmp_path):
    cfg = tmp_path / "cfg"
    env = {"CLAUDE_CONFIG_DIR": str(cfg), "CLAUDE_CODE_CUSTOM_OAUTH_URL": "https://x"}
    assert claude_paths.locate(env).global_config == cfg / ".claude-custom-oauth.json"
    assert claude_paths.locate({"CLAUDE_CODE_CUSTOM_OAUTH_URL": " "}).global_config == (
        home / ".claude-custom-oauth.json")
    assert claude_paths.locate({"CLAUDE_CODE_CUSTOM_OAUTH_URL": ""}).global_config == (
        home / ".claude.json")


# ---- the legacy .config.json -------------------------------------------------------

def _legacy_case(case: str, cfg: Path) -> None:
    legacy = cfg / ".config.json"
    if case == "nonempty":
        _write(legacy, {"x": 1})
        _write(cfg / ".claude.json", {})
    elif case == "empty-writable-no-new":
        _write(legacy, "")
        legacy.chmod(0o644)
    elif case == "empty-writable-new-exists":
        _write(legacy, "")
        legacy.chmod(0o644)
        _write(cfg / ".claude.json", {})
    elif case == "empty-readonly":
        _write(legacy, "")
        legacy.chmod(0o444)
    elif case == "directory":
        (legacy).mkdir(parents=True)
        _write(legacy / "x", "1")
    elif case == "symlink-to-nonempty":
        target = _write(cfg / "elsewhere.json", {"x": 1})
        legacy.symlink_to(target)
    elif case == "stat-error":
        legacy.symlink_to(cfg / "missing.json")


@pytest.mark.parametrize("case,legacy", [
    ("nonempty", True),
    ("empty-writable-no-new", True),
    ("empty-writable-new-exists", False),
    ("empty-readonly", False),
    ("directory", False),
    ("symlink-to-nonempty", True),
    ("stat-error", False),
], ids=["nonempty", "empty-writable-no-new", "empty-writable-new-exists", "empty-readonly",
        "directory", "symlink-to-nonempty", "stat-error"])
def test_the_legacy_rule(home, tmp_path, case, legacy):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    _legacy_case(case, cfg)
    p = claude_paths.locate({"CLAUDE_CONFIG_DIR": str(cfg)})
    assert p.legacy is legacy
    assert p.global_config == (cfg / ".config.json" if legacy else cfg / ".claude.json")


def test_the_legacy_file_of_the_default_home(home):
    _write(home / ".claude" / ".config.json", {"x": 1})
    _write(home / ".claude.json", {})
    p = claude_paths.locate({})
    assert p.legacy and p.global_config == home / ".claude" / ".config.json"


def test_the_custom_oauth_suffix_is_the_new_style_file_of_the_legacy_rule(home, tmp_path):
    """An empty writable .config.json next to only the plain .claude.json:
    the suffixed file is the one missing, so the legacy file wins (P3 f)."""
    cfg = tmp_path / "cfg"
    _write(cfg / ".config.json", "")
    (cfg / ".config.json").chmod(0o644)
    _write(cfg / ".claude.json", {})
    env = {"CLAUDE_CONFIG_DIR": str(cfg), "CLAUDE_CODE_CUSTOM_OAUTH_URL": "https://x"}
    assert claude_paths.locate(env).legacy
    _write(cfg / ".claude-custom-oauth.json", {})
    assert claude_paths.locate(env).global_config == cfg / ".claude-custom-oauth.json"


# ---- a settings env block that moves the home ---------------------------------------

def test_a_config_dir_in_user_settings_moves_settings_credentials_and_transcripts(
        home, tmp_path):
    """Measured with 2.1.303 (P2): hooks, statusLine, credentials and
    transcripts come from the moved home; the global config does not move."""
    moved = tmp_path / "moved"
    _write(home / ".claude" / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(moved)}})
    p = claude_paths.locate({})
    assert p.moved_by_settings and p.from_env
    assert p.start_home == home / ".claude"
    assert p.config_home == moved
    assert p.user_settings == moved / "settings.json"
    assert p.credentials == moved / ".credentials.json"
    assert p.projects == moved / "projects"
    assert p.global_config == home / ".claude.json"


def test_a_managed_config_dir_moves_the_home_and_wins(home, tmp_path):
    _write(home / ".claude" / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "u")}})
    _managed("managed-settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "m")}})
    p = claude_paths.locate({})
    assert p.config_home == tmp_path / "m" and p.projects == tmp_path / "m" / "projects"


def test_the_move_is_one_hop_for_settings_but_transcripts_follow_the_last_value(
        home, tmp_path):
    """Measured with 2.1.303 (P7): .claude -> c1 -> c2: hooks and the
    credentials from c1, transcripts in c2."""
    c1, c2 = tmp_path / "c1", tmp_path / "c2"
    _write(home / ".claude" / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(c1)}})
    _write(c1 / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(c2)}})
    _write(c2 / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "c3")}})
    p = claude_paths.locate({})
    assert p.user_settings == c1 / "settings.json"
    assert p.credentials == c1 / ".credentials.json"
    assert p.projects == c2 / "projects"


def test_a_config_dir_in_the_session_env_reads_that_homes_settings(home, tmp_path):
    cfg, moved = tmp_path / "cfg", tmp_path / "moved"
    _write(cfg / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(moved)}})
    p = claude_paths.locate({"CLAUDE_CONFIG_DIR": str(cfg)})
    assert p.start_home == cfg and p.config_home == moved
    assert p.global_config == cfg / ".claude.json"


def test_a_settings_value_that_is_not_text_moves_nothing(home):
    _write(home / ".claude" / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": 7}})
    p = claude_paths.locate({})
    assert not p.moved_by_settings and p.config_home == home / ".claude"


def test_the_legacy_check_stays_in_the_start_home(home, tmp_path):
    """Measured with 2.1.303 (P5): a .config.json in the moved home is not read."""
    moved = tmp_path / "moved"
    _write(home / ".claude" / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(moved)}})
    _write(moved / ".config.json", {"x": 1})
    _write(home / ".claude.json", {})
    assert claude_paths.locate({}).global_config == home / ".claude.json"


# ---- the settings layers -----------------------------------------------------------

def test_settings_layers_order_and_precedence(home, tmp_path):
    folder = tmp_path / "proj"
    _write(home / ".claude" / "settings.json", {"env": {"A": "user", "U": "1"}})
    _write(folder / ".claude" / "settings.json", {"env": {"A": "project", "P": "1"}})
    _write(folder / ".claude" / "settings.local.json", {"env": {"A": "local", "L": "1"}})
    _managed("managed-settings.json", {"env": {"A": "managed", "M": "1"}})
    _managed("managed-settings.d/b.json", {"env": {"A": "managed-d-b"}})
    _managed("managed-settings.d/a.json", {"env": {"A": "managed-d-a", "D": "1"}})
    p = claude_paths.locate({}, cwd=str(folder))
    layers = claude_paths.settings_layers(p, str(folder))
    assert [k for k, _ in layers] == ["user", "project", "local", "managed", "managed",
                                      "managed"]
    env = claude_paths.layered_env({"A": "session", "S": "1"}, layers)
    assert env == {"A": "managed-d-b", "S": "1", "U": "1", "P": "1", "L": "1", "M": "1",
                   "D": "1"}


def test_managed_settings_win(home, tmp_path):
    folder = tmp_path / "proj"
    _write(folder / ".claude" / "settings.local.json", {"env": {"K": "local"}})
    _managed("managed-settings.json", {"env": {"K": "managed"}})
    p = claude_paths.locate({}, cwd=str(folder))
    env = claude_paths.layered_env({}, claude_paths.settings_layers(p, str(folder)))
    assert env["K"] == "managed"


def test_both_user_files_apply_when_the_home_moves_the_moved_one_winning(home, tmp_path):
    """Measured with 2.1.303 (P6)."""
    moved = tmp_path / "moved"
    _write(home / ".claude" / "settings.json",
           {"env": {"CLAUDE_CONFIG_DIR": str(moved), "A1": "start", "S": "start"}})
    _write(moved / "settings.json", {"env": {"A2": "eff", "S": "eff"}})
    p = claude_paths.locate({})
    env = claude_paths.layered_env({}, claude_paths.settings_layers(p, None))
    assert env["A1"] == "start" and env["A2"] == "eff" and env["S"] == "eff"


def test_project_flag_false_skips_project_and_local(home, tmp_path):
    folder = tmp_path / "proj"
    _write(folder / ".claude" / "settings.json", {"env": {"P": "1"}})
    p = claude_paths.locate({}, cwd=str(folder))
    assert [k for k, _ in claude_paths.settings_layers(p, str(folder), project=False)] == [
        "user"]
    assert [k for k, _ in claude_paths.settings_layers(p, None)] == ["user"]


def test_settings_env_keeps_only_text(home):
    _write(home / ".claude" / "settings.json",
           {"env": {"A": "x", "N": 1, "L": ["y"], "Z": None}})
    p = claude_paths.locate({})
    assert claude_paths.layered_env({"N": "kept"}, claude_paths.settings_layers(p, None)) == {
        "A": "x", "N": "kept"}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "\"text\"", None, b"\xff\xfe"],
                         ids=["malformed", "array", "string", "missing", "not-utf8"])
def test_an_unreadable_settings_file_gives_nothing(home, content):
    path = home / ".claude" / "settings.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif content is not None:
        path.write_text(content)
    assert claude_paths.read_settings(path) == {}
    p = claude_paths.locate({})
    assert claude_paths.layered_env({"A": "1"}, claude_paths.settings_layers(p, None)) == {
        "A": "1"}


def test_a_settings_file_that_cannot_be_read_gives_nothing(home):
    (home / ".claude" / "settings.json").mkdir()
    assert claude_paths.read_settings(home / ".claude" / "settings.json") == {}


def test_layer_value_is_the_last_one_set(home, tmp_path):
    layers = [("user", {"apiKeyHelper": "a"}), ("project", {}), ("managed", {"apiKeyHelper": "m"})]
    assert claude_paths.layer_value(layers, "apiKeyHelper") == "m"
    assert claude_paths.layer_value([("user", {})], "apiKeyHelper") is None


def test_the_managed_d_filter(home):
    """Measured with 2.1.303: files and symlinks named *.json, not starting
    with a dot, sorted by name."""
    base = claude_paths.MANAGED_SETTINGS_DIR
    _managed("managed-settings.d/20-b.json", {})
    _managed("managed-settings.d/10-a.json", {})
    _managed("managed-settings.d/.hidden.json", {})
    _managed("managed-settings.d/notes.txt", "x")
    (base / "managed-settings.d" / "dir.json").mkdir()
    target = _write(base / "elsewhere.json", {})
    (base / "managed-settings.d" / "30-link.json").symlink_to(target)
    names = [p.name for p in claude_paths.managed_settings_files()]
    assert names == ["10-a.json", "20-b.json", "30-link.json"]


def test_no_managed_folder_means_no_managed_files(home, monkeypatch):
    monkeypatch.setattr(claude_paths, "MANAGED_SETTINGS_DIR", None)
    assert claude_paths.managed_settings_files() == []


@pytest.mark.parametrize("platform,want", [
    ("linux", Path("/etc/claude-code")),
    ("darwin", Path("/Library/Application Support/ClaudeCode")),
    ("win32", None),
])
def test_the_managed_folder_per_os(monkeypatch, platform, want):
    monkeypatch.setattr(sys, "platform", platform)
    assert claude_paths._managed_dir() == want


# ---- hook settings candidates --------------------------------------------------------

def test_hook_settings_candidates_are_deduped_by_real_path(home, tmp_path):
    assert claude_paths.hook_settings_candidates({}) == [home / ".claude" / "settings.json"]
    link = tmp_path / "link"
    link.symlink_to(home / ".claude")
    assert claude_paths.hook_settings_candidates({"CLAUDE_CONFIG_DIR": str(link)}) == [
        link / "settings.json"]


def test_hook_settings_candidates_name_every_file(home, tmp_path):
    cfg, moved = tmp_path / "cfg", tmp_path / "moved"
    _write(cfg / "settings.json", {"env": {"CLAUDE_CONFIG_DIR": str(moved)}})
    assert claude_paths.hook_settings_candidates({"CLAUDE_CONFIG_DIR": str(cfg)}) == [
        moved / "settings.json", cfg / "settings.json", home / ".claude" / "settings.json"]


def test_for_sessions_reads_daemon_env(home, tmp_path, monkeypatch):
    from aipager import daemon_secrets
    cfg = tmp_path / "from-daemon-env"
    env_file = _write(tmp_path / "daemon.env", f"CLAUDE_CONFIG_DIR={cfg}\n")
    monkeypatch.setattr(daemon_secrets, "DAEMON_ENV_PATH", env_file)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    assert claude_paths.for_sessions().config_home == cfg


def test_locate_never_raises(home):
    p = claude_paths.locate({"CLAUDE_CONFIG_DIR": "/a\x00b"})
    assert p.global_config.name == ".claude.json"


# ---- isolation ------------------------------------------------------------------------

def test_the_module_imports_only_the_stdlib_at_module_level():
    """It is imported by modules the hook may one day load: never
    aipager.i18n, never anything that is not the standard library."""
    tree = ast.parse((ROOT / "aipager" / "claude_paths.py").read_text())
    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert all(n.split(".")[0] in sys.stdlib_module_names for n in names), names
