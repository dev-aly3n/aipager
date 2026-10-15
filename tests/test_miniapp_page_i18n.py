"""The Mini App page in English, Persian and Russian, driven for real.

``tests/js/miniapp_i18n.js`` parses the page's markup into a small DOM
(text nodes, attributes, attribute selectors), walks every screen
(grid, session page, menus and confirms, diff, timeline, settings,
Updates, the problem report, the new-session form, the connection
states) and prints what a person sees on each one.

``tests/golden/miniapp_page_english.json`` holds that for an English
viewer, recorded on the page as it was before any of its text moved to
keys (run with ``AIPAGER_RECORD_PAGE=1`` to record). An English viewer
must still see exactly those bytes, through exactly the same requests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "miniapp_i18n.js"
GOLDEN = Path(__file__).parent / "golden" / "miniapp_page_english.json"


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available - the page i18n harness is skipped")
    return exe


def drive(node_bin, tmp_path, page: str, scenario: str = "screens", opts: dict | None = None,
          name: str = "page.html") -> dict:
    """Run the harness on *page*; the parsed RESULT, or a failed test."""
    path = tmp_path / name
    path.write_text(page, encoding="utf-8")
    env = dict(os.environ)
    if opts is not None:
        opts_path = tmp_path / (name + ".opts.json")
        opts_path.write_text(json.dumps(opts, ensure_ascii=False), encoding="utf-8")
        env["AIPAGER_I18N_OPTS"] = str(opts_path)
    proc = subprocess.run([node_bin, str(HARNESS), str(path), scenario],
                          capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-4000:]}"
    lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, proc.stdout[-2000:]
    return json.loads(lines[-1][len("RESULT "):])


def _english(node_bin, tmp_path, page: str) -> dict:
    run = drive(node_bin, tmp_path, page)
    alone = drive(node_bin, tmp_path, page, opts={"noInit": True}, name="noinit.html")
    return {"screens": [[s["name"], s["texts"]] for s in run["screens"]],
            "calls": run["calls"], "lang": run["lang"], "dir": run["dir"],
            "no_init": alone["screens"][0]["texts"], "no_init_calls": alone["calls"]}


def _dump(data: dict) -> str:
    """One screen per line, so a failure diff names the screen."""
    out = ["{"]
    keys = sorted(data)
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        if key == "screens":
            out.append(' "screens": [')
            rows = data["screens"]
            for j, row in enumerate(rows):
                out.append("  " + json.dumps(row, ensure_ascii=True)
                           + ("," if j < len(rows) - 1 else ""))
            out.append(" ]" + comma)
        else:
            out.append(f" {json.dumps(key)}: {json.dumps(data[key], ensure_ascii=True)}{comma}")
    out.append("}")
    return "\n".join(out) + "\n"


def test_english_snapshot(node_bin, tmp_path):
    """Every visible text, aria-label, placeholder, title, MainButton text
    and confirm message on every screen, and every request the page made,
    for an English viewer: byte for byte what the page showed before its
    text moved to keys."""
    from aipager.miniapp.static import INDEX_HTML

    got = _english(node_bin, tmp_path, INDEX_HTML)
    if os.environ.get("AIPAGER_RECORD_PAGE") == "1":
        GOLDEN.write_text(_dump(got), encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [s[0] for s in got["screens"]] == [s[0] for s in want["screens"]]
    for (name, texts), (_n, golden) in zip(got["screens"], want["screens"]):
        assert texts == golden, name
    assert got["no_init"] == want["no_init"]
    # The same requests in the same order: an English viewer never asks for
    # a catalog, and nothing waits on one.
    assert got["calls"] == want["calls"]
    assert got["no_init_calls"] == want["no_init_calls"] == []
    assert (got["lang"], got["dir"]) == (want["lang"], want["dir"]) == ("en", None)


def test_the_english_snapshot_sees_one_changed_word(node_bin, tmp_path):
    """Guard the guard: one English word changed anywhere in the page's
    own text fails the snapshot."""
    from aipager.miniapp.static import SOURCE_INDEX_HTML

    broken = SOURCE_INDEX_HTML.replace("Finished (", "Finishd (")
    assert broken != SOURCE_INDEX_HTML
    got = _english(node_bin, tmp_path, broken)
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [s[1] for s in got["screens"]] != [s[1] for s in want["screens"]]


# ---- Persian, Russian, the pseudo language -----------------------------------------------

import re  # noqa: E402

from aipager import i18n  # noqa: E402
from aipager.i18n import Plural, core, plurals  # noqa: E402
from aipager.miniapp import ui_text  # noqa: E402

RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"
PERSIAN_LETTER = re.compile("[\u0600-\u06ef\u06fa-\u06ff]")
PERSIAN_DIGIT = re.compile("[\u06f0-\u06f9]")
CYRILLIC = re.compile("[\u0400-\u04ff]")
RAW_KEY = re.compile(r"\b(?:ui|app)\.[a-z0-9_]+\.[a-z0-9_.]+")


def _opts(code: str, catalog: dict | None = None, **extra) -> dict:
    body = catalog if catalog is not None else ui_text.catalog(code)
    ui = {"code": code, "rtl": body["rtl"], "v": ui_text.catalog_version(code) or "test"}
    return {"ui": ui, "catalogs": {code: body}, **extra}


def _after_boot(run: dict):
    """Every screen after the first, which is the page before any answer
    (the first open of a language shows English for one poll)."""
    for name, texts in ((s["name"], s["texts"]) for s in run["screens"][1:]):
        for text in texts:
            m = re.match(r"[@#][a-z-]+=", text)
            yield name, text[m.end():] if m else text


def _plain_english() -> dict[str, str]:
    """key -> English of every page string without a placeholder."""
    out = {}
    for m in ui_text.PAGE_STRINGS:
        if not isinstance(m, Plural) and not core.placeholders(str(m)):
            out[m.key] = str(m)
    return out


def _leaks(run: dict, code: str) -> list:
    """Page strings shown in English although *code* has them."""
    cat = core._catalog(code)
    english = {v: k for k, v in _plain_english().items() if cat.get(k) not in (None, v)}
    served = set(run["served"])
    return [(name, text) for name, text in _after_boot(run)
            if text in english and text not in served]


def test_fa(node_bin, tmp_path):
    """A Persian viewer: right to left, Persian everywhere the page speaks,
    Persian digits on numbers only, names and code left to right."""
    from aipager.miniapp.static import INDEX_HTML

    fa = core._catalog("fa")
    labels = {fid: fa["app.fact." + fid] for fid in
              ("model", "context", "cost", "working", "last_active", "cwd")}
    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts("fa", factLabels=labels))
    assert (run["lang"], run["dir"]) == ("fa", "rtl")
    assert _leaks(run, "fa") == []
    texts = [t for _n, t in _after_boot(run)]
    assert not [t for t in texts if RAW_KEY.search(t)]
    screens = {s["name"]: s["texts"] for s in run["screens"]}
    grid = screens["grid"]
    # The tab bar and the summary in Persian, the counts in Persian digits.
    assert fa["ui.tab.sessions"] in grid and fa["ui.tab.settings"] in grid
    assert "۲ " + fa["ui.pulse.need"]["other"].replace("{n} ", "") in grid
    assert "۶۴" in grid and "64" not in grid
    assert fa["ui.age.seconds"].replace("{n}", "۳۰") in grid
    # Names stay Latin and unconverted: x1-style labels, model ids, versions.
    assert "charlie" in grid and "Opus 4.6 · $۰٫۵۰" in grid
    assert "old1" in screens["grid.shelf"] and "old1" in screens["detail.gone"]
    assert any(t.startswith("@aipager_bot · v0.7.17 · ") for t in grid)
    # A name inside a sentence is isolated.
    assert "@aria-label=" + fa["ui.beacon.open_aria"].replace(
        "{session}", FSI + "alpha" + PDI) in grid
    detail = screens["detail.busy"]
    assert fa["ui.state_line.working_for"].replace("{clock}", FSI + "۳:۱۲" + PDI) in detail
    assert "۳:۱۲" in detail and "3:12" in detail      # the chip ours, the fact the server's
    report = screens["report.too_long"]
    assert "۳۳ / ۲۰" in report
    # The ring's percent sign and the step numbers.
    assert screens["new.open"].count("۱") >= 1 and "1" not in screens["new.open"]
    # The islands.
    islands = run["islands"]
    for name in ("tile_label", "tile_meta", "beacon_label", "shelf_label", "detail_label",
                 "fact_cwd", "timeline_tool", "rp_exact", "rp_ref", "daemon_line", "new_name",
                 "new_model_name", "new_folder_name", "rename_input"):
        assert islands[name] == "ltr", name
    for name in ("beacon_summary", "preview", "waiting_text", "chat_chip", "shelf_meta"):
        assert islands[name] == "auto", name
    # The ring's percent sign is Persian's.
    assert islands["pct_sign"] == "\u066a"
    # No direction mark the page wrote ever splits a word or a command.
    for name, text in _after_boot(run):
        assert not re.search("[A-Za-z0-9_][" + FSI + PDI + "][A-Za-z0-9_]", text), (name, text)
        assert not re.search("(?<![\\w./-])/[" + FSI + PDI + RLM + "]", text), (name, text)


def test_ru(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts("ru"))
    assert (run["lang"], run["dir"]) == ("ru", "ltr")
    assert run["islands"]["pct_sign"] == "%"
    assert _leaks(run, "ru") == []
    texts = [t for _n, t in _after_boot(run)]
    assert any(CYRILLIC.search(t) for t in texts)
    assert not [t for t in texts if any(m in t for m in (RLM, FSI, PDI))]
    assert not [t for t in texts if PERSIAN_DIGIT.search(t) or RAW_KEY.search(t)]


COUNTS = (1, 2, 5, 11, 21, 22, 25)


def _form(code: str, key: str, n: int) -> str:
    forms = core._catalog(code)[key]
    return forms[plurals.category(code, n)].replace("{n}", str(n))


def test_ru_plurals(node_bin, tmp_path):
    """Every counted page string at 1, 2, 5, 11, 21, 22 and 25: the
    catalog's form for that count's Russian category."""
    from aipager.miniapp.static import INDEX_HTML

    run = drive(node_bin, tmp_path, INDEX_HTML, scenario="plurals",
                opts=_opts("ru", counts=list(COUNTS)))
    assert [plurals.category("ru", n) for n in COUNTS] == [
        "one", "few", "many", "many", "one", "few", "many"]
    for n in COUNTS:
        got = run["plurals"][str(n)]
        assert got["need"] == _form("ru", "ui.pulse.need", n), n
        assert got["queued"] == _form("ru", "ui.activity.queued", n), n
        assert got["timeline"] == _form("ru", "ui.timeline.title_n", n), n
        assert got["errors"] == _form("ru", "ui.report.errors_n", n + 3), n
        assert got["more"] == _form("ru", "ui.report.show_more", n), n
        assert got["tooLong"] == _form("ru", "ui.report.too_long", n), n


def test_the_page_falls_back_to_english_never_to_a_key(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    body = ui_text.catalog("fa")
    gone = ("ui.tab.sessions", "ui.pulse.need", "ui.btn.action.stop", "ui.state.busy")
    body["strings"] = {k: v for k, v in body["strings"].items() if k not in gone}
    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts("fa", body))
    screens = {s["name"]: s["texts"] for s in run["screens"]}
    assert "Sessions" in screens["grid"]
    assert "۲ need you" in screens["grid"]          # English form, the page's digits
    assert "working" in screens["grid"]
    assert "Stop" in screens["detail.busy"]
    assert not [t for _n, t in _after_boot(run) if RAW_KEY.search(t)]
    # Everything else is still Persian.
    assert core._catalog("fa")["ui.tab.settings"] in screens["grid"]


def test_qps_marks_every_page_string(node_bin, tmp_path, monkeypatch):
    """Under the pseudo language every string the page itself shows is
    marked; what is left unmarked is the server's data or a name."""
    from aipager.i18n import locales
    from aipager.miniapp.static import INDEX_HTML
    from tests.i18n_surfaces import unmarked_words

    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts("qps"))
    # The server's words as the page shows them: a settings title without
    # its emoji, an option's label up to its " - ", a line cut at its code.
    served = set(run["served"])
    served |= {re.sub(r"^[^\w]+\s+", "", v) for v in run["served"]}
    served |= {v.split(" - ")[0] for v in run["served"]}
    served |= {part.strip() for v in run["served"] for part in v.split("`")}
    served = sorted(served, key=len, reverse=True)
    # Names and ids the page shows as they are: a product, libraries, the
    # report's counter ids, the examples in empty fields, typed names.
    names = ("Python", "httpx", "stale", "card", "edits", "aipager report", "frontend",
             "claude-opus-5", "my-project", "zz", "-bad")
    leaks = []
    for name, text in _after_boot(run):
        if text.startswith("{"):
            continue                    # the report's exact JSON: always English
        rest = text
        for value in names:
            rest = rest.replace(value, " ")
        for value in served:
            if value and len(value) > 1:
                rest = re.sub(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", " ", rest)
        words = unmarked_words(rest)
        if words:
            leaks.append((name, text, words))
    assert not leaks, leaks[:20]


def _hostile(code: str) -> dict:
    body = ui_text.catalog(code)
    def bad(v):
        return v + "<img src=x onerror=1>"
    body["strings"] = {k: ({c: bad(f) for c, f in v.items()} if isinstance(v, dict) else bad(v))
                       for k, v in body["strings"].items()}
    return body


def test_no_raw_html(node_bin, tmp_path):
    """Hostile catalog values and hostile server values in every screen:
    nothing ever becomes markup (no <img> element is ever made)."""
    from aipager.miniapp.static import INDEX_HTML

    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts("fa", _hostile("fa"), hostile=True))
    assert run["imgs"] == 0
    assert any("<img src=x onerror=1>" in t for _n, t in _after_boot(run))
    run = drive(node_bin, tmp_path, INDEX_HTML, opts={"hostile": True}, name="en.html")
    assert run["imgs"] == 0


def test_the_hostile_run_sees_raw_markup(node_bin, tmp_path):
    """Guard the guard: one innerHTML without escaping makes an <img>."""
    from aipager.miniapp.static import SOURCE_INDEX_HTML

    old = "    var html = '<span class=\"dis-title\">' + escapeHtml(title) +"
    assert SOURCE_INDEX_HTML.count(old) == 1
    broken = SOURCE_INDEX_HTML.replace(old, "    var html = '<span class=\"dis-title\">' + title +")
    run = drive(node_bin, tmp_path, broken, opts=_opts("fa", _hostile("fa"), hostile=True))
    assert run["imgs"] > 0


def test_live_switch(node_bin, tmp_path):
    """App language: English to Persian and back without a reload. The
    catalog is fetched once, the direction flips, the static text changes,
    Settings comes back from the server; English again restores the
    markup's English byte for byte, with no catalog fetch."""
    from aipager.miniapp.static import INDEX_HTML

    opts = _opts("fa")
    run = drive(node_bin, tmp_path, INDEX_HTML, scenario="live_switch",
                opts={"switchTo": opts["ui"], "catalogs": opts["catalogs"]})
    live = run["live"]
    sw, back = live["switched"], live["back"]
    assert (sw["lang"], sw["dir"]) == ("fa", "rtl")
    fetches = [c for c in sw["calls"] if c.startswith("GET /api/i18n/")]
    assert fetches == [f"GET /api/i18n/fa.json?v={opts['ui']['v']}"]
    assert sw["calls"].index(fetches[0]) < sw["calls"].index("GET /api/preferences")
    assert "CONFIRMED" in sw["notice"]
    fa = core._catalog("fa")
    shown = {row[0]: row[1] for row in sw["texts"]}
    assert shown["ui.settings.chat_prefs"] == fa["ui.settings.chat_prefs"]
    assert shown["ui.settings.appearance"] == fa["ui.settings.appearance"]
    assert shown["ui.tab.settings"] == fa["ui.tab.settings"]
    assert fa["ui.btn.check_updates"] in sw["screen"]
    assert (back["lang"], back["dir"]) == ("en", "ltr")
    assert not [c for c in back["calls"] if "/api/i18n/" in c]
    assert "GET /api/preferences" in back["calls"]
    assert "BACK" in back["notice"]
    assert back["texts"] == live["english"]
    assert "Check for updates" in back["screen"]


@pytest.mark.parametrize("then", [None, "ru"])
def test_a_late_catalog_never_takes_the_page_back(node_bin, tmp_path, then):
    """Persian is chosen, then English or Russian before the Persian
    catalog arrived: when it finally does, the page ignores it. It never
    shows Persian, not even for a moment, asks for nothing more, and the
    later choice stays (its language, its direction, what the phone
    remembers)."""
    from aipager.miniapp.static import INDEX_HTML

    fa = _opts("fa")
    opts = {"first": fa["ui"], "catalogs": dict(fa["catalogs"]), "storage": {}}
    if then:
        ru = _opts(then)
        opts["then"] = ru["ui"]
        opts["catalogs"].update(ru["catalogs"])
    run = drive(node_bin, tmp_path, INDEX_HTML, scenario="stale_switch", opts=opts)
    stale = run["stale"]
    assert stale["waiting"] == 1   # the Persian catalog was on its way
    assert f"GET /api/i18n/fa.json?v={fa['ui']['v']}" in run["calls"]
    assert stale["langs"] == [] and stale["calls"] == []
    want = (then or "en", "ltr" if then else stale["before"]["dir"])
    assert (stale["before"]["lang"], stale["before"]["dir"]) == want
    assert (run["lang"], run["dir"]) == want
    assert run["dir"] != "rtl"
    assert run["storage"] == stale["before"]["storage"] == (
        {"aipager.lang": then} if then else {})


def test_the_loading_line_reads_in_its_language(node_bin, tmp_path):
    """Before the first grid answer the daemon line says "Loading" in the
    viewer's language, in that language's direction; the version line
    that replaces it is a name and reads left to right."""
    from aipager.miniapp.static import INDEX_HTML

    run = drive(node_bin, tmp_path, INDEX_HTML, scenario="loading",
                opts=_opts("fa", storage={"aipager.lang": "fa"}, holdGrid=True))
    loading = run["loading"]
    assert loading["waiting"] == 1
    assert loading["before"] == {"dir": "auto",
                                 "text": core._catalog("fa")["ui.shell.loading"]}
    assert loading["after"]["dir"] == "ltr" and loading["after"]["text"].startswith("@")


def test_remembered(node_bin, tmp_path):
    """A language remembered on this phone: the first grid poll waits for
    the chat list and the catalog, so the grid never flashes English."""
    from aipager.miniapp.static import INDEX_HTML

    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts("fa", storage={"aipager.lang": "fa"}))
    calls = run["calls"]
    assert calls[:3] == ["GET /api/chats", f"GET /api/i18n/fa.json?v={_opts('fa')['ui']['v']}",
                         "GET /api/sessions"]
    assert run["storage"] == {"aipager.lang": "fa"}
    # Changed back to English in the chat: no catalog, the memory goes.
    run = drive(node_bin, tmp_path, INDEX_HTML, opts={"storage": {"aipager.lang": "fa"}},
                name="back.html")
    assert run["calls"][:2] == ["GET /api/chats", "GET /api/sessions"]
    assert not [c for c in run["calls"] if "/api/i18n/" in c]
    assert run["storage"] == {}
    # The first ever open: one English poll, then the language.
    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts("fa", storage={}), name="first.html")
    assert run["calls"][:3] == ["GET /api/chats", "GET /api/sessions",
                                f"GET /api/i18n/fa.json?v={_opts('fa')['ui']['v']}"]
    assert run["storage"] == {"aipager.lang": "fa"}


def test_the_english_catalog_is_the_page_strings():
    """The page's inline English is exactly ui_text's, as one statement
    that cannot close its script element."""
    import json as _json

    from aipager.miniapp.static import INDEX_HTML

    start = INDEX_HTML.index("var AIPAGER_UI_EN=") + len("var AIPAGER_UI_EN=")
    end = INDEX_HTML.index(";\n", start)
    assert _json.loads(INDEX_HTML[start:end].replace("<\\/", "</")) == ui_text.english_catalog()
    assert "</" not in INDEX_HTML[start:end]
    assert all(ord(c) < 128 for c in INDEX_HTML[start:end])
    assert ui_text.english_catalog()["ui.pulse.need"] == {"one": "{n} needs you",
                                                         "other": "{n} need you"}
    assert i18n.get("en").text(ui_text.REPORT_TRY_LATER) == str(ui_text.REPORT_TRY_LATER)


def test_a_catalog_value_cannot_close_the_script(monkeypatch):
    """The inline English is written into a <script> element: a value
    holding "</script>" must not end it there, and must still read back
    as the same value."""
    import json as _json

    from aipager.miniapp import static

    hostile = {"ui.x": "a</script><img src=x onerror=1>", "ui.y": {"one": "</b", "other": "<"}}
    monkeypatch.setattr(ui_text, "english_catalog", lambda: hostile)
    stmt = static._english_catalog_js()
    assert "</" not in stmt
    assert stmt.startswith("var AIPAGER_UI_EN=") and stmt.endswith(";\n")
    assert _json.loads(stmt[len("var AIPAGER_UI_EN="):-2]) == hostile
