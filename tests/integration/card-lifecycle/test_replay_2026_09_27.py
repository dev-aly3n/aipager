"""The 2026-09-27 09:23-09:28 timeline, replayed (roadmap 8.55-8.58).

What happened, from the journal and the session's own transcript (entry
types and the test messages only): Claude Code 2.1.283, two background
pipeline agents running, the operator sending a message every few seconds
into a chat with one ban in its 7-day memory (ceiling 0.5/s, hourly budget
halved to 600). Busy cards doubled up (4837/4839, 4845/4846, 4849/4850),
cards stayed on "Thinking…" with a live Stop button, a message Claude
answered kept its 👀, and the background agents' tool calls showed up on
the parent's card.

The transcript names the cause the journal could not: each of those
messages reached an IDLE Claude and ran at once as a new turn (a plain
``user`` prompt line, no ``queue-operation``) — but the daemon was BUSY,
because a background agent's own tool call had flipped it there. So the
message was recorded as "queued while busy" (👀, no card of its own), and
every later Stop started a phantom "next turn" for it, card and all, on
top of the card the message's own handler had sent.

The replay drives the REAL daemon: Telegram messages through
``_handle_message`` one at a time (PTB's default), hook datagrams through
``HookReceiver._on_datagram`` concurrently (each is its own task in the
daemon), the real card animator and typing loop, and the real limiter with
the chat's ban history — on a virtual loop. The Telegram side is
:class:`CardChat`, which remembers every card's Stop button.
"""

from __future__ import annotations

import asyncio

from aipager import preferences as prefs

CHAT = 256113222
PREFIX = "[via Telegram · @owner]\n"

#: Every Telegram call the replay made on main 2f5c364 (measured by running
#: this module's scenario there; its own assertions fail there). The fix
#: must spend fewer.
BEFORE_CALLS = 103


async def _scenario(r) -> dict:
    """The incident's shape, with its own rhythm: every prompt reaches an
    idle Claude and runs as its own turn; the two background agents keep
    calling tools the whole time."""
    worker = asyncio.ensure_future(r._updates())
    prompts: list[int] = []

    # 09:24:10 — the turn that launched the two pipeline agents.
    r.say(4821, "run both pipelines")
    await r.updates.join()
    r.prompt_hooks(4821, "run both pipelines")
    prompts.append(4821)
    await r.settle(2)
    r.hook(hook_event_name="PreToolUse", tool_name="Agent",
           tool_input={"description": "pipeline one"})
    r.hook(hook_event_name="SubagentStart", agent_id="a1",
           agent_type="pipeline-runner")
    r.hook(hook_event_name="PreToolUse", tool_name="Agent",
           tool_input={"description": "pipeline two"})
    r.hook(hook_event_name="SubagentStart", agent_id="a2",
           agent_type="pipeline-runner")
    await r.settle(4)
    r.stop("launched both, they run in the background")
    await r.settle(4)

    # 09:25:22-09:26:46 — a message every few seconds, each answered as its
    # own turn, the agents working throughout.
    texts = ["even now this one is still stuck", "ok lets test again",
             "ok now what?", "hey", "I cant see any new bussy message",
             "oh now I see one", "now I see two"]
    for k, text in enumerate(texts):
        mid = 4825 + 2 * k
        r.tool(f"agent step {k}", agent_id=("a1", "a2")[k % 2])
        await r.settle(1.5)
        r.say(mid, text)
        await r.updates.join()
        r.prompt_hooks(mid, text)
        prompts.append(mid)
        await r.settle(1)
        if k % 2:
            r.tool(f"check turn {k}")
            await r.settle(1)
        r.tool(f"agent work {k}", agent_id=("a2", "a1")[k % 2])
        await r.settle(1.5)
        r.stop(f"answer {k}")
        await r.settle(2)

    # 09:27:03 on — a message typed while a turn runs (2.1.283: its
    # UserPromptSubmit fires at ENQUEUE), popped at that turn's Stop; the
    # popped turn's first tool call lands while the finish is still out.
    r.say(4861, "one more")
    await r.updates.join()
    r.prompt_hooks(4861, "one more")
    prompts.append(4861)
    await r.settle(1)
    r.tool("work for one more")
    await r.settle(1)
    r.say(4863, "and this after it")
    await r.updates.join()
    r.prompt_hooks(4863, "and this after it")
    r.append({"type": "queue-operation", "operation": "enqueue",
              "content": PREFIX + "and this after it"})
    await r.settle(2)
    r.stop("answer one more")
    await r.settle(0.3)
    r.append({"type": "queue-operation", "operation": "dequeue"})
    r.tool("work for the queued one")
    prompts.append(4863)
    await r.settle(4)
    r.stop("answer the queued one")
    await r.settle(2)

    # The agents finish, and Claude wakes itself for their results: the
    # job's continuation turn, whose Stop is the job's real end.
    r.hook(hook_event_name="SubagentStop", agent_id="a1",
           agent_type="pipeline-runner")
    await r.settle(1)
    r.hook(hook_event_name="SubagentStop", agent_id="a2",
           agent_type="pipeline-runner")
    await r.settle(2)
    r.hook(hook_event_name="UserPromptSubmit",
           prompt="<task-notification>both agents finished")
    await r.settle(1)
    r.tool("read the pipeline reports")
    await r.settle(3)
    r.stop("both pipelines are done")
    await r.settle(120)
    await asyncio.gather(*r.tasks, return_exceptions=True)
    worker.cancel()
    return {"prompts": prompts}


def _run(replay, vloop):
    prefs.set_preference(CHAT, "layout", "card")
    out = vloop.run_until_complete(_scenario(replay))
    return replay, out


def test_replay_limiter_has_the_incident_budget(vlimiter):
    """The chat's state on 2026-09-27: one ban remembered, ceiling 0.5/s,
    hourly budget 600 (540 ornament)."""
    assert vlimiter.bans_remembered(CHAT) == 1
    assert vlimiter.ceiling_for(CHAT) == 0.5
    usage = vlimiter.hourly_usage(CHAT)
    assert (usage["budget"], usage["ornament_budget"]) == (600, 540)


def test_replay_one_live_card_per_turn_none_orphaned(replay, vloop):
    r, out = _run(replay, vloop)
    chat = r.chat
    print(f"REPLAY calls={chat.count()} cards={len(chat.cards)} "
          f"by_endpoint={ {e: chat.count(e) for e in sorted({e for e, _ in chat.calls})} }")
    # No card is left showing Stop once nothing runs.
    assert chat.live_cards() == [], (
        f"cards still showing Stop: {chat.live_cards()} "
        f"({[chat.cards[m] for m in chat.live_cards()]})")
    # Never two cards for one prompt.
    doubled = {m: chat.card_sends_for(m) for m in out["prompts"]
               if chat.card_sends_for(m) > 1}
    assert doubled == {}, f"prompts with more than one card: {doubled}"
    # Every prompt Claude took says so.
    stuck = [m for m in out["prompts"]
             if (chat.reactions.get(m) or ["-"])[-1] != "👍"]
    assert stuck == [], f"taken but not 👍: {stuck} {chat.reactions}"
    # Every prompt's answer went under that prompt.
    answered = sorted(chat.answers.values(), key=lambda v: (v is None, v))
    print(f"REPLAY answers={answered} cards={chat.cards}")
    assert set(out["prompts"]) <= set(chat.answers.values()), (
        f"prompts with no answer under them: "
        f"{set(out['prompts']) - set(chat.answers.values())}")


def test_replay_spends_fewer_calls_than_main(replay, vloop):
    r, _out = _run(replay, vloop)
    assert r.chat.count() < BEFORE_CALLS, (
        f"{r.chat.count()} calls, main spent {BEFORE_CALLS}")
