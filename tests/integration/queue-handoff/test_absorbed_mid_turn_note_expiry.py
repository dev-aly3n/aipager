"""intent.md requirement 2: a message injected into an already-BUSY
session (design.md "Every inbound message injects immediately now,
regardless of Status.BUSY") can be silently absorbed into the turn
already running — Claude Code decides per turn whether to fold it into
the current generation or auto-submit it as a fresh prompt once that
turn ends, and only the latter produces a ``UserPromptSubmit`` the
normal ``notify_hook._match_and_promote`` pick-up path can match. When
it's absorbed instead, nothing ever consumes the note it wrote at
inject time, and it used to sit until the (24h) queue TTL — the
mechanism this file exercises (``hook_receiver``'s Stop-triggered
``policy_snapshot.expire_notes_after_turn_end``) is what makes that
bound "the turn that absorbed it", not "a day later".

Full-stack, hook-datagram level (same pattern as
``tests/integration/busy-card-agent-rows/test_attribution.py``'s
end-to-end test): a real ``TelegramBot._handle_message`` call performs
the mid-turn injection, and a real ``HookReceiver._on_datagram`` call
(sharing the same registry + ``bot.notify``) delivers the session's
``Stop`` hook event that should trigger the sweep.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status

CHAT_ID = -1001
NAME = "claude-x"
USER_A = 12345


@pytest.fixture
def stack(mk_bot, monkeypatch):
    """A (bot, sess, recv) sharing one registry, wired the same way the
    ``wired`` fixture wires a bare ``TelegramBot`` — plus a real
    ``HookReceiver`` bound to the same ``bot.notify``, since this file
    needs to drive both the injecting side and the Stop side of the
    same session.
    """
    registry = SessionRegistry()
    bot = mk_bot(registry)
    bot._app.bot = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()

    async def _send_text_and_enter(name, body):
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        _send_text_and_enter)

    sess = registry.get_or_create(NAME)
    sess.label = "x"
    registry.last_active_session = NAME

    recv = hr.HookReceiver(registry, bot.notify)
    return bot, sess, recv


def _update(mk_update, text, message_id, user_id=USER_A, chat_id=CHAT_ID):
    return mk_update(text, message_id=message_id, user_id=user_id,
                      chat_id=chat_id)


def _notes_dir_has_entries():
    d = ps.notes_dir(NAME)
    return d.exists() and any(p for p in d.iterdir() if p.suffix == ".json")


def _send_stop(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def test_note_from_a_mid_turn_injection_is_gone_after_the_turns_stop(
    stack, mk_update, run_async, monkeypatch,
):
    bot, sess, recv = stack
    monkeypatch.setattr(ps, "MID_TURN_NOTE_GRACE_SECONDS", 0)

    bot.registry.transition(NAME, Status.BUSY)  # a turn is already running
    run_async(bot._handle_message(
        _update(mk_update, "ok. now test it again", 1), MagicMock()))

    assert _notes_dir_has_entries(), (
        "setup assumption broke: the mid-turn injection wrote no note")

    # The turn that absorbed the message ends — its own Stop hook fires.
    _send_stop(recv, run_async, hook_event_name="Stop", session=NAME,
              last_assistant_message="All done.")

    assert not _notes_dir_has_entries(), (
        "the mid-turn message's note outlived the turn that absorbed it "
        "— it would otherwise sit until the queue TTL, exactly the live "
        "incident this sweep fixes")


def test_note_from_an_idle_start_is_also_gone_after_its_own_stop(
    stack, mk_update, run_async, monkeypatch,
):
    """Positive control: an ordinary (not mid-turn) turn's own note is
    swept too if somehow never matched — proves the sweep isn't
    somehow a no-op in the ordinary case, only relevant for the
    mid-turn scenario above."""
    bot, sess, recv = stack
    monkeypatch.setattr(ps, "MID_TURN_NOTE_GRACE_SECONDS", 0)

    bot.registry.transition(NAME, Status.IDLE)
    run_async(bot._handle_message(
        _update(mk_update, "hello", 1), MagicMock()))
    assert _notes_dir_has_entries()

    _send_stop(recv, run_async, hook_event_name="Stop", session=NAME,
              last_assistant_message="Hi!")

    assert not _notes_dir_has_entries()


def _note_bodies():
    d = ps.notes_dir(NAME)
    if not d.exists():
        return set()
    out = set()
    for p in d.iterdir():
        if p.suffix != ".json":
            continue
        out.add(json.loads(p.read_text()).get("body"))
    return out


def test_a_zero_grace_sweep_still_lets_a_same_event_drain_note_survive(
    stack, mk_update, run_async, monkeypatch,
):
    """The pre-notify snapshot ordering, with a genuinely non-empty
    snapshot (so the "nothing was outstanding" early-exit can't hide a
    broken sweep): a mid-turn-absorbed note from THIS turn is
    outstanding when Stop fires, and — as part of the very same
    Stop/idle_prompt handling — a DIFFERENT, held message drains into a
    fresh prompt, writing a brand-new note. The sweep armed by this
    Stop must remove only the pre-existing (absorbed) note and must
    never touch the freshly drained one, even with the grace window
    collapsed to 0."""
    bot, sess, recv = stack
    monkeypatch.setattr(ps, "MID_TURN_NOTE_GRACE_SECONDS", 0)

    bot.registry.transition(NAME, Status.BUSY)
    run_async(bot._handle_message(
        _update(mk_update, "absorbed leftover", 1, user_id=USER_A),
        MagicMock()))
    assert _note_bodies() == {"absorbed leftover"}, "setup assumption broke"

    # A different sender's message is held (mixed-sender hold) rather
    # than injected — nothing new on disk yet.
    run_async(bot._handle_message(
        _update(mk_update, "please continue", 2, user_id=USER_A + 1),
        MagicMock()))
    assert sess.pending_queue, "setup assumption broke: nothing was queued"
    assert _note_bodies() == {"absorbed leftover"}, (
        "a held (not yet injected) message must not have written a note")

    # This turn ends — Stop fires. Its own snapshot is {"absorbed
    # leftover"} (non-empty), and its notify_fn drains the held message
    # into a fresh prompt, writing a brand-new note for it.
    _send_stop(recv, run_async, hook_event_name="Stop", session=NAME,
              last_assistant_message="Done.")

    assert _note_bodies() == {"please continue"}, (
        f"expected only the freshly drained note to survive, got "
        f"{_note_bodies()}")
