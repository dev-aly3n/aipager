"""Check 7: length budgets, by key prefix, for every translation and for
the English of every key now (a phone screen, a two-per-row button, a
toast Telegram silently drops past 200 characters)."""

from __future__ import annotations

from tests import i18n_tool

tool = i18n_tool.load()


def test_english_fits():
    rows = tool.load_jsonl(tool.SOURCE)
    assert tool.check_english_budgets(rows) == []


def test_every_key_has_a_known_prefix():
    for row in tool.load_jsonl(tool.SOURCE):
        assert tool.budget_for(row["k"]) is not None, row["k"]


def test_an_unknown_prefix_fails():
    assert tool.check_budget("misc.thing", "x")
    assert tool.budget_for("set.level.title") and not tool.budget_for("set.level.hint")


def test_a_long_label_fails():
    assert tool.check_budget("kb.back", "« Back") == []
    assert tool.check_budget("kb.back", "« Zurück zum Hauptmenü")
    # Wide characters count twice.
    assert tool.cells("简体中文") == 8
    assert tool.check_budget("kb.back", "返回返回返回返回")
    assert tool.check_budget("toast.x", "x" * 200) == []
    assert tool.check_budget("toast.x", "x" * 201)


def test_the_language_is_measured_with_the_longest_name():
    text = "x" * 222 + " {language}"
    assert len(text.replace("{language}", "Bahasa Indonesia")) <= 240
    assert tool.check_budget("lang.changed", text)
    assert tool.longest_endonym("chars") == "Português (Brasil)"


def test_rtl_marks_count_toward_a_toast():
    """A Persian toast gets a mark at the start and at the end of each
    line and an isolate pair around each text value: 196 visible
    characters with one text value are 200 on the wire and fit; 198 are
    202 and do not."""
    src = {"k": "toast.x", "en": "x {name}", "eg": {}}
    fits = "ب" * 189 + " {name}"
    assert len(fits.replace("{name}", "{name}")) == 196
    assert tool.check_budget("toast.x", fits, src=src, code="fa") == []
    long = "ب" * 191 + " {name}"
    assert tool.check_budget("toast.x", long, src=src, code="fa")
    # 197 visible are 201 on the wire: the end mark is counted.
    tight = "ب" * 190 + " {name}"
    assert tool.check_budget("toast.x", tight, src=src, code="fa")
    # The same text is fine where no marks are added, and a count is no
    # text value.
    assert tool.check_budget("toast.x", long, src=src, code="ru") == []
    counted = {"k": "toast.x", "en": "x {n} {m}", "eg": {"m": 3}}
    assert tool.rtl_overhead("fa", "{n} {m}\nx", counted) == 4
    assert tool.rtl_overhead("fa", "{name}", src) == 4
    assert tool.rtl_overhead("fa", "x", None) == 2
    assert tool.rtl_overhead("en", "{name}", src) == 0


def test_a_menu_description_counts_its_start_mark_only():
    """A / menu description is marked at its start only (`Tr.lead`), so
    its budget counts one mark, not two: ar's cmd.mode fits its 40."""
    src = {"k": "cmd.x", "en": "x {name}", "eg": {}}
    assert tool.rtl_overhead("fa", "x", None, kind="menu") == 1
    assert tool.rtl_overhead("fa", "{name}", src, kind="menu") == 3
    assert tool.rtl_overhead("fa", "{name}", src, kind="toast") == 4
    assert tool.check_budget("cmd.x", "ب" * 39, code="fa") == []
    assert tool.check_budget("cmd.x", "ب" * 40, code="fa")
