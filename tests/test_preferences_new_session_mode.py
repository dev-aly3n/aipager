"""A chat's New sessions mode: three ids under ``new_session_perm_mode``,
the older ``new_session_mode`` read through its migration and written as a
mirror an older daemon reads no wider."""

from __future__ import annotations

import json

import pytest

from aipager import preferences


def _file() -> dict:
    return json.loads(preferences._PREFERENCES_PATH.read_text(encoding="utf-8"))


def _store(entry: dict) -> None:
    preferences._PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    preferences._PREFERENCES_PATH.write_text(json.dumps({"555": entry}), encoding="utf-8")


def test_nothing_stored_is_auto():
    assert preferences.get_new_session_defaults(555).mode == "auto"


@pytest.mark.parametrize("entry, want", [
    ({"new_session_mode": "auto"}, "full"),
    ({"new_session_mode": "ask"}, "ask"),
    ({"new_session_mode": ""}, "auto"),
    ({"new_session_perm_mode": "auto", "new_session_mode": "ask"}, "auto"),
    ({"new_session_perm_mode": True}, "auto"),
], ids=["legacy-auto-was-bypass", "legacy-ask", "legacy-empty", "new-wins", "bad-new"])
def test_read_migrates(entry, want):
    _store(entry)
    assert preferences.get_new_session_defaults(555).mode == want


@pytest.mark.parametrize("mode, mirror", [("ask", "ask"), ("auto", "ask"), ("full", None)],
                         ids=["ask", "auto", "full"])
def test_write_keeps_a_rollback_mirror_never_wider(mode, mirror):
    _store({"new_session_mode": "auto"})
    assert preferences.set_new_session_default(555, "mode", mode).mode == mode
    entry = _file()["555"]
    assert entry["new_session_perm_mode"] == mode
    assert entry.get("new_session_mode") == mirror


def test_clearing_the_mode_is_auto():
    preferences.set_new_session_default(555, "mode", "full")
    assert preferences.set_new_session_default(555, "mode", "").mode == "auto"
    assert _file()["555"]["new_session_perm_mode"] == "auto"


@pytest.mark.parametrize("bad", ["bypass", "Auto", "manual"], ids=["bypass", "case", "manual"])
def test_an_unknown_mode_is_refused_before_writing(bad):
    with pytest.raises(ValueError):
        preferences.set_new_session_default(555, "mode", bad)
    assert not preferences._PREFERENCES_PATH.exists()
