"""A card adopted at a restart is a working session's card (roadmap 8.101).

Seen live on 2026-10-07 (two restarts mid-answer): the adoption kept the
card but left the session IDLE and the card still until the turn's next
hook, and a text-only answer fires none before its Stop. The card stopped
ticking for minutes, the pin said idle, and an answer longer than the
adoption window would have lost its card to the orphan sweep.

Each row drives the real daemon on the virtual loop (see conftest.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from aipager import preferences as prefs
from aipager.bot import animation
from aipager.bot import lifecycle as lifecycle_mod
from aipager.session_monitor import (
    IDLE_RECOVERY_GRACE,
    ORPHAN_CARD_GRACE_SECONDS,
    SessionMonitor,
    _quiet_since,
    orphan_card_due,
)
from aipager.state import SessionRegistry, Status

CHAT = 256113222


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _restored(r, monkeypatch, vloop, *, seconds_in: float | None = 40.0,
              status: Status = Status.GONE, layout: str = "card"):
    """What a restart finds mid-answer: the card the old process recorded
    as live, a transcript whose turn has no answer yet, the session as the
    state file restores a live one (GONE with a backfilled ``gone_at``, or
    UNKNOWN), and the card clock anchor the old process saved
    (``seconds_in`` before now; None for a state file without one)."""
    prefs.set_preference(CHAT, "layout", layout)
    sess = r.sess
    card = r.chat.new_id()
    r.chat.cards[card] = {"reply_to": 1, "stop": True, "deleted": False,
                          "text": "⏳ aipager_boss · Working · 13s"}
    sess.busy_msg_id = card
    sess.trigger_msg_id = 1
    sess.busy_card_trigger = 1
    sess.status = status
    sess.gone_at = vloop.wall() - 60 if status is Status.GONE else None
    if seconds_in is not None:
        sess.restored_card_started_wall = vloop.wall() - seconds_in
    r.append({"type": "user", "message": {
        "role": "user", "content": "[via Telegram · @owner]\nwrite the essay"}})
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[sess.name]))
    return sess, card


@pytest.mark.parametrize("layout", ["card", "replace", "merged"])
@pytest.mark.parametrize("status", [Status.GONE, Status.UNKNOWN])
def test_adopted_card_ticks_with_no_hook_and_the_stop_finishes_it(
        replay, vloop, monkeypatch, caplog, layout, status):
    r = replay
    sess, card = _restored(r, monkeypatch, vloop, seconds_in=40.0,
                           status=status, layout=layout)
    anchor = sess.restored_card_started_wall

    async def scenario():
        await r.bot.recover_sessions()
        at_adoption = (sess.status, sess.gone_at, sess.animation_running(),
                       sess.busy_started_wall, sess.turn_entered_wall)
        await asyncio.sleep(30)  # the answer is being written: no hook
        mid = (sess.status, animation.card_age(sess, vloop.time()),
               list(r.chat.cards[card].get("texts", [])))
        r.stop("the essay")
        await asyncio.sleep(10)
        return at_adoption, mid

    with caplog.at_level(logging.INFO):
        at_adoption, mid = _run(vloop, scenario())
    # BUSY, alive, ticking, and the turn's own start for the answer's
    # time and the transcript reads that skip earlier turns.
    assert at_adoption == (Status.BUSY, None, True, anchor, anchor)
    status_mid, age, texts = mid
    assert status_mid is Status.BUSY
    assert 69 <= age <= 72           # 40 s before the restart + 30 s since
    assert texts and "1m" in texts[-1]  # the counter went on from 40 s
    assert r.chat.live_cards() == []
    assert sess.busy_msg_id is None and sess.status is Status.IDLE
    assert not any("Stop while already IDLE" in m for m in caplog.messages)


def test_a_message_during_the_adopted_turn_waits_like_any_busy_turns(
        replay, vloop, monkeypatch):
    r = replay
    sess, card = _restored(r, monkeypatch, vloop)

    async def scenario():
        w = asyncio.ensure_future(r._updates())
        await r.bot.recover_sessions()
        r.say(3, "one more thing")
        await r.updates.join()
        await asyncio.sleep(2)
        w.cancel()

    _run(vloop, scenario())
    assert sess.status is Status.BUSY
    assert r.chat.card_sends_for(3) == 0     # no turn of its own started
    assert r.chat.live_cards() == [card]


def test_a_card_refused_later_in_the_adopted_turn_is_still_paid(
        replay, vloop, monkeypatch):
    """The adoption is this turn's start as far as the card goes: a card
    lost and then refused by the flood gate is owed to THIS turn, and paid
    when the chat can take it (8.17c), not dropped as an older turn's."""
    r = replay
    sess, card = _restored(r, monkeypatch, vloop)

    async def scenario():
        await r.bot.recover_sessions()
        r.bot._stop_animation(sess)
        sess.busy_msg_id = None     # the card was lost ...
        sess.busy_card_owed = True  # ... and its re-send refused
        await r.bot._send_owed_card(sess, reason="the chat can take it")
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert sess.busy_msg_id and sess.busy_msg_id != card
    assert sess.busy_msg_id in r.chat.live_cards()


def test_an_answer_longer_than_the_adoption_window_keeps_its_card(
        replay, vloop, monkeypatch):
    r = replay
    sess, card = _restored(r, monkeypatch, vloop)

    async def scenario():
        await r.bot.recover_sessions()
        await asyncio.sleep(lifecycle_mod.CARD_ADOPT_SECONDS + 20)
        now = vloop.time()
        return (sess.status, sess.animation_running(),
                orphan_card_due(sess, now),
                orphan_card_due(sess, now + ORPHAN_CARD_GRACE_SECONDS))

    assert _run(vloop, scenario()) == (Status.BUSY, True, False, False)
    assert r.chat.live_cards() == [card]


@pytest.mark.parametrize("seconds_in", [40.0, None])
def test_a_lost_stop_is_recovered_once_the_transcript_shows_the_end(
        replay, vloop, monkeypatch, caplog, seconds_in):
    """The release the adoption relies on when its Stop never comes:
    idle-recovery, with or without a saved clock."""
    r = replay
    sess, card = _restored(r, monkeypatch, vloop, seconds_in=seconds_in)
    monitor = SessionMonitor(r.bot.registry, r.bot.notify)

    async def scenario():
        await r.bot.recover_sessions()
        await asyncio.sleep(20)
        stamp = datetime.fromtimestamp(vloop.wall(), timezone.utc)
        r.append({"type": "assistant", "timestamp": stamp.isoformat(),
                  "message": {"id": "m9", "role": "assistant",
                              "stop_reason": "end_turn",
                              "content": [{"type": "text",
                                           "text": "the essay"}]}})
        os.utime(r.transcript, (vloop.wall(), vloop.wall()))
        await asyncio.sleep(IDLE_RECOVERY_GRACE + 2)
        await monitor._scan()
        await asyncio.sleep(10)

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert sess.status is Status.IDLE
    assert r.chat.live_cards() == []
    # Released by idle-recovery (no Stop was sent), with this turn's answer.
    assert any("recovering to IDLE (missed Stop hook)" in m
               for m in caplog.messages)
    assert any("the essay" in t for t in r.chat.sent_texts()), \
        r.chat.sent_texts()


def test_silence_for_the_stale_note_counts_from_the_adoption(
        replay, vloop, monkeypatch):
    """A misjudged adoption is released like any BUSY session whose Stop
    does not come; its stale-BUSY note measures silence from the adoption,
    not from the turn's start before the restart."""
    r = replay
    sess, _card = _restored(r, monkeypatch, vloop, seconds_in=500.0)
    _run(vloop, r.bot.recover_sessions())
    assert _quiet_since(sess) == vloop.time()
    assert vloop.time() - sess.busy_started_at >= 500  # the card's own clock


def test_without_a_saved_anchor_the_card_counts_from_the_adoption(
        replay, vloop, monkeypatch):
    r = replay
    sess, _card = _restored(r, monkeypatch, vloop, seconds_in=None)

    async def scenario():
        await r.bot.recover_sessions()
        adopted_at = vloop.time()
        await asyncio.sleep(30)
        return (sess.status, sess.animation_running(),
                sess.busy_started_at == adopted_at)

    assert _run(vloop, scenario()) == (Status.BUSY, True, True)


def test_an_anchor_the_clocks_cannot_place_is_not_used(replay, vloop,
                                                       monkeypatch):
    """Ahead of the wall clock, or older than this boot's monotonic clock
    can express: counted from the adoption instead of a wrong start."""
    r = replay
    for seconds_in in (-120.0, vloop.time() + 10):
        sess, _card = _restored(r, monkeypatch, vloop, seconds_in=seconds_in)
        sess.busy_started_at = 0.0
        _run(vloop, r.bot.recover_sessions())
        assert sess.status is Status.BUSY, seconds_in
        assert sess.busy_started_at == vloop.time(), seconds_in
        r.bot._stop_animation(sess)
        sess.status = Status.GONE
        sess.busy_started_at = 0.0


def test_an_open_prompt_is_not_painted_over(replay, vloop, monkeypatch):
    """A hook that arrived before the recovery and left the session
    INTERACTIVE owns the card (its permission prompt): not made BUSY, and
    the card is not claimed as a running turn's."""
    r = replay
    sess, _card = _restored(r, monkeypatch, vloop, status=Status.INTERACTIVE)
    _run(vloop, r.bot.recover_sessions())
    assert sess.status is Status.INTERACTIVE
    assert not sess.animation_running()
    assert sess.card_turn_seq is None


# ── the anchor in the state file ─────────────────────────────────────────

def test_the_card_clock_survives_a_save_and_load():
    reg = SessionRegistry()
    sess = reg.get_or_create("claude-x")
    sess.busy_msg_id = 77
    sess.busy_started_at = time.monotonic() - 90
    reg.save()
    again = SessionRegistry()
    again.load()
    restored = again.get("claude-x").restored_card_started_wall
    assert abs((time.time() - restored) - 90) < 2


def test_a_saved_clock_without_its_card_is_not_restored():
    from aipager import state

    reg = SessionRegistry()
    reg.get_or_create("claude-x")
    reg.save()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    data["sessions"]["claude-x"]["card_started_wall"] = time.time() - 30
    state.SESSION_STATE_FILE.write_text(json.dumps(data))
    again = SessionRegistry()
    again.load()
    assert again.get("claude-x").restored_card_started_wall == 0.0


def test_no_card_or_no_clock_saves_no_clock():
    from aipager import state

    reg = SessionRegistry()
    gone = reg.get_or_create("claude-x")
    gone.busy_started_at = time.monotonic() - 90  # its card is gone
    unclocked = reg.get_or_create("claude-y")
    unclocked.busy_msg_id = 78                     # a card, no clock
    reg.save()
    saved = json.loads(state.SESSION_STATE_FILE.read_text())["sessions"]
    assert "card_started_wall" not in saved["claude-x"]
    assert "card_started_wall" not in saved["claude-y"]


def test_an_unusable_saved_clock_restores_as_none():
    from aipager import state

    for bad in ("soon", float("inf"), time.time() + 3600, -5.0):
        reg = SessionRegistry()
        sess = reg.get_or_create("claude-x")
        sess.busy_msg_id = 77
        reg.save()
        data = json.loads(state.SESSION_STATE_FILE.read_text())
        data["sessions"]["claude-x"]["card_started_wall"] = bad
        state.SESSION_STATE_FILE.write_text(json.dumps(data))
        again = SessionRegistry()
        again.load()
        assert again.get("claude-x").restored_card_started_wall == 0.0, bad
