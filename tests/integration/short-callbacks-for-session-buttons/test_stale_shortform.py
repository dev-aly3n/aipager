"""Task instruction #4 — stale short form.

entrypoints.md: "A stale `_:sx:<idx>` (out of range, malformed, or
session gone) always answers 'That session is no longer available' --
never silence, regardless of verb."

Four equivalence classes for the index itself (out-of-range,
non-numeric, negative, leading-zero, empty -- boundary-value analysis
on the documented grammar "non-negative decimal integer, no leading
zeros"), each swept across EVERY documented session-scoped verb, not
just the ones the ⋮-menu family already covered before this ship.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from aipager.bot import session_parity
from aipager.state import Status, TrackedSession

from _verbs import ALL_SESSION_SCOPED_VERBS, STALE_MESSAGE

CHAT_ID = -100


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _assert_stale(q, verb, idx_repr):
    assert q.answer.await_args is not None, (
        f"verb={verb!r} idx={idx_repr}: expected {STALE_MESSAGE!r}, got no "
        f"answer() call (silence)"
    )
    assert q.answer.await_args.args[0] == STALE_MESSAGE, (
        f"verb={verb!r} idx={idx_repr}: expected {STALE_MESSAGE!r}, got "
        f"{q.answer.await_args!r}"
    )


# ---- out-of-range: a syntactically valid index that was never --------
# ---- registered in this chat's table -----------------------------------

@pytest.mark.parametrize("verb", ALL_SESSION_SCOPED_VERBS)
def test_out_of_range_index_answers_stale_for_every_verb(scb_bot, helpers, verb):
    bot = scb_bot()
    # No session ever registered in this chat -> index 0 is out of range.
    cb_upd, q = helpers.make_callback_update(f"_:sx:0:{verb}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    _assert_stale(q, verb, "0 (never registered)")


@pytest.mark.parametrize("verb", ALL_SESSION_SCOPED_VERBS)
def test_far_out_of_range_index_answers_stale_for_every_verb(scb_bot, helpers, verb):
    bot = scb_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE,
                           scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    session_parity.session_cb(bot, CHAT_ID, sess, "menu")  # registers index 0 only
    cb_upd, q = helpers.make_callback_update(f"_:sx:999999:{verb}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    _assert_stale(q, verb, "999999 (far beyond the one registered entry)")


# ---- malformed index: non-numeric, negative, leading zeros, empty ------

@pytest.mark.parametrize("verb", ["allow", "stop", "kill", "resume", "opt0", "new_resume"])
@pytest.mark.parametrize("malformed_idx", ["abc", "-1", "007", "", "1.5", "0x1"])
def test_malformed_index_answers_stale(scb_bot, helpers, verb, malformed_idx):
    bot = scb_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE,
                           scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    session_parity.session_cb(bot, CHAT_ID, sess, "menu")
    cb_upd, q = helpers.make_callback_update(
        f"_:sx:{malformed_idx}:{verb}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    _assert_stale(q, verb, repr(malformed_idx))


# ---- session gone: index was valid, session existed, then vanished -----
# ---- (registry mutated between render and tap) --------------------------

@pytest.mark.parametrize("verb", ALL_SESSION_SCOPED_VERBS)
def test_index_valid_but_session_removed_answers_stale(scb_bot, helpers, verb):
    bot = scb_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE,
                           scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    cb = session_parity.session_cb(bot, CHAT_ID, sess, "menu")
    idx = cb.split(":")[2]
    # The session is removed from the registry after the button was
    # rendered (e.g. killed, or cleared) but the per-chat index table
    # (append-only, in-memory) still has the old entry pointing at a
    # name the registry no longer knows.
    del bot.registry._sessions[sess.name]

    cb_upd, q = helpers.make_callback_update(f"_:sx:{idx}:{verb}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    _assert_stale(q, verb, f"{idx} (session removed after render)")
