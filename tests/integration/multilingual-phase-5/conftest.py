"""Shared fixtures for the black-box tests of multilingual phase 5 (the launch).

Everything drives the public surface of entrypoints.md: ``aipager.i18n``'s
resolvers and ``persist``, ``preferences``, ``language_menu``,
``language_offer``, ``language_launch``, ``bot_descriptions``,
``mode_labels``, the handlers and ``_handle_callback``, the Mini App over
HTTP, and ``tools/i18n.py`` as a CLI on a throwaway HOME. Every Telegram call
is a recorded mock or goes to the fake Bot API on 127.0.0.1.

Ids (from ``tests/i18n_ml2_bb``): the owner's DM 555 (its person is 555), bob's
DM 2, carol's DM 3, the group -1001. ``marker()`` writes a launch marker in
which only the people named are "existing".
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests import i18n_ml2_bb as bb

_THIS = sys.modules[__name__]

OWNER, BOB, CAROL = bb.OWNER, bb.BOB, bb.CAROL
DM_A, DM_B, DM_C, GROUP = bb.DM_A, bb.DM_B, bb.DM_C, bb.GROUP
#: A person in no scope, never in a marker.
NEWBIE = 4321

#: The 14 launched languages and the Telegram code each is set under.
LAUNCHED = ("es", "ru", "pt-BR", "zh-Hans", "fr", "de", "ko", "ar", "id", "uk", "it", "tr",
            "fa", "vi")
TELEGRAM_CODES = ("es", "ru", "pt", "zh", "fr", "de", "ko", "ar", "id", "uk", "it", "tr",
                  "fa", "vi")


def marker(*existing):
    """Write the launch marker with *existing* as the people known at launch."""
    from aipager.i18n import persist
    return persist.ensure_launch_marker(list(existing), prior_run=True)


def restart():
    """A simulated daemon restart: every in-memory copy is forgotten."""
    import aipager.preferences as prefs
    from aipager.i18n import core, persist
    persist.reset()
    prefs._cache = None
    core.reset_caches()


def sends(bot, chat=None):
    """``send_message`` calls (as kwargs dicts, chat_id and text filled in)."""
    out = []
    for c in bot._app.bot.send_message.await_args_list:
        kw = dict(c.kwargs)
        if c.args:
            kw.setdefault("chat_id", c.args[0])
            if len(c.args) > 1:
                kw.setdefault("text", c.args[1])
        if chat is None or kw.get("chat_id") == chat:
            out.append(kw)
    return out


def inline_buttons(markup):
    rows = getattr(markup, "inline_keyboard", None) or ()
    return [(b.text, b.callback_data) for row in rows for b in row]


@pytest.fixture
def bot(mk_bot):
    """A scope-mode bot (three DMs and a group) whose Telegram calls are mocks."""
    b = bb.make_bot(mk_bot)
    b._update_bot_commands = AsyncMock()
    tg = b._app.bot
    tg.get_my_description = AsyncMock(return_value=MagicMock(description=""))
    tg.get_my_short_description = AsyncMock(return_value=MagicMock(short_description=""))
    tg.set_my_description = AsyncMock(return_value=True)
    tg.set_my_short_description = AsyncMock(return_value=True)
    return b


@pytest.fixture
def launch(run_async):
    """Feed one update through the post-reply hook and wait for its work."""
    from aipager.bot import language_launch

    def _go(bot, update):
        async def _both():
            await language_launch.after_update(bot, update, MagicMock())
            await language_launch.drain(bot)
        run_async(_both())
    return _go


@pytest.fixture
def p5():
    """This module's helpers (the directory name has hyphens, so test modules
    cannot import it; bound at import time, see phase 0-1's ``ml``)."""
    return _THIS
