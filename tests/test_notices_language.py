"""The session notices in the session's chat's language, through the
real ``notify()`` path (multilingual phase 3a, design section F/notify).

The per-surface fa and ru sweeps live in the render tests; here: the
session-end line chosen by Claude Code's ``source`` id (never by its
words), observers fed the very text the chat got, and the chat-move
notice in the language of the chat it moved to."""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import chat_migration
from aipager.bot import notify as notify_mod
from tests import i18n_surfaces_p3a as p3a
from tests.i18n_surfaces import Env
from tests.test_group_supergroup_migration import GROUP as OLD_GROUP
from tests.test_group_supergroup_migration import NEW, _fixed_policy, gbot  # noqa: F401

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")

#: Claude Code's SessionEnd ``source`` ids and the line each one gets.
BY_SOURCE = {
    "clear": "notice.session_end.clear",
    "resume": "notice.session_end.resume",
    "logout": "notice.session_end.logout",
    "prompt_input_exit": "notice.session_end.exited",
    "bypass_permissions_disabled": "notice.session_end.permissions",
    "disappeared": "notice.session_end.crashed",
    "other": "notice.session_end.exited",
    "unknown": "notice.session_end.exited",
    "something_new": "notice.session_end.exited",
}


def _world(mk_bot, run_async, monkeypatch, code):
    env = Env(mk_bot, run_async, monkeypatch, code)
    bot, tg, x1 = p3a._notice_world(env)
    return env, bot, tg, x1


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_session_end_by_source_id(mk_bot, run_async, monkeypatch, code):
    env, bot, tg, x1 = _world(mk_bot, run_async, monkeypatch, code)
    tr = i18n.get(code)
    msgs = {m.key: m for m in notify_mod.SESSION_END_MSGS.values()}
    for source, key in BY_SOURCE.items():
        x1.busy_msg_id = None
        tg.out.clear()
        env.run(bot.notify(x1, "session_end", {"source": source}))
        assert tg.out == [tr.html(msgs[key], session="x1")], source


def test_every_end_line_has_its_own_words():
    """No two source lines render the same in any language."""
    for code in ("en", "fa", "ru"):
        tr = i18n.get(code)
        lines = [tr.html(m, session="x1") for m in notify_mod.SESSION_END_MSGS.values()]
        assert len(set(lines)) == len(lines), code


@pytest.mark.parametrize("code,script", [("fa", PERSIAN), ("ru", CYRILLIC)])
def test_observers_receive_the_rendered_text(mk_bot, run_async, monkeypatch, code, script):
    env, bot, tg, x1 = _world(mk_bot, run_async, monkeypatch, code)
    bot.observers = MagicMock()
    bot.observers.broadcast = AsyncMock()

    async def _go():
        await bot.notify(x1, "session_end", {"source": "clear"})
        await bot.notify(x1, "context_warning", {"context_pct": 92})
        for _ in range(5):
            await asyncio.sleep(0)
    env.run(_go())
    seen = [c.args[0] for c in bot.observers.broadcast.await_args_list]
    assert len(seen) == 2 and seen == tg.out[:2]          # then the Compact button
    assert all(script.search(text) for text in seen)


@pytest.mark.parametrize("code,script", [("fa", PERSIAN), ("ru", CYRILLIC)])
def test_the_notices_read_in_the_sessions_chat(mk_bot, run_async, monkeypatch, code, script):
    """The chat the session lives in decides, not the owner's DM."""
    env, bot, tg, x1 = _world(mk_bot, run_async, monkeypatch, code)
    preferences.set_ui_language(p3a.DM, "en")
    env.run(bot.notify(x1, "prompt_not_taken", {"grace": 45.0}))
    env.run(bot.notify(x1, "stale_busy", {"minutes": 5}))
    assert len(tg.out) >= 2 and all(script.search(text) for text in tg.out)


@pytest.mark.parametrize("code,script", [("fa", PERSIAN), ("ru", CYRILLIC)])
def test_chat_move_notice_uses_the_new_chat(gbot, run_async, code, script):  # noqa: F811
    """The group's language moves with it, and the notice reads in it."""
    bot = gbot()
    preferences.set_ui_language(OLD_GROUP, code)
    run_async(chat_migration.migrate_chat(bot, OLD_GROUP, NEW))
    sent = [c for c in bot._app.bot.send_message.await_args_list
            if (c.kwargs.get("chat_id", c.args[0] if c.args else None)) == NEW]
    assert sent
    text = sent[-1].kwargs.get("text", sent[-1].args[1] if len(sent[-1].args) > 1 else None)
    assert text == i18n.get(code).text(chat_migration.NOTICE)
    assert script.search(text)


#: The report button's words in each language (phase 3b's
#: ``btn.rep.report_this``, without its emoji), until that Msg is here.
REPORT_BUTTON_WORDS = {"fa": "گزارش این مورد", "ru": "Сообщить об этом"}


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_memory_cap_nudge_names_the_report_button_by_its_words(code):
    """The owner's nudge names the button under the notice as that button
    reads in the same language (after phase 3b's merge, from its Msg)."""
    from aipager.bot import report_flow
    tr = i18n.get(code)
    words = REPORT_BUTTON_WORDS[code]
    button = report_flow.REPORT_THIS_BUTTON
    if isinstance(button, i18n.Msg):
        words = tr.text(button).replace("\u200f", "").replace("🐞", "").strip()
    nudge = notify_mod.memory_cap_body("aipager-hook", True, tr)
    assert f"«{words}»" in nudge
    assert "Report this" not in nudge
