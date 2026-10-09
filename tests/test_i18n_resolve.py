"""Which language a message uses (section 6), the ``ui_language`` store
behind it, and check 12: a message is never sent in another chat's
language."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aipager import i18n, preferences
from aipager.i18n import locales, resolve

GROUP, DM, ALY, BOB = -1001, 555, 555, 777


def _update(chat_id, user_id, language_code=None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=user_id, language_code=language_code),
    )


@pytest.fixture
def qps(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps", "fa"}))


def _writes(monkeypatch):
    calls = []
    real = preferences._save_raw
    monkeypatch.setattr(preferences, "_save_raw",
                        lambda store: (calls.append(dict(store)), real(store)))
    return calls


# ---- storage ---------------------------------------------------------------

def test_an_unknown_code_writes_nothing(monkeypatch):
    calls = _writes(monkeypatch)
    for bad in ("fa", "qps", "qps-rtl", "xx", "EN", None, 5):
        with pytest.raises(ValueError):
            preferences.set_ui_language(DM, bad)
    assert calls == []
    assert not preferences._PREFERENCES_PATH.exists()
    assert preferences.get_ui_language(DM) == ""


def test_a_choice_is_stored_in_the_chat_entry_and_survives_a_reload(qps):
    preferences.set_ui_language(GROUP, "qps")
    preferences._cache = None
    assert preferences.get_ui_language(GROUP) == "qps"
    assert preferences.get_ui_language(DM) == ""
    preferences.set_ui_language(GROUP, "")
    assert preferences.get_ui_language(GROUP) == ""


def test_a_stored_value_of_the_wrong_shape_reads_as_unset(qps):
    preferences._ensure_loaded()[str(DM)] = {"ui_language": 7}
    assert preferences.get_ui_language(DM) == ""
    assert i18n.for_chat(DM).code == "en"


def test_a_stored_language_that_is_no_longer_enabled_reads_as_english(qps, monkeypatch):
    preferences.set_ui_language(GROUP, "fa")
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset())
    assert i18n.for_chat(GROUP).code == "en"


def test_the_language_is_not_a_reply_style():
    assert "ui_language" not in preferences._FIELD_VALIDATORS
    assert "ui_language" not in preferences.Preferences.__dataclass_fields__


def test_move_chat_carries_the_language(qps):
    preferences.set_ui_language(GROUP, "qps")
    assert preferences.move_chat(GROUP, -1002)
    assert preferences.get_ui_language(-1002) == "qps"


# ---- resolution ------------------------------------------------------------

def test_a_private_chat_follows_telegram_until_it_chooses(qps, monkeypatch):
    calls = _writes(monkeypatch)
    assert i18n.for_update(_update(DM, ALY, "fa-IR")).code == "fa"
    # Remembered for background messages to that chat, in memory only.
    assert i18n.for_chat(DM).code == "fa"
    assert calls == []
    preferences.set_ui_language(DM, "en")
    assert i18n.for_update(_update(DM, ALY, "fa")).code == "en"
    # A language we do not have falls back to English and is forgotten.
    preferences.set_ui_language(DM, "")
    assert i18n.for_update(_update(DM, ALY, "ja")).code == "en"
    assert i18n.for_chat(DM).code == "en"


def test_a_group_never_gets_the_dm_language(qps):
    preferences.set_ui_language(DM, "qps")
    i18n.for_update(_update(DM, ALY, "fa"))
    assert i18n.for_chat(GROUP).code == "en"
    assert i18n.for_update(_update(GROUP, ALY, "fa")).code == "en"
    # What only Aly sees in the group is hers.
    assert i18n.for_person(_update(GROUP, ALY, "fa")).code == "qps"


def test_a_dm_never_gets_the_group_language(qps):
    preferences.set_ui_language(GROUP, "qps")
    assert i18n.for_update(_update(GROUP, BOB)).code == "qps"
    assert i18n.for_chat(DM).code == "en"
    assert i18n.for_update(_update(DM, ALY)).code == "en"
    assert i18n.for_person(_update(GROUP, BOB, None)).code == "qps"
    assert i18n.for_person(_update(GROUP, BOB, "fa")).code == "fa"


def test_the_group_seen_language_is_never_recorded(qps):
    i18n.for_update(_update(GROUP, BOB, "fa"))
    assert resolve._seen == {}
    assert i18n.for_chat(BOB).code == "en"


def test_strangers_and_viewers(qps):
    assert i18n.for_stranger(_update(DM, 9, "fa")).code == "fa"
    assert i18n.for_stranger(_update(DM, 9, "ja")).code == "en"
    assert i18n.for_stranger(_update(DM, 9, "qps")).code == "en"
    assert i18n.for_viewer(ALY, "fa").code == "fa"
    preferences.set_ui_language(ALY, "en")
    assert i18n.for_viewer(ALY, "fa").code == "en"
    assert i18n.for_viewer(None, None).code == "en"
    assert i18n.for_viewer(GROUP, "fa").code == "fa"


def test_for_sess_uses_the_session_chat(qps):
    preferences.set_ui_language(GROUP, "qps")
    assert i18n.for_sess(SimpleNamespace(scope_chat_id=GROUP, name="x")).code == "qps"
    assert i18n.for_sess(SimpleNamespace(scope_chat_id=DM, name="x")).code == "en"


def test_junk_updates_give_english():
    assert i18n.for_update(SimpleNamespace()).code == "en"
    assert i18n.for_person(SimpleNamespace()).code == "en"
    assert i18n.for_chat("x").code == "en"
    assert i18n.default_code() == "en"
