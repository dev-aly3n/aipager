"""Black-box, fix iteration 2: a damaged ``languages.json`` is moved aside and
everyone stays English, with no offer and no fresh marker (phase 5 item 3;
entrypoints.md ``persist.is_damaged``: "the file on disk was unreadable,
oversized, not an object or had a wrong-shaped ``launch``; it was moved to
``languages.json.bad-<unix time>`` and the new file holds ``"damaged"``. While
so: no marker (everyone existing), ``ensure_launch_marker`` returns None, no
offer"; "A section of the wrong shape reads as empty").

Partitions: file = absent / good / not JSON / JSON but not an object / a
wrong-shaped ``launch`` / oversized / unreadable / a wrong-shaped other
section; after = the same process / a restart / the operator deleted the file.
The file path is the suite's redirect of ``config.LANGUAGE_STATE_FILE``
(under tmp), never the real home.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from aipager import config, i18n
from aipager import preferences as prefs
from aipager.bot import language_launch
from aipager.i18n import persist
from tests.i18n_ml2_bb import message

OWNER, BOB = 555, 2

GOOD = {"v": 1, "launch": {"at": 1760000000, "version": "1.1.0", "fresh": False,
                           "people": [OWNER]}}
DAMAGE = {
    "not-json": b"{\"v\": 1, \"launch\": {",
    "a-list": b"[1, 2, 3]",
    "a-string": b"\"languages\"",
    "launch-a-string": json.dumps({"v": 1, "launch": "yesterday"}).encode(),
    "launch-a-list": json.dumps({"v": 1, "launch": [OWNER]}).encode(),
    "oversized": json.dumps({"v": 1, "launch": GOOD["launch"],
                             "pad": "x" * 2_500_000}).encode(),
}


def _path():
    return config.LANGUAGE_STATE_FILE


def _write(data: bytes):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    persist.reset()


def _aside():
    return sorted(_path().parent.glob(_path().name + ".bad-*"))


@pytest.fixture(params=sorted(DAMAGE))
def damaged(request):
    _write(DAMAGE[request.param])
    return DAMAGE[request.param]


@pytest.fixture
def broken():
    """One representative damage (not JSON)."""
    _write(DAMAGE["not-json"])
    return DAMAGE["not-json"]


@pytest.fixture(autouse=True)
def _no_sweep_wait(monkeypatch):
    monkeypatch.setattr(language_launch, "SWEEP_DELAY", 0.0)


def _upd(user, code):
    return message("hi", chat=user, user=user, language_code=code)


# ---- what counts as damage --------------------------------------------------------------------

def test_damage_is_detected(damaged):
    assert persist.is_damaged() is True


def test_a_damaged_file_is_moved_aside_with_its_bytes(damaged):
    persist.is_damaged()
    assert [p.read_bytes() for p in _aside()] == [damaged]


def test_the_moved_file_is_named_bad_and_a_unix_time(damaged):
    persist.is_damaged()
    (aside,) = _aside()
    assert aside.name.split(".bad-", 1)[1].isdigit()


def test_the_new_file_records_the_damage(damaged):
    persist.is_damaged()
    assert "damaged" in json.loads(_path().read_text(encoding="utf-8"))


def test_an_unreadable_file_is_damage():
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    _write(json.dumps(GOOD).encode())
    os.chmod(_path(), 0)
    try:
        assert persist.is_damaged() is True
    finally:
        for p in [_path(), *_aside()]:
            if p.exists():
                os.chmod(p, 0o600)


def test_a_good_file_is_not_damage():
    _write(json.dumps(GOOD).encode())
    assert persist.is_damaged() is False


def test_a_good_file_is_never_moved():
    _write(json.dumps(GOOD).encode())
    persist.launch_marker()
    assert _aside() == []


def test_a_good_files_marker_is_kept():
    _write(json.dumps(GOOD).encode())
    assert persist.launch_marker()["people"] == [OWNER]


def test_no_file_at_all_is_not_damage():
    assert persist.is_damaged() is False


@pytest.mark.parametrize("section", ["seen", "offered", "first_contact", "descriptions"])
def test_a_wrong_shaped_other_section_is_not_damage(section):
    _write(json.dumps({**GOOD, section: "garbage"}).encode())
    assert persist.is_damaged() is False


def test_a_wrong_shaped_other_section_keeps_the_marker():
    _write(json.dumps({**GOOD, "seen": [1, 2]}).encode())
    assert persist.launch_marker()["people"] == [OWNER]


# ---- while damaged: no marker, everyone English ------------------------------------------------

def test_a_damaged_file_gives_no_marker(damaged):
    assert persist.launch_marker() is None


def test_ensure_launch_marker_writes_nothing_while_damaged(damaged):
    assert persist.ensure_launch_marker([OWNER, BOB], prior_run=True) is None


def test_ensure_launch_marker_leaves_no_marker_behind(broken):
    persist.ensure_launch_marker([OWNER], prior_run=True)
    assert persist.launch_marker() is None


def test_a_fresh_marker_is_not_written_while_damaged(broken):
    persist.ensure_launch_marker([], prior_run=False)
    assert persist.launch_marker() is None


def test_the_start_marker_step_does_not_raise_and_writes_none(broken, bot):
    language_launch.ensure_marker(bot)
    assert persist.launch_marker() is None


def test_every_person_counts_as_existing_while_damaged(broken):
    assert persist.is_existing_person(BOB) is True


def test_a_new_person_with_a_persian_app_reads_english(broken):
    assert i18n.for_update(_upd(BOB, "fa")).code == "en"


def test_a_new_chat_with_a_persian_app_reads_english_afterwards(broken):
    i18n.for_update(_upd(BOB, "fa"))
    assert i18n.for_chat(BOB).code == "en"


def test_an_explicit_choice_still_wins_while_damaged(broken):
    prefs.set_ui_language(BOB, "fa")
    assert i18n.for_chat(BOB).code == "fa"


# ---- while damaged: nothing is sent ----------------------------------------------------------------

def test_no_offer_while_damaged(broken, bot, launch, p5):
    launch(bot, _upd(OWNER, "fa"))
    assert p5.sends(bot, OWNER) == []


def test_no_first_contact_line_while_damaged(broken, bot, launch, p5):
    launch(bot, _upd(BOB, "fa"))
    assert p5.sends(bot, BOB) == []


def test_the_start_sweep_sends_nothing_while_damaged(broken, bot, run_async, p5):
    persist.set_seen(OWNER, "fa")
    run_async(language_launch.offer_sweep(bot))
    assert p5.sends(bot) == []


def test_no_offer_is_recorded_while_damaged(broken, bot, launch):
    launch(bot, _upd(OWNER, "fa"))
    assert persist.offer_done(OWNER) is False


# ---- across a restart, and the way out --------------------------------------------------------------

def test_the_damage_survives_a_restart(broken, p5):
    persist.is_damaged()
    p5.restart()
    assert persist.is_damaged() is True


def test_no_marker_after_a_restart_either(broken, p5):
    persist.is_damaged()
    p5.restart()
    persist.ensure_launch_marker([OWNER], prior_run=True)
    assert persist.launch_marker() is None


def test_a_restart_moves_nothing_more_aside(broken, p5):
    persist.is_damaged()
    p5.restart()
    persist.is_damaged()
    assert len(_aside()) == 1


def test_deleting_the_file_ends_the_damage(broken, p5):
    persist.is_damaged()
    _path().unlink()
    p5.restart()
    assert persist.is_damaged() is False


def test_after_deleting_the_file_a_marker_is_written_again(broken, p5):
    persist.is_damaged()
    _path().unlink()
    p5.restart()
    assert persist.ensure_launch_marker([OWNER], prior_run=True) is not None


def test_putting_a_repaired_file_back_ends_the_damage(broken, p5):
    persist.is_damaged()
    _write(json.dumps(GOOD).encode())
    assert persist.launch_marker()["people"] == [OWNER]


def test_the_moved_copy_survives_later_writes(broken, bot, launch):
    persist.is_damaged()
    persist.set_seen(OWNER, "fa")
    persist.mark_first_contact_done(BOB)
    launch(bot, _upd(BOB, "ru"))
    assert [p.read_bytes() for p in _aside()] == [broken]


def test_a_damaged_file_is_not_silently_rewritten_without_a_copy(broken):
    persist.ensure_launch_marker([OWNER], prior_run=True)
    persist.set_seen(OWNER, "fa")
    assert _aside() != []


@pytest.mark.parametrize("kind", ["offer", "first_contact"])
def test_other_records_still_work_while_damaged(broken, kind):
    if kind == "offer":
        persist.mark_offer_done(OWNER)
        assert persist.offer_done(OWNER) is True
    else:
        persist.mark_first_contact_done(BOB)
        assert persist.first_contact_done(BOB) is True


def test_the_post_reply_hook_never_raises_on_a_damaged_file(broken, bot, run_async):
    async def go():
        await language_launch.after_update(bot, _upd(BOB, "fa"), MagicMock())
        await language_launch.drain(bot)
    assert run_async(go()) is None
