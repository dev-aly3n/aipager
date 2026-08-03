"""Unit tests for the new rich-message IDLE send path in notify.py.

Covers:
- content-selection rule (raw_md → summary → sess.summary → "")
- Header sent via PTB send_message (HTML, contains Finished)
- Body sent via sendRichMessage
- Fallback plain-text on RichMessageFallbackRequired (no parse_mode)
- No fallback on RichMessageBlocked (403)
- No body call when content is empty
- Overflow (>32 768 UTF-8 bytes) → truncate + .txt attachment
- draft_id / stream_offset / stream_text reset on IDLE
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.rich_message import RichMessageBlocked, RichMessageFallbackRequired
from aipager.state import Status, TrackedSession


def _sess(label="jim", status=Status.IDLE, *, scope_kind="dm"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_started_at = time.monotonic()
    s.scope_kind = scope_kind
    s.scope_chat_id = 123456
    return s


@pytest.fixture
def bot_with_rich(mk_bot, monkeypatch):
    """Return (bot, send_rich_mock) — send_rich_message is mocked to succeed."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    rich_mock = AsyncMock(return_value={})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", rich_mock)
    return bot, rich_mock


# ── content-selection ────────────────────────────────────────────────────────

def test_content_selection_raw_md_preferred(bot_with_rich, run_async):
    bot, rich_mock = bot_with_rich
    sess = _sess()
    sess.summary = "session fallback"
    run_async(bot.notify(sess, "idle_prompt", {
        "raw_md": "raw markdown",
        "summary": "ignored summary",
    }))
    # sendRichMessage was called with raw_md
    assert rich_mock.await_args.args[1] == "raw markdown"


def test_content_selection_summary_when_no_raw_md(bot_with_rich, run_async):
    bot, rich_mock = bot_with_rich
    sess = _sess()
    sess.summary = "session fallback"
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "context summary",
    }))
    assert rich_mock.await_args.args[1] == "context summary"


def test_content_selection_sess_summary_fallback(bot_with_rich, run_async):
    """Session's own .summary used when context has neither raw_md nor summary."""
    bot, rich_mock = bot_with_rich
    sess = _sess()
    sess.summary = "session-level summary"
    run_async(bot.notify(sess, "idle_prompt", {}))
    assert rich_mock.await_args.args[1] == "session-level summary"


def test_content_selection_empty_means_no_body_call(bot_with_rich, run_async):
    """All empty → header only, no sendRichMessage call."""
    bot, rich_mock = bot_with_rich
    sess = _sess()
    sess.summary = ""
    run_async(bot.notify(sess, "idle_prompt", {}))
    rich_mock.assert_not_awaited()


def test_no_response_suppresses_sess_summary_fallback(bot_with_rich, run_async):
    """no_response ⇒ header only, never the previous turn's cached answer.

    sess.summary holds the last turn's text. When the current turn produced
    nothing, falling through to it publishes a stale answer under the new
    prompt — plausible enough to be believed, and so a worse failure than
    sending no body at all.
    """
    bot, rich_mock = bot_with_rich
    sess = _sess()
    sess.summary = "answer to the PREVIOUS prompt"
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "", "no_response": True,
    }))
    rich_mock.assert_not_awaited()


def test_no_response_still_sends_the_finished_header(bot_with_rich, run_async):
    bot, _ = bot_with_rich
    sess = _sess()
    sess.summary = "answer to the PREVIOUS prompt"
    run_async(bot.notify(sess, "idle_prompt", {
        "summary": "", "no_response": True,
    }))
    header = bot._app.bot.send_message.await_args.args[1]
    assert "Finished" in header


def test_no_response_does_not_suppress_real_content(bot_with_rich, run_async):
    """The flag only gates the cached fallback; real text still publishes."""
    bot, rich_mock = bot_with_rich
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {
        "raw_md": "a real answer", "summary": "", "no_response": True,
    }))
    assert rich_mock.await_args.args[1] == "a real answer"


def test_content_selection_idle_recovery_path(bot_with_rich, run_async):
    """The session_monitor idle-recovery path sends {"summary": ...} with no raw_md."""
    bot, rich_mock = bot_with_rich
    sess = _sess()
    sess.summary = "stale"
    run_async(bot.notify(sess, "idle_prompt", {"summary": "recovered summary"}))
    assert rich_mock.await_args.args[1] == "recovered summary"


# ── header ────────────────────────────────────────────────────────────────────

def test_idle_header_contains_finished(bot_with_rich, run_async):
    bot, _ = bot_with_rich
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "content"}))
    header = bot._app.bot.send_message.await_args_list[0].args[1]
    assert "Finished" in header
    assert "jim" in header


def test_idle_header_no_blockquote(bot_with_rich, run_async):
    """The header must not contain <blockquote> — the body is in sendRichMessage."""
    bot, _ = bot_with_rich
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "**bold** content"}))
    header = bot._app.bot.send_message.await_args_list[0].args[1]
    assert "<blockquote" not in header


def test_idle_header_parse_mode_html(bot_with_rich, run_async):
    bot, _ = bot_with_rich
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "hi"}))
    kwargs = bot._app.bot.send_message.await_args_list[0].kwargs
    assert kwargs.get("parse_mode") == "HTML"


# ── fallback ─────────────────────────────────────────────────────────────────

def test_fallback_on_rich_message_required(mk_bot, run_async, monkeypatch):
    """RichMessageFallbackRequired → PTB send_message with no parse_mode."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(side_effect=RichMessageFallbackRequired("400")),
    )
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "# Heading\n\nText"}))
    # Two calls: header + fallback body
    assert bot._app.bot.send_message.await_count >= 2
    # The fallback call(s) must NOT pass parse_mode
    for c in bot._app.bot.send_message.await_args_list[1:]:
        assert c.kwargs.get("parse_mode") is None


def test_fallback_contains_body_content(mk_bot, run_async, monkeypatch):
    """The fallback plain-text message contains the original content."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(side_effect=RichMessageFallbackRequired("bad")),
    )
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "My important answer"}))
    texts = [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    assert any("My important answer" in t for t in texts)


def test_no_fallback_on_rich_message_blocked(mk_bot, run_async, monkeypatch):
    """RichMessageBlocked (403) → no fallback send_message for the body."""
    bot = mk_bot()
    sess = _sess()
    send_count = 0

    async def _counting_send(*a, **kw):
        nonlocal send_count
        send_count += 1
        return MagicMock(message_id=send_count)

    bot._app.bot.send_message = _counting_send
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(side_effect=RichMessageBlocked("forbidden")),
    )
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "secret"}))
    # Only the header should have been sent (count == 1)
    assert send_count == 1


# ── overflow ─────────────────────────────────────────────────────────────────

def test_overflow_triggers_file_attachment(mk_bot, run_async, monkeypatch):
    """Content > 32 768 UTF-8 bytes → body truncated + .txt attachment."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    long_content = "x" * 34_000  # 34 000 UTF-8 bytes
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": long_content}))
    bot._app.bot.send_document.assert_awaited_once()
    # Header includes the "attached below" notice
    header = bot._app.bot.send_message.await_args_list[0].args[1]
    assert "attached" in header.lower()


def test_overflow_body_truncated_below_limit(mk_bot, run_async, monkeypatch):
    """The markdown passed to sendRichMessage is ≤ 32 768 UTF-8 bytes."""
    bot = mk_bot()
    sess = _sess()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    captured = {}

    async def _capture(chat_id, markdown, **kw):
        captured["md"] = markdown
        return {}

    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _capture)
    long_content = "y" * 34_000
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": long_content}))
    assert len(captured["md"].encode("utf-8")) <= 32_768


def test_no_overflow_no_attachment(bot_with_rich, run_async):
    """Content under the limit must not trigger a file attachment."""
    bot, _ = bot_with_rich
    sess = _sess()
    bot._app.bot.send_document = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "short content"}))
    bot._app.bot.send_document.assert_not_awaited()


# ── stream field reset ────────────────────────────────────────────────────────

def test_idle_resets_all_stream_fields(bot_with_rich, run_async):
    bot, _ = bot_with_rich
    sess = _sess()
    sess.draft_id = 77
    sess.stream_offset = 9999
    sess.stream_text = "leftover"
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "done"}))
    assert sess.draft_id == 0
    assert sess.stream_offset == 0
    assert sess.stream_text == ""


# ── group scope works too ────────────────────────────────────────────────────

def test_idle_rich_message_sent_for_group_scope(mk_bot, run_async, monkeypatch):
    """Rich messages apply to group scopes too, not just DM."""
    bot = mk_bot()
    sess = _sess(scope_kind="group")
    sess.scope_chat_id = -100
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    rich_mock = AsyncMock(return_value={})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", rich_mock)
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "group content"}))
    rich_mock.assert_awaited_once()
