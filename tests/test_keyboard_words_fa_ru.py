"""Keyboard words and default labels typed or tapped in Persian and
Russian (multilingual design 5.5): they resolve, with or without the
invisible mark and with the Arabic forms of ye and kaf; a translated
template or command sends the English payload (G58); a group tap is
compared normalized (G60, G61)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aipager import config, i18n
from aipager.i18n import Msg
from aipager.bot import kbwords

RLM = "\u200f"


@pytest.fixture
def bot():
    return SimpleNamespace(
        _template_map=dict(config._DEFAULT_TEMPLATES),
        _command_map=dict(config._DEFAULT_COMMANDS),
        _model_map={"Opus": "/model opus"},
        registry=SimpleNamespace(live_labels=lambda chat_id: {"x1"}))


def _label(code, msg):
    # The App button is a fixed name, the same in every language.
    return i18n.get(code).text(msg) if isinstance(msg, Msg) else str(msg)


def test_persian_and_russian_words_resolve():
    for code in ("fa", "ru"):
        for action, msg in kbwords.WORDS.items():
            shown = _label(code, msg)
            assert " " not in shown.strip(), (code, shown)
            assert kbwords.bare_word(shown) == action, (code, shown)
            assert kbwords.bare_word(shown.replace(RLM, "")) == action
        for action, msg in kbwords.NAV.items():
            assert kbwords.nav_action(_label(code, msg)) == f"nav:{action}"
    assert kbwords.bare_word("СТОП") == "stop"


def test_arabic_ye_and_kaf_resolve():
    status = _label("fa", kbwords.KB_STATUS).replace(RLM, "")
    assert "ی" in status
    typed = status.replace("ی", "ي").replace("ک", "ك")
    assert typed != status
    assert kbwords.bare_word(typed) == "status"
    assert kbwords.bare_word(RLM + typed) == "status"


def test_a_persian_template_tap_sends_the_english_prompt(bot):
    for code in ("fa", "ru"):
        for (label, payload), msg in kbwords.DEFAULT_LABELS.items():
            shown = kbwords.shown_label(i18n.get(code), label, payload)
            kind = "cmd" if payload.startswith("/") else "tpl"
            assert kbwords.custom_action(bot, shown) == (kind, payload), (code, shown)
            assert kbwords.custom_action(bot, shown.replace(RLM, "")) == (kind, payload)
    # English still works in every chat.
    assert kbwords.custom_action(bot, "Run tests") == ("tpl", "Run the tests")


def test_a_marked_persian_tap_without_its_mark_is_accepted(bot):
    stop = _label("fa", kbwords.KB_STOP)
    assert stop.startswith(RLM)
    assert kbwords.is_group_label(bot, -1001, stop)
    assert kbwords.is_group_label(bot, -1001, stop.replace(RLM, ""))
    tpl = kbwords.shown_label(i18n.get("fa"), "Run tests", "Run the tests")
    assert kbwords.is_group_label(bot, -1001, tpl.replace(RLM, ""))
    assert not kbwords.is_group_label(bot, -1001, _label("fa", kbwords.KB_KILL))


def test_translated_words_never_look_like_session_names():
    from aipager.dtach.inject import _VALID_NAME
    for code in ("fa", "ru"):
        for msg in (*kbwords.WORDS.values(), *kbwords.NAV.values()):
            assert not _VALID_NAME.match(_label(code, msg).replace(RLM, "")), msg
    assert kbwords.label_problems(("en", "ru", "fa", "qps")) == []


def test_the_router_reads_a_translated_label_only_in_its_chats_language(bot):
    """A translated default label runs its command (Clear runs /clear) only
    in English and the chat's own language (review rev-iter1-005): a
    Russian "Clear" typed into a Persian or English chat is a message."""
    for code, others in (("fa", ("ru", "en")), ("ru", ("fa", "en"))):
        tr = i18n.get(code)
        for (label, payload), _msg in kbwords.DEFAULT_LABELS.items():
            kind = "cmd" if payload.startswith("/") else "tpl"
            shown = kbwords.shown_label(tr, label, payload)
            assert kbwords.custom_action(bot, shown, code) == (kind, payload), shown
            assert kbwords.custom_action(bot, shown.replace(RLM, ""), code) == (kind, payload)
            # English works in every chat.
            for chat in (code, *others):
                assert kbwords.custom_action(bot, label, chat) == (kind, payload)
            if kbwords.norm(shown) == label:
                continue  # kept English (Commit): matched as English
            for other in others:
                assert kbwords.custom_action(bot, shown, other) is None, (other, shown)


def _route(mk_bot, monkeypatch, run_async, chat_code, text):
    """What the text router does with *text* typed in a private chat set
    to *chat_code*: the commands and templates it sent."""
    from unittest.mock import AsyncMock, MagicMock

    from aipager import preferences
    from tests.test_claude_facing_golden import ALICE, DM, _bot
    preferences.set_ui_language(DM, chat_code)
    bot, _typed, _ = _bot(mk_bot, monkeypatch)
    bot._template_map = dict(config._DEFAULT_TEMPLATES)
    bot._command_map = dict(config._DEFAULT_COMMANDS)
    sent: list[str] = []

    async def _command(update, payload):
        sent.append(payload)

    async def _template(update, payload, *, button=""):
        sent.append(payload)
    bot._send_command = _command
    bot._send_template = _template
    update = MagicMock()
    update.message = MagicMock(text=text, message_id=9, reply_to_message=None,
                               quote=None, external_reply=None, forward_origin=None,
                               via_bot=None, media_group_id=None, caption=None)
    update.message.reply_text = AsyncMock()
    update.effective_user = MagicMock(id=ALICE)
    update.effective_chat = MagicMock(id=DM, type="private")
    run_async(bot._handle_message(update, MagicMock()))
    return sent


@pytest.mark.parametrize("chat_code, label_code, runs", [
    ("ru", "ru", True), ("fa", "fa", True), ("fa", "ru", False), ("ru", "fa", False),
    ("en", "ru", False), ("en", "fa", False), ("fa", "en", True), ("en", "en", True)])
def test_clear_typed_in_another_language_never_runs_clear(
        mk_bot, monkeypatch, run_async, chat_code, label_code, runs):
    clear = kbwords.shown_label(i18n.get(label_code), "Clear", "/clear")
    sent = _route(mk_bot, monkeypatch, run_async, chat_code, clear)
    assert sent == (["/clear"] if runs else []), (chat_code, label_code, clear)
