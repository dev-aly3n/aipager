"""Tests for aipager.bot.session_ops.SessionOpsMixin.

The session-operation methods (_stop_session, _kill_session_core,
_stop_by_label, _switch_session, _guess_session_from_text) handle the
"do something with session X" flows. Each is exercised here so any
silent break in the registry / dtach plumbing surfaces in CI.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest


from aipager.state import Status, TrackedSession


# ---- _stop_session -------------------------------------------------------


def _claude_holds(sess, tmp_path, *contents):
    """Claude Code's transcript ``enqueue`` line for each of *contents*,
    with no fate after it: the only evidence /stop and /clearqueue type
    their queue wipe on (roadmap 8.37)."""
    import json
    t = tmp_path / "transcript.jsonl"
    with open(t, "a", encoding="utf-8") as fh:
        for content in contents:
            fh.write(json.dumps({"type": "queue-operation",
                                 "operation": "enqueue",
                                 "content": content}) + "\n")
    sess.transcript_path = str(t)

def test_stop_session_sends_two_escapes(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", sent)
    discard = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", discard)
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    run_async(bot._stop_session(sess))
    keys = [c.args[1] for c in sent.await_args_list]
    assert keys == ["Escape", "Escape"]
    discard.assert_not_awaited()


def test_stop_session_discards_pending_queue(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.queue_prompt("a", 1)
    sess.queue_prompt("b", 2)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    run_async(bot._stop_session(sess))
    assert sess.pending_queue == []
    assert sess.status == Status.IDLE


def test_stop_session_via_query_edits_message(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    query = MagicMock()
    query.message.message_id = 42  # the busy card
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    run_async(bot._stop_session(sess, query=query))
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_awaited_once()
    query.edit_message_reply_markup.assert_not_awaited()


def test_stop_on_a_prompt_message_keeps_its_text(mk_bot, run_async, monkeypatch):
    """Stop tapped on a message that is not the busy card (a prompt's own
    message): its text stays, only its buttons go (rev-iter6-004)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    query = MagicMock()
    query.message.message_id = 43
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    run_async(bot._stop_session(sess, query=query))
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_stop_session_via_update_reacts_with_emoji(mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
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
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock(side_effect=RuntimeError("old"))
    # MUST NOT raise
    run_async(bot._stop_session(sess, query=query))


# ---- _kill_session_core (/kill's End, ⋮ End, the Mini App) ---------------

def test_kill_session_core_ends_it_and_drops_it_from_the_registry(
        mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    bot._update_bot_commands = AsyncMock()
    outcome = run_async(bot._kill_session_core("claude-jim", "jim"))
    assert outcome.result == "killed"
    assert bot.registry.get("claude-jim") is None


def test_kill_session_core_says_not_found_when_nothing_was_killed(
        mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=False))
    outcome = run_async(bot._kill_session_core("claude-nonexistent", "nonexistent"))
    assert outcome.result == "not_found"


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
    assert text == "<b>jim</b> is not working."


def test_stop_by_label_unknown_label_replies_unknown(mk_bot, mk_update, run_async):
    bot = mk_bot()
    update = mk_update("/nope stop")
    run_async(bot._stop_by_label(update, "nope"))
    text = update.message.reply_text.await_args.args[0]
    assert text == "⚠️ No session named <b>nope</b> here."


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
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()

    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok is True
    assert outcome.label == "jim"
    assert outcome.dropped == 2
    assert sess.status == Status.IDLE


def test_stop_session_core_discards_input_and_counts_notes_when_outstanding(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    """design.md "Stop and /clearqueue on the same primitive": with an
    outstanding note, Stop's dropped count includes it AND
    discard_queued_input fires after the interrupt pair — extending the
    pinned ["Escape", "Escape"] assertion (see
    tests/test_stale_stop_button.py's docstring) for exactly this case.
    The discard's own keys (one Escape, then Ctrl+U) are pinned in
    tests/test_dtach_inject_helpers.py; here it is one recorded step."""
    from aipager import policy_snapshot as ps
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.queue_prompt("a", 1)
    bot.registry._sessions["claude-jim"] = sess
    ps.write_note("claude-jim", None, None, None, msg_id=9, chat_id=1,
                  sender_key=(1, 1), body="note text", raw_text="note text")
    _claude_holds(sess, tmp_path, "note text")

    keys = []
    async def _send_keys(name, k):
        keys.append(k)
        return True
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    wiped_chars = []
    async def _discard(name, chars=0):
        keys.append("discard")
        wiped_chars.append(chars)
        return True
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", _discard)
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()

    outcome = run_async(bot._stop_session_core(sess))

    assert outcome.dropped == 2  # 1 queued + 1 note
    assert keys == ["Escape", "Escape", "discard"]
    # Counted before the Escapes: every character Claude held, line break
    # included, gets its Ctrl+U.
    assert wiped_chars == [len("note text") + 1]
    assert ps.list_outstanding_notes("claude-jim") == []


def test_stop_session_core_omits_discard_when_no_notes_outstanding(
    mk_bot, run_async, monkeypatch,
):
    """The reverse of the test above — no outstanding notes means no
    discard, matching the pinned two-Escape assertions in
    tests/test_stale_stop_button.py."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess

    keys = []
    async def _send_keys(name, k):
        keys.append(k)
        return True
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    async def _discard(name, chars=0):
        keys.append("discard")
        return True
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", _discard)
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()

    run_async(bot._stop_session_core(sess))

    assert keys == ["Escape", "Escape"]


# ---- design.md "model Claude Code background-agent jobs" ----------------

def test_stop_session_core_succeeds_while_job_background_open(
    mk_bot, run_async, monkeypatch,
):
    """A session waiting on background work reads as IDLE, not BUSY —
    the gate must still admit Stop (requirement 1: "Stop still tappable"),
    where today's refusal-only-on-status check would say "is not busy"."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": time.monotonic()}
    bot.registry._sessions["claude-jim"] = sess

    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    monkeypatch.setattr("aipager.dtach.inject.press_kill_agents", AsyncMock(return_value=True))
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()

    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok is True
    assert sess.status == Status.IDLE


def test_stop_session_core_clears_active_subagents(mk_bot, run_async, monkeypatch):
    """Genuinely ends the job — active_subagents must not survive a
    successful Stop, or job_background_open() would stay true underneath
    the now-"Stopped" card."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.active_subagents["a1"] = {"type": "Explore", "started_at": time.monotonic()}
    bot.registry._sessions["claude-jim"] = sess

    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    monkeypatch.setattr("aipager.dtach.inject.press_kill_agents", AsyncMock(return_value=True))
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()

    run_async(bot._stop_session_core(sess))
    assert sess.active_subagents == {}
    assert sess.job_background_open() is False


def test_stop_session_core_still_refuses_plain_idle(mk_bot, run_async, monkeypatch):
    """Unchanged: a genuinely idle session (no background job open) is
    still refused — the gate widened, it didn't loosen wholesale."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess

    async def _boom(*args, **kwargs):
        raise AssertionError("inject.send_keys must not run")
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)

    outcome = run_async(bot._stop_session_core(sess))
    assert outcome.ok is False


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
# ORCHESTRATOR OVERRIDE). session_ops._sleep (never the global
# asyncio.sleep) is neutered so no test actually waits out the 0.5s
# Ctrl-C pause or the up-to-3s poll loop; the socket
# poll itself is controlled via aipager.bot.session_ops.Path so the
# still-stopping/success branches are deterministic regardless of what is
# or isn't a real file on this machine.

def _neuter_sleep(monkeypatch):
    async def _no_sleep(_):
        pass
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)


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
        sess, target_mode="full", interrupt_first=False,
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
    sess.relaunch_in_flight = True

    async def _boom(*a, **k):
        raise AssertionError("dtach must not be touched while already restarting")
    monkeypatch.setattr("aipager.dtach.inject.kill_session", _boom)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_mode="full", interrupt_first=False,
    ))
    assert outcome.ok is False
    assert outcome.reason == "already_restarting"


def _terminal_capture(monkeypatch, *, kill_ok=True, socket_gone=True, on_write=None):
    """The real ``inject.send_keys`` writing into a fake dtach: every
    write's bytes, the waits, the kill and the relaunch, in order.

    ``socket_gone``: what the socket poll after the kill sees (False: the
    session did not die)."""
    from aipager.dtach import inject
    events: list = []

    async def _run(args, stdin=b"", timeout=5):
        events.append(("write", bytes(stdin)))
        if on_write is not None:
            on_write(bytes(stdin))
        return True, ""

    async def _kill(name):
        events.append(("kill", name))
        return kill_ok

    async def _launch(short_name, **kw):
        events.append(("launch", short_name, kw.get("mode"), kw.get("resume_id")))
        return True, ""

    async def _sleep(secs):
        events.append(("sleep", secs))

    monkeypatch.setattr(inject, "_run", _run)
    monkeypatch.setattr(inject, "kill_session", _kill)
    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _sleep)
    monkeypatch.setattr("aipager.bot.session_ops.Path",
                        _fake_path_cls(not socket_gone))
    return events


def _writes(events) -> list:
    return [e[1] for e in events if e[0] == "write"]


def _assert_no_text_typed(events) -> None:
    for w in _writes(events):
        assert b"C-c" not in w, w
        assert not any(0x20 <= b < 0x7f for b in w), f"printable text typed: {w!r}"


def test_kill_and_relaunch_core_interrupt_first_escapes_then_kills(
    mk_bot, run_async, monkeypatch,
):
    """Stop & switch on a busy session (roadmap 8.137): Escape twice into
    the terminal (Stop's interrupt, never "C-c" typed as text), a wait
    for the interrupt record, then the hard kill and the relaunch."""
    from aipager.bot import session_ops
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.claude_session_id = "uuid-1"
    bot.registry._sessions["claude-jim"] = sess
    events = _terminal_capture(monkeypatch)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_mode="full", interrupt_first=True,
    ))
    assert outcome.ok is True
    assert outcome.reason == "done"
    _assert_no_text_typed(events)
    head = [e for e in events if e[0] != "sleep" or e[1] != session_ops._PERMS_POLL_INTERVAL]
    assert head[:6] == [
        ("write", b"\x1b"), ("sleep", 0.15), ("write", b"\x1b"),
        ("sleep", session_ops._INTERRUPT_SETTLE_SECONDS),
        ("kill", "claude-jim"), ("launch", "jim", "full", "uuid-1"),
    ], events
    assert session_ops._INTERRUPT_SETTLE_SECONDS > 0
    assert sess.perm_mode == "full"
    assert sess.status == Status.IDLE


def test_kill_and_relaunch_core_interrupt_first_survivor_still_stopping(
    mk_bot, run_async, monkeypatch,
):
    """A busy session that outlives the kill (its socket stays): no
    relaunch, ``still_stopping``, mode unchanged."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-jim"] = sess
    events = _terminal_capture(monkeypatch, socket_gone=False)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_mode="full", interrupt_first=True,
    ))
    assert outcome.ok is False
    assert outcome.reason == "still_stopping"
    assert ("kill", "claude-jim") in events
    assert not [e for e in events if e[0] == "launch"]
    assert sess.perm_mode == "ask"


def test_kill_and_relaunch_core_interrupt_first_failed_kill_not_success(
    mk_bot, run_async, monkeypatch,
):
    """A kill that signalled nothing (False, socket kept) is never
    reported as a switch."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-jim"] = sess
    events = _terminal_capture(monkeypatch, kill_ok=False, socket_gone=False)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_mode="full", interrupt_first=True,
    ))
    assert outcome.ok is False
    assert outcome.reason == "still_stopping"
    assert not [e for e in events if e[0] == "launch"]
    assert sess.perm_mode == "ask"


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
        sess, target_mode="ask", interrupt_first=False,
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
        sess, target_mode="full", interrupt_first=False,
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
        sess, target_mode="full", interrupt_first=False,
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
        sess, target_mode="full", interrupt_first=False,
    ))
    assert outcome.ok is True
    assert outcome.reason == "done"
    assert outcome.mode == "full"
    assert sess.perm_mode == "full"
    assert launch.await_args.kwargs["mode"] == "full"
    # A relaunch starts mode tracking over: the first hook checks it.
    assert sess.mode_launch_unchecked is True and sess.reported_mode == ""
    assert sess.claude_session_id == "uuid-1"
    assert sess.cwd == "/home/user/project"
    assert sess.status == Status.IDLE
    launch.assert_awaited_once()
    assert launch.await_args.kwargs["resume_id"] == "uuid-1"
    assert launch.await_args.kwargs["cwd"] == "/home/user/project"


def test_kill_and_relaunch_core_keeps_the_launch_model(mk_bot, run_async, monkeypatch):
    """A mode switch or restart starts the session with the model it was
    started with (or last switched to), not Claude Code's default."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.launch_model = "haiku"
    bot.registry._sessions["claude-jim"] = sess
    # The poll interval, not the global asyncio.sleep (CLAUDE.md).
    monkeypatch.setattr("aipager.bot.session_ops._PERMS_POLL_INTERVAL", 0)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    monkeypatch.setattr("aipager.dtach.inject.kill_session",
                        AsyncMock(return_value=True))
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)

    run_async(bot._kill_and_relaunch_core(
        sess, target_mode="ask", interrupt_first=False))

    assert launch.await_args.kwargs.get("model") == "haiku"


def test_create_session_records_the_launch_model(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    name, _err = run_async(bot.create_session("jim", scope_chat_id=0, model="opus", mode="ask"))

    assert bot.registry.get(name).launch_model == "opus"


def test_a_sent_model_switch_becomes_the_launch_model(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.launch_model = "opus"
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)

    outcome = run_async(bot._switch_model_core(sess, "sonnet"))

    assert outcome.ok is True
    assert sess.launch_model == "sonnet"


def test_resume_keeps_the_launch_model(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "uuid-1"
    sess.launch_model = "haiku"
    bot.registry._sessions["claude-jim"] = sess
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    run_async(bot._do_resume_core(sess, driver_user_id=None))

    assert launch.await_args.kwargs.get("model") == "haiku"


def _busy_with_a_held_message(bot, tmp_path):
    """A busy session whose Claude Code queue holds two Telegram messages:
    msg 9 by its note, msg 7 as a queued target (a message sent while the
    turn ran), each with the transcript's ``enqueue`` line."""
    from aipager import policy_snapshot as ps
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-jim"] = sess
    ps.write_note("claude-jim", None, None, None, msg_id=9, chat_id=1,
                  sender_key=(1, 1), body="note text", raw_text="note text")
    sess.queued_targets = [{"msg_id": 7, "chat_id": 1, "raw_text": "later"}]
    _claude_holds(sess, tmp_path, "note text", "later")
    return sess


def _record_wipes_and_marks(bot, monkeypatch, events):
    async def _discard(name, chars=0):
        events.append(("discard", chars))
        return True
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", _discard)
    marked: list = []

    async def _mark(sess, entries):
        events.append(("not_delivered", sorted(e.get("msg_id") for e in entries)))
        marked.extend(entries)
    bot._mark_not_delivered = _mark
    return marked


@pytest.mark.parametrize("dies", [True, False], ids=["killed", "outlives_the_kill"])
def test_busy_switch_reads_claudes_queue_before_the_escapes(
    mk_bot, run_async, monkeypatch, tmp_path, dies,
):
    """Messages Claude Code held when Stop & switch was tapped: after the
    Escapes the transcript no longer shows them held (here the fake
    terminal writes ``popAll``; Claude Code 2.1.311 took the message as a
    turn the second Escape cut), so they are read before the Escapes (as
    Stop reads them), wiped from the input box and forgotten after them,
    and marked not delivered (🤷) after the kill, as the hard kill's
    session end marks them. Also when the process outlives the kill: the
    text must not join the next message, nor come back as a turn
    (roadmap 8.137)."""
    import json
    bot = mk_bot()
    sess = _busy_with_a_held_message(bot, tmp_path)

    def _claude_code(data):
        # Claude Code's answer to an Escape while it holds a queue: the
        # queue goes to the input box, and the transcript says so.
        if data == b"\x1b":
            with open(sess.transcript_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "queue-operation",
                                     "operation": "popAll"}) + "\n")
    events = _terminal_capture(monkeypatch, socket_gone=dies, on_write=_claude_code)
    _record_wipes_and_marks(bot, monkeypatch, events)

    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_mode="full", interrupt_first=True,
    ))
    from aipager import policy_snapshot as ps
    assert outcome.ok is dies
    assert outcome.reason == ("done" if dies else "still_stopping")
    steps = [e for e in events if e[0] in ("write", "discard", "not_delivered", "kill")]
    assert steps == [
        ("write", b"\x1b"), ("write", b"\x1b"),
        ("discard", len("note text") + 1 + len("later") + 1),
        ("kill", "claude-jim"),
        ("not_delivered", [7, 9]),
    ], events
    # Forgotten, also when the session lives on: neither comes back as a
    # turn of its own later.
    assert sess.queued_targets == []
    assert ps.list_outstanding_notes("claude-jim") == []


def test_busy_switch_with_nothing_held_wipes_and_marks_nothing(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    events = _terminal_capture(monkeypatch)
    _record_wipes_and_marks(bot, monkeypatch, events)

    assert run_async(bot._kill_and_relaunch_core(
        sess, target_mode="full", interrupt_first=True)).ok is True
    assert not [e for e in events if e[0] in ("discard", "not_delivered")], events


def test_idle_switch_never_reads_or_wipes_claudes_queue(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    """The hard kill alone (not busy): no wipe, no 🤷 here; the session
    end's teardown marks what died with the process, as before."""
    bot = mk_bot()
    sess = _busy_with_a_held_message(bot, tmp_path)
    sess.status = Status.IDLE
    events = _terminal_capture(monkeypatch)
    _record_wipes_and_marks(bot, monkeypatch, events)

    assert run_async(bot._kill_and_relaunch_core(
        sess, target_mode="full", interrupt_first=False)).ok is True
    assert not [e for e in events if e[0] in ("write", "discard", "not_delivered")], events


def test_perms_switch_core_busy_interrupts_first(mk_bot, run_async, monkeypatch):
    """The perms wrapper's interrupt_first=True derivation for BUSY: the
    terminal gets two Escapes and nothing else, then the kill."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    events = _terminal_capture(monkeypatch)

    outcome = run_async(bot._perms_switch_core(sess, "full"))
    assert outcome.ok is True
    assert _writes(events) == [b"\x1b", b"\x1b"]
    _assert_no_text_typed(events)
    kinds = [e[0] for e in events if e[0] != "sleep"]
    assert kinds == ["write", "write", "kill", "launch"], events


def test_perms_switch_core_idle_writes_nothing_to_the_terminal(
    mk_bot, run_async, monkeypatch,
):
    """A switch on a session that is not busy is the hard kill alone: no
    Escape reaches its terminal."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    events = _terminal_capture(monkeypatch)

    outcome = run_async(bot._perms_switch_core(sess, "full"))
    assert outcome.ok is True
    assert _writes(events) == []
    kinds = [e[0] for e in events if e[0] != "sleep"]
    assert kinds == ["kill", "launch"], events


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
    sess.perm_mode = "ask"
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
    # The mode is unchanged - a restart is not a mode switch.
    assert launch.await_args.kwargs["mode"] == "ask"


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


def test_clear_queue_core_sends_no_keys_when_only_pending_queue(mk_bot, run_async, monkeypatch):
    """pending_queue holds are aipager-side only — never injected into
    the pty — so there is nothing in the input box to wipe. Confirms
    /clearqueue neither types a key nor wipes Claude's queue for a hold
    that never touched dtach."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.queue_prompt("a", 1)
    bot.registry._sessions["claude-jim"] = sess

    async def _boom(*a, **k):
        raise AssertionError("no dtach keys should be sent for a pending_queue-only clear")
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", _boom)

    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.ok is True
    assert outcome.dropped == 1


def test_clear_queue_core_discards_notes_and_counts_them_combined(mk_bot, run_async, monkeypatch,
                                                                tmp_path):
    """design.md "Stop and /clearqueue on the same primitive": ONE call
    to discard_queued_input, never preceded by an interrupt Escape — and
    the combined count includes outstanding notes even with an empty
    pending_queue."""
    from aipager import policy_snapshot as ps
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    ps.write_note("claude-jim", None, None, None, msg_id=9, chat_id=1,
                  sender_key=(1, 1), body="note text", raw_text="note text")
    _claude_holds(sess, tmp_path, "note text")

    keys = []
    async def _send_keys(name, k):
        keys.append(k)
        return True
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    wiped_chars = []
    async def _discard(name, chars=0):
        keys.append("discard")
        wiped_chars.append(chars)
        return True
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", _discard)

    outcome = run_async(bot._clear_queue_core(sess))

    assert outcome.ok is True
    assert outcome.dropped == 1
    assert keys == ["discard"]  # never an interrupt Escape before it
    assert wiped_chars == [len("note text") + 1]
    assert ps.list_outstanding_notes("claude-jim") == []


def test_clear_queue_core_combined_count_with_both_queue_and_notes(mk_bot, run_async, monkeypatch,
                                                                   tmp_path):
    from aipager import policy_snapshot as ps
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.queue_prompt("a", 1)
    sess.queue_prompt("b", 2)
    bot.registry._sessions["claude-jim"] = sess
    ps.write_note("claude-jim", None, None, None, msg_id=9, chat_id=1,
                  sender_key=(1, 1), body="note text", raw_text="note text")
    _claude_holds(sess, tmp_path, "note text")
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))

    outcome = run_async(bot._clear_queue_core(sess))

    assert outcome.dropped == 3  # 2 queued + 1 note
    assert sess.pending_queue == []
    assert ps.list_outstanding_notes("claude-jim") == []


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
    assert sess.pending_queue == [
        ("/compact", None, sess.pending_queue[0][2], "", None)
    ]


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
    monkeypatch.setattr("aipager.bot.session_ops._spawn", fake_create_task)

    outcome = run_async(bot._rename_session_core(sess, "james"))
    assert outcome.ok is True
    assert outcome.changed is True
    assert outcome.previous_label == "jim"
    assert outcome.new_label == "james"
    assert sess.label == "james"
    assert bot.registry._dirty is True
    assert len(scheduled) == 1


def test_kill_and_relaunch_core_drops_queued_targets(mk_bot, run_async, monkeypatch):
    """The relaunched claude starts with an empty input queue: a message
    the old process had queued but never absorbed must not start a
    phantom turn later ("anchor-on-transcript-consumption", review
    rev-iter1-002)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.claude_session_id = "uuid-1"
    sess.queued_targets = [{"msg_id": 7, "chat_id": -1, "raw_text": "later"}]
    bot.registry._sessions["claude-jim"] = sess
    _neuter_sleep(monkeypatch)
    monkeypatch.setattr("aipager.bot.session_ops.Path", _fake_path_cls(False))
    monkeypatch.setattr("aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", AsyncMock(return_value=(True, "")))
    outcome = run_async(bot._kill_and_relaunch_core(
        sess, target_mode="full", interrupt_first=False,
    ))
    assert outcome.ok is True
    assert sess.queued_targets == []


def test_do_resume_drops_queued_targets(mk_bot, run_async, monkeypatch):
    """rev-iter2-001: a resumed claude has an empty input queue; a stale
    queued target must not survive the resume and start a phantom turn."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.claude_session_id = "UUID-1"
    sess.cwd = "/x"
    sess.gone_at = 1234.0
    sess.queued_targets = [{"msg_id": 7, "chat_id": -1, "raw_text": "later"}]
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    bot._build_session_dashboard = MagicMock(return_value="dashboard text")
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    run_async(bot._do_resume(label="jim", reply_fn=AsyncMock()))
    assert sess.status != Status.GONE
    assert sess.queued_targets == []


def test_create_session_reusing_a_finished_name_drops_queued_targets(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    # create_session builds the scoped name (`claude-jim__d0` for scope 0);
    # the finished entry must sit under THAT name to be reused.
    sess = TrackedSession(name="claude-jim__d0", label="jim", status=Status.GONE)
    sess.queued_targets = [{"msg_id": 7, "chat_id": -1, "raw_text": "later"}]
    bot.registry._sessions["claude-jim__d0"] = sess
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(return_value=(True, "")))
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    name, err = run_async(bot.create_session("jim", scope_chat_id=0, mode="ask"))
    assert err == "" and name == "claude-jim__d0"
    assert bot.registry.get(name) is sess, "the finished entry is reused, not replaced"
    assert sess.queued_targets == []


def test_clear_queue_core_drops_queued_targets(mk_bot, run_async, tmp_path):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.queue_prompt("a", 1)
    sess.queued_targets = [{"msg_id": 7, "chat_id": -1, "raw_text": "later"}]
    _claude_holds(sess, tmp_path, "later")
    bot.registry._sessions["claude-jim"] = sess
    outcome = run_async(bot._clear_queue_core(sess))
    assert outcome.ok is True
    assert sess.queued_targets == []


def test_halt_for_safety_drops_queued_targets(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.scope_chat_id = -1
    sess.queued_targets = [{"msg_id": 7, "chat_id": -1, "raw_text": "later"}]
    bot.registry._sessions["claude-jim"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", AsyncMock(return_value=True))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._app.bot.send_message = AsyncMock()
    run_async(bot._halt_for_safety(sess, "policy"))
    assert sess.queued_targets == []
