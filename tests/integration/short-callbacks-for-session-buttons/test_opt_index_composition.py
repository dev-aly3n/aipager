"""Task instruction #5 — `opt<n>` composition, the nested index.

entrypoints.md: "<verb> is one of the verbs below; for options it is
`opt<n>` with `n` a single digit `0`-`3`."

Boundary-value analysis on `n`: just-inside (0 and 3), and outside the
documented bound (4+, negative, non-digit) -- checked for both (a) the
byte budget composition (does embedding `opt<n>` inside the already
index-carrying `_:sx:<idx>:opt<n>` ever reintroduce the overflow
design.md explicitly worries about: "check the composition does not
reintroduce the overflow") and (b) actual dispatch behaviour for `n`
outside the documented 0-3 range, which must not crash even though
it's outside the contract's own stated domain.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from aipager.bot import session_parity
from aipager.state import Status, TrackedSession

from _verbs import TELEGRAM_CALLBACK_DATA_LIMIT

CHAT_ID = -100


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- byte-budget composition: opt<n> at the documented in-bound values,---
# ---- against a 64-byte session name and a large index --------------------

@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_opt_n_in_documented_bound_fits_budget_at_max_name_and_large_index(
        scb_bot, n):
    bot = scb_bot()
    for i in range(400):
        filler = TrackedSession(name=f"claude-f{i}__d{i:010d}", label=f"f{i}",
                                 status=Status.IDLE, scope_chat_id=CHAT_ID)
        session_parity.session_cb(bot, CHAT_ID, filler, "menu")
    sess = TrackedSession(name="a" * 64, label="x", status=Status.BUSY,
                           scope_chat_id=CHAT_ID)
    cb = session_parity.session_cb(bot, CHAT_ID, sess, f"opt{n}")
    assert len(cb.encode("utf-8")) <= TELEGRAM_CALLBACK_DATA_LIMIT
    assert cb.endswith(f":opt{n}")


# ---- outside the documented bound: what happens -------------------------

@pytest.mark.parametrize("n_repr", ["4", "9", "99", "-1", "a", ""])
def test_opt_n_outside_documented_bound_does_not_crash(scb_bot, helpers, n_repr):
    """`n` outside 0-3 is outside the grammar's own documented domain --
    no promise is made about the exact wording of the response, only
    (per the shared "never silence, never a crash" contract this whole
    grammar is built on) that the bot answers SOMETHING rather than
    raising or hanging."""
    bot = scb_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY,
                           scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    cb = session_parity.session_cb(bot, CHAT_ID, sess, "menu")
    idx = cb.split(":")[2]

    cb_upd, q = helpers.make_callback_update(
        f"_:sx:{idx}:opt{n_repr}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))  # must not raise
    assert q.answer.await_args is not None, (
        f"opt{n_repr}: expected some acknowledgement, got silence"
    )


def test_opt_n_at_the_documented_upper_bound_reaches_a_real_option(
        scb_bot, helpers):
    """n=3 (the documented upper bound, "just inside") must behave like
    a normal, working option pick -- not merely "doesn't crash"."""
    bot = scb_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY,
                           scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    options = [{"label": "Red"}, {"label": "Green"}, {"label": "Blue"}, {"label": "Yellow"}]
    kb = bot._build_inline_ask_keyboard(sess, options, multi_select=False)
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    opt3_cb = next(cb for cb in cbs if cb.endswith(":opt3"))

    cb_upd, q = helpers.make_callback_update(opt3_cb, chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    assert q.answer.await_args is not None
    # Must not be the stale/malformed fallback -- opt3 for a real,
    # freshly rendered 4-option keyboard is a legitimate pick.
    assert q.answer.await_args.args[0] != "That session is no longer available"
