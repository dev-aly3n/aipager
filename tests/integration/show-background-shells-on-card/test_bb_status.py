"""Black-box: /stop, /kill, SessionEnd, the max-age sweep, its env
resolver, the cap, persistence and the answer-line wording helpers
(design.md success criteria 7 to 11)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from aipager import preferences as prefs
from aipager import session_monitor
from aipager.bot.agents_line import AgentRow, BlockView, render_block
from aipager.session_monitor import SessionMonitor, resolve_bg_shell_max_track
from aipager.state import BG_SHELLS_CAP

CHAT = 256113222
DESC = "run the tests"
DONE = f'Background command "{DESC}" completed (exit code 0)'
IDLE = "💤 aipager_boss (idle)"
IDLE_ONE = "💤 aipager_boss (idle, 1 shell running)"


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _worker(r):
    return asyncio.ensure_future(r._updates())


async def _turn(r, mid: int = 1, text: str = "please run it") -> None:
    r.say(mid, text)
    await r.updates.join()
    r.prompt_hooks(mid, text)
    await asyncio.sleep(1)


def _setup(monkeypatch, r):
    prefs.set_preference(CHAT, "layout", "card")
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))

    async def _names():
        return [r.sess.name] if r.bot.registry.get(r.sess.name) else []

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)


def _pin(r) -> str:
    return r.bot._render_pinned(CHAT)[0]


def _every_text(r) -> str:
    parts = [t for c in r.chat.cards.values()
             for t in c.get("texts", [c["text"]])]
    parts += [t for v in r.chat.answer_texts.values() for t in v]
    parts += [t for t, _ in r.chat.plain]
    return "\n".join(str(p) for p in parts)


async def _job_waiting(r, desc=DESC, task_id="bshell1"):
    await _turn(r)
    r.bg_bash(desc, task_id)
    await asyncio.sleep(5)
    r.stop("started it")
    await asyncio.sleep(10)


# ── /stop: closes the job, keeps the shell counted ─────────────────────────

def _stopped(r, vloop, monkeypatch, *, wake=False):
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _job_waiting(r)
        await r.bot._stop_session_core(r.sess)
        await asyncio.sleep(10)
        out = {"pin": _pin(r), "cards_after_stop": len(r.chat.cards),
               "live_after_stop": list(r.chat.live_cards()),
               "answers": [r.chat.answer_texts[m][-1]
                           for m in sorted(r.chat.answer_texts)]}
        if wake:
            r.wake(r.notification("bshell1", "completed", DONE))
            await asyncio.sleep(2)
            r.tool("read the output")
            await asyncio.sleep(2)
            r.stop("the tests passed")
            await asyncio.sleep(15)
            out["pin_end"] = _pin(r)
        w.cancel()
        return out

    return _run(vloop, scenario())


def test_bb_stop_keeps_the_shell_in_the_pin(replay, vloop, monkeypatch):
    out = _stopped(replay, vloop, monkeypatch)
    assert out["pin"] == IDLE_ONE


def test_bb_stop_closes_the_job_card(replay, vloop, monkeypatch):
    out = _stopped(replay, vloop, monkeypatch)
    assert out["live_after_stop"] == []


def test_bb_stop_keeps_the_answer_line_running(replay, vloop, monkeypatch):
    out = _stopped(replay, vloop, monkeypatch)
    assert out["answers"][0].rstrip().splitlines()[-1] == "🖥 1 shell running"


def test_bb_wakeup_after_stop_opens_a_fresh_card(replay, vloop, monkeypatch):
    out = _stopped(replay, vloop, monkeypatch, wake=True)
    assert len(replay.chat.cards) == out["cards_after_stop"] + 1


def test_bb_wakeup_after_stop_clears_the_pin(replay, vloop, monkeypatch):
    out = _stopped(replay, vloop, monkeypatch, wake=True)
    assert out["pin_end"] == IDLE


# ── SessionEnd and /kill ────────────────────────────────────────────────────

@pytest.mark.parametrize("reason", ["clear", "prompt_input_exit", "logout",
                                    "other"])
def test_bb_session_end_drops_shells_from_the_pin(replay, vloop, monkeypatch,
                                                  reason):
    r = replay
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _job_waiting(r)
        r.hook(hook_event_name="SessionEnd", reason=reason)
        await asyncio.sleep(5)
        w.cancel()
        return _pin(r)

    assert "shell" not in _run(vloop, scenario())


def test_bb_kill_drops_shells_from_the_pin(replay, vloop, monkeypatch):
    r = replay
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _job_waiting(r)
        await r.bot._kill_session_core(r.sess.name, "aipager_boss")
        await asyncio.sleep(5)
        w.cancel()
        return _pin(r)

    assert "shell" not in _run(vloop, scenario())


# ── the max-age sweep ───────────────────────────────────────────────────────

def _aged(r, vloop, monkeypatch, *, max_age, wait, busy=False):
    _setup(monkeypatch, r)
    monkeypatch.setattr(session_monitor, "BG_SHELL_MAX_TRACK_SECONDS",
                        float(max_age))

    async def scenario():
        w = _worker(r)
        if busy:
            await _turn(r)
            r.bg_bash(DESC, "bshell1")
            await asyncio.sleep(5)
        else:
            await _job_waiting(r)
        await asyncio.sleep(wait)
        await SessionMonitor(r.bot.registry, r.bot.notify)._scan()
        await asyncio.sleep(15)
        out = {"pin": _pin(r), "live": list(r.chat.live_cards()),
               "card": r.chat.cards[max(r.chat.cards)]["text"]}
        w.cancel()
        return out

    return _run(vloop, scenario())


def test_bb_shell_past_max_age_stops_counting(replay, vloop, monkeypatch):
    out = _aged(replay, vloop, monkeypatch, max_age=120, wait=200)
    assert out["pin"] == IDLE


def test_bb_shell_under_max_age_keeps_counting(replay, vloop, monkeypatch):
    out = _aged(replay, vloop, monkeypatch, max_age=600, wait=200)
    assert out["pin"] == IDLE_ONE


def test_bb_shell_past_max_age_closes_the_idle_job(replay, vloop,
                                                   monkeypatch):
    out = _aged(replay, vloop, monkeypatch, max_age=120, wait=200)
    assert out["live"] == []


def test_bb_shell_past_max_age_sends_the_shell_notice(replay, vloop,
                                                      monkeypatch):
    _aged(replay, vloop, monkeypatch, max_age=120, wait=200)
    assert ("Finished (no end seen for a background shell after"
            in _every_text(replay))


def test_bb_shell_past_max_age_row_reads_no_end_seen(replay, vloop,
                                                     monkeypatch):
    _aged(replay, vloop, monkeypatch, max_age=120, wait=200, busy=True)
    assert f"⏹ `shell: {DESC} - no end seen (" in _every_text(replay)


def test_bb_busy_turn_sweep_sends_no_job_notice(replay, vloop, monkeypatch):
    _aged(replay, vloop, monkeypatch, max_age=120, wait=200, busy=True)
    assert "no end seen for a background shell" not in _every_text(replay)


def test_bb_answer_line_left_as_sent_after_max_age(replay, vloop,
                                                   monkeypatch):
    _aged(replay, vloop, monkeypatch, max_age=120, wait=200)
    first = min(replay.chat.answer_texts)
    assert len(replay.chat.answer_texts[first]) == 1


def test_bb_late_wakeup_after_max_age_opens_a_fresh_card(replay, vloop,
                                                         monkeypatch):
    r = replay
    _setup(monkeypatch, r)
    monkeypatch.setattr(session_monitor, "BG_SHELL_MAX_TRACK_SECONDS", 120.0)

    async def scenario():
        w = _worker(r)
        await _job_waiting(r)
        await asyncio.sleep(200)
        await SessionMonitor(r.bot.registry, r.bot.notify)._scan()
        await asyncio.sleep(15)
        before = len(r.chat.cards)
        r.wake(r.notification("bshell1", "completed", DONE))
        await asyncio.sleep(2)
        r.tool("read the output")
        await asyncio.sleep(2)
        r.stop("late result")
        await asyncio.sleep(15)
        w.cancel()
        return before

    before = _run(vloop, scenario())
    assert len(r.chat.cards) == before + 1


# ── resolve_bg_shell_max_track: env boundaries ─────────────────────────────

@pytest.mark.parametrize("value", [None, "", "   ", "0", "0.0", "-1", "-7200",
                                   "abc", "12abc", "1e"])
def test_bb_env_falls_back_to_default(value):
    env = {} if value is None else {"AIPAGER_BG_SHELL_MAX_TRACK": value}
    assert resolve_bg_shell_max_track(env) == 7200.0


@pytest.mark.parametrize("value,expected", [("90", 90.0), ("1", 1.0),
                                            ("0.5", 0.5), ("86400", 86400.0),
                                            (" 300 ", 300.0)])
def test_bb_env_valid_value_is_used(value, expected):
    assert resolve_bg_shell_max_track(
        {"AIPAGER_BG_SHELL_MAX_TRACK": value}) == expected


def test_bb_env_resolver_reads_process_env(monkeypatch):
    monkeypatch.setenv("AIPAGER_BG_SHELL_MAX_TRACK", "45")
    assert resolve_bg_shell_max_track() == 45.0


def test_bb_env_resolver_default_without_process_env(monkeypatch):
    monkeypatch.delenv("AIPAGER_BG_SHELL_MAX_TRACK", raising=False)
    assert resolve_bg_shell_max_track() == 7200.0


# ── the cap ─────────────────────────────────────────────────────────────────

def _launched(r, vloop, monkeypatch, n) -> str:
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        for i in range(n):
            r.bg_bash(f"job {i}", f"bjob{i}")
        await asyncio.sleep(5)
        w.cancel()
        return _pin(r)

    return _run(vloop, scenario())


def test_bb_cap_is_fifty():
    assert BG_SHELLS_CAP == 50


def test_bb_cap_exactly_counts_all(replay, vloop, monkeypatch):
    pin = _launched(replay, vloop, monkeypatch, BG_SHELLS_CAP)
    assert f"{BG_SHELLS_CAP} shells running" in pin


def test_bb_cap_plus_one_counts_the_cap(replay, vloop, monkeypatch):
    pin = _launched(replay, vloop, monkeypatch, BG_SHELLS_CAP + 1)
    assert f"{BG_SHELLS_CAP} shells running" in pin


def test_bb_cap_far_above_counts_the_cap(replay, vloop, monkeypatch):
    pin = _launched(replay, vloop, monkeypatch, BG_SHELLS_CAP * 3)
    assert f"{BG_SHELLS_CAP} shells running" in pin


# ── nothing persisted ───────────────────────────────────────────────────────

def test_bb_shells_are_not_written_to_the_state_file(replay, vloop,
                                                     monkeypatch,
                                                     tmp_state_file):
    r = replay
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.bg_bash("zq unique shell label", "bpersist42")
        await asyncio.sleep(5)
        r.bot.registry.save()
        w.cancel()

    _run(vloop, scenario())
    raw = tmp_state_file.read_text()
    assert "bpersist42" not in raw and "zq unique shell label" not in raw


# ── the answer's status block: shells ───────────────────────────────────────

def test_bb_block_one_shell():
    assert render_block(BlockView(shells_running=1)) == "🖥 1 shell running"


def test_bb_block_two_shells():
    assert render_block(BlockView(shells_running=2)) == "🖥 2 shells running"


def test_bb_block_mixed():
    view = BlockView(agents=(AgentRow("ship-reviewer", False),), shells_running=1)
    assert render_block(view).split("\n\n")[0] == (
        "⏳ 1 agent working · 🖥 1 shell running")


def test_bb_block_never_names_a_shell():
    """A count only: the shell's command never shows."""
    assert DESC not in render_block(BlockView(shells_running=1))


def test_bb_block_one_shell_finished():
    assert render_block(BlockView(shells_finished=1, shells_finished_seconds=45)) == \
        "1 shell finished (0:45)"


def test_bb_block_agents_done_and_a_shell_finished():
    view = BlockView(agents=(AgentRow("a", True, 1), AgentRow("b", True, 2)),
                     agents_done_seconds=6 * 60, shells_finished=1,
                     shells_finished_seconds=3 * 3600 + 3 * 60)
    assert render_block(view) == "✅ 2 agents done (6:00) · 1 shell finished (3:03:00)"


def test_bb_block_two_shells_stopped():
    assert render_block(BlockView(shells_stopped=2, shells_stopped_seconds=61)) == \
        "2 shells stopped (1:01)"


@pytest.mark.parametrize("view", [
    BlockView(shells_running=1),
    BlockView(agents=(AgentRow("a", False),), shells_running=1),
    BlockView(shells_finished=1, shells_stopped=1),
], ids=["shell", "agent-and-shell", "finished-and-stopped"])
def test_bb_helper_lines_have_no_em_dash(view):
    assert "—" not in render_block(view)


def test_bb_no_em_dash_across_status_scenarios(replay, vloop, monkeypatch):
    _aged(replay, vloop, monkeypatch, max_age=120, wait=200)
    assert "—" not in _every_text(replay) + _pin(replay)
