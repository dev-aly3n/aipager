"""Where Claude Code keeps its files for an environment
(``claude_paths.locate``), against the rules measured with Claude Code
2.1.303 (probe-notes.md P0-P3, P5-P7; design-amendment-1.md A1).

Black-box: only the public resolver API is used, and every folder is a
tmp folder.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aipager import claude_paths

from _cfgdir_kit import wjson

CCD = "CLAUDE_CONFIG_DIR"
OAUTH = "CLAUDE_CODE_CUSTOM_OAUTH_URL"


def _locate(env, cc):
    return claude_paths.locate(env, cwd=str(cc.work))


# ---- config_dir_value: Claude Code's `??` reading (amendment A1) ----------------

@pytest.mark.parametrize("env,want", [
    ({}, None),
    ({CCD: "/srv/claude"}, "/srv/claude"),
    ({CCD: ""}, ""),
    ({CCD: "  "}, "  "),
    ({CCD: "rel"}, "rel"),
], ids=["absent", "absolute", "empty", "whitespace", "relative"])
def test_config_dir_value_is_the_raw_value_when_the_key_is_present(env, want):
    assert claude_paths.config_dir_value(env) == want


# ---- P0 / P1: the default and an absolute CLAUDE_CONFIG_DIR -----------------------

def test_no_config_dir_uses_the_default_home(cc):
    assert _locate({}, cc).config_home == cc.dot_claude


def test_no_config_dir_is_not_from_env(cc):
    assert _locate({}, cc).from_env is False


def test_no_config_dir_global_config_is_the_home_claude_json(cc):
    assert _locate({}, cc).global_config == cc.home / ".claude.json"


@pytest.mark.parametrize("field,name", [
    ("user_settings", "settings.json"),
    ("credentials", ".credentials.json"),
    ("projects", "projects"),
], ids=["settings", "credentials", "projects"])
def test_no_config_dir_files_are_in_the_default_home(cc, field, name):
    assert getattr(_locate({}, cc), field) == cc.dot_claude / name


def test_an_absolute_config_dir_is_the_config_home(cc):
    assert _locate({CCD: str(cc.x)}, cc).config_home == cc.x


def test_an_absolute_config_dir_is_from_env(cc):
    assert _locate({CCD: str(cc.x)}, cc).from_env is True


def test_an_absolute_config_dir_is_not_relative(cc):
    assert _locate({CCD: str(cc.x)}, cc).relative is False


@pytest.mark.parametrize("field,name", [
    ("user_settings", "settings.json"),
    ("credentials", ".credentials.json"),
    ("projects", "projects"),
    ("global_config", ".claude.json"),
], ids=["settings", "credentials", "projects", "global-config"])
def test_an_absolute_config_dir_moves_every_claude_file(cc, field, name):
    """P1: hooks, transcripts and the startup keys all live in $CCD."""
    assert getattr(_locate({CCD: str(cc.x)}, cc), field) == cc.x / name


def test_locate_never_raises_on_a_config_dir_that_does_not_exist(cc):
    gone = cc.tmp / "never-made"
    assert _locate({CCD: str(gone)}, cc).user_settings == gone / "settings.json"


# ---- P3 odd values: no strip, no `~`, relative against the launch folder ----------

def test_an_empty_config_dir_is_the_launch_folder(cc):
    """P3: CLAUDE_CONFIG_DIR="" reads hooks from <cwd>/settings.json."""
    assert _locate({CCD: ""}, cc).user_settings == cc.work / "settings.json"


def test_an_empty_config_dir_is_relative(cc):
    assert _locate({CCD: ""}, cc).relative is True


def test_an_empty_config_dir_leaves_the_global_config_in_the_home(cc):
    """P3: the global config uses `||`, so "" falls back to the home."""
    assert _locate({CCD: ""}, cc).global_config == cc.home / ".claude.json"


def test_a_whitespace_config_dir_is_a_folder_under_the_launch_folder(cc):
    assert _locate({CCD: "  "}, cc).user_settings == cc.work / "  " / "settings.json"


def test_a_whitespace_config_dir_moves_the_global_config_too(cc):
    """P3: `||` keeps a whitespace value (it is not empty)."""
    assert _locate({CCD: "  "}, cc).global_config == cc.work / "  " / ".claude.json"


def test_a_relative_config_dir_resolves_against_cwd(cc):
    assert _locate({CCD: "rel"}, cc).config_home == cc.work / "rel"


def test_a_relative_config_dir_is_flagged_relative(cc):
    assert _locate({CCD: "rel"}, cc).relative is True


def test_a_relative_config_dir_global_config_resolves_against_cwd(cc):
    assert _locate({CCD: "rel"}, cc).global_config == cc.work / "rel" / ".claude.json"


def test_a_tilde_config_dir_is_not_expanded(cc):
    """P3: "~/cfgt" is <cwd>/~/cfgt, not the home."""
    assert _locate({CCD: "~/cfgt"}, cc).config_home == cc.work / "~" / "cfgt"


def test_a_relative_config_dir_without_cwd_uses_the_process_folder(cc, monkeypatch):
    monkeypatch.chdir(cc.work)
    assert claude_paths.locate({CCD: "rel"}).config_home == cc.work / "rel"


# ---- the custom-oauth suffix (P3 e, binary read: any non-empty value) ------------

def test_the_custom_oauth_url_names_the_suffixed_file(cc):
    paths = _locate({CCD: str(cc.x), OAUTH: "https://login.example.com"}, cc)
    assert paths.global_config == cc.x / ".claude-custom-oauth.json"


def test_the_custom_oauth_url_names_the_suffixed_file_in_the_home(cc):
    paths = _locate({OAUTH: "https://login.example.com"}, cc)
    assert paths.global_config == cc.home / ".claude-custom-oauth.json"


def test_an_empty_custom_oauth_url_adds_no_suffix(cc):
    assert _locate({CCD: str(cc.x), OAUTH: ""}, cc).global_config == cc.x / ".claude.json"


def test_a_whitespace_custom_oauth_url_adds_the_suffix(cc):
    """Binary read: `if(process.env.CLAUDE_CODE_CUSTOM_OAUTH_URL)` (no trim)."""
    paths = _locate({CCD: str(cc.x), OAUTH: " "}, cc)
    assert paths.global_config == cc.x / ".claude-custom-oauth.json"


# ---- P3: the legacy `.config.json` rule -----------------------------------------

def _legacy_case(cc, case):
    legacy = cc.x / ".config.json"
    new = cc.x / ".claude.json"
    if case == "nonempty":
        wjson(legacy, {"mcpServers": {}})
        wjson(new, {})
    elif case == "nonempty-no-new":
        wjson(legacy, {"a": 1})
    elif case == "empty-writable-no-new":
        legacy.write_bytes(b"")
        legacy.chmod(0o644)
    elif case == "empty-writable-new-exists":
        legacy.write_bytes(b"")
        legacy.chmod(0o644)
        wjson(new, {})
    elif case == "empty-readonly-new-exists":
        legacy.write_bytes(b"")
        wjson(new, {})
        legacy.chmod(0o444)
    elif case == "empty-readonly-no-new":
        legacy.write_bytes(b"")
        legacy.chmod(0o444)
    elif case == "directory":
        legacy.mkdir()
        (legacy / "x").write_text("{}")
        wjson(new, {})
    elif case == "symlink-to-nonempty":
        target = wjson(cc.tmp / "real-legacy.json", {"a": 1})
        legacy.symlink_to(target)
        wjson(new, {})
    elif case == "dangling-symlink":
        legacy.symlink_to(cc.tmp / "nowhere.json")
        wjson(new, {})
    elif case == "absent":
        wjson(new, {})
    return legacy, new


LEGACY_CASES = [
    ("nonempty", True),
    ("nonempty-no-new", True),
    ("empty-writable-no-new", True),
    ("empty-writable-new-exists", False),
    ("empty-readonly-new-exists", False),
    ("empty-readonly-no-new", False),
    ("directory", False),
    ("symlink-to-nonempty", True),
    ("dangling-symlink", False),
    ("absent", False),
]


@pytest.mark.parametrize("case,used", LEGACY_CASES, ids=[c for c, _ in LEGACY_CASES])
def test_the_legacy_rule_picks_the_file_claude_code_uses(cc, case, used):
    legacy, new = _legacy_case(cc, case)
    want = legacy if used else new
    assert _locate({CCD: str(cc.x)}, cc).global_config == want


@pytest.mark.parametrize("case,used", LEGACY_CASES, ids=[c for c, _ in LEGACY_CASES])
def test_the_legacy_flag_says_whether_the_legacy_file_is_used(cc, case, used):
    _legacy_case(cc, case)
    assert _locate({CCD: str(cc.x)}, cc).legacy is used


def test_a_nonempty_legacy_file_in_the_default_home_is_used(cc):
    """P3 a0: no CLAUDE_CONFIG_DIR, non-empty ~/.claude/.config.json."""
    wjson(cc.dot_claude / ".config.json", {"a": 1})
    wjson(cc.home_json, {})
    assert _locate({}, cc).global_config == cc.dot_claude / ".config.json"


def test_the_custom_oauth_file_is_the_new_style_file_for_the_legacy_rule(cc):
    """P3 f: empty writable .config.json + only the plain .claude.json, with
    a custom OAuth URL: the suffixed file is missing, so .config.json wins."""
    legacy = cc.x / ".config.json"
    legacy.write_bytes(b"")
    legacy.chmod(0o644)
    wjson(cc.x / ".claude.json", {})
    paths = _locate({CCD: str(cc.x), OAUTH: "https://login.example.com"}, cc)
    assert paths.global_config == legacy


def test_the_suffixed_file_wins_over_an_empty_writable_legacy_file_when_it_exists(cc):
    legacy = cc.x / ".config.json"
    legacy.write_bytes(b"")
    legacy.chmod(0o644)
    wjson(cc.x / ".claude-custom-oauth.json", {})
    paths = _locate({CCD: str(cc.x), OAUTH: "https://login.example.com"}, cc)
    assert paths.global_config == cc.x / ".claude-custom-oauth.json"


def test_an_unreadable_config_folder_never_raises(cc):
    locked = cc.tmp / "locked"
    locked.mkdir()
    wjson(locked / ".config.json", {"a": 1})
    locked.chmod(0o000)
    try:
        if os.access(locked, os.R_OK | os.X_OK):
            pytest.skip("chmod 000 does not lock a folder here (root?)")
        paths = _locate({CCD: str(locked)}, cc)
    finally:
        locked.chmod(0o755)
    assert paths.global_config == locked / ".claude.json"


# ---- P2/P5/P6/P7: a CLAUDE_CONFIG_DIR in a settings `env` block -------------------

def _move_by_start_settings(cc, target: Path):
    wjson(cc.home_settings, {"env": {CCD: str(target)}})


def test_a_settings_env_value_moves_the_settings_file(cc):
    """P2: only the moved file's hooks fire."""
    _move_by_start_settings(cc, cc.x)
    assert _locate({}, cc).user_settings == cc.x / "settings.json"


def test_a_settings_env_value_moves_the_credentials(cc):
    """P2d: the fake OAuth file in the moved home logs in."""
    _move_by_start_settings(cc, cc.x)
    assert _locate({}, cc).credentials == cc.x / ".credentials.json"


def test_a_settings_env_value_moves_the_transcripts(cc):
    _move_by_start_settings(cc, cc.x)
    assert _locate({}, cc).projects == cc.x / "projects"


def test_a_settings_env_value_does_not_move_the_global_config(cc):
    """P2: startup keys and MCP servers stay in ~/.claude.json."""
    _move_by_start_settings(cc, cc.x)
    assert _locate({}, cc).global_config == cc.home / ".claude.json"


def test_a_settings_env_move_is_reported(cc):
    _move_by_start_settings(cc, cc.x)
    assert _locate({}, cc).moved_by_settings is True


def test_a_settings_env_move_keeps_the_start_home(cc):
    _move_by_start_settings(cc, cc.x)
    assert _locate({}, cc).start_home == cc.dot_claude


def test_a_settings_env_move_counts_as_from_env(cc):
    _move_by_start_settings(cc, cc.x)
    assert _locate({}, cc).from_env is True


def test_no_settings_env_value_is_not_a_move(cc):
    wjson(cc.home_settings, {"env": {"A1": "x"}})
    assert _locate({}, cc).moved_by_settings is False


def test_a_legacy_file_in_the_moved_home_is_not_used(cc):
    """P5 a: the legacy check uses the START home."""
    _move_by_start_settings(cc, cc.x)
    wjson(cc.x / ".config.json", {"a": 1})
    wjson(cc.home_json, {})
    assert _locate({}, cc).global_config == cc.home / ".claude.json"


def test_a_legacy_file_in_the_start_home_is_used_after_a_move(cc):
    """P5 b."""
    _move_by_start_settings(cc, cc.x)
    wjson(cc.dot_claude / ".config.json", {"a": 1})
    assert _locate({}, cc).global_config == cc.dot_claude / ".config.json"


def test_a_non_text_settings_env_value_does_not_move_anything(cc):
    wjson(cc.home_settings, {"env": {CCD: 7}})
    assert _locate({}, cc).user_settings == cc.home_settings


def test_a_session_env_value_reads_its_own_settings_for_a_move(cc):
    """The start home is $CCD; its settings.json may move it again."""
    y = cc.tmp / "elsewhere" / "y"
    wjson(cc.x / "settings.json", {"env": {CCD: str(y)}})
    assert _locate({CCD: str(cc.x)}, cc).user_settings == y / "settings.json"


def test_a_managed_env_value_moves_the_settings_file(cc):
    cc.managed_file({"env": {CCD: str(cc.x)}})
    assert _locate({}, cc).user_settings == cc.x / "settings.json"


def test_a_managed_env_value_wins_over_the_user_settings_value(cc):
    y = cc.tmp / "elsewhere" / "y"
    _move_by_start_settings(cc, y)
    cc.managed_file({"env": {CCD: str(cc.x)}})
    assert _locate({}, cc).user_settings == cc.x / "settings.json"


def test_a_relative_settings_env_value_resolves_against_cwd(cc):
    _move_by_start_settings(cc, Path("relcfg"))
    assert _locate({}, cc).user_settings == cc.work / "relcfg" / "settings.json"


def test_a_relative_settings_env_value_is_flagged_relative(cc):
    wjson(cc.home_settings, {"env": {CCD: "relcfg"}})
    assert _locate({}, cc).relative is True


# P7: a chain .claude -> c1 -> c2

def _chain(cc):
    c1 = cc.tmp / "chain" / "c1"
    c2 = cc.tmp / "chain" / "c2"
    _move_by_start_settings(cc, c1)
    wjson(c1 / "settings.json", {"env": {CCD: str(c2)}})
    return c1, c2


def test_a_chain_loads_settings_one_hop_only(cc):
    c1, _ = _chain(cc)
    assert _locate({}, cc).user_settings == c1 / "settings.json"


def test_a_chain_reads_credentials_from_the_one_hop_home(cc):
    c1, _ = _chain(cc)
    assert _locate({}, cc).credentials == c1 / ".credentials.json"


def test_a_chain_writes_transcripts_to_the_last_value(cc):
    _, c2 = _chain(cc)
    assert _locate({}, cc).projects == c2 / "projects"


def test_a_chain_leaves_the_global_config_in_the_home(cc):
    _chain(cc)
    assert _locate({}, cc).global_config == cc.home / ".claude.json"


def test_a_cycle_back_to_the_start_home_terminates(cc):
    c1 = cc.tmp / "chain" / "c1"
    _move_by_start_settings(cc, c1)
    wjson(c1 / "settings.json", {"env": {CCD: str(cc.dot_claude)}})
    assert _locate({}, cc).user_settings == c1 / "settings.json"


def test_a_cycle_back_to_the_start_home_puts_transcripts_there(cc):
    c1 = cc.tmp / "chain" / "c1"
    _move_by_start_settings(cc, c1)
    wjson(c1 / "settings.json", {"env": {CCD: str(cc.dot_claude)}})
    assert _locate({}, cc).projects == cc.dot_claude / "projects"


# ---- malformed settings never move or raise ---------------------------------------

@pytest.mark.parametrize("content", [b"{not json", b"[1, 2]", b"\xff\xfe\x00", b""],
                         ids=["invalid-json", "list", "not-utf8", "empty"])
def test_a_malformed_start_settings_file_moves_nothing(cc, content):
    cc.home_settings.write_bytes(content)
    assert _locate({}, cc).user_settings == cc.home_settings


def test_a_malformed_managed_file_moves_nothing(cc):
    (cc.managed).mkdir(parents=True)
    (cc.managed / "managed-settings.json").write_text("{nope")
    assert _locate({}, cc).user_settings == cc.home_settings


# ---- for_sessions: process env plus daemon.env --------------------------------------

def test_for_sessions_reads_daemon_env(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    assert claude_paths.for_sessions(cwd=str(cc.work)).user_settings == cc.x / "settings.json"


def test_for_sessions_reads_the_process_env(cc, monkeypatch):
    monkeypatch.setenv(CCD, str(cc.x))
    assert claude_paths.for_sessions(cwd=str(cc.work)).user_settings == cc.x / "settings.json"


def test_for_sessions_without_any_value_is_the_default_home(cc):
    assert claude_paths.for_sessions(cwd=str(cc.work)).user_settings == cc.home_settings


# ---- hook_settings_candidates ---------------------------------------------------

def test_candidates_list_the_session_file_first(cc):
    assert claude_paths.hook_settings_candidates({CCD: str(cc.x)})[0] == cc.x / "settings.json"


def test_candidates_include_the_default_file(cc):
    assert cc.home_settings in claude_paths.hook_settings_candidates({CCD: str(cc.x)})


def test_candidates_hold_the_default_file_once_without_a_config_dir(cc):
    assert claude_paths.hook_settings_candidates({}) == [cc.home_settings]


def test_candidates_dedupe_by_real_path(cc):
    link = cc.tmp / "link-to-dot-claude"
    link.symlink_to(cc.dot_claude, target_is_directory=True)
    assert len(claude_paths.hook_settings_candidates({CCD: str(link)})) == 1


def test_candidates_include_the_moved_file_after_a_settings_move(cc):
    _move_by_start_settings(cc, cc.x)
    assert cc.x / "settings.json" in claude_paths.hook_settings_candidates({})
