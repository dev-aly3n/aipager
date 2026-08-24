"""Tests for the IDLE + INTERACTIVE branches of NotifyMixin.notify.

The bot's `notify()` dispatcher dispatches on event type for the
"live busy-status events" (tool_use, subagent_*, compacting, etc.)
but ALSO has two big status-based branches:
- ``sess.status == Status.IDLE``: send the final response summary, optionally
  with a file attachment when the response is too long.
- ``sess.status == Status.INTERACTIVE``: render an inline permission prompt.

This file covers those paths.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from aipager.state import Status, TrackedSession


def _sess(label="jim", status=Status.IDLE, *, busy_msg_id=None):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    return s


@pytest.fixture(autouse=True)
def _mock_send_rich_message(monkeypatch):
    """Prevent real HTTP calls to Telegram in every test in this module.

    send_rich_message is mocked to succeed (returns {}) so only the
    PTB send_message path (header) fires. Tests that need to exercise
    the fallback path override this fixture locally.
    """
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(return_value={}),
    )


# ---- IDLE: simple "Finished" message ------------------------------------

def test_idle_sends_finished_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # First send_message call is the header
    first_call = bot._app.bot.send_message.await_args_list[0]
    text = first_call.args[1]
    assert "Finished" in text
    assert "jim" in text


def test_idle_renders_final_card_and_clears_busy_msg(mk_bot, run_async, monkeypatch):
    """The card stays in the chat, re-rendered once, and is no longer live."""
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    bot._edit_busy_rich = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    bot._app.bot.delete_message.assert_not_awaited()
    assert bot._edit_busy_rich.await_args.kwargs["final"] is True
    assert sess.busy_msg_id is None


def test_idle_final_render_failure_never_breaks_the_turn(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    bot._edit_busy_rich = AsyncMock(side_effect=RuntimeError("telegram exploded"))
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    assert sess.busy_msg_id is None
    # The answer still went out.
    bot._app.bot.send_message.assert_awaited()


def test_idle_deletes_busy_msg_when_knob_off(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", False)
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    bot._app.bot.delete_message.assert_awaited_once()
    assert sess.busy_msg_id is None


def test_idle_swallows_delete_failure(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", False)
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock(side_effect=BadRequest("old"))
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # Cleared anyway
    assert sess.busy_msg_id is None


# ---- layout stage 3: stored /settings preference vs the env-var seed -----

def test_idle_no_stored_pref_keep_finished_card_off_matches_replace(
    mk_bot, run_async, monkeypatch,
):
    """No stored layout + KEEP_FINISHED_CARD=0 behaves like a stored
    "replace" preference: the busy card is deleted."""
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", False)
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    bot._app.bot.delete_message.assert_awaited_once()


def test_idle_stored_card_preference_overrides_keep_finished_card_off(
    mk_bot, run_async, monkeypatch,
):
    """A stored "card" preference wins over KEEP_FINISHED_CARD=0."""
    from aipager import preferences
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", False)
    preferences.set_preference(0, "layout", "card")
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    bot._edit_busy_rich = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    bot._app.bot.delete_message.assert_not_awaited()
    assert bot._edit_busy_rich.await_args.kwargs["final"] is True


def test_idle_session_override_wins_over_scope_layout(mk_bot, run_async, monkeypatch):
    """THE load-bearing regression test for design.md's second critical
    read site (notify.py's idle-notification layout): it must resolve via
    resolve_preferences(scope, sess.preference_overrides()), never
    get_preferences(scope) alone, or a session's layout override silently
    does nothing for the one place an operator would actually see it."""
    from aipager import preferences
    # Scope default resolves to "replace" (no stored pref, knob off) —
    # the busy card would normally be deleted outright.
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", False)
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    sess.override_layout = "card"  # THIS session keeps the card
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    bot._edit_busy_rich = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    bot._app.bot.delete_message.assert_not_awaited()
    assert bot._edit_busy_rich.await_args.kwargs["final"] is True
    # And the scope's own preference was never touched by the override.
    assert preferences.get_preferences(sess.scope_chat_id).layout == "replace"


def test_idle_marks_tools_done_when_no_agents_open(mk_bot, run_async):
    """A genuinely finished turn (no active_subagents) marks tool_history
    done, same as always."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.tool_history = [("Bash: ls", False), ("Read: /x", False)]
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # All tools marked done
    assert all(done is True for _, done in sess.tool_history)


def test_idle_with_open_agents_does_not_clear_or_finish(mk_bot, run_async):
    """design.md "model Claude Code background-agent jobs": an idle
    transition while active_subagents is non-empty means a background job
    is still open — job_background_open() is now True — so this must NOT
    take the Finished-card path at all (no tool_history mutation, no
    active_subagents.clear(), no "Finished" header). This is the exact bug
    the feature fixes: the OLD unconditional active_subagents.clear() at
    the top of the IDLE branch is what silently erased the very state a
    waiting card needs to render correctly.
    """
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.tool_history = [("Bash: ls", False), ("Read: /x", False)]
    sess.active_subagents = {"a1": {"type": "x", "started_at": time.monotonic()}}
    sess.busy_msg_id = 42
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._edit_busy_rich = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # Nothing marked done — the turn isn't over.
    assert all(done is False for _, done in sess.tool_history)
    # active_subagents survives — job_background_open() must keep working.
    assert sess.active_subagents == {"a1": {"type": "x",
                                             "started_at": sess.active_subagents["a1"]["started_at"]}}
    # No "Finished" header sent.
    for call in bot._app.bot.send_message.await_args_list:
        assert "Finished" not in call.args[1]
    # The waiting card was rendered instead.
    bot._edit_busy_rich.assert_awaited()
    assert bot._edit_busy_rich.await_args.kwargs.get("waiting") is True


def test_idle_with_short_summary_includes_blockquote(mk_bot, run_async):
    """The body is now sent via sendRichMessage, not inline in send_message.
    The header still shows only the ✅ Finished line (no blockquote)."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "Short reply"}))
    # Header is sent via send_message; body goes to sendRichMessage (mocked).
    # The header must contain "Finished" but not the body.
    first_call = bot._app.bot.send_message.await_args_list[0]
    text = first_call.args[1]
    assert "Finished" in text


def test_idle_with_html_summary_preserves_html(mk_bot, run_async):
    """html_summary flag is no longer used for the body (rich messages take
    raw markdown). The header is still valid HTML."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "print(1)", "html_summary": True,
    }))
    # Must not raise; header contains "Finished"
    first_call = bot._app.bot.send_message.await_args_list[0]
    text = first_call.args[1]
    assert "Finished" in text


def test_idle_shows_elapsed_time(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_started_at = time.monotonic() - 75  # 1m 15s
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # Elapsed time is in the HEADER (first send_message call).
    first_call = bot._app.bot.send_message.await_args_list[0]
    text = first_call.args[1]
    # Should include "1m" or "75s"
    assert "m" in text or "75s" in text


def test_idle_shows_lines_changed(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.last_lines_added = 10
    sess.last_lines_removed = 5
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # Lines-changed indicator is in the HEADER.
    first_call = bot._app.bot.send_message.await_args_list[0]
    text = first_call.args[1]
    assert "+10" in text
    assert "-5" in text


def test_idle_clears_trigger_msg_id(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.trigger_msg_id = 555
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    assert sess.trigger_msg_id is None  # reply cycle complete


# ---- IDLE: API error path -----------------------------------------------

def test_idle_detects_api_error_and_sends_friendly_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.last_prompt = "do thing"  # enables retry button
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "API Error: 429 rate_limit_error",
    }))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "Rate limit" in text or "rate limit" in text.lower()
    # Retry button attached (because last_prompt is set)
    kb = bot._app.bot.send_message.await_args.kwargs.get("reply_markup")
    assert kb is not None


def test_idle_api_error_no_last_prompt_no_retry_button(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.last_prompt = ""  # no retry possible
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "API Error: 500 internal server error",
    }))
    kb = bot._app.bot.send_message.await_args.kwargs.get("reply_markup")
    assert kb is None


def test_idle_api_error_swallows_send_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.last_prompt = "x"
    bot._app.bot.send_message = AsyncMock(side_effect=BadRequest("nope"))
    # MUST NOT raise
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "API Error: 429 rate_limit",
    }))


# ---- IDLE: pending-queue flush -----------------------------------------

def test_idle_flushes_next_queued_prompt(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.queue_prompt("queued prompt", 100)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    # The queued prompt was popped and injected
    assert sess.pending_queue == []
    bot._send_busy_and_animate.assert_awaited_once()


# ---- INTERACTIVE: inline permission ------------------------------------

def test_interactive_with_busy_msg_inlines_permission(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "ls", "input": {}},
    }))
    # pending_permission set, busy_msg_id used
    assert sess.pending_permission is not None
    assert sess.pending_permission["tool_summary"] == "ls"


def test_interactive_without_busy_msg_sends_separate(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=None)
    bot._stop_animation = MagicMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "ls", "input": {}},
    }))
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "Permission needed" in text


def test_interactive_ask_user_question_inline(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_inline_ask_keyboard = MagicMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {
            "name": "AskUserQuestion",
            "input": {"questions": [{
                "question": "Pick one", "options": [
                    {"label": "A"}, {"label": "B"},
                ],
            }]},
        },
    }))
    assert sess.pending_permission["ask_question"] is True
    assert sess.pending_permission["question"] == "Pick one"


def test_interactive_ask_user_question_no_questions_degrades(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._build_permission_keyboard = MagicMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "AskUserQuestion",
                       "input": {"questions": []},
                       "summary": "Loading"},
    }))
    # Degraded to Allow/Deny keyboard
    bot._build_permission_keyboard.assert_called_once()


def test_interactive_inline_falls_back_when_edit_returns_none(mk_bot, run_async):
    """If _edit_busy_raw returns None (msg gone), fall back to separate."""
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=None)  # msg deleted
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "ls", "input": {}},
    }))
    # Fallback sent a separate message
    bot._app.bot.send_message.assert_awaited_once()


def test_interactive_selector_keyboard_used_when_options_supplied(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=None)
    bot._stop_animation = MagicMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": None,
        "selector_text": "Pick option",
        "selector_options": [(1, "Yes"), (2, "No")],
    }))
    bot._app.bot.send_message.assert_awaited_once()


def test_interactive_team_rule_auto_denies_tool(mk_bot, run_async):
    """When team.yaml's deny_tools matches, the prompt is auto-denied."""
    from aipager.team import Role, Rules, Team, User as TeamUser
    bot = mk_bot()
    bot.team = Team(
        group_id=-100,
        users={1: TeamUser(id=1, label="admin", role=Role.ADMIN)},
        rules=Rules(deny_tools=["Bash"]),
    )
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=42)
    # Driver is not an admin (so they ARE subject to the rule)
    bot.team.users[2] = TeamUser(id=2, label="dev", role=Role.DEVELOPER)
    sess.last_driver_user_id = 2
    bot._auto_deny = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "rm -rf /",
                       "input": {"command": "rm -rf /"}},
    }))
    bot._auto_deny.assert_awaited_once()


# ---- The "Finished" header is redundant once the card stays ---------------

def _finished_card_bot(mk_bot, monkeypatch, *, card_kept=True):
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    bot._edit_busy_rich = AsyncMock(return_value=card_kept)
    return bot


def _headers(bot):
    return [c.args[1] for c in bot._app.bot.send_message.await_args_list
            if "Finished" in str(c.args[1])]


def test_idle_skips_the_finished_header_when_the_card_stays(
    mk_bot, run_async, monkeypatch,
):
    """The card above the answer already says ✅ label · Done · elapsed."""
    bot = _finished_card_bot(mk_bot, monkeypatch)
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))
    assert _headers(bot) == []


def test_idle_answer_carries_the_reply_link_when_the_header_is_skipped(
    mk_bot, run_async, monkeypatch,
):
    """Dropping the header must not orphan the reply from its prompt."""
    sent = AsyncMock(return_value={"message_id": 555})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", sent)
    bot = _finished_card_bot(mk_bot, monkeypatch)
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    sess.trigger_msg_id = 7
    # A resolvable numeric destination, deliberately independent of the
    # ambient (possibly-empty) config.CHAT_ID — this test's point is the
    # reply-link/tracking wiring around a *reached* sendRichMessage call,
    # not chat-id resolution itself (covered separately).
    sess.scope_chat_id = 4242
    bot.registry.track_message = MagicMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))
    assert sent.await_args.kwargs["reply_to_message_id"] == 7
    # The body takes over as the tracked message for this reply.
    bot.registry.track_message.assert_called_once_with(555, sess.name, 4242)


def test_idle_answer_delivered_when_session_has_no_scope_and_no_chat_id(
    mk_bot, run_async, monkeypatch,
):
    """An unscoped session (``scope_chat_id == 0``) with no global
    ``CHAT_ID`` configured either has no numeric destination for the rich-
    message API — that must never abort the IDLE branch outright. The
    answer still has to reach the user via the plain-text fallback path,
    and `get_preferences` must resolve (not raise) for this same
    unscoped chat id along the way.
    """
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    bot = _finished_card_bot(mk_bot, monkeypatch)
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    assert sess.scope_chat_id == 0  # unscoped — never explicitly stamped
    sess.trigger_msg_id = 7
    bot.registry.track_message = MagicMock()

    # Must not raise — this is the regression itself: notify() aborting
    # partway through, before anything is sent.
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    # No numeric chat id → sendRichMessage is never reached; the answer
    # still goes out through the plain-text fallback (bot.send_message).
    sent_texts = [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    assert any("the answer" in t for t in sent_texts)
    bot.registry.track_message.assert_called_once()


def test_idle_keeps_the_header_when_the_card_was_not_kept(
    mk_bot, run_async, monkeypatch,
):
    """A failed final render leaves nothing above the answer to name it."""
    bot = _finished_card_bot(mk_bot, monkeypatch, card_kept=False)
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))
    assert len(_headers(bot)) == 1


def test_idle_keeps_the_header_when_there_is_no_body(
    mk_bot, run_async, monkeypatch,
):
    """With no answer text the header is the only message — never drop it."""
    bot = _finished_card_bot(mk_bot, monkeypatch)
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    run_async(bot.notify(sess, "idle_prompt",
                         {"summary": "", "no_response": True}))
    assert len(_headers(bot)) == 1


def test_idle_keeps_the_header_when_the_answer_overflows(
    mk_bot, run_async, monkeypatch,
):
    """The overflow note and the document's reply target both live on it."""
    bot = _finished_card_bot(mk_bot, monkeypatch)
    bot._app.bot.send_document = AsyncMock()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "x" * 40_000}))
    assert len(_headers(bot)) == 1
    assert "attached below" in _headers(bot)[0]



def test_idle_backstop_drops_a_mis_anchored_answer(mk_bot, run_async, monkeypatch):
    """When the anchor slips, the persisted card must still not quote the
    answer above the message carrying it."""
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", True)
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=42)
    sess.stream_commentary = [(0, "Let me check that file."),
                              (0, "The file looks fine.")]
    captured = {}

    async def _capture(s, _verb, **_kw):
        captured["commentary"] = list(s.stream_commentary)
        return True

    bot._edit_busy_rich = _capture
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "The file looks fine."}))
    assert captured["commentary"] == [(0, "Let me check that file.")]


# ---- a deliberate restart is not a crash ---------------------------------

def test_session_end_during_a_restart_is_not_announced(mk_bot, run_async):
    """`/perms` kills the session to relaunch it. Reporting that exit as a
    crash told the user the session was fine and dead in the same breath."""
    import time as _t
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.restarting_until = _t.monotonic() + 10
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "session_end", {"source": "prompt_input_exit"}))
    bot._app.bot.send_message.assert_not_awaited()


def test_a_real_crash_is_still_announced(mk_bot, run_async):
    """The suppression must be scoped to the restart window, not global."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    assert sess.restarting_until == 0.0
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))
    bot._app.bot.send_message.assert_awaited_once()
    assert "crashed or killed" in bot._app.bot.send_message.await_args.args[1]


def test_the_restart_window_expires_on_its_own(mk_bot, run_async):
    """A flag that failed to clear would silence real crashes forever."""
    import time as _t
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.restarting_until = _t.monotonic() - 0.01   # already elapsed
    assert sess.is_restarting() is False
    bot._app.bot.send_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))
    bot._app.bot.send_message.assert_awaited_once()
