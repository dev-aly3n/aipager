"""Plan usage (roadmap 8.131): the ``rate_limits`` parser and the per
sign-in store of readings.

aipager never fetches plan usage: it keeps only what Claude Code hands its
status line. These pin the rules that keep that honest: a malformed window
is dropped, a payload without ``rate_limits`` never erases a reading, two
sign-ins never share one, and a restart keeps what was seen."""

from __future__ import annotations

import json
import math

import pytest

from aipager import config, usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.statusline_file import Window, parse_rate_limits

NOW = 1_794_000_000.0
LATER = int(NOW) + 2 * 86400
SAVED = SessionLogin("saved", "", "/home/alice/.claude")
TOKEN = SessionLogin("token", "env", "/home/alice/.claude")


def _rl(**windows) -> dict:
    return {"rate_limits": windows}


# ---- the parser ---------------------------------------------------------------------

def test_valid_windows_are_kept_in_the_known_names_only():
    got = parse_rate_limits(_rl(
        five_hour={"used_percentage": 7, "resets_at": LATER},
        seven_day={"used_percentage": 83.5, "resets_at": float(LATER) + 0.9},
        spend_limit={"used_percentage": 140, "resets_at": LATER, "used_usd": 3.2},
        fourteen_day={"used_percentage": 1, "resets_at": LATER}))
    assert got == {"five_hour": Window(7.0, LATER), "seven_day": Window(83.5, LATER),
                   "spend_limit": Window(140.0, LATER)}


@pytest.mark.parametrize("window", [
    {"used_percentage": True, "resets_at": LATER},
    {"used_percentage": "42", "resets_at": LATER},
    {"used_percentage": math.nan, "resets_at": LATER},
    {"used_percentage": math.inf, "resets_at": LATER},
    {"used_percentage": -1, "resets_at": LATER},
    {"used_percentage": 1000.5, "resets_at": LATER},
    {"used_percentage": None, "resets_at": LATER},
    {"used_percentage": 10},
    {"used_percentage": 10, "resets_at": "soon"},
    {"used_percentage": 10, "resets_at": False},
    {"used_percentage": 10, "resets_at": 999_999_999},
    {"used_percentage": 10, "resets_at": 4_000_000_001},
    {"used_percentage": 10, "resets_at": math.inf},
    "83%",
    None,
], ids=["pct-bool", "pct-str", "pct-nan", "pct-inf", "pct-neg", "pct-over", "pct-null",
        "no-reset", "reset-str", "reset-bool", "reset-low", "reset-high", "reset-inf",
        "not-object", "null"])
def test_a_malformed_window_is_dropped(window):
    data = _rl(seven_day=window, five_hour={"used_percentage": 5, "resets_at": LATER})
    assert parse_rate_limits(data) == {"five_hour": Window(5.0, LATER)}
    # Alone, nothing valid remains: nothing is returned at all.
    assert parse_rate_limits(_rl(seven_day=window)) is None


@pytest.mark.parametrize("data", [{}, {"rate_limits": None}, {"rate_limits": []},
                                  {"rate_limits": {}}, None, [], "x"],
                         ids=["absent", "null", "list", "empty", "none", "top-list", "str"])
def test_no_rate_limits_parses_as_none(data):
    assert parse_rate_limits(data) is None


# ---- the store ---------------------------------------------------------------------

def _windows(pct=42.0, resets=LATER, name="seven_day"):
    return {name: Window(pct, resets)}


def test_a_reading_is_kept_with_its_session_and_time():
    assert usage_store.note_plan("frontend", _windows(), SAVED, now=NOW) is True
    (reading,) = usage_store.snapshot(None, now=NOW).plan
    assert (reading.kind, reading.session, reading.seen_at) == ("saved", "frontend", NOW)
    assert reading.windows == {"seven_day": Window(42.0, LATER)}


def test_two_sign_ins_keep_two_readings_never_merged():
    usage_store.note_plan("frontend", _windows(42.0), SAVED, now=NOW)
    usage_store.note_plan("backend", _windows(9.0, name="five_hour"), TOKEN, now=NOW + 5)
    plan = usage_store.snapshot(None, now=NOW + 5).plan
    assert [(r.kind, r.session) for r in plan] == [("token", "backend"), ("saved", "frontend")]
    assert plan[1].windows == {"seven_day": Window(42.0, LATER)}
    assert plan[0].windows == {"five_hour": Window(9.0, LATER)}


@pytest.mark.parametrize("other", [
    SessionLogin("saved", "", "/home/alice/.claude-work"),
    SessionLogin("token", "user", "/home/alice/.claude"),
    SessionLogin("token", "project:/home/alice/frontend", "/home/alice/.claude"),
    SessionLogin("gateway", "gw.example", "/home/alice/.claude"),
], ids=["config-home", "source-layer", "folder-layer", "gateway"])
def test_logins_that_differ_in_any_part_are_kept_apart(other):
    usage_store.note_plan("a", _windows(1.0), TOKEN, now=NOW)
    usage_store.note_plan("b", _windows(2.0), other, now=NOW + 1)
    assert len(usage_store.snapshot(None, now=NOW + 1).plan) == 2


def test_the_same_sign_in_newest_reading_wholly_replaces_the_old():
    usage_store.note_plan("frontend", {"five_hour": Window(5.0, LATER),
                                       "seven_day": Window(40.0, LATER)}, SAVED, now=NOW)
    usage_store.note_plan("backend", _windows(41.0), SAVED, now=NOW + 60)
    (reading,) = usage_store.snapshot(None, now=NOW + 60).plan
    # five_hour stopped being reported: it is no longer shown.
    assert reading.windows == {"seven_day": Window(41.0, LATER)}
    assert reading.session == "backend"


def test_gateway_keeps_its_host_and_no_other_kind_does():
    usage_store.note_plan("a", _windows(), SessionLogin("gateway", "gw.example", "/h"), now=NOW)
    usage_store.note_plan("b", _windows(), SessionLogin("token", "user", "/h"), now=NOW)
    hosts = {r.kind: r.host for r in usage_store.snapshot(None, now=NOW).plan}
    assert hosts == {"gateway": "gw.example", "token": ""}


def test_at_most_eight_sign_ins_least_recently_seen_go_first():
    for i in range(usage_store.MAX_READINGS + 2):
        usage_store.note_plan(f"s{i}", _windows(), SessionLogin("saved", "", f"/h{i}"),
                              now=NOW + i)
    plan = usage_store.snapshot(None, now=NOW + 20).plan
    assert len(plan) == usage_store.MAX_READINGS
    assert {r.session for r in plan} == {f"s{i}" for i in range(2, 10)}


def test_a_reading_older_than_eight_days_is_not_shown():
    usage_store.note_plan("old", _windows(), SAVED, now=NOW)
    assert usage_store.snapshot(None, now=NOW + 8 * 86400 + 1).plan == ()
    assert len(usage_store.snapshot(None, now=NOW + 7 * 86400).plan) == 1


def test_unchanged_reading_is_not_rewritten_within_a_minute():
    usage_store.note_plan("a", _windows(), SAVED, now=NOW)
    assert usage_store.save_if_dirty(force=True) is True
    assert usage_store.note_plan("a", _windows(), SAVED, now=NOW + 30) is False
    assert usage_store.save_if_dirty(force=True) is False
    assert usage_store.note_plan("a", _windows(), SAVED, now=NOW + 61) is True


# ---- restart -----------------------------------------------------------------------

def test_a_restart_keeps_the_readings():
    usage_store.note_plan("frontend", _windows(83.0), SAVED, now=NOW)
    usage_store.note_plan("backend", _windows(5.0, name="five_hour"), TOKEN, now=NOW + 1)
    assert usage_store.save_if_dirty(force=True)
    before = usage_store.snapshot(None, now=NOW + 2).plan
    usage_store.reset_for_tests()
    assert usage_store.load() is True
    assert usage_store.snapshot(None, now=NOW + 2).plan == before


def test_the_file_is_owner_only():
    usage_store.note_plan("a", _windows(), SAVED, now=NOW)
    usage_store.save_if_dirty(force=True)
    assert (config.USAGE_STATE_FILE.stat().st_mode & 0o777) == 0o600


def _write(text: str) -> None:
    config.USAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.USAGE_STATE_FILE.write_text(text)


def _good_doc(**over) -> dict:
    doc = {"version": 1, "written_at": NOW, "plan": [{
        "key": "saved||/h", "kind": "saved", "host": "", "session": "a", "seen_at": NOW,
        "windows": {"seven_day": {"pct": 10.0, "resets_at": LATER}}}],
        "days": {}, "seen": {}, "cursors": {}}
    doc.update(over)
    return doc


@pytest.mark.parametrize("text", [
    "not json",
    json.dumps(_good_doc(version=2)),
    json.dumps(_good_doc()).replace("10.0", "NaN"),
    json.dumps(_good_doc()).replace("10.0", "Infinity"),
    json.dumps([1, 2]),
], ids=["not-json", "version", "nan", "infinity", "list"])
def test_a_bad_file_loads_as_empty_and_never_raises(text):
    _write(text)
    assert usage_store.load() is False
    assert usage_store.snapshot(None, now=NOW).plan == ()


def test_an_oversized_file_loads_as_empty(monkeypatch):
    _write(json.dumps(_good_doc()))
    monkeypatch.setattr(usage_store, "MAX_FILE_BYTES", 10)
    assert usage_store.load() is False


def test_the_good_file_loads(monkeypatch):
    _write(json.dumps(_good_doc()))
    assert usage_store.load() is True
    assert len(usage_store.snapshot(None, now=NOW).plan) == 1


@pytest.mark.parametrize("bad", [
    {"kind": "nope"},
    {"kind": "token"},                       # key does not match its kind
    {"seen_at": "yesterday"},
    {"windows": {"seven_day": {"pct": True, "resets_at": LATER}}},
    {"windows": {"seven_day": {"pct": 10, "resets_at": 12}}},
    {"windows": {"weekly": {"pct": 10, "resets_at": LATER}}},
    {"windows": {}},
], ids=["kind", "key-kind", "seen-at", "pct-bool", "reset-low", "window-name", "no-window"])
def test_a_bad_reading_in_the_file_is_dropped(bad):
    doc = _good_doc()
    doc["plan"][0].update(bad)
    _write(json.dumps(doc))
    usage_store.load()
    assert usage_store.snapshot(None, now=NOW).plan == ()
