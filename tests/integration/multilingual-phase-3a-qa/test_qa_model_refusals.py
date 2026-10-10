"""Success criterion 15: model-switch refusals read in the tapper's (pop-up)
language, while the Mini App's English is today's: ``MODEL_REFUSAL_MSGS``
render to exactly ``miniapp.sessions.MODEL_SWITCH_*_REASON`` in English and
``model_switch_refusal`` still returns the English."""

from __future__ import annotations

import re

import pytest
import qa_p3a_scenarios as S

from aipager import i18n
from aipager.bot import session_ops
from aipager.miniapp import sessions as mini

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")

MINI = {"not_live": "MODEL_SWITCH_NOT_LIVE_REASON", "busy": "MODEL_SWITCH_BUSY_REASON",
        "prompt_open": "MODEL_SWITCH_PROMPT_OPEN_REASON",
        "unknown": "MODEL_SWITCH_UNKNOWN_REASON", "switch_pending": "MODEL_SWITCH_PENDING_REASON"}


def test_one_message_per_refusal_code():
    assert set(session_ops.MODEL_REFUSAL_MSGS) == set(MINI)


@pytest.mark.parametrize("code", sorted(MINI))
def test_the_english_equals_the_mini_apps(code):
    shown = i18n.get("en").text(session_ops.MODEL_REFUSAL_MSGS[code])
    assert shown == getattr(mini, MINI[code])


@pytest.mark.parametrize("lang", ["fa", "ru"])
@pytest.mark.parametrize("code", sorted(MINI))
def test_each_refusal_is_translated(lang, code):
    shown = i18n.get(lang).text(session_ops.MODEL_REFUSAL_MSGS[code])
    assert (PERSIAN if lang == "fa" else CYRILLIC).search(shown)


def _busy_session(h, chat_lang):
    from aipager.state import Status
    bot, tg = S.world(h, chat_lang)
    x1 = S.session(bot, "x1", chat=S.DM, status=Status.BUSY)
    return x1


@pytest.mark.parametrize("lang", ["en", "fa", "ru"])
def test_the_mini_app_still_gets_todays_english(h, lang):
    x1 = _busy_session(h, lang)
    refusal = session_ops.model_switch_refusal(x1)
    assert refusal is not None and refusal[1] == getattr(mini, MINI[refusal[0]])


@pytest.fixture
def ready(scenario_out):
    return lambda lang: scenario_out("ready_card_model_refusals", lang)


@pytest.mark.parametrize("k,code", [(0, "busy"), (1, "prompt_open"), (2, "unknown")])
def test_an_english_ready_card_refusal_is_the_mini_apps_sentence(ready, k, code):
    assert ready("en")[k] == "[toast] " + getattr(mini, MINI[code])


@pytest.mark.parametrize("lang", ["fa", "ru"])
@pytest.mark.parametrize("k", range(3))
def test_a_ready_card_refusal_reads_in_the_chats_language(ready, lang, k):
    assert (PERSIAN if lang == "fa" else CYRILLIC).search(ready(lang)[k])


@pytest.mark.parametrize("k,code", [(0, "busy"), (1, "prompt_open"), (2, "unknown")])
def test_a_russian_tapper_in_a_persian_group_reads_russian(h, k, code):
    from aipager.state import Status
    bot, tg = S._new_world(h, "en")
    S.choose(h, bot, S.GROUP, "fa")
    S.choose(h, bot, S.DM, "ru")
    S.cmd(h, bot, tg, "_handle_new_cmd", "/new x5", chat=S.GROUP)
    sess = bot.registry.find_by_label("x5", S.GROUP)
    sess.status = (Status.BUSY, Status.INTERACTIVE, Status.UNKNOWN)[k]
    if code == "prompt_open":
        sess.pending_permission = {"tool_summary": "x"}
    start = len(tg.out)
    S.tap(h, bot, tg, f"{sess.name}:rdy_m1", chat=S.GROUP)
    toast = [t for t in tg.out[start:] if t.startswith("[toast]")][0]
    assert toast == "[toast] " + i18n.get("ru").text(session_ops.MODEL_REFUSAL_MSGS[code])


def test_the_ready_card_after_the_refusal_stays_persian(h):
    from aipager.state import Status
    bot, tg = S._new_world(h, "en")
    S.choose(h, bot, S.GROUP, "fa")
    S.choose(h, bot, S.DM, "ru")
    S.cmd(h, bot, tg, "_handle_new_cmd", "/new x5", chat=S.GROUP)
    sess = bot.registry.find_by_label("x5", S.GROUP)
    sess.status = Status.BUSY
    start = len(tg.out)
    S.tap(h, bot, tg, f"{sess.name}:rdy_m1", chat=S.GROUP)
    cards = [t for t in tg.out[start:] if not t.startswith("[")]
    assert all(PERSIAN.search(t) and not CYRILLIC.search(t) for t in cards)

