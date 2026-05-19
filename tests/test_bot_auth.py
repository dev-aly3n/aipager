"""Tests for aipager.bot.auth.AuthMixin — team-mode allow-list, role
gating, auto-deny, driver attribution.

Security-critical code: every code path must be covered so the install
on a user's machine doesn't accidentally hand shell access to someone
off the allow-list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.state import Status, TrackedSession
from aipager.team import Role, Rules, Team, User as TeamUser


def _team(*users):
    """Build a Team with the given users dict, no deny rules."""
    return Team(
        group_id=-100,
        users={u.id: u for u in users},
        rules=Rules(deny_tools=[]),
    )


def _admin(uid=1, label="admin"):
    return TeamUser(id=uid, label=label, role=Role.ADMIN)


def _developer(uid=2, label="dev"):
    return TeamUser(id=uid, label=label, role=Role.DEVELOPER)


def _readonly(uid=3, label="reader"):
    return TeamUser(id=uid, label=label, role=Role.READ_ONLY)


# ---- _team_user ----------------------------------------------------------

def test_team_user_personal_mode_returns_none(mk_bot, mk_update):
    bot = mk_bot()  # team=None by default
    update = mk_update("hi")
    assert bot._team_user(update) is None


def test_team_user_no_effective_user_returns_none(mk_bot, mk_update):
    bot = mk_bot()
    bot.team = _team(_admin())
    update = mk_update("hi")
    update.effective_user = None
    assert bot._team_user(update) is None


def test_team_user_matches_admin(mk_bot, mk_update):
    bot = mk_bot()
    admin = _admin()
    bot.team = _team(admin)
    update = mk_update("hi", user_id=admin.id)
    assert bot._team_user(update) is admin


def test_team_user_unknown_id_returns_none(mk_bot, mk_update):
    bot = mk_bot()
    bot.team = _team(_admin())
    update = mk_update("hi", user_id=99999)
    assert bot._team_user(update) is None


# ---- _authorize ----------------------------------------------------------

def test_authorize_personal_mode_always_true(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("hi")
    assert run_async(bot._authorize(update)) is True


def test_authorize_no_effective_user_returns_false(mk_bot, mk_update, run_async):
    bot = mk_bot()
    bot.team = _team(_admin())
    update = mk_update("hi")
    update.effective_user = None
    assert run_async(bot._authorize(update)) is False


def test_authorize_unauthorized_user_sends_oneshot_reply(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    bot.team = _team(_admin())
    update = mk_update("hi", user_id=99999)
    update.effective_user.username = "stranger"
    update.effective_user.first_name = "Some"
    update.effective_user.last_name = "One"
    update.effective_message = update.message
    # Stub pending-user persistence
    monkeypatch.setattr("aipager.bot.auth.record_pending_user",
                        MagicMock())
    # Force "first time we see this user" so the reply fires
    monkeypatch.setattr("aipager.bot.auth.remember_unauthorized",
                        lambda uid: False)
    assert run_async(bot._authorize(update)) is False
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "not on this bot's allow-list" in text


def test_authorize_unauthorized_user_silent_after_first_reply(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    bot.team = _team(_admin())
    update = mk_update("hi", user_id=99999)
    monkeypatch.setattr("aipager.bot.auth.record_pending_user",
                        MagicMock())
    # Already seen — second reply should be suppressed
    monkeypatch.setattr("aipager.bot.auth.remember_unauthorized",
                        lambda uid: True)
    assert run_async(bot._authorize(update)) is False
    update.message.reply_text.assert_not_awaited()


def test_authorize_unauthorized_user_persists_pending_record(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    bot.team = _team(_admin())
    update = mk_update("hi", user_id=99999)
    update.effective_user.username = "newbie"
    update.effective_user.first_name = "New"
    update.effective_user.last_name = None
    update.effective_message = update.message
    record_mock = MagicMock()
    monkeypatch.setattr("aipager.bot.auth.record_pending_user", record_mock)
    monkeypatch.setattr("aipager.bot.auth.remember_unauthorized",
                        lambda uid: True)
    run_async(bot._authorize(update))
    record_mock.assert_called_once()
    call = record_mock.call_args
    assert call.args[0] == 99999
    assert call.kwargs["username"] == "newbie"
    assert call.kwargs["display_name"] == "New"


def test_authorize_admin_passes(mk_bot, mk_update, run_async):
    bot = mk_bot()
    admin = _admin()
    bot.team = _team(admin)
    update = mk_update("hi", user_id=admin.id)
    assert run_async(bot._authorize(update)) is True


def test_authorize_read_only_rejected_by_default(mk_bot, mk_update, run_async):
    bot = mk_bot()
    reader = _readonly()
    bot.team = _team(_admin(), reader)
    update = mk_update("hi", user_id=reader.id)
    update.effective_message = update.message
    assert run_async(bot._authorize(update)) is False
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "read_only" in text


def test_authorize_read_only_allowed_when_flag_set(mk_bot, mk_update, run_async):
    bot = mk_bot()
    reader = _readonly()
    bot.team = _team(_admin(), reader)
    update = mk_update("hi", user_id=reader.id)
    assert run_async(bot._authorize(update, allow_read_only=True)) is True


def test_authorize_swallows_pending_record_failure(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    bot.team = _team(_admin())
    update = mk_update("hi", user_id=99999)
    update.effective_message = update.message
    # Force record_pending_user to raise — auth must not propagate
    monkeypatch.setattr("aipager.bot.auth.record_pending_user",
                        MagicMock(side_effect=RuntimeError("io")))
    monkeypatch.setattr("aipager.bot.auth.remember_unauthorized",
                        lambda uid: True)
    assert run_async(bot._authorize(update)) is False


# ---- _auto_deny ----------------------------------------------------------

def test_auto_deny_injects_down_enter_and_marks_busy(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.pending_permission = {"x": "y"}  # should be cleared
    bot.registry._sessions["claude-jim"] = sess
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.auth.asyncio.sleep", _no_sleep)

    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "git push --force"}, _admin()))
    # Sent Down then Enter
    keys = [c.args[1] for c in sent.await_args_list]
    assert keys == ["Down", "Enter"]
    # Session transitioned to BUSY
    assert sess.status == Status.BUSY
    assert sess.pending_permission is None


def test_auto_deny_sends_chat_notice_with_triggerer_attribution(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.auth.asyncio.sleep", _no_sleep)

    driver = _developer(uid=42, label="dave")
    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "rm -rf /"}, driver))
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "Auto-denied" in text
    assert "Bash" in text
    assert "dave" in text


def test_auto_deny_swallows_key_injection_failure(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=False))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.auth.asyncio.sleep", _no_sleep)
    # MUST NOT raise
    run_async(bot._auto_deny(sess, {"name": "Edit", "summary": "/etc/passwd"}, None))
    # Session still transitions to BUSY
    assert sess.status == Status.BUSY


def test_auto_deny_swallows_send_message_failure(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.auth.asyncio.sleep", _no_sleep)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("flooded"))
    # MUST NOT raise
    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": ""}, None))


def test_auto_deny_writes_audit_record(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.auth.asyncio.sleep", _no_sleep)
    audit_calls = []
    def _fake_append(**kwargs):
        audit_calls.append(kwargs)
    monkeypatch.setattr("aipager.audit.append", _fake_append)

    driver = _developer(uid=42, label="dave")
    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "danger"}, driver))
    assert len(audit_calls) == 1
    assert audit_calls[0]["session"] == "claude-jim"
    assert audit_calls[0]["tool"] == "Bash"
    assert audit_calls[0]["user_id"] == 42


# ---- _mark_driver --------------------------------------------------------

def test_mark_driver_personal_mode_returns_none(mk_bot, mk_update):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim")
    update = mk_update("hi")
    assert bot._mark_driver(sess, update) is None


def test_mark_driver_first_touch_sets_creator(mk_bot, mk_update):
    bot = mk_bot()
    admin = _admin()
    bot.team = _team(admin)
    sess = TrackedSession(name="claude-jim", label="jim")
    update = mk_update("hi", user_id=admin.id)
    member = bot._mark_driver(sess, update)
    assert member is admin
    assert sess.created_by_user_id == admin.id
    assert sess.last_driver_user_id == admin.id


def test_mark_driver_subsequent_touch_updates_last_driver_only(mk_bot, mk_update):
    bot = mk_bot()
    admin = _admin(uid=1, label="alice")
    dev = _developer(uid=2, label="bob")
    bot.team = _team(admin, dev)
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.created_by_user_id = admin.id
    sess.last_driver_user_id = admin.id
    # bob acts on alice's session
    update = mk_update("hi", user_id=dev.id)
    member = bot._mark_driver(sess, update)
    assert member is dev
    assert sess.created_by_user_id == admin.id  # unchanged
    assert sess.last_driver_user_id == dev.id


def test_mark_driver_unauthorized_returns_none(mk_bot, mk_update):
    bot = mk_bot()
    bot.team = _team(_admin())
    sess = TrackedSession(name="claude-jim", label="jim")
    update = mk_update("hi", user_id=99999)
    assert bot._mark_driver(sess, update) is None


# ---- _driver_user --------------------------------------------------------

def test_driver_user_personal_mode_returns_none(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.last_driver_user_id = 42
    assert bot._driver_user(sess) is None


def test_driver_user_no_recorded_driver_returns_none(mk_bot):
    bot = mk_bot()
    bot.team = _team(_admin())
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.last_driver_user_id = None
    assert bot._driver_user(sess) is None


def test_driver_user_returns_matching_member(mk_bot):
    bot = mk_bot()
    admin = _admin()
    bot.team = _team(admin)
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.last_driver_user_id = admin.id
    assert bot._driver_user(sess) is admin


def test_driver_user_removed_member_returns_none(mk_bot):
    """Driver was on the team but has since been removed."""
    bot = mk_bot()
    bot.team = _team(_admin())
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.last_driver_user_id = 999  # not in team anymore
    assert bot._driver_user(sess) is None


# ---- _authorize_callback ------------------------------------------------

def test_authorize_callback_personal_mode_returns_sentinel(mk_bot, run_async):
    bot = mk_bot()
    query = MagicMock()
    query.from_user = MagicMock(id=12345)
    out = run_async(bot._authorize_callback(query))
    assert out is not None
    # The sentinel has id=0 and label="me"
    assert out.id == 0


def test_authorize_callback_no_from_user_returns_none(mk_bot, run_async):
    bot = mk_bot()
    bot.team = _team(_admin())
    query = MagicMock()
    query.from_user = None
    assert run_async(bot._authorize_callback(query)) is None


def test_authorize_callback_unauthorized_toasts_and_returns_none(mk_bot, run_async):
    bot = mk_bot()
    bot.team = _team(_admin())
    query = MagicMock()
    query.from_user = MagicMock(id=99999)
    query.answer = AsyncMock()
    assert run_async(bot._authorize_callback(query)) is None
    query.answer.assert_awaited_once()


def test_authorize_callback_admin_returns_member(mk_bot, run_async):
    bot = mk_bot()
    admin = _admin()
    bot.team = _team(admin)
    query = MagicMock()
    query.from_user = MagicMock(id=admin.id)
    assert run_async(bot._authorize_callback(query)) is admin


def test_authorize_callback_swallows_toast_failure(mk_bot, run_async):
    bot = mk_bot()
    bot.team = _team(_admin())
    query = MagicMock()
    query.from_user = MagicMock(id=99999)
    query.answer = AsyncMock(side_effect=RuntimeError("flooded"))
    # MUST NOT raise
    assert run_async(bot._authorize_callback(query)) is None
