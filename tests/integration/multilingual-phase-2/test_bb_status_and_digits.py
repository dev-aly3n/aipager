"""Black-box: /status in a Persian, Russian and English chat (entrypoints.md
"/status", "Format rules"): the list reads in the chat's language,
counts and clocks get Persian digits, session names keep Latin digits and
sit in isolates, callback data never changes with the language, and
English is today's text.

Also: the Persian catalog writes no Latin digit in its own prose (only
inside code such as an environment variable).
"""

from __future__ import annotations

import re
import time
from unittest.mock import MagicMock

import pytest

from aipager import preferences
from aipager.state import Status
from tests.i18n_ml2_bb import (
    BIDI_CONTROLS,
    CYRILLIC,
    DM_A,
    FSI,
    PDI,
    PERSIAN,
    RLM,
    add_session,
    catalog,
    make_bot,
    message,
)


def _status(mk_bot, run_async, code):
    bot = make_bot(mk_bot)
    preferences.set_ui_language(DM_A, code)
    s = add_session(bot, chat=DM_A, label="x12", status=Status.BUSY)
    s.busy_started_at = time.monotonic() - 75
    add_session(bot, chat=DM_A, label="y3", status=Status.IDLE)
    update = message("/status")
    run_async(bot._handle_status(update, MagicMock()))
    call = update.message.reply_text.await_args
    assert call is not None, "/status sent no reply"
    text = call.args[0] if call.args else call.kwargs["text"]
    markup = call.kwargs.get("reply_markup")
    data = [b.callback_data for row in markup.inline_keyboard for b in row
            if b.callback_data]
    return text, [b.text for row in markup.inline_keyboard for b in row], data


def test_the_persian_count_is_in_persian_digits(mk_bot, run_async):
    text, _b, _d = _status(mk_bot, run_async, "fa")
    assert "(۲)" in text.split("\n", 1)[0]


def test_the_persian_working_clock_is_in_persian_digits(mk_bot, run_async):
    text, _b, _d = _status(mk_bot, run_async, "fa")
    assert "۱:۱۵" in text


def test_persian_session_names_stay_latin_and_isolated(mk_bot, run_async):
    text, _b, _d = _status(mk_bot, run_async, "fa")
    assert FSI + "x12" + PDI in text and FSI + "y3" + PDI in text


def test_every_persian_status_line_starts_right_to_left(mk_bot, run_async):
    text, _b, _d = _status(mk_bot, run_async, "fa")
    lines = [ln for ln in text.split("\n") if ln]
    assert all(ln.startswith(RLM) for ln in lines)


def test_persian_status_has_no_latin_digit_outside_names(mk_bot, run_async):
    text, _b, _d = _status(mk_bot, run_async, "fa")
    assert not re.search("[0-9]", text.replace("x12", "").replace("y3", ""))


def test_persian_status_buttons_keep_session_names_latin(mk_bot, run_async):
    _t, buttons, _d = _status(mk_bot, run_async, "fa")
    assert any(b.endswith("x12") for b in buttons)


def test_persian_status_buttons_are_persian(mk_bot, run_async):
    _t, buttons, _d = _status(mk_bot, run_async, "fa")
    assert any(PERSIAN.search(b) for b in buttons)


def test_russian_status_is_russian(mk_bot, run_async):
    text, _b, _d = _status(mk_bot, run_async, "ru")
    assert CYRILLIC.search(text) and not PERSIAN.search(text)


def test_russian_status_has_no_marks(mk_bot, run_async):
    text, buttons, _d = _status(mk_bot, run_async, "ru")
    assert not BIDI_CONTROLS.search(text + "".join(buttons))


def test_russian_status_keeps_latin_clock(mk_bot, run_async):
    text, _b, _d = _status(mk_bot, run_async, "ru")
    assert "1:15" in text


def test_english_status_is_todays_text(mk_bot, run_async):
    text, _b, _d = _status(mk_bot, run_async, "")
    assert text == ("\U0001f4ca <b>Sessions (2)</b>\n\n<b>x12</b> · ⚙️ working 1:15"
                    "\n\n<b>y3</b> · \U0001f4a4 idle")


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_callback_data_does_not_depend_on_language(mk_bot, run_async, code):
    _t, _b, english = _status(mk_bot, run_async, "")
    _t, _b, other = _status(mk_bot, run_async, code)
    strip = re.compile(r"\d{6,}")   # a per-turn stamp in the stop button
    assert [strip.sub("", d) for d in other] == [strip.sub("", d) for d in english]


def test_persian_prose_writes_no_latin_digits():
    # Code is not prose, as tools/i18n.py check_digits has it: a value a
    # person types (an address, a port) stays as it is typed, inside <code>.
    code_token = re.compile(r"\b[A-Z][A-Z0-9_]*=\d+\b|\{[a-z_0-9]+\}|U\+[0-9A-F.]+"
                            r"|<code>[^<]*</code>")
    bad = []
    for key, row in catalog("fa").items():
        values = row["v"].values() if isinstance(row["v"], dict) else [row["v"]]
        if any(re.search("[0-9]", code_token.sub("", v)) for v in values):
            bad.append(key)
    assert bad == []


# ---- the pinned bar ------------------------------------------------------------------

def _pinned(mk_bot, monkeypatch, code, statuses):
    from aipager.bot import dashboard
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    bot = make_bot(mk_bot)
    preferences.set_ui_language(DM_A, code)
    sessions = [add_session(bot, chat=DM_A, label=label, status=st)
                for label, st in statuses]
    text, markup = bot._render_pinned(DM_A, sessions)
    rows = [b for row in markup.inline_keyboard for b in row]
    return text, rows


ONE_WORKING = [("y3", Status.BUSY)]
MIXED = [("x12", Status.INTERACTIVE), ("y3", Status.BUSY), ("z4", Status.IDLE)]


def _glyphs(text):
    from tests.i18n_ml2_bb import strip_marks
    return [strip_marks(ln)[:1] for ln in text.split("\n")]


@pytest.mark.parametrize("code", ["fa", "ru"])
@pytest.mark.parametrize("statuses", [ONE_WORKING, MIXED], ids=["one", "mixed"])
def test_the_pinned_glyphs_do_not_depend_on_language(mk_bot, monkeypatch, code, statuses):
    english, _r = _pinned(mk_bot, monkeypatch, "", statuses)
    other, _r = _pinned(mk_bot, monkeypatch, code, statuses)
    assert _glyphs(other) == _glyphs(english)


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_pinned_answer_button_keeps_its_callback(mk_bot, monkeypatch, code):
    _t, english = _pinned(mk_bot, monkeypatch, "", MIXED)
    _t, other = _pinned(mk_bot, monkeypatch, code, MIXED)
    assert [b.callback_data for b in other] == [b.callback_data for b in english]


def test_the_persian_answer_button_isolates_the_name(mk_bot, monkeypatch):
    _t, rows = _pinned(mk_bot, monkeypatch, "fa", MIXED)
    assert any(FSI + "x12" + PDI in b.text for b in rows)


def test_the_persian_pinned_bar_reads_in_persian(mk_bot, monkeypatch):
    text, _r = _pinned(mk_bot, monkeypatch, "fa", MIXED)
    assert all(PERSIAN.search(ln) for ln in text.split("\n"))


def test_the_russian_pinned_bar_has_no_marks(mk_bot, monkeypatch):
    text, rows = _pinned(mk_bot, monkeypatch, "ru", MIXED)
    assert not BIDI_CONTROLS.search(text + "".join(b.text for b in rows))


def test_the_english_pinned_bar_is_todays_text(mk_bot, monkeypatch):
    text, _r = _pinned(mk_bot, monkeypatch, "", MIXED)
    assert text == ("⏳ x12 needs you\n• <b>y3</b> (working)\n"
                    "• <b>z4</b> (idle)")
