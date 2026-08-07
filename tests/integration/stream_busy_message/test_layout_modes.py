"""Black-box tests for the three /settings `layout` modes on a finished turn.

Exercised only via `bot.notify(sess, "idle_prompt", context)`, per
entrypoints.md — the HTTP layer is mocked at `rich_message._post` (same
convention as test_transport_contracts.py); PTB's own `send_message` /
`delete_message` are mocked on the bot's `_app.bot`.

Message-count contract (per design.md's stage 4 success criteria, which
match today's actual v0.5.0 `card` / `KEEP_FINISHED_CARD=0` behaviour —
`replace` sends a header message THEN the answer message, i.e. TWO
outbound sends, not one; design.md's own stage-4 success criteria say
"falls back to two-message `replace` behaviour" for the identical code
path. entrypoints.md's "1 outbound message" phrasing for plain `replace`
appears to be a documentation error — flagged in implementation.md):

- `card`:   1 edit (finished card) + 1 new message (answer)  = 2 sends
- `replace`: 1 new message (header) + 1 new message (answer) = 2 sends
- `merged` (success): 1 edit (card + separator + answer)     = 1 send
- `merged` (fallback, either reason): same as `replace`      = 2 sends
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences as prefs
from aipager.state import Status, TrackedSession


def _sess(label="dev", *, scope_chat_id=555):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.busy_msg_id = 42
    s.busy_started_at = time.monotonic() - 5
    s.scope_chat_id = scope_chat_id
    return s


@pytest.fixture
def rich_calls(monkeypatch):
    """Capture every raw HTTP call (method, payload) made through
    rich_message._post — the single transport for editMessageText and
    sendRichMessage alike."""
    calls = []

    async def _fake_post(method, payload):
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


# ---- card ------------------------------------------------------------------

def test_card_layout_edits_card_then_sends_answer(mk_bot, run_async, rich_calls):
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    methods = [m for m, _p in rich_calls]
    assert methods == ["editMessageText", "sendRichMessage"]
    bot._app.bot.send_message.assert_not_awaited()  # header skipped — card carries it
    bot._app.bot.delete_message.assert_not_awaited()
    assert sess.busy_msg_id is None


# ---- replace -----------------------------------------------------------------

def test_replace_layout_deletes_card_sends_header_and_answer(mk_bot, run_async, rich_calls):
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "replace")
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    bot._app.bot.delete_message.assert_awaited_once()
    bot._app.bot.send_message.assert_awaited_once()  # header
    methods = [m for m, _p in rich_calls]
    assert methods == ["sendRichMessage"]  # answer body
    assert sess.busy_msg_id is None


def test_no_stored_pref_with_keep_finished_card_off_matches_replace(
    mk_bot, run_async, rich_calls, monkeypatch,
):
    monkeypatch.setattr("aipager.preferences.KEEP_FINISHED_CARD", False)
    bot = _wire_bot(mk_bot)
    sess = _sess(scope_chat_id=777)  # untouched scope — no stored preference
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))
    bot._app.bot.delete_message.assert_awaited_once()
    bot._app.bot.send_message.assert_awaited_once()


# ---- merged: success ----------------------------------------------------

def test_merged_layout_delivers_whole_turn_in_one_edit(mk_bot, run_async, rich_calls):
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    assert [m for m, _p in rich_calls] == ["editMessageText"]
    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.delete_message.assert_not_awaited()
    assert sess.busy_msg_id is None

    payload = rich_calls[0][1]
    markdown = payload["rich_message"]["markdown"]
    assert "the answer" in markdown
    assert "Done" in markdown  # the card's own settled header, not a second one


def test_merged_layout_empty_answer_still_one_edit(mk_bot, run_async, rich_calls):
    """No answer text — still exactly one message: the finished card alone."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    run_async(bot.notify(sess, "idle_prompt", {"summary": "", "no_response": True}))
    assert [m for m, _p in rich_calls] == ["editMessageText"]
    bot._app.bot.send_message.assert_not_awaited()


def test_merged_layout_rtl_detection_uses_combined_text(mk_bot, run_async, rich_calls):
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    run_async(bot.notify(sess, "idle_prompt", {"summary": "سلام دنیا " * 10}))
    payload = rich_calls[0][1]
    assert payload["rich_message"]["is_rtl"] is True


# ---- merged: size fallback -----------------------------------------------

def test_merged_layout_size_fallback_delivers_full_answer_via_replace(
    mk_bot, run_async, rich_calls,
):
    """Combined card+answer over the byte ceiling → no edit is attempted at
    all; falls back to the replace-style send. The FULL answer must still
    reach the user (via the existing .txt-attachment overflow path)."""
    bot = _wire_bot(mk_bot)
    captured_doc = {}

    async def _capture_document(_chat_id, *, document, filename, reply_to_message_id=None):
        captured_doc["bytes"] = document.read()  # read while the file is still open
        captured_doc["filename"] = filename
    bot._app.bot.send_document = AsyncMock(side_effect=_capture_document)

    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    big_answer = "x" * 40_000
    run_async(bot.notify(sess, "idle_prompt", {"summary": big_answer}))

    # No edit attempted at all — the ceiling check happens before any call.
    assert "editMessageText" not in [m for m, _p in rich_calls]
    bot._app.bot.delete_message.assert_awaited_once()
    bot._app.bot.send_message.assert_awaited_once()  # header (overflow note)
    assert "sendRichMessage" in [m for m, _p in rich_calls]
    # The untruncated answer went out as a .txt attachment.
    assert captured_doc["bytes"].decode("utf-8") == big_answer
    assert sess.busy_msg_id is None


# ---- merged: edit-failure fallback ----------------------------------------

def test_merged_layout_edit_failure_still_delivers_the_answer(
    mk_bot, run_async, monkeypatch,
):
    """editMessageText_rich fails (any reason) → falls back to the
    replace-style send so the answer is never silently dropped."""
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")

    calls = []

    async def _fake_post(method, payload):
        calls.append((method, payload))
        if method == "editMessageText":
            return {"ok": False, "error_code": 400, "description": "boom"}
        return {"ok": True, "result": {"message_id": 5}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    methods = [m for m, _p in calls]
    assert "editMessageText" in methods  # the merge WAS attempted
    assert "sendRichMessage" in methods  # and the answer still went out
    body_payload = next(p for m, p in calls if m == "sendRichMessage")
    assert "the answer" in body_payload["rich_message"]["markdown"]
    bot._app.bot.delete_message.assert_awaited_once()
    bot._app.bot.send_message.assert_awaited_once()  # header
    assert sess.busy_msg_id is None


def test_merged_layout_message_gone_falls_back_and_clears_busy_msg_id(
    mk_bot, run_async, monkeypatch,
):
    bot = _wire_bot(mk_bot)
    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")

    async def _fake_post(method, payload):
        if method == "editMessageText":
            return {
                "ok": False, "error_code": 400,
                "description": "Bad Request: message to edit not found",
            }
        return {"ok": True, "result": {"message_id": 5}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    bot._app.bot.send_message.assert_awaited_once()  # answer still delivered
    assert sess.busy_msg_id is None
