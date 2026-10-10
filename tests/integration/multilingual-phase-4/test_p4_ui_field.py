"""The ``ui`` entry on GET /api/chats, GET /api/preferences and PUT
/api/app-language (entrypoints.md: present only when the viewer's language is
not English; ``{"code", "rtl", "v"}``, ``v`` = the catalog version).

Partitions: viewer language = none stored, en stored, fa stored, ru stored;
Telegram language_code fa with nothing stored (detection is off); a language
stored only for the group being looked at (never the viewer's); PUT to en,
auto, fa, ru from each side.
"""

from __future__ import annotations

import pytest

from aipager import i18n
from aipager.miniapp import ui_text

ROUTES = [("GET", "/api/chats"), ("GET", "/api/preferences")]


def _ui(code):
    return {"code": code, "rtl": code == "fa", "v": ui_text.catalog_version(code)}


@pytest.mark.parametrize("method,path", ROUTES)
def test_no_ui_for_a_viewer_with_nothing_stored(env, method, path):
    assert "ui" not in env.call(method, path).body


@pytest.mark.parametrize("method,path", ROUTES)
def test_no_ui_for_a_viewer_who_chose_english(env, p4, method, path):
    p4.set_lang(p4.BOB, "en")
    assert "ui" not in env.call(method, path).body


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("code", ["fa", "ru"])
def test_ui_for_a_viewer_who_chose_a_language(env, p4, method, path, code):
    p4.set_lang(p4.BOB, code)
    assert env.call(method, path).body["ui"] == _ui(code)


@pytest.mark.parametrize("method,path", ROUTES)
def test_telegrams_language_alone_gives_no_ui(env, p4, method, path):
    """Auto-detection is phase 5: a Persian Telegram app is still English."""
    r = env.call(method, path, headers=p4.hdr(p4.BOB, language_code="fa"))
    assert "ui" not in r.body


@pytest.mark.parametrize("method,path", ROUTES)
def test_the_groups_language_is_not_the_viewers(env, p4, method, path):
    p4.set_lang(p4.GROUP, "fa")
    assert "ui" not in env.call(method, path, scope=p4.GROUP).body


@pytest.mark.parametrize("method,path", ROUTES)
def test_the_viewers_language_holds_whichever_chat_they_look_at(env, p4, method, path):
    p4.set_lang(p4.BOB, "ru")
    p4.set_lang(p4.GROUP, "fa")
    assert env.call(method, path, scope=p4.GROUP).body["ui"]["code"] == "ru"


def test_each_viewer_gets_their_own_language(env, p4):
    p4.set_lang(p4.BOB, "ru")
    p4.set_lang(p4.ADA, "fa")
    assert env.call("GET", "/api/chats", user=p4.ADA).body["ui"]["code"] == "fa"


def test_an_english_viewers_chats_body_is_unchanged_by_another_viewers_choice(env, p4):
    before = env.call("GET", "/api/chats").body
    p4.set_lang(p4.ADA, "fa")
    assert env.call("GET", "/api/chats").body == before


# ---- PUT /api/app-language ---------------------------------------------------------

@pytest.mark.parametrize("code", ["fa", "ru"])
def test_put_a_language_answers_with_its_ui(env, code):
    r = env.call("PUT", "/api/app-language", json={"value": code})
    assert r.body["ui"] == _ui(code)


@pytest.mark.parametrize("value", ["en", "auto"])
def test_put_english_or_auto_from_persian_has_no_ui(env, p4, value):
    p4.set_lang(p4.BOB, "fa")
    r = env.call("PUT", "/api/app-language", json={"value": value})
    assert "ui" not in r.body


def test_put_english_body_is_exactly_todays(env, p4):
    p4.set_lang(p4.BOB, "fa")
    r = env.call("PUT", "/api/app-language", json={"value": "en"})
    assert r.body == {
        "value": "en", "effective": "en",
        "confirmation": "\u2705 aipager now speaks English here. Your sessions and "
                        "Claude's answers are unchanged."}


def test_put_russian_from_persian_carries_the_new_language(env, p4):
    p4.set_lang(p4.BOB, "fa")
    r = env.call("PUT", "/api/app-language", json={"value": "ru"})
    assert r.body["ui"]["code"] == "ru"


def test_put_persian_is_confirmed_in_persian(env, p4):
    r = env.call("PUT", "/api/app-language", json={"value": "fa"})
    assert p4.has_arabic_script(r.body["confirmation"])


@pytest.mark.parametrize("value", ["ja", "qps", "zz", 5, None])
def test_a_refused_put_carries_no_ui(env, value):
    r = env.call("PUT", "/api/app-language", json={"value": value})
    assert r.status == 400 and "ui" not in (r.body or {})


def test_after_put_the_chats_route_follows(env):
    env.call("PUT", "/api/app-language", json={"value": "ru"})
    assert env.call("GET", "/api/chats").body["ui"]["code"] == "ru"


def test_ui_v_names_a_catalog_the_route_caches(env, p4):
    p4.set_lang(p4.BOB, "fa")
    v = env.call("GET", "/api/chats").body["ui"]["v"]
    r = env.call("GET", f"/api/i18n/fa.json?v={v}")
    assert r.headers.get("Cache-Control") == "private, max-age=31536000, immutable"


# ---- ui_payload --------------------------------------------------------------------

def test_ui_payload_is_none_for_english():
    assert ui_text.ui_payload(i18n.get("en")) is None


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_ui_payload_for_a_language(code):
    assert ui_text.ui_payload(i18n.get(code)) == _ui(code)


# ---- chat labels -------------------------------------------------------------------

@pytest.fixture
def unlabelled(env):
    """The group carries no label in aipager.yaml, so the route's fallback
    word is shown."""
    from aipager.scope import Member, Scope
    env.bot.scopes = [
        Scope(chat_id=-1001, kind="group", label="", members=(
            Member(id=555, label="ada", role="owner"),
            Member(id=2, label="bob", role="admin"),
        )),
        Scope(chat_id=2, kind="dm", label="", members=(
            Member(id=2, label="bob", role="owner"),)),
    ]
    return env


def _labels(env):
    return [c["label"] for c in env.call("GET", "/api/chats").body["chats"]]


def test_fallback_chat_labels_are_english_for_an_english_viewer(unlabelled):
    assert sorted(_labels(unlabelled)) == ["DM", "Group"]


@pytest.mark.parametrize("code,check", [("fa", "has_arabic_script"), ("ru", "has_cyrillic")])
def test_fallback_chat_labels_follow_the_viewer(unlabelled, p4, code, check):
    p4.set_lang(p4.BOB, code)
    assert all(getattr(p4, check)(x) for x in _labels(unlabelled))


def test_a_configured_chat_label_is_a_name_and_never_translated(env, p4):
    p4.set_lang(p4.BOB, "fa")
    assert [c["label"] for c in env.call("GET", "/api/chats").body["chats"]] == ["team"]
