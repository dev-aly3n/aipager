"""The permission modes are named as modes in every language (operator,
2026-10-10: "instead of پرسش its better to say مود پرسش and مود خودکار ...
do it in all language including english"), while the words typed after
/mode stay the single words they were (mode.word.ask, mode.word.auto)."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import kbwords
from aipager.bot.keyboards import DEFAULT_MODE
from aipager.bot.mode_labels import (
    MODE_ASK,
    MODE_AUTO,
    WORD_ASK,
    WORD_AUTO,
    mode_from_word,
    parse_mode_args,
)
from aipager.i18n import Safe, core, locales
from tests import i18n_ml2, i18n_tool

tool = i18n_tool.load()

#: code -> (Auto label, Ask label, typed Auto word, typed Ask word). The
#: typed words are the catalogs' mode words before 2026-10-10 without
#: their emoji, so /mode reads exactly what it read then.
NAMED = {
    "en": ("🤖 Auto mode", "💬 Ask mode", "Auto", "Ask"),
    "ar": ("🤖 الوضع التلقائي", "💬 وضع «اسأل»", "تلقائي", "اسأل"),
    "de": ("🤖 Auto-Modus", "💬 Fragen-Modus", "Auto", "Fragen"),
    "es": ("🤖 Modo Automático", "💬 Modo Preguntar", "Automático", "Preguntar"),
    "fa": ("🤖 مود خودکار", "💬 مود پرسش", "خودکار", "پرسش"),
    "fr": ("🤖 Mode Automatique", "💬 Mode Demander", "Automatique", "Demander"),
    "id": ("🤖 Mode Auto", "💬 Mode Tanya", "Auto", "Tanya"),
    "it": ("🤖 Modalità «Automatico»", "💬 Modalità «Chiedi»", "Automatico", "Chiedi"),
    "ko": ("🤖 자동 모드", "💬 확인 모드", "자동", "확인"),
    "pt-BR": ("🤖 Modo Automático", "💬 Modo Perguntar", "Automático", "Perguntar"),
    "ru": ("🤖 Режим Авто", "💬 Режим «Спрашивать»", "Авто", "Спрашивать"),
    "tr": ("🤖 Otomatik mod", "💬 Sor modu", "Otomatik", "Sor"),
    "uk": ("🤖 Режим Авто", "💬 Режим «Питати»", "Авто", "Питати"),
    "vi": ("🤖 Chế độ Auto", "💬 Chế độ Hỏi", "Auto", "Hỏi"),
    "zh-Hans": ("🤖 自动模式", "💬 询问模式", "自动", "询问"),
}
CODES = sorted(NAMED)
#: (code, mode, typed word) for both modes of every language.
TYPED = [(c, m, NAMED[c][2] if m == "auto" else NAMED[c][3]) for c in CODES
         for m in ("auto", "ask")]
TYPED_IDS = [f"{c}-{m}" for c, m, _w in TYPED]
RLM = "\u200f"


def _shown(code, msg):
    return i18n.get(code).text(msg).removeprefix(RLM)


def test_every_enabled_language_is_covered():
    assert set(CODES) == set(locales.enabled_codes())


def test_persian_reads_the_operators_words():
    assert _shown("fa", MODE_ASK) == "💬 مود پرسش"
    assert _shown("fa", MODE_AUTO) == "🤖 مود خودکار"


@pytest.mark.parametrize("code", CODES)
def test_the_labels_and_the_typed_words(code):
    auto, ask, word_auto, word_ask = NAMED[code]
    assert (_shown(code, MODE_AUTO), _shown(code, MODE_ASK)) == (auto, ask)
    assert (_shown(code, WORD_AUTO), _shown(code, WORD_ASK)) == (word_auto, word_ask)


@pytest.mark.parametrize("code, mode, word", TYPED, ids=TYPED_IDS)
def test_the_typed_word_still_switches(code, mode, word):
    for typed in (word, word.lower(), word.upper(), ("🤖" if mode == "auto" else "💬") + word,
                  RLM + word):
        assert mode_from_word(typed) == mode, typed
    assert parse_mode_args(["x1", word], lambda _n: True) == (mode, "x1", True)


@pytest.mark.parametrize("code", CODES)
def test_the_word_for_mode_in_a_label_is_no_mode_word(code):
    """The extra word the label gained ("mode", "مود", "Modo", "Режим",
    "모드") is read as a session name, never as a mode."""
    for msg, word in ((MODE_AUTO, NAMED[code][2]), (MODE_ASK, NAMED[code][3])):
        for token in re.split(r"[\s«»]+", kbwords.mode_word(_shown(code, msg))):
            if token and kbwords.mode_word(word) not in token:
                assert mode_from_word(token) is None, (code, token)


def test_indonesian_tanya_still_yields_to_a_live_session_of_that_name():
    assert parse_mode_args(["tanya"], lambda n: n == "tanya") == (None, "tanya", True)
    assert parse_mode_args(["tanya"], lambda _n: False) == ("ask", None, True)


@pytest.mark.parametrize("code", CODES)
def test_the_mode_buttons_fit_their_cells(code):
    """The new-session buttons ("🤖 Auto mode ✅") and the resume card's
    default ("💬 Ask mode (default)") hold a whole label: 40 cells, wide
    characters counting 2."""
    tr = i18n.get(code)
    for msg in (MODE_AUTO, MODE_ASK):
        label = tr.text(msg)
        for shown in (label, f"{label} ✅", tr.text(DEFAULT_MODE, mode=Safe(label))):
            assert tool.cells(shown) <= 40, (code, shown)


# ---- through the handler, in each language's own chat ------------------------------------

@pytest.fixture
def mode_chat(mk_bot):
    def make(code):
        preferences.set_ui_language(i18n_ml2.DM, code)
        bot = i18n_ml2.make_bot(mk_bot)
        sess = i18n_ml2.add_session(bot)
        bot.registry.last_active_session = sess.name
        return bot, sess
    return make


@pytest.mark.parametrize("code, mode, word", TYPED, ids=TYPED_IDS)
def test_typing_the_word_in_that_chat_switches(mode_chat, run_async, code, mode, word):
    bot, sess = mode_chat(code)
    sess.skip_perms = mode != "auto"
    bot._perms_flow = AsyncMock()
    run_async(bot._handle_mode_cmd(i18n_ml2.message(f"/mode x1 {word}"), MagicMock()))
    assert bot._perms_flow.await_args.args[1] is (mode == "auto")


@pytest.mark.parametrize("code", CODES)
def test_the_mode_card_shows_the_label(mode_chat, run_async, code):
    bot, sess = mode_chat(code)
    for auto in (True, False):
        sess.skip_perms = auto
        update = i18n_ml2.message("/mode x1")
        run_async(bot._handle_mode_cmd(update, MagicMock()))
        text = str(update.message.reply_text.await_args.args[0])
        label = NAMED[code][0 if auto else 1]
        assert label in text.replace("\u2068", "").replace("\u2069", ""), (code, text)


def test_persian_names_the_modes_one_way():
    """Persian says مود for the Ask and Auto modes everywhere, as the labels
    do, never حالت خودکار / حالت پرسش next to them on the same card."""
    import json

    path = core.LOCALES_DIR / "fa.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        for form in (entry["v"].values() if isinstance(entry["v"], dict) else [entry["v"]]):
            assert "حالت خودکار" not in form and "حالت پرسش" not in form, entry["k"]
