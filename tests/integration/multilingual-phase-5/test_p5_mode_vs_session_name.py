"""Black-box: ``/mode`` when a translated mode word is also a session name
(spec 7b; design.md section 7, D4; entrypoints.md ``/mode`` and
``mode_labels.parse_mode_args``).

Rule: when a LIVE session with exactly that name exists in the chat, the
argument names the session; otherwise a translated mode word switches the
mode; English ``ask``/``auto`` behave exactly as today (always the mode);
after a session name a translated mode word is always the mode; three words
or two labels give the usage reply.

Every language whose Ask or Auto word fits the session-name pattern once
lowered is covered: de, es, fr, it, pt-BR, tr, id (design.md Tests list).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n
from aipager.bot import kbwords
from aipager.bot.mode_labels import MODE_ASK, MODE_AUTO, mode_from_word, parse_mode_args
from aipager.state import Status
from tests import i18n_ml2_bb as bb

#: (language, typed word, the mode it means) per spec 7b and design.md.
WORDS = [
    ("fr", "demander", "ask"), ("fr", "automatique", "auto"),
    ("es", "preguntar", "ask"),
    ("pt-BR", "perguntar", "ask"),
    ("it", "chiedi", "ask"), ("it", "automatico", "auto"),
    ("de", "fragen", "ask"),
    ("tr", "sor", "ask"), ("tr", "otomatik", "auto"),
    ("id", "tanya", "ask"),
]
IDS = [f"{c}-{w}" for c, w, _m in WORDS]


def _never(_label):
    return False


def _always(_label):
    return True


# ---- the words are what the catalogs say ----------------------------------------------

@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_the_word_is_the_catalogs_mode_word(code, word, mode):
    key = MODE_ASK if mode == "ask" else MODE_AUTO
    assert kbwords.mode_word(i18n.get(code).text(key)).lower() == word


@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_the_word_is_a_mode_word(code, word, mode):
    assert mode_from_word(word) == mode


def test_indonesian_ask_reads_tanya():
    assert "Tanya" in i18n.get("id").text(MODE_ASK)


# ---- parse_mode_args: one word ------------------------------------------------------------

@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_without_a_session_of_that_name_the_word_switches_the_mode(code, word, mode):
    assert parse_mode_args([word], _never) == (mode, None, True)


@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_with_a_session_of_that_name_the_word_names_it(code, word, mode):
    assert parse_mode_args([word], lambda label: label == word) == (None, word, True)


@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_a_session_with_another_name_does_not_capture_the_word(code, word, mode):
    assert parse_mode_args([word], lambda label: label == "x1") == (mode, None, True)


@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_a_capitalised_word_switches_without_a_session(code, word, mode):
    assert parse_mode_args([word.capitalize()], _never) == (mode, None, True)


@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_the_lookup_is_asked_about_the_word_itself(code, word, mode):
    asked = []
    parse_mode_args([word], lambda label: asked.append(label) or False)
    assert word in asked


# ---- parse_mode_args: two words -----------------------------------------------------------

@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_after_a_session_name_the_word_is_the_mode(code, word, mode):
    assert parse_mode_args(["x1", word], _always) == (mode, "x1", True)


@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_before_a_session_name_a_named_session_word_is_a_label_then_two_labels_fail(
        code, word, mode):
    ok = parse_mode_args([word, "x1"], _always)[2]
    assert ok is False


@pytest.mark.parametrize("code, word, mode", WORDS, ids=IDS)
def test_a_word_then_a_session_switches_that_session(code, word, mode):
    assert parse_mode_args([word, "x1"], lambda label: label == "x1") == (mode, "x1", True)


def test_two_labels_are_not_ok():
    assert parse_mode_args(["x1", "x2"], _always)[2] is False


def test_three_words_are_not_ok():
    assert parse_mode_args(["x1", "auto", "x2"], _always)[2] is False


def test_no_words_is_ok_and_empty():
    assert parse_mode_args([], _never) == (None, None, True)


# ---- English is unchanged -------------------------------------------------------------------

@pytest.mark.parametrize("word, mode", [("ask", "ask"), ("auto", "auto"), ("ASK", "ask"),
                                        ("Auto", "auto")])
def test_english_words_are_the_mode_even_with_a_session_of_that_name(word, mode):
    assert parse_mode_args([word], _always) == (mode, None, True)


@pytest.mark.parametrize("word, mode", [("ask", "ask"), ("auto", "auto")])
def test_english_words_after_a_label_are_the_mode(word, mode):
    assert parse_mode_args(["x1", word], _always) == (mode, "x1", True)


@pytest.mark.parametrize("word, mode", [("ask", "ask"), ("auto", "auto")])
def test_english_words_are_the_mode_before_a_label(word, mode):
    assert parse_mode_args([word, "x1"], _always) == (mode, "x1", True)


def test_a_plain_label_is_a_label():
    assert parse_mode_args(["x1"], _always) == (None, "x1", True)


def test_an_unknown_word_without_a_session_is_still_a_label():
    assert parse_mode_args(["zz"], _never) == (None, "zz", True)


@pytest.mark.parametrize("code", ["de", "vi"])
def test_a_language_whose_auto_word_is_auto_keeps_it_a_mode(code):
    word = kbwords.mode_word(i18n.get(code).text(MODE_AUTO)).lower()
    assert parse_mode_args([word], _always)[0] == "auto"


# ---- through the handler: live, GONE, none --------------------------------------------------

def _run_mode(bot, run_async, text):
    update = bb.message(text, chat=bb.DM_A, user=bb.OWNER)
    run_async(bot._handle_mode_cmd(update, MagicMock()))
    call = update.message.reply_text.await_args
    return str(call.args[0]) if call else ""


@pytest.fixture
def chat(mk_bot):
    """The owner's DM with a current session x1 in Auto."""
    bot = bb.make_bot(mk_bot)
    x1 = bb.add_session(bot, chat=bb.DM_A, label="x1")
    x1.skip_perms = True
    bot.registry.last_active_session = x1.name
    bot._perms_flow = AsyncMock()
    return bot


def _switched(bot):
    return [(c.args[0].label, c.args[1]) for c in bot._perms_flow.await_args_list]


@pytest.mark.parametrize("word", ["demander", "preguntar", "chiedi", "tanya", "sor"])
def test_handler_a_live_session_of_that_name_gets_its_mode_card(chat, run_async, word):
    bb.add_session(chat, chat=bb.DM_A, label=word)
    assert f"<b>{word}</b>" in _run_mode(chat, run_async, f"/mode {word}")


@pytest.mark.parametrize("word", ["demander", "preguntar", "chiedi", "tanya", "sor"])
def test_handler_a_live_session_of_that_name_is_not_switched_away(chat, run_async, word):
    bb.add_session(chat, chat=bb.DM_A, label=word)
    _run_mode(chat, run_async, f"/mode {word}")
    assert _switched(chat) == []


@pytest.mark.parametrize("word", ["demander", "preguntar", "chiedi", "tanya", "sor"])
def test_handler_without_a_session_the_word_switches_the_current_one(chat, run_async, word):
    _run_mode(chat, run_async, f"/mode {word}")
    assert _switched(chat) == [("x1", False)]


@pytest.mark.parametrize("word", ["demander", "tanya"])
def test_handler_a_gone_session_of_that_name_does_not_count(chat, run_async, word):
    bb.add_session(chat, chat=bb.DM_A, label=word, status=Status.GONE)
    _run_mode(chat, run_async, f"/mode {word}")
    assert _switched(chat) == [("x1", False)]


def test_handler_a_session_of_that_name_in_another_chat_does_not_count(chat, run_async):
    bb.add_session(chat, chat=bb.DM_B, label="demander")
    _run_mode(chat, run_async, "/mode demander")
    assert _switched(chat) == [("x1", False)]


def test_handler_session_then_word_switches_that_session(chat, run_async):
    s = bb.add_session(chat, chat=bb.DM_A, label="demander")
    s.skip_perms = True
    _run_mode(chat, run_async, "/mode demander demander")
    assert _switched(chat) == [("demander", False)]


def test_handler_english_ask_with_a_session_named_ask_switches_the_current_one(chat, run_async):
    bb.add_session(chat, chat=bb.DM_A, label="ask")
    _run_mode(chat, run_async, "/mode ask")
    assert _switched(chat) == [("x1", False)]


def test_handler_english_auto_with_a_session_named_auto_switches_the_current_one(chat,
                                                                                 run_async):
    chat.registry.get(chat.registry.last_active_session).skip_perms = False
    bb.add_session(chat, chat=bb.DM_A, label="auto")
    _run_mode(chat, run_async, "/mode auto")
    assert _switched(chat) == [("x1", True)]


def test_handler_two_labels_give_the_usage_reply(chat, run_async):
    bb.add_session(chat, chat=bb.DM_A, label="demander")
    bb.add_session(chat, chat=bb.DM_A, label="x2")
    text = _run_mode(chat, run_async, "/mode demander x2")
    assert "/mode" in text and _switched(chat) == []
