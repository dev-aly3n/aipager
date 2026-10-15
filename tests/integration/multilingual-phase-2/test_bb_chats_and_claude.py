"""Black-box: each chat in its own language, pop-ups in the tapper's, and
Claude reading the same bytes whatever the chat's language (design.md
success criteria "In a mixed group, shared messages use the group's
language and pop-ups use the tapper's" and "Claude receives the English
prompts and commands"; entrypoints.md "Mixed group", "No automatic switch",
"Keyboard and commands in fa or ru chats").

Partitions for the tapper: stored DM language different from the group /
none stored with an English app / none stored with a Persian app.
Chats in one daemon: a Russian DM, a Persian group, an unset DM.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import animation, kbwords, settings_menu
from aipager.state import Status
from tests.i18n_ml2_bb import (
    BOB,
    CAROL,
    CYRILLIC,
    DM_A,
    DM_B,
    DM_C,
    GROUP,
    OWNER,
    PERSIAN,
    add_session,
    callback,
    edited_text,
    make_bot,
    message,
    toast_texts,
)
from tests.footer_text import status_md


@pytest.fixture
def bot(mk_bot):
    return make_bot(mk_bot)


# ---- no message in another chat's language ----------------------------------------

@pytest.fixture
def three_chats(bot):
    preferences.set_ui_language(DM_A, "ru")
    preferences.set_ui_language(GROUP, "fa")
    out = {}
    for chat, label in ((DM_A, "x1"), (GROUP, "g1"), (DM_B, "b1")):
        s = add_session(bot, chat=chat, label=label, status=Status.BUSY)
        s.busy_started_at = time.monotonic() - 30
        s.tool_history = [("Read: /a", True)]
        out[chat] = s
    return out


def test_the_russian_dm_card_is_russian(three_chats):
    card = animation.build_stream_card(three_chats[DM_A], "Working")
    assert CYRILLIC.search(card) and not PERSIAN.search(card)


def test_the_persian_group_card_is_persian(three_chats):
    card = animation.build_stream_card(three_chats[GROUP], "Working")
    assert PERSIAN.search(card) and not CYRILLIC.search(card)


def test_the_unset_dm_card_is_english(three_chats):
    card = animation.build_stream_card(three_chats[DM_B], "Working")
    assert not PERSIAN.search(card) and not CYRILLIC.search(card)


def test_the_unset_dm_card_is_todays_english(three_chats):
    card = animation.build_stream_card(three_chats[DM_B], "Working")
    assert status_md(card) == "⏳ **b1** · Working · 0:30 · Read ×1"


@pytest.mark.parametrize("chat, want, other", [(DM_A, CYRILLIC, PERSIAN),
                                                (GROUP, PERSIAN, CYRILLIC)])
def test_each_settings_root_is_in_its_own_chats_language(three_chats, chat, want, other):
    text, _m = settings_menu.render_settings_root(chat)
    assert want.search(text) and not other.search(text)


def test_the_unset_settings_root_is_english(three_chats):
    text, _m = settings_menu.render_settings_root(DM_B)
    assert text.startswith("⚙️ <b>Settings</b>")


def _answer_header(bot, sess, monkeypatch, run_async):
    rich = AsyncMock(return_value={"message_id": 7})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", rich)
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    call = rich.await_args
    assert call is not None, "no answer was sent"
    return call.args[1].split("\n", 1)[0], call.args[0]


def test_answers_go_to_each_chat_in_its_own_language(bot, three_chats, monkeypatch,
                                                      run_async):
    got = {chat: _answer_header(bot, three_chats[chat], monkeypatch, run_async)
           for chat in (DM_A, GROUP, DM_B)}
    langs = {}
    for chat, (header, _target) in got.items():
        langs[chat] = ("fa" if PERSIAN.search(header)
                       else "ru" if CYRILLIC.search(header) else "en")
    assert langs == {DM_A: "ru", GROUP: "fa", DM_B: "en"}


def test_a_group_tap_does_not_change_what_the_dm_reads(bot, three_chats, run_async):
    update, _q = callback("_:set:length:short", chat=GROUP, user=OWNER)
    run_async(bot._handle_callback(update, MagicMock()))
    text, _m = settings_menu.render_settings_root(DM_A)
    assert CYRILLIC.search(text) and not PERSIAN.search(text)


# ---- pop-ups in the tapper's language ------------------------------------------------

def _tap(bot, run_async, data, *, user, language_code="en", chat=GROUP):
    update, query = callback(data, chat=chat, user=user, language_code=language_code)
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def test_a_persian_member_gets_a_persian_popup_in_a_russian_group(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(DM_C, "fa")
    query = _tap(bot, run_async, "_:set:length:short", user=CAROL)
    toasts = toast_texts(query)
    assert toasts and PERSIAN.search(toasts[-1]) and not CYRILLIC.search(toasts[-1])


def test_a_member_with_a_persian_app_and_no_choice_gets_the_groups_language(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    query = _tap(bot, run_async, "_:set:length:short", user=BOB, language_code="fa")
    toasts = toast_texts(query)
    assert toasts and CYRILLIC.search(toasts[-1])


def test_a_member_with_an_english_app_and_no_choice_gets_english(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    query = _tap(bot, run_async, "_:set:length:short", user=BOB, language_code="en")
    toasts = toast_texts(query)
    assert toasts and not CYRILLIC.search(toasts[-1]) and not PERSIAN.search(toasts[-1])


def test_the_shared_page_after_a_persian_members_tap_stays_russian(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(DM_C, "fa")
    query = _tap(bot, run_async, "_:set", user=CAROL)
    text = edited_text(query)
    assert CYRILLIC.search(text) and not PERSIAN.search(text)


def test_a_refused_language_tap_in_a_persian_group_is_in_the_tappers_language(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(DM_B, "ru")
    query = _tap(bot, run_async, "_:set:lang:ru", user=BOB)
    toasts = toast_texts(query)
    assert toasts and CYRILLIC.search(toasts[-1]) and not PERSIAN.search(toasts[-1])


def test_a_refused_language_tap_leaves_the_group_persian(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    _tap(bot, run_async, "_:set:lang:ru", user=BOB)
    assert preferences.get_ui_language(GROUP) == "fa"


# ---- what Claude reads --------------------------------------------------------------

@pytest.fixture
def wired(bot, monkeypatch):
    s = add_session(bot, chat=DM_A, label="x1")
    bot.registry.last_active_session = s.name
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    return bot, s, sent


def _claude_reads(wired, run_async, code, text_for):
    bot, sess, sent = wired
    preferences.set_ui_language(DM_A, "" if code == "en" else code)
    sess.status = Status.IDLE
    sent.reset_mock()
    run_async(bot._handle_message(message(text_for(code)), MagicMock()))
    return [c.args for c in sent.await_args_list]


def _template_label(code):
    return kbwords.shown_label(i18n.get(code), "Run tests", "Run the tests")


def _command_label(code):
    return kbwords.shown_label(i18n.get(code), "Compact", "/compact")


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_translated_template_sends_claude_the_english_bytes(wired, run_async, code):
    english = _claude_reads(wired, run_async, "en", _template_label)
    assert english and _claude_reads(wired, run_async, code, _template_label) == english


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_translated_command_sends_claude_the_english_command(wired, run_async, code):
    english = _claude_reads(wired, run_async, "en", _command_label)
    assert english and _claude_reads(wired, run_async, code, _command_label) == english


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_typed_prompt_reaches_claude_with_the_same_marker(wired, run_async, code):
    def typed(_c):
        return "/x1 fix the bug"
    english = _claude_reads(wired, run_async, "en", typed)
    assert english and _claude_reads(wired, run_async, code, typed) == english


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_what_claude_reads_carries_no_bidi_marks(wired, run_async, code):
    from tests.i18n_ml2_bb import BIDI_CONTROLS
    got = _claude_reads(wired, run_async, code, _template_label)
    assert got and not any(BIDI_CONTROLS.search(str(a)) for a in got)


def test_a_russian_stop_word_after_the_session_is_a_prompt(wired, run_async):
    got = _claude_reads(wired, run_async, "ru", lambda _c: "/x1 стоп")
    assert got and got[-1][1].endswith("\nстоп")


def test_the_english_stop_after_the_session_is_not_a_prompt(wired, run_async):
    assert _claude_reads(wired, run_async, "ru", lambda _c: "/x1 stop") == []


# ---- busy card direction (a group, no commentary) ------------------------------------

def _card_is_rtl(bot, run_async, monkeypatch, commentary=()):
    edit = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(animation, "edit_message_text_rich", edit)
    s = add_session(bot, chat=GROUP, label="g1", status=Status.BUSY)
    s.busy_started_at = time.monotonic() - 5
    s.busy_msg_id = 77
    s.tool_history = [("Read: /a", True)]
    s.stream_commentary = [(0, t) for t in commentary]
    run_async(bot._edit_busy_rich(s, "Working"))
    return edit.await_args.kwargs["is_rtl"]


@pytest.mark.parametrize("code, want", [("fa", True), ("ru", False), ("", False)])
def test_a_group_card_without_commentary_follows_the_chat(bot, run_async, monkeypatch,
                                                           code, want):
    preferences.set_ui_language(GROUP, code)
    assert _card_is_rtl(bot, run_async, monkeypatch) is want


def test_english_commentary_turns_a_persian_group_card_left_to_right(bot, run_async,
                                                                    monkeypatch):
    preferences.set_ui_language(GROUP, "fa")
    assert _card_is_rtl(bot, run_async, monkeypatch, ["Reading it now."]) is False


def test_persian_commentary_turns_a_russian_group_card_right_to_left(bot, run_async,
                                                                    monkeypatch):
    preferences.set_ui_language(GROUP, "ru")
    assert _card_is_rtl(bot, run_async, monkeypatch,
                        ["فایل را می\u200cخوانم"]) is True
