"""Tests for aipager.bot.session_ops.SessionOpsMixin.

The session-operation methods (_stop_session, _kill_session_by_label,
_stop_by_label, _switch_session, _guess_session_from_text) handle the
"do something with session X" flows. Each is exercised here so any
silent break in the registry / dtach plumbing surfaces in CI.
"""

from __future__ import annotations

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
