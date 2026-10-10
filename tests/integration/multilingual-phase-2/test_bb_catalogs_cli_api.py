"""Black-box: the shipped Persian and Russian catalogs, the dev CLI's
verdict on them, the hook's import closure, and the Mini App's App
language route with fa and ru (design.md success criteria "extract
--check and check exit 0 ... fa.jsonl and ru.jsonl hold every key",
"The hook never loads aipager.i18n"; entrypoints.md "Files (observable)",
"CLI commands", "HTTP routes").

Every subprocess gets a throwaway HOME and must leave the catalogs
byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

from aipager import preferences
from tests.i18n_ml2_bb import (
    BIDI_CONTROLS,
    BOB,
    CATALOGS,
    EM_DASH,
    GROUP,
    REPO,
    RLM,
    catalog,
    init_header,
    source_rows,
)

# Phase 5 run 1 enabled es, pt-BR, fr and it (beta, complete catalogs).
DISABLED = ("zh-Hans", "de", "ko", "ar", "id", "uk", "tr", "vi")
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
FORMS = {"fa": {"one", "other"}, "ru": {"one", "few", "many", "other"}}


def _source_values(row):
    if "en" in row:
        return [row["en"]]
    return [row[f] for f in ("one", "other") if f in row]


def _values(v):
    return list(v.values()) if isinstance(v, dict) else [v]


# ---- the catalog files ---------------------------------------------------------------

@pytest.mark.parametrize("code", ["fa", "ru", "es", "pt-BR", "fr", "it"])
def test_the_catalog_holds_exactly_the_source_keys(code):
    assert set(catalog(code)) == {r["k"] for r in source_rows()}


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_catalog_is_sorted_by_key(code):
    lines = (CATALOGS / f"{code}.jsonl").read_text(encoding="utf-8").splitlines()
    keys = [json.loads(x)["k"] for x in lines if x.strip()]
    assert keys == sorted(keys)


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_every_catalog_line_has_k_v_src(code):
    assert all(set(r) == {"k", "v", "src"} for r in catalog(code).values())


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_no_em_dash_in_any_translation(code):
    bad = [k for k, r in catalog(code).items() if any(EM_DASH in x for x in _values(r["v"]))]
    assert bad == []


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_no_raw_bidi_control_in_any_translation(code):
    bad = [k for k, r in catalog(code).items()
           if any(BIDI_CONTROLS.search(x) for x in _values(r["v"]))]
    assert bad == []


def test_no_right_arrow_in_persian():
    bad = [k for k, r in catalog("fa").items() if any("→" in x for x in _values(r["v"]))]
    assert bad == []


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_plural_values_have_the_languages_forms(code):
    plural_keys = {r["k"] for r in source_rows() if "en" not in r}
    bad = [k for k in plural_keys if set(catalog(code)[k]["v"]) != FORMS[code]]
    assert plural_keys and bad == []


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_every_translation_keeps_the_source_placeholders(code):
    cat = catalog(code)
    bad = []
    for row in source_rows():
        want = set().union(*(PLACEHOLDER.findall(x) for x in _source_values(row)))
        for value in _values(cat[row["k"]]["v"]):
            got = set(PLACEHOLDER.findall(value))
            # a plural "one" form may spell the count out; every other name stays
            if got - want or (want - got) - {"n"}:
                bad.append(row["k"])
    assert bad == []


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_html_tags_are_kept(code):
    cat = catalog(code)
    tag = re.compile(r"</?[a-z]+>")
    bad = [r["k"] for r in source_rows() if "en" in r
           and sorted(tag.findall(r["en"])) != sorted(tag.findall(cat[r["k"]]["v"]))]
    assert bad == []


@pytest.mark.parametrize("code", DISABLED)
def test_the_other_catalogs_stay_empty(code):
    assert (CATALOGS / f"{code}.jsonl").read_text(encoding="utf-8").strip() == ""


# ---- the CLI ----------------------------------------------------------------------

def _env(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (tmp_path / "inst").mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "AIPAGER", "XDG_"))}
    env.update(PYTHONPATH=str(REPO), HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               AIPAGER_INSTANCE_DIR=str(tmp_path / "inst"), TMPDIR=str(tmp_path))
    return env


def _digest():
    h = hashlib.sha256()
    for sub in ("aipager/i18n/locales", "tools/i18n"):
        for p in sorted((REPO / sub).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                h.update(str(p).encode())
                h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def cli(tmp_path):
    before = _digest()

    def _run(*args):
        return subprocess.run([sys.executable, str(REPO / "tools" / "i18n.py"), *args],
                              capture_output=True, text=True, env=_env(tmp_path),
                              cwd=str(REPO), timeout=300)
    yield _run
    assert _digest() == before, "the CLI changed the shipped catalogs"


def test_extract_check_passes(cli):
    r = cli("extract", "--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_passes_with_persian_and_russian_enabled(cli):
    r = cli("check")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


@pytest.mark.parametrize("code", ["fa", "ru", "es", "pt-BR", "fr", "it"])
def test_todo_is_empty_for_a_complete_language(cli, code):
    r = cli("todo", "--lang", code)
    assert r.returncode == 0 and json.loads(r.stdout)["entries"] == []


def test_todo_lists_every_key_for_german(cli):
    r = cli("todo", "--lang", "de")
    entries = json.loads(r.stdout)["entries"]
    assert len(entries) == len(source_rows())


# ---- the hook's import closure ------------------------------------------------------

@pytest.mark.parametrize("module", ["aipager.dtach.notify_hook",
                                    "aipager.dtach.statusline_notify"])
def test_the_hook_binaries_do_not_load_i18n(tmp_path, module):
    code = (f"import sys, {module}\n"
            "print(sorted(m for m in sys.modules if m.startswith('aipager.i18n')"
            " or m == 'aipager.bot.card_rows'))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=_env(tmp_path), cwd=str(tmp_path), timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == "[]", r.stdout + r.stderr


# ---- the Mini App route -------------------------------------------------------------

@pytest.fixture
def http(mk_bot, run_async, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from aipager.miniapp.server import MiniAppServer
    from tests.i18n_ml2_bb import BOT_TOKEN, make_bot
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    bot = make_bot(mk_bot)
    server = MiniAppServer(bot, bot.registry, port=8765)

    def _call(method, path, *, user_id=BOB, language_code=None, **kw):
        async def _go():
            client = TestClient(TestServer(server._build_app()))
            await client.start_server()
            try:
                resp = await client.request(method, path, headers=init_header(
                    user_id, language_code=language_code), **kw)
                text = await resp.text()
                try:
                    return resp.status, json.loads(text)
                except ValueError:
                    return resp.status, text
            finally:
                await client.close()
        return run_async(_go())
    return _call


def _options(body):
    return body["app_language"]["options"]


def test_the_options_are_auto_english_and_the_beta_languages(http):
    _s, body = http("GET", "/api/preferences")
    assert [o["value"] for o in _options(body)] == ["auto", "en", "es", "ru", "pt-BR", "fr",
                                                    "it", "fa"]


def test_the_option_labels_are_endonyms_without_beta(http):
    _s, body = http("GET", "/api/preferences")
    labels = {o["value"]: o["label"] for o in _options(body)}
    assert (labels["ru"], labels["fa"]) == ("Русский",
                                            "فارسی")


@pytest.mark.parametrize("tag", ["fa", "ru"])
def test_a_persian_or_russian_viewer_with_no_choice_is_english(http, tag):
    _s, body = http("GET", "/api/preferences", language_code=tag)
    assert body["app_language"]["effective"] == "en"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_putting_persian_or_russian_is_accepted(http, code):
    status, body = http("PUT", "/api/app-language", json={"value": code})
    assert status == 200 and body["effective"] == code


def test_putting_persian_writes_the_callers_private_chat(http):
    http("PUT", "/api/app-language", json={"value": "fa"})
    assert preferences.get_ui_language(BOB) == "fa"


def test_putting_persian_leaves_the_group_alone(http):
    http("PUT", "/api/app-language", json={"value": "fa"})
    assert preferences.get_ui_language(GROUP) in (None, "")


def test_the_persian_confirmation_reads_right_to_left(http):
    _s, body = http("PUT", "/api/app-language", json={"value": "fa"})
    assert body["confirmation"].startswith(RLM)


def test_the_russian_confirmation_has_no_marks(http):
    _s, body = http("PUT", "/api/app-language", json={"value": "ru"})
    assert not BIDI_CONTROLS.search(body["confirmation"])


def test_the_title_follows_the_viewers_choice(http):
    http("PUT", "/api/app-language", json={"value": "ru"})
    _s, body = http("GET", "/api/preferences")
    assert re.search("[Ѐ-ӿ]", body["app_language"]["title"])


@pytest.mark.parametrize("value", DISABLED + ("qps", "qps-rtl", "FA", "fa-IR", "", "xx"))
def test_a_disabled_or_unknown_code_is_400(http, value):
    status, _b = http("PUT", "/api/app-language", json={"value": value})
    assert status == 400


@pytest.mark.parametrize("value", ["de", "qps"])
def test_a_refused_code_writes_nothing(http, value):
    http("PUT", "/api/app-language", json={"value": value})
    assert preferences.get_ui_language(BOB) in (None, "")


def test_bad_json_is_400(http):
    status, _b = http("PUT", "/api/app-language", data=b"{not json")
    assert status == 400
