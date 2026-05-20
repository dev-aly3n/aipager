"""Phase C: /new builds a scope-disambiguated session name."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.dtach import inject


def test_new_builds_suffixed_name_in_group(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    captured = {}

    async def _fake_launch(name, **kw):
        captured["name"] = name
        return True, ""

    monkeypatch.setattr(inject, "launch_session", _fake_launch)
    bot._mark_driver = MagicMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    update = mk_update("/new jim", chat_id=-4152307515)
    run_async(bot._handle_new_cmd(update, MagicMock()))

    # launch_session got the short disambiguated name
    assert captured["name"] == "jim__g4152307515"
    # registry has the full disambiguated name + correct label/scope
    sess = bot.registry.get("claude-jim__g4152307515")
    assert sess is not None
    assert sess.label == "jim"
    assert sess.scope_chat_id == -4152307515
    assert sess.scope_kind == "group"


def test_new_in_dm_uses_d_prefix(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr(inject, "launch_session", AsyncMock(return_value=(True, "")))
    bot._mark_driver = MagicMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    update = mk_update("/new dev", chat_id=256113222)
    run_async(bot._handle_new_cmd(update, MagicMock()))
    sess = bot.registry.get("claude-dev__d256113222")
    assert sess is not None and sess.label == "dev" and sess.scope_kind == "dm"


def test_new_same_label_two_scopes_coexist(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr(inject, "launch_session", AsyncMock(return_value=(True, "")))
    bot._mark_driver = MagicMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    run_async(bot._handle_new_cmd(mk_update("/new jim", chat_id=111), MagicMock()))
    run_async(bot._handle_new_cmd(mk_update("/new jim", chat_id=-222), MagicMock()))
    # Two distinct registry entries, no collision
    assert bot.registry.get("claude-jim__d111") is not None
    assert bot.registry.get("claude-jim__g222") is not None
    # Scoped lookup resolves each independently
    assert bot.registry.find_by_label("jim", 111).scope_chat_id == 111
    assert bot.registry.find_by_label("jim", -222).scope_chat_id == -222
