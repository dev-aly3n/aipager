"""A second `/new` (no args) replaces the open Name card; the old card's
keyboard is stripped. The pending state is per chat and the callbacks are
fixed tokens, so a tap from the REPLACED card (already in flight, or on a
client that has not rendered the strip) would otherwise change the NEW
card's choices. Only the open card's own message may drive it; the new
card stays intact and can still be completed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = 555  # private chat


async def _launch_ok(*a, **kw):
    return True, ""


def _start_wizard(helpers, bot, *, message_id):
    upd = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    upd.message.reply_text.return_value.message_id = message_id
    _run(bot._handle_new_cmd(upd, MagicMock()))
    return upd


def _send_text(helpers, bot, text):
    upd = helpers.make_message_update(text, chat_id=CHAT, chat_type="private")
    _run(bot._handle_message(upd, MagicMock()))
    return upd


def _tap(helpers, bot, callback_data, *, message_id):
    upd, q = helpers.make_callback_update(
        callback_data, chat_id=CHAT, chat_type="private", message_id=message_id,
    )
    _run(bot._handle_callback(upd, MagicMock()))
    return upd, q


@pytest.fixture(autouse=True)
def _no_real_launch():
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        yield


def test_a_tap_from_a_replaced_card_does_not_change_the_new_one(mk_bot, helpers):
    from aipager.bot import new_flow
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot, message_id=9001)
    _start_wizard(helpers, bot, message_id=9002)
    assert new_flow._pending_store(bot)[CHAT]["skip_perms"] is True

    _, q = _tap(helpers, bot, "_:nw:mode:ask", message_id=9001)

    assert new_flow._pending_store(bot)[CHAT]["skip_perms"] is True
    assert "replaced" in q.answer.await_args.args[0]


def test_a_cancel_from_a_replaced_card_leaves_the_new_one_open(mk_bot, helpers):
    from aipager.bot import new_flow
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot, message_id=9001)
    _start_wizard(helpers, bot, message_id=9002)

    _tap(helpers, bot, "_:nw:cancel", message_id=9001)

    assert CHAT in new_flow._pending_store(bot)


def test_the_new_card_can_still_be_completed(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot, message_id=9001)
    _start_wizard(helpers, bot, message_id=9002)
    _tap(helpers, bot, "_:nw:mode:ask", message_id=9001)
    _tap(helpers, bot, "_:nw:confirm", message_id=9001)   # the old wizard's

    _send_text(helpers, bot, "secondwizname")

    sess = bot.registry.find_by_label("secondwizname", CHAT)
    assert sess is not None and sess.skip_perms is True
