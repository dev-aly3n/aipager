"""The sentences around a mode's name are whole sentences in every
language (multilingual phase 3a; P0-15): the busy card's "Switch to …
mode?" is one key per mode, never the shown label with its emoji sliced
off, and the switch lines carry the mode's whole name."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import mode_labels
from aipager.i18n import locales
from aipager.state import Status
from tests import i18n_ml2

PERSIAN = re.compile("[؀-ۿ]")


def _busy_prompt(mk_bot, run_async, code, target):
    preferences.set_ui_language(i18n_ml2.DM, code)
    bot = i18n_ml2.make_bot(mk_bot)
    sess = i18n_ml2.add_session(bot, status=Status.BUSY)
    update = i18n_ml2.message(f"/mode x1 {'auto' if target else 'ask'}")
    sess.skip_perms = not target
    run_async(bot._handle_mode_cmd(update, MagicMock()))
    return update.message.reply_text.await_args.args[0]


@pytest.mark.parametrize("target", [True, False])
def test_busy_prompt_names_the_whole_mode(mk_bot, run_async, monkeypatch, target):
    """Under the pseudo locale the whole prompt is one marked key: a label
    put in (sliced or not) would be a second marked piece inside it."""
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    text = _busy_prompt(mk_bot, run_async, "qps", target)
    assert text.startswith("⟦") and text.endswith("⟧"), text
    assert text.count("⟦") == 1 and text.count("⟧") == 1, text
    assert ("Àûţö" if target else "Àšķ") in text.split("\n", 1)[1]


@pytest.mark.parametrize("target", [True, False])
def test_the_busy_prompt_in_persian(mk_bot, run_async, target):
    text = _busy_prompt(mk_bot, run_async, "fa", target)
    question = text.split("\n", 1)[1]
    word = i18n.get("fa").text(mode_labels.mode_label(target)).split(" ", 1)[1]
    assert PERSIAN.search(question) and word.strip("\u200f") in question
    assert "Auto" not in question and "Ask" not in question


def test_the_english_busy_prompt_is_todays(mk_bot, run_async):
    assert _busy_prompt(mk_bot, run_async, "en", True) == (
        "⚙️ <b>x1</b> is busy.\nSwitch to Auto mode?")
    assert _busy_prompt(mk_bot, run_async, "en", False) == (
        "⚙️ <b>x1</b> is busy.\nSwitch to Ask mode?")


def test_switching_lines_carry_the_whole_mode_name(mk_bot, run_async):
    preferences.set_ui_language(i18n_ml2.DM, "fa")
    bot = i18n_ml2.make_bot(mk_bot)
    sess = i18n_ml2.add_session(bot)
    sess.skip_perms = True
    bot._perms_switch_core = AsyncMock(return_value=MagicMock(ok=True, reason="", err=""))
    update = i18n_ml2.message("/mode x1 ask")
    run_async(bot._handle_mode_cmd(update, MagicMock()))
    first = update.message.reply_text.await_args_list[0].args[0]
    assert i18n.get("fa").text(mode_labels.MODE_ASK).strip("\u200f") in first
