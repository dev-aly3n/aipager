"""design.md success criteria "Card": ``build_stream_card_ex`` draws the note
rows directly above the divider on a non-final card, the status line last,
never on the final card, and always within 8,800 chars / 32,768 bytes."""

from __future__ import annotations

import time

import pytest

from aipager import i18n, usage_store
from aipager.bot import usage_note
from aipager.bot.animation import build_stream_card_ex
from aipager.bot.divider import DIVIDER_SEP
from aipager.statusline_file import Window
from tests.footer_text import status_md

from ._kit import DAY, card_sess, strip_marks

EN = i18n.get("en")
CHAR_CEILING = 8800
BYTE_CEILING = 32768
WEEKLY = "Weekly limit almost reached: 92% used"


@pytest.fixture(autouse=True)
def _utc_clock(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


def _ahead():
    # Middle of an hour of a days-form reset, so "Resets in 2 d 5 h" is stable.
    return int(time.time()) + 2 * DAY + 5 * 3600 + 1800


def _windows(pct=92):
    return {"seven_day": Window(pct, _ahead())}


def _card(sess, *, final=False, waiting=False, tr=EN, rtl=None):
    card, _ = build_stream_card_ex(sess, "Working", final=final, waiting=waiting, tr=tr,
                                   rtl=rtl)
    return card


def _note_sess(windows=None, **kw):
    sess = card_sess(**kw)
    sess.card_usage_windows = _windows() if windows is None else windows
    return sess


# ---- placement --------------------------------------------------------------------------

def test_busy_card_shows_note():
    assert WEEKLY in _card(_note_sess())


def test_note_sits_directly_above_the_divider():
    body = _card(_note_sess()).rpartition(DIVIDER_SEP)[0]
    assert WEEKLY in strip_marks(body.splitlines()[-1])


def test_status_line_stays_last():
    _body, _sep, status = _card(_note_sess()).rpartition(DIVIDER_SEP)
    assert "limit" not in status


def test_note_row_text_equals_card_rows():
    sess = _note_sess()
    (row,) = usage_note.card_rows(EN, sess.card_usage_windows, now=time.time())
    body = _card(sess).rpartition(DIVIDER_SEP)[0]
    assert strip_marks(body.splitlines()[-1]) == strip_marks(row)


def test_timeline_stays_above_the_note():
    body = _card(_note_sess()).rpartition(DIVIDER_SEP)[0]
    assert body.index("Bash: ls") < body.index(WEEKLY)


def test_card_without_timeline_still_shows_note():
    assert WEEKLY in _card(_note_sess(rows=()))


def test_three_rows_in_window_order_above_divider():
    t = _ahead()
    sess = _note_sess({"spend_limit": Window(250, t), "five_hour": Window(95, t),
                       "seven_day": Window(100, t)})
    body = strip_marks(_card(sess).rpartition(DIVIDER_SEP)[0])
    lines = [line for line in body.splitlines() if line.strip()]
    last3 = [line.split(":")[0] for line in lines[-3:]]
    assert last3 == ["⚠️ 5-hour limit almost reached", "⛔ Weekly limit reached",
                     "⛔ Spend limit reached"]


# ---- when it is not drawn ---------------------------------------------------------------

def test_final_card_has_no_note():
    assert "limit" not in _card(_note_sess(), final=True)


def test_waiting_card_shows_note():
    assert WEEKLY in _card(_note_sess(), waiting=True)


def test_card_note_drops_after_reset():
    sess = _note_sess({"seven_day": Window(92, int(time.time()) - 1)})
    assert "limit" not in _card(sess)


def test_card_below_threshold_has_no_note():
    assert "limit" not in _card(_note_sess(_windows(pct=89.4)))


def test_card_with_no_windows_has_no_note():
    sess = card_sess()
    sess.card_usage_windows = None
    assert "limit" not in _card(sess)


def test_card_with_empty_windows_is_the_plain_card():
    plain = card_sess()
    plain.card_usage_windows = None
    empty = card_sess()
    empty.card_usage_windows = {}
    assert _card(empty) == _card(plain)


# ---- language and direction -------------------------------------------------------------

def test_persian_card_carries_the_persian_row():
    fa = i18n.get("fa")
    sess = _note_sess()
    (row,) = usage_note.card_rows(fa, sess.card_usage_windows, now=time.time())
    assert strip_marks(row) in strip_marks(_card(sess, tr=fa, rtl=True))


def test_persian_card_has_no_english_row():
    assert "Weekly" not in _card(_note_sess(), tr=i18n.get("fa"), rtl=True)


# ---- the ceiling ------------------------------------------------------------------------

def _huge(windows):
    rows = []
    for i in range(400):
        rows.append((f"🔑 Bash: echo {i} → Allowed " + "🙂" * 40, True))
        rows.append((f"🤖 Explore · {i} tool calls · 1:05", True))
    sess = card_sess(rows=rows)
    sess.stream_commentary = [(i * 2, "پیامی طولانی از Claude 😀 " * 30) for i in range(120)]
    sess.card_usage_windows = windows
    return sess


def _three():
    t = _ahead()
    return {"five_hour": Window(95, t), "seven_day": Window(100, t),
            "spend_limit": Window(250, t)}


@pytest.mark.parametrize("code", ["en", "fa"])
def test_note_reserved_within_char_ceiling(code):
    tr = i18n.get(code)
    assert len(_card(_huge(_three()), tr=tr, rtl=code == "fa")) <= CHAR_CEILING


@pytest.mark.parametrize("code", ["en", "fa"])
def test_note_reserved_within_byte_ceiling(code):
    tr = i18n.get(code)
    card = _card(_huge(_three()), tr=tr, rtl=code == "fa")
    assert len(card.encode("utf-8")) <= BYTE_CEILING


@pytest.mark.parametrize("code", ["en", "fa"])
def test_all_three_rows_survive_the_fit(code):
    tr = i18n.get(code)
    sess = _huge(_three())
    rows = usage_note.card_rows(tr, sess.card_usage_windows, now=time.time())
    card = strip_marks(_card(sess, tr=tr, rtl=code == "fa"))
    assert all(strip_marks(r) in card for r in rows)


@pytest.mark.parametrize("code", ["en", "fa"])
def test_status_line_survives_the_fit(code):
    tr = i18n.get(code)
    card = _card(_huge(_three()), tr=tr, rtl=code == "fa")
    assert status_md(card).lstrip("\u200f\u200e").startswith("⏳")


def test_huge_card_note_still_directly_above_divider():
    body = strip_marks(_card(_huge(_three())).rpartition(DIVIDER_SEP)[0])
    assert body.splitlines()[-1].startswith("⛔ Spend limit reached")
