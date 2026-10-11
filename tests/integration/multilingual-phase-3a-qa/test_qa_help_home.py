"""Success criterion 13 (and the home screen of 17): /help in a Persian or
Russian private chat and group is fully translated, keeps every command and
the bot's @name whole, and has the same lines as the English; /start's home
screen and its buttons are translated too."""

from __future__ import annotations

import re

import pytest

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
SCRIPT = {"fa": PERSIAN, "ru": CYRILLIC}
#: Latin words a translation keeps: product, tool and key names, "diff"
#: (the git word), HTML entities.
VERBATIM = {"aipager", "Claude", "Code", "Telegram", "Tab", "diff", "lt", "gt",
            "x1", "g1", "x3"}
HELPS = ("help_dm", "help_group", "help_group_unnamed")

@pytest.fixture
def render(scenario_out):
    return scenario_out


def _latin_words(text):
    prose = re.sub(r"<code>.*?</code>|<[^>]+>|/\w+|@\w+", "", text, flags=re.S)
    return set(re.findall(r"[A-Za-z][A-Za-z0-9]+", prose))


CASES = [(n, lang) for n in HELPS for lang in ("fa", "ru")]


@pytest.mark.parametrize("name,lang", CASES)
def test_help_is_one_message(render, name, lang):
    assert len(render(name, lang)) == 1


@pytest.mark.parametrize("name,lang", CASES)
def test_help_has_no_english_word_left(render, name, lang):
    assert _latin_words(render(name, lang)[0]) - VERBATIM == set()


@pytest.mark.parametrize("name,lang", CASES)
def test_help_is_in_the_chats_script(render, name, lang):
    assert SCRIPT[lang].search(render(name, lang)[0])


@pytest.mark.parametrize("name,lang", CASES)
def test_help_has_the_english_line_count(render, name, lang):
    assert render(name, lang)[0].count("\n") == render(name, "en")[0].count("\n")


@pytest.mark.parametrize("name,lang", CASES)
def test_help_keeps_every_command(render, name, lang):
    commands = set(re.findall(r"(?<![\w;])/[a-z]+", render(name, "en")[0]))
    assert {c for c in commands if c not in render(name, lang)[0]} == set()


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_group_help_names_the_bot_whole(render, lang):
    assert "@aipager_test_bot" in render("help_group", lang)[0]


@pytest.mark.parametrize("lang", ["en", "fa", "ru"])
def test_group_help_without_a_username_names_no_bot(render, lang):
    assert "@" not in render("help_group_unnamed", lang)[0]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_dm_and_group_talk_lines_differ(render, lang):
    assert render("help_dm", lang)[0] != render("help_group", lang)[0]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_named_and_unnamed_group_lines_differ(render, lang):
    assert render("help_group", lang)[0] != render("help_group_unnamed", lang)[0]


def test_english_dm_help_equals_the_exported_help_text(render):
    from aipager.bot import handlers
    assert render("help_dm", "en")[0] == handlers.HELP_TEXT


def test_english_group_help_differs_from_dm_only_in_the_talk_line(render):
    dm = render("help_dm", "en")[0].split("\n")
    group = render("help_group", "en")[0].split("\n")
    assert [i for i, (a, b) in enumerate(zip(dm, group)) if a != b] == [3]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_translated_group_help_differs_from_dm_only_in_the_talk_line(render, lang):
    dm = render("help_dm", lang)[0].split("\n")
    group = render("help_group", lang)[0].split("\n")
    assert [i for i, (a, b) in enumerate(zip(dm, group)) if a != b] == [3]


# ---- the home screen ----------------------------------------------------------------------

@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_home_text_has_no_english_word_left(render, lang):
    texts = [t for t in render("home_screens", lang) if not t.startswith("[btn]")]
    assert set().union(*(_latin_words(t) for t in texts)) - VERBATIM == set()


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_home_buttons_are_translated(render, lang):
    buttons = [t for t in render("home_screens", lang) if t.startswith("[btn]")]
    assert buttons and all(SCRIPT[lang].search(b) for b in buttons)


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_home_keeps_new_and_help_tappable(render, lang):
    first = render("home_screens", lang)[0]
    assert "/new" in first and "/help" in first


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_home_resume_button_only_when_something_can_be_resumed(render, lang):
    en = render("home_screens", "en")
    other = render("home_screens", lang)
    assert [i for i, t in enumerate(other) if t.startswith("[btn]")] == \
        [i for i, t in enumerate(en) if t.startswith("[btn]")]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_home_group_line_names_the_bot_whole(render, lang):
    group = render("home_screens", lang)[6]
    assert "@aipager_test_bot" in group
