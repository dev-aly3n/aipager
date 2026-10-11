"""Black-box, iteration 2: the pop-ups a refused prompt tap shows (session
not found, already answered, prompt expired) and the rate-limit toast on a
refused Retry, all in the TAPPER's language (entrypoints.md "Pop-ups after
a tap use the tapper's language", "The rate-limit toast on a refused
Retry"; design.md decision 13; review rev-iter1-002).

Driven through the real ``_handle_callback``.

Tapper partitions (design.md decision 13):
- a stored DM language that differs from the group's (ru tapper in a fa
  group, fa tapper in a ru group): the DM language;
- nothing stored, an English Telegram app: English (today's text);
- nothing stored, a Persian Telegram app: the group's language.
Refusal partitions: no such session / already answered (busy session,
stale prompt surface) / expired (a button on a message that is not the
current prompt).
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences
from aipager.bot.flood import MUTE
from aipager.state import Status
from tests.i18n_ml2_bb import (
    BOB,
    CAROL,
    CYRILLIC,
    DM_A,
    DM_B,
    DM_C,
    FA_DIGITS,
    GROUP,
    LATIN_DIGITS,
    OWNER,
    RLM,
    add_session,
    callback,
    catalog,
    make_bot,
    strip_marks,
    toast_texts,
)

PROMPT_VERBS = ["allow", "deny", "allow_always", "opt0", "submit", "stop", "pin_answer"]


@pytest.fixture
def bot(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    return make_bot(mk_bot)


def _tap(bot, run_async, data, *, user=BOB, language_code="fa", chat=GROUP,
         message_id=42):
    update, query = callback(data, chat=chat, user=user, language_code=language_code,
                             message_id=message_id)
    run_async(bot._handle_callback(update, MagicMock()))
    toasts = toast_texts(query)
    assert toasts, f"no toast for {data}"
    return toasts[-1]


def _ru(key):
    return catalog("ru")[key]["v"]


def _fa(key):
    return catalog("fa")[key]["v"]


@pytest.fixture
def ru_tapper_in_fa_group():
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(DM_B, "ru")


@pytest.fixture
def fa_tapper_in_ru_group():
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(DM_C, "fa")


# ---- session not found ---------------------------------------------------------------

@pytest.mark.parametrize("verb", PROMPT_VERBS)
def test_session_not_found_is_in_the_russian_tappers_language(
        bot, run_async, ru_tapper_in_fa_group, verb):
    toast = _tap(bot, run_async, f"claude-nobody:{verb}")
    assert toast == _ru("toast.session_not_found")


@pytest.mark.parametrize("verb", ["allow", "opt0", "stop"])
def test_session_not_found_is_in_the_persian_tappers_language(
        bot, run_async, fa_tapper_in_ru_group, verb):
    toast = _tap(bot, run_async, f"claude-nobody:{verb}", user=CAROL, language_code="ru")
    assert strip_marks(toast) == _fa("toast.session_not_found")


def test_a_persian_refusal_toast_starts_right_to_left(bot, run_async,
                                                     fa_tapper_in_ru_group):
    toast = _tap(bot, run_async, "claude-nobody:allow", user=CAROL, language_code="ru")
    assert toast.startswith(RLM)


def test_session_not_found_for_an_english_app_with_no_choice_is_english(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    toast = _tap(bot, run_async, "claude-nobody:allow", language_code="en")
    assert toast == "Session not found"


def test_session_not_found_for_a_persian_app_with_no_choice_uses_the_group(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    toast = _tap(bot, run_async, "claude-nobody:deny", language_code="fa")
    assert toast == _ru("toast.session_not_found")


def test_retry_on_a_gone_session_is_in_the_tappers_language(bot, run_async,
                                                           ru_tapper_in_fa_group):
    """Retry sits on the turn path next to its rate-limit toast (which
    entrypoints.md lists); its sibling refusal should follow the tapper."""
    toast = _tap(bot, run_async, "claude-nobody:retry")
    assert CYRILLIC.search(toast)


def test_a_short_form_prompt_button_for_a_gone_session_is_in_the_tappers_language(
        bot, run_async, ru_tapper_in_fa_group):
    """``_:sx:<n>:<verb>`` is the prompt-button form for sessions whose
    name is too long for callback data; a stale tap on it is the same
    refusal as ``claude-x:allow`` on a gone session."""
    toast = _tap(bot, run_async, "_:sx:99:allow")
    assert CYRILLIC.search(toast)


# ---- already answered ---------------------------------------------------------------------

def test_already_answered_on_a_busy_session_is_russian(bot, run_async,
                                                       ru_tapper_in_fa_group):
    s = add_session(bot, chat=GROUP, label="g1", status=Status.BUSY)
    assert _tap(bot, run_async, f"{s.name}:pin_answer") == _ru("toast.already_answered")


def test_already_answered_on_a_stale_prompt_surface_is_russian(bot, run_async,
                                                               ru_tapper_in_fa_group):
    s = add_session(bot, chat=GROUP, label="g1", status=Status.BUSY)
    bot.register_prompt_surface(GROUP, 4000, s, 7)
    toast = _tap(bot, run_async, f"{s.name}:allow", message_id=4000)
    assert toast == _ru("toast.already_answered")


def test_already_answered_for_a_persian_tapper_is_persian(bot, run_async,
                                                          fa_tapper_in_ru_group):
    s = add_session(bot, chat=GROUP, label="g1", status=Status.BUSY)
    toast = _tap(bot, run_async, f"{s.name}:pin_answer", user=CAROL, language_code="ru")
    assert strip_marks(toast) == _fa("toast.already_answered")


def test_already_answered_for_an_english_app_is_todays_text(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    s = add_session(bot, chat=GROUP, label="g1", status=Status.BUSY)
    toast = _tap(bot, run_async, f"{s.name}:pin_answer", language_code="en")
    assert toast == "already answered"


# ---- prompt expired ------------------------------------------------------------------------

def _waiting(bot):
    s = add_session(bot, chat=GROUP, label="g1", status=Status.INTERACTIVE)
    s.pending_permission = {"tool_summary": "Bash: ls", "tool_info": None}
    s.busy_msg_id = 71
    return s


@pytest.mark.parametrize("verb", ["allow", "deny", "opt0"])
def test_an_expired_prompt_tap_is_russian(bot, run_async, ru_tapper_in_fa_group, verb):
    s = _waiting(bot)
    toast = _tap(bot, run_async, f"{s.name}:{verb}", message_id=4242)
    assert toast == _ru("toast.prompt_expired")


def test_an_expired_prompt_tap_for_a_persian_tapper_is_persian(bot, run_async,
                                                               fa_tapper_in_ru_group):
    s = _waiting(bot)
    toast = _tap(bot, run_async, f"{s.name}:allow", user=CAROL, language_code="ru",
                 message_id=4242)
    assert strip_marks(toast) == _fa("toast.prompt_expired")


def test_an_expired_prompt_tap_for_an_english_app_is_todays_text(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    s = _waiting(bot)
    toast = _tap(bot, run_async, f"{s.name}:allow", language_code="en", message_id=4242)
    assert toast == "this prompt has expired"


def test_an_expired_tap_decides_nothing_in_any_language(bot, run_async,
                                                        ru_tapper_in_fa_group):
    s = _waiting(bot)
    _tap(bot, run_async, f"{s.name}:allow", message_id=4242)
    assert s.status is Status.INTERACTIVE


# ---- the rate-limit toast on a refused Retry -----------------------------------------------

def _rate_limit_toast(bot, run_async, *, chat, user, language_code="fa"):
    """The toast Retry hands the dispatcher while the chat is muted. The
    dispatcher withholds every answer during a ban, so the offered text is
    read where it is handed over (the same seam the flood suite uses)."""
    kind = "dm" if chat > 0 else "group"
    s = add_session(bot, chat=chat, label="x1", status=Status.IDLE)
    assert s.scope_kind == kind
    s.last_prompt = "the prompt"
    bot._inject_prompt = AsyncMock(return_value=True)
    offered: list = []

    async def _answer(query, text=None, **kw):
        offered.append(text)

    bot._safe_answer = _answer
    MUTE.mute(chat, 600)
    update, _q = callback(f"{s.name}:retry", chat=chat, user=user,
                          language_code=language_code)
    run_async(bot._handle_callback(update, MagicMock()))
    texts = [t for t in offered if t]
    assert len(texts) == 1, offered
    return texts[0], bot


def _prefix(code):
    return _ru("toast.rate_limited").split("{time}")[0] if code == "ru" else None


def test_the_russian_rate_limit_toast_reads_russian(bot, run_async):
    preferences.set_ui_language(DM_A, "ru")
    toast, _b = _rate_limit_toast(bot, run_async, chat=DM_A, user=OWNER)
    assert toast.startswith(_prefix("ru"))


def test_the_russian_rate_limit_toast_keeps_a_latin_clock(bot, run_async):
    preferences.set_ui_language(DM_A, "ru")
    toast, _b = _rate_limit_toast(bot, run_async, chat=DM_A, user=OWNER)
    assert re.search(r"\d{1,2}:\d\d$", toast)


def test_the_persian_rate_limit_toast_has_a_persian_clock(bot, run_async):
    preferences.set_ui_language(DM_A, "fa")
    toast, _b = _rate_limit_toast(bot, run_async, chat=DM_A, user=OWNER)
    assert re.search("[۰-۹]{1,2}:[۰-۹]{2}", toast)


def test_the_persian_rate_limit_toast_has_no_latin_digit(bot, run_async):
    preferences.set_ui_language(DM_A, "fa")
    toast, _b = _rate_limit_toast(bot, run_async, chat=DM_A, user=OWNER)
    assert not LATIN_DIGITS.search(toast)


def test_the_persian_rate_limit_toast_starts_right_to_left(bot, run_async):
    preferences.set_ui_language(DM_A, "fa")
    toast, _b = _rate_limit_toast(bot, run_async, chat=DM_A, user=OWNER)
    assert toast.startswith(RLM)


def test_the_persian_rate_limit_toast_keeps_the_telegram_name(bot, run_async):
    preferences.set_ui_language(DM_A, "fa")
    toast, _b = _rate_limit_toast(bot, run_async, chat=DM_A, user=OWNER)
    assert "Telegram" in toast and FA_DIGITS.search(toast)


def test_the_english_rate_limit_toast_is_todays_text(bot, run_async):
    toast, _b = _rate_limit_toast(bot, run_async, chat=DM_A, user=OWNER, language_code="en")
    assert toast.startswith("Telegram is rate-limiting this chat - try again after ")


def test_the_rate_limit_toast_in_a_persian_group_follows_a_russian_tapper(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(DM_B, "ru")
    toast, _b = _rate_limit_toast(bot, run_async, chat=GROUP, user=BOB)
    assert toast.startswith(_prefix("ru"))


def test_a_refused_retry_injects_nothing_in_a_persian_chat(bot, run_async):
    preferences.set_ui_language(DM_A, "fa")
    _t, b = _rate_limit_toast(bot, run_async, chat=DM_A, user=OWNER)
    b._inject_prompt.assert_not_awaited()
