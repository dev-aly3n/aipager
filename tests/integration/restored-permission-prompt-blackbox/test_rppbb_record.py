"""Black-box rows for roadmap 8.102: the saved prompt is in the state file
only while the session waits on it (design.md success criterion 13).

Observed through the real ``SESSION_STATE_FILE`` written by ``save()`` (or
the session monitor's tick, which saves a dirty registry).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from aipager import preferences as prefs
from aipager import state
from aipager.session_monitor import INTERACTIVE_TIMEOUT_SECONDS, SessionMonitor

CHAT = 256113222
NAME = "claude-rppbb_harness"
MARKER = "[Request interrupted by user for tool use]"


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _dead(tmp_path) -> str:
    return str(tmp_path / "gone.sock")


def _file_entry() -> dict:
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    return data["sessions"][NAME]


def _saved(r) -> dict:
    r.bot.registry.save()
    return _file_entry()


def test_open_inline_prompt_is_saved(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        await r.inline_prompt(reply_addr=_dead(tmp_path))
        return _saved(r)

    assert _run(vloop, scenario())["open_prompt"]["kind"] == "inline"


def test_open_separate_prompt_is_saved(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        await r.separate_prompt(reply_addr=_dead(tmp_path))
        return _saved(r)

    assert _run(vloop, scenario())["open_prompt"]["kind"] == "separate"


def test_monitor_tick_writes_the_open_prompt(replay, vloop, tmp_path):
    """The prompt marks the registry dirty: the next tick's save has it,
    with no explicit save."""
    r = replay

    async def scenario():
        await r.inline_prompt(reply_addr=_dead(tmp_path))
        await SessionMonitor(r.bot.registry, r.bot.notify).tick()
        return _file_entry()

    assert "open_prompt" in _run(vloop, scenario())


async def _exit(r, tmp_path, how: str):
    card = await r.inline_prompt(reply_addr=_dead(tmp_path))
    if how == "allow":
        await r.tap(card, "allow")
    elif how == "deny":
        await r.tap(card, "deny")
    elif how == "stop":
        r.stop("stopped")
    elif how == "pre_tool":
        r.pre_tool(tool_use_id="toolu_02", tool_input={"command": "ls"})
    elif how == "watchdog":
        monitor = SessionMonitor(r.bot.registry, r.bot.notify)
        await asyncio.sleep(INTERACTIVE_TIMEOUT_SECONDS + 30)
        await monitor.tick()
    elif how == "gone":
        r.pty.alive.clear()
        monitor = SessionMonitor(r.bot.registry, r.bot.notify)
        for _ in range(10):
            await monitor.tick()
            await asyncio.sleep(2)
    elif how == "restored-allow":
        await r.restart()
        await r.tap(card, "allow")
    await asyncio.sleep(3)
    return r.sess.status


@pytest.mark.parametrize("how", ["allow", "deny", "stop", "pre_tool",
                                 "watchdog", "gone", "restored-allow"])
def test_no_record_once_the_wait_is_over(replay, vloop, tmp_path, how):
    r = replay

    async def scenario():
        await _exit(r, tmp_path, how)
        return _saved(r)

    assert "open_prompt" not in _run(vloop, scenario())


@pytest.mark.parametrize("how", ["allow", "watchdog"])
def test_monitor_tick_clears_the_record(replay, vloop, tmp_path, how):
    """The exit marks the registry dirty: the next tick rewrites the entry
    without the record."""
    r = replay

    async def scenario():
        await r.inline_prompt(reply_addr=_dead(tmp_path))
        monitor = SessionMonitor(r.bot.registry, r.bot.notify)
        await monitor.tick()
        assert "open_prompt" in _file_entry()
        await _exit_after_open(r, how, monitor)
        await monitor.tick()
        return _file_entry()

    assert "open_prompt" not in _run(vloop, scenario())


async def _exit_after_open(r, how, monitor):
    if how == "allow":
        await r.tap(r.sess.busy_msg_id, "allow")
    else:
        await asyncio.sleep(INTERACTIVE_TIMEOUT_SECONDS + 30)
        await monitor.tick()
    await asyncio.sleep(1)


def test_answered_separate_prompt_leaves_no_record_on_the_next_card_turn(
        replay, vloop, tmp_path):
    """The separate prompt's state lingers after its answer; a later turn's
    save must not resurrect it."""
    r = replay

    async def scenario():
        msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
        await r.tap(msg, "allow")
        await asyncio.sleep(2)
        r.stop("done")
        await asyncio.sleep(20)
        await r.card_turn(5, "again")
        await asyncio.sleep(5)
        return _saved(r)

    assert "open_prompt" not in _run(vloop, scenario())


def test_new_inline_prompt_after_an_answered_separate_one_is_saved_inline(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
        await r.tap(msg, "allow")
        await asyncio.sleep(2)
        r.stop("done")
        await asyncio.sleep(20)
        await r.card_turn(5, "again")
        r.pre_tool(tool_use_id="toolu_05")
        await asyncio.sleep(0.5)
        r.permission(reply_addr=_dead(tmp_path), request_id="req-5")
        await asyncio.sleep(15)
        return _saved(r)["open_prompt"]

    rec = _run(vloop, scenario())
    assert (rec["kind"], rec["tool_use_id"]) == ("inline", "toolu_05")


def test_unrestorable_question_after_answered_separate_prompt_is_not_saved(
        replay, vloop, tmp_path):
    """INTERACTIVE again, on a prompt that is never saved, with an older
    separate prompt's state still around: nothing is saved."""
    r = replay

    async def scenario():
        msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
        await r.tap(msg, "allow")
        await asyncio.sleep(2)
        r.pre_tool("AskUserQuestion", {"questions": [
            {"question": "Which?", "header": "P", "multiSelect": True,
             "options": [{"label": "A", "description": ""},
                         {"label": "B", "description": ""}]}]},
            tool_use_id="toolu_q9")
        await asyncio.sleep(15)
        return _saved(r)

    assert "open_prompt" not in _run(vloop, scenario())
