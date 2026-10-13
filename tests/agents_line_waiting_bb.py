"""Helpers (a plain module, imported as tests.agents_line_waiting_bb) for the
black-box plumbing for a background agent that waits for its own agent,
and for running agents across a daemon restart (agents-line-waiting).

Only the surface entrypoints.md names is driven: hook datagrams through
``HookReceiver._on_datagram`` wired to the real ``bot.notify``, the bot's
``idle_prompt`` (an answer goes out) and ``agents_line_done`` (the
monitor's tick), ``SessionMonitor._scan``, ``SessionRegistry.save/load``,
``bot.recover_sessions`` and the ``TrackedSession`` fields and methods
entrypoints.md exports. Every Telegram call lands in one recorded list
(``tests/answer_status_bb.py``); nothing reaches the network.

Time: a virtual event loop (copied from
``tests/integration/card-lifecycle/conftest.py``, as this repo does between
integration directories). Nothing in ``asyncio`` is patched: each module's
OWN ``time`` reference is rebound to the loop's clock, so ``now`` moves
only when the test sleeps on the loop. No exact real-clock read is ever
asserted.
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from unittest.mock import AsyncMock

import pytest

from aipager import preferences as prefs
from aipager.bot import notify as notify_mod
from aipager.dtach import hook_receiver as hr
from aipager.state import Status
from tests import answer_status_bb as bb

CHAT = bb.CHAT
TRIGGER = bb.TRIGGER
NAME = "claude-alw_harness"
LABEL = "frontend"

#: Ids shaped like Claude Code 2.1.296's (17 hex-ish characters).
P = "a505c081ca3926ed5"      # pipeline-runner, launched by the main loop
C = "ac1d54c85d78f59ea"      # ship-reviewer, launched by P
G = "ab77e10f00d1c2b3a"      # a grandchild, launched by C
P_TYPE = "pipeline-runner"
C_TYPE = "ship-reviewer"
G_TYPE = "Explore"
ROBOT = "\U0001f916"

_LATENCY = 1e-6
_WALL0 = 1_800_000_000.0


class VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps to the next timer instead of
    waiting for it."""

    def __init__(self, real_seconds: float = 90.0) -> None:
        super().__init__()
        self._virtual = 1_000_000.0
        self._real_deadline = time.monotonic() + real_seconds

    def time(self) -> float:
        return self._virtual

    def wall(self) -> float:
        return _WALL0 + (self._virtual - 1_000_000.0)

    def _run_once(self) -> None:
        if time.monotonic() > self._real_deadline:
            raise AssertionError(
                "the virtual loop spun for too many REAL seconds")
        if self._scheduled and not self._ready:
            live = [h._when for h in self._scheduled if not h._cancelled]
            if live and min(live) > self._virtual:
                self._virtual = min(live) + _LATENCY
        super()._run_once()


@pytest.fixture
def vloop(monkeypatch):
    from aipager import session_monitor, state
    from aipager.bot import animation, flood, flood_budget, lifecycle
    from aipager.bot import notify

    loop = VirtualLoop()
    fake = types.SimpleNamespace(monotonic=loop.time, time=loop.wall,
                                 sleep=time.sleep)
    for module in (animation, notify, flood, flood_budget, state,
                   hr, session_monitor, lifecycle):
        monkeypatch.setattr(module, "time", fake, raising=False)
    yield loop
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        loop.close()


@pytest.fixture
def wire(monkeypatch):
    return bb.make_wire(monkeypatch)


@pytest.fixture
def dtach_side(monkeypatch):
    """The dtach side, mocked: every session is alive, keystrokes land."""
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))


class Rig:
    """One daemon (bot + hook receiver) on the virtual loop."""

    def __init__(self, loop, bot, wire, transcript, sess=None):
        self.loop = loop
        self.bot = bot
        self.wire = wire
        self.transcript = transcript
        self.recv = hr.HookReceiver(bot.registry, bot.notify)
        self.n = 0
        self.sess = sess if sess is not None else make_session(bot, loop, transcript)
        self.events: list[str] = []

    # -- the loop ---------------------------------------------------------
    def run(self, coro):
        return self.loop.run_until_complete(asyncio.wait_for(coro, 86400))

    def now(self) -> float:
        return self.loop.time()

    def sleep(self, seconds: float) -> None:
        self.run(asyncio.sleep(seconds))

    def drain(self) -> None:
        async def _d():
            for _ in range(5):
                pending = [t for t in getattr(notify_mod, "_BACKGROUND_TASKS", set())
                           if not t.done()]
                if not pending:
                    return
                await asyncio.gather(*pending, return_exceptions=True)
        self.run(_d())

    # -- hook datagrams ---------------------------------------------------
    def hook(self, event: str, **fields) -> None:
        """One datagram. Every payload carries a fresh ``prompt_id``, so
        the receiver's real 3 s dedup never drops a repeated event."""
        self.n += 1
        payload = {"session": self.sess.name, "hook_event_name": event,
                   "transcript_path": self.transcript, "cwd": "/home/alice/frontend",
                   "prompt_id": f"pid-{self.n}"}
        payload.update(fields)
        self.events.append(event)
        self.run(self.recv._on_datagram(json.dumps(payload).encode()))
        self.drain()

    def prompt(self, text: str = "check the reviewer") -> None:
        self.hook("UserPromptSubmit", prompt=text)

    def agent_call(self, child: str, child_type: str, *, by: str = "") -> None:
        """An ``Agent`` call that launches *child* in the background:
        the caller's PreToolUse, the child's SubagentStart, the caller's
        PostToolUse whose ``tool_response`` names the child. *by* is the
        calling agent's id ("" = the main loop)."""
        own = {"agent_id": by, "agent_type": "x"} if by else {}
        tool_input = {"subagent_type": child_type, "description": f"run {child_type}",
                      "prompt": "go", "run_in_background": True}
        self.hook("PreToolUse", tool_name="Agent", tool_input=tool_input, **own)
        self.hook("SubagentStart", agent_id=child, agent_type=child_type)
        self.link(child, by=by, tool_input=tool_input)

    def link(self, child: str, *, by: str, tool_input=None, response=None,
             tool_name: str = "Agent") -> None:
        own = {"agent_id": by, "agent_type": "x"} if by else {}
        if response is None:
            response = {"isAsync": True, "status": "async_launched",
                        "agentId": child, "description": "run it"}
        self.hook("PostToolUse", tool_name=tool_name,
                  tool_input=tool_input or {"subagent_type": "x", "prompt": "go"},
                  tool_response=response, **own)

    def start(self, agent: str, agent_type: str) -> None:
        self.hook("SubagentStart", agent_id=agent, agent_type=agent_type)

    def stop(self, agent: str, agent_type: str, **extra) -> None:
        self.hook("SubagentStop", agent_id=agent, agent_type=agent_type,
                  last_assistant_message="waiting on my reviewer", **extra)

    def phantom(self, n: int = 1) -> None:
        for i in range(n):
            self.hook("SubagentStop", agent_id=f"f{i}{self.n:015x}", agent_type="")

    def tool(self, agent: str, tool_name: str = "Read", arg: str = "/home/alice/a.py",
             post: bool = True) -> None:
        self.hook("PreToolUse", tool_name=tool_name, tool_input={"file_path": arg},
                  agent_id=agent, agent_type="x")
        if post:
            self.hook("PostToolUse", tool_name=tool_name, tool_input={"file_path": arg},
                      tool_response={"ok": True}, agent_id=agent, agent_type="x")

    # -- the bot ----------------------------------------------------------
    def answer(self, text: str = bb.ANSWER) -> str:
        """The turn ends and its answer goes out (``idle_prompt``);
        returns the status block under it."""
        self.sess.trigger_msg_id = TRIGGER
        self.sess.status = Status.IDLE
        self.run(self.bot.notify(self.sess, "idle_prompt",
                                 {"summary": text, "raw_md": text}))
        self.drain()
        return bb.block_of(bb.answer_text(self.wire, text), text)

    def tick(self, dt: float) -> None:
        """The monitor's agents-line tick, *dt* virtual seconds from now
        (the loop's clock moves there first)."""
        self.sleep(dt)
        self.run(self.bot.notify(self.sess, "agents_line_done", {"now": self.now()}))
        self.drain()

    def last_block(self, text: str = bb.ANSWER) -> str:
        edits = bb.answer_edits(self.wire, text)
        if not edits:
            return bb.block_of(bb.answer_text(self.wire, text), text)
        return bb.block_of(bb.md(edits[-1]), text)

    def robot_rows(self, agent_type: str) -> list[tuple[str, bool]]:
        return [(s, d) for s, d in self.sess.tool_history
                if s.startswith(ROBOT) and agent_type in s]


def make_session(bot, loop, transcript, *, name=NAME, label=LABEL):
    s = bot.registry.get_or_create(name)
    s.label = label
    s.status = Status.IDLE
    s.scope_chat_id = CHAT
    s.scope_kind = "dm"
    s.busy_msg_id = None
    s.trigger_msg_id = TRIGGER
    s.transcript_path = transcript
    s.tool_history = []
    prefs.set_preference(CHAT, "layout", "card")
    bot.registry.last_active_session = name
    return s


@pytest.fixture
def rig(vloop, wire, mk_bot, dtach_side, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"")
    bot = bb.make_wired_bot(mk_bot, wire)
    return Rig(vloop, bot, wire, str(transcript))


def header(block: str) -> str:
    return block.split("\n\n", 1)[0]


def rows(block: str) -> list[str]:
    return [x for x in block.split("\n\n")[1:] if "**" in x]


def row_of(block: str, agent_type: str) -> str:
    found = [x for x in rows(block) if f"**{agent_type}**" in x]
    assert len(found) == 1, block
    return found[0]


def is_working_row(row: str) -> bool:
    return row.startswith("○ ")


def is_done_row(row: str) -> bool:
    return row.startswith("✓ ")


# -- a daemon restart ---------------------------------------------------------

def append(transcript: str, *entries: dict) -> None:
    with open(transcript, "a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def user_line(text: str = "[via Telegram · @alice]\nship the settings fix") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def restart(r: Rig, mk_bot, monkeypatch, *, live: bool = True,
            gap: float = 1.0, recover: bool = True) -> Rig:
    """The daemon stops (its registry saved, as at shutdown), *gap*
    virtual seconds pass, and a new daemon loads the state file and runs
    its startup recovery. *live*: the session's dtach socket is still
    there (it loads as running); otherwise it loads as ended."""
    from aipager.dtach import inject
    from aipager.state import SessionRegistry

    r.bot.registry.save()
    if gap:
        r.sleep(gap)
    names = {r.sess.name} if live else set()
    monkeypatch.setattr(inject, "live_session_names", lambda: set(names))
    monkeypatch.setattr(inject, "list_sessions",
                        AsyncMock(return_value=sorted(names)))
    registry = SessionRegistry()
    registry.load()
    bot = bb.make_wired_bot(lambda: mk_bot(registry), r.wire)
    sess = registry.get(r.sess.name)
    assert sess is not None, "the session did not come back from the state file"
    again = Rig(r.loop, bot, r.wire, r.transcript, sess=sess)
    if recover:
        again.run(bot.recover_sessions())
        again.drain()
    return again


# -- the session monitor ------------------------------------------------------

class Monitor:
    """The real ``SessionMonitor`` over the rig's registry; its notify
    events are recorded and then handed to the real bot."""

    def __init__(self, r: Rig, monkeypatch) -> None:
        from aipager.dtach import inject
        from aipager.session_monitor import SessionMonitor

        self.r = r
        self.events: list[str] = []
        monkeypatch.setattr(inject, "list_sessions",
                            AsyncMock(return_value=[r.sess.name]))

        async def _notify(sess, event, context):
            self.events.append(event)
            await r.bot.notify(sess, event, context)

        self.monitor = SessionMonitor(r.bot.registry, _notify)

    def scan(self) -> None:
        self.r.run(self.monitor._scan())
        self.r.drain()
