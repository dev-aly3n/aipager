"""Black-box integration test for design.md success criterion 6 -- the
highest-consequence behaviour in this feature per the task brief.

The policy snapshot is rewritten on every turn. If a plain (non-reply)
message after a reply-with-pointer fails to clear ``reply_context``,
Claude keeps believing the user is pointing at an old message on every
subsequent turn. This drives TWO real, sequential ``_handle_message``
calls against the SAME session and reads the snapshot back after each
one -- proving the actual handler wiring clears it, not merely that
``write_snapshot``'s own default is empty (already unit-tested
elsewhere at the ``policy_snapshot`` layer, which cannot prove
``_handle_message`` actually calls it with an empty value on the
non-reply turn).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.state import Status, TrackedSession

CHAT_ID = -1001
BOT_ID = 87654321


@pytest.fixture(autouse=True)
def _isolate_snapshot_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")


def _ctx():
    c = MagicMock()
    c.bot.id = BOT_ID
    return c


def _wire_happy_dtach(monkeypatch, bot):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()


def _reply_target(*, message_id, text="(old text)"):
    m = MagicMock()
    m.message_id = message_id
    m.text = text
    m.caption = None
    m.from_user = None
    return m


def test_plain_message_immediately_after_a_reply_clears_stale_reply_context(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """THE mutation target: deleting/breaking the ``reply_context=""``
    default (or otherwise forgetting to pass an empty value through on
    a non-reply turn) must make THIS test fail."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(1, sess.name, CHAT_ID)   # the older message
    bot.registry.track_message(999, sess.name, CHAT_ID)  # establishes "latest"
    _wire_happy_dtach(monkeypatch, bot)

    # Turn 1: reply to the OLDER message (1, not the latest 999) -- must
    # produce a non-empty pointer.
    turn1 = mk_update("what did you mean here?", chat_id=CHAT_ID, message_id=2000)
    turn1.message.reply_to_message = _reply_target(message_id=1, text="the old thing")
    run_async(bot._handle_message(turn1, _ctx()))

    snap1 = ps.read_snapshot(sess.name)
    assert snap1 is not None
    assert snap1["reply_context"] != "", "precondition failed: turn 1 produced no pointer"

    # Simulate the turn completing (the daemon transitions the session
    # back to IDLE once Claude finishes and is ready for the next prompt).
    sess.status = Status.IDLE

    # Turn 2: an ordinary, non-reply follow-up message.
    turn2 = mk_update("ok thanks, moving on", chat_id=CHAT_ID, message_id=2001)
    assert turn2.message.reply_to_message is None
    run_async(bot._handle_message(turn2, _ctx()))

    snap2 = ps.read_snapshot(sess.name)
    assert snap2 is not None
    assert snap2["reply_context"] == "", (
        "a plain message immediately after a reply left a stale "
        "reply_context in the snapshot -- Claude would keep believing "
        "the user is pointing at the old message on this and every "
        "subsequent turn"
    )


def test_staleness_guard_holds_across_repeated_reply_then_plain_cycles(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Runs the reply-then-plain cycle twice in a row to catch a guard
    that only works "once" (e.g. a one-shot clear flag) rather than
    being unconditional on every non-reply turn."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.scope_chat_id = CHAT_ID
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(1, sess.name, CHAT_ID)
    bot.registry.track_message(999, sess.name, CHAT_ID)
    _wire_happy_dtach(monkeypatch, bot)

    for cycle in range(2):
        reply_update = mk_update(f"cycle {cycle} reply", chat_id=CHAT_ID,
                                 message_id=3000 + cycle * 2)
        reply_update.message.reply_to_message = _reply_target(message_id=1, text="old")
        run_async(bot._handle_message(reply_update, _ctx()))
        assert ps.read_snapshot(sess.name)["reply_context"] != "", f"cycle {cycle} setup failed"
        sess.status = Status.IDLE

        plain_update = mk_update(f"cycle {cycle} plain", chat_id=CHAT_ID,
                                 message_id=3001 + cycle * 2)
        run_async(bot._handle_message(plain_update, _ctx()))
        assert ps.read_snapshot(sess.name)["reply_context"] == "", (
            f"cycle {cycle}: stale reply_context survived a plain message"
        )
        sess.status = Status.IDLE
