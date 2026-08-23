"""entrypoints.md "Side effects (observable) > Reactions":

- 👀 set when aipager sends the message into the pty OR when a message
  is genuinely held (dialog-open or mixed-sender).
- 👍 set once the hook confirms this specific message contributed to a
  turn — assert on the resulting reaction, not on internal matching
  functions.
- No reaction change on TTL expiry.

Black-box boundary: ``bot.notify(sess, "queue_pickup", {"consumed": [...],
"expired": [...]})`` is the documented call shape between the hook
receiver and the bot (mirrors the pre-existing, non-queue-handoff
``bot.notify(sess, "idle_prompt", ...)`` pattern already used for
releasing held messages in ``tests/test_hold_prompt_during_open_dialog.py``).
Reactions are observed through the mocked
``bot._app.bot.set_message_reaction`` boundary, per entrypoints.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-x"


def _in_state(bot, sess, status, *, permission=None):
    bot.registry.transition(NAME, status)
    sess.pending_permission = permission


def _update(mk_update, text, message_id, user_id=12345, chat_id=CHAT_ID):
    return mk_update(text, message_id=message_id, user_id=user_id,
                      chat_id=chat_id)


async def _send_text(bot, update):
    await bot._handle_message(update, MagicMock())


def _reaction_calls(bot):
    return bot._app.bot.set_message_reaction.await_args_list


# ---- 👀 on send (both injected and genuinely-held cases) ----------------

def test_injected_message_gets_watched_reaction(
        wired_reactions, mk_update, run_async):
    bot, sess, injected, keys = wired_reactions
    _in_state(bot, sess, Status.IDLE)

    run_async(_send_text(bot, _update(mk_update, "hi", 1)))

    calls = _reaction_calls(bot)
    assert any(c.args == (CHAT_ID, 1, "👀") for c in calls), (
        f"no 👀 reaction on an injected message; calls={calls}")


def test_held_interactive_message_still_gets_watched_reaction(
        wired_reactions, mk_update, run_async):
    """Held is not the same as ignored — the operator still sees 👀."""
    bot, sess, injected, keys = wired_reactions
    _in_state(bot, sess, Status.INTERACTIVE,
              permission={"tool_summary": "Bash: rm -rf /"})

    run_async(_send_text(bot, _update(mk_update, "held one", 2)))

    calls = _reaction_calls(bot)
    assert any(c.args == (CHAT_ID, 2, "👀") for c in calls), (
        f"a held (dialog-open) message never got 👀; calls={calls}")


def test_held_mixed_sender_message_still_gets_watched_reaction(
        wired_reactions, mk_update, run_async):
    bot, sess, injected, keys = wired_reactions
    _in_state(bot, sess, Status.IDLE)
    run_async(_send_text(bot, _update(mk_update, "from A", 1, user_id=111)))
    bot._app.bot.set_message_reaction.reset_mock()

    run_async(_send_text(bot, _update(mk_update, "from B", 2, user_id=222)))

    calls = _reaction_calls(bot)
    assert any(c.args == (CHAT_ID, 2, "👀") for c in calls), (
        f"a held (mixed-sender) message never got 👀; calls={calls}")


# ---- 👍 only on confirmed pick-up ----------------------------------------

def test_thumbs_up_never_set_before_any_pickup_confirmation(
        wired_reactions, mk_update, run_async):
    """Sending alone (👀) must never also produce a 👍 — that glyph is
    reserved for hook confirmation."""
    bot, sess, injected, keys = wired_reactions
    _in_state(bot, sess, Status.IDLE)

    run_async(_send_text(bot, _update(mk_update, "hi", 1)))

    calls = _reaction_calls(bot)
    assert not any(c.args[2] == "👍" for c in calls), (
        f"👍 appeared without any pick-up confirmation; calls={calls}")


def test_thumbs_up_set_on_every_consumed_message_in_a_run(
        wired_reactions, run_async):
    """Boundary: a run of several (n=3) consumed notes — every one gets
    👍, not just the first or last."""
    bot, sess, injected, keys = wired_reactions

    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [
            {"msg_id": 10, "chat_id": CHAT_ID, "raw_text": "a"},
            {"msg_id": 11, "chat_id": CHAT_ID, "raw_text": "b"},
            {"msg_id": 12, "chat_id": CHAT_ID, "raw_text": "c"},
        ],
        "expired": [],
    }))

    calls = _reaction_calls(bot)
    thumbs = [c.args for c in calls if c.args[2] == "👍"]
    assert set(thumbs) == {
        (CHAT_ID, 10, "👍"), (CHAT_ID, 11, "👍"), (CHAT_ID, 12, "👍"),
    }, f"not every consumed message in the run got 👍: {thumbs}"


def test_thumbs_up_set_on_a_single_consumed_note(wired_reactions, run_async):
    """Boundary n=1."""
    bot, sess, injected, keys = wired_reactions

    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [{"msg_id": 42, "chat_id": CHAT_ID, "raw_text": "solo"}],
        "expired": [],
    }))

    calls = _reaction_calls(bot)
    assert any(c.args == (CHAT_ID, 42, "👍") for c in calls)


def test_zero_consumed_zero_expired_is_a_true_noop(wired_reactions, run_async):
    """Boundary n=0: neither list populated must produce no reaction
    activity at all."""
    bot, sess, injected, keys = wired_reactions

    run_async(bot.notify(sess, "queue_pickup", {"consumed": [], "expired": []}))

    assert _reaction_calls(bot) == []


# ---- TTL expiry: deliberately no glyph change ----------------------------

def test_ttl_expired_message_gets_no_reaction_change(wired_reactions, run_async):
    """entrypoints.md: "No reaction change on TTL expiry — deliberately
    left at 👀 rather than flipped to a failure glyph." An expired-only
    pick-up must produce zero calls to set_message_reaction."""
    bot, sess, injected, keys = wired_reactions

    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [],
        "expired": [{"msg_id": 5, "chat_id": CHAT_ID, "raw_text": "stale"}],
    }))

    calls = _reaction_calls(bot)
    assert calls == [], (
        f"TTL expiry must not touch the reaction at all; calls={calls}")


def test_partial_run_only_the_consumed_half_gets_thumbs_up(
        wired_reactions, run_async):
    """Boundary: a partial run — 2 consumed, 1 expired, in the SAME
    pick-up. The expired member must never appear in a reaction call
    (positive AND negative assertion on the same batch)."""
    bot, sess, injected, keys = wired_reactions

    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [
            {"msg_id": 20, "chat_id": CHAT_ID, "raw_text": "kept-1"},
            {"msg_id": 21, "chat_id": CHAT_ID, "raw_text": "kept-2"},
        ],
        "expired": [{"msg_id": 19, "chat_id": CHAT_ID, "raw_text": "dropped"}],
    }))

    calls = _reaction_calls(bot)
    reacted_ids = {c.args[1] for c in calls}
    assert reacted_ids == {20, 21}, (
        f"the expired member of a mixed batch was reacted to, or a "
        f"consumed one was skipped: reacted_ids={reacted_ids}")
    assert all(c.args[2] == "👍" for c in calls)


# ---- full round trip: send (👀) then confirm (👍) on the SAME message ----

def test_full_round_trip_watched_then_thumbs_up_on_the_same_message(
        wired_reactions, mk_update, run_async):
    """The whole visible point of this feature (intent.md): sent-versus-
    picked-up must be distinguishable on ONE message across its two
    lifecycle events, not just across two independently-fabricated
    contexts."""
    bot, sess, injected, keys = wired_reactions
    _in_state(bot, sess, Status.IDLE)

    update = _update(mk_update, "watch me", 77)
    run_async(_send_text(bot, update))

    watched_calls = [c for c in _reaction_calls(bot) if c.args == (CHAT_ID, 77, "👀")]
    assert watched_calls, "the sent message never got 👀"
    thumbs_so_far = [c for c in _reaction_calls(bot) if c.args[2] == "👍"]
    assert thumbs_so_far == [], "👍 appeared before any pick-up confirmation"

    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [{"msg_id": 77, "chat_id": CHAT_ID, "raw_text": "watch me"}],
        "expired": [],
    }))

    calls = _reaction_calls(bot)
    assert any(c.args == (CHAT_ID, 77, "👍") for c in calls), (
        f"the confirmed message never got 👍; calls={calls}")
    # Ordering: the 👀 call must precede the 👍 call for this message.
    idx_watched = calls.index(watched_calls[0])
    idx_thumb = next(i for i, c in enumerate(calls) if c.args == (CHAT_ID, 77, "👍"))
    assert idx_watched < idx_thumb, "👍 was recorded before 👀 for the same message"
