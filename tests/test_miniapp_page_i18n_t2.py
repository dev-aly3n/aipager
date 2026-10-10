"""The Mini App page in German, Turkish, Ukrainian and Arabic, driven for
real through ``tests/js/miniapp_i18n.js`` (phase 5, translation run 2):
the main view in the language with no English left, numbers in the
language's digits (Arabic-Indic for Arabic, with its own percent sign),
names and code left to right, and every counted string's form for the
counts each language distinguishes."""

from __future__ import annotations

import re

import pytest

from aipager import i18n
from aipager.i18n import core, plurals
from tests.test_miniapp_page_i18n import (
    FSI,
    PDI,
    RAW_KEY,
    RLM,
    _after_boot,
    _leaks,
    _opts,
    drive,
    node_bin,  # noqa: F401  (the fixture)
)

CODES = ("de", "tr", "uk", "ar")
ALM = "\u061c"
AR_DIGIT = re.compile("[٠-٩]")
FA_DIGIT = re.compile("[۰-۹]")
ARABIC_LETTER = re.compile("[ؠ-ي]")


def _labels(code):
    cat = core._catalog(code)
    return {fid: cat["app.fact." + fid] for fid in
            ("model", "context", "cost", "working", "last_active", "cwd")}


@pytest.mark.parametrize("code", CODES)
def test_the_main_view(code, node_bin, tmp_path):  # noqa: F811
    from aipager.miniapp.static import INDEX_HTML
    from tests.test_i18n_render_t2 import english_left

    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts(code, factLabels=_labels(code)))
    assert (run["lang"], run["dir"]) == (code, "rtl" if code == "ar" else "ltr")
    assert _leaks(run, code) == []
    texts = [t for _n, t in _after_boot(run)]
    assert not [t for t in texts if RAW_KEY.search(t)]
    assert not [t for t in texts if FA_DIGIT.search(t)]
    cat = core._catalog(code)
    screens = {s["name"]: s["texts"] for s in run["screens"]}
    grid = screens["grid"]
    assert cat["ui.tab.sessions"] in grid and cat["ui.tab.settings"] in grid
    # The page's own words, as the grid and the session page show them.
    served = set(run["served"])
    # The server's words as the page shows them (a summary cut at its code).
    served |= {part.strip() for v in run["served"] for part in v.split("`")}
    served |= {re.sub(r"^[^\w]+\s+", "", v) for v in run["served"]}
    served |= {v.split(" - ")[0] for v in run["served"]}
    for name in ("grid", "detail.busy"):
        for text in screens[name]:
            text = re.sub(r"^[@#][a-z-]+=", "", text)
            if text in served or text.startswith("{"):
                continue
            assert english_left(text, code, name) == [], (name, text)
    if code == "ar":
        tr = i18n.get("ar")
        need = cat["ui.pulse.need"][plurals.category("ar", 2)]
        assert need.replace("{n}", tr.num(2)) in grid
        assert tr.num(64) in grid and "64" not in grid
        assert cat["ui.age.seconds"].replace("{n}", tr.num(30)) in grid
        assert "Opus 4.6 · $٠٫٥٠" in grid
        assert "charlie" in grid and any(t.startswith("@aipager_bot · v0.7.17 · ")
                                         for t in grid)
        assert "@aria-label=" + cat["ui.beacon.open_aria"].replace(
            "{session}", FSI + "alpha" + PDI) in grid
        detail = screens["detail.busy"]
        clock = tr.clock(192)
        assert cat["ui.state_line.working_for"].replace("{clock}", FSI + clock + PDI) in detail
        assert run["islands"]["pct_sign"] == "٪" + ALM
        for name in ("tile_label", "tile_meta", "beacon_label", "detail_label", "fact_cwd",
                     "timeline_tool", "new_name", "rename_input"):
            assert run["islands"][name] == "ltr", name
        assert any(ARABIC_LETTER.search(t) for t in texts)
        for name, text in _after_boot(run):
            assert not re.search("[A-Za-z0-9_][" + FSI + PDI + "][A-Za-z0-9_]", text), (
                name, text)
            assert not re.search("(?<![\\w./-])/[" + FSI + PDI + RLM + "]", text), (name, text)
    else:
        assert run["islands"]["pct_sign"] == "%"
        assert not [t for t in texts if any(m in t for m in (RLM, FSI, PDI, ALM))]
        assert not [t for t in texts if AR_DIGIT.search(t)]
        assert "64" in grid


def _form(code: str, key: str, n: int) -> str:
    forms = core._catalog(code)[key]
    return forms[plurals.category(code, n)].replace("{n}", i18n.get(code).num(n))


@pytest.mark.parametrize("code", CODES)
def test_counted_strings(code, node_bin, tmp_path):  # noqa: F811
    """Every counted page string at 1, 2, 3, 5, 11, 21, 22 and 25 (and 100
    in Arabic, its "other"): the catalog's form for that count's category,
    the count in the language's digits. Arabic's zero form is checked
    through the bot (the page shows no "0 need you")."""
    from aipager.miniapp.static import INDEX_HTML

    counts = [1, 2, 3, 5, 11, 21, 22, 25] + ([100] if code == "ar" else [])
    run = drive(node_bin, tmp_path, INDEX_HTML, scenario="plurals",
                opts=_opts(code, counts=counts))
    seen = set()
    for n in counts:
        got = run["plurals"][str(n)]
        seen.add(plurals.category(code, n))
        assert got["need"] == _form(code, "ui.pulse.need", n), n
        assert got["queued"] == _form(code, "ui.activity.queued", n), n
        assert got["timeline"] == _form(code, "ui.timeline.title_n", n), n
        assert got["errors"] == _form(code, "ui.report.errors_n", n + 3), n
        assert got["more"] == _form(code, "ui.report.show_more", n), n
        assert got["tooLong"] == _form(code, "ui.report.too_long", n), n
    want = {"de": {"one", "other"}, "tr": {"one", "other"}, "uk": {"one", "few", "many"},
            "ar": {"one", "two", "few", "many", "other"}}
    assert seen == want[code], seen
