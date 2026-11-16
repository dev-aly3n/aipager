"""No string can stop usage.json from being written (review
rev-iter2-001).

A lone surrogate comes from untrusted input the store reads: a broken
``\\ud800`` escape in a transcript's model id, a non-UTF-8 subagent file
name (``os.listdir`` gives ``\\udcXX``), a sign-in's config home or
gateway host, a session label. Saved with ``ensure_ascii=False`` it made
the strict UTF-8 encode fail on every save while the string stayed in
memory. Text that is shown is cleaned where it comes in; a path, which
must stay exact to be opened again, is escaped by the save."""

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
DAY = usage_store._day_key(dt.datetime.fromtimestamp(NOW, dt.timezone.utc).date())


@pytest.fixture(autouse=True)
def _utc_clock(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


def _line(mid: str, model: str = "claude-haiku", ts: float = NOW) -> str:
    stamp = dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")
    # json.dumps writes a lone surrogate as its ASCII escape, as a
    # transcript holding a broken escape would.
    return json.dumps({"type": "assistant", "timestamp": stamp, "message": {
        "id": mid, "model": model, "usage": {"input_tokens": 10,
                                             "output_tokens": 1}}}) + "\n"


def _encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _save_and_reload() -> None:
    assert usage_store.save_if_dirty(force=True) is True
    assert usage_store._dirty is False
    Path(config.USAGE_STATE_FILE).read_bytes().decode("utf-8")   # valid UTF-8
    usage_store.reset_for_tests()
    assert usage_store.load() is True


# ---- the helper ----------------------------------------------------------------------------

def test_clean_text_replaces_only_what_cannot_be_encoded():
    assert usage_store.clean_text("gw-\ud800x") == "gw-?x"
    assert usage_store.clean_text("\udcff") == "?"
    ok = "claude-\U0001F600-é"
    assert usage_store.clean_text(ok) is ok


# ---- a model id from a transcript ------------------------------------------------------------

def test_a_surrogate_model_id_saves_and_reloads(tmp_path):
    registry = SessionRegistry()
    sess = registry.get_or_create("claude-frontend")
    sess.label, sess.scope_chat_id = "frontend", -1
    path = tmp_path / "t.jsonl"
    path.write_text(_line("m-1", model="gw-\ud800"), encoding="ascii")
    sess.transcript_path = str(path)
    usage_store.scan(registry, now=NOW, force=True)
    assert usage_store._days[DAY][-1] == {"gw-?": [10, 1, 0, 0]}
    _save_and_reload()
    assert usage_store._days[DAY][-1] == {"gw-?": [10, 1, 0, 0]}
    assert dict(usage_store.snapshot(None, now=NOW).models) == {"gw-?": (10, 1, 0, 0)}


# ---- a file name that is not UTF-8 -----------------------------------------------------------

def test_a_surrogate_file_path_saves_reloads_and_is_not_counted_twice(tmp_path):
    registry = SessionRegistry()
    sess = registry.get_or_create("claude-frontend")
    sess.label, sess.scope_chat_id = "front\udc80end", -1
    main = tmp_path / "s.jsonl"
    main.write_text("")
    sess.transcript_path = str(main)
    subdir = tmp_path / "s" / "subagents"
    subdir.mkdir(parents=True)
    raw_name = os.fsencode(str(subdir)) + b"/agent-\xff.jsonl"
    with open(raw_name, "wb") as f:
        f.write(_line("m-sub").encode("ascii"))
    sub = os.fsdecode(raw_name)
    assert not _encodable(sub)

    usage_store.scan(registry, now=NOW, force=True)
    assert usage_store.snapshot(None, now=NOW).today == (10, 1, 0, 0)
    assert usage_store._cursors[sub]["session"] == "front?end"
    offset = usage_store._cursors[sub]["offset"]
    assert offset > 0
    _save_and_reload()
    # The path comes back exact (it must still open), its place kept.
    assert usage_store._cursors[sub]["offset"] == offset
    assert usage_store._cursors[sub]["session"] == "front?end"
    usage_store.scan(registry, now=NOW + 60, force=True)
    assert usage_store.snapshot(None, now=NOW + 60).today == (10, 1, 0, 0)


# ---- a sign-in's key, host and session label ------------------------------------------------

def test_a_surrogate_plan_key_host_and_label_save_and_reload():
    windows = {"seven_day": Window(42.0, int(NOW) + 86400)}
    usage_store.note_plan("lab\ud800el", windows,
                          SessionLogin("gateway", "gw.\udc80example", "/home/alice/\udcff"),
                          scope=-1, now=NOW)
    usage_store.note_plan("plain", windows, SessionLogin("saved", "", "/home/alice/c\ud800"),
                          scope=-1, now=NOW + 1)
    for r in usage_store._plan.values():
        assert all(_encodable(r[k]) for k in ("key", "host", "session"))
    _save_and_reload()
    readings = {r.kind: r for r in usage_store.snapshot(None, now=NOW + 2).plan}
    assert set(readings) == {"gateway", "saved"}
    gw = readings["gateway"]
    assert (gw.key, gw.host, gw.session) == (
        "gateway|gw.?example|/home/alice/?", "gw.?example", "lab?el")
    assert readings["saved"].key == "saved||/home/alice/c?"
    assert readings["saved"].windows["seven_day"].pct == 42.0


# ---- the save itself ------------------------------------------------------------------------

def test_the_save_escapes_a_surrogate_that_got_into_memory_anyway():
    """A final fallback: whatever got past the cleaning, the save still
    writes everything, and the value loads back the same."""
    usage_store.load()
    usage_store._days[DAY] = {-1: {"m-\ud800": [1, 2, 3, 4], "ok": [1, 1, 1, 1]}}
    path = "/home/alice/agent-\udcff.jsonl"
    usage_store._cursors[path] = usage_store._new_cursor(os.stat("/"), -1, "x")
    usage_store._dirty = True
    assert usage_store.save_if_dirty(force=True) is True
    doc = usage_store.read()
    assert path in doc["cursors"]
    # Shown text loads cleaned.
    assert doc["days"][DAY][-1] == {"m-?": [1, 2, 3, 4], "ok": [1, 1, 1, 1]}


def test_stored_text_with_an_escaped_surrogate_loads_cleaned():
    doc = {"version": usage_store.SCHEMA_VERSION, "plan": [{
        "key": "saved||/h\ud800", "kind": "saved", "host": "", "session": "s\udc80",
        "scope": -1, "seen_at": NOW,
        "windows": {"seven_day": {"pct": 1.0, "resets_at": int(NOW) + 60}}}],
        # Two ids that clean to the same text (built, so no linter merges them).
        "days": {DAY: {"-1": {"a" + chr(0xD800): [1, 0, 0, 0], "a" + chr(0xDC80): [2, 0, 0, 0]}}},
        "cursors": {"/h/t.jsonl": {"ino": 1, "dev": 1, "offset": 0, "skip": False,
                                   "scope": -1, "session": "c\ud800",
                                   "grown_at": NOW, "read_at": 0.0}}}
    Path(config.USAGE_STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.USAGE_STATE_FILE).write_text(json.dumps(doc), encoding="utf-8")
    got = usage_store.read()
    reading = got["plan"][0]
    assert (reading["key"], reading["session"]) == ("saved||/h?", "s?")
    assert got["days"][DAY][-1] == {"a?": [1, 0, 0, 0]}   # the first of the two
    assert got["cursors"]["/h/t.jsonl"]["session"] == "c?"


# ---- the key's length -----------------------------------------------------------------------

def test_a_plan_key_past_1024_chars_is_cut_with_a_hash_and_survives_a_restart():
    windows = {"seven_day": Window(5.0, int(NOW) + 86400)}
    long_a = "/home/alice/" + "a" * 2000 + "/one"
    long_b = "/home/alice/" + "a" * 2000 + "/two"
    usage_store.note_plan("s1", windows, SessionLogin("saved", "", long_a), now=NOW)
    usage_store.note_plan("s2", windows, SessionLogin("saved", "", long_b), now=NOW + 1)
    keys = set(usage_store._plan)
    assert len(keys) == 2                       # two sign-ins, two keys
    assert all(len(k) <= 1024 for k in keys)
    _save_and_reload()
    assert set(usage_store._plan) == keys
