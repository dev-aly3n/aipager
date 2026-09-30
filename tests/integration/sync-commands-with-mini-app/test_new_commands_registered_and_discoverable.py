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


def test_new_commands_leave_the_menu_but_keep_working():
    """Since 2026-09-30 (4.6) the / menu holds the frequent commands only.
    /restart /rename /delete /diff still work when typed (every dispatched
    command is pinned by test_reserved_name_reconciliation) and are one tap
    away in /status → ⋮."""
    from aipager.bot.lifecycle import LifecycleMixin
    cmd_names = {c.command for c in LifecycleMixin._command_list(set())}
    assert not [c for c in NEW_COMMANDS if c in cmd_names]


def test_new_commands_are_mentioned_in_help_text(mk_bot, helpers):
    """``/help`` is its own short guide since 2026-09-30: it names the
    actions under Manage (/status, then ⋮)."""
    bot = helpers.make_personal_bot(mk_bot)
    upd = helpers.make_message_update("/help", chat_id=555, chat_type="private")
    _run(bot._handle_help_cmd(upd, MagicMock()))

    text = upd.message.reply_text.await_args.args[0]
    missing = [c for c in NEW_COMMANDS if c not in text]
    assert not missing, f"/help does not name: {missing}"
    assert "/status" in text and "⋮" in text