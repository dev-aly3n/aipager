"""Tests for aipager.bot.session_ops.SessionOpsMixin.

The session-operation methods (_stop_session, _kill_session_by_label,
_stop_by_label, _switch_session, _guess_session_from_text) handle the
"do something with session X" flows. Each is exercised here so any
silent break in the registry / dtach plumbing surfaces in CI.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock


from aipager.state import Status, TrackedSession


# ---- _stop_session -------------------------------------------------------

def test_stop_session_sends_two_escapes(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    run_async(bot._stop_session(sess))
    keys = [c.args[1] for c in sent.await_args_list]
    assert keys == ["Escape", "Escape"]


def test_stop_session_discards_pending_queue(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.queue_prompt("a", 1)
    sess.queue_prompt("b", 2)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    run_async(bot._stop_session(sess))
    assert sess.pending_queue == []
    assert sess.status == Status.IDLE


def test_stop_session_via_query_edits_message(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    run_async(bot._stop_session(sess, query=query))
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_awaited_once()


def test_stop_session_via_update_reacts_with_emoji(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    bot._react = AsyncMock()
    update = mk_update("/stop")
    run_async(bot._stop_session(sess, update=update))
    bot._react.assert_awaited_once()


def test_stop_session_swallows_query_edit_failure(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock(side_effect=RuntimeError("old"))
    # MUST NOT raise
    run_async(bot._stop_session(sess, query=query))


# ---- _kill_session_by_label ---------------------------------------------

def test_kill_session_finds_in_registry(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    bot._update_bot_commands = AsyncMock()
    update = mk_update("/kill jim")
    run_async(bot._kill_session_by_label(update, "jim"))
    update.message.reply_text.assert_awaited_once()
    assert "Killed" in update.message.reply_text.await_args.args[0]
    assert bot.registry.get("claude-jim") is None


def test_kill_session_via_query_uses_edit(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    bot._update_bot_commands = AsyncMock()
    query = MagicMock()
    query.message = None
    query.edit_message_text = AsyncMock()
    run_async(bot._kill_session_by_label(query, "jim"))
    query.edit_message_text.assert_awaited_once()


def test_kill_session_kill_returns_false_warns(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=False))
    update = mk_update("/kill jim")
    run_async(bot._kill_session_by_label(update, "jim"))
    text = update.message.reply_text.await_args.args[0]
    assert "not found" in text


def test_kill_session_label_not_in_registry_falls_back_to_claude_prefix(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=False))
    update = mk_update("/kill nonexistent")
    run_async(bot._kill_session_by_label(update, "nonexistent"))
    text = update.message.reply_text.await_args.args[0]
    assert "not found" in text


# ---- _stop_by_label -----------------------------------------------------

def test_stop_by_label_busy_session_invokes_stop(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot._stop_session = AsyncMock()
    update = mk_update("/jim stop")
    run_async(bot._stop_by_label(update, "jim"))
    bot._stop_session.assert_awaited_once()


def test_stop_by_label_idle_session_replies_not_busy(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    update = mk_update("/jim stop")
    run_async(bot._stop_by_label(update, "jim"))
    text = update.message.reply_text.await_args.args[0]
    assert "not busy" in text


def test_stop_by_label_unknown_label_replies_unknown(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/nope stop")
    run_async(bot._stop_by_label(update, "nope"))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown" in text


# ---- _guess_session_from_text -------------------------------------------

def test_guess_session_finds_unambiguous_match(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    assert bot._guess_session_from_text("⚙️ jim · Working…") is sess


def test_guess_session_returns_none_for_no_text(mk_bot):
    bot = mk_bot()
    assert bot._guess_session_from_text("") is None
    assert bot._guess_session_from_text(None) is None


def test_guess_session_returns_none_when_ambiguous(mk_bot):
    bot = mk_bot()
    s1 = TrackedSession(name="claude-a", label="a", status=Status.IDLE)
    s2 = TrackedSession(name="claude-b", label="b", status=Status.IDLE)
    bot.registry._sessions["claude-a"] = s1
    bot.registry._sessions["claude-b"] = s2
    # Both labels appear → ambiguous, return None
    assert bot._guess_session_from_text("a · b · ?") is None


def test_guess_session_skips_gone_sessions(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    bot.registry._sessions["claude-jim"] = sess
    assert bot._guess_session_from_text("⚙️ jim · Working") is None


def test_guess_session_word_boundary(mk_bot):
    """`jim` should match standalone but not inside another word."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    # Should NOT match "jimmy" or "majim"
    assert bot._guess_session_from_text("jimmy and majim") is None


# ---- _switch_session ----------------------------------------------------

def test_switch_session_existing_session(mk_bot, mk_update, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._build_session_dashboard = MagicMock(return_value="<dashboard>")
    update = mk_update("/jim")
    run_async(bot._switch_session(update, "jim"))
    assert bot.registry.last_active_session == "claude-jim"
    update.message.reply_text.assert_awaited_once()


def test_switch_session_auto_discovers_alive(mk_bot, mk_update, run_async, monkeypatch):
    """Bare /<label> when no registry entry but socket is alive → create entry."""
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._build_session_dashboard = MagicMock(return_value="<dashboard>")
    bot._update_bot_commands = AsyncMock()
    update = mk_update("/discovered")
    run_async(bot._switch_session(update, "discovered"))
    assert bot.registry.get("claude-discovered") is not None
    update.message.reply_text.assert_awaited_once()


def test_switch_session_unknown_warns(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update = mk_update("/nope")
    run_async(bot._switch_session(update, "nope"))
    text = update.message.reply_text.await_args.args[0]
    assert "Unknown" in text


# ---- _do_resume (already covered in test_telegram_bot_resume.py but
#       hits the session_ops module path now after restructure)

def test_do_resume_no_session_in_registry(mk_bot, run_async):
    bot = mk_bot()
    reply = AsyncMock()
    run_async(bot._do_resume(label="jim", reply_fn=reply))
    text = reply.await_args.args[0]
    assert "No session named" in text


def test_do_resume_session_alive_rejects(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    reply = AsyncMock()
    run_async(bot._do_resume(label="jim", reply_fn=reply))
    text = reply.await_args.args[0]
    assert "already running" in text


def test_do_resume_session_without_id_rejects(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.gone_at = 1234.0
    # No claude_session_id
    bot.registry._sessions["claude-jim"] = sess
    reply = AsyncMock()
    run_async(bot._do_resume(label="jim", reply_fn=reply))
    text = reply.await_args.args[0]
    assert "no resumable transcript" in text


def test_do_resume_happy_path_restores_session(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    sess.cwd = "/x"
    sess.gone_at = 1234.0
    sess.last_assistant_preview = "what I did"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    bot._build_session_dashboard = MagicMock(return_value="dashboard text")
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    reply = AsyncMock()
    run_async(bot._do_resume(label="jim", reply_fn=reply))
    text = reply.await_args.args[0]
    assert "Resumed" in text
    assert "what I did" in text
    # Status restored
    assert sess.status != Status.GONE
    assert sess.gone_at is None


def test_do_resume_failure_restores_session_id(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(False, "dtach broken")))
    reply = AsyncMock()
    run_async(bot._do_resume(label="jim", reply_fn=reply))
    text = reply.await_args.args[0]
    assert "Couldn't resume" in text
    # The claude_session_id is restored so user can retry
    assert sess.claude_session_id == "UUID-1"
    assert sess.status == Status.GONE


# ---- _stop_session_core / _kill_session_core / _do_resume_core ----------
#
# The shared seam both chat's wrappers above AND the Mini App's routes
# call. Each refusal is the FIRST statement, before any await — several
# tests below prove this directly by making the very next dtach call
# raise if it is ever reached, rather than merely asserting the return
# value (design.md's "no awaits before this check").

def test_stop_session_core_refuses_when_idle(mk_bot, run_async, monkeypatch):
    """A non-busy session must refuse before touching dtach at all."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess

    async def _boom(*args, **kwargs):
        raise AssertionError("inject.send_keys must not run for a non-busy session")
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)

    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok is False
    assert outcome.label == "jim"
    assert outcome.dropped == 0


def test_stop_session_core_refuses_when_gone(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok is False


def test_stop_session_core_ok_true_with_dropped_count(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.queue_prompt("a", 1)
    sess.queue_prompt("b", 2)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()

    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok is True
    assert outcome.label == "jim"
    assert outcome.dropped == 2
    assert sess.status == Status.IDLE


def test_kill_session_core_returns_killed(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    bot._update_bot_commands = AsyncMock()

    outcome = run_async(bot._kill_session_core("claude-jim", "jim"))
    assert outcome.result == "killed"
    assert outcome.label == "jim"
    assert outcome.session_name == "claude-jim"
    assert bot.registry.get("claude-jim") is None


def test_kill_session_core_returns_still_running_when_alive_but_not_killed(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=False))
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))

    outcome = run_async(bot._kill_session_core("claude-jim", "jim"))
    assert outcome.result == "still_running"
    assert outcome.label == "jim"


def test_kill_session_core_returns_not_found(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=False))
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))

    outcome = run_async(bot._kill_session_core("claude-jim", "jim"))
    assert outcome.result == "not_found"
    assert outcome.label == "jim"


def test_do_resume_core_refuses_when_not_gone(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)

    async def _boom(*args, **kwargs):
        raise AssertionError("inject.launch_session must not run when not GONE")
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)

    outcome = run_async(bot._do_resume_core(sess))
    assert outcome.ok is False
    assert outcome.reason == "not_gone"


def test_do_resume_core_refuses_when_no_transcript(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    # No claude_session_id set — default is "".

    async def _boom(*args, **kwargs):
        raise AssertionError("inject.launch_session must not run with no transcript")
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)

    outcome = run_async(bot._do_resume_core(sess))
    assert outcome.ok is False
    assert outcome.reason == "no_transcript"


def test_do_resume_core_launch_failure_restores_id(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(False, "dtach broken")))

    outcome = run_async(bot._do_resume_core(sess))
    assert outcome.ok is False
    assert outcome.reason == "launch_failed"
    assert outcome.err == "dtach broken"
    assert sess.claude_session_id == "UUID-1"
    assert sess.status == Status.GONE


def test_do_resume_core_happy_path_transitions_idle(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    sess.gone_at = 1234.0
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    outcome = run_async(bot._do_resume_core(sess))
    assert outcome.ok is True
    assert outcome.reason == "resumed"
    assert sess.status == Status.IDLE
    assert sess.gone_at is None


def test_do_resume_core_sets_driver_user_id_directly(mk_bot, run_async, monkeypatch):
    """Locks in the design's "Unknown 2" decision: a given
    ``driver_user_id`` is stamped directly onto the session rather than
    routed through ``_mark_driver`` (which needs an ``Update`` the Mini
    App never has)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    outcome = run_async(bot._do_resume_core(sess, driver_user_id=777))
    assert outcome.ok is True
    assert sess.last_driver_user_id == 777
    assert sess.created_by_user_id == 777


def test_do_resume_core_keeps_existing_created_by_user_id(mk_bot, run_async, monkeypatch):
    """created_by_user_id is first-touch-only — a resume must not
    overwrite who originally created the session, only who is currently
    driving it."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    sess.created_by_user_id = 111
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    outcome = run_async(bot._do_resume_core(sess, driver_user_id=777))
    assert outcome.ok is True
    assert sess.last_driver_user_id == 777
    assert sess.created_by_user_id == 111


def test_do_resume_core_no_driver_when_none_given(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    outcome = run_async(bot._do_resume_core(sess))
    assert outcome.ok is True
    assert sess.last_driver_user_id is None
    assert sess.created_by_user_id is None


# ---- _kill_and_relaunch_core / _perms_switch_core / _restart_session_core -
#
# The single kill/poll/relaunch seam chat's /perms (both branches) and the
# Mini App's perms + restart routes all go through now (design.md
# ORCHESTRATOR OVERRIDE). asyncio.sleep is neutered so no test actually
# waits out the 0.5s Ctrl-C pause or the up-to-3s poll loop; the socket
# poll itself is controlled via aipager.bot.session_ops.Path so the
# still-stopping/success branches are deterministic regardless of what is
# or isn't a real file on this machine.

def _neuter_sleep(monkeypatch):
    async def _no_sleep(_):
        pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)


def _fake_path_cls(exists):
    return MagicMock(return_value=MagicMock(
        is_socket=MagicMock(return_value=exists),
    ))


def test_kill_and_relaunch_core_refuses_when_not_live(mk_bot, run_async, monkeypatch):
    """GONE/UNKNOWN must refuse before touching dtach at all - no awaits,
    no side effects, so the refusal is testable with zero mocking."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)

    async def _boom(*a, **k):
        raise AssertionError("dtach must not be touched for a non-live session")
    monkeypatch.setattr("aipager.dtach.inject.kill_session", _boom)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_skip_perms=True, interrupt_first=False,
    ))
    assert outcome.ok is False
    assert outcome.reason == "not_live"
    assert outcome.label == "jim"


def test_kill_and_relaunch_core_refuses_when_already_restarting(
    mk_bot, run_async, monkeypatch,
):
    """A second kill/relaunch must not start while one is already in
    flight - the guard a stale Mini App menu or a double-tap needs."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.restarting_until = time.monotonic() + 10.0

    async def _boom(*a, **k):
        raise AssertionError("dtach must not be touched while already restarting")
    monkeypatch.setattr("aipager.dtach.inject.kill_session", _boom)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_skip_perms=True, interrupt_first=False,
    ))
    assert outcome.ok is False
    assert outcome.reason == "already_restarting"


def test_kill_and_relaunch_core_interrupt_first_sends_ctrl_c_not_kill(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", send_keys)
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill_session)
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_skip_perms=True, interrupt_first=True,
    ))
    assert outcome.ok is True
    send_keys.assert_awaited_once_with("claude-jim", "C-c")
    kill_session.assert_not_awaited()


def test_kill_and_relaunch_core_hard_kill_not_ctrl_c(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", send_keys)
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill_session)
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_skip_perms=False, interrupt_first=False,
    ))
    assert outcome.ok is True
    kill_session.assert_awaited_once_with("claude-jim")
    send_keys.assert_not_awaited()


def test_kill_and_relaunch_core_still_stopping_clears_restarting_until(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    # Socket never disappears -> the poll loop exhausts its budget.
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(True))
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))

    async def _boom(*a, **k):
        raise AssertionError("launch_session must not run after a still_stopping timeout")
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_skip_perms=True, interrupt_first=False,
    ))
    assert outcome.ok is False
    assert outcome.reason == "still_stopping"
    assert sess.restarting_until == 0.0


def test_kill_and_relaunch_core_launch_failed_clears_restarting_until(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(False, "dtach broken")))

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_skip_perms=True, interrupt_first=False,
    ))
    assert outcome.ok is False
    assert outcome.reason == "launch_failed"
    assert outcome.err == "dtach broken"
    assert sess.restarting_until == 0.0


def test_kill_and_relaunch_core_success_restores_state_and_transitions_idle(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.claude_session_id = "uuid-1"
    sess.cwd = "/home/user/project"
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_skip_perms=True, interrupt_first=False,
    ))
    assert outcome.ok is True
    assert outcome.reason == "done"
    assert outcome.skip_perms is True
    assert sess.skip_perms is True
    assert sess.claude_session_id == "uuid-1"
    assert sess.cwd == "/home/user/project"
    assert sess.status == Status.IDLE
    launch.assert_awaited_once()
    assert launch.await_args.kwargs["resume_id"] == "uuid-1"
    assert launch.await_args.kwargs["cwd"] == "/home/user/project"


def test_perms_switch_core_busy_interrupts_first(mk_bot, run_async, monkeypatch):
    """The perms wrapper's interrupt_first=True derivation for BUSY."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", send_keys)
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill_session)
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))

    outcome = run_async(bot._perms_switch_core(sess, True))
    assert outcome.ok is True
    send_keys.assert_awaited_once_with("claude-jim", "C-c")
    kill_session.assert_not_awaited()


def test_perms_switch_core_idle_hard_kills(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", send_keys)
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill_session)
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))

    outcome = run_async(bot._perms_switch_core(sess, True))
    assert outcome.ok is True
    kill_session.assert_awaited_once_with("claude-jim")
    send_keys.assert_not_awaited()


def test_perms_switch_core_waiting_hard_kills_like_idle(mk_bot, run_async, monkeypatch):
    """chat's own /perms folds INTERACTIVE into the same IDLE flow - the
    core reproduces that fold, so a waiting session hard-kills too."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", send_keys)
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill_session)
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))

    outcome = run_async(bot._perms_switch_core(sess, True))
    assert outcome.ok is True
    kill_session.assert_awaited_once_with("claude-jim")
    send_keys.assert_not_awaited()


def test_restart_session_core_hard_kills_even_when_busy(mk_bot, run_async, monkeypatch):
    """Restart deliberately does NOT reuse perms' busy-only Ctrl-C
    courtesy - a user-invoked restart is Kill's hard-kill semantics."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.skip_perms = False
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", send_keys)
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill_session)
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)

    outcome = run_async(bot._restart_session_core(sess))
    assert outcome.ok is True
    kill_session.assert_awaited_once_with("claude-jim")
    send_keys.assert_not_awaited()
    # skip_perms is unchanged - a restart is not a mode switch.
    assert launch.await_args.kwargs["skip_perms"] is False


# ---- _clear_queue_core ----------------------------------------------------

def test_clear_queue_core_refuses_when_empty(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.ok is False
    assert outcome.dropped == 0


def test_clear_queue_core_clears_and_reports_dropped_count(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.queue_prompt("a", 1)
    sess.queue_prompt("b", 2)
    bot.registry._sessions["claude-jim"] = sess

    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.ok is True
    assert outcome.dropped == 2
    assert sess.pending_queue == []


# ---- _compact_session_core -------------------------------------------------

def test_compact_session_core_refuses_when_not_live(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)

    async def _boom(*a, **k):
        raise AssertionError("must not touch dtach for a non-live session")
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", _boom)

    outcome = run_async(bot._compact_session_core(sess))
    assert outcome.ok is False
    assert outcome.reason == "not_live"


def test_compact_session_core_busy_queues_the_slash_command(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess

    outcome = run_async(bot._compact_session_core(sess))
    assert outcome.ok is True
    assert outcome.reason == "queued"
    assert sess.pending_queue == [("/compact", None, sess.pending_queue[0][2])]


def test_compact_session_core_busy_refuses_when_queue_is_full(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    for i in range(50):
        sess.queue_prompt(f"msg{i}", i)
    bot.registry._sessions["claude-jim"] = sess

    outcome = run_async(bot._compact_session_core(sess))
    assert outcome.ok is False
    assert outcome.reason == "queue_full"
    assert len(sess.pending_queue) == 50  # unchanged


def test_compact_session_core_idle_sends_immediately(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", send)

    outcome = run_async(bot._compact_session_core(sess))
    assert outcome.ok is True
    assert outcome.reason == "sent"
    send.assert_awaited_once()
    assert send.await_args.args == ("claude-jim", "/compact")
    # No pending_queue growth on the immediate path.
    assert sess.pending_queue == []


def test_compact_session_core_idle_send_failed(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))

    outcome = run_async(bot._compact_session_core(sess))
    assert outcome.ok is False
    assert outcome.reason == "send_failed"


# ---- _rename_session_core --------------------------------------------------

def test_rename_session_core_no_op_when_unchanged(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._update_bot_commands = AsyncMock()
    bot.registry._dirty = False

    outcome = run_async(bot._rename_session_core(sess, "jim"))
    assert outcome.ok is True
    assert outcome.changed is False
    assert outcome.previous_label == "jim"
    assert outcome.new_label == "jim"
    assert sess.label == "jim"
    assert bot.registry._dirty is False
    bot._update_bot_commands.assert_not_awaited()


def test_rename_session_core_real_change_schedules_command_refresh(
    mk_bot, run_async, monkeypatch,
):
    """_update_bot_commands must be scheduled ONLY on a real change - a
    no-op rename must not touch chat's `/`-command list at all."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess

    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()  # avoid an "unawaited coroutine" warning
        return MagicMock()
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.create_task", fake_create_task)

    outcome = run_async(bot._rename_session_core(sess, "james"))
    assert outcome.ok is True
    assert outcome.changed is True
    assert outcome.previous_label == "jim"
    assert outcome.new_label == "james"
    assert sess.label == "james"
    assert bot.registry._dirty is True
    assert len(scheduled) == 1
