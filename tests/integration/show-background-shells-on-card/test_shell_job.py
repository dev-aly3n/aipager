"""A background shell keeps its job open the way a background agent does:
the turn's Stop is an interim one, the card waits on the shell, and the
shell's <task-notification> wake-up continues the same job on the same card.

Driven through the real daemon on the virtual loop (see conftest.py).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from aipager import preferences as prefs
from aipager.session_monitor import SessionMonitor
from aipager.state import JOB_CONTINUATION_GRACE_SECONDS

CHAT = 256113222
DESC = "run the tests"
DONE = f'Background command "{DESC}" completed (exit code 0)'


def _layout(layout: str = "card") -> None:
    prefs.set_preference(CHAT, "layout", layout)


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _worker(r):
    return asyncio.ensure_future(r._updates())


async def _turn(r, mid: int, text: str, *, tools: int = 1) -> None:
    r.say(mid, text)
    await r.updates.join()
    r.prompt_hooks(mid, text)
    await asyncio.sleep(1)
    for i in range(tools):
        r.tool(f"{text} step {i}")
        await asyncio.sleep(1)


async def _shell_job(r, task_id: str = "bshell01") -> None:
    """A turn that launched a background shell and ended: the job waits."""
    await _turn(r, 1, "launch it")
    r.bg_bash(DESC, task_id)
    await asyncio.sleep(2)
    r.stop("started the tests")
    await asyncio.sleep(8)


def _monitor(r, monkeypatch) -> SessionMonitor:
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[r.sess.name]))
    return SessionMonitor(r.bot.registry, r.bot.notify)


def _only_card(r) -> dict:
    assert len(r.chat.cards) == 1, list(r.chat.cards.values())
    (card,) = r.chat.cards.values()
    return card


def test_turn_end_with_running_shell_is_an_interim(replay, vloop):
    """G15: no Finished card while the job's shell runs; the card waits
    with its Stop button, and the answer goes out on its own."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        w.cancel()

    _run(vloop, scenario())
    card = _only_card(r)
    assert card["stop"] is True
    assert "Done" not in card["text"]
    assert "1 shell still working" in card["text"]
    assert 1 in r.chat.answers.values()


def test_waiting_card_says_a_shell_is_still_working(replay, vloop):
    """G26: the waiting status names the shell, alone and beside an
    agent, never "finishing up"."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        alone = _only_card(r)["text"]
        w.cancel()
        return alone

    alone = _run(vloop, scenario())
    assert "🔄 **aipager\\_boss** · 1 shell still working · " in alone
    assert "finishing up" not in alone


def test_waiting_card_names_agents_and_shells(replay, vloop):
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="ship-reviewer")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.stop("launched both")
        await asyncio.sleep(8)
        w.cancel()

    _run(vloop, scenario())
    assert ("· 1 agent (ship-reviewer), 1 shell still working ·"
            in _only_card(r)["text"])


def test_wakeup_continues_the_same_job_and_card(replay, vloop):
    """G16: the shell's wake-up is the job's continuation: the same card
    comes back to life and its Stop closes the job as Finished there.
    The daemon must decide "continue" before taking the shell off."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(2)
        r.tool("read the output")
        await asyncio.sleep(2)
        r.stop("the tests pass")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    card = _only_card(r)
    assert card["stop"] is False
    assert "Done" in card["text"]
    assert f"✅ `shell: {DESC} - done (" in card["text"]
    assert "✅ `Bash: read the output`" in card["text"]
    assert r.bot._render_pinned(CHAT)[0].endswith("(idle)")


def test_end_read_on_the_waiting_card_before_the_wakeup_still_continues(
        replay, vloop):
    """G17: the waiting card's own tick can read the notification's
    enqueue line before the wake-up's hook arrives. That end keeps the job
    open for the wake-up (the grace window), as an agent's stop does."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        r.enqueue(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(10)
        read_first = "shell" not in r.bot._render_pinned(CHAT)[0]
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(2)
        r.tool("read the output")
        await asyncio.sleep(2)
        r.stop("the tests pass")
        await asyncio.sleep(10)
        w.cancel()
        return read_first

    assert _run(vloop, scenario()) is True
    card = _only_card(r)
    assert card["stop"] is False
    assert "✅ `Bash: read the output`" in card["text"]


def test_continuation_that_launches_a_shell_waits_again(replay, vloop):
    """G19: the continuation's own Stop closes the job only when no job
    work runs; a new shell it launched keeps the job waiting."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(2)
        r.bg_bash("second run", "bshell02")
        await asyncio.sleep(2)
        r.stop("started it again")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    card = _only_card(r)
    assert card["stop"] is True
    assert "1 shell still working" in card["text"]
    assert "⏳ `shell: second run (" in card["text"]


def test_continuation_shell_end_without_wakeup_closes_after_grace(
        replay, vloop, monkeypatch):
    """G20: a continuation that launched a new shell goes back to plain
    waiting. When that shell's end is seen but no wake-up follows, the
    grace window closes the job."""
    _layout()
    r = replay
    mon = _monitor(r, monkeypatch)

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(2)
        r.bg_bash("second run", "bshell02")
        await asyncio.sleep(2)
        r.stop("started it again")
        await asyncio.sleep(8)
        r.enqueue(r.notification(
            "bshell02", "completed",
            'Background command "second run" completed (exit code 0)'))
        await asyncio.sleep(10)
        await mon._scan()
        await asyncio.sleep(JOB_CONTINUATION_GRACE_SECONDS + 5)
        await mon._scan()
        await asyncio.sleep(5)
        w.cancel()

    _run(vloop, scenario())
    card = _only_card(r)
    assert card["stop"] is False
    assert "Finished" in card["text"]


def test_message_popped_while_continuation_shell_runs_stays_in_the_job(
        replay, vloop):
    """G21: a message queued during a continuation that launched a shell
    is popped at its Stop INSIDE the job: that Stop is an interim one, the
    job's one card stays up (no Finished, no second card), and the popped
    turn's own Stop still waits on the shell."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(2)
        r.bg_bash("second run", "bshell02")
        await asyncio.sleep(1)
        r.say(3, "queued in the job")
        await r.updates.join()
        r.prompt_hooks(3, "queued in the job")
        await asyncio.sleep(1)
        r.stop("started it again")
        await asyncio.sleep(10)
        live = list(r.chat.live_cards())
        r.stop("answer three")
        await asyncio.sleep(10)
        w.cancel()
        return live

    live = _run(vloop, scenario())
    assert live == [min(r.chat.cards)]  # the job's own card, still up
    # No card was ever settled as a Finished one (the job card may move
    # under the newest answer, which deletes the old copy).
    assert not [c for c in r.chat.cards.values()
                if not c["stop"] and not c["deleted"]]
    (now,) = r.chat.live_cards()
    assert "1 shell still working" in r.chat.cards[now]["text"]
    assert r.chat.reactions[3][-1] == "👍"
    assert 3 in r.chat.answers.values()


def test_first_tool_of_a_lost_wakeup_keeps_the_job_card(replay, vloop):
    """G22: the wake-up's UserPromptSubmit datagram is lost and the
    turn's first PreToolUse arrives while the session looks idle. Waiting
    on a shell is background work: that tool resumes the job, not a new
    turn, so a message sent meanwhile finds the job's card running (a new
    turn would settle it as Done and open another)."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        r.tool("read the output")
        await asyncio.sleep(3)
        r.say(3, "while it runs")
        await r.updates.join()
        r.prompt_hooks(3, "while it runs")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    assert len(r.chat.cards) == 1
    card = _only_card(r)
    assert card["stop"] is True
    assert card["reply_to"] == 1
    assert "Done" not in card["text"]
    assert f"⏳ `shell: {DESC} (" in card["text"]
    assert "✅ `Bash: read the output`" in card["text"]


def test_agent_stop_while_shell_runs_keeps_waiting_past_grace(
        replay, vloop, monkeypatch):
    """G23: the job's agent stopping while its shell still runs arms no
    grace window, so the job does not close under the running shell."""
    _layout()
    r = replay
    mon = _monitor(r, monkeypatch)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="ship-reviewer")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.stop("launched both")
        await asyncio.sleep(8)
        r.hook(hook_event_name="SubagentStop", agent_id="a1",
               agent_type="ship-reviewer")
        await asyncio.sleep(2)
        await mon._scan()
        await asyncio.sleep(JOB_CONTINUATION_GRACE_SECONDS + 5)
        await mon._scan()
        await asyncio.sleep(5)
        w.cancel()

    _run(vloop, scenario())
    card = _only_card(r)
    assert card["stop"] is True
    assert "1 shell still working" in card["text"]


def test_new_prompt_while_waiting_carries_the_shell_row(replay, vloop):
    """G13, G24: a new prompt while the job's shell runs: the new turn's
    card carries the live shell row and the job; the old card is settled
    saying the shell is still running, with no frozen counter."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        await _turn(r, 5, "and now this", tools=0)
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    live = r.chat.live_cards()
    assert len(live) == 1 and r.chat.cards[live[0]]["reply_to"] == 5
    assert f"⏳ `shell: {DESC} (" in r.chat.cards[live[0]]["text"]
    (old,) = [c for c in r.chat.cards.values() if c["reply_to"] == 1]
    assert old["stop"] is False
    assert f"⏳ `shell: {DESC} (still running)`" in old["text"]


def test_new_prompt_carrying_the_shell_stays_inside_the_job(replay, vloop):
    """The carried shell keeps the new turn's Stop an interim one."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        await _turn(r, 5, "and now this", tools=0)
        await asyncio.sleep(2)
        r.stop("answer five")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    live = r.chat.live_cards()
    assert len(live) == 1 and r.chat.cards[live[0]]["reply_to"] == 5
    assert "1 shell still working" in r.chat.cards[live[0]]["text"]


def test_new_turn_without_a_live_card_drops_shells_from_the_job(replay,
                                                                vloop):
    """G25: a new turn with no live job card to take over (the card is
    gone) starts clean: the shell leaves the job, so the new turn's Stop is
    an ordinary Finished; the pin still counts the shell."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        # The waiting card was deleted in the chat: nothing to reclaim.
        sess.busy_msg_id = None
        await _turn(r, 5, "and now this")
        await asyncio.sleep(2)
        r.stop("answer five")
        await asyncio.sleep(10)
        pin = r.bot._render_pinned(CHAT)[0]
        w.cancel()
        return pin

    pin = _run(vloop, scenario())
    assert r.chat.live_cards() == [] or all(
        r.chat.cards[m]["reply_to"] == 1 for m in r.chat.live_cards())
    (new,) = [c for c in r.chat.cards.values() if c["reply_to"] == 5]
    assert new["stop"] is False
    assert "Done" in new["text"]
    assert "`shell:" not in new["text"]
    assert pin.endswith("(idle, 1 shell running)")
