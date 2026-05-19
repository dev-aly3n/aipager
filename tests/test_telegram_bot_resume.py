"""Tests for /resume command, paginated picker, and resume callbacks."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from aipager.bot import TelegramBot
from aipager.dtach import inject
from aipager.state import SessionRegistry, Status, TrackedSession


def _gone_session(label="jim", *, claude_session_id="abc-def-uuid",
                  cwd="/home/aly/work", gone_at=None, preview=""):
    sess = TrackedSession(name=f"claude-{label}", label=label,
                          status=Status.GONE)
    sess.claude_session_id = claude_session_id
    sess.cwd = cwd
    sess.gone_at = gone_at if gone_at is not None else time.time() - 60
    sess.last_assistant_preview = preview
    return sess


# ---- /resume command ----------------------------------------------------

def test_resume_no_arg_with_empty_history_replies_empty(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    bot = mk_bot(registry)
    update = mk_update("/resume")
    run_async(bot._handle_resume_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "No previous sessions" in text


def test_resume_unknown_name_friendly_error(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    bot = mk_bot(registry)
    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "No session named" in text
    assert "jim" in text


def test_resume_alive_session_rejects(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "already running" in text


def test_resume_gone_without_claude_session_id_rejects(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.gone_at = time.time()
    # claude_session_id intentionally left empty
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    assert "no resumable transcript" in text


def test_resume_happy_path_calls_launch_with_resume_id(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = _gone_session(label="jim", claude_session_id="UUID-1",
                          cwd="/tmp", preview="I refactored auth.")
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)

    captured = {}

    async def _fake_launch(name, *, resume_id=None, cwd=None, **kw):
        captured["name"] = name
        captured["resume_id"] = resume_id
        captured["cwd"] = cwd
        return True, ""

    monkeypatch.setattr(inject, "launch_session", _fake_launch)
    # Stub _build_session_dashboard so we don't touch unrelated rendering.
    monkeypatch.setattr(bot, "_build_session_dashboard",
                        lambda s: "<dashboard>")

    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))

    assert captured["name"] == "jim"
    assert captured["resume_id"] == "UUID-1"
    assert captured["cwd"] == "/tmp"

    text = update.message.reply_text.await_args.args[0]
    assert "Resumed" in text
    assert "jim" in text
    assert "I refactored auth." in text

    # Status recovered + gone_at cleared
    assert sess.status != Status.GONE
    assert sess.gone_at is None


def test_resume_launch_failure_restores_session_id(monkeypatch, mk_bot, mk_update, run_async):
    """If launch_session fails, the cleared claude_session_id is restored."""
    registry = SessionRegistry()
    sess = _gone_session(label="jim", claude_session_id="UUID-X")
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)

    async def _fake_launch(*a, **kw):
        return False, "dtach is sad"

    monkeypatch.setattr(inject, "launch_session", _fake_launch)

    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))

    text = update.message.reply_text.await_args.args[0]
    assert "Couldn't resume" in text
    assert "dtach is sad" in text
    assert sess.claude_session_id == "UUID-X"  # restored
    assert sess.status == Status.GONE          # still GONE


# ---- Paginated picker ---------------------------------------------------

def test_picker_shows_single_page_for_small_history(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    for i in range(3):
        s = _gone_session(label=f"old{i}", gone_at=time.time() - i)
        registry._sessions[s.name] = s
    bot = mk_bot(registry)
    text, kb = bot._render_resume_picker(page=0)
    assert "3 total" in text
    # Three rows, no nav row (only one page)
    assert len(kb.inline_keyboard) == 3


def test_picker_navigates_to_second_page(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    for i in range(15):
        s = _gone_session(label=f"old{i:02d}", gone_at=time.time() - i)
        registry._sessions[s.name] = s
    bot = mk_bot(registry)
    text, kb = bot._render_resume_picker(page=0)
    assert "15 total" in text
    # First page: 10 session rows + 1 nav row
    assert len(kb.inline_keyboard) == 11
    nav = kb.inline_keyboard[-1]
    # Page 1: no Prev, has Page indicator, has Next
    cb_data = [b.callback_data for b in nav]
    assert all("resume_page:0" not in c for c in cb_data)
    assert any("resume_page:1" in c for c in cb_data)

    text2, kb2 = bot._render_resume_picker(page=1)
    nav2 = kb2.inline_keyboard[-1]
    cb_data2 = [b.callback_data for b in nav2]
    assert any("resume_page:0" in c for c in cb_data2)
    # Last page has no Next button
    assert not any("resume_page:2" in c for c in cb_data2)


def test_picker_callback_format_is_session_name_resume(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    s = _gone_session(label="jim")
    registry._sessions[s.name] = s
    bot = mk_bot(registry)
    _, kb = bot._render_resume_picker(page=0)
    assert kb.inline_keyboard[0][0].callback_data == "claude-jim:resume"


def test_picker_sorts_newest_first(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    # Add in reverse-chronological order on purpose
    older = _gone_session(label="older", gone_at=1000.0)
    newer = _gone_session(label="newer", gone_at=2000.0)
    registry._sessions[older.name] = older
    registry._sessions[newer.name] = newer
    bot = mk_bot(registry)
    _, kb = bot._render_resume_picker(page=0)
    # First button should be the newer entry
    assert kb.inline_keyboard[0][0].callback_data == "claude-newer:resume"
    assert kb.inline_keyboard[1][0].callback_data == "claude-older:resume"


# ---- fmt_gone_ago -------------------------------------------------------

def test_fmt_gone_ago_handles_none(mk_bot, mk_update, run_async):
    assert TelegramBot._fmt_gone_ago(None) == "?"


def test_fmt_gone_ago_seconds(mk_bot, mk_update, run_async):
    assert "s ago" in TelegramBot._fmt_gone_ago(time.time() - 30)


def test_fmt_gone_ago_hours(mk_bot, mk_update, run_async):
    assert "h ago" in TelegramBot._fmt_gone_ago(time.time() - 7200)
