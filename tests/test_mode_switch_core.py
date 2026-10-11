"""SessionOpsMixin._mode_switch_core: the one dispatcher every surface
calls. Ask <-> Auto presses Shift+Tab in place (never while a prompt is on
screen, rolled back when the write fails); to or from Full access is a
restart."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aipager.bot.session_ops import RestartOutcome
from aipager.state import Status, TrackedSession


@pytest.fixture
def world(mk_bot, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    press = AsyncMock(return_value=True)
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", press)
    bot._perms_switch_core = AsyncMock(
        return_value=RestartOutcome(ok=True, reason="restarted", label="jim"))
    return bot, sess, press


@pytest.mark.parametrize("start, target, presses", [
    ("ask", "auto", 3), ("auto", "ask", 1),
], ids=["ask-to-auto", "auto-to-ask"])
def test_in_place_presses_and_records_the_switch(world, run_async, start, target, presses):
    bot, sess, press = world
    sess.perm_mode = start
    out = run_async(bot._mode_switch_core(sess, target))
    assert (out.ok, out.reason, out.in_place, out.mode) == (True, "sent", True, target)
    press.assert_awaited_once_with("claude-jim", presses)
    assert sess.perm_mode == target
    assert sess.mode_switch is not None and sess.mode_switch.target == target
    bot._perms_switch_core.assert_not_awaited()


def test_counts_from_the_mode_claude_code_reported(world, run_async):
    bot, sess, press = world
    sess.perm_mode = "ask"
    sess.reported_mode = "plan"
    run_async(bot._mode_switch_core(sess, "auto"))
    press.assert_awaited_once_with("claude-jim", 1)


def test_works_while_a_turn_runs(world, run_async):
    bot, sess, press = world
    sess.status = Status.BUSY
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert out.reason == "sent"
    press.assert_awaited_once()


def test_refused_while_a_prompt_is_on_screen(world, run_async):
    bot, sess, press = world
    sess.status = Status.INTERACTIVE
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert (out.ok, out.reason, out.in_place) == (False, "dialog_open", True)
    press.assert_not_awaited()
    assert sess.perm_mode == "ask" and sess.mode_switch is None


def test_a_failed_write_rolls_back(world, run_async):
    bot, sess, press = world
    press.return_value = False
    sess.reported_mode = "default"
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert (out.ok, out.reason) == (False, "send_failed")
    assert (sess.perm_mode, sess.reported_mode, sess.mode_switch) == ("ask", "default", None)


@pytest.mark.parametrize("start, target", [("ask", "full"), ("full", "auto")],
                         ids=["into-full", "out-of-full"])
def test_full_access_is_a_restart(world, run_async, start, target):
    bot, sess, press = world
    sess.perm_mode = start
    out = run_async(bot._mode_switch_core(sess, target))
    assert out.reason == "restarted"
    bot._perms_switch_core.assert_awaited_once_with(sess, target)
    press.assert_not_awaited()


@pytest.mark.parametrize("status, reason", [(Status.GONE, "not_live"),
                                            (Status.UNKNOWN, "starting")],
                         ids=["gone", "starting"])
def test_not_a_live_session(world, run_async, status, reason):
    bot, sess, press = world
    sess.status = status
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert (out.ok, out.reason) == (False, reason)
    press.assert_not_awaited()


def test_already_there(world, run_async):
    bot, sess, press = world
    sess.perm_mode = "auto"
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert (out.ok, out.reason) == (True, "already")
    press.assert_not_awaited()


@pytest.mark.parametrize("bad", [True, "", "bypass"], ids=["bool", "empty", "unknown"])
def test_a_bad_target_raises(world, run_async, bad):
    bot, sess, _press = world
    with pytest.raises(ValueError):
        run_async(bot._mode_switch_core(sess, bad))
