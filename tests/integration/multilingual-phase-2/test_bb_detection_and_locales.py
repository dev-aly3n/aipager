"""Black-box: which languages are offered and that a Persian or Russian
Telegram app never switches a chat by itself (entrypoints.md
``aipager.i18n.locales``, resolvers, ``first_contact_text``; design.md
success criterion "A Persian or Russian Telegram app never switches a chat
by itself. The first-contact line never appears while the switch is off").

Partitions: Telegram language = en / en-US / fa / fa-IR / ru / de / empty /
None; stored DM choice = none / fa / ru / en; detection switch = off / on.
"""

from __future__ import annotations

import pytest

from aipager import i18n, preferences
from aipager.bot import language_menu
from aipager.i18n import locales
from tests.i18n_ml2_bb import BOB, DM_A, DM_B, GROUP, OWNER, callback, message

# Phase 5 run 1 enabled es, pt-BR, fr and it, run 2 de, ar, uk and tr, run 3
# zh-Hans, ko, id and vi, as beta.
BETA = ("es", "ru", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk", "it", "tr", "fa",
        "vi")
#: Known languages still switched off (none once every catalog lands),
#: plus Japanese and Polish, which aipager does not offer at all.
OFF = tuple(c for c, loc in locales.LOCALES.items() if not loc.enabled)
DISABLED = OFF + ("ja", "pl")


def test_exactly_english_and_the_beta_languages_are_enabled():
    assert locales.enabled_codes() == ("en",) + BETA


@pytest.mark.parametrize("code", BETA)
def test_the_enabled_translations_are_beta(code):
    assert locales.LOCALES[code].quality == "beta"


def test_persian_is_right_to_left():
    assert locales.LOCALES["fa"].rtl is True


def test_russian_is_left_to_right():
    assert not locales.LOCALES["ru"].rtl


@pytest.mark.parametrize("code", ("en",) + BETA)
def test_the_enabled_codes_are_selectable(code):
    assert locales.is_selectable(code)


@pytest.mark.parametrize("code", DISABLED)
def test_the_others_are_not_selectable(code):
    assert not locales.is_selectable(code)


def test_detection_is_on():
    # Phase 5: on, for people new since the launch marker.
    assert locales.AUTO_DETECT is True


@pytest.mark.parametrize("tag", ["fa", "fa-IR", "ru", "ru-RU", "de", "", None])
def test_detect_ignores_non_english_for_someone_who_does_not_follow(tag):
    assert locales.detect(tag, follow=False) is None


@pytest.mark.parametrize("tag", ["fa", "fa-IR", "ru", "ru-RU", "de", "", None])
def test_detect_ignores_non_english_while_off(tag, monkeypatch):
    monkeypatch.setattr(locales, "AUTO_DETECT", False)
    assert locales.detect(tag) is None


@pytest.mark.parametrize("tag", ["en", "en-US", "EN-gb"])
def test_detect_keeps_english(tag):
    assert locales.detect(tag) == "en"


@pytest.mark.parametrize("tag, want", [("fa-IR", "fa"), ("ru", "ru"), ("fa", "fa")])
def test_normalize_keeps_its_meaning(tag, want):
    assert locales.normalize(tag) == want


@pytest.mark.parametrize("tag, want", [("fa", "fa"), ("ru-RU", "ru"), ("en", "en")])
def test_the_switch_turns_detection_on(monkeypatch, tag, want):
    monkeypatch.setattr(locales, "AUTO_DETECT", True)
    assert locales.detect(tag) == want


# ---- resolvers -------------------------------------------------------------------

@pytest.mark.parametrize("tag", ["fa", "ru", "fa-IR"])
def test_a_private_chat_stays_english_for_a_non_english_telegram_app(tag):
    tr = i18n.for_update(message("hi", chat=DM_A, user=OWNER, language_code=tag))
    assert tr.code == "en"


@pytest.mark.parametrize("tag", ["fa", "ru"])
def test_background_messages_stay_english_after_a_non_english_app_was_seen(tag):
    i18n.for_update(message("hi", chat=DM_A, user=OWNER, language_code=tag))
    assert i18n.for_chat(DM_A).code == "en"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_stored_choice_is_used(code):
    preferences.set_ui_language(DM_A, code)
    assert i18n.for_chat(DM_A).code == code


@pytest.mark.parametrize("tag", ["fa", "ru"])
def test_the_mini_app_viewer_default_stays_english(tag):
    assert i18n.for_viewer(OWNER, tag).code == "en"


def test_the_mini_app_viewer_gets_a_stored_choice():
    preferences.set_ui_language(OWNER, "ru")
    assert i18n.for_viewer(OWNER, "fa").code == "ru"


def test_for_person_prefers_the_tappers_stored_dm_language():
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(DM_B, "ru")
    update, _q = callback("_:set", chat=GROUP, user=BOB, language_code="fa")
    assert i18n.for_person(update).code == "ru"


def test_for_person_falls_back_to_the_chat_for_a_non_english_app():
    preferences.set_ui_language(GROUP, "ru")
    update, _q = callback("_:set", chat=GROUP, user=BOB, language_code="fa")
    assert i18n.for_person(update).code == "ru"


def test_for_person_uses_an_english_telegram_app():
    preferences.set_ui_language(GROUP, "fa")
    update, _q = callback("_:set", chat=GROUP, user=BOB, language_code="en")
    assert i18n.for_person(update).code == "en"


# ---- the first-contact line ------------------------------------------------------

# Phase 5: the line is its own message, sent once by the language launch to
# a chat new since the marker (tests/test_language_launch.py); here only its
# words.

@pytest.mark.parametrize("code, name", [("fa", "فارسی"), ("ru", "Русский")])
def test_first_contact_text_names_the_language(code, name):
    line = language_menu.first_contact_text(i18n.get(code))
    assert name in line and "/language" in line


def test_the_start_screen_never_carries_the_line(mk_bot, run_async):
    from tests.i18n_ml2_bb import make_bot
    from aipager.i18n import persist
    persist.ensure_launch_marker(people=(), prior_run=False)
    bot = make_bot(mk_bot)
    update = message("/start", chat=DM_A, user=OWNER, language_code="ru")
    run_async(bot._handle_start_cmd(update, None))
    assert not any("/language" in t for t in _sent_texts(bot, update))


def _sent_texts(bot, update):
    out = []
    for c in update.message.reply_text.await_args_list:
        out.append(c.args[0] if c.args else c.kwargs.get("text", ""))
    for name in ("send_message", "edit_message_text"):
        for c in getattr(bot._app.bot, name).await_args_list:
            out.append(c.kwargs.get("text") or (c.args[1] if len(c.args) > 1 else ""))
    return [t for t in out if isinstance(t, str)]


def test_a_persian_app_start_gets_no_first_contact_line(mk_bot, run_async):
    """/start from a Persian Telegram app in a fresh private chat: English,
    and no stored language."""
    from tests.i18n_ml2_bb import PERSIAN, make_bot
    bot = make_bot(mk_bot)
    update = message("/start", chat=DM_A, user=OWNER, language_code="fa")
    run_async(bot._handle_start_cmd(update, None))
    texts = _sent_texts(bot, update)
    assert texts and not any(PERSIAN.search(t) for t in texts)


def test_a_persian_app_start_stores_nothing(mk_bot, run_async):
    from tests.i18n_ml2_bb import make_bot
    bot = make_bot(mk_bot)
    run_async(bot._handle_start_cmd(message("/start", chat=DM_A, user=OWNER,
                                        language_code="fa"), None))
    assert preferences.get_ui_language(DM_A) in (None, "")
