"""Black-box, iteration 2: a translated default template or command label
acts only in English and in the chat's own language (the change made after
review rev-iter1-005). Everywhere else the typed word is ordinary text and
goes to Claude as a prompt.

Driven through the real message handler (``_handle_message``) with the
dtach side mocked, so what Claude would read is observed directly.

Partitions per (chat language, label language):
- label in English: acts in every chat;
- label in the chat's own language: acts;
- label in another enabled language: goes to Claude as text, never as the
  English prompt or command.
Label kinds: a template (a prompt) and a command (``/clear``, a destructive
one, ``/compact``). Typed-form variants for the own-language label: the
invisible marks dropped, Arabic ي/ك, the ZWNJ dropped.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import kbwords
from aipager.state import Status
from tests.i18n_ml2_bb import DM_A, add_session, make_bot, message, strip_marks

TEMPLATE = ("Run tests", "Run the tests")
CLEAR = ("Clear", "/clear")
COMPACT = ("Compact", "/compact")
MARKER = "[via Telegram · @alice]\n"


@pytest.fixture
def wired(mk_bot, monkeypatch):
    bot = make_bot(mk_bot)
    s = add_session(bot, chat=DM_A, label="x1")
    bot.registry.last_active_session = s.name
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    return bot, s, sent


def _label(code, pair):
    return strip_marks(kbwords.shown_label(i18n.get(code), *pair))


def _claude_reads(wired, run_async, chat_code, typed):
    """The texts the session received when *typed* was sent in a chat set
    to *chat_code* ("en" = nothing stored)."""
    bot, sess, sent = wired
    preferences.set_ui_language(DM_A, "" if chat_code == "en" else chat_code)
    sess.status = Status.IDLE
    sent.reset_mock()
    run_async(bot._handle_message(message(typed), MagicMock()))
    return [c.args[1] for c in sent.await_args_list]


# ---- the label acts in its own chat language and in English -------------------------

@pytest.mark.parametrize("code", ["fa", "ru"])
def test_an_own_language_template_label_sends_the_english_prompt(wired, run_async, code):
    got = _claude_reads(wired, run_async, code, _label(code, TEMPLATE))
    assert got == [MARKER + TEMPLATE[1]]


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_an_own_language_command_label_sends_the_english_command(wired, run_async, code):
    assert _claude_reads(wired, run_async, code, _label(code, COMPACT)) == [COMPACT[1]]


@pytest.mark.parametrize("chat", ["en", "fa", "ru"])
def test_the_english_template_label_acts_in_every_chat(wired, run_async, chat):
    assert _claude_reads(wired, run_async, chat, TEMPLATE[0]) == [MARKER + TEMPLATE[1]]


@pytest.mark.parametrize("chat", ["en", "fa", "ru"])
def test_the_english_clear_label_acts_in_every_chat(wired, run_async, chat):
    assert _claude_reads(wired, run_async, chat, CLEAR[0]) == [CLEAR[1]]


# ---- a label in another language is plain text -------------------------------------------

CROSS = [("en", "fa"), ("en", "ru"), ("fa", "ru"), ("ru", "fa")]


@pytest.mark.parametrize("chat, label_code", CROSS)
def test_a_foreign_clear_label_never_runs_clear(wired, run_async, chat, label_code):
    got = _claude_reads(wired, run_async, chat, _label(label_code, CLEAR))
    assert CLEAR[1] not in got


@pytest.mark.parametrize("chat, label_code", CROSS)
def test_a_foreign_clear_label_reaches_claude_as_typed(wired, run_async, chat, label_code):
    typed = _label(label_code, CLEAR)
    assert _claude_reads(wired, run_async, chat, typed) == [MARKER + typed]


@pytest.mark.parametrize("chat, label_code", CROSS)
def test_a_foreign_template_label_never_sends_the_english_prompt(wired, run_async, chat,
                                                                  label_code):
    got = _claude_reads(wired, run_async, chat, _label(label_code, TEMPLATE))
    assert MARKER + TEMPLATE[1] not in got


@pytest.mark.parametrize("chat, label_code", CROSS)
def test_a_foreign_template_label_reaches_claude_as_typed(wired, run_async, chat,
                                                          label_code):
    typed = _label(label_code, TEMPLATE)
    assert _claude_reads(wired, run_async, chat, typed) == [MARKER + typed]


# ---- typed forms of the own-language label -----------------------------------------------

def test_a_persian_label_typed_with_arabic_ye_acts(wired, run_async):
    typed = _label("fa", TEMPLATE).replace("ی", "ي").replace("ک", "ك")
    assert typed != _label("fa", TEMPLATE)          # premise: the label has ی or ک
    assert _claude_reads(wired, run_async, "fa", typed) == [MARKER + TEMPLATE[1]]


def test_a_persian_label_typed_without_its_zwnj_acts(wired, run_async):
    typed = _label("fa", TEMPLATE).replace("‌", "")
    assert _claude_reads(wired, run_async, "fa", typed) == [MARKER + TEMPLATE[1]]


def test_a_russian_label_in_capitals_is_matched_like_the_english_one(wired, run_async):
    """English labels match case-sensitively ("RUN TESTS" is a prompt), so
    the translated label must not act more loosely than the English one."""
    english = _claude_reads(wired, run_async, "en", TEMPLATE[0].upper())
    typed = _label("ru", TEMPLATE).upper()
    russian = _claude_reads(wired, run_async, "ru", typed)
    assert (russian == [MARKER + typed]) == (english == [MARKER + TEMPLATE[0].upper()])


def test_a_persian_label_with_its_own_mark_acts(wired, run_async):
    typed = kbwords.shown_label(i18n.get("fa"), *TEMPLATE)
    assert _claude_reads(wired, run_async, "fa", typed) == [MARKER + TEMPLATE[1]]


def test_a_persian_label_with_its_mark_in_a_russian_chat_is_text(wired, run_async):
    typed = kbwords.shown_label(i18n.get("fa"), *CLEAR)
    assert CLEAR[1] not in _claude_reads(wired, run_async, "ru", typed)

