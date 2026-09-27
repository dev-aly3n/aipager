"""A new turn's first steps belong to its own card (roadmap 8.62), and a
job's interim answer goes under the message that run absorbed (8.63).

The gap: a turn can start while the previous turn's finish is still out
(a queued message popped at the Stop, or a Telegram prompt sent meanwhile),
and its card, with the reset of the card state that comes with it, waits
for that finish. The new turn's hooks that arrive in between are held until
its card state exists, then handled in order. Rows drive the real daemon on
the virtual loop (see conftest.py).
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs
from aipager.dtach import hook_receiver as hook_receiver_mod
from aipager.state import Status

CHAT = 256113222
PREFIX = "[via Telegram · @owner]\n"


def _layout(layout: str = "card") -> None:
    prefs.set_preference(CHAT, "layout", layout)


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _drain(r, seconds: float = 30.0) -> None:
    await asyncio.sleep(seconds)
    await asyncio.gather(*r.tasks, return_exceptions=True)


async def _turn(r, mid: int, text: str, *, tools: int = 1) -> None:
    r.say(mid, text)
    await r.updates.join()
    r.prompt_hooks(mid, text)
    await asyncio.sleep(1)
    for i in range(tools):
        r.tool(f"{text} step {i}")
        await asyncio.sleep(1)


def _worker(r):
    return asyncio.ensure_future(r._updates())


def _slow_finish(r, monkeypatch, seconds: float = 6.0):
    """Keep the next finish out for *seconds*, like a flood-paced call."""
    real = r.bot._mark_ran_commands

    async def _slow(sess):
        await asyncio.sleep(seconds)
        await real(sess)

    monkeypatch.setattr(r.bot, "_mark_ran_commands", _slow)
    return real


def _cards_for(r, reply_to) -> list[dict]:
    return [c for c in r.chat.cards.values() if c["reply_to"] == reply_to]


def _ever_showed(card: dict, needle: str) -> bool:
    return any(needle in (t or "") for t in card.get("texts", []))


def _queue_after_first(r):
    """Turn 1 running with one step, message 3 queued behind it."""
    async def _go():
        await _turn(r, 1, "first", tools=1)
        r.say(3, "queued one")
        await r.updates.join()
        r.prompt_hooks(3, "queued one")
        await asyncio.sleep(1)
    return _go()


# ── (a) the popped turn's first steps ─────────────────────────────────────

def test_popped_turns_first_steps_land_on_its_own_card(replay, vloop,
                                                       monkeypatch):
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _queue_after_first(r)
        real = _slow_finish(r, monkeypatch)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        # Claude has already started the popped message: its first step
        # and sentence arrive while turn 1's finish is still out.
        r.tool("popped step")
        await asyncio.sleep(0.3)
        r.hook(hook_event_name="MessageDisplay",
               delta="Working on the queued one now.", message_id="m-pop",
               index=0)
        await asyncio.sleep(20)
        (live,) = _cards_for(r, 3)
        live_text = live["text"]
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        r.stop("answer queued")
        await _drain(r)
        w.cancel()
        return live_text

    live_text = _run(vloop, scenario())
    # Its PostToolUse was held too, and settled the row on the new card.
    assert "✅ `Bash: popped step`" in live_text
    (old,) = _cards_for(r, 1)
    (new,) = _cards_for(r, 3)
    assert not _ever_showed(old, "popped step")
    assert not _ever_showed(old, "Working on the queued one now.")
    assert "first step 0" in old["text"]
    assert "popped step" in new["text"]
    assert "Working on the queued one now." in new["text"]
    assert r.chat.live_cards() == []


# ── (b) a background agent the popped turn launches in the gap ───────────

def test_agent_launched_in_the_gap_stays_tracked(replay, vloop, monkeypatch):
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _queue_after_first(r)
        real = _slow_finish(r, monkeypatch)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.hook(hook_event_name="PreToolUse", tool_name="Agent",
               tool_input={"description": "background work"})
        await asyncio.sleep(0.2)
        r.hook(hook_event_name="SubagentStart", agent_id="a9",
               agent_type="pipeline-runner")
        await asyncio.sleep(0.2)
        r.hook(hook_event_name="PostToolUse", tool_name="Agent",
               tool_input={"description": "background work"})
        await asyncio.sleep(20)
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        r.stop("launched it")
        await _drain(r, 15)
        w.cancel()

    _run(vloop, scenario())
    assert "a9" in sess.active_subagents
    assert sess.job_background_open()
    live = r.chat.live_cards()
    assert len(live) == 1 and r.chat.cards[live[0]]["reply_to"] == 3
    assert "pipeline-runner" in r.chat.cards[live[0]]["text"]
    (old,) = _cards_for(r, 1)
    assert not _ever_showed(old, "pipeline-runner")


# ── (c) a Telegram prompt sent while the previous finish is out ──────────

def test_prompt_sent_during_a_finish_keeps_its_first_step(replay, vloop,
                                                          monkeypatch):
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first", tools=1)
        real = _slow_finish(r, monkeypatch)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.say(5, "next")
        await r.updates.join()
        r.prompt_hooks(5, "next")
        await asyncio.sleep(0.5)
        r.tool("next step")
        await asyncio.sleep(20)
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        r.stop("answer next")
        await _drain(r)
        w.cancel()

    _run(vloop, scenario())
    (old,) = _cards_for(r, 1)
    (new,) = _cards_for(r, 5)
    assert not _ever_showed(old, "next step")
    assert "next step" in new["text"]
    assert r.chat.live_cards() == []


# ── (d) the popped turn ends inside the gap ──────────────────────────────

def test_popped_turn_ending_in_the_gap_is_finished_on_its_own_card(
        replay, vloop, monkeypatch):
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _queue_after_first(r)
        real = _slow_finish(r, monkeypatch)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.tool("popped step")
        await asyncio.sleep(0.3)
        r.stop("answer queued")
        await asyncio.sleep(1)
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        await _drain(r)
        w.cancel()

    _run(vloop, scenario())
    assert sorted(v for v in r.chat.answers.values() if v in (1, 3)) == [1, 3]
    (old,) = _cards_for(r, 1)
    (new,) = _cards_for(r, 3)
    assert not _ever_showed(old, "popped step")
    assert "popped step" in new["text"] and new["stop"] is False
    assert r.chat.live_cards() == []
    assert r.sess.status == Status.IDLE


# ── (e) a hold that nothing releases gives up ────────────────────────────

def test_a_hold_nothing_releases_gives_up_after_its_bound(replay, vloop,
                                                          monkeypatch):
    r = replay
    sess = r.sess
    monkeypatch.setattr(hook_receiver_mod, "TURN_STATE_HOLD_SECONDS", 5.0)

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.card_turn_seq = sess.turn_seq - 1
        sess.hold_turn_state(sess.turn_seq)
        started = vloop.time()
        task = r.hook(hook_event_name="PreToolUse", tool_name="Bash",
                      tool_input={"command": "held step"})
        await asyncio.sleep(1)
        rows_while_held = [s for s, _ in sess.tool_history]
        await task
        return rows_while_held, vloop.time() - started

    rows_while_held, waited = _run(vloop, scenario())
    assert "Bash: held step" not in rows_while_held
    assert "Bash: held step" in [s for s, _ in sess.tool_history]
    assert 5.0 <= waited < 6.0
    assert sess.turn_state_hold is None


# ── (f) what the hold does not touch ─────────────────────────────────────

def test_a_hold_delays_only_the_turns_own_activity(replay, vloop):
    r = replay
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.card_turn_seq = sess.turn_seq - 1
        sess.hold_turn_state(sess.turn_seq)
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "held step"})
        r.prompt_hooks(9, "sent meanwhile")
        await asyncio.sleep(0.5)
        queued_while_held = [t.get("msg_id") for t in sess.queued_targets]
        rows_while_held = [s for s, _ in sess.tool_history]
        sess.release_turn_state()
        await asyncio.sleep(0.5)
        return queued_while_held, rows_while_held

    queued_while_held, rows_while_held = _run(vloop, scenario())
    assert 9 in queued_while_held
    assert "Bash: held step" not in rows_while_held
    assert "Bash: held step" in [s for s, _ in sess.tool_history]


def test_an_in_job_pop_holds_nothing(replay, vloop, monkeypatch):
    """A message popped inside an open job continues the job's turn: no new
    turn state is coming, so nothing may wait for one."""
    _layout()
    r = replay
    sess = r.sess
    holds = []

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
        real = _slow_finish(r, monkeypatch)
        r.stop("launched")
        await asyncio.sleep(0.5)
        holds.append((sess.turn_state_hold_for, sess.turn_state_held()))
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        await asyncio.sleep(15)
        w.cancel()

    _run(vloop, scenario())
    assert holds == [(None, False)]


# ── (g) 8.63: the interim answer follows what the run absorbed ───────────

def test_interim_answer_replies_to_the_message_the_run_absorbed(replay,
                                                                vloop):
    _layout()
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "launch it", tools=0)
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        r.say(2, "fold this in")
        await r.updates.join()
        r.prompt_hooks(2, "fold this in")
        await asyncio.sleep(1)
        r.append({"type": "queue-operation", "operation": "remove",
                  "reason": "absorbed_mid_turn",
                  "content": PREFIX + "fold this in"})
        r.stop("launched, and folded that in")
        await asyncio.sleep(15)
        w.cancel()

    _run(vloop, scenario())
    assert 2 in r.chat.answers.values()
    assert 1 not in r.chat.answers.values()
    assert r.chat.reactions[2][-1] == "👍"


# ── releases: at the reset, and when a card request is dropped ───────────

def test_held_steps_are_handled_while_the_new_card_is_sent(replay, vloop,
                                                           monkeypatch):
    """Released at the reset, not after the card send returns: under flood
    pacing that send takes seconds, and a held permission prompt or row
    should not wait on it too."""
    _layout()
    r = replay
    rows_at_send_end = []
    real_open = r.bot._open_busy_card

    async def _open_spy(s):
        msg_id = await real_open(s)
        rows_at_send_end.append([row for row, _ in s.tool_history])
        return msg_id

    async def scenario():
        w = _worker(r)
        await _queue_after_first(r)
        monkeypatch.setattr(r.bot, "_open_busy_card", _open_spy)
        real = _slow_finish(r, monkeypatch)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.tool("popped step")
        await asyncio.sleep(20)
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        w.cancel()

    _run(vloop, scenario())
    assert "Bash: popped step" in rows_at_send_end[-1]


async def _row_seen_at(r, row: str, limit: float) -> float | None:
    """Virtual seconds until *row* is in the session's tool rows, or None."""
    start = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - start < limit:
        if row in [s for s, _ in r.sess.tool_history]:
            return asyncio.get_running_loop().time() - start
        await asyncio.sleep(0.5)
    return None


def test_a_dropped_popped_card_request_releases_the_hold(replay, vloop,
                                                         monkeypatch):
    _layout()
    r = replay

    async def _dropped(sess, **kwargs):
        return None  # the turn-card step sent nothing and reset nothing

    async def scenario():
        w = _worker(r)
        await _queue_after_first(r)
        real = _slow_finish(r, monkeypatch)
        monkeypatch.setattr(r.bot, "_send_busy_and_animate", _dropped)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.tool("popped step")
        seen = await _row_seen_at(r, "Bash: popped step", 40)
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        w.cancel()
        return seen

    seen = _run(vloop, scenario())
    assert seen is not None and seen < 20  # not the 60 s bound


def test_a_dropped_gated_card_request_releases_the_hold(replay, vloop,
                                                        monkeypatch):
    _layout()
    r = replay
    real_send = r.bot._send_busy_and_animate

    async def _drop_after_gate(sess, **kwargs):
        if kwargs.get("_after_gate"):
            return None  # dropped once the finish let it through
        return await real_send(sess, **kwargs)

    async def scenario():
        w = _worker(r)
        await _turn(r, 1, "first", tools=1)
        real = _slow_finish(r, monkeypatch)
        monkeypatch.setattr(r.bot, "_send_busy_and_animate", _drop_after_gate)
        r.stop("answer first")
        await asyncio.sleep(0.5)
        r.say(5, "next")
        await r.updates.join()
        r.prompt_hooks(5, "next")
        await asyncio.sleep(0.5)
        held = r.sess.turn_state_held()
        r.tool("next step")
        seen = await _row_seen_at(r, "Bash: next step", 40)
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        w.cancel()
        return held, seen

    held, seen = _run(vloop, scenario())
    assert held is True
    assert seen is not None and seen < 20


# ── the hold's own contract ──────────────────────────────────────────────

def test_hold_contract(vloop):
    from aipager.state import TrackedSession

    sess = TrackedSession(name="claude-x", label="x")
    sess.turn_seq = 5
    sess.card_turn_seq = 4
    sess.hold_turn_state(5)
    first = sess.turn_state_hold
    assert sess.turn_state_held()
    # A second hold for the same turn keeps the first (nothing released).
    sess.hold_turn_state(5)
    assert sess.turn_state_hold is first and not first.is_set()
    # An older turn's release never frees a newer turn's hold.
    sess.release_turn_state(4)
    assert sess.turn_state_held() and not first.is_set()
    # A late hold request for a turn that is over holds nothing, and never
    # frees the running turn's hold.
    sess.hold_turn_state(4)
    assert sess.turn_state_hold is first and not first.is_set()
    # A newer turn's hold releases the older one first.
    sess.turn_seq = 6
    sess.hold_turn_state(6)
    assert first.is_set() and sess.turn_state_hold_for == 6
    # Released by its own turn (or a later one): idempotent.
    second = sess.turn_state_hold
    sess.release_turn_state(7)
    assert second.is_set() and sess.turn_state_hold is None
    sess.release_turn_state(7)
    assert not sess.turn_state_held()


def test_a_stale_or_satisfied_hold_holds_nothing(vloop):
    from aipager.state import TrackedSession

    sess = TrackedSession(name="claude-x", label="x")
    sess.turn_seq = 5
    sess.card_turn_seq = 4
    sess.hold_turn_state(5)
    sess.turn_seq = 6  # a later turn is running: not the held one
    assert not sess.turn_state_held()
    sess.turn_seq = 5
    sess.card_turn_seq = 5  # the held turn already has its card state
    assert not sess.turn_state_held()


@pytest.mark.parametrize("event,held", [
    ("PermissionRequest", True), ("permission_prompt", True),
    ("PreToolUse", True), ("PostToolUse", True), ("PostToolUseFailure", True),
    ("MessageDisplay", True), ("SubagentStart", True), ("SubagentStop", True),
    ("PreCompact", True), ("PostCompact", True), ("SessionStart", True),
    ("StopFailure", True), ("Stop", True), ("idle_prompt", True),
    ("Notification", True),
    ("UserPromptSubmit", False), ("queue_pickup", False),
    ("statusline", False), ("SessionEnd", False), ("safety_blocked", False),
    ("hook_memory_cap_hit", False), ("permission_reply_timeout", False),
])
def test_which_events_wait_for_the_turn_state(event, held):
    assert hook_receiver_mod._is_turn_activity(event) is held


def test_a_held_permission_request_waits_only_briefly(replay, vloop):
    """The permission hook waits 20 s for Telegram's answer: held, the
    request waits at most PERMISSION_HOLD_SECONDS, and the turn's other
    events stay held."""
    r = replay
    sess = r.sess

    async def scenario():
        r.bot.registry.transition(sess.name, Status.BUSY)
        sess.card_turn_seq = sess.turn_seq - 1
        sess.hold_turn_state(sess.turn_seq)
        r.hook(hook_event_name="PermissionRequest", tool_name="Bash",
               tool_input={"command": "rm -rf build"})
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input={"command": "held step"})
        await asyncio.sleep(
            hook_receiver_mod.PERMISSION_HOLD_SECONDS + 1)
        return (sess.status, sess.turn_state_held(),
                [s for s, _ in sess.tool_history])

    status, still_held, rows = _run(vloop, scenario())
    assert status == Status.INTERACTIVE
    assert still_held
    assert "Bash: held step" not in rows


def test_the_finishing_turns_late_answer_prose_is_not_held(replay, vloop,
                                                          monkeypatch):
    """The finishing turn's own answer, if its MessageDisplay reaches the
    daemon after its Stop, is that turn's: never the popped turn's first
    sentence."""
    _layout()
    r = replay
    sess = r.sess

    async def scenario():
        w = _worker(r)
        await _queue_after_first(r)
        real = _slow_finish(r, monkeypatch)
        r.stop("answer first")
        await asyncio.sleep(0.3)
        r.hook(hook_event_name="MessageDisplay", delta="answer first",
               message_id="m-first", index=0)
        await asyncio.sleep(0.3)
        r.tool("popped step")
        await asyncio.sleep(20)
        monkeypatch.setattr(r.bot, "_mark_ran_commands", real)
        r.stop("answer queued")
        await _drain(r)
        w.cancel()

    _run(vloop, scenario())
    (new,) = _cards_for(r, 3)
    assert not _ever_showed(new, "answer first")
    assert "popped step" in new["text"]
    assert sess.finishing_answer == ""  # cleared with the finish's gate


def test_a_held_turn_start_counts_as_work_in_flight(vloop):
    """The held turn's PreToolUse is not recorded yet, so the idle-recovery
    and stale-busy checks must not read the quiet transcript as a turn that
    ended."""
    from aipager.state import TURN_STATE_HOLD_SECONDS, TrackedSession

    sess = TrackedSession(name="claude-x", label="x")
    sess.turn_seq = 5
    sess.card_turn_seq = 4
    now = vloop.time()
    assert sess.work_in_flight_reason(now) is None
    sess.hold_turn_state(5)
    reason = sess.work_in_flight_reason(now + 10)
    assert reason is not None and reason[0] == "turn start"
    assert sess.work_in_flight_reason(now + TURN_STATE_HOLD_SECONDS) is None
