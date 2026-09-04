"""design.md success criteria / research.md gotcha: `_send_merged_final`
has NO functional changes from this feature — it still calls
`build_stream_card_ex`, still checks the combined byte ceiling BEFORE any
network call, and still returns `False` on any failure, triggering the
caller's existing delete-the-card-and-fall-through-to-answer-only-send
behavior (notify.py:1436-1460). This regression check confirms that
still holds now that `card_md` may carry a `<details>` block (larger
than before for the same underlying content) — mirrors
`tests/integration/stream_busy_message/test_layout_modes.py`'s own
`test_merged_layout_size_fallback_delivers_full_answer_via_replace`,
narrowed to this feature's own directory per design.md's file-by-file
plan.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager import preferences as prefs
from aipager.state import Status, TrackedSession


def _sess(label="jim", *, scope_chat_id=555):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.busy_started_at = time.monotonic() - 30
    sess.scope_chat_id = scope_chat_id
    return sess


def _wire_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.delete_message = AsyncMock()
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


def test_merged_layout_still_deletes_the_card_when_the_combined_text_overflows(
    mk_bot, run_async, monkeypatch,
):
    """Combined card+answer over the byte ceiling → no edit attempted at
    all; falls back to delete-the-card-then-send-the-answer, exactly as
    before this feature. A big tool_history (forcing a <details> block,
    now part of card_md) plus a big answer together blow the ceiling."""
    bot = _wire_bot(mk_bot)
    rich_calls = []

    async def _fake_post(method, payload):
        rich_calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 999}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)

    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    sess.tool_history = [(f"Bash: step-{i} " + "z" * 300, True) for i in range(500)]
    big_answer = "x" * 40_000
    run_async(bot.notify(sess, "idle_prompt", {"summary": big_answer}))

    # No edit attempted at all — the ceiling check happens before any call.
    assert "editMessageText" not in [m for m, _p in rich_calls]
    bot._app.bot.delete_message.assert_awaited_once()
    assert "sendRichMessage" in [m for m, _p in rich_calls]
    assert sess.busy_msg_id is None


def test_merged_layout_edit_failure_still_falls_back_and_delivers_the_answer(
    mk_bot, run_async, monkeypatch,
):
    """editMessageText_rich failing (any reason) still degrades to the
    replace-style send, unchanged, even with a <details>-bearing card."""
    bot = _wire_bot(mk_bot)
    calls = []

    async def _fake_post(method, payload):
        calls.append((method, payload))
        if method == "editMessageText":
            return {"ok": False, "error_code": 400, "description": "boom"}
        return {"ok": True, "result": {"message_id": 5}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)

    sess = _sess()
    prefs.set_preference(sess.scope_chat_id, "layout", "merged")
    sess.tool_history = [(f"Bash: t-{i} " + "x" * 200, True) for i in range(60)]
    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    methods = [m for m, _p in calls]
    assert "editMessageText" in methods  # the merge WAS attempted
    assert "sendRichMessage" in methods  # and the answer still went out
    body_payload = next(p for m, p in calls if m == "sendRichMessage")
    assert "the answer" in body_payload["rich_message"]["markdown"]
    bot._app.bot.delete_message.assert_awaited_once()
    assert sess.busy_msg_id is None
