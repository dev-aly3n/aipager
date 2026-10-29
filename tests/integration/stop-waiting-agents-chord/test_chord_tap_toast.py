"""A Stop tap's one answer says something true (live round 2, H3 and H6).

A tap takes ONE answer, and the dispatcher sends the empty one that clears
the spinner after ``callbacks.CALLBACK_ACK_BOUND``. The kill chord alone
takes a second and the Escape path took 5 to 6 s live, so every card and
/status Stop got that empty answer and its toast was dropped. Now a Stop
still running at ``session_ops.STOP_TOAST_WAIT`` (below the bound) answers
"Stopping" at once, the card (or the list) then shows the outcome, and a
refusal that comes later is a reply under the tapped message. A Stop done
in time answers with its outcome, as before. Never two answers.

The clocks here are scaled down (bound 0.3 s, toast wait 0.05 s) and the
slow step is a real short wait through the seams (``inject._key_gap_sleep``
for the chord's gap, ``session_ops._sleep`` between the Escapes)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import callbacks, session_ops
from aipager.bot.session_ops import StopOutcome
from aipager.dtach import inject
from aipager.state import Status
from tests.two_prompts_rig import ALY, CARD, GROUP, OTHER, S, BG

CHORD = b"\x18\x0b"
STOPPING = "⏹ Stopping api…"
SLOW = 0.4


@pytest.fixture(autouse=True)
def _scaled(monkeypatch):
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.3)
    monkeypatch.setattr(session_ops, "STOP_TOAST_WAIT", 0.05)


def _slow_chord(rig, monkeypatch):
    async def _gap(delay):
        rig.writes.append(("gap", delay))
        await asyncio.sleep(SLOW)

    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)


def _waiting(rig):
    rig.launch(BG)
    rig.launch(OTHER, agent_type="ship-other")
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Started them.")
    assert rig.sess.status is Status.IDLE
    return rig.sess


def _tap(rig, msg_id, data, uid=ALY):
    """``Rig.tap`` with a message that can be replied to."""
    q = MagicMock()
    q.data = f"{S}:{data}"
    q.from_user = MagicMock(id=uid)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=GROUP)
    q.message.chat_id = GROUP
    q.message.message_id = msg_id
    q.message.text = ""
    q.message.reply_text = AsyncMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    u.message = None
    rig.run_async(rig.bot._handle_callback(u, MagicMock()))
    return q


def _answers(q) -> list:
    """Every answer the tap got, the empty one as None."""
    return [c.args[0] if c.args else None for c in q.answer.await_args_list]


def test_a_slow_kill_from_the_card_is_toasted_stopping_and_the_card_says_how(
        rig, monkeypatch):
    sess = _waiting(rig)
    _slow_chord(rig, monkeypatch)
    q = _tap(rig, CARD, "stop")
    assert rig.writes.bytes() == [CHORD, CHORD]
    assert _answers(q) == [STOPPING]
    assert rig.card_edits[-1][1] == "⚠️ <b>api</b> · @alice stopped 2 background agents"
    assert sess.status is Status.IDLE


def test_a_kill_done_in_time_is_toasted_its_outcome(rig):
    """Control: the chord's gap takes no time here, so the Stop is done
    before the toast wait and the tap shows the outcome itself."""
    _waiting(rig)
    q = _tap(rig, CARD, "stop")
    assert _answers(q) == ["⏹ @alice stopped 2 background agents of api"]


def test_a_slow_escape_stop_on_a_working_session_is_toasted_stopping(rig, monkeypatch):
    """H6: the Escape path (a working session), the same tap path."""
    async def _slow(_delay):
        await asyncio.sleep(SLOW)

    monkeypatch.setattr(session_ops, "_sleep", _slow)
    sess = rig.sess
    assert sess.status is Status.BUSY
    q = _tap(rig, CARD, "stop")
    assert rig.writes.bytes() == [b"\x1b", b"\x1b"]
    assert _answers(q) == [STOPPING]
    assert rig.card_edits[-1][1] == "⚠️ <b>api</b> · Stopped by @alice"
    assert sess.status is Status.IDLE


def test_a_slow_kill_from_status_is_toasted_stopping_and_the_list_redrawn(
        rig, monkeypatch):
    sess = _waiting(rig)
    _slow_chord(rig, monkeypatch)
    q = _tap(rig, 77, f"ststop{sess.turn_key}")
    assert rig.writes.bytes() == [CHORD, CHORD]
    assert _answers(q) == [STOPPING]
    assert q.edit_message_text.await_count == 1  # the list, redrawn
    assert sess.status is Status.IDLE


def test_a_slow_kill_from_the_picker_ends_as_its_outcome(rig, monkeypatch):
    sess = _waiting(rig)
    _slow_chord(rig, monkeypatch)
    q = _tap(rig, 78, f"pstop{sess.turn_key}")
    assert _answers(q) == [STOPPING]
    text = q.edit_message_text.await_args.args[0]
    assert "stopped 2 background agents of" in text


def _refused_late(rig, monkeypatch):
    async def _core(sess, *, by=""):
        await asyncio.sleep(SLOW)
        return StopOutcome(ok=False, label=sess.label, reason="agents_not_stopped",
                           by=by, mode="kill_agents")

    monkeypatch.setattr(rig.bot, "_stop_session_core", _core)


@pytest.mark.parametrize("where", ["card", "status"])
def test_a_refusal_after_the_stopping_toast_is_a_reply(rig, monkeypatch, where):
    sess = _waiting(rig)
    _refused_late(rig, monkeypatch)
    q = (_tap(rig, CARD, "stop") if where == "card"
         else _tap(rig, 77, f"ststop{sess.turn_key}"))
    assert _answers(q) == [STOPPING]
    q.message.reply_text.assert_awaited_once()
    reply = q.message.reply_text.await_args
    assert reply.args[0] == ("The background agents of api could not be stopped. "
                             "Try Stop again.")
    assert reply.kwargs.get("parse_mode") == "HTML"


def test_a_refusal_in_time_is_the_toast(rig, monkeypatch):
    """Control: refused before the toast wait, the refusal is the tap's
    one answer and no reply is sent."""
    _waiting(rig)

    async def _core(sess, *, by=""):
        return StopOutcome(ok=False, label=sess.label, reason="agents_not_stopped",
                           by=by, mode="kill_agents")

    monkeypatch.setattr(rig.bot, "_stop_session_core", _core)
    q = _tap(rig, CARD, "stop")
    assert _answers(q) == ["The background agents of api could not be stopped. "
                           "Try Stop again."]
    q.message.reply_text.assert_not_awaited()


def test_the_stop_survives_the_taps_handler_being_cancelled(rig, monkeypatch):
    """The core runs in its own task: a cancelled tap does not cut the
    Stop short between its presses."""
    sess = _waiting(rig)
    _slow_chord(rig, monkeypatch)
    q = MagicMock()
    q.from_user = MagicMock(id=ALY)
    q.message = MagicMock(message_id=CARD)
    q.answer = AsyncMock()

    async def go():
        task = asyncio.ensure_future(rig.bot._stop_for_tap(
            sess, q, MagicMock(text=lambda *a, **k: STOPPING), by=""))
        await asyncio.sleep(0.1)
        task.cancel()
        for _ in range(40):
            if sess.status is Status.IDLE and rig.writes.bytes() == [CHORD, CHORD]:
                break
            await asyncio.sleep(0.05)

    rig.run_async(go())
    assert rig.writes.bytes() == [CHORD, CHORD]
    assert sess.status is Status.IDLE


def test_the_toast_wait_counts_from_the_tap_not_from_the_stop(rig, monkeypatch):
    """The toast is due ``STOP_TOAST_WAIT`` after the tap came in: checks
    that take a while before the Stop starts never push it past the empty
    ack's bound. Here they take 0.2 s, and a wait counted from the Stop's
    start (0.15 s more) would end after the 0.3 s bound, its toast dropped."""
    monkeypatch.setattr(session_ops, "STOP_TOAST_WAIT", 0.15)
    real_gate = rig.bot._tap_passes_gate

    async def _slow_gate(*a, **k):
        await asyncio.sleep(0.2)
        return await real_gate(*a, **k)

    monkeypatch.setattr(rig.bot, "_tap_passes_gate", _slow_gate)

    async def _slow(_delay):
        await asyncio.sleep(SLOW)

    monkeypatch.setattr(session_ops, "_sleep", _slow)
    q = _tap(rig, CARD, "stop")
    assert _answers(q) == [STOPPING]
    assert rig.sess.status is Status.IDLE


def _gone_late(rig, monkeypatch):
    """The session went away during the Stop: nothing was stopped, and
    there is no refusal to tell (``ok=False``, no reason)."""
    async def _core(sess, *, by=""):
        await asyncio.sleep(SLOW)
        return StopOutcome(ok=False, label=sess.label)

    monkeypatch.setattr(rig.bot, "_stop_session_core", _core)


@pytest.mark.parametrize("where", ["card", "status"])
def test_a_toasted_stop_that_found_nothing_says_so_in_a_reply(
        rig, monkeypatch, where):
    """The tap's one answer went to "Stopping": that it found nothing to
    stop is a reply, not an answer Telegram would drop."""
    sess = _waiting(rig)
    _gone_late(rig, monkeypatch)
    q = (_tap(rig, CARD, "stop") if where == "card"
         else _tap(rig, 77, f"ststop{sess.turn_key}"))
    assert _answers(q) == [STOPPING]
    q.message.reply_text.assert_awaited_once()
    reply = q.message.reply_text.await_args
    assert reply.args[0] == "<b>api</b> is not working."
    assert reply.kwargs.get("parse_mode") == "HTML"


def test_a_stop_that_found_nothing_in_time_is_the_toast(rig, monkeypatch):
    """Control: done before the toast wait, it is the tap's own answer."""
    _waiting(rig)

    async def _core(sess, *, by=""):
        return StopOutcome(ok=False, label=sess.label)

    monkeypatch.setattr(rig.bot, "_stop_session_core", _core)
    q = _tap(rig, CARD, "stop")
    assert _answers(q) == ["api is not working"]
    q.message.reply_text.assert_not_awaited()
