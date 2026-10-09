"""Black-box: the /language list with its beta mark and what a language
switch sends (entrypoints.md ``language_menu``, "Callbacks"; design.md
success criterion "/language lists Automatic (private chats), English,
Русский (beta), فارسی (beta), « Back. A pick re-registers the menu once,
sends one keyboard and sets the menu button once").

Partitions: chat = private / group; current = en / ru / fa; tapper = admin
/ ordinary member; code = enabled change / same as stored / disabled (12
codes) / pseudo / unknown. Error guessing: double refresh, sends on a
no-op tap, a write on refusal, the menu button left in the old language.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import kbwords, language_menu
from tests.i18n_ml2_bb import (
    BOB,
    CYRILLIC,
    DM_A,
    GROUP,
    OWNER,
    PERSIAN,
    RLM,
    add_session,
    button_texts,
    callback,
    catalog,
    edited_text,
    make_bot,
    reply_keyboards,
    toast_texts,
)

DISABLED = ("es", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk", "it",
            "tr", "vi")


def _rows(tr, private, current):
    _text, markup = language_menu.render_language_menu(tr, private=private, current=current)
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


# ---- the list --------------------------------------------------------------------

def test_the_private_list_in_english():
    assert _rows(i18n.get("en"), True, "en") == [
        ("Automatic (your Telegram language)", "_:set:lang:auto"),
        ("English ✅", "_:set:lang:en"),
        ("Русский (beta)", "_:set:lang:ru"),
        ("فارسی (beta)", "_:set:lang:fa"),
        ("« Back", "_:set:back"),
    ]


def test_the_group_list_has_no_automatic_row():
    data = [d for _t, d in _rows(i18n.get("en"), False, "en")]
    assert data == ["_:set:lang:en", "_:set:lang:ru", "_:set:lang:fa", "_:set:back"]


def test_the_current_language_gets_the_check():
    rows = dict((d, t) for t, d in _rows(i18n.get("en"), True, "ru"))
    assert rows["_:set:lang:ru"].endswith(" ✅") and not rows["_:set:lang:en"].endswith("✅")


def test_english_never_gets_a_beta_mark_in_any_language():
    for code in ("en", "ru", "fa"):
        rows = dict((d, t) for t, d in _rows(i18n.get(code), True, "auto"))
        assert rows["_:set:lang:en"].replace(RLM, "") == "English", code


def test_the_beta_mark_is_in_the_viewers_language_ru():
    rows = dict((d, t) for t, d in _rows(i18n.get("ru"), True, "ru"))
    want = catalog("ru")["lang.beta"]["v"].replace("{language}", "Русский")
    assert rows["_:set:lang:ru"] == want + " ✅"


def test_the_beta_mark_is_in_the_viewers_language_fa():
    rows = dict((d, t) for t, d in _rows(i18n.get("fa"), True, "en"))
    suffix = catalog("fa")["lang.beta"]["v"].replace("{language}", "")
    assert suffix.strip() in rows["_:set:lang:fa"]


def test_the_persian_list_text_reads_right_to_left():
    text, _m = language_menu.render_language_menu(i18n.get("fa"), private=True, current="fa")
    assert text.startswith(RLM)


def test_the_settings_row_has_no_beta_mark():
    assert language_menu.row_text(i18n.get("en"), "ru") == \
        "\U0001f310 Language: Русский"


def test_the_settings_row_for_persian_has_no_beta_mark():
    assert "beta" not in language_menu.row_text(i18n.get("en"), "fa")


# ---- switching -------------------------------------------------------------------

@pytest.fixture
def bot(mk_bot):
    b = make_bot(mk_bot)
    add_session(b, chat=DM_A, label="x1")
    return b


def _tap(bot, run_async, data, *, chat=DM_A, user=OWNER):
    update, query = callback(data, chat=chat, user=user)
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def _commands(call):
    return call.kwargs["commands"] if "commands" in call.kwargs else call.args[0]


def _menus_for(bot, chat):
    return [c for c in bot._app.bot.set_my_commands.await_args_list
            if getattr(c.kwargs.get("scope"), "chat_id", None) == chat]


def test_picking_russian_stores_it(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    assert preferences.get_ui_language(DM_A) == "ru"


def test_picking_russian_sends_one_keyboard(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    assert len(reply_keyboards(bot)) == 1


def test_the_russian_keyboard_reads_in_russian(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    words = reply_keyboards(bot)[0]
    assert i18n.get("ru").text(kbwords.KB_STATUS) in words


def test_the_russian_keyboard_keeps_the_session_name(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    assert "x1" in reply_keyboards(bot)[0]


def test_picking_russian_registers_the_menu_once(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    assert len(_menus_for(bot, DM_A)) == 1


def test_the_russian_menu_descriptions_are_russian(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    commands = _commands(_menus_for(bot, DM_A)[0])
    assert all(CYRILLIC.search(c.description) for c in commands)


def test_the_russian_menu_keeps_english_command_words(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    commands = _commands(_menus_for(bot, DM_A)[0])
    assert all(c.command.isascii() for c in commands)


def test_the_russian_menu_talks_to_the_session(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    by_word = {c.command: c.description for c in _commands(_menus_for(bot, DM_A)[0])}
    assert by_word.get("x1") == catalog("ru")["cmd.talk"]["v"].replace("{session}", "x1")


def test_a_second_switch_registers_only_that_chats_menu(bot, run_async):
    add_session(bot, chat=GROUP, label="g1")
    _tap(bot, run_async, "_:set:lang:ru")
    _reset(bot)
    _tap(bot, run_async, "_:set:lang:fa")
    assert bot._app.bot.set_my_commands.await_count == 1


def test_picking_russian_sets_the_menu_button_once(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    assert bot._app.bot.set_chat_menu_button.await_count == 1


def test_the_menu_button_text_follows_the_language(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    button = bot._app.bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert button.text == catalog("ru")["kb.app"]["v"]


def test_the_persian_confirmation_reads_right_to_left(bot, run_async):
    query = _tap(bot, run_async, "_:set:lang:fa")
    assert edited_text(query).startswith(RLM)


def test_the_russian_confirmation_is_russian(bot, run_async):
    query = _tap(bot, run_async, "_:set:lang:ru")
    assert CYRILLIC.search(edited_text(query))


def _reset(bot):
    for name in ("send_message", "set_my_commands", "set_chat_menu_button"):
        getattr(bot._app.bot, name).reset_mock()


def test_tapping_the_same_language_sends_no_keyboard(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    _reset(bot)
    _tap(bot, run_async, "_:set:lang:ru")
    assert reply_keyboards(bot) == []


def test_tapping_the_same_language_registers_no_menu(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    _reset(bot)
    _tap(bot, run_async, "_:set:lang:ru")
    assert bot._app.bot.set_my_commands.await_count == 0


def test_tapping_the_same_language_sets_no_menu_button(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    _reset(bot)
    _tap(bot, run_async, "_:set:lang:ru")
    assert bot._app.bot.set_chat_menu_button.await_count == 0


def test_switching_from_persian_to_russian_sends_one_russian_keyboard(bot, run_async):
    _tap(bot, run_async, "_:set:lang:fa")
    _reset(bot)
    _tap(bot, run_async, "_:set:lang:ru")
    kbs = reply_keyboards(bot)
    assert len(kbs) == 1 and not any(PERSIAN.search(w) for w in kbs[0])


def test_switching_back_to_english_restores_the_english_keyboard(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    _reset(bot)
    _tap(bot, run_async, "_:set:lang:en")
    assert "status" in reply_keyboards(bot)[0]


@pytest.mark.parametrize("code", DISABLED + ("qps", "qps-rtl", "xx", ""))
def test_a_disabled_code_is_refused_with_invalid_value(bot, run_async, code):
    query = _tap(bot, run_async, f"_:set:lang:{code}")
    assert "Invalid value" in toast_texts(query)


@pytest.mark.parametrize("code", ("de", "es", "qps"))
def test_a_disabled_code_writes_nothing(bot, run_async, code):
    _tap(bot, run_async, f"_:set:lang:{code}")
    assert preferences.get_ui_language(DM_A) in (None, "")


@pytest.mark.parametrize("code", ("de", "qps"))
def test_a_disabled_code_sends_no_keyboard(bot, run_async, code):
    _tap(bot, run_async, f"_:set:lang:{code}")
    assert reply_keyboards(bot) == []


# ---- groups ----------------------------------------------------------------------

def test_an_ordinary_member_cannot_switch_the_group(bot, run_async):
    _tap(bot, run_async, "_:set:lang:fa", chat=GROUP, user=BOB)
    assert preferences.get_ui_language(GROUP) in (None, "")


def test_an_admin_switches_the_group(bot, run_async):
    _tap(bot, run_async, "_:set:lang:fa", chat=GROUP, user=OWNER)
    assert preferences.get_ui_language(GROUP) == "fa"


def test_a_group_switch_sends_one_keyboard_to_the_group(bot, run_async):
    add_session(bot, chat=GROUP, label="g1")
    _tap(bot, run_async, "_:set:lang:fa", chat=GROUP, user=OWNER)
    assert len(reply_keyboards(bot, chat=GROUP)) == 1


def test_a_group_switch_keyboard_is_persian(bot, run_async):
    add_session(bot, chat=GROUP, label="g1")
    _tap(bot, run_async, "_:set:lang:fa", chat=GROUP, user=OWNER)
    words = reply_keyboards(bot, chat=GROUP)[0]
    assert any(PERSIAN.search(w) for w in words)


def test_a_group_switch_does_not_touch_the_admins_dm(bot, run_async):
    _tap(bot, run_async, "_:set:lang:fa", chat=GROUP, user=OWNER)
    assert preferences.get_ui_language(DM_A) in (None, "")


def test_the_settings_root_in_russian_shows_russian(bot, run_async):
    _tap(bot, run_async, "_:set:lang:ru")
    query = _tap(bot, run_async, "_:set")
    assert CYRILLIC.search(edited_text(query))


def test_the_settings_root_buttons_in_persian(bot, run_async):
    from aipager.bot import settings_menu
    preferences.set_ui_language(DM_A, "fa")
    _text, markup = settings_menu.render_settings_root(DM_A)
    assert all(PERSIAN.search(t) for t in button_texts(markup))
