"""Integration: content-selection rule and boundary cases.

Success criteria covered:
  SC2  - raw_md → summary → sess.summary → ""
  SC3  - {"summary": ...} with no raw_md key (idle-recovery path) sends "text"
  SC4  - all empty → header only, zero body sends of any kind

Also covers error-guessing scenarios:
  - raw_md present but empty string (treated as falsy, falls through to summary)
  - summary present but empty string (falls through to sess.summary)
  - sess.summary present but empty (falls through to "", no body send)
  - KeyError must never be raised when raw_md key is absent
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


def _sess(label="bob", *, scope_kind="dm"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.busy_started_at = time.monotonic()
    s.scope_kind = scope_kind
    s.scope_chat_id = 222222
    return s


@pytest.fixture
def rich_mock(monkeypatch):
    mock = AsyncMock(return_value={})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", mock)
    return mock


# ── SC2 — raw_md preferred over summary ──────────────────────────────────────

def test_sc2_raw_md_wins_over_summary(mk_bot, run_async, rich_mock):
    """raw_md non-empty ⇒ content == raw_md; summary is ignored for the body."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = "stale summary"

    run_async(bot.notify(sess, "idle_prompt", {
        "raw_md": "live raw markdown",
        "summary": "summary that should be ignored",
    }))

    assert rich_mock.await_args.args[1] == "live raw markdown"


def test_sc2_summary_used_when_raw_md_absent(mk_bot, run_async, rich_mock):
    """raw_md absent ⇒ content == summary from context."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = "stale summary"

    run_async(bot.notify(sess, "idle_prompt", {"summary": "context summary text"}))

    assert rich_mock.await_args.args[1] == "context summary text"


def test_sc2_sess_summary_fallback_when_context_empty(mk_bot, run_async, rich_mock):
    """raw_md absent, summary absent ⇒ content == sess.summary."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = "session-level summary text"

    run_async(bot.notify(sess, "idle_prompt", {}))

    assert rich_mock.await_args.args[1] == "session-level summary text"


# ── SC3 — idle-recovery path: {"summary": ...} with NO raw_md key ────────────

def test_sc3_idle_recovery_no_keyerror(mk_bot, run_async, rich_mock):
    """Context with only 'summary' (no 'raw_md' key) must not raise KeyError."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()

    # Must NOT raise
    run_async(bot.notify(sess, "idle_prompt", {"summary": "recovered from idle"}))


def test_sc3_idle_recovery_sends_summary_text(mk_bot, run_async, rich_mock):
    """The idle-recovery path (no raw_md) sends the summary as the body."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = "stale old summary"

    run_async(bot.notify(sess, "idle_prompt", {"summary": "recovered summary text"}))

    assert rich_mock.await_args.args[1] == "recovered summary text"


# ── SC4 — all empty → header only, zero body sends ───────────────────────────

def test_sc4_all_empty_no_body_call(mk_bot, run_async, rich_mock):
    """All sources empty → sendRichMessage must NOT be called."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = ""

    run_async(bot.notify(sess, "idle_prompt", {}))

    rich_mock.assert_not_awaited()


def test_sc4_all_empty_header_still_sent(mk_bot, run_async, rich_mock):
    """All sources empty → the ✅ Finished header is still sent."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = ""

    run_async(bot.notify(sess, "idle_prompt", {}))

    bot._app.bot.send_message.assert_awaited_once()
    header_text = bot._app.bot.send_message.await_args.args[1]
    assert "Finished" in header_text


def test_sc4_empty_raw_md_string_falls_through_to_summary(mk_bot, run_async, rich_mock):
    """raw_md='' (empty string, falsy) must fall through to summary."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = "sess fallback"

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "", "summary": "ctx summary"}))

    # Summary should be used, not the empty raw_md
    assert rich_mock.await_args.args[1] == "ctx summary"


def test_sc4_empty_summary_falls_through_to_sess_summary(mk_bot, run_async, rich_mock):
    """summary='' (falsy) must fall through to sess.summary."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = "session fallback text"

    run_async(bot.notify(sess, "idle_prompt", {"summary": ""}))

    assert rich_mock.await_args.args[1] == "session fallback text"


def test_sc4_empty_body_must_not_send_plain_text_either(mk_bot, run_async, rich_mock, monkeypatch):
    """All empty → no plain-text fallback send either (only the header)."""
    bot = mk_bot()
    send_calls = []

    async def _track_send(chat_id, text, **kw):
        send_calls.append(text)
        return MagicMock(message_id=len(send_calls))

    bot._app.bot.send_message = _track_send
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()
    sess.summary = ""

    run_async(bot.notify(sess, "idle_prompt", {}))

    # Only one call: the header
    assert len(send_calls) == 1
    assert "Finished" in send_calls[0]
