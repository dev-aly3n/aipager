"""Black-box: a reply-keyboard label carrying U+200F at its start, end or
both still classifies like the bare label (``aipager.bot.kbwords``), and
``tools/i18n.py``'s ``rtl_overhead``/``check_budget`` count the marks a
right-to-left line now carries (entrypoints.md examples)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aipager import i18n
from aipager.bot import kbwords
from tests import i18n_tool

RLM = "\u200f"
tool = i18n_tool.load()

_WRAPS = {"start": lambda s: RLM + s, "end": lambda s: s + RLM,
          "both": lambda s: RLM + s + RLM, "doubled": lambda s: RLM + RLM + s + RLM + RLM}


@pytest.fixture
def bot():
    return SimpleNamespace(
        _template_map={"Run tests": "Run the tests"},
        _command_map={"Clear": "/clear"},
        _model_map={"Opus": "/model opus"},
        registry=SimpleNamespace(live_labels=lambda chat_id: {"x1"}))


_NAV = (kbwords.KB_TEMPLATES, kbwords.KB_COMMANDS, kbwords.KB_MODELS, kbwords.KB_BACK)
_WORDS = (kbwords.KB_STATUS, kbwords.KB_STOP, kbwords.KB_NEW, kbwords.KB_KILL)


def _labels(msgs):
    """Each label as English writes it and as fa and ar render it."""
    out = []
    for msg in msgs:
        out.append(str(msg))
        for code in ("fa", "ar"):
            out.append(i18n.get(code).text(msg))
    return out


@pytest.mark.parametrize("wrap", _WRAPS)
@pytest.mark.parametrize("label", _labels(_NAV))
def test_nav_action_ignores_end_marks(label, wrap):
    assert kbwords.nav_action(_WRAPS[wrap](label)) == kbwords.nav_action(label.strip(RLM))


@pytest.mark.parametrize("label", _labels(_NAV))
def test_a_rendered_nav_label_is_recognised(label):
    assert kbwords.nav_action(label) is not None


@pytest.mark.parametrize("wrap", _WRAPS)
@pytest.mark.parametrize("label", _labels(_WORDS))
def test_bare_word_ignores_end_marks(label, wrap):
    assert kbwords.bare_word(_WRAPS[wrap](label)) == kbwords.bare_word(label.strip(RLM))


@pytest.mark.parametrize("label", _labels(_WORDS))
def test_a_rendered_bare_word_is_recognised(label):
    assert kbwords.bare_word(label) is not None


@pytest.mark.parametrize("wrap", _WRAPS)
@pytest.mark.parametrize("label", ("Run tests", "Clear"))
def test_custom_action_ignores_end_marks(bot, label, wrap):
    assert kbwords.custom_action(bot, _WRAPS[wrap](label)) == kbwords.custom_action(bot, label)


def test_custom_action_ignores_end_marks_on_a_model_label(bot):
    """entrypoints.md: a label carrying RLM at its start, end or both
    classifies exactly like the bare label, for custom_action too."""
    assert kbwords.custom_action(bot, RLM + "Opus" + RLM) == ("model", "/model opus")


@pytest.mark.parametrize("wrap", _WRAPS)
@pytest.mark.parametrize("label", ("new", "Templates", "« Back", "stop", "Run tests",
                                   "my-session", "clear"))
def test_classify_ignores_end_marks(bot, label, wrap):
    assert kbwords.classify(bot, _WRAPS[wrap](label)) == kbwords.classify(bot, label)


@pytest.mark.parametrize("wrap", _WRAPS)
@pytest.mark.parametrize("label", ("status", "Templates", "Run tests", "x1", "nope"))
def test_is_group_label_ignores_end_marks(bot, label, wrap):
    got = kbwords.is_group_label(bot, -1001, _WRAPS[wrap](label))
    assert got == kbwords.is_group_label(bot, -1001, label)


@pytest.mark.parametrize("wrap", _WRAPS)
@pytest.mark.parametrize("label", ("\U0001f916 Auto", "Ask", "\U0001f4ac Ask mode"))
def test_mode_word_ignores_end_marks(label, wrap):
    assert kbwords.mode_word(_WRAPS[wrap](label)) == kbwords.mode_word(label)


@pytest.mark.parametrize("wrap", _WRAPS)
def test_norm_drops_end_marks(wrap):
    assert kbwords.norm(_WRAPS[wrap]("بی")) == kbwords.norm("بی")


# ---- tools/i18n.py ------------------------------------------------------------------------

_SRC = {"k": "toast.x", "en": "x {name}", "eg": {}}
_COUNTED = {"k": "toast.x", "en": "x {n} {m}", "eg": {"m": 3}}


@pytest.mark.parametrize(("args", "kwargs", "want"), [
    (("fa", "x", None), {}, 2),
    (("fa", "x", None), {"kind": "menu"}, 1),
    (("fa", "{name}", _SRC), {}, 4),
    (("fa", "{name}", _SRC), {"kind": "menu"}, 3),
    (("fa", "{n} {m}\nx", _COUNTED), {}, 4),
    (("en", "{name}", _SRC), {}, 0),
    (("ar", "a\nb\nc", None), {}, 6),
    (("ar", "a\nb\nc", None), {"kind": "menu"}, 3),
    (("ru", "a\nb", None), {}, 0),
], ids=["fa-x", "fa-x-menu", "fa-name", "fa-name-menu", "fa-counted", "en", "ar-3-lines",
        "ar-3-lines-menu", "ru"])
def test_rtl_overhead_examples(args, kwargs, want):
    assert tool.rtl_overhead(*args, **kwargs) == want


def test_a_fa_toast_with_one_text_value_fits_at_196():
    fits = "ب" * 189 + " {name}"
    assert tool.check_budget("toast.x", fits, src=_SRC, code="fa") == []


def test_a_fa_toast_with_one_text_value_fails_at_198():
    long = "ب" * 191 + " {name}"
    assert tool.check_budget("toast.x", long, src=_SRC, code="fa") != []


def test_a_fa_toast_with_one_text_value_fails_at_197():
    long = "ب" * 190 + " {name}"
    assert tool.check_budget("toast.x", long, src=_SRC, code="fa") != []


def test_the_same_toast_fits_in_a_left_to_right_language():
    long = "ب" * 191 + " {name}"
    assert tool.check_budget("toast.x", long, src=_SRC, code="ru") == []
