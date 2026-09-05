"""Contract case table row B6: "absorption line first seen at Stop" —
"R5: card + answer re-sent under M2 (merged: sent, not edited)." Also
covers the required negative "replace unchanged" partition and the
required "both layouts" partition explicitly.

design.md: "card -> the finished card is re-sent under the new target
and the answer replies to it; merged -> the old card is deleted and the
merged message is SENT as a reply to the new target instead of edited
in place; replace -> unchanged (delete + answer under the target)."

Unlike B1/B3, no live tick ever runs here — the absorption line is
written to the transcript but the ONLY sync that ever sees it is the
finish path's own, driven purely through
``bot.notify(sess, "idle_prompt", ...)``.
"""

from __future__ import annotations

from aipager import preferences as prefs
from aipager.state import Status

M1 = 1001
M2 = 1002


def _absorbed_at_stop(bot, mid_turn, append_queue_op):
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    append_queue_op(sess, "remove", "absorbed_mid_turn", "second")
    sess.status = Status.IDLE  # Stop already flipped status by the time
                               # idle_prompt fires
    return sess, c1


def test_card_layout_resends_finished_card_under_m2_not_edits_in_place(
    wire_transport, rich_calls, mid_turn, append_queue_op, run_async,
):
    bot, injected = wire_transport
    sess, c1 = _absorbed_at_stop(bot, mid_turn, append_queue_op)
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    run_async(bot.notify(sess, "idle_prompt", {"summary": "it was a test"}))

    # "Re-sent" means a brand-new send_message under the new target (the
    # plain busy-card transport send_busy always uses) — never an
    # in-place edit of the STALE card (C1). The freshly-sent card is
    # then itself rendered via a rich edit (an editMessageText on the
    # NEW message id) — that part is the SAME two-step every ordinary
    # busy-card render already uses, not a re-anchor-specific quirk.
    bot._app.bot.send_message.assert_awaited_once()
    send_kwargs = bot._app.bot.send_message.await_args.kwargs
    assert send_kwargs.get("reply_to_message_id") == M2
    assert send_kwargs.get("disable_notification") is True

    edit_payloads = [p for m, p in rich_calls if m == "editMessageText"]
    assert all(p.get("message_id") != c1 for p in edit_payloads), (
        f"the STALE card (C1={c1}) was edited in place: {edit_payloads}")

    bot._app.bot.delete_message.assert_awaited_once()
    assert bot._app.bot.delete_message.await_args.kwargs.get("message_id") == c1

    send_rich_payloads = [p for m, p in rich_calls if m == "sendRichMessage"]
    assert send_rich_payloads, f"no answer sent: {rich_calls}"
    assert send_rich_payloads[-1].get("reply_to_message_id") == M2


def test_merged_layout_sends_a_new_message_instead_of_editing(
    wire_transport, rich_calls, mid_turn, append_queue_op, run_async,
):
    bot, injected = wire_transport
    sess, c1 = _absorbed_at_stop(bot, mid_turn, append_queue_op)
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")

    run_async(bot.notify(sess, "idle_prompt", {"summary": "it was a test"}))

    methods = [m for m, _p in rich_calls]
    assert "editMessageText" not in methods, (
        f"merged layout edited the stale card in place instead of "
        f"sending a new one under the new target: {rich_calls}")
    assert "sendRichMessage" in methods, (
        f"merged layout never sent the combined card+answer: {rich_calls}")

    payload = [p for m, p in rich_calls if m == "sendRichMessage"][-1]
    assert payload.get("reply_to_message_id") == M2


def test_merged_layout_new_send_is_not_muted(
    wire_transport, rich_calls, mid_turn, append_queue_op, run_async,
):
    """Unlike a LIVE re-anchor (always disable_notification=True), the
    merged resend IS the turn's one user-facing message — design.md:
    "it must ping normally, unlike a re-anchor that's always followed
    by a separately-notified answer." """
    bot, injected = wire_transport
    sess, c1 = _absorbed_at_stop(bot, mid_turn, append_queue_op)
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")

    run_async(bot.notify(sess, "idle_prompt", {"summary": "it was a test"}))

    payload = [p for m, p in rich_calls if m == "sendRichMessage"][-1]
    assert not payload.get("is_silent") and not payload.get("disable_notification"), (
        f"the merged turn message must not be sent silently: {payload}")


def test_merged_layout_deletes_the_old_card(
    wire_transport, rich_calls, mid_turn, append_queue_op, run_async,
):
    bot, injected = wire_transport
    sess, c1 = _absorbed_at_stop(bot, mid_turn, append_queue_op)
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")

    run_async(bot.notify(sess, "idle_prompt", {"summary": "it was a test"}))

    bot._app.bot.delete_message.assert_awaited_once()
    assert bot._app.bot.delete_message.await_args.kwargs.get("message_id") == c1


def test_replace_layout_is_unchanged_by_absorption_besides_the_target(
    wire_transport, rich_calls, mid_turn, append_queue_op, run_async,
):
    """R5: "replace: unchanged (delete + answer under the target)." The
    absorption still moves the TARGET (trigger_msg_id), but replace's
    own disposal mechanics (one delete, one plain send, no special
    re-anchor ceremony) stay exactly as they are for an ordinary,
    non-absorbed turn."""
    bot, injected = wire_transport
    sess, c1 = _absorbed_at_stop(bot, mid_turn, append_queue_op)
    prefs.set_preference(sess.scope_chat_id, "layout", "replace")

    run_async(bot.notify(sess, "idle_prompt", {"summary": "it was a test"}))

    bot._app.bot.delete_message.assert_awaited_once()
    assert bot._app.bot.delete_message.await_args.kwargs.get("message_id") == c1
    bot._app.bot.send_message.assert_not_awaited()  # header skipped, one message total

    methods = [m for m, _p in rich_calls]
    assert methods == ["sendRichMessage"], (
        f"replace layout must still be exactly one answer send: {rich_calls}")
    payload = rich_calls[0][1]
    assert payload.get("reply_to_message_id") == M2, (
        "replace layout's answer must still follow the corrected target")
    # NOTE: sess.trigger_msg_id is reset to None once the reply cycle
    # completes (both for an ordinary turn and an absorbed one) —
    # verified via the payload's reply_to_message_id above instead of
    # the now-cleared field.
