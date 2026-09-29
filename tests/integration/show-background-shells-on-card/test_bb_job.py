"""Black-box: a background shell holds its job open like a background
agent does (design.md success criteria 3 to 5, 7 and 10).

Scenarios drive the daemon through the replay harness (conftest.py) and
assert on the cards, answers and pinned bar the chat would show.
"""

from __future__ import annotations

import asyncio

from aipager import preferences as prefs
from aipager.session_monitor import SessionMonitor

CHAT = 256113222
DESC = "run the tests"
DONE = f'Background command "{DESC}" completed (exit code 0)'
LINE = f"⏳ 1 shell still running ({DESC}) - results will follow here"


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _worker(r):
    return asyncio.ensure_future(r._updates())


async def _turn(r, mid: int = 1, text: str = "please run it") -> None:
    r.say(mid, text)
    await r.updates.join()
    r.prompt_hooks(mid, text)
    await asyncio.sleep(1)


def _setup(monkeypatch):
    prefs.set_preference(CHAT, "layout", "card")
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))


def _pin(r) -> str:
    return r.bot._render_pinned(CHAT)[0]


def _answers(r) -> list[str]:
    """Every answer's latest text, oldest first."""
    return [r.chat.answer_texts[m][-1] for m in sorted(r.chat.answer_texts)]


def _last_line(text: str) -> str:
    return text.rstrip().splitlines()[-1]


async def _job_waiting(r, *, agent=False, desc=DESC, task_id="bshell1"):
    """A turn that launched a background shell (and maybe an agent) and
    ended while it runs."""
    await _turn(r)
    if agent:
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
    r.bg_bash(desc, task_id)
    await asyncio.sleep(5)
    r.stop("started it")
    await asyncio.sleep(10)


async def _wake_and_finish(r, task_id="bshell1", summary=DONE,
                           status="completed"):
    r.wake(r.notification(task_id, status, summary))
    await asyncio.sleep(2)
    r.tool("read the output")
    await asyncio.sleep(2)
    r.stop("the tests passed")
    await asyncio.sleep(15)


def _waiting(r, vloop, monkeypatch, **kw) -> dict:
    _setup(monkeypatch)

    async def scenario():
        w = _worker(r)
        await _job_waiting(r, **kw)
        snap = {"live": list(r.chat.live_cards()),
                "card": r.chat.cards[max(r.chat.cards)]["text"],
                "pin": _pin(r), "answers": _answers(r)}
        w.cancel()
        return snap

    return _run(vloop, scenario())


# ── Stop while the shell runs: interim ─────────────────────────────────────

def test_bb_stop_with_running_shell_keeps_the_stop_button(replay, vloop,
                                                          monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch)
    assert len(snap["live"]) == 1


def test_bb_stop_with_running_shell_is_not_done(replay, vloop, monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch)
    assert "· Done ·" not in snap["card"]


def test_bb_waiting_card_says_one_shell_still_working(replay, vloop,
                                                      monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch)
    assert "· 1 shell still working ·" in snap["card"]


def test_bb_waiting_card_keeps_the_running_row(replay, vloop, monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch)
    assert f"⏳ `shell: {DESC} (" in snap["card"]


def test_bb_answer_ends_with_shell_still_running_line(replay, vloop,
                                                      monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch)
    assert _last_line(snap["answers"][0]) == LINE


def test_bb_pin_says_idle_with_one_shell_running(replay, vloop, monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch)
    assert snap["pin"] == "💤 aipager_boss (idle, 1 shell running)"


def test_bb_answer_line_label_is_cut_to_32(replay, vloop, monkeypatch):
    long = "z" * 40
    snap = _waiting(replay, vloop, monkeypatch, desc=long)
    line = _last_line(snap["answers"][0])
    assert line.startswith("⏳ 1 shell still running (zzz") and long not in line


def test_bb_answer_line_label_of_32_is_whole(replay, vloop, monkeypatch):
    label = "q" * 32
    snap = _waiting(replay, vloop, monkeypatch, desc=label)
    assert f"({label})" in _last_line(snap["answers"][0])


# ── mixed agents and shells ─────────────────────────────────────────────────

def test_bb_mixed_waiting_card_wording(replay, vloop, monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch, agent=True)
    assert "· 1 agent (pipeline-runner), 1 shell still working ·" in \
        snap["card"]


def test_bb_mixed_answer_line_wording(replay, vloop, monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch, agent=True)
    assert _last_line(snap["answers"][0]) == (
        f"⏳ 1 agent, 1 shell still running (pipeline-runner, {DESC}) - "
        "results will follow here")


def test_bb_mixed_pin_wording(replay, vloop, monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch, agent=True)
    assert snap["pin"] == "💤 aipager_boss (idle, 1 agent, 1 shell running)"


# ── the wake-up continues the same job ─────────────────────────────────────

def _full_job(r, vloop, monkeypatch, *, pre_read=False, scans=False):
    _setup(monkeypatch)

    async def _names():
        return [r.sess.name]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)

    async def scenario():
        w = _worker(r)
        await _job_waiting(r)
        if pre_read:
            # The end reaches the transcript and a waiting-card tick reads
            # it before the wake-up's UserPromptSubmit arrives.
            r.enqueue(r.notification("bshell1", "completed", DONE))
            await asyncio.sleep(5)
        await _wake_and_finish(r)
        if scans:
            mon = SessionMonitor(r.bot.registry, r.bot.notify)
            await mon._scan()
            await asyncio.sleep(6)
            await mon._scan()
            await asyncio.sleep(10)
        w.cancel()
        return {"pin": _pin(r)}

    return _run(vloop, scenario())


def test_bb_wakeup_reuses_the_same_card(replay, vloop, monkeypatch):
    _full_job(replay, vloop, monkeypatch)
    assert len(replay.chat.cards) == 1


def test_bb_wakeup_final_stop_settles_the_card(replay, vloop, monkeypatch):
    _full_job(replay, vloop, monkeypatch)
    assert replay.chat.live_cards() == []


def test_bb_wakeup_final_card_reads_done(replay, vloop, monkeypatch):
    _full_job(replay, vloop, monkeypatch)
    card = replay.chat.cards[max(replay.chat.cards)]["text"]
    assert f"✅ `shell: {DESC} - done (" in card


def test_bb_wakeup_clears_the_pin_count(replay, vloop, monkeypatch):
    out = _full_job(replay, vloop, monkeypatch)
    assert out["pin"] == "💤 aipager_boss (idle)"


def test_bb_answer_line_settles_to_shell_done(replay, vloop, monkeypatch):
    _full_job(replay, vloop, monkeypatch, scans=True)
    first = min(replay.chat.answer_texts)
    assert _last_line(replay.chat.answer_texts[first][-1]).startswith(
        f"✅ shell: {DESC} - done (")


def test_bb_answer_line_settles_with_one_edit(replay, vloop, monkeypatch):
    _full_job(replay, vloop, monkeypatch, scans=True)
    first = min(replay.chat.answer_texts)
    assert len(replay.chat.answer_texts[first]) == 2


def test_bb_answer_line_not_settled_while_shell_runs(replay, vloop,
                                                     monkeypatch):
    _setup(monkeypatch)
    r = replay

    async def _names():
        return [r.sess.name]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)

    async def scenario():
        w = _worker(r)
        await _job_waiting(r)
        mon = SessionMonitor(r.bot.registry, r.bot.notify)
        for _ in range(3):
            await mon._scan()
            await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    first = min(r.chat.answer_texts)
    assert _last_line(r.chat.answer_texts[first][-1]) == LINE


def test_bb_end_read_before_wakeup_still_reuses_the_card(replay, vloop,
                                                         monkeypatch):
    _full_job(replay, vloop, monkeypatch, pre_read=True)
    assert len(replay.chat.cards) == 1


def test_bb_end_read_before_wakeup_final_stop_settles(replay, vloop,
                                                      monkeypatch):
    _full_job(replay, vloop, monkeypatch, pre_read=True)
    assert replay.chat.live_cards() == []


def test_bb_failed_wakeup_keeps_failed_row_on_final_card(replay, vloop,
                                                         monkeypatch):
    _setup(monkeypatch)
    r = replay

    async def scenario():
        w = _worker(r)
        await _job_waiting(r)
        await _wake_and_finish(
            r, status="failed",
            summary=f'Background command "{DESC}" failed with exit code 4')
        w.cancel()

    _run(vloop, scenario())
    card = r.chat.cards[max(r.chat.cards)]["text"]
    assert f"❌ `shell: {DESC} - failed (exit 4)`" in card


def test_bb_monitor_wakeup_does_not_end_the_shell(replay, vloop, monkeypatch):
    """A Monitor event (no <status>) waking Claude is not the shell's end:
    the pin still counts the shell after that turn."""
    _setup(monkeypatch)
    r = replay

    async def scenario():
        w = _worker(r)
        await _job_waiting(r)
        r.wake(r.notification("bmon1", None, 'Monitor event: "watch" fired'))
        await asyncio.sleep(2)
        r.stop("noted the event")
        await asyncio.sleep(10)
        w.cancel()
        return _pin(r)

    assert _run(vloop, scenario()) == "💤 aipager_boss (idle, 1 shell running)"


def test_bb_subagent_shell_does_not_hold_the_job(replay, vloop, monkeypatch):
    _setup(monkeypatch)
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.bg_bash(DESC, "bsub1", agent_id="a9")
        await asyncio.sleep(5)
        r.stop("done")
        await asyncio.sleep(15)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.live_cards() == []


# ── TaskStop ends the job's wait ────────────────────────────────────────────

def test_bb_taskstop_in_continuation_lets_the_job_finish(replay, vloop,
                                                         monkeypatch):
    """Two shells; one ends and wakes Claude, which TaskStops the other.
    Nothing is left running, so that turn's Stop settles the card."""
    _setup(monkeypatch)
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.bg_bash("first job", "bone1")
        r.bg_bash("second job", "btwo2")
        await asyncio.sleep(5)
        r.stop("started both")
        await asyncio.sleep(10)
        r.wake(r.notification("bone1", "completed",
                              'Background command "first job" completed '
                              '(exit code 0)'))
        await asyncio.sleep(2)
        r.task_stop("btwo2")
        await asyncio.sleep(2)
        r.stop("stopped the second")
        await asyncio.sleep(15)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.live_cards() == []


def test_bb_one_of_two_shells_ending_keeps_the_job_waiting(replay, vloop,
                                                           monkeypatch):
    _setup(monkeypatch)
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.bg_bash("first job", "bone1")
        r.bg_bash("second job", "btwo2")
        await asyncio.sleep(5)
        r.stop("started both")
        await asyncio.sleep(10)
        await _wake_and_finish(
            r, task_id="bone1",
            summary='Background command "first job" completed (exit code 0)')
        w.cancel()

    _run(vloop, scenario())
    assert len(r.chat.live_cards()) == 1


# ── working pin with two shells ─────────────────────────────────────────────

def test_bb_pin_working_with_two_shells(replay, vloop, monkeypatch):
    _setup(monkeypatch)
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.bg_bash("first job", "bone1")
        r.bg_bash("second job", "btwo2")
        await asyncio.sleep(5)
        pin = _pin(r)
        w.cancel()
        return pin

    assert "(working, 2 shells running)" in _run(vloop, scenario())


def test_bb_no_em_dash_in_job_texts(replay, vloop, monkeypatch):
    snap = _waiting(replay, vloop, monkeypatch, agent=True)
    texts = [snap["card"], snap["pin"], *snap["answers"]]
    assert all("—" not in t for t in texts)
