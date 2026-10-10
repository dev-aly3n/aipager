"""Black-box: keyboard words, default template and command labels, and
/mode words in Persian and Russian (entrypoints.md ``aipager.bot.kbwords``
and ``mode_labels.mode_from_word``; design.md success criterion "Keyboard
words, template and command labels, /mode words ... work in fa and ru
chats. Claude receives the English prompts and commands").

Partitions per typed word: exactly as shown / mark dropped / mark added /
U+FE0F added / Arabic ي and ك / an NFKC presentation form / other case;
invalid: leading ``/``, a second token, a session name, an unrelated word.
Label origin: default pair / keyboard.json override / model.
"""

from __future__ import annotations

import re

import pytest

from aipager import i18n
from aipager.bot import kbwords
from aipager.bot.mode_labels import mode_from_word
from tests.i18n_ml2_bb import GROUP, RLM, catalog, make_bot

BARE = ("kb.status", "kb.stop", "kb.new", "kb.kill")
NAV = ("kb.templates", "kb.commands", "kb.models", "kb.back")
MSGS = {"kb.status": kbwords.KB_STATUS, "kb.stop": kbwords.KB_STOP,
        "kb.new": kbwords.KB_NEW, "kb.kill": kbwords.KB_KILL,
        "kb.templates": kbwords.KB_TEMPLATES, "kb.commands": kbwords.KB_COMMANDS,
        "kb.models": kbwords.KB_MODELS, "kb.back": kbwords.KB_BACK}
LANGS = ("fa", "ru")


def _word(code, key):
    return i18n.get(code).text(MSGS[key])


def _en(key):
    return i18n.get("en").text(MSGS[key])


@pytest.fixture
def bot(mk_bot):
    return make_bot(mk_bot)


# ---- bare words (status, stop, new, kill) ----------------------------------------

@pytest.mark.parametrize("code", LANGS)
@pytest.mark.parametrize("key", BARE)
def test_a_translated_bare_word_means_the_english_one(code, key):
    assert kbwords.bare_word(_word(code, key)) == kbwords.bare_word(_en(key))


@pytest.mark.parametrize("key", BARE)
def test_the_english_bare_word_still_works(key):
    assert kbwords.bare_word(_en(key)) is not None


@pytest.mark.parametrize("key", BARE)
def test_a_persian_bare_word_without_its_mark(key):
    assert kbwords.bare_word(_word("fa", key).replace(RLM, "")) == kbwords.bare_word(_en(key))


@pytest.mark.parametrize("key", BARE)
def test_a_russian_bare_word_with_a_stray_mark(key):
    assert kbwords.bare_word(RLM + _word("ru", key)) == kbwords.bare_word(_en(key))


@pytest.mark.parametrize("key", BARE)
def test_a_russian_bare_word_in_capitals(key):
    assert kbwords.bare_word(_word("ru", key).upper()) == kbwords.bare_word(_en(key))


@pytest.mark.parametrize("key", BARE)
def test_a_persian_bare_word_with_a_variation_selector(key):
    assert kbwords.bare_word(_word("fa", key) + "️") == kbwords.bare_word(_en(key))


@pytest.mark.parametrize("key", BARE)
def test_a_persian_bare_word_with_an_isolate_around_it(key):
    word = "\u2068" + _word("fa", key).replace(RLM, "") + "\u2069"
    assert kbwords.bare_word(word) == kbwords.bare_word(_en(key))


def _arabic_variants():
    out = []
    for key in BARE + NAV:
        word = _word("fa", key)
        if "ی" in word or "ک" in word:
            out.append((key, word.replace("ی", "ي").replace("ک", "ك")))
    return out


def test_some_persian_keyboard_word_has_ye_or_kaf():
    assert _arabic_variants()


@pytest.mark.parametrize("key, typed", _arabic_variants())
def test_arabic_ye_and_kaf_resolve(key, typed):
    if key in BARE:
        assert kbwords.bare_word(typed) == kbwords.bare_word(_en(key))
    else:
        assert kbwords.nav_action(typed) == kbwords.nav_action(_en(key))


def test_a_presentation_form_letter_resolves_through_nfkc():
    word = _word("fa", "kb.status").replace(RLM, "")
    assert word.startswith("و")  # و
    typed = "ﻭ" + word[1:]       # isolated presentation form of و
    assert kbwords.bare_word(typed) == kbwords.bare_word(_en("kb.status"))


@pytest.mark.parametrize("code", LANGS)
def test_a_bare_word_with_a_slash_is_not_a_keyboard_word(code):
    assert kbwords.bare_word("/" + _word(code, "kb.status").replace(RLM, "")) is None


@pytest.mark.parametrize("code", LANGS)
def test_a_bare_word_with_a_second_token_is_not_a_keyboard_word(code):
    assert kbwords.bare_word(_word(code, "kb.stop") + " x1") is None


@pytest.mark.parametrize("text", ["x1", "X1", "hello", "", " "])
def test_a_session_name_or_chatter_is_not_a_bare_word(text):
    assert kbwords.bare_word(text) is None


@pytest.mark.parametrize("code", LANGS)
@pytest.mark.parametrize("key", BARE)
def test_a_translated_bare_word_is_one_token(code, key):
    assert " " not in _word(code, key).strip()


@pytest.mark.parametrize("code", LANGS)
@pytest.mark.parametrize("key", BARE + NAV)
def test_a_translated_keyboard_word_can_never_be_a_session_name(code, key):
    word = _word(code, key).replace(RLM, "")
    assert not re.fullmatch(r"[A-Za-z0-9_.\-]+", word)


# ---- navigation words ------------------------------------------------------------

@pytest.mark.parametrize("code", LANGS)
@pytest.mark.parametrize("key", NAV)
def test_a_translated_nav_word_means_the_english_one(code, key):
    assert kbwords.nav_action(_word(code, key)) == kbwords.nav_action(_en(key))


@pytest.mark.parametrize("key", NAV)
def test_a_persian_nav_word_without_its_mark(key):
    assert kbwords.nav_action(_word("fa", key).replace(RLM, "")) == kbwords.nav_action(_en(key))


@pytest.mark.parametrize("key", NAV)
def test_the_english_nav_word_resolves(key):
    assert kbwords.nav_action(_en(key)) is not None


def test_a_session_name_is_not_a_nav_word():
    assert kbwords.nav_action("x1") is None


@pytest.mark.parametrize("code", LANGS)
def test_classify_reads_a_translated_word_like_the_english(bot, code):
    assert kbwords.classify(bot, _word(code, "kb.status")) == kbwords.classify(bot, "status")


def test_classify_does_not_take_a_session_name(bot):
    assert kbwords.classify(bot, "x1") is None


# ---- default template and command labels ----------------------------------------

def _defaults(bot):
    return ([("tpl", lb, p) for lb, p in bot._template_map.items()]
            + [("cmd", lb, p) for lb, p in bot._command_map.items()])


def test_the_bot_has_the_thirteen_default_pairs(bot):
    assert len(_defaults(bot)) == 13


@pytest.mark.parametrize("code", LANGS)
def test_a_translated_default_label_returns_the_english_payload(bot, code):
    tr = i18n.get(code)
    got = [kbwords.custom_action(bot, kbwords.shown_label(tr, lb, p))
           for _k, lb, p in _defaults(bot)]
    assert got == [(k, p) for k, _lb, p in _defaults(bot)]


@pytest.mark.parametrize("code", LANGS)
def test_a_translated_default_label_without_marks_is_accepted(bot, code):
    tr = i18n.get(code)
    got = [kbwords.custom_action(bot, kbwords.shown_label(tr, lb, p).replace(RLM, ""))
           for _k, lb, p in _defaults(bot)]
    assert got == [(k, p) for k, _lb, p in _defaults(bot)]


def test_the_english_default_label_keeps_working(bot):
    assert kbwords.custom_action(bot, "Run tests") == ("tpl", "Run the tests")


def test_a_shown_default_label_is_translated():
    assert kbwords.shown_label(i18n.get("ru"), "Continue", "Continue") == \
        catalog("ru")["kbl.tpl.continue"]["v"]


def test_english_shown_label_is_the_label():
    assert kbwords.shown_label(i18n.get("en"), "Run tests", "Run the tests") == "Run tests"


def test_a_keyboard_json_label_is_never_translated():
    assert kbwords.shown_label(i18n.get("ru"), "Continue", "carry on please") == "Continue"


def test_a_custom_label_name_is_shown_as_written():
    assert kbwords.shown_label(i18n.get("fa"), "Deploy", "deploy it") == "Deploy"


@pytest.mark.parametrize("label, payload", [("Opus", "/model opus"), ("Sonnet 5.5",
                                             "/model claude-sonnet-5-5")])
def test_a_model_label_is_never_translated(label, payload):
    assert kbwords.shown_label(i18n.get("ru"), label, payload) == label


def test_an_overridden_default_label_drops_its_translation(bot):
    bot._template_map = {"Continue": "carry on please"}
    ru = catalog("ru")["kbl.tpl.continue"]["v"]
    assert kbwords.custom_action(bot, ru) is None


def test_an_overridden_default_label_still_works_in_english(bot):
    bot._template_map = {"Continue": "carry on please"}
    assert kbwords.custom_action(bot, "Continue") == ("tpl", "carry on please")


def test_a_removed_default_label_drops_its_translation(bot):
    bot._command_map = {}
    ru = catalog("ru")["kbl.cmd.compact"]["v"]
    assert kbwords.custom_action(bot, ru) is None


def test_chatter_is_not_a_custom_action(bot):
    assert kbwords.custom_action(bot, "سلام") is None


# ---- group labels ----------------------------------------------------------------

@pytest.mark.parametrize("code", LANGS)
@pytest.mark.parametrize("key", ("kb.status", "kb.stop", "kb.new", "kb.templates"))
def test_a_group_tap_in_either_language_is_a_group_label(bot, code, key):
    assert kbwords.is_group_label(bot, GROUP, _word(code, key))


@pytest.mark.parametrize("key", ("kb.status", "kb.templates"))
def test_a_persian_group_tap_without_its_mark_is_a_group_label(bot, key):
    assert kbwords.is_group_label(bot, GROUP, _word("fa", key).replace(RLM, ""))


def test_a_persian_group_tap_with_arabic_ye_is_a_group_label(bot):
    typed = _word("fa", "kb.status").replace("ی", "ي")
    assert kbwords.is_group_label(bot, GROUP, typed)


@pytest.mark.parametrize("code", LANGS)
def test_a_translated_default_label_is_a_group_label(bot, code):
    label = kbwords.shown_label(i18n.get(code), "Run tests", "Run the tests")
    assert kbwords.is_group_label(bot, GROUP, label)


def test_group_labels_hold_the_persian_and_russian_words(bot):
    labels = {x.replace(RLM, "") for x in kbwords.group_labels(bot, GROUP)}
    want = {_word("fa", "kb.status").replace(RLM, ""), _word("ru", "kb.status")}
    assert want <= labels


def test_chatter_is_not_a_group_label(bot):
    assert not kbwords.is_group_label(bot, GROUP, "سلام")


# ---- /mode words -----------------------------------------------------------------

def _mode_word(code, key):
    value = catalog(code)[key]["v"]
    return re.sub(r"[^\w\s\u200c-]", "", value).strip()


@pytest.mark.parametrize("word, want", [("ask", "ask"), ("auto", "auto"), ("AUTO", "auto"),
                                        ("Ask", "ask")])
def test_mode_accepts_the_english_ids(word, want):
    assert mode_from_word(word) == want


@pytest.mark.parametrize("code", LANGS)
@pytest.mark.parametrize("key, want", [("mode.word.ask", "ask"), ("mode.word.auto", "auto")])
def test_mode_accepts_the_translated_word(code, key, want):
    assert mode_from_word(_mode_word(code, key)) == want


@pytest.mark.parametrize("key, want", [("mode.word.ask", "ask"), ("mode.word.auto", "auto")])
def test_mode_accepts_the_russian_word_in_any_case(key, want):
    assert mode_from_word(_mode_word("ru", key).upper()) == want


@pytest.mark.parametrize("key, want", [("mode.word.ask", "ask"), ("mode.word.auto", "auto")])
def test_mode_accepts_the_persian_word_with_its_emoji(key, want):
    emoji = "💬" if want == "ask" else "🤖"
    assert mode_from_word(emoji + catalog("fa")[key]["v"]) == want


def test_mode_accepts_the_persian_word_with_a_mark():
    assert mode_from_word(RLM + _mode_word("fa", "mode.word.auto")) == "auto"


@pytest.mark.parametrize("word", ["x1", "ask1", "automatic", "", "status",
                                  "سلام"])
def test_mode_rejects_anything_else(word):
    assert mode_from_word(word) is None


def test_the_two_mode_words_differ_in_each_language():
    assert all(_mode_word(c, "mode.word.ask").lower() != _mode_word(c, "mode.word.auto").lower()
               for c in LANGS)
