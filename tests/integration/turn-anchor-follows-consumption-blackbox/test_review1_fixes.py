"""Black-box coverage for the two review-1 fixes landed in iteration 2
(``fixes-2.md``), driven purely through entrypoints.md's documented
surfaces:

(a) rev-iter1-001 — a `card`-layout finish-path re-anchor used to render
    the final card BEFORE the pre-existing ``_drop_answer_tail`` trim had
    a chance to remove a trailing timeline block that duplicates the
    incoming answer; ``card_already_final`` then skipped the only other
    render that would have caught it, so the duplicate could survive in
    the re-anchored card's own timeline for good.

(b) rev-iter1-002 — the ``compacting``/``compact_done`` bypass sites
    (branches that send/resolve a card without ever calling
    ``send_busy``) never recorded ``busy_card_trigger``, so a genuine
    absorption occurring any time after such a session touched
    compaction could silently never move the card again.
"""

from __future__ import annotations

from aipager import preferences as prefs
from aipager.state import Status

M1 = 1401
M2 = 1402


# ===== (a) rev-iter1-001: no duplicate answer in the re-sent card =========

def test_b6_card_reanchor_does_not_duplicate_the_answer_in_its_own_timeline(
    wire_transport, rich_calls, mid_turn, append_queue_op, run_async,
):
    """B6 (absorption first seen at Stop), card layout, with a trailing
    live-timeline block that happens to equal the incoming answer text
    (the exact shape review-1's rev-iter1-001 described). The re-sent,
    re-anchored final card's own rendered markdown must not carry the
    answer a second time."""
    bot, injected = wire_transport
    sess, tp, c1 = mid_turn(bot, "hello?", M1, ("second", M2))
    answer = "it was indeed a test"

    # A trailing block on the live timeline that duplicates the eventual
    # answer text — design.md names these two TrackedSession fields
    # explicitly (`sess.tool_history`/`sess.stream_commentary`) as the
    # exact state `_reanchor_busy_card`'s render reads UN-reset; setting
    # them directly is this suite's ground-truth session setup, the same
    # pattern `install_session`/`mid_turn` already use for
    # busy_msg_id/trigger_msg_id/busy_card_trigger. (A purely
    # transcript-replayed reproduction was tried first and did not
    # reproduce the bug — the pre-existing fold/anchor timing this
    # depends on is explicitly out of this feature's scope per design.md,
    # so its rendering pipeline isn't something a black-box test should
    # reverse-engineer; the FIELDS it reads are, since design.md names
    # them as part of this feature's own re-anchor contract.)
    sess.tool_history = [("Read file.py", True)]
    sess.stream_commentary = [(0, answer)]

    append_queue_op(sess, "remove", "absorbed_mid_turn",
                    "[via Telegram · @owner]\nsecond")
    sess.status = Status.IDLE  # Stop already flipped status by the time
                               # idle_prompt fires
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    run_async(bot.notify(sess, "idle_prompt", {"summary": answer, "raw_md": answer}))

    edit_payloads = [p for m, p in rich_calls if m == "editMessageText"]
    assert edit_payloads, f"the re-sent card was never rendered: {rich_calls}"
    final_markdown = edit_payloads[-1]["rich_message"]["markdown"]
    assert answer not in final_markdown, (
        "the re-anchored FINAL card must not carry the answer's own text "
        f"in its own timeline (rev-iter1-001); markdown={final_markdown!r}")
    # The trim actually ran (not just happened to not show up in this
    # one render) — the duplicate block is gone from session state too.
    assert sess.stream_commentary == []

    # The re-anchor and the answer still both landed under M2 — the fix
    # for the duplicate-answer ordering bug must not have broken the
    # re-anchor itself.
    bot._app.bot.delete_message.assert_awaited_once()
    assert bot._app.bot.delete_message.await_args.kwargs.get("message_id") == c1
    send_rich_payloads = [p for m, p in rich_calls if m == "sendRichMessage"]
    assert send_rich_payloads, f"no answer was sent at all: {rich_calls}"
    assert send_rich_payloads[-1].get("reply_to_message_id") == M2


# ===== (b) rev-iter1-002: compaction bypass sites seed busy_card_trigger ===

def test_absorption_after_compaction_bypass_still_reanchors_the_live_card(
    wire_transport, install_session, append_queue_op, tick, send_update,
    send_text, run_async, monkeypatch,
):
    """No card is live; ``compacting`` sends a fresh one (a bypass site,
    never ``send_busy``); ``compact_done`` resolves it in place; only
    THEN does a message get absorbed mid-turn. Before review-1's fix,
    none of the compaction bypass sites recorded ``busy_card_trigger``,
    so this exact sequence left it at ``None`` forever and the later
    absorption never re-anchored. The re-anchor must still fire here."""
    bot, injected = wire_transport
    sess = install_session(bot, status=Status.BUSY, busy_msg_id=None,
                           trigger_msg_id=M1, busy_card_trigger=None)

    # Scoped to notify's own constant — never asyncio.sleep through a
    # module path (CLAUDE.md), matching this file's own existing
    # compaction tests' convention.
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)

    # ---- compacting: no live card -> sends a fresh one ----
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.send_message.assert_awaited_once()
    c1 = sess.busy_msg_id
    assert c1 is not None
    assert sess.busy_card_trigger == M1, (
        "compacting's bypass send must record busy_card_trigger "
        "(rev-iter1-002) — otherwise the later absorption below can "
        "never detect a mismatch and re-anchor")

    # ---- compact_done: resolves the same message in place ----
    bot._app.bot.send_message.reset_mock()
    run_async(bot.notify(sess, "compact_done", {"before_pct": 80, "after_pct": 5}))
    assert sess.busy_msg_id == c1, "compact_done must not send a second message"
    assert sess.busy_card_trigger == M1

    # ---- M2 arrives while the (post-compaction) turn is still BUSY ----
    bot._app.bot.send_message.reset_mock()
    bot._app.bot.delete_message.reset_mock()
    run_async(send_text(bot, send_update("second", M2)))
    assert sess.trigger_msg_id == M1, "R1 must still hold the target"

    # ---- M2 gets absorbed mid-turn ----
    append_queue_op(sess, "remove", "absorbed_mid_turn",
                    "[via Telegram · @owner]\nsecond")
    tick(bot, run_async, sess)

    bot._app.bot.delete_message.assert_awaited_once()
    assert bot._app.bot.delete_message.await_args.kwargs.get("message_id") == c1, (
        "the OLD (post-compaction) card was not the one deleted")

    bot._app.bot.send_message.assert_awaited_once()
    send_kwargs = bot._app.bot.send_message.await_args.kwargs
    assert send_kwargs.get("reply_to_message_id") == M2
    assert sess.busy_card_trigger == M2
    assert sess.trigger_msg_id == M2


def test_compacting_send_records_busy_card_trigger_directly(mk_bot, run_async, monkeypatch):
    """Narrower unit-shaped check on the same bypass site, isolated from
    the rest of the sequence above — the fresh-send branch alone must
    seed busy_card_trigger the moment it sends."""
    from unittest.mock import AsyncMock, MagicMock
    from aipager.state import TrackedSession

    bot = mk_bot()
    sess = TrackedSession(name="claude-y", label="y", status=Status.BUSY)
    sess.trigger_msg_id = 77
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))

    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))

    assert sess.busy_msg_id == 555
    assert sess.busy_card_trigger == 77
