"""The catalog checks of the multilingual design (section 9): 1 every key
in every enabled language, 2 no stale translation, 3 placeholders, 4 tags,
5 plural forms, 8 no long dash or direction control, 9 digits, 16 the file
format, plus ``source.jsonl`` in sync with the code.

With every catalog empty most of these pass trivially, so each also runs
against a synthetic catalog under ``tmp_path`` that must fail: those tests
are what proves the checks work.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from tests import i18n_tool

tool = i18n_tool.load()

SRC = [
    {"k": "toast.agents", "one": "{n} agent on {s}", "other": "{n} agents on {s}",
     "placeholders": ["n", "s"], "eg": {}},
    {"k": "toast.saved", "en": "Saved <b>{name}</b> for {n} min", "placeholders": ["name", "n"],
     "eg": {}},
]


def _row(entry):
    return {"k": entry["k"], "v": entry["v"], "src": entry.get("src") or tool.src_hash(
        next(r for r in SRC if r["k"] == entry["k"]))}


def _write(path, entries, *, raw=None):
    if raw is None:
        raw = "".join(json.dumps(_row(e), ensure_ascii=False, sort_keys=True) + "\n"
                      for e in entries)
    path.write_text(raw, encoding="utf-8")
    return path


def _check(tmp_path, code, entries, *, enabled=None, raw=None):
    path = _write(tmp_path / f"{code}.jsonl", entries, raw=raw)
    return tool.check_catalogs(SRC, {code: path}, enabled or (), glossaries={})


GOOD_DE = [
    {"k": "toast.agents", "v": {"one": "{n} Agent auf {s}", "other": "{n} Agenten auf {s}"}},
    {"k": "toast.saved", "v": "<b>{name}</b> für {n} Min. gespeichert"},
]


def test_a_good_catalog_passes(tmp_path):
    assert _check(tmp_path, "de", GOOD_DE, enabled=("en", "de")) == []


# ---- the real files -------------------------------------------------------

def test_the_shipped_catalogs_pass_every_check():
    rows = tool.load_jsonl(tool.SOURCE)
    from aipager.i18n import locales
    assert tool.check_catalogs(rows, tool.all_catalogs(), locales.enabled_codes(),
                               glossaries=tool.all_glossaries()) == []


def test_source_file_matches_the_code():
    assert tool.check_source_sync() == []


def test_extract_check_from_the_command_line(tmp_path):
    out = subprocess.run([sys.executable, str(tool.ROOT / "tools" / "i18n.py"), "extract",
                          "--check"], capture_output=True, text=True, cwd=tool.ROOT,
                         env=i18n_tool.isolated_env(tmp_path))
    assert out.returncode == 0, out.stderr


def test_a_child_process_gets_a_throwaway_home(tmp_path):
    """Guard for ``isolated_env``: the child's home, instance dir and temp
    dir are all under ``tmp_path``, never the operator's."""
    script = ("import os, tempfile; from pathlib import Path; "
              "print(Path.home()); print(os.environ.get('AIPAGER_INSTANCE_DIR')); "
              "print(tempfile.gettempdir())")
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         check=True, env=i18n_tool.isolated_env(tmp_path))
    home, instance, tmp = out.stdout.split("\n")[:3]
    assert home == str(tmp_path / "home")
    assert instance == str(tmp_path / "instance")
    assert tmp == str(tmp_path)


def test_a_stale_source_file_is_caught(tmp_path):
    pkg = tmp_path / "aipager"
    pkg.mkdir()
    (pkg / "m.py").write_text('X = Msg("a.b", "One")\n', encoding="utf-8")
    source = tmp_path / "source.jsonl"
    source.write_text(tool.dump_jsonl(tool.extract_entries(pkg)), encoding="utf-8")
    assert tool.check_source_sync(pkg, source) == []
    (pkg / "m.py").write_text('X = Msg("a.b", "Two")\n', encoding="utf-8")
    assert tool.check_source_sync(pkg, source)


def test_extract_refuses_non_literals_and_conflicts(tmp_path):
    pkg = tmp_path / "aipager"
    pkg.mkdir()
    for body in ('X = Msg(KEY, "One")\n', 'X = Msg("a.b", f"{y}")\n',
                 'X = Msg("Bad", "x")\n',
                 'X = Msg("a.b", "One")\nY = Msg("a.b", "Two")\n',
                 'X = Plural("a.b", one="x")\n'):
        (pkg / "m.py").write_text(body, encoding="utf-8")
        with pytest.raises(tool.ExtractError):
            tool.extract_entries(pkg)


def test_extract_records_scope_not_line_numbers(tmp_path):
    pkg = tmp_path / "aipager"
    pkg.mkdir()
    (pkg / "m.py").write_text(
        'class C:\n    def f(self, tr):\n        return tr.html("a.b", "Hi {who}", who=1)\n'
        'P = Plural("c.d", one="{n} x", other="{n} xs", note="n", eg={"n": 2})\n',
        encoding="utf-8")
    rows = tool.extract_entries(pkg)
    assert [r["k"] for r in rows] == ["a.b", "c.d"]
    assert rows[0]["scope"] == "C.f" and rows[0]["file"] == "aipager/m.py"
    assert rows[0]["placeholders"] == ["who"]
    assert rows[1]["eg"] == {"n": 2} and rows[1]["note"] == "n"
    assert "line" not in json.dumps(rows)


# ---- check 1: complete ------------------------------------------------------

def test_a_missing_key_in_an_enabled_language_fails(tmp_path):
    entries = GOOD_DE[:1]
    assert _check(tmp_path, "de", entries) == []
    problems = _check(tmp_path, "de", entries, enabled=("en", "de"))
    assert any("missing keys ['toast.saved']" in p for p in problems)


# ---- check 2: stale -----------------------------------------------------------

def test_a_stale_translation_fails(tmp_path):
    stale = [dict(GOOD_DE[1], src="deadbeef")]
    problems = _check(tmp_path, "de", stale)
    assert any("stale" in p for p in problems)


def test_the_plural_hash_covers_every_form():
    a = tool.src_hash({"one": "x", "other": "y"})
    assert a != tool.src_hash({"one": "x", "other": "z"})
    assert a != tool.src_hash({"one": "z", "other": "y"})
    assert tool.src_hash({"en": "Saved"}) == hashlib.sha1(b"Saved").hexdigest()[:8]
    assert len(a) == 8


# ---- check 3: placeholders -------------------------------------------------------

def test_wrong_placeholders_fail(tmp_path):
    for v in ("<b>{nom}</b> {n}", "<b>{name}</b>", "<b>{name}</b> {n} {extra}",
              "<b>{name}</b> {n:>3}"):
        problems = _check(tmp_path, "de", [{"k": "toast.saved", "v": v}])
        assert any("placeholders" in p or "bad placeholder" in p for p in problems), v


def test_a_plural_form_may_drop_the_count():
    src = SRC[0]
    assert tool.check_translation("de", src, {"one": "ein Agent auf {s}",
                                              "other": "{n} Agenten auf {s}"}) == []



def test_a_form_covering_many_counts_must_show_the_count():
    src = SRC[0]
    # uk "many" covers 0, 5-20, 25...: a form without {n} would hide the count.
    uk = {"one": "{n} агент на {s}", "few": "{n} агенти на {s}",
          "many": "агентів на {s}", "other": "{n} агента на {s}"}
    problems = tool.check_translation("uk", src, uk)
    assert any("'many' form must show {n}" in p for p in problems)
    assert tool.check_translation("uk", src, dict(uk, many="{n} агентів на {s}")) == []
    # uk "one" covers 1, 21, 31...: it must show the count too.
    problems = tool.check_translation("uk", src, dict(uk, one="один агент на {s}",
                                                      many="{n} агентів на {s}"))
    assert any("'one' form must show {n}" in p for p in problems)


def test_arabic_single_number_forms_may_name_the_count():
    src = SRC[0]
    words = {"zero": "لا وكلاء على {s}", "one": "وكيل واحد على {s}",
             "two": "وكيلان على {s}", "few": "{n} وكلاء على {s}",
             "many": "{n} وكيلًا على {s}", "other": "{n} وكيل على {s}"}
    assert tool.check_translation("ar", src, words) == []
    problems = tool.check_translation("ar", src, dict(words, few="وكلاء على {s}"))
    assert any("'few' form must show {n}" in p for p in problems)

# ---- check 4: tags -----------------------------------------------------------------

def test_wrong_tags_fail(tmp_path):
    for v in ("{name} für {n}", "<i>{name}</i> für {n}", "<b>{name}</b> für {n} <br>",
              "<b>{name}</b> &nbsp; {n}", "<b>{name}</b> <script>{n}</script>"):
        problems = _check(tmp_path, "de", [{"k": "toast.saved", "v": v}])
        assert any("tag" in p or "entities" in p for p in problems), v


# ---- check 5: plural forms ------------------------------------------------------------

def test_missing_plural_forms_fail(tmp_path):
    entries = [{"k": "toast.agents", "v": {"one": "{n} агент {s}", "other": "{n} агента {s}"}}]
    problems = _check(tmp_path, "ru", entries)
    assert any("missing plural forms ['few', 'many']" in p for p in problems)
    problems = _check(tmp_path, "de", [{"k": "toast.agents", "v": "{n} Agenten {s}"}])
    assert any("object of forms" in p for p in problems)
    problems = _check(tmp_path, "de", [{"k": "toast.agents", "v": {
        "one": "{n} A {s}", "other": "{n} B {s}", "few": "{n} C {s}"}}])
    assert any("not used in de" in p for p in problems)


# ---- check 8: long dashes and direction controls ------------------------------------------

def test_a_long_dash_or_direction_control_fails(tmp_path):
    for bad in ("Gespeichert \u2014 ok", "x \u202e y", "x \u200f y", "x \u2068y\u2069"):
        path = tmp_path / "x.jsonl"
        path.write_text(json.dumps({"k": "a.b", "v": bad, "src": "0"}, ensure_ascii=False)
                        + "\n", encoding="utf-8")
        assert tool.scan_files([path]), repr(bad)


def test_the_scans_reach_catalogs_and_tools():
    names = {p.name for p in tool.scanned_files()}
    assert {"fa.jsonl", "source.jsonl", "fa.json", "fa.md", "i18n.py"} <= names
    assert tool.scan_files(tool.scanned_files()) == []


def test_apply_refuses_a_long_dash(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "SOURCE", tmp_path / "source.jsonl")
    (tmp_path / "source.jsonl").write_text(tool.dump_jsonl([SRC[1]]), encoding="utf-8")
    cat = tmp_path / "de.jsonl"
    problems = tool.apply("de", [{"k": "toast.saved",
                                  "v": "<b>{name}</b> \u2014 {n} Min."}], catalog_path=cat)
    assert problems and not cat.exists()


# ---- check 9: digits -----------------------------------------------------------------------

def test_digits_follow_the_language():
    assert tool.check_digits("fa", "k", "۳ پیام {n} <code>x1</code>") == []
    assert tool.check_digits("fa", "k", "3 پیام")
    assert tool.check_digits("fa", "k", "٣ پیام")
    assert tool.check_digits("ar", "k", "٣ رسائل") == []
    assert tool.check_digits("ar", "k", "۳ رسائل")
    assert tool.check_digits("de", "k", "3 Nachrichten") == []
    assert tool.check_digits("de", "k", "۳ Nachrichten")


def test_wrong_digits_in_a_catalog_fail(tmp_path):
    entries = [{"k": "toast.saved", "v": "<b>{name}</b> 5 {n}"}]
    problems = _check(tmp_path, "fa", entries)
    assert any("fa digits" in p for p in problems)


# ---- check 16: format ------------------------------------------------------------------------

def test_unsorted_or_duplicate_catalog_fails(tmp_path):
    a = json.dumps({"k": "a.a", "v": "x", "src": "0"})
    b = json.dumps({"k": "b.b", "v": "y", "src": "0"})
    path = tmp_path / "de.jsonl"
    cases = {
        "not sorted": b + "\n" + a + "\n",
        "duplicate": a + "\n" + a + "\n",
        "trailing newline": a,
        "exactly k, v and src": json.dumps({"k": "a.a", "v": "x"}) + "\n",
        "one JSON object": a + " " + b + "\n",
        "not UTF-8": None,
    }
    for want, raw in cases.items():
        if raw is None:
            path.write_bytes(b'{"k": "a.a", "v": "\xff", "src": "0"}\n')
        else:
            path.write_text(raw, encoding="utf-8")
        problems = tool.check_catalog_format(path)
        assert any(want in p for p in problems), (want, problems)
    path.write_text(a + "\n" + b + "\n", encoding="utf-8")
    assert tool.check_catalog_format(path) == []


def test_apply_writes_a_sorted_stamped_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "SOURCE", tmp_path / "source.jsonl")
    (tmp_path / "source.jsonl").write_text(tool.dump_jsonl(SRC), encoding="utf-8")
    cat = tmp_path / "de.jsonl"
    assert tool.apply("de", {"entries": list(reversed(GOOD_DE))}, catalog_path=cat) == []
    assert tool.check_catalog_format(cat) == []
    rows = [json.loads(line) for line in cat.read_text(encoding="utf-8").splitlines()]
    assert [r["k"] for r in rows] == ["toast.agents", "toast.saved"]
    assert rows[1]["src"] == tool.src_hash(SRC[1])
    assert tool.apply("de", [{"k": "toast.saved", "v": "{name}"}], catalog_path=cat)
    assert tool.apply("de", [{"k": "no.such", "v": "x"}], catalog_path=cat)
    assert tool.apply("en", [], catalog_path=cat)


# ---- prune: keys the code no longer has ---------------------------------------------------

def _prune_world(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(tool.dump_jsonl(SRC), encoding="utf-8")
    gone = {"k": "toast.gone", "v": "weg", "src": "0"}
    raw = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n"
                  for e in sorted([_row(GOOD_DE[0]), gone, _row(GOOD_DE[1])],
                                  key=lambda e: e["k"]))
    de = tmp_path / "de.jsonl"
    de.write_text(raw, encoding="utf-8")
    fr = _write(tmp_path / "fr.jsonl", [
        {"k": "toast.saved", "v": "<b>{name}</b> enregistré pour {n} min"}])
    return source, {"de": de, "fr": fr}


def test_prune_drops_only_the_keys_gone_from_the_source(tmp_path):
    source, cats = _prune_world(tmp_path)
    fr_before = cats["fr"].read_bytes()
    assert tool.prune(catalogs=cats, source=source) == [("de", "toast.gone")]
    rows = [json.loads(line) for line in cats["de"].read_text(encoding="utf-8").splitlines()]
    assert [r["k"] for r in rows] == ["toast.agents", "toast.saved"]
    assert rows == [_row(GOOD_DE[0]), _row(GOOD_DE[1])]       # values and stamps kept
    assert tool.check_catalog_format(cats["de"]) == []
    assert cats["fr"].read_bytes() == fr_before                # nothing to drop there
    assert tool.prune(catalogs=cats, source=source) == []      # idempotent


def test_prune_dry_run_changes_nothing(tmp_path):
    source, cats = _prune_world(tmp_path)
    before = cats["de"].read_bytes()
    assert tool.prune(dry_run=True, catalogs=cats, source=source) == [("de", "toast.gone")]
    assert cats["de"].read_bytes() == before


def test_prune_refuses_a_stale_source(monkeypatch, capsys):
    monkeypatch.setattr(tool, "check_source_sync", lambda *a, **k: ["source.jsonl is stale"])

    def _never(**_kw):
        raise AssertionError("prune ran on a stale source")

    monkeypatch.setattr(tool, "prune", _never)
    assert tool.main(["prune"]) == 1
    assert "extract" in capsys.readouterr().err


def test_the_shipped_catalogs_have_nothing_to_prune():
    assert tool.prune(dry_run=True) == []


def test_todo_and_review_pack_cover_missing_and_changed_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(tool, "SOURCE", tmp_path / "source.jsonl")
    monkeypatch.setattr(tool, "LOCALES_DIR", tmp_path)
    (tmp_path / "source.jsonl").write_text(tool.dump_jsonl([
        dict(r, file="aipager/x.py", kind="toast", budget={}, note="")
        for r in SRC]), encoding="utf-8")
    _write(tmp_path / "ru.jsonl", [{"k": "toast.saved", "v": "<b>{name}</b> {n}",
                                    "src": "deadbeef"}])
    batch = tool.todo("ru")
    assert [e["k"] for e in batch["entries"]] == ["toast.agents", "toast.saved"]
    assert batch["entries"][0]["categories"] == ["one", "few", "many", "other"]
    assert batch["entries"][1]["previous"] == "<b>{name}</b> {n}"
    assert [e["k"] for e in tool.todo("ru", changed=True)["entries"]] == ["toast.saved"]
    pack, answers = tool.review_pack("ru")
    assert pack == {"ru": [{"k": "toast.saved", "v": "<b>{name}</b> {n}"}]}
    assert "en" not in json.dumps(pack)
    assert answers["ru"]["toast.saved"] == SRC[1]["en"]


# ---- checks 3b, 4b, 9b (phase 2) ------------------------------------------------

def _translation(code, english, value, key="toast.x", **extra):
    src = {"k": key, "en": english, "eg": {},
           "placeholders": tool.core.placeholders(english), **extra}
    return tool.check_translation(code, src, value, glossary=None)


def test_a_command_glued_to_a_placeholder_fails():
    assert _translation("de", "Send {cmd} now", "Sende {cmd} jetzt") == []
    assert any("Command" in p for p in _translation("de", "Send {cmd} now",
                                                      "Sende /{cmd} jetzt"))
    assert any("Command" in p for p in _translation("de", "Ask {who}", "Frag @{who}"))
    rows = [{"k": "toast.a", "en": "Talk to /{label}"}, {"k": "toast.b", "en": "Hi {x}"}]
    problems = tool.check_english_patterns(rows)
    assert len(problems) == 1 and "toast.a" in problems[0]


def test_a_lost_markdown_marker_fails():
    english = "⏳ **{session}** · _working_"
    assert _translation("de", english, "⏳ **{session}** · _arbeitet_") == []
    assert _translation("de", english, "⏳ {session} · _arbeitet_")
    assert _translation("de", english, "⏳ **{session}** · arbeitet")
    # A placeholder's own underscore is not a marker.
    assert _translation("de", "{snake_case} ok", "{snake_case} gut") == []
    # No backtick in any pattern, English or translated.
    assert any("backtick" in p for p in _translation("de", "Run it", "Führe `x` aus"))
    assert tool.check_english_patterns([{"k": "toast.c", "en": "Run `ls`"}])


def test_an_rtl_entry_with_a_right_arrow_fails():
    assert _translation("fa", "{a} → {b}", "{a} ← {b}") == []
    assert any("←" in p for p in _translation("fa", "{a} → {b}", "{a} → {b}"))
    assert any("←" in p for p in _translation("ar", "{a} → {b}", "{a} → {b}"))
    assert _translation("ru", "{a} → {b}", "{a} → {b}") == []


def test_an_identifier_keeps_its_latin_digits():
    english = "Off through AIPAGER_REPORT_PROMPTS=0 on x1 after 3 days."
    assert _translation("fa", english,
                        "خاموش با AIPAGER_REPORT_PROMPTS=0 روی x1 پس از ۳ روز.") == []
    assert _translation("fa", english,
                        "خاموش با AIPAGER_REPORT_PROMPTS=0 روی x1 پس از 3 روز.")
