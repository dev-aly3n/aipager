"""Roadmap 8.127, holding and releasing: a message that meets a dialog
open in Claude Code's terminal (one no hook tells of, a resume question)
is held, nothing of it typed, and goes to Claude once the dialog closed
and the session settled, in the order sent. 8.124's extra Enter waits
too. Every terminal write is captured (exact bytes)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_jobs, term_dialog
from aipager.bot.transport import PROMPT_DIALOG
from aipager.state import Status
from tests.resume_dialog_kit import NAME, World


@pytest.fixture
def w(mk_bot, monkeypatch, tmp_path, run_async):
    return World(mk_bot, monkeypatch, tmp_path, run_async)


def _open(w, **kw):
    w.waiting(**kw)
    w.scan()
    assert w.sess.terminal_dialog_open()


# ---- the fresh read before typing ---------------------------------------------------

def test_fresh_read_refuses_before_first_character(w, mk_update):
    """The dialog opened after the monitor's last look: the read made
    right before typing finds it, and not one character is typed."""
    w.scan()
    assert not w.sess.terminal_dialog_open()
    w.waiting()
    w.send(mk_update, "hello", msg_id=41)
    assert w.writes == []
    assert [e[0] for e in w.sess.pending_queue] == ["hello"]
    assert w.sess.terminal_dialog_open()
    assert w.sess.dialog_hold is not None and w.sess.dialog_hold["terminal"] is True


def test_no_fresh_read_while_busy(w, mk_update, monkeypatch):
    """A message sent during a turn reads nothing (no resume question
    opens during a turn)."""
    reads = []

    async def fresh(sess, **_kw):
        reads.append(sess.name)
        return None
    monkeypatch.setattr(term_dialog, "fresh_record", fresh)
    w.bot.registry.transition(NAME, Status.BUSY)
    w.waiting()
    w.send(mk_update, "hello", msg_id=41)
    assert reads == []
    assert w.bodies() == ["hello"]
    # The control: the same message to an idle session reads once.
    w.bot.registry.transition(NAME, Status.IDLE)
    w.send(mk_update, "again", msg_id=42)
    assert reads == [NAME]


def test_fresh_read_reads_only_own_record(w, mk_update, monkeypatch):
    """With the session's claude pid known: its own file only, never the
    whole registry."""
    def no_registry(*_a, **_k):
        raise AssertionError("the whole registry was read")
    monkeypatch.setattr(claude_jobs, "read_registry", no_registry)
    w.waiting()
    w.send(mk_update, "hello", msg_id=41)
    assert w.writes == []
    assert w.sess.terminal_dialog_open()


def test_no_whole_registry_read_before_typing(w, mk_update, monkeypatch):
    """With no claude pid known yet, the message is not delayed by a read of
    the whole registry: the monitor's next look finds the client."""
    reads = []
    real = claude_jobs.read_registry

    def counted(*a, **k):
        reads.append(1)
        return real(*a, **k)
    monkeypatch.setattr(claude_jobs, "read_registry", counted)
    w.sess.claude_pid = 0
    w.idle(ago=5)
    w.send(mk_update, "hello", msg_id=41)
    assert reads == []
    assert w.bodies() == ["hello"]


def test_drain_meets_terminal_dialog_holds_again(w):
    """A queued message drained while a terminal dialog is open goes back
    to the head and is held for that dialog."""
    _open(w)
    assert w.sess.queue_prompt("hello", 41, "", 12345)
    w.run_async(w.bot._drain_next_queued(w.sess))
    assert w.writes == []
    assert [e[0] for e in w.sess.pending_queue] == ["hello"]
    assert w.sess.dialog_hold is not None and w.sess.dialog_hold["terminal"] is True


def test_message_right_after_resume_waits_for_dialog(w, mk_update):
    """Sent within the settle wait after a ``--resume`` start, before the
    question draws: the record is read again until it does, and the
    message is held, not typed."""
    w.sess.session_start_at["resume"] = w.clock.wall - 0.5
    w.idle(ago=0.4)

    def draw(n):
        w.clock.advance(term_dialog.FRESH_POLL_SECONDS)
        if n == 3:
            w.waiting(ago=0.1)
    w.on_sleep = draw
    w.send(mk_update, "hello", msg_id=41)
    assert w.writes == []
    assert [e[0] for e in w.sess.pending_queue] == ["hello"]
    assert w.sess.terminal_dialog_open()
    assert w.sleeps[:3] == [term_dialog.FRESH_POLL_SECONDS] * 3


def test_message_after_resume_window_is_typed(w, mk_update):
    w.sess.session_start_at["resume"] = w.clock.wall - term_dialog.RESUME_TYPE_SETTLE_SECONDS - 1
    w.idle(ago=5)
    w.send(mk_update, "hello", msg_id=41)
    assert w.bodies() == ["hello"]
    assert w.sleeps == []
    # Inside the window with no question ever drawn: typed once it is over.
    w.sess.session_start_at["resume"] = w.clock.wall - 1.0
    w.on_sleep = lambda n: w.clock.advance(term_dialog.FRESH_POLL_SECONDS)
    w.bot.registry.transition(NAME, Status.IDLE)
    w.send(mk_update, "again", msg_id=42)
    assert w.bodies() == ["hello", "again"]
    assert 0 < len(w.sleeps) <= int(term_dialog.RESUME_TYPE_SETTLE_SECONDS
                                     / term_dialog.FRESH_POLL_SECONDS) + 1


# ---- held while open, released after the settle -------------------------------------

def test_message_held_while_terminal_dialog_open(w, mk_update, monkeypatch):
    """Held by the message gate itself: nothing is even tried."""
    _open(w)
    tries = []
    real = w.bot._inject_prompt

    async def inject_prompt(*a, **k):
        tries.append(a)
        return await real(*a, **k)
    monkeypatch.setattr(w.bot, "_inject_prompt", inject_prompt)
    w.send(mk_update, "hello", msg_id=41)
    assert tries == []
    assert w.writes == []
    assert [e[0] for e in w.sess.pending_queue] == ["hello"]
    w.bot._react.assert_awaited()
    assert w.sess.dialog_hold["terminal"] is True


@pytest.mark.parametrize("case", ["too_soon", "stale_idle"])
def test_hold_not_released_before_record_idle(w, mk_update, case):
    _open(w, ago=3.0)
    w.send(mk_update, "hello", msg_id=41)
    w.clock.advance(0.5)
    # Closed: a newer idle status (or, stale, one no newer than the opening).
    w.idle(ago=0.1 if case == "too_soon" else 10.0)
    w.scan()
    assert not w.sess.terminal_dialog_open()
    assert w.sess.dialog_hold.get("settling") is True
    w.clock.advance(1.0)
    w.scan()
    assert w.writes == [], "released before the settle"
    if case == "stale_idle":
        w.clock.advance(5.0)
        w.scan()
        assert w.writes == [], "released on an idle status older than the dialog"
        w.idle(ago=0.0)
    else:
        w.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS)
    w.scan()
    assert w.bodies() == ["hello"]


def test_held_messages_released_after_settle_in_order(w, mk_update):
    _open(w)
    w.send(mk_update, "first", msg_id=41)
    w.send(mk_update, "second", msg_id=42)
    assert w.writes == []
    w.idle(ago=0.0)
    w.scan()
    w.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    w.scan()
    assert w.bodies() == ["first", "second"]
    assert w.sess.pending_queue == []
    assert w.sess.dialog_hold is None


def test_message_during_settle_goes_after_held(w, mk_update):
    _open(w)
    w.send(mk_update, "first", msg_id=41)
    w.idle(ago=0.0)
    w.scan()
    assert not w.sess.terminal_dialog_open()
    w.send(mk_update, "second", msg_id=42)
    assert w.writes == [], "a message typed while the held one waits"
    assert [e[0] for e in w.sess.pending_queue] == ["first", "second"]
    w.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    w.scan()
    assert w.bodies() == ["first", "second"]


def test_settling_hold_is_not_cleared(w, mk_update):
    _open(w)
    w.send(mk_update, "first", msg_id=41)
    w.idle(ago=0.0)
    w.scan()
    hold = w.sess.dialog_hold
    assert hold.get("settling") is True
    hold["closed"] = "answered"     # even so: not while it settles
    w.run_async(w.bot._settle_dialog_hold(w.sess))
    assert w.sess.dialog_hold is hold
    assert w.writes == []
    assert [e[0] for e in w.sess.pending_queue] == ["first"]


def test_settle_backstop_releases(w, mk_update):
    """No record or hook ever says the session settled: the held message
    goes after AFTER_ANSWER_MAX_WAIT all the same."""
    path = w.record(status="waiting", waitingFor="dialog open",
                    statusUpdatedAt=int((w.clock.wall - 1) * 1000))
    w.scan()
    w.send(mk_update, "hello", msg_id=41)
    path.unlink()                   # closed, and no record after it
    w.scan()
    assert not w.sess.terminal_dialog_open()
    w.clock.advance(term_dialog.AFTER_ANSWER_MAX_WAIT - 5)
    w.scan()
    assert w.writes == []
    w.clock.advance(10)
    w.scan()
    assert w.bodies() == ["hello"]


def test_held_back_enter_waits_for_terminal_dialog(w, tmp_path):
    note = tmp_path / "note.json"
    note.write_text("{}")
    assert w.bot._held_back_verdict(w.sess, note) == "press"
    w.sess.term_dialog = {"epoch": 1, "pid": 4100, "kind": "other", "seen_at": 1.0}
    assert w.bot._held_back_verdict(w.sess, note) == "wait"
    w.sess.term_dialog = None
    w.sess.term_dialog_settle = {"since": 1.0, "after_wall": 1.0, "expect": ""}
    assert w.bot._held_back_verdict(w.sess, note) == "wait"
    assert w.bot._held_back_verdict(w.sess, note, releasing=True) == "wait"
    w.sess.term_dialog_settle = None
    assert w.bot._held_back_verdict(w.sess, note) == "press"


def test_held_back_enter_is_never_pressed_while_open(w):
    """The second Enter itself goes through the probe: nothing written."""
    from aipager.dtach import inject
    _open(w)
    assert w.run_async(inject.press_enter_gated(NAME)) is inject.DIALOG_BEFORE_TYPING
    assert w.writes == []


@pytest.mark.parametrize("expect", ["clear", ""])
def test_clear_gone_keeps_settling_hold(w, mk_update, expect):
    """The /clear after "Start a new conversation" ends the conversation on
    purpose: the hold waits for the new one. Any other end marks it ended."""
    _open(w)
    w.send(mk_update, "hello", msg_id=41)
    w.idle(ago=0.0)
    w.scan()
    hold = w.sess.dialog_hold
    w.sess.term_dialog_settle["expect"] = expect
    w.bot.registry.transition(NAME, Status.GONE)
    if expect == "clear":
        assert hold.get("settling") is True and "closed" not in hold
        assert w.sess.term_dialog_settle is not None
    else:
        assert hold.get("closed") == "ended" and "settling" not in hold
        assert w.sess.term_dialog_settle is None


def test_relaunch_clears_terminal_dialog(w):
    _open(w)
    w.sess.term_dialog["card"] = (-1001, 950)
    w.sess.term_dialog["card_kind"] = "card"
    w.bot._resume_target = AsyncMock(return_value=("x", object()))
    w.bot._bg_refused_restart = MagicMock(return_value="refused")
    out = w.run_async(w.bot._kill_and_relaunch_in_flight(
        w.sess, target_mode="ask", interrupt_first=False))
    assert out == "refused"
    assert not w.sess.terminal_dialog_open()
    assert w.sess.term_dialog_closing[-1]["outcome"] == "restarted"


def test_inject_prompt_says_dialog_without_a_note(w, monkeypatch):
    """The refusal comes before the message's note is written."""
    from aipager.policy_snapshot import list_outstanding_notes
    _open(w)
    ok = w.run_async(w.bot._inject_prompt(w.sess, "hello"))
    assert ok is PROMPT_DIALOG
    assert list_outstanding_notes(NAME) == []


# ---- review rev-iter1-001: only the scan ends a settle -------------------------------

def test_typing_between_close_and_scan_does_not_strand_the_hold(w, mk_update):
    """Another typing path (the Compact button here) runs after the close's
    settle time but before the scan: its look before typing must not take
    the settle, or the held message would stay held for good."""
    _open(w)
    w.send(mk_update, "first", msg_id=41)
    w.idle(ago=0.0)
    w.scan()
    assert w.sess.dialog_hold.get("settling") is True
    w.clock.advance(term_dialog.ANSWER_SETTLE_SECONDS + 0.5)
    out = w.run_async(w.bot._compact_session_core(w.sess))
    assert out.reason == "sent"
    w.bot.registry.transition(NAME, Status.IDLE)
    w.scan()
    assert "first" in w.bodies()
    assert w.sess.pending_queue == []
    assert w.sess.dialog_hold is None
    assert w.sess.term_dialog_settle is None
