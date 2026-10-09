"""The ``/`` menu cache is keyed by (language, labels): a language switch
re-registers the menu, and nothing else costs a call (the multilingual
design, 5.4)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aipager import preferences
from aipager.i18n import locales
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP, DM = -1001, 555


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[
        Scope(chat_id=GROUP, kind="group", label="g",
              members=(Member(id=DM, label="aly", role="owner"),)),
        Scope(chat_id=DM, kind="dm", label="aly",
              members=(Member(id=DM, label="aly", role="owner"),))])
    sess = b.registry.get_or_create("claude-x1__g1001")
    sess.label, sess.scope_chat_id = "x1", GROUP
    b.registry.transition(sess.name, Status.IDLE)
    b._app.bot.set_my_commands = AsyncMock()
    return b


def _chats(b):
    return [c.kwargs["scope"].chat_id for c in b._app.bot.set_my_commands.await_args_list]


def _descriptions(b, chat):
    for c in b._app.bot.set_my_commands.await_args_list:
        if c.kwargs["scope"].chat_id == chat:
            commands = c.args[0]
    return {cmd.command: cmd.description for cmd in commands}


def test_same_language_same_labels_no_call(bot, run_async):
    run_async(bot._update_bot_commands_per_scope())
    assert sorted(_chats(bot)) == [GROUP, DM]
    bot._app.bot.set_my_commands.reset_mock()
    run_async(bot._update_bot_commands_per_scope())
    preferences.set_ui_language(GROUP, "en")        # an explicit choice, same language
    run_async(bot._update_bot_commands_per_scope())
    assert _chats(bot) == []


def test_a_language_switch_reregisters_the_menu(bot, run_async, monkeypatch):
    run_async(bot._update_bot_commands_per_scope())
    bot._app.bot.set_my_commands.reset_mock()
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    preferences.set_ui_language(GROUP, "qps")
    run_async(bot._update_bot_commands_per_scope())
    assert _chats(bot) == [GROUP]
    assert _descriptions(bot, GROUP)["language"].startswith("⟦")
    bot._app.bot.set_my_commands.reset_mock()
    run_async(bot._update_bot_commands_per_scope())
    assert _chats(bot) == []


def test_the_global_menu_follows_the_language_too(mk_bot, run_async, monkeypatch):
    b = mk_bot()
    b._app.bot.set_my_commands = AsyncMock()
    run_async(b._update_bot_commands_global())
    run_async(b._update_bot_commands_global())
    assert b._app.bot.set_my_commands.await_count == 1
    from aipager import config
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    preferences.set_ui_language(int(config.CHAT_ID), "qps")
    run_async(b._update_bot_commands_global())
    assert b._app.bot.set_my_commands.await_count == 2


def test_the_command_list_renders_every_description_through_tr():
    """Phase 2: every description is a key (phase 1 had only /language's);
    the command words never change."""
    from aipager.bot.lifecycle import LifecycleMixin
    from aipager.i18n import get
    english = {c.command: c.description for c in LifecycleMixin._command_list({"x1"})}
    pseudo = {c.command: c.description
              for c in LifecycleMixin._command_list({"x1"}, tr=get("qps"))}
    assert english["language"] == "Language of aipager's messages"
    assert english["x1"] == "Talk to x1"
    assert list(pseudo) == list(english)
    assert all(pseudo[k] != english[k] and pseudo[k].startswith("⟦") for k in english)
