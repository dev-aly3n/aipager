"""GET /api/i18n/{code}.json?v={version} (entrypoints.md HTTP routes).

Partitions:
- caller: no initData, forged initData, outsider, read-only member, admin;
- name: enabled code (en, fa, ru), known but disabled code (de, ar), unknown
  code (zz), pseudo code with and without the test switch, malformed names
  (no suffix, wrong suffix, double suffix, upper case, traversal);
- v: current version, absent, stale, one character off, empty;
- Accept-Encoding: gzip, identity.
Error guessing: a missing translation filled with English or the raw key,
an English body that is not empty, a version that is not stable.
"""

from __future__ import annotations

import gzip
import json
import re

import pytest

from aipager.miniapp import ui_text

IMMUTABLE = "private, max-age=31536000, immutable"


def _get(env, name, *, user=None, v=None, headers=None, **kw):
    path = f"/api/i18n/{name}"
    if v is not None:
        path += f"?v={v}"
    if user is None:
        user = 2
    return env.call("GET", path, user=user, headers=headers, **kw)


# ---- auth ---------------------------------------------------------------------

def test_no_init_data_is_401(env):
    r = _get(env, "fa.json", headers={})
    assert (r.status, r.body) == (401, {"error": "unauthorized"})


def test_forged_init_data_is_401(env, p4):
    r = _get(env, "fa.json", headers={"X-Telegram-Init-Data": p4.init_data(
        p4.BOB, token="111:not-the-bot-token")})
    assert (r.status, r.body) == (401, {"error": "unauthorized"})


def test_an_outsider_is_403(env, p4):
    r = _get(env, "fa.json", user=p4.OUTSIDER)
    assert (r.status, r.body) == (403, {"error": "forbidden"})


@pytest.mark.parametrize("who", ["ADA", "BOB", "CY"])
def test_any_member_of_a_chat_gets_the_catalog(env, p4, who):
    assert _get(env, "fa.json", user=getattr(p4, who)).status == 200


# ---- which names are served ---------------------------------------------------------

@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_every_enabled_code_is_served(env, code):
    assert _get(env, f"{code}.json").status == 200


@pytest.mark.parametrize("name", [
    "ja.json",          # not one of aipager's languages
    "pl.json",          # not one of aipager's languages
    "zz.json",          # unknown
    "qps.json",         # pseudo, not switched on
    "qps-rtl.json",
    "fa",               # no suffix
    "fa.js",            # wrong suffix
    "fa.json.json",     # double suffix
    "FA.json",          # case
    "fa.JSON",
    ".json",            # empty code
    "..%2Ffa.json",     # traversal
    "fa.json%00",
])
def test_an_unknown_disabled_or_malformed_name_is_404(env, name):
    r = _get(env, name)
    assert (r.status, r.body) == (404, {"error": "not_found"})


def test_the_404_body_carries_no_detail_sentence(env):
    assert "detail" not in (_get(env, "ja.json").body or {})


def test_a_pseudo_code_is_served_once_a_test_switches_it_on(env, monkeypatch):
    from aipager.i18n import locales
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    assert _get(env, "qps.json").status == 200


def test_a_pseudo_catalog_marks_its_strings(env, monkeypatch):
    from aipager.i18n import locales
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    strings = _get(env, "qps.json").body["strings"]
    flat = [v for s in strings.values() for v in (s.values() if isinstance(s, dict) else [s])]
    assert flat and all("\u27e6" in v and "\u27e7" in v for v in flat)


# ---- body shape -----------------------------------------------------------------

def test_the_body_has_exactly_the_documented_keys(env):
    assert set(_get(env, "fa.json").body) == {
        "code", "rtl", "digits", "decimal", "group", "percent", "list", "strings"}


@pytest.mark.parametrize("code,rtl", [("en", False), ("fa", True), ("ru", False)])
def test_rtl_follows_the_language(env, code, rtl):
    assert _get(env, f"{code}.json").body["rtl"] is rtl


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_code_echoes_the_requested_language(env, code):
    assert _get(env, f"{code}.json").body["code"] == code


def test_persian_digits_are_the_extended_arabic_ones(env, p4):
    assert _get(env, "fa.json").body["digits"] == p4.FA_DIGITS


@pytest.mark.parametrize("code", ["en", "ru"])
def test_latin_languages_send_latin_digits(env, code):
    assert _get(env, f"{code}.json").body["digits"] == "0123456789"


def test_persian_percent_sign_is_the_arabic_one(env):
    assert _get(env, "fa.json").body["percent"] == "\u066a"


def test_persian_decimal_separator_is_not_a_latin_dot(env):
    assert _get(env, "fa.json").body["decimal"] != "."


def test_english_strings_are_empty(env):
    assert _get(env, "en.json").body["strings"] == {}


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_strings_are_a_subset_of_the_page_keys(env, code):
    keys = set(ui_text.english_catalog())
    assert set(_get(env, f"{code}.json").body["strings"]) <= keys


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_complete_language_has_every_page_key(env, code):
    """fa and ru are enabled, so CI holds them complete (design 8.2)."""
    assert set(_get(env, f"{code}.json").body["strings"]) == set(ui_text.english_catalog())


def test_a_russian_plural_carries_the_four_forms(env):
    en = ui_text.english_catalog()
    strings = _get(env, "ru.json").body["strings"]
    plurals = [k for k, v in en.items() if isinstance(v, dict)]
    assert plurals and all(set(strings[k]) == {"one", "few", "many", "other"} for k in plurals)


def test_a_persian_plural_carries_its_cldr_forms(env):
    en = ui_text.english_catalog()
    strings = _get(env, "fa.json").body["strings"]
    plurals = [k for k, v in en.items() if isinstance(v, dict)]
    assert all(set(strings[k]) == {"one", "other"} for k in plurals)


def test_no_value_is_the_raw_key(env):
    strings = _get(env, "fa.json").body["strings"]
    assert [k for k, v in strings.items() if v == k] == []


def test_no_catalog_value_holds_an_em_dash(env):
    for code in ("fa", "ru"):
        assert "\u2014" not in json.dumps(_get(env, f"{code}.json").body, ensure_ascii=False)


def test_the_route_serves_the_documented_catalog_function(env):
    assert _get(env, "ru.json").body == ui_text.catalog("ru")


@pytest.fixture
def full_fa_version():
    return ui_text.catalog_version("fa")


@pytest.fixture
def fa_without_one_key(monkeypatch, tmp_path):
    """A fa catalog file with one page key removed (the filesystem is the
    boundary: the catalogs directory is redirected)."""
    from aipager.i18n import core
    src = core.LOCALES_DIR
    for f in src.glob("*.jsonl"):
        (tmp_path / f.name).write_bytes(f.read_bytes())
    lines = (tmp_path / "fa.jsonl").read_text(encoding="utf-8").splitlines()
    victim = sorted(k for k in ui_text.english_catalog() if not k.startswith("ui.btn."))[0]
    kept = [ln for ln in lines if json.loads(ln)["k"] != victim]
    assert len(kept) == len(lines) - 1
    (tmp_path / "fa.jsonl").write_text("\n".join(kept) + "\n", encoding="utf-8")
    monkeypatch.setattr(core, "LOCALES_DIR", tmp_path)
    core.reset_caches()
    return victim


def test_a_missing_translation_is_left_out(env, fa_without_one_key):
    assert fa_without_one_key not in _get(env, "fa.json").body["strings"]


def test_a_missing_translation_changes_the_version(full_fa_version, fa_without_one_key):
    assert ui_text.catalog_version("fa") != full_fa_version


# ---- version and cache headers -------------------------------------------------------

@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_the_version_is_16_hex_characters(code):
    assert re.fullmatch(r"[0-9a-f]{16}", ui_text.catalog_version(code))


def test_the_version_is_stable():
    assert ui_text.catalog_version("fa") == ui_text.catalog_version("fa")


def test_fa_and_ru_have_different_versions():
    assert ui_text.catalog_version("fa") != ui_text.catalog_version("ru")


@pytest.mark.parametrize("code", ["ja", "zz", "qps"])
def test_no_version_for_a_name_that_is_not_served(code):
    assert ui_text.catalog_version(code) is None


@pytest.mark.parametrize("code", ["ja", "zz", "qps", "", "FA"])
def test_no_catalog_for_a_name_that_is_not_served(code):
    assert ui_text.catalog(code) is None


def test_the_current_version_is_cached_for_a_year(env):
    r = _get(env, "fa.json", v=ui_text.catalog_version("fa"))
    assert r.headers.get("Cache-Control") == IMMUTABLE


def test_no_version_is_not_cached(env):
    assert _get(env, "fa.json").headers.get("Cache-Control") == "no-cache"


def test_an_empty_version_is_not_cached(env):
    assert _get(env, "fa.json", v="").headers.get("Cache-Control") == "no-cache"


def test_a_stale_version_is_not_cached(env):
    r = _get(env, "fa.json", v="0000000000000000")
    assert r.headers.get("Cache-Control") == "no-cache"


def test_a_version_one_character_off_is_not_cached(env):
    v = ui_text.catalog_version("fa")
    off = v[:-1] + ("0" if v[-1] != "0" else "1")
    assert _get(env, "fa.json", v=off).headers.get("Cache-Control") == "no-cache"


def test_another_languages_version_is_not_cached(env):
    r = _get(env, "fa.json", v=ui_text.catalog_version("ru"))
    assert r.headers.get("Cache-Control") == "no-cache"


def test_a_stale_version_still_gets_the_current_body(env):
    r = _get(env, "fa.json", v="0000000000000000")
    assert r.body == ui_text.catalog("fa")


# ---- compression -------------------------------------------------------------------

def test_gzip_when_the_client_accepts_it(env, p4):
    r = _get(env, "fa.json", headers={**p4.hdr(p4.BOB), "Accept-Encoding": "gzip"},
             auto_decompress=False)
    assert r.headers.get("Content-Encoding") == "gzip"


def test_the_gzipped_body_is_the_catalog(env, p4):
    r = _get(env, "fa.json", headers={**p4.hdr(p4.BOB), "Accept-Encoding": "gzip"},
             auto_decompress=False)
    assert json.loads(gzip.decompress(r.raw).decode("utf-8")) == ui_text.catalog("fa")


def test_a_gzipped_catalog_varies_on_accept_encoding(env, p4):
    r = _get(env, "fa.json", headers={**p4.hdr(p4.BOB), "Accept-Encoding": "gzip"},
             auto_decompress=False)
    assert "accept-encoding" in r.headers.get("Vary", "").lower()


def test_no_gzip_for_identity(env, p4):
    r = _get(env, "fa.json", headers={**p4.hdr(p4.BOB), "Accept-Encoding": "identity"},
             auto_decompress=False)
    assert r.headers.get("Content-Encoding") in (None, "identity")


def test_an_identity_body_is_the_catalog(env, p4):
    r = _get(env, "fa.json", headers={**p4.hdr(p4.BOB), "Accept-Encoding": "identity"},
             auto_decompress=False)
    assert json.loads(r.raw.decode("utf-8")) == ui_text.catalog("fa")


def test_a_known_language_switched_off_is_404(env, monkeypatch):
    """A language aipager knows but has switched off is not served (every
    language is enabled since phase 5 run 3, so the test switches one off)."""
    import dataclasses

    from aipager.i18n import locales
    monkeypatch.setitem(locales.LOCALES, "ko",
                        dataclasses.replace(locales.LOCALES["ko"], enabled=False))
    r = _get(env, "ko.json")
    assert (r.status, r.body) == (404, {"error": "not_found"})
