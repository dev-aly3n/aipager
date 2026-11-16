"""Restart persistence and an untrusted state file (design.md Success
criteria 12 and 13; entrypoints.md "Side effects").

Partitions: a clean save/load, then each kind of bad file (corrupt JSON,
wrong version, non-finite number, oversized, wrong top-level type, bad
entries inside a good document). Boundary: MAX_FILE_BYTES exactly vs one
byte more. Error guessing: a missing file, a directory in the file's
place, a reload followed by a re-scan of the same transcripts."""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from aipager import config, usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.state import SessionRegistry
from aipager.statusline_file import Window

NOW = time.time()
SAVED = SessionLogin("saved", "", "/home/alice/.claude")
SCOPE = -100777


@pytest.fixture
def rig(h, tmp_path):
    reg = SessionRegistry()
    path = tmp_path / "conv-a.jsonl"
    h.append(path, h.entry("msg_1", NOW - 5, 100, 20, 3, 4))
    h.add_session(reg, "claude-frontend", scope=SCOPE, transcript=path)
    usage_store.note_plan("frontend", {"seven_day": Window(83.0, int(NOW + 86400))}, SAVED,
                          now=NOW)
    h.scan(reg, now=NOW)
    return reg, path


def _restart(path=None):
    usage_store.save_if_dirty(path=path, force=True)
    usage_store.reset_for_tests()
    return usage_store.load(path=path)


def _state() -> tuple:
    s = usage_store.snapshot(None, now=NOW)
    return len(s.plan), tuple(int(x) for x in s.today)


def test_save_writes_the_state_file(rig):
    usage_store.save_if_dirty(force=True)
    assert os.path.isfile(config.USAGE_STATE_FILE)


def test_the_state_file_is_owner_only(rig):
    usage_store.save_if_dirty(force=True)
    assert stat.S_IMODE(os.stat(config.USAGE_STATE_FILE).st_mode) == 0o600


def test_the_state_file_has_the_documented_top_level_keys(rig):
    usage_store.save_if_dirty(force=True)
    doc = json.loads(open(config.USAGE_STATE_FILE).read())
    assert set(doc) >= {"version", "written_at", "plan", "days", "seen", "cursors"}


def test_the_state_file_is_version_1(rig):
    usage_store.save_if_dirty(force=True)
    assert json.loads(open(config.USAGE_STATE_FILE).read())["version"] == 1


def test_the_state_file_holds_no_dollar_or_cost(rig):
    usage_store.save_if_dirty(force=True)
    raw = open(config.USAGE_STATE_FILE).read()
    assert ("$" in raw, "cost" in raw, "usd" in raw) == (False, False, False)


def test_load_reports_success(rig):
    assert _restart() is True


def test_a_restart_keeps_the_plan_reading(rig):
    _restart()
    assert _state()[0] == 1


def test_a_restart_keeps_the_token_totals(rig):
    _restart()
    assert _state()[1] == (100, 20, 3, 4)


def test_a_rescan_after_a_restart_adds_nothing(h, rig):
    reg, _path = rig
    _restart()
    h.scan(reg, now=NOW + 1)
    assert _state()[1] == (100, 20, 3, 4)


def test_a_rescan_after_a_restart_still_counts_new_lines(h, rig):
    reg, path = rig
    _restart()
    h.append(path, h.entry("msg_2", NOW - 1, 1, 1, 1, 1))
    h.scan(reg, now=NOW + 1)
    assert _state()[1] == (101, 21, 4, 5)


def test_a_reset_without_the_file_forgets_everything(rig):
    """Control for the restart tests: what survives comes from the file."""
    usage_store.save_if_dirty(force=True)
    os.unlink(config.USAGE_STATE_FILE)
    usage_store.reset_for_tests()
    assert _state() == (0, (0, 0, 0, 0))


def test_an_explicit_path_round_trips(rig, tmp_path):
    p = tmp_path / "elsewhere" / "usage.json"
    p.parent.mkdir()
    _restart(p)
    assert _state() == (1, (100, 20, 3, 4))


def test_read_returns_the_saved_plan(rig):
    usage_store.save_if_dirty(force=True)
    assert len(usage_store.read()["plan"]) == 1


def test_nothing_to_save_writes_nothing():
    assert usage_store.save_if_dirty() is False


# ---- untrusted files --------------------------------------------------------------------

def _good_doc(rig) -> str:
    usage_store.save_if_dirty(force=True)
    return open(config.USAGE_STATE_FILE).read()


def _load_text(text: str | bytes) -> tuple:
    p = config.USAGE_STATE_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    usage_store.reset_for_tests()
    if isinstance(text, bytes):
        open(p, "wb").write(text)
    else:
        open(p, "w").write(text)
    usage_store.load()
    return _state()


@pytest.mark.parametrize("text", ["", "{not json", "[]", "null", "42", '"x"',
                                  b"\xff\xfe\x00garbage"],
                         ids=["empty", "corrupt", "list", "null", "number", "string",
                              "binary"])
def test_a_corrupt_file_loads_as_empty(rig, text):
    assert _load_text(text) == (0, (0, 0, 0, 0))


@pytest.mark.parametrize("version", [0, 2, "1", None], ids=["0", "2", "string", "none"])
def test_a_wrong_version_loads_as_empty(rig, version):
    doc = json.loads(_good_doc(rig))
    doc["version"] = version
    assert _load_text(json.dumps(doc)) == (0, (0, 0, 0, 0))


def test_a_good_document_loads_control(rig):
    """Control: the unmodified document loads (so the cases above are real)."""
    assert _load_text(_good_doc(rig)) == (1, (100, 20, 3, 4))


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_number_never_loads(rig, bad):
    text = _good_doc(rig).replace("83.0", bad)
    assert _load_text(text)[0] == 0


def test_a_file_over_the_size_cap_loads_as_empty(rig):
    text = _good_doc(rig)
    padded = text + " " * (usage_store.MAX_FILE_BYTES + 1 - len(text.encode()))
    assert _load_text(padded) == (0, (0, 0, 0, 0))


def test_a_file_at_the_size_cap_loads(rig):
    text = _good_doc(rig)
    padded = text + " " * (usage_store.MAX_FILE_BYTES - len(text.encode()))
    assert _load_text(padded)[0] == 1


def test_a_missing_file_loads_as_empty():
    assert (usage_store.load(), _state()) == (False, (0, (0, 0, 0, 0)))


def test_a_directory_in_place_of_the_file_never_raises(tmp_path):
    d = tmp_path / "dir-usage.json"
    d.mkdir()
    assert usage_store.load(path=d) is False


def test_a_bad_plan_entry_is_dropped_and_the_tokens_kept(rig):
    doc = json.loads(_good_doc(rig))
    doc["plan"] = [{"key": 5, "windows": "x"}]
    assert _load_text(json.dumps(doc)) == (0, (100, 20, 3, 4))


def test_a_bad_day_entry_is_dropped_and_the_plan_kept(rig):
    doc = json.loads(_good_doc(rig))
    doc["days"] = {"not-a-date": {"x": "y"}}
    assert _load_text(json.dumps(doc)) == (1, (0, 0, 0, 0))


def test_negative_counts_in_the_file_never_load(rig):
    doc = json.loads(_good_doc(rig))
    for scopes in doc["days"].values():
        for models in scopes.values():
            for k in models:
                models[k] = [-1, -1, -1, -1]
    assert _load_text(json.dumps(doc))[1] == (0, 0, 0, 0)


def test_read_of_a_corrupt_file_never_raises(rig):
    os.makedirs(os.path.dirname(config.USAGE_STATE_FILE), exist_ok=True)
    open(config.USAGE_STATE_FILE, "w").write("{not json")
    assert isinstance(usage_store.read(), dict)
