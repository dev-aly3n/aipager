"""QA iteration 3: a safety halt that waits for an in-flight bring-back lets
nothing held through.

Contract (iteration-3 brief): while a safety halt waits for an in-flight
bring-back to finish, nothing held is released (even if the relaunched
claude's SessionStart arrives then); the held messages are dropped, and
nothing stays armed afterwards.

How: a Telegram message to the parked session (settled park, idle job) is
held and the gate starts its bring-back. That bring-back is suspended inside
its launch (the ``inject.launch_session`` seam waits on an event). The halt
starts while it is suspended, so it has to wait. The waits go through the
``session_ops._sleep`` seam, which here yields to the loop once
(``asyncio.sleep(0)`` called, never patched), so both run in one loop.

Partitions:
- the relaunched claude's SessionStart: inside the launch while the halt
  waits / after the bring-back ended, before the halt goes on / never;
- held messages: one / two;
- afterwards: a later own SessionStart / the 10 s backstop / the next turn
  end (none of them may type a dropped message).
Control: the same suspended bring-back without a halt releases the message.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from aipager import claude_jobs
from aipager.bot import session_ops
from aipager.dtach import inject


def _kit():
    import importlib.util
    import sys
    from pathlib import Path
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()

HALT = "🛑 <b>frontend</b> · Blocked by safety policy"
WHEN = [pytest.param("inside", id="start_while_halt_waits"),
        pytest.param("between", id="start_after_bring_back_before_halt_goes_on"),
        pytest.param("never", id="no_start")]
HELD = [pytest.param(("one",), id="one_held"), pytest.param(("one", "two"), id="two_held")]


def _bodies(stack):
    return [t.rsplit("\n", 1)[-1] for t in stack.typed]


def _start_datagram(stack, n):
    ids = stack.ids
    return json.dumps({"hook_event_name": "SessionStart", "source": "resume",
                       "session": ids.NAME, "session_id": ids.JS,
                       "claude_pid": ids.RELAUNCHED, "n": n}).encode()


def _scenario(stack, mk_update, monkeypatch, *, when, held, halt=True):
    """Hold *held*, suspend the gate's bring-back in its launch, run the
    halt meanwhile. Returns what was typed by the time both ended."""
    ids = stack.ids
    qa.park_and_tick(stack)
    for i, text in enumerate(held):
        stack.message(mk_update, text, msg_id=41 + i)
    assert stack.typed == [] and stack.spawned, "precondition: held, a bring-back started"
    bring_back = stack.spawned.pop(0)
    extras = list(stack.spawned)          # one per later held message
    stack.spawned.clear()

    async def yielding_sleep(seconds):
        stack.dtach.append(("sleep", seconds))
        await asyncio.sleep(0)
    monkeypatch.setattr(session_ops, "_sleep", yielding_sleep)

    async def main():
        entered = asyncio.Event()
        go_on = asyncio.Event()

        async def launch(short_name, **kw):
            stack.dtach.append(("launch", kw))
            entered.set()
            await go_on.wait()
            stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=ids.JS)
            return True, ""
        monkeypatch.setattr(inject, "launch_session", launch)

        bb = asyncio.ensure_future(bring_back)
        await asyncio.wait_for(entered.wait(), 5)
        more = [asyncio.ensure_future(e) for e in extras]
        h = None
        if halt:
            h = asyncio.ensure_future(stack.bot._halt_for_safety(stack.sess, "qa halt"))
            for _ in range(5):
                await asyncio.sleep(0)
            assert not h.done(), "precondition: the halt waits for the bring-back"
        if when == "inside":
            stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=ids.JS)
            await stack.receiver._on_datagram(_start_datagram(stack, 9301))
        go_on.set()
        await asyncio.wait_for(bb, 10)
        if when == "between":
            await stack.receiver._on_datagram(_start_datagram(stack, 9302))
        if h is not None:
            await asyncio.wait_for(h, 60)
        for m in more:
            await asyncio.wait_for(m, 10)
        return list(stack.typed)

    return stack.run(main())


# ---- control ----------------------------------------------------------------------------

def test_control_without_a_halt_the_held_message_goes_out(stack, mk_update, monkeypatch):
    _scenario(stack, mk_update, monkeypatch, when="inside", held=("one",), halt=False)
    assert _bodies(stack) == ["one"]


# ---- while and right after the halt waits -----------------------------------------------

@pytest.mark.parametrize("held", HELD)
@pytest.mark.parametrize("when", WHEN)
def test_nothing_held_is_typed(stack, mk_update, monkeypatch, when, held):
    typed = _scenario(stack, mk_update, monkeypatch, when=when, held=held)
    assert typed == []


@pytest.mark.parametrize("held", HELD)
@pytest.mark.parametrize("when", WHEN)
def test_the_held_messages_are_dropped(stack, mk_update, monkeypatch, when, held):
    _scenario(stack, mk_update, monkeypatch, when=when, held=held)
    assert list(stack.sess.pending_queue) == []


@pytest.mark.parametrize("held", HELD)
@pytest.mark.parametrize("when", WHEN)
def test_each_dropped_message_gets_a_shrug(stack, mk_update, monkeypatch, when, held):
    _scenario(stack, mk_update, monkeypatch, when=when, held=held)
    assert qa.reactions(stack).count("🤷") == len(held)


@pytest.mark.parametrize("when", WHEN)
def test_the_halt_notice_is_sent_once(stack, mk_update, monkeypatch, when):
    _scenario(stack, mk_update, monkeypatch, when=when, held=("one",))
    assert len([t for t in qa.sent_texts(stack) if t.startswith(HALT)]) == 1


@pytest.mark.parametrize("when", WHEN)
def test_nothing_stays_armed(stack, mk_update, monkeypatch, when):
    _scenario(stack, mk_update, monkeypatch, when=when, held=("one",))
    assert stack.sess.bg_release_pending is False


# ---- afterwards -------------------------------------------------------------------------

@pytest.mark.parametrize("when", WHEN)
def test_a_later_session_start_types_nothing(stack, mk_update, monkeypatch, when):
    _scenario(stack, mk_update, monkeypatch, when=when, held=("one", "two"))
    qa.relaunched_start(stack)
    assert stack.typed == []


@pytest.mark.parametrize("when", WHEN)
def test_the_backstop_types_nothing(stack, mk_update, monkeypatch, when):
    _scenario(stack, mk_update, monkeypatch, when=when, held=("one", "two"))
    stack.sess.bg_released_at = time.monotonic() - (claude_jobs.RELEASE_BACKSTOP + 1)
    stack.tick()
    assert stack.typed == []


@pytest.mark.parametrize("when", WHEN)
def test_the_next_turn_end_types_nothing(stack, mk_update, monkeypatch, when):
    _scenario(stack, mk_update, monkeypatch, when=when, held=("one", "two"))
    qa.relaunched_start(stack)
    stack.hook(hook_event_name="UserPromptSubmit", prompt="typed at the terminal",
               session_id=stack.ids.JS,
               transcript_path=str(stack.claude.projects / f"{stack.ids.JS}.jsonl"))
    qa.own_stop(stack)
    assert stack.typed == []
