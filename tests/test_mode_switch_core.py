"""SessionOpsMixin._mode_switch_core: the one dispatcher every surface
calls. Ask <-> Auto presses Shift+Tab in place (never while a prompt is on
screen, rolled back when the write fails); to or from Full access is a
restart."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


# ---- "already" to a narrower request is recorded (review rev-iter1-001) ----

def test_already_in_a_narrower_mode_records_it(world, run_async):
    # Someone in the terminal pressed Shift+Tab: the session already is in
    # the mode asked for, but a restart would launch the wider perm_mode.
    bot, sess, press = world
    sess.perm_mode = "auto"
    sess.reported_mode = "default"
    bot.registry.mark_dirty = dirty = MagicMock()
    out = run_async(bot._mode_switch_core(sess, "ask"))
    assert (out.ok, out.reason) == (True, "already")
    assert sess.perm_mode == "ask"
    dirty.assert_called()
    press.assert_not_awaited()
    bot._perms_switch_core.assert_not_awaited()


# ---- a Full access launch narrowed in the terminal restarts (rev-iter2-001) ----

@pytest.mark.parametrize("reported, target", [("default", "ask"), ("auto", "auto")],
                         ids=["full-launch-now-manual", "full-launch-now-auto"])
def test_full_launch_in_a_narrower_mode_restarts_into_it(world, run_async, reported, target):
    # The live process was started with the bypass flag: bypass is in its
    # Shift+Tab cycle. "Already" would leave it running; the relaunch into
    # the target drops the flag.
    bot, sess, press = world
    sess.perm_mode = "full"
    sess.reported_mode = reported
    out = run_async(bot._mode_switch_core(sess, target))
    assert out.reason == "restarted"
    bot._perms_switch_core.assert_awaited_once_with(sess, target)
    press.assert_not_awaited()


def test_full_launch_reported_manual_ask_then_auto_never_presses(world, run_async):
    # The reviewer's sequence: /mode ask, then /mode auto, on a session
    # launched in Full access that reports Manual. While that process is
    # the live one, neither may press Shift+Tab (three presses from
    # Manual land in bypassPermissions); each is a restart into its target.
    bot, sess, press = world
    sess.perm_mode = "full"
    sess.reported_mode = "default"
    first = run_async(bot._mode_switch_core(sess, "ask"))
    second = run_async(bot._mode_switch_core(sess, "auto"))
    assert (first.reason, second.reason) == ("restarted", "restarted")
    press.assert_not_awaited()
    assert [c.args[1] for c in bot._perms_switch_core.await_args_list] == ["ask", "auto"]
    assert sess.perm_mode == "full" and sess.mode_switch is None


# ---- a process that reported bypass since its launch (rev-iter2-002) ----------

def test_a_bypass_report_makes_every_switch_restart(world, run_async):
    # A CLI session started with the bypass flag: perm_mode stays "ask".
    # The terminal user cycled it to Manual; Auto by presses would pass
    # through bypassPermissions again, so it restarts instead.
    bot, sess, press = world
    sess.perm_mode = "ask"
    assert sess.note_reported_mode("bypassPermissions", 0.0) == "recorded"
    assert sess.note_reported_mode("default", 0.0) == "recorded"
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert out.reason == "restarted"
    bot._perms_switch_core.assert_awaited_once_with(sess, "auto")
    press.assert_not_awaited()
    assert sess.perm_mode == "ask"


def test_already_never_widens(world, run_async):
    bot, sess, press = world
    sess.perm_mode = "ask"
    sess.reported_mode = "auto"
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert out.reason == "already"
    assert sess.perm_mode == "ask"


# ---- the settle window runs from the last key (review rev-iter1-006) ----

def test_the_switch_is_stamped_after_the_presses(world, run_async):
    bot, sess, press = world

    async def _slow_press(name, count):
        # The presses waited for the write lock: the stamp taken before
        # them is old by now.
        sess.mode_switch.sent_at = -100.0
        return True

    press.side_effect = _slow_press
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert out.reason == "sent"
    assert sess.mode_switch.sent_at > 0


# ---- a failed write rolls back only its own switch (review rev-iter1-007) ----

def test_a_failed_write_leaves_a_newer_state_alone(world, run_async):
    bot, sess, press = world
    sess.perm_mode = "ask"

    async def _relaunched_meanwhile(name, count):
        # A Full access restart ran while the presses waited for the lock.
        sess.perm_mode = "full"
        sess.reset_mode_tracking()
        return False

    press.side_effect = _relaunched_meanwhile
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert (out.ok, out.reason) == (False, "send_failed")
    assert (sess.perm_mode, sess.mode_switch) == ("full", None)
