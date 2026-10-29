"""design.md success criteria, end to end: a background agent (and shell)
outlives Claude's Stop, and every surface shows the waiting state with the
same counts; then each end path (the continuation turn's Stop, grace
expiry, /stop, the session ending) brings every surface back, the CLI
within one monitor scan.

Driven through the real ``HookReceiver`` and ``TelegramBot`` on the
virtual loop (conftest.py); the CLI reads the file the monitor's scan
saves; the Mini App row is ``session_summary`` (its HTTP route is covered
in test_bb_miniapp.py).
"""

from __future__ import annotations

import asyncio
import importlib
import re
from unittest.mock import AsyncMock

import pytest

from aipager.miniapp.sessions import session_summary
from aipager.session_monitor import SessionMonitor
from aipager.state import Status
from tests.footer_text import unfooter

kit = importlib.import_module("tests.integration.waiting-for-agents-blackbox.conftest")
LABEL = "frontend"
DONE = 'Agent "survey" completed'


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _turn(r, mid: int, text: str) -> None:
    r.say(mid, text)
    await r.updates.join()
    r.prompt_hooks(mid, text)
    await asyncio.sleep(1)
    r.tool(f"{text} step")
    await asyncio.sleep(1)


def _start_agent(r, agent_id: str) -> None:
    r.hook(hook_event_name="SubagentStart", agent_id=agent_id, agent_type="Explore")


def _stop_agent(r, agent_id: str) -> None:
    r.hook(hook_event_name="SubagentStop", agent_id=agent_id, agent_type="Explore")


@pytest.fixture
def world(replay, vloop, monkeypatch):
    """The replay, a monitor whose scan saves the registry, and readers
    for every surface."""
    from aipager import preferences as prefs
    prefs.set_preference(kit.CHAT, "layout", "card")
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[replay.sess.name]))
    for fn in ("send_keys", "discard_queued_input", "kill_session"):
        monkeypatch.setattr(f"aipager.dtach.inject.{fn}", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.session_ops._sleep", AsyncMock())
    mon = SessionMonitor(replay.bot.registry, replay.bot.notify)

    class W:
        r = replay
        sess = replay.sess

        @staticmethod
        def bar() -> str:
            return re.sub(r"</?b>", "", replay.bot._render_pinned(kit.CHAT)[0])

        @staticmethod
        def card_last() -> str:
            texts = [c["text"] for c in replay.chat.cards.values() if not c["deleted"]]
            return unfooter(texts[-1]).splitlines()[-1] if texts else ""

        @staticmethod
        async def scan() -> None:
            """One monitor tick: the scan, then the registry's
            ``save_if_dirty()`` (entrypoints.md: the record is written by
            the next ``save_if_dirty()``)."""
            await mon._scan()
            replay.bot.registry.save_if_dirty()

        @staticmethod
        def cli_row() -> dict:
            return kit.cli_rows(monkeypatch, {replay.sess.name})[replay.sess.name]

        @staticmethod
        def ma_row() -> dict:
            return session_summary(replay.sess, vloop.time())

    return W


async def _agents_outlive_stop(w, *, agents=("a1", "a2"), shell=True) -> None:
    await _turn(w.r, 1, "launch it")
    for a in agents:
        _start_agent(w.r, a)
    if shell:
        w.r.bg_bash("run the tests", "bshell01")
    await asyncio.sleep(2)
    w.r.stop("launched")
    await asyncio.sleep(8)
    await w.scan()


def _snapshot(w) -> dict:
    row, cli = w.ma_row(), w.cli_row()
    return {
        "shown": tuple(w.sess.shown_state()),
        "bar": w.bar().split("\n")[0],
        "miniapp": (row["status"], row["job_agents"], row["job_shells"]),
        "cli": (cli["status"], cli["job_agents"], cli["job_shells"]),
    }


# ---- every surface agrees -----------------------------------------------------------

def test_every_surface_shows_the_same_counts(world, vloop):
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world)
        snap = _snapshot(world)
        snap["card"] = world.card_last()
        w.cancel()
        return snap

    snap = _run(vloop, scenario())
    card = snap.pop("card")
    assert snap == {
        "shown": ("tasks", 2, 1),
        "bar": f"🔄 {LABEL} (waiting for 2 agents and 1 shell)",
        "miniapp": ("tasks", 2, 1),
        "cli": ("TASKS", 2, 1),
    }
    assert re.fullmatch(rf"🔄 \*\*{LABEL}\*\* · waiting for 2 agents and 1 shell · \d+:\d\d",
                        card), card


def test_the_session_is_idle_underneath(world, vloop):
    """The derived state is never a new Status: Claude's turn ended."""
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world)
        w.cancel()

    _run(vloop, scenario())
    assert world.sess.status is Status.IDLE


# ---- the end paths -------------------------------------------------------------------

def test_last_agent_stopping_shows_finishing_up(world, vloop):
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world, agents=("a1",), shell=False)
        _stop_agent(world.r, "a1")
        await asyncio.sleep(0.2)
        await world.scan()
        snap = _snapshot(world)
        w.cancel()
        return snap

    snap = _run(vloop, scenario())
    assert (snap["shown"], snap["bar"], snap["cli"]) == (
        ("tasks", 0, 0), f"🔄 {LABEL} (finishing up)", ("TASKS", 0, 0))


def test_continuation_turn_stop_brings_every_surface_back_to_idle(world, vloop):
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world, agents=("a1",), shell=False)
        _stop_agent(world.r, "a1")
        await asyncio.sleep(0.2)
        world.r.wake(world.r.notification("a1", "completed", DONE))
        await asyncio.sleep(2)
        mid = world.sess.shown_state().state
        world.r.stop("all done")
        await asyncio.sleep(8)
        await world.scan()
        snap = _snapshot(world)
        w.cancel()
        return mid, snap

    mid, snap = _run(vloop, scenario())
    assert (mid, snap) == ("busy", {
        "shown": ("idle", 0, 0), "bar": f"💤 {LABEL} (idle)",
        "miniapp": ("idle", 0, 0), "cli": ("IDLE", 0, 0)})


def test_grace_expiry_without_a_continuation_brings_every_surface_back(world, vloop):
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world, agents=("a1",), shell=False)
        _stop_agent(world.r, "a1")
        await asyncio.sleep(0.2)
        await world.scan()
        before = world.cli_row()["status"]
        await asyncio.sleep(61)
        await world.scan()
        snap = _snapshot(world)
        w.cancel()
        return before, snap

    before, snap = _run(vloop, scenario())
    assert (before, snap["shown"][0], snap["bar"], snap["miniapp"][0], snap["cli"][0]) == (
        "TASKS", "idle", f"💤 {LABEL} (idle)", "idle", "IDLE")


def test_stop_while_waiting_brings_every_surface_back(world, vloop):
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world, agents=("a1",), shell=False)
        outcome = await world.r.bot._stop_session_core(world.sess)
        await asyncio.sleep(2)
        await world.scan()
        snap = _snapshot(world)
        w.cancel()
        return outcome, snap

    outcome, snap = _run(vloop, scenario())
    assert (outcome.ok, snap["shown"][0], snap["miniapp"][0], snap["cli"][0]) == (
        True, "idle", "idle", "IDLE")


def test_after_stop_a_surviving_agent_reads_as_idle_work_on_the_bar(world, vloop):
    """design.md 5: /stop leaves the agent running; the bar shows it as
    work outside the job, never as waiting."""
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world, agents=("a1",), shell=False)
        await world.r.bot._stop_session_core(world.sess)
        await asyncio.sleep(2)
        await world.scan()
        bar = world.bar().split("\n")[0]
        w.cancel()
        return bar

    assert _run(vloop, scenario()) == f"💤 {LABEL} (idle, 1 agent running)"


def test_the_session_ending_shows_gone(world, vloop, monkeypatch):
    """The terminal goes away (a kill): the monitor marks it GONE, and the
    saved record is dropped on that tick."""
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world, agents=("a1",), shell=False)
        monkeypatch.setattr("aipager.dtach.inject.list_sessions", AsyncMock(return_value=[]))
        monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
        for _ in range(3):
            await world.scan()
            await asyncio.sleep(2)
        w.cancel()

    _run(vloop, scenario())
    assert (world.sess.shown_state().state, "job_wait" in kit.saved_sessions()[world.sess.name]) \
        == ("gone", False)


def test_a_new_prompt_while_waiting_shows_working(world, vloop):
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world, agents=("a1",), shell=False)
        await _turn(world.r, 2, "and another thing")
        await asyncio.sleep(2)   # the new card lands (its id is what the CLI reads)
        await world.scan()
        snap = _snapshot(world)
        w.cancel()
        return snap

    snap = _run(vloop, scenario())
    assert (snap["shown"][0], snap["bar"].startswith(f"⚙️ {LABEL} (working"), snap["cli"][0]) == (
        "busy", True, "BUSY")


def test_a_shell_only_job_reads_waiting_for_one_shell(world, vloop):
    async def scenario():
        w = asyncio.ensure_future(world.r._updates())
        await _agents_outlive_stop(world, agents=(), shell=True)
        snap = _snapshot(world)
        w.cancel()
        return snap

    snap = _run(vloop, scenario())
    assert snap == {"shown": ("tasks", 0, 1),
                    "bar": f"🔄 {LABEL} (waiting for 1 shell)",
                    "miniapp": ("tasks", 0, 1), "cli": ("TASKS", 0, 1)}
