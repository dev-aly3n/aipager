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
    # Orphan sessions with no gone_at and no recoverable transcript
    # render as "earlier" rather than the cryptic "?".
    assert TelegramBot._fmt_gone_ago(None) == "earlier"


def test_fmt_gone_ago_handles_zero(mk_bot, mk_update, run_async):
    assert TelegramBot._fmt_gone_ago(0) == "earlier"


def test_fmt_gone_ago_seconds(mk_bot, mk_update, run_async):
    assert "s ago" in TelegramBot._fmt_gone_ago(time.time() - 30)


def test_fmt_gone_ago_hours(mk_bot, mk_update, run_async):
    assert "h ago" in TelegramBot._fmt_gone_ago(time.time() - 7200)


def test_hidden_session_still_in_resume_picker(mk_bot, mk_update, run_async):
    """hidden_from_status only affects /status — /resume must still surface it."""
    registry = SessionRegistry()
    visible = _gone_session(label="visible", gone_at=2000.0)
    hidden = _gone_session(label="hidden", gone_at=1000.0)
    hidden.hidden_from_status = True
    registry._sessions[visible.name] = visible
    registry._sessions[hidden.name] = hidden
    bot = mk_bot(registry)
    text, kb = bot._render_resume_picker(page=0)
    assert "2 total" in text
    cb = [row[0].callback_data for row in kb.inline_keyboard]
    assert "claude-visible:resume" in cb
    assert "claude-hidden:resume" in cb


# ---- /resume preview: post-resume confirmation + picker snippets -------

def test_do_resume_uses_cached_preview_when_present(monkeypatch, mk_bot, mk_update, run_async):
    """A non-empty cached preview is used as-is without re-reading transcript."""
    registry = SessionRegistry()
    sess = _gone_session(label="jim", claude_session_id="UUID",
                         preview="cached recap line.")
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)

    spy = MagicMock()
    monkeypatch.setattr("aipager.bot.session_ops._read_preview", spy)
    monkeypatch.setattr(inject, "launch_session",
                        _async_return((True, "")))
    monkeypatch.setattr(bot, "_build_session_dashboard", lambda s: "<dash>")

    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))

    spy.assert_not_called()  # cached preview short-circuits the read
    body = update.message.reply_text.await_args.args[0]
    assert "cached recap line." in body
    assert "Last response from this session" in body


def test_do_resume_derives_preview_when_cached_empty(monkeypatch, mk_bot, mk_update, run_async):
    """Cached preview empty → read from transcript on disk."""
    registry = SessionRegistry()
    sess = _gone_session(label="jim", claude_session_id="UUID",
                         preview="")
    sess.transcript_path = "/fake/transcript.jsonl"
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)

    monkeypatch.setattr("aipager.bot.session_ops._read_preview",
                        lambda path, max_chars=200: "derived from disk")
    monkeypatch.setattr(inject, "launch_session", _async_return((True, "")))
    monkeypatch.setattr(bot, "_build_session_dashboard", lambda s: "<dash>")

    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))

    body = update.message.reply_text.await_args.args[0]
    assert "derived from disk" in body
    assert "Last response from this session" in body


def test_do_resume_no_section_when_no_preview(monkeypatch, mk_bot, mk_update, run_async):
    """Empty cache + transcript missing → no Last-response section."""
    registry = SessionRegistry()
    sess = _gone_session(label="jim", claude_session_id="UUID", preview="")
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)

    monkeypatch.setattr("aipager.bot.session_ops._read_preview",
                        lambda path, max_chars=200: "")
    monkeypatch.setattr(inject, "launch_session", _async_return((True, "")))
    monkeypatch.setattr(bot, "_build_session_dashboard", lambda s: "<dash>")

    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))

    body = update.message.reply_text.await_args.args[0]
    assert "Last response" not in body
    assert "Resumed" in body and "jim" in body


def test_do_resume_uses_500_char_cap_on_derivation(monkeypatch, mk_bot, mk_update, run_async):
    """When cache is empty, _read_preview is invoked with max_chars=500."""
    registry = SessionRegistry()
    sess = _gone_session(label="jim", claude_session_id="UUID", preview="")
    sess.transcript_path = "/x.jsonl"
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)

    seen_kwargs = {}
    def _spy(path, max_chars=200):
        seen_kwargs["max_chars"] = max_chars
        return "derived"
    monkeypatch.setattr("aipager.bot.session_ops._read_preview", _spy)
    monkeypatch.setattr(inject, "launch_session", _async_return((True, "")))
    monkeypatch.setattr(bot, "_build_session_dashboard", lambda s: "<dash>")

    update = mk_update("/resume jim")
    run_async(bot._handle_resume_cmd(update, MagicMock()))
    assert seen_kwargs["max_chars"] == 500


def test_picker_body_includes_snippets(mk_bot, mk_update, run_async):
    """Picker text body includes the preview blockquote for each row."""
    registry = SessionRegistry()
    a = _gone_session(label="alpha", gone_at=2000.0, preview="alpha snippet here")
    b = _gone_session(label="beta", gone_at=1000.0, preview="beta snippet here")
    registry._sessions[a.name] = a
    registry._sessions[b.name] = b
    bot = mk_bot(registry)
    text, _kb = bot._render_resume_picker(page=0)
    assert "alpha snippet here" in text
    assert "beta snippet here" in text
    assert "<blockquote>" in text


def test_picker_body_handles_missing_preview(monkeypatch, mk_bot, mk_update, run_async):
    """A row with no cached and no derivable preview falls back to '(no preview)'."""
    registry = SessionRegistry()
    s = _gone_session(label="orphan", gone_at=1000.0, preview="")
    registry._sessions[s.name] = s
    bot = mk_bot(registry)
    # Force the derivation path to also return empty
    monkeypatch.setattr("aipager.bot.dashboard._read_preview",
                        lambda path, max_chars=140: "")
    text, _kb = bot._render_resume_picker(page=0)
    assert "(no preview)" in text
    assert "orphan" in text


def test_picker_body_uses_transcript_fallback_for_missing_cache(monkeypatch, mk_bot, mk_update, run_async):
    """Cached preview empty but transcript readable → derive snippet."""
    registry = SessionRegistry()
    s = _gone_session(label="recovered", gone_at=1000.0, preview="")
    s.transcript_path = "/some/path.jsonl"
    registry._sessions[s.name] = s
    bot = mk_bot(registry)
    monkeypatch.setattr("aipager.bot.dashboard._read_preview",
                        lambda path, max_chars=140: "FROM-DISK-SNIPPET")
    text, _kb = bot._render_resume_picker(page=0)
    assert "FROM-DISK-SNIPPET" in text


def test_picker_body_within_telegram_message_limit(mk_bot, mk_update, run_async):
    """Full page of 10 sessions × 140-char snippets fits under Telegram's 4096 limit."""
    registry = SessionRegistry()
    for i in range(10):
        s = _gone_session(label=f"sess{i:02d}", gone_at=time.time() - i,
                          preview="x" * 140)
        registry._sessions[s.name] = s
    bot = mk_bot(registry)
    text, _kb = bot._render_resume_picker(page=0)
    assert len(text) < 4096


def _async_return(value):
    """Build an async function that returns ``value`` regardless of args."""
    async def _f(*a, **kw):
        return value
    return _f
