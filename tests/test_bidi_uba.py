"""The pure-Python bidi algorithm the right-to-left sweeps judge lines
with (tests/bidi_uba.py), pinned against cases recorded with ICU 74
(tests/golden/bidi_icu_cases.json), and its iPhone judgement."""

from __future__ import annotations

import json
import os
import random
import re
import unicodedata
from pathlib import Path

import pytest

from tests import bidi_uba

RLM, LRM, FSI, PDI = "\u200f", "\u200e", "\u2068", "\u2069"
RAH = "\u0631\u0627\u0647"
GUIDE = "\u0631\u0627\u0647\u0646\u0645\u0627\u06cc"
FIXTURE = Path(__file__).parent / "golden" / "bidi_icu_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="ascii"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_python_knows_the_recorded_classes(case):
    """The classes the fixture was recorded with are this Python's: a
    future Unicode change shows up here, by character, not as a level."""
    for i, ch in enumerate(case["text"]):
        assert bidi_uba.bidi_class(ch) == case["classes"][i], (i, hex(ord(ch)))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_helper_matches_icu(case):
    text, para = case["text"], case["para_in"]
    if para is None:
        assert bidi_uba.paragraph_level(text) == case["para"]
    assert bidi_uba.levels(text, para) == case["levels"]
    assert bidi_uba.visual_order(text, para) == case["visual"]


def test_the_fixture_covers_what_the_sweep_meets():
    names = {c["name"] for c in CASES}
    for name in ("help_title", "trailing_isolated_param", "trailing_space", "cost", "tally",
                 "clock", "page", "command_end", "bracket_tool_end", "n0_rtl_inside_ltr",
                 "guillemets_not_paired", "nested_isolates", "unmatched_isolate",
                 "fsi_rtl_in_ltr", "nsm_after_pdi", "emoji_start", "emoji_end", "alm_percent",
                 "zwnj", "lre_pdf", "rlo", "neutral_only", "ltr_with_rtl_phrase",
                 "deep_embedding", "deep_isolates", "forced_rtl_latin"):
        assert name in names
    # ICU's own leftmost character: a line ending in a Latin word sits
    # left, the same line ending in U+200F sits right.
    odd = {c["name"]: c["leftmost_odd"] for c in CASES}
    assert odd["rlm_word_latin_end"] is False
    assert odd["rlm_word_latin_end_rlm"] is True


def test_every_bracket_is_a_mirrored_pair():
    closers = set(bidi_uba.BRACKETS.values())
    assert len(closers) == len(bidi_uba.BRACKETS) == 64
    for opening, closing in bidi_uba.BRACKETS.items():
        assert opening not in closers and closing not in bidi_uba.BRACKETS
        for ch in (opening, closing):
            # Unicode 14 added four pairs; Python 3.10 (Unicode 13) does
            # not know them, and gives them L, so they are never brackets.
            if unicodedata.bidirectional(ch):
                assert unicodedata.mirrored(ch) == 1, hex(ord(ch))
                assert unicodedata.bidirectional(ch) == "ON", hex(ord(ch))


def test_brackets_pair_by_the_table():
    # "(abc)" inside a right-to-left line: the pair takes the line's
    # direction (N0 b), so the whole bracket sits on its right side.
    text = RAH + " (abc) " + RAH
    lv = bidi_uba.levels(text)
    assert lv[4] == lv[8] == 1
    # Without N0 the closing bracket would sit between "c" and a space.
    assert bidi_uba.levels("abc (" + RAH + ") def")[4] == 0


@pytest.mark.parametrize("line, right", [
    (RLM + RAH + " aipager", False),
    (RLM + RAH + " aipager" + RLM, True),
    (RLM + GUIDE + " aipager" + RLM, True),
    # Strict: a trailing isolate or space is looked past, so what is at
    # the left edge is the Latin word.
    (RLM + RAH + " " + FSI + "aipager" + PDI, False),
    (RLM + RAH + " " + FSI + "aipager" + PDI + RLM, True),
    (RLM + RAH + " aipager ", False),
    (RLM + RAH + " \u06f1:\u06f0\u06f6", False),
    (RLM + RAH + " \u06f1:\u06f0\u06f6" + RLM, True),
    (RAH, True),
    ("abc", False),
    (LRM + RAH, False),
    # A trailing LRM is a strong mark: at its own (even) level.
    (RLM + RAH + " abc" + LRM, False),
], ids=["latin-end", "latin-end-rlm", "help-title", "isolate-end", "isolate-end-rlm",
        "space-end", "clock-end", "clock-end-rlm", "plain", "latin", "lrm-start", "lrm-end"])
def test_sits_right_on_iphone(line, right):
    assert bidi_uba.sits_right_on_iphone(line) is right


def test_nothing_to_judge_is_not_right():
    assert bidi_uba.leftmost("   ") is None
    assert bidi_uba.sits_right_on_iphone(" " + FSI + PDI) is False


def test_shown_text_drops_markup_and_pre_blocks():
    html = ("<b>" + RAH + "</b> <code>ls</code> &amp; x\n<pre>a\nb</pre>\n"
            "<blockquote>q</blockquote>")
    assert bidi_uba.shown_lines(html) == [RAH + " ls & x", "", "", "q"]
    md = "**" + RAH + "** `Read` \\_x\\_ ~~y~~ ||s||\n```\ncode\n```\nz"
    assert bidi_uba.shown_lines(md) == [RAH + " Read _x_ y s", "", "", "z"]


def test_an_rtl_letter_is_a_letter():
    assert bidi_uba.has_rtl_letter(RAH)
    assert bidi_uba.has_rtl_letter("\u05d0")
    for not_letter in (RLM, "\u061c", "\u06f1\u0661", "\u060c\u061f", "abc"):
        assert not bidi_uba.has_rtl_letter(not_letter), ascii(not_letter)


# ---- optional: the live cross-check (local only) -----------------------------------

#: ICU 74's two departures from UAX #9 (see tests/bidi_uba.py): a
#: nonspacing mark right after a closing bracket, and an unmatched PDI
#: inside an override.
_ICU_QUIRK = re.compile("[)\\]}\u3009\u232a][\u064b\u0301]|[\u202d\u202e].*\u2069", re.S)


def _icu():
    if os.environ.get("AIPAGER_TEST_ICU") != "1":
        pytest.skip("AIPAGER_TEST_ICU=1 runs the live ICU cross-check")
    import ctypes
    try:
        lib = ctypes.CDLL("libicuuc.so.74")
    except OSError:
        pytest.skip("libicuuc.so.74 is not installed")
    set_para = lib.ubidi_setPara_74
    set_para.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint8,
                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    lib.ubidi_open_74.restype = ctypes.c_void_p
    lib.ubidi_close_74.argtypes = [ctypes.c_void_p]
    lib.ubidi_getVisualMap_74.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32),
                                          ctypes.POINTER(ctypes.c_int)]

    def visual(text: str, para) -> list[int]:
        units = text.encode("utf-16-le")
        n = len(units) // 2
        buf = ctypes.create_string_buffer(units + b"\0\0")
        handle = lib.ubidi_open_74()
        err = ctypes.c_int(0)
        set_para(handle, buf, n, 0xFE if para is None else para, None, ctypes.byref(err))
        vmap = (ctypes.c_int32 * max(n, 1))()
        lib.ubidi_getVisualMap_74(handle, vmap, ctypes.byref(err))
        lib.ubidi_close_74(handle)
        index = [i for i, ch in enumerate(text) for _u in range(2 if ord(ch) > 0xFFFF else 1)]
        out: list[int] = []
        for u in vmap[:n]:
            i = index[u]
            if bidi_uba.bidi_class(text[i]) in bidi_uba._REMOVED or (out and out[-1] == i):
                continue
            out.append(i)
        return out

    return visual


def test_the_helper_orders_like_live_icu():
    visual = _icu()
    alphabet = list("ab \t1+-,.:%$#()[]{}/") + [
        "\u0631", "\u0627", "\u05d0", "\u06f1", "\u0661", "\u066c", "\u066b", "\u064b",
        "\u0301", LRM, RLM, "\u2066", "\u2067", FSI, PDI, "\u202a", "\u202b", "\u202c",
        "\u202d", "\u202e", "\u200b", "\u00ab", "\u00bb", "\u2329", "\u3009", "\u061c",
        "\U0001f600", "\u00d7"]
    rng = random.Random(20261014)
    checked = 0
    for _ in range(5000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 18)))
        if _ICU_QUIRK.search(text):
            continue
        para = rng.choice((None, None, 0, 1))
        assert bidi_uba.visual_order(text, para) == visual(text, para), (ascii(text), para)
        checked += 1
    assert checked > 3000
