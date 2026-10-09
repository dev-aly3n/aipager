"""A private chat picks فارسی and stays fully usable (multilingual
phase 2): one Persian keyboard arrives, its words and its default labels
work (Claude gets the English), /x1 and replies route, and the turn's
surfaces read in Persian."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import kbwords
from aipager.state import Status
from tests.i18n_ml2 import DM, OWNER, add_session, callback, message


PERSIAN = re.compile("[؀-ۿ]")


def _main_keyboards(bot):
    out = []
    for call in bot._app.bot.send_message.await_args_list:
        markup = call.kwargs.get("reply_markup")
        if markup is not None and hasattr(markup, "keyboard"):
            out.append([b.text for row in markup.keyboard for b in row])
    return out


def test_picking_persian_sends_one_persian_keyboard(ml2_bot, run_async):
    add_session(ml2_bot)
    update, query = callback("_:set:lang:fa")
    run_async(ml2_bot._handle_callback(update, MagicMock()))
    assert preferences.get_ui_language(DM) == "fa"
    keyboards = _main_keyboards(ml2_bot)
    assert len(keyboards) == 1
    words = keyboards[0]
    assert "x1" in words
    fa = i18n.get("fa")
    for msg in (kbwords.KB_STATUS, kbwords.KB_STOP, kbwords.KB_NEW,
                kbwords.KB_TEMPLATES, kbwords.KB_COMMANDS, kbwords.KB_APP):
        assert fa.text(msg) in words
    menus = [c.kwargs for c in ml2_bot._app.bot.set_my_commands.await_args_list
             if c.kwargs.get("scope") is not None and c.kwargs["scope"].chat_id == DM]
    assert len(menus) == 1
    button = ml2_bot._app.bot.set_chat_menu_button.await_args.kwargs["menu_button"]
    assert PERSIAN.search(button.text)
    edited = query.edit_message_text.await_args
    assert PERSIAN.search(edited.args[0] if edited.args else edited.kwargs["text"])


@pytest.fixture
def persian(ml2_bot):
    preferences.set_ui_language(DM, "fa")
    for name in ("_handle_status", "_handle_stop_cmd", "_handle_new_cmd",
                 "_handle_kill_cmd", "_send_keyboard", "_send_command", "_switch_session",
                 "_direct_send"):
        setattr(ml2_bot, name, AsyncMock())
    ml2_bot._send_template = AsyncMock()
    ml2_bot._template_map = {"Run tests": "Run the tests"}
    ml2_bot._command_map = {"Compact": "/compact"}
    return ml2_bot


def _route(bot, run_async, text, **kw):
    run_async(bot._handle_message(message(text, **kw), MagicMock()))


def test_each_persian_keyboard_word_acts(persian, run_async):
    fa = i18n.get("fa")
    for msg, handler in ((kbwords.KB_STATUS, "_handle_status"),
                         (kbwords.KB_STOP, "_handle_stop_cmd"),
                         (kbwords.KB_NEW, "_handle_new_cmd"),
                         (kbwords.KB_KILL, "_handle_kill_cmd")):
        _route(persian, run_async, fa.text(msg))
        _route(persian, run_async, fa.text(msg).replace("\u200f", ""))
        assert getattr(persian, handler).await_count == 2, handler
    _route(persian, run_async, fa.text(kbwords.KB_TEMPLATES))
    assert persian._send_keyboard.await_args.kwargs["level"] == "templates"


def test_persian_template_and_command_send_the_english(persian, run_async):
    fa = i18n.get("fa")
    _route(persian, run_async, kbwords.shown_label(fa, "Run tests", "Run the tests"))
    assert persian._send_template.await_args.args[1] == "Run the tests"
    _route(persian, run_async, kbwords.shown_label(fa, "Compact", "/compact"))
    assert persian._send_command.await_args.args[1] == "/compact"


def test_commands_and_names_still_route(persian, run_async):
    add_session(persian)
    _route(persian, run_async, "/x1 fix the bug")
    assert persian._direct_send.await_args.args[1:] == ("x1", "fix the bug")
    _route(persian, run_async, "/x1")
    assert persian._switch_session.await_args.args[1] == "x1"
    _route(persian, run_async, "x1")
    assert persian._switch_session.await_count == 2


def test_the_turn_reads_in_persian(persian, run_async, monkeypatch):
    from aipager.bot import dashboard
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    sess = add_session(persian, status=Status.BUSY)
    sess.tool_history = [("Read: /a", True)]
    bar, _kb = persian._render_pinned(DM, [sess])
    status, _kb = persian._render_status_list(DM)
    assert PERSIAN.search(bar) and PERSIAN.search(status)
    rich = AsyncMock(return_value={"message_id": 7})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", rich)
    sess.status = Status.IDLE
    run_async(persian.notify(sess, "idle_prompt", {"summary": "done"}))
    header = rich.await_args.args[1].split("\n", 1)[0]
    assert PERSIAN.search(header) and "x1" in header


def test_switching_back_keeps_the_old_keyboard_working(persian, run_async):
    fa_stop = i18n.get("fa").text(kbwords.KB_STOP)
    update, _q = callback("_:set:lang:en", user=OWNER)
    run_async(persian._handle_callback(update, MagicMock()))
    assert preferences.get_ui_language(DM) == "en"
    _route(persian, run_async, fa_stop)
    assert persian._handle_stop_cmd.await_count == 1
