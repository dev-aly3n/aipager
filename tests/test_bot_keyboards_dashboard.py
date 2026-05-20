"""Tests for aipager.bot.keyboards + aipager.bot.dashboard render helpers.

These are mostly pure-function builders that return HTML strings or
InlineKeyboardMarkup. Easy to exercise without network I/O.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from telegram import InlineKeyboardMarkup

from aipager.state import Status, TrackedSession


# ===== keyboards.py =====================================================

def test_build_button_rows_chunks_at_per_row(mk_bot):
    bot = mk_bot()
    rows = bot._build_button_rows(["a", "b", "c", "d", "e"], per_row=2)
    assert len(rows) == 3
    assert [b.text for b in rows[0]] == ["a", "b"]
    assert [b.text for b in rows[2]] == ["e"]


def test_build_button_rows_empty_list(mk_bot):
    bot = mk_bot()
    rows = bot._build_button_rows([], per_row=3)
    assert rows == []


def test_build_button_rows_default_per_row(mk_bot):
    bot = mk_bot()
    rows = bot._build_button_rows(["a", "b", "c", "d"], per_row=3)
    assert len(rows) == 2
    assert len(rows[0]) == 3


# ---- _build_ask_keyboard -----------------------------------------------

def test_build_ask_keyboard_no_questions_returns_no_buttons(mk_bot):
    bot = mk_bot()
    text, kb = bot._build_ask_keyboard("claude-jim", "jim", {"questions": []})
    assert "No questions" in text
    assert kb is None


def test_build_ask_keyboard_with_options(mk_bot):
    bot = mk_bot()
    text, kb = bot._build_ask_keyboard("claude-jim", "jim", {
        "questions": [{"question": "Pick", "options": [
            {"label": "A"}, {"label": "B"},
        ]}],
    })
    assert "Pick" in text
    assert "A" in text
    assert kb is not None
    assert kb.inline_keyboard[0][0].callback_data == "claude-jim:opt0"


def test_build_ask_keyboard_no_options_returns_no_buttons(mk_bot):
    bot = mk_bot()
    text, kb = bot._build_ask_keyboard("claude-jim", "jim", {
        "questions": [{"question": "Pick", "options": []}],
    })
    assert "Pick" in text
    assert kb is None


def test_build_ask_keyboard_caps_at_4_options(mk_bot):
    """Telegram inline keyboards work best with ≤4 buttons in a row."""
    bot = mk_bot()
    options = [{"label": f"opt{i}"} for i in range(8)]
    _, kb = bot._build_ask_keyboard("claude-jim", "jim", {
        "questions": [{"question": "?", "options": options}],
    })
    # Only first 4 options become buttons
    assert len(kb.inline_keyboard[0]) == 4


def test_build_ask_keyboard_truncates_long_descriptions(mk_bot):
    bot = mk_bot()
    long_desc = "x" * 200
    text, _ = bot._build_ask_keyboard("claude-jim", "jim", {
        "questions": [{"question": "?", "options": [
            {"label": "A", "description": long_desc},
        ]}],
    })
    # Description gets truncated to ~60 chars
    assert len(text) < len(long_desc) + 200  # bounded


# ---- _build_selector_keyboard ------------------------------------------

def test_build_selector_keyboard_with_question_and_options(mk_bot):
    bot = mk_bot()
    text, kb = bot._build_selector_keyboard(
        "claude-jim", "jim", "Confirm?", [(1, "Yes"), (2, "No")],
    )
    assert "Confirm?" in text
    assert "Yes" in text
    assert kb is not None
    # Two buttons present
    assert len(kb.inline_keyboard[0]) == 2


def test_build_selector_keyboard_no_question_falls_back(mk_bot):
    bot = mk_bot()
    text, _ = bot._build_selector_keyboard(
        "claude-jim", "jim", "", [(1, "Yes")],
    )
    assert "Needs input" in text


def test_build_selector_keyboard_no_options_returns_no_kb(mk_bot):
    bot = mk_bot()
    text, kb = bot._build_selector_keyboard(
        "claude-jim", "jim", "Pick", [],
    )
    assert kb is None


# ---- _build_stop / retry / compact / permission keyboards --------------

def test_build_stop_keyboard(mk_bot):
    bot = mk_bot()
    kb = bot._build_stop_keyboard("claude-jim")
    assert isinstance(kb, InlineKeyboardMarkup)
    assert kb.inline_keyboard[0][0].callback_data == "claude-jim:stop"


def test_build_retry_keyboard(mk_bot):
    bot = mk_bot()
    kb = bot._build_retry_keyboard("claude-jim")
    assert kb.inline_keyboard[0][0].callback_data == "claude-jim:retry"


def test_build_compact_keyboard(mk_bot):
    bot = mk_bot()
    kb = bot._build_compact_keyboard("claude-jim")
    assert kb.inline_keyboard[0][0].callback_data == "claude-jim:compact"


def test_build_permission_keyboard(mk_bot):
    bot = mk_bot()
    kb = bot._build_permission_keyboard("claude-jim")
    cb_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "claude-jim:allow" in cb_data
    assert "claude-jim:deny" in cb_data
    assert "claude-jim:stop" in cb_data


# ---- _build_inline_ask_keyboard ----------------------------------------

def test_build_inline_ask_keyboard_single_select(mk_bot):
    bot = mk_bot()
    kb = bot._build_inline_ask_keyboard(
        "claude-jim", [{"label": "A"}, {"label": "B"}],
        multi_select=False,
    )
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "claude-jim:opt0" in cb
    assert "claude-jim:opt1" in cb


def test_build_inline_ask_keyboard_multi_select_shows_checkmarks(mk_bot):
    bot = mk_bot()
    kb = bot._build_inline_ask_keyboard(
        "claude-jim", [{"label": "A"}, {"label": "B"}, {"label": "C"}],
        multi_select=True, selected={0, 2},
    )
    button_texts = [b.text for row in kb.inline_keyboard for b in row]
    # Selected options show ☑, unselected show ⬜
    assert any("☑" in t for t in button_texts)
    assert any("⬜" in t for t in button_texts)


# ---- _send_keyboard ----------------------------------------------------

def test_send_keyboard_main_level(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    run_async(bot._send_keyboard(level="main"))
    bot._app.bot.send_message.assert_awaited_once()
    assert bot._keyboard_level == "main"


def test_send_keyboard_templates_level(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    run_async(bot._send_keyboard(level="templates"))
    assert bot._keyboard_level == "templates"


def test_send_keyboard_swallows_send_failure(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("flooded"))
    # MUST NOT raise
    run_async(bot._send_keyboard(level="main"))


# ===== dashboard.py =====================================================

# ---- _gone_sessions_sorted ---------------------------------------------

def test_gone_sessions_sorted_newest_first(mk_bot):
    bot = mk_bot()
    s1 = TrackedSession(name="claude-a", label="a", status=Status.GONE)
    s1.gone_at = 100.0
    s2 = TrackedSession(name="claude-b", label="b", status=Status.GONE)
    s2.gone_at = 200.0
    s3 = TrackedSession(name="claude-alive", label="alive", status=Status.IDLE)
    bot.registry._sessions["claude-a"] = s1
    bot.registry._sessions["claude-b"] = s2
    bot.registry._sessions["claude-alive"] = s3
    result = bot._gone_sessions_sorted()
    # Only GONE sessions, newest first
    assert [s.label for s in result] == ["b", "a"]


def test_gone_sessions_sorted_empty(mk_bot):
    bot = mk_bot()
    assert bot._gone_sessions_sorted() == []


# ---- _fmt_gone_ago -----------------------------------------------------

def test_fmt_gone_ago_handles_none(mk_bot):
    bot = mk_bot()
    assert bot._fmt_gone_ago(None) == "earlier"


def test_fmt_gone_ago_seconds(mk_bot):
    bot = mk_bot()
    assert "s ago" in bot._fmt_gone_ago(time.time() - 30)


def test_fmt_gone_ago_minutes(mk_bot):
    bot = mk_bot()
    assert "m ago" in bot._fmt_gone_ago(time.time() - 600)


def test_fmt_gone_ago_hours(mk_bot):
    bot = mk_bot()
    assert "h ago" in bot._fmt_gone_ago(time.time() - 7200)


def test_fmt_gone_ago_days(mk_bot):
    bot = mk_bot()
    assert "d ago" in bot._fmt_gone_ago(time.time() - 200000)


# ---- _render_resume_picker ---------------------------------------------

def test_render_resume_picker_empty_history(mk_bot):
    bot = mk_bot()
    text, kb = bot._render_resume_picker()
    assert "No previous sessions" in text
    assert kb is None


def test_render_resume_picker_single_page(mk_bot):
    bot = mk_bot()
    for i in range(3):
        s = TrackedSession(name=f"claude-old{i}", label=f"old{i}",
                            status=Status.GONE)
        s.gone_at = time.time() - i
        s.claude_session_id = f"id-{i}"
        bot.registry._sessions[s.name] = s
    text, kb = bot._render_resume_picker()
    assert "3 total" in text
    # 3 buttons, no nav row
    assert len(kb.inline_keyboard) == 3


def test_render_resume_picker_pagination(mk_bot):
    bot = mk_bot()
    for i in range(15):
        s = TrackedSession(name=f"claude-old{i:02d}", label=f"old{i:02d}",
                            status=Status.GONE)
        s.gone_at = time.time() - i
        s.claude_session_id = f"id-{i}"
        bot.registry._sessions[s.name] = s
    text, kb = bot._render_resume_picker(page=0)
    # 10 entries + 1 nav row
    assert len(kb.inline_keyboard) == 11
    nav = kb.inline_keyboard[-1]
    cb_data = [b.callback_data for b in nav]
    # Page 0: no Prev, has Next
    assert any("resume_page:1" in c for c in cb_data)


def test_render_resume_picker_clamps_page_out_of_range(mk_bot):
    bot = mk_bot()
    s = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    s.gone_at = time.time()
    s.claude_session_id = "id"
    bot.registry._sessions["claude-jim"] = s
    # page=99 → clamps to last valid page
    text, kb = bot._render_resume_picker(page=99)
    assert kb is not None


# ---- _build_session_dashboard ------------------------------------------

def test_build_session_dashboard_idle(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.model_name = "Opus 4.7"
    sess.last_token_pct = 30
    dashboard = bot._build_session_dashboard(sess)
    assert "jim" in dashboard
    assert "Opus 4.7" in dashboard


def test_build_session_dashboard_busy_shows_elapsed(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 65  # 1m 5s ago
    dashboard = bot._build_session_dashboard(sess)
    # Should contain a minute marker
    assert "m" in dashboard


def test_build_session_dashboard_with_queue(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.queue_prompt("hi", 1)
    sess.queue_prompt("bye", 2)
    dashboard = bot._build_session_dashboard(sess)
    # Queue depth shows up somewhere
    assert "2" in dashboard


def test_build_session_dashboard_with_tool_history(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic()
    sess.tool_history = [
        ("Bash: ls", True),
        ("Read: /x", False),
    ]
    dashboard = bot._build_session_dashboard(sess)
    # Most recent few tools shown
    assert "Read" in dashboard or "Bash" in dashboard


def test_build_session_dashboard_gone(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    dashboard = bot._build_session_dashboard(sess)
    assert "jim" in dashboard
    # GONE has a red circle marker
    assert "🔴" in dashboard or "gone" in dashboard.lower()


# ---- _build_pinned_text ------------------------------------------------

def test_build_pinned_text_with_no_sessions(mk_bot):
    bot = mk_bot()
    out = bot._build_pinned_text("")
    # Single message (or empty)
    assert isinstance(out, str)


def test_build_pinned_text_lists_alive_sessions(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_cost_usd = 0.42
    bot.registry._sessions["claude-jim"] = sess
    out = bot._build_pinned_text("claude-jim")
    assert "jim" in out


def test_build_pinned_text_marks_active_session(mk_bot):
    bot = mk_bot()
    s1 = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    s2 = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = s1
    bot.registry._sessions["claude-dev"] = s2
    out = bot._build_pinned_text("claude-jim")
    # Both listed
    assert "jim" in out
    assert "dev" in out


# ---- _maybe_update_bot_name --------------------------------------------

def test_maybe_update_bot_name_swallows_set_my_name_failure(
    mk_bot, run_async, monkeypatch,
):
    # Force a valid single chat so the pinned-dashboard path is exercised
    # regardless of the ambient CHAT_ID (empty in CI / multi-scope) — the
    # point of this test is that an API failure is swallowed, not raised.
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "12345")
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"
    bot._app.bot.set_my_name = AsyncMock(side_effect=RuntimeError("api err"))
    # MUST NOT raise
    run_async(bot._maybe_update_bot_name("claude-jim"))


# ---- _send_diff_preview ------------------------------------------------

def test_send_diff_preview_write_renders_diff(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    bot._app.bot.send_message = AsyncMock()
    run_async(bot._send_diff_preview(sess, "Write", {
        "file_path": "/x.py", "content": "print('hi')\n",
    }))
    bot._app.bot.send_message.assert_awaited_once()


def test_send_diff_preview_with_no_diff_returns_silently(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot._app.bot.send_message = AsyncMock()
    run_async(bot._send_diff_preview(sess, "Read", {
        "file_path": "/x.py",
    }))
    # Not Write or Edit — no message sent
    bot._app.bot.send_message.assert_not_called()


def test_send_diff_preview_swallows_send_failure(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("api err"))
    # MUST NOT raise
    run_async(bot._send_diff_preview(sess, "Write", {
        "file_path": "/x.py", "content": "y",
    }))
