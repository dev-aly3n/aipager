"""In a group set to Persian, a member whose own chat is Russian gets the
pop-ups of their taps in Russian, while what the group sees stays Persian
(multilingual design 6; G71)."""

from __future__ import annotations

import re

import pytest

from aipager import preferences
from tests.test_separate_permission_prompt import BOB, GROUP, _World

CYRILLIC = re.compile("[Ѐ-ӿ]")
PERSIAN = re.compile("[؀-ۿ]")


@pytest.fixture
def world(mk_bot, monkeypatch):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    return _World(mk_bot, monkeypatch)


def _toasts(query):
    return [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]


def test_a_russian_member_gets_russian_toasts_in_a_persian_group(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    query = world.tap(run_async, GROUP, BOB, "deny")
    toasts = _toasts(query)
    assert toasts and all(CYRILLIC.search(t) and not PERSIAN.search(t) for t in toasts)
    line = world.lines(GROUP)[-1][0]
    assert PERSIAN.search(line) and not CYRILLIC.search(line)


def test_a_member_with_no_choice_gets_the_groups_language(world, run_async):
    preferences.set_ui_language(BOB, "")
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    toasts = _toasts(world.tap(run_async, GROUP, BOB, "allow"))
    assert toasts and all(PERSIAN.search(t) for t in toasts)


def _ru(msg, **params):
    from aipager import i18n
    return i18n.get("ru").text(msg, **params)


def test_a_double_tap_is_refused_in_the_tappers_language(world, run_async):
    """The refusals after a prompt tap are pop-ups too (rev-iter1-002):
    a second tap on an answered prompt says "already answered" in the
    tapper's language, not in English."""
    from aipager.bot.dashboard import ALREADY_ANSWERED
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    world.tap(run_async, GROUP, BOB, "allow")
    assert _toasts(world.tap(run_async, GROUP, BOB, "allow")) == [_ru(ALREADY_ANSWERED)]


def test_a_tap_on_a_stale_message_is_refused_in_the_tappers_language(world, run_async):
    from aipager.bot.callbacks import PROMPT_EXPIRED
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    query = world.tap(run_async, GROUP, BOB, "allow", msg_id=12345)
    assert _toasts(query) == [_ru(PROMPT_EXPIRED)]
    assert world.keys == []


def test_a_tap_for_a_dead_session_is_refused_in_the_tappers_language(world, run_async,
                                                                     monkeypatch):
    from unittest.mock import AsyncMock

    from aipager.bot.callbacks import SESSION_GONE
    from aipager.dtach import inject
    from tests.test_separate_permission_prompt import S
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=False))
    query = world.tap(run_async, GROUP, BOB, "allow")
    assert _toasts(query) == [_ru(SESSION_GONE, session=S)]
    assert S in _toasts(query)[0]


def test_a_tap_for_an_unknown_session_is_refused_in_the_tappers_language(world, run_async):
    from aipager.bot.callbacks import NOT_FOUND
    query = world.tap(run_async, GROUP, BOB, "allow", name="claude-gone__g1001")
    assert _toasts(query) == [_ru(NOT_FOUND)]


def test_the_bars_answer_for_an_unknown_session_is_refused_in_the_tappers_language(
        world, run_async):
    from aipager.bot.callbacks import NOT_FOUND
    from aipager.bot.dashboard import PINNED_ANSWER_VERB
    query = world.tap(run_async, GROUP, BOB, PINNED_ANSWER_VERB, name="claude-gone__g1001")
    assert _toasts(query) == [_ru(NOT_FOUND)]


def test_retry_for_an_unknown_session_is_refused_in_the_tappers_language(world, run_async):
    from aipager.bot.callbacks import NOT_FOUND
    query = world.tap(run_async, GROUP, BOB, "retry", name="claude-gone__g1001")
    assert _toasts(query) == [_ru(NOT_FOUND)]


def test_a_stale_short_form_tap_is_refused_in_the_tappers_language(world, run_async):
    """``_:sx:<n>:<verb>`` is the prompt button for a long session name; an
    index nobody registered is the same stale-tap refusal (tester-iter2-002)."""
    from aipager.bot.callbacks import SHORT_CB_GONE
    query = world.tap(run_async, GROUP, BOB, "sx:99:allow", name="_")
    assert _toasts(query) == [_ru(SHORT_CB_GONE)]
    assert world.keys == []


@pytest.mark.parametrize(("name", "verb", "text"), [
    ("claude-gone__g1001", "retry", "Session not found"),
    ("_", "sx:99:allow", "That session is no longer available"),
])
def test_the_english_refusals_are_todays_text(world, run_async, name, verb, text):
    preferences.set_ui_language(GROUP, "en")
    preferences.set_ui_language(BOB, "en")
    assert _toasts(world.tap(run_async, GROUP, BOB, verb, name=name)) == [text]
