"""`/mode` with a translated mode word that is also a session name (7b).

In a Latin-script language a translated mode word can be a valid session
name (fr ``demander``, it ``chiedi``, id ``tanya``). When a LIVE session
with exactly that name exists in the chat, the word names that session;
otherwise it switches the mode. After a session name it is always the
mode. English ``ask``/``auto`` behave exactly as before: always the mode,
even with a session of that name.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import kbwords
from aipager.bot.mode_labels import MODE_ASK, MODE_AUTO, mode_from_word, parse_mode_args
from aipager.dtach.inject import _VALID_NAME
from aipager.i18n import get
from aipager.state import Status, TrackedSession

CHAT = 4242
LANGS = ("de", "es", "fr", "it", "pt-BR", "tr", "id", "vi")


def _colliding_words():
    """``(code, mode, word)`` for every translated mode word that fits the
    session-name pattern once lowered."""
    out = []
    for code in LANGS:
        tr = get(code)
        for mode, msg in (("ask", MODE_ASK), ("auto", MODE_AUTO)):
            word = kbwords.mode_word(tr.text(msg))
            if word in ("ask", "auto") or not _VALID_NAME.match(word):
                continue
            out.append((code, mode, word))
    return out


COLLIDING = _colliding_words()


def test_the_languages_that_collide_are_the_ones_the_translation_runs_named():
    words = {w for _c, _m, w in COLLIDING}
    assert {"demander", "automatique", "preguntar", "perguntar", "chiedi",
            "automatico", "fragen", "sor", "otomatik", "tanya"} <= words, words
    # vi "Hỏi" is not ASCII; vi/de "Auto" is the English id.
    assert "hỏi" not in words


def test_indonesian_ask_is_tanya_again():
    assert get("id").text(MODE_ASK) == "💬 Tanya"
    assert mode_from_word("tanya") == "ask" and mode_from_word("Tanya") == "ask"


# ---- the pure rule ------------------------------------------------------------------

@pytest.mark.parametrize("code, mode, word", COLLIDING)
def test_a_live_session_of_that_name_wins(code, mode, word):
    assert parse_mode_args([word], lambda n: n == word) == (None, word, True)


@pytest.mark.parametrize("code, mode, word", COLLIDING)
def test_without_one_the_word_switches_the_mode(code, mode, word):
    assert parse_mode_args([word], lambda n: False) == (mode, None, True)


@pytest.mark.parametrize("code, mode, word", COLLIDING)
def test_after_a_session_name_it_is_the_mode(code, mode, word):
    assert parse_mode_args(["x1", word], lambda n: True) == (mode, "x1", True)
    # And before one, with a session of that name too: it names the
    # session, and the second word is a second name, which is not ok.
    assert parse_mode_args([word, "x1"], lambda n: True)[2] is False


@pytest.mark.parametrize("word", ["ask", "auto", "Ask", "AUTO"])
def test_english_ids_are_always_the_mode(word):
    assert parse_mode_args([word], lambda n: True) == (word.lower(), None, True)
    assert parse_mode_args(["x1", word], lambda n: True) == (word.lower(), "x1", True)


def test_three_words_are_not_ok():
    assert parse_mode_args(["x1", "ask", "x2"], lambda n: False)[2] is False
    assert parse_mode_args(["x1", "x2"], lambda n: False)[2] is False
    assert parse_mode_args([], lambda n: False) == (None, None, True)


def test_a_slash_prefixed_word_is_a_name():
    assert parse_mode_args(["/x1"], lambda n: False) == (None, "x1", True)


# ---- through /mode --------------------------------------------------------------------

def _sess(bot, label, status=Status.IDLE):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.scope_chat_id = CHAT
    bot.registry._sessions[s.name] = s
    return s


@pytest.fixture
def bot(mk_bot):
    b = mk_bot()
    _sess(b, "x1")
    b.registry.last_active_session = "claude-x1"
    b._perms_flow = AsyncMock()
    b._render_mode_card = MagicMock(return_value=("card", None))
    return b


def _mode(bot, run_async, mk_update, text, mode=None):
    if mode is not None:
        # x1 in the other mode, so the switch is a real one.
        bot.registry._sessions["claude-x1"].skip_perms = mode == "ask"
    update = mk_update(text, chat_id=CHAT)
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=1))
    run_async(bot._handle_mode_cmd(update, MagicMock()))
    return update


@pytest.mark.parametrize("code, mode, word", COLLIDING)
def test_mode_word_names_an_existing_session(bot, run_async, mk_update, code, mode, word):
    _sess(bot, word)
    _mode(bot, run_async, mk_update, f"/mode {word}")
    bot._perms_flow.assert_not_awaited()
    assert bot._render_mode_card.call_args.args[1].label == word


@pytest.mark.parametrize("code, mode, word", COLLIDING)
def test_mode_word_switches_without_such_a_session(bot, run_async, mk_update, code, mode,
                                                   word):
    _mode(bot, run_async, mk_update, f"/mode {word}", mode)
    sess, want = bot._perms_flow.await_args.args[:2]
    assert sess.label == "x1" and want is (mode == "auto")


@pytest.mark.parametrize("code, mode, word", COLLIDING)
def test_a_gone_session_of_that_name_does_not_count(bot, run_async, mk_update, code, mode,
                                                    word):
    _sess(bot, word, status=Status.GONE)
    _mode(bot, run_async, mk_update, f"/mode {word}", mode)
    sess, want = bot._perms_flow.await_args.args[:2]
    assert sess.label == "x1" and want is (mode == "auto")


@pytest.mark.parametrize("code, mode, word", COLLIDING)
def test_second_position_is_the_mode(bot, run_async, mk_update, code, mode, word):
    _sess(bot, word)
    _mode(bot, run_async, mk_update, f"/mode x1 {word}", mode)
    sess, want = bot._perms_flow.await_args.args[:2]
    assert sess.label == "x1" and want is (mode == "auto")


@pytest.mark.parametrize("word, want", [("ask", False), ("auto", True)])
def test_english_switches_even_with_a_session_of_that_name(bot, run_async, mk_update, word,
                                                           want):
    _sess(bot, word)
    bot.registry.last_active_session = "claude-x1"
    _mode(bot, run_async, mk_update, f"/mode {word}", word)
    sess, got = bot._perms_flow.await_args.args[:2]
    assert sess.label == "x1" and got is want
