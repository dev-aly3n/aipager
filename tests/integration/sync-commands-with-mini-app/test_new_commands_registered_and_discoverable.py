"""design.md's very first success criterion: "/restart, /rename,
/delete, /diff exist as registered bot commands and appear in /help's
command list". Neither iteration 1's coverage nor the developer's own
wiring tests (test_parity_integration.py) directly asserts this pair —
the existing coverage exercises the confirm/cancel *behavior* of these
commands, and separately checks that anything /start's welcome text
*mentions* is registered (the reverse direction), but nothing asserts
that these four specific NEW commands actually made it into both
places. ``/help`` is registered as a literal alias for
``_handle_start_cmd`` (`lifecycle.py`), so "appear in /help's command
list" is checked via that same welcome text.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


NEW_COMMANDS = ("restart", "rename", "delete", "diff")


def test_new_commands_are_in_the_telegram_slash_command_menu():
    """``_command_list`` is what ``lifecycle.py`` feeds to
    ``set_my_commands`` — Telegram's own "/" autocomplete menu."""
    from aipager.bot.lifecycle import LifecycleMixin
    cmds = LifecycleMixin._command_list(set())
    cmd_names = {c.command for c in cmds}
    missing = [c for c in NEW_COMMANDS if c not in cmd_names]
    assert not missing, (
        f"design.md: /restart /rename /delete /diff must be registered "
        f"bot commands; missing from _command_list: {missing}"
    )


def test_new_commands_are_mentioned_in_help_text(mk_bot, helpers):
    """``/help`` == ``_handle_start_cmd`` (a literal alias registered in
    lifecycle.py) — this is what a user actually sees when they run
    ``/help``."""
    bot = helpers.make_personal_bot(mk_bot)
    upd = helpers.make_message_update("/help", chat_id=555, chat_type="private")
    _run(bot._handle_start_cmd(upd, MagicMock()))

    texts = []
    if upd.message.reply_text.await_args_list:
        texts.extend(c.args[0] for c in upd.message.reply_text.await_args_list if c.args)
    for c in bot._app.bot.send_message.await_args_list:
        t = c.kwargs.get("text") or (c.args[1] if len(c.args) > 1 else (c.args[0] if c.args else ""))
        texts.append(t or "")
    assert texts, "expected /help to send at least one message"
    # The welcome text is whichever call is longest — the OTHER calls
    # in this turn are the short "keyboard active" confirmation
    # (`_send_keyboard`), not the welcome itself.
    text = max(texts, key=len)
    missing = [c for c in NEW_COMMANDS if f"/{c}" not in text]
    assert not missing, (
        f"design.md: /restart /rename /delete /diff must appear in "
        f"/help's command list; missing from the welcome text: {missing}"
    )
