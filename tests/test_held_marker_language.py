"""An answer held through a Telegram rate limit opens with the late marker
in the chat's language (multilingual phase 3a, design section C).

held.py stays import-free and keeps its English ``LATE_MARKER``; the
daemon renders ``held_message.LATE_MARKER`` at delivery, counting the
minutes exactly as ``held.late_marker`` does. The marker opens both the
rich and the plain delivery, so no language's form may hold a Markdown or
HTML character."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import held as held_mod
from aipager.bot import held_message
from aipager.bot import notify as notify_mod
from aipager.i18n import core, locales
from aipager.state import Status, TrackedSession

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
CHAT = 555


def test_english_equals_held():
    en = i18n.get("en")
    for seconds in [*range(0, 7201, 7), 29.9, 30, 89.9, 90, 150.0]:
        assert held_message.late_marker_text(en, seconds) == held_mod.late_marker(seconds)
    for odd in (-5, "90", "abc", None, [], "1e3"):
        assert held_message.late_marker_text(en, odd) == held_mod.late_marker(odd), odd
    assert (held_message.LATE_MARKER.one == held_message.LATE_MARKER.other
            == held_mod.LATE_MARKER.replace("{minutes}", "{n}"))


def _bot(mk_bot, monkeypatch, code, *, rich_fails=False):
    preferences.set_ui_language(CHAT, code)
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._confirm_held = MagicMock()
    sent: list[str] = []

    async def _rich(chat, text, **kw):
        if rich_fails:
            raise RuntimeError("bad markdown")
        sent.append(text)
        return {"message_id": 1}
    monkeypatch.setattr(notify_mod, "send_rich_message", AsyncMock(side_effect=_rich))
    entry = SimpleNamespace(
        session="claude-x1", label="x1", chat_id=CHAT, key="k", rich_text="**done**",
        plain_text="done", reply_to=None,
        attempts=held_mod.HELD_ANSWER_MAX_ATTEMPTS - 1 if rich_fails else 0,
        held_seconds=lambda: 300.0)
    monkeypatch.setattr(held_mod.HELD, "pending", lambda chat: [entry])
    monkeypatch.setattr(held_mod.HELD, "drop", lambda chat, key: None)
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.IDLE)
    sess.scope_chat_id = CHAT
    bot.registry._sessions[sess.name] = sess
    return bot, sess, sent


def _first_line(text):
    return text.split("\n", 1)[0]


def test_a_persian_chat_gets_a_persian_marker(mk_bot, run_async, monkeypatch):
    bot, sess, sent = _bot(mk_bot, monkeypatch, "fa")
    run_async(bot.flush_held_answers(sess))
    marker = _first_line(sent[0])
    assert PERSIAN.search(marker) and "Telegram" in marker
    # Over an English answer it reads left to right with it, in Persian
    # (multilingual design 5.7, rule 5).
    assert marker == held_message.late_marker_text(i18n.get("fa").directed(False), 300)
    assert "۵" in marker and "delivered late" not in marker
    assert sent[0].endswith("\n\n**done**")


def test_a_russian_plain_delivery_opens_with_the_russian_marker(mk_bot, run_async,
                                                               monkeypatch):
    bot, sess, _sent = _bot(mk_bot, monkeypatch, "ru", rich_fails=True)
    run_async(bot.flush_held_answers(sess))
    text = bot._app.bot.send_message.await_args.args[1]
    marker = _first_line(text)
    assert CYRILLIC.search(marker) and "5 минут " in marker
    assert text.endswith("\n\ndone")


def test_an_english_chat_reads_held_s_own_marker(mk_bot, run_async, monkeypatch):
    bot, sess, sent = _bot(mk_bot, monkeypatch, "en")
    run_async(bot.flush_held_answers(sess))
    assert _first_line(sent[0]) == held_mod.late_marker(300)


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_no_markup_in_any_language(code):
    forms = core._catalog(code)[held_message.LATE_MARKER.key]
    assert set(forms) == set(core._plurals.categories(code))
    for form in forms.values():
        assert not re.search(r"[*_`\[\]<>&]", form), form


def test_every_enabled_language_has_the_marker():
    for code in locales.enabled_codes():
        if code != "en":
            assert held_message.LATE_MARKER.key in core._catalog(code), code


def test_a_persian_answer_keeps_a_right_to_left_marker(mk_bot, run_async, monkeypatch):
    bot, sess, sent = _bot(mk_bot, monkeypatch, "fa")
    (entry,) = held_mod.HELD.pending(CHAT)
    entry.rich_text = "\u067e\u0627\u0633\u062e"
    run_async(bot.flush_held_answers(sess))
    assert _first_line(sent[0]) == held_message.late_marker_text(i18n.get("fa"), 300)
