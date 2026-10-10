"""The block in fa, ar, ru and zh-Hans (design.md success criterion 2).

Expected word forms are read from the shipped catalogs (read only), so
these check that the right key and CLDR form are PICKED, the marks and
digits are right, and Claude's own words pass through untouched, not how
a translator worded a phrase.
"""

from __future__ import annotations

import re

import pytest

from aipager import i18n
from aipager.bot.agents_line import AgentRow, BlockView, render_block
from tests.i18n_ml2_bb import BIDI_CONTROLS, FSI, PDI, RLM, catalog, strip_marks

SEP = "\n\n"
FA_DIGITS = re.compile("[۰-۹]")
AR_DIGITS = re.compile("[٠-٩]")
LATIN_DIGITS = re.compile("[0-9]")
HAN = re.compile("[一-鿿]")

PROSE = "Writing final.md for the pipeline"
PROSE_NUM = "Checked 42 files"


def _five_states():
    return {
        "working": BlockView(
            agents=(AgentRow("pipeline-runner", False, prose=PROSE),
                    AgentRow("Explore", False, tool="Read", tool_arg="state.py")),
            shells_running=1),
        "one-done": BlockView(
            agents=(AgentRow("Explore", True, 42.0),
                    AgentRow("pipeline-runner", False, prose="Running the full suite")),
            shells_running=1),
        "agents-done-shell-up": BlockView(
            agents=(AgentRow("Explore", True, 42.0),
                    AgentRow("pipeline-runner", True, 300.0)),
            agents_done_seconds=372.0, shells_running=1),
        "all-finished": BlockView(
            agents=(AgentRow("Explore", True, 42.0),
                    AgentRow("pipeline-runner", True, 300.0)),
            agents_done_seconds=372.0, shells_finished=1,
            shells_finished_seconds=7985.0),
        "shell-only": BlockView(shells_running=1),
        "shell-stopped": BlockView(shells_stopped=1, shells_stopped_seconds=65.0),
    }


STATES = sorted(_five_states())
RTL = ["fa", "ar"]
ALL = ["fa", "ar", "ru", "zh-Hans"]


def _render(state: str, code: str) -> str:
    return render_block(_five_states()[state], i18n.get(code))


# ---- every state renders in every language, and differs from English ---------------------

@pytest.mark.parametrize("code", ALL)
@pytest.mark.parametrize("state", STATES)
def test_every_state_is_translated(state, code):
    assert _render(state, code) != render_block(_five_states()[state])


@pytest.mark.parametrize("code", ALL)
@pytest.mark.parametrize("state", STATES)
def test_no_english_frame_words_remain(state, code):
    out = strip_marks(_render(state, code))
    for word in ("working", "running", "finished", "stopped", "Results",
                 " agents"):
        assert word not in out.replace(PROSE, "").replace("Running the full suite", "")


@pytest.mark.parametrize("code", ALL)
@pytest.mark.parametrize("state", STATES)
def test_no_em_dash(state, code):
    assert "\u2014" not in _render(state, code)


# ---- RTL: marks, isolation, digits ------------------------------------------------------

@pytest.mark.parametrize("code", RTL)
@pytest.mark.parametrize("state", STATES)
def test_rtl_every_line_starts_with_rlm(state, code):
    lines = _render(state, code).split(SEP)
    assert all(line.startswith(RLM) for line in lines)


@pytest.mark.parametrize("code", RTL)
def test_rtl_agent_type_is_isolated_whole(code):
    out = _render("working", code)
    assert (f"{FSI}**pipeline-runner**{PDI}" in out
            or f"**{FSI}pipeline-runner{PDI}**" in out)


@pytest.mark.parametrize("code", RTL)
def test_rtl_prose_is_isolated_and_untranslated(code):
    assert f"{FSI}{PROSE}{PDI}" in _render("working", code)


@pytest.mark.parametrize("code", RTL)
def test_rtl_tool_argument_is_isolated(code):
    assert f"{FSI}state.py{PDI}" in _render("working", code)


@pytest.mark.parametrize("code", RTL)
def test_rtl_description_is_isolated(code):
    view = BlockView(agents=(AgentRow("Explore", False, description="Audit it"),))
    assert f"{FSI}Audit it{PDI}" in render_block(view, i18n.get(code))


@pytest.mark.parametrize("code", RTL)
def test_rtl_prose_digits_stay_latin(code):
    view = BlockView(agents=(AgentRow("Explore", False, prose=PROSE_NUM),))
    assert f"{FSI}{PROSE_NUM}{PDI}" in render_block(view, i18n.get(code))


@pytest.mark.parametrize("code", RTL)
def test_rtl_tool_name_is_never_digit_converted_or_translated(code):
    view = BlockView(agents=(AgentRow("Explore", False, tool="mcp2go"),))
    assert "mcp2go" in render_block(view, i18n.get(code))


def test_fa_clock_uses_persian_digits():
    out = _render("all-finished", "fa")
    assert "۶:۱۲" in out and "۲:۱۳:۰۵" in out


def test_ar_clock_uses_arabic_digits():
    out = _render("all-finished", "ar")
    assert "٦:١٢" in out and "٢:١٣:٠٥" in out


def test_fa_done_row_clock_is_persian():
    assert "۰:۴۲" in _render("one-done", "fa")


def _outside_isolates(text: str) -> str:
    return re.sub(f"{FSI}.*?{PDI}", "", text)


@pytest.mark.parametrize("code, latin", [("fa", FA_DIGITS), ("ar", AR_DIGITS)],
                         ids=["fa", "ar"])
@pytest.mark.parametrize("state", STATES)
def test_rtl_no_latin_digit_outside_claudes_words(state, code, latin):
    assert not LATIN_DIGITS.search(_outside_isolates(_render(state, code)))


@pytest.mark.parametrize("code", RTL)
def test_rtl_agent_type_with_digits_is_not_converted(code):
    view = BlockView(agents=(AgentRow("agent-2", False),))
    assert f"{FSI}agent-2{PDI}" in render_block(view, i18n.get(code))


# ---- non-RTL: no marks ------------------------------------------------------------------

@pytest.mark.parametrize("code", ["ru", "zh-Hans"])
@pytest.mark.parametrize("state", STATES)
def test_ltr_has_no_bidi_controls(state, code):
    assert not BIDI_CONTROLS.search(_render(state, code))


@pytest.mark.parametrize("code", ["ru", "zh-Hans"])
def test_ltr_prose_passes_through_verbatim(code):
    assert PROSE in _render("working", code)


def test_zh_frame_words_are_chinese():
    assert HAN.search(_render("working", "zh-Hans").split(SEP)[0])


def test_zh_promise_is_chinese():
    last = _render("working", "zh-Hans").split(SEP)[-1]
    assert HAN.search(last)


def test_zh_agent_type_is_not_translated():
    assert "**pipeline-runner**" in _render("working", "zh-Hans")


# ---- CLDR plural forms (ru, ar) ----------------------------------------------------------

RU_FORM = {1: "one", 21: "one", 2: "few", 22: "few", 5: "many", 11: "many", 25: "many"}
AR_FORM = {1: "one", 2: "two", 3: "few", 10: "few", 11: "many", 99: "many", 100: "other"}


def _pick(code, key, n, **extra):
    forms = catalog(code)[key]["v"]
    text = forms[{"ru": RU_FORM, "ar": AR_FORM}[code][n]]
    tr = i18n.get(code)
    text = text.replace("{n}", tr.num(n))
    for k, v in extra.items():
        text = text.replace("{" + k + "}", v)
    return text


def _working(n):
    return BlockView(agents=tuple(AgentRow(f"a{i}", False) for i in range(n)))


@pytest.mark.parametrize("n", sorted(RU_FORM))
def test_ru_working_header_picks_the_cldr_form(n):
    head = render_block(_working(n), i18n.get("ru")).split(SEP)[0]
    assert head == _pick("ru", "agents.head.working", n)


@pytest.mark.parametrize("n", sorted(RU_FORM))
def test_ru_shells_running_picks_the_cldr_form(n):
    out = render_block(BlockView(shells_running=n), i18n.get("ru"))
    assert out == _pick("ru", "agents.head.shells_running", n)


@pytest.mark.parametrize("n", [5, 6, 9], ids=["more1", "more2", "more5"])
def test_ru_more_line_picks_the_cldr_form(n):
    lines = render_block(_working(n), i18n.get("ru")).split(SEP)
    assert _pick("ru", "agents.more", n - 4) in lines


@pytest.mark.parametrize("n", sorted(AR_FORM))
def test_ar_working_header_picks_the_cldr_form(n):
    head = strip_marks(render_block(_working(n), i18n.get("ar")).split(SEP)[0])
    assert head == strip_marks(_pick("ar", "agents.head.working", n))


@pytest.mark.parametrize("n", sorted(AR_FORM))
def test_ar_shells_running_picks_the_cldr_form(n):
    out = strip_marks(render_block(BlockView(shells_running=n), i18n.get("ar")))
    assert out == strip_marks(_pick("ar", "agents.head.shells_running", n))


def test_ar_catalog_has_every_cldr_form_for_the_working_header():
    forms = catalog("ar")["agents.head.working"]["v"]
    assert set(forms) >= {"zero", "one", "two", "few", "many", "other"}


def test_ru_catalog_has_every_cldr_form_for_the_working_header():
    forms = catalog("ru")["agents.head.working"]["v"]
    assert set(forms) >= {"one", "few", "many", "other"}
