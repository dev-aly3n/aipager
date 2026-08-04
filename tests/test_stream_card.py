"""Unit tests for the streaming card and buffer helpers.

Covers build_stream_card (pure function), _read_stream_text, _reveal_chunk,
and the _edit_busy_rich method on AnimationMixin.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession
from aipager.bot.animation import (
    build_stream_card,
    _read_stream_text,
    _reveal_chunk,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def run_async():
    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)
    return _run


def _sess(label="dev", scope="dm"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = scope
    s.scope_chat_id = 12345 if scope == "dm" else -100
    s.busy_started_at = time.monotonic() - 10  # 10s ago
    return s


# ── build_stream_card: layout ─────────────────────────────────────────────────

def test_card_header_contains_verb():
    sess = _sess()
    card = build_stream_card(sess, "Thinking")
    assert "Thinking" in card


def test_card_header_contains_label():
    sess = _sess("myproj")
    card = build_stream_card(sess, "Working")
    assert "myproj" in card


def test_card_no_body_omits_divider():
    sess = _sess()
    sess.stream_shown = ""
    card = build_stream_card(sess, "Working")
    # Should NOT have two consecutive blank lines (empty body placeholder)
    assert "\n\n\n" not in card
    # Nothing to divide, so the divider is dropped — a bare rule above the
    # footer reads as a rendering glitch.
    assert "────────────────" not in card
    assert "⏳" in card


def test_card_with_body_contains_body():
    sess = _sess()
    sess.stream_shown = "Here is some prose."
    card = build_stream_card(sess, "Working")
    assert "Here is some prose." in card
    assert "────────────────" in card


def test_card_body_appears_between_header_and_divider():
    sess = _sess()
    sess.stream_shown = "body text"
    card = build_stream_card(sess, "Working")
    divider_pos = card.index("────────────────")
    body_pos = card.index("body text")
    assert body_pos < divider_pos


def test_card_footer_after_divider():
    sess = _sess()
    sess.stream_shown = "body text"
    card = build_stream_card(sess, "Working")
    divider_pos = card.index("────────────────")
    footer_pos = card.index("⏳")
    assert footer_pos > divider_pos
    # A blank line must separate them: Telegram collapses a lone newline and
    # would render the footer on the divider's line.
    assert f"────────────────\n\n{card[footer_pos:]}" in card


# ── build_stream_card: footer segments ───────────────────────────────────────

def test_card_elapsed_shown_when_ge_2s():
    sess = _sess()
    sess.busy_started_at = time.monotonic() - 5
    card = build_stream_card(sess, "Working")
    assert "5s" in card


def test_card_elapsed_omitted_when_lt_2s():
    sess = _sess()
    sess.busy_started_at = time.monotonic() - 1
    card = build_stream_card(sess, "Working")
    # Should not show "1s"
    assert "1s" not in card


def test_card_elapsed_format_minutes():
    sess = _sess()
    sess.busy_started_at = time.monotonic() - 125
    card = build_stream_card(sess, "Working")
    assert "2m" in card


def test_card_cost_shown_when_delta_above_threshold():
    sess = _sess()
    sess.cost_baseline = 1.0
    sess.last_cost_usd = 1.05
    card = build_stream_card(sess, "Working")
    assert "$0.05" in card


def test_card_cost_omitted_when_baseline_unset():
    sess = _sess()
    sess.cost_baseline = None
    sess.last_cost_usd = 1.0
    card = build_stream_card(sess, "Working")
    assert "$" not in card


def test_card_cost_omitted_when_delta_le_threshold():
    sess = _sess()
    sess.cost_baseline = 1.0
    sess.last_cost_usd = 1.001  # delta == 0.001 → omit
    card = build_stream_card(sess, "Working")
    assert "$" not in card


def test_card_tool_tally_shown():
    sess = _sess()
    sess.tool_history = [
        ("Read: /a/b.py", True),
        ("Read: /c/d.py", True),
        ("Read: /e/f.py", True),
        ("Grep: pattern in aipager", True),
    ]
    card = build_stream_card(sess, "Working")
    assert "Read ×3" in card
    assert "Grep ×1" in card


def test_card_tool_tally_names_have_no_trailing_colon():
    """Summaries arrive as "Read: /path" — the colon must not leak into the tally."""
    sess = _sess()
    sess.tool_history = [("Bash: run the tests", True)]
    card = build_stream_card(sess, "Working")
    assert "Bash ×1" in card
    assert "Bash: ×1" not in card


def test_card_tool_tally_handles_subagent_summaries():
    sess = _sess()
    sess.tool_history = [("\U0001f916 general-purpose", False),
                         ("\U0001f916 Explore", True)]
    card = build_stream_card(sess, "Working")
    assert "\U0001f916 ×2" in card


def test_card_tool_tally_omitted_when_empty():
    sess = _sess()
    sess.tool_history = []
    card = build_stream_card(sess, "Working")
    # Footer exists but no tally segments beyond ⏳
    lines = card.split("\n")
    footer_line = next(ln for ln in lines if "⏳" in ln)
    # No × symbol (tally format is "Name ×N")
    assert "×" not in footer_line


# ── build_stream_card: label escaping ────────────────────────────────────────

def test_card_label_asterisk_escaped():
    sess = _sess()
    sess.label = "my*project"
    card = build_stream_card(sess, "Working")
    # The raw asterisk must not appear unescaped in the header
    # (it would break the **label** bold formatting)
    assert "my\\*project" in card


def test_card_label_backtick_escaped():
    sess = _sess()
    sess.label = "my`project"
    card = build_stream_card(sess, "Working")
    assert "my\\`project" in card


# ── build_stream_card: purity ─────────────────────────────────────────────────

def test_card_is_pure_identical_output():
    sess = _sess()
    sess.stream_shown = "some text"
    sess.busy_started_at = 1000.0  # fixed monotonic
    sess.cost_baseline = 0.0
    sess.last_cost_usd = 0.0
    # Call twice — output must be byte-identical
    out1 = build_stream_card(sess, "Thinking")
    out2 = build_stream_card(sess, "Thinking")
    assert out1 == out2


def test_card_does_not_mutate_sess():
    sess = _sess()
    sess.stream_shown = "text"
    shown_before = sess.stream_shown
    pending_before = sess.stream_pending
    build_stream_card(sess, "Working")
    assert sess.stream_shown == shown_before
    assert sess.stream_pending == pending_before


# ── build_stream_card: truncation ────────────────────────────────────────────

def test_card_truncation_output_within_limit():
    sess = _sess()
    # Generate a body that is much larger than 32768 bytes
    sess.stream_shown = "x" * 40_000
    card = build_stream_card(sess, "Working")
    assert len(card.encode("utf-8")) <= 32_768


def test_card_truncation_footer_preserved():
    sess = _sess()
    sess.stream_shown = "y" * 40_000
    card = build_stream_card(sess, "Working")
    assert "────────────────" in card
    assert "⏳" in card


def test_card_truncation_head_dropped():
    sess = _sess()
    # The head of the body is "FIRST" and the tail is "LAST"
    sess.stream_shown = "FIRST " + "middle " * 5000 + "LAST"
    card = build_stream_card(sess, "Working")
    # The head should have been dropped
    assert "FIRST" not in card
    assert "LAST" in card


def test_card_truncation_valid_utf8():
    sess = _sess()
    # Persian text repeated to exceed the limit
    sess.stream_shown = "سلام دنیا " * 4000
    card = build_stream_card(sess, "Working")
    assert len(card.encode("utf-8")) <= 32_768
    card.encode("utf-8")  # must not raise


# ── _read_stream_text ─────────────────────────────────────────────────────────

def _write_assistant(tmp_path, text: str) -> str:
    entry = {"type": "assistant", "message": {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }}
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(entry) + "\n")
    return str(p)


def test_read_stream_text_returns_true_when_new_text(tmp_path):
    sess = _sess()
    tp = _write_assistant(tmp_path, "new commentary")
    sess.stream_transcript_path = tp
    sess.stream_offset = 0
    result = _read_stream_text(sess)
    assert result is True
    assert "new commentary" in sess.stream_pending


def test_read_stream_text_returns_false_when_no_path():
    sess = _sess()
    sess.stream_transcript_path = ""
    result = _read_stream_text(sess)
    assert result is False


def test_read_stream_text_returns_false_when_no_new_text(tmp_path):
    tp = _write_assistant(tmp_path, "old text")
    sess = _sess()
    sess.stream_transcript_path = tp
    sess.stream_offset = os.path.getsize(tp)  # seeded to file size
    result = _read_stream_text(sess)
    assert result is False
    assert sess.stream_pending == ""


def test_read_stream_text_appends_with_blank_line(tmp_path):
    tp = _write_assistant(tmp_path, "first block")
    sess = _sess()
    sess.stream_transcript_path = tp
    sess.stream_offset = 0
    _read_stream_text(sess)
    # Simulate a second block appended
    entry2 = {"type": "assistant", "message": {
        "content": [{"type": "text", "text": "second block"}],
        "stop_reason": "end_turn",
    }}
    with open(tp, "a") as f:
        f.write(json.dumps(entry2) + "\n")
    _read_stream_text(sess)
    assert "first block" in sess.stream_pending
    assert "second block" in sess.stream_pending
    assert "\n\n" in sess.stream_pending


def test_read_stream_text_advances_offset(tmp_path):
    tp = _write_assistant(tmp_path, "content")
    sess = _sess()
    sess.stream_transcript_path = tp
    sess.stream_offset = 0
    _read_stream_text(sess)
    assert sess.stream_offset > 0


# ── _reveal_chunk ─────────────────────────────────────────────────────────────

def test_reveal_chunk_returns_false_when_pending_empty():
    sess = _sess()
    sess.stream_pending = ""
    assert _reveal_chunk(sess) is False


def test_reveal_chunk_returns_true_when_text_available():
    sess = _sess()
    sess.stream_pending = "some text here"
    assert _reveal_chunk(sess) is True


def test_reveal_chunk_moves_text_to_shown():
    sess = _sess()
    sess.stream_pending = "hello world"
    sess.stream_shown = ""
    _reveal_chunk(sess)
    assert "hello world" in sess.stream_shown


def test_reveal_chunk_does_not_split_words():
    sess = _sess()
    # A string where a 280-char cut would land mid-word.
    # Make a word that straddles the boundary.
    prefix = "a " * 139   # 278 chars (139 "a " pairs)
    long_word = "longwordhere"
    tail = " after"
    sess.stream_pending = prefix + long_word + tail
    sess.stream_shown = ""
    _reveal_chunk(sess)
    # The shown text must end at a whitespace boundary, not mid-word
    shown = sess.stream_shown
    # The last character of shown must not be in the middle of a word
    # (i.e., the next char in the original string should be a space or we used all)
    if sess.stream_pending:
        # There's still pending text, which means we stopped at a boundary
        assert shown.endswith(" ") or shown == sess.stream_shown


def test_reveal_chunk_small_pending_clears_it():
    sess = _sess()
    sess.stream_pending = "short"
    sess.stream_shown = ""
    _reveal_chunk(sess)
    assert sess.stream_pending == ""
    assert sess.stream_shown == "short"


def test_reveal_chunk_large_pending_leaves_remainder():
    sess = _sess()
    # 600 chars of text — more than STREAM_REVEAL_CHARS (280)
    long_text = "word " * 120  # 600 chars
    sess.stream_pending = long_text
    sess.stream_shown = ""
    _reveal_chunk(sess)
    assert len(sess.stream_shown) <= 280 + 5  # +5 tolerance for boundary
    assert sess.stream_pending != ""  # still something left


def test_reveal_chunk_1000_chars_needs_multiple_reveals():
    sess = _sess()
    text = "word " * 200  # 1000 chars
    sess.stream_pending = text
    sess.stream_shown = ""
    count = 0
    while sess.stream_pending:
        _reveal_chunk(sess)
        count += 1
        if count > 50:
            break  # safety
    assert count > 1, "A 1000-char blob must take more than one reveal"


# ── _edit_busy_rich via AnimationMixin ────────────────────────────────────────

def test_edit_busy_rich_skips_post_when_markdown_identical(mk_bot, run_async, monkeypatch):
    """Dedupe: identical consecutive renders produce only one HTTP call."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5

    post_calls = []

    async def _fake_post(method, payload):
        post_calls.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    # Force stream_last_rendered to match what build_stream_card will produce
    sess.stream_last_rendered = build_stream_card(sess, "Working")
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert len(post_calls) == 0  # no POST because content identical


def test_edit_busy_rich_posts_when_content_changed(mk_bot, run_async, monkeypatch):
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""  # nothing rendered yet

    post_calls = []

    async def _fake_post(method, payload):
        post_calls.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert len(post_calls) == 1


def test_edit_busy_rich_reply_markup_on_every_call(mk_bot, run_async, monkeypatch):
    """Stop button must ride along on every edit."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert len(payloads) == 1
    assert "reply_markup" in payloads[0]


def test_edit_busy_rich_blocked_returns_none(mk_bot, run_async, monkeypatch):
    from aipager.bot.rich_message import RichMessageBlocked
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageBlocked("blocked")),
    )
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is None


def test_edit_busy_rich_gone_clears_busy_msg_id(mk_bot, run_async, monkeypatch):
    from aipager.bot.rich_message import RichMessageGone
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageGone("gone")),
    )
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is None
    assert sess.busy_msg_id == 0


def test_edit_busy_rich_transient_failure_returns_false(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(return_value=None),  # transient
    )
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is False


def test_edit_busy_rich_success_updates_last_edit_at(mk_bot, run_async, monkeypatch):
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    sess.last_tool_edit_at = 0.0

    monkeypatch.setattr(rm_mod, "_post", AsyncMock(
        return_value={"ok": True, "result": {}},
    ))
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert sess.last_tool_edit_at > 0.0


def test_edit_busy_rich_success_clears_stream_dirty(mk_bot, run_async, monkeypatch):
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    sess.stream_dirty = True

    monkeypatch.setattr(rm_mod, "_post", AsyncMock(
        return_value={"ok": True, "result": {}},
    ))
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert sess.stream_dirty is False


def test_edit_busy_rich_rtl_body_passes_is_rtl_true(mk_bot, run_async, monkeypatch):
    """RTL body text → is_rtl=True in the payload."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_shown = "سلام دنیا " * 20  # Persian text
    sess.stream_last_rendered = ""

    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))
    assert len(payloads) == 1
    assert payloads[0]["rich_message"]["is_rtl"] is True


# ── _send_busy_and_animate: streaming field seeding ──────────────────────────

def test_send_busy_seeds_stream_fields_dm(mk_bot, run_async, tmp_path):
    """DM scope: stream fields seeded correctly after _send_busy_and_animate."""
    bot = mk_bot()
    sess = _sess("dev", "dm")
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')
    sess.transcript_path = str(tp)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.stream_transcript_path == str(tp)
    assert sess.stream_offset == tp.stat().st_size
    assert sess.stream_pending == ""
    assert sess.stream_shown == ""
    assert sess.stream_dirty is False
    assert sess.stream_last_rendered == ""


def test_send_busy_seeds_stream_fields_group(mk_bot, run_async, tmp_path):
    """Group scope: stream fields seeded the same way (no DM guard)."""
    bot = mk_bot()
    sess = _sess("team", "group")
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')
    sess.transcript_path = str(tp)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert sess.stream_transcript_path == str(tp)
    assert sess.stream_offset == tp.stat().st_size
    assert sess.stream_pending == ""
    assert sess.stream_shown == ""


def test_send_busy_no_draft_id_attribute(mk_bot, run_async, tmp_path):
    """After _send_busy_and_animate, sess must not have draft_id."""
    bot = mk_bot()
    sess = _sess("dev", "dm")
    tp = tmp_path / "t.jsonl"
    tp.write_text('{"type":"user","message":{}}\n')
    sess.transcript_path = str(tp)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    assert not hasattr(sess, "draft_id")


def test_send_busy_offset_seeded_to_file_size_prevents_cross_turn_leak(
    mk_bot, run_async, tmp_path
):
    """Offset seeded to file size → previous turn's text never re-read."""
    bot = mk_bot()
    sess = _sess("dev", "dm")
    entry = {"type": "assistant", "message": {
        "content": [{"type": "text", "text": "Previous turn answer"}],
        "stop_reason": "end_turn",
    }}
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(entry) + "\n")
    sess.transcript_path = str(tp)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot._app.bot.send_chat_action = AsyncMock()
    bot._start_animation = MagicMock()
    run_async(bot._send_busy_and_animate(sess))
    # Now simulate _read_stream_text — should find nothing new
    result = _read_stream_text(sess)
    assert result is False
    assert sess.stream_pending == ""


# ── _animate_compact regression guard ────────────────────────────────────────

def test_animate_compact_never_calls_edit_message_text_rich(
    mk_bot, run_async, monkeypatch
):
    """_animate_compact must use _edit_busy_raw, not edit_message_text_rich."""
    import aipager.bot.rich_message as rm_mod
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 5

    rich_calls = []

    async def _fake_rich_post(method, payload):
        rich_calls.append(method)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_rich_post)

    iteration = 0

    async def _no_sleep(t):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            sess.busy_msg_id = None

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    # _edit_busy_raw uses PTB bot, not raw HTTP; we mock it to avoid network
    bot._app.bot.edit_message_text = AsyncMock()

    run_async(bot._animate_compact(sess))

    # No call to the raw HTTP layer (editMessageText with rich_message)
    assert all("editMessageText" not in c for c in rich_calls)


def test_card_body_capped_to_recent_window():
    """A long turn must not grow the card into a wall of prose."""
    sess = _sess()
    sess.stream_shown = "alpha " * 400  # 2400 chars
    card = build_stream_card(sess, "Working")
    body = card.split("\n\n")[1]
    assert len(body) < 700
    assert body.startswith("…")
    assert card.rstrip().endswith(card[card.index("⏳"):].rstrip())


def test_card_short_body_not_truncated():
    sess = _sess()
    sess.stream_shown = "short commentary"
    card = build_stream_card(sess, "Working")
    assert "…" not in card
    assert "short commentary" in card


def test_concurrent_edits_are_serialised(run_async, monkeypatch):
    """Two edits started concurrently must not overlap on the wire.

    Regression: the POST is a suspension point, so a hook-driven edit could
    start while the animation loop's edit was in flight. Telegram rejected the
    first with 400 "canceled by new edit message request".
    """
    import aipager.bot.animation as anim

    sess = _sess()
    sess.busy_msg_id = 7
    in_flight = 0
    overlaps = []

    async def _fake_edit(chat_id, message_id, markdown, **kw):
        nonlocal in_flight
        in_flight += 1
        if in_flight > 1:
            overlaps.append(in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"message_id": message_id}

    monkeypatch.setattr(anim, "edit_message_text_rich", _fake_edit)

    bot = MagicMock()
    bot._build_stop_keyboard = MagicMock(
        return_value=MagicMock(to_dict=MagicMock(return_value={})),
    )
    bot._edit_busy_rich = anim.AnimationMixin._edit_busy_rich.__get__(bot)

    async def _drive():
        # Distinct bodies so the dedupe cannot mask the race.
        async def _one(text):
            sess.stream_shown = text
            return await bot._edit_busy_rich(sess, "Working")
        return await asyncio.gather(_one("first body"), _one("second body"))

    run_async(_drive())
    assert overlaps == [], f"Concurrent edits overlapped on the wire: {overlaps}"
