"""Contract case table row B5: "job waiting frame; M2 delivered to the
background agent (delivered_to_agent)" — "waiting card re-anchored
↳ M2 ... job's single answer ↳ M2 ... even while sess.status ==
Status.IDLE (job_background_open() True)."

The first two tests exercise the fully-reachable PRECONDITION through
entrypoints.md's own documented surfaces (a real ``HookReceiver``-driven
interim Stop, matching CLAUDE.md's own "Background agents end the turn"
model): M2 is sent while the foreground turn is genuinely BUSY (R1 holds
the target and writes the note), an interim Stop fires (status flips to
IDLE, ``job_background_open()`` becomes True, the waiting card stays
live), and — concurrently, before the Stop-triggered safety-net sweep
(``policy_snapshot.expire_notes_after_turn_end``'s 5s grace) can run its
course — M2's note is still outstanding, exactly as R7 requires for a
legitimate absorption to win the race.

CLOSED (iteration 2): entrypoints.md was updated after iteration 1 to
document ``TelegramBot._animate_tick(sess, verb, waiting)`` as a
sanctioned seam "added specifically for case B5" — the periodic animator
tick this row's re-anchor actually depends on, which iteration 1 had no
way to drive deterministically. The third test below drives the full
row end to end: the precondition above, then a ``delivered_to_agent``
line appended to the transcript, one direct
``bot._animate_tick(sess, "Working", True)`` call (mirroring
``tests/test_transcript_exact_anchors.py::test_a_tick_corrects_an_anchor_and_marks_the_card_dirty``'s
own mocking of ``_edit_busy_rich``/``send_chat_action``), and finally the
job's real close (SubagentStop + the ``<task-notification>`` continuation
+ the final Stop) to confirm the job's one answer follows M2.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
import json

from aipager.state import Status

M1 = 1301
M2 = 1302


def _write_task_transcript(tp):
    lines = [
        {"type": "user", "message": {"role": "user", "content": "start the job"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Task", "input": {"description": "Explore"}}]}},
    ]
    tp.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_message_while_busy_before_a_background_job_starts_holds_the_target(
    wire_transport, rich_calls, install_session, send_update, send_text,
    run_async, tmp_path,
):
    """R1's own precondition for B5: a message sent while the FOREGROUND
    turn is still busy (before the background job/SubagentStart even
    exists) must hold the target and write a note — exactly the general
    R1 rule, applied to the specific lead-up this row needs."""
    bot, injected = wire_transport
    sess = install_session(bot, status=Status.IDLE, busy_msg_id=None,
                           trigger_msg_id=None, busy_card_trigger=None)
    tp = tmp_path / "job.jsonl"
    _write_task_transcript(tp)

    run_async(send_text(bot, send_update("start the job", M1)))
    bot._app.bot.send_message.reset_mock()

    run_async(send_text(bot, send_update("second message", M2)))

    assert sess.trigger_msg_id == M1
    bot._app.bot.send_message.assert_not_awaited()


def test_interim_stop_opens_the_job_while_the_notes_race_is_still_live(
    wire_transport, rich_calls, install_session, send_update, send_text,
    run_async, tmp_path, hook_receiver, append_queue_op,
):
    """After the interim Stop: status is IDLE, job_background_open() is
    True (the waiting frame this row's card sits in), and M2's note is
    STILL outstanding while the safety-net sweep's grace window is
    still running — the precondition R2/R4 need to have a chance to win
    the race against R7's sweep."""
    bot, injected = wire_transport
    recv = hook_receiver(bot)
    sess = install_session(bot, status=Status.IDLE, busy_msg_id=None,
                           trigger_msg_id=None, busy_card_trigger=None)
    tp = tmp_path / "job.jsonl"
    _write_task_transcript(tp)

    run_async(send_text(bot, send_update("start the job", M1)))
    run_async(send_text(bot, send_update("second message", M2)))

    from aipager import policy_snapshot as ps

    async def _scenario():
        await recv._on_datagram(json.dumps({
            "hook_event_name": "SubagentStart", "session": "claude-x",
            "agent_id": "ab1", "agent_type": "Explore",
        }).encode())

        stop_task = asyncio.create_task(recv._on_datagram(json.dumps({
            "hook_event_name": "Stop", "session": "claude-x",
            "last_assistant_message": "interim", "transcript_path": str(tp),
        }).encode()))
        await asyncio.sleep(0.05)  # let Stop's own handling start

        assert sess.status == Status.IDLE
        assert sess.job_background_open() is True
        assert any(n["msg_id"] == M2
                   for n in ps.list_outstanding_notes("claude-x")), (
            "M2's note must still be outstanding while the 5s safety-net "
            "sweep's grace window is still running")

        await stop_task  # let the sweep run its course, uninterrupted

    run_async(_scenario())


AGENT_ID = "ab5"


def test_b5_waiting_card_reanchors_under_delivered_to_agent_and_job_answer_follows_m2(
    wire_transport, rich_calls, install_session, send_update, send_text,
    run_async, tmp_path, hook_receiver, append_queue_op, send_datagram,
):
    """The full row, end to end, using the seam the orchestrator added
    to entrypoints.md after iteration 1: M1 starts a real foreground
    turn (a genuinely live card, C0); M2 arrives while it is still BUSY
    (R1 holds the target, writes M2's note); a SubagentStart + interim
    Stop opens the job (status -> IDLE, job_background_open() True, the
    waiting card is still C0); Claude Code then writes a
    delivered_to_agent line for M2; ONE direct ``_animate_tick`` call
    (the documented seam) must re-anchor the waiting card to M2 WHILE
    M2's note is still outstanding (the same race against the 5s
    safety-net sweep the precondition test above already proved is
    winnable); the job's real close (SubagentStop, the
    ``<task-notification>`` continuation, and the final Stop) must then
    deliver its one answer as a reply to M2, not M1.
    """
    from aipager import preferences as prefs

    bot, injected = wire_transport
    recv = hook_receiver(bot)
    sess = install_session(bot, status=Status.IDLE, busy_msg_id=None,
                           trigger_msg_id=None, busy_card_trigger=None)
    tp = tmp_path / "job.jsonl"
    _write_task_transcript(tp)
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    run_async(send_text(bot, send_update("start the job", M1)))
    c0 = sess.busy_msg_id
    assert c0, "the foreground turn must have produced a live busy card"

    # The message that starts a turn from IDLE is consumed by its OWN
    # real UserPromptSubmit hook (send and consumption are the same
    # event) — this test drives only the Telegram side of that send, so
    # M1's note would otherwise sit outstanding forever and, being the
    # OLDEST outstanding note, would block the prefix-run matcher
    # (oldest-first, stops at the first miss) from ever reaching M2's
    # later note. Same workaround the ``live_turn`` fixture documents
    # for the same reason.
    from aipager import policy_snapshot as ps
    own_note = [n for n in ps.list_outstanding_notes("claude-x")
               if n.get("msg_id") == M1]
    if own_note:
        ps.delete_notes("claude-x", own_note)

    run_async(send_text(bot, send_update("second message", M2)))
    assert sess.trigger_msg_id == M1, "R1 must hold the target while genuinely busy"

    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._app.bot.send_chat_action = AsyncMock()

    async def _scenario():
        await recv._on_datagram(json.dumps({
            "hook_event_name": "SubagentStart", "session": "claude-x",
            "agent_id": AGENT_ID, "agent_type": "Explore",
        }).encode())

        # Same technique as test_interim_stop_opens_the_job_while_the_
        # notes_race_is_still_live above: the Stop handler's own
        # coroutine awaits the 5s safety-net sweep to completion before
        # returning, so a plain sequential await here would let the
        # sweep silently delete M2's note before this test ever gets to
        # exercise the re-anchor. Racing it (a background task + a short
        # sleep) observes the note while it is still outstanding, then
        # `await stop_task` at the end lets the sweep run its course
        # uninterrupted (a no-op by then — R7 — since the note is
        # already consumed).
        stop_task = asyncio.create_task(recv._on_datagram(json.dumps({
            "hook_event_name": "Stop", "session": "claude-x",
            "last_assistant_message": "interim", "transcript_path": str(tp),
        }).encode()))
        await asyncio.sleep(0.05)

        assert sess.status == Status.IDLE
        assert sess.job_background_open() is True
        assert sess.busy_msg_id == c0, "the waiting frame keeps the same live card"

        # The interim Stop's own reset clears stream_transcript_path
        # (same reason live_turn/mid_turn re-seed it after a send) —
        # re-point it at the job transcript this suite controls, offset
        # at its current size so only NEW lines (the delivered_to_agent
        # line appended next) are read on the next tick, exactly as a
        # live hook event would leave it.
        sess.stream_transcript_path = str(tp)
        sess.stream_offset = tp.stat().st_size
        sess.stream_hook_live = True

        append_queue_op(sess, "remove", "delivered_to_agent",
                        "[via Telegram · @owner]\nsecond message")

        bot._app.bot.send_message.reset_mock()
        bot._app.bot.delete_message.reset_mock()
        bot._app.bot.set_message_reaction.reset_mock()

        await bot._animate_tick(sess, "Working", True)

        bot._app.bot.delete_message.assert_awaited_once()
        assert bot._app.bot.delete_message.await_args.kwargs.get("message_id") == c0, (
            "the OLD waiting card was not the one deleted")

        bot._app.bot.send_message.assert_awaited_once()
        send_kwargs = bot._app.bot.send_message.await_args.kwargs
        assert send_kwargs.get("reply_to_message_id") == M2
        assert send_kwargs.get("disable_notification") is True

        assert sess.busy_card_trigger == M2
        assert sess.trigger_msg_id == M2

        reaction_calls = bot._app.bot.set_message_reaction.await_args_list
        assert any(c.args[1] == M2 and c.args[2] == "\U0001f44d" for c in reaction_calls), (
            f"M2 never got a thumbs-up for being delivered to the agent: {reaction_calls}")

        await stop_task  # let the sweep run its course, uninterrupted

    run_async(_scenario())

    # ---- close the job for real (fresh event-loop calls are fine now
    # that the interim Stop's own coroutine, sweep included, has fully
    # settled) ----
    send_datagram(recv, hook_event_name="SubagentStop", session="claude-x",
                 agent_id=AGENT_ID, agent_type="Explore")
    send_datagram(recv, hook_event_name="UserPromptSubmit", session="claude-x",
                 prompt=(f"<task-notification>\n<task-id>{AGENT_ID}</task-id>\n"
                         "Background agent finished."),
                 transcript_path=str(tp))

    rich_calls.clear()
    send_datagram(recv, hook_event_name="Stop", session="claude-x",
                 last_assistant_message="the real answer", transcript_path=str(tp))

    assert sess.job_background_open() is False, "the job must be closed by now"

    send_rich_payloads = [p for m, p in rich_calls if m == "sendRichMessage"]
    assert send_rich_payloads, f"the job's final answer was never sent: {rich_calls}"
    assert send_rich_payloads[-1].get("reply_to_message_id") == M2, (
        f"the job's one answer must follow M2 (the message it delivered "
        f"to the agent), not M1: {send_rich_payloads[-1]}")
