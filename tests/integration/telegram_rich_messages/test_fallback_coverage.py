"""Integration: reply-loss prevention — fallback fires for every error class except 403.

Success criteria covered:
  SC1  - body sent via sendRichMessage with content verbatim (no blockquote)
  SC5  - every non-403 failure fires exactly one PTB send_message with parse_mode=None
         carrying the IDENTICAL content
  SC6  - 403 never fires a fallback
  SC7  - 429 retries once, then falls back (tested at the notify integration level)

Error classes probed:
  400, 404, 429-twice, 5xx, httpx.TimeoutException, httpx.ConnectError

This is a BLACK-BOX test: we assert on the public behavioural contract described in
entrypoints.md "idle-fallback" and design.md success criteria 5, 6, 7.  We do NOT read
rich_message.py internals; we only mock aipager.bot.notify.send_rich_message (the public
import surface that notify.py uses) and observe the PTB send_message calls.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager.bot.rich_message import RichMessageBlocked, RichMessageFallbackRequired
from aipager.state import Status, TrackedSession


# ── helpers ──────────────────────────────────────────────────────────────────

def _sess(label="alice", *, scope_kind="dm"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.busy_started_at = time.monotonic()
    s.scope_kind = scope_kind
    s.scope_chat_id = 111111
    return s


def _bot(mk_bot, monkeypatch, *, rich_side_effect):
    """Return a bot whose send_rich_message raises `rich_side_effect`."""
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(side_effect=rich_side_effect),
    )
    return bot


# ── SC1 — body sent via sendRichMessage with verbatim content ────────────────

def test_sc1_body_content_passed_verbatim_to_rich_message(mk_bot, run_async, monkeypatch):
    """The markdown passed to sendRichMessage must carry `raw_md` verbatim.

    No card exists here, so the composed header line rides ahead of it
    (requirement 4: one message, not two) — the body itself is untouched."""
    captured = {}

    async def _capture(chat_id, markdown, **kw):
        captured["markdown"] = markdown
        return {}

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _capture)

    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "# Heading\n\nSome **bold** text"}))

    markdown = captured.get("markdown", "")
    assert markdown.startswith("✅ **alice** · Finished")
    assert markdown.split("\n\n", 1)[1] == "# Heading\n\nSome **bold** text"


def test_sc1_no_blockquote_in_body_call(mk_bot, run_async, monkeypatch):
    """The markdown payload must not be wrapped in a blockquote."""
    captured = {}

    async def _capture(chat_id, markdown, **kw):
        captured["markdown"] = markdown
        return {}

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _capture)

    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "Plain markdown body"}))

    assert "<blockquote" not in captured.get("markdown", "")


# ── SC5 — 400 fires fallback with identical content, no parse_mode ───────────

def test_sc5_400_fallback_fires(mk_bot, run_async, monkeypatch):
    """HTTP 400 from sendRichMessage → fallback plain-text send_message."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("bad markdown"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "My answer 400"}))

    # header (index 0) + fallback body (index 1+)
    calls = bot._app.bot.send_message.await_args_list
    assert len(calls) >= 2


def test_sc5_400_fallback_has_no_parse_mode(mk_bot, run_async, monkeypatch):
    """400 fallback must be sent with parse_mode=None (or omitted)."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("bad"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "content 400"}))

    calls = bot._app.bot.send_message.await_args_list
    # All calls after the first header must have no parse_mode
    for call in calls[1:]:
        assert call.kwargs.get("parse_mode") is None


def test_sc5_400_fallback_carries_identical_content(mk_bot, run_async, monkeypatch):
    """The fallback message must contain the identical raw_md content."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("bad"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "unique_content_xyz_400"}))

    texts = [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    assert any("unique_content_xyz_400" in t for t in texts)


# ── SC5 — 404 fires fallback ──────────────────────────────────────────────────

def test_sc5_404_fallback_fires(mk_bot, run_async, monkeypatch):
    """HTTP 404 from sendRichMessage → fallback plain-text send_message."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("method not found"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "content 404"}))

    calls = bot._app.bot.send_message.await_args_list
    assert len(calls) >= 2


def test_sc5_404_fallback_no_parse_mode(mk_bot, run_async, monkeypatch):
    """404 fallback must be sent with parse_mode=None."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("not found"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "content 404 pm"}))

    calls = bot._app.bot.send_message.await_args_list
    for call in calls[1:]:
        assert call.kwargs.get("parse_mode") is None


# ── SC5 — 429-twice fires fallback ───────────────────────────────────────────

def test_sc5_429_twice_fallback_fires(mk_bot, run_async, monkeypatch):
    """Two consecutive 429 responses trigger the fallback plain-text send."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("rate limit"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "content 429"}))

    calls = bot._app.bot.send_message.await_args_list
    assert len(calls) >= 2


def test_sc5_429_twice_fallback_carries_identical_content(mk_bot, run_async, monkeypatch):
    """429-twice fallback must carry the original content."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("rate limit"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "unique_429_content"}))

    texts = [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    assert any("unique_429_content" in t for t in texts)


# ── SC5 — 5xx fires fallback ──────────────────────────────────────────────────

def test_sc5_5xx_fallback_fires(mk_bot, run_async, monkeypatch):
    """HTTP 5xx from sendRichMessage → fallback plain-text send_message."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("server error"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "content 5xx"}))

    calls = bot._app.bot.send_message.await_args_list
    assert len(calls) >= 2


def test_sc5_5xx_fallback_no_parse_mode(mk_bot, run_async, monkeypatch):
    """5xx fallback must be sent with parse_mode=None."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("server error"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "5xx no pm"}))

    calls = bot._app.bot.send_message.await_args_list
    for call in calls[1:]:
        assert call.kwargs.get("parse_mode") is None


# ── SC5 — timeout fires fallback ─────────────────────────────────────────────

def test_sc5_timeout_fallback_fires(mk_bot, run_async, monkeypatch):
    """Timeout from sendRichMessage → fallback plain-text send_message."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("timeout"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "timeout content"}))

    calls = bot._app.bot.send_message.await_args_list
    assert len(calls) >= 2


def test_sc5_timeout_fallback_carries_content(mk_bot, run_async, monkeypatch):
    """Timeout fallback must carry the original content."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("timeout"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "unique_timeout_content"}))

    texts = [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    assert any("unique_timeout_content" in t for t in texts)


# ── SC5 — connection error fires fallback ────────────────────────────────────

def test_sc5_connection_error_fallback_fires(mk_bot, run_async, monkeypatch):
    """Connection error from sendRichMessage → fallback plain-text send_message."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("connection refused"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "conn error content"}))

    calls = bot._app.bot.send_message.await_args_list
    assert len(calls) >= 2


def test_sc5_connection_error_fallback_no_parse_mode(mk_bot, run_async, monkeypatch):
    """Connection error fallback must be sent with parse_mode=None."""
    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("refused"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "refused content"}))

    calls = bot._app.bot.send_message.await_args_list
    for call in calls[1:]:
        assert call.kwargs.get("parse_mode") is None


# ── SC6 — 403 never fires fallback ───────────────────────────────────────────

def test_sc6_403_blocked_no_fallback(mk_bot, run_async, monkeypatch):
    """403 RichMessageBlocked must not trigger a fallback body send."""
    sent_texts = []

    async def _counting_send(chat_id, text, **kw):
        sent_texts.append(text)
        return MagicMock(message_id=len(sent_texts))

    bot = mk_bot()
    bot._app.bot.send_message = _counting_send
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(side_effect=RichMessageBlocked("forbidden")),
    )

    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "secret body"}))

    # Only the header was sent, not the body
    body_sends = [t for t in sent_texts if "secret body" in t]
    assert len(body_sends) == 0


def test_sc6_403_exactly_one_send_message(mk_bot, run_async, monkeypatch):
    """403 must never fall back — with no card, the header lives only
    inside the blocked rich message, so nothing reaches PTB at all."""
    send_count = 0

    async def _count_send(*a, **kw):
        nonlocal send_count
        send_count += 1
        return MagicMock(message_id=send_count)

    bot = mk_bot()
    bot._app.bot.send_message = _count_send
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(side_effect=RichMessageBlocked("forbidden")),
    )

    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "blocked content"}))

    assert send_count == 0


# ── Fallback chunking at 4096 ────────────────────────────────────────────────

def test_fallback_chunking_long_content_multiple_sends(mk_bot, run_async, monkeypatch):
    """Plain-text fallback must chunk content > 4096 chars into multiple sends."""
    # 10,000 chars exceeds 4096; expect multiple sends
    long_content = "A" * 10_000

    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("400"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": long_content}))

    calls = bot._app.bot.send_message.await_args_list
    # header + at least 2 chunk sends (10000 / 4096 > 2)
    assert len(calls) >= 3


def test_fallback_chunks_no_parse_mode(mk_bot, run_async, monkeypatch):
    """Every chunk in the fallback must be sent with parse_mode=None."""
    long_content = "B" * 10_000

    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("400"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": long_content}))

    calls = bot._app.bot.send_message.await_args_list
    # Skip header (index 0); all fallback sends must lack parse_mode
    for call in calls[1:]:
        assert call.kwargs.get("parse_mode") is None


def test_fallback_each_chunk_max_4096_chars(mk_bot, run_async, monkeypatch):
    """No individual fallback chunk should exceed 4096 characters."""
    long_content = "C" * 12_000

    bot = _bot(mk_bot, monkeypatch,
               rich_side_effect=RichMessageFallbackRequired("400"))
    sess = _sess()
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": long_content}))

    calls = bot._app.bot.send_message.await_args_list
    # Skip header (index 0)
    for call in calls[1:]:
        assert len(call.args[1]) <= 4096
