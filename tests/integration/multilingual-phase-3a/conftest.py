"""Fixtures for the black-box scenarios of multilingual phase 3a: the
real bot (aly's, bob's and carol's DMs and their group), every Telegram
call recorded in order, the language of each chat chosen the way a person
chooses it (a ``_:set:lang:<code>`` tap in that chat)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences
from tests import i18n_ml2_bb as bb
from tests import i18n_surfaces_p3a as p3a


class World:
    def __init__(self, mk_bot, run_async, monkeypatch):
        from aipager.dtach import inject
        self.run = run_async
        self.mp = monkeypatch
        self.bot = bb.make_bot(mk_bot)
        self.tg = p3a.Tg()
        self.tg.wire(self.bot)
        for name in ("_maybe_update_bot_name", "_update_bot_commands", "_send_keyboard",
                     "_react", "_card_for_injected", "_send_busy_and_animate"):
            setattr(self.bot, name, AsyncMock())
        self.bot._start_animation = MagicMock()
        self.bot._stop_animation = MagicMock()
        self.bot._miniapp_url = ""
        monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(inject, "send_keys", AsyncMock(return_value=True))
        monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=[]))

    def choose(self, chat, code, *, user=None):
        """The chat's owner taps the language in the chat's own menu."""
        user = user if user is not None else (chat if chat > 0 else bb.OWNER)
        self.tap(f"_:set:lang:{code}", chat=chat, user=user)
        assert preferences.get_ui_language(chat) == code
        self.tg.out.clear()

    def tap(self, data, *, chat=bb.DM_A, user=bb.OWNER, message_id=42):
        update, query = p3a.query(self.tg, data, chat=chat, user=user,
                                  message_id=message_id)
        self.run(self.bot._handle_callback(update, MagicMock()))
        return query

    def send(self, handler, text, *, chat=bb.DM_A, user=bb.OWNER):
        update = p3a.msg(self.tg, text, chat=chat, user=user)
        self.run(getattr(self.bot, handler)(update, MagicMock()))
        return update

    def session(self, label="x1", *, chat=bb.DM_A, status=None):
        return p3a.session(self.bot, label, chat=chat, status=status)


@pytest.fixture
def world(mk_bot, run_async, monkeypatch):
    return World(mk_bot, run_async, monkeypatch)
