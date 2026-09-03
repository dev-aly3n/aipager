"""Tests for aipager.bot.notify.NotifyMixin.notify — event dispatch.

The notify() coroutine is the entry point that hook_receiver calls when
a session changes state. Each event type is a separate branch; we test
them one at a time, with mocked Telegram I/O so no network is touched.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from telegram.error import BadRequest, Forbidden

from aipager import preferences
from aipager.state import Status, TrackedSession


def _sess(label="jim", *, status=Status.BUSY, busy_msg_id=100):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    return s


# ---- early-return paths --------------------------------------------------

def test_notify_no_app_is_noop(mk_bot, run_async):
    bot = mk_bot()
    bot._app = None
    sess = _sess()
    # MUST NOT raise even though _app is None
    run_async(bot.notify(sess, "tool_use", {"tool_summary": "x"}))


def test_notify_pinned_update_does_nothing(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "pinned_update", {}))
    # No send_message, no edit_message_text
    bot._app.bot.send_message.assert_not_called()


# ---- user_prompt_submit -------------------------------------------------

def test_user_prompt_submit_skips_when_busy_msg_exists(mk_bot, run_async):
    """No duplicate card when one is genuinely live.

    Asserts the OUTCOME (nothing new is sent) rather than the old
    mechanism (that _send_busy_and_animate was never called). notify no
    longer pre-judges with its own `if not busy_msg_id` gate — it
    delegates to _send_busy_and_animate, which bails on its own when a
    live card exists. That gate could not tell a live card from a wedged
    one, which is what let a stuck compacting card swallow every
    terminal-initiated prompt.
    """
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)

    bot._app.bot.send_message = AsyncMock()

    async def _scenario():
        # Task must be created on the loop run_async actually runs, or it
        # belongs to a stale loop and .done() lies.
        async def _alive():
            await asyncio.sleep(100)
        sess.animate_task = asyncio.create_task(_alive())
        try:
            await bot.notify(sess, "user_prompt_submit", {})
        finally:
            sess.animate_task.cancel()

    run_async(_scenario())

    bot._app.bot.send_message.assert_not_awaited()  # no duplicate card
    assert sess.busy_msg_id == 42                   # still the same card


def test_user_prompt_submit_sends_busy_when_no_msg_id(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._send_busy_and_animate = AsyncMock()
    run_async(bot.notify(sess, "user_prompt_submit", {}))
    bot._send_busy_and_animate.assert_awaited_once_with(sess)


# ---- tool_use ------------------------------------------------------------

def test_tool_use_appends_to_history(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)  # no edit fires
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Read: /x",
        "tool_name": "Read",
        "tool_input_full": None,
    }))
    assert sess.tool_history == [("Read: /x", False)]
    assert sess.last_tool_summary == "Read: /x"


# ---- agent attribution ("agent activity rows on the busy card") --------

def test_tool_use_with_matching_agent_id_updates_agent_entry_and_skips_parent_row(
    mk_bot, run_async,
):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.active_subagents["agent-1"] = {
        "type": "explore", "started_at": 0.0, "history_idx": 0,
        "activity": "", "tool_count": 0, "last_tool_at": 0.0, "tools": [],
    }
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: ls",
        "tool_name": "Bash",
        "tool_input_full": None,
        "agent_id": "agent-1",
    }))
    assert sess.tool_history == []
    assert sess.active_subagents["agent-1"]["activity"] == "Bash: ls"


def test_tool_use_attribution_increments_tool_count_activity_and_tools_list(
    mk_bot, run_async,
):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.active_subagents["agent-1"] = {
        "type": "explore", "started_at": 0.0, "history_idx": 0,
        "activity": "", "tool_count": 0, "last_tool_at": 0.0, "tools": [],
    }
    for summary in ("Bash: ls", "Read: /x"):
        run_async(bot.notify(sess, "tool_use", {
            "tool_summary": summary, "tool_name": summary.split(":")[0],
            "tool_input_full": None, "agent_id": "agent-1",
        }))
    info = sess.active_subagents["agent-1"]
    assert info["tool_count"] == 2
    assert info["activity"] == "Read: /x"
    assert info["tools"] == ["Bash: ls", "Read: /x"]


def test_tool_use_with_unknown_agent_id_falls_back_to_parent_row(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    # agent-1 already stopped (or never started) — not in active_subagents
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: ls",
        "tool_name": "Bash",
        "tool_input_full": None,
        "agent_id": "agent-1",
    }))
    assert sess.tool_history == [("Bash: ls", False)]


def test_tool_use_with_empty_agent_id_falls_back_to_parent_row(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: ls",
        "tool_name": "Bash",
        "tool_input_full": None,
        "agent_id": "",
    }))
    assert sess.tool_history == [("Bash: ls", False)]


def test_tool_done_for_attributed_tool_does_not_touch_parent_tool_history(
    mk_bot, run_async,
):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    # An unrelated in-flight parent row that the "no exact match, mark
    # most recent undone" fallback would otherwise wrongly settle.
    sess.tool_history = [("Read: /unrelated", False)]
    sess.active_subagents["agent-1"] = {
        "type": "explore", "started_at": 0.0, "history_idx": None,
    }
    run_async(bot.notify(sess, "tool_done", {
        "tool_name": "Bash", "tool_summary": "Bash: ls",
        "agent_id": "agent-1",
    }))
    assert sess.tool_history == [("Read: /unrelated", False)]


def test_tool_failed_for_attributed_tool_does_not_touch_parent_tool_history(
    mk_bot, run_async,
):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("Read: /unrelated", False)]
    sess.active_subagents["agent-1"] = {
        "type": "explore", "started_at": 0.0, "history_idx": None,
    }
    run_async(bot.notify(sess, "tool_failed", {
        "tool_name": "Bash", "tool_summary": "Bash: ls",
        "agent_id": "agent-1",
    }))
    assert sess.tool_history == [("Read: /unrelated", False)]


def _edit_event():
    return {
        "tool_summary": "Edit: /x",
        "tool_name": "Edit",
        "tool_input_full": {"file_path": "/x", "old_string": "a", "new_string": "b"},
    }


def test_tool_use_diff_preview_off_by_default(mk_bot, run_async):
    """Guard 1 ("diff-preview-settings-toggle"): with untouched preferences
    an Edit posts NO separate diff message — the busy-card row is its only
    trace. This is the defect: previews used to be on unless an
    undocumented env var said otherwise, so a background agent's edits
    landed as extra messages between the busy card and the job's single
    answer."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._send_diff_preview = AsyncMock()
    run_async(bot.notify(sess, "tool_use", _edit_event()))
    bot._send_diff_preview.assert_not_called()
    assert ("Edit: /x", False) in sess.tool_history  # the card row still lands


def test_tool_use_diff_preview_fires_when_toggle_on(mk_bot, run_async):
    """Guard 2: the scope's /settings toggle on → the preview task is
    created for this session/tool/input (`_send_diff_preview` itself
    threads it under the busy message — pinned in
    test_bot_keyboards_dashboard.py)."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    preferences.set_preference(sess.scope_chat_id or 0, "diff_preview", True)
    bot._send_diff_preview = AsyncMock()
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Write: /x",
        "tool_name": "Write",
        "tool_input_full": {"file_path": "/x", "content": "y"},
    }))
    bot._send_diff_preview.assert_called_once()
    args = bot._send_diff_preview.call_args.args
    assert args[0] is sess
    assert args[1] == "Write"
    assert args[2] == {"file_path": "/x", "content": "y"}
    assert ("Write: /x", False) in sess.tool_history


def test_tool_use_diff_preview_session_override_wins(mk_bot, run_async):
    """Guard 3: the gate reads the RESOLVED preference — a per-session
    override beats the scope value in both directions. A gate that read
    `get_preferences` (scope only) would pass every other test here and
    silently ignore the Mini App's per-session toggle."""
    # scope off (default), session on → fires
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.override_diff_preview = True
    bot._send_diff_preview = AsyncMock()
    run_async(bot.notify(sess, "tool_use", _edit_event()))
    bot._send_diff_preview.assert_called_once()
    # scope on, session off → silent
    bot2 = mk_bot()
    sess2 = _sess("kim", busy_msg_id=None)
    preferences.set_preference(sess2.scope_chat_id or 0, "diff_preview", True)
    sess2.override_diff_preview = False
    bot2._send_diff_preview = AsyncMock()
    run_async(bot2.notify(sess2, "tool_use", _edit_event()))
    bot2._send_diff_preview.assert_not_called()


def test_tool_use_edits_busy_when_debounce_elapsed(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.last_tool_edit_at = 0  # ensures debounce window has passed
    bot._edit_busy_rich = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: ls",
        "tool_name": "Bash",
        "tool_input_full": None,
    }))
    bot._edit_busy_rich.assert_awaited_once()


def test_tool_use_sets_stream_dirty(mk_bot, run_async):
    """tool_use must set stream_dirty=True before any edit attempt."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    sess.last_tool_edit_at = 0
    bot._edit_busy_rich = AsyncMock(return_value=True)
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: ls",
        "tool_name": "Bash",
        "tool_input_full": None,
    }))
    # After a successful rich edit, stream_dirty is cleared — verify it was set
    # by checking _edit_busy_rich was called (dirty was True when it fired).
    bot._edit_busy_rich.assert_awaited_once()


# ---- tool_done / tool_failed --------------------------------------------

def test_tool_done_marks_last_undone_entry(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("Read: /x", False), ("Bash: ls", False)]
    run_async(bot.notify(sess, "tool_done", {
        "tool_name": "Bash", "tool_summary": "Bash: ls",
    }))
    assert sess.tool_history == [("Read: /x", False), ("Bash: ls", True)]


def test_tool_failed_marks_with_failed(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("Bash: ls", False)]
    run_async(bot.notify(sess, "tool_failed", {
        "tool_name": "Bash", "tool_summary": "Bash: ls",
    }))
    assert sess.tool_history == [("Bash: ls", "failed")]


def test_tool_done_no_exact_match_marks_last_undone(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("Read: /x", False), ("Bash: rm", False)]
    # Summary doesn't match either entry — marks the LAST undone
    run_async(bot.notify(sess, "tool_done", {
        "tool_name": "Bash", "tool_summary": "completely different",
    }))
    assert sess.tool_history == [("Read: /x", False), ("Bash: rm", True)]


# ---- subagent_start / subagent_stop ------------------------------------

def test_subagent_start_appends_and_increments_count(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.active_subagents["agent-1"] = {"type": "x", "started_at": 0.0, "history_idx": None}
    run_async(bot.notify(sess, "subagent_start", {
        "agent_id": "agent-1", "agent_type": "explore",
    }))
    assert sess.subagent_count_this_turn == 1
    # The tool_history got a new entry and its index was stored
    assert sess.active_subagents["agent-1"]["history_idx"] == 0
    assert "explore" in sess.tool_history[0][0]


def test_subagent_stop_marks_history_done(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("🤖 explore", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-1", "agent_type": "explore",
        "elapsed": 3.5, "history_idx": 0,
    }))
    assert sess.tool_history[0][1] is True
    assert "3s" in sess.tool_history[0][0]


def test_subagent_stop_long_elapsed_uses_minutes(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("🤖 explore", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-1", "agent_type": "explore",
        "elapsed": 125.0, "history_idx": 0,
    }))
    assert "m" in sess.tool_history[0][0]
    assert "5s" in sess.tool_history[0][0]


def test_subagent_stop_with_no_match_appends_done(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = []  # no matching start
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-x", "agent_type": "explore",
        "elapsed": 1.0, "history_idx": None,
    }))
    # New entry appended as done
    assert len(sess.tool_history) == 1
    assert sess.tool_history[0][1] is True


def test_subagent_stop_settles_row_with_frozen_tool_count_and_elapsed(
    mk_bot, run_async,
):
    """"agent activity rows on the busy card": the settled row is the
    fixed three-segment shape "type · N tool calls · elapsed", frozen
    into tool_history — a later render never recomputes it."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("🤖 explore", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-1", "agent_type": "explore",
        "elapsed": 42.0, "history_idx": 0, "tool_count": 5,
    }))
    summary, done = sess.tool_history[0]
    assert done is True
    assert summary == "🤖 explore · 5 tool calls · 42s"


def test_subagent_stop_settled_row_singular_tool_call(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("🤖 explore", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-1", "agent_type": "explore",
        "elapsed": 5.0, "history_idx": 0, "tool_count": 1,
    }))
    summary, _done = sess.tool_history[0]
    assert "1 tool call · " in summary
    assert "1 tool calls" not in summary


def test_phantom_subagent_stop_empty_type_adds_no_row(mk_bot, run_async):
    """design.md "model Claude Code background-agent jobs" requirement 5:
    an unknown-id, empty-agent_type SubagentStop (hook_receiver's phantom
    events, e.g. steps 4 of the hiva sequence) must not pollute the
    timeline with a meaningless "🤖 " row — unlike a real "no matching
    start" stop (agent_type non-empty), which still appends one."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = []
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "unknown-1", "agent_type": "",
        "elapsed": 0.0, "history_idx": None,
    }))
    assert sess.tool_history == []


def test_phantom_subagent_stop_does_not_append_to_finished_subagents(
    mk_bot, run_async,
):
    """NEW — same phantom setup as above, at the finished_subagents layer.
    (notify.py's subagent_stop handler never touches finished_subagents —
    that's hook_receiver.py's archive_finished_subagent's job — this pins
    the invariant regardless of which layer changes next.)"""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = []
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "unknown-1", "agent_type": "",
        "elapsed": 0.0, "history_idx": None,
    }))
    assert sess.finished_subagents == []


def test_subagent_stop_with_trimmed_history_idx_appends_fresh_row_without_crashing(
    mk_bot, run_async,
):
    """A history_idx that pointed at a row record_tool's TOOL_HISTORY_CAP
    trim has since shifted away (stale, out of range) must not crash —
    falls through to the "no matching start" fresh-append branch."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    sess.tool_history = [("Bash: unrelated", True)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "agent-1", "agent_type": "explore",
        "elapsed": 3.0, "history_idx": 99,  # stale/out-of-range
        "tool_count": 1,
    }))
    assert sess.tool_history[0] == ("Bash: unrelated", True)  # untouched
    assert sess.tool_history[-1][1] is True
    assert "explore" in sess.tool_history[-1][0]


# ---- compacting ---------------------------------------------------------

def test_compacting_edits_busy_when_present(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._edit_busy_raw = AsyncMock()
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._stop_animation.assert_called_once()
    bot._edit_busy_raw.assert_awaited_once()


def test_compacting_sends_new_when_no_busy(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id == 555


# ---- context_warning ---------------------------------------------------

def test_context_warning_sends_with_compact_button(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "context_warning", {"context_pct": 85}))
    bot._app.bot.send_message.assert_awaited_once()
    call = bot._app.bot.send_message.await_args
    text = call.args[1] if len(call.args) > 1 else call.kwargs.get("text", "")
    assert "85%" in text
    assert call.kwargs.get("reply_markup") is not None


def test_context_warning_swallows_send_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(side_effect=BadRequest("nope"))
    # MUST NOT raise
    run_async(bot.notify(sess, "context_warning", {"context_pct": 85}))


# ---- stale_busy --------------------------------------------------------

def test_stale_busy_sends_alert(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "stale_busy", {"minutes": 5}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "quiet for 5 min" in text
    assert "still working" in text
    # "subscription limit" stays available in the collapsed cause list.
    assert "subscription" in text.lower()


def test_stale_busy_reads_as_status_not_error(mk_bot, run_async):
    """The note must not look like a failure report.

    Users were reading the old ⚠️-plus-bullet-wall as "something broke"
    and interrupting healthy sessions — a quiet session is usually just
    running a long tool call. The neutral headline is the whole point of
    the message, so guard it rather than the incidental wording.
    """
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "stale_busy", {"minutes": 10}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "⚠️" not in text
    assert text.startswith("⏳")


def test_stale_busy_collapses_causes_behind_expandable_quote(mk_bot, run_async):
    """Diagnostic causes are available but must not lead the message."""
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "stale_busy", {"minutes": 10}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "<blockquote expandable>" in text
    causes = text.split("<blockquote expandable>", 1)[1]
    for cause in ("tool call", "Compaction", "Rate-limit",
                  "subscription", "Network wedge"):
        assert cause in causes, f"{cause!r} escaped the collapsed section"


def test_stale_busy_keeps_stop_keyboard(mk_bot, run_async):
    """Softening the copy must not cost the user the ability to interrupt."""
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "stale_busy", {"minutes": 10}))
    kwargs = bot._app.bot.send_message.await_args.kwargs
    assert kwargs.get("reply_markup") is not None


def test_stale_busy_minutes_default_tracks_threshold(mk_bot, run_async):
    """With no minutes in context, fall back to the configured threshold."""
    from aipager.config import STALE_BUSY_TIMEOUT
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "stale_busy", {}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert f"quiet for {int(STALE_BUSY_TIMEOUT / 60)} min" in text


def test_stale_busy_escapes_label(mk_bot, run_async):
    """A session label with HTML metacharacters must not break parse_mode."""
    bot = mk_bot()
    sess = _sess()
    sess.label = "a<b>&c"
    run_async(bot.notify(sess, "stale_busy", {"minutes": 10}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "a&lt;b&gt;&amp;c" in text


def test_stale_busy_swallows_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(side_effect=Forbidden("blocked"))
    run_async(bot.notify(sess, "stale_busy", {"minutes": 2}))


# ---- queue_pickup (design.md "queue handoff") ---------------------------

def test_queue_pickup_reacts_thumbs_up_on_every_consumed_message(mk_bot, run_async):
    from aipager.state import SessionRegistry

    bot = mk_bot(registry=SessionRegistry())
    sess = _sess()
    sess.scope_chat_id = -1001
    bot.registry._sessions[sess.name] = sess
    bot._app.bot.set_message_reaction = AsyncMock()

    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [
            {"msg_id": 10, "chat_id": -1001, "raw_text": "a"},
            {"msg_id": 11, "chat_id": -1001, "raw_text": "b"},
        ],
        "expired": [],
    }))

    calls = bot._app.bot.set_message_reaction.await_args_list
    assert len(calls) == 2
    assert calls[0].args == (-1001, 10, "👍")
    assert calls[1].args == (-1001, 11, "👍")


def test_queue_pickup_tracks_every_consumed_message(mk_bot, run_async):
    from aipager.state import SessionRegistry

    bot = mk_bot(registry=SessionRegistry())
    sess = _sess()
    sess.scope_chat_id = -1001
    bot.registry._sessions[sess.name] = sess
    bot._app.bot.set_message_reaction = AsyncMock()

    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [
            {"msg_id": 10, "chat_id": -1001, "raw_text": "a"},
            {"msg_id": 11, "chat_id": -1001, "raw_text": "b"},
        ],
        "expired": [],
    }))

    assert bot.registry.get_session_by_msg(10, -1001) is sess
    assert bot.registry.get_session_by_msg(11, -1001) is sess


def test_queue_pickup_expired_gets_no_reaction_only_a_notice(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    sess.scope_chat_id = -1001
    bot._app.bot.set_message_reaction = AsyncMock()

    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [],
        "expired": [{"msg_id": 5, "chat_id": -1001, "raw_text": "stale one"}],
    }))

    bot._app.bot.set_message_reaction.assert_not_awaited()
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "wasn't confirmed" in text or "picked up" in text


def test_queue_pickup_no_consumed_no_expired_sends_nothing(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.set_message_reaction = AsyncMock()

    run_async(bot.notify(sess, "queue_pickup", {"consumed": [], "expired": []}))

    bot._app.bot.set_message_reaction.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()


def test_queue_pickup_swallows_reaction_failure(mk_bot, run_async):
    from telegram.error import BadRequest
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.set_message_reaction = AsyncMock(side_effect=BadRequest("nope"))
    # MUST NOT raise even if the reaction API call fails.
    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [{"msg_id": 10, "chat_id": -1001, "raw_text": "a"}],
        "expired": [],
    }))


# ---- hook_memory_cap_hit ------------------------------------------------

def test_hook_memory_cap_hit_sends_message(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "hook_memory_cap_hit", {"hook": "aipager-hook"}))
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "memory cap hit" in text
    assert "1 GB" in text
    assert "aipager-hook" in text
    assert "jim" in text  # session label surfaces


def test_hook_memory_cap_hit_default_hook_name(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    # No "hook" key in context — falls back to default
    run_async(bot.notify(sess, "hook_memory_cap_hit", {}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "aipager-hook" in text


def test_hook_memory_cap_hit_swallows_send_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(side_effect=Forbidden("blocked"))
    # MUST NOT raise even if Telegram send fails
    run_async(bot.notify(sess, "hook_memory_cap_hit", {"hook": "aipager-hook"}))


def test_hook_memory_cap_hit_includes_tool_in_headline(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "hook_memory_cap_hit",
                         {"hook": "aipager-hook", "tool": "Bash"}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "memory cap hit during" in text
    assert "Bash" in text
    assert "aipager-hook" in text


def test_hook_memory_cap_hit_no_tool_omits_suffix(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    run_async(bot.notify(sess, "hook_memory_cap_hit",
                         {"hook": "aipager-hook", "tool": ""}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "memory cap hit\n" in text  # bare headline, no "during"
    assert "memory cap hit during" not in text


# ---- compact_done ------------------------------------------------------

def test_compact_done_edits_busy_message(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    # Skip the 2-second pause
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.notify.asyncio.sleep", _no_sleep)
    run_async(bot.notify(sess, "compact_done", {
        "before_pct": 80, "after_pct": 5,
    }))
    assert sess.last_token_pct == 5
    bot._stop_animation.assert_called_once()
    bot._start_animation.assert_called_once()


def test_compact_done_sends_new_when_no_busy(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.notify.asyncio.sleep", _no_sleep)
    run_async(bot.notify(sess, "compact_done", {
        "before_pct": 80, "after_pct": 10,
    }))
    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id == 999


# ---- session_end -------------------------------------------------------

def test_session_end_deletes_busy_and_sends_alert(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=42)
    bot._stop_animation = MagicMock()
    bot._app.bot.delete_message = AsyncMock()
    run_async(bot.notify(sess, "session_end", {"source": "disappeared"}))
    bot._app.bot.delete_message.assert_awaited_once()
    assert sess.busy_msg_id is None
    bot._app.bot.send_message.assert_awaited_once()
    text = bot._app.bot.send_message.await_args.args[1]
    assert "crashed or killed" in text


def test_session_end_unknown_source_label(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    run_async(bot.notify(sess, "session_end", {"source": "completely-unknown"}))
    text = bot._app.bot.send_message.await_args.args[1]
    # Falls back to "exited" generic label
    assert "exited" in text


def test_session_end_user_logout(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=None)
    run_async(bot.notify(sess, "session_end", {"source": "logout"}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert "logged out" in text


# ---- BUSY status branch -------------------------------------------------

def test_busy_status_edits_last_msg(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=None)
    sess.last_msg_id = 77
    bot._app.bot.edit_message_text = AsyncMock()
    # Pass an unrecognized event — falls through to the status-based branch
    run_async(bot.notify(sess, "unrecognized_event", {}))
    bot._app.bot.edit_message_text.assert_awaited_once()
    call = bot._app.bot.edit_message_text.await_args
    assert call.kwargs.get("message_id") == 77


def test_busy_status_swallows_edit_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=None)
    sess.last_msg_id = 77
    bot._app.bot.edit_message_text = AsyncMock(side_effect=BadRequest("old"))
    # Must not raise
    run_async(bot.notify(sess, "unrecognized_event", {}))


def test_separate_message_permission_prompt_shows_the_real_command(mk_bot, run_async):
    """With no busy message to edit, the prompt goes out as its own message
    — it carries the real command too (rev-iter1-004), and only Allow/Deny."""
    bot = mk_bot()
    sess = _sess(status=Status.INTERACTIVE, busy_msg_id=None)
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "input": {"command": "rm -rf /tmp/x && ls"},
                      "summary": "Bash: Clean up", "always_available": True,
                      "detail": "rm -rf /tmp/x && ls"},
    }))
    call = bot._app.bot.send_message.await_args
    text = call.args[1] if len(call.args) > 1 else call.kwargs["text"]
    assert "<pre>rm -rf /tmp/x &amp;&amp; ls</pre>" in text
    kb = call.kwargs["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert not any("Allow always" in lbl for lbl in labels), labels


# ---- full-log snapshot: log_agents ("agent activity rows on the busy
# card") ----------------------------------------------------------------

def test_full_log_snapshot_includes_finished_and_still_active_agents(
    mk_bot, run_async, monkeypatch,
):
    """notify.py's IDLE-close path snapshots log_agents from
    finished_subagents + (defensively) active_subagents, chronological by
    start time, and passes it through to build_full_log's agents= kwarg.
    """
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message", AsyncMock(return_value={}),
    )
    bot = mk_bot()
    sess = _sess(status=Status.IDLE, busy_msg_id=None)
    sess.last_card_truncated = True  # forces the .txt attachment
    sess.finished_subagents = [
        {"type": "explore", "started_at": 10.0, "elapsed": 5.0,
         "tool_count": 2, "tools": ["Bash: ls", "Read: /x"]},
    ]
    sess.active_subagents["agent-2"] = {
        "type": "review", "started_at": 20.0, "tool_count": 1,
        "tools": ["Grep: foo"],
    }
    # job_background_open() would normally route a non-empty
    # active_subagents through the interim path instead of this close
    # path — overridden so the (defensive) "still active" half of the
    # snapshot gets exercised too, per design.md's own rationale for why
    # it's there.
    sess.job_background_open = lambda: False
    bot._app.bot.send_document = AsyncMock()

    import aipager.bot.notify as notify_mod
    real_build_full_log = notify_mod.build_full_log
    captured: dict = {}

    def _spy(*args, **kwargs):
        captured["agents"] = kwargs.get("agents")
        return real_build_full_log(*args, **kwargs)

    monkeypatch.setattr(notify_mod, "build_full_log", _spy)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    agents = captured["agents"]
    assert [a["type"] for a in agents] == ["explore", "review"]  # start order
    finished, active = agents
    assert finished["tool_count"] == 2
    assert finished["tools"] == ["Bash: ls", "Read: /x"]
    assert active["tool_count"] == 1
    assert active["tools"] == ["Grep: foo"]
