"""One live busy card per turn, never orphaned (roadmap 8.55/8.56/8.57/8.58).

Each row drives the real daemon on the virtual loop (see conftest.py):
Telegram messages through ``_handle_message``, hooks through the hook
receiver, the real limiter with the chat's ban history, and a Telegram that
remembers every card's Stop button.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from aipager import preferences as prefs
from aipager.bot import lifecycle as lifecycle_mod
from aipager.session_monitor import ORPHAN_CARD_GRACE_SECONDS, orphan_card_due
from aipager.state import SessionRegistry, Status

CHAT = 256113222


def _layout(layout: str = "card") -> None:
    prefs.set_preference(CHAT, "layout", layout)


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _drain(r, seconds: float = 30.0) -> None:
    await asyncio.sleep(seconds)
    await asyncio.gather(*r.tasks, return_exceptions=True)


async def _turn(r, mid: int, text: str, *, tools: int = 1) -> None:
    """A Telegram prompt reaching an idle Claude, taken as a new turn."""
    r.say(mid, text)
    await r.updates.join()
    r.prompt_hooks(mid, text)
    await asyncio.sleep(1)
    for i in range(tools):
        r.tool(f"{text} step {i}")
        await asyncio.sleep(1)


def _worker(r):
    return asyncio.ensure_future(r._updates())


# ── R1: two start paths for one turn ────────────────────────────────────────

def test_popped_turn_gets_one_card_though_three_paths_ask(replay, vloop,
                                                          monkeypatch):
    """Regression guard (passes on main too): a message queued mid-turn is
    popped at the Stop; the popped turn's first tool call lands while the
    finish is still out, and a THIRD message's handler asks for a card too.
    One card, under the popped message. The main-failing reproductions of
    the incident are ``test_prompt_after_a_detached_agent_tool_is_taken_not_
    queued`` and the replay."""
    _layout()
    r = replay
    slow = r.bot._mark_ran_commands

    async def _slow_mark(sess):
        await asyncio.sleep(6)  # the finish stays out, like a flood wait
        await slow(sess)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first")
        r.say(3, "queued one")
        await r.updates.join()
        r.prompt_hooks(3, "queued one")
        await asyncio.sleep(1)
        monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow_mark)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.tool("the popped turn's first tool")
        await asyncio.sleep(0.5)
        # Typed while the popped turn runs and the finish is still out:
        # its handler asks for the running turn's card too.
        r.say(5, "while it runs")
        await r.updates.join()
        r.prompt_hooks(5, "while it runs")
        await asyncio.sleep(20)
        live_mid_turn = list(r.chat.live_cards())
        monkeypatch.setattr(r.bot, "_mark_ran_commands", slow)
        r.stop("answer queued one")
        await asyncio.sleep(5)
        r.stop("answer while it runs")
        await _drain(r)
        w.cancel()
        return live_mid_turn

    live_mid_turn = _run(vloop, scenario())
    chat = r.chat
    assert chat.card_sends_for(3) == 1
    assert len(live_mid_turn) == 1
    assert chat.cards[live_mid_turn[0]]["reply_to"] == 3
    assert chat.card_sends_for(5) == 1  # its own turn, popped at the Stop
    assert chat.live_cards() == []


def test_two_requests_for_one_turn_send_one_card(replay, vloop):
    """Regression guard: two turn-card requests for the same turn, the
    second arriving while the first's send waits on the flood gate: one
    card."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.trigger_msg_id = 11
        await asyncio.gather(r.bot._send_busy_and_animate(sess),
                             r.bot._send_busy_and_animate(sess))
        await r.bot._send_busy_and_animate(sess)

    _run(vloop, scenario())
    assert r.chat.card_sends_for(11) == 1
    assert len(r.chat.cards) == 1


def test_same_turn_request_moves_the_card_when_the_target_moved(replay, vloop):
    """A second request for the SAME turn after its reply target moved (the
    queued pick-up) re-anchors the card: one live card, under the new
    target."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.trigger_msg_id = 11
        await r.bot._send_busy_and_animate(sess)
        sess.trigger_msg_id = 13
        await r.bot._send_busy_and_animate(sess)

    _run(vloop, scenario())
    assert r.chat.card_sends_for(13) == 1
    assert len(r.chat.live_cards()) == 1
    assert r.chat.cards[r.chat.live_cards()[0]]["reply_to"] == 13


def test_request_for_a_superseded_turn_sends_nothing(replay, vloop):
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        old_turn = sess.turn_seq
        sess.status = Status.IDLE
        r.bot.registry.transition(sess.name, Status.BUSY)
        assert sess.turn_seq == old_turn + 1
        await r.bot._send_busy_and_animate(sess, turn=old_turn)

    _run(vloop, scenario())
    assert r.chat.cards == {}


# ── R2: no orphan ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("layout", ["card", "replace", "merged"])
def test_card_send_racing_the_stop_is_settled_per_layout(replay, vloop,
                                                         layout):
    """Regression guard (main's card lock already covers it): the card's
    send is still waiting on the flood gate when the turn's Stop arrives.
    The finish waits for it and settles it as the layout says:
    ``card`` keeps it (Stop off), ``replace`` deletes it, ``merged`` folds
    the answer into it."""
    _layout(layout)
    r = replay
    real_send_busy = r.bot.send_busy

    async def _slow_send_busy(sess, **kw):
        await asyncio.sleep(5)
        return await real_send_busy(sess, **kw)

    r.bot.send_busy = _slow_send_busy

    async def scenario():
        w = _worker(r)
        r.say(1, "quick one")
        await asyncio.sleep(1)
        r.prompt_hooks(1, "quick one")
        await asyncio.sleep(1)
        r.stop("quick answer")
        await r.updates.join()
        await _drain(r)
        w.cancel()

    _run(vloop, scenario())
    (card,) = r.chat.cards.values()
    assert r.chat.live_cards() == []
    if layout == "replace":
        assert card["deleted"]
    else:
        assert not card["deleted"] and card["stop"] is False
    assert r.sess.busy_msg_id is None


def test_card_request_after_its_turn_finished_sends_nothing(replay, vloop):
    """A request for a turn whose finish already ran (it waited on the lock
    or on a flood gate that long) opens no card for it."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first")
        turn = sess.turn_seq
        r.stop("answer first")
        await _drain(r, 10)
        cards_before = len(r.chat.cards)
        sess.status = Status.BUSY  # a stale status, as a racing hook leaves
        await r.bot._send_busy_and_animate(sess, turn=turn)
        w.cancel()
        return cards_before

    cards_before = _run(vloop, scenario())
    assert len(r.chat.cards) == cards_before
    assert r.chat.live_cards() == []


def test_new_turn_waits_for_the_previous_finish(replay, vloop, monkeypatch):
    """A prompt that starts the next turn while the previous turn's finish is
    still out: the finish settles ITS card and answers under ITS message;
    the new turn gets its own live card under the new message."""
    _layout()
    r = replay
    slow = r.bot._mark_ran_commands

    async def _slow_mark(sess):
        await asyncio.sleep(6)
        await slow(sess)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first")
        monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow_mark)
        r.stop("answer first")
        await asyncio.sleep(1)
        r.say(7, "next")
        await r.updates.join()
        r.prompt_hooks(7, "next")
        await asyncio.sleep(20)
        live = list(r.chat.live_cards())
        w.cancel()
        return live

    live = _run(vloop, scenario())
    first = [m for m, c in r.chat.cards.items() if c["reply_to"] == 1]
    assert len(first) == 1 and r.chat.cards[first[0]]["stop"] is False
    assert 1 in r.chat.answers.values()
    assert 7 not in r.chat.answers.values()
    assert len(live) == 1 and r.chat.cards[live[0]]["reply_to"] == 7
    assert r.sess.trigger_msg_id == 7


def test_stale_card_of_an_earlier_turn_is_settled_not_forgotten(replay,
                                                                vloop):
    """A card this process sent for an earlier turn, its animation dead: the
    next turn's card start settles it (Stop off) before forgetting it."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.trigger_msg_id = 1
        await r.bot._send_busy_and_animate(sess)
        r.bot._stop_animation(sess)
        sess.job_reclaim_pending = False  # nothing else marks it new
        sess.turn_seq += 1
        sess.trigger_msg_id = 3
        await r.bot._send_busy_and_animate(sess)

    _run(vloop, scenario())
    old = [c for c in r.chat.cards.values() if c["reply_to"] == 1]
    assert old and old[0]["stop"] is False
    assert len(r.chat.live_cards()) == 1


# ── the orphan sweep ────────────────────────────────────────────────────────

def _orphan(r, vloop, layout):
    """A live card whose turn has ended and that nothing owns."""
    _layout(layout)
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.trigger_msg_id = 1
        await r.bot._send_busy_and_animate(sess)
        r.bot._stop_animation(sess)
        sess.status = Status.IDLE

    _run(vloop, scenario())
    return sess


@pytest.mark.parametrize("layout", ["card", "replace", "merged"])
def test_orphan_sweep_settles_the_card_per_layout(replay, vloop, layout):
    r = replay
    sess = _orphan(r, vloop, layout)
    now = vloop.time()
    assert orphan_card_due(sess, now) is False  # first sighting
    assert orphan_card_due(sess, now + ORPHAN_CARD_GRACE_SECONDS - 1) is False
    assert orphan_card_due(sess, now + ORPHAN_CARD_GRACE_SECONDS) is True
    _run(vloop, r.bot.notify(sess, "orphan_card", {}))
    (card,) = r.chat.cards.values()
    assert r.chat.live_cards() == []
    assert card["deleted"] is (layout != "card")
    assert sess.busy_msg_id is None


def test_orphan_wait_restarts_when_the_card_is_owned_again(replay, vloop):
    r = replay
    sess = _orphan(r, vloop, "card")
    now = vloop.time()
    assert orphan_card_due(sess, now) is False
    sess.status = Status.BUSY
    assert orphan_card_due(sess, now + 10) is False
    sess.status = Status.IDLE
    assert orphan_card_due(sess, now + ORPHAN_CARD_GRACE_SECONDS + 1) is False
    assert orphan_card_due(sess, now + 2 * ORPHAN_CARD_GRACE_SECONDS + 2) is True


@pytest.mark.parametrize("owner", [
    "busy", "interactive", "job", "notify", "gate", "lock", "animation",
    "adopted", "compacting"])
def test_owned_card_is_not_orphaned(replay, vloop, owner):
    r = replay
    sess = _orphan(r, vloop, "card")
    now = vloop.time()
    assert sess.card_orphaned(now)
    lock_held = None
    if owner == "busy":
        sess.status = Status.BUSY
    elif owner == "interactive":
        sess.status = Status.INTERACTIVE
    elif owner == "job":
        sess.active_subagents["a1"] = {"type": "x", "started_at": now}
    elif owner == "notify":
        sess.notify_in_flight = 1
    elif owner == "gate":
        sess.finish_gate = asyncio.Event()
    elif owner == "lock":
        lock_held = sess.animate_lock
        _run(vloop, lock_held.acquire())
    elif owner == "animation":
        sess.animate_task = vloop.create_task(asyncio.sleep(999))
    elif owner == "adopted":
        sess.card_adopt_until = now + 5
    elif owner == "compacting":
        sess.push_compacting(sess.busy_msg_id, now, None)
    assert not sess.card_orphaned(now)
    if lock_held is not None:
        lock_held.release()


# ── R3: restart ─────────────────────────────────────────────────────────────

def _transcript_tail(path, *, finished: bool) -> None:
    if finished:
        entry = {"type": "assistant", "message": {
            "id": "m1", "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "done"}]}}
    else:
        entry = {"type": "assistant", "message": {
            "id": "m1", "role": "assistant", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                         "input": {}}]}}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _restored_card(r, monkeypatch, *, finished: bool, alive: bool = True):
    """What a restart finds: a card the old process recorded as live."""
    sess = r.sess
    card = r.chat.new_id()
    r.chat.cards[card] = {"reply_to": 1, "stop": True, "deleted": False}
    sess.busy_msg_id = card
    sess.trigger_msg_id = 1
    sess.busy_card_trigger = 1
    sess.status = Status.IDLE
    _transcript_tail(r.transcript, finished=finished)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[sess.name] if alive else []))
    return sess, card


def test_restart_finalizes_a_recorded_card_of_an_idle_session(
        replay, vloop, monkeypatch):
    r = replay
    sess, card = _restored_card(r, monkeypatch, finished=True)
    _run(vloop, r.bot.recover_sessions())
    assert r.chat.cards[card]["stop"] is False
    assert "Daemon restarted" in r.chat.cards[card]["text"]
    assert sess.busy_msg_id is None


def test_restart_adopts_a_card_whose_turn_still_runs(replay, vloop,
                                                     monkeypatch):
    """Mid-turn at the restart: the card is kept, its turn's next hook
    resumes its animation, and that turn's Stop settles it."""
    _layout()
    r = replay
    sess, card = _restored_card(r, monkeypatch, finished=False)

    async def scenario():
        await r.bot.recover_sessions()
        adopted = (sess.busy_msg_id, r.chat.cards[card]["stop"],
                   sess.card_adopt_until > vloop.time())
        r.tool("the running turn's next tool")
        await asyncio.sleep(3)
        animating = sess.animation_running()
        r.stop("the answer")
        await _drain(r, 10)
        return adopted, animating

    adopted, animating = _run(vloop, scenario())
    assert adopted == (card, True, True)
    assert animating
    assert r.chat.live_cards() == []
    assert sess.busy_msg_id is None


def test_adopted_card_with_no_hook_is_settled_by_the_sweep(replay, vloop,
                                                           monkeypatch):
    r = replay
    sess, card = _restored_card(r, monkeypatch, finished=False)
    _run(vloop, r.bot.recover_sessions())
    now = vloop.time()
    assert sess.card_adopt_until == now + lifecycle_mod.CARD_ADOPT_SECONDS
    assert orphan_card_due(sess, now) is False  # adopted
    assert orphan_card_due(sess, now + ORPHAN_CARD_GRACE_SECONDS) is False
    sess.card_adopt_until = now - 1  # the window ran out, no hook came
    assert orphan_card_due(sess, now) is False
    assert orphan_card_due(sess, now + ORPHAN_CARD_GRACE_SECONDS) is True
    _run(vloop, r.bot.notify(sess, "orphan_card", {}))
    assert r.chat.cards[card]["stop"] is False
    # This process has none of that turn's rows: the restart's own line.
    assert "Daemon restarted" in r.chat.cards[card]["text"]
    assert sess.busy_msg_id is None


def test_restart_deletes_cards_whose_delete_it_cut_off(replay, vloop,
                                                       monkeypatch):
    r = replay
    sess = r.sess
    card = r.chat.new_id()
    r.chat.cards[card] = {"reply_to": 1, "stop": True, "deleted": False}
    sess.pending_card_deletes = [card]
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[sess.name]))
    _run(vloop, r.bot.recover_sessions())
    assert r.chat.cards[card]["deleted"]
    assert sess.pending_card_deletes == []


def test_pending_card_deletes_survive_a_save_and_load(mk_bot):
    reg = SessionRegistry()
    sess = reg.get_or_create("claude-x")
    sess.pending_card_deletes = [41, 42]
    reg.save()
    again = SessionRegistry()
    again.load()
    assert again.get("claude-x").pending_card_deletes == [41, 42]


def test_deferred_delete_is_owed_until_it_lands(replay, vloop):
    r = replay
    sess = r.sess
    card = r.chat.new_id()
    r.chat.cards[card] = {"reply_to": 1, "stop": True, "deleted": False}

    async def scenario():
        r.bot._delete_card_later(sess, card)
        owed = list(sess.pending_card_deletes)
        await asyncio.sleep(5)
        return owed

    owed = _run(vloop, scenario())
    assert owed == [card]
    assert r.chat.cards[card]["deleted"]
    assert sess.pending_card_deletes == []


# ── R4: the job's card follows the newest message ──────────────────────────

async def _job_open(r):
    """A turn that launched a background agent, ended: the job's card is up,
    waiting."""
    await _turn(r, 1, "launch it", tools=0)
    r.hook(hook_event_name="SubagentStart", agent_id="a1",
           agent_type="pipeline-runner")
    await asyncio.sleep(2)
    r.stop("launched")
    await asyncio.sleep(8)


def test_message_popped_inside_a_job_moves_the_job_card(replay, vloop):
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it", tools=0)
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        r.say(3, "queued in the job")
        await r.updates.join()
        r.prompt_hooks(3, "queued in the job")
        await asyncio.sleep(1)
        r.stop("launched")
        await asyncio.sleep(15)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.card_sends_for(3) == 1
    live = r.chat.live_cards()
    assert len(live) == 1 and r.chat.cards[live[0]]["reply_to"] == 3
    assert "a1" in sess.active_subagents
    assert sess.status == Status.BUSY
    assert 1 in r.chat.answers.values()  # the interim answer, under ITS prompt


def test_new_prompt_inside_a_job_moves_the_job_card(replay, vloop):
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _job_open(r)
        await _turn(r, 5, "and now this", tools=0)
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    live = r.chat.live_cards()
    assert len(live) == 1 and r.chat.cards[live[0]]["reply_to"] == 5
    assert all(c["deleted"] for c in r.chat.cards.values()
               if c["reply_to"] == 1)
    assert "a1" in sess.active_subagents


# ── 8.58: a detached background agent is not the parent's turn ────────────

def test_detached_agent_tool_neither_starts_a_turn_nor_adds_a_row(replay,
                                                                  vloop):
    r = replay
    sess = r.sess
    sess.status = Status.IDLE

    async def scenario():
        r.tool("agent writes a script", agent_id="bg1")
        await _drain(r, 2)

    _run(vloop, scenario())
    assert sess.status == Status.IDLE
    assert sess.tool_history == []
    assert r.chat.cards == {}


def test_detached_agent_tools_stay_off_the_running_turn(replay, vloop):
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        r.say(1, "work")
        await r.updates.join()
        r.prompt_hooks(1, "work")
        await asyncio.sleep(1)
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "parent step"})
        await asyncio.sleep(0.5)
        pending_before = sess.pending_tool_started_at
        r.tool("agent step", agent_id="bg1")
        await asyncio.sleep(0.5)
        w.cancel()
        return pending_before

    pending_before = _run(vloop, scenario())
    assert [row for row, _done in sess.tool_history] == ["Bash: parent step"]
    assert sess.tool_history[0][1] is False  # the agent's end settled nothing
    assert sess.pending_tool_started_at == pending_before


def test_prompt_after_a_detached_agent_tool_is_taken_not_queued(replay,
                                                                vloop):
    """Item 6, the exact shape (2026-09-27 09:26:42-09:26:46): Claude's turn
    Stops; a background agent's tool call arrives; "now I see two" reaches
    the idle Claude and runs as its own turn — no queue-operation line.
    It must turn 👍 and get the turn's card and answer."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _turn(r, 4834, "oh now I see one")
        r.stop("answer one")
        await asyncio.sleep(1)
        r.tool("agent step", agent_id="bg1")
        await asyncio.sleep(4)
        r.say(4836, "now I see two")
        await r.updates.join()
        r.prompt_hooks(4836, "now I see two")
        await asyncio.sleep(1)
        r.tool("parent step")
        await asyncio.sleep(1)
        r.stop("answer two")
        await _drain(r, 10)
        w.cancel()

    _run(vloop, scenario())
    assert r.chat.reactions[4836][-1] == "👍"
    assert sess.queued_targets == []
    assert r.chat.card_sends_for(4836) == 1
    assert 4836 in r.chat.answers.values()
    assert r.chat.live_cards() == []


# ── more of R1/R2's edges ───────────────────────────────────────────────────

def test_lost_card_is_sent_again_without_resetting_its_turn(replay, vloop):
    """The turn's card send failed; the next request for the SAME turn sends
    it again and keeps what the turn recorded meanwhile."""
    _layout()
    r = replay
    sess = r.sess
    real_send_busy = r.bot.send_busy
    calls = {"n": 0}

    async def _first_fails(s, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_send_busy(s, **kw)

    r.bot.send_busy = _first_fails

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.trigger_msg_id = 11
        await r.bot._send_busy_and_animate(sess)
        assert sess.busy_msg_id is None
        sess.record_tool("Bash: done meanwhile", True)
        await r.bot._send_busy_and_animate(sess)

    _run(vloop, scenario())
    assert r.chat.card_sends_for(11) == 1
    assert sess.tool_history == [("Bash: done meanwhile", True)]


def test_stale_request_does_not_earn_the_current_turns_deferred_card(
        replay, vloop):
    """A self-woken turn's card is deferred; a request made for an EARLIER
    turn must not be the thing that sends it."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        old_turn = sess.turn_seq
        sess.status = Status.IDLE
        r.bot.registry.transition(sess.name, Status.BUSY)
        await r.bot._send_busy_and_animate(sess, lazy=True)
        await r.bot._send_busy_and_animate(sess, turn=old_turn)
        pending = bool(sess.lazy_card_at)
        r.bot._cancel_lazy_card(sess)
        r.bot._stop_animation(sess)
        return pending

    assert _run(vloop, scenario()) is True
    assert r.chat.cards == {}


def test_absorbed_message_read_at_the_stop_answers_that_turn_only(
        replay, vloop, monkeypatch):
    """A message absorbed into turn N whose absorption line is first read
    at N's Stop, while the next prompt has already started turn N+1: N's
    answer goes under the absorbed message, and N+1 keeps its own target."""
    _layout()
    r = replay
    slow = r.bot._mark_ran_commands

    async def _slow_mark(sess):
        await asyncio.sleep(6)
        await slow(sess)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first")
        r.say(3, "fold this in")
        await r.updates.join()
        r.prompt_hooks(3, "fold this in")
        await asyncio.sleep(1)
        r.append({"type": "queue-operation", "operation": "remove",
                  "reason": "absorbed_mid_turn",
                  "content": "[via Telegram · @owner]\nfold this in"})
        monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow_mark)
        r.stop("answer first and fold")
        await asyncio.sleep(1)
        r.say(7, "next")
        await r.updates.join()
        r.prompt_hooks(7, "next")
        await asyncio.sleep(20)
        w.cancel()

    _run(vloop, scenario())
    assert 3 in r.chat.answers.values()
    assert 7 not in r.chat.answers.values()
    assert r.chat.reactions[3][-1] == "👍"
    assert r.sess.trigger_msg_id == 7


def test_notify_counts_itself_while_it_runs(replay, vloop, monkeypatch):
    """The orphan sweep's "nothing is handling this session" reads this."""
    r = replay
    sess = r.sess
    seen = []

    async def _event(s, event, context):
        seen.append(s.notify_in_flight)

    monkeypatch.setattr(r.bot, "_notify_event", _event)
    _run(vloop, r.bot.notify(sess, "tool_use", {}))
    assert seen == [1]
    assert sess.notify_in_flight == 0


def test_message_popped_inside_a_job_keeps_the_job_open(replay, vloop):
    """The popped turn runs INSIDE the job: its Stop is an interim one, and
    the job's card keeps waiting on the agent."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it", tools=0)
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        r.say(3, "queued in the job")
        await r.updates.join()
        r.prompt_hooks(3, "queued in the job")
        await asyncio.sleep(1)
        r.stop("launched")
        await asyncio.sleep(10)
        turn_after_pop = sess.turn_seq
        r.stop("answer queued")
        await asyncio.sleep(10)
        w.cancel()
        return turn_after_pop

    turn_after_pop = _run(vloop, scenario())
    assert turn_after_pop == 1  # the pop joined the job, no new turn
    assert sess.job_background_open()
    assert len(r.chat.live_cards()) == 1
    assert 3 in r.chat.answers.values()


def test_stale_request_does_not_pay_the_current_turns_owed_card(
        replay, vloop):
    """The current turn's card is owed (a flood refusal); a request made for
    an EARLIER turn must not be what sends it."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        old_turn = sess.turn_seq
        sess.status = Status.IDLE
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.job_reclaim_pending = False
        sess.card_turn_seq = sess.turn_seq
        sess.busy_card_owed = True
        await r.bot._send_busy_and_animate(sess, turn=old_turn)
        return sess.busy_card_owed

    assert _run(vloop, scenario()) is True
    assert r.chat.cards == {}


def test_detached_agent_tool_end_does_not_touch_the_card(mk_bot, run_async):
    """A detached agent's PostToolUse changed nothing on this turn's card:
    no edit is spent on it."""
    from aipager.state import TrackedSession

    bot = mk_bot()
    sess = TrackedSession(name="claude-x", label="x", status=Status.BUSY)
    sess.scope_chat_id = CHAT
    sess.busy_msg_id = 77
    sess.tool_history = [("Bash: parent", False)]
    edits = []

    async def _edit(*a, **k):
        edits.append(a)
        return True

    bot._edit_busy_rich = _edit
    run_async(bot.notify(sess, "tool_done", {
        "tool_name": "Bash", "tool_summary": "Bash: agent", "agent_id": "bg"}))
    assert edits == []
    assert sess.tool_history == [("Bash: parent", False)]


def test_api_error_stop_leaves_the_queue_for_the_next_finish(replay, vloop):
    """A run that ended on an API error processed nothing: the message
    queued during it is not taken as a next turn there (as before)."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first")
        r.say(3, "queued one")
        await r.updates.join()
        r.prompt_hooks(3, "queued one")
        await asyncio.sleep(1)
        r.stop("API Error: 402 credit balance too low")
        await asyncio.sleep(10)
        w.cancel()

    _run(vloop, scenario())
    assert [t["msg_id"] for t in sess.queued_targets] == [3]
    assert r.chat.card_sends_for(3) == 0


# ── review iteration 1 ──────────────────────────────────────────────────────

def _owned(r) -> bool:
    """Every card still showing Stop is the session's own live card, on a
    turn or job that is running."""
    sess = r.sess
    live = r.chat.live_cards()
    return all(m == sess.busy_msg_id for m in live) and (
        not live or sess.status in (Status.BUSY, Status.INTERACTIVE)
        or sess.job_background_open())


def test_stop_while_a_job_card_moves_leaves_no_orphan(replay, vloop,
                                                      monkeypatch):
    """R2, the 09:25:51-09:25:56 shape: a prompt to an idle session with a
    job open; the turn's Stop lands while the card step is still busy with
    the job's old card. On main the Stop was read as the job's interim (its
    agent still listed), the card step then dropped the agent and sent a
    fresh card with no turn running: a live Stop button nothing owned."""
    _layout()
    r = replay
    real_close = r.bot._close_superseded_card

    async def _slow_close(sess):
        await asyncio.sleep(5)
        await real_close(sess)

    monkeypatch.setattr(r.bot, "_close_superseded_card", _slow_close)
    real_send_busy = r.bot.send_busy

    async def _slow_send_busy(sess, **kw):
        await asyncio.sleep(5)
        return await real_send_busy(sess, **kw)

    r.bot.send_busy = _slow_send_busy

    async def scenario():
        w = _worker(r)
        await _job_open(r)
        r.say(5, "ok now what?")
        await asyncio.sleep(1)
        r.prompt_hooks(5, "ok now what?")
        await asyncio.sleep(1)
        r.stop("answer five")
        await asyncio.sleep(20)
        owned = _owned(r)
        w.cancel()
        return owned

    assert _run(vloop, scenario()) is True


def test_message_popped_at_the_jobs_final_stop_gets_its_card(replay, vloop):
    """The continuation turn's Stop closes the job; a message queued during
    it is popped there as a turn of its own, with its card."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _job_open(r)
        r.hook(hook_event_name="SubagentStop", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        r.hook(hook_event_name="UserPromptSubmit",
               prompt="<task-notification>a1 finished")
        await asyncio.sleep(1)
        r.tool("read the report")
        await asyncio.sleep(1)
        r.say(3, "queued in the continuation")
        await r.updates.join()
        r.prompt_hooks(3, "queued in the continuation")
        await asyncio.sleep(1)
        r.stop("the job is done")
        await asyncio.sleep(10)
        running = list(r.chat.live_cards())
        r.tool("work for the popped one")
        await asyncio.sleep(1)
        r.stop("answer three")
        await _drain(r, 10)
        w.cancel()
        return running

    running = _run(vloop, scenario())
    assert r.chat.card_sends_for(3) == 1
    assert len(running) == 1 and r.chat.cards[running[0]]["reply_to"] == 3
    assert r.chat.live_cards() == []
    assert 3 in r.chat.answers.values()
    assert not sess.job_background_open()


@pytest.mark.parametrize("layout", ["card", "replace", "merged"])
def test_popped_turn_ending_during_the_previous_finish(replay, vloop,
                                                      monkeypatch, layout):
    """Two finishes out at once: turn N's is slow (a flood wait), the popped
    turn N+1 runs a tool and Stops two seconds later. Each card is settled
    by its own turn's finish, and no card is sent for a turn that already
    ended."""
    _layout(layout)
    r = replay
    sess = r.sess
    slow = r.bot._mark_ran_commands
    slowed = {"n": 0}

    async def _slow_once(s):
        slowed["n"] += 1
        if slowed["n"] == 2:  # turn N's finish (the first is turn 0's none)
            await asyncio.sleep(6)
        await slow(s)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first")
        r.say(3, "queued one")
        await r.updates.join()
        r.prompt_hooks(3, "queued one")
        await asyncio.sleep(1)
        slowed["n"] = 1
        monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow_once)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.tool("the popped turn's tool")
        await asyncio.sleep(1.5)
        r.stop("answer queued")
        await _drain(r, 30)
        w.cancel()

    _run(vloop, scenario())
    chat = r.chat
    assert chat.live_cards() == []
    assert all(c["reply_to"] is not None for c in chat.cards.values())
    assert chat.card_sends_for(1) == 1
    assert chat.card_sends_for(3) <= 1
    first_card = [c for c in chat.cards.values() if c["reply_to"] == 1][0]
    if layout == "merged":  # turn N's answer is folded into its card
        assert "answer first" in first_card.get("text", "")
    else:
        assert 1 in chat.answers.values()
    assert 3 in chat.answers.values() or any(
        "answer queued" in c.get("text", "") for c in chat.cards.values()
        if c["reply_to"] == 3)
    assert sess.status == Status.IDLE and sess.busy_msg_id is None


def test_agent_tool_after_a_terminal_answered_permission_resumes_busy(
        replay, vloop):
    """A foreground agent's permission answered in the terminal: its next
    tool call means the session is working again (INTERACTIVE -> BUSY), as
    before 8.58. Only IDLE -> BUSY is what an agent's tool never causes."""
    r = replay
    sess = r.sess
    sess.status = Status.INTERACTIVE
    sess.active_subagents["fg1"] = {"type": "Explore", "started_at": 0.0}

    async def scenario():
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "after the answer"}, agent_id="fg1")
        await _drain(r, 1)

    _run(vloop, scenario())
    assert sess.status == Status.BUSY


def test_running_agents_long_tool_counts_as_work_in_flight(replay, vloop):
    """A tool of an agent this turn owns stamps the in-flight marker (the
    stale-busy warning stands down); a detached agent's does not."""
    r = replay
    sess = r.sess
    sess.status = Status.BUSY
    sess.active_subagents["fg1"] = {"type": "Explore", "started_at": 0.0}

    async def scenario():
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "detached"}, agent_id="bg1")
        await _drain(r, 1)
        detached = sess.pending_tool_started_at
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "owned"}, agent_id="fg1")
        await _drain(r, 1)
        return detached

    detached = _run(vloop, scenario())
    assert detached is None
    assert sess.pending_tool_started_at is not None


def test_detached_agent_tool_failure_keeps_the_parents_marker(replay, vloop):
    r = replay
    sess = r.sess

    async def scenario():
        sess.status = Status.BUSY
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "parent"})
        await _drain(r, 0.5)
        before = sess.pending_tool_started_at
        r.hook(hook_event_name="PostToolUseFailure", tool_name="Bash",
               tool_input={"command": "agent"}, agent_id="bg1")
        await _drain(r, 0.5)
        return before

    before = _run(vloop, scenario())
    assert before is not None
    assert sess.pending_tool_started_at == before


def test_restart_does_not_adopt_on_a_missing_transcript(replay, vloop,
                                                        monkeypatch):
    r = replay
    sess, card = _restored_card(r, monkeypatch, finished=False)
    sess.transcript_path = str(r.transcript) + ".missing"
    _run(vloop, r.bot.recover_sessions())
    assert r.chat.cards[card]["stop"] is False
    assert sess.busy_msg_id is None


def test_group_scoped_card_is_closed_in_its_own_chat(replay, vloop,
                                                     monkeypatch):
    """A restart settles and deletes a group session's cards in that group,
    never in the configured DM."""
    r = replay
    sess = r.sess
    sess.scope_chat_id = -100555
    calls = []
    real = r.bot._app.bot

    class _Spy:
        def __getattr__(self, name):
            method = getattr(real, name)

            async def _m(*a, **k):
                calls.append((name, k.get("chat_id")))
                return await method(*a, **k)
            return _m

    r.bot._app.bot = _Spy()
    card = r.chat.new_id()
    r.chat.cards[card] = {"reply_to": 1, "stop": True, "deleted": False}
    sess.pending_card_deletes = [card]
    sess, live = _restored_card(r, monkeypatch, finished=True)
    _run(vloop, r.bot.recover_sessions())
    assert calls and all(c == -100555 for _n, c in calls), calls


def test_stopped_turn_gets_no_late_card(replay, vloop, monkeypatch):
    """/stop ends the turn outside the finish path: a card request for that
    turn still waiting sends nothing."""
    _layout()
    r = replay
    sess = r.sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input",
                        AsyncMock(return_value=True))

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first")
        turn = sess.turn_seq
        await r.bot._stop_session_core(sess)
        cards = len(r.chat.cards)
        sess.busy_msg_id = None
        sess.status = Status.BUSY  # a stale status, as a racing hook leaves
        await r.bot._send_busy_and_animate(sess, turn=turn)
        w.cancel()
        return cards

    cards = _run(vloop, scenario())
    assert len(r.chat.cards) == cards


@pytest.mark.parametrize("path", [
    "session_end", "job_grace_expired", "job_agents_lost", "prompt_not_taken",
    "halt", "kill"])
def test_every_turn_ending_path_closes_the_turn_for_the_card_step(
        replay, vloop, monkeypatch, path):
    """A turn ended outside the finish path is closed for the card step
    too: a request for it that is still waiting sends no live card."""
    _layout()
    r = replay
    sess = r.sess
    for fn in ("send_keys", "discard_queued_input", "kill_session"):
        monkeypatch.setattr(f"aipager.dtach.inject.{fn}",
                            AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.trigger_msg_id = 1
        await r.bot._send_busy_and_animate(sess)
        turn = sess.turn_seq
        if path == "halt":
            await r.bot._halt_for_safety(sess, "blocked")
        elif path == "kill":
            await r.bot._kill_session_core(sess.name, sess.label)
        else:
            if path in ("job_grace_expired", "job_agents_lost"):
                sess.status = Status.IDLE
            await r.bot.notify(sess, path, {"source": "other", "grace": 5})
        return turn

    turn = _run(vloop, scenario())
    assert sess.closed_turn_seq == turn


def test_waiting_card_start_does_not_hold_telegram_updates(replay, vloop,
                                                           monkeypatch):
    """PTB handles updates one at a time: a prompt whose card must wait for
    the previous turn's finish returns at once, the card following later."""
    _layout()
    r = replay
    sess = r.sess
    slow = r.bot._mark_ran_commands

    async def _slow_mark(s):
        await asyncio.sleep(6)
        await slow(s)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first")
        monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow_mark)
        r.stop("answer first")
        await asyncio.sleep(1)
        r.say(7, "next")
        await r.updates.join()
        finish_still_out = sess.finish_gate is not None
        await asyncio.sleep(20)
        w.cancel()
        return finish_still_out

    assert _run(vloop, scenario()) is True
    assert r.chat.card_sends_for(7) == 1


def test_lost_card_is_not_resent_once_the_turn_is_not_running(replay, vloop):
    _layout()
    r = replay
    sess = r.sess
    real_send_busy = r.bot.send_busy
    calls = {"n": 0}

    async def _first_fails(s, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_send_busy(s, **kw)

    r.bot.send_busy = _first_fails

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.trigger_msg_id = 11
        await r.bot._send_busy_and_animate(sess)
        sess.status = Status.IDLE  # ended, its finish not run yet
        await r.bot._send_busy_and_animate(sess)

    _run(vloop, scenario())
    assert r.chat.cards == {}


def test_closed_turn_never_moves_backwards():
    """A slow finish of an older turn must not reopen a newer turn that has
    already ended."""
    from aipager.state import TrackedSession

    sess = TrackedSession(name="claude-x", label="x")
    sess.close_turn(5)
    sess.close_turn(3)
    assert sess.closed_turn_seq == 5
    sess.turn_seq = 7
    sess.close_turn()
    assert sess.closed_turn_seq == 7
