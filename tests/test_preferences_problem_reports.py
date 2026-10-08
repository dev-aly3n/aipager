"""The owner's "Problem reports: Ask me / Off" switch (roadmap 8.112).

Stored once per install under its own top-level key of preferences.json,
never as a reply-style field: it must not reach ``settings_schema()``
(every chat's Mini App settings) nor any chat's entry.
"""

from __future__ import annotations

import json

import pytest

from aipager import preferences
from aipager.bot import settings_menu


def _file() -> dict:
    return json.loads(preferences._PREFERENCES_PATH.read_text(encoding="utf-8"))


def test_default_is_ask_and_nothing_is_written():
    assert preferences.get_problem_reports() == "ask"
    assert not preferences._PREFERENCES_PATH.exists()


@pytest.mark.parametrize("value", ["ask", "off"])
def test_round_trip(value):
    assert preferences.set_problem_reports(value) == value
    assert preferences.get_problem_reports() == value
    assert _file()["_install"] == {"problem_reports": value}


@pytest.mark.parametrize("stored", ["never", "", 0, None, True, ["ask"]])
def test_invalid_reads_off(stored):
    """A value aipager did not write errs quiet: no automatic offers."""
    preferences._PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    preferences._PREFERENCES_PATH.write_text(
        json.dumps({"_install": {"problem_reports": stored}}), encoding="utf-8")
    assert preferences.get_problem_reports() == "off"


@pytest.mark.parametrize("bad", ["", "Off", "yes", None, 1, True])
def test_setter_rejects_before_writing(bad):
    with pytest.raises(ValueError):
        preferences.set_problem_reports(bad)
    assert not preferences._PREFERENCES_PATH.exists()
    assert preferences.get_problem_reports() == "ask"


def test_other_chats_preferences_untouched():
    preferences.set_preference(555, "answer_length", "short")
    preferences.set_new_session_default(555, "mode", "ask")
    preferences.set_problem_reports("off")
    assert preferences.get_preferences(555).answer_length == "short"
    assert preferences.get_new_session_defaults(555).mode == "ask"
    stored = _file()
    assert stored["555"] == {"answer_length": "short", "new_session_mode": "ask"}
    assert "problem_reports" not in json.dumps(stored["555"])
    preferences.set_preference(555, "answer_length", "long")
    assert preferences.get_problem_reports() == "off"


def test_not_in_the_shared_settings_schema():
    assert "problem_reports" not in json.dumps(settings_menu.settings_schema())
    assert "problem_reports" not in preferences._FIELD_VALIDATORS
