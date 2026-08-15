"""Regression test: reply routing after a successful `merged`-layout edit.

`_send_merged_final` (notify.py) delivers a finished turn by EDITING the
existing busy message in place — it never calls `registry.track_message`
itself. Reply routing for that message_id keeps working only because it
was already registered once, when the busy message was first sent
(animation.py's `send_busy` → `registry.track_message` call), and the
message_id never changes across the edit. This test pins that dependency
down so a future refactor that swaps the edited message_id (or drops the
original `track_message` call) fails loudly here instead of silently
breaking "reply to the busy message" for merged-layout users.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager.preferences import set_preference
from aipager.state import Status, TrackedSession


def _sess(chat_id, *, busy_msg_id=42, label="jim"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.scope_kind = "dm" if chat_id > 0 else "group"
    s.scope_chat_id = chat_id
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic() - 5
    s.trigger_msg_id = 7
    return s


def test_merged_edit_success_preserves_reply_routing_to_busy_msg_id(
    mk_bot, monkeypatch, run_async,
):
    chat_id = 4242
    set_preference(chat_id, "layout", "merged")
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._stop_animation = MagicMock()

    sess = _sess(chat_id, busy_msg_id=42)
    bot.registry._sessions[sess.name] = sess
    # Mirrors animation.py's send_busy → track_message call: the busy
    # message is registered ONCE, before any edit happens.
    bot.registry.track_message(42, sess.name)

    async def _fake_post(method, payload):
        assert method == "editMessageText"
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "the final answer"}))

    # The merged edit delivered the turn in place — no new message sent.
    bot._app.bot.send_message.assert_not_awaited()
    # A reply to the (still-42) busy message must still resolve to this
    # session after the edit.
    resolved = bot.registry.get_session_by_msg(42)
    assert resolved is not None
    assert resolved.name == sess.name
