"""Black-box, iteration 3: a shell's end seen before its launch, and which
wake-ups wait behind a held turn.

1. The end is read before the launch is recorded, in both orders of
   arrival: a transcript ``queue-operation`` end read by a card tick
   before the launch PostToolUse, and a wake-up that arrives while the
   launch is still held behind an earlier turn's held answer. For each
   outcome (completed, failed, killed) the shell must be settled at once:
   its row, the pin and the answer line never show it as running.
2. An end for an id that is never launched creates no row and no count,
   and does not stop a different id from being tracked.
3. The same id launched long after the remembered end (an hour of
   virtual time) is a new shell and is tracked as running again.
4. A prompt typed by a human while an earlier answer is held is handled
   at once, and a Telegram prompt sent while a shell turn is held starts
   its own job.
5. An agent's wake-up under a held finish still continues its job.

Written from design.md, entrypoints.md, review-2.md and the claimed fixes
only. Mocked beyond the harness: Telegram's latency on the first
``sendRichMessage`` (a flood-held answer), as in test_bb_iter2.py.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

import aipager.bot.rich_message as rm
from aipager import preferences as prefs

CHAT = 256113222
DESC = "run the tests"
IDLE = "💤 aipager_boss (idle)"
WORKING = "⚙️ aipager_boss (working"
HELD = 30.0
#: Well beyond the few minutes an ended id is remembered for.
LONG_AFTER = 3600

SUMMARY = {
    "completed": f'Background command "{DESC}" completed (exit code 0)',
    "failed": f'Background command "{DESC}" failed with exit code 3',
    "killed": f'Background command "{DESC}" was stopped',
}
ROW = {
    "completed": f"✅ `shell: {DESC} - done (",
    "failed": f"❌ `shell: {DESC} - failed (exit 3)`",
    "killed": f"⏹ `shell: {DESC} - stopped (",
}
OUTCOMES = list(SUMMARY)


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

    async def _names():
        return [r.sess.name]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _names)


def _pin(r) -> str:
    return r.bot._render_pinned(CHAT)[0]


def _card(r) -> str:
    assert r.chat.cards, "no card was sent"
    return r.chat.cards[max(r.chat.cards)]["text"]


def _all_card_texts(r) -> str:
    return "\n".join(t for c in r.chat.cards.values()
                     for t in c.get("texts", [c["text"]]))


def _answer_saying(r, words: str) -> int:
    hits = [m for m in sorted(r.chat.answer_texts)
            if words in r.chat.answer_texts[m][-1]]
    assert hits, f"no answer says {words!r}"
    return hits[-1]


def _answer(r, words: str) -> str:
    return r.chat.answer_texts[_answer_saying(r, words)][-1]


def _hold_first_answer(monkeypatch, seconds: float) -> None:
    """Telegram takes ``seconds`` to answer the first ``sendRichMessage``
    (a flood-held answer); every later call answers at once."""
    inner = rm._client._transport.handler
    held = {"done": False}

    async def handler(request: httpx.Request) -> httpx.Response:
        if (request.url.path.endswith("sendRichMessage")
                and not held["done"]):
            held["done"] = True
            await asyncio.sleep(seconds)
        return inner(request)

    monkeypatch.setattr(rm, "_client", httpx.AsyncClient(
        transport=httpx.MockTransport(handler)))


# ── 1a. the transcript end is read before the launch arrives ──────────────

def _end_read_first(r, vloop, monkeypatch, status: str) -> dict:
    """Mid-turn, a card tick reads the shell's end from the transcript;
    only then does the launch PostToolUse arrive. The turn then stops."""
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.tool("look around")
        await asyncio.sleep(3)
        r.enqueue(r.notification("bshell1", status, SUMMARY[status]))
        await asyncio.sleep(10)
        r.bg_bash(DESC, "bshell1")
        await asyncio.sleep(10)
        out = {"card_mid": _card(r), "pin_mid": _pin(r)}
        r.stop("started it")
        await asyncio.sleep(20)
        out.update(pin=_pin(r), card=_card(r))
        w.cancel()
        return out

    return _run(vloop, scenario())


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_end_read_first_row_is_settled_at_launch(replay, vloop,
                                                    monkeypatch, status):
    out = _end_read_first(replay, vloop, monkeypatch, status)
    assert ROW[status] in out["card_mid"]


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_end_read_first_row_never_runs(replay, vloop, monkeypatch,
                                          status):
    _end_read_first(replay, vloop, monkeypatch, status)
    assert f"⏳ `shell: {DESC}" not in _all_card_texts(replay)


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_end_read_first_pin_never_counts_it(replay, vloop, monkeypatch,
                                               status):
    out = _end_read_first(replay, vloop, monkeypatch, status)
    assert "shell" not in out["pin_mid"]


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_end_read_first_stop_finishes_the_card(replay, vloop,
                                                  monkeypatch, status):
    """Nothing runs, so the Stop is not an interim: no Stop button."""
    _end_read_first(replay, vloop, monkeypatch, status)
    assert replay.chat.live_cards() == []


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_end_read_first_finished_card_keeps_the_outcome(replay, vloop,
                                                           monkeypatch,
                                                           status):
    out = _end_read_first(replay, vloop, monkeypatch, status)
    assert ROW[status] in out["card"]


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_end_read_first_answer_has_no_running_line(replay, vloop,
                                                      monkeypatch, status):
    _end_read_first(replay, vloop, monkeypatch, status)
    assert "still running" not in _answer(replay, "started it")


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_end_read_first_pin_ends_idle(replay, vloop, monkeypatch,
                                         status):
    out = _end_read_first(replay, vloop, monkeypatch, status)
    assert out["pin"] == IDLE


def test_bb_end_read_a_minute_before_the_launch_still_settles_it(
        replay, vloop, monkeypatch):
    """Just inside the remembered window: a launch 60 s after its end was
    read is still settled at once."""
    _setup(monkeypatch, replay)
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.enqueue(r.notification("bshell1", "completed",
                                 SUMMARY["completed"]))
        await asyncio.sleep(60)
        r.bg_bash(DESC, "bshell1")
        await asyncio.sleep(5)
        pin = _pin(r)
        w.cancel()
        return pin

    assert "shell" not in _run(vloop, scenario())


# ── 1b. the wake-up arrives while the launch is still held ────────────────

def _wakeup_before_held_launch(r, vloop, monkeypatch, status: str) -> dict:
    """Turn 1's answer is held. Turn 2 launches a shell and stops (both
    held behind turn 1's finish); the shell's wake-up arrives one second
    later, runs a tool and stops."""
    _setup(monkeypatch, r)
    _hold_first_answer(monkeypatch, HELD)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.tool("look around")
        await asyncio.sleep(3)
        r.stop("answer one")
        await asyncio.sleep(2)
        await _turn(r, 2, "now run the tests")
        r.bg_bash(DESC, "bshell1")
        await asyncio.sleep(3)
        r.stop("started it")
        await asyncio.sleep(1)
        r.wake(r.notification("bshell1", status, SUMMARY[status]))
        await asyncio.sleep(2)
        r.tool("read the output")
        await asyncio.sleep(2)
        r.stop("the tests ended")
        await asyncio.sleep(HELD + 30)
        w.cancel()
        return {"pin": _pin(r)}

    return _run(vloop, scenario())


def _card_for(r, reply_to: int) -> str:
    mids = [m for m, c in r.chat.cards.items()
            if c["reply_to"] == reply_to and not c["deleted"]]
    assert mids, f"no card replies to {reply_to}"
    return r.chat.cards[max(mids)]["text"]


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_wakeup_before_held_launch_row_shows_the_outcome(replay, vloop,
                                                            monkeypatch,
                                                            status):
    _wakeup_before_held_launch(replay, vloop, monkeypatch, status)
    assert ROW[status] in _card_for(replay, 2)


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_wakeup_before_held_launch_pin_ends_idle(replay, vloop,
                                                    monkeypatch, status):
    out = _wakeup_before_held_launch(replay, vloop, monkeypatch, status)
    assert out["pin"] == IDLE


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_wakeup_before_held_launch_leaves_no_live_card(replay, vloop,
                                                          monkeypatch,
                                                          status):
    _wakeup_before_held_launch(replay, vloop, monkeypatch, status)
    assert replay.chat.live_cards() == []


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_wakeup_before_held_launch_answer_has_no_running_line(
        replay, vloop, monkeypatch, status):
    _wakeup_before_held_launch(replay, vloop, monkeypatch, status)
    assert "still running" not in _answer(replay, "the tests ended")


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_wakeup_before_held_launch_continues_the_job_card(
        replay, vloop, monkeypatch, status):
    _wakeup_before_held_launch(replay, vloop, monkeypatch, status)
    assert "Bash: read the output" in _card_for(replay, 2)


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_wakeup_before_held_launch_answer_replies_to_the_job(
        replay, vloop, monkeypatch, status):
    _wakeup_before_held_launch(replay, vloop, monkeypatch, status)
    assert replay.chat.answers[_answer_saying(replay, "the tests ended")] == 2


@pytest.mark.parametrize("status", OUTCOMES)
def test_bb_wakeup_before_held_launch_no_bare_finished_header(
        replay, vloop, monkeypatch, status):
    _wakeup_before_held_launch(replay, vloop, monkeypatch, status)
    assert "· Finished (" not in _answer(replay, "the tests ended")


# ── 2. an end for an id that is never launched ────────────────────────────

def _ghost_end(r, vloop, monkeypatch, *, then_launch: str | None) -> dict:
    """Mid-turn, the transcript carries the end of ``bghost1``, which
    this session never launched; optionally another shell launches."""
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.enqueue(r.notification("bghost1", "completed", SUMMARY["completed"]))
        await asyncio.sleep(10)
        if then_launch:
            r.bg_bash(DESC, then_launch)
            await asyncio.sleep(10)
        out = {"pin_mid": _pin(r), "card_mid": _card(r)}
        r.stop("all good")
        await asyncio.sleep(20)
        out.update(pin=_pin(r), live=list(r.chat.live_cards()))
        w.cancel()
        return out

    return _run(vloop, scenario())


def test_bb_ghost_end_creates_no_row(replay, vloop, monkeypatch):
    _ghost_end(replay, vloop, monkeypatch, then_launch=None)
    assert "shell:" not in _all_card_texts(replay)


def test_bb_ghost_end_is_not_counted(replay, vloop, monkeypatch):
    out = _ghost_end(replay, vloop, monkeypatch, then_launch=None)
    assert "shell" not in out["pin_mid"]


def test_bb_ghost_end_does_not_hold_the_card(replay, vloop, monkeypatch):
    out = _ghost_end(replay, vloop, monkeypatch, then_launch=None)
    assert out["live"] == []


def test_bb_ghost_end_answer_has_no_running_line(replay, vloop, monkeypatch):
    _ghost_end(replay, vloop, monkeypatch, then_launch=None)
    assert "still running" not in _answer(replay, "all good")


def test_bb_ghost_end_pin_stays_idle(replay, vloop, monkeypatch):
    out = _ghost_end(replay, vloop, monkeypatch, then_launch=None)
    assert out["pin"] == IDLE


def test_bb_ghost_end_leaves_another_id_running(replay, vloop, monkeypatch):
    out = _ghost_end(replay, vloop, monkeypatch, then_launch="breal1")
    assert f"⏳ `shell: {DESC} (" in out["card_mid"]


def test_bb_ghost_end_leaves_another_id_counted(replay, vloop, monkeypatch):
    out = _ghost_end(replay, vloop, monkeypatch, then_launch="breal1")
    assert out["pin"] == "💤 aipager_boss (idle, 1 shell running)"


def test_bb_ghost_end_leaves_another_id_holding_the_job(replay, vloop,
                                                        monkeypatch):
    out = _ghost_end(replay, vloop, monkeypatch, then_launch="breal1")
    assert len(out["live"]) == 1


def _ghost_wakeup(r, vloop, monkeypatch) -> dict:
    """An idle session gets a wake-up for a shell it never launched."""
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.stop("nothing running")
        await asyncio.sleep(10)
        r.wake(r.notification("bghost1", "failed", SUMMARY["failed"]))
        await asyncio.sleep(2)
        pin_mid = _pin(r)
        r.tool("read the output")
        await asyncio.sleep(2)
        r.stop("that was not mine")
        await asyncio.sleep(20)
        w.cancel()
        return {"pin_mid": pin_mid, "pin": _pin(r)}

    return _run(vloop, scenario())


def test_bb_ghost_wakeup_creates_no_row(replay, vloop, monkeypatch):
    _ghost_wakeup(replay, vloop, monkeypatch)
    assert "shell:" not in _all_card_texts(replay)


def test_bb_ghost_wakeup_is_not_counted(replay, vloop, monkeypatch):
    out = _ghost_wakeup(replay, vloop, monkeypatch)
    assert "shell" not in out["pin_mid"]


def test_bb_ghost_wakeup_pin_ends_idle(replay, vloop, monkeypatch):
    out = _ghost_wakeup(replay, vloop, monkeypatch)
    assert out["pin"] == IDLE


# ── 3. the same id launched long after its remembered end ─────────────────

def _relaunch_long_after(r, vloop, monkeypatch, *, source: str) -> dict:
    """Turn 1 sees an end for ``bshell1`` (from the transcript, or as an
    idle wake-up) that no launch claims. An hour later turn 2 launches a
    shell with the same id and stops while it runs."""
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        note = r.notification("bshell1", "completed", SUMMARY["completed"])
        if source == "transcript":
            r.enqueue(note)
            await asyncio.sleep(10)
            r.stop("one")
        else:
            r.stop("one")
            await asyncio.sleep(10)
            r.wake(note)
            await asyncio.sleep(2)
            r.stop("woken")
        await asyncio.sleep(10)
        await asyncio.sleep(LONG_AFTER)
        await _turn(r, 2, "run them again")
        r.bg_bash(DESC, "bshell1")
        await asyncio.sleep(5)
        r.stop("started it")
        await asyncio.sleep(10)
        out = {"pin": _pin(r), "card": _card(r),
               "live": list(r.chat.live_cards())}
        w.cancel()
        return out

    return _run(vloop, scenario())


SOURCES = ["transcript", "wakeup"]


@pytest.mark.parametrize("source", SOURCES)
def test_bb_relaunch_long_after_shows_a_running_row(replay, vloop,
                                                    monkeypatch, source):
    out = _relaunch_long_after(replay, vloop, monkeypatch, source=source)
    assert f"⏳ `shell: {DESC} (" in out["card"]


@pytest.mark.parametrize("source", SOURCES)
def test_bb_relaunch_long_after_is_counted_in_the_pin(replay, vloop,
                                                      monkeypatch, source):
    out = _relaunch_long_after(replay, vloop, monkeypatch, source=source)
    assert out["pin"] == "💤 aipager_boss (idle, 1 shell running)"


@pytest.mark.parametrize("source", SOURCES)
def test_bb_relaunch_long_after_keeps_the_job_waiting(replay, vloop,
                                                      monkeypatch, source):
    out = _relaunch_long_after(replay, vloop, monkeypatch, source=source)
    assert "1 shell still working" in out["card"]


@pytest.mark.parametrize("source", SOURCES)
def test_bb_relaunch_long_after_keeps_the_stop_button(replay, vloop,
                                                      monkeypatch, source):
    out = _relaunch_long_after(replay, vloop, monkeypatch, source=source)
    assert len(out["live"]) == 1


@pytest.mark.parametrize("source", SOURCES)
def test_bb_relaunch_long_after_answer_names_it_running(replay, vloop,
                                                        monkeypatch, source):
    _relaunch_long_after(replay, vloop, monkeypatch, source=source)
    assert _answer(replay, "started it").rstrip().endswith(
        f"⏳ 1 shell still running ({DESC}) - results will follow here")


# ── 4. human prompts are never held behind a held turn ────────────────────

def _typed_while_answer_held(r, vloop, monkeypatch) -> dict:
    """Turn 1 launches a shell and stops; Telegram holds its answer. The
    operator types a prompt at the terminal while that answer is out."""
    _setup(monkeypatch, r)
    _hold_first_answer(monkeypatch, HELD)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.bg_bash(DESC, "bshell1")
        r.tool("look around")
        await asyncio.sleep(3)
        r.stop("answer one")
        await asyncio.sleep(2)
        before = _pin(r)
        r.hook(hook_event_name="UserPromptSubmit",
               prompt="typed at the terminal")
        await asyncio.sleep(0.5)
        out = {"before": before, "at_once": _pin(r)}
        w.cancel()
        return out

    return _run(vloop, scenario())


def test_bb_typed_prompt_scenario_starts_idle(replay, vloop, monkeypatch):
    """Control: before the typed prompt the pin reads idle, so the next
    test's flip to working is the prompt's doing."""
    out = _typed_while_answer_held(replay, vloop, monkeypatch)
    assert out["before"].startswith(IDLE[:-1])


def test_bb_typed_prompt_while_answer_held_is_handled_at_once(replay, vloop,
                                                              monkeypatch):
    """Half a second after the typed prompt (the held answer still has 25
    seconds to go) the pin already reads working."""
    out = _typed_while_answer_held(replay, vloop, monkeypatch)
    assert out["at_once"].startswith(WORKING)


def _telegram_prompt_during_held_shell_turn(r, vloop, monkeypatch) -> None:
    """Turn 1's answer is held. Turn 2 launches a shell and stops (held).
    The operator sends message 3 while turn 2 is held; it runs a tool and
    stops. Later the shell ends and wakes Claude."""
    _setup(monkeypatch, r)
    _hold_first_answer(monkeypatch, HELD)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.tool("look around")
        await asyncio.sleep(3)
        r.stop("answer one")
        await asyncio.sleep(2)
        await _turn(r, 2, "now run the tests")
        r.bg_bash(DESC, "bshell1")
        await asyncio.sleep(3)
        r.stop("started it")
        await asyncio.sleep(1)
        await _turn(r, 3, "what else")
        r.tool("other work")
        await asyncio.sleep(2)
        r.stop("other done")
        await asyncio.sleep(HELD + 10)
        r.wake(r.notification("bshell1", "completed", SUMMARY["completed"]))
        await asyncio.sleep(2)
        r.tool("read the output")
        await asyncio.sleep(2)
        r.stop("the tests ended")
        await asyncio.sleep(30)
        w.cancel()

    _run(vloop, scenario())


def test_bb_telegram_prompt_during_hold_answer_replies_to_it(replay, vloop,
                                                            monkeypatch):
    """The human prompt is a new job, not a continuation of turn 2's."""
    _telegram_prompt_during_held_shell_turn(replay, vloop, monkeypatch)
    assert replay.chat.answers[_answer_saying(replay, "other done")] == 3


def test_bb_telegram_prompt_during_hold_is_taken(replay, vloop, monkeypatch):
    _telegram_prompt_during_held_shell_turn(replay, vloop, monkeypatch)
    assert replay.chat.reactions[3][-1] == "👍"


def test_bb_telegram_prompt_during_hold_leaves_no_live_card(replay, vloop,
                                                            monkeypatch):
    _telegram_prompt_during_held_shell_turn(replay, vloop, monkeypatch)
    assert replay.chat.live_cards() == []


def test_bb_telegram_prompt_during_hold_pin_ends_idle(replay, vloop,
                                                      monkeypatch):
    _telegram_prompt_during_held_shell_turn(replay, vloop, monkeypatch)
    assert _pin(replay) == IDLE


def test_bb_telegram_prompt_during_hold_shell_row_ends_done(replay, vloop,
                                                            monkeypatch):
    _telegram_prompt_during_held_shell_turn(replay, vloop, monkeypatch)
    assert ROW["completed"] in _card(replay)


# ── 5. an agent's wake-up under a held finish ─────────────────────────────

def _agent_wakeup_under_held_finish(r, vloop, monkeypatch) -> dict:
    """Turn 1's answer is held. Turn 2 starts a background agent, runs a
    tool and stops; the agent stops and its wake-up runs and stops."""
    _setup(monkeypatch, r)
    _hold_first_answer(monkeypatch, HELD)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.tool("look around")
        await asyncio.sleep(3)
        r.stop("answer one")
        await asyncio.sleep(2)
        await _turn(r, 2, "review it")
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="pipeline-runner")
        r.tool("step two")
        await asyncio.sleep(3)
        r.stop("reviewer started")
        await asyncio.sleep(1)
        r.hook(hook_event_name="SubagentStop", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        r.wake(r.notification("a1", "completed",
                              'Agent "pipeline-runner" completed',
                              result="looks fine"))
        await asyncio.sleep(1)
        r.tool("read the review")
        await asyncio.sleep(2)
        r.stop("review done")
        await asyncio.sleep(HELD + 30)
        w.cancel()
        return {"pin": _pin(r)}

    return _run(vloop, scenario())


def test_bb_agent_wakeup_under_held_finish_reuses_the_job_card(
        replay, vloop, monkeypatch):
    _agent_wakeup_under_held_finish(replay, vloop, monkeypatch)
    assert "Bash: read the review" in _card_for(replay, 2)


def test_bb_agent_wakeup_under_held_finish_opens_no_extra_card(
        replay, vloop, monkeypatch):
    _agent_wakeup_under_held_finish(replay, vloop, monkeypatch)
    assert len(replay.chat.cards) == 2


def test_bb_agent_wakeup_under_held_finish_answer_replies_to_the_job(
        replay, vloop, monkeypatch):
    _agent_wakeup_under_held_finish(replay, vloop, monkeypatch)
    assert replay.chat.answers[_answer_saying(replay, "review done")] == 2


def test_bb_agent_wakeup_under_held_finish_no_bare_finished_header(
        replay, vloop, monkeypatch):
    _agent_wakeup_under_held_finish(replay, vloop, monkeypatch)
    assert "· Finished (" not in _answer(replay, "review done")


def test_bb_agent_wakeup_under_held_finish_leaves_no_live_card(
        replay, vloop, monkeypatch):
    _agent_wakeup_under_held_finish(replay, vloop, monkeypatch)
    assert replay.chat.live_cards() == []


def test_bb_agent_wakeup_under_held_finish_pin_ends_idle(replay, vloop,
                                                         monkeypatch):
    out = _agent_wakeup_under_held_finish(replay, vloop, monkeypatch)
    assert out["pin"] == IDLE


def test_bb_agent_wakeup_under_held_finish_card_reads_done(replay, vloop,
                                                           monkeypatch):
    _agent_wakeup_under_held_finish(replay, vloop, monkeypatch)
    assert "· Done ·" in _card_for(replay, 2)
