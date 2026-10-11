"""aipager/bot/mode_switch.py: confirming an in-place Ask <-> Auto switch
from the mode the next main-agent hook reports, with one retry counted
from that mode, and showing the result where "switch sent" was shown."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from aipager.bot import mode_switch
from aipager.state import ModeSwitch, SessionRegistry, Status


class _Bot:
    def __init__(self, with_app=True):
        self._app = SimpleNamespace(bot=object()) if with_app else None
        self.cards: list = []

    def _mode_card(self, chat_id, sess, note=""):
        self.cards.append((chat_id, note))
        return f"CARD {note}", "KB"


@pytest.fixture
def world(monkeypatch):
    registry = SessionRegistry()
    sess = registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.chat_id = 7
    presses: list = []
    press_ok = {"ok": True}

    async def _press(name, count):
        presses.append((name, count))
        return press_ok["ok"]

    edits: list = []
    sends: list = []

    async def _edit(bot, *, chat_id, message_id, text, parse_mode=None, reply_markup=None):
        edits.append((chat_id, message_id, text, reply_markup))

    async def _send(bot, *, chat_id, text, parse_mode=None, **kw):
        sends.append((chat_id, text))

    monkeypatch.setattr(mode_switch.inject, "press_mode_cycle", _press)
    monkeypatch.setattr(mode_switch, "edit_text_at", _edit)
    monkeypatch.setattr(mode_switch, "send_text", _send)
    monkeypatch.setattr(mode_switch, "resolve_chat_id_int", lambda s: 7)
    return SimpleNamespace(sess=sess, presses=presses, press_ok=press_ok, edits=edits,
                           sends=sends)


def _pending(target="auto", **kw):
    return ModeSwitch(target=target, presses=3, sent_at=time.monotonic() - 5, **kw)


def _report(bot, sess, raw, run_async, dialog=False):
    """Record *raw* as the hook would and run on_reported, draining the
    tasks it starts in the same loop."""
    sess.reported_mode = raw

    async def go():
        await mode_switch.on_reported(bot, sess, {"dialog": dialog})
        while mode_switch._TASKS:
            await asyncio.gather(*list(mode_switch._TASKS))

    run_async(go())


def test_the_target_reported_confirms(world, run_async):
    bot, sess = _Bot(), world.sess
    sess.mode_switch = _pending(reply=(7, 11, "mode_card"))
    _report(bot, sess, "auto", run_async)
    assert sess.mode_switch is None
    assert world.presses == []
    (chat, msg, text, kb), = world.edits
    assert (chat, msg, kb) == (7, 11, "KB")
    assert "is now in" in text and "Auto mode" in text
    assert world.sends == []


def test_a_confirm_with_nowhere_to_edit_sends_nothing(world, run_async):
    bot, sess = _Bot(), world.sess
    sess.mode_switch = _pending()
    _report(bot, sess, "auto", run_async)
    assert world.edits == [] and world.sends == []


def test_a_wrong_mode_retries_once_counted_from_it(world, run_async):
    bot, sess = _Bot(), world.sess
    sess.mode_switch = pending = _pending()
    _report(bot, sess, "acceptEdits", run_async)
    assert world.presses == [("claude-jim", 2)]
    assert sess.mode_switch is pending and pending.retried and pending.presses == 2
    assert sess.reported_mode == ""  # the next report decides
    assert world.edits == [] and world.sends == []


@pytest.mark.parametrize("dialog_flag, dialog_open", [(True, False), (False, True)],
                         ids=["dialog-event", "dialog-on-screen"])
def test_no_retry_while_a_dialog_shows(world, run_async, monkeypatch, dialog_flag,
                                       dialog_open):
    bot, sess = _Bot(), world.sess
    monkeypatch.setattr(type(sess), "dialog_is_open", lambda self: dialog_open)
    sess.mode_switch = pending = _pending()
    _report(bot, sess, "default", run_async, dialog=dialog_flag)
    assert world.presses == []
    assert sess.mode_switch is pending and not pending.retried


def test_still_wrong_after_the_retry_is_reported_honestly(world, run_async):
    bot, sess = _Bot(), world.sess
    sess.mode_switch = _pending(target="ask", retried=True, reply=(7, 11, "mode_card"))
    _report(bot, sess, "plan", run_async)
    assert sess.mode_switch is None
    (_c, _m, text, _kb), = world.edits
    assert "didn't switch" in text
    assert sess.auto_unavailable is False


def test_auto_asked_and_still_ask_means_auto_unavailable(world, run_async):
    bot, sess = _Bot(), world.sess
    sess.mode_switch = _pending(retried=True)
    _report(bot, sess, "default", run_async)
    assert sess.mode_switch is None
    assert sess.auto_unavailable is True
    (chat, text), = world.sends  # no reply to edit: a failure is sent
    assert chat == 7 and "isn't available" in text


def test_retry_without_auto_in_the_cycle(world, run_async):
    bot, sess = _Bot(), world.sess
    sess.auto_unavailable = True
    sess.mode_switch = _pending(target="ask")
    _report(bot, sess, "plan", run_async)
    assert world.presses == [("claude-jim", 1)]


def test_unreachable_by_shift_tab_settles_at_once(world, run_async):
    bot, sess = _Bot(), world.sess
    sess.mode_switch = _pending(target="ask")
    _report(bot, sess, "bypassPermissions", run_async)
    assert world.presses == []
    assert sess.mode_switch is None
    (_chat, text), = world.sends
    assert "didn't switch" in text


def test_a_failed_retry_write_settles_unconfirmed(world, run_async):
    bot, sess = _Bot(), world.sess
    world.press_ok["ok"] = False
    sess.mode_switch = _pending()
    _report(bot, sess, "default", run_async)
    assert sess.mode_switch is None
    assert sess.reported_mode == "default"
    (_chat, text), = world.sends
    assert "didn't switch" in text


def test_nothing_waits_nothing_happens(world, run_async):
    bot, sess = _Bot(), world.sess
    _report(bot, sess, "default", run_async)
    assert world.presses == [] and world.edits == [] and world.sends == []


def test_a_failed_edit_falls_back_to_a_message_for_a_failure(world, run_async, monkeypatch):
    bot, sess = _Bot(), world.sess

    async def _broken(*a, **k):
        raise RuntimeError("gone")

    monkeypatch.setattr(mode_switch, "edit_text_at", _broken)
    sess.mode_switch = _pending(target="ask", retried=True, reply=(7, 11, "mode_card"))
    _report(bot, sess, "plan", run_async)
    (_chat, text), = world.sends
    assert "didn't switch" in text


def test_attach_reply_only_while_a_switch_waits(world):
    sess = world.sess
    mode_switch.attach_reply(sess, 7, 11, "mode_card", "@bob")
    assert sess.mode_switch is None
    sess.mode_switch = pending = _pending()
    mode_switch.attach_reply(sess, 7, None, "mode_card")
    assert pending.reply is None
    mode_switch.attach_reply(sess, "7", "11", "ready", "@bob")
    assert pending.reply == (7, 11, "ready") and pending.by == "@bob"


# ---- the retry presses only into its own, live switch (review rev-iter1-007) ----

def _run_retry(sess, pending, run_async):
    run_async(mode_switch._retry(_Bot(), sess, pending, 2, "acceptEdits"))


def test_retry_presses_while_its_switch_is_current(world, run_async):
    sess = world.sess
    sess.status = Status.BUSY
    sess.mode_switch = pending = _pending(retried=True)
    _run_retry(sess, pending, run_async)
    assert world.presses == [("claude-jim", 2)]


def test_retry_presses_nothing_for_a_superseded_switch(world, run_async):
    sess = world.sess
    sess.status = Status.IDLE
    pending = _pending(retried=True)
    sess.mode_switch = newer = _pending(target="ask")
    _run_retry(sess, pending, run_async)
    assert world.presses == []
    assert sess.mode_switch is newer and not newer.retried


@pytest.mark.parametrize("state", ["relaunching", "gone"])
def test_retry_presses_nothing_into_a_relaunch_or_an_ended_session(world, run_async, state):
    sess = world.sess
    sess.status = Status.GONE if state == "gone" else Status.IDLE
    sess.relaunch_in_flight = state == "relaunching"
    sess.mode_switch = pending = _pending(retried=True)
    _run_retry(sess, pending, run_async)
    assert world.presses == []


def test_retry_waits_when_a_dialog_opened_meanwhile(world, run_async):
    sess = world.sess
    sess.status = Status.INTERACTIVE
    sess.mode_switch = pending = _pending(retried=True)
    _run_retry(sess, pending, run_async)
    assert world.presses == []
    # Not spent: the next hook may still retry, from the mode reported.
    assert sess.mode_switch is pending and not pending.retried
    assert sess.reported_mode == "acceptEdits"


def test_retry_is_stamped_after_its_presses(world, run_async, monkeypatch):
    sess = world.sess
    sess.status = Status.IDLE
    sess.mode_switch = pending = _pending(retried=True)

    async def _slow_press(name, count):
        pending.sent_at = -100.0  # the presses waited for the write lock
        return True

    monkeypatch.setattr(mode_switch.inject, "press_mode_cycle", _slow_press)
    _run_retry(sess, pending, run_async)
    assert pending.sent_at > 0
