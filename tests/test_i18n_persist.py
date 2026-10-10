"""The language state file (multilingual phase 5, ``aipager.i18n.persist``).

Round trip through a simulated restart (``persist.reset()``), the once-only
marker and records, the cap on remembered languages, sections of the wrong
shape, and the redirect: no test ever reaches the real
``~/.local/state/aipager/languages.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipager import _test_guard, config
from aipager.i18n import persist


def _file() -> Path:
    return Path(config.LANGUAGE_STATE_FILE)


def _on_disk() -> dict:
    return json.loads(_file().read_text(encoding="utf-8"))


def test_the_state_file_is_redirected_under_the_test_home():
    assert "pytest-" in str(_file())
    assert _file().name == "languages.json"
    assert _file().parent.name == "aipager" and _file().parent.parent.name == "state"


def test_nothing_is_written_until_a_value_changes():
    assert persist.seen(5) is None
    assert not persist.offer_done(5)
    assert persist.launch_marker() is None
    assert not _file().exists()


def test_seen_survives_a_restart():
    assert persist.set_seen(5, "fa") is True
    persist.reset()
    assert persist.seen(5) == "fa"
    assert _on_disk()["seen"] == {"5": "fa"}
    assert _on_disk()["v"] == 1


def test_seen_is_written_only_when_it_changes(monkeypatch):
    persist.set_seen(5, "fa")
    writes = []
    monkeypatch.setattr(persist, "write_private", lambda *a, **k: writes.append(a))
    assert persist.set_seen(5, "fa") is False
    assert persist.set_seen(6, None) is False
    assert writes == []
    assert persist.set_seen(5, "ru") is True
    assert len(writes) == 1


def test_clearing_seen_forgets_it_after_a_restart():
    persist.set_seen(5, "fa")
    assert persist.set_seen(5, None) is True
    persist.reset()
    assert persist.seen(5) is None


def test_seen_keeps_at_most_the_cap_and_drops_the_oldest(monkeypatch):
    monkeypatch.setattr(persist, "SEEN_CAP", 3)
    for uid in (1, 2, 3):
        persist.set_seen(uid, "fa")
    persist.set_seen(1, "ru")          # a change makes 1 the newest
    persist.set_seen(4, "de")
    assert persist.seen(2) is None
    assert [persist.seen(u) for u in (1, 3, 4)] == ["ru", "fa", "de"]
    persist.reset()
    assert len(_on_disk()["seen"]) == 3


def test_the_offer_and_first_contact_records_survive_a_restart():
    persist.mark_offer_done(5, now=1_800_000_000)
    persist.mark_first_contact_done(6, now=1_800_000_001)
    persist.reset()
    assert persist.offer_done(5) and not persist.offer_done(6)
    assert persist.first_contact_done(6) and not persist.first_contact_done(5)
    assert _on_disk()["offered"] == {"5": 1_800_000_000}


def test_a_record_is_never_rewritten():
    persist.mark_offer_done(5, now=1_800_000_000)
    persist.mark_offer_done(5, now=1_900_000_000)
    assert _on_disk()["offered"] == {"5": 1_800_000_000}


def test_description_hashes_round_trip():
    assert persist.description_hash("zh", "short") is None
    persist.record_description("zh", "short", "0123456789abcdef")
    persist.record_description("zh", "description", "fedcba9876543210")
    persist.reset()
    assert persist.description_hash("zh", "short") == "0123456789abcdef"
    assert persist.description_hash("zh", "description") == "fedcba9876543210"
    with pytest.raises(ValueError):
        persist.record_description("zh", "name", "00")


# ---- the existing-install marker --------------------------------------------

def test_without_a_marker_everyone_counts_as_existing():
    assert persist.is_existing_person(5)
    assert persist.is_existing_person(123456789)


def test_the_marker_is_written_once_and_never_rewritten():
    first = persist.ensure_launch_marker([5, 7, -100, 5], prior_run=True, now=1_800_000_000)
    assert first["people"] == [5, 7] and first["fresh"] is False
    assert first["at"] == 1_800_000_000
    persist.reset()
    again = persist.ensure_launch_marker([9], prior_run=False, now=1_900_000_000)
    assert again == first
    assert _on_disk()["launch"] == first
    assert persist.is_existing_person(5) and persist.is_existing_person(7)
    assert not persist.is_existing_person(9)


def test_a_fresh_install_has_no_existing_people():
    marker = persist.ensure_launch_marker([5, 7], prior_run=False)
    assert marker["fresh"] is True and marker["people"] == []
    assert not persist.is_existing_person(5)


def test_the_marker_takes_note_prior_run_and_errs_to_existing_without_it():
    marker = persist.ensure_launch_marker([5])
    assert marker["fresh"] is False and marker["people"] == [5]


def test_note_prior_run_sees_a_fresh_machine_and_an_old_one(monkeypatch):
    assert persist.note_prior_run() is False
    assert persist.ensure_launch_marker([5])["fresh"] is True


@pytest.mark.parametrize("which", ["SESSION_STATE_FILE", "FLOOD_STATE_FILE",
                                   "REPORT_INSTALL_FILE", "preferences"])
def test_note_prior_run_finds_each_earlier_file(which):
    from aipager import preferences
    from aipager.report import markers
    if which == "REPORT_INSTALL_FILE":
        markers.first_start()
    else:
        path = Path(preferences._PREFERENCES_PATH if which == "preferences"
                    else getattr(config, which))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    assert persist.note_prior_run() is True
    assert persist.ensure_launch_marker([5])["people"] == [5]


@pytest.mark.parametrize("bad", [
    "not json", "[]", json.dumps({"launch": [1], "seen": "x", "offered": 3}),
    json.dumps({"launch": {"at": "x", "people": []}}),
    json.dumps({"launch": {"at": 1_800_000_000, "people": ["5"]}}),
])
def test_a_section_of_the_wrong_shape_reads_as_empty(bad):
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(bad, encoding="utf-8")
    assert persist.launch_marker() is None
    assert persist.is_existing_person(5)
    assert persist.seen(5) is None
    assert not persist.offer_done(5)
    # and the next write replaces the bad section
    persist.set_seen(5, "fa")
    persist.reset()
    assert persist.seen(5) == "fa"


def test_an_oversized_file_reads_as_empty(monkeypatch):
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps({"seen": {"5": "fa"}}), encoding="utf-8")
    monkeypatch.setattr(persist, "MAX_BYTES", 5)
    assert persist.seen(5) is None


def test_a_write_failure_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("read-only")
    monkeypatch.setattr(persist, "write_private", boom)
    assert persist.set_seen(5, "fa") is True
    assert persist.seen(5) == "fa"      # kept in memory for this process


def test_the_file_is_owner_only():
    persist.set_seen(5, "fa")
    assert _file().stat().st_mode & 0o077 == 0


def test_a_write_into_the_real_home_is_refused(tmp_path, monkeypatch, real_home_refusals_expected):
    home = tmp_path / "realhome"
    target = home / ".local" / "state" / "aipager" / "languages.json"
    monkeypatch.setattr(_test_guard, "_real_home", lambda: str(home))
    monkeypatch.setattr(config, "LANGUAGE_STATE_FILE", target)
    _test_guard.refusals.clear()
    persist.set_seen(5, "fa")
    assert not target.exists()
    assert any(str(target) in r for r in _test_guard.refusals), _test_guard.refusals


# ---- a damaged file is moved aside, never replaced (review rev-iter1-004) ---------------

def _bad_copies() -> list[Path]:
    return sorted(_file().parent.glob(_file().name + ".bad-*"))


def _write_raw(raw: str) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(raw, encoding="utf-8")
    persist.reset()


GOOD_MARKER = {"at": 1_800_000_000, "version": "1.0.1", "fresh": False, "people": [5]}


@pytest.mark.parametrize("raw", [
    "{not json", "[]", "", json.dumps({"launch": {"at": "x", "people": []}, "seen": {}}),
])
def test_a_damaged_file_is_moved_aside_with_its_bytes(raw):
    _write_raw(raw)
    assert persist.is_damaged()
    (aside,) = _bad_copies()
    assert aside.read_text(encoding="utf-8") == raw
    # The new file records the damage at once, so the next start knows.
    assert persist.DAMAGED in _on_disk()


def test_an_oversized_file_is_moved_aside(monkeypatch):
    _write_raw(json.dumps({"launch": GOOD_MARKER}))
    monkeypatch.setattr(persist, "MAX_BYTES", 5)
    assert persist.is_damaged() and len(_bad_copies()) == 1


def test_while_damaged_everyone_is_existing_and_no_marker_is_written():
    _write_raw("{not json")
    assert persist.launch_marker() is None
    assert persist.ensure_launch_marker([5], prior_run=False) is None
    assert persist.is_existing_person(12345)
    persist.reset()                                   # a restart
    assert persist.is_damaged()
    assert persist.ensure_launch_marker([5], prior_run=False) is None
    assert "launch" not in _on_disk()


def test_other_records_still_work_while_damaged():
    _write_raw("{not json")
    persist.set_seen(5, "fa")
    persist.reset()
    assert persist.seen(5) == "fa" and persist.is_damaged()


def test_deleting_the_file_ends_the_damage():
    _write_raw("{not json")
    assert persist.is_damaged()
    _file().unlink()
    persist.reset()
    assert not persist.is_damaged()
    assert persist.ensure_launch_marker([5], prior_run=True)["people"] == [5]


def test_a_good_file_is_never_moved_aside():
    _write_raw(json.dumps({"launch": GOOD_MARKER, "offered": {"5": 1}}))
    assert not persist.is_damaged() and persist.offer_done(5)
    assert _bad_copies() == []


def test_a_damaged_file_that_cannot_be_moved_is_never_written_over(monkeypatch, caplog):
    _write_raw("{not json")

    real_check = persist.check_write

    def _refuse_the_move(target):
        if ".bad-" in Path(target).name:
            raise OSError("read-only folder")
        real_check(target)

    monkeypatch.setattr(persist, "check_write", _refuse_the_move)
    with caplog.at_level("WARNING", logger=persist.__name__):
        assert persist.is_damaged()
    persist.set_seen(5, "fa")
    persist.ensure_launch_marker([5])
    assert _file().read_text(encoding="utf-8") == "{not json"
    assert _bad_copies() == []
    assert any("left in place" in r.getMessage() for r in caplog.records)


def test_the_damage_is_logged_as_a_warning(caplog):
    _write_raw("{not json")
    with caplog.at_level("WARNING", logger=persist.__name__):
        persist.is_damaged()
    (record,) = [r for r in caplog.records if r.name == persist.__name__]
    assert record.levelname == "WARNING" and ".bad-" in record.getMessage()


def test_a_wrong_shaped_other_section_is_not_damage():
    _write_raw(json.dumps({"launch": GOOD_MARKER, "seen": "x"}))
    assert not persist.is_damaged() and persist.launch_marker() is not None
    assert _bad_copies() == []
