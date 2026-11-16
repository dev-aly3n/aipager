"""The token tally (roadmap 8.131): read incrementally from the
transcripts of aipager's sessions, each ``message.id`` counted once.

Every guard here is about never double counting (and never losing a
whole line) across what really happens to a transcript: more lines of the
same message, /compact and resume (same file), /clear (a new file), a fork
or copy (copied lines), truncation or replacement, a daemon restart, a
half-written last line, a giant line, and a first run's backlog."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import time
from unittest.mock import AsyncMock

import pytest

from aipager import usage_store
from aipager.state import SessionRegistry

GROUP_A, GROUP_B = -100, -200
NOW = time.time()


@pytest.fixture(autouse=True)
def _utc_days(monkeypatch):
    """Days in UTC here, so a timestamp's day is plain to read (the seam,
    never ``time`` itself)."""
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _line(mid, *, ts=NOW, inp=10, out=5, cr=100, cw=20, model="claude-haiku-4-5",
          kind="assistant", usage=True) -> str:
    message = {"id": mid, "model": model, "role": "assistant", "content": [{"type": "text"}]}
    if usage:
        message["usage"] = {"input_tokens": inp, "output_tokens": out,
                            "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}
    if mid is None:
        del message["id"]
    return json.dumps({"type": kind, "timestamp": _iso(ts), "message": message}) + "\n"


def _append(path, *lines):
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(lines))


@pytest.fixture
def world(tmp_path):
    registry = SessionRegistry()

    def session(label, scope=GROUP_A, name=None):
        sess = registry.get_or_create(f"claude-{name or label}")
        sess.label = label
        sess.scope_chat_id = scope
        path = tmp_path / "projects" / f"{label}-{len(registry.all_sessions())}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        sess.transcript_path = str(path)
        return sess, path

    return registry, session


def _scan(registry, now=NOW):
    usage_store.scan(registry, now=now, force=True)


def _today(scopes=None, now=NOW):
    return usage_store.snapshot(scopes, now=now).today


# ---- counting once ------------------------------------------------------------------

def test_each_message_counts_once_across_its_lines_and_scans(world):
    registry, session = world
    _sess, path = session("frontend")
    # Claude Code writes one line per content block, all with one id.
    _append(path, _line("msg_1"), _line("msg_1"), _line("msg_1"))
    _scan(registry)
    assert _today() == (10, 5, 100, 20)
    _append(path, _line("msg_1"), _line("msg_2", inp=1, out=2, cr=3, cw=4))
    _scan(registry)
    assert _today() == (11, 7, 103, 24)


def test_a_larger_later_output_adds_only_the_difference(world):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1", out=3))
    _scan(registry)
    _append(path, _line("msg_1", out=160), _line("msg_1", out=40))
    _scan(registry)
    # 160 in all, never 3 + 160 + 40; input counted once.
    assert _today() == (10, 160, 100, 20)


def test_lines_without_an_id_still_dedupe_on_their_copy(world):
    registry, session = world
    _sess, path = session("frontend")
    line = _line(None)
    _append(path, line, line)
    _scan(registry)
    assert _today() == (10, 5, 100, 20)


@pytest.mark.parametrize("line", [
    _line("msg_s", model="<synthetic>"),
    _line("msg_u", kind="user"),
    _line("msg_n", usage=False),
    json.dumps({"type": "assistant", "timestamp": _iso(NOW), "message": "x usage"}) + "\n",
    json.dumps({"type": "assistant", "message": {"id": "m", "usage": {"input_tokens": 9}}})
    + "\n",
    "{not json, \"usage\" \"assistant\"\n",
], ids=["synthetic", "user", "no-usage", "message-not-object", "no-timestamp", "not-json"])
def test_lines_that_are_not_counted(world, line):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, line, _line("msg_ok"))
    _scan(registry)
    assert _today() == (10, 5, 100, 20)


def test_a_bad_count_counts_zero(world):
    registry, session = world
    _sess, path = session("frontend")
    raw = json.loads(_line("msg_1"))
    raw["message"]["usage"].update(input_tokens=True, output_tokens=-4,
                                   cache_read_input_tokens="7",
                                   cache_creation_input_tokens=10**12)
    _append(path, json.dumps(raw) + "\n")
    _scan(registry)
    assert usage_store.snapshot(None, now=NOW).week == (0, 0, 0, 0)


# ---- what happens to a transcript ---------------------------------------------------

def test_compact_appends_to_the_same_file_and_the_offset_continues(world):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1"))
    _scan(registry)
    _append(path, json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n",
            _line("msg_2"))
    _scan(registry)
    assert _today() == (20, 10, 200, 40)


def test_resume_after_a_restart_neither_forgets_nor_counts_twice(world):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1"), _line("msg_2"))
    _scan(registry)
    assert usage_store.save_if_dirty(force=True)
    usage_store.reset_for_tests()
    assert usage_store.load()
    _scan(registry)                       # a re-scan after load adds nothing
    assert _today() == (20, 10, 200, 40)
    _append(path, _line("msg_3"))         # resume: the same file goes on
    _scan(registry)
    assert _today() == (30, 15, 300, 60)


def test_restart_without_saved_offsets_still_counts_once(world):
    """A crash between saves loses offsets, counts and ids TOGETHER (one
    document), so the re-read counts everything exactly once."""
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1"))
    _scan(registry)
    usage_store.reset_for_tests()         # nothing was saved
    _scan(registry)
    assert _today() == (10, 5, 100, 20)


def test_clear_starts_a_new_file_and_the_old_ones_tail_is_finished(world, tmp_path):
    registry, session = world
    sess, old = session("frontend")
    _append(old, _line("msg_1"))
    _scan(registry)
    new = tmp_path / "projects" / "after-clear.jsonl"
    _append(old, _line("msg_2"))          # flushed late, after /clear
    _append(new, _line("msg_3"))
    sess.transcript_path = str(new)
    _scan(registry)
    assert _today() == (30, 15, 300, 60)


def test_a_fork_or_copy_counts_only_its_new_messages(world, tmp_path):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1"), _line("msg_2"))
    _scan(registry)
    forked, fork_path = session("job", name="job")
    _append(fork_path, path.read_text(), _line("msg_new"))
    _scan(registry)
    assert _today() == (30, 15, 300, 60)


def test_truncation_rereads_without_counting_twice(world):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1"), _line("msg_2"))
    _scan(registry)
    path.write_text(_line("msg_2"))       # smaller than the offset
    _scan(registry)
    assert _today() == (20, 10, 200, 40)
    _append(path, _line("msg_3"))
    _scan(registry)
    assert _today() == (30, 15, 300, 60)


def test_a_replaced_file_is_read_from_the_start(world, tmp_path):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1"))
    _scan(registry)
    other = tmp_path / "replacement.jsonl"
    other.write_text(_line("msg_1") + _line("msg_2") + _line("msg_3"))
    os.replace(other, path)               # a new inode, larger than before
    _scan(registry)
    assert _today() == (30, 15, 300, 60)


def test_a_partial_trailing_line_is_never_consumed(world):
    registry, session = world
    _sess, path = session("frontend")
    whole = _line("msg_1")
    _append(path, whole[:40])
    _scan(registry)
    _append(path, whole[40:])
    _scan(registry)
    assert _today() == (10, 5, 100, 20)


def test_subagent_transcripts_count_under_the_parents_scope(world):
    registry, session = world
    _sess, path = session("frontend", scope=GROUP_B)
    sub = path.with_suffix("") / "subagents"
    sub.mkdir(parents=True)
    _append(sub / "agent-a1b2.jsonl", _line("msg_agent"))
    _append(sub / "notes.jsonl", _line("msg_not_an_agent"))
    _append(path, _line("msg_main"))
    _scan(registry)
    assert _today(frozenset({GROUP_B})) == (20, 10, 200, 40)
    assert _today(frozenset({GROUP_A})) == (0, 0, 0, 0)


# ---- bounds ------------------------------------------------------------------------

def test_one_scan_reads_at_most_its_budget_and_says_it_is_behind(world, monkeypatch):
    registry, session = world
    monkeypatch.setattr(usage_store, "SCAN_BUDGET_BYTES", 1500)
    monkeypatch.setattr(usage_store, "CHUNK_BYTES", 600)
    paths = [session(f"s{i}")[1] for i in range(3)]
    for i, p in enumerate(paths):
        _append(p, *(_line(f"msg_{i}_{j}") for j in range(6)))
    total = sum(p.stat().st_size for p in paths)
    _scan(registry)
    snap = usage_store.snapshot(None, now=NOW)
    assert snap.behind is True
    read = sum(c["offset"] for c in usage_store._cursors.values())
    assert 0 < read <= 1500
    for _ in range(20):
        _scan(registry)
    assert sum(c["offset"] for c in usage_store._cursors.values()) == total
    snap = usage_store.snapshot(None, now=NOW)
    assert snap.behind is False
    assert snap.today == (180, 90, 1800, 360)


def test_a_line_longer_than_the_limit_is_skipped_and_reading_resumes(world, monkeypatch):
    registry, session = world
    monkeypatch.setattr(usage_store, "CHUNK_BYTES", 300)
    monkeypatch.setattr(usage_store, "MAX_LINE_BYTES", 1000)
    _sess, path = session("frontend")
    giant = json.loads(_line("msg_giant"))
    giant["message"]["content"] = [{"type": "text", "text": "x" * 3000}]
    _append(path, json.dumps(giant) + "\n", _line("msg_after"))
    for _ in range(10):
        _scan(registry)
    assert _today() == (10, 5, 100, 20)   # msg_after only


def test_a_line_longer_than_a_chunk_but_within_the_limit_counts(world, monkeypatch):
    registry, session = world
    monkeypatch.setattr(usage_store, "CHUNK_BYTES", 300)
    _sess, path = session("frontend")
    big = json.loads(_line("msg_big"))
    big["message"]["content"] = [{"type": "text", "text": "x" * 3000}]
    _append(path, json.dumps(big) + "\n", _line("msg_after"))
    _scan(registry)
    _scan(registry)
    assert _today() == (20, 10, 200, 40)


def test_without_force_a_scan_waits_its_interval(world):
    registry, session = world
    _sess, path = session("frontend")
    usage_store.scan(registry, now=NOW)
    _append(path, _line("msg_1"))
    usage_store.scan(registry, now=NOW)
    assert _today() == (0, 0, 0, 0)
    usage_store.scan(registry, now=NOW, force=True)
    assert _today() == (10, 5, 100, 20)


def test_models_past_the_cap_go_to_other(world, monkeypatch):
    registry, session = world
    monkeypatch.setattr(usage_store, "MAX_MODELS", 2)
    _sess, path = session("frontend")
    _append(path, *(_line(f"m{i}", model=f"model-{i}") for i in range(4)))
    _scan(registry)
    models = dict(usage_store.snapshot(None, now=NOW).models)
    assert set(models) == {"model-0", "model-1", "other"}
    assert models["other"] == (20, 10, 200, 40)


# ---- days ----------------------------------------------------------------------------

def test_days_follow_the_entry_timestamp_in_local_time(world, monkeypatch):
    """Two entries a minute before and after LOCAL midnight land on two
    days, in a zone three hours east of UTC."""
    registry, session = world
    monkeypatch.setattr(usage_store, "localtime", lambda t: time.gmtime(t + 3 * 3600))
    midnight_local = (int(NOW + 3 * 3600) // 86400) * 86400 - 3 * 3600
    _sess, path = session("frontend")
    _append(path, _line("before", ts=midnight_local - 60, inp=1),
            _line("after", ts=midnight_local + 60, inp=2))
    _scan(registry, now=midnight_local + 120)
    days = usage_store.snapshot(None, now=midnight_local + 120).days
    assert days[0][1][0] == 2 and days[1][1][0] == 1
    # And not the UTC split: in UTC both minutes are the same day.
    assert time.gmtime(midnight_local - 60).tm_mday == time.gmtime(midnight_local + 60).tm_mday


def test_entries_older_than_seven_days_or_far_in_the_future_are_ignored(world):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("old", ts=NOW - 8 * 86400), _line("future", ts=NOW + 3 * 86400),
            _line("week", ts=NOW - 6 * 86400, inp=1, out=0, cr=0, cw=0))
    _scan(registry)
    snap = usage_store.snapshot(None, now=NOW)
    assert snap.week == (1, 0, 0, 0)
    assert len(snap.days) == 7 and snap.days[6][1] == (1, 0, 0, 0)


def test_old_days_are_pruned_with_their_ids(world):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1", ts=NOW - 6 * 86400))
    _scan(registry)
    assert usage_store._seen and usage_store._days
    usage_store.scan(registry, now=NOW + 3 * 86400, force=True)
    assert usage_store._days == {} and usage_store._seen == {} and usage_store._seen_day == {}


def test_the_week_is_today_and_the_six_days_before(world):
    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("t", ts=NOW, inp=1, out=0, cr=0, cw=0),
            _line("y", ts=NOW - 86400, inp=10, out=0, cr=0, cw=0),
            _line("d7", ts=NOW - 7 * 86400, inp=100, out=0, cr=0, cw=0))
    _scan(registry)
    snap = usage_store.snapshot(None, now=NOW)
    assert snap.today == (1, 0, 0, 0)
    assert snap.week == (11, 0, 0, 0)     # the 8th day back is kept, not shown
    assert [c[0] for _d, c in snap.days] == [1, 10, 0, 0, 0, 0, 0]


# ---- scopes --------------------------------------------------------------------------

def test_each_scope_keeps_its_own_tokens(world):
    registry, session = world
    _a, pa = session("frontend", scope=GROUP_A)
    _b, pb = session("backend", scope=GROUP_B)
    _append(pa, _line("a1", inp=1))
    _append(pb, _line("b1", inp=2))
    _scan(registry)
    assert _today(frozenset({GROUP_A}))[0] == 1
    assert _today(frozenset({GROUP_B}))[0] == 2
    assert _today(None)[0] == 3
    assert _today(frozenset())[0] == 0


# ---- the tick ------------------------------------------------------------------------

def test_the_monitor_tick_scans_and_saves(world, monkeypatch):
    from aipager import config
    from aipager.session_monitor import SessionMonitor

    registry, session = world
    _sess, path = session("frontend")
    _append(path, _line("msg_1"))

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", AsyncMock(return_value=[]))
    monitor = SessionMonitor(registry, _noop)
    asyncio.run(monitor.tick())
    assert _today(now=time.time())[0] == 10
    assert config.USAGE_STATE_FILE.is_file()


# ---- the file -----------------------------------------------------------------------

def test_bad_cursors_and_days_in_the_file_are_dropped(world):
    from aipager import config

    config.USAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.fromtimestamp(NOW, dt.timezone.utc).date().isoformat()
    config.USAGE_STATE_FILE.write_text(json.dumps({
        "version": 1, "written_at": NOW, "plan": [],
        "days": {day: {str(GROUP_A): {"m": [1, 2, 3, 4], "bad": [1, 2, 3]},
                       "x": {"m": [1, 1, 1, 1]}},
                 "2026-13": {str(GROUP_A): {"m": [1, 1, 1, 1]}}},
        "seen": {day: {"0123456789abcdef": 2, "nothex": 1}},
        "cursors": {"relative.jsonl": {}, "/abs/x.jsonl": {
            "ino": 1, "dev": 1, "offset": -5, "skip": False, "scope": 0, "session": "s",
            "grown_at": NOW, "read_at": NOW}}}))
    assert usage_store.load()
    assert usage_store.snapshot(None, now=NOW).today == (1, 2, 3, 4)
    assert usage_store._cursors == {}
    assert set(usage_store._seen[day]) == {"0123456789abcdef"}
