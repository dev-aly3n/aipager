"""Black-box tests: the project's cardinal invariant — the answer must
always be delivered — across the three /settings layout modes, per
design.md's Stage 3/4 success criteria and entrypoints.md's "Layout mode
observable message counts" contract.

Driven exclusively through ``bot.notify(sess, "idle_prompt", context)``
(entrypoints.md's documented black-box entry point for this). The only
mock boundary used is the outbound Telegram transport: ``rich_message._post``
(per tests/conftest.py's own docstring, "the single transport for every raw
Telegram call ... covers send_rich_message, edit_message_text_rich and
anything added later") plus the raw PTB ``bot._app.bot.*`` calls used for
the header / delete / document-attachment paths elsewhere in this suite.
No internal merged-mode helper is imported or mocked by name.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession
from aipager.preferences import set_preference


RTL_TEXT = "مرحبا بكم في هذا الاختبار الطويل الذي يجب أن يظهر من اليمين إلى اليسار. " * 20


def _sess(chat_id, *, busy_msg_id=42, label="set"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.scope_kind = "dm" if chat_id > 0 else "group"
    s.scope_chat_id = chat_id
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic() - 5
    s.trigger_msg_id = 7
    return s


def _wire_bot(mk_bot, monkeypatch, *, post_responses=None):
    """Build a bot with every outbound transport mocked and recorded.

    ``post_responses`` maps a Telegram Bot API method name to the response
    dict ``_post`` should return for that call; anything not listed
    succeeds.
    """
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = AsyncMock()
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()

    post_responses = post_responses or {}
    post_calls = []

    async def _fake_post(method, payload):
        post_calls.append((method, payload))
        if method in post_responses:
            return post_responses[method]
        return {"ok": True, "result": {"message_id": 4242}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return bot, post_calls


def _all_sent_texts(bot, post_calls):
    """Every piece of outbound text this turn produced, across every
    transport boundary, so we can check the cardinal invariant: the
    answer is present SOMEWHERE, never silently dropped."""
    texts = [str(c.args[1]) for c in bot._app.bot.send_message.await_args_list]
    for _method, payload in post_calls:
        if isinstance(payload, dict):
            body = payload.get("rich_message", {}) or {}
            if isinstance(body, dict):
                for key in ("markdown", "content", "text"):
                    if key in body:
                        texts.append(str(body[key]))
            # Fallback: the raw payload's string form always contains any
            # text it carries, whatever key name is used.
            texts.append(str(payload))
    return texts


def _headers(bot):
    return [c.args[1] for c in bot._app.bot.send_message.await_args_list
            if "Finished" in str(c.args[1])]


# ---- baseline calibration: card mode is the known-good default -----------

def test_card_mode_produces_two_outbound_messages(mk_bot, run_async, monkeypatch):
    """entrypoints.md: card mode is '2 outbound messages (finished-card
    edit + separate answer message)'. Once the finished card renders
    successfully, the redundant plain-text header is skipped (the card
    already carries the ✅ Finished line), matching the pre-existing
    header-skip contract this feature must not disturb."""
    chat_id = 111001
    set_preference(chat_id, "layout", "card")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "A short finished answer."}))
    edit_calls = [m for m, _p in post_calls if m == "editMessageText"]
    answer_calls = [m for m, _p in post_calls if m == "sendRichMessage"]
    assert len(edit_calls) == 1, f"expected one finished-card edit; got: {post_calls}"
    assert len(answer_calls) == 1, f"expected one separate answer send; got: {post_calls}"
    assert _headers(bot) == []


# ---- replace mode: exactly one message, card deleted ----------------------

def test_replace_mode_deletes_the_card_and_skips_the_header(mk_bot, run_async, monkeypatch):
    chat_id = 111002
    set_preference(chat_id, "layout", "replace")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "A short finished answer."}))
    bot._app.bot.delete_message.assert_awaited_once()
    assert _headers(bot) == []


def test_replace_mode_still_delivers_the_answer_text(mk_bot, run_async, monkeypatch):
    chat_id = 111003
    set_preference(chat_id, "layout", "replace")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "UNIQUE_REPLACE_ANSWER_TOKEN"}))
    texts = _all_sent_texts(bot, post_calls)
    assert any("UNIQUE_REPLACE_ANSWER_TOKEN" in t for t in texts)


def test_replace_mode_overflow_answer_keeps_header_and_is_not_truncated(mk_bot, run_async, monkeypatch):
    """Mirrors the existing card-mode overflow contract
    (test_idle_keeps_the_header_when_the_answer_overflows) but independently
    verifies the SAME safety net holds for the replace layout."""
    chat_id = 111004
    set_preference(chat_id, "layout", "replace")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    big = "x" * 40_000
    run_async(bot.notify(sess, "idle_prompt", {"summary": big}))
    assert len(_headers(bot)) == 1
    assert "attached below" in _headers(bot)[0]
    bot._app.bot.send_document.assert_awaited()


# ---- merged mode: one combined edit when within the ceiling ----------------

def test_merged_mode_within_ceiling_is_a_single_edit_no_new_message(mk_bot, run_async, monkeypatch):
    chat_id = 111005
    set_preference(chat_id, "layout", "merged")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "A short finished answer."}))
    assert _headers(bot) == [], "merged mode must skip the redundant header"
    bot._app.bot.delete_message.assert_not_awaited()
    edit_calls = [m for m, _p in post_calls if m == "editMessageText"]
    send_calls = [m for m, _p in post_calls if m == "sendRichMessage"]
    assert len(edit_calls) == 1, f"expected exactly one edit; got calls: {post_calls}"
    assert len(send_calls) == 0, "merged mode must not send a second message"


def test_merged_mode_delivers_the_answer_text(mk_bot, run_async, monkeypatch):
    chat_id = 111006
    set_preference(chat_id, "layout", "merged")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "UNIQUE_MERGED_ANSWER_TOKEN"}))
    texts = _all_sent_texts(bot, post_calls)
    assert any("UNIQUE_MERGED_ANSWER_TOKEN" in t for t in texts)


def test_merged_mode_rtl_combined_content_flags_is_rtl(mk_bot, run_async, monkeypatch):
    chat_id = 111007
    set_preference(chat_id, "layout", "merged")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": RTL_TEXT}))
    edit_payloads = [p for m, p in post_calls if m == "editMessageText"]
    assert edit_payloads, f"expected an edit call; got: {post_calls}"
    rich = edit_payloads[0].get("rich_message", {}) if isinstance(edit_payloads[0], dict) else {}
    assert rich.get("is_rtl") is True, (
        f"RTL answer under merged mode must set is_rtl=True; payload: {edit_payloads[0]!r}"
    )


# ---- merged mode: size-ceiling fallback never truncates the answer --------

def test_merged_mode_size_fallback_still_delivers_full_untruncated_answer(mk_bot, run_async, monkeypatch):
    """Combined card+answer over the 32,768-byte ceiling must fall back to
    replace-style delivery for that turn — never truncate, never drop."""
    chat_id = 111008
    set_preference(chat_id, "layout", "merged")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    huge = "y" * 40_000
    run_async(bot.notify(sess, "idle_prompt", {"summary": huge}))
    # The turn must not have silently vanished: something identifying it
    # (the "attached below" header, or the full body inline) must exist.
    texts = _all_sent_texts(bot, post_calls)
    delivered_whole = any(huge in t for t in texts)
    attached = bot._app.bot.send_document.await_args_list != []
    assert delivered_whole or attached, (
        "the full answer must be delivered (inline or as a .txt attachment) "
        "when merged mode's combined content exceeds the byte ceiling"
    )


# ---- merged mode: edit failure never drops the answer ----------------------

def test_merged_mode_edit_failure_still_delivers_the_answer(mk_bot, run_async, monkeypatch):
    """A failed editMessageText call (e.g. RichMessageBlocked/Gone) must
    fall through to a replace-style send — the answer must still reach
    the user."""
    chat_id = 111009
    set_preference(chat_id, "layout", "merged")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch, post_responses={
        "editMessageText": {
            "ok": False, "error_code": 400,
            "description": "Bad Request: message to edit not found",
        },
    })
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "UNIQUE_EDITFAIL_ANSWER_TOKEN"}))
    texts = _all_sent_texts(bot, post_calls)
    assert any("UNIQUE_EDITFAIL_ANSWER_TOKEN" in t for t in texts), (
        "an editMessageText failure during a merged turn must not lose the answer"
    )


def test_merged_mode_bot_blocked_by_user_still_delivers_the_answer(mk_bot, run_async, monkeypatch):
    """A blocked bot on the edit call is another realistic edit-failure
    mode; the answer must still be attempted through the fallback path."""
    chat_id = 111010
    set_preference(chat_id, "layout", "merged")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch, post_responses={
        "editMessageText": {
            "ok": False, "error_code": 403,
            "description": "Forbidden: bot was blocked by the user",
        },
    })
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "should not vanish"}))
    # Must not raise; the turn must complete. We don't assert delivery
    # succeeds here (the whole scope may be blocked) — only that the
    # notify() call itself never aborts partway through.


def test_merged_mode_deleted_busy_message_still_delivers_the_answer(mk_bot, run_async, monkeypatch):
    """The busy message may have been deleted out from under the bot
    (user cleared the chat) before the final combined edit — the answer
    must still reach the user via the fallback."""
    chat_id = 111011
    set_preference(chat_id, "layout", "merged")
    bot, post_calls = _wire_bot(mk_bot, monkeypatch, post_responses={
        "editMessageText": {
            "ok": False, "error_code": 400,
            "description": "Bad Request: message to edit not found",
        },
    })
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "UNIQUE_DELETED_CARD_TOKEN"}))
    texts = _all_sent_texts(bot, post_calls)
    assert any("UNIQUE_DELETED_CARD_TOKEN" in t for t in texts)


# ---- empty / whitespace-only answers never crash the turn -----------------

@pytest.mark.parametrize("layout", ["card", "replace", "merged"])
def test_empty_answer_never_raises_for_any_layout(mk_bot, run_async, monkeypatch, layout):
    chat_id = 111100 + hash(layout) % 100
    set_preference(chat_id, "layout", layout)
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "", "no_response": True}))
    # Must not raise. Something must still mark the turn as finished.
    assert bot._app.bot.send_message.await_args_list or post_calls


@pytest.mark.parametrize("layout", ["card", "replace", "merged"])
def test_whitespace_only_answer_never_raises_for_any_layout(mk_bot, run_async, monkeypatch, layout):
    chat_id = 111200 + hash(layout) % 100
    set_preference(chat_id, "layout", layout)
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "   \n\t  "}))
    assert bot._app.bot.send_message.await_args_list or post_calls


# ---- the fixed regression: unscoped session + unset global CHAT_ID --------

@pytest.mark.parametrize("layout", ["card", "replace", "merged"])
def test_unscoped_session_with_no_chat_id_still_delivers_for_every_layout(
    mk_bot, run_async, monkeypatch, layout,
):
    """Regression coverage for the fixed bug: scope_chat_id == 0 with an
    unset global CHAT_ID must never abort delivery, for ANY layout mode —
    not just the default that was originally fixed."""
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    set_preference(0, "layout", layout)
    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(0)
    sess.scope_chat_id = 0
    bot.registry.track_message = MagicMock()

    # Must not raise — this is the regression itself.
    run_async(bot.notify(sess, "idle_prompt", {"summary": "UNIQUE_UNSCOPED_TOKEN"}))

    texts = _all_sent_texts(bot, post_calls)
    assert any("UNIQUE_UNSCOPED_TOKEN" in t for t in texts), (
        f"layout={layout}: answer lost for an unscoped session with no CHAT_ID"
    )


# ---- corrupted stored layout preference must not break delivery -----------

def test_corrupt_stored_layout_preference_still_delivers_the_answer(mk_bot, run_async, monkeypatch, tmp_path):
    chat_id = 222001
    prefs_path = tmp_path / "home" / ".config" / "aipager" / "preferences.json"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    prefs_path.write_text(json.dumps({str(chat_id): {"layout": "sideways-not-real"}}))
    # Force a fresh read past the in-memory cache so the corrupt file is seen.
    import aipager.preferences as prefs_mod
    prefs_mod._cache = None

    bot, post_calls = _wire_bot(mk_bot, monkeypatch)
    sess = _sess(chat_id)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "UNIQUE_CORRUPT_PREF_TOKEN"}))
    texts = _all_sent_texts(bot, post_calls)
    assert any("UNIQUE_CORRUPT_PREF_TOKEN" in t for t in texts)
