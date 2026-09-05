"""Case table row A (design.md): a message that starts a turn from IDLE.
Send and consumption are the same event — the reply target is exactly
the message that started the turn, no re-anchor ever fires."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager.state import Status

CHAT_ID = -2002


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def test_a_message_from_idle_anchors_card_and_answer_to_itself(
    wired, mk_update, run_async, rich_calls,
):
    bot, sess, injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))

    assert injected == ["hello?"]
    bot._app.bot.send_message.assert_awaited_once()
    first_send = bot._app.bot.send_message.await_args_list[0]
    assert first_send.kwargs["reply_to_message_id"] == 1
    assert sess.trigger_msg_id == 1
    assert sess.busy_card_trigger == 1

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "hi there", "raw_md": "hi there",
    }))
    answer_payload = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert answer_payload["reply_to_message_id"] == 1
