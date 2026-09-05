"""``send_busy``'s new ``reply_to``/``disable_notification`` kwargs and
``_reanchor_busy_card`` (design.md "turn anchor follows consumption").

The case-table black-box tests
(``tests/integration/turn-anchor-follows-consumption/``) exercise these
through the full notify()/handler surface; these are the Developer's
own narrower unit tests against the two new pieces directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def rich_calls(monkeypatch):
    """Capture every raw HTTP call (method, payload) made through
    ``rich_message._post`` (mirrors
    ``tests/integration/stream_busy_message/test_layout_modes.py``)."""
    calls = []

    async def _fake_post(method, payload):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 999}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return calls


def _sess(*, busy_msg_id=100, trigger_msg_id=1):
    s = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    s.scope_chat_id = 555  # avoid falling back to the real config.CHAT_ID
    s.busy_msg_id = busy_msg_id
    s.trigger_msg_id = trigger_msg_id
    return s


# ---- send_busy's new kwargs ----------------------------------------------

def test_send_busy_defaults_reply_to_trigger_msg_id(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=0, trigger_msg_id=7)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))

    msg_id = run_async(bot.send_busy(sess))

    assert msg_id == 42
    assert bot._app.bot.send_message.await_args.kwargs["reply_to_message_id"] == 7
    assert bot._app.bot.send_message.await_args.kwargs["disable_notification"] is False
    assert sess.busy_card_trigger == 7


def test_send_busy_explicit_reply_to_overrides_trigger_msg_id(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=0, trigger_msg_id=7)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))

    msg_id = run_async(bot.send_busy(sess, reply_to=99, disable_notification=True))

    assert msg_id == 42
    assert bot._app.bot.send_message.await_args.kwargs["reply_to_message_id"] == 99
    assert bot._app.bot.send_message.await_args.kwargs["disable_notification"] is True
    assert sess.busy_card_trigger == 99


def test_send_busy_failure_leaves_busy_card_trigger_untouched(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=0, trigger_msg_id=7)
    assert sess.busy_card_trigger is None
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("flooded"))

    assert run_async(bot.send_busy(sess, reply_to=99)) is None
    assert sess.busy_card_trigger is None


# ---- _reanchor_busy_card --------------------------------------------------

def _wire_reanchor_bot(mk_bot, rich_calls, new_msg_id=200):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=new_msg_id))
    bot._app.bot.delete_message = AsyncMock()
    bot.registry.track_message = MagicMock()
    return bot


def test_reanchor_busy_card_sends_new_before_deleting_old(mk_bot, run_async, rich_calls):
    bot = _wire_reanchor_bot(mk_bot, rich_calls)
    sess = _sess(busy_msg_id=100, trigger_msg_id=1)
    sess.busy_card_trigger = 1

    call_order = []
    orig_send = bot._app.bot.send_message
    orig_delete = bot._app.bot.delete_message

    async def _tracked_send(*a, **kw):
        call_order.append("send")
        return await orig_send(*a, **kw)

    async def _tracked_delete(*a, **kw):
        call_order.append("delete")
        return await orig_delete(*a, **kw)

    bot._app.bot.send_message = AsyncMock(side_effect=_tracked_send)
    bot._app.bot.delete_message = AsyncMock(side_effect=_tracked_delete)

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert call_order == ["send", "delete"], (
        "B8: send-then-delete, never the other order — a failed send must "
        "never leave the session with NO live card at all"
    )
    assert sess.busy_msg_id == 200
    assert sess.busy_card_trigger == 2
    bot._app.bot.delete_message.assert_awaited_once_with(chat_id=555, message_id=100)


def test_reanchor_busy_card_swallows_delete_failure(mk_bot, run_async, rich_calls):
    """B8: delete raises — the new card must still be live."""
    bot = _wire_reanchor_bot(mk_bot, rich_calls)
    bot._app.bot.delete_message = AsyncMock(side_effect=RuntimeError("gone"))
    sess = _sess(busy_msg_id=100, trigger_msg_id=1)
    sess.busy_card_trigger = 1

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert sess.busy_msg_id == 200
    bot._app.bot.delete_message.assert_awaited_once()


def test_reanchor_busy_card_noop_when_nothing_live(mk_bot, run_async, rich_calls):
    bot = _wire_reanchor_bot(mk_bot, rich_calls)
    sess = _sess(busy_msg_id=0, trigger_msg_id=1)

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.delete_message.assert_not_awaited()
    assert sess.busy_msg_id == 0


def test_reanchor_busy_card_send_failure_keeps_the_stale_card(mk_bot, run_async, rich_calls):
    bot = _wire_reanchor_bot(mk_bot, rich_calls)
    bot._app.bot.send_message = AsyncMock(return_value=None)
    sess = _sess(busy_msg_id=100, trigger_msg_id=1)
    sess.busy_card_trigger = 1

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert sess.busy_msg_id == 100, "keeps the stale card rather than losing it"
    bot._app.bot.delete_message.assert_not_awaited()


def test_reanchor_busy_card_final_renders_without_stop_button(mk_bot, run_async, rich_calls):
    bot = _wire_reanchor_bot(mk_bot, rich_calls)
    sess = _sess(busy_msg_id=100, trigger_msg_id=1)
    sess.busy_card_trigger = 1
    sess.status = Status.IDLE

    run_async(bot._reanchor_busy_card(sess, 2, final=True))

    edits = [p for m, p in rich_calls if m == "editMessageText"]
    assert edits, "the re-anchored card must still be rendered"
    assert edits[-1].get("reply_markup") is None


def test_reanchor_busy_card_serializes_via_animate_lock(mk_bot, run_async, rich_calls):
    """Two concurrent re-anchor attempts on the same session never
    interleave — the second waits for the first's lock to release."""
    bot = _wire_reanchor_bot(mk_bot, rich_calls, new_msg_id=201)
    sess = _sess(busy_msg_id=100, trigger_msg_id=1)
    sess.busy_card_trigger = 1

    gate = asyncio.Event()
    entered = asyncio.Event()
    calls = {"n": 0}

    async def _send_message(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            entered.set()
            await gate.wait()
        return MagicMock(message_id=200 + calls["n"])

    bot._app.bot.send_message = AsyncMock(side_effect=_send_message)

    async def scenario():
        first = asyncio.create_task(bot._reanchor_busy_card(sess, 2, final=False))
        await entered.wait()
        assert sess.animate_lock.locked()
        second = asyncio.create_task(bot._reanchor_busy_card(sess, 3, final=False))
        await asyncio.sleep(0)  # let `second` start and block on the lock
        assert not second.done()
        gate.set()
        await asyncio.gather(first, second)

    run_async(scenario())
    assert calls["n"] == 2
    assert sess.busy_msg_id == 202  # the SECOND call's send won last


# ---- R4: only remove/absorbed_mid_turn and remove/delivered_to_agent -----

def _write_queue_op(path, operation, reason, content):
    import json
    line = {"type": "queue-operation", "operation": operation, "content": content}
    if reason is not None:
        line["reason"] = reason
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _hook_live_sess(tmp_path):
    from aipager import policy_snapshot as ps
    s = TrackedSession(name="claude-r4", label="r4", status=Status.BUSY)
    p = tmp_path / "t.jsonl"
    p.write_bytes(b"")
    s.stream_transcript_path = str(p)
    s.stream_offset = 0
    s.stream_hook_live = True
    ps.write_note(
        s.name, None, None, None,
        msg_id=2, chat_id=555, sender_key=(1, 1),
        body="ignored text", raw_text="ignored text",
    )
    return s


@pytest.mark.parametrize("operation,reason", [
    ("enqueue", None),
    ("dequeue", None),
    ("remove", None),  # bare remove == discard, not absorption
    ("popAll", None),
    ("remove", "some_other_reason"),
])
def test_sync_anchors_ignores_every_non_absorption_queue_operation(
    operation, reason, tmp_path,
):
    from aipager.bot.animation import _sync_anchors_from_transcript
    from aipager import policy_snapshot as ps

    sess = _hook_live_sess(tmp_path)
    _write_queue_op(sess.stream_transcript_path, operation, reason, "ignored text")

    _sync_anchors_from_transcript(sess)

    assert sess.stream_consumed_notes == [], (
        f"operation={operation!r} reason={reason!r} must never consume a note"
    )
    assert len(ps.list_outstanding_notes(sess.name)) == 1, (
        "the note must still be outstanding — R4's filter must not touch it"
    )


@pytest.mark.parametrize("reason", ["absorbed_mid_turn", "delivered_to_agent"])
def test_sync_anchors_consumes_on_the_two_real_absorption_reasons(reason, tmp_path):
    from aipager.bot.animation import _sync_anchors_from_transcript
    from aipager import policy_snapshot as ps

    sess = _hook_live_sess(tmp_path)
    _write_queue_op(sess.stream_transcript_path, "remove", reason, "ignored text")

    _sync_anchors_from_transcript(sess)

    assert len(sess.stream_consumed_notes) == 1
    assert ps.list_outstanding_notes(sess.name) == []
