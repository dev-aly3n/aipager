"""Black-box: the one language rule of design section 6 (phase 5 item 2) and
the launch marker that tells existing people from new ones (item 3).

design.md success criteria, item 2: a new private chat with nothing stored
follows a Persian app; a new person's pop-up and Mini App follow their app;
an explicit choice wins; stored "auto" follows an app change; strangers get
their Telegram language; a background message to a new private chat uses the
persisted last-seen language after ``persist.reset()``; an absent
``language_code`` keeps it and an unsupported one clears it. Item 3: the
marker is written once and survives restarts; an existing person with a
Persian app stays English everywhere until they choose; a fresh install's
people list is empty.

Partitions: person = existing (in the marker) / new (not in it) / no marker
at all; stored = nothing / a code / "auto"; Telegram language = en / fa /
fa-IR / ja (unsupported) / "" / None; chat = private / group; detection
switch = on / off.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aipager import config, i18n
from aipager import preferences as prefs
from aipager.i18n import locales, persist
from tests.i18n_ml2_bb import callback, message

OWNER, NEWBIE, GROUP = 555, 4321, -1001


@pytest.fixture
def launched(p5):
    """The owner existed before the launch; NEWBIE is new since."""
    p5.marker(OWNER)


def _msg(uid, code, chat=None):
    return message("hi", chat=uid if chat is None else chat, user=uid, language_code=code)


# ---- a new private chat with nothing stored ---------------------------------------

@pytest.mark.parametrize("tag, want", [("fa", "fa"), ("fa-IR", "fa"), ("ru", "ru"),
                                       ("pt-BR", "pt-BR"), ("en", "en")])
def test_a_new_private_chat_follows_the_telegram_app(launched, tag, want):
    assert i18n.for_update(_msg(NEWBIE, tag)).code == want


@pytest.mark.parametrize("tag", ["ja", "pl", "", None])
def test_a_new_private_chat_with_no_supported_app_language_is_english(launched, tag):
    assert i18n.for_update(_msg(NEWBIE, tag)).code == "en"


def test_a_background_message_to_a_new_chat_uses_the_last_seen_language(launched):
    i18n.for_update(_msg(NEWBIE, "fa"))
    assert i18n.for_chat(NEWBIE).code == "fa"


def test_the_last_seen_language_survives_a_restart(launched, p5):
    i18n.for_update(_msg(NEWBIE, "fa"))
    p5.restart()
    assert i18n.for_chat(NEWBIE).code == "fa"


def test_the_last_seen_language_is_written_to_the_language_state_file(launched):
    i18n.for_update(_msg(NEWBIE, "ru"))
    doc = json.loads(config.LANGUAGE_STATE_FILE.read_text(encoding="utf-8"))
    assert doc["seen"][str(NEWBIE)] == "ru"


def test_the_language_state_file_is_under_the_redirected_home(tmp_path):
    assert str(config.LANGUAGE_STATE_FILE).startswith(str(tmp_path))


def test_a_new_chat_never_seen_reads_english(launched):
    assert i18n.for_chat(NEWBIE).code == "en"


# ---- what keeps, what clears (U3) --------------------------------------------------

@pytest.mark.parametrize("tag", [None, ""])
def test_an_absent_language_code_keeps_the_last_seen_one(launched, tag):
    i18n.for_update(_msg(NEWBIE, "fa"))
    i18n.for_update(_msg(NEWBIE, tag))
    assert i18n.for_chat(NEWBIE).code == "fa"


@pytest.mark.parametrize("tag", ["ja", "zh-Hant", "pl"])
def test_an_unsupported_language_code_clears_the_last_seen_one(launched, tag):
    i18n.for_update(_msg(NEWBIE, "fa"))
    i18n.for_update(_msg(NEWBIE, tag))
    assert i18n.for_chat(NEWBIE).code == "en"


def test_a_cleared_last_seen_language_stays_cleared_after_a_restart(launched, p5):
    i18n.for_update(_msg(NEWBIE, "fa"))
    i18n.for_update(_msg(NEWBIE, "ja"))
    p5.restart()
    assert i18n.for_chat(NEWBIE).code == "en"


def test_an_app_switch_moves_the_chat(launched):
    i18n.for_update(_msg(NEWBIE, "fa"))
    i18n.for_update(_msg(NEWBIE, "ru"))
    assert i18n.for_chat(NEWBIE).code == "ru"


def test_note_language_reports_a_change(launched):
    from aipager.i18n import resolve
    assert resolve.note_language(_msg(NEWBIE, "fa")) is True


def test_note_language_reports_no_change_for_the_same_language(launched):
    from aipager.i18n import resolve
    resolve.note_language(_msg(NEWBIE, "fa"))
    assert resolve.note_language(_msg(NEWBIE, "fa-IR")) is False


def test_note_language_reports_no_change_for_an_absent_code(launched):
    from aipager.i18n import resolve
    resolve.note_language(_msg(NEWBIE, "fa"))
    assert resolve.note_language(_msg(NEWBIE, None)) is False


def test_a_group_message_records_no_last_seen_language(launched):
    i18n.for_update(_msg(NEWBIE, "fa", chat=GROUP))
    assert persist.seen(NEWBIE) is None


def test_a_group_update_is_not_a_change(launched):
    from aipager.i18n import resolve
    assert resolve.note_language(_msg(NEWBIE, "fa", chat=GROUP)) is False


def test_the_last_seen_language_is_never_an_explicit_choice(launched):
    i18n.for_update(_msg(NEWBIE, "fa"))
    assert prefs.get_ui_language(NEWBIE) in (None, "")


def test_one_persons_language_never_reaches_another_dm(launched):
    i18n.for_update(_msg(NEWBIE, "fa"))
    assert i18n.for_chat(NEWBIE + 1).code == "en"


def test_one_persons_language_never_reaches_a_group(launched):
    i18n.for_update(_msg(NEWBIE, "fa"))
    assert i18n.for_chat(GROUP).code == "en"


# ---- an explicit choice wins ---------------------------------------------------------

@pytest.mark.parametrize("who", [OWNER, NEWBIE])
def test_a_stored_code_wins_over_the_app(launched, who):
    prefs.set_ui_language(who, "ru")
    assert i18n.for_update(_msg(who, "fa")).code == "ru"


def test_stored_english_wins_over_a_persian_app_for_a_new_person(launched):
    prefs.set_ui_language(NEWBIE, "en")
    assert i18n.for_update(_msg(NEWBIE, "fa")).code == "en"


@pytest.mark.parametrize("who", [OWNER, NEWBIE])
def test_stored_automatic_follows_the_app(launched, who):
    prefs.set_ui_language(who, "auto")
    assert i18n.for_update(_msg(who, "fa")).code == "fa"


@pytest.mark.parametrize("who", [OWNER, NEWBIE])
def test_stored_automatic_follows_an_app_change(launched, who):
    prefs.set_ui_language(who, "auto")
    i18n.for_update(_msg(who, "fa"))
    i18n.for_update(_msg(who, "ru"))
    assert i18n.for_chat(who).code == "ru"


def test_stored_automatic_in_an_existing_chat_survives_a_restart(launched, p5):
    prefs.set_ui_language(OWNER, "auto")
    i18n.for_update(_msg(OWNER, "fa"))
    p5.restart()
    assert i18n.for_chat(OWNER).code == "fa"


def test_stored_automatic_with_nothing_seen_is_english(launched):
    prefs.set_ui_language(OWNER, "auto")
    assert i18n.for_chat(OWNER).code == "en"


# ---- an existing person stays English ------------------------------------------------

@pytest.mark.parametrize("tag", ["fa", "ru", "de", "zh-Hans"])
def test_an_existing_private_chat_stays_english_for_a_non_english_app(launched, tag):
    assert i18n.for_update(_msg(OWNER, tag)).code == "en"


def test_an_existing_chat_stays_english_in_the_background(launched):
    i18n.for_update(_msg(OWNER, "fa"))
    assert i18n.for_chat(OWNER).code == "en"


def test_an_existing_chat_stays_english_after_a_restart(launched, p5):
    i18n.for_update(_msg(OWNER, "fa"))
    p5.restart()
    assert i18n.for_chat(OWNER).code == "en"


def test_an_existing_persons_language_is_still_remembered_for_the_offer(launched):
    i18n.for_update(_msg(OWNER, "fa-IR"))
    assert persist.seen(OWNER) == "fa"


def test_an_existing_persons_mini_app_stays_english(launched):
    assert i18n.for_viewer(OWNER, "fa").code == "en"


def test_an_existing_persons_group_pop_up_stays_on_the_groups_language(launched):
    update, _q = callback("_:set", chat=GROUP, user=OWNER, language_code="fa")
    assert i18n.for_person(update).code == "en"


def test_without_a_marker_everyone_counts_as_existing():
    assert i18n.for_update(_msg(NEWBIE, "fa")).code == "en"


def test_without_a_marker_is_existing_person_is_true():
    assert persist.is_existing_person(NEWBIE) is True


# ---- a new person's pop-ups and Mini App --------------------------------------------

def test_a_new_persons_mini_app_follows_their_app(launched):
    assert i18n.for_viewer(NEWBIE, "fa").code == "fa"


def test_a_new_persons_group_pop_up_follows_their_app(launched):
    update, _q = callback("_:set", chat=GROUP, user=NEWBIE, language_code="fa")
    assert i18n.for_person(update).code == "fa"


def test_a_new_persons_stored_dm_choice_wins_in_a_group_pop_up(launched):
    prefs.set_ui_language(NEWBIE, "ru")
    update, _q = callback("_:set", chat=GROUP, user=NEWBIE, language_code="fa")
    assert i18n.for_person(update).code == "ru"


def test_a_new_persons_mini_app_with_an_unsupported_app_is_english(launched):
    assert i18n.for_viewer(NEWBIE, "ja").code == "en"


def test_a_group_with_nothing_stored_is_english_for_a_new_persons_message(launched):
    assert i18n.for_update(_msg(NEWBIE, "fa", chat=GROUP)).code == "en"


def test_a_group_with_a_stored_code_uses_it(launched):
    prefs.set_ui_language(GROUP, "de")
    assert i18n.for_update(_msg(NEWBIE, "fa", chat=GROUP)).code == "de"


# ---- strangers -------------------------------------------------------------------------

@pytest.mark.parametrize("tag, want", [("fa", "fa"), ("ru-RU", "ru"), ("ja", "en"),
                                       (None, "en"), ("", "en")])
def test_a_stranger_gets_their_telegram_language(tag, want):
    user = SimpleNamespace(id=777001, language_code=tag)
    assert i18n.for_stranger(user).code == want


def test_a_stranger_gets_their_telegram_language_from_an_update():
    assert i18n.for_stranger(_msg(777002, "fa")).code == "fa"


def test_a_strangers_stored_dm_choice_wins():
    prefs.set_ui_language(777003, "ru")
    assert i18n.for_stranger(SimpleNamespace(id=777003, language_code="fa")).code == "ru"


def test_a_stranger_who_is_an_existing_person_still_gets_their_language(launched):
    assert i18n.for_stranger(SimpleNamespace(id=OWNER, language_code="fa")).code == "fa"


def test_the_stranger_switch_is_gone():
    from aipager.bot import auth
    assert not hasattr(auth, "STRANGER_LANGUAGE")


# ---- the kill switch -------------------------------------------------------------------

def test_the_detection_switch_is_on():
    assert locales.AUTO_DETECT is True


def test_switching_detection_off_keeps_a_new_chat_english(launched, monkeypatch):
    monkeypatch.setattr(locales, "AUTO_DETECT", False)
    assert i18n.for_update(_msg(NEWBIE, "fa")).code == "en"


def test_switching_detection_off_still_serves_strangers_their_language(monkeypatch):
    monkeypatch.setattr(locales, "AUTO_DETECT", False)
    assert i18n.for_stranger(SimpleNamespace(id=777004, language_code="fa")).code == "fa"


def test_switching_detection_off_returns_stored_automatic_to_english(launched, monkeypatch):
    """D17: False returns everyone except strangers to pre-launch behaviour
    (pre-launch, Automatic read English). design.md section 3's ``follows``
    formula reads otherwise; D17 is the operator's decision."""
    monkeypatch.setattr(locales, "AUTO_DETECT", False)
    prefs.set_ui_language(OWNER, "auto")
    assert i18n.for_update(_msg(OWNER, "fa")).code == "en"


def test_switching_detection_off_still_remembers_the_language(launched, monkeypatch):
    monkeypatch.setattr(locales, "AUTO_DETECT", False)
    i18n.for_update(_msg(OWNER, "fa"))
    assert persist.seen(OWNER) == "fa"


@pytest.mark.parametrize("tag, follow, want", [
    ("fa", True, "fa"), ("fa", False, None), ("en-GB", False, "en"), ("ja", True, None),
    (None, True, None), ("", True, None)])
def test_detect_follow_partitions(tag, follow, want):
    assert locales.detect(tag, follow=follow) == want


# ---- automatic_code ----------------------------------------------------------------------

def test_automatic_code_prefers_the_given_app_language(launched):
    persist.set_seen(OWNER, "ru")
    assert i18n.automatic_code(OWNER, "fa-IR") == "fa"


def test_automatic_code_falls_back_to_the_last_seen_language(launched):
    persist.set_seen(OWNER, "ru")
    assert i18n.automatic_code(OWNER) == "ru"


def test_automatic_code_is_english_with_nothing_known():
    assert i18n.automatic_code(NEWBIE) == "en"


# ---- the marker ---------------------------------------------------------------------------

def test_the_marker_names_the_existing_people(p5):
    assert sorted(p5.marker(OWNER, 2)["people"]) == [2, OWNER]


def test_the_marker_is_written_once(p5):
    p5.marker(OWNER)
    persist.ensure_launch_marker([NEWBIE], prior_run=True)
    assert NEWBIE not in persist.launch_marker()["people"]


def test_the_marker_survives_a_restart_unchanged(p5):
    p5.marker(OWNER)
    before = persist.launch_marker()
    p5.restart()
    persist.ensure_launch_marker([NEWBIE], prior_run=True)
    assert persist.launch_marker() == before


def test_the_marker_file_bytes_do_not_change_on_a_second_start(p5):
    p5.marker(OWNER)
    before = config.LANGUAGE_STATE_FILE.read_bytes()
    p5.restart()
    persist.ensure_launch_marker([OWNER, NEWBIE], prior_run=True)
    assert config.LANGUAGE_STATE_FILE.read_bytes() == before


def test_a_fresh_install_has_no_existing_people():
    assert persist.ensure_launch_marker([OWNER, 2], prior_run=False)["people"] == []


def test_a_fresh_install_is_marked_fresh():
    assert persist.ensure_launch_marker([OWNER], prior_run=False)["fresh"] is True


def test_on_a_fresh_install_the_owner_follows_the_app():
    persist.ensure_launch_marker([OWNER], prior_run=False)
    assert i18n.for_update(_msg(OWNER, "fa")).code == "fa"


def test_an_upgrade_is_not_fresh(p5):
    assert p5.marker(OWNER)["fresh"] is False


def test_the_marker_records_a_version_and_a_time(p5):
    m = p5.marker(OWNER)
    assert isinstance(m["at"], int) and m["version"]


def test_a_person_added_after_the_marker_is_not_existing(p5):
    p5.marker(OWNER)
    assert persist.is_existing_person(NEWBIE) is False


def test_a_marked_person_is_existing(p5):
    p5.marker(OWNER)
    assert persist.is_existing_person(OWNER) is True


def test_no_prior_run_on_an_empty_home():
    assert persist.note_prior_run() is False


def test_a_preferences_file_means_a_prior_run():
    prefs.set_ui_language(OWNER, "en")   # writes preferences.json
    assert persist.note_prior_run() is True


def test_a_sessions_file_means_a_prior_run():
    config.SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SESSION_STATE_FILE.write_text("{}", encoding="utf-8")
    assert persist.note_prior_run() is True


# ---- a damaged state file reads as empty, never crashes --------------------------------

def _write_state(doc):
    path = config.LANGUAGE_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    persist.reset()


@pytest.mark.parametrize("doc", ["not json", "[]", "", "\x00\x01"])
def test_a_corrupt_state_file_means_everyone_is_existing(doc):
    _write_state(doc)
    assert persist.is_existing_person(NEWBIE) is True


@pytest.mark.parametrize("launch", ["x", [], 5, {"people": "555"}])
def test_a_wrong_shaped_marker_reads_as_no_marker(launch):
    _write_state({"v": 1, "launch": launch})
    assert persist.launch_marker() is None


@pytest.mark.parametrize("seen", ["fa", [], 3])
def test_a_wrong_shaped_seen_section_reads_as_empty(seen):
    _write_state({"v": 1, "seen": seen})
    assert persist.seen(NEWBIE) is None


def test_a_good_seen_entry_is_read_back_from_disk():
    _write_state({"v": 1, "seen": {str(NEWBIE): "ru"}})
    assert persist.seen(NEWBIE) == "ru"


def test_a_corrupt_state_file_does_not_stop_a_resolver():
    _write_state("{{{")
    assert i18n.for_update(_msg(NEWBIE, "fa")).code == "en"


# ---- the seen cap (boundary) -----------------------------------------------------------

def test_the_seen_cap_keeps_the_newest_entries():
    cap = persist.SEEN_CAP
    for uid in range(1, cap + 2):
        persist.set_seen(uid, "fa")
    assert persist.seen(1) is None


def test_the_seen_cap_keeps_the_entry_just_inside():
    cap = persist.SEEN_CAP
    for uid in range(1, cap + 2):
        persist.set_seen(uid, "fa")
    assert persist.seen(2) == "fa"


def test_set_seen_none_clears():
    persist.set_seen(NEWBIE, "fa")
    persist.set_seen(NEWBIE, None)
    assert persist.seen(NEWBIE) is None
