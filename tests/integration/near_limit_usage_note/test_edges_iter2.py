"""Iteration 2 boundary and error-guessing cases: a card whose UTF-8 bytes
bind before its chars (emoji and CJK heavy, rows in zh-Hans / ko / ar),
spend_limit readings above 100, mixed windows where one has reset, and
odd percentages that must never raise or draw a row."""

from __future__ import annotations

import math
import time

import pytest

from aipager import i18n, usage_store
from aipager.bot import usage_note
from aipager.bot.animation import build_stream_card_ex
from aipager.bot.divider import DIVIDER_SEP
from aipager.statusline_file import Window
from tests.footer_text import status_md

from ._kit import DAY, NOW, card_sess, strip_marks

EN = i18n.get("en")
CHAR_CEILING = 8800
BYTE_CEILING = 32768
LATER = int(NOW) + 2 * DAY + 5 * 3600
BYTE_LANGS = ["zh-Hans", "ko", "ar", "en"]


@pytest.fixture(autouse=True)
def _utc_clock(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


def _ahead():
    return int(time.time()) + 2 * DAY + 5 * 3600 + 1800


def _three():
    t = _ahead()
    return {"five_hour": Window(95, t), "seven_day": Window(100, t),
            "spend_limit": Window(250, t)}


def _card(sess, *, code="en", waiting=False):
    card, _ = build_stream_card_ex(sess, "Working", waiting=waiting, tr=i18n.get(code),
                                   rtl=code in ("ar", "fa"))
    return card


def _byte_heavy(windows):
    """A timeline of almost only 4-byte emoji, so the fitted card hits
    32,768 UTF-8 bytes while still under 8,800 chars."""
    rows = []
    for i in range(300):
        rows.append((f"🔑 {i}" + "😀" * 500, True))
        rows.append(("🤖" + "🚀" * 500 + "代码", True))
    sess = card_sess(rows=rows)
    sess.stream_commentary = [(i * 2, "😀🎉🚀" * 200 + "检查")
                              for i in range(120)]
    sess.card_usage_windows = windows
    return sess


def _rows(windows, *, now=NOW, tr=EN):
    return usage_note.card_rows(tr, windows, now=now)


# ---- card byte ceiling where bytes bind first ------------------------------------------

@pytest.mark.parametrize("code", BYTE_LANGS)
def test_byte_bound_card_within_byte_ceiling(code):
    card = _card(_byte_heavy(_three()), code=code)
    assert len(card.encode("utf-8")) <= BYTE_CEILING


@pytest.mark.parametrize("code", BYTE_LANGS)
def test_byte_bound_card_within_char_ceiling(code):
    card = _card(_byte_heavy(_three()), code=code)
    assert len(card) <= CHAR_CEILING


@pytest.mark.parametrize("code", BYTE_LANGS)
def test_byte_bound_card_keeps_all_three_rows(code):
    tr = i18n.get(code)
    sess = _byte_heavy(_three())
    rows = usage_note.card_rows(tr, sess.card_usage_windows, now=time.time())
    card = strip_marks(_card(sess, code=code))
    assert all(strip_marks(r) in card for r in rows)


@pytest.mark.parametrize("code", BYTE_LANGS)
def test_byte_bound_card_keeps_status_last(code):
    card = _card(_byte_heavy(_three()), code=code)
    assert status_md(card).lstrip("\u200f\u200e").startswith("⏳")


@pytest.mark.parametrize("code", BYTE_LANGS)
def test_byte_bound_card_note_directly_above_divider(code):
    tr = i18n.get(code)
    sess = _byte_heavy(_three())
    last_row = usage_note.card_rows(tr, sess.card_usage_windows, now=time.time())[-1]
    body = _card(sess, code=code).rpartition(DIVIDER_SEP)[0]
    assert strip_marks(body.splitlines()[-1]) == strip_marks(last_row)


def test_byte_bound_scenario_really_is_byte_bound():
    """Control: the fitted card is far under the char ceiling, so it was the
    byte ceiling that trimmed it (otherwise the cases above prove nothing new)."""
    card = _card(_byte_heavy(_three()), code="zh-Hans")
    assert len(card) < CHAR_CEILING - 300


@pytest.mark.parametrize("code", BYTE_LANGS)
def test_byte_bound_waiting_card_within_byte_ceiling(code):
    card = _card(_byte_heavy(_three()), code=code, waiting=True)
    assert len(card.encode("utf-8")) <= BYTE_CEILING


def test_zh_hans_card_carries_the_chinese_row():
    zh = i18n.get("zh-Hans")
    sess = card_sess()
    sess.card_usage_windows = {"seven_day": Window(92, _ahead())}
    (row,) = usage_note.card_rows(zh, sess.card_usage_windows, now=time.time())
    assert strip_marks(row) in strip_marks(_card(sess, code="zh-Hans"))


# ---- spend_limit above 100 --------------------------------------------------------------

@pytest.mark.parametrize("pct, shown", [
    (100.4, "100%"), (101, "101%"), (250, "250%"), (999.4, "999%"), (1000, "1000%"),
    (1000.0, "1000%"),
], ids=lambda v: str(v).replace(".", "p").replace("%", ""))
def test_spend_limit_over_100_row_reads_reached(pct, shown):
    (row,) = _rows({"spend_limit": Window(pct, LATER)})
    assert row.startswith(f"⛔ Spend limit reached: {shown} used · ")


@pytest.mark.parametrize("pct", [100.4, 101, 500, 1000, 1500, 1e308],
                         ids=["100p4", "101", "500", "1000", "1500", "1e308"])
def test_limit_level_above_100_is_reached(pct):
    assert usage_store.limit_level(pct) == 2


def test_spend_limit_1000_on_the_card():
    sess = card_sess()
    sess.card_usage_windows = {"spend_limit": Window(1000, _ahead())}
    assert "⛔ Spend limit reached: 1000% used" in _card(sess)


def test_spend_limit_over_100_but_reset_shows_nothing():
    assert _rows({"spend_limit": Window(400, int(NOW) - 1)}) == []


# ---- mixed windows, one has reset -------------------------------------------------------

def test_mixed_reached_window_reset_only_live_one_on_card():
    sess = card_sess()
    sess.card_usage_windows = {"five_hour": Window(100, int(time.time()) - 5),
                               "seven_day": Window(92, _ahead())}
    assert "5-hour" not in _card(sess)


def test_mixed_reached_window_reset_live_one_still_on_card():
    sess = card_sess()
    sess.card_usage_windows = {"five_hour": Window(100, int(time.time()) - 5),
                               "seven_day": Window(92, _ahead())}
    assert "Weekly limit almost reached: 92% used" in _card(sess)


def test_all_windows_reset_card_is_the_plain_card():
    gone = int(time.time()) - 5
    stale = card_sess()
    stale.card_usage_windows = {"five_hour": Window(100, gone), "seven_day": Window(95, gone),
                                "spend_limit": Window(300, gone)}
    plain = card_sess()
    plain.card_usage_windows = None
    assert _card(stale) == _card(plain)


def test_mixed_reset_below_and_near_gives_only_the_near_row():
    rows = _rows({"five_hour": Window(100, int(NOW) - 1), "seven_day": Window(40, LATER),
                  "spend_limit": Window(91, LATER)})
    assert [r.split(":")[0] for r in rows] == ["⚠️ Spend limit almost reached"]


def test_mixed_reset_exactly_now_and_one_second_ahead():
    rows = _rows({"five_hour": Window(95, int(NOW)), "seven_day": Window(95, int(NOW) + 1)})
    assert [r.split(":")[0] for r in rows] == ["⚠️ Weekly limit almost reached"]


def test_middle_window_reset_keeps_order_of_the_others():
    rows = _rows({"spend_limit": Window(100, LATER), "seven_day": Window(99, int(NOW) - 1),
                  "five_hour": Window(90, LATER)})
    assert [r.split(":")[0] for r in rows] == ["⚠️ 5-hour limit almost reached",
                                               "⛔ Spend limit reached"]


# ---- odd percentages never raise and never draw ----------------------------------------

@pytest.mark.parametrize("pct", [math.nan, math.inf, -math.inf, True, -5],
                         ids=["nan", "inf", "neginf", "true", "neg5"])
def test_odd_percentage_gives_no_row(pct):
    assert _rows({"seven_day": Window(pct, LATER)}) == []


@pytest.mark.parametrize("pct, level", [
    (-(10**400), 0), (2**63, 2), (-0.0, 0), (5e-324, 0), (89.50000001, 1), (99.49999, 1),
], ids=["neg-huge-int", "2pow63", "neg-zero", "denormal", "just-over-89p5", "just-under-99p5"])
def test_limit_level_extra_edges(pct, level):
    assert usage_store.limit_level(pct) == level


def test_odd_percentage_card_never_raises():
    sess = card_sess()
    sess.card_usage_windows = {"seven_day": Window(math.nan, _ahead()),
                               "five_hour": Window(math.inf, _ahead())}
    assert "limit" not in _card(sess)
