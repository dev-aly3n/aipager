"""Tests for aipager.bot.callbacks.CallbackDispatchMixin._handle_callback.

The dispatcher routes inline-button taps based on ``callback_data`` of
the form ``"<session_name>:<action>"``. Each action gets its own
branch; we test the easy paths here (early returns + simple state
mutations) and avoid the deep tool-permission injection paths that
require full dtach key-injection mocking.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def mk_query():
    """Build a mocked Telegram CallbackQuery."""
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        return update, query
    return _mk


# ---- early returns -------------------------------------------------------

def test_invalid_callback_no_colon(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("invalid-no-colon")
    run_async(bot._handle_callback(update, MagicMock()))
    # First answer is the eager-ack (no text — None). Then the toast.
    answers = [c.args[0] if c.args else None for c in query.answer.await_args_list]
    assert any(a and "Invalid callback" in a for a in answers)


def test_team_mode_unauthorized_returns_early(mk_bot, mk_query, run_async):
    """When _authorize_callback returns None (unauthorized), handler exits."""
    bot = mk_bot()
    bot._authorize_callback = AsyncMock(return_value=None)
    bot._stop_session = AsyncMock()
    update, query = mk_query("claude-jim:stop", user_id=99999)
    run_async(bot._handle_callback(update, MagicMock()))
    # No action method should have been called
    bot._stop_session.assert_not_awaited()


# ---- action: stop ------------------------------------------------------

def test_stop_with_unknown_session(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-nope:stop")
    run_async(bot._handle_callback(update, MagicMock()))
    # Should toast "Session not found"
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_stop_with_existing_session_invokes_stop_session(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot._stop_session = AsyncMock()
    update, query = mk_query("claude-jim:stop")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._stop_session.assert_awaited_once()


def test_stop_callback_toasts_when_not_busy(mk_bot, mk_query, run_async, monkeypatch):
    """The one deliberate, narrow behaviour change the refactor
    introduces (design.md): the `:stop` callback previously had NO
    status guard at all and would silently run its full body on a
    non-busy session. It now refuses through the shared core exactly
    like every other stop path, and says so instead of no-opping.

    Real `_stop_session` runs here (not mocked) so the guard under test
    is the only thing that can produce this toast — asserting
    inject.send_keys was never called proves the guard ran before any
    dtach interaction, not just that SOME toast happened to appear.
    """
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess

    async def _boom(*args, **kwargs):
        raise AssertionError("inject.send_keys must not run for a non-busy session")
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)

    update, query = mk_query("claude-jim:stop")
    run_async(bot._handle_callback(update, MagicMock()))

    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not busy" in (a or "").lower() for a in answers)
    assert sess.status == Status.IDLE


# ---- action: kill / kill-confirm / kill-cancel -------------------------

def test_kill_calls_kill_by_label(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._kill_session_by_label = AsyncMock()
    update, query = mk_query("claude-jim:kill")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._kill_session_by_label.assert_awaited_once()


def test_kill_confirm_calls_kill_by_label(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._kill_session_by_label = AsyncMock()
    update, query = mk_query("claude-jim:kill-confirm")
    run_async(bot._handle_callback(update, MagicMock()))
    bot._kill_session_by_label.assert_awaited_once()


def test_kill_cancel_edits_message(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-jim:kill-cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert "Cancelled" in text


def test_kill_cancel_swallows_edit_failure(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-jim:kill-cancel")
    query.edit_message_text = AsyncMock(side_effect=RuntimeError("boom"))
    # MUST NOT raise
    run_async(bot._handle_callback(update, MagicMock()))


# ---- voice extra subactions --------------------------------------------

def test_voice_cancel_edits_message(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("__voice__:cancel")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    assert "not installed" in query.edit_message_text.await_args.args[0]


def test_voice_install_fires_task(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._install_voice_extra = AsyncMock()
    update, query = mk_query("__voice__:install")
    run_async(bot._handle_callback(update, MagicMock()))
    # task scheduled — we can't await it but the AsyncMock should
    # at least have been bound; just verify no crash
    # (create_task means the call site is reached)


def test_voice_restart_fires_task(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._restart_daemon = AsyncMock()
    update, query = mk_query("__voice__:restart")
    run_async(bot._handle_callback(update, MagicMock()))


def test_voice_unknown_action_is_noop(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("__voice__:bogus")
    run_async(bot._handle_callback(update, MagicMock()))


# ---- action: retry ------------------------------------------------------

def test_retry_with_no_session_toasts(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-nope:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_retry_with_no_last_prompt_toasts(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = ""  # nothing to retry
    bot.registry._sessions["claude-jim"] = sess
    update, query = mk_query("claude-jim:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Nothing to retry" in (a or "") for a in answers)


def test_retry_with_dead_session_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = "do thing"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not alive" in (a or "").lower() for a in answers)


def test_retry_happy_path(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = "the prompt"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    update, query = mk_query("claude-jim:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    assert sess.status == Status.BUSY
    bot._send_busy_and_animate.assert_awaited_once()


@pytest.mark.parametrize("author,tapper,expected", [
    (777, 777, 777),     # your own prompt: runs as you (an owner keeps Bash)
    (555, 777, None),    # someone else's text: no borrowed rights -> floor
    (None, 777, None),   # author unknown: floor
])
def test_retry_runs_as_the_tapper_only_for_their_own_prompt(
        mk_bot, mk_query, run_async, monkeypatch, author, tapper, expected):
    """Roadmap 8.50, 2026-09-27: a Retry wrote its policy note with no
    sender, so the turn ran on the floor — which now denies Bash, so an
    owner's own Retry would lose it. It runs as the tapper when the
    tapper sent the prompt; never lends the tapper's rights to someone
    else's text (test_retry_privilege_attribution.py)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = "the prompt"
    sess.last_prompt_driver_user_id = author
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._send_busy_and_animate = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    update, query = mk_query("claude-jim:retry", user_id=tapper)
    run_async(bot._handle_callback(update, MagicMock()))
    bot._inject_prompt.assert_awaited_once()
    assert bot._inject_prompt.await_args.kwargs["driver_user_id"] == expected


def test_retry_send_text_fails_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_prompt = "x"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:retry")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to retry" in (a or "") for a in answers)


# ---- action: compact ---------------------------------------------------

def test_compact_with_no_session_toasts(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("claude-nope:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_compact_with_dead_session_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_compact_happy_path_sends_slash_compact(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", sent)
    bot._app.bot.delete_message = AsyncMock()
    update, query = mk_query("claude-jim:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "/compact"


def test_compact_send_failure_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:compact")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to send" in (a or "") for a in answers)


# ---- action: clear_gone ------------------------------------------------

def test_clear_gone_with_no_gone_sessions(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    # Insert one alive session
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    update, query = mk_query("anything:clear_gone")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("No gone sessions" in (a or "") for a in answers)
    # Session still present
    assert bot.registry.get("claude-jim") is not None


def test_clear_gone_hides_session_preserves_resume_id(mk_bot, mk_query, run_async):
    bot = mk_bot()
    s = TrackedSession(name="claude-old", label="old", status=Status.GONE)
    s.claude_session_id = "abc-uuid"
    s.cwd = "/home/u/proj"
    bot.registry._sessions["claude-old"] = s
    update, query = mk_query("anything:clear_gone")
    run_async(bot._handle_callback(update, MagicMock()))
    # Session NOT removed
    assert bot.registry.get("claude-old") is s
    # Hidden flag flipped
    assert s.hidden_from_status is True
    # Resume metadata preserved
    assert s.claude_session_id == "abc-uuid"
    assert s.cwd == "/home/u/proj"
    # Message edited with the new "Still available in /resume" copy
    query.edit_message_text.assert_awaited_once()
    body = query.edit_message_text.await_args.args[0]
    assert "Hidden from /status" in body
    assert "Still available in /resume" in body


def test_clear_gone_skips_already_hidden(mk_bot, mk_query, run_async):
    bot = mk_bot()
    s1 = TrackedSession(name="claude-a", label="a", status=Status.GONE)
    s2 = TrackedSession(name="claude-b", label="b", status=Status.GONE)
    s2.hidden_from_status = True  # already hidden
    bot.registry._sessions["claude-a"] = s1
    bot.registry._sessions["claude-b"] = s2
    update, query = mk_query("anything:clear_gone")
    run_async(bot._handle_callback(update, MagicMock()))
    assert s1.hidden_from_status is True
    assert s2.hidden_from_status is True
    # Toast says "Hidden 1 session(s)" — only the newly flipped one
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Hidden 1 session" in (a or "") for a in answers)


def test_clear_gone_only_targets_gone_sessions(mk_bot, mk_query, run_async):
    bot = mk_bot()
    busy = TrackedSession(name="claude-busy", label="busy", status=Status.BUSY)
    gone = TrackedSession(name="claude-gone", label="gone", status=Status.GONE)
    bot.registry._sessions["claude-busy"] = busy
    bot.registry._sessions["claude-gone"] = gone
    update, query = mk_query("anything:clear_gone")
    run_async(bot._handle_callback(update, MagicMock()))
    assert gone.hidden_from_status is True
    assert busy.hidden_from_status is False


# ---- action: resume ----------------------------------------------------

def test_resume_callback_shows_mode_picker(mk_bot, mk_query, run_async):
    """Tapping a session in the /resume picker now shows a mode-picker keyboard
    instead of immediately calling _do_resume."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    sess.gone_at = 1234.0
    bot.registry._sessions["claude-jim"] = sess
    bot._do_resume = AsyncMock()
    update, query = mk_query("claude-jim:resume")
    run_async(bot._handle_callback(update, MagicMock()))
    # Mode picker should be shown, not _do_resume called
    bot._do_resume.assert_not_awaited()
    query.edit_message_text.assert_awaited_once()
    # The pending entry should be set
    assert "claude-jim" in bot._resume_mode_pending
    assert bot._resume_mode_pending["claude-jim"] == "jim"


def test_resume_callback_resolves_label_for_suffixed_name(
        mk_bot, mk_query, run_async):
    # Suffixed names (label__d<chat_id>) must resolve to the registry
    # label, not the prefix-stripped internal name — mode picker stores correct label.
    bot = mk_bot()
    sess = TrackedSession(name="claude-newName__d1921747733",
                          label="newName", status=Status.GONE)
    sess.claude_session_id = "UUID-2"
    sess.gone_at = 1234.0
    bot.registry._sessions[sess.name] = sess
    bot._do_resume = AsyncMock()
    update, query = mk_query("claude-newName__d1921747733:resume")
    run_async(bot._handle_callback(update, MagicMock()))
    # Mode picker shown, not direct resume
    bot._do_resume.assert_not_awaited()
    # Pending entry should store the registry label
    assert bot._resume_mode_pending.get("claude-newName__d1921747733") == "newName"


def test_resume_page_edits_picker(mk_bot, mk_query, run_async):
    bot = mk_bot()
    # Populate enough GONE sessions to render a picker
    for i in range(12):
        s = TrackedSession(name=f"claude-old{i:02d}", label=f"old{i:02d}",
                            status=Status.GONE)
        s.gone_at = 1000.0 - i
        s.claude_session_id = f"UUID-{i}"
        bot.registry._sessions[s.name] = s
    update, query = mk_query("_:resume_page:1")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()


def test_resume_page_malformed_index_falls_to_zero(mk_bot, mk_query, run_async):
    bot = mk_bot()
    # Just one GONE session — pagination call should still work
    s = TrackedSession(name="claude-old", label="old", status=Status.GONE)
    s.gone_at = 1234.0
    s.claude_session_id = "x"
    bot.registry._sessions[s.name] = s
    update, query = mk_query("_:resume_page:notanumber")
    run_async(bot._handle_callback(update, MagicMock()))
    # Edit fires (or not), but no exception
    # We just care it didn't crash.


def test_resume_noop_does_nothing(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:resume_noop")
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_not_awaited()


# ---- /new conflict callbacks -------------------------------------------

def test_new_cancel_edits_message(mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._new_conflict_pending["claude-jim"] = {"prompt": "", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}
    update, query = mk_query("claude-jim:new_cancel", user_id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert "Cancelled" in text
    # Pending entry should be popped
    assert "claude-jim" not in bot._new_conflict_pending


def test_new_resume_alive_session_switches(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {"prompt": "go", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}
    update, query = mk_query("claude-jim:new_resume", user_id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    # Switched: last_active_session updated
    assert bot.registry.last_active_session == "claude-jim"
    # Prompt should be queued
    assert any(t == "go" for t, *_ in sess.pending_queue)


def test_new_resume_queues_the_prompt_as_its_author_not_the_tapper(
        mk_bot, mk_query, run_async):
    """review 2026-09-27: the queued prompt is the text the /new AUTHOR
    typed, so it is credited to the author (user 111), never to whoever
    tapped the button, or the tapper's rights would be lent to someone
    else's prompt. Since 2026-09-30 only the author may tap a card with a
    known author; a card whose author is unknown (0) can be tapped by
    anyone who may prompt (222 here), and credits the prompt to nobody."""
    for author, tapper, expected in ((111, 111, 111), (0, 222, None)):
        bot = mk_bot()
        sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
        bot.registry._sessions["claude-jim"] = sess
        bot._new_conflict_pending["claude-jim"] = {
            "prompt": "go", "skip_perms": False, "user_id": author, "msg_id": 5}
        update, _query = mk_query("claude-jim:new_resume", user_id=tapper)
        run_async(bot._handle_callback(update, MagicMock()))
        (entry,) = [e for e in sess.pending_queue if e[0] == "go"]
        assert entry[4] == expected


def test_new_resume_gone_session_routes_to_do_resume(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    bot.registry._sessions["claude-jim"] = sess
    bot._do_resume = AsyncMock()
    bot._new_conflict_pending["claude-jim"] = {"prompt": "", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}
    update, query = mk_query("claude-jim:new_resume", user_id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    bot._do_resume.assert_awaited_once()


def test_new_resume_of_a_gone_session_queues_as_the_author(
        mk_bot, mk_query, run_async):
    """The GONE branch of Resume (review 2026-09-27): the prompt is the
    /new author's (111), queued once the session came back."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    bot.registry._sessions["claude-jim"] = sess

    async def _resumed(**kw):
        sess.status = Status.IDLE
    bot._do_resume = AsyncMock(side_effect=_resumed)
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "go", "skip_perms": False, "user_id": 111, "msg_id": 5}
    update, _query = mk_query("claude-jim:new_resume", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))
    (entry,) = [e for e in sess.pending_queue if e[0] == "go"]
    assert entry[4] == 111


def test_new_replace_queues_the_prompt_as_its_author(
        mk_bot, mk_query, run_async, monkeypatch):
    """Replace (review 2026-09-27): the relaunched session's queued prompt
    is credited to the /new author (111). Only the author may tap Replace
    since 2026-09-30 (see the refusal rows below). A GONE session, so no
    kill and no socket wait is involved."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "go", "skip_perms": False, "user_id": 111, "msg_id": 5}
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    update, _query = mk_query("claude-jim:new_replace", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))
    new_sess = bot.registry.get("claude-jim")
    (entry,) = [e for e in new_sess.pending_queue if e[0] == "go"]
    assert entry[4] == 111


def test_new_replace_starts_fresh_with_the_chosen_folder_and_model_and_ends_ready(
        mk_bot, mk_query, run_async, monkeypatch):
    """Replace starts the fresh session through create_session with the
    mode, folder and model /new chose, in the replaced session's own
    scope, and the conflict card becomes the Ready card (2026-09-30)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "", "skip_perms": True, "user_id": 111, "msg_id": 5,
        "cwd": "/srv/proj", "model": "opus"}
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)

    update, query = mk_query("claude-jim:new_replace", user_id=111)
    update.effective_chat.id = 4242   # tapped in a real DM
    run_async(bot._handle_callback(update, MagicMock()))

    kw = launch.await_args.kwargs
    # An unscoped legacy session stays unscoped: the same name, never
    # the tapping chat's suffix or scope.
    assert launch.await_args.args[0] == "jim"
    assert bot.registry.get("claude-jim").scope_chat_id == 0
    assert (kw["skip_perms"], kw["cwd"], kw["model"]) == (True, "/srv/proj", "opus")
    texts = [str(c.args[0]) if c.args else str(c.kwargs.get("text"))
             for c in query.edit_message_text.await_args_list]
    assert any("jim</b> is ready" in t for t in texts), texts


def test_new_replace_keeps_a_scoped_sessions_own_name(
        mk_bot, mk_query, run_async, monkeypatch):
    """A session scoped to a group is replaced under the same internal
    name, so the fresh one is the same session to that group."""
    from aipager.scope import disambiguated_name

    bot = mk_bot()
    name = disambiguated_name("jim", -100, "group")
    sess = TrackedSession(name=name, label="jim", status=Status.GONE)
    sess.scope_chat_id = -100
    bot.registry._sessions[name] = sess
    bot._new_conflict_pending[name] = {
        "prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 5}
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)

    update, _query = mk_query(f"{name}:new_replace", user_id=111)
    update.effective_chat.id = -100
    run_async(bot._handle_callback(update, MagicMock()))

    assert launch.await_args.args[0] == name.removeprefix("claude-")
    assert bot.registry.get(name).scope_chat_id == -100


def test_new_replace_keeps_a_renamed_sessions_internal_name(
        mk_bot, mk_query, run_async, monkeypatch):
    """/rename changes the label, never the internal name: Replace must
    restart THAT session, not start a second one under a name derived
    from the new label."""
    from aipager.scope import disambiguated_name

    bot = mk_bot()
    name = disambiguated_name("old", -100, "group")
    sess = TrackedSession(name=name, label="jim", status=Status.GONE)
    sess.scope_chat_id = -100
    bot.registry._sessions[name] = sess
    bot._new_conflict_pending[name] = {
        "prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 5}
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)

    update, _query = mk_query(f"{name}:new_replace", user_id=111)
    update.effective_chat.id = -100
    run_async(bot._handle_callback(update, MagicMock()))

    assert launch.await_args.args[0] == name.removeprefix("claude-")
    assert [s.name for s in bot.registry.all_sessions().values()
            if s.label == "jim"] == [name]


def _replace_refusal_setup(mk_bot, monkeypatch, pending):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    if pending is not None:
        bot._new_conflict_pending["claude-jim"] = pending
    kill = AsyncMock()
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill)
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)
    return bot, kill, launch


def test_new_replace_by_someone_else_is_refused_and_kills_nothing(
        mk_bot, mk_query, run_async, monkeypatch):
    """Replace kills a session: only the person who sent /new may tap
    it, and a refused tap leaves the card for its author."""
    pending = {"prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 5}
    bot, kill, launch = _replace_refusal_setup(mk_bot, monkeypatch, pending)

    update, query = mk_query("claude-jim:new_replace", user_id=222)
    run_async(bot._handle_callback(update, MagicMock()))

    kill.assert_not_awaited()
    launch.assert_not_awaited()
    assert "claude-jim" in bot._new_conflict_pending
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Only the person who sent /new" in a for a in answers), answers


def test_new_replace_by_an_author_who_may_no_longer_prompt_is_refused(
        mk_bot, mk_query, run_async, monkeypatch):
    pending = {"prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 5}
    bot, kill, launch = _replace_refusal_setup(mk_bot, monkeypatch, pending)
    bot._can_prompt_user = MagicMock(return_value=False)

    update, _query = mk_query("claude-jim:new_replace", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    kill.assert_not_awaited()
    launch.assert_not_awaited()
    assert "claude-jim" in bot._new_conflict_pending


def test_new_replace_with_no_card_state_is_refused(
        mk_bot, mk_query, run_async, monkeypatch):
    """The card outlived its state (a daemon restart): nobody's choices
    or rights are known, so it kills nothing."""
    bot, kill, launch = _replace_refusal_setup(mk_bot, monkeypatch, None)

    update, query = mk_query("claude-jim:new_replace", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    kill.assert_not_awaited()
    launch.assert_not_awaited()
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("expired" in a for a in answers), answers


def test_new_replace_after_a_turn_started_since_the_card_is_refused_and_kept(
        mk_bot, mk_query, run_async, monkeypatch):
    """jim started a turn (busy card 50) after the name was sent (40):
    the card never showed that work, so Replace refuses, and the card's
    first message is kept for a retry."""
    pending = {"prompt": "do it", "skip_perms": False, "user_id": 111, "msg_id": 40}
    bot, kill, launch = _replace_refusal_setup(mk_bot, monkeypatch, pending)
    bot.registry.get("claude-jim").busy_msg_id = 50

    update, query = mk_query("claude-jim:new_replace", user_id=111, message_id=60)
    run_async(bot._handle_callback(update, MagicMock()))

    kill.assert_not_awaited()
    launch.assert_not_awaited()
    assert bot._new_conflict_pending["claude-jim"]["prompt"] == "do it"


def test_new_replace_from_an_edited_name_card_is_not_mistaken_for_stale(
        mk_bot, mk_query, run_async, monkeypatch):
    """The conflict card is the older Name card (message 10), edited: a
    turn that started before the name was sent (busy card 30, name 40)
    is the one the card showed, so the tap goes through."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.busy_msg_id = 30
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 40}
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)

    update, _query = mk_query("claude-jim:new_replace", user_id=111, message_id=10)
    run_async(bot._handle_callback(update, MagicMock()))

    launch.assert_awaited_once()


@pytest.mark.parametrize("verb", ["new_cancel", "new_resume"])
def test_cancel_and_resume_take_the_cards_state(
        mk_bot, mk_query, run_async, monkeypatch, verb):
    """Only a refused Replace leaves the card's state behind; every action
    that goes ahead takes it, so nothing is left to act on twice."""
    bot = mk_bot()
    bot.registry._sessions["claude-jim"] = TrackedSession(
        name="claude-jim", label="jim", status=Status.IDLE)
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 5}

    update, _query = mk_query(f"claude-jim:{verb}", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    assert "claude-jim" not in bot._new_conflict_pending


def test_a_tap_for_a_session_no_longer_tracked_takes_the_cards_state(
        mk_bot, mk_query, run_async):
    bot = mk_bot()
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 5}

    update, query = mk_query("claude-jim:new_replace", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    assert "claude-jim" not in bot._new_conflict_pending
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert "Session not found" in answers


@pytest.mark.parametrize("verb", ["new_cancel", "new_resume"])
def test_cancel_and_resume_by_someone_else_are_refused_and_keep_the_card(
        mk_bot, mk_query, run_async, verb):
    """The conflict card is its author's, like the Name card: nobody else
    cancels it (losing the author's first message) or resumes with it."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "go", "skip_perms": False, "user_id": 111, "msg_id": 5}

    update, query = mk_query(f"claude-jim:{verb}", user_id=222)
    run_async(bot._handle_callback(update, MagicMock()))

    assert bot._new_conflict_pending["claude-jim"]["prompt"] == "go"
    assert sess.pending_queue == []
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Only the person who sent /new" in a for a in answers), answers


def test_resume_by_an_author_who_may_no_longer_prompt_is_refused(
        mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "go", "skip_perms": False, "user_id": 111, "msg_id": 5}
    bot._can_prompt_user = MagicMock(return_value=False)

    update, _query = mk_query("claude-jim:new_resume", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    assert sess.pending_queue == []
    assert "claude-jim" in bot._new_conflict_pending


def test_resume_brings_back_exactly_the_cards_session(
        mk_bot, mk_query, run_async, monkeypatch):
    """Review-3's probe: a GONE `jim` in two groups. Resume on group B's
    card resumes B's jim (not A's, found by label anywhere), and the
    first message is queued there."""
    from aipager.scope import disambiguated_name

    bot = mk_bot()
    names = {}
    for chat in (-100, -200):
        name = disambiguated_name("jim", chat, "group")
        gone = TrackedSession(name=name, label="jim", status=Status.GONE,
                              claude_session_id=f"id{chat}")
        gone.scope_chat_id = chat
        bot.registry._sessions[name] = gone
        names[chat] = name
    b = names[-200]
    bot._new_conflict_pending[b] = {
        "prompt": "go", "skip_perms": False, "user_id": 111, "msg_id": 5}
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)
    monkeypatch.setattr("aipager.bot.session_ops._read_preview", lambda *a, **k: "",
                        raising=False)
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._read_status_file = MagicMock(return_value=None)

    update, _query = mk_query(f"{b}:new_resume", user_id=111)
    update.effective_chat.id = -200
    run_async(bot._handle_callback(update, MagicMock()))

    assert launch.await_args.args[0] == b.removeprefix("claude-")
    assert launch.await_args.kwargs["resume_id"] == "id-200"
    assert [q[0] for q in bot.registry.get(b).pending_queue] == ["go"]
    assert bot.registry.get(names[-100]).status == Status.GONE


def test_resume_given_the_session_never_looks_one_up_by_label(
        mk_bot, run_async, monkeypatch):
    """The conflict card hands `_do_resume` its exact session: a label
    lookup (which another chat's or a newer same-named session could
    answer) is never consulted."""
    bot = mk_bot()
    gone = TrackedSession(name="claude-jim", label="jim", status=Status.GONE,
                          claude_session_id="abc")
    bot.registry._sessions["claude-jim"] = gone
    decoy = TrackedSession(name="claude-jim__g999", label="jim",
                           status=Status.GONE, claude_session_id="zzz")
    monkeypatch.setattr(bot.registry, "find_by_label", lambda *a, **k: decoy)
    core = AsyncMock(return_value=MagicMock(ok=False, reason="launch_failed", err="x"))
    bot._do_resume_core = core

    run_async(bot._do_resume(label="jim", reply_fn=AsyncMock(), sess=gone))

    assert core.await_args.args[0] is gone


def test_resume_on_the_card_resumes_the_cards_own_session(
        mk_bot, mk_query, run_async, monkeypatch):
    """The card knows its session: Resume never re-finds it by label (a
    newer or another chat's `jim` could answer that)."""
    bot = mk_bot()
    gone = TrackedSession(name="claude-jim", label="jim", status=Status.GONE,
                          claude_session_id="abc")
    bot.registry._sessions["claude-jim"] = gone
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 5}
    decoy = TrackedSession(name="claude-jim__g999", label="jim",
                           status=Status.GONE, claude_session_id="zzz")
    monkeypatch.setattr(bot.registry, "find_by_label", lambda *a, **k: decoy)
    core = AsyncMock(return_value=MagicMock(ok=False, reason="launch_failed", err="x"))
    bot._do_resume_core = core

    update, _query = mk_query("claude-jim:new_resume", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    assert core.await_args.args[0] is gone


def test_a_failed_resume_queues_nothing(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    gone = TrackedSession(name="claude-jim", label="jim", status=Status.GONE,
                          claude_session_id="abc")
    bot.registry._sessions["claude-jim"] = gone
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "go", "skip_perms": False, "user_id": 111, "msg_id": 5}
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(False, "dtach broken")))

    update, _query = mk_query("claude-jim:new_resume", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    assert gone.status == Status.GONE
    assert gone.pending_queue == []


def test_new_replace_refuses_a_turn_that_started_after_an_idle_card(
        mk_bot, mk_query, run_async, monkeypatch):
    """Review-3: a self-woken turn holds its card back for seconds, so the
    busy card cannot tell. The card was shown on an idle session and the
    session now works: that is work the card never showed."""
    pending = {"prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 40,
               "was_working": False}
    bot, kill, launch = _replace_refusal_setup(mk_bot, monkeypatch, pending)
    bot.registry.get("claude-jim").status = Status.BUSY      # no busy card yet

    update, _query = mk_query("claude-jim:new_replace", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    kill.assert_not_awaited()
    launch.assert_not_awaited()
    assert "claude-jim" in bot._new_conflict_pending


def test_new_replace_of_a_session_shown_working_goes_ahead(
        mk_bot, mk_query, run_async, monkeypatch):
    """The card said it was already running: replacing it is what the
    author chose."""
    pending = {"prompt": "", "skip_perms": False, "user_id": 111, "msg_id": 40,
               "was_working": True}
    bot, kill, launch = _replace_refusal_setup(mk_bot, monkeypatch, pending)
    bot.registry.get("claude-jim").status = Status.BUSY
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)

    update, _query = mk_query("claude-jim:new_replace", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    kill.assert_awaited_once()
    launch.assert_awaited_once()


def test_the_conflict_card_records_whether_the_session_was_working(
        mk_bot, mk_update, run_async):
    bot = mk_bot()
    for status, expected in ((Status.BUSY, True), (Status.IDLE, False)):
        sess = TrackedSession(name="claude-jim", label="jim", status=status)
        update = mk_update("/new jim", user_id=111)
        run_async(bot._send_new_conflict_prompt(
            update=update, existing=sess, prompt="", skip_perms=False))
        assert bot._new_conflict_pending["claude-jim"]["was_working"] is expected


def test_a_newer_conflict_card_for_the_same_name_retires_the_older_one(
        mk_bot, mk_update, run_async):
    """Review-4: two people send /new jim. The state is per name, so the
    first card's buttons would refuse its own author; it says it was
    replaced instead of keeping live buttons."""
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    first = mk_update("/new jim", user_id=111, message_id=10)
    first.message.reply_text = AsyncMock(return_value=MagicMock(message_id=11))
    run_async(bot._send_new_conflict_prompt(
        update=first, existing=sess, prompt="", skip_perms=False))
    second = mk_update("/new jim", user_id=222, message_id=12)
    second.message.reply_text = AsyncMock(return_value=MagicMock(message_id=13))
    run_async(bot._send_new_conflict_prompt(
        update=second, existing=sess, prompt="", skip_perms=False))

    assert bot._new_conflict_pending["claude-jim"]["user_id"] == 222
    assert bot._app.bot.edit_message_text.await_count == 1, "older card not retired"
    kw = bot._app.bot.edit_message_text.await_args.kwargs
    assert kw["message_id"] == 11 and "replaced this card" in kw["text"]
    assert kw.get("reply_markup") is None


def test_two_conflict_cards_racing_retire_the_older_one(mk_bot, mk_update, run_async):
    """Review-5: a second /new jim lands while the first card is still
    going out. The first card's id belongs to the first entry, which is
    no longer current, so that card retires itself; the new card stays."""
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    second = mk_update("/new jim", user_id=222, message_id=12)
    second.message.reply_text = AsyncMock(return_value=MagicMock(message_id=13))

    async def _first_goes_out(*a, **k):
        await bot._send_new_conflict_prompt(
            update=second, existing=sess, prompt="", skip_perms=False)
        return MagicMock(message_id=11)
    first = mk_update("/new jim", user_id=111, message_id=10)
    first.message.reply_text = AsyncMock(side_effect=_first_goes_out)

    run_async(bot._send_new_conflict_prompt(
        update=first, existing=sess, prompt="", skip_perms=False))

    current = bot._new_conflict_pending["claude-jim"]
    assert (current["user_id"], current.get("card", (None, None))[1]) == (222, 13)
    edited = [c.kwargs["message_id"] for c in bot._app.bot.edit_message_text.await_args_list]
    assert edited == [11]


def test_new_replace_re_checks_auto_at_creation(
        mk_bot, mk_query, run_async, monkeypatch):
    """The card stored Auto, but its author is no longer an admin: the
    fresh session asks."""
    bot = mk_bot()
    bot.registry._sessions["claude-jim"] = TrackedSession(
        name="claude-jim", label="jim", status=Status.GONE)
    bot._new_conflict_pending["claude-jim"] = {
        "prompt": "", "skip_perms": True, "user_id": 111, "msg_id": 5}
    bot._is_admin_user = MagicMock(return_value=False)
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)

    update, _query = mk_query("claude-jim:new_replace", user_id=111)
    run_async(bot._handle_callback(update, MagicMock()))

    assert launch.await_args.kwargs["skip_perms"] is False


def test_new_replace_kills_alive_then_launches(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.claude_session_id = "old"  # state to be cleared
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {"prompt": "", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}

    kill_called = AsyncMock()
    launch_called = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.kill_session", kill_called)
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch_called)
    # Pretend the socket disappears immediately (one real 0.2 s poll: the
    # global asyncio.sleep is never patched, CLAUDE.md).
    from pathlib import Path
    monkeypatch.setattr(Path, "is_socket", lambda self: False)

    update, query = mk_query("claude-jim:new_replace", user_id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    kill_called.assert_awaited_once()
    launch_called.assert_awaited_once()
    # The resume metadata was cleared
    assert sess.claude_session_id == ""


def test_new_replace_launch_failure_messages_error(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    bot.registry._sessions["claude-jim"] = sess
    bot._new_conflict_pending["claude-jim"] = {"prompt": "", "skip_perms": False,
                                                  "user_id": 1, "msg_id": 5}
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(False, "dtach broken")))
    update, query = mk_query("claude-jim:new_replace", user_id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    # The conflict card itself says why (it was the "Starting" message).
    texts = [str(c.args[0]) if c.args else str(c.kwargs.get("text"))
             for c in query.edit_message_text.await_args_list]
    assert any("dtach broken" in t for t in texts), texts


# ---- unknown action toast ----------------------------------------------

def test_unknown_action_toasts(mk_bot, mk_query, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    update, query = mk_query("claude-jim:totally_unknown_action")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Unknown" in (a or "") for a in answers)


# ---- tool-permission actions (allow / deny / continue / opt / submit) ----

def _setup_alive_session(bot, monkeypatch, *, pending_permission=None,
                          busy_msg_id=100):
    """Helper: build an alive session ready for permission-action tests."""
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.busy_msg_id = busy_msg_id
    if pending_permission is not None:
        sess.pending_permission = pending_permission
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    return sess


def test_action_no_session_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    update, query = mk_query("claude-nope:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_action_dead_session_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (a or "").lower() for a in answers)


def test_allow_sends_enter(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    # Allow sends one Enter
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "Enter"


def test_deny_overshoots_then_enters(mk_bot, mk_query, run_async, monkeypatch):
    """Deny walks past the end of the menu, which clamps on the refusal.

    A single Down would land on "Yes, and always allow …" instead.
    """
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:deny")
    run_async(bot._handle_callback(update, MagicMock()))
    keys_sent = [c.args[1] for c in sent.await_args_list]
    assert keys_sent[-1] == "Enter"
    assert set(keys_sent[:-1]) == {"Down"}
    assert len(keys_sent) - 1 >= 3, (
        f"too few Downs to clamp past a three-item menu; got {keys_sent}"
    )


def test_continue_sends_enter(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    update, query = mk_query("claude-jim:continue")
    run_async(bot._handle_callback(update, MagicMock()))
    sent.assert_awaited_once()
    assert sent.await_args.args[1] == "Enter"


def test_allow_send_key_failure_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to send" in (a or "") for a in answers)


def test_opt_single_select_navigates_down_then_enter(mk_bot, mk_query, run_async, monkeypatch):
    """opt2 → press Down twice, then Enter (no multi_select)."""
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)  # no pending_permission → single-select
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:opt2")
    run_async(bot._handle_callback(update, MagicMock()))
    keys = [c.args[1] for c in sent.await_args_list]
    # opt2 → Down twice, then Enter
    assert keys == ["Down", "Down", "Enter"]


def test_opt_multi_select_toggles_checkbox(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
        "selected": set(),
        "cursor_pos": 0,
        "questions": [{}],
        "current_idx": 0,
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_inline_ask_keyboard = MagicMock(return_value=MagicMock())
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:opt1", message_id=100)
    run_async(bot._handle_callback(update, MagicMock()))
    # Down once to opt1, then Enter to toggle
    keys = [c.args[1] for c in sent.await_args_list]
    assert keys == ["Down", "Enter"]
    # Selection now has index 1
    assert 1 in perm["selected"]


def test_opt_multi_select_send_fail_toasts(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}, {"label": "B"}],
        "selected": set(),
        "cursor_pos": 0,
        "questions": [{}],
        "current_idx": 0,
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=False))
    update, query = mk_query("claude-jim:opt1", message_id=100)
    run_async(bot._handle_callback(update, MagicMock()))
    answers = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("Failed to send keys" in (a or "") for a in answers)


def test_submit_multi_select_advances_question(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}, {"label": "B"}],
        "selected": {0},  # A selected
        "cursor_pos": 0,
        "questions": [
            {"question": "Q1", "options": [{"label": "A"}, {"label": "B"}],
             "multiSelect": True},
            {"question": "Q2", "options": [{"label": "C"}, {"label": "D"}],
             "multiSelect": False},
        ],
        "current_idx": 0,
        "question": "Q1",
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_inline_ask_keyboard = MagicMock(return_value=MagicMock())
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:submit", message_id=100)
    run_async(bot._handle_callback(update, MagicMock()))
    # After submit on a non-last question, pending_permission advances to next
    assert bot.registry.get("claude-jim").pending_permission["current_idx"] == 1
    assert bot.registry.get("claude-jim").pending_permission["question"] == "Q2"


def test_submit_multi_select_last_question_finishes(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": True,
        "multi_select": True,
        "options": [{"label": "A"}],
        "selected": {0},
        "cursor_pos": 0,
        "questions": [
            {"question": "Q1", "options": [{"label": "A"}], "multiSelect": True},
        ],
        "current_idx": 0,
        "question": "Q1",
        "tool_info": {"name": "AskUserQuestion"},
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_inline_ask_keyboard = MagicMock(return_value=MagicMock())
    bot._build_stop_keyboard = MagicMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:submit", message_id=100)
    run_async(bot._handle_callback(update, MagicMock()))
    # On last question: pending_permission cleared, session transitions to BUSY
    sess = bot.registry.get("claude-jim")
    assert sess.pending_permission is None
    assert sess.status == Status.BUSY


def test_allow_with_pending_permission_records_tool(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    perm = {
        "ask_question": False,
        "tool_summary": "Bash: ls",
        "tool_info": {"name": "Bash"},
        "wait_started_at": 0,
    }
    _setup_alive_session(bot, monkeypatch, pending_permission=perm)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_busy_text = MagicMock(return_value="text")
    bot._build_stop_keyboard = MagicMock(return_value=MagicMock())
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.callbacks.asyncio.sleep", _no_sleep)
    update, query = mk_query("claude-jim:allow", message_id=100)
    run_async(bot._handle_callback(update, MagicMock()))
    sess = bot.registry.get("claude-jim")
    # tool_history got a "Allowed" entry
    assert any("Allowed" in s for s, _ in sess.tool_history)
    # pending_permission cleared
    assert sess.pending_permission is None


def test_allow_without_pending_permission_edits_separate_message(mk_bot, mk_query, run_async, monkeypatch):
    bot = mk_bot()
    _setup_alive_session(bot, monkeypatch)  # no pending_permission
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    update, query = mk_query("claude-jim:allow")
    run_async(bot._handle_callback(update, MagicMock()))
    # The separate-message path edits query (not _edit_busy_raw)
    query.edit_message_text.assert_awaited_once()
