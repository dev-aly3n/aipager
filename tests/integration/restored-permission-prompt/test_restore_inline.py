"""A restart while the busy card shows a permission prompt (roadmap 8.102).

Before: the restarted daemon adopted the session as BUSY ("Working",
ticking) and the card's Allow/Deny answered nothing. Now the session comes
back waiting on that prompt, the card is left as it was, and its buttons
answer it: by keystrokes once the hook stopped waiting, through the hook
while it still waits.

Each row drives the real daemon on the virtual loop and restarts it
through the real state file (see conftest.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from datetime import datetime, timezone

from aipager import audit as audit_mod
from aipager.bot.dashboard import _pinned_state
from aipager.session_monitor import (
    IDLE_RECOVERY_GRACE,
    INTERACTIVE_TIMEOUT_SECONDS,
    SessionMonitor,
)
from aipager.state import Status

# The harness's (conftest.py; a hyphenated directory is no package).
LABEL = "aipager_boss"
OWNER = 12345
TUID = "toolu_01Restored"


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _dead_hook(tmp_path) -> dict:
    """The hook's reply channel once it gave up: nobody listens there."""
    return {"addr": str(tmp_path / "gone.sock"), "request_id": "req-dead"}


def _audit() -> dict:
    lines = audit_mod.AUDIT_LOG_PATH.read_text().splitlines()
    assert lines, "no audit record"
    return json.loads(lines[-1])


def _ts(wall: float) -> str:
    return datetime.fromtimestamp(wall, timezone.utc).isoformat()


async def _at_prompt(r, tmp_path, *, hook=None) -> int:
    card = await r.open_card()
    await r.permission(hook=hook or _dead_hook(tmp_path))
    assert r.sess.status is Status.INTERACTIVE
    assert r.sess.pending_permission is not None
    await r.shown(card, "allow")
    await asyncio.sleep(5)  # and nothing else in flight for the card
    return card


def test_restored_card_not_closed_or_animated(replay, vloop, tmp_path, caplog):
    r = replay

    async def scenario():
        card = await _at_prompt(r, tmp_path)
        texts_before = list(r.chat.cards[card]["texts"])
        await r.restart()
        await asyncio.sleep(5)
        return card, texts_before

    with caplog.at_level(logging.INFO):
        card, texts_before = _run(vloop, scenario())
    sess = r.sess
    assert sess.status is Status.INTERACTIVE
    assert not sess.animation_running()
    assert sess.busy_msg_id == card
    # The card was not edited at the restore: still its "Waiting" frame,
    # still its buttons.
    assert r.chat.cards[card]["texts"] == texts_before
    assert r.chat.cards[card]["stop"] and not r.chat.cards[card]["deleted"]
    assert "needs you" in _pinned_state(sess)
    assert (f"[{LABEL}] permission prompt restored after restart - waiting "
            f"for an answer (inline card {card})") in caplog.messages
    assert any(m.startswith("recovered 1 sessions: 1 restored")
               for m in caplog.messages)
    assert not any("adopted" in m for m in caplog.messages)


def test_allow_on_the_restored_card_answers_by_keystrokes(replay, vloop,
                                                         tmp_path):
    r = replay

    async def scenario():
        card = await _at_prompt(r, tmp_path)
        await r.restart()
        toast = await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(2)
        return card, toast

    card, toast = _run(vloop, scenario())
    sess = r.sess
    assert r.keys == ["Enter"]
    assert toast == f"Allowed [{LABEL}]"
    record = _audit()
    assert (record["via"], record["action"], record["user_id"]) == (
        "keystroke_fallback", "Allowed", OWNER)
    assert record["denied"] is False
    line = [m for m in r.chat.messages.values() if "Allowed by" in m["text"]]
    assert line and line[0]["reply_to"] == card
    assert sess.status is Status.BUSY
    assert sess.pending_permission is None
    assert sess.animation_running()
    assert "Working" in r.chat.cards[card]["text"]


def test_stop_after_answer_finishes_the_same_card(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _at_prompt(r, tmp_path)
        await r.restart()
        await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(2)
        r.hook(hook_event_name="PostToolUse", tool_name="Bash",
               tool_input={"command": "rm -rf build"}, tool_use_id=TUID)
        await asyncio.sleep(3)
        r.stop("build removed")
        await asyncio.sleep(15)
        return card

    card = _run(vloop, scenario())
    sess = r.sess
    assert sess.status is Status.IDLE
    assert list(r.chat.cards) == [card]  # no other card for this turn
    assert r.chat.live_cards() == []
    assert any("build removed" in t for t in r.chat.sent_texts()) or \
        "build removed" in r.chat.cards[card]["text"]


def test_a_message_during_the_answered_turn_waits_like_any_busy_turns(
        replay, vloop, tmp_path):
    """After the answer the card is the running turn's own (8.57 R1): a
    message sent meanwhile waits for the turn and starts no card of its
    own, and the card stays the one live card."""
    r = replay

    async def scenario():
        card = await _at_prompt(r, tmp_path)
        await r.restart()
        await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(2)
        w = asyncio.ensure_future(r._updates())
        r.say(3, "one more thing")
        await r.updates.join()
        await asyncio.sleep(5)
        w.cancel()
        return card

    card = _run(vloop, scenario())
    assert r.sess.status is Status.BUSY
    assert r.chat.card_sends_for(3) == 0
    assert r.chat.live_cards() == [card]


def test_a_repeated_turn_start_after_the_answer_reuses_the_card(
        replay, vloop, tmp_path):
    """After the answer the card is the running turn's own (8.57 R1): a
    second card request for this turn (its animator died meanwhile) puts
    the same card back to work instead of sending another."""
    r = replay

    async def scenario():
        card = await _at_prompt(r, tmp_path)
        await r.restart()
        await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(2)
        r.bot._stop_animation(r.sess)
        await r.bot._send_busy_and_animate(r.sess)
        await asyncio.sleep(2)
        return card

    card = _run(vloop, scenario())
    assert r.sess.busy_msg_id == card
    assert list(r.chat.cards) == [card]
    assert r.sess.animation_running()


def test_a_card_lost_after_the_answer_is_still_paid(replay, vloop, tmp_path):
    """After the answer the card is the running turn's own (8.57 R1), as an
    adopted card is (8.101): lost and then refused by the flood gate, it is
    owed to THIS turn and paid when the chat can take it."""
    r = replay

    async def scenario():
        card = await _at_prompt(r, tmp_path)
        await r.restart()
        await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(2)
        r.bot._stop_animation(r.sess)
        r.sess.busy_msg_id = None     # the card was lost ...
        r.sess.busy_card_owed = True  # ... and its re-send refused
        await r.bot._send_owed_card(r.sess, reason="the chat can take it")
        await asyncio.sleep(15)  # its first frames
        return card

    card = _run(vloop, scenario())
    new = r.sess.busy_msg_id
    assert new and new != card
    assert new in r.chat.live_cards()
    # Re-sent as this turn's card, not as a new turn's: the answered
    # prompt's row is still on it.
    assert any("rm -rf build" in t for t in r.chat.cards[new]["texts"]), \
        r.chat.cards[new]["texts"]


def test_deny_on_the_restored_card_ends_the_turn_like_a_typed_refusal(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _at_prompt(r, tmp_path)
        await r.restart()
        await r.tap(card, r.cb(card, "deny"))
        await asyncio.sleep(1)
        # Claude Code writes its interrupt marker for the refusal row.
        r.append({"type": "user", "timestamp": _ts(vloop.wall()),
                  "message": {"role": "user", "content":
                              "[Request interrupted by user for tool use]"}})
        await asyncio.sleep(10)
        return card

    _run(vloop, scenario())
    assert r.keys == ["Down"] * 5 + ["Enter"]
    record = _audit()
    assert (record["via"], record["action"], record["denied"]) == (
        "keystroke_fallback", "Denied", True)
    assert r.sess.status is Status.IDLE
    assert r.chat.live_cards() == []


def test_live_hook_answers_after_quick_restart(replay, vloop, tmp_path):
    """Restarted within the hook's 20 s: it still waits, and the answer goes
    to it, not into the terminal."""
    r = replay
    path = str(tmp_path / "live.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(path)
    listener.settimeout(2.0)
    try:
        async def scenario():
            card = await _at_prompt(
                r, tmp_path, hook={"addr": path, "request_id": "req-live"})
            await r.restart()
            await r.tap(card, r.cb(card, "allow"))
            await asyncio.sleep(1)

        _run(vloop, scenario())
        try:
            decision = json.loads(listener.recvfrom(65536)[0].decode())
        except socket.timeout:
            decision = None  # nothing reached the hook
    finally:
        listener.close()
    assert r.keys == []
    assert decision is not None and decision["request_id"] == "req-live"
    assert _audit()["via"] == "hook_decision"
    assert r.sess.status is Status.BUSY


def test_previous_turn_tail_restores(replay, vloop, tmp_path, caplog):
    """What the transcript looks like while Claude Code's dialog waits: the
    previous turn's end, queue bookkeeping, sidecar lines, none of the
    waiting turn's own entries (live 2026-10-07). 8.101's reading calls
    that turn finished and would close the card; the prompt is restored."""
    r = replay

    async def scenario():
        r.append({"type": "user", "timestamp": _ts(vloop.wall() - 300),
                  "message": {"role": "user", "content": "earlier"}},
                 {"type": "assistant", "timestamp": _ts(vloop.wall() - 290),
                  "message": {"role": "assistant", "stop_reason": "end_turn",
                              "content": [{"type": "text", "text": "done"}]}},
                 {"type": "system", "subtype": "turn_duration",
                  "timestamp": _ts(vloop.wall() - 289)})
        card = await _at_prompt(r, tmp_path)
        await asyncio.sleep(5)
        # Written while the dialog waits: bookkeeping only.
        r.append({"type": "queue-operation", "operation": "enqueue",
                  "timestamp": _ts(vloop.wall()), "content": "typed meanwhile"},
                 {"type": "system", "subtype": "informational",
                  "timestamp": _ts(vloop.wall())},
                 {"type": "ai-title", "aiTitle": "cleanup"})
        os.utime(r.transcript, (vloop.wall(), vloop.wall()))
        await r.restart(recover=False)
        still_running = r.bot._turn_still_running(r.sess)
        await r.bot.recover_sessions()
        return card, still_running

    with caplog.at_level(logging.INFO):
        card, still_running = _run(vloop, scenario())
    assert still_running is False  # 8.101 alone would have closed the card
    assert r.sess.status is Status.INTERACTIVE
    assert r.chat.cards[card]["stop"]
    assert not any("Daemon restarted" in t for t in r.chat.cards[card]["texts"])


def test_watchdog_counts_from_restore(replay, vloop, tmp_path):
    """The card's clock says the turn is 400 s old; the INTERACTIVE
    watchdog still gives the restored prompt its full silence window,
    counted from the restore."""
    r = replay
    monitor = SessionMonitor(r.bot.registry, r.bot.notify)

    async def scenario():
        await _at_prompt(r, tmp_path)
        r.sess.busy_started_at -= 400
        await r.restart()
        restored_at = vloop.time()
        await monitor._scan()
        first = r.sess.status
        await asyncio.sleep(INTERACTIVE_TIMEOUT_SECONDS - 10)
        await monitor._scan()
        before_timeout = r.sess.status
        await asyncio.sleep(20)
        await monitor._scan()
        return first, before_timeout, restored_at

    first, before_timeout, restored_at = _run(vloop, scenario())
    assert vloop.time() - r.sess.busy_started_at > INTERACTIVE_TIMEOUT_SECONDS
    assert first is Status.INTERACTIVE
    assert before_timeout is Status.INTERACTIVE
    assert r.sess.status is Status.BUSY  # demoted, at last


def test_answered_turn_not_idle_recovered(replay, vloop, tmp_path, caplog):
    """After the answer the tool runs, and the transcript's tail is still
    the previous turn's end: quiet and finished-looking. The answered turn
    stays BUSY until its own hooks end it."""
    r = replay
    monitor = SessionMonitor(r.bot.registry, r.bot.notify)

    async def scenario():
        r.append({"type": "user", "timestamp": _ts(vloop.wall() - 300),
                  "message": {"role": "user", "content": "earlier"}},
                 {"type": "assistant", "timestamp": _ts(vloop.wall() - 290),
                  "message": {"role": "assistant", "stop_reason": "end_turn",
                              "content": [{"type": "text", "text": "done"}]}})
        card = await _at_prompt(r, tmp_path)
        await r.restart()
        await r.tap(card, r.cb(card, "allow"))
        os.utime(r.transcript, (vloop.wall(), vloop.wall()))
        await asyncio.sleep(IDLE_RECOVERY_GRACE + 5)
        await monitor._scan()
        await asyncio.sleep(2)

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert r.sess.status is Status.BUSY
    assert not any("recovering to IDLE" in m for m in caplog.messages)


def test_a_restored_prompt_survives_a_second_restart(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _at_prompt(r, tmp_path)
        await r.restart()
        await asyncio.sleep(30)
        await r.restart()
        await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.keys == ["Enter"]
    assert r.sess.status is Status.BUSY


def test_the_answer_discounts_the_whole_wait_from_the_card(replay, vloop,
                                                          tmp_path):
    """The card's clock does not count the time the prompt waited, before
    the restart or after it."""
    r = replay

    async def scenario():
        await r.open_card()
        await asyncio.sleep(20)                      # working: counted
        card = r.sess.busy_msg_id
        await r.permission(hook=_dead_hook(tmp_path))
        await asyncio.sleep(100)                     # waiting: not counted
        await r.restart()
        await asyncio.sleep(50)                      # still waiting
        await r.tap(card, r.cb(card, "allow"))
        return vloop.time() - r.sess.busy_started_at

    age = _run(vloop, scenario())
    assert 20 <= age <= 26, age
