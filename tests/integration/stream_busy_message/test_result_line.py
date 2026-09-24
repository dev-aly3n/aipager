"""The result line ("session-name-on-every-message").

Every message aipager sends for a turn names its session and says which
kind it is, in the same place every time:

- the busy/finished card: its LAST line, ``⏳``/``✅``/``🔄`` + bold name
  (unchanged — ``animation._status_line``);
- every result message: its FIRST line, ``💬`` + bold name — the short
  form when a finished card is left in the chat to carry the turn's
  stats (`card`), the stats form ``💬 **name** · Finished (…)`` when no
  card remains (`replace`, `merged`'s fallback, a card-less turn, the
  overflow header);
- `merged`: one message, two sections, each with its own line exactly
  as it would be unmerged — status line, separator, result line, answer.

Exercised only via ``bot.notify(sess, "idle_prompt", context)`` and the
interim-buffer flush; HTTP mocked at ``rich_message._post`` (same
convention as test_layout_modes.py).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences as prefs
from aipager.bot.animation import FINAL_VERB, _RICH_LIMIT, build_stream_card_ex
from aipager.bot.notify import _MERGED_SEPARATOR
from aipager.state import Status, TrackedSession


def _sess(label="dev", *, scope_chat_id=555, busy_msg_id=42):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic() - 5
    s.scope_chat_id = scope_chat_id
    return s


@pytest.fixture
def rich_calls(monkeypatch):
    calls = []

    async def _fake_post(method, payload, **_kw):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 999}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return calls


def _wire_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.delete_message = AsyncMock()
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


def _md(calls, method):
    return [p["rich_message"]["markdown"] for m, p in calls if m == method]


def _finish(bot, run_async, sess, answer="the answer"):
    run_async(bot.notify(sess, "idle_prompt", {"summary": answer}))


# ── one line per layout ──────────────────────────────────────────────────────

def test_card_layout_answer_starts_with_the_result_line(mk_bot, run_async, rich_calls):
    """The finished card stays and already says Done + stats, so the
    answer carries the SHORT line: name and glyph, nothing repeated."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    # A card with a timeline to keep (a tool-less one goes alone since
    # roadmap 8.32 — tests/test_quiet_toolless_turns.py).
    sess.tool_history = [("Read: /a.py", True)]
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    _finish(bot, run_async, sess)

    (answer,) = _md(rich_calls, "sendRichMessage")
    assert answer == "💬 **dev**\n\nthe answer"
    (card,) = _md(rich_calls, "editMessageText")
    assert card.rpartition("\n\n")[2].startswith("✅ **dev** · Done")


def test_replace_layout_answer_starts_with_the_stats_result_line(
    mk_bot, run_async, rich_calls,
):
    """No card is left to carry the stats, so the one message says it all."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "replace")
    _finish(bot, run_async, sess)

    (answer,) = _md(rich_calls, "sendRichMessage")
    first, _, body = answer.partition("\n\n")
    assert first.startswith("💬 **dev** · Finished (")
    assert body == "the answer"
    bot._app.bot.send_message.assert_not_awaited()  # still one message


def test_merged_layout_marks_both_sections(mk_bot, run_async, rich_calls):
    """Status line, separator, result line, answer — each section carries
    exactly the line it would carry unmerged."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    sess.record_tool("Bash: ls", True)
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    _finish(bot, run_async, sess)

    (merged,) = _md(rich_calls, "editMessageText")
    assert not _md(rich_calls, "sendRichMessage")
    status = merged.index("✅ **dev** · Done")
    sep = merged.index(_MERGED_SEPARATOR)
    line = merged.index("💬 **dev**")
    body = merged.index("the answer")
    assert status < sep < line < body
    assert merged.endswith(f"{_MERGED_SEPARATOR}\n\n💬 **dev**\n\nthe answer")
    assert "Finished" not in merged


def test_merged_fallback_answer_starts_with_the_stats_result_line(
    mk_bot, run_async, monkeypatch,
):
    """The merge edit fails → the card is deleted and the answer goes out
    alone, so it carries the stats form like `replace`."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    calls = []

    async def _fake_post(method, payload, **_kw):
        calls.append((method, payload))
        if method == "editMessageText":
            return {"ok": False, "error_code": 400, "description": "boom"}
        return {"ok": True, "result": {"message_id": 5}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    _finish(bot, run_async, sess)

    (answer,) = _md(calls, "sendRichMessage")
    assert answer.startswith("💬 **dev** · Finished (")
    assert answer.endswith("\n\nthe answer")
    bot._app.bot.delete_message.assert_awaited_once()


def test_no_card_turn_composed_header_uses_the_result_glyph(
    mk_bot, run_async, rich_calls,
):
    bot = _wire_bot(mk_bot)
    sess = _sess(busy_msg_id=None)
    _finish(bot, run_async, sess)

    (answer,) = _md(rich_calls, "sendRichMessage")
    assert answer.startswith("💬 **dev** · Finished (")
    assert answer.endswith("\n\nthe answer")


def test_overflow_standalone_header_uses_the_result_glyph(
    mk_bot, run_async, rich_calls,
):
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    _finish(bot, run_async, sess, "x" * 40_000)

    bot._app.bot.send_message.assert_awaited_once()
    header = bot._app.bot.send_message.await_args.args[1]
    assert header.startswith("💬 <b>dev</b> · Finished (")
    assert "attached below" in header
    # The header message already opens the result; the body follows it
    # bare rather than naming the session twice.
    (body,) = _md(rich_calls, "sendRichMessage")
    assert not body.startswith("💬")


def test_interim_buffer_flush_starts_with_the_result_line(
    mk_bot, run_async, rich_calls,
):
    """An interim answer flushed on a job's close path is a result too."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    sess.job_interim_buffer = ["first interim", "second interim"]
    run_async(bot._flush_job_buffer(sess))

    (flushed,) = _md(rich_calls, "sendRichMessage")
    assert flushed == "💬 **dev**\n\nfirst interim\n\n———\n\nsecond interim"


def test_plain_text_fallback_first_chunk_starts_with_the_plain_result_line(
    mk_bot, run_async, monkeypatch,
):
    bot = _wire_bot(mk_bot)
    sess = _sess()
    sess.tool_history = [("Read: /a.py", True)]  # a kept card (8.32)
    prefs.set_preference(sess.scope_chat_id, "layout", "card")

    async def _fake_post(method, payload, **_kw):
        if method == "sendRichMessage":
            return {"ok": False, "error_code": 400, "description": "nope"}
        return {"ok": True, "result": {"message_id": 5}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    _finish(bot, run_async, sess)

    bot._app.bot.send_message.assert_awaited_once()
    assert bot._app.bot.send_message.await_args.args[1] == "💬 dev\n\nthe answer"


def test_interim_flush_plain_fallback_starts_with_the_plain_result_line(
    mk_bot, run_async, monkeypatch,
):
    bot = _wire_bot(mk_bot)
    sess = _sess()
    sess.job_interim_buffer = ["interim"]

    async def _fake_post(method, payload, **_kw):
        return {"ok": False, "error_code": 400, "description": "nope"}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    run_async(bot._flush_job_buffer(sess))

    bot._app.bot.send_message.assert_awaited_once()
    assert bot._app.bot.send_message.await_args.args[1] == "💬 dev\n\ninterim"


# ── the line counts against the ceilings ─────────────────────────────────────

def test_merged_size_check_counts_the_result_line(mk_bot, run_async, rich_calls):
    """A combined text that fit the byte ceiling by less than the result
    line must now fall back, never send an over-limit edit."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    sess.busy_started_at = None  # no ticking elapsed segment: deterministic size
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    card_md, _hid = build_stream_card_ex(sess, FINAL_VERB, final=True)
    base = len(f"{card_md}\n\n{_MERGED_SEPARATOR}\n\n".encode("utf-8"))
    answer = "x" * (_RICH_LIMIT - base - 2)  # fits without the line, not with it
    _finish(bot, run_async, sess, answer)

    assert "editMessageText" not in [m for m, _p in rich_calls]
    assert "sendRichMessage" in [m for m, _p in rich_calls]
    for markdown in _md(rich_calls, "sendRichMessage"):
        assert len(markdown.encode("utf-8")) <= _RICH_LIMIT


def test_body_overflow_threshold_leaves_room_for_the_result_line(
    mk_bot, run_async, rich_calls,
):
    """A body exactly at the old ceiling plus the new first line would
    exceed it; the overflow check must leave room for the line."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    _finish(bot, run_async, sess, "y" * _RICH_LIMIT)

    for markdown in _md(rich_calls, "sendRichMessage"):
        assert len(markdown.encode("utf-8")) <= _RICH_LIMIT
    bot._app.bot.send_message.assert_awaited_once()  # the overflow header went out


# ── the plain-text chunker packs paragraphs ──────────────────────────────────

def test_plain_text_chunks_pack_short_paragraphs_into_one_message():
    """The result line is the first paragraph of every plain-text
    fallback; splitting at every blank line made it a message of its own."""
    from aipager.bot.notify import _plain_text_chunks
    assert _plain_text_chunks("💬 dev\n\nthe answer\n\nsecond para") == [
        "💬 dev\n\nthe answer\n\nsecond para",
    ]


def test_plain_text_chunks_split_only_when_a_paragraph_would_overflow():
    from aipager.bot.notify import _plain_text_chunks
    first = "a" * 3000
    second = "b" * 3000
    third = "c" * 500
    chunks = _plain_text_chunks(f"{first}\n\n{second}\n\n{third}")
    # A boundary sits between the two newlines (transport._md_safe_boundaries),
    # so the cut leaves one on each side — the pre-existing split shape.
    assert chunks == [f"{first}\n", f"\n{second}\n\n{third}"]
    assert all(len(c.encode("utf-8")) <= 4096 for c in chunks)


def test_plain_text_chunks_hard_cut_an_oversized_paragraph():
    from aipager.bot.notify import _plain_text_chunks
    chunks = _plain_text_chunks("x" * 9000)
    assert [len(c) for c in chunks] == [4096, 4096, 808]
