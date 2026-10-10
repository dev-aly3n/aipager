"""A catalog that answers late, or after a newer choice, never wins (design.md
success criterion "Choosing a language in App language changes the open page
at once ... Choosing English restores today's page exactly"; entrypoints.md
Page contract: the page shows the language the server's ``ui`` names, and
``localStorage["aipager.lang"]`` holds the last non-English code).

The node harness (bb_i18n.js) answers a fixture response after ``delay`` ms,
so a slow catalog can arrive after the person has already picked another
language. Every payload is a real server answer for viewer BOB.

Partitions:
- live switch en -> fa (slow) -> en: the late Persian catalog arrives while
  the page is English;
- live switch en -> fa (slow) -> ru (fast): the Persian catalog arrives after
  the Russian one;
- reopen with fa remembered while the server now says ru, or now says
  English (no ``ui``), with the remembered catalog slow.
Each scenario has a precondition test proving the slow catalog was answered
before the last snapshot (otherwise the scenario would prove nothing).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
RUSSIAN = "\u0420\u0443\u0441\u0441\u043a\u0438\u0439"   # the endonym on the App language row
SLOW = 1500
SETTLE = 3000


def _page_runs():
    name = "p4_page_runs_helpers"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / "test_p4_page_runs.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


PR = _page_runs()
FARSI = PR.FARSI


@pytest.fixture(scope="module")
def node():
    import shutil
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _slow(fix, code, ms=SLOW):
    path = "GET /api/i18n/" + code + ".json"
    fix[path] = {"status": 200, "body": fix[path], "delay": ms}


def _put_answers(env, p4, fix, values):
    """Real PUT /api/app-language answers and what the server says after each."""
    after = {}
    for value in values:
        r = env.call("PUT", "/api/app-language", json={"value": value})
        assert r.status == 200, r.body
        fix["PUT /api/app-language " + value] = r.body
        after[value] = {"GET " + path: env.call("GET", path, scope=p4.GROUP).body
                        for path in ("/api/preferences", "/api/chats")}
    return fix, after


def _switch(env, p4, node, tmp_path, second, second_text):
    """English page; pick Persian (its catalog slow), then at once *second*."""
    fix = PR.payloads(env, p4, "en")
    fix, after = _put_answers(env, p4, fix, ["fa", second] if second == "en" else ["fa", second, "en"])
    _slow(fix, "fa")
    fixture = PR.boot(fix, steps=[
        {"do": "wait", "name": "grid"},
        {"do": "tab", "id": "maintab-settings", "name": "settings"},
        {"do": "tap_text", "id": "settings-app-language", "text": FARSI,
         "open": "App language", "name": "fa_pending"},
        {"do": "tap_text", "id": "settings-app-language", "text": second_text,
         "name": "second"},
        {"do": "wait", "ms": SETTLE, "name": "late"},
    ])
    fixture["after_put"] = after
    out = PR.run_page(node, tmp_path, fixture)
    out["fix"] = fix
    return out


def _fa_catalog(snap):
    return [c for c in snap["calls"] if c["url"].split("?")[0] == "/api/i18n/fa.json"]


def _persian_leaves(snap, p4):
    """Visible text in Persian script other than the language row's own endonym."""
    return [x["text"] for x in PR.visible_leaves(snap)
            if p4.has_arabic_script(x["text"]) and x["text"].strip() != FARSI]


def _changed_statics(snap):
    return [s["key"] for s in PR.statics(snap) if s["text"] != s["markup"]["text"]]


def _second_pick_answered(out):
    """When the page heard the answer to the person's second pick."""
    puts = [c for c in out["late"]["calls"] if c["method"] == "PUT"]
    return puts[1]["answered"]


def _chats_answered(out):
    return next(c["answered"] for c in out["late"]["calls"] if c["url"] == "/api/chats")


def _html_writes_after(out, clock):
    """lang/dir values written on <html> after *clock*, even for a moment."""
    return [(k, v) for k, v, at in out["late"]["html_log"] if at > clock]


def _remembered_after(out, clock):
    return [v for op, k, v, at in out["late"]["storage_log"]
            if op == "set" and k == "aipager.lang" and at > clock]


# ---- en -> fa (slow) -> en ---------------------------------------------------------------

@pytest.fixture
def back_to_en(env, p4, node, tmp_path):
    return _switch(env, p4, node, tmp_path, "en", "English")


def test_late_fa_then_en_runs_without_errors(back_to_en):
    assert back_to_en["errors"] == []


def test_late_fa_then_en_catalog_was_still_pending_when_english_was_picked(back_to_en):
    assert [c["answered"] for c in _fa_catalog(back_to_en["second"])] == [None]


def test_late_fa_then_en_catalog_arrived_before_the_last_look(back_to_en):
    assert all(c["answered"] is not None for c in _fa_catalog(back_to_en["late"]))


def test_late_fa_then_en_keeps_lang_en(back_to_en):
    assert back_to_en["late"]["html"].get("lang") == "en"


def test_late_fa_then_en_keeps_ltr(back_to_en):
    assert back_to_en["late"]["html"].get("dir") in (None, "ltr")


def test_late_fa_then_en_keeps_the_markup_english(back_to_en):
    assert _changed_statics(back_to_en["late"]) == []


def test_late_fa_then_en_shows_no_persian_text(back_to_en, p4):
    assert _persian_leaves(back_to_en["late"], p4) == []


def test_late_fa_then_en_remembers_no_language(back_to_en):
    assert "aipager.lang" not in back_to_en["late"]["storage"]


def test_late_fa_then_en_never_shows_lang_fa_after_english_was_picked(back_to_en):
    after = _html_writes_after(back_to_en, _second_pick_answered(back_to_en))
    assert ("lang", "fa") not in after


def test_late_fa_then_en_never_turns_rtl_after_english_was_picked(back_to_en):
    after = _html_writes_after(back_to_en, _second_pick_answered(back_to_en))
    assert ("dir", "rtl") not in after


def test_late_fa_then_en_never_remembers_fa_after_english_was_picked(back_to_en):
    assert "fa" not in _remembered_after(back_to_en, _second_pick_answered(back_to_en))


# ---- en -> fa (slow) -> ru ---------------------------------------------------------------

@pytest.fixture
def fa_then_ru(env, p4, node, tmp_path):
    return _switch(env, p4, node, tmp_path, "ru", RUSSIAN)


def test_late_fa_then_ru_runs_without_errors(fa_then_ru):
    assert fa_then_ru["errors"] == []


def test_late_fa_then_ru_catalog_arrived_before_the_last_look(fa_then_ru):
    assert all(c["answered"] is not None for c in _fa_catalog(fa_then_ru["late"]))


def test_late_fa_then_ru_ru_catalog_answered_first(fa_then_ru):
    snap = fa_then_ru["late"]
    ru = [c["answered"] for c in snap["calls"] if c["url"].split("?")[0] == "/api/i18n/ru.json"]
    assert ru and max(ru) < min(c["answered"] for c in _fa_catalog(snap))


def test_late_fa_then_ru_ends_lang_ru(fa_then_ru):
    assert fa_then_ru["late"]["html"].get("lang") == "ru"


def test_late_fa_then_ru_ends_ltr(fa_then_ru):
    assert fa_then_ru["late"]["html"].get("dir") in (None, "ltr")


def test_late_fa_then_ru_static_text_is_russian(fa_then_ru):
    strings = fa_then_ru["fix"]["GET /api/i18n/ru.json"]["strings"]
    tab = next(s for s in PR.statics(fa_then_ru["late"]) if s["markup"]["text"] == "Settings")
    assert tab["text"] == strings[tab["key"]]


def test_late_fa_then_ru_shows_no_persian_text(fa_then_ru, p4):
    assert _persian_leaves(fa_then_ru["late"], p4) == []


def test_late_fa_then_ru_remembers_ru(fa_then_ru):
    assert fa_then_ru["late"]["storage"].get("aipager.lang") == "ru"


def test_late_fa_then_ru_never_shows_lang_fa_after_russian_was_picked(fa_then_ru):
    after = _html_writes_after(fa_then_ru, _second_pick_answered(fa_then_ru))
    assert ("lang", "fa") not in after


def test_late_fa_then_ru_never_turns_rtl_after_russian_was_picked(fa_then_ru):
    after = _html_writes_after(fa_then_ru, _second_pick_answered(fa_then_ru))
    assert ("dir", "rtl") not in after


def test_late_fa_then_ru_never_remembers_fa_after_russian_was_picked(fa_then_ru):
    assert "fa" not in _remembered_after(fa_then_ru, _second_pick_answered(fa_then_ru))


# ---- reopen with fa remembered, the server now says something else ------------------------

def _reopen(env, p4, node, tmp_path, now):
    fix = PR.payloads(env, p4, now)   # holds the fa and ru catalogs either way
    _slow(fix, "fa", 800)
    out = PR.run_page(node, tmp_path, PR.boot(fix, storage={"aipager.lang": "fa"}, steps=[
        {"do": "wait", "name": "grid"},
        {"do": "wait", "ms": SETTLE, "name": "late"},
    ]))
    out["fix"] = fix
    return out


@pytest.fixture
def reopen_ru(env, p4, node, tmp_path):
    return _reopen(env, p4, node, tmp_path, "ru")


@pytest.fixture
def reopen_en(env, p4, node, tmp_path):
    return _reopen(env, p4, node, tmp_path, "en")


def test_reopen_fa_remembered_ru_now_runs_without_errors(reopen_ru):
    assert reopen_ru["errors"] == []


def test_reopen_fa_remembered_ru_now_ends_lang_ru(reopen_ru):
    assert reopen_ru["late"]["html"].get("lang") == "ru"


def test_reopen_fa_remembered_ru_now_ends_ltr(reopen_ru):
    assert reopen_ru["late"]["html"].get("dir") in (None, "ltr")


def test_reopen_fa_remembered_ru_now_shows_no_persian_text(reopen_ru, p4):
    assert _persian_leaves(reopen_ru["late"], p4) == []


def test_reopen_fa_remembered_ru_now_remembers_ru(reopen_ru):
    assert reopen_ru["late"]["storage"].get("aipager.lang") == "ru"


def test_reopen_fa_remembered_en_now_runs_without_errors(reopen_en):
    assert reopen_en["errors"] == []


def test_reopen_fa_remembered_en_now_ends_lang_en(reopen_en):
    assert reopen_en["late"]["html"].get("lang") == "en"


def test_reopen_fa_remembered_en_now_ends_ltr(reopen_en):
    assert reopen_en["late"]["html"].get("dir") in (None, "ltr")


def test_reopen_fa_remembered_en_now_ends_with_the_markup_english(reopen_en):
    assert _changed_statics(reopen_en["late"]) == []


def test_reopen_fa_remembered_en_now_shows_no_persian_text(reopen_en, p4):
    assert _persian_leaves(reopen_en["late"], p4) == []


@pytest.mark.parametrize("which", ["reopen_ru", "reopen_en"])
def test_reopen_never_shows_lang_fa_once_the_server_named_another(request, which):
    out = request.getfixturevalue(which)
    assert ("lang", "fa") not in _html_writes_after(out, _chats_answered(out))


@pytest.mark.parametrize("which", ["reopen_ru", "reopen_en"])
def test_reopen_never_turns_rtl_once_the_server_named_another(request, which):
    out = request.getfixturevalue(which)
    assert ("dir", "rtl") not in _html_writes_after(out, _chats_answered(out))


@pytest.mark.parametrize("which", ["reopen_ru", "reopen_en"])
def test_reopen_never_remembers_fa_once_the_server_named_another(request, which):
    out = request.getfixturevalue(which)
    assert "fa" not in _remembered_after(out, _chats_answered(out))
