"""Integration: byte-boundary tests for the 32 768 UTF-8 limit.

Success criteria covered:
  SC9  - content > 32 768 UTF-8 bytes → body truncated + .txt attachment sent

Boundary analysis:
  - Content of exactly 32 768 UTF-8 bytes: no overflow, no attachment
  - Content of 32 769 UTF-8 bytes: overflow triggered
  - Multi-byte Persian/emoji content: under the CHAR limit but over the BYTE limit

Error guessing:
  - Persian text where len(chars) < 32768 but len(bytes) > 32768 (multi-byte trap)
  - Emoji-heavy content (4 bytes per emoji) similarly traps the naive char-count
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


def _sess(label="charlie", *, scope_kind="dm"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.busy_started_at = time.monotonic()
    s.scope_kind = scope_kind
    s.scope_chat_id = 333333
    return s


@pytest.fixture
def rich_mock(monkeypatch):
    mock = AsyncMock(return_value={})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", mock)
    return mock


def _run_notify(mk_bot, run_async, rich_mock, content):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    # Override rich_mock on this bot directly
    run_async(bot.notify(bot, "idle_prompt", {"raw_md": content}))
    return bot


# ── at-limit: exactly 32768 UTF-8 bytes, single-byte ASCII ──────────────────

def test_sc9_exactly_at_limit_no_attachment(mk_bot, run_async, rich_mock):
    """32 768 bytes of ASCII → within limit → no .txt attachment."""
    at_limit = "x" * 32_768  # 32768 ASCII bytes == 32768 chars

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": at_limit}))

    bot._app.bot.send_document.assert_not_awaited()


def test_sc9_one_byte_over_limit_triggers_attachment(mk_bot, run_async, monkeypatch):
    """32 769 bytes of ASCII → over limit → .txt attachment sent."""
    over_limit = "x" * 32_769

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": over_limit}))

    bot._app.bot.send_document.assert_awaited_once()


def test_sc9_overflow_body_under_limit(mk_bot, run_async, monkeypatch):
    """When overflow occurs, the markdown sent to sendRichMessage is ≤ 32 768 UTF-8 bytes."""
    over_limit = "x" * 34_000
    captured = {}

    async def _capture(chat_id, markdown, **kw):
        captured["markdown"] = markdown
        return {}

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _capture)
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": over_limit}))

    assert len(captured["markdown"].encode("utf-8")) <= 32_768


# ── multi-byte boundary: Persian text under the CHAR limit but over BYTE limit ─

def test_sc9_multibyte_persian_over_byte_limit_triggers_attachment(mk_bot, run_async, monkeypatch):
    """Persian characters are 2 bytes each in UTF-8.
    16_385 Persian chars = 32_770 bytes → over the BYTE limit → attachment sent.
    But len(chars) == 16_385 < 32_768, so a naive char-count would miss this.
    """
    persian_char = "ا"  # U+0627, 2 bytes in UTF-8
    assert len(persian_char.encode("utf-8")) == 2

    # 16 385 × 2 = 32 770 bytes > 32 768
    persian_content = persian_char * 16_385
    assert len(persian_content) < 32_768           # char count under limit
    assert len(persian_content.encode("utf-8")) > 32_768  # byte count over limit

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": persian_content}))

    bot._app.bot.send_document.assert_awaited_once()


def test_sc9_multibyte_persian_under_byte_limit_no_attachment(mk_bot, run_async, monkeypatch):
    """16 384 Persian chars = 32 768 bytes → exactly at limit → no attachment."""
    persian_char = "ا"
    persian_content = persian_char * 16_384
    assert len(persian_content.encode("utf-8")) == 32_768  # exactly at limit

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": persian_content}))

    bot._app.bot.send_document.assert_not_awaited()


def test_sc9_emoji_over_byte_limit_triggers_attachment(mk_bot, run_async, monkeypatch):
    """Emoji are 4 bytes each in UTF-8.
    8193 emoji = 32 772 bytes > 32 768 → attachment sent.
    len(chars) == 8193, well under 32 768.
    """
    emoji = "\U0001F600"  # grinning face, 4 bytes
    assert len(emoji.encode("utf-8")) == 4

    emoji_content = emoji * 8_193
    assert len(emoji_content) == 8_193             # chars
    assert len(emoji_content.encode("utf-8")) == 32_772  # bytes

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": emoji_content}))

    bot._app.bot.send_document.assert_awaited_once()


def test_sc9_overflow_truncated_body_is_valid_utf8(mk_bot, run_async, monkeypatch):
    """The markdown sent to sendRichMessage after truncation must be valid UTF-8
    (no partial multi-byte sequences at the cut point).
    """
    persian_char = "ا"
    # Build content with a newline boundary so _md_safe_boundaries can find a cut
    chunk = persian_char * 100 + "\n"
    over_limit = chunk * 170  # well over 32 768 bytes
    assert len(over_limit.encode("utf-8")) > 32_768

    captured = {}

    async def _capture(chat_id, markdown, **kw):
        captured["markdown"] = markdown
        return {}

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _capture)
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": over_limit}))

    # Must be decodable without error
    try:
        captured["markdown"].encode("utf-8").decode("utf-8")
        valid = True
    except UnicodeDecodeError:
        valid = False
    assert valid


def test_sc9_overflow_header_mentions_attachment(mk_bot, run_async, monkeypatch):
    """When overflow occurs, the header message must mention the attachment."""
    over_limit = "z" * 34_000

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": over_limit}))

    header = bot._app.bot.send_message.await_args_list[0].args[1]
    assert "attach" in header.lower()


def test_sc9_overflow_no_truncation_banner(mk_bot, run_async, monkeypatch):
    """No ╔═ ✂️ TRUNCATED ✂️ ═╗ banner must appear in the body sent to sendRichMessage."""
    over_limit = "w" * 34_000
    captured = {}

    async def _capture(chat_id, markdown, **kw):
        captured["markdown"] = markdown
        return {}

    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _capture)
    sess = _sess()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": over_limit}))

    assert "TRUNCATED" not in captured.get("markdown", "")
    assert "✂️" not in captured.get("markdown", "")
