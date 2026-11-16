"""Where a ``/usage`` goes (review rev-iter1-004): ``/usage`` and
``/usage@<this bot>`` reach aipager's own handler; ``/<label> /usage``
still types Claude Code's own ``/usage`` into that session."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, MessageEntity, Update, User
from telegram.ext import CommandHandler

from aipager import config
from aipager.bot import usage_cmd
from aipager.state import Status, TrackedSession

OPERATOR = 4242
BOT_NAME = "aipager_test_bot"


@pytest.fixture
def bot(mk_bot, monkeypatch):
    monkeypatch.setattr(config, "CHAT_ID", str(OPERATOR))
    # The chat gate start() builds reads its own import of it.
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", str(OPERATOR))
    b = mk_bot()
    b._app.bot.username = BOT_NAME
    for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                 "_send_busy_and_animate"):
        setattr(b, name, AsyncMock())
    return b


def _registered(bot, run_async):
    """Every ``(handler, group)`` ``start()`` registers, in order."""
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(
        side_effect=lambda hd, *a, **k: registered.append((hd, k.get("group", a[0] if a else 0))))
    stub.bot = bot._app.bot
    stub.bot.set_my_commands = AsyncMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater = MagicMock()
    stub.updater.start_polling = AsyncMock()
    bot._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    bot._update_bot_commands = AsyncMock()
    try:
        run_async(bot.start())
    except Exception:  # noqa: BLE001 - only the registration matters here
        pass
    return registered


def _ptb_update(text):
    first = text.split(" ", 1)[0]
    msg = Message(message_id=1, date=dt.datetime.now(dt.timezone.utc),
                  chat=Chat(id=OPERATOR, type="private"), text=text,
                  from_user=User(id=OPERATOR, first_name="A", is_bot=False),
                  entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0,
                                          length=len(first))])
    me = MagicMock()
    me.username = BOT_NAME
    msg.set_bot(me)
    return Update(update_id=1, message=msg)


def _taker(registered, update):
    """The group-0 handler that takes *update* (the first that matches)."""
    return next((hd for hd, group in registered
                 if group == 0 and hd.check_update(update)), None)


def _is_usage(handler) -> bool:
    return (isinstance(handler, CommandHandler)
            and getattr(handler.callback, "func", None) is usage_cmd.handle_usage_cmd)


@pytest.mark.parametrize("text", ["/usage", f"/usage@{BOT_NAME}", f"/usage@{BOT_NAME} now"],
                         ids=["bare", "bot-name", "bot-name-and-text"])
def test_usage_and_usage_at_this_bot_reach_aipagers_handler(bot, run_async, text):
    assert _is_usage(_taker(_registered(bot, run_async), _ptb_update(text)))


def test_usage_for_another_bot_is_not_aipagers(bot, run_async):
    assert not _is_usage(_taker(_registered(bot, run_async),
                                _ptb_update("/usage@some_other_bot")))


def test_label_then_usage_reaches_the_message_router_not_usage(bot, run_async):
    taker = _taker(_registered(bot, run_async), _ptb_update("/frontend /usage"))
    assert not _is_usage(taker)
    assert taker is not None and taker.callback == bot._handle_message


def test_label_then_usage_types_claude_codes_usage_into_that_session(
        bot, mk_update, run_async, monkeypatch):
    sess = TrackedSession(name="claude-frontend", label="frontend", status=Status.IDLE)
    bot.registry._sessions["claude-frontend"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    handled = AsyncMock()
    monkeypatch.setattr(usage_cmd, "handle_usage_cmd", handled)
    update = mk_update("/frontend /usage", user_id=OPERATOR, chat_id=OPERATOR)
    run_async(bot._handle_message(update, MagicMock()))
    bot._inject_prompt.assert_awaited_once()
    assert bot._inject_prompt.await_args.args[:2] == (sess, "/usage")
    handled.assert_not_awaited()
    assert not any("Usage" in str(c.args[0]) for c in update.message.reply_text.await_args_list)
