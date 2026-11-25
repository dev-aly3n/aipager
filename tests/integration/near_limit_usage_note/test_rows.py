"""design.md success criteria "Rows": ``usage_note.card_rows`` is pure, takes
an explicit ``now``, shows one row per present window at or over the
threshold whose reset is still ahead, in five_hour, seven_day, spend_limit
order, each ending in /usage's own reset sentence."""

from __future__ import annotations

import time

import pytest

from aipager import i18n, usage_store
from aipager.bot import usage_cmd, usage_note
from aipager.statusline_file import Window

from ._kit import DAY, NOW

EN = i18n.get("en")
FA = i18n.get("fa")
LATER = int(NOW) + 2 * DAY + 5 * 3600


@pytest.fixture(autouse=True)
def _utc_clock(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


def _rows(windows, *, now=NOW, tr=EN):
    return usage_note.card_rows(tr, windows, now=now)


# ---- equivalence classes of the input dict ----------------------------------------------

def test_none_gives_no_rows():
    assert _rows(None) == []


def test_empty_dict_gives_no_rows():
    assert _rows({}) == []


def test_window_below_threshold_gives_no_row():
    assert _rows({"seven_day": Window(50, LATER)}) == []


def test_near_window_gives_one_row():
    assert len(_rows({"seven_day": Window(92, LATER)})) == 1


# ---- threshold edges --------------------------------------------------------------------

def test_89_4_gives_no_row():
    assert _rows({"seven_day": Window(89.4, LATER)}) == []


def test_89_9_reads_90_percent_used():
    (row,) = _rows({"seven_day": Window(89.9, LATER)})
    assert row.startswith("⚠️ Weekly limit almost reached: 90% used · ")


def test_90_reads_near_wording():
    (row,) = _rows({"seven_day": Window(90, LATER)})
    assert row.startswith("⚠️ Weekly limit almost reached: 90% used · ")


def test_99_4_is_still_near():
    (row,) = _rows({"seven_day": Window(99.4, LATER)})
    assert row.startswith("⚠️ Weekly limit almost reached: 99% used · ")


def test_99_5_reads_reached():
    (row,) = _rows({"seven_day": Window(99.5, LATER)})
    assert row.startswith("⛔ Weekly limit reached: 100% used · ")


def test_100_reads_reached_wording():
    (row,) = _rows({"seven_day": Window(100, LATER)})
    assert row.startswith("⛔ Weekly limit reached: 100% used · ")


def test_spend_limit_over_100_reads_reached():
    (row,) = _rows({"spend_limit": Window(250, LATER)})
    assert row.startswith("⛔ Spend limit reached: 250% used · ")


# ---- every window and step wording (entrypoints.md English rows) -----------------------

@pytest.mark.parametrize("name, pct, prefix", [
    ("five_hour", 92, "⚠️ 5-hour limit almost reached: 92% used · "),
    ("seven_day", 92, "⚠️ Weekly limit almost reached: 92% used · "),
    ("spend_limit", 92, "⚠️ Spend limit almost reached: 92% used · "),
    ("five_hour", 100, "⛔ 5-hour limit reached: 100% used · "),
    ("seven_day", 100, "⛔ Weekly limit reached: 100% used · "),
    ("spend_limit", 100, "⛔ Spend limit reached: 100% used · "),
], ids=["5h-near", "7d-near", "spend-near", "5h-reached", "7d-reached", "spend-reached"])
def test_row_wording_per_window_and_step(name, pct, prefix):
    (row,) = _rows({name: Window(pct, LATER)})
    assert row.startswith(prefix)


# ---- reset edges ------------------------------------------------------------------------

def test_row_hidden_when_resets_at_equals_now():
    assert _rows({"seven_day": Window(95, int(NOW))}) == []


def test_row_hidden_when_resets_at_passed():
    assert _rows({"seven_day": Window(95, int(NOW) - 1)}) == []


def test_row_shown_one_minute_before_reset():
    assert len(_rows({"seven_day": Window(95, int(NOW) + 60)})) == 1


def test_row_shown_one_second_before_reset():
    assert len(_rows({"seven_day": Window(95, int(NOW) + 1)})) == 1


def test_reset_window_dropped_while_live_one_stays():
    rows = _rows({"five_hour": Window(99, int(NOW) - 10), "seven_day": Window(95, LATER)})
    assert [r.split(":")[0] for r in rows] == ["⚠️ Weekly limit almost reached"]


# ---- presence, order and bound ----------------------------------------------------------

def test_missing_window_not_shown():
    rows = _rows({"spend_limit": Window(95, LATER)})
    assert [r.split(":")[0] for r in rows] == ["⚠️ Spend limit almost reached"]


def test_rows_order_and_bound():
    windows = {"spend_limit": Window(95, LATER), "seven_day": Window(95, LATER),
               "five_hour": Window(95, LATER)}
    assert [r.split(":")[0] for r in _rows(windows)] == [
        "⚠️ 5-hour limit almost reached", "⚠️ Weekly limit almost reached",
        "⚠️ Spend limit almost reached"]


def test_unknown_window_name_is_ignored():
    """Error guessing: a future window kind never makes a fourth row."""
    windows = {"five_hour": Window(95, LATER), "seven_day": Window(95, LATER),
               "spend_limit": Window(95, LATER), "seven_day_opus": Window(99, LATER)}
    assert len(_rows(windows)) == 3


def test_card_rows_is_pure_with_respect_to_its_input():
    windows = {"seven_day": Window(95, LATER)}
    _rows(windows)
    assert windows == {"seven_day": Window(95, LATER)}


# ---- the reset sentence is /usage's own --------------------------------------------------

@pytest.mark.parametrize("code", ["en", "fa"])
@pytest.mark.parametrize("ahead", [2 * DAY + 5 * 3600, 3 * 3600 + 10 * 60, 25 * 60],
                         ids=["days", "hours", "minutes"])
def test_reset_text_is_usage_reset_text(code, ahead):
    tr = i18n.get(code)
    window = Window(95, int(NOW) + ahead)
    (row,) = _rows({"seven_day": window}, tr=tr)
    assert row.endswith(" · " + usage_cmd.reset_text(window, NOW, tr, "md"))


def test_reset_text_days_form_english():
    window = Window(95, int(NOW) + 2 * DAY + 5 * 3600)
    assert usage_cmd.reset_text(window, NOW, EN, "text") == "Resets in 2 d 5 h"


def test_reset_text_minutes_form_carries_the_clock():
    window = Window(95, int(NOW) + 25 * 60)
    at = time.strftime("%H:%M", time.gmtime(NOW + 25 * 60))
    assert usage_cmd.reset_text(window, NOW, EN, "text") == f"Resets in 25 min, at {at}"


def test_reset_text_hours_form_carries_the_clock():
    window = Window(95, int(NOW) + 3 * 3600 + 10 * 60)
    at = time.strftime("%H:%M", time.gmtime(NOW + 3 * 3600 + 10 * 60))
    assert usage_cmd.reset_text(window, NOW, EN, "text") == f"Resets in 3 h 10 min, at {at}"


def test_persian_row_uses_persian_digits():
    (row,) = _rows({"seven_day": Window(92, LATER)}, tr=FA)
    assert "۹۲" in row


def test_persian_row_is_translated():
    (row,) = _rows({"seven_day": Window(92, LATER)}, tr=FA)
    assert "Weekly" not in row


@pytest.mark.parametrize("code", ["ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR",
                                  "ru", "tr", "uk", "vi", "zh-Hans"])
def test_every_language_has_its_own_near_row(code):
    english = _rows({"seven_day": Window(92, LATER)})
    assert _rows({"seven_day": Window(92, LATER)}, tr=i18n.get(code)) != english


@pytest.mark.parametrize("code", ["ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR",
                                  "ru", "tr", "uk", "vi", "zh-Hans", "en"])
def test_no_em_dash_in_any_row(code):
    tr = i18n.get(code)
    windows = {"five_hour": Window(100, LATER), "seven_day": Window(92, LATER),
               "spend_limit": Window(250, LATER)}
    assert "\u2014" not in "".join(_rows(windows, tr=tr))
