"""Priority: the per-chat session index table
(``bot._session_pref_index[chat_id]``) that every session-scoped button
(`⋮`, Restart/Rename/Delete confirm, and `_:spref` preferences) now
addresses through, under session churn.

entrypoints.md's own promise for the (structurally identical)
`_:spref:<idx>` scheme is the contract every session-scoped callback
must meet: "a stale index after a session is killed/renamed mid-view
fails closed ... rather than silently writing to the wrong session."
``tests/integration/sync-commands-with-mini-app/test_stale_spref_fails_closed.py``
already exercises that promise exhaustively for `_:spref`. This file
exercises the SAME underlying table through the OTHER surfaces that
now share it — `/status`'s ⋮ row and the ⋮ menu's own Restart/Rename/
Delete/Diff rows — with a particular focus on the risk `_:spref`'s own
tests don't cover: the table is a SINGLE per-chat slot
(``bot._session_pref_index[chat_id]``, not per-message), so any render
of ANY session-scoped picker/menu for this chat can rebuild it. If that
rebuild ever reassigns an index a stale button is still holding to a
DIFFERENT session, tapping the stale button would silently act on the
wrong session — the sharpest possible violation of the "never silently
writing to the wrong session" promise.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from aipager.bot import session_parity
from aipager.state import Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _alive(name):
    return True


def _status(bot, upd):
    """``/status`` only renders a ⋮ row for sessions ``inject.is_alive``
    reports as alive — the real dtach socket, mocked here per the
    external-boundary mandate (never a real socket in these tests)."""
    with patch("aipager.dtach.inject.is_alive", side_effect=_alive):
        _run(bot._handle_status(upd, MagicMock()))


CHAT = -100


def _two_session_bot(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    sess_a = TrackedSession(name="claude-alpha__g100", label="alpha",
                             status=Status.IDLE, scope_chat_id=CHAT)
    sess_b = TrackedSession(name="claude-beta__g100", label="beta",
                             status=Status.IDLE, scope_chat_id=CHAT)
    bot.registry._sessions[sess_a.name] = sess_a
    bot.registry._sessions[sess_b.name] = sess_b
    return bot, sess_a, sess_b


def _status_menu_cb_for(helpers, bot, chat_id, cbs, sess_name):
    """The raw ``:menu``-suffixed callback_data whose destination is
    ``sess_name``, as actually rendered by ``/status``."""
    menu_cbs = [c for c in cbs if helpers.destinations(bot, chat_id, [c])[0][1] == "menu"]
    for c in menu_cbs:
        dest, verb = helpers.destinations(bot, chat_id, [c])[0]
        if dest == sess_name:
            return c
    raise AssertionError(f"no :menu callback resolves to {sess_name}: {cbs}")


# --------------------------------------------------------------------------- #
# Simple case: the session a stale button pointed to was removed mid-view.  #
# --------------------------------------------------------------------------- #

def test_status_menu_button_for_a_session_removed_mid_view_fails_closed(
        mk_bot, helpers):
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    upd = helpers.make_message_update(
        "/status", chat_id=CHAT, chat_type="group")
    _status(bot, upd)
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    cb_a = _status_menu_cb_for(helpers, bot, CHAT, cbs, sess_a.name)

    del bot.registry._sessions[sess_a.name]  # A dies mid-view

    cb_upd, q = helpers.make_callback_update(
        cb_a, chat_id=CHAT, chat_type="group", message_id=1)
    _run(bot._handle_callback(cb_upd, MagicMock()))  # must not raise

    # B must be completely untouched by resolving A's now-dead index.
    assert bot.registry._sessions[sess_b.name] is sess_b
    assert sess_b.status == Status.IDLE
    # If anything was rendered at all, it must not be B's menu — a stale
    # index resolving to A must never silently render B instead.
    if q.edit_message_text.await_args_list:
        rendered = q.edit_message_text.await_args.args[0] if q.edit_message_text.await_args.args \
            else q.edit_message_text.await_args.kwargs.get("text", "")
        assert "beta" not in (rendered or "").lower(), (
            f"a stale button for the removed session 'alpha' rendered "
            f"session 'beta' instead: {rendered!r}"
        )


# --------------------------------------------------------------------------- #
# The sharpest version: does registering ONE session's own menu (which     #
# shares the same per-chat table) corrupt an EARLIER, still-valid button   #
# for a DIFFERENT session captured before that render?                     #
# --------------------------------------------------------------------------- #

def test_opening_one_sessions_menu_does_not_redirect_an_earlier_buttons_destination(
        mk_bot, helpers):
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    upd = helpers.make_message_update(
        "/status", chat_id=CHAT, chat_type="group")
    _status(bot, upd)
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    cb_a = _status_menu_cb_for(helpers, bot, CHAT, cbs, sess_a.name)
    cb_b = _status_menu_cb_for(helpers, bot, CHAT, cbs, sess_b.name)

    # Sanity: right now, cb_a really does resolve to alpha.
    assert helpers.destinations(bot, CHAT, [cb_a])[0][0] == sess_a.name

    # Someone opens B's own ⋮ menu — this independently registers/
    # touches the SAME shared per-chat index table.
    cb_upd_b, q_b = helpers.make_callback_update(
        cb_b, chat_id=CHAT, chat_type="group", message_id=1)
    _run(bot._handle_callback(cb_upd_b, MagicMock()))

    # The EARLIER button captured for A, unchanged bytes, is tapped now.
    cb_upd_a, q_a = helpers.make_callback_update(
        cb_a, chat_id=CHAT, chat_type="group", message_id=2)
    _run(bot._handle_callback(cb_upd_a, MagicMock()))

    assert q_a.edit_message_text.await_args is not None, (
        "expected A's ⋮ menu (or a fail-closed notice) to be rendered")
    rendered = q_a.edit_message_text.await_args.args[0] if q_a.edit_message_text.await_args.args \
        else q_a.edit_message_text.await_args.kwargs.get("text", "")
    assert "beta" not in (rendered or "").lower(), (
        f"tapping alpha's captured ⋮ button rendered beta's menu instead "
        f"after beta's own menu was opened in between: {rendered!r}"
    )


# --------------------------------------------------------------------------- #
# Destructive-action safety: a stale restart-confirm must never restart.   #
# --------------------------------------------------------------------------- #

async def _launch_ok(*a, **kw):
    return True, ""


def test_restart_confirm_via_a_stale_index_after_removal_never_restarts_anything(
        mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    sess = TrackedSession(name="claude-gamma__g100", label="gamma",
                           status=Status.IDLE, scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/restart gamma", chat_id=CHAT, chat_type="group")
    _run(session_parity.handle_restart_cmd(bot, upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    dests = helpers.destinations(bot, CHAT, cbs)
    assert (sess.name, "restart-confirm") in dests
    confirm_cb = cbs[[v for _, v in dests].index("restart-confirm")]

    del bot.registry._sessions[sess.name]  # dies before the confirm tap

    with patch("aipager.dtach.inject.launch_session",
               side_effect=_launch_ok) as launch:
        cb_upd, q = helpers.make_callback_update(
            confirm_cb, chat_id=CHAT, chat_type="group", message_id=1)
        _run(bot._handle_callback(cb_upd, MagicMock()))  # must not raise

    launch.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Error guessing: out-of-range / negative index on a destructive verb.     #
# --------------------------------------------------------------------------- #

def test_out_of_range_sx_index_on_restart_confirm_does_not_crash_or_restart(
        mk_bot, helpers):
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    _status(bot, helpers.make_message_update(
        "/status", chat_id=CHAT, chat_type="group"))  # table: 2 entries, 0/1

    with patch("aipager.dtach.inject.launch_session",
               side_effect=_launch_ok) as launch:
        cb_upd, q = helpers.make_callback_update(
            "_:sx:99:restart-confirm", chat_id=CHAT, chat_type="group",
            message_id=1,
        )
        _run(bot._handle_callback(cb_upd, MagicMock()))  # must not raise

    launch.assert_not_awaited()


def test_negative_sx_index_on_delete_confirm_does_not_crash_or_delete(
        mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    sess = TrackedSession(name="claude-delta__g100", label="delta",
                           status=Status.GONE, scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess
    _status(bot, helpers.make_message_update(
        "/status", chat_id=CHAT, chat_type="group"))

    cb_upd, q = helpers.make_callback_update(
        "_:sx:-1:delete-confirm", chat_id=CHAT, chat_type="group",
        message_id=1,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))  # must not raise

    assert sess.name in bot.registry._sessions, (
        "a negative sx index must never resolve to (and delete) the "
        "last-registered session via Python's -1 list semantics"
    )


# --------------------------------------------------------------------------- #
# Cross-chat isolation, mirroring test_stale_spref_fails_closed.py's own   #
# equivalent for `_:spref`.                                                 #
# --------------------------------------------------------------------------- #

def test_sx_index_from_a_different_chat_does_not_leak_across_chats(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    other_chat_sess = TrackedSession(
        name="claude-onlyinotherchat__g1", label="onlyinotherchat",
        status=Status.IDLE, scope_chat_id=1,
    )
    bot.registry._sessions[other_chat_sess.name] = other_chat_sess
    _status(bot, helpers.make_message_update(
        "/status", chat_id=1, chat_type="group"))  # registers chat_id=1's table only

    with patch("aipager.dtach.inject.launch_session",
               side_effect=_launch_ok) as launch:
        cb_upd, q = helpers.make_callback_update(
            "_:sx:0:restart-confirm", chat_id=CHAT, chat_type="group",
            message_id=1,
        )
        _run(bot._handle_callback(cb_upd, MagicMock()))  # must not raise

    launch.assert_not_awaited()
