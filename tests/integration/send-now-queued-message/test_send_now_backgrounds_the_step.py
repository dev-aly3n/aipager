"""Send now moving the running step to the background (fresh-session live
test 2026-09-29 09:31-09:33, Claude Code 2.1.283).

Live: a foreground ``ping -c 60`` ran, four messages were queued, Send now
was tapped. Claude Code moved the ping to the background (its tool result
carries ``backgroundTaskId`` and ``backgroundedToDeliverMessage``), took
all four messages at once, answered, and woke itself with the ping's
``<task-notification>`` when it finished. Without background-shell tracking
that wake-up found "no open job" and got a separate card with no reply
target (``trigger=None``). With it, the shell holds the job open: the
answer's Stop is an interim, and the wake-up continues on the same card.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs
from aipager.state import Status

CHAT = 256113222
TASK = "bping0929"
COMMAND = "ping -c 60 127.0.0.1"
NOTIFICATION = (
    "<task-notification>\n"
    f"<task-id>{TASK}</task-id>\n"
    "<tool-use-id>toolu_ping</tool-use-id>\n"
    f"<output-file>/tmp/claude-x/tasks/{TASK}.output</output-file>\n"
    "<status>completed</status>\n"
    f"<summary>Background command \"{COMMAND}\" completed (exit code 0)"
    "</summary>\n"
    "</task-notification>")


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


@pytest.fixture
def rp(replay, pty):
    replay._pty = pty
    return replay


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _scenario(r):
    """Returns the live card ids right after the answer's Stop."""
    async def scenario():
        w = r.worker()
        await r.turn(1, "run the ping inline")
        await asyncio.sleep(8)
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": COMMAND})
        await asyncio.sleep(7)
        await r.queue(2, "hello?")
        await r.queue(3, "hi?")
        line = await r.wait_line(3, timeout=30)
        assert line is not None, "precondition: no line under message 3"
        await r.tap(line["id"])
        # Claude moves the ping to the background to deliver the messages.
        await r.hook(hook_event_name="PostToolUse", tool_name="Bash",
                     tool_input={"command": COMMAND},
                     tool_response={"backgroundTaskId": TASK,
                                    "backgroundedToDeliverMessage": True,
                                    "interrupted": False, "stdout": "",
                                    "stderr": "", "isImage": False,
                                    "noOutputExpected": False})
        r.absorb("hello?")
        r.absorb("hi?")
        await asyncio.sleep(3)
        await r.stop("I'm here; the ping keeps going in the background.")
        # The job's card moves under the last absorbed message once the
        # answer is out (a new card, then the old one deleted, both paced by
        # the flood gate): look once that move has settled.
        await asyncio.sleep(12)
        live_after_answer = list(r.chat.live_cards())
        status_after_answer = r.sess.status
        # The ping ends: Claude wakes itself with its notification.
        await r.hook(hook_event_name="UserPromptSubmit", prompt=NOTIFICATION)
        await asyncio.sleep(1)
        r.tool("show the ping summary")
        await asyncio.sleep(2)
        await r.stop("The ping finished: 60 packets, 0% loss.")
        await asyncio.sleep(12)
        w.cancel()
        return live_after_answer, status_after_answer
    return scenario()


def test_the_answer_stop_keeps_the_card_waiting_on_the_ping(rp, vloop):
    live_after_answer, status = _run(vloop, _scenario(rp))
    assert status == Status.IDLE
    assert len(live_after_answer) == 1, (
        "the job's card should stay up, waiting on the backgrounded ping")
    waiting = rp.chat.cards[live_after_answer[0]]["texts"]
    assert any("1 shell still working" in (t or "") for t in waiting)


def test_the_wake_up_continues_on_the_same_card(rp, vloop):
    live_after_answer, _ = _run(vloop, _scenario(rp))
    kept = [m for m, c in rp.chat.cards.items() if not c["deleted"]]
    assert kept == live_after_answer, (
        "the wake-up opened another card instead of continuing the job's")


def test_no_card_without_a_reply_target(rp, vloop):
    _run(vloop, _scenario(rp))
    assert all(c["reply_to"] is not None for c in rp.chat.cards.values())


def test_the_card_shows_the_ping_as_a_finished_shell_and_ends_done(rp, vloop):
    _run(vloop, _scenario(rp))
    kept = [c for c in rp.chat.cards.values() if not c["deleted"]]
    assert len(kept) == 1
    text = kept[0]["text"] or ""
    assert "shell: ping -c 60 127.0.0.1 - done (" in text
    assert rp.chat.live_cards() == []
