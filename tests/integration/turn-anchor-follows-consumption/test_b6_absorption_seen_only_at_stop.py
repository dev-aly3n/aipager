"""Case table row B6 (design.md): the absorption line is first visible
only at Stop — no tick ran between it landing on disk and the turn
ending. R5 says the finish path re-anchors too: `card` re-sends the
finished card fresh under M2 and the answer replies to M2; `merged`
deletes the old card and SENDS (never edits) the combined card+answer
under M2, with a normal (not muted) notification.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager.policy_snapshot import delete_notes, list_outstanding_notes
from aipager.state import Status

CHAT_ID = -2002


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def _absorb_m2_with_no_tick(bot, mk_update, run_async, append_queue_op, sess):
    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))
    c1 = sess.busy_msg_id
    # The MessageDisplay hook was already live earlier in this same turn
    # (some assistant prose before M2 ever arrived) — a real prerequisite
    # for exact-anchor scanning to run at all; simulated directly rather
    # than replaying prose chunks that aren't this test's point.
    sess.stream_hook_live = True
    run_async(bot._handle_message(_update(mk_update, "no it was a test",
                                          message_id=2), MagicMock()))
    assert sess.trigger_msg_id == 1

    notes = list_outstanding_notes(sess.name)
    m1_note = next(n for n in notes if n.get("msg_id") == 1)
    m2_note = next(n for n in notes if n.get("msg_id") == 2)
    delete_notes(sess.name, [m1_note])
    append_queue_op(sess, "remove", "absorbed_mid_turn", m2_note["body"])
    # Deliberately no notify("assistant_text", ...) tick in between — the
    # line sits unread until the Stop/idle_prompt sync itself finds it.
    return c1


def test_b6_card_layout_resends_finished_card_and_answer_under_m2(
    wired, mk_update, run_async, rich_calls, append_queue_op,
):
    bot, sess, injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    c1 = _absorb_m2_with_no_tick(bot, mk_update, run_async, append_queue_op, sess)

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "it was indeed a test", "raw_md": "it was indeed a test",
    }))

    assert sess.trigger_msg_id is None  # reply cycle complete, reset at the end
    bot._app.bot.delete_message.assert_any_await(chat_id=CHAT_ID, message_id=c1)
    finished_card_send = next(
        c for c in bot._app.bot.send_message.await_args_list
        if c.kwargs.get("reply_to_message_id") == 2
    )
    assert finished_card_send.kwargs.get("disable_notification") is True

    answer_payload = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert answer_payload["reply_to_message_id"] == 2


def test_b6_merged_layout_sends_fresh_combined_message_under_m2(
    wired, mk_update, run_async, rich_calls, append_queue_op,
):
    bot, sess, injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    c1 = _absorb_m2_with_no_tick(bot, mk_update, run_async, append_queue_op, sess)

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "it was indeed a test", "raw_md": "it was indeed a test",
    }))

    # The old card is deleted — never edited in place.
    bot._app.bot.delete_message.assert_any_await(chat_id=CHAT_ID, message_id=c1)
    methods_and_payloads = [(m, p) for m, p in rich_calls]
    assert not any(m == "editMessageText" and p.get("message_id") == c1
                   for m, p in methods_and_payloads), (
        "the stale card must be SENT fresh, never edited in place — "
        f"rich_calls={rich_calls}"
    )
    merged_send = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert merged_send["reply_to_message_id"] == 2
    # The reply cycle is complete by the time notify() returns — both
    # trigger_msg_id and busy_card_trigger reset together at the finish
    # path's very end (mirroring _send_busy_and_animate's own seed).
    assert sess.trigger_msg_id is None
    assert sess.busy_card_trigger is None
