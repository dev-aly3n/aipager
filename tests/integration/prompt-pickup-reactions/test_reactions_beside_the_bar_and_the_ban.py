"""Reactions (8.33) beside the pinned bar's Answer button (8.31) and the
every-ban-mutes rule (8.30) — the integration branch's interplay rows.

- A message held behind a permission prompt that is then answered from
  the bar's re-sent copy keeps its lifecycle: 👀 at the hold, 👍 when
  Claude takes it after the release — once each, never repeated.
- A ban answered to a reaction mutes the chat, as a ban on any call to
  the chat does, and the next reaction is not sent into it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from telegram.error import RetryAfter

from aipager.bot import session_parity
from aipager.state import Status

CHAT_ID = -3003
EYES, THUMBS = "👀", "👍"


def _query(data, message_id):
    toasts: list = []

    async def _answer(text=None, **_kw):
        toasts.append(text)

    async def _edit(*_a, **_k):
        return None

    query = MagicMock()
    query.data = data
    query.answer = _answer
    query.edit_message_text = _edit
    query.edit_message_reply_markup = _edit
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.message.chat = MagicMock()
    query.message.chat.id = CHAT_ID
    query.from_user = MagicMock()
    query.from_user.id = 12345
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = CHAT_ID
    update.effective_chat.type = "private"
    return update, toasts


def test_a_message_held_behind_a_prompt_answered_from_the_bar_is_taken_once(
        wired, run_async, send_text, pickup, reactions):
    bot, sess, injected, keys = wired
    sess.status = Status.INTERACTIVE
    sess.pending_permission = {"tool_summary": "Bash: ls", "tool_info": None,
                               "wait_started_at": 0.0}
    sess.busy_msg_id = 700

    # Sent while the dialog is open: held, 👀.
    run_async(send_text(bot, "after this", 5))
    assert len(sess.pending_queue) == 1
    assert reactions(bot) == {5: [EYES]}

    # The bar's "Answer x" re-sends the prompt at the bottom of the chat.
    data = session_parity.session_cb(bot, CHAT_ID, sess, "pin_answer")
    update, _t = _query(data, 424242)
    run_async(bot._handle_callback(update, MagicMock()))
    copy_call = bot._app.bot.send_message.await_args_list[-1]
    markup = copy_call.kwargs["reply_markup"]
    allow = [b for row in markup.inline_keyboard for b in row
             if b.text == "✅ Allow"][0]
    copy_id = 50_000 + bot._app.bot.send_message.await_count

    # ...and its Allow answers the prompt.
    update, _t = _query(allow.callback_data, copy_id)
    run_async(bot._handle_callback(update, MagicMock()))
    assert "Enter" in keys
    assert sess.status == Status.BUSY
    assert reactions(bot) == {5: [EYES]}, "an answer is not a pick-up"

    # The turn ends: the held message is released and Claude takes it.
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done",
                                               "raw_md": "done"}))
    assert any("after this" in body for body in injected)
    run_async(pickup(bot, sess, 5))
    assert reactions(bot) == {5: [EYES, THUMBS]}
    # A repeated pick-up (the hook's retry, a late datagram) adds nothing.
    run_async(pickup(bot, sess, 5))
    wire = [{"msg_id": 5, "chat_id": CHAT_ID, "raw_text": "after this"}]
    run_async(bot.notify(sess, "queue_pickup", {"consumed": wire,
                                                "expired": []}))
    assert reactions(bot) == {5: [EYES, THUMBS]}


def test_a_ban_answered_to_a_reaction_mutes_the_chat(
        wired, run_async, send_text, pickup):
    from aipager.bot.flood import MUTE
    from aipager.bot.flood_budget import BudgetRateLimiter

    bot, sess, _inj, _keys = wired
    limiter = BudgetRateLimiter(signal_path=None)
    wire_calls: list[str] = []

    async def _gated(chat_id, msg_id, emoji, rate_limit_args=None):
        async def _call():
            wire_calls.append(emoji)
            if emoji == EYES:
                raise RetryAfter(7 * 3600)
        return await limiter.process_request(
            callback=_call, args=(), kwargs={},
            endpoint="setMessageReaction", data={"chat_id": CHAT_ID},
            rate_limit_args=rate_limit_args,
        )

    bot._app.bot.set_message_reaction = _gated
    try:
        run_async(send_text(bot, "hello", 1))
        assert wire_calls == [EYES]
        assert MUTE.is_muted(CHAT_ID), "a ban on a reaction must mute the chat"
        run_async(pickup(bot, sess, 1))
        assert wire_calls == [EYES], "the 👍 went into the ban"
    finally:
        MUTE.clear()
        limiter.reset()
