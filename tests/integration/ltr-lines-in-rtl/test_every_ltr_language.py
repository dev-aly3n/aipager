"""Every left-to-right language's in-RTL rendering keeps every existing rule,
and nothing that must not change does (design.md Part A "Success
criteria": the sweep, the byte-for-byte rules, reserve_bytes;
spec.md "What to build" items 1, 3 and 4).

``main_bytes_0412268.json`` holds what main 0412268c rendered for the
cases in ``_kit.render_cases()`` (each LTR language's base and
``directed(None)`` / ``directed(False)``, fa and ar in both directions, the
busy card in every direction that was not right to left), recorded by
running ``_kit.py`` under main's tree in a separate interpreter with a
throwaway HOME.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from aipager import i18n
from aipager.bot import agents_line, animation, held_message
from aipager.bot.footer import footer_block
from aipager.bot.notify import finished_line
from aipager.i18n import locales


def _load_kit():
    name = "ltr_lines_in_rtl_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _load_kit()
LTR_LANGS = sorted(c for c, loc in locales.LOCALES.items() if not loc.rtl)
RTL_LANGS = ("fa", "ar")
MAIN = json.loads((Path(__file__).parent / "main_bytes_0412268.json").read_text("utf-8"))
A, V = agents_line.AgentRow, agents_line.BlockView
_SPLIT_RUN = re.compile("([*_])[\u200e\u2066-\u2069]\\1")


# ---- byte for byte against main ------------------------------------------------------------

_NOW = None


def _now_renders():
    global _NOW
    if _NOW is None:
        _NOW = kit.render_cases()
    return _NOW


@pytest.mark.parametrize("code", sorted(locales.LOCALES))
def test_what_must_not_change_renders_as_on_main(code):
    now = _now_renders()
    keys = [k for k in MAIN if k.split("/", 1)[0] == code]
    assert keys
    changed = [k for k in keys if now.get(k) != MAIN[k]]
    assert changed == [], changed[:5]


def test_the_main_recording_covers_every_language():
    assert {k.split("/", 1)[0] for k in MAIN} == set(locales.LOCALES)


# ---- the twin's renders for the sweep ------------------------------------------------------

def _pure_renders(tr) -> dict[str, str]:
    out = {f"block/{name}": agents_line.render_block(view, tr=tr)
           for name, view in kit._views().items()}
    out["finished"] = str(finished_line(tr, "\U0001f4ac **x1**", "md", seconds=250,
                                        added=10, removed=5))
    out["late"] = str(held_message.late_marker_text(tr, 180))
    out["fold"] = str(tr.n(animation.FOLD, 5, kind="md"))
    return out


def _card_pair(code: str, label: str, waiting: bool) -> tuple[str, str]:
    """The same turn's card drawn right to left (Claude writes Persian) and
    left to right, in *code*."""
    s = kit.card_session(label, kit.FA_PROSE, waiting_agents=3 if waiting else 0)
    tr = i18n.get(code)
    rtl, _ = animation.build_stream_card_ex(s, "Working", tr=tr, rtl=True, waiting=waiting)
    ltr, _ = animation.build_stream_card_ex(s, "Working", tr=tr, rtl=False, waiting=waiting)
    return rtl, ltr


_CARDS = [(label, waiting) for label in ("x1", kit.FA_NAME) for waiting in (False, True)]
_CARD_IDS = ["ascii-busy", "ascii-waiting", "persian-busy", "persian-waiting"]


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_output_without_its_marks_is_the_base_output(code):
    base, twin = i18n.get(code), i18n.get(code).directed(True)
    b, t = _pure_renders(base), _pure_renders(twin)
    assert {k: kit.strip_marks(v) for k, v in t.items()} == b


@pytest.mark.parametrize("code", LTR_LANGS)
@pytest.mark.parametrize("label,waiting", _CARDS, ids=_CARD_IDS)
def test_twin_card_without_its_marks_is_the_left_to_right_card(code, label, waiting):
    rtl, ltr = _card_pair(code, label, waiting)
    assert kit.strip_marks(rtl) == ltr


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_output_differs_from_base(code):
    base, twin = i18n.get(code), i18n.get(code).directed(True)
    assert _pure_renders(twin)["block/operator"] != _pure_renders(base)["block/operator"]


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_lines_all_start_with_an_lrm(code):
    t = _pure_renders(i18n.get(code).directed(True))
    bad = [ln for text in t.values() for ln in text.split("\n")
           if ln.strip() and not re.sub(r"^(?:- |#{1,6} |> |\d+\. )", "", ln).startswith(kit.LRM)]
    assert bad == []


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_lines_all_end_with_an_lrm(code):
    t = _pure_renders(i18n.get(code).directed(True))
    bad = [ln for text in t.values() for ln in text.split("\n")
           if ln.strip() and not ln.endswith(kit.LRM)]
    assert bad == []


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_lines_all_read_in_order_on_desktop(code):
    t = _pure_renders(i18n.get(code).directed(True))
    bad = [ln for text in t.values() for ln in kit.lines(text) if kit.order_problems(ln, 1)]
    assert bad == []


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_lines_all_read_in_order_on_ios_and_android(code):
    t = _pure_renders(i18n.get(code).directed(True))
    bad = [ln for text in t.values() for ln in kit.lines(text)
           if kit.order_problems(ln, None) or kit.bidi_uba.paragraph_level(ln) != 0]
    assert bad == []


@pytest.mark.parametrize("code", LTR_LANGS)
@pytest.mark.parametrize("label,waiting", _CARDS, ids=_CARD_IDS)
def test_twin_card_aipager_lines_read_in_order_on_desktop(code, label, waiting):
    rtl, _ltr = _card_pair(code, label, waiting)
    own = [ln for ln in kit.lines(rtl) if kit.FA_PROSE not in ln]
    assert own and [ln for ln in own if kit.order_problems(ln, 1)] == []


def _all_twin_text(code: str) -> list[str]:
    t = list(_pure_renders(i18n.get(code).directed(True)).values())
    t += [_card_pair(code, label, waiting)[0] for label, waiting in _CARDS]
    t += [footer_block(x) for x in t[: len(kit._views())]]
    return t


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_never_puts_two_marks_side_by_side_outside_code(code):
    bad = [x for x in _all_twin_text(code) if kit.PAIR.search(kit.outside_code(x))]
    assert bad == []


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_puts_nothing_invisible_inside_code_or_tags(code):
    bad = [b for x in _all_twin_text(code) for b in kit.invisible_in_code_or_tags(x)]
    assert bad == []


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_marks_no_divider_or_blank_line(code):
    bad = [b for x in _all_twin_text(code) for b in kit.marked_divider_or_blank(x)]
    assert bad == []


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_writes_no_rlm(code):
    assert not any(kit.RLM in x for x in _all_twin_text(code))


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_never_splits_an_emphasis_run(code):
    bad = [x for x in _all_twin_text(code) if _SPLIT_RUN.search(x)]
    assert bad == []


@pytest.mark.parametrize("code", LTR_LANGS)
def test_twin_keeps_bold_pairs_whole_on_each_line(code):
    t = _pure_renders(i18n.get(code).directed(True))
    bad = [ln for text in t.values() for ln in text.split("\n")
           if ln.count("**") % 2]
    assert bad == []


# ---- the twin as an object (entrypoints.md "Tr.directed") ----------------------------------

@pytest.mark.parametrize("code", LTR_LANGS)
@pytest.mark.parametrize("rtl", [None, False], ids=["none", "false"])
def test_no_direction_or_left_to_right_returns_the_base(code, rtl):
    tr = i18n.get(code)
    assert tr.directed(rtl) is tr


@pytest.mark.parametrize("code", LTR_LANGS)
def test_right_to_left_for_a_non_rich_send_returns_the_base(code):
    tr = i18n.get(code)
    assert tr.directed(True, rich=False) is tr


@pytest.mark.parametrize("code", LTR_LANGS)
def test_right_to_left_returns_one_cached_twin(code):
    tr = i18n.get(code)
    assert tr.directed(True) is tr.directed(True) and tr.directed(True) is not tr


@pytest.mark.parametrize("code", LTR_LANGS)
def test_the_twin_says_what_it_is(code):
    twin = i18n.get(code).directed(True)
    assert (twin.ltr_in_rtl, twin.rtl, twin.language_rtl, twin.code) == (True, False, False, code)


@pytest.mark.parametrize("code", LTR_LANGS)
def test_the_base_is_not_a_twin(code):
    assert i18n.get(code).ltr_in_rtl is False


@pytest.mark.parametrize("code", LTR_LANGS)
def test_the_twin_returns_to_its_base(code):
    tr = i18n.get(code)
    assert tr.directed(True).directed(None) is tr


@pytest.mark.parametrize("code", RTL_LANGS)
@pytest.mark.parametrize("rich", [True, False])
def test_a_right_to_left_language_keeps_its_base_for_right_to_left(code, rich):
    tr = i18n.get(code)
    assert tr.directed(True, rich=rich) is tr


@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_right_to_left_language_is_never_an_in_rtl_twin(code):
    tr = i18n.get(code)
    assert not tr.ltr_in_rtl and not tr.directed(False).ltr_in_rtl


@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_right_to_left_language_writes_no_lrm_right_to_left(code):
    tr = i18n.get(code).directed(True)
    texts = list(_pure_renders(tr).values())
    assert not any(kit.LRM in x for x in texts)


# ---- line / bare / isolate ------------------------------------------------------------------

def test_line_marks_both_ends_on_the_twin():
    twin = i18n.get("en").directed(True)
    assert twin.line("✅ `SendMessage`", kind="md") == \
        kit.LRM + "✅ `SendMessage`" + kit.LRM


def test_line_keeps_marks_outside_a_code_span_on_the_twin():
    twin = i18n.get("en").directed(True)
    assert kit.invisible_in_code_or_tags(twin.line("`a b`", kind="md")) == []


def test_line_does_not_double_an_existing_lrm_on_the_twin():
    twin = i18n.get("en").directed(True)
    once = twin.line("x1 done", kind="md")
    assert twin.line(once, kind="md") == once


def test_line_on_the_base_is_unchanged():
    en = i18n.get("en")
    assert en.line("✅ `SendMessage`", kind="md") == "✅ `SendMessage`"


def test_bare_strips_the_twins_end_marks():
    twin = i18n.get("en").directed(True)
    assert twin.bare(kit.LRM + "x1 done" + kit.LRM) == "x1 done"


def test_bare_on_the_base_keeps_an_lrm():
    en = i18n.get("en")
    assert en.bare(kit.LRM + "x1" + kit.LRM) == kit.LRM + "x1" + kit.LRM


def test_isolate_wraps_on_the_twin():
    twin = i18n.get("en").directed(True)
    assert twin.isolate(kit.FA_NAME) == kit.FSI + kit.FA_NAME + kit.PDI


@pytest.mark.parametrize("code", RTL_LANGS)
def test_isolate_wraps_on_a_right_to_left_language(code):
    assert i18n.get(code).isolate("x1") == kit.FSI + "x1" + kit.PDI


def test_isolate_on_the_base_is_unchanged():
    assert i18n.get("en").isolate(kit.FA_NAME) == kit.FA_NAME


def test_mark_and_lead_stay_rlm_only_on_the_twin():
    twin, en = i18n.get("en").directed(True), i18n.get("en")
    assert (twin.mark, twin.lead("x")) == (en.mark, en.lead("x"))


# ---- reserve_bytes ----------------------------------------------------------------------------

@pytest.mark.parametrize("code", LTR_LANGS)
def test_the_twin_reserves_more_than_the_base(code):
    tr = i18n.get(code)
    twin = agents_line.reserve_bytes(tr.directed(True))
    assert twin > agents_line.reserve_bytes(tr)


@pytest.mark.parametrize("code", LTR_LANGS)
def test_the_twin_and_base_reserves_are_cached_apart(code):
    tr = i18n.get(code)
    first = agents_line.reserve_bytes(tr.directed(True))
    base = agents_line.reserve_bytes(tr)
    assert agents_line.reserve_bytes(tr.directed(True)) == first != base


@pytest.mark.parametrize("code", LTR_LANGS)
def test_a_worst_case_marked_block_fits_its_reserve(code):
    twin = i18n.get(code).directed(True)
    view = V(agents=tuple(A("x" * 60, False, description="W " * 300, parent=(0 if i % 2 else -1))
                          for i in range(12)),
             shells_running=3, shells_finished=2, shells_finished_seconds=99999,
             shells_stopped=2, shells_stopped_seconds=99999)
    block = footer_block(agents_line.render_block(view, tr=twin))
    assert len(block.encode()) <= agents_line.reserve_bytes(twin)
