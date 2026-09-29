"""A "⏳ Queued" line due before Claude has written the message's queue
record (fresh-session live test 2026-09-29 09:31-09:32, Claude Code
2.1.283).

Behind a step already 3 s old the line is due the moment the message is
picked up, and Claude writes its ``enqueue`` queue-operation a moment later.
The timer used to check the transcript once and give up: the first and the
last of four queued messages got no line ("no queue evidence"). It now
re-checks a few times, briefly, before it gives up.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from aipager import preferences as prefs
from aipager.bot import send_now as sn

CHAT = 256113222
#: How late a due line may land because it waits for its token.
BUDGET_SLACK = 2.5
SETTLE = 8.0
TEXT = "queued two"


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


@pytest.fixture
def reads(monkeypatch):
    """Every evidence read the line timer makes, as ``(msg_ids, still
    queued)`` pairs: whether the message was still a queued target when it
    was read."""
    calls: list[tuple[list, bool]] = []
    real = sn.held_by_claude

    def _counting(sess, targets):
        ids = [t.get("msg_id") if isinstance(t, dict) else None
               for t in targets]
        queued = {t.get("msg_id") for t in sess.queued_targets}
        calls.append((ids, all(i in queued for i in ids)))
        return real(sess, targets)

    monkeypatch.setattr(sn, "held_by_claude", _counting)
    return calls


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _line(r):
    return r.chat.line_for(2)


async def _behind_old_step(r, vloop, *, evidence: bool) -> float:
    """Turn 1 running a step 7 s old; message 2 typed behind it, so its line
    is due at its pick-up. Returns the pick-up time."""
    await r.turn(1, "first")
    await asyncio.sleep(SETTLE)
    await r.tool_start("long step")
    await asyncio.sleep(7.0)
    return await r.queue(2, TEXT, evidence=evidence)


def test_line_appears_when_the_queue_record_lands_a_moment_late(
        replay, vloop, reads):
    async def scenario():
        w = replay.worker()
        t0 = await _behind_old_step(replay, vloop, evidence=False)
        await asyncio.sleep(0.15)
        replay.enqueue(TEXT)          # Claude's record, a moment late
        await asyncio.sleep(6)
        w.cancel()
        return t0
    t0 = _run(vloop, scenario())
    line = _line(replay)
    assert line is not None, "no line: the late record was never re-read"
    assert line["t"] - t0 <= 1.0 + BUDGET_SLACK


def test_line_is_sent_after_one_read_when_the_record_is_already_there(
        replay, vloop, reads):
    """With the evidence already there no re-check series runs: one read
    before the send, then the send's own check and its re-check once sent."""
    async def scenario():
        w = replay.worker()
        t0 = await _behind_old_step(replay, vloop, evidence=True)
        await asyncio.sleep(4)
        w.cancel()
        return t0
    t0 = _run(vloop, scenario())
    line = _line(replay)
    assert line is not None and line["t"] - t0 <= BUDGET_SLACK
    assert [ids for ids, _q in reads if ids == [2]] == [[2], [2], [2]]


def test_evidence_that_never_comes_gives_up_after_bounded_reads(
        replay, vloop, reads, caplog):
    async def scenario():
        w = replay.worker()
        await _behind_old_step(replay, vloop, evidence=False)
        await asyncio.sleep(12)
        w.cancel()
    with caplog.at_level(logging.INFO, logger="aipager.bot.send_now"):
        _run(vloop, scenario())
    assert _line(replay) is None
    assert len([1 for ids, _q in reads if ids == [2]]) == (
        len(sn._QUEUED_LINE_EVIDENCE_PAUSES) + 1)
    gave_up = [r for r in caplog.records
               if "queued line for 2 not sent: no queue evidence"
               in r.getMessage()]
    assert len(gave_up) == 1


def test_message_taken_during_the_rechecks_gets_no_line_and_no_more_reads(
        replay, vloop, reads):
    async def scenario():
        w = replay.worker()
        await _behind_old_step(replay, vloop, evidence=False)
        await asyncio.sleep(0.1)
        # Claude takes it (absorbed at its next tool round) before its
        # queue record was ever seen; its prose reaching the screen makes
        # aipager read the transcript at once, inside the re-checks.
        replay.absorb(TEXT)
        await replay.hook(hook_event_name="MessageDisplay", delta="On it.",
                          message_id="msg_taken", index=0, final=False)
        taken_now = all(t.get("msg_id") != 2
                        for t in replay.sess.queued_targets)
        assert taken_now, "precondition: the absorption was read at once"
        await asyncio.sleep(8)
        w.cancel()
    _run(vloop, scenario())
    assert _line(replay) is None
    # No read once it was taken: none for message 2 outside the queue, and
    # none for a target that is no longer there at all.
    assert all(still_queued for ids, still_queued in reads
               if ids in ([2], [None]))
