"""A new turn inside an open job carries the job's RUNNING agents to its
card (roadmap 8.56), read after the old card's settling edit, not before.

That edit can wait seconds for a flood token. On main the agents were read
before it: an agent whose SubagentStop landed during the wait was carried
back in as running, so the job never closed and its card kept "1 agent
still working" and a live Stop button for good (found by the 2026-09-27
replay once a turn-starting message's card went to the background,
2026-10-16). One that started during the wait was dropped from the card.
"""

from __future__ import annotations

import asyncio

from aipager import preferences as prefs
from tests.card_tasks import drain_card_tasks

CHAT = 256113222


def _held_close(bot, monkeypatch):
    """Hold the old card's settling edit until the returned event is set;
    the list says whether the card step reached it."""
    gate = asyncio.Event()
    reached: list[bool] = []
    real = bot._close_superseded_card

    async def held(sess):
        reached.append(True)
        await gate.wait()
        await real(sess)

    monkeypatch.setattr(bot, "_close_superseded_card", held)
    return gate, reached


def test_an_agent_that_stops_or_starts_during_the_old_cards_edit(
        replay, vloop, monkeypatch):
    prefs.set_preference(CHAT, "layout", "card")
    r = replay
    sess = r.sess
    gate, reached = _held_close(r.bot, monkeypatch)

    async def scenario():
        w = asyncio.ensure_future(r._updates())
        # A turn that launched a background agent, ended: the job's card is
        # up, waiting on it.
        r.say(1, "launch it")
        await r.updates.join()
        await drain_card_tasks()
        r.prompt_hooks(1, "launch it")
        await asyncio.sleep(1)
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(2)
        r.stop("launched")
        await asyncio.sleep(8)
        assert list(sess.active_subagents) == ["a1"], "precondition"
        # A new prompt while the job is open: its card step settles the
        # job's old card, and that edit waits.
        r.say(3, "what now?")
        await r.updates.join()
        r.prompt_hooks(3, "what now?")
        await asyncio.sleep(1)
        assert reached == [True], "precondition: the old card's edit waits"
        r.hook(hook_event_name="SubagentStop", agent_id="a1",
               agent_type="pipeline-runner")
        r.hook(hook_event_name="SubagentStart", agent_id="a2",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        gate.set()
        await drain_card_tasks()
        await asyncio.sleep(1)
        carried = sorted(sess.active_subagents)
        # The turn, then the job, end.
        r.stop("answer three")
        await asyncio.sleep(2)
        r.hook(hook_event_name="SubagentStop", agent_id="a2",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        r.hook(hook_event_name="UserPromptSubmit",
               prompt="<task-notification>a2 finished")
        await asyncio.sleep(1)
        r.stop("the job is done")
        await asyncio.sleep(30)
        await asyncio.gather(*r.tasks, return_exceptions=True)
        w.cancel()
        return carried

    carried = vloop.run_until_complete(scenario())
    assert carried == ["a2"]
    assert not sess.job_background_open()
    assert r.chat.live_cards() == []
