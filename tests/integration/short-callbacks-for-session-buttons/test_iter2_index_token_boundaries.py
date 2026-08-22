"""Iteration 2, task item #4 — the leading-space index nit from
iteration 1 (`_:sx: 0:<verb>` was wrongly ACCEPTED as index 0) is
reported fixed by a strict pattern this round. Verify that directly,
and probe its neighbours: other int()-leniencies a naive `int(token)`
parse would accept but the documented grammar
("non-negative decimal integer, no leading zeros") forbids, plus a
positive control proving the strict pattern didn't over-tighten and
start rejecting genuinely valid indices.

Exercises `session_parity.resolve_short_cb` directly (an
entrypoints.md "Exported function") for precision, and cross-checks
the full end-to-end dispatch (`_handle_callback`) for a representative
subset, matching entrypoints.md's promise: "A stale `_:sx:<idx>` (out
of range, malformed, or session gone) always answers 'That session is
no longer available' -- never silence, regardless of verb."

None of the boundary values below are derived from any constant this
suite pins (the 64-byte Telegram budget, the grammar's own digit
pattern) -- they are int()-parsing leniencies (whitespace, sign,
underscore digit-grouping, non-ASCII digit codepoints) picked because
Python's own `int()` builtin accepts every one of them, which is
exactly the class of bug this fix targets.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from aipager.bot import session_parity
from aipager.state import Status, TrackedSession

from _verbs import STALE_MESSAGE

CHAT_ID = -100


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def registered_bot(scb_bot):
    """A bot with a single real session registered at index 0."""
    bot = scb_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE,
                           scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    session_parity.session_cb(bot, CHAT_ID, sess, "menu")  # registers index 0
    return bot, sess


# ---- rejected: every int()-leniency the strict pattern must refuse -------

REJECTED_INDEX_TOKENS = [
    pytest.param(" 0", id="leading-space"),
    pytest.param("0 ", id="trailing-space"),
    pytest.param("0\t", id="trailing-tab"),
    pytest.param("0\n", id="trailing-newline"),
    pytest.param("\t0", id="leading-tab"),
    pytest.param("+0", id="explicit-plus-sign"),
    pytest.param("00", id="leading-zero-double"),
    pytest.param("0_0", id="underscore-digit-grouping"),
    pytest.param("1_0", id="underscore-digit-grouping-nonzero"),
    pytest.param("٠", id="arabic-indic-digit-zero"),        # U+0660
    pytest.param("٠0", id="arabic-indic-prefixed"),
    pytest.param("０", id="fullwidth-digit-zero"),          # U+FF10
    pytest.param("０0", id="fullwidth-prefixed"),
]


@pytest.mark.parametrize("tok", REJECTED_INDEX_TOKENS)
def test_int_leniency_token_rejected_by_resolve_short_cb(registered_bot, tok):
    bot, sess = registered_bot
    result = session_parity.resolve_short_cb(bot, CHAT_ID, "_", f"sx:{tok}:allow")
    assert result is None, (
        f"token={tok!r}: expected resolve_short_cb to reject this as "
        f"malformed (int() would accept it, the grammar does not), got "
        f"{result!r} instead of None"
    )


@pytest.mark.parametrize("tok", REJECTED_INDEX_TOKENS)
def test_int_leniency_token_rejected_end_to_end(scb_bot, helpers, tok):
    """Same probe through the full dispatcher, confirming the caller-side
    contract (a toast, never silence, never a crash) holds too, not
    just the resolver function in isolation."""
    bot = scb_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE,
                           scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    session_parity.session_cb(bot, CHAT_ID, sess, "menu")
    cb_upd, q = helpers.make_callback_update(f"_:sx:{tok}:allow", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert q.answer.await_args is not None, f"token={tok!r}: silence"
    assert q.answer.await_args.args[0] == STALE_MESSAGE, (
        f"token={tok!r}: expected {STALE_MESSAGE!r}, got {q.answer.await_args!r}"
    )


# ---- a very long, syntactically-valid digit string: no crash, treated ----
# ---- as (legitimately) out of range, not specially accepted --------------

def test_very_long_digit_string_is_out_of_range_not_a_crash(registered_bot):
    bot, sess = registered_bot
    huge = "1" + "0" * 40  # 41 digits, no leading zero -- grammar-valid shape
    result = session_parity.resolve_short_cb(bot, CHAT_ID, "_", f"sx:{huge}:allow")
    assert result is None, (
        f"a 41-digit index with no chat table anywhere near that size should "
        f"resolve to None (out of range), got {result!r}"
    )


def test_very_long_digit_string_end_to_end_answers_stale_not_crash(scb_bot, helpers):
    bot = scb_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE,
                           scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    session_parity.session_cb(bot, CHAT_ID, sess, "menu")
    huge = "9" * 60
    cb_upd, q = helpers.make_callback_update(f"_:sx:{huge}:allow", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert q.answer.await_args is not None
    assert q.answer.await_args.args[0] == STALE_MESSAGE


# ---- positive control: the strict pattern must not have over-tightened ---

def test_bare_zero_still_resolves(registered_bot):
    bot, sess = registered_bot
    result = session_parity.resolve_short_cb(bot, CHAT_ID, "_", "sx:0:allow")
    assert result == (sess.name, "allow"), (
        f"a plain, grammar-conformant '0' must still resolve; got {result!r}"
    )


def test_a_larger_genuinely_registered_index_still_resolves(scb_bot):
    bot = scb_bot()
    sessions = []
    for i in range(50):
        s = TrackedSession(name=f"claude-s{i}", label=f"s{i}", status=Status.IDLE,
                            scope_chat_id=CHAT_ID)
        bot.registry._sessions[s.name] = s
        session_parity.session_cb(bot, CHAT_ID, s, "menu")
        sessions.append(s)
    target = sessions[37]
    result = session_parity.resolve_short_cb(bot, CHAT_ID, "_", "sx:37:stop")
    assert result == (target.name, "stop"), (
        f"a genuinely registered double-digit index must still resolve; got {result!r}"
    )
