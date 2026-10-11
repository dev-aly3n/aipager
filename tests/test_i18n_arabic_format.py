"""Arabic's formatter details (multilingual design 5.6 and 5.7): Arabic-Indic
digits (U+0660-U+0669) on numbers only, CLDR's decimal and thousands
separators, its percent sign (U+066A with the Arabic letter mark), the
list separator, all six plural categories, and the same right-to-left
marks and isolates as Persian. Persian is unchanged by any of it."""

from __future__ import annotations

import json

import pytest

from aipager import i18n
from aipager.i18n import core, locales, plurals
from aipager.i18n.core import Command, Plural, Span
from tests import i18n_tool

tool = i18n_tool.load()

RLM, FSI, PDI, ALM = "\u200f", "\u2068", "\u2069", "\u061c"
AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


@pytest.fixture
def catalogs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LOCALES_DIR", tmp_path)
    core.reset_caches()

    def write(code, entries):
        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        (tmp_path / f"{code}.jsonl").write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8")
        core.reset_caches()
    yield write
    core.reset_caches()


def test_the_locale_carries_cldr_arabic_symbols():
    ar = locales.LOCALES["ar"]
    assert (ar.rtl, ar.digits, ar.decimal, ar.group) == (True, "arab", "٫", "٬")
    assert ar.percent == "٪" + ALM
    fa = locales.LOCALES["fa"]
    assert (fa.digits, fa.decimal, fa.group, fa.percent) == (
        "arabext", "٫", "٬", "٪")


def test_numbers_get_arabic_indic_digits_and_separators():
    ar = i18n.get("ar")
    assert ar.num(1234567890) == "١٢٣٤٥٦٧٨٩٠"
    assert ar.num(0.25) == "٠٫٢٥"
    assert ar.num(3.0) == "٣"
    assert ar.num("$1,234.50") == "$١٬٢٣٤٫٥٠"
    assert ar.num("45%") == "٤٥٪" + ALM
    assert ar.clock(250) == "٤:١٠"
    assert ar.clock(5010) == "١:٢٣:٣٠"
    assert ar.text("a.b", "{n}", n=2026) == RLM + "٢٠٢٦"
    assert ar.join(["a", "b", "c"]) == "a، b، c"


def test_persian_is_unchanged():
    fa = i18n.get("fa")
    assert fa.num("$1,234.50") == "$۱٬۲۳۴٫۵۰"
    assert fa.num("45%") == "۴۵٪"
    assert fa.clock(250) == "۴:۱۰"
    assert fa.join(["a", "b"]) == "a، b"
    assert not any(c in fa.num("1.5%") for c in AR_DIGITS + ALM)


def test_strings_names_versions_paths_and_code_keep_latin_digits():
    ar = i18n.get("ar")
    pattern = "{n} {name} {ver} {path} <code>{c}</code> {cmd} {span} {flag}"
    out = ar.html("a.b", pattern, n=12, name="x12", ver="v1.0.1", path="/tmp/a1",
                  c=34, cmd=Command("/x1"), span=Span("`r2`"), flag=True)
    assert out.startswith(RLM + "١٢ ")
    for value in ("x12", "v1.0.1", "/tmp/a1", "/x1", "`r2`", "True"):
        assert FSI + value + PDI in out, value
    assert "<code>34</code>" in out
    assert not any(d in out for d in FA_DIGITS)


def test_rtl_marks_and_isolates_are_persians():
    """The same pattern renders with the same invisible marks in Arabic and
    Persian; only the digits differ."""
    ar, fa = i18n.get("ar"), i18n.get("fa")
    table = str.maketrans(AR_DIGITS, FA_DIGITS)
    patterns = [
        ("a.b", "# {title}\n- {item} {n}\n> {who}", dict(title="T", item="i", n=3, who="@b")),
        ("a.c", "<b>{s}</b> <code>{c}</code> {cmd}", dict(s="x1", c="ls -1", cmd=Command("/x1"))),
        ("a.d", "{a} ← {b}", dict(a="opus", b="sonnet")),
    ]
    for key, pattern, params in patterns:
        a = ar.html(key, pattern, **params)
        f = fa.html(key, pattern, **params)
        assert a.translate(table) == f, pattern
        assert a.count(RLM) == pattern.count("\n") + 1


def test_six_plural_forms(catalogs):
    msg = Plural("card.things", one="{n} thing", other="{n} things")
    forms = {"zero": "zero {n}", "one": "one {n}", "two": "two {n}", "few": "few {n}",
             "many": "many {n}", "other": "other {n}"}
    catalogs("ar", [{"k": "card.things", "src": "0", "v": forms}])
    ar = i18n.get("ar")
    want = {0: "zero", 1: "one", 2: "two", 3: "few", 5: "few", 10: "few", 11: "many",
            21: "many", 22: "many", 25: "many", 99: "many", 100: "other", 101: "other",
            102: "other", 103: "few", 111: "many", 1000: "other"}
    for n, cat in want.items():
        assert plurals.category("ar", n) == cat, n
        out = ar.n(msg, n, kind="text")
        assert out == RLM + cat + " " + ar.num(n), n
    assert plurals.categories("ar") == ("zero", "one", "two", "few", "many", "other")


def test_check_nine_wants_arabic_indic_digits():
    assert tool.check_digits("ar", "k", "٣ رسائل") == []
    assert tool.check_digits("ar", "k", "3 رسائل")
    assert tool.check_digits("ar", "k", "۳ رسائل")
    # Identifiers, placeholders and code keep their own digits.
    assert tool.check_digits("ar", "k", "{n} x1 <code>sleep 5</code> AIPAGER_X=0") == []
    assert tool.check_digits("fa", "k", "٣") and tool.check_digits("fa", "k", "3")


def test_an_arabic_plural_needs_all_six_forms():
    src = {"k": "card.x", "one": "{n} x", "other": "{n} xs", "eg": {}, "placeholders": ["n"]}
    five = {c: "{n} س" for c in ("one", "two", "few", "many", "other")}
    problems = tool.check_translation("ar", src, five, glossary=None)
    assert any("zero" in p for p in problems)
    six = dict(five, zero="لا شيء")
    assert tool.check_translation("ar", src, six, glossary=None) == []


def test_the_page_catalog_hands_the_symbols_over(monkeypatch):
    from aipager.miniapp import ui_text
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"ar"}))
    body = ui_text.catalog("ar")
    assert (body["rtl"], body["digits"]) == (True, AR_DIGITS)
    assert (body["decimal"], body["group"], body["percent"]) == (
        "٫", "٬", "٪" + ALM)
    assert body["list"] == "، "
    fa = ui_text.catalog("fa")
    assert (fa["digits"], fa["percent"]) == (FA_DIGITS, "٪")


def test_a_form_may_name_the_count_in_words(catalogs):
    """Check 3 lets a plural form leave {n} out (Arabic says one and two
    in words); rendering such a form must work, the other forms still get
    the count."""
    msg = Plural("card.agents", one="{n} agent", other="{n} agents")
    forms = {"zero": "\u0644\u0627 \u0648\u0643\u0644\u0627\u0621",
             "one": "\u0648\u0643\u064a\u0644 \u0648\u0627\u062d\u062f",
             "two": "\u0648\u0643\u064a\u0644\u0627\u0646",
             "few": "{n} \u0648\u0643\u0644\u0627\u0621",
             "many": "{n} \u0648\u0643\u064a\u0644\u064b\u0627",
             "other": "{n} \u0648\u0643\u064a\u0644"}
    catalogs("ar", [{"k": "card.agents", "src": "0", "v": forms}])
    ar = i18n.get("ar")
    assert ar.n(msg, 1, kind="text") == RLM + forms["one"]
    assert ar.n(msg, 2, kind="text") == RLM + forms["two"]
    assert ar.n(msg, 0, kind="text") == RLM + forms["zero"]
    assert ar.n(msg, 5, kind="text") == RLM + "\u0665 \u0648\u0643\u0644\u0627\u0621"
    # A form that names neither the count nor a given parameter still
    # refuses the parameter it does not use.
    with pytest.raises(ValueError):
        ar.n(msg, 1, kind="text", extra="x")
