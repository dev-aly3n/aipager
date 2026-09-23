"""§4.6 / row V: the ``aipager status`` line per chat, WITH and WITHOUT
each optional segment — the warning regime, the ban count, minimal mode,
the mute — and the reader against a hostile or stale state file.

Files are produced the way the daemon produces them (a real limiter,
``flood_state.save_if_dirty(force=True)``) into the ISOLATED path, or
hand-written there for the hostile rows. ``status`` runs in another
process with its own wall clock: its ``time`` is set to "file time + N".
"""

from __future__ import annotations

import json
import math
import re
import types
from pathlib import Path

import pytest

import aipager.bot.rich_message as rm
from aipager import config, status
from aipager.bot import flood_state
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = 256113222
OTHER = 111222333
DAY = 86400.0
HOUR = 3600.0


@pytest.fixture
def clk(flood_clock, monkeypatch):
    monkeypatch.setattr(flood_state, "time", types.SimpleNamespace(
        monotonic=lambda: flood_clock.now, time=lambda: flood_clock.wall))
    return flood_clock


async def _ok():
    return "sent"


def daemon(clk, run_async, *, calls=10, chat=CHAT, rows=None):
    lim = BudgetRateLimiter(clock=clk, sleep=clk.sleep,
                            wall_clock=clk.wall_clock,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(lim)
    if rows:
        lim.restore(rows)

    async def _go():
        for _ in range(calls):
            await lim.process_request(
                callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": chat}, rate_limit_args=None)
    run_async(_go())
    return lim


def write(lim):
    assert flood_state.save_if_dirty(force=True)


def read(monkeypatch, clk, later=5.0):
    monkeypatch.setattr(status, "time", types.SimpleNamespace(
        time=lambda: clk.wall + later))
    return status.read_flood_chats()


def line_for(rows, chat=CHAT):
    (row,) = [r for r in rows if r.get("chat_id") == chat]
    (line,) = status.flood_chat_lines([row])
    return line


# ── a clean chat: only the always-on segments ────────────────────────────────

def test_clean_chat_shows_calls_budget_and_age(clk, run_async, monkeypatch):
    write(daemon(clk, run_async, calls=10))
    line = line_for(read(monkeypatch, clk, later=5.0))
    assert "10/1200 calls in the last hour (as of 5s ago)" in line, line


def test_clean_chat_shows_the_full_ceiling(clk, run_async, monkeypatch):
    write(daemon(clk, run_async))
    assert "(ceiling 1.00/s)" in line_for(read(monkeypatch, clk))


def test_clean_chat_has_no_warning_segment(clk, run_async, monkeypatch):
    write(daemon(clk, run_async))
    assert "warning regime" not in line_for(read(monkeypatch, clk))


def test_clean_chat_has_no_ban_segment(clk, run_async, monkeypatch):
    write(daemon(clk, run_async))
    assert "ban(s)" not in line_for(read(monkeypatch, clk))


def test_clean_chat_has_no_minimal_segment(clk, run_async, monkeypatch):
    write(daemon(clk, run_async))
    assert "MINIMAL MODE" not in line_for(read(monkeypatch, clk))


def test_clean_chat_has_no_mute_segment(clk, run_async, monkeypatch):
    write(daemon(clk, run_async))
    assert "flood-muted" not in line_for(read(monkeypatch, clk))


def test_clean_chat_line_starts_with_the_chat(clk, run_async, monkeypatch):
    write(daemon(clk, run_async))
    assert line_for(read(monkeypatch, clk)).lstrip().startswith(
        f"Telegram chat {CHAT}: rate ")


def test_clean_chat_row_reports_no_warning(clk, run_async, monkeypatch):
    write(daemon(clk, run_async))
    (row,) = read(monkeypatch, clk)
    assert row["warning_remaining"] == 0.0


# ── the warning regime segment ───────────────────────────────────────────────

def test_warned_chat_shows_the_time_left(clk, run_async, monkeypatch):
    lim = daemon(clk, run_async)
    lim.note_retry_after(CHAT, 5)
    write(lim)
    line = line_for(read(monkeypatch, clk, later=60.0))
    assert "warning regime, 5h 59m left" in line, line


def test_warned_chat_shows_the_warned_ceiling(clk, run_async, monkeypatch):
    lim = daemon(clk, run_async)
    lim.note_retry_after(CHAT, 5)
    write(lim)
    assert "(ceiling 0.50/s)" in line_for(read(monkeypatch, clk))


def test_a_regime_that_expired_since_the_write_is_not_shown(
    clk, run_async, monkeypatch,
):
    lim = daemon(clk, run_async)
    lim.note_retry_after(CHAT, 5)
    write(lim)
    line = line_for(read(monkeypatch, clk,
                         later=config.FLOOD_WARNING_HOURS * HOUR + 1.0))
    assert "warning regime" not in line, line


def test_a_regime_that_expired_since_the_write_restores_the_ceiling(
    clk, run_async, monkeypatch,
):
    lim = daemon(clk, run_async)
    lim.note_retry_after(CHAT, 5)
    write(lim)
    (row,) = read(monkeypatch, clk,
                  later=config.FLOOD_WARNING_HOURS * HOUR + 1.0)
    assert row["ceiling"] == pytest.approx(config.TELEGRAM_PRIVATE_MAX_RATE)


# ── the ban segment ──────────────────────────────────────────────────────────

def _banned(clk, run_async, ages_days):
    w = clk.wall
    return daemon(clk, run_async,
                  rows=[{"chat_id": CHAT,
                         "ban_stamps": [w - a * DAY for a in ages_days]}])


def test_two_bans_this_week_are_shown(clk, run_async, monkeypatch):
    write(_banned(clk, run_async, [1, 3]))
    assert "(2 ban(s) in the last 7 days)" in line_for(read(monkeypatch, clk))


def test_two_bans_third_the_budget_in_the_line(clk, run_async, monkeypatch):
    write(_banned(clk, run_async, [1, 3]))
    assert "/400 calls in the last hour" in line_for(read(monkeypatch, clk))


def test_two_bans_third_the_ceiling_in_the_line(clk, run_async, monkeypatch):
    write(_banned(clk, run_async, [1, 3]))
    assert "(ceiling 0.33/s)" in line_for(read(monkeypatch, clk))


def test_a_ban_eight_days_old_is_not_shown(clk, run_async, monkeypatch):
    write(_banned(clk, run_async, [8]))
    assert "ban(s)" not in line_for(read(monkeypatch, clk))


def test_a_ban_eight_days_old_leaves_the_full_budget(clk, run_async,
                                                     monkeypatch):
    write(_banned(clk, run_async, [8]))
    (row,) = read(monkeypatch, clk)
    assert row["hourly_budget"] == config.FLOOD_HOURLY_MAX


def test_a_ban_that_aged_out_since_the_write_is_not_counted(
    clk, run_async, monkeypatch,
):
    """status counts the week against ITS clock, not the file's."""
    write(daemon(clk, run_async, rows=[{
        "chat_id": CHAT, "ban_stamps": [clk.wall - 7 * DAY + 30.0]}]))
    (row,) = read(monkeypatch, clk, later=60.0)
    assert row["bans_7d"] == 0


# ── minimal mode and the mute ────────────────────────────────────────────────

def test_hourly_minimal_mode_is_shown(clk, run_async, monkeypatch):
    """The ornament share spent: minimal mode through the HOUR is still
    minimal mode, and status says so."""
    write(daemon(clk, run_async, calls=1080))
    assert "MINIMAL MODE, card updates paused" in line_for(
        read(monkeypatch, clk))


def test_a_muted_chat_shows_the_mute(clk, run_async, monkeypatch):
    lim = daemon(clk, run_async)
    MUTE.mute(CHAT, 3 * HOUR)
    flood_state.mark_dirty()
    write(lim)
    assert "flood-muted until" in line_for(read(monkeypatch, clk))


# ── several chats ────────────────────────────────────────────────────────────

def test_two_chats_two_rows(clk, run_async, monkeypatch):
    lim = daemon(clk, run_async, calls=3)

    async def _other():
        for _ in range(4):
            await lim.process_request(
                callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": OTHER}, rate_limit_args=None)
    run_async(_other())
    write(lim)
    rows = read(monkeypatch, clk)
    assert {r["chat_id"]: r["hourly_used"] for r in rows} == {CHAT: 3, OTHER: 4}


def test_status_json_rows_are_strict_json(clk, run_async, monkeypatch):
    lim = daemon(clk, run_async)
    lim.note_retry_after(CHAT, 5)
    write(lim)
    json.dumps(read(monkeypatch, clk), allow_nan=False)


# ── a hostile or stale file ──────────────────────────────────────────────────

def _hand_write(chat_row, written_at):
    path = Path(config.FLOOD_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "written_at": written_at,
                                "chats": [chat_row]}))


NOW = 1_900_000_000.0


@pytest.fixture
def status_now(monkeypatch):
    monkeypatch.setattr(status, "time", types.SimpleNamespace(time=lambda: NOW))
    return NOW


@pytest.mark.parametrize("field,value", [
    ("hourly", "junk"),
    ("hourly", [[None, None, None]]),
    ("hourly", [["x", "y", "z"]]),
    ("warned_until", "soon"),
    ("warned_until", [1]),
    ("ban_stamps", "many"),
    ("ban_stamps", [None, "x", {}]),
    ("hourly_minimal", "yes"),
    ("typing_shed", 7),
])
def test_status_reads_a_hostile_field_without_raising(status_now, field, value):
    _hand_write({"chat_id": CHAT, "rate": 0.5, field: value}, NOW - 10.0)
    rows = status.read_flood_chats()
    status.flood_chat_lines(rows)


@pytest.mark.parametrize("key", ["hourly_used", "hourly_budget",
                                 "warning_remaining", "ceiling"])
def test_status_values_stay_finite_on_a_hostile_file(status_now, key):
    _hand_write({"chat_id": CHAT, "rate": 0.5,
                 "hourly": [[NOW - 5.0, float("inf"), 3]],
                 "warned_until": float("inf"),
                 "ban_stamps": [float("nan")]}, NOW - 10.0)
    rows = status.read_flood_chats()
    assert rows and all(math.isfinite(float(r[key])) for r in rows)


def test_status_does_not_show_a_warning_longer_than_one_regime(status_now):
    """A file written before a clock jump claims a regime years long:
    status must not report more than one regime left."""
    _hand_write({"chat_id": CHAT, "rate": 0.5,
                 "warned_until": NOW + 3e7}, NOW - 10.0)
    (row,) = status.read_flood_chats()
    assert row["warning_remaining"] <= config.FLOOD_WARNING_HOURS * HOUR + 1.0


def test_status_never_reports_negative_calls(status_now):
    _hand_write({"chat_id": CHAT, "rate": 0.5,
                 "hourly": [[NOW - 5.0, -50, -50]]}, NOW - 10.0)
    (row,) = status.read_flood_chats()
    assert row["hourly_used"] >= 0


def test_status_labels_a_stale_file_with_its_age(status_now):
    _hand_write({"chat_id": CHAT, "rate": 0.5,
                 "hourly": [[NOW - 100.0, 1, 1]]}, NOW - 125.0)
    (row,) = status.read_flood_chats()
    (line,) = status.flood_chat_lines([row])
    assert re.search(r"as of 125s ago|as of 2m ?5s ago|as of 2m ago", line), line


def test_status_hour_ignores_buckets_older_than_an_hour(status_now):
    _hand_write({"chat_id": CHAT, "rate": 0.5,
                 "hourly": [[NOW - 3700.0, 9, 9], [NOW - 3500.0, 1, 2]]},
                NOW - 10.0)
    (row,) = status.read_flood_chats()
    assert row["hourly_used"] == 3
