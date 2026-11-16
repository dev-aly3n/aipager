"""The usage store's caps agree with its read limit (review
rev-iter1-002): the largest document the caps let aipager write still
loads, so a heavy install never restarts to an empty store. Plus the caps
that keep it so: chats per day, path length, days kept whatever the clock
did, model rows on load, and the resave marks of dropped readings."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import pytest

from aipager import config, usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.state import SessionRegistry
from aipager.statusline_file import Window

NOW = 1_794_000_000.0


@pytest.fixture(autouse=True)
def _utc_clock(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


def _days(n: int) -> list[str]:
    today = dt.date(2026, 11, 16)
    return [(today - dt.timedelta(days=i)).isoformat() for i in range(n)]


def _fill_to_the_caps(ch: str) -> dict:
    """Memory at every cap, each text field at its cap in *ch*. Returns
    what was put in, to compare with what loads back."""
    s = usage_store
    for i in range(s.MAX_READINGS):
        key = "gateway|" + ch * (1024 - len("gateway|") - 4) + f"{i:04d}"
        s._plan[key] = {"key": key, "kind": "gateway", "host": ch * 255,
                        "session": ch * 128, "scope": -(10**19) - i,
                        "seen_at": float(s.TS_MAX),
                        "windows": {n: {"pct": 1000.0, "resets_at": s.TS_MAX}
                                    for n in ("five_hour", "seven_day", "spend_limit")}}
    days = _days(s.MAX_DAYS)
    for d, day in enumerate(days):
        s._days[day] = {
            -(10**19) - scope: {
                **{(ch * (s.MODEL_MAX_CHARS - 4) + f"{m:04d}"): [s.SUM_MAX] * 4
                   for m in range(s.MAX_MODELS - 1)},
                s.OTHER_MODEL: [s.SUM_MAX] * 4}
            for scope in range(s.MAX_SCOPES_PER_DAY)}
        ids = {f"{d:02x}{i:014x}": s.COUNT_MAX - 1 for i in range(s.MAX_IDS_PER_DAY)}
        s._seen[day] = ids
        s._seen_day.update(dict.fromkeys(ids, day))
    for i in range(s.MAX_CURSORS):
        path = "/" + ch * (s.MAX_PATH_CHARS - 1 - 10) + f"{i:04d}.jsonl"
        s._cursors[path] = {"ino": 2**64 - 1, "dev": 2**64 - 1, "offset": 2**63 - 1,
                            "skip": True, "scope": -(10**19), "session": ch * 128,
                            "grown_at": float(s.TS_MAX) - 0.123456789,
                            "read_at": float(s.TS_MAX) - 0.987654321}
    return {"plan": len(s._plan), "days": {d: len(v) for d, v in s._days.items()},
            "seen": sum(len(v) for v in s._seen.values()), "cursors": len(s._cursors)}


@pytest.mark.parametrize("ch", ["\x01", "\U0001F600", "a"],
                         ids=["escaped-control", "astral", "plain"])
def test_the_largest_document_the_caps_allow_loads_again(ch):
    usage_store.load()                # an empty, loaded store
    put = _fill_to_the_caps(ch)
    usage_store._dirty = True
    assert usage_store.save_if_dirty(force=True)
    size = Path(config.USAGE_STATE_FILE).stat().st_size
    assert size < usage_store.MAX_FILE_BYTES * 0.8   # well under, not just under
    usage_store.reset_for_tests()
    assert usage_store.load() is True
    s = usage_store
    assert {"plan": len(s._plan), "days": {d: len(v) for d, v in s._days.items()},
            "seen": sum(len(v) for v in s._seen.values()),
            "cursors": len(s._cursors)} == put
    assert all(len(models) == s.MAX_MODELS for day in s._days.values()
               for models in day.values())


# ---- chats per day -----------------------------------------------------------------------

def _line(mid: str, ts: float = NOW) -> str:
    stamp = dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps({"type": "assistant", "timestamp": stamp, "message": {
        "id": mid, "model": "claude-haiku", "usage": {"input_tokens": 10,
                                                      "output_tokens": 1}}}) + "\n"


def _session(registry, tmp_path, label, scope, parent=None):
    sess = registry.get_or_create(f"claude-{label}")
    sess.label, sess.scope_chat_id = label, scope
    path = (parent or tmp_path) / f"{label}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_line(f"m-{label}"))
    sess.transcript_path = str(path)
    return path


def test_a_day_counts_at_most_max_scopes_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_store, "MAX_SCOPES_PER_DAY", 2)
    registry = SessionRegistry()
    for i, scope in enumerate((-1, -2, -3)):
        _session(registry, tmp_path, f"s{i}", scope)
    usage_store.scan(registry, now=NOW, force=True)
    day = usage_store._day_key(usage_store._date_of(NOW))
    assert len(usage_store._days[day]) == 2
    # The chats already counted still count.
    assert usage_store.snapshot(None, now=NOW).today == (20, 2, 0, 0)


# ---- path length -------------------------------------------------------------------------

def test_a_transcript_path_past_the_cap_is_not_followed(tmp_path):
    registry = SessionRegistry()
    deep = tmp_path
    while len(str(deep)) <= usage_store.MAX_PATH_CHARS:
        deep = deep / ("d" * 200)
    long_path = _session(registry, tmp_path, "far", -1, parent=deep)
    short_path = _session(registry, tmp_path, "near", -1)
    assert len(str(long_path)) > usage_store.MAX_PATH_CHARS >= len(str(short_path))
    usage_store.scan(registry, now=NOW, force=True)
    assert set(usage_store._cursors) == {str(short_path)}
    assert usage_store.snapshot(None, now=NOW).today == (10, 1, 0, 0)


def test_a_stored_cursor_past_the_path_cap_is_dropped():
    usage_store.load()
    path = "/" + "p" * usage_store.MAX_PATH_CHARS + ".jsonl"
    usage_store._cursors[path] = usage_store._new_cursor(os.stat("/"), -1, "x")
    usage_store._dirty = True
    usage_store.save_if_dirty(force=True)
    assert usage_store.read()["cursors"] == {}


# ---- days kept whatever the clock did ----------------------------------------------------

def test_a_clock_set_back_still_keeps_at_most_max_days():
    """Days ahead of a clock set back are not past days, so only the
    count cap removes them; the newest are kept."""
    usage_store.load()
    later = [(dt.date(2027, 3, 1) + dt.timedelta(days=i)).isoformat() for i in range(12)]
    for day in later:
        usage_store._days[day] = {-1: {"m": [1, 1, 1, 1]}}
        usage_store._seen[day] = {f"{len(usage_store._seen):016x}": 1}
    for day, ids in usage_store._seen.items():
        usage_store._seen_day.update(dict.fromkeys(ids, day))
    usage_store.scan(SessionRegistry(), now=NOW, force=True)
    keep = sorted(later)[-usage_store.MAX_DAYS:]
    assert sorted(usage_store._days) == keep
    assert sorted(usage_store._seen) == keep
    assert set(usage_store._seen_day.values()) == set(keep)


# ---- model rows ---------------------------------------------------------------------------

def test_a_stored_day_keeps_max_models_rows_other_included():
    models = {f"model-{i}": [i, 0, 0, 0] for i in range(20)}
    models[usage_store.OTHER_MODEL] = [7, 0, 0, 0]
    doc = {"version": usage_store.SCHEMA_VERSION, "days": {"2026-11-16": {"-1": models}}}
    Path(config.USAGE_STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.USAGE_STATE_FILE).write_text(json.dumps(doc), encoding="utf-8")
    kept = usage_store.read()["days"]["2026-11-16"][-1]
    assert len(kept) == usage_store.MAX_MODELS
    assert kept[usage_store.OTHER_MODEL] == [7, 0, 0, 0]
    assert sorted(m for m in kept if m != usage_store.OTHER_MODEL) == sorted(
        f"model-{i}" for i in range(usage_store.MAX_MODELS - 1))


# ---- resave marks -------------------------------------------------------------------------

def test_a_dropped_reading_takes_its_resave_mark_with_it():
    windows = {"seven_day": Window(1.0, int(NOW) + 86400)}
    for i in range(usage_store.MAX_READINGS + 2):
        usage_store.note_plan(f"s{i}", windows, SessionLogin("saved", "", f"/h{i}"),
                              now=NOW + i)
    assert set(usage_store._plan_marked) == set(usage_store._plan)
    usage_store.scan(SessionRegistry(), now=NOW + usage_store.KEEP_DAYS * 86400 + 100,
                     force=True)
    assert usage_store._plan == {} and usage_store._plan_marked == {}
