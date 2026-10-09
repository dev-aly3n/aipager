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
