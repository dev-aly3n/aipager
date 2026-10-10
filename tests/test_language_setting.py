"""The Language setting (the multilingual design, 7.2-7.3): the first row
of ``/settings``, ``/language`` and the group picker. English is the only
language offered; "Automatic" in a private chat only; anyone may look,
only an admin changes a group's language; nothing is written for a chat
that did not choose; and the menu, keyboard and menu button are re-sent
only when the effective language really changed."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences
from aipager.i18n import locales
from aipager.policy import load_policy
from aipager.scope import Member, Scope

GROUP, DM = -1001, 555
ALY, BOB = 555, 777

LIST_PRIVATE = ("🌐 The language aipager uses for its own messages and buttons, here and in "
                "the Mini App. Your Claude sessions don't change: Claude answers in the "
                "language you write to it.")
LIST_GROUP = ("🌐 The language aipager uses for this group's cards and buttons. Claude's "
              "sessions don't change: Claude answers in the language you write to it.")
CONFIRM = ("✅ aipager now speaks English here. Your sessions and Claude's answers are "
           "unchanged.")


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=ALY, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"))),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=ALY, label="aly", role="owner"),)),
    ])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    b._app.bot.set_my_commands = AsyncMock()
    b._app.bot.set_chat_menu_button = AsyncMock()
    b._send_keyboard = AsyncMock()
    return b


def tap(bot, run_async, data, *, chat, user, language_code=None):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = 42
    query.message.chat = MagicMock(id=chat)
    query.message.chat_id = chat
    query.from_user = MagicMock(id=user, language_code=language_code)
    query.from_user.username = "aly" if user == ALY else "bob"
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock(id=chat)
    update.message = None
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def edited(query):
    call = query.edit_message_text.await_args
    if call is None:
        return None, []
    text = call.args[0] if call.args else call.kwargs.get("text")
    kb = call.kwargs.get("reply_markup")
    rows = [[(b.text, b.callback_data) for b in row] for row in kb.inline_keyboard] if kb else []
    return text, rows


def toast(query):
    call = query.answer.await_args
    if call is None:
        return None, None
    text = call.args[0] if call.args else call.kwargs.get("text")
    return text, call.kwargs.get("show_alert")


def writes(monkeypatch):
    calls = []
    real = preferences._save_raw
    monkeypatch.setattr(preferences, "_save_raw",
                        lambda store: (calls.append(1), real(store)))
    return calls


def _disable_german(monkeypatch):
    """German is enabled (beta) since translation run 2; it stands here for
    a known language that is switched off."""
    monkeypatch.setitem(locales.LOCALES, "de", dataclasses.replace(
        locales.LOCALES["de"], enabled=False))


BETA_ROWS = [[(name, f"_:set:lang:{code}")] for code, name in (
    ("es", "Español"), ("ru", "Русский"), ("pt-BR", "Português (Brasil)"),
    ("zh-Hans", "简体中文"), ("fr", "Français"), ("de", "Deutsch"), ("ko", "한국어"),
    ("ar", "العربية"), ("id", "Bahasa Indonesia"), ("uk", "Українська"),
    ("it", "Italiano"), ("tr", "Türkçe"), ("fa", "فارسی"), ("vi", "Tiếng Việt"))]


def test_the_language_row_opens_the_list(bot, run_async):
    q = tap(bot, run_async, "_:set:lang", chat=DM, user=ALY)
    text, rows = edited(q)
    assert text == LIST_PRIVATE
    # Phase 2: Russian and Persian join the list, marked beta; phase 5
    # run 1: Spanish, Portuguese (Brazil), French and Italian; run 2:
    # German, Arabic, Ukrainian and Turkish.
    assert rows == [[("Automatic (your Telegram language) ✅", "_:set:lang:auto")],
                    [("English", "_:set:lang:en")], *BETA_ROWS,
                    [("« Back", "_:set:back")]]
    q = tap(bot, run_async, "_:set:lang", chat=GROUP, user=BOB)
    text, rows = edited(q)
    assert text == LIST_GROUP
    assert rows == [[("English ✅", "_:set:lang:en")], *BETA_ROWS, [("« Back", "_:set:back")]]


def test_picking_english_confirms(bot, run_async):
    q = tap(bot, run_async, "_:set:lang:en", chat=DM, user=ALY)
    text, rows = edited(q)
    assert text == CONFIRM + "\n\n" + LIST_PRIVATE
    assert rows[1] == [("English ✅", "_:set:lang:en")]
    assert preferences.get_ui_language(DM) == "en"
    q = tap(bot, run_async, "_:set:lang:auto", chat=DM, user=ALY)
    assert preferences.get_ui_language(DM) == "auto"
    assert edited(q)[1][0] == [("Automatic (your Telegram language) ✅", "_:set:lang:auto")]


def test_a_group_change_names_who(bot, run_async):
    q = tap(bot, run_async, "_:set:lang:en", chat=GROUP, user=ALY)
    text, _rows = edited(q)
    assert text == CONFIRM + "\n\n" + LIST_GROUP + "\n\n<i>Changed by @aly.</i>"
    assert preferences.get_ui_language(GROUP) == "en"


def test_a_member_cannot_change_the_group_language(bot, run_async, monkeypatch):
    calls = writes(monkeypatch)
    q = tap(bot, run_async, "_:set:lang:en", chat=GROUP, user=BOB)
    assert toast(q) == ("Only an admin can change settings in this group.", True)
    assert q.edit_message_text.await_count == 0
    assert calls == [] and preferences.get_ui_language(GROUP) == ""


def test_auto_is_refused_in_a_group(bot, run_async, monkeypatch):
    calls = writes(monkeypatch)
    q = tap(bot, run_async, "_:set:lang:auto", chat=GROUP, user=ALY)
    assert toast(q)[0] == "Invalid value"
    assert calls == [] and q.edit_message_text.await_count == 0


@pytest.mark.parametrize("code", ["de", "ja", "pl", "qps", "qps-rtl", "xx", "EN", ""])
def test_a_disabled_or_pseudo_code_is_refused(bot, run_async, monkeypatch, code):
    _disable_german(monkeypatch)
    calls = writes(monkeypatch)
    # Refused by the callback itself, before the store's own check.
    asked = []
    real = preferences.set_ui_language
    monkeypatch.setattr(preferences, "set_ui_language",
                        lambda chat_id, c: (asked.append(c), real(chat_id, c))[1])
    for chat, user in ((DM, ALY), (GROUP, ALY)):
        q = tap(bot, run_async, f"_:set:lang:{code}", chat=chat, user=user)
        assert toast(q)[0] in ("Invalid value", "Invalid callback")
        assert q.edit_message_text.await_count == 0
    assert calls == [] and asked == []


def test_nothing_is_written_for_a_chat_that_did_not_choose(bot, run_async, monkeypatch):
    calls = writes(monkeypatch)
    tap(bot, run_async, "_:set:lang", chat=DM, user=ALY, language_code="fa")
    tap(bot, run_async, "_:set", chat=GROUP, user=BOB)
    assert calls == []
    tap(bot, run_async, "_:set:lang:en", chat=DM, user=ALY)
    tap(bot, run_async, "_:set:lang:en", chat=DM, user=ALY)        # the same again
    assert len(calls) == 1
    assert not preferences._PREFERENCES_PATH.exists() or "-1001" not in (
        preferences._PREFERENCES_PATH.read_text())


def test_repicking_makes_no_extra_call(bot, run_async):
    tap(bot, run_async, "_:set:lang:en", chat=DM, user=ALY)
    tap(bot, run_async, "_:set:lang:auto", chat=DM, user=ALY)
    tap(bot, run_async, "_:set:lang:en", chat=GROUP, user=ALY)
    assert bot._app.bot.set_my_commands.await_count == 0
    assert bot._send_keyboard.await_count == 0
    assert bot._app.bot.set_chat_menu_button.await_count == 0


def test_switching_reregisters_once(bot, run_async, monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    tap(bot, run_async, "_:set:lang:qps", chat=GROUP, user=ALY)
    chats = [c.kwargs["scope"].chat_id for c in bot._app.bot.set_my_commands.await_args_list]
    assert chats.count(GROUP) == 1
    assert [c.kwargs.get("chat_id") for c in bot._send_keyboard.await_args_list] == [GROUP]
    tap(bot, run_async, "_:set:lang:qps", chat=GROUP, user=ALY)
    chats = [c.kwargs["scope"].chat_id for c in bot._app.bot.set_my_commands.await_args_list]
    assert chats.count(GROUP) == 1
    assert bot._send_keyboard.await_count == 1


def test_the_settings_root_starts_with_the_language(bot, run_async):
    q = tap(bot, run_async, "_:set", chat=DM, user=ALY)
    _text, rows = edited(q)
    assert rows[0] == [("🌐 Language: English", "_:set:lang")]


def test_language_is_a_reserved_session_name_and_an_own_command():
    from aipager.bot import group_intake
    from aipager.dtach.inject import _RESERVED
    assert "language" in _RESERVED and "language" in group_intake.OWN_COMMANDS


def test_back_to_automatic_leaves_no_empty_entry():
    """Guard: clearing the only stored key removes the chat's entry; an
    entry that holds other preferences keeps them."""
    import json
    preferences.set_ui_language(DM, "en")
    preferences.set_ui_language(DM, "")
    assert str(DM) not in json.loads(preferences._PREFERENCES_PATH.read_text())
    preferences.set_preference(GROUP, "language_level", "advanced")
    preferences.set_ui_language(GROUP, "en")
    preferences.set_ui_language(GROUP, "")
    stored = json.loads(preferences._PREFERENCES_PATH.read_text())
    assert stored[str(GROUP)] == {"language_level": "advanced"}
    assert preferences.get_preferences(GROUP).language_level == "advanced"
