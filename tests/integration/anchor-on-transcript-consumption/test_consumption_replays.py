"""Anchor on REAL consumption ("anchor-on-transcript-consumption").

Measured 2026-09-05 on the live daemon: Claude Code fires the
``UserPromptSubmit`` hook (so aipager's ``queue_pickup``) the moment a
message is SUBMITTED while a turn runs — not when it is picked up — and
a queued message popped as the NEXT turn (transcript ``dequeue``) fires
no hook at all. So a pick-up while BUSY is "queued, fate unknown": the
transcript's ``absorbed_mid_turn`` line is the real consumption signal,
and an unconsumed queued message at Stop is the next turn's prompt.

Black-box: ``TelegramBot._handle_message``, ``bot.notify(...)``,
transcript JSONL lines, the mocked Telegram transports.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

from aipager import preferences as prefs
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status

CHAT_ID = -2002
PREFIX = "[via Telegram · @owner]\n"


def _update(mk_update, text, message_id, user_id=12345):
    return mk_update(text, message_id=message_id, user_id=user_id, chat_id=CHAT_ID)


def _pickup(bot, run_async, sess, msg_id, text):
    """The hook's submit-time pick-up for a message typed while BUSY."""
    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [{"msg_id": msg_id, "chat_id": CHAT_ID, "raw_text": text}],
        "expired": [],
    }))


def _start_turn(bot, mk_update, run_async, sess):
    run_async(bot._handle_message(_update(mk_update, "hello?", message_id=1),
                                  MagicMock()))
    assert sess.status == Status.BUSY and sess.trigger_msg_id == 1
    return sess.busy_msg_id


def _reanchor_sends(bot):
    return [c for c in bot._app.bot.send_message.await_args_list
            if c.kwargs.get("disable_notification") is True]


def _answers(rich_calls):
    return [p["reply_to_message_id"] for m, p in rich_calls if m == "sendRichMessage"]


# ── (b) absorbed: the pick-up at submit moves nothing; the transcript does ─

def test_pickup_while_busy_moves_nothing_and_absorption_reanchors(
    wired, mk_update, run_async, rich_calls, append_queue_op,
):
    bot, sess, _injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    c1 = _start_turn(bot, mk_update, run_async, sess)
    run_async(bot._handle_message(_update(mk_update, "second", message_id=2), MagicMock()))
    _pickup(bot, run_async, sess, 2, "second")

    assert sess.trigger_msg_id == 1, "a pick-up at submit time is not consumption"
    assert sess.busy_msg_id == c1 and not _reanchor_sends(bot)
    assert [t["msg_id"] for t in sess.queued_targets] == [2]
    assert any(c.args[1] == 2 and c.args[2] == "👍"
               for c in bot._app.bot.set_message_reaction.await_args_list)

    append_queue_op(sess, "remove", "absorbed_mid_turn", PREFIX + "second")
    run_async(bot.notify(sess, "assistant_text", {"delta": "going on", "message_id": "m1"}))

    assert sess.trigger_msg_id == 2
    assert sess.busy_card_trigger == 2 and sess.busy_msg_id != c1
    assert sess.queued_targets == []
    (re_send,) = _reanchor_sends(bot)
    assert re_send.kwargs["reply_to_message_id"] == 2
    bot._app.bot.delete_message.assert_any_await(chat_id=CHAT_ID, message_id=c1)

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done", "raw_md": "done"}))
    assert _answers(rich_calls) == [2]
    assert sess.status == Status.IDLE, "nothing was left queued, no next turn"


# ── (a) queued to the next turn: turn 1 stays home, turn 2 gets its own ──

def test_unconsumed_queued_message_becomes_the_next_turn(
    wired, mk_update, run_async, rich_calls,
):
    bot, sess, _injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    c1 = _start_turn(bot, mk_update, run_async, sess)
    run_async(bot._handle_message(_update(mk_update, "second", message_id=2), MagicMock()))
    _pickup(bot, run_async, sess, 2, "second")
    assert sess.trigger_msg_id == 1 and sess.busy_msg_id == c1

    # Turn 1 ends with M2 still unconsumed: Claude Code pops it next.
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "answer one", "raw_md": "answer one"}))

    assert _answers(rich_calls) == [1], "turn 1's answer stays under M1"
    assert sess.status == Status.BUSY, "the queued message's turn starts at once"
    assert sess.trigger_msg_id == 2 and sess.busy_card_trigger == 2
    assert sess.busy_msg_id and sess.busy_msg_id != c1
    assert sess.queued_targets == []
    new_card = bot._app.bot.send_message.await_args_list[-1]
    assert new_card.kwargs["reply_to_message_id"] == 2
    assert new_card.kwargs.get("disable_notification") is not True

    # Turn 2 ends (in real life inside the 10 s IDLE debounce).
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "answer two", "raw_md": "answer two"}))
    assert _answers(rich_calls) == [1, 2]
    assert sess.status == Status.IDLE


# ── (c) two queued: first absorbed live, second popped as the next turn ──

def test_first_queued_absorbed_second_becomes_next_turn(
    wired, mk_update, run_async, rich_calls, append_queue_op,
):
    bot, sess, _injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    c1 = _start_turn(bot, mk_update, run_async, sess)
    for mid, text in ((2, "second"), (3, "third")):
        run_async(bot._handle_message(_update(mk_update, text, message_id=mid), MagicMock()))
        _pickup(bot, run_async, sess, mid, text)
    assert [t["msg_id"] for t in sess.queued_targets] == [2, 3]

    append_queue_op(sess, "remove", "absorbed_mid_turn", PREFIX + "second")
    run_async(bot.notify(sess, "assistant_text", {"delta": "…", "message_id": "m1"}))
    assert sess.trigger_msg_id == 2 and sess.busy_card_trigger == 2
    assert [t["msg_id"] for t in sess.queued_targets] == [3]

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "answer one", "raw_md": "answer one"}))
    assert _answers(rich_calls) == [2]
    assert sess.status == Status.BUSY and sess.trigger_msg_id == 3
    assert bot._app.bot.send_message.await_args_list[-1].kwargs["reply_to_message_id"] == 3
    assert sess.queued_targets == [] and sess.busy_msg_id != c1


# ── (d) /stop drops the queued targets; no phantom next turn ─────────────

def test_stop_drops_queued_targets_and_starts_no_next_turn(
    wired, mk_update, run_async, rich_calls, monkeypatch,
):
    bot, sess, _injected = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    _start_turn(bot, mk_update, run_async, sess)
    run_async(bot._handle_message(_update(mk_update, "second", message_id=2), MagicMock()))
    _pickup(bot, run_async, sess, 2, "second")
    assert sess.queued_targets

    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.discard_queued_input", AsyncMock(return_value=True))
    run_async(bot._stop_session_core(sess))
    assert sess.queued_targets == []

    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": "stopped", "raw_md": "stopped"}))
    assert sess.status == Status.IDLE, "no next turn was started from a dropped message"


# ── (e) R9: a real turn end is never debounced; a late Stop still delivers ─

def test_busy_to_idle_is_never_debounced():
    r = SessionRegistry()
    r.transition("claude-x", Status.BUSY)
    assert r.transition("claude-x", Status.IDLE) is not None
    r.transition("claude-x", Status.BUSY)  # a new turn starts within 10 s
    assert r.transition("claude-x", Status.IDLE) is not None, (
        "a BUSY → IDLE transition is a real turn end, never debounced"
    )


def test_stop_on_idle_session_delivers_an_undelivered_answer(run_async):
    """The next-turn Stop landed on an IDLE session with no reply target
    (turn 1's finish had cleared it) and was dropped. It must deliver."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    registry.transition("claude-x", Status.BUSY)
    registry.transition("claude-x", Status.IDLE)
    sess = registry.get("claude-x")
    sess.trigger_msg_id = None

    payload = json.dumps({"hook_event_name": "Stop", "session": "claude-x",
                          "last_assistant_message": "Christopher Columbus, 1492."}).encode()
    run_async(recv._on_datagram(payload))

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "idle_prompt" in events, "an undelivered answer must reach the finish path"
    ctx = next(c.args[2] for c in notify_fn.await_args_list if c.args[1] == "idle_prompt")
    assert "Columbus" in (ctx.get("raw_md") or ctx.get("summary") or "")

    # Already delivered → a repeated Stop does not re-notify.
    notify_fn.reset_mock()
    sess.remember_delivered(hashlib.md5(b"Christopher Columbus, 1492.").hexdigest())
    run_async(recv._on_datagram(json.dumps({"hook_event_name": "Stop", "session": "claude-x",
                                            "last_assistant_message": "Christopher Columbus, 1492.",
                                            "again": 1}).encode()))
    assert "idle_prompt" not in [c.args[1] for c in notify_fn.await_args_list]


def test_receiver_pickup_while_busy_leaves_the_target_alone(run_async):
    """The hook receiver's own queue_pickup branch pre-applies the target
    before notify — it must not do so while a turn is running, or turn 1's
    answer would already be re-targeted before notify can classify the
    message as queued."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    registry.transition("claude-x", Status.BUSY)
    sess = registry.get("claude-x")
    sess.trigger_msg_id = 1
    sess.last_prompt = "first"

    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "queue_pickup", "session": "claude-x",
        "consumed": [{"msg_id": 2, "chat_id": CHAT_ID, "raw_text": "second"}],
        "expired": [],
    }).encode()))

    assert sess.trigger_msg_id == 1 and sess.last_prompt == "first"
    assert "queue_pickup" in [c.args[1] for c in notify_fn.await_args_list]

    # Idle session: the pick-up starts a turn and does move the target.
    registry.transition("claude-x", Status.IDLE)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "queue_pickup", "session": "claude-x",
        "consumed": [{"msg_id": 3, "chat_id": CHAT_ID, "raw_text": "third"}],
        "expired": [],
    }).encode()))
    assert sess.trigger_msg_id == 3 and sess.last_prompt == "third"


# ── review-1 fixes ───────────────────────────────────────────────────────

def test_absorption_matcher_prefers_the_longest_matching_target():
    """rev-iter1-001: a short queued message ("ok") is a substring of a
    longer one; the line about the longer one must pop the longer one."""
    from aipager.bot.animation import _pop_queued_target
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-x", label="x", status=Status.BUSY)
    sess.queued_targets = [
        {"msg_id": 5, "chat_id": CHAT_ID, "raw_text": "ok"},
        {"msg_id": 6, "chat_id": CHAT_ID, "raw_text": "ok let's actually go with plan B instead"},
    ]
    popped = _pop_queued_target(sess, PREFIX + "ok let's actually go with plan B instead")
    assert popped["msg_id"] == 6
    assert [t["msg_id"] for t in sess.queued_targets] == [5]
    assert _pop_queued_target(sess, PREFIX + "ok")["msg_id"] == 5
    assert sess.queued_targets == []
    assert _pop_queued_target(sess, PREFIX + "anything") is None


def test_stop_hook_right_after_an_explicit_stop_is_not_delivered(run_async):
    """rev-iter1-003: /stop sets IDLE directly; Claude then finalises the
    interrupted turn with a Stop hook whose text is a partial answer. It
    must not be delivered under a card that already says Stopped — but
    the same Stop long after a /stop (a genuinely unanswered turn) is."""
    import time as _t
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    registry.transition("claude-x", Status.BUSY)
    sess = registry.get("claude-x")
    sess.status = Status.IDLE  # _stop_session_core's post-conditions
    sess.trigger_msg_id = None
    sess.user_stopped_at = _t.monotonic()

    payload = json.dumps({"hook_event_name": "Stop", "session": "claude-x",
                          "last_assistant_message": "Partial answer that was interrupted."}).encode()
    run_async(recv._on_datagram(payload))
    assert "idle_prompt" not in [c.args[1] for c in notify_fn.await_args_list]

    sess.user_stopped_at = _t.monotonic() - hr._STOP_SUPPRESS_SECONDS - 1
    run_async(recv._on_datagram(json.dumps({"hook_event_name": "Stop", "session": "claude-x",
                                            "last_assistant_message": "Partial answer that was interrupted.",
                                            "later": 1}).encode()))
    assert "idle_prompt" in [c.args[1] for c in notify_fn.await_args_list]


def test_stop_on_idle_session_falls_back_to_the_transcript(run_async, tmp_path):
    """rev-iter1-004: a Stop variant without last_assistant_message on an
    already-IDLE session must read the transcript like the normal path."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    registry.transition("claude-x", Status.BUSY)
    registry.transition("claude-x", Status.IDLE)
    sess = registry.get("claude-x")
    sess.trigger_msg_id = None
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "text", "text": "Answer only in the transcript."}],
        "stop_reason": "end_turn"}}) + "\n")
    sess.transcript_path = str(tp)

    run_async(recv._on_datagram(json.dumps({"hook_event_name": "Stop", "session": "claude-x",
                                            "transcript_path": str(tp)}).encode()))

    calls = [c for c in notify_fn.await_args_list if c.args[1] == "idle_prompt"]
    assert calls, "the transcript's undelivered answer must reach the finish path"
    ctx = calls[0].args[2]
    assert "only in the transcript" in (ctx.get("raw_md") or ctx.get("summary") or "")


def test_absorption_matcher_tie_breaks_oldest_and_skips_empty_text():
    from aipager.bot.animation import _pop_queued_target
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-x", label="x", status=Status.BUSY)
    sess.queued_targets = [
        {"msg_id": 1, "chat_id": CHAT_ID, "raw_text": ""},
        {"msg_id": 2, "chat_id": CHAT_ID, "raw_text": "same"},
        {"msg_id": 3, "chat_id": CHAT_ID, "raw_text": "same"},
    ]
    assert _pop_queued_target(sess, PREFIX + "same")["msg_id"] == 2
    assert _pop_queued_target(sess, PREFIX + "same")["msg_id"] == 3
    assert _pop_queued_target(sess, PREFIX + "same") is None
    assert [t["msg_id"] for t in sess.queued_targets] == [1]
