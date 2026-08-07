"""Integration tests: transport error contract and observable edit behaviour.

Covers the contract from entrypoints.md:
  - 400 'message is not modified' → logged at DEBUG, not WARNING; returns None; does not raise
  - RichMessageBlocked stops animation loop, does not propagate
  - RichMessageGone clears busy_msg_id and stops loop
  - Transient failure (None) does NOT clear busy_msg_id, does not raise, loop retries
  - Every edit call includes a reply_markup carrying the Stop button
  - Two consecutive renders with identical content produce ONE HTTP call, not two
  - An RTL body results in is_rtl=True being passed to the transport
  - Group scopes behave the same as DMs for the Stop button
  - send_rich_message_draft does not exist in the module (removed surface)
  - _push_draft does not exist on AnimationMixin (removed surface)
  - send_rich_message, detect_rtl, close_client keep their signatures (regression)

These are BLACK-BOX tests.  HTTP layer is mocked at _post / edit_message_text_rich.
"""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock

from aipager.state import Status, TrackedSession
from aipager.bot.animation import build_stream_card
from aipager.bot.rich_message import (
    RichMessageBlocked,
    RichMessageGone,
    edit_message_text_rich,
    send_rich_message,
    detect_rtl,
    close_client,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sess(label="dev", scope_kind="dm"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = scope_kind
    s.scope_chat_id = 12345 if scope_kind == "dm" else -100999888777
    s.busy_msg_id = 55
    s.busy_started_at = time.monotonic() - 10
    s.stream_last_rendered = ""
    return s


# ── SC-TRANS-1: 400 'message is not modified' → DEBUG log, not WARNING ────────

def test_400_not_modified_logged_at_debug_not_warning(run_async, monkeypatch, caplog):
    """entrypoints.md: 400 'message is not modified' must be logged at DEBUG,
    NOT at WARNING level."""
    import aipager.bot.rich_message as rm_mod

    async def _fake_post(method, payload):
        return {
            "ok": False,
            "error_code": 400,
            "description": "Bad Request: message is not modified",
        }

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")

    with caplog.at_level(logging.DEBUG, logger="aipager"):
        run_async(edit_message_text_rich(1, 2, "same content"))

    warning_records = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "not modified" in r.message.lower()
    ]
    assert len(warning_records) == 0, (
        f"'message is not modified' was logged at WARNING level: "
        f"{[r.message for r in warning_records]}"
    )


# ── SC-TRANS-2: RichMessageBlocked stops loop, does not propagate ─────────────

def test_rich_message_blocked_does_not_propagate_from_edit_busy_rich(mk_bot, run_async, monkeypatch):
    """entrypoints.md: RichMessageBlocked from the transport stops the animation
    loop and does not propagate out of the loop.  _edit_busy_rich must return None
    (not raise)."""
    bot = mk_bot()
    sess = _sess()

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageBlocked("blocked")),
    )

    # Must not raise — RichMessageBlocked is caught inside _edit_busy_rich
    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is None, "RichMessageBlocked must return None from _edit_busy_rich"


# ── SC-TRANS-3: RichMessageGone clears busy_msg_id ───────────────────────────

def test_rich_message_gone_clears_busy_msg_id(mk_bot, run_async, monkeypatch):
    """entrypoints.md: RichMessageGone clears busy_msg_id and stops the loop."""
    bot = mk_bot()
    sess = _sess()
    assert sess.busy_msg_id == 55

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageGone("gone")),
    )

    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is None, "RichMessageGone must return None from _edit_busy_rich"
    assert sess.busy_msg_id == 0, (
        f"busy_msg_id should be 0 after RichMessageGone; got {sess.busy_msg_id}"
    )


# ── SC-TRANS-4: Transient failure (None) does NOT clear busy_msg_id ──────────

def test_transient_failure_does_not_clear_busy_msg_id(mk_bot, run_async, monkeypatch):
    """entrypoints.md: A transient failure (transport returned None) does NOT clear
    busy_msg_id, does not raise, and the loop tries again on the next tick."""
    bot = mk_bot()
    sess = _sess()
    assert sess.busy_msg_id == 55

    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(return_value=None),
    )

    result = run_async(bot._edit_busy_rich(sess, "Working"))
    assert result is False, "Transient failure must return False from _edit_busy_rich"
    assert sess.busy_msg_id == 55, (
        f"busy_msg_id must NOT be cleared on transient failure; got {sess.busy_msg_id}"
    )


# ── SC-TRANS-5: Stop button on every edit (DM scope) ─────────────────────────

def test_every_edit_carries_stop_button_dm(mk_bot, run_async, monkeypatch):
    """entrypoints.md: Every edit call includes a reply_markup carrying the Stop
    button.  Tested for DM scope."""
    import aipager.bot.rich_message as rm_mod

    bot = mk_bot()
    sess = _sess("dev", "dm")
    sess.stream_last_rendered = ""

    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))

    assert len(payloads) == 1
    assert "reply_markup" in payloads[0], (
        "reply_markup (Stop button) is missing from the edit payload (DM scope)"
    )


# ── SC-TRANS-6: Stop button on every edit (group scope) ──────────────────────

def test_every_edit_carries_stop_button_group(mk_bot, run_async, monkeypatch):
    """entrypoints.md: Group scopes behave the same as DMs — Stop button rides
    on every edit."""
    import aipager.bot.rich_message as rm_mod

    bot = mk_bot()
    sess = _sess("team", "group")
    sess.stream_last_rendered = ""

    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))

    assert len(payloads) == 1
    assert "reply_markup" in payloads[0], (
        "reply_markup (Stop button) is missing from the edit payload (group scope)"
    )


# ── SC-TRANS-7: Identical consecutive renders → one HTTP call, not two ───────

def test_identical_consecutive_renders_produce_one_http_call(mk_bot, run_async, monkeypatch):
    """entrypoints.md: Two consecutive renders with identical content produce ONE
    HTTP call, not two — the second is skipped before the POST."""
    import aipager.bot.rich_message as rm_mod

    bot = mk_bot()
    sess = _sess()
    # Force stream_last_rendered to match what build_stream_card will produce
    # on the FIRST call — so the second call finds it already cached.
    card = build_stream_card(sess, "Working")
    sess.stream_last_rendered = card  # pre-populate cache

    http_calls = []

    async def _fake_post(method, payload):
        http_calls.append(method)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)

    # Second render with identical content — must be skipped
    run_async(bot._edit_busy_rich(sess, "Working"))

    assert len(http_calls) == 0, (
        f"Expected 0 HTTP calls for identical content; got {len(http_calls)}"
    )


# ── SC-TRANS-8: RTL body → is_rtl=True in payload ───────────────────────────

def test_rtl_body_sends_is_rtl_true(mk_bot, run_async, monkeypatch):
    """entrypoints.md: An RTL body results in is_rtl=True being passed to the
    transport."""
    import aipager.bot.rich_message as rm_mod

    bot = mk_bot()
    sess = _sess()
    sess.stream_commentary = [(0, "سلام دنیا " * 10)]  # Persian text (RTL)
    sess.stream_last_rendered = ""

    payloads = []

    async def _fake_post(method, payload):
        payloads.append(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    run_async(bot._edit_busy_rich(sess, "Working"))

    assert len(payloads) == 1
    assert payloads[0].get("rich_message", {}).get("is_rtl") is True, (
        "RTL body must produce is_rtl=True in the rich_message payload"
    )


# ── SC-TRANS-9: send_rich_message_draft is removed ──────────────────────────

def test_send_rich_message_draft_not_importable():
    """entrypoints.md 'Removed surface': send_rich_message_draft must no longer
    exist in aipager.bot.rich_message."""
    import aipager.bot.rich_message as rm_mod
    assert not hasattr(rm_mod, "send_rich_message_draft"), (
        "send_rich_message_draft still exists — the draft path was not removed"
    )


# ── SC-TRANS-10: _push_draft is removed from AnimationMixin ─────────────────

def test_push_draft_not_on_animation_mixin():
    """entrypoints.md 'Removed surface': _push_draft must no longer exist on
    AnimationMixin."""
    from aipager.bot.animation import AnimationMixin
    assert not hasattr(AnimationMixin, "_push_draft"), (
        "_push_draft still exists on AnimationMixin — was not removed"
    )


# ── SC-TRANS-11: send_rich_message still callable (regression guard) ─────────

def test_send_rich_message_still_callable():
    """entrypoints.md 'Unchanged surface': send_rich_message must keep its
    signature and be importable."""
    assert callable(send_rich_message)


# ── SC-TRANS-12: detect_rtl still callable (regression guard) ────────────────

def test_detect_rtl_still_callable():
    """entrypoints.md 'Unchanged surface': detect_rtl must keep its signature."""
    assert callable(detect_rtl)
    # Basic sanity: detect_rtl correctly identifies Arabic as RTL
    assert detect_rtl("مرحبا") is True
    assert detect_rtl("hello") is False


# ── SC-TRANS-13: close_client still callable (regression guard) ──────────────

def test_close_client_still_callable():
    """entrypoints.md 'Unchanged surface': close_client must keep its signature."""
    assert callable(close_client)


# ── SC-TRANS-14: _animate_compact never calls edit_message_text_rich ─────────

def test_animate_compact_does_not_call_edit_message_text_rich(mk_bot, run_async, monkeypatch):
    """entrypoints.md '_animate_compact still edits through _edit_busy_raw;
    running it must produce NO calls to edit_message_text_rich'."""
    import aipager.bot.rich_message as rm_mod

    bot = mk_bot()
    sess = TrackedSession(name="claude-compact", label="compact", status=Status.BUSY)
    sess.busy_msg_id = 10

    rich_edit_calls = []

    async def _fake_post(method, payload):
        # This _post is used by edit_message_text_rich (the rich path)
        rich_edit_calls.append(method)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)

    iteration = [0]

    async def _no_sleep(t):
        iteration[0] += 1
        if iteration[0] >= 2:
            sess.busy_msg_id = None  # stop the loop

    monkeypatch.setattr("aipager.bot.animation.asyncio.sleep", _no_sleep)
    # _edit_busy_raw uses the PTB bot.edit_message_text, not our _post mock
    bot._app.bot.edit_message_text = AsyncMock()

    run_async(bot._animate_compact(sess))

    # No calls to the raw HTTP layer (editMessageText with rich_message)
    assert all("editMessageText" not in c for c in rich_edit_calls), (
        "_animate_compact called edit_message_text_rich — it must only use _edit_busy_raw"
    )
