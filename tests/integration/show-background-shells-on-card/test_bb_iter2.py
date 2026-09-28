"""Black-box, iteration 2: the three behaviours review-1 reported.

1. A shell's wake-up arriving while a Stop's finish still waits on an
   earlier finish that Telegram is holding: the wake-up's work lands on a
   card, no answer goes out card-less, and the earlier card settles.
2. An agent and a shell in one job, the agent swept by the silence window:
   the card keeps waiting on the shell, and the shell's wake-up continues
   the same card.
3. Two shells with the same description own one row each; a foreground
   Bash with a running shell's summary settles its own row, never the
   shell's.

Written from design.md, entrypoints.md and review-1.md's observable
symptoms only. The one thing mocked beyond the harness is Telegram's
latency: a ``sendRichMessage`` that takes a while to answer, the way a
flood-held answer does.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

import aipager.bot.rich_message as rm
from aipager import preferences as prefs
from aipager.session_monitor import SessionMonitor

CHAT = 256113222
DESC = "run the tests"
DONE = f'Background command "{DESC}" completed (exit code 0)'
IDLE = "💤 aipager_boss (idle)"
#: A silence well past the agents' 30-minute window.
SILENT = 31 * 60


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


def _answers(r) -> list[str]:
    return [r.chat.answer_texts[m][-1] for m in sorted(r.chat.answer_texts)]


def _answer_saying(r, words: str) -> int:
    hits = [m for m in sorted(r.chat.answer_texts)
            if words in r.chat.answer_texts[m][-1]]
    assert hits, f"no answer says {words!r}"
    return hits[-1]


def _all_card_texts(r) -> str:
    return "\n".join(t for c in r.chat.cards.values()
                     for t in c.get("texts", [c["text"]]))


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


# ── 1a. the earlier turn's answer is held while the shell's turn runs ──────

def _held_earlier_turn(r, vloop, monkeypatch, hold: float = 30.0) -> dict:
    """Turn 1 ends and its answer is held. Turn 2 starts, launches a
    shell and stops; the shell ends and wakes Claude one second later,
    while turn 2's finish still waits on turn 1's."""
    _setup(monkeypatch, r)
    _hold_first_answer(monkeypatch, hold)

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
        r.wake(r.notification("bshell1", "completed", DONE))
        await asyncio.sleep(2)
        r.tool("read the output")
        await asyncio.sleep(2)
        r.stop("the tests passed")
        await asyncio.sleep(hold + 30)
        w.cancel()
        return {"pin": _pin(r)}

    return _run(vloop, scenario())


@pytest.mark.parametrize("hold", [8.0, 30.0])
def test_bb_held_turn_wakeup_ends_the_shell_in_the_pin(replay, vloop,
                                                       monkeypatch, hold):
    """The wake-up carries the shell's completed status: once every turn
    is over the pin counts no shell."""
    out = _held_earlier_turn(replay, vloop, monkeypatch, hold)
    assert out["pin"] == IDLE


@pytest.mark.parametrize("hold", [8.0, 30.0])
def test_bb_held_turn_leaves_no_live_card(replay, vloop, monkeypatch, hold):
    """Nothing runs at the end, so no card may keep its Stop button."""
    _held_earlier_turn(replay, vloop, monkeypatch, hold)
    assert replay.chat.live_cards() == []


def test_bb_held_turn_final_answer_claims_no_running_shell(replay, vloop,
                                                           monkeypatch):
    _held_earlier_turn(replay, vloop, monkeypatch)
    text = replay.chat.answer_texts[
        _answer_saying(replay, "the tests passed")][-1]
    assert "still running" not in text


def test_bb_held_turn_earlier_card_settles(replay, vloop, monkeypatch):
    _held_earlier_turn(replay, vloop, monkeypatch)
    first = [m for m, c in replay.chat.cards.items() if c["reply_to"] == 1]
    assert first and all(not replay.chat.cards[m]["stop"] for m in first)


def test_bb_held_turn_wakeup_work_is_on_a_card(replay, vloop, monkeypatch):
    _held_earlier_turn(replay, vloop, monkeypatch)
    assert "Bash: read the output" in _all_card_texts(replay)


def test_bb_held_turn_every_answer_has_a_reply_target(replay, vloop,
                                                      monkeypatch):
    _held_earlier_turn(replay, vloop, monkeypatch)
    assert None not in replay.chat.answers.values()


def test_bb_held_turn_no_bare_finished_header(replay, vloop, monkeypatch):
    _held_earlier_turn(replay, vloop, monkeypatch)
    assert all("· Finished (" not in a for a in _answers(replay))


# ── 1b. the job's interim answer is held; a continuation's Stop waits ─────

def _held_interim(r, vloop, monkeypatch) -> None:
    """Two shells, the interim answer held. The first shell's wake-up
    runs and stops; while that Stop's finish waits on the held answer,
    the second shell ends and wakes Claude."""
    _setup(monkeypatch, r)
    _hold_first_answer(monkeypatch, 30.0)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.bg_bash("first job", "bone1")
        r.bg_bash("second job", "btwo2")
        await asyncio.sleep(5)
        r.stop("started both")
        await asyncio.sleep(2)
        r.wake(r.notification(
            "bone1", "completed",
            'Background command "first job" completed (exit code 0)'))
        await asyncio.sleep(1)
        r.tool("read one")
        await asyncio.sleep(1)
        r.stop("one done")
        await asyncio.sleep(1)
        r.wake(r.notification(
            "btwo2", "completed",
            'Background command "second job" completed (exit code 0)'))
        await asyncio.sleep(1)
        r.tool("read two")
        await asyncio.sleep(1)
        r.stop("both done")
        await asyncio.sleep(60)
        w.cancel()

    _run(vloop, scenario())


def test_bb_held_interim_leaves_no_live_card(replay, vloop, monkeypatch):
    _held_interim(replay, vloop, monkeypatch)
    assert replay.chat.live_cards() == []


def test_bb_held_interim_every_answer_has_a_reply_target(replay, vloop,
                                                         monkeypatch):
    _held_interim(replay, vloop, monkeypatch)
    assert None not in replay.chat.answers.values()


def test_bb_held_interim_second_wakeup_work_is_on_a_card(replay, vloop,
                                                         monkeypatch):
    _held_interim(replay, vloop, monkeypatch)
    assert "Bash: read two" in _all_card_texts(replay)


def test_bb_held_interim_pin_ends_idle(replay, vloop, monkeypatch):
    _held_interim(replay, vloop, monkeypatch)
    assert _pin(replay) == IDLE


def test_bb_held_interim_no_bare_finished_header(replay, vloop, monkeypatch):
    """The second wake-up's answer must not go out as a card-less
    ``· Finished (Ns)`` answer."""
    _held_interim(replay, vloop, monkeypatch)
    text = replay.chat.answer_texts[_answer_saying(replay, "both done")][-1]
    assert "· Finished (" not in text


# ── 2. an agent swept for silence while the job's shell still runs ────────

def _agent_swept(r, vloop, monkeypatch, *, wake: bool) -> dict:
    _setup(monkeypatch, r)

    async def scenario():
        w = _worker(r)
        await _turn(r)
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        r.bg_bash(DESC, "bshell1")
        await asyncio.sleep(5)
        r.stop("started it")
        await asyncio.sleep(10)
        mon = SessionMonitor(r.bot.registry, r.bot.notify)
        await mon._scan()
        await asyncio.sleep(SILENT)
        await mon._scan()
        await asyncio.sleep(10)
        out = {"pin": _pin(r), "live": list(r.chat.live_cards()),
               "cards": len(r.chat.cards), "card": _card(r),
               "plain": list(r.chat.plain)}
        if wake:
            r.wake(r.notification("bshell1", "completed", DONE))
            await asyncio.sleep(2)
            r.tool("read the output")
            await asyncio.sleep(2)
            r.stop("the tests passed")
            await asyncio.sleep(20)
        w.cancel()
        return out

    return _run(vloop, scenario())


def test_bb_swept_agent_card_keeps_its_stop_button(replay, vloop,
                                                   monkeypatch):
    out = _agent_swept(replay, vloop, monkeypatch, wake=False)
    assert len(out["live"]) == 1


def test_bb_swept_agent_card_not_finished_as_agent_lost(replay, vloop,
                                                        monkeypatch):
    out = _agent_swept(replay, vloop, monkeypatch, wake=False)
    assert "background agent lost" not in out["card"]


def test_bb_swept_agent_card_still_waits_on_the_shell(replay, vloop,
                                                      monkeypatch):
    out = _agent_swept(replay, vloop, monkeypatch, wake=False)
    assert "1 shell still working" in out["card"]


def test_bb_swept_agent_pin_still_counts_the_shell(replay, vloop,
                                                   monkeypatch):
    out = _agent_swept(replay, vloop, monkeypatch, wake=False)
    assert out["pin"] == "💤 aipager_boss (idle, 1 shell running)"


def test_bb_swept_agent_sends_no_notice(replay, vloop, monkeypatch):
    out = _agent_swept(replay, vloop, monkeypatch, wake=False)
    assert out["plain"] == []


def test_bb_swept_agent_shell_wakeup_reuses_the_card(replay, vloop,
                                                     monkeypatch):
    _agent_swept(replay, vloop, monkeypatch, wake=True)
    assert len(replay.chat.cards) == 1


def test_bb_swept_agent_shell_wakeup_settles_the_card(replay, vloop,
                                                      monkeypatch):
    _agent_swept(replay, vloop, monkeypatch, wake=True)
    assert replay.chat.live_cards() == []


def test_bb_swept_agent_shell_wakeup_card_reads_done(replay, vloop,
                                                     monkeypatch):
    _agent_swept(replay, vloop, monkeypatch, wake=True)
    assert f"✅ `shell: {DESC} - done (" in _card(replay)


def test_bb_swept_agent_wakeup_answer_has_a_reply_target(replay, vloop,
                                                         monkeypatch):
    _agent_swept(replay, vloop, monkeypatch, wake=True)
    mid = _answer_saying(replay, "the tests passed")
    assert replay.chat.answers[mid] is not None


def test_bb_swept_agent_wakeup_answer_has_no_finished_header(replay, vloop,
                                                             monkeypatch):
    _agent_swept(replay, vloop, monkeypatch, wake=True)
    text = replay.chat.answer_texts[
        _answer_saying(replay, "the tests passed")][-1]
    assert "· Finished (" not in text


# ── 3. rows bound one per shell, never taken by a foreground Bash ─────────

def _rows(r, vloop, body) -> dict:
    """Run ``body`` inside one turn; it returns snapshots of the card."""
    prefs.set_preference(CHAT, "layout", "card")

    async def scenario():
        w = _worker(r)
        await _turn(r)
        snaps = await body(r)
        w.cancel()
        return snaps

    return _run(vloop, scenario())


async def _twins(r) -> dict:
    snaps = {}
    r.bg_bash(DESC, "btwin1")
    r.bg_bash(DESC, "btwin2")
    await asyncio.sleep(20)
    snaps["both"] = _card(r)
    r.enqueue(r.notification("btwin1", "completed", DONE))
    await asyncio.sleep(15)
    snaps["one"] = _card(r)
    r.enqueue(r.notification(
        "btwin2", "failed",
        f'Background command "{DESC}" failed with exit code 2'))
    await asyncio.sleep(15)
    snaps["two"] = _card(r)
    return snaps


def test_bb_twin_shells_show_two_running_rows(replay, vloop):
    snaps = _rows(replay, vloop, _twins)
    assert snaps["both"].count(f"⏳ `shell: {DESC} (") == 2


def test_bb_twin_shells_leave_no_plain_pending_bash_row(replay, vloop):
    snaps = _rows(replay, vloop, _twins)
    assert "⏳ `Bash:" not in snaps["both"]


def test_bb_twin_first_end_settles_one_row_done(replay, vloop):
    snaps = _rows(replay, vloop, _twins)
    assert snaps["one"].count(f"✅ `shell: {DESC} - done (") == 1


def test_bb_twin_first_end_leaves_the_other_running(replay, vloop):
    snaps = _rows(replay, vloop, _twins)
    assert snaps["one"].count(f"⏳ `shell: {DESC} (") == 1


def test_bb_twin_second_end_keeps_the_first_done_row(replay, vloop):
    snaps = _rows(replay, vloop, _twins)
    assert snaps["two"].count(f"✅ `shell: {DESC} - done (") == 1


def test_bb_twin_second_end_settles_its_own_row_failed(replay, vloop):
    snaps = _rows(replay, vloop, _twins)
    assert snaps["two"].count(f"❌ `shell: {DESC} - failed (exit 2)`") == 1


def test_bb_twin_second_end_leaves_no_running_row(replay, vloop):
    snaps = _rows(replay, vloop, _twins)
    assert f"⏳ `shell: {DESC}" not in snaps["two"]


_SAME = {"command": "npm test", "description": DESC}
_FG_RESPONSE = {"stdout": "ok", "stderr": "", "interrupted": False,
                "isImage": False, "noOutputExpected": False}


async def _foreground_twin(r) -> dict:
    """A running shell, then a foreground Bash with the exact same
    input (so the same summary), then the shell's own end."""
    snaps = {}
    r.bg_bash(DESC, "bshell1", command="npm test")
    await asyncio.sleep(10)
    r.hook(hook_event_name="PreToolUse", tool_name="Bash", tool_input=_SAME)
    await asyncio.sleep(3)
    r.hook(hook_event_name="PostToolUse", tool_name="Bash",
           tool_input=_SAME, tool_response=_FG_RESPONSE)
    await asyncio.sleep(15)
    snaps["fg_done"] = _card(r)
    r.enqueue(r.notification("bshell1", "completed", DONE))
    await asyncio.sleep(15)
    snaps["shell_done"] = _card(r)
    return snaps


def test_bb_foreground_twin_leaves_the_shell_row_running(replay, vloop):
    snaps = _rows(replay, vloop, _foreground_twin)
    assert f"⏳ `shell: {DESC} (" in snaps["fg_done"]


def test_bb_foreground_twin_never_marks_the_shell_done(replay, vloop):
    snaps = _rows(replay, vloop, _foreground_twin)
    assert "✅ `shell:" not in snaps["fg_done"]


def test_bb_foreground_twin_settles_its_own_row(replay, vloop):
    snaps = _rows(replay, vloop, _foreground_twin)
    assert f"✅ `Bash: {DESC}`" in snaps["fg_done"]


def test_bb_foreground_twin_leaves_no_pending_bash_row(replay, vloop):
    snaps = _rows(replay, vloop, _foreground_twin)
    assert "⏳ `Bash:" not in snaps["fg_done"]


def test_bb_foreground_twin_shell_end_settles_the_shell_row(replay, vloop):
    snaps = _rows(replay, vloop, _foreground_twin)
    assert f"✅ `shell: {DESC} - done (" in snaps["shell_done"]


def test_bb_foreground_twin_row_stays_done_after_the_shell_ends(replay,
                                                                vloop):
    snaps = _rows(replay, vloop, _foreground_twin)
    assert f"✅ `Bash: {DESC}`" in snaps["shell_done"]


async def _orphan_post(r) -> dict:
    """A foreground PostToolUse whose PreToolUse never arrived (no row
    matches its summary) while a shell runs: it must not take the
    shell's row."""
    r.bg_bash(DESC, "bshell1")
    await asyncio.sleep(10)
    r.hook(hook_event_name="PostToolUse", tool_name="Bash",
           tool_input={"command": "ls -la"}, tool_response=_FG_RESPONSE)
    await asyncio.sleep(15)
    return {"card": _card(r)}


def test_bb_orphan_post_leaves_the_shell_row_running(replay, vloop):
    snaps = _rows(replay, vloop, _orphan_post)
    assert f"⏳ `shell: {DESC} (" in snaps["card"]
