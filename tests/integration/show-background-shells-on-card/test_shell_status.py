"""A background shell in the session's status: the pinned bar, the answer
line, the max-age sweep, and what /stop, /kill, a session's end and /clear
do to it.

Driven through the real daemon on the virtual loop (see conftest.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from aipager import preferences as prefs
from aipager import session_monitor
from aipager import state as state_mod
from aipager.session_monitor import SessionMonitor, resolve_bg_shell_max_track
from aipager.state import BG_AGENTS_SETTLE_SECONDS, BG_SHELLS_CAP

CHAT = 256113222
DESC = "run the tests"
DONE = f'Background command "{DESC}" completed (exit code 0)'
EM = "—"


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


def _pin(r) -> str:
    return r.bot._render_pinned(CHAT)[0]


def _answer(r) -> list[str]:
    """Every version of the scenario's first answer."""
    return r.chat.answer_texts[min(r.chat.answer_texts)]


def _mock_dtach(monkeypatch) -> None:
    for fn in ("send_keys", "discard_queued_input", "kill_session"):
        monkeypatch.setattr(f"aipager.dtach.inject.{fn}",
                            AsyncMock(return_value=True))


# ── the pinned bar ──────────────────────────────────────────────────────────

def test_pin_counts_a_running_shell(replay, vloop):
    """G28."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        working = _pin(r)
        r.stop("started the tests")
        await asyncio.sleep(8)
        idle = _pin(r)
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(2)
        r.stop("the tests pass")
        await asyncio.sleep(8)
        w.cancel()
        return working, idle

    working, idle = _run(vloop, scenario())
    assert working == "⚙️ aipager_boss (working, 1 shell running)"
    assert idle == "💤 aipager_boss (idle, 1 shell running)"
    assert _pin(r) == "💤 aipager_boss (idle)"


def test_pin_counts_agents_and_shells(replay, vloop):
    """G28: agents first, then shells, one count each."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="ship-reviewer")
        r.hook(hook_event_name="SubagentStart", agent_id="a2",
               agent_type="ship-dev")
        r.bg_bash(DESC, "bshell01")
        r.bg_bash("build", "bshell02")
        await asyncio.sleep(2)
        r.stop("launched")
        await asyncio.sleep(8)
        w.cancel()

    _run(vloop, scenario())
    assert _pin(r) == "💤 aipager_boss (idle, 2 agents, 2 shells running)"


# ── the answer line ─────────────────────────────────────────────────────────

def test_answer_line_names_the_running_shell(replay, vloop):
    """G29: the answer sent while the shell runs says so, by name."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        w.cancel()

    _run(vloop, scenario())
    assert _answer(r)[0].endswith(
        f"⏳ 1 shell still running ({DESC}) - results will follow here")


def test_answer_line_names_agents_and_shells(replay, vloop):
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="ship-reviewer")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(2)
        r.stop("launched")
        await asyncio.sleep(8)
        w.cancel()

    _run(vloop, scenario())
    assert _answer(r)[0].endswith(
        "⏳ 1 agent, 1 shell still running (ship-reviewer, run the tests) - "
        "results will follow here")


def test_answer_line_settles_only_after_the_shell_ends(replay, vloop,
                                                      monkeypatch):
    """G30: the line waits for the shell, then is edited once to done."""
    _layout()
    r = replay
    mon = _monitor(r, monkeypatch)

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        await mon._scan()
        await asyncio.sleep(BG_AGENTS_SETTLE_SECONDS + 5)
        await mon._scan()
        before = list(_answer(r))
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(2)
        r.stop("the tests pass")
        await asyncio.sleep(3)
        await mon._scan()
        await asyncio.sleep(BG_AGENTS_SETTLE_SECONDS + 1)
        await mon._scan()
        await asyncio.sleep(3)
        w.cancel()
        return before

    before = _run(vloop, scenario())
    assert len(before) == 1  # not edited while the shell ran
    after = _answer(r)
    assert len(after) == 2
    assert f"✅ shell: {DESC} - done (" in after[-1]
    assert "still running" not in after[-1]


# ── the max age ─────────────────────────────────────────────────────────────

def test_shell_past_max_age_closes_the_job_with_a_notice(replay, vloop,
                                                        monkeypatch):
    """G18, G32: past the max age the shell stops counting, and a job
    waiting only on it closes with the shell's own notice (the sweep is
    not an end, so no grace window keeps the job open)."""
    monkeypatch.setattr(session_monitor, "BG_SHELL_MAX_TRACK_SECONDS", 100.0)
    _layout()
    r = replay
    mon = _monitor(r, monkeypatch)

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        await mon._scan()
        early = _pin(r)
        await asyncio.sleep(100)
        await mon._scan()
        await asyncio.sleep(3)
        w.cancel()
        return early

    early = _run(vloop, scenario())
    assert early == "💤 aipager_boss (idle, 1 shell running)"
    assert _pin(r) == "💤 aipager_boss (idle)"
    card = _only_card(r)
    assert card["stop"] is False
    assert card["text"].startswith(
        "⚠️ <b>aipager_boss</b> · Finished (no end seen for a background "
        "shell after ")


def test_shell_past_max_age_mid_turn_reads_no_end_seen(replay, vloop,
                                                      monkeypatch):
    """A running turn's shell swept by age settles its row to "no end
    seen"; the turn itself goes on."""
    monkeypatch.setattr(session_monitor, "BG_SHELL_MAX_TRACK_SECONDS", 30.0)
    _layout()
    r = replay
    mon = _monitor(r, monkeypatch)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(35)
        r.tool("still going")
        await mon._scan()
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    card = _only_card(r)
    assert card["stop"] is True
    assert f"⏹ `shell: {DESC} - no end seen (" in card["text"]


def test_lost_shell_leaves_the_answer_line_as_sent(replay, vloop,
                                                  monkeypatch):
    """G31: a shell dropped by age is not known to have ended: the line
    that named it is never edited to done."""
    monkeypatch.setattr(session_monitor, "BG_SHELL_MAX_TRACK_SECONDS", 100.0)
    _layout()
    r = replay
    mon = _monitor(r, monkeypatch)

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        await asyncio.sleep(100)
        for _ in range(4):
            await mon._scan()
            await asyncio.sleep(BG_AGENTS_SETTLE_SECONDS + 1)
        w.cancel()

    _run(vloop, scenario())
    assert len(_answer(r)) == 1
    assert "still running" in _answer(r)[0]


def test_max_track_env_override(caplog):
    """G33: blank or unset is the default, quietly; an unparsable or
    non-positive value is the default with a warning; a valid one wins."""
    env = "AIPAGER_BG_SHELL_MAX_TRACK"

    def resolve(value):
        try:
            return resolve_bg_shell_max_track({env: value})
        except ValueError:
            return None

    caplog.set_level(logging.WARNING, logger="aipager.session_monitor")
    assert resolve_bg_shell_max_track({}) == 7200.0
    assert resolve("") == 7200.0
    assert resolve("   ") == 7200.0
    assert caplog.records == []
    assert resolve("600") == 600.0
    assert resolve("90.5") == 90.5
    assert resolve("abc") == 7200.0
    assert resolve("-5") == 7200.0
    assert resolve("0") == 7200.0
    assert len(caplog.records) == 3


# ── /stop, /kill, a session's end, /clear ───────────────────────────────────

def test_stop_closes_the_job_but_keeps_the_shell_counted(replay, vloop,
                                                         monkeypatch):
    """G27: /stop ends the job, but Escape does not end a background
    shell: the pin keeps counting it, and its later wake-up is a fresh
    turn with a card of its own."""
    _layout()
    r = replay
    _mock_dtach(monkeypatch)

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        await r.bot._stop_session_core(r.sess)
        await asyncio.sleep(2)
        after_stop = _pin(r)
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(1)
        r.tool("read the output")
        await asyncio.sleep(2)
        r.stop("the tests pass")
        await asyncio.sleep(10)
        w.cancel()
        return after_stop

    after_stop = _run(vloop, scenario())
    assert after_stop == "💤 aipager_boss (idle, 1 shell running)"
    first = r.chat.cards[min(r.chat.cards)]
    assert "Stopped" in first["text"]
    assert len(r.chat.cards) == 2
    assert _pin(r) == "💤 aipager_boss (idle)"


def test_kill_removes_shells(replay, vloop, monkeypatch):
    _layout()
    r = replay
    _mock_dtach(monkeypatch)
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        before = _pin(r)
        await r.bot._kill_session_core(r.sess.name, r.sess.label)
        await asyncio.sleep(2)
        w.cancel()
        return before

    before = _run(vloop, scenario())
    assert "1 shell running" in before
    assert r.bot.registry.get(r.sess.name) is None
    assert "shell" not in _pin(r)


@pytest.mark.parametrize("reason", ["prompt_input_exit", "other"])
def test_session_end_clears_shells(replay, vloop, reason):
    """G34: a session's end (SessionEnd, whatever its reason) runs no
    shell any more."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        r.hook(hook_event_name="SessionEnd", reason=reason)
        await asyncio.sleep(5)
        w.cancel()

    _run(vloop, scenario())
    assert r.sess.bg_shells == {}
    assert r.sess.bg_shell_labels() == []
    assert r.sess.agents_lines == []


def test_clear_clears_shells(replay, vloop):
    """G34: /clear ends the Claude session with reason "clear"."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        r.hook(hook_event_name="SessionEnd", reason="clear")
        await asyncio.sleep(5)
        w.cancel()

    _run(vloop, scenario())
    assert r.sess.bg_shells == {}


# ── bounds ──────────────────────────────────────────────────────────────────

def test_many_shells_are_capped(replay, vloop):
    """G35: never more than BG_SHELLS_CAP counted; the oldest go first."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it", tools=0)
        for i in range(BG_SHELLS_CAP + 5):
            r.bg_bash(f"job {i}", f"bshell{i:03d}")
            await asyncio.sleep(0.01)
        await asyncio.sleep(5)
        w.cancel()

    _run(vloop, scenario())
    assert _pin(r) == \
        f"⚙️ aipager_boss (working, {BG_SHELLS_CAP} shells running)"
    assert "bshell000" not in r.sess.bg_shells
    assert f"bshell{BG_SHELLS_CAP + 4:03d}" in r.sess.bg_shells


def test_shells_are_never_persisted(replay, vloop):
    """G36: monotonic stamps mean nothing after a restart."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _shell_job(r)
        w.cancel()

    _run(vloop, scenario())
    assert r.sess.bg_shells
    r.bot.registry.save()
    saved = state_mod.SESSION_STATE_FILE.read_text()
    assert "bshell01" not in saved
    assert "bg_shells" not in json.dumps(json.loads(saved))
    fresh = state_mod.SessionRegistry()
    fresh.load()
    assert fresh.get(r.sess.name).bg_shells == {}


def test_end_for_an_unknown_id_changes_nothing(replay, vloop):
    """G37: a TaskStop of an id that is no running shell (an agent's) and
    a notification for an unknown id end nothing, and the stop's own row
    settles as any tool's does."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        r.bg_bash(DESC, "bshell01")
        await asyncio.sleep(1)
        r.task_stop("a1234")
        r.enqueue(r.notification("bnever99", "completed", "x"))
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    card = _only_card(r)
    assert "✅ `TaskStop`" in card["text"]
    assert f"⏳ `shell: {DESC} (" in card["text"]
    assert "1 shell running" in _pin(r)


# ── wording and volume ──────────────────────────────────────────────────────

def test_no_em_dash_in_any_shell_text(replay, vloop, monkeypatch):
    """Every card version, answer, pin and notice this feature produced."""
    monkeypatch.setattr(session_monitor, "BG_SHELL_MAX_TRACK_SECONDS", 500.0)
    _layout()
    r = replay
    mon = _monitor(r, monkeypatch)
    pins = []

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="ship-reviewer")
        r.bg_bash(DESC, "bshell01")
        r.bg_bash("second", "bshell02")
        r.bg_bash("third", "bshell03")
        r.bg_bash("fourth", "bshell04")
        await asyncio.sleep(2)
        r.enqueue(r.notification(
            "bshell02", "failed",
            'Background command "second" failed with exit code 2'))
        r.task_stop("bshell03")
        await asyncio.sleep(5)
        pins.append(_pin(r))
        r.stop("launched")
        await asyncio.sleep(8)
        pins.append(_pin(r))
        r.wake(r.notification("bshell01", "completed", DONE))
        await asyncio.sleep(2)
        r.stop("one done")
        await asyncio.sleep(8)
        await asyncio.sleep(500)
        await mon._scan()
        await asyncio.sleep(10)
        pins.append(_pin(r))
        w.cancel()

    _run(vloop, scenario())
    texts = list(pins)
    for card in r.chat.cards.values():
        texts.extend(card["texts"])
    for versions in r.chat.answer_texts.values():
        texts.extend(versions)
    texts.extend(t for t, _ in r.chat.plain)
    joined = "\n".join(texts)
    assert "shell" in joined
    assert EM not in joined


def test_one_card_edit_path_no_new_message_per_shell(replay, vloop):
    """Shells ride the card's own edits: launching and ending several
    sends no message of its own."""
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it")
        for i in range(3):
            r.bg_bash(f"job {i}", f"bshell0{i}")
        await asyncio.sleep(2)
        for i in range(3):
            r.enqueue(r.notification(
                f"bshell0{i}", "completed",
                f'Background command "job {i}" completed (exit code 0)'))
        await asyncio.sleep(10)
        r.stop("all three done")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.count("sendMessage") == 1  # the card
    assert r.chat.count("sendRichMessage") == 1  # the answer
    assert r.chat.plain == []
