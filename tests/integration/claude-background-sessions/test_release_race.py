"""The release after a bring-back, when the relaunched claude's own
SessionStart comes while the bring-back is still finishing (review
rev-iter1-002, live check 1: the held message then waited for the 10 s
backstop).

Through the wired stack (``conftest.Stack``): a Telegram message brings
the session back by itself, the relaunched claude's SessionStart arrives
from inside the launch, and the held messages go out in order: the first
as the bring-back ends, the next at the first own turn's end. The backstop
afterwards adds nothing.
"""

from __future__ import annotations

import json
import time

from aipager import claude_jobs
from aipager.dtach import inject
from aipager.state import Status


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


def _settled_idle_park(stack):
    """``←`` at an empty prompt, noticed a while ago."""
    stack.registry.transition(stack.ids.NAME, Status.IDLE)
    qa.park_and_tick(stack)
    stack.sess.parked_since = time.time() - 60


def _start_inside_the_launch(stack, monkeypatch):
    """The relaunched claude's first own SessionStart, delivered before
    ``launch_session`` returns."""
    ids = stack.ids
    launch = inject.launch_session
    seen = {}

    async def launch_and_start(short_name, **kw):
        ok = await launch(short_name, **kw)
        stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=ids.JS)
        await stack.receiver._on_datagram(json.dumps({
            "hook_event_name": "SessionStart", "source": "resume",
            "session": ids.NAME, "session_id": ids.JS,
            "claude_pid": ids.RELAUNCHED, "n": 9001}).encode())
        seen["typed_inside"] = list(stack.typed)
        return ok

    monkeypatch.setattr(inject, "launch_session", launch_and_start)
    return seen


def test_held_messages_go_out_in_order_when_the_start_wins_the_race(stack, mk_update,
                                                                     monkeypatch):
    _settled_idle_park(stack)
    seen = _start_inside_the_launch(stack, monkeypatch)
    stack.message(mk_update, "one", msg_id=41)
    stack.message(mk_update, "two", msg_id=42)
    stack.run_spawned()
    assert seen["typed_inside"] == [], "nothing typed while the bring-back runs"
    assert [t.rsplit("\n", 1)[-1] for t in stack.typed] == ["one"], (
        "the first goes out as the bring-back ends, not after the backstop")
    qa.own_turns(stack, 2)
    assert [t.rsplit("\n", 1)[-1] for t in stack.typed] == ["one", "two"]
    stack.sess.bg_released_at = time.monotonic() - (claude_jobs.RELEASE_BACKSTOP + 1)
    stack.tick()
    assert len(stack.typed) == 2, "the backstop adds nothing"
