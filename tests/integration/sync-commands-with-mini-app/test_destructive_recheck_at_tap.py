"""Priority 4 (task brief): destructive actions re-check at tap time.

entrypoints.md: `/restart` and `/delete` both draw a confirm whose
`-confirm` callback is separately gated by `bot._can_prompt_user`
"(re-checked at tap time)" — the confirm message can sit unanswered for
any length of time, and can in principle be tapped by anyone who can
see it in a group, so the tap itself must re-derive authorization
rather than trust that the command that drew the confirm was once
authorized.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from aipager.bot import session_parity
from aipager.bot.session_ops import RestartOutcome
from aipager.state import Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = -100
USER = 2


def _live_session(name="claude-foo__g100", label="foo"):
    return TrackedSession(name=name, label=label, status=Status.IDLE,
                           scope_chat_id=CHAT)


def _gone_session(name="claude-foo__g100", label="foo"):
    return TrackedSession(name=name, label=label, status=Status.GONE,
                           scope_chat_id=CHAT, claude_session_id="abc123")


def _demote_to_read_only(helpers, bot):
    bot.scopes = helpers.make_scopes(
        chat_id=CHAT, kind="group", members=[(USER, "bob", "read_only")])


# --------------------------------------------------------------------------- #
# /restart                                                                   #
# --------------------------------------------------------------------------- #

def test_restart_command_draws_a_confirm_with_confirm_and_cancel(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _live_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/restart foo", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(session_parity.handle_restart_cmd(bot, upd, MagicMock()))

    upd.message.reply_text.assert_awaited_once()
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    dests = helpers.destinations(bot, CHAT, cbs)
    assert (sess.name, "restart-confirm") in dests
    assert (sess.name, "restart-cancel") in dests


def test_restart_confirm_tap_demoted_between_command_and_tap_is_refused(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _live_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/restart foo", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(session_parity.handle_restart_cmd(bot, upd, MagicMock()))

    _demote_to_read_only(helpers, bot)
    bot._restart_session_core = AsyncMock()

    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:restart-confirm", chat_id=CHAT, chat_type="group",
        user_id=USER, message_id=42,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))

    bot._restart_session_core.assert_not_awaited()


def test_restart_confirm_tap_not_demoted_actually_restarts(mk_bot, helpers):
    """Control case: the SAME confirm tap, from a caller who was NOT
    demoted, must still succeed — proves the refusal above is the
    re-check firing, not some unrelated breakage."""
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _live_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/restart foo", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(session_parity.handle_restart_cmd(bot, upd, MagicMock()))

    bot._restart_session_core = AsyncMock(
        return_value=RestartOutcome(ok=True, reason="done", label="foo"))
    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:restart-confirm", chat_id=CHAT, chat_type="group",
        user_id=USER, message_id=42,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))

    bot._restart_session_core.assert_awaited_once()


def test_restart_cancel_tap_never_restarts(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _live_session()
    bot.registry._sessions[sess.name] = sess
    bot._restart_session_core = AsyncMock()

    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:restart-cancel", chat_id=CHAT, chat_type="group",
        user_id=USER, message_id=42,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))

    bot._restart_session_core.assert_not_awaited()


def test_restart_confirm_tap_by_outsider_is_refused(mk_bot, helpers):
    """Anyone who can SEE the confirm message in a group can, in
    principle, tap it — a non-member (never authorized at all) tapping
    the confirm button must be refused exactly like a demoted member."""
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _live_session()
    bot.registry._sessions[sess.name] = sess
    bot._restart_session_core = AsyncMock()

    OUTSIDER = 99999
    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:restart-confirm", chat_id=CHAT, chat_type="group",
        user_id=OUTSIDER, message_id=42,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))

    bot._restart_session_core.assert_not_awaited()


# --------------------------------------------------------------------------- #
# /delete                                                                    #
# --------------------------------------------------------------------------- #

def test_delete_only_offered_for_a_gone_session(mk_bot, helpers):
    """entrypoints.md: delete confirm is shown "only when the session
    is GONE"; a live session's /delete must not offer it (mirrors the
    Mini App's 409)."""
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _live_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/delete foo", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(session_parity.handle_delete_cmd(bot, upd, MagicMock()))

    upd.message.reply_text.assert_awaited_once()
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb) if kb else []
    assert f"{sess.name}:delete-confirm" not in cbs


def test_delete_command_on_gone_session_draws_a_confirm(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _gone_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/delete foo", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(session_parity.handle_delete_cmd(bot, upd, MagicMock()))

    upd.message.reply_text.assert_awaited_once()
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    dests = helpers.destinations(bot, CHAT, cbs)
    assert (sess.name, "delete-confirm") in dests
    assert (sess.name, "delete-cancel") in dests


def test_delete_confirm_tap_demoted_between_command_and_tap_is_refused(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _gone_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/delete foo", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(session_parity.handle_delete_cmd(bot, upd, MagicMock()))

    _demote_to_read_only(helpers, bot)

    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:delete-confirm", chat_id=CHAT, chat_type="group",
        user_id=USER, message_id=42,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert sess.name in bot.registry._sessions, (
        "a demoted caller's stale delete-confirm tap removed the session"
    )


def test_delete_confirm_tap_not_demoted_actually_deletes(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _gone_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/delete foo", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(session_parity.handle_delete_cmd(bot, upd, MagicMock()))

    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:delete-confirm", chat_id=CHAT, chat_type="group",
        user_id=USER, message_id=42,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert sess.name not in bot.registry._sessions


def test_delete_confirm_marks_registry_dirty(mk_bot, helpers):
    """design.md Stream B item 6's own explicit footgun warning:
    `registry.remove()` does NOT call `mark_dirty()` itself — the
    delete callback must call both, or a delete would silently fail to
    persist across a daemon restart."""
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _gone_session()
    bot.registry._sessions[sess.name] = sess
    bot.registry._dirty = False

    upd = helpers.make_message_update(
        "/delete foo", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(session_parity.handle_delete_cmd(bot, upd, MagicMock()))

    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:delete-confirm", chat_id=CHAT, chat_type="group",
        user_id=USER, message_id=42,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert bot.registry._dirty is True


def test_delete_cancel_tap_never_deletes(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _gone_session()
    bot.registry._sessions[sess.name] = sess

    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:delete-cancel", chat_id=CHAT, chat_type="group",
        user_id=USER, message_id=42,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))

    assert sess.name in bot.registry._sessions
