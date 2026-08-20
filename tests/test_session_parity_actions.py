"""Tests for aipager.bot.session_parity — the ⋮ session menu, and the
restart/rename/delete/diff commands + their callbacks.

Same calling convention as test_session_parity_prefs.py: every test
calls the module's exported functions directly (``handle_restart_cmd``,
``handle_rename_cmd``, ``handle_delete_cmd``, ``handle_diff_cmd``,
``maybe_handle_text``, ``handle_callback``) since this module is not
wired into handlers.py/callbacks.py in this worktree.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.bot.session_ops import RestartOutcome
from aipager.state import Status, TrackedSession
from aipager.team import Role, Team
from aipager.team import User as TeamUser


@pytest.fixture
def mk_query():
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.message.reply_text = AsyncMock()
        query.message.reply_document = AsyncMock()
        query.from_user = MagicMock()
        query.from_user.id = user_id
        return query
    return _mk


def _mk_cb_update(chat_id, user_id):
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def _session(name="claude-dev", label="dev", **kwargs):
    return TrackedSession(name=name, label=label, **kwargs)


def _no_op_bot_command_refresh(monkeypatch):
    """_rename_session_core schedules asyncio.create_task(self.
    _update_bot_commands()) on a real change. Mirrors
    tests/test_bot_session_ops.py's own precedent: close the coroutine
    instead of running it, so the test never depends on bot._app's
    unconfigured MagicMock surface."""
    monkeypatch.setattr(
        "aipager.bot.session_ops.asyncio.create_task",
        lambda coro: coro.close(),
    )


# ---- _RESERVED guard (dtach/inject.py's one-line change) -----------------

def test_new_command_names_are_reserved():
    from aipager.dtach import inject
    assert {"restart", "rename", "delete", "diff"} <= inject._RESERVED


def test_reserved_names_rejected_by_launch_session(run_async):
    from aipager.dtach import inject
    for name in ("restart", "rename", "delete", "diff"):
        ok, err = run_async(inject.launch_session(name))
        assert ok is False
        assert "reserved" in err.lower()


def test_reserved_names_rejected_by_miniapp_validate_session_name():
    from aipager.miniapp.launch import validate_session_name
    for name in ("restart", "rename", "delete", "diff"):
        clean, err = validate_session_name(name)
        assert clean == ""
        assert "reserved" in err.lower()


def test_rename_new_label_reserved_word_refused(mk_bot, mk_update, run_async):
    """End-to-end proof the _RESERVED addition is actually exercised by
    chat's own /rename validation, not just present in the set."""
    bot = mk_bot()
    sess = _session(label="old")
    bot.registry._sessions[sess.name] = sess

    update = mk_update("/rename old restart")
    run_async(session_parity.handle_rename_cmd(bot, update, MagicMock()))

    assert sess.label == "old"
    text = update.message.reply_text.await_args.args[0]
    assert "reserved" in text.lower()


# ---- rename_pending state isolation ---------------------------------

def test_rename_pending_lives_on_instance_not_module(mk_bot):
    bot1 = mk_bot()
    bot2 = mk_bot()
    session_parity._rename_pending_map(bot1)[7] = {
        "session_name": "claude-x", "label": "x",
    }
    assert session_parity._rename_pending_map(bot2).get(7) is None


# ---- ⋮ session menu ----------------------------------------------------

def test_menu_renders_rows_in_order_for_live_session(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(status=Status.IDLE, scope_chat_id=0)
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:menu")
    update = _mk_cb_update(0, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, sess.name, "menu"))

    assert handled is True
    labels = [btn.text for row in query.edit_message_text.await_args.kwargs["reply_markup"]
              .inline_keyboard for btn in row]
    assert labels[:4] == ["🔄 Restart", "✏️ Rename", "👤 Preferences", "📝 Diff"]
    assert "🗑️ Delete" not in labels
    assert bot._session_pref_index[0] == [sess.name]


def test_menu_includes_delete_only_when_gone(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(status=Status.GONE)
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:menu")
    update = _mk_cb_update(0, 1)
    run_async(session_parity.handle_callback(bot, update, query, sess.name, "menu"))

    labels = [btn.text for row in query.edit_message_text.await_args.kwargs["reply_markup"]
              .inline_keyboard for btn in row]
    assert "🗑️ Delete" in labels


def test_menu_preferences_row_resolves_back_to_same_session(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(label="dev", scope_chat_id=777)
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:menu")
    update = _mk_cb_update(777, 1)
    run_async(session_parity.handle_callback(bot, update, query, sess.name, "menu"))

    pref_row = next(
        b for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row if b.text == "👤 Preferences"
    )
    cb = pref_row.callback_data
    cb_session_name, cb_action = cb.split(":", 1)

    query2 = mk_query(cb)
    handled = run_async(
        session_parity.handle_callback(bot, update, query2, cb_session_name, cb_action),
    )

    assert handled is True
    assert "dev" in query2.edit_message_text.await_args.args[0]


def test_menu_close_dismisses(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session()
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:menu-close")
    update = _mk_cb_update(0, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, sess.name, "menu-close"))

    assert handled is True
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is None


def test_handle_callback_session_not_found(mk_bot, run_async, mk_query):
    bot = mk_bot()
    query = mk_query("claude-ghost:restart")
    update = _mk_cb_update(0, 1)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, "claude-ghost", "restart"),
    )
    assert handled is True
    query.answer.assert_awaited_once_with("Session not found")
    query.edit_message_text.assert_not_awaited()


def test_handle_callback_returns_false_for_unrelated_action(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session()
    bot.registry._sessions[sess.name] = sess
    query = mk_query(f"{sess.name}:kill")
    update = _mk_cb_update(0, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, sess.name, "kill"))
    assert handled is False
    query.edit_message_text.assert_not_awaited()


def _destinations(bot, chat_id, cbs):
    """Resolve each button's ``callback_data`` to ``(session_name, verb)``.

    The buttons carry an opaque per-chat index rather than the session
    name — no ``{name}:<verb>`` form can fit Telegram's 64-byte
    ``callback_data`` cap, because internal names are themselves capped
    at 64. Asserting the literal string would pin the encoding instead
    of the destination, and the destination is the thing that must not
    drift: a button that resolves to the wrong session restarts or
    deletes something the user was not looking at.
    """
    out = []
    for cb in cbs:
        sentinel, rest = cb.split(":", 1)
        kind, idx, verb = rest.split(":", 2)
        assert (sentinel, kind) == ("_", "sx"), f"unexpected callback form: {cb!r}"
        sess = session_parity._resolve_pref_index(bot, chat_id, idx)
        out.append((sess.name if sess is not None else None, verb))
    return out


# ---- /restart ----------------------------------------------------------

def test_restart_cmd_no_label_shows_picker_of_live_sessions(mk_bot, mk_update, run_async):
    bot = mk_bot()
    live = _session(name="claude-live", label="live", status=Status.IDLE)
    gone = _session(name="claude-gone", label="gone", status=Status.GONE)
    bot.registry._sessions[live.name] = live
    bot.registry._sessions[gone.name] = gone

    update = mk_update("/restart")
    run_async(session_parity.handle_restart_cmd(bot, update, MagicMock()))

    kwargs = update.message.reply_text.await_args.kwargs
    cbs = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    dests = _destinations(bot, update.effective_chat.id, cbs)
    assert dests == [("claude-live", "restart")]


def test_restart_cmd_with_label_shows_confirm_directly(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = _session(status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess

    update = mk_update(f"/restart {sess.label}")
    run_async(session_parity.handle_restart_cmd(bot, update, MagicMock()))

    kwargs = update.message.reply_text.await_args.kwargs
    cbs = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    dests = _destinations(bot, update.effective_chat.id, cbs)
    assert (sess.name, "restart-confirm") in dests
    assert (sess.name, "restart-cancel") in dests


def test_restart_cmd_unknown_label(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/restart nope")
    run_async(session_parity.handle_restart_cmd(bot, update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown" in text


def test_restart_cmd_refuses_read_only_member(mk_bot, mk_update, run_async):
    team = Team(group_id=-1001, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    update = mk_update("/restart", user_id=999, chat_id=-1001)
    update.effective_message = update.message  # _authorize's refusal reply target
    run_async(session_parity.handle_restart_cmd(bot, update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "read_only" in text


def test_restart_callback_shows_confirm(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:restart")
    update = _mk_cb_update(0, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, sess.name, "restart"))

    assert handled is True
    query.edit_message_text.assert_awaited_once()


def test_restart_confirm_executes_and_reports_success(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess
    outcome = RestartOutcome(ok=True, reason="done", label=sess.label)
    bot._restart_session_core = AsyncMock(return_value=outcome)

    query = mk_query(f"{sess.name}:restart-confirm")
    update = _mk_cb_update(0, 1)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, sess.name, "restart-confirm"),
    )

    assert handled is True
    bot._restart_session_core.assert_awaited_once_with(sess)
    text = query.edit_message_text.await_args.args[0]
    assert "restarted" in text


def test_restart_confirm_reports_failure_reason(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess
    outcome = RestartOutcome(ok=False, reason="still_stopping", label=sess.label)
    bot._restart_session_core = AsyncMock(return_value=outcome)

    query = mk_query(f"{sess.name}:restart-confirm")
    update = _mk_cb_update(0, 1)
    run_async(session_parity.handle_callback(bot, update, query, sess.name, "restart-confirm"))

    text = query.edit_message_text.await_args.args[0]
    assert "didn't stop in time" in text


def test_restart_confirm_denied_for_non_prompt_capable_member(mk_bot, run_async, mk_query):
    """Destructive-adjacent action: the gate is re-checked at confirm
    time, independently of whatever gated the earlier "show confirm"
    tap — a demotion between the two taps must not slip through."""
    team = Team(group_id=0, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    sess = _session(status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess
    bot._restart_session_core = AsyncMock()

    query = mk_query(f"{sess.name}:restart-confirm", user_id=999)
    update = _mk_cb_update(0, 999)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, sess.name, "restart-confirm"),
    )

    assert handled is True
    bot._restart_session_core.assert_not_awaited()
    query.answer.assert_awaited_once_with("You can't restart this session.")


def test_restart_show_confirm_denied_for_non_prompt_capable_member(mk_bot, run_async, mk_query):
    team = Team(group_id=0, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    sess = _session(status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:restart", user_id=999)
    update = _mk_cb_update(0, 999)
    handled = run_async(session_parity.handle_callback(bot, update, query, sess.name, "restart"))

    assert handled is True
    query.edit_message_text.assert_not_awaited()
    query.answer.assert_awaited_once_with("You can't restart this session.")


def test_restart_cancel_dismisses(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session()
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:restart-cancel")
    update = _mk_cb_update(0, 1)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, sess.name, "restart-cancel"),
    )

    assert handled is True
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is None


# ---- /rename -------------------------------------------------------------

def test_rename_cmd_two_args_direct_no_confirm(mk_bot, mk_update, run_async, monkeypatch):
    _no_op_bot_command_refresh(monkeypatch)
    bot = mk_bot()
    sess = _session(label="old")
    bot.registry._sessions[sess.name] = sess

    update = mk_update("/rename old newname")
    run_async(session_parity.handle_rename_cmd(bot, update, MagicMock()))

    assert sess.label == "newname"
    text = update.message.reply_text.await_args.args[0]
    assert "renamed to" in text


def test_rename_cmd_unknown_old_label(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/rename nope newname")
    run_async(session_parity.handle_rename_cmd(bot, update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown" in text


def test_rename_cmd_collision_refused(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess_a = _session(name="claude-a", label="a", status=Status.IDLE)
    sess_b = _session(name="claude-b", label="b", status=Status.IDLE)
    bot.registry._sessions[sess_a.name] = sess_a
    bot.registry._sessions[sess_b.name] = sess_b

    update = mk_update("/rename a b")
    run_async(session_parity.handle_rename_cmd(bot, update, MagicMock()))

    assert sess_a.label == "a"
    text = update.message.reply_text.await_args.args[0]
    assert "already exists" in text


def test_rename_cmd_no_args_shows_picker(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = _session(label="dev")
    bot.registry._sessions[sess.name] = sess

    update = mk_update("/rename")
    run_async(session_parity.handle_rename_cmd(bot, update, MagicMock()))

    kwargs = update.message.reply_text.await_args.kwargs
    cbs = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    dests = _destinations(bot, update.effective_chat.id, cbs)
    assert dests == [(sess.name, "rename")]


def test_rename_cmd_one_arg_also_shows_picker(mk_bot, mk_update, run_async):
    """entrypoints.md: "Fewer args -> picker" covers both 0 and 1 arg —
    a single old-label argument alone does not skip the picker."""
    bot = mk_bot()
    sess = _session(label="dev")
    bot.registry._sessions[sess.name] = sess

    update = mk_update("/rename dev")
    run_async(session_parity.handle_rename_cmd(bot, update, MagicMock()))

    kwargs = update.message.reply_text.await_args.kwargs
    assert kwargs["reply_markup"] is not None


def test_rename_cmd_refuses_read_only_member(mk_bot, mk_update, run_async):
    team = Team(group_id=-1001, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    update = mk_update("/rename", user_id=999, chat_id=-1001)
    update.effective_message = update.message  # _authorize's refusal reply target
    run_async(session_parity.handle_rename_cmd(bot, update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "read_only" in text


def test_rename_callback_starts_capture(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(label="dev")
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:rename")
    update = _mk_cb_update(555, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, sess.name, "rename"))

    assert handled is True
    assert bot._rename_pending[555] == {"session_name": sess.name, "label": "dev"}
    query.edit_message_text.assert_awaited_once()


def test_rename_callback_denied_for_read_only_does_not_start_capture(mk_bot, run_async, mk_query):
    team = Team(group_id=555, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    sess = _session(label="dev")
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:rename", user_id=999)
    update = _mk_cb_update(555, 999)
    handled = run_async(session_parity.handle_callback(bot, update, query, sess.name, "rename"))

    assert handled is True
    assert 555 not in session_parity._rename_pending_map(bot)


def test_rename_cancel_clears_pending(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(label="dev")
    bot.registry._sessions[sess.name] = sess
    bot._rename_pending = {555: {"session_name": sess.name, "label": "dev"}}

    query = mk_query(f"{sess.name}:rename-cancel")
    update = _mk_cb_update(555, 1)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, sess.name, "rename-cancel"),
    )

    assert handled is True
    assert 555 not in bot._rename_pending


def test_maybe_handle_text_applies_pending_rename(mk_bot, mk_update, run_async, monkeypatch):
    _no_op_bot_command_refresh(monkeypatch)
    bot = mk_bot()
    sess = _session(label="dev")
    bot.registry._sessions[sess.name] = sess
    bot._rename_pending = {-1001: {"session_name": sess.name, "label": "dev"}}

    update = mk_update("newname", chat_id=-1001)
    handled = run_async(
        session_parity.maybe_handle_text(bot, update, MagicMock(), "newname"),
    )

    assert handled is True
    assert sess.label == "newname"
    assert -1001 not in bot._rename_pending


def test_maybe_handle_text_returns_false_when_nothing_pending(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("hello", chat_id=-1001)
    handled = run_async(
        session_parity.maybe_handle_text(bot, update, MagicMock(), "hello"),
    )
    assert handled is False


def test_maybe_handle_text_fails_closed_if_session_gone(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot._rename_pending = {-1001: {"session_name": "claude-vanished", "label": "old"}}

    update = mk_update("newname", chat_id=-1001)
    handled = run_async(
        session_parity.maybe_handle_text(bot, update, MagicMock(), "newname"),
    )

    assert handled is True
    text = update.message.reply_text.await_args.args[0]
    assert "no longer available" in text
    assert -1001 not in bot._rename_pending  # consumed, not left stale


def test_maybe_handle_text_denied_for_read_only_member(mk_bot, mk_update, run_async):
    team = Team(group_id=-1001, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    sess = _session(label="dev")
    bot.registry._sessions[sess.name] = sess
    bot._rename_pending = {-1001: {"session_name": sess.name, "label": "dev"}}

    update = mk_update("newname", chat_id=-1001, user_id=999)
    handled = run_async(
        session_parity.maybe_handle_text(bot, update, MagicMock(), "newname"),
    )

    assert handled is True
    assert sess.label == "dev"


# ---- /delete -------------------------------------------------------------

def test_delete_cmd_no_label_shows_picker_of_gone_sessions(mk_bot, mk_update, run_async):
    bot = mk_bot()
    gone = _session(name="claude-gone", label="gone", status=Status.GONE)
    live = _session(name="claude-live", label="live", status=Status.IDLE)
    bot.registry._sessions[gone.name] = gone
    bot.registry._sessions[live.name] = live

    update = mk_update("/delete")
    run_async(session_parity.handle_delete_cmd(bot, update, MagicMock()))

    kwargs = update.message.reply_text.await_args.kwargs
    cbs = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    dests = _destinations(bot, update.effective_chat.id, cbs)
    assert dests == [("claude-gone", "delete")]


def test_delete_cmd_label_not_gone_refused(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = _session(status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess

    update = mk_update(f"/delete {sess.label}")
    run_async(session_parity.handle_delete_cmd(bot, update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert "still running" in text


def test_delete_cmd_unknown_label(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/delete nope")
    run_async(session_parity.handle_delete_cmd(bot, update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown" in text


def test_delete_cmd_refuses_read_only_member(mk_bot, mk_update, run_async):
    team = Team(group_id=-1001, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    update = mk_update("/delete", user_id=999, chat_id=-1001)
    update.effective_message = update.message  # _authorize's refusal reply target
    run_async(session_parity.handle_delete_cmd(bot, update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "read_only" in text


def test_delete_confirm_removes_and_marks_dirty(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(status=Status.GONE)
    bot.registry._sessions[sess.name] = sess
    bot.registry._dirty = False

    query = mk_query(f"{sess.name}:delete-confirm")
    update = _mk_cb_update(0, 1)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, sess.name, "delete-confirm"),
    )

    assert handled is True
    assert sess.name not in bot.registry._sessions
    assert bot.registry._dirty is True


def test_delete_confirm_refuses_if_no_longer_gone(mk_bot, run_async, mk_query):
    """The tap can arrive long after the confirm was drawn — if the
    session came back to life in the meantime (e.g. /resume), the
    delete must refuse rather than removing a live session."""
    bot = mk_bot()
    sess = _session(status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:delete-confirm")
    update = _mk_cb_update(0, 1)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, sess.name, "delete-confirm"),
    )

    assert handled is True
    assert sess.name in bot.registry._sessions


def test_delete_confirm_denied_for_non_prompt_capable_member(mk_bot, run_async, mk_query):
    team = Team(group_id=0, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    sess = _session(status=Status.GONE)
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:delete-confirm", user_id=999)
    update = _mk_cb_update(0, 999)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, sess.name, "delete-confirm"),
    )

    assert handled is True
    assert sess.name in bot.registry._sessions


def test_delete_cancel_dismisses(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(status=Status.GONE)
    bot.registry._sessions[sess.name] = sess

    query = mk_query(f"{sess.name}:delete-cancel")
    update = _mk_cb_update(0, 1)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, sess.name, "delete-cancel"),
    )

    assert handled is True
    assert sess.name in bot.registry._sessions
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is None


# ---- /diff -----------------------------------------------------------

@pytest.mark.parametrize("reason,expected_fragment", [
    ("cwd_missing", "working directory is no longer there"),
    ("git_not_installed", "git isn't available"),
    ("not_a_git_repo", "isn't a git repo"),
    ("no_commits_yet", "no commits yet"),
    ("git_error", "Couldn't read the diff"),
])
def test_diff_reason_fallbacks(mk_bot, mk_update, run_async, monkeypatch, reason, expected_fragment):
    bot = mk_bot()
    sess = _session(cwd="/tmp/proj")
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": False, "reason": reason}),
    )

    update = mk_update("/diff")
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert expected_fragment in text


def test_diff_no_changes_is_the_sixth_fallback(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = _session(cwd="/tmp/proj")
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": True, "files": [], "files_truncated": False}),
    )

    update = mk_update("/diff")
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert "No changes" in text


def test_diff_small_patch_sent_inline(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = _session(cwd="/tmp/proj", label="dev")
    bot.registry._sessions[sess.name] = sess
    patch = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1 +1,2 @@\n-old\n+new\n+line2\n"
    )
    files = [{"path": "a.py", "change_type": "modified", "binary": False,
              "truncated": False, "patch": patch}]
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": True, "files": files, "files_truncated": False}),
    )

    update = mk_update(f"/diff {sess.label}")
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "1 files changed, +2/-1" in text
    assert "<pre>" in text
    update.message.reply_document.assert_not_called()


def test_diff_large_patch_sent_as_document(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = _session(cwd="/tmp/proj", label="dev")
    bot.registry._sessions[sess.name] = sess
    big_patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n" + "+line\n" * 2000
    files = [{"path": "a.py", "change_type": "modified", "binary": False,
              "truncated": False, "patch": big_patch}]
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": True, "files": files, "files_truncated": False}),
    )

    update = mk_update(f"/diff {sess.label}")
    update.message.reply_document = AsyncMock()
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    assert update.message.reply_text.await_count == 1  # stat line, alone
    stat_text = update.message.reply_text.await_args.args[0]
    assert "<pre>" not in stat_text
    update.message.reply_document.assert_awaited_once()
    kwargs = update.message.reply_document.await_args.kwargs
    assert kwargs["filename"] == "dev.diff"
    doc = kwargs["document"]
    assert doc.read().decode("utf-8") == big_patch


def test_diff_truncated_file_forces_document_even_if_short(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = _session(cwd="/tmp/proj", label="dev")
    bot.registry._sessions[sess.name] = sess
    files = [{"path": "a.bin", "change_type": "modified", "binary": True,
              "truncated": False, "patch": None}]
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": True, "files": files, "files_truncated": False}),
    )

    update = mk_update(f"/diff {sess.label}")
    update.message.reply_document = AsyncMock()
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    stat_text = update.message.reply_text.await_args.args[0]
    assert "<pre>" not in stat_text
    update.message.reply_document.assert_awaited_once()


def test_diff_cmd_no_label_uses_last_active_session(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = _session(cwd="/tmp/proj", label="dev")
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": True, "files": [], "files_truncated": False}),
    )

    update = mk_update("/diff")
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert "dev" in text


def test_diff_cmd_no_active_session(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/diff")
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "No active session" in text


def test_diff_cmd_unknown_label(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/diff nope")
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown" in text


def test_diff_cmd_allows_read_only_member(mk_bot, mk_update, run_async, monkeypatch):
    team = Team(group_id=-1001, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    sess = _session(cwd="/tmp/proj", label="dev", scope_chat_id=-1001)
    bot.registry._sessions[sess.name] = sess
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": True, "files": [], "files_truncated": False}),
    )

    update = mk_update(f"/diff {sess.label}", user_id=999, chat_id=-1001)
    run_async(session_parity.handle_diff_cmd(bot, update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert "No changes" in text


def test_diff_callback_uses_query_message(mk_bot, run_async, mk_query, monkeypatch):
    bot = mk_bot()
    sess = _session(cwd="/tmp/proj", label="dev")
    bot.registry._sessions[sess.name] = sess
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": True, "files": [], "files_truncated": False}),
    )

    query = mk_query(f"{sess.name}:diff")
    update = _mk_cb_update(0, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, sess.name, "diff"))

    assert handled is True
    query.message.reply_text.assert_awaited_once()
