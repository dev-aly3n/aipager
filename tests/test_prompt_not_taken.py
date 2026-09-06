"""A prompt Claude Code never took is surfaced in Telegram (roadmap 8.11).

Observed 2026-09-05: a message Claude Code refused outright (an unknown
slash command) printed its error in the terminal and fired NO hook. The
daemon had flipped IDLE → BUSY at send time and posted a busy card, so
Telegram showed a spinner with nothing to say why; the operator had to
read the terminal and ``/stop``. The stale-busy note would have taken
ten minutes.

Contract pinned here:

- ``_inject_prompt`` stamps ``prompt_sent_at`` / ``prompt_sent_msg`` on a
  successful turn-starting send only — a send made while BUSY neither
  stamps nor disturbs an earlier stamp (R1);
- every datagram except ``statusline`` and the phantom ``SubagentStop``
  stamps ``turn_hook_at`` (R2);
- ``prompt_not_taken`` holds for a BUSY session whose turn-starting send
  saw no such datagram within ``PROMPT_HOOK_GRACE_SECONDS``; the monitor
  fires the event once (R3);
- the handler turns the card into a warning, drops the message's note
  and returns the session to idle (R4); a late prompt hook then starts a
  turn with a fresh card as usual (R5).

No real Telegram, dtach or claude. Stamps are fabricated from
``steady_clock`` so they are never negative on a fresh runner.
"""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import AsyncMock

import pytest

from aipager import policy_snapshot as ps
from aipager.config import PROMPT_HOOK_GRACE_SECONDS
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.session_monitor import SessionMonitor, prompt_not_taken
from aipager.state import SessionRegistry, Status, TrackedSession

NAME = "claude-jim"
CHAT = -1001


@pytest.fixture
def receiver():
    """(registry, recv, notify_fn) — a wired-up HookReceiver."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    return registry, hr.HookReceiver(registry, notify_fn), notify_fn


async def _coroutine_returning(value):
    return value


def _sess(status=Status.IDLE) -> TrackedSession:
    return TrackedSession(name=NAME, label="jim", status=status)


def _registry_with(sess) -> SessionRegistry:
    registry = SessionRegistry()
    registry._sessions[sess.name] = sess
    return registry


def _monitor(registry, notify_fn, monkeypatch) -> SessionMonitor:
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning([NAME]),
    )
    return SessionMonitor(registry, notify_fn)


def _fired(notify_fn, event="prompt_not_taken"):
    return [c for c in notify_fn.await_args_list if c.args[1] == event]


def _warning_sends(bot):
    """``send_message`` calls carrying the warning — ``notify`` also
    creates the 📌 pinned message on a fresh bot, which is not ours."""
    return [c for c in bot._app.bot.send_message.await_args_list
            if "Not taken by Claude Code" in str(c.args[1:2])]


def _turn_started_send(sess, now, *, age, msg=(777, CHAT)):
    """State right after a turn-starting Telegram send ``age`` seconds
    ago whose hooks never came: BUSY, a live card, the R1 stamps."""
    sess.status = Status.BUSY
    sess.busy_msg_id = 4242
    sess.trigger_msg_id = msg[0] if msg else None
    sess.busy_card_trigger = sess.trigger_msg_id
    sess.busy_started_at = now - age
    sess.prompt_sent_at = now - age
    sess.prompt_sent_msg = msg
    return sess


# ===== R1: _inject_prompt stamps a successful send only =================

def test_inject_prompt_stamps_a_successful_send(mk_bot, run_async, monkeypatch, steady_clock):
    bot = mk_bot()
    sess = _sess()
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))

    before = time.monotonic()  # the real clock: steady_clock pins only the monitor and the animator
    ok = run_async(bot._inject_prompt(sess, "hello", msg_id=555, chat_id=CHAT))

    assert ok is True
    assert sess.prompt_sent_at >= before
    assert sess.prompt_sent_msg == (555, CHAT)


def test_inject_prompt_while_busy_stamps_nothing(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(Status.BUSY)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))

    run_async(bot._inject_prompt(sess, "and this", msg_id=556, chat_id=CHAT))

    assert sess.prompt_sent_at == 0.0
    assert sess.prompt_sent_msg is None


def test_a_second_send_while_falsely_busy_keeps_the_first_deadline(
    mk_bot, run_async, monkeypatch, steady_clock,
):
    """rev-iter1-001: message 1 from IDLE is rejected (no hook, session
    left BUSY); message 2 arrives 3 s later and is injected. It must not
    overwrite message 1's stamp, or the watchdog would never fire."""
    bot = mk_bot()
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=3)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))

    run_async(bot._inject_prompt(sess, "second", msg_id=778, chat_id=CHAT))

    assert sess.prompt_sent_at == now - 3
    assert sess.prompt_sent_msg == (777, CHAT)
    assert prompt_not_taken(sess, now + PROMPT_HOOK_GRACE_SECONDS) is True


def test_inject_prompt_without_ids_stamps_no_message(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess()
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))

    run_async(bot._inject_prompt(sess, "hello"))

    assert sess.prompt_sent_at > 0
    assert sess.prompt_sent_msg is None


def test_inject_prompt_failed_send_stamps_nothing(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess()
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=False))

    ok = run_async(bot._inject_prompt(sess, "hello", msg_id=557, chat_id=CHAT))

    assert ok is False
    assert sess.prompt_sent_at == 0.0
    assert sess.prompt_sent_msg is None


# ===== R2: which datagrams count as evidence of a turn ==================

def _datagram(recv, run_async, **fields):
    fields.setdefault("session", NAME)
    fields.setdefault("transcript_path", "")
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def test_prompt_submit_and_precompact_stamp_turn_hook_at(receiver, run_async):
    registry, recv, _notify = receiver
    for event in ("UserPromptSubmit", "PreCompact"):
        sess = registry.get_or_create(NAME)
        sess.turn_hook_at = 0.0
        _datagram(recv, run_async, hook_event_name=event, prompt="hi")
        assert sess.turn_hook_at > 0, event
        assert sess.last_hook_at > 0


def test_statusline_and_phantom_subagent_stop_do_not_stamp_turn_hook_at(
    receiver, run_async, tmp_path, monkeypatch,
):
    registry, recv, _notify = receiver
    sess = registry.get_or_create(NAME)
    monkeypatch.setattr(hr, "_read_statusline", lambda _name: None)

    _datagram(recv, run_async, notification_type="statusline")
    assert sess.last_hook_at > 0  # still activity for the stale detector
    assert sess.turn_hook_at == 0.0

    _datagram(recv, run_async, hook_event_name="SubagentStop", agent_id="a1",
              agent_type="", agent_transcript_path="")
    assert sess.turn_hook_at == 0.0


# ===== R3: the predicate and the monitor ================================

def test_predicate_holds_only_past_the_grace(steady_clock):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=PROMPT_HOOK_GRACE_SECONDS + 1)
    assert prompt_not_taken(sess, now) is True

    sess = _turn_started_send(_sess(), now, age=3)
    assert prompt_not_taken(sess, now) is False


def test_predicate_is_cleared_by_a_turn_hook_after_the_send(steady_clock):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=PROMPT_HOOK_GRACE_SECONDS + 1)
    sess.turn_hook_at = sess.prompt_sent_at + 0.2
    assert prompt_not_taken(sess, now) is False


def test_predicate_needs_busy_and_a_stamp(steady_clock):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=60)
    sess.status = Status.IDLE
    assert prompt_not_taken(sess, now) is False

    sess = _turn_started_send(_sess(), now, age=60)
    sess.prompt_sent_at = 0.0
    assert prompt_not_taken(sess, now) is False


def test_monitor_fires_once_past_the_grace(steady_clock, monkeypatch, run_async):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=PROMPT_HOOK_GRACE_SECONDS + 1)
    registry = _registry_with(sess)
    notify_fn = AsyncMock()
    monitor = _monitor(registry, notify_fn, monkeypatch)

    run_async(monitor._scan())
    fired = _fired(notify_fn)
    assert len(fired) == 1
    fired_sess, _event, ctx = fired[0].args
    assert fired_sess is sess
    assert ctx == {"grace": PROMPT_HOOK_GRACE_SECONDS, "msg": (777, CHAT)}
    assert sess.prompt_sent_at == 0.0  # fire once

    run_async(monitor._scan())
    assert len(_fired(notify_fn)) == 1


def test_monitor_stays_quiet_inside_the_grace(steady_clock, monkeypatch, run_async):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=3)
    registry = _registry_with(sess)
    notify_fn = AsyncMock()
    monitor = _monitor(registry, notify_fn, monkeypatch)

    run_async(monitor._scan())

    assert _fired(notify_fn) == []
    assert sess.prompt_sent_at == now - 3


def test_monitor_stays_quiet_when_a_turn_hook_followed(steady_clock, monkeypatch, run_async):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=PROMPT_HOOK_GRACE_SECONDS + 1)
    sess.turn_hook_at = sess.prompt_sent_at + 0.2
    registry = _registry_with(sess)
    notify_fn = AsyncMock()
    monitor = _monitor(registry, notify_fn, monkeypatch)

    run_async(monitor._scan())

    assert _fired(notify_fn) == []


def test_monitor_never_fires_for_a_send_made_while_busy(
    mk_bot, steady_clock, monkeypatch, run_async,
):
    """A message queued behind a running turn is not judged: it is
    never stamped, so even a minute later nothing fires."""
    bot = mk_bot()
    sess = _sess(Status.BUSY)
    sess.busy_msg_id = 4242
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    run_async(bot._inject_prompt(sess, "queued", msg_id=779, chat_id=CHAT))
    sess.turn_hook_at = 0.0
    registry = _registry_with(sess)
    notify_fn = AsyncMock()
    monitor = _monitor(registry, notify_fn, monkeypatch)

    run_async(monitor._scan())

    assert _fired(notify_fn) == []


# ===== R4: the handler ====================================================

def _wired_bot(mk_bot, sess):
    bot = mk_bot(_registry_with(sess))
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._send_busy_and_animate = AsyncMock()
    return bot


def test_handler_turns_the_card_into_a_warning_and_idles_the_session(
    mk_bot, run_async, steady_clock, caplog,
):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=9)
    ps.write_note(NAME, None, None, None, msg_id=777, chat_id=CHAT,
                  sender_key=(0, 0), body="/nosuch hello", raw_text="/nosuch hello")
    ps.write_note(NAME, None, None, None, msg_id=778, chat_id=CHAT,
                  sender_key=(0, 0), body="keep me", raw_text="keep me")
    bot = _wired_bot(mk_bot, sess)
    t0 = time.monotonic()

    with caplog.at_level(logging.WARNING, logger="aipager.bot.notify"):
        run_async(bot.notify(sess, "prompt_not_taken",
                             {"grace": PROMPT_HOOK_GRACE_SECONDS, "msg": (777, CHAT)}))

    bot._edit_busy_raw.assert_awaited_once()
    args, kwargs = bot._edit_busy_raw.await_args
    assert args[0] == 4242
    text = args[1]
    assert text.startswith("⚠️ <b>jim</b> · Not taken by Claude Code")
    assert f"within {PROMPT_HOOK_GRACE_SECONDS:.0f} s" in text
    assert "slash command" in text and "terminal" in text
    assert kwargs.get("reply_markup") is None
    assert _warning_sends(bot) == []

    assert sess.status == Status.IDLE
    assert sess.busy_msg_id is None
    assert sess.trigger_msg_id is None
    assert sess.busy_card_trigger is None
    assert sess.prompt_sent_msg is None
    assert sess.last_idle_at >= t0
    remaining = [n["msg_id"] for n in ps.list_outstanding_notes(NAME)]
    assert remaining == [778]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("prompt not taken by Claude Code" in w and "777" in w for w in warnings)


def test_handler_escapes_the_label(mk_bot, run_async, steady_clock):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=9)
    sess.label = "a<b>"
    bot = _wired_bot(mk_bot, sess)

    run_async(bot.notify(sess, "prompt_not_taken", {"grace": 8.0, "msg": (777, CHAT)}))

    text = bot._edit_busy_raw.await_args.args[1]
    assert "<b>a&lt;b&gt;</b>" in text


def test_handler_without_a_card_replies_to_the_trigger(mk_bot, run_async, steady_clock):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=9)
    sess.busy_msg_id = None
    bot = _wired_bot(mk_bot, sess)

    run_async(bot.notify(sess, "prompt_not_taken", {"grace": 8.0, "msg": (777, CHAT)}))

    bot._edit_busy_raw.assert_not_awaited()
    sends = _warning_sends(bot)
    assert len(sends) == 1
    kwargs = sends[0].kwargs
    assert kwargs.get("reply_to_message_id") == 777
    assert kwargs.get("parse_mode") == "HTML"
    assert sess.status == Status.IDLE


def test_handler_deletes_only_the_note_from_the_same_chat(mk_bot, run_async, steady_clock):
    """rev-iter1-003: a message id is only unique within its chat."""
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=9)
    ps.write_note(NAME, None, None, None, msg_id=777, chat_id=CHAT,
                  sender_key=(0, 0), body="mine", raw_text="mine")
    ps.write_note(NAME, None, None, None, msg_id=777, chat_id=CHAT - 1,
                  sender_key=(0, 0), body="other chat", raw_text="other chat")
    bot = _wired_bot(mk_bot, sess)

    run_async(bot.notify(sess, "prompt_not_taken", {"grace": 8.0, "msg": (777, CHAT)}))

    left = [(n["msg_id"], n["chat_id"]) for n in ps.list_outstanding_notes(NAME)]
    assert left == [(777, CHAT - 1)]


def test_handler_drains_a_held_message_once_idle(mk_bot, run_async, steady_clock, monkeypatch):
    """rev-iter1-002: a message parked in pending_queue behind the
    rejected one gets its turn now, like the idle path would give it."""
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=9)
    sess.pending_queue.append(("held text", 780, now - 5, "", None))
    bot = _wired_bot(mk_bot, sess)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "send_text_and_enter", sent)

    run_async(bot.notify(sess, "prompt_not_taken", {"grace": 8.0, "msg": (777, CHAT)}))

    sent.assert_awaited_once()
    assert sent.await_args.args[1].endswith("held text")
    assert sess.pending_queue == []
    assert sess.status == Status.BUSY
    assert sess.trigger_msg_id == 780
    assert sess.prompt_sent_msg is not None and sess.prompt_sent_msg[0] == 780
    bot._send_busy_and_animate.assert_awaited_once_with(sess)


def test_handler_without_a_message_deletes_no_note(mk_bot, run_async, steady_clock):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=9, msg=None)
    ps.write_note(NAME, None, None, None, msg_id=778, chat_id=CHAT,
                  sender_key=(0, 0), body="keep me", raw_text="keep me")
    bot = _wired_bot(mk_bot, sess)

    run_async(bot.notify(sess, "prompt_not_taken", {"grace": 8.0, "msg": None}))

    assert [n["msg_id"] for n in ps.list_outstanding_notes(NAME)] == [778]
    assert sess.status == Status.IDLE


# ===== R5: a late prompt hook still starts the turn =====================

def test_late_prompt_hook_after_the_revert_starts_a_fresh_turn(
    mk_bot, run_async, steady_clock,
):
    now = steady_clock()
    sess = _turn_started_send(_sess(), now, age=9)
    bot = _wired_bot(mk_bot, sess)
    run_async(bot.notify(sess, "prompt_not_taken", {"grace": 8.0, "msg": (777, CHAT)}))
    assert sess.status == Status.IDLE and sess.busy_msg_id is None

    recv = hr.HookReceiver(bot.registry, bot.notify)
    _datagram(recv, run_async, hook_event_name="UserPromptSubmit",
              prompt="[via Telegram · @owner]\nhello")

    assert sess.status == Status.BUSY
    bot._send_busy_and_animate.assert_awaited_once_with(sess)
    assert sess.turn_hook_at > 0


# ===== transient: never persisted ========================================

def test_watchdog_fields_are_not_persisted(tmp_state_file, steady_clock):
    registry = SessionRegistry()
    sess = _turn_started_send(_sess(), steady_clock(), age=9)
    sess.turn_hook_at = 1.0
    registry._sessions[sess.name] = sess
    registry.save()

    loaded = SessionRegistry()
    loaded.load()
    back = loaded.get(NAME)
    assert back is not None
    assert back.prompt_sent_at == 0.0
    assert back.prompt_sent_msg is None
    assert back.turn_hook_at == 0.0
