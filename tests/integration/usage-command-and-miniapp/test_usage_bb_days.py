"""Days are the daemon's local days (design.md Success criterion 11; spec
requirement 2; entrypoints.md test seam ``usage_store.localtime``).

Boundary-value analysis on local midnight (23:59 vs 00:01 local, in a zone
that is not UTC, so a UTC bucketing would put both on one day), the week
edge (today minus 6 vs minus 7 days) and the future edge (within a day vs
more). Equivalence partitions over zones east and west of UTC. Error
guessing: an entry stamp with no timestamp, a malformed one."""

from __future__ import annotations

import time

import pytest

from aipager import usage_store
from aipager.bot import usage_cmd
from aipager.state import SessionRegistry

SCOPE = -100777


@pytest.fixture
def rig(h, tmp_path):
    reg = SessionRegistry()
    path = tmp_path / "conv-a.jsonl"
    path.write_text("")
    h.add_session(reg, "claude-frontend", scope=SCOPE, transcript=path)
    return reg, path


def _days(h, now):
    return [h.counts(d)[0] for d in usage_store.snapshot(None, now=now).days]


@pytest.mark.parametrize("hours", [3, -5, 5.5, 0], ids=["utc+3", "utc-5", "utc+5:30", "utc"])
def test_23_59_and_00_01_local_land_on_two_days(h, rig, zone, hours):
    reg, path = rig
    off = zone(hours)
    now = time.time()
    mid = h.local_midnight(now, off)
    h.append(path, h.entry("msg_late", mid - 60, 10, 0), h.entry("msg_early", mid + 60, 1, 0))
    h.scan(reg, now=max(now, mid + 61))
    assert _days(h, max(now, mid + 61))[:2] == [1, 10]


@pytest.mark.parametrize("hours", [3, -5], ids=["utc+3", "utc-5"])
def test_today_starts_at_local_midnight(h, rig, zone, hours):
    reg, path = rig
    off = zone(hours)
    now = time.time()
    mid = h.local_midnight(now, off)
    h.append(path, h.entry("msg_late", mid - 60, 10, 0), h.entry("msg_early", mid + 60, 1, 0))
    h.scan(reg, now=max(now, mid + 61))
    assert h.counts(usage_store.snapshot(None, now=max(now, mid + 61)).today)[0] == 1


def test_a_utc_day_is_not_the_local_day(h, rig, zone):
    """UTC+3: 22:30Z is 01:30 local, so it belongs to the next local day."""
    reg, path = rig
    off = zone(3)
    now = time.time()
    mid = h.local_midnight(now, off)
    stamp = mid + 90 * 60        # 01:30 local == 22:30Z the previous UTC day
    h.append(path, h.entry("msg_1", stamp, 5, 0))
    h.scan(reg, now=max(now, stamp + 1))
    assert h.counts(usage_store.snapshot(None, now=max(now, stamp + 1)).today)[0] == 5


def test_the_days_list_has_seven_entries(h, rig):
    reg, _path = rig
    h.scan(reg)
    assert len(usage_store.snapshot(None, now=time.time()).days) == 7


def test_the_days_list_is_newest_first(h, rig, zone):
    reg, path = rig
    off = zone(0)
    now = time.time()
    mid = h.local_midnight(now, off)
    h.append(path, *[h.entry(f"msg_{k}", mid - k * 86400 + 3600, 10 ** k, 0)
                     for k in range(7)])
    h.scan(reg, now=now)
    assert _days(h, now) == [10 ** k for k in range(7)]


def test_six_days_ago_is_in_the_week(h, rig, zone):
    reg, path = rig
    off = zone(0)
    now = time.time()
    mid = h.local_midnight(now, off)
    h.append(path, h.entry("msg_1", mid - 6 * 86400 + 60, 5, 0))
    h.scan(reg, now=now)
    assert h.counts(usage_store.snapshot(None, now=now).week)[0] == 5


def test_seven_days_ago_is_not_in_the_week(h, rig, zone):
    reg, path = rig
    off = zone(0)
    now = time.time()
    mid = h.local_midnight(now, off)
    h.append(path, h.entry("msg_1", mid - 7 * 86400 + 60, 5, 0),
             h.entry("msg_2", now - 10, 1, 0))
    h.scan(reg, now=now)
    assert h.counts(usage_store.snapshot(None, now=now).week)[0] == 1


def test_an_entry_older_than_seven_days_is_ignored_and_the_next_counts(h, rig, zone):
    reg, path = rig
    zone(0)
    now = time.time()
    h.append(path, h.entry("msg_old", now - 20 * 86400, 500, 0),
             h.entry("msg_new", now - 10, 1, 0))
    h.scan(reg, now=now)
    assert h.counts(usage_store.snapshot(None, now=now).week)[0] == 1


def test_an_entry_more_than_a_day_ahead_is_never_counted(h, rig, zone):
    reg, path = rig
    zone(0)
    now = time.time()
    ahead = now + 2 * 86400 + 3600
    h.append(path, h.entry("msg_future", ahead, 500, 0))
    h.scan(reg, now=now)
    assert h.counts(usage_store.snapshot(None, now=ahead).today)[0] == 0


def test_an_entry_a_few_hours_ahead_counts(h, rig, zone):
    """Clock skew of a few hours is within the one-day tolerance."""
    reg, path = rig
    zone(0)
    now = time.time()
    ahead = now + 3 * 3600
    h.append(path, h.entry("msg_soon", ahead, 5, 0))
    h.scan(reg, now=now)
    assert h.counts(usage_store.snapshot(None, now=ahead).week)[0] == 5


def test_a_day_rolls_over_as_time_passes(h, rig, zone):
    """Counted today, then viewed after local midnight: it is yesterday."""
    reg, path = rig
    off = zone(0)
    now = time.time()
    h.append(path, h.entry("msg_1", now - 5, 5, 0))
    h.scan(reg, now=now)
    later = h.local_midnight(now, off) + 86400 + 60
    assert _days(h, later)[:2] == [0, 5]


def test_a_week_rolls_off_as_time_passes(h, rig, zone):
    reg, path = rig
    off = zone(0)
    now = time.time()
    h.append(path, h.entry("msg_1", now - 5, 5, 0))
    h.scan(reg, now=now)
    later = h.local_midnight(now, off) + 7 * 86400 + 60
    assert h.counts(usage_store.snapshot(None, now=later).week)[0] == 0


@pytest.mark.parametrize("stamp", [None, "", "yesterday", 12345, "2026-13-45T99:99:99Z"],
                         ids=["none", "empty", "words", "number", "impossible"])
def test_a_bad_timestamp_never_breaks_the_scan(h, rig, stamp):
    reg, path = rig
    e = h.entry("msg_bad", time.time(), 500, 0)
    if stamp is None:
        del e["timestamp"]
    else:
        e["timestamp"] = stamp
    h.append(path, e, h.entry("msg_ok", time.time() - 5, 1, 0))
    h.scan(reg)
    assert h.counts(usage_store.snapshot(None, now=time.time()).week)[0] in (1, 501)


# ---- the day rows the reader sees --------------------------------------------------------

def _day_labels(h, now):
    view = usage_cmd.usage_view(usage_store.snapshot(None, now=now),
                                usage_cmd.UsageAccess(None, "all", True), h.en(), now)
    return [d["label"] for d in view["tokens"]["days"]]


def test_day_rows_are_labelled_today_yesterday_then_days_ago(h, rig):
    reg, path = rig
    h.append(path, h.entry("msg_1", time.time() - 5, 5, 0))
    h.scan(reg)
    assert _day_labels(h, time.time()) == ["Today", "Yesterday", "2 days ago", "3 days ago",
                                           "4 days ago", "5 days ago", "6 days ago"]


def test_the_rows_are_today_then_last_7_days(h, rig):
    reg, path = rig
    h.append(path, h.entry("msg_1", time.time() - 5, 5, 0))
    h.scan(reg)
    now = time.time()
    view = usage_cmd.usage_view(usage_store.snapshot(None, now=now),
                                usage_cmd.UsageAccess(None, "all", True), h.en(), now)
    assert [r["label"] for r in view["tokens"]["rows"]] == ["Today", "Last 7 days"]
