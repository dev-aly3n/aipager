"""Tests for aipager.bot.auth.AuthMixin — team-mode allow-list, role
gating, auto-deny, driver attribution.

Security-critical code: every code path must be covered so the install
on a user's machine doesn't accidentally hand shell access to someone
off the allow-list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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

def test_auto_deny_types_the_deny_buttons_keys_and_marks_busy(mk_bot, run_async, monkeypatch):
    """The Deny button's key: one Escape. Claude Code's menus wrap, so any
    count of Downs lands on an allow row of some dialog shape (five Downs
    ran a denied Bash command through "always allow"; roadmap 8.126)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.pending_permission = {"x": "y"}  # should be cleared
    bot.registry._sessions["claude-jim"] = sess
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.auth._sleep", _no_sleep)

    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "git push --force"}, _admin()))
    keys = [c.args[1] for c in sent.await_args_list]
    assert keys == ["Escape"]
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
    monkeypatch.setattr("aipager.bot.auth._sleep", _no_sleep)

    driver = _developer(uid=42, label="dave")
    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "rm -rf /"}, driver))
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "Bash blocked for @dave (role developer)" in text
    assert "rules.deny_tools" not in text


def test_auto_deny_swallows_key_injection_failure(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=False))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.auth._sleep", _no_sleep)
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
    monkeypatch.setattr("aipager.bot.auth._sleep", _no_sleep)
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
    monkeypatch.setattr("aipager.bot.auth._sleep", _no_sleep)
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


def test_auto_deny_answers_a_waiting_hook_and_types_nothing(mk_bot, run_async, monkeypatch):
    """While the PermissionRequest hook waits, the refusal goes through it
    (the button's way): no key reaches a dialog Claude Code has not drawn
    yet, and the turn goes on, so nothing watches it."""
    from aipager.bot.callbacks import DENIED_VIA_AIPAGER
    from aipager.dtach import hook_reply
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    decisions = []
    monkeypatch.setattr(hook_reply, "send_decision",
                        lambda reply, decision: decisions.append((reply, decision)) or True)
    bot._watch_keystroke_answer = MagicMock()
    reply = {"addr": "/nonexistent/reply.sock", "request_id": "req-1"}

    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "rm -rf /"}, None, reply))
    assert decisions == [(reply, hook_reply.deny_decision(message=DENIED_VIA_AIPAGER,
                                                          interrupt=False))]
    assert sent.await_count == 0
    bot._watch_keystroke_answer.assert_not_called()
    assert sess.status == Status.BUSY


def test_a_typed_auto_deny_is_watched_like_a_typed_deny(mk_bot, run_async, monkeypatch):
    """No hook waits: the Escape refuses in the dialog, which ends the turn
    with no Stop hook. The turn is watched (roadmap 8.99), from BUSY, so the
    card does not say Working until the cap."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    held = [{"text": "queued"}]
    bot._claude_queue_before_typed_refusal = AsyncMock(return_value=held)
    seen = []
    bot._watch_keystroke_answer = MagicMock(
        side_effect=lambda s, **kw: seen.append((s.status, kw)))

    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": "rm -rf /"}, None, None))
    assert [c.args[1] for c in sent.await_args_list] == ["Escape"]
    assert len(seen) == 1
    status, kw = seen[0]
    assert status == Status.BUSY
    assert kw["refusal"] is True and kw["held"] == held
    assert kw["answered_at"] > 0


def test_an_auto_deny_whose_key_failed_is_not_watched(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=False))
    bot._watch_keystroke_answer = MagicMock()
    run_async(bot._auto_deny(sess, {"name": "Bash", "summary": ""}, None, None))
    bot._watch_keystroke_answer.assert_not_called()


def test_an_auto_denied_question_is_typed_and_watched_as_no_refusal(mk_bot, run_async,
                                                                    monkeypatch):
    """A question is always typed, as on the button; its Escape is not a
    tool's refusal, so only the interrupt marker may end its turn."""
    from aipager.dtach import hook_reply
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    decide = MagicMock(return_value=True)
    monkeypatch.setattr(hook_reply, "send_decision", decide)
    bot._watch_keystroke_answer = MagicMock()
    run_async(bot._auto_deny(sess, {"name": "AskUserQuestion", "summary": ""}, None,
                             {"addr": "/nonexistent/reply.sock", "request_id": "req-1"}))
    decide.assert_not_called()
    assert [c.args[1] for c in sent.await_args_list] == ["Escape"]
    assert bot._watch_keystroke_answer.call_args.kwargs["refusal"] is False


def _two_due_refusals(sess, tools=("Write", "Edit")):
    sess.extra_prompts = [
        {"text": "", "perm": {"tool_info": {"name": tool}}, "auto_deny_due": True}
        for tool in tools]


def _stamping_keys(events):
    from aipager.dtach import inject

    async def _keys(name, key):
        inject._note_write(name)
        events.append(key)
        return True
    return _keys


def test_two_due_auto_denies_are_not_typed_back_to_back(mk_bot, run_async, monkeypatch):
    """Two Escapes in a row are Claude Code's double Escape (it interrupts
    the turn): the second waits out the settle time from the first."""
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    events = []

    async def _pause(seconds):
        events.append(("pause", round(seconds, 1)))
        inject._LAST_WRITE_AT["claude-jim"] -= seconds     # the time passes
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")
    _two_due_refusals(sess)

    run_async(bot._run_due_auto_denies(sess))
    assert events == ["Escape", ("pause", auth.AUTO_DENY_SETTLE_SECONDS), "Escape"]
    assert auth.AUTO_DENY_SETTLE_SECONDS >= 0.3
    assert sess.open_prompts() == []


def test_a_due_auto_deny_waits_after_a_persons_typed_answer(mk_bot, run_async, monkeypatch):
    """A person's answer typed into the dialog above (an Enter for Allow, an
    Escape for Deny) just now: the refusal below waits before its Escape."""
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    inject._note_write("claude-jim")          # the person's key, just now
    events = []

    async def _pause(seconds):
        events.append("pause")
        inject._LAST_WRITE_AT["claude-jim"] -= seconds     # the time passes
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")
    _two_due_refusals(sess, tools=("Write",))

    run_async(bot._run_due_auto_denies(sess))
    assert events == ["pause", "Escape"]


def test_a_due_auto_deny_no_longer_on_top_after_the_pause_waits(mk_bot, run_async,
                                                               monkeypatch):
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    events = []
    verdicts = iter(["yes", "yes", "behind"])
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: next(verdicts, "behind"))

    async def _pause(seconds):
        pass
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)
    _two_due_refusals(sess)

    run_async(bot._run_due_auto_denies(sess))
    assert events == ["Escape"]
    assert [rec["auto_deny_due"] for rec in sess.extra_prompts] == [True]
    assert sess.status == Status.INTERACTIVE


def test_a_refusal_waiting_out_the_pause_is_never_promoted(mk_bot, run_async, monkeypatch):
    """The prompt above closes during the pause: the refusal waiting to be
    typed stays a waiting refusal (never the primary prompt, which would
    show nothing and type nothing) and is typed once the pause is over."""
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    inject._note_write("claude-jim")
    primary = {"tool_info": {"name": "AskUserQuestion"}}
    sess.pending_permission = primary
    _two_due_refusals(sess, tools=("Write",))
    refusal = sess.extra_prompts[0]
    events = []

    async def _pause(seconds):
        # The person answers the question above meanwhile.
        sess.close_prompts(lambda p: p is primary)
        events.append(("promoted", sess.pending_prompt_msg is refusal))
        inject._LAST_WRITE_AT["claude-jim"] -= seconds     # the time passes
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")

    run_async(bot._run_due_auto_denies(sess))
    assert events == [("promoted", False), "Escape"]
    assert sess.open_prompts() == []


def test_a_refusal_left_waiting_after_the_pause_is_typed_by_a_later_run(mk_bot, run_async,
                                                                        monkeypatch):
    """No longer on top once the pause is over: it waits for a later run,
    unmarked (a mark left behind would make every later run skip it, and
    the session would sit on a dialog nobody refuses)."""
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    inject._note_write("claude-jim")
    _two_due_refusals(sess, tools=("Write",))
    events = []
    verdicts = iter(["yes", "behind"])
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: next(verdicts, "yes"))

    async def _pause(seconds):
        inject._LAST_WRITE_AT["claude-jim"] -= seconds     # the time passes
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)

    run_async(bot._run_due_auto_denies(sess))
    assert events == []
    assert "auto_deny_typing" not in sess.extra_prompts[0]
    run_async(bot._run_due_auto_denies(sess))
    assert events == ["Escape"]


def test_a_refusal_whose_pause_was_cancelled_is_typed_by_a_later_run(mk_bot, run_async,
                                                                     monkeypatch):
    import asyncio

    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    inject._note_write("claude-jim")
    _two_due_refusals(sess, tools=("Write",))
    events = []
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")
    cancelled = []

    async def _pause(seconds):
        if not cancelled:
            cancelled.append(True)
            raise asyncio.CancelledError
        inject._LAST_WRITE_AT["claude-jim"] -= seconds     # the time passes
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)

    with pytest.raises(asyncio.CancelledError):
        run_async(bot._run_due_auto_denies(sess))
    assert sess.extra_prompts[0].get("auto_deny_due") is True
    assert "auto_deny_typing" not in sess.extra_prompts[0]
    run_async(bot._run_due_auto_denies(sess))
    assert events == ["Escape"]


def test_a_refusal_is_not_typed_once_the_session_stopped_waiting(mk_bot, run_async,
                                                                 monkeypatch):
    """The session left its prompts during the pause (a Stop, /stop): no
    Escape into whatever Claude Code shows next."""
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    inject._note_write("claude-jim")
    _two_due_refusals(sess, tools=("Write",))
    events = []
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")

    async def _pause(seconds):
        sess.status = Status.BUSY
        inject._LAST_WRITE_AT["claude-jim"] -= seconds     # the time passes
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)

    run_async(bot._run_due_auto_denies(sess))
    assert events == []


def test_a_key_typed_during_the_pause_is_waited_out_too(mk_bot, run_async, monkeypatch):
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    inject._note_write("claude-jim")
    _two_due_refusals(sess, tools=("Write",))
    events = []
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")

    async def _pause(seconds):
        events.append("pause")
        if events.count("pause") == 1:
            inject._note_write("claude-jim")              # someone's key, now
        else:
            inject._LAST_WRITE_AT["claude-jim"] -= seconds
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)

    run_async(bot._run_due_auto_denies(sess))
    assert events == ["pause", "pause", "Escape"]


def test_a_refusal_closed_during_the_pause_is_not_typed(mk_bot, run_async, monkeypatch):
    """Its dialog went away meanwhile (answered in the terminal, its agent
    stopped): no Escape for a prompt that is no longer there."""
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    inject._note_write("claude-jim")
    sess.pending_permission = {"tool_info": {"name": "AskUserQuestion"}}
    _two_due_refusals(sess, tools=("Write",))
    gone = sess.extra_prompts[0]["perm"]
    events = []

    async def _pause(seconds):
        sess.close_prompts(lambda p: p is gone)
        inject._LAST_WRITE_AT["claude-jim"] -= seconds     # the time passes
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")

    run_async(bot._run_due_auto_denies(sess))
    assert events == []


def test_a_refusal_waiting_out_the_pause_is_not_typed_twice(mk_bot, run_async, monkeypatch):
    """Another run during the pause (an event, a tap) skips the refusal
    the first run is about to type."""
    from aipager.bot import auth
    from aipager.dtach import inject
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr(inject, "_LAST_WRITE_AT", {})
    inject._note_write("claude-jim")
    _two_due_refusals(sess, tools=("Write",))
    events = []
    nested = []

    async def _pause(seconds):
        if not nested:
            nested.append(True)
            await bot._run_due_auto_denies(sess)
            events.append("other run done")
        inject._LAST_WRITE_AT["claude-jim"] -= seconds     # the time passes
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _stamping_keys(events))
    monkeypatch.setattr(auth, "_sleep", _pause)
    monkeypatch.setattr(sess, "keys_verdict", lambda perm, now: "yes")

    run_async(bot._run_due_auto_denies(sess))
    # The other run left it alone; the first typed it once, after its pause.
    assert events == ["other run done", "Escape"]


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


# ---- _can_prompt_user (batch 4: session-scoped preference writes) --------
#
# The bar for the Mini App's per-session preference PUT/DELETE — see
# design.md's Authorization section. Legacy team-mode + personal-mode
# coverage lives here; scope mode (the primary path today) is covered in
# test_bot_auth_scoped.py alongside its `_is_admin_user` asymmetry check.

def test_can_prompt_user_personal_mode_always_true(mk_bot):
    bot = mk_bot()  # team=None, scopes=None
    assert bot._can_prompt_user(12345, -100) is True
    assert bot._can_prompt_user(None, None) is True


def test_can_prompt_user_team_mode_admin_and_developer_true(mk_bot):
    bot = mk_bot()
    bot.team = _team(_admin(), _developer())
    assert bot._can_prompt_user(1, -100) is True
    assert bot._can_prompt_user(2, -100) is True


def test_can_prompt_user_team_mode_read_only_false(mk_bot):
    """READ_ONLY can authenticate + GET in the Mini App, but must never be
    handed a capability they have nowhere else — the escalation this gate
    exists to close."""
    bot = mk_bot()
    bot.team = _team(_readonly())
    assert bot._can_prompt_user(3, -100) is False


def test_can_prompt_user_team_mode_unknown_user_false(mk_bot):
    bot = mk_bot()
    bot.team = _team(_admin())
    assert bot._can_prompt_user(999, -100) is False


def test_can_prompt_user_team_mode_none_user_id_false(mk_bot):
    bot = mk_bot()
    bot.team = _team(_admin())
    assert bot._can_prompt_user(None, -100) is False


def test_can_prompt_user_developer_true_but_is_admin_user_false(mk_bot):
    """The asymmetry the whole design decision rests on: a developer may
    set a session override (can_prompt) but may NOT change the scope-wide
    setting (not admin). Same user, same chat, two different answers from
    two deliberately different gates."""
    bot = mk_bot()
    bot.team = _team(_developer())
    assert bot._can_prompt_user(2, -100) is True
    assert bot._is_admin_user(2, -100) is False
