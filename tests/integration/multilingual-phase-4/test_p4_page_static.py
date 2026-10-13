"""The served page's static contract (entrypoints.md "Page contract") and
``aipager.miniapp.ui_text``'s English catalog.

Partitions: the page built with and without the self-hosted SDK; text,
aria-label and placeholder keys; Msg and Plural entries; every code the
Python plural table knows (plus an unknown one) at counts around every CLDR
boundary.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aipager.i18n import locales, plurals
from aipager.miniapp import static, ui_text

ROOT = Path(__file__).resolve().parents[3]
PAGE = static.INDEX_HTML
BIDI_RE = re.compile("[\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]")


def _inline_english(page):
    m = re.search(r"var AIPAGER_UI_EN\s*=\s*(\{.*?\});\n", page, re.S)
    assert m, "the page defines no AIPAGER_UI_EN global"
    return json.loads(m.group(1))


def _markup():
    body = PAGE[PAGE.index("<body"):]
    return body[:body.index("<script")]


def _tagged(attr):
    """(key, tag, element text or attribute value) of every element with *attr*."""
    out = []
    for m in re.finditer(r"<([a-zA-Z0-9]+)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", _markup()):
        tag, attrs = m.group(1), m.group(2)
        km = re.search(r"\s" + re.escape(attr) + r"=\"([^\"]*)\"", attrs)
        if not km:
            continue
        if attr == "data-i18n":
            rest = _markup()[m.end():]
            close = rest.index(f"</{tag}>")
            shown = re.sub(r"<[^>]*>", "", rest[:close])
        else:
            target = "aria-label" if attr == "data-i18n-label" else "placeholder"
            vm = re.search(r"\s" + target + r"=\"([^\"]*)\"", attrs)
            shown = vm.group(1) if vm else None
        out.append((km.group(1), tag, shown))
    return out


def _matches_pattern(pattern, shown):
    """The markup's English equals the key's English, with each {param}
    standing for whatever the page fills in."""
    parts = re.split(r"\{[a-z_]+\}", pattern)
    rx = "".join(re.escape(p) + ("(.+?)" if i < len(parts) - 1 else "")
                 for i, p in enumerate(parts))
    return re.fullmatch(rx, shown) is not None


# ---- one source of English ------------------------------------------------------------

def test_the_page_defines_the_english_catalog():
    assert _inline_english(PAGE) == ui_text.english_catalog()


def test_both_sdk_variants_carry_the_same_english():
    other = static.index_html(sdk_from_self=False)
    assert _inline_english(other) == _inline_english(PAGE)


def test_the_english_catalog_holds_every_page_string():
    keys = {m.key for m in ui_text.PAGE_STRINGS}
    assert set(ui_text.english_catalog()) == keys


def test_page_strings_have_no_duplicate_key():
    keys = [m.key for m in ui_text.PAGE_STRINGS]
    assert len(keys) == len(set(keys))


def test_a_plural_has_its_one_and_other_english():
    plural = [v for v in ui_text.english_catalog().values() if isinstance(v, dict)]
    assert plural and all(set(v) == {"one", "other"} for v in plural)


def test_every_page_key_is_in_the_extracted_source():
    src = {json.loads(ln)["k"] for ln in
           (ROOT / "tools/i18n/source.jsonl").read_text(encoding="utf-8").splitlines() if ln}
    assert set(ui_text.english_catalog()) - src == set()


def test_the_inline_catalog_is_ascii_only():
    m = re.search(r"var AIPAGER_UI_EN\s*=\s*(\{.*?\});\n", PAGE, re.S)
    assert all(ord(c) < 128 for c in m.group(1))


def test_the_inline_catalog_cannot_close_the_script():
    m = re.search(r"var AIPAGER_UI_EN\s*=\s*(\{.*?\});\n", PAGE, re.S)
    assert "</" not in m.group(1)


def test_no_english_page_string_holds_an_em_dash():
    assert "\u2014" not in json.dumps(ui_text.english_catalog(), ensure_ascii=False)


def test_no_raw_bidi_control_in_the_page():
    assert not BIDI_RE.search(PAGE)


def test_the_page_starts_english():
    assert re.search(r"<html lang=\"en\"", PAGE)


def test_the_report_try_later_is_the_report_modules_wording():
    from aipager.report import wording
    assert ui_text.REPORT_TRY_LATER == wording.TRY_LATER


@pytest.mark.parametrize("name,english", [("AGO_S", "{n}s ago"), ("AGO_M", "{n}m ago"),
                                          ("AGO_H", "{n}h ago"), ("AGO_D", "{n}d ago")])
def test_the_age_words_are_documented_english(name, english):
    from aipager import i18n
    assert i18n.get("en").text(getattr(ui_text, name), n=7) == english.replace("{n}", "7")


# ---- static markup on keys ---------------------------------------------------------

@pytest.mark.parametrize("attr", ["data-i18n", "data-i18n-label", "data-i18n-placeholder"])
def test_the_page_marks_static_strings_with(attr):
    assert _tagged(attr)


@pytest.mark.parametrize("attr", ["data-i18n", "data-i18n-label", "data-i18n-placeholder"])
def test_every_static_key_exists(attr):
    en = ui_text.english_catalog()
    assert [k for k, _t, _s in _tagged(attr) if k not in en] == []


@pytest.mark.parametrize("attr", ["data-i18n", "data-i18n-label", "data-i18n-placeholder"])
def test_every_static_element_shows_its_keys_english(attr):
    en = ui_text.english_catalog()
    bad = [(k, s) for k, _t, s in _tagged(attr)
           if isinstance(en.get(k), str) and not _matches_pattern(en[k], s or "")]
    assert bad == []


NAME_PLACEHOLDERS = {"frontend", "claude-opus-5", "my-project"}


@pytest.mark.parametrize("attr,key_attr", [("aria-label", "data-i18n-label"),
                                           ("placeholder", "data-i18n-placeholder")])
def test_every_static_aria_label_and_placeholder_is_keyed(attr, key_attr):
    """Every static string element carries its key (entrypoints.md); only
    the name placeholders stay untranslated."""
    bad = []
    for m in re.finditer(r"<([a-zA-Z0-9]+)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", _markup()):
        attrs = m.group(2)
        vm = re.search(r"\s" + attr + r"=\"([^\"]*)\"", attrs)
        if vm and vm.group(1) not in NAME_PLACEHOLDERS and (key_attr + "=") not in attrs:
            bad.append(vm.group(1))
    assert bad == []


def test_names_stay_untranslated_in_the_markup():
    """<title>, the h1 and the example placeholders are names, never keyed."""
    assert re.search(r"<h1>aipager</h1>", _markup())


@pytest.mark.parametrize("ident", ["new-name", "new-model-name", "new-folder-name",
                                   "confirm-rename-input", "daemon-line"])
def test_an_ltr_island_in_the_markup(ident):
    m = re.search(r"<[a-z]+[^>]*\bid=\"" + ident + r"\"[^>]*>", _markup())
    assert m and 'dir="ltr"' in m.group(0)


# ---- the page talks only to its own API ------------------------------------------------

def test_every_literal_fetch_url_starts_with_api():
    urls = re.findall(r"\b(?:fetch|[A-Za-z]+Fetch)\(\s*\"([^\"]*)\"", PAGE)
    assert urls and [u for u in urls if not u.startswith("/api/")] == []


def test_the_catalog_url_is_under_api():
    assert re.search(r"Fetch\(\s*\"/api/i18n/\"", PAGE)


# ---- budgets ---------------------------------------------------------------------------

def test_the_page_stays_under_its_byte_budget():
    from tests.test_miniapp_page_rules import PAGE_BUDGET_BYTES
    assert len(PAGE.encode("utf-8")) <= PAGE_BUDGET_BYTES


def test_the_page_stays_under_its_gzipped_byte_budget():
    from tests.test_miniapp_page_rules import SENT_BUDGET_BYTES
    assert len(gzip.compress(PAGE.encode("utf-8"), 9)) <= SENT_BUDGET_BYTES


# ---- the plural port equals Python --------------------------------------------------------

COUNTS = list(range(0, 132)) + [200, 201, 202, 205, 211, 1000, 1001, 1002, 1005,
                               10000, 1000000, 1000001, 2000000, 2000001, 3000002]


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _slice():
    """The plural port as the page serves it. Its markers are comments,
    which the served page leaves out: cut it from the page as written,
    strip it the way the page is, and find it in the served page."""
    from aipager.miniapp.static._strip import strip_js
    src = static.SOURCE_INDEX_HTML
    start = src.index("// plurals: port of aipager/i18n/plurals.py")
    end = src.index("// end plurals", start)
    served = strip_js(src[start:end])
    assert served.strip() and served in PAGE
    return served


def test_the_page_marks_a_plural_slice():
    assert "pluralCategory" in _slice()


def _codes():
    return sorted(set(locales.LOCALES) | {"qps", "qps-rtl", "xx-unknown"})


def test_the_slice_runs_alone_and_equals_python(node, tmp_path):
    script = tmp_path / "plurals.js"
    codes, counts = _codes(), COUNTS
    script.write_text(_slice() + "\nconst out = {};\n"
                      f"for (const c of {json.dumps(codes)}) {{ out[c] = "
                      f"{json.dumps(counts)}.map(n => pluralCategory(c, n)); }}\n"
                      "process.stdout.write(JSON.stringify(out));\n", encoding="utf-8")
    got = json.loads(subprocess.run([node, str(script)], capture_output=True, text=True,
                                    check=True, timeout=60).stdout)
    want = {c: [plurals.category(c, n) for n in counts] for c in codes}
    assert got == want


@pytest.mark.parametrize("n,cat", [(1, "one"), (2, "few"), (5, "many"), (11, "many"),
                                   (21, "one"), (22, "few"), (25, "many"), (111, "many"),
                                   (0, "many"), (12, "many"), (101, "one")])
def test_russian_categories_by_cldr(node, tmp_path, n, cat):
    script = tmp_path / "ru.js"
    script.write_text(_slice() + f"\nprocess.stdout.write(pluralCategory('ru', {n}));\n",
                      encoding="utf-8")
    out = subprocess.run([node, str(script)], capture_output=True, text=True, check=True,
                         timeout=60).stdout
    assert out == cat
