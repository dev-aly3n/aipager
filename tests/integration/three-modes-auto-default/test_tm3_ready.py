"""The /new Ready card's mode buttons (criteria 5, 7, 18): in-place
Ask <-> Auto from the card, allowed while busy, refused while a dialog is
open with nothing written, and Full access only for the admin who started
the session."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager import preferences
from aipager.state import SessionRegistry, Status

from _tm3 import (BACKTAB, DM_CHAT, OWNER, answers, dm_bot, quiet_restart, settle_tasks,
                  tap, typed)


def _ready(mk_bot, run_async, *, admin=True, default=None):
    bot = dm_bot(mk_bot, SessionRegistry(), admin=admin)
    if default:
        preferences.set_new_session_default(DM_CHAT, "mode", default)
    u = typed("/new x1", chat_id=DM_CHAT, user_id=OWNER)

    async def _go():
        await bot._handle_new_cmd(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    sess = next(s for s in bot.registry.all_sessions().values() if s.label == "x1")
    sess.status = Status.IDLE
    edits = bot._app.bot.edit_message_text.await_args_list
    card = edits[-1].kwargs
    btns = [(b.text, b.callback_data) for row in card["reply_markup"].inline_keyboard
            for b in row]
    return bot, sess, card, btns


def _tap_verb(bot, run_async, btns, card, verb):
    cb = next(cb for _t, cb in btns if cb and cb.endswith(verb))
    u, q = tap(cb, chat_id=DM_CHAT, user_id=OWNER, msg_id=card.get("message_id", 50))

    async def _go():
        await bot._handle_callback(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    return q


def test_ready_card_shows_auto(mk_bot, run_async, pty):
    _b, _s, card, _btns = _ready(mk_bot, run_async)
    assert "🤖 Auto mode" in card["text"]


def test_ready_card_offers_ask_and_full_to_an_admin(mk_bot, run_async, pty):
    _b, _s, _c, btns = _ready(mk_bot, run_async)
    verbs = sorted(cb.rsplit(":", 1)[-1] for _t, cb in btns if cb and "rdy_" in cb
                   and cb.rsplit(":", 1)[-1] in ("rdy_ask", "rdy_auto", "rdy_full"))
    assert verbs == ["rdy_ask", "rdy_full"]


def test_ready_card_offers_no_full_access_to_a_non_admin(mk_bot, run_async, pty):
    _b, _s, _c, btns = _ready(mk_bot, run_async, admin=False)
    assert not any(cb and cb.endswith("rdy_full") for _t, cb in btns)


def test_ready_card_auto_to_ask_presses_once(mk_bot, run_async, pty):
    bot, _s, card, btns = _ready(mk_bot, run_async)
    _tap_verb(bot, run_async, btns, card, "rdy_ask")
    assert (pty.presses, pty.killed) == ([BACKTAB], [])


def test_ready_card_ask_to_auto_presses_three(mk_bot, run_async, pty):
    bot, _s, card, btns = _ready(mk_bot, run_async, default="ask")
    _tap_verb(bot, run_async, btns, card, "rdy_auto")
    assert pty.presses == [BACKTAB] * 3


def test_ready_card_switch_while_busy_presses(mk_bot, run_async, pty):
    bot, sess, card, btns = _ready(mk_bot, run_async)
    sess.status = Status.BUSY
    _tap_verb(bot, run_async, btns, card, "rdy_ask")
    assert pty.presses == [BACKTAB]


def test_ready_card_switch_while_a_dialog_is_open_writes_nothing(mk_bot, run_async, pty):
    bot, sess, card, btns = _ready(mk_bot, run_async)
    sess.status = Status.INTERACTIVE
    _tap_verb(bot, run_async, btns, card, "rdy_ask")
    assert pty.runs == []


def test_ready_card_switch_while_a_dialog_is_open_answers(mk_bot, run_async, pty):
    bot, sess, card, btns = _ready(mk_bot, run_async)
    sess.status = Status.INTERACTIVE
    q = _tap_verb(bot, run_async, btns, card, "rdy_ask")
    assert any(a and a != "None" for a in answers(q)), answers(q)


def test_ready_card_full_access_relaunches_in_full(mk_bot, run_async, pty, monkeypatch):
    quiet_restart(monkeypatch)
    bot, sess, card, btns = _ready(mk_bot, run_async)
    sess.claude_session_id = "UUID-1"
    _tap_verb(bot, run_async, btns, card, "rdy_full")
    assert pty.launched_modes[-1:] == ["full"]


@pytest.mark.parametrize("admin", [False], ids=["non-admin"])
def test_ready_card_forged_full_tap_by_a_non_admin_changes_nothing(mk_bot, run_async, pty,
                                                                   admin):
    bot, sess, card, _btns = _ready(mk_bot, run_async, admin=admin)
    launched_before = list(pty.launched)
    u, q = tap(f"{sess.name}:rdy_full", chat_id=DM_CHAT, user_id=OWNER,
               msg_id=card.get("message_id", 50))
    run_async(bot._handle_callback(u, MagicMock()))
    assert (pty.launched, pty.killed, sess.perm_mode) == (launched_before, [], "auto")
