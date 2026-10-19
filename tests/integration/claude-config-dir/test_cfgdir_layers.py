"""The settings ``env`` a session gets: user < project < local < managed
files (brief; design "settings_layers"), both user files after a move
(P6), the managed folder's file filter (P3 binary read), and malformed
files contributing nothing (design success criterion 5)."""

from __future__ import annotations

import os

import pytest

from aipager import claude_bootstrap, claude_paths

from _cfgdir_kit import wjson

CCD = "CLAUDE_CONFIG_DIR"


def _env(cc, *, folder=True, project=True, base=None):
    paths = claude_paths.locate(base or {}, cwd=str(cc.work))
    layers = claude_paths.settings_layers(paths, str(cc.work) if folder else None,
                                          project=project)
    return claude_paths.layered_env(base or {}, layers)


# ---- read_settings -----------------------------------------------------------------

@pytest.mark.parametrize("content", [b"{bad", b"[1]", b"\"text\"", b"\xff\xfe", b""],
                         ids=["invalid-json", "list", "string", "not-utf8", "empty"])
def test_read_settings_gives_an_empty_dict_for_a_bad_file(cc, content):
    p = cc.tmp / "s.json"
    p.write_bytes(content)
    assert claude_paths.read_settings(p) == {}


def test_read_settings_gives_an_empty_dict_for_a_missing_file(cc):
    assert claude_paths.read_settings(cc.tmp / "absent.json") == {}


def test_read_settings_gives_an_empty_dict_for_a_directory(cc):
    assert claude_paths.read_settings(cc.tmp) == {}


def test_read_settings_gives_an_empty_dict_for_an_unreadable_file(cc):
    p = wjson(cc.tmp / "locked.json", {"a": 1})
    p.chmod(0o000)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("chmod 000 does not lock a file here (root?)")
        got = claude_paths.read_settings(p)
    finally:
        p.chmod(0o600)
    assert got == {}


def test_read_settings_returns_the_object(cc):
    p = wjson(cc.tmp / "ok.json", {"env": {"A": "1"}})
    assert claude_paths.read_settings(p) == {"env": {"A": "1"}}


# ---- precedence: user < project < local < managed ----------------------------------

def test_user_settings_env_is_laid_over_the_session_env(cc):
    wjson(cc.home_settings, {"env": {"V": "user"}})
    assert _env(cc, base={"V": "session"})["V"] == "user"


def test_project_wins_over_user(cc):
    wjson(cc.home_settings, {"env": {"V": "user"}})
    cc.project({"env": {"V": "project"}})
    assert _env(cc)["V"] == "project"


def test_local_wins_over_project(cc):
    cc.project({"env": {"V": "project"}})
    cc.project({"env": {"V": "local"}}, local=True)
    assert _env(cc)["V"] == "local"


def test_managed_wins_over_local(cc):
    cc.project({"env": {"V": "local"}}, local=True)
    cc.managed_file({"env": {"V": "managed"}})
    assert _env(cc)["V"] == "managed"


def test_managed_d_wins_over_managed_json(cc):
    cc.managed_file({"env": {"V": "managed-json"}})
    cc.managed_file({"env": {"V": "d-a"}}, "a.json")
    assert _env(cc)["V"] == "d-a"


def test_managed_d_files_apply_in_name_order(cc):
    cc.managed_file({"env": {"V": "d-b"}}, "b.json")
    cc.managed_file({"env": {"V": "d-a"}}, "a.json")
    assert _env(cc)["V"] == "d-b"


def test_lower_layers_keys_survive(cc):
    wjson(cc.home_settings, {"env": {"U": "user"}})
    cc.managed_file({"env": {"M": "managed"}})
    assert _env(cc)["U"] == "user"


def test_project_is_skipped_when_project_is_false(cc):
    cc.project({"env": {"V": "project"}})
    assert "V" not in _env(cc, project=False)


def test_local_is_skipped_when_project_is_false(cc):
    cc.project({"env": {"V": "local"}}, local=True)
    assert "V" not in _env(cc, project=False)


def test_project_is_skipped_without_a_folder(cc):
    cc.project({"env": {"V": "project"}})
    assert "V" not in _env(cc, folder=False)


def test_managed_still_applies_without_a_folder(cc):
    cc.managed_file({"env": {"V": "managed"}})
    assert _env(cc, folder=False)["V"] == "managed"


# P6: after a move both user files apply, the moved one wins

def test_after_a_move_the_start_files_env_still_applies(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x), "A1": "start"}})
    wjson(cc.x / "settings.json", {"env": {"A2": "eff"}})
    assert _env(cc, folder=False)["A1"] == "start"


def test_after_a_move_the_moved_files_env_applies(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x), "A1": "start"}})
    wjson(cc.x / "settings.json", {"env": {"A2": "eff"}})
    assert _env(cc, folder=False)["A2"] == "eff"


def test_after_a_move_the_moved_file_wins(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x), "SHARED": "start"}})
    wjson(cc.x / "settings.json", {"env": {"SHARED": "eff"}})
    assert _env(cc, folder=False)["SHARED"] == "eff"


# ---- only text values ---------------------------------------------------------------

@pytest.mark.parametrize("value", [7, True, None, ["a"], {"a": "b"}],
                         ids=["int", "bool", "null", "list", "object"])
def test_a_non_text_env_value_is_dropped(cc, value):
    wjson(cc.home_settings, {"env": {"V": value}})
    assert "V" not in _env(cc, folder=False)


def test_a_non_text_env_value_does_not_hide_a_lower_layer(cc):
    wjson(cc.home_settings, {"env": {"V": "user"}})
    cc.managed_file({"env": {"V": 7}})
    assert _env(cc, folder=False)["V"] == "user"


@pytest.mark.parametrize("env_block", [[1], "x", 3, None], ids=["list", "string", "int", "null"])
def test_an_env_block_that_is_not_an_object_contributes_nothing(cc, env_block):
    wjson(cc.home_settings, {"env": env_block})
    assert _env(cc, folder=False, base={"B": "1"}) == {"B": "1"}


def test_a_malformed_project_file_contributes_nothing(cc):
    (cc.work / ".claude").mkdir()
    (cc.work / ".claude" / "settings.json").write_text("{bad")
    cc.project({"env": {"L": "local"}}, local=True)
    assert _env(cc)["L"] == "local"


def test_a_malformed_managed_file_contributes_nothing(cc):
    cc.managed.mkdir(parents=True)
    (cc.managed / "managed-settings.json").write_text("{bad")
    wjson(cc.home_settings, {"env": {"V": "user"}})
    assert _env(cc)["V"] == "user"


def test_layered_env_does_not_change_its_input(cc):
    wjson(cc.home_settings, {"env": {"V": "user"}})
    base = {"B": "1"}
    _env(cc, folder=False, base=base)
    assert base == {"B": "1"}


# ---- layer_value -----------------------------------------------------------------

def test_layer_value_is_the_last_layer_that_sets_it(cc):
    wjson(cc.home_settings, {"apiKeyHelper": "/home/alice/bin/user-key"})
    cc.managed_file({"apiKeyHelper": "/opt/managed-key"})
    paths = claude_paths.locate({}, cwd=str(cc.work))
    layers = claude_paths.settings_layers(paths, None)
    assert claude_paths.layer_value(layers, "apiKeyHelper") == "/opt/managed-key"


def test_layer_value_is_none_when_no_layer_sets_it(cc):
    wjson(cc.home_settings, {"env": {}})
    paths = claude_paths.locate({}, cwd=str(cc.work))
    layers = claude_paths.settings_layers(paths, None)
    assert claude_paths.layer_value(layers, "apiKeyHelper") is None


def test_layer_value_skips_a_layer_without_the_key(cc):
    wjson(cc.home_settings, {"apiKeyHelper": "/home/alice/bin/user-key"})
    cc.managed_file({"env": {"X": "1"}})
    paths = claude_paths.locate({}, cwd=str(cc.work))
    layers = claude_paths.settings_layers(paths, None)
    assert claude_paths.layer_value(layers, "apiKeyHelper") == "/home/alice/bin/user-key"


# ---- the managed folder: which files count (P3 binary read) -------------------------

def test_managed_files_are_empty_when_the_folder_is_absent(cc):
    assert claude_paths.managed_settings_files() == []


def test_managed_json_comes_first(cc):
    cc.managed_file({}, "a.json")
    main = cc.managed_file({})
    assert claude_paths.managed_settings_files()[0] == main


def test_managed_d_entries_are_sorted_by_name(cc):
    for n in ("20-b.json", "10-a.json", "30-c.json"):
        cc.managed_file({}, n)
    names = [p.name for p in claude_paths.managed_settings_files()]
    assert names == ["10-a.json", "20-b.json", "30-c.json"]


@pytest.mark.parametrize("name", ["notes.txt", ".hidden.json", "a.json.bak", "JSON"],
                         ids=["not-json", "dotfile", "backup", "no-suffix"])
def test_managed_d_skips_names_claude_code_skips(cc, name):
    cc.managed_file({}, "keep.json")
    cc.managed_file({}, name)
    assert [p.name for p in claude_paths.managed_settings_files()] == ["keep.json"]


def test_managed_d_skips_a_directory_named_json(cc):
    cc.managed_file({}, "keep.json")
    (cc.managed / "managed-settings.d" / "dir.json").mkdir()
    assert [p.name for p in claude_paths.managed_settings_files()] == ["keep.json"]


def test_managed_d_keeps_a_symlink(cc):
    target = wjson(cc.tmp / "outside.json", {"env": {"V": "linked"}})
    d = cc.managed / "managed-settings.d"
    d.mkdir(parents=True)
    (d / "l.json").symlink_to(target)
    assert [p.name for p in claude_paths.managed_settings_files()] == ["l.json"]


def test_a_symlinked_managed_file_applies(cc):
    target = wjson(cc.tmp / "outside.json", {"env": {"V": "linked"}})
    d = cc.managed / "managed-settings.d"
    d.mkdir(parents=True)
    (d / "l.json").symlink_to(target)
    assert _env(cc, folder=False)["V"] == "linked"


def test_no_managed_folder_on_this_platform_means_no_files(cc, monkeypatch):
    monkeypatch.setattr(claude_paths, "MANAGED_SETTINGS_DIR", None)
    assert claude_paths.managed_settings_files() == []


# ---- claude_env: the env doctor and the session see ----------------------------------

def test_claude_env_includes_a_managed_base_url(cc):
    """design: check_claude_auth sees a managed ANTHROPIC_BASE_URL through
    claude_env()."""
    cc.managed_file({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9/gw"}})
    assert claude_bootstrap.claude_env({})["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9/gw"


def test_claude_env_includes_the_config_dirs_settings_env(cc):
    wjson(cc.x / "settings.json", {"env": {"V": "from-x"}})
    assert claude_bootstrap.claude_env({CCD: str(cc.x)})["V"] == "from-x"


def test_claude_env_ignores_the_default_settings_when_the_config_dir_is_elsewhere(cc):
    wjson(cc.home_settings, {"env": {"V": "home"}})
    assert "V" not in claude_bootstrap.claude_env({CCD: str(cc.x)})


def test_claude_env_with_a_folder_includes_its_project_settings(cc):
    cc.project({"env": {"V": "project"}})
    assert claude_bootstrap.claude_env({}, folder=str(cc.work))["V"] == "project"


def test_claude_env_without_a_folder_ignores_project_settings(cc, monkeypatch):
    monkeypatch.chdir(cc.work)
    cc.project({"env": {"V": "project"}})
    assert "V" not in claude_bootstrap.claude_env({})
