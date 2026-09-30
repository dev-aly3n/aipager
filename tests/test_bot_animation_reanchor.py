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

    async def _fake_post(method, payload, **_kw):
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


# ---- _reanchor_busy_card: the card's move ---------------------------------
#
# Operator, 2026-09-29: "when the copy went below, the old bussy card was
# still there for few second ... check and see if that one was complete
# copy". The move is ONE sendRichMessage carrying the complete card (Stop
# button included), then the old card's delete at once; both INSTANT.

INSTANT = {"class": "instant"}


@pytest.fixture
def rich_post(monkeypatch):
    """``rich_message._post`` answered like Telegram: a sendRichMessage
    comes back with id 200 and the keyboard it carried. ``state["send"]`` /
    ``state["edit"]`` override the answer; ``state["log"]`` is the shared
    order of rich calls and deletes."""
    state = {"calls": [], "send": None, "edit": None, "log": []}

    async def _fake_post(method, payload, **kw):
        state["calls"].append((method, payload, kw))
        state["log"].append(method)
        if method == "sendRichMessage":
            if state["send"] is not None:
                return await state["send"](payload)
            result = {"message_id": 200}
            if "reply_markup" in payload:
                result["reply_markup"] = payload["reply_markup"]
            return {"ok": True, "result": result}
        if state["edit"] is not None:
            return await state["edit"](payload)
        return {"ok": True, "result": {"message_id": payload.get("message_id")}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return state


def _wire_move_bot(mk_bot, rich_post):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=300))

    async def _delete(**kw):
        rich_post["log"].append("deleteMessage")
    bot._app.bot.delete_message = AsyncMock(side_effect=_delete)
    bot.registry.track_message = MagicMock()
    return bot


def _moving_sess():
    """A live card with a timeline worth copying: tool rows, prose, a
    background shell's row and an agent's row."""
    # The real precondition of a move: the reply target has moved on to
    # the message Claude took (2), the card is still under the old one (1).
    s = _sess(busy_msg_id=100, trigger_msg_id=2)
    s.busy_card_trigger = 1
    s.busy_started_at = __import__("time").monotonic() - 30
    from aipager import bg_shells
    s.tool_history = [
        ("Bash: ping -c 60 127.0.0.1", True),
        (bg_shells.live_row("ping localhost 60 times", "20s"), False),
        ("\U0001f916 general-purpose", False),
        ("WebSearch: bazaars in Iran", True)]
    s.stream_commentary = [(0, "Running the ping now."),
                           (3, "Searching the web.")]
    return s


def _sends(rich_post):
    return [p for m, p, _k in rich_post["calls"] if m == "sendRichMessage"]


def test_the_move_is_one_complete_send_then_the_delete(mk_bot, run_async,
                                                       rich_post):
    from aipager.bot.animation import build_stream_card_ex
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()
    expected, _hid = build_stream_card_ex(sess, "Working", final=False,
                                          waiting=False)

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert rich_post["log"] == ["sendRichMessage", "deleteMessage"], (
        "one send carrying the whole card, then the delete: no bare frame, "
        "no fill-in edit in between")
    (payload,) = _sends(rich_post)
    assert payload["rich_message"]["markdown"] == expected
    assert payload["reply_to_message_id"] == 2
    assert payload["disable_notification"] is True
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Stop"]
    bot._app.bot.send_message.assert_not_awaited()
    assert (sess.busy_msg_id, sess.busy_card_trigger) == (200, 2)
    assert sess.stream_last_rendered == expected


def test_the_copy_carries_the_whole_timeline(mk_bot, run_async, rich_post):
    """Nothing is dropped: every tool row, the prose and the background
    shell's row are in the copy."""
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    (payload,) = _sends(rich_post)
    body = payload["rich_message"]["markdown"]
    for piece in ("ping -c 60 127.0.0.1", "bazaars in Iran",
                  "Running the ping now.", "Searching the web.",
                  "shell: ping localhost 60 times", "general-purpose"):
        assert piece in body, piece


def test_the_move_goes_out_instant_both_calls(mk_bot, run_async, rich_post):
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    ((_m, _p, kw),) = [c for c in rich_post["calls"]
                       if c[0] == "sendRichMessage"]
    assert kw.get("priority") == "instant"
    bot._app.bot.delete_message.assert_awaited_once_with(
        chat_id=555, message_id=100, rate_limit_args=INSTANT)


def test_a_final_move_is_the_finished_card_with_no_stop_button(
        mk_bot, run_async, rich_post):
    from aipager.bot.animation import FINAL_VERB, build_stream_card_ex
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()
    sess.status = Status.IDLE
    sess.card_elapsed_unit = "s"
    expected, _hid = build_stream_card_ex(sess, FINAL_VERB, final=True,
                                          waiting=False)

    run_async(bot._reanchor_busy_card(sess, 2, final=True))

    (payload,) = _sends(rich_post)
    assert "reply_markup" not in payload
    assert payload["rich_message"]["markdown"] == expected
    assert rich_post["log"] == ["sendRichMessage", "deleteMessage"]


def test_a_delete_failure_leaves_the_old_card_and_the_copy_live(
        mk_bot, run_async, rich_post, caplog):
    """B8: the delete raises, the copy is still the live card."""
    import logging
    bot = _wire_move_bot(mk_bot, rich_post)
    bot._app.bot.delete_message = AsyncMock(side_effect=RuntimeError("gone"))
    sess = _moving_sess()

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert sess.busy_msg_id == 200
    bot._app.bot.delete_message.assert_awaited_once()
    assert any("old card was left behind" in r.getMessage()
               for r in caplog.records)


def test_the_move_is_logged_at_info(mk_bot, run_async, rich_post, caplog):
    import logging
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert "[jim] card moved under 2 (100 -> 200)" in [
        r.getMessage() for r in caplog.records]


def test_nothing_moves_when_nothing_is_live(mk_bot, run_async, rich_post):
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _sess(busy_msg_id=0, trigger_msg_id=2)

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert rich_post["log"] == []
    bot._app.bot.send_message.assert_not_awaited()
    assert sess.busy_msg_id == 0


def test_a_muted_chat_moves_nothing(mk_bot, run_async, rich_post):
    """The copy is refused by the mute: no two-step copy is tried into the
    ban, no delete, and the old card stays the live one."""
    from aipager.bot.flood import MUTE
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()
    MUTE.mute(555, 600)

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert rich_post["log"] == []
    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.delete_message.assert_not_awaited()
    assert sess.busy_msg_id == 100


def test_a_blocked_bot_moves_nothing(mk_bot, run_async, rich_post, caplog):
    """Telegram answers 403 (the bot is blocked): nothing can be shown,
    so no two-step copy, no delete; the old card stays the live one."""
    import logging
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    async def _blocked(_payload):
        return {"ok": False, "error_code": 403,
                "description": "Forbidden: bot was blocked by the user"}
    rich_post["send"] = _blocked

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(bot._reanchor_busy_card(sess, 2, final=False))

    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.delete_message.assert_not_awaited()
    assert sess.busy_msg_id == 100
    assert any("the bot is blocked" in r.getMessage() for r in caplog.records)


def test_a_refused_copy_falls_back_to_the_two_step_copy(mk_bot, run_async,
                                                        rich_post):
    """The rich API refused the one-call copy (a 400): a bare frame, the
    fill-in edit, then the delete, all INSTANT."""
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    async def _bad(_payload):
        return {"ok": False, "error_code": 400, "description": "bad"}
    rich_post["send"] = _bad

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    kw = bot._app.bot.send_message.await_args.kwargs
    assert (kw["reply_to_message_id"], kw["rate_limit_args"]) == (2, INSTANT)
    assert rich_post["log"] == ["sendRichMessage", "editMessageText",
                                "deleteMessage"]
    (fill_in_kw,) = [k for m, _p, k in rich_post["calls"]
                     if m == "editMessageText"]
    assert fill_in_kw.get("priority") == "instant"
    assert sess.busy_msg_id == 300


def test_when_both_copies_fail_the_old_card_stays_live(mk_bot, run_async,
                                                     rich_post):
    """The one-call copy is refused and the bare frame cannot be sent
    either: nothing is deleted, the old card stays the live one."""
    bot = _wire_move_bot(mk_bot, rich_post)
    bot._app.bot.send_message = AsyncMock(return_value=None)
    sess = _moving_sess()

    async def _bad(_payload):
        return {"ok": False, "error_code": 400, "description": "bad"}
    rich_post["send"] = _bad

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert sess.busy_msg_id == 100, "keeps the stale card rather than losing it"
    assert sess.busy_card_trigger == 1
    bot._app.bot.delete_message.assert_not_awaited()


def test_a_failed_fill_in_is_rendered_again_by_the_next_edit(
        mk_bot, run_async, rich_post):
    """Two-step copy, fill-in lost: the copy must not stay a bare frame
    because the next edit thinks nothing changed."""
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()
    sess.stream_last_rendered = "what the OLD card showed"
    edits = []

    async def _bad(_payload):
        return {"ok": False, "error_code": 400, "description": "bad"}

    async def _edit(payload):
        edits.append(payload)
        if len(edits) == 1:
            return {"ok": False, "error_code": 500, "description": "down"}
        return {"ok": True, "result": {"message_id": payload["message_id"]}}
    rich_post["send"] = _bad
    rich_post["edit"] = _edit

    async def scenario():
        await bot._reanchor_busy_card(sess, 2, final=False)
        assert sess.stream_last_rendered == ""
        return await bot._edit_busy_rich(sess, "Working")

    assert run_async(scenario()) is True
    assert [e["message_id"] for e in edits] == [300, 300]


def test_a_copy_without_its_button_gets_it_from_the_next_edit(
        mk_bot, run_async, rich_post):
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    async def _no_button(_payload):
        return {"ok": True, "result": {"message_id": 200}}
    rich_post["send"] = _no_button

    async def scenario():
        await bot._reanchor_busy_card(sess, 2, final=False)
        before = len(rich_post["calls"])
        await bot._edit_busy_rich(sess, "Working")
        return rich_post["calls"][before:]

    after = run_async(scenario())
    assert [m for m, _p, _k in after] == ["editMessageText"]
    assert "reply_markup" in after[0][1]


@pytest.fixture
def card_retry(monkeypatch):
    """The delete's 429 retry wait, made instant (a seam, never asyncio)."""
    waits = []

    async def _now(seconds):
        waits.append(seconds)
    monkeypatch.setattr("aipager.bot.animation._card_retry_sleep", _now)
    return waits


def test_a_small_429_on_the_delete_is_waited_out_once(mk_bot, run_async,
                                                      rich_post, card_retry):
    from telegram.error import RetryAfter
    bot = _wire_move_bot(mk_bot, rich_post)
    bot._app.bot.delete_message = AsyncMock(side_effect=[RetryAfter(3), True])
    sess = _moving_sess()

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert card_retry == [3.0]
    assert bot._app.bot.delete_message.await_count == 2


def test_a_ban_sized_429_on_the_delete_gives_up_at_once(mk_bot, run_async,
                                                        rich_post, card_retry):
    from telegram.error import RetryAfter
    bot = _wire_move_bot(mk_bot, rich_post)
    bot._app.bot.delete_message = AsyncMock(side_effect=[RetryAfter(19289)])
    sess = _moving_sess()

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert card_retry == []
    assert bot._app.bot.delete_message.await_count == 1
    assert sess.busy_msg_id == 200


def test_reanchor_busy_card_serializes_via_animate_lock(mk_bot, run_async,
                                                        rich_post):
    """Two concurrent moves on the same session never interleave: the
    second waits for the first's lock to release."""
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _sess(busy_msg_id=100, trigger_msg_id=1)
    sess.busy_card_trigger = 1

    gate = asyncio.Event()
    entered = asyncio.Event()
    ids = iter((201, 202))

    async def _send(_payload):
        n = next(ids)
        if n == 201:
            entered.set()
            await gate.wait()
        return {"ok": True, "result": {"message_id": n,
                                       "reply_markup": {"x": 1}}}
    rich_post["send"] = _send

    async def scenario():
        sess.trigger_msg_id = 2
        first = asyncio.create_task(bot._reanchor_busy_card(sess, 2, final=False))
        await entered.wait()
        assert sess.animate_lock.locked()
        sess.trigger_msg_id = 3      # another message taken meanwhile
        second = asyncio.create_task(bot._reanchor_busy_card(sess, 3, final=False))
        await asyncio.sleep(0)  # let `second` start and block on the lock
        assert not second.done()
        gate.set()
        await asyncio.gather(first, second)

    run_async(scenario())
    assert len(_sends(rich_post)) == 2
    assert sess.busy_msg_id == 202  # the SECOND move's copy won last


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


def test_an_edit_of_the_old_card_in_flight_neither_delays_nor_loses_the_move(
        mk_bot, run_async, rich_post):
    """A card edit is out (waiting for a token, then its POST) on the old
    card when the move starts. The move does not wait for it (review
    rev-iter1-003: it used to, for up to a token interval); the old card
    is deleted under it, Telegram answers "message to edit not found",
    and that edit must NOT clear ``busy_msg_id``, which is the copy's."""
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()
    gate = asyncio.Event()
    entered = asyncio.Event()
    deleted = []

    async def _delete(**kw):
        deleted.append(kw["message_id"])
        rich_post["log"].append("deleteMessage")
    bot._app.bot.delete_message = AsyncMock(side_effect=_delete)

    async def _edit(payload):
        entered.set()
        await gate.wait()
        if payload["message_id"] in deleted:
            return {"ok": False, "error_code": 400,
                    "description": "Bad Request: message to edit not found"}
        return {"ok": True, "result": {"message_id": payload["message_id"]}}
    rich_post["edit"] = _edit

    async def scenario():
        edit = asyncio.create_task(bot._edit_busy_rich(sess, "Working"))
        await entered.wait()
        move = asyncio.create_task(bot._reanchor_busy_card(sess, 2, final=False))
        for _ in range(20):
            await asyncio.sleep(0)
        moved_before_the_edit_returned = move.done()
        gate.set()
        result = (await asyncio.gather(edit, move))[0]
        return moved_before_the_edit_returned, result

    moved_first, edit_result = run_async(scenario())
    assert moved_first is True, "the move waited for the edit"
    assert edit_result is False, "a gone OLD card is transient, not the end"
    assert sess.busy_msg_id == 200
    assert deleted == [100]


def test_a_copy_whose_send_timed_out_is_not_sent_again(mk_bot, run_async,
                                                       rich_post, caplog):
    """The one-call copy's request timed out: it may be on screen already.
    A second copy would be the duplicate card this change removes, so the
    card does not move and the old card stays (review rev-iter1-002)."""
    import logging

    import httpx
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    async def _timeout(_payload):
        raise httpx.ReadTimeout("read timed out")
    rich_post["send"] = _timeout

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(bot._reanchor_busy_card(sess, 2, final=False))

    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.delete_message.assert_not_awaited()
    assert (sess.busy_msg_id, sess.busy_card_trigger) == (100, 1)
    assert any("unknown outcome" in r.getMessage() for r in caplog.records)


def test_a_copy_accepted_without_a_message_id_is_not_sent_again(
        mk_bot, run_async, rich_post):
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    async def _no_id(_payload):
        return {"ok": True, "result": True}
    rich_post["send"] = _no_id

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.delete_message.assert_not_awaited()
    assert sess.busy_msg_id == 100


def test_a_copy_that_could_not_connect_falls_back_to_two_steps(
        mk_bot, run_async, rich_post):
    """A connection that never opened sent nothing: the two-step copy is
    safe to send."""
    import httpx
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    async def _refused(_payload):
        raise httpx.ConnectError("connection refused")
    rich_post["send"] = _refused

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id == 300


def test_a_paused_card_moves_as_the_paused_line(mk_bot, run_async, rich_post):
    """Minimal mode: the old card shows "working (updates paused)"; its
    copy says the same, and stays marked as paused so the tick does not
    send the line again."""
    from aipager.bot.animation import _PAUSED_CARD_TEXT
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()
    sess.stream_last_rendered = _PAUSED_CARD_TEXT

    run_async(bot._reanchor_busy_card(sess, 2, final=False))

    (payload,) = _sends(rich_post)
    assert payload["rich_message"]["markdown"] == (
        "⏳ **jim** · working (updates paused)")
    assert "Stop" in str(payload["reply_markup"])
    assert sess.stream_last_rendered == _PAUSED_CARD_TEXT
    assert rich_post["log"] == ["sendRichMessage", "deleteMessage"]


def test_the_two_step_fallback_is_logged_at_info(mk_bot, run_async,
                                                 rich_post, caplog):
    import logging
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    async def _bad(_payload):
        return {"ok": False, "error_code": 400, "description": "bad"}
    rich_post["send"] = _bad

    with caplog.at_level(logging.INFO, logger="aipager.bot.animation"):
        run_async(bot._reanchor_busy_card(sess, 2, final=False))

    assert any("sending it in two steps" in r.getMessage()
               for r in caplog.records)


def test_two_movers_to_the_same_message_send_one_copy(mk_bot, run_async,
                                                      rich_post):
    """The queue watcher, the tick and a hook can all see the same taken
    message: the first to get the lock moves the card, the others find it
    already there (a second copy would flash two cards)."""
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()

    async def scenario():
        await asyncio.gather(
            bot._reanchor_busy_card(sess, 2, final=False),
            bot._reanchor_busy_card(sess, 2, final=False),
            bot._reanchor_busy_card(sess, 2, final=False))

    run_async(scenario())
    assert len(_sends(rich_post)) == 1
    assert (sess.busy_msg_id, sess.busy_card_trigger) == (200, 2)


def test_a_final_move_is_never_skipped_as_already_there(mk_bot, run_async,
                                                        rich_post):
    """The finish renders the finished card even when a live move just put
    the card under the same message: skipping it would leave "Working"
    and a Stop button on a finished turn."""
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()
    sess.busy_card_trigger = 2     # a live move already put it there
    sess.status = Status.IDLE

    run_async(bot._reanchor_busy_card(sess, 2, final=True))

    sends = _sends(rich_post)
    assert len(sends) == 1, "the final move was skipped"
    assert "reply_markup" not in sends[0]


def test_a_move_whose_target_moved_on_while_it_waited_sends_nothing(
        mk_bot, run_async, rich_post):
    """A live move waits for the card's lock; meanwhile a Stop popped the
    next message as a new turn, whose card is under that message. The
    waiting move must not drag the new turn's card back under the old
    target (review rev-iter1-001)."""
    bot = _wire_move_bot(mk_bot, rich_post)
    sess = _moving_sess()
    sess.trigger_msg_id = 2        # the move is for 2 ...

    async def scenario():
        await sess.animate_lock.acquire()          # the finish holds it
        move = asyncio.create_task(bot._reanchor_busy_card(sess, 2,
                                                           final=False))
        for _ in range(5):
            await asyncio.sleep(0)
        # ... the finish popped 3 as a new turn, with its own card.
        sess.trigger_msg_id = 3
        sess.busy_card_trigger = 3
        sess.busy_msg_id = 300
        sess.animate_lock.release()
        await move

    run_async(scenario())
    assert _sends(rich_post) == []
    assert (sess.busy_msg_id, sess.trigger_msg_id) == (300, 3)
