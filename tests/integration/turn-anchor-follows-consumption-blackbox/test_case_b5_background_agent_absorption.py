"""Contract case table row B5: "job waiting frame; M2 delivered to the
background agent (delivered_to_agent)" — "waiting card re-anchored
↳ M2 ... job's single answer ↳ M2 ... even while sess.status ==
Status.IDLE (job_background_open() True)."

This file tests the fully-reachable PRECONDITION through entrypoints.md's
own documented surfaces (a real ``HookReceiver``-driven interim Stop,
matching CLAUDE.md's own "Background agents end the turn" model): M2 is
sent while the foreground turn is genuinely BUSY (R1 holds the target
and writes the note), an interim Stop fires (status flips to IDLE,
``job_background_open()`` becomes True, the waiting card stays live),
and — concurrently, before the Stop-triggered safety-net sweep
(``policy_snapshot.expire_notes_after_turn_end``'s 5s grace) can run
its course — M2's note is still outstanding, exactly as R7 requires for
a legitimate absorption to win the race.

MISSING-COVERAGE CAVEAT (see test-report issues): the actual re-anchor
assertion for THIS row could not be exercised through the black-box
surfaces entrypoints.md documents. Verified empirically, with the
Developer's own already-implemented code, that firing
``bot.notify(sess, "assistant_text", ...)`` — entrypoints.md's stated
proxy for "the mid-turn absorption-detection tick" — while
``sess.status == Status.IDLE`` (this row's own precondition) produces
NO transcript sync/re-anchor at all, even when the outstanding note is
demonstrably still present and the timing race against the 5s sweep is
won (both confirmed via direct instrumentation while building this
test). design.md's own rationale text says the real trigger for this
row is the PERIODIC ANIMATOR TICK (`_animate_tick`), which entrypoints.md
does not expose as a black-box seam and which this suite cannot drive
deterministically without either touching an internal function (barred)
or a real-time wait long enough to cross an undocumented interval
(against this project's own steady_clock/no-real-sleep testing
convention). The precondition below IS exercised and passes; the
re-anchor's own outcome is left to the Developer's/Reviewer's internal
test coverage — flagged rather than invented.
"""

from __future__ import annotations

import asyncio
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
