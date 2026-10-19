"""``aipager doctor``'s ``settings_json`` row checks the settings file the
daemon's sessions read (design success criterion 6, as changed by
amendment A3: a settings-layer move is a detail line, not a WARN)."""

from __future__ import annotations

import pytest

from aipager import doctor, service

from _cfgdir_kit import rjson, wire, wjson

CCD = "CLAUDE_CONFIG_DIR"


def _row():
    return doctor.check_settings_json()


def _detail(r) -> str:
    return "\n".join(r.detail).replace("\n", " ")


def _service_installed():
    unit = service.LINUX_UNIT_PATH
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("[Unit]\nDescription=aipager (test)\n")


# ---- nothing set: as before ------------------------------------------------------

def test_a_wired_default_file_is_ok(cc):
    wire(cc.home_settings)
    assert _row().status == doctor.OK


def test_the_row_keeps_its_title(cc):
    wire(cc.home_settings)
    assert _row().title == "Claude Code settings.json"


def test_nothing_set_names_no_config_dir(cc):
    wire(cc.home_settings)
    assert CCD not in _detail(_row())


def test_a_missing_default_file_fails(cc):
    assert _row().status == doctor.FAIL


# ---- the effective file -------------------------------------------------------------

def test_a_wired_config_dir_file_is_ok_without_a_default_file(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wire(cc.x / "settings.json")
    assert _row().status == doctor.OK


def test_a_missing_config_dir_file_fails_even_with_a_wired_default_file(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wire(cc.home_settings)
    assert _row().status == doctor.FAIL


@pytest.mark.parametrize("content", [b"{not json", b"[1]"], ids=["invalid-json", "list"])
def test_an_invalid_config_dir_file_fails(cc, content):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    (cc.x / "settings.json").write_bytes(content)
    assert _row().status == doctor.FAIL


def test_a_config_dir_file_without_aipager_hooks_warns(cc):
    """The "sessions would run without aipager's hooks" signal."""
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wjson(cc.x / "settings.json", {"model": "opus"})
    wire(cc.home_settings)
    assert _row().status == doctor.WARN


def test_a_config_dir_file_without_a_status_line_warns(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wire(cc.x / "settings.json")
    data = rjson(cc.x / "settings.json")
    data.pop("statusLine", None)
    wjson(cc.x / "settings.json", data)
    assert _row().status == doctor.WARN


def test_the_detail_names_the_config_dirs_file(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wire(cc.x / "settings.json")
    assert str(cc.x / "settings.json") in _detail(_row())


def test_the_detail_says_the_value_came_from_daemon_env(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wire(cc.x / "settings.json")
    assert "daemon.env" in _detail(_row())


def test_the_detail_says_a_shell_value_came_from_this_shell(cc, monkeypatch):
    monkeypatch.setenv(CCD, str(cc.x))
    wire(cc.x / "settings.json")
    assert "shell" in _detail(_row())


# ---- WARN: a value only this shell has while a service is installed -------------------

def test_a_shell_only_value_with_a_service_warns(cc, monkeypatch):
    _service_installed()
    monkeypatch.setenv(CCD, str(cc.x))
    wire(cc.x / "settings.json")
    assert _row().status == doctor.WARN


def test_a_shell_only_value_with_a_service_has_a_fix(cc, monkeypatch):
    _service_installed()
    monkeypatch.setenv(CCD, str(cc.x))
    wire(cc.x / "settings.json")
    assert "daemon.env" in (_row().fix or "")


def test_a_shell_only_value_without_a_service_is_ok(cc, monkeypatch):
    monkeypatch.setenv(CCD, str(cc.x))
    wire(cc.x / "settings.json")
    assert _row().status == doctor.OK


def test_a_daemon_env_value_with_a_service_is_ok(cc):
    _service_installed()
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wire(cc.x / "settings.json")
    assert _row().status == doctor.OK


# ---- WARN: a relative value ---------------------------------------------------------

@pytest.mark.parametrize("value", ["relcfg", "", "  "], ids=["relative", "empty", "whitespace"])
def test_a_relative_config_dir_warns(cc, monkeypatch, value):
    """Amendment A3: "" and whitespace are relative values now."""
    monkeypatch.chdir(cc.work)
    target = cc.work / value if value else cc.work
    target.mkdir(parents=True, exist_ok=True)
    wire(target / "settings.json")
    monkeypatch.setenv(CCD, value)
    assert _row().status == doctor.WARN


def test_a_relative_daemon_env_value_warns(cc, monkeypatch):
    monkeypatch.chdir(cc.work)
    wire(cc.work / "relcfg" / "settings.json")
    cc.set_daemon_env(CLAUDE_CONFIG_DIR="relcfg")
    assert _row().status == doctor.WARN


# ---- a settings-layer move: followed, shown as a detail ---------------------------------

def test_a_settings_move_to_a_wired_file_is_ok(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    wire(cc.x / "settings.json")
    assert _row().status == doctor.OK


def test_a_settings_move_names_the_moved_home_in_the_detail(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    wire(cc.x / "settings.json")
    assert str(cc.x) in _detail(_row())


def test_a_settings_move_to_an_unwired_file_warns(cc):
    """The start file holds the move and aipager's hooks; the hooks there
    never fire (P2), so this is the hookless case."""
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    wire(cc.home_settings)
    wjson(cc.x / "settings.json", {"model": "opus"})
    assert _row().status == doctor.WARN


def test_a_settings_move_to_a_missing_file_fails(cc):
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    wire(cc.home_settings)
    assert _row().status == doctor.FAIL


def test_a_managed_move_is_followed(cc):
    cc.managed_file({"env": {CCD: str(cc.x)}})
    wire(cc.x / "settings.json")
    assert _row().status == doctor.OK


def test_a_second_hop_is_named_in_the_detail(cc):
    """P7: the moved file's own CLAUDE_CONFIG_DIR is not followed again for
    settings; amendment A3 asks for a detail line."""
    c2 = cc.tmp / "chain" / "c2"
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    wire(cc.x / "settings.json")
    data = rjson(cc.x / "settings.json")
    data["env"] = {CCD: str(c2)}
    wjson(cc.x / "settings.json", data)
    assert str(c2) in _detail(_row())
