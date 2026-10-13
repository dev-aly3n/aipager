"""The served page in English, Persian and Russian, run in node through
``bb_i18n.js`` (entrypoints.md "Page contract"; design.md success criteria
for the fa and ru viewer, the live switch and English unchanged).

Every payload the page receives is a real server response (``/api/chats``,
``/api/i18n/<code>.json``, ``/api/sessions``, ``/api/sessions/<label>``,
``/api/preferences``, ``PUT /api/app-language``) from a MiniAppServer whose
viewer has the language stored, except where a scenario edits one on purpose
(a catalog with keys removed, hostile values, a crowd of waiting sessions).

Partitions: no ``ui`` (English), ``ui`` fa, ``ui`` ru; a catalog complete,
with keys missing, or hostile; storage holding fa, holding nothing, or
missing; English -> fa -> English on the App language row.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aipager.i18n import plurals
from aipager.miniapp import static, ui_text

HERE = Path(__file__).parent
HARNESS = HERE / "bb_i18n.js"
KEYISH = re.compile(r"\b(?:ui|app)\.[a-z0-9_]+\.[a-z0-9_.]+")
LATIN_DIGIT = re.compile(r"[0-9]")
FA_DIGIT = re.compile("[\u06f0-\u06f9]")
HOSTILE = "<img src=x onerror=1>"
FARSI = "\u0641\u0627\u0631\u0633\u06cc"   # the endonym on the App language row


@pytest.fixture(scope="module")
def node():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def run_page(node, tmp_path, fixture, page=None):
    page_path = tmp_path / "page.html"
    page_path.write_text(page if page is not None else static.INDEX_HTML, encoding="utf-8")
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps(fixture), encoding="utf-8")
    result = tmp_path / "result.json"
    proc = subprocess.run([node, str(HARNESS), str(page_path), str(fx), str(result)],
                          capture_output=True, text=True, timeout=120)
    assert result.exists(), f"harness wrote no result: {proc.stderr[-2000:]}"
    if proc.stderr:
        print(proc.stderr)   # BB_DUMP=<ids> prints those subtrees here
    return json.loads(result.read_text(encoding="utf-8"))


# ---- real payloads -----------------------------------------------------------------

def payloads(env, p4, code):
    """Real server answers for viewer BOB with *code* stored ("en" = none)."""
    if code != "en":
        p4.set_lang(p4.BOB, code)
    env.session("web2", status="busy", model="claude-opus-5", cwd="/srv/web2", queued=2)
    env.session("echo", status="idle", model="claude-opus-5")
    env.session("old1", status="gone")
    fix = {}
    for path in ("/api/chats", "/api/sessions", "/api/sessions/web2", "/api/sessions/echo",
                 "/api/preferences", "/api/session-options",
                 "/api/sessions/web2/preferences", "/api/sessions/echo/preferences"):
        r = env.call("GET", path, scope=p4.GROUP)
        assert r.status == 200, (path, r.status, r.body)
        fix["GET " + path] = r.body
    for c in ("fa", "ru"):
        fix["GET /api/i18n/" + c + ".json"] = env.call("GET", f"/api/i18n/{c}.json").body
    fix["GET /api/update"] = {"status": 403, "body": {"error": "forbidden"}}
    return fix


def put_answers(env, p4, fix):
    """Real PUT /api/app-language answers for fa and en (stored state is
    restored by the second PUT)."""
    after = {}
    for value in ("fa", "en"):
        r = env.call("PUT", "/api/app-language", json={"value": value})
        assert r.status == 200
        fix["PUT /api/app-language " + value] = r.body
        after[value] = {"GET " + path: env.call("GET", path, scope=p4.GROUP).body
                        for path in ("/api/preferences", "/api/chats")}
    return fix, after


def boot(fix, *, storage=None, steps=None):
    return {"fix": fix, "storage": {} if storage is None else storage,
            "steps": steps or [{"do": "wait", "name": "grid"}]}




GRID_AND_DETAIL = [{"do": "wait", "name": "grid"},
                   {"do": "tap_tile", "label": "web2", "name": "detail"}]


def with_numbers(fix):
    """Give web2 numbers to show: 45% context, $1.50, 3:12 working."""
    fix = copy.deepcopy(fix)
    for row in fix["GET /api/sessions"]["sessions"]:
        if row["label"] == "web2":
            row.update(context_pct=45, cost_usd=1.5)
    fix["GET /api/sessions"]["totals"]["cost_usd"] = 1.5
    fix["GET /api/sessions/web2"].update(context_pct=45, cost_usd=1.5, busy_elapsed_seconds=192)
    return fix


def english_key(value):
    """The page key whose English is *value* (found through english_catalog,
    never spelled in the test)."""
    hits = [k for k, v in ui_text.english_catalog().items() if v == value]
    assert len(hits) == 1, (value, hits)
    return hits[0]


def pattern_rx(pattern):
    """A regex for *pattern* with each {param} standing for anything, and the
    first-strong isolates the page may put around a param."""
    parts = re.split(r"\{[a-z_]+\}", pattern)
    return "".join(re.escape(p) + ("(.+?)" if i < len(parts) - 1 else "")
                   for i, p in enumerate(parts))


def statics(snap, field="key"):
    """Static string elements; the daemon line only shows "Loading..." until
    the script writes the daemon's own line into it."""
    return [s for s in snap["statics"] if s[field] and s["id"] != "daemon-line"]


def visible_leaves(snap):
    return [x for x in snap["leaves"] if x["visible"]]


def text_of(snap, ident):
    return snap["ids"][ident]["text"]


def calls(snap, prefix):
    return [c for c in snap["calls"] if c["url"].startswith(prefix)]


# ---- Persian -----------------------------------------------------------------------------

@pytest.fixture
def fa(env, p4, node, tmp_path):
    fix = with_numbers(payloads(env, p4, "fa"))
    out = run_page(node, tmp_path, boot(fix, steps=GRID_AND_DETAIL))
    out["fix"] = fix
    return out


def test_fa_runs_without_errors(fa):
    assert fa["errors"] == []


def test_fa_html_lang(fa):
    assert fa["grid"]["html"].get("lang") == "fa"


def test_fa_html_dir(fa):
    assert fa["grid"]["html"].get("dir") == "rtl"


def test_fa_fetches_the_catalog_once(fa):
    assert len(calls(fa["detail"], "/api/i18n/")) == 1


def test_fa_catalog_url_carries_the_version_from_chats(fa):
    v = fa["fix"]["GET /api/chats"]["ui"]["v"]
    assert calls(fa["grid"], "/api/i18n/")[0]["url"] == f"/api/i18n/fa.json?v={v}"


def test_fa_every_fetch_is_under_api(fa):
    assert [c["url"] for c in fa["detail"]["calls"] if not c["url"].startswith("/api/")] == []


def test_fa_static_texts_are_persian(fa):
    strings = fa["fix"]["GET /api/i18n/fa.json"]["strings"]
    bad = [(s["key"], s["text"]) for s in statics(fa["grid"])
           if isinstance(strings.get(s["key"]), str)
           and not re.fullmatch(pattern_rx(strings[s["key"]]), s["text"])]
    assert bad == []


def test_fa_static_aria_labels_are_persian(fa):
    strings = fa["fix"]["GET /api/i18n/fa.json"]["strings"]
    bad = [(s["label_key"], s["aria"]) for s in statics(fa["grid"], "label_key")
           if s["aria"] != strings[s["label_key"]]]
    assert bad == []


def test_fa_static_placeholders_are_persian(fa):
    strings = fa["fix"]["GET /api/i18n/fa.json"]["strings"]
    bad = [(s["placeholder_key"], s["placeholder"])
           for s in statics(fa["grid"], "placeholder_key")
           if s["placeholder"] != strings[s["placeholder_key"]]]
    assert bad == []


@pytest.mark.parametrize("view", ["grid", "detail"])
def test_fa_no_raw_key_is_shown(fa, view):
    snap = fa[view]
    shown = [x["text"] for x in snap["leaves"]] + snap["arias"] + snap["confirms"]
    assert [t for t in shown if KEYISH.search(t)] == []


@pytest.mark.parametrize("view", ["grid", "detail"])
def test_fa_no_visible_static_string_is_english(fa, view):
    leftovers = [s["key"] for s in statics(fa[view])
                 if s["text"] and s["text"] == s["markup"]["text"]
                 and re.search("[A-Za-z]{3}", s["text"])]
    assert leftovers == []


def test_fa_tile_label_keeps_latin_digits(fa):
    labels = [x["text"] for x in fa["grid"]["leaves"] if "tile-label" in x["cls"]]
    assert "web2" in labels


def test_fa_tile_label_is_an_ltr_island(fa):
    dirs = {x["dir"] for x in fa["grid"]["leaves"] if "tile-label" in x["cls"]}
    assert dirs == {"ltr"}


def test_fa_model_id_keeps_latin_digits(fa):
    metas = [x["text"] for x in fa["grid"]["leaves"] if "tile-meta" in x["cls"]]
    assert metas and all("claude-opus-5" in m for m in metas)


def test_fa_cost_uses_persian_digits(fa):
    metas = [x["text"] for x in fa["grid"]["leaves"] if "tile-meta" in x["cls"]]
    assert any("$\u06f1\u066b\u06f5\u06f0" in m for m in metas)


def test_fa_ring_percent_uses_persian_digits_and_sign(fa):
    texts = [x["text"] for x in fa["grid"]["leaves"]]
    assert "\u06f4\u06f5" in texts and "\u066a" in texts


@pytest.mark.parametrize("cls", ["tile-project", "tile-meta", "shelf-meta"])
def test_fa_model_and_folder_texts_are_ltr_islands(fa, cls):
    """entrypoints.md: model and folder texts carry dir="ltr" (a folder may
    be dir="auto", which resolves left to right for a Latin path)."""
    dirs = [x["dir"] for x in fa["grid"]["leaves"] if cls in x["cls"].split()]
    assert dirs and all(d in ("ltr", "auto") for d in dirs)


def test_fa_pulse_counts_use_persian_digits(fa):
    assert FA_DIGIT.search(text_of(fa["grid"], "grid-totals"))


def test_fa_pulse_has_no_latin_digit(fa):
    assert not LATIN_DIGIT.search(text_of(fa["grid"], "grid-totals"))


def test_fa_finished_count_uses_persian_digits(fa):
    toggle = text_of(fa["grid"], "gone-toggle")
    assert "\u06f2" in toggle and not LATIN_DIGIT.search(toggle)


def test_fa_step_numbers_use_persian_digits(fa):
    nums = [s["text"] for s in statics(fa["grid"], "num") if s["num"].isdigit()]
    assert nums == ["\u06f1", "\u06f2", "\u06f3"]


def test_fa_string_params_sit_in_isolates(fa, p4):
    assert any(p4.FSI + "web2" + p4.PDI in a for a in fa["grid"]["arias"])


def test_fa_command_in_page_text_is_isolated_and_latin(fa, p4):
    texts = [s["text"] for s in statics(fa["grid"]) if "/new" in s["text"]]
    assert texts and all(p4.FSI + "/new" + p4.PDI in t for t in texts)


def test_fa_no_direction_mark_inside_a_session_name(fa, p4):
    names = [x["text"] for x in fa["grid"]["leaves"]
             if {"tile-label", "shelf-label"} & set(x["cls"].split())]
    assert names and not any(p4.has_bidi(n) for n in names)


def test_fa_language_is_remembered(fa):
    assert fa["grid"]["storage"].get("aipager.lang") == "fa"


def test_fa_detail_name_is_an_ltr_island(fa):
    assert (fa["detail"]["ids"]["detail-label"]["text"],
            fa["detail"]["ids"]["detail-label"]["dir"]) == ("web2", "ltr")


@pytest.mark.parametrize("value", ["claude-opus-5", "/srv/web2"])
def test_fa_fact_model_and_folder_are_ltr_and_latin(fa, value):
    """The fact value is dir="ltr"; the same id shown as a setting value may
    be dir="auto" (resolves left to right for Latin text)."""
    hits = [x for x in fa["detail"]["leaves"] if x["text"] == value and x["visible"]]
    assert any(x["dir"] == "ltr" for x in hits) and all(x["dir"] in ("ltr", "auto") for x in hits)


def test_fa_working_clock_uses_persian_digits(fa):
    texts = " ".join(x["text"] for x in visible_leaves(fa["detail"]))
    assert "\u06f3:\u06f1\u06f2" in texts


def test_fa_working_clock_has_no_latin_form(fa):
    texts = " ".join(x["text"] for x in visible_leaves(fa["detail"]))
    assert "3:12" not in texts


def test_fa_queued_count_uses_persian_digits(fa):
    en = english_key({"one": "{n} queued", "other": "{n} queued"})
    form = fa["fix"]["GET /api/i18n/fa.json"]["strings"][en][plurals.category("fa", 2)]
    texts = [x["text"] for x in visible_leaves(fa["detail"])]
    assert form.replace("{n}", "\u06f2") in texts


def test_fa_detail_ring_reads_persian_percent(fa):
    texts = [x["text"] for x in visible_leaves(fa["detail"])]
    assert "\u06f4\u06f5\u066a" in texts


# ---- Russian -------------------------------------------------------------------------------

@pytest.fixture
def ru(env, p4, node, tmp_path):
    fix = with_numbers(payloads(env, p4, "ru"))
    out = run_page(node, tmp_path, boot(fix, steps=GRID_AND_DETAIL + [
        {"do": "tab", "id": "maintab-settings", "name": "settings"}]))
    out["fix"] = fix
    return out


def test_ru_runs_without_errors(ru):
    assert ru["errors"] == []


def test_ru_html_lang(ru):
    assert ru["grid"]["html"].get("lang") == "ru"


def test_ru_html_dir_stays_ltr(ru):
    assert ru["grid"]["html"].get("dir", "ltr") == "ltr"


@pytest.mark.parametrize("view", ["grid", "detail", "settings"])
def test_ru_page_text_carries_no_direction_control(ru, p4, view):
    shown = [x["text"] for x in ru[view]["leaves"] if not x["static"] or x["visible"]]
    shown += ru[view]["arias"]
    assert [t for t in shown if p4.has_bidi(t)] == []


def test_ru_static_texts_are_russian(ru):
    strings = ru["fix"]["GET /api/i18n/ru.json"]["strings"]
    bad = [(s["key"], s["text"]) for s in statics(ru["grid"])
           if isinstance(strings.get(s["key"]), str)
           and not re.fullmatch(pattern_rx(strings[s["key"]]), s["text"])]
    assert bad == []


def test_ru_numbers_stay_latin(ru):
    assert not FA_DIGIT.search(text_of(ru["grid"], "grid-totals"))


def test_ru_working_clock_is_latin(ru):
    texts = " ".join(x["text"] for x in visible_leaves(ru["detail"]))
    assert "3:12" in texts


@pytest.mark.parametrize("view", ["grid", "detail", "settings"])
def test_ru_no_raw_key_is_shown(ru, view):
    shown = [x["text"] for x in ru[view]["leaves"]] + ru[view]["arias"]
    assert [t for t in shown if KEYISH.search(t)] == []


def test_ru_settings_group_titles_keep_every_word(ru):
    """The page strips a title's leading emoji only (B.11): a Cyrillic title
    is shown whole."""
    titles = [g["title"] for g in ru["fix"]["GET /api/preferences"]["schema"]]
    want = [re.sub(r"^[^\w\s]+\s+", "", t.lstrip("\u200f")) for t in titles]
    shown = {x["text"] for x in visible_leaves(ru["settings"])}
    assert [w for w in want if w not in shown] == []


def crowd(fix, n):
    """n sessions waiting on a permission (a real row cloned), and web2's
    queue n deep."""
    fix = copy.deepcopy(fix)
    proto = next(r for r in fix["GET /api/sessions"]["sessions"] if r["label"] == "echo")
    rows = [r for r in fix["GET /api/sessions"]["sessions"] if r["status"] != "waiting"]
    for i in range(n):
        rows.append(dict(proto, label=f"w{i}", status="waiting", waiting_kind="permission",
                         waiting_summary="Bash: ls"))
    fix["GET /api/sessions"]["sessions"] = rows
    totals = fix["GET /api/sessions"]["totals"]
    totals.update(waiting=n, total=totals["total"] + n, live=totals["live"] + n)
    fix["GET /api/sessions/web2"]["queue_depth"] = n
    return fix


@pytest.fixture
def ru_base(env, p4):
    return payloads(env, p4, "ru")


@pytest.fixture
def ru_crowd(ru_base, node, tmp_path):
    def _run(n):
        out = run_page(node, tmp_path, boot(crowd(ru_base, n), steps=GRID_AND_DETAIL))
        out["strings"] = ru_base["GET /api/i18n/ru.json"]["strings"]
        return out
    return _run


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21, 22, 25])
def test_ru_need_you_pulse_uses_the_cldr_form(ru_crowd, n):
    out = ru_crowd(n)
    key = english_key({"one": "{n} needs you", "other": "{n} need you"})
    form = out["strings"][key][plurals.category("ru", n)].replace("{n}", str(n))
    assert form in text_of(out["grid"], "grid-totals")


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21, 22, 25])
def test_ru_queued_chip_uses_the_cldr_form(ru_crowd, n):
    out = ru_crowd(n)
    key = english_key({"one": "{n} queued", "other": "{n} queued"})
    form = out["strings"][key][plurals.category("ru", n)].replace("{n}", str(n))
    assert form in [x["text"] for x in visible_leaves(out["detail"])]


# ---- English: no catalog, today's text --------------------------------------------------------

@pytest.fixture
def en(env, p4, node, tmp_path):
    fix = with_numbers(payloads(env, p4, "en"))
    out = run_page(node, tmp_path, boot(fix, steps=GRID_AND_DETAIL))
    out["fix"] = fix
    return out


def test_en_runs_without_errors(en):
    assert en["errors"] == []


def test_en_fetches_no_catalog(en):
    assert calls(en["detail"], "/api/i18n/") == []


def test_en_keeps_lang_en(en):
    assert en["detail"]["html"].get("lang") == "en"


def test_en_sets_no_rtl(en):
    assert en["detail"]["html"].get("dir") in (None, "ltr")


def test_en_static_texts_are_the_markup(en):
    assert [s["key"] for s in statics(en["grid"]) if s["text"] != s["markup"]["text"]] == []


def test_en_static_aria_labels_are_the_markup(en):
    assert [s["label_key"] for s in statics(en["grid"], "label_key")
            if s["aria"] != s["markup"]["aria"]] == []


def test_en_remembers_no_language(en):
    assert "aipager.lang" not in en["grid"]["storage"]


def test_en_numbers_are_latin(en):
    assert not FA_DIGIT.search(json.dumps(en["detail"]["leaves"], ensure_ascii=False))


BASE_PAGE = HERE / "base_page_english.json"


def _dynamic(snap):
    """Script-built text. The ring's percent sign moved from CSS ``::after``
    into its own span (design E.4, drawn the same), so a "%" leaf right
    after a number is folded back into it."""
    texts = []
    for x in snap["leaves"]:
        if x["static"]:
            continue
        if x["text"] == "%" and texts and texts[-1].isdigit():
            continue
        texts.append(x["text"])
    return {"texts": texts,
            "calls": [(c["method"], c["url"]) for c in snap["calls"]],
            "arias": sorted(snap["arias"])}


def test_record_base_page(env, p4, node, tmp_path):
    """Recorder, not a check: with P4_BASE_PAGE=<base branch page.html> it
    runs that page on this file's English payloads and writes
    base_page_english.json."""
    import os
    base_page = os.environ.get("P4_BASE_PAGE")
    if not base_page:
        pytest.skip("recorder: set P4_BASE_PAGE to re-record")
    fix = with_numbers(payloads(env, p4, "en"))
    out = run_page(node, tmp_path, boot(fix, steps=GRID_AND_DETAIL),
                   page=Path(base_page).read_text(encoding="utf-8"))
    assert out["errors"] == []
    golden = {v: _dynamic(out[v]) for v in ("grid", "detail")}
    BASE_PAGE.write_text(json.dumps(golden, ensure_ascii=True, indent=1) + "\n", encoding="utf-8")


def test_en_script_built_text_equals_the_base_page(en):
    """The base branch's page, run in this harness on the same English
    payloads, built exactly this text (recorded in base_page_english.json)."""
    base = json.loads(BASE_PAGE.read_text(encoding="utf-8"))
    want = {v: list(base[v]["texts"]) for v in ("grid", "detail")}
    # The three permission modes (2026-10-11) added one fact, the session's
    # mode, before the folder; nothing else the script builds changed.
    at = want["detail"].index("Directory")
    want["detail"][at:at] = ["Mode", "💬 Ask mode"]
    assert {v: _dynamic(en[v])["texts"] for v in ("grid", "detail")} == want


def test_en_requests_equal_the_base_page(en):
    base = json.loads(BASE_PAGE.read_text(encoding="utf-8"))
    assert [list(c) for c in _dynamic(en["detail"])["calls"]] == base["detail"]["calls"]


#: Labels a later change removed on purpose: the folder picker's "+" (read
#: out as "Create this folder") became the text button "Create folder",
#: whose visible text is its name (2026-10-12).
REMOVED_ARIAS = {"Create this folder"}


def test_en_dynamic_aria_labels_equal_the_base_page(en):
    base = json.loads(BASE_PAGE.read_text(encoding="utf-8"))
    dyn = [a for a in _dynamic(en["grid"])["arias"]]
    assert dyn == [a for a in base["grid"]["arias"] if a not in REMOVED_ARIAS]


# ---- fallback: a missing translation shows English, never a key ------------------------------

@pytest.fixture
def fa_gaps(env, p4, node, tmp_path):
    fix = payloads(env, p4, "fa")
    strings = fix["GET /api/i18n/fa.json"]["strings"]
    page_keys = sorted({k for k in re.findall(r'data-i18n="([^"]+)"', static.INDEX_HTML)
                        if isinstance(strings.get(k), str) and "{" not in strings[k]})
    removed = page_keys[:4]
    for k in removed:
        del strings[k]
    removed_plural = english_key({"one": "{n} working", "other": "{n} working"})
    del strings[removed_plural]
    out = run_page(node, tmp_path, boot(fix))
    out["removed"], out["removed_plural"] = removed, removed_plural
    return out


def test_fallback_runs_without_errors(fa_gaps):
    assert fa_gaps["errors"] == []


def test_fallback_shows_english_for_a_missing_key(fa_gaps):
    en = ui_text.english_catalog()
    got = {s["key"]: s["text"] for s in statics(fa_gaps["grid"]) if s["key"] in fa_gaps["removed"]}
    assert got == {k: en[k] for k in fa_gaps["removed"]}


def test_fallback_keeps_the_page_persian_elsewhere(fa_gaps):
    assert fa_gaps["grid"]["html"].get("dir") == "rtl"


def test_fallback_never_shows_a_key(fa_gaps):
    shown = [x["text"] for x in fa_gaps["grid"]["leaves"]] + fa_gaps["grid"]["arias"]
    assert [t for t in shown if KEYISH.search(t)] == []


def test_fallback_plural_uses_the_english_form_with_persian_digits(fa_gaps):
    pulse = text_of(fa_gaps["grid"], "grid-totals")
    assert "\u06f1 working" in pulse


# ---- live switch on the App language row --------------------------------------------------------

@pytest.fixture
def live(env, p4, node, tmp_path):
    fix, after = put_answers(env, p4, payloads(env, p4, "en"))
    fixture = boot(fix, steps=[
        {"do": "wait", "name": "grid"},
        {"do": "tab", "id": "maintab-settings", "name": "settings"},
        {"do": "tap_text", "id": "settings-app-language", "text": FARSI,
         "open": "App language", "name": "fa"},
        {"do": "tap_text", "id": "settings-app-language", "text": "English",
         "open": "App language", "name": "back"},
    ])
    fixture["after_put"] = after
    out = run_page(node, tmp_path, fixture)
    out["fix"] = fix
    return out


def _puts(snap):
    return [json.loads(c["body"])["value"] for c in snap["calls"]
            if c["method"] == "PUT" and c["url"] == "/api/app-language"]


def test_live_switch_runs_without_errors(live):
    assert live["errors"] == []


def test_live_switch_puts_the_choice(live):
    assert _puts(live["fa"]) == ["fa"]


def test_live_switch_flips_dir_without_reload(live):
    assert (live["settings"]["html"].get("dir", "ltr"), live["fa"]["html"].get("dir")) == ("ltr", "rtl")


def test_live_switch_sets_lang(live):
    assert live["fa"]["html"].get("lang") == "fa"


def test_live_switch_fetches_the_catalog_once(live):
    assert len(calls(live["fa"], "/api/i18n/fa.json")) == 1


def test_live_switch_re_applies_static_text(live):
    strings = live["fix"]["GET /api/i18n/fa.json"]["strings"]
    tab = next(s for s in statics(live["fa"]) if s["key"] and s["markup"]["text"] == "Settings")
    assert tab["text"] == strings[tab["key"]]


def test_live_switch_refetches_preferences(live):
    put_at = next(c["sent"] for c in live["fa"]["calls"] if c["method"] == "PUT")
    assert [c for c in live["fa"]["calls"] if c["url"] == "/api/preferences" and c["sent"] > put_at]


def test_back_to_english_restores_ltr(live):
    assert live["back"]["html"].get("dir") in (None, "ltr")


def test_back_to_english_restores_lang(live):
    assert live["back"]["html"].get("lang") == "en"


def test_back_to_english_restores_the_markup_byte_for_byte(live):
    assert [s["key"] for s in statics(live["back"]) if s["text"] != s["markup"]["text"]] == []


def test_back_to_english_restores_aria_labels(live):
    assert [s["label_key"] for s in statics(live["back"], "label_key")
            if s["aria"] != s["markup"]["aria"]] == []


def test_back_to_english_fetches_no_catalog(live):
    assert len(calls(live["back"], "/api/i18n/")) == 1


def test_back_to_english_forgets_the_language(live):
    assert "aipager.lang" not in live["back"]["storage"]


# ---- remembered language ------------------------------------------------------------------------

def _first(snap, url):
    return next(c for c in snap["calls"] if c["url"].split("?")[0] == url)


@pytest.fixture
def remembered(env, p4, node, tmp_path):
    fix = payloads(env, p4, "fa")
    return run_page(node, tmp_path, boot(fix, storage={"aipager.lang": "fa"}))


def test_remembered_first_poll_waits_for_the_catalog(remembered):
    snap = remembered["grid"]
    cat = next(c for c in snap["calls"] if c["url"].startswith("/api/i18n/"))
    assert _first(snap, "/api/sessions")["sent"] > cat["answered"]


def test_remembered_first_poll_waits_for_chats(remembered):
    snap = remembered["grid"]
    assert _first(snap, "/api/sessions")["sent"] > _first(snap, "/api/chats")["answered"]


def test_remembered_page_is_persian(remembered):
    assert remembered["grid"]["html"].get("dir") == "rtl"


def test_without_storage_the_page_still_turns_persian(env, p4, node, tmp_path):
    out = run_page(node, tmp_path, {"fix": payloads(env, p4, "fa"), "storage": None,
                                    "steps": [{"do": "wait", "name": "grid"}]})
    assert (out["errors"], out["grid"]["html"].get("dir")) == ([], "rtl")


def test_a_failed_catalog_keeps_the_page_english(env, p4, node, tmp_path):
    fix = payloads(env, p4, "fa")
    fix["GET /api/i18n/fa.json"] = {"reject": True}
    out = run_page(node, tmp_path, boot(fix))
    assert [s["key"] for s in statics(out["grid"]) if s["text"] != s["markup"]["text"]] == []


def test_a_404_catalog_shows_no_key(env, p4, node, tmp_path):
    fix = payloads(env, p4, "fa")
    fix["GET /api/i18n/fa.json"] = {"status": 404, "body": {"error": "not_found"}}
    out = run_page(node, tmp_path, boot(fix))
    shown = [x["text"] for x in out["grid"]["leaves"]] + out["grid"]["arias"]
    assert [t for t in shown if KEYISH.search(t)] == []


# ---- hostile values never become HTML -------------------------------------------------------------

def _poison(obj):
    if isinstance(obj, dict):
        return {k: _poison(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_poison(v) for v in obj]
    if isinstance(obj, str):
        return HOSTILE + obj
    return obj


@pytest.fixture
def hostile(env, p4, node, tmp_path):
    fix = with_numbers(payloads(env, p4, "fa"))
    cat = fix["GET /api/i18n/fa.json"]
    cat["strings"] = _poison(cat["strings"])
    for key in ("GET /api/sessions/web2", "GET /api/preferences",
                "GET /api/sessions/web2/preferences"):
        body = fix[key]
        for name in ("last_message", "waiting_summary"):
            if name in body:
                body[name] = HOSTILE
        for f in body.get("facts", []):
            f["label"], f["value"] = HOSTILE + f["label"], HOSTILE + f["value"]
        for a in (body.get("actions") or {}).values():
            if a:
                a["reason"] = HOSTILE
        for g in body.get("schema", []):
            g["title"] = HOSTILE + g["title"]
            for o in g["options"]:
                o["label"], o["help"] = HOSTILE + o["label"], HOSTILE + o["help"]
    for row in fix["GET /api/sessions"]["sessions"]:
        row["waiting_summary"] = HOSTILE
        row["model"] = HOSTILE
    return run_page(node, tmp_path, boot(fix, steps=GRID_AND_DETAIL + [
        {"do": "tab", "id": "maintab-settings", "name": "settings"}]))


@pytest.mark.parametrize("view", ["grid", "detail", "settings"])
def test_no_hostile_value_is_written_as_html(hostile, view):
    assert [h for h in hostile[view]["htmls"] if "<img" in h] == []


def test_hostile_values_are_still_shown_as_text(hostile):
    assert any(HOSTILE in x["text"] for x in hostile["detail"]["leaves"])


# ---- pseudo-locale: every page string is marked ------------------------------------------------------

@pytest.fixture
def qps(env, p4, node, tmp_path, monkeypatch):
    from aipager.i18n import locales
    fix = payloads(env, p4, "en")
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    cat = env.call("GET", "/api/i18n/qps.json")
    assert cat.status == 200
    fix["GET /api/i18n/qps.json"] = cat.body
    fix["GET /api/chats"] = dict(fix["GET /api/chats"],
                                 ui={"code": "qps", "rtl": False, "v": ui_text.catalog_version("qps")})
    return run_page(node, tmp_path, boot(fix))


def test_qps_marks_every_static_string(qps):
    assert [s["key"] for s in statics(qps["grid"]) if "\u27e6" not in s["text"]] == []


def test_qps_marks_every_static_aria_label(qps):
    assert [s["label_key"] for s in statics(qps["grid"], "label_key")
            if "\u27e6" not in (s["aria"] or "")] == []


@pytest.mark.parametrize("ident", ["grid-totals", "gone-toggle"])
def test_qps_marks_script_built_text(qps, ident):
    assert "\u27e6" in text_of(qps["grid"], ident)


# ---- the chat switcher (shown with two chats or more) -------------------------------------------

@pytest.fixture
def fa_two_chats(env, p4, node, tmp_path):
    fix = payloads(env, p4, "fa")
    chats = fix["GET /api/chats"]
    chats["chats"] = chats["chats"] + [{"scope": "2", "label": "bob DM"}]
    out = run_page(node, tmp_path, boot(fix))
    out["fix"] = fix
    return out


def test_fa_chat_switcher_is_shown(fa_two_chats):
    assert fa_two_chats["grid"]["ids"]["chat-switch"]["hidden"] is False


def test_fa_chat_switcher_aria_label_is_persian(fa_two_chats, p4):
    assert p4.has_arabic_script(fa_two_chats["grid"]["ids"]["chat-switch"]["aria"])


def test_fa_chat_names_follow_their_own_direction(fa_two_chats):
    chips = [x for x in fa_two_chats["grid"]["leaves"] if x["text"] in ("team", "bob DM")]
    assert chips and all(x["dir"] == "auto" for x in chips)
