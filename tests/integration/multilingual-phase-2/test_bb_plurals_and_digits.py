"""Black-box: Russian plural forms and Persian digits on the turn path
(entrypoints.md "Format rules"; design.md success criterion "With a chat
set to fa ... Persian digits on numbers only ... With ru, Cyrillic and
correct plural forms").

Boundary values for CLDR Russian: 1 and 21 (one), 2 and 22 (few), 5, 11,
12 and 25 (many). The expected word forms are read from the shipped
``ru.jsonl``, so these tests check that the right form is PICKED, not how
it is worded.
"""

from __future__ import annotations

import re
import time

import pytest

from aipager import i18n
from aipager.bot import agents_line, animation
from aipager.state import SessionRegistry, Status
from tests.i18n_ml2_bb import (
    BIDI_CONTROLS,
    FA_DIGITS,
    LATIN_DIGITS,
    PDI,
    catalog,
    code_segments,
    strip_marks,
)

FORM_OF = {1: "one", 21: "one", 2: "few", 22: "few",
           5: "many", 11: "many", 12: "many", 25: "many"}


def _form(key, n):
    pattern = catalog("ru")[key]["v"][FORM_OF[n]]
    return pattern.replace("{n}", str(n))


def _has_exact(text, phrase):
    return re.search(re.escape(phrase) + r"(?!\w)", text) is not None


@pytest.mark.parametrize("n", sorted(FORM_OF))
def test_russian_running_agents_pick_the_cldr_form(n):
    out = agents_line.running_line([f"a{i}" for i in range(n)], tr=i18n.get("ru"))
    assert _has_exact(out, _form("agents.count_agents", n))


@pytest.mark.parametrize("n", sorted(FORM_OF))
def test_russian_running_shells_pick_the_cldr_form(n):
    out = agents_line.running_line([f"s{i}" for i in range(n)], kinds=["shell"] * n,
                                   tr=i18n.get("ru"))
    assert _has_exact(out, _form("agents.count_shells", n))


@pytest.mark.parametrize("n", [2, 5, 11, 21, 22])
def test_russian_done_line_picks_the_cldr_form(n):
    out = agents_line.done_line([f"a{i}" for i in range(n)], 65, tr=i18n.get("ru"))
    assert _has_exact(out, _form("agents.count_agents", n))


@pytest.mark.parametrize("n", [1, 2, 5, 21])
def test_russian_agents_line_has_no_marks(n):
    out = agents_line.running_line([f"a{i}" for i in range(n)], tr=i18n.get("ru"))
    assert not BIDI_CONTROLS.search(out)


def test_russian_done_line_keeps_latin_clock():
    out = agents_line.done_line(["a1"], 65, tr=i18n.get("ru"))
    assert "1:05" in out


# ---- Persian digits on numbers only ---------------------------------------------

def test_persian_running_count_uses_persian_digits():
    out = agents_line.running_line([f"a{i}" for i in range(12)], tr=i18n.get("fa"))
    assert "\u06f1\u06f2" in out


def test_persian_running_line_has_no_latin_count():
    out = agents_line.running_line(["alpha", "beta"], tr=i18n.get("fa"))
    assert not LATIN_DIGITS.search(out)


def test_persian_agent_name_keeps_latin_digits():
    out = agents_line.running_line(["agent7"], tr=i18n.get("fa"))
    assert "agent7" in out


def test_persian_done_clock_is_in_persian_digits():
    out = agents_line.done_line(["a1"], 65, tr=i18n.get("fa"))
    assert "\u06f1:\u06f0\u06f5" in out


def test_persian_done_clock_long_boundary():
    out = agents_line.done_line(["a1"], 3600 + 59, tr=i18n.get("fa"))
    assert not re.search(r"[0-9]:[0-9][0-9]", strip_marks(out).replace("a1", ""))


def test_english_agents_line_is_marked_free():
    out = agents_line.running_line(["a1", "a2"], tr=i18n.get("en"))
    assert not BIDI_CONTROLS.search(out)


def test_english_agents_line_default_language_is_english():
    assert agents_line.running_line(["a1"]) == agents_line.running_line(
        ["a1"], tr=i18n.get("en"))


# ---- the busy card ---------------------------------------------------------------

@pytest.fixture
def busy():
    reg = SessionRegistry()
    s = reg.get_or_create("claude-x12__d4242")
    s.label = "x12"
    s.scope_chat_id, s.scope_kind = 4242, "dm"
    s.status = Status.BUSY
    s.busy_started_at = time.monotonic() - 754
    s.tool_history = [("Read: /tmp/a1.py", True), ("Bash: ls 123", True),
                      ("Bash: echo 4", True)]
    return s


def _status_line(card):
    return card.rstrip("\n").rsplit("\n", 1)[-1]


def test_persian_card_clock_in_persian_digits(busy):
    line = _status_line(animation.build_stream_card(busy, "Working", tr=i18n.get("fa")))
    assert "\u06f1\u06f2:\u06f3" in line


def test_persian_card_tally_count_in_persian_digits(busy):
    line = _status_line(animation.build_stream_card(busy, "Working", tr=i18n.get("fa")))
    assert "Bash \u00d7\u06f2" in line


def test_persian_card_session_name_latin_and_isolated(busy):
    line = _status_line(animation.build_stream_card(busy, "Working", tr=i18n.get("fa")))
    assert "\u2068x12" + PDI in line


def test_persian_card_tool_name_untranslated(busy):
    line = _status_line(animation.build_stream_card(busy, "Working", tr=i18n.get("fa")))
    assert "Read \u00d7" in line


def test_persian_card_code_spans_hold_no_marks(busy):
    card = animation.build_stream_card(busy, "Working", tr=i18n.get("fa"))
    assert not any(BIDI_CONTROLS.search(seg) for seg in code_segments(card))


def test_persian_card_code_spans_keep_latin_digits(busy):
    card = animation.build_stream_card(busy, "Working", tr=i18n.get("fa"))
    assert "`Bash: ls 123`" in card


def test_persian_card_code_spans_hold_no_persian_digits(busy):
    card = animation.build_stream_card(busy, "Working", tr=i18n.get("fa"))
    assert not any(FA_DIGITS.search(seg) for seg in code_segments(card))


def test_persian_card_status_line_starts_with_rlm(busy):
    line = _status_line(animation.build_stream_card(busy, "Working", tr=i18n.get("fa")))
    assert line.startswith("\u200f")


def test_russian_card_has_no_marks(busy):
    assert not BIDI_CONTROLS.search(
        animation.build_stream_card(busy, "Working", tr=i18n.get("ru")))


def test_russian_card_has_latin_clock(busy):
    line = _status_line(animation.build_stream_card(busy, "Working", tr=i18n.get("ru")))
    assert "12:34" in line


def test_english_card_default_equals_explicit_english(busy):
    assert animation.build_stream_card(busy, "Working") == animation.build_stream_card(
        busy, "Working", tr=i18n.get("en"))


def test_english_card_status_line_is_todays_text(busy):
    line = _status_line(animation.build_stream_card(busy, "Working", tr=i18n.get("en")))
    assert line == "\u23f3 **x12** \u00b7 Working \u00b7 12:34 \u00b7 Read \u00d71 \u00b7 Bash \u00d72"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_no_em_dash_on_the_card(busy, code):
    for verb, kw in (("Working", {}), ("Done", {"final": True}), ("Waiting", {"waiting": True})):
        assert "\u2014" not in animation.build_stream_card(busy, verb, tr=i18n.get(code), **kw)


def test_persian_card_verb_is_persian(busy):
    from tests.i18n_ml2_bb import PERSIAN
    line = _status_line(animation.build_stream_card(busy, "Done", final=True,
                                                    tr=i18n.get("fa")))
    assert PERSIAN.search(line)
