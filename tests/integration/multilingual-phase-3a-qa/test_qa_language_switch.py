"""Error guessing around the chat's stored language: a chat switched back to
English reads exactly what a never-switched chat reads; a card opened in
one language and tapped after the switch follows the chat's language now;
an oversized safety reason still renders."""

from __future__ import annotations

import re

import pytest
import qa_p3a_scenarios as S

from aipager import i18n
from aipager.bot import safety_notice

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")


def _commands(h, bot, tg):
    S.cmd(h, bot, tg, "_handle_stop_cmd", "/stop", chat=S.DM)
    S.cmd(h, bot, tg, "_handle_kill_cmd", "/kill", chat=S.DM)
    S.cmd(h, bot, tg, "_handle_help_cmd", "/help", chat=S.DM)
    S.cmd(h, bot, tg, "_handle_start_cmd", "/start", chat=S.DM)
    S.tap(h, bot, tg, "nocolon", chat=S.DM)
    return tg.out


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_a_chat_switched_back_to_english_reads_todays_english(h, lang):
    fresh_bot, fresh_tg = S.world(h, "en", chats=())
    fresh = list(_commands(h, fresh_bot, fresh_tg))
    bot, tg = S.world(h, "en", chats=())
    S.choose(h, bot, S.DM, lang)
    S.tap(h, bot, S.Tg(), "_:set:lang:en", chat=S.DM)
    assert _commands(h, bot, tg) == fresh


def test_a_name_card_tapped_after_the_switch_follows_the_chat(h):
    from aipager.bot import new_flow
    from aipager.miniapp import launch
    bot, tg = S.world(h, "en", chats=())
    S.choose(h, bot, S.DM, "fa")
    h.mp.setattr(new_flow, "_project_dir", lambda: "/work/app")
    h.mp.setattr(launch, "allowed_roots", lambda *a, **k: ["/work/app"])
    h.mp.setattr(launch, "default_folder_is_wide", lambda: False)
    S.cmd(h, bot, tg, "_handle_new_cmd", "/new", chat=S.DM)
    card = tg._next
    S.choose(h, bot, S.DM, "ru")
    start = len(tg.out)
    S.tap(h, bot, tg, "_:nw:opt:model", chat=S.DM, message_id=card)
    page = tg.out[start]
    assert CYRILLIC.search(page) and not PERSIAN.search(page)


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_an_oversized_protected_path_is_shown_whole(lang):
    glob = "/srv/" + "a" * 20000 + "/**"
    shown = safety_notice.reason_text(i18n.get(lang), f"Read on protected path {glob}")
    assert glob in shown


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_a_tool_name_shaped_like_markup_is_escaped(lang):
    shown = safety_notice.notice_html(i18n.get(lang), "x1",
                                      "<b>X</b> with an unreadable path")
    assert "<b>X</b>" not in shown
