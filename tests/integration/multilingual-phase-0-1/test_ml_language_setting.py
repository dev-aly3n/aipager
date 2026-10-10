"""The Language setting in Telegram (design.md success criterion 5;
entrypoints.md "Telegram surface" and ``preferences.get/set_ui_language``).

Partitions: private chat vs group; admin vs member vs read-only member;
token ``auto`` vs the enabled code ``en`` vs a disabled code (``de``) vs a
pseudo code (``qps``, ``qps-rtl``) vs garbage; nothing stored vs the same
value stored vs a different value stored.
"""

from __future__ import annotations

import pytest

from aipager import preferences as prefs
from aipager.i18n import locales

DM = 555
GROUP = -1001
OWNER = 555
BOB = 2
RO = 3


#: The beta languages, in the list's order (phase 2: ru, fa; phase 5 run 1:
#: es, pt-BR, fr, it; run 2: de, ar, uk, tr).
BETA_CODES = ("es", "ru", "pt-BR", "fr", "de", "ar", "uk", "it", "tr", "fa")
BETA = ["Español (beta)", "Русский (beta)", "Português (Brasil) (beta)", "Français (beta)",
        "Deutsch (beta)", "العربية (beta)", "Українська (beta)", "Italiano (beta)",
        "Türkçe (beta)", "فارسی (beta)"]
BETA_DATA = [f"_:set:lang:{c}" for c in BETA_CODES]


# ---- /language replies ------------------------------------------------------------

def test_language_in_a_dm_shows_the_private_text(tg_bot, command, ml):
    text, _kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=DM, user_id=OWNER)
    assert text == ml.DM_PRIVATE_TEXT


def test_language_in_a_dm_offers_automatic_then_english_then_back(tg_bot, command, ml):
    _text, kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=DM, user_id=OWNER)
    # Phase 2: Russian and Persian follow English; phase 5 run 2 adds
    # German, Arabic, Ukrainian and Turkish (in LOCALES order).
    assert [d for _t, d in ml.buttons(kb)] == [
        "_:set:lang:auto", "_:set:lang:en", *BETA_DATA, "_:set:back"]


def test_language_in_a_dm_marks_automatic_when_nothing_is_stored(tg_bot, command, ml):
    _text, kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=DM, user_id=OWNER)
    assert [t for t, _d in ml.buttons(kb)] == [
        ml.AUTO_LABEL + " ✅", "English", *BETA, "« Back"]


def test_language_buttons_are_one_per_row(tg_bot, command):
    _text, kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=DM, user_id=OWNER)
    assert [len(row) for row in kb.inline_keyboard] == [1] * (3 + len(BETA))


def test_language_in_a_dm_marks_english_once_chosen(tg_bot, command, ml):
    prefs.set_ui_language(DM, "en")
    _text, kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=DM, user_id=OWNER)
    assert [t for t, _d in ml.buttons(kb)] == [ml.AUTO_LABEL, "English ✅", *BETA, "« Back"]


def test_language_in_a_group_shows_the_group_text(tg_bot, command, ml):
    text, _kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=GROUP, user_id=BOB)
    assert text == ml.GROUP_LIST_TEXT


def test_language_in_a_group_has_no_automatic(tg_bot, command, ml):
    _text, kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=GROUP, user_id=BOB)
    assert [d for _t, d in ml.buttons(kb)] == [
        "_:set:lang:en", *BETA_DATA, "_:set:back"]


def test_language_in_a_group_lists_english_by_its_name(tg_bot, command, ml):
    _text, kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=GROUP, user_id=BOB)
    assert ml.buttons(kb)[0][0].removesuffix(" ✅") == "English"


def test_a_read_only_member_may_open_language(tg_bot, command, ml):
    text, _kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=GROUP, user_id=RO)
    assert text == ml.GROUP_LIST_TEXT


def test_no_disabled_language_is_listed(tg_bot, command, ml):
    """English and the beta languages are selectable; the others are
    hidden (ruling; phase 2 enabled ru and fa, phase 5 run 1 es, pt-BR,
    fr and it, run 2 de, ar, uk and tr). Japanese and Polish are not
    offered at all."""
    _text, kb = command(tg_bot, "_handle_language_cmd", "/language",
                        chat_id=DM, user_id=OWNER)
    shown = {d for _t, d in ml.buttons(kb)}
    off = [c for c, loc in locales.LOCALES.items() if not loc.enabled]
    for code in ("ja", "pl", *off, "qps", "qps-rtl"):
        assert f"_:set:lang:{code}" not in shown


# ---- /settings and its Language row ------------------------------------------------

def test_settings_first_row_is_the_language(tg_bot, command, ml):
    _text, kb = command(tg_bot, "_handle_settings_cmd", "/settings",
                        chat_id=DM, user_id=OWNER)
    assert ml.buttons(kb)[0] == ("🌐 Language: English", "_:set:lang")


def test_settings_first_row_is_the_language_in_a_group(tg_bot, command, ml):
    _text, kb = command(tg_bot, "_handle_settings_cmd", "/settings",
                        chat_id=GROUP, user_id=BOB)
    assert ml.buttons(kb)[0] == ("🌐 Language: English", "_:set:lang")


def test_render_settings_root_puts_language_first():
    from aipager.bot.settings_menu import render_settings_root
    _text, kb = render_settings_root(DM)
    first = kb.inline_keyboard[0][0]
    assert (first.text, first.callback_data) == ("🌐 Language: English", "_:set:lang")


def test_the_language_row_opens_the_list_in_a_dm(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang", chat_id=DM, user_id=OWNER)
    assert ml.edited(q)[0] == ml.DM_PRIVATE_TEXT


def test_a_member_may_look_at_the_group_list(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang", chat_id=GROUP, user_id=BOB)
    assert ml.edited(q)[0] == ml.GROUP_LIST_TEXT


def test_looking_at_the_group_list_raises_no_alert(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang", chat_id=GROUP, user_id=BOB)
    assert not any(alert for _t, alert in ml.toasts(q))


def test_the_row_and_the_command_show_the_same_list(tg_bot, tap, command, ml):
    q = tap(tg_bot, "_:set:lang", chat_id=DM, user_id=OWNER)
    _t, kb_cmd = command(tg_bot, "_handle_language_cmd", "/language",
                         chat_id=DM, user_id=OWNER)
    assert ml.buttons(ml.edited(q)[1]) == ml.buttons(kb_cmd)


# ---- picking ------------------------------------------------------------------------

def test_picking_english_in_a_dm_stores_it(tg_bot, tap):
    tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(DM) == "en"


def test_picking_english_in_a_dm_confirms(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    assert ml.edited(q)[0] == ml.CONFIRMATION + "\n\n" + ml.DM_PRIVATE_TEXT


def test_picking_english_moves_the_tick(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    assert [t for t, _d in ml.buttons(ml.edited(q)[1])] == [
        ml.AUTO_LABEL, "English ✅", *BETA, "« Back"]


def test_picking_automatic_in_a_dm_clears_the_choice(tg_bot, tap):
    prefs.set_ui_language(DM, "en")
    tap(tg_bot, "_:set:lang:auto", chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(DM) == ""


def test_picking_automatic_removes_the_key_from_the_file(tg_bot, tap, ml):
    prefs.set_ui_language(DM, "en")
    tap(tg_bot, "_:set:lang:auto", chat_id=DM, user_id=OWNER)
    assert b"ui_language" not in (ml.prefs_bytes() or b"")


def test_picking_automatic_confirms(tg_bot, tap, ml):
    prefs.set_ui_language(DM, "en")
    q = tap(tg_bot, "_:set:lang:auto", chat_id=DM, user_id=OWNER)
    assert ml.edited(q)[0].startswith(ml.CONFIRMATION + "\n\n")


def test_an_admin_sets_the_group_language(tg_bot, tap):
    tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=OWNER)
    assert prefs.get_ui_language(GROUP) == "en"


def test_the_group_confirmation_names_who_changed_it(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=OWNER)
    assert "<i>Changed by @aly.</i>" in ml.edited(q)[0]


def test_the_group_confirmation_leads_with_the_line_and_the_list(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=OWNER)
    assert ml.edited(q)[0].startswith(
        ml.CONFIRMATION + "\n\n" + ml.GROUP_LIST_TEXT)


def test_a_group_choice_never_becomes_the_admins_own(tg_bot, tap):
    tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=OWNER)
    assert prefs.get_ui_language(OWNER) == ""


def test_a_dm_choice_never_becomes_the_groups(tg_bot, tap):
    tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(GROUP) == ""


# ---- the admin gate ----------------------------------------------------------------

def test_a_member_cannot_change_the_group_language(tg_bot, tap):
    tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=BOB)
    assert prefs.get_ui_language(GROUP) == ""


def test_a_member_gets_the_admin_alert(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=BOB)
    assert (ml.ADMIN_ALERT, True) in ml.toasts(q)


def test_a_member_tap_writes_nothing(tg_bot, tap, ml):
    before = ml.prefs_bytes()
    tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=BOB)
    assert ml.prefs_bytes() == before


def test_a_read_only_member_cannot_change_the_group_language(tg_bot, tap):
    tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=RO)
    assert prefs.get_ui_language(GROUP) == ""


def test_a_member_tap_does_not_confirm(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:en", chat_id=GROUP, user_id=BOB)
    edits = [c.args[0] if c.args else c.kwargs.get("text", "")
             for c in q.edit_message_text.await_args_list]
    assert not any(ml.CONFIRMATION in e for e in edits)


# ---- refusals -----------------------------------------------------------------------

def test_auto_is_refused_in_a_group(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:auto", chat_id=GROUP, user_id=OWNER)
    assert "Invalid value" in [t for t, _a in ml.toasts(q)]


def test_auto_in_a_group_writes_nothing(tg_bot, tap, ml):
    prefs.set_ui_language(GROUP, "en")
    before = ml.prefs_bytes()
    tap(tg_bot, "_:set:lang:auto", chat_id=GROUP, user_id=OWNER)
    assert ml.prefs_bytes() == before


@pytest.mark.parametrize("code", ["ja", "pl", "qps", "qps-rtl", "xx", "EN",
                                  "en-US", "", "english"])
@pytest.mark.parametrize("chat", [DM, GROUP])
def test_a_disabled_pseudo_or_unknown_code_is_refused(tg_bot, tap, ml, code, chat):
    q = tap(tg_bot, f"_:set:lang:{code}", chat_id=chat, user_id=OWNER)
    assert "Invalid value" in [t for t, _a in ml.toasts(q)]


@pytest.mark.parametrize("code", ["ja", "qps", "qps-rtl", "xx"])
@pytest.mark.parametrize("chat", [DM, GROUP])
def test_a_refused_code_writes_nothing(tg_bot, tap, ml, code, chat):
    before = ml.prefs_bytes()
    tap(tg_bot, f"_:set:lang:{code}", chat_id=chat, user_id=OWNER)
    assert ml.prefs_bytes() == before


def test_a_refused_code_keeps_the_previous_choice(tg_bot, tap):
    prefs.set_ui_language(DM, "en")
    tap(tg_bot, "_:set:lang:ja", chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(DM) == "en"


def test_extra_segments_are_not_a_language(tg_bot, tap):
    tap(tg_bot, "_:set:lang:en:extra", chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(DM) in ("", "en")
    assert prefs.get_ui_language(DM) != "en:extra"


# ---- nothing written for a chat that did not choose ---------------------------------

def test_viewing_writes_nothing(tg_bot, tap, command, ml):
    before = ml.prefs_bytes()
    command(tg_bot, "_handle_settings_cmd", "/settings", chat_id=DM, user_id=OWNER)
    command(tg_bot, "_handle_language_cmd", "/language", chat_id=DM, user_id=OWNER)
    command(tg_bot, "_handle_language_cmd", "/language", chat_id=GROUP, user_id=BOB)
    tap(tg_bot, "_:set:lang", chat_id=DM, user_id=OWNER)
    tap(tg_bot, "_:set:lang", chat_id=GROUP, user_id=BOB)
    tap(tg_bot, "_:set", chat_id=GROUP, user_id=BOB)
    assert ml.prefs_bytes() == before


def test_automatic_with_nothing_stored_writes_nothing(tg_bot, tap, ml):
    before = ml.prefs_bytes()
    tap(tg_bot, "_:set:lang:auto", chat_id=DM, user_id=OWNER)
    assert ml.prefs_bytes() == before


def test_automatic_with_nothing_stored_still_confirms(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:lang:auto", chat_id=DM, user_id=OWNER)
    assert ml.edited(q)[0].startswith(ml.CONFIRMATION)


def test_repicking_the_same_language_writes_nothing(tg_bot, tap, ml):
    tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    before = ml.prefs_bytes()
    tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    assert ml.prefs_bytes() == before


def test_repicking_the_same_language_still_confirms(tg_bot, tap, ml):
    tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    q = tap(tg_bot, "_:set:lang:en", chat_id=DM, user_id=OWNER)
    assert ml.edited(q)[0].startswith(ml.CONFIRMATION)


# ---- side effects only when the effective language changes --------------------------

@pytest.mark.parametrize("token,chat", [("en", DM), ("auto", DM), ("en", GROUP)])
def test_an_unchanged_effective_language_reregisters_no_menu(tg_bot, tap, token, chat):
    tap(tg_bot, f"_:set:lang:{token}", chat_id=chat, user_id=OWNER)
    assert tg_bot._update_bot_commands.await_count == 0
    assert tg_bot._app.bot.set_my_commands.await_count == 0


@pytest.mark.parametrize("token,chat", [("en", DM), ("auto", DM), ("en", GROUP)])
def test_an_unchanged_effective_language_sends_no_keyboard(tg_bot, tap, token, chat):
    tap(tg_bot, f"_:set:lang:{token}", chat_id=chat, user_id=OWNER)
    assert tg_bot._app.bot.send_message.await_count == 0


@pytest.mark.parametrize("token,chat", [("en", DM), ("auto", DM), ("en", GROUP)])
def test_an_unchanged_effective_language_sets_no_menu_button(tg_bot, tap, token, chat):
    tap(tg_bot, f"_:set:lang:{token}", chat_id=chat, user_id=OWNER)
    assert tg_bot._app.bot.set_chat_menu_button.await_count == 0


# ---- the storage functions -----------------------------------------------------------

def test_get_ui_language_is_empty_when_unset():
    assert prefs.get_ui_language(424242) == ""


def test_set_ui_language_returns_the_code():
    assert prefs.set_ui_language(424242, "en") == "en"


def test_set_ui_language_survives_a_cache_reset():
    """'survives restarts': the value is on disk, not only in memory."""
    prefs.set_ui_language(424242, "en")
    prefs._cache = None
    assert prefs.get_ui_language(424242) == "en"


def test_set_ui_language_empty_clears():
    prefs.set_ui_language(424242, "en")
    prefs.set_ui_language(424242, "")
    assert prefs.get_ui_language(424242) == ""


@pytest.mark.parametrize("code", ["ja", "qps", "qps-rtl", "xx", "EN", "auto"])
def test_set_ui_language_refuses_a_code_that_is_not_selectable(code):
    with pytest.raises(ValueError):
        prefs.set_ui_language(424242, code)


@pytest.mark.parametrize("code", ["ja", "qps", "xx"])
def test_a_refused_code_is_refused_before_any_write(ml, code):
    before = ml.prefs_bytes()
    with pytest.raises(ValueError):
        prefs.set_ui_language(424242, code)
    assert ml.prefs_bytes() == before


def test_set_ui_language_unchanged_does_not_rewrite_the_file():
    import os
    from pathlib import Path
    prefs.set_ui_language(424242, "en")
    path = Path(prefs._PREFERENCES_PATH)
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))   # an old, known stamp
    prefs.set_ui_language(424242, "en")
    assert path.stat().st_mtime_ns == 1_000_000_000


def test_clearing_nothing_creates_no_file(ml):
    prefs.set_ui_language(424242, "")
    assert ml.prefs_bytes() is None


def test_the_language_key_is_named_ui_language(ml):
    import json
    prefs.set_ui_language(424242, "en")
    data = json.loads(ml.prefs_bytes())
    assert data["424242"]["ui_language"] == "en"

