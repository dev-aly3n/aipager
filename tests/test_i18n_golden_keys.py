"""The golden strings (multilingual design 8.5, phase 5).

``tools/i18n/golden.txt`` lists the keys people see most, by section, for
a native reviewer; ``check`` keeps it valid and ``review-pack --golden``
hands it out with the English.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests import i18n_tool

tool = i18n_tool.load()


def _rows():
    return tool.load_jsonl(tool.SOURCE)


def test_the_list_has_every_section_and_a_reviewable_size():
    golden = tool.load_golden()
    assert tool.GOLDEN_MIN <= len(golden) <= tool.GOLDEN_MAX
    assert [s for s in tool.GOLDEN_SECTIONS if s in {sec for sec, _k in golden}] == list(
        tool.GOLDEN_SECTIONS)
    # Sections in the fixed order, each once.
    order = []
    for section, _key in golden:
        if not order or order[-1] != section:
            order.append(section)
    assert order == list(tool.GOLDEN_SECTIONS)


def test_the_shipped_list_is_clean():
    assert tool.check_golden(_rows()) == []


def test_the_list_keeps_what_the_design_names():
    keys = {k for _s, k in tool.load_golden()}
    for key in ("lang.first_contact", "lang.offer.text", "lang.row", "lang.auto", "lang.beta",
                "lang.changed", "lang.list.private", "lang.list.group", "mode.ask", "mode.auto",
                "perm.btn.allow", "perm.btn.deny", "card.verb.working", "kb.status",
                "botdesc.short"):
        assert key in keys, key
    rows = {r["k"] for r in _rows()}
    assert {k for k in rows if k.startswith("kb.")} <= keys
    assert {k for k in rows if k.startswith("perm.btn.")} <= keys
    assert {k for k in rows if k.startswith("mode.")} <= keys


def test_load_golden_reads_sections_and_skips_comments(tmp_path):
    path = tmp_path / "golden.txt"
    path.write_text("# a comment line here\nlang.row\n# language\nlang.auto\n\n"
                    "#keyboard\nkb.stop\n", encoding="utf-8")
    assert tool.load_golden(path) == [("", "lang.row"), ("language", "lang.auto"),
                                      ("keyboard", "kb.stop")]


@pytest.mark.parametrize("golden, want", [
    ([("language", "lang.nope")], "lang.nope is not a key"),
    ([("language", "lang.row"), ("language", "lang.row")], "lang.row is listed twice"),
    ([("menus", "lang.row")], "unknown section 'menus'"),
    ([("", "lang.row")], "unknown section ''"),
    ([("language", "lang.row")], "the section keyboard is empty"),
    ([("language", "lang.row")], "1 keys, want 120 to 180"),
])
def test_check_golden_names_each_problem(golden, want):
    problems = tool.check_golden(_rows(), golden)
    assert any(want in p for p in problems), problems


def test_too_many_keys_is_a_problem():
    rows = _rows()
    golden = [(tool.GOLDEN_SECTIONS[i % len(tool.GOLDEN_SECTIONS)], r["k"])
              for i, r in enumerate(rows[:tool.GOLDEN_MAX + 1])]
    assert any("want 120 to 180" in p for p in tool.check_golden(rows, golden))
    assert not any("want 120" in p for p in tool.check_golden(rows, golden[:tool.GOLDEN_MAX]))


def test_the_check_runs_the_golden_check(monkeypatch):
    monkeypatch.setattr(tool, "check_golden", lambda rows: ["golden list: marker"])
    assert "golden list: marker" in tool.run_checks()


def _run(tmp_path, *args):
    return subprocess.run([sys.executable, str(tool.ROOT / "tools" / "i18n.py"), *args],
                          capture_output=True, text=True, cwd=tool.ROOT,
                          env=i18n_tool.isolated_env(tmp_path))


def test_review_pack_golden_gives_only_the_golden_keys_with_english(tmp_path):
    proc = _run(tmp_path, "review-pack", "--golden", "--lang", "fa")
    assert proc.returncode == 0, proc.stderr
    pack = json.loads(proc.stdout)
    items = pack["fa"]
    golden = tool.load_golden()
    assert [i["k"] for i in items] == [k for _s, k in golden]
    assert [i["section"] for i in items] == [s for s, _k in golden]
    rows = {r["k"]: r for r in _rows()}
    for item in items:
        src = rows[item["k"]]
        if "en" in src:
            assert item["en"] == src["en"]
        else:
            assert (item["one"], item["other"]) == (src["one"], src["other"])
        assert item["v"] and "note" in item


def test_review_pack_golden_for_every_language(tmp_path):
    proc = _run(tmp_path, "review-pack", "--golden")
    assert proc.returncode == 0, proc.stderr
    pack = json.loads(proc.stdout)
    assert sorted(pack) == sorted(tool.TRANSLATED)
    assert all(len(items) == len(tool.load_golden()) for items in pack.values())


def test_review_pack_golden_refuses_a_language_we_do_not_have(tmp_path):
    assert _run(tmp_path, "review-pack", "--golden", "--lang", "ja").returncode == 2


def test_the_blind_pack_still_has_no_english():
    pack, answers = tool.review_pack("fa")
    assert all(set(item) == {"k", "v"} for item in pack["fa"])
    assert answers["fa"]
