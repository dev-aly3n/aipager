"""Success criteria 7 and 8 on the counted surfaces: Russian picks the
one/few/many form by the CLDR rule at 1, 2, 5, 11, 12, 21, 22 and 25 (and
1234), and Persian writes counts, minutes and percentages in Persian digits
while names keep theirs.

The Russian category is computed here, independently of aipager: one when
n % 10 == 1 and n % 100 != 11; few when n % 10 in 2..4 and n % 100 not in
12..14; many otherwise. Two counts of the same category must read the same
once the number is taken out; different categories must not."""

from __future__ import annotations

import re

import pytest
import qa_p3a_scenarios as S

BOUNDARY = (1, 2, 5, 11, 12, 21, 22, 25)
REPRESENTATIVE = {"one": 1, "few": 2, "many": 5}


def ru_category(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


@pytest.fixture
def render(scenario_out):
    return scenario_out


def _template(text, n):
    return re.sub(rf"(?<![\w.]){n}(?![\d.])", "{n}", text, count=1)


def clearqueue(render):
    out = render("clearqueue_counts", "ru")
    counted = [n for n in S.COUNTS if n > 0]
    return dict(zip(counted, out[2:]))


def quiet_turn(render):
    out = render("notice_quiet_turn", "ru")
    return dict(zip(S.MINUTES, out[0::2]))


def compact_timeout(render):
    out = render("notice_compact", "ru")
    # 120 s -> 2 min, 300 s -> 5 min, 1260 s -> 21 min (the English shows these).
    return {2: out[7], 5: out[8], 21: out[9], 1: out[1]}


def clear_gone(render):
    out = render("clear_gone_counts", "ru")
    return dict(zip((1, 2, 5, 11, 21), out[:5]))


SURFACES = {"clearqueue": clearqueue, "quiet_turn": quiet_turn}


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("n", BOUNDARY)
def test_russian_form_follows_the_cldr_category(render, surface, n):
    forms = SURFACES[surface](render)
    rep = REPRESENTATIVE[ru_category(n)]
    assert _template(forms[n], n) == _template(forms[rep], rep)


@pytest.mark.parametrize("surface", sorted(SURFACES))
@pytest.mark.parametrize("a,b", [("one", "many"), ("few", "many"), ("one", "few")])
def test_russian_categories_read_differently(render, surface, a, b):
    forms = SURFACES[surface](render)
    ra, rb = REPRESENTATIVE[a], REPRESENTATIVE[b]
    assert _template(forms[ra], ra) != _template(forms[rb], rb)


def test_russian_1234_takes_the_few_form_of_clearqueue(render):
    forms = clearqueue(render)
    assert _template(forms[1234], 1234) == _template(forms[2], 2)


@pytest.mark.parametrize("n", (1, 2, 5, 21))
def test_russian_compaction_timeout_minutes(render, n):
    forms = compact_timeout(render)
    rep = REPRESENTATIVE[ru_category(n)]
    assert _template(forms[n], n) == _template(forms[rep], rep)


@pytest.mark.parametrize("n", (1, 2, 5, 11, 21))
def test_russian_clear_all_pop_up(render, n):
    forms = clear_gone(render)
    rep = REPRESENTATIVE[ru_category(n)]
    assert _template(forms[n], n) == _template(forms[rep], rep)


# ---- Persian digits -------------------------------------------------------------------

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa(n) -> str:
    return str(n).translate(FA_DIGITS)


@pytest.mark.parametrize("n", [n for n in S.COUNTS if n > 0])
def test_persian_clearqueue_count_in_persian_digits(render, n):
    out = render("clearqueue_counts", "fa")
    counted = [c for c in S.COUNTS if c > 0]
    assert _fa(n) in out[2 + counted.index(n)]


@pytest.mark.parametrize("n", [n for n in S.COUNTS if n > 0])
def test_persian_clearqueue_has_no_latin_count(render, n):
    out = render("clearqueue_counts", "fa")
    counted = [c for c in S.COUNTS if c > 0]
    text = out[2 + counted.index(n)].replace("x1", "")
    assert not re.search(r"\d", re.sub("[۰-۹]", "", text))


@pytest.mark.parametrize("pct", (0, 1, 80, 92, 100))
def test_persian_context_percentage_in_persian_digits(render, pct):
    out = render("notice_context", "fa")
    text = out[2 * (0, 1, 80, 92, 100).index(pct)]
    assert _fa(pct) in text and not re.search(rf"(?<!x){pct}", text)


@pytest.mark.parametrize("minutes", S.MINUTES)
def test_persian_quiet_minutes_in_persian_digits(render, minutes):
    out = render("notice_quiet_turn", "fa")
    assert f"({_fa(minutes)} " in out[2 * S.MINUTES.index(minutes)]


def test_persian_compacted_line_points_left_with_persian_percentages(render):
    out = render("notice_compact", "fa")
    assert re.search(f"{_fa(88)}.*←.*{_fa(12)}", out[0])


def test_persian_name_x1_keeps_its_latin_digit_on_the_counted_reply(render):
    out = render("clearqueue_counts", "fa")
    assert "x1" in out[3]


def test_persian_file_name_keeps_its_latin_digit(render):
    out = render("media_files", "fa")
    assert "notes 7.txt" in out[1]


def test_persian_transcript_keeps_its_latin_digits(render):
    """What the voice note said is shown as heard, never digit-converted."""
    out = render("media_files", "fa")
    assert any("fix bug 42 in x1 &amp; &lt;tag&gt;" in t for t in out)


def test_persian_unknown_safety_reason_keeps_its_digits(render):
    out = render("safety_blocks", "fa")
    assert any("something else entirely 42" in t for t in out)
