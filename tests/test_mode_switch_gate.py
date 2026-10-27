"""Roadmap 8.146: the in-place mode switch never presses Shift+Tab into a
permission dialog.

In Claude Code's file-edit permission dialog Shift+Tab means "allow all
edits during this session" (2.1.311, live: the write ran and the session
went on in acceptEdits). Claude Code draws the dialog the moment the tool's
PreToolUse hooks return, before aipager hears of it, so
``inject.press_mode_cycle`` goes through the typing gate as a prompt does
(``tests/test_typing_gate.py``): the marker holds tool calls while it
presses, a call that passed just before is waited out, and the whole gate
is looked at before every press. Its callers refuse the switch as "a prompt
is open" and never claim the target mode.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n
from aipager.bot import mode_switch
from aipager.bot.handlers import MODE_DIALOG_OPEN
from aipager.bot.session_ops import RestartOutcome
from aipager.dtach import inject, typing_gate
from aipager.state import ModeSwitch, SessionRegistry, Status, TrackedSession

NAME = "claude-modegate"
BACKTAB = b"\x1b[Z"


def _marker(tmp_path):
    # conftest redirects gate_dir into tmp_path whatever the base is.
    return typing_gate.gate_dir(str(tmp_path), NAME) / "typing"


def _marker_live(tmp_path) -> bool:
    return typing_gate._marker_live(typing_gate.gate_dir(str(tmp_path), NAME), time.time())


class _Terminal:
    """Every write, whether the typing marker was live during it, and the
    gaps between them."""

    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.writes: list[bytes] = []
        self.marker_live: list[bool] = []
        self.gaps: list[float] = []
        self.fail_at = 0

    async def run(self, args, stdin=b"", timeout=5):
        self.writes.append(bytes(stdin))
        self.marker_live.append(_marker_live(self.tmp_path))
        return len(self.writes) != self.fail_at, ""


@pytest.fixture
def term(tmp_path, monkeypatch):
    t = _Terminal(tmp_path)
    monkeypatch.setattr(inject, "_run", t.run)

    async def gap(secs):
        t.gaps.append(secs)
    monkeypatch.setattr(inject, "_key_gap_sleep", gap)
    return t


@pytest.fixture
def gate_waits(monkeypatch):
    waits: list[float] = []

    async def record(secs):
        waits.append(secs)
    monkeypatch.setattr(inject, "_gate_sleep", record)
    return waits


# ── inject.press_mode_cycle ──────────────────────────────────────────────


def test_the_presses_go_in_with_the_marker_up_and_it_stays_a_moment_after(term, tmp_path,
                                                                          run_async):
    async def scenario():
        ok = await inject.press_mode_cycle(NAME, 3)
        right_after = _marker_live(tmp_path)
        await asyncio.sleep(inject.GATE_AFTER_ENTER_SECONDS + 0.1)
        return ok, right_after, _marker(tmp_path).exists()

    ok, right_after, later = run_async(scenario())
    assert ok is True
    assert term.writes == [BACKTAB] * 3
    assert term.marker_live == [True, True, True]
    assert right_after is True
    assert later is False
    assert inject._GATE_HOLDS == {}


def test_a_dialog_open_before_the_first_press_gets_nothing(term, tmp_path, run_async):
    inject.set_dialog_probe(lambda name: name == NAME)
    before = inject.last_write_at(NAME)
    result = run_async(inject.press_mode_cycle(NAME, 3))
    assert result is inject.DIALOG_BEFORE_TYPING
    assert not result
    assert term.writes == []
    assert inject.last_write_at(NAME) == before      # nothing counted as written
    assert not _marker(tmp_path).exists()
    assert inject._GATE_HOLDS == {}


def test_a_probe_that_raises_counts_as_a_dialog(term, run_async):
    def broken(_name):
        raise RuntimeError("boom")
    inject.set_dialog_probe(broken)
    assert run_async(inject.press_mode_cycle(NAME, 1)) is inject.DIALOG_BEFORE_TYPING
    assert term.writes == []


def test_a_dialog_that_opened_while_the_lock_was_awaited_gets_nothing(term, run_async):
    looks = []

    def probe(_name):
        looks.append(1)
        return len(looks) > 1          # open from the look under the lock on
    inject.set_dialog_probe(probe)
    before = inject.last_write_at(NAME)
    assert run_async(inject.press_mode_cycle(NAME, 3)) is inject.DIALOG_BEFORE_TYPING
    assert term.writes == []
    assert inject.last_write_at(NAME) == before


def test_the_wait_for_a_passed_call_never_holds_the_write_lock(term, monkeypatch, run_async):
    """A Stop's Escape is not kept waiting behind the gate's first wait: it
    goes in while the presses wait for a tool call that has just passed."""
    answers = [1, 1, 1, 0, 0]
    monkeypatch.setattr(typing_gate, "fresh_passes",
                        lambda base, session, window: answers.pop(0) if answers else 0)

    async def gate_sleep(_secs):
        await asyncio.sleep(0.01)
    monkeypatch.setattr(inject, "_gate_sleep", gate_sleep)

    async def scenario():
        press = asyncio.ensure_future(inject.press_mode_cycle(NAME, 1))
        await asyncio.sleep(0)
        await inject.send_keys(NAME, "Escape")
        return await press

    assert run_async(scenario()) is True
    assert term.writes == [b"\x1b", BACKTAB]


@pytest.mark.parametrize("after", [1, 2], ids=["after-one", "after-two"])
def test_a_dialog_opening_between_presses_stops_the_rest(term, tmp_path, run_async, after):
    inject.set_dialog_probe(lambda _name: len(term.writes) >= after)
    result = run_async(inject.press_mode_cycle(NAME, 3))
    assert result is inject.DIALOG_WHILE_TYPING
    assert not result
    assert term.writes == [BACKTAB] * after
    # Stopped: the marker goes at once (no moment held after a last key).
    assert not _marker(tmp_path).exists()
    assert inject._GATE_HOLDS == {}


def test_it_waits_out_a_tool_call_that_passed_just_before(term, gate_waits, monkeypatch,
                                                          tmp_path, run_async):
    answers = [1, 1, 0]
    held = []

    def fresh(base, session, window):
        held.append(_marker_live(tmp_path))
        return answers.pop(0) if answers else 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    assert run_async(inject.press_mode_cycle(NAME, 1)) is True
    assert gate_waits == [inject.GATE_POLL_SECONDS, inject.GATE_POLL_SECONDS]
    assert term.writes == [BACKTAB]
    assert held and all(held)


def test_a_call_that_passes_at_its_cap_between_presses_is_waited_out(
        term, gate_waits, monkeypatch, run_async):
    """A tool call held at the marker longer than its hook's cap goes on
    with a fresh stamp while the keys are pressed: the next press waits."""
    looks: list[int] = []

    def fresh(base, session, window):
        looks.append(len(term.writes))
        return 1 if len(term.writes) == 1 and len(gate_waits) < 2 else 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    assert run_async(inject.press_mode_cycle(NAME, 3)) is True
    assert term.writes == [BACKTAB] * 3
    assert gate_waits == [inject.GATE_POLL_SECONDS] * 2
    # The waits came after the first press and before the second.
    assert looks.count(1) >= 3


def test_a_call_that_passes_between_presses_and_draws_its_dialog_stops_them(
        term, gate_waits, monkeypatch, run_async):
    monkeypatch.setattr(typing_gate, "fresh_passes",
                        lambda base, session, window: 1 if len(term.writes) == 1 else 0)
    inject.set_dialog_probe(lambda _name: len(gate_waits) >= 2)
    assert run_async(inject.press_mode_cycle(NAME, 3)) is inject.DIALOG_WHILE_TYPING
    assert term.writes == [BACKTAB]


def test_every_look_before_a_press_renews_the_marker(term, monkeypatch, run_async):
    begins = []
    real_begin = typing_gate.begin

    def begin(base_dir, session, **kw):
        begins.append(session)
        return real_begin(base_dir, session, **kw)
    monkeypatch.setattr(typing_gate, "begin", begin)
    assert run_async(inject.press_mode_cycle(NAME, 3)) is True
    # The marker begun before the first look, then renewed before each press.
    assert len(begins) == 4


def test_the_wait_for_a_passed_call_is_bounded(term, monkeypatch, run_async):
    monkeypatch.setattr(inject, "GATE_WAIT_LIMIT_SECONDS", 0.05)
    monkeypatch.setattr(inject, "GATE_WAIT_HARD_LIMIT", 0.08)
    monkeypatch.setattr(inject, "GATE_POLL_SECONDS", 0.01)
    looks = []

    def fresh(base, session, window):
        # Fresh far longer than the limits allow, then quiet: a missing
        # limit fails this test instead of hanging it.
        looks.append(window)
        return 1 if len(looks) < 2000 else 0
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)
    started = time.monotonic()
    assert run_async(inject.press_mode_cycle(NAME, 2)) is True
    assert time.monotonic() - started < 2.0
    assert len(looks) < 1000
    assert term.writes == [BACKTAB] * 2


def test_the_gaps_between_presses_are_kept(term, run_async):
    from aipager import perm_modes
    assert run_async(inject.press_mode_cycle(NAME, 3)) is True
    assert term.gaps == [perm_modes.MODE_PRESS_GAP] * 2


def test_a_failed_press_lets_the_marker_go_at_once(term, tmp_path, run_async):
    term.fail_at = 2
    assert run_async(inject.press_mode_cycle(NAME, 3)) is False
    assert term.writes == [BACKTAB] * 2
    assert not _marker(tmp_path).exists()
    assert inject._GATE_HOLDS == {}


def test_a_switch_no_longer_wanted_gets_no_press(term, tmp_path, run_async):
    assert run_async(inject.press_mode_cycle(NAME, 3, still_wanted=lambda: False)) is False
    assert term.writes == []
    assert not _marker(tmp_path).exists()


def test_a_switch_that_stops_being_wanted_stops_its_presses(term, tmp_path, run_async):
    counted = []
    result = run_async(inject.press_mode_cycle(
        NAME, 3, still_wanted=lambda: len(term.writes) < 2,
        on_press=lambda: counted.append(1)))
    assert result is False
    assert term.writes == [BACKTAB] * 2
    assert len(counted) == 2
    assert not _marker(tmp_path).exists()


def test_each_press_that_went_in_is_told(term, run_async):
    counted = []
    inject.set_dialog_probe(lambda _name: len(term.writes) >= 2)
    result = run_async(inject.press_mode_cycle(NAME, 3, on_press=lambda: counted.append(1)))
    assert result is inject.DIALOG_WHILE_TYPING
    assert len(counted) == 2


@pytest.mark.parametrize("start, count, auto, reached", [
    ("default", 1, True, "acceptEdits"), ("default", 2, True, "plan"),
    ("default", 3, True, "auto"), ("auto", 1, True, "default"),
    ("manual", 1, True, "acceptEdits"), ("plan", 1, False, "default"),
    ("bypassPermissions", 1, True, ""),
], ids=["d1", "d2", "d3", "a1", "manual", "no-auto", "unknown"])
def test_after_presses_follows_the_cycle(start, count, auto, reached):
    from aipager import perm_modes
    assert perm_modes.after_presses(start, count, auto_in_cycle=auto) == reached


def test_overlapping_with_a_prompt_keeps_the_marker_until_both_are_done(term, tmp_path,
                                                                        run_async):
    async def scenario():
        await asyncio.gather(inject.press_mode_cycle(NAME, 2),
                             inject.send_text_and_enter(NAME, "hello"))
        await asyncio.sleep(inject.GATE_AFTER_ENTER_SECONDS + 0.1)
        return _marker(tmp_path).exists()

    assert run_async(scenario()) is False
    assert all(term.marker_live)
    assert sorted(term.writes) == sorted([BACKTAB, BACKTAB, b"hello", b"\r"])
    assert inject._GATE_HOLDS == {}


# ── SessionOpsMixin._mode_switch_core ────────────────────────────────────


@pytest.fixture
def core(mk_bot, monkeypatch):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.perm_mode = "ask"
    sess.reported_mode = "default"
    bot.registry._sessions["claude-jim"] = sess
    seen = {}

    async def press(name, count, *, still_wanted=None, on_press=None):
        seen["pressing"] = sess.mode_switch.pressing if sess.mode_switch else None
        seen["shown"] = sess.shown_mode()
        for _ in range(seen.get("went_in", 0)):
            on_press()
        return seen.get("result", True)
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", press)
    return bot, sess, seen


def test_a_dialog_before_the_presses_is_refused_as_a_prompt_open(core, run_async):
    bot, sess, seen = core
    seen["result"] = inject.DIALOG_BEFORE_TYPING
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert (out.ok, out.reason, out.in_place, out.mode) == (False, "dialog_open", True, "auto")
    # Nothing was pressed: everything as before.
    assert (sess.perm_mode, sess.reported_mode, sess.mode_switch) == ("ask", "default", None)


@pytest.mark.parametrize("went_in, reached", [(1, "acceptEdits"), (2, "plan")],
                         ids=["one", "two"])
def test_a_dialog_between_presses_never_claims_the_switch(core, run_async, went_in, reached):
    bot, sess, seen = core
    seen["result"] = inject.DIALOG_WHILE_TYPING
    seen["went_in"] = went_in
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert (out.ok, out.reason, out.in_place) == (False, "dialog_open", True)
    # Not the target, no switch waiting, and shown in the mode the presses
    # that went in reached (never Ask: Claude Code is not in it any more)
    # until the next hook reports one.
    assert sess.perm_mode == "ask"
    assert sess.mode_switch is None
    assert sess.reported_mode == reached
    assert sess.shown_mode() == ("other", reached)
    # The next hook is recorded at once (no switch is waiting to settle).
    assert sess.note_reported_mode("acceptEdits", time.monotonic()) == "recorded"


def test_the_core_stops_the_presses_for_a_relaunch_begun_during_the_gate_wait(
        mk_bot, term, monkeypatch, run_async):
    """The gate can wait seconds before the presses: a relaunch begun then
    gets no stray Shift+Tab (review rev-iter1-001)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-jim"] = sess
    answers = [1, 0]
    monkeypatch.setattr(typing_gate, "fresh_passes",
                        lambda base, session, window: answers.pop(0) if answers else 0)

    async def gate_sleep(_secs):
        sess.relaunch_in_flight = True
    monkeypatch.setattr(inject, "_gate_sleep", gate_sleep)
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert term.writes == []
    assert out.reason != "sent"


def test_the_core_stops_the_presses_for_a_switch_reset_meanwhile(mk_bot, term, monkeypatch,
                                                                 run_async):
    """A relaunch's reset (mode tracking starts over) during the gate wait:
    the switch is gone, so are its presses."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-jim"] = sess
    answers = [1, 0]
    monkeypatch.setattr(typing_gate, "fresh_passes",
                        lambda base, session, window: answers.pop(0) if answers else 0)

    async def gate_sleep(_secs):
        sess.reset_mode_tracking()
    monkeypatch.setattr(inject, "_gate_sleep", gate_sleep)
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert term.writes == []
    assert out.reason != "sent"
    assert sess.mode_switch is None


def test_a_second_switch_waits_for_the_first_ones_presses(mk_bot, term, monkeypatch,
                                                          run_async):
    """/mode auto held at the typing gate, then /mode ask: the second
    counts from where the first leaves the session, so the presses add up
    to the mode asked for last (review rev-iter2-001: the first's presses
    were dropped and the session ended in acceptEdits)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.perm_mode = "ask"
    sess.reported_mode = "default"
    bot.registry._sessions["claude-jim"] = sess
    looks = [0]

    def fresh(base, session, window):
        looks[0] += 1
        return 1 if looks[0] <= 3 else 0     # a call just passed: the first waits
    monkeypatch.setattr(typing_gate, "fresh_passes", fresh)

    async def gate_sleep(_secs):
        await asyncio.sleep(0.005)
    monkeypatch.setattr(inject, "_gate_sleep", gate_sleep)

    async def scenario():
        first = asyncio.ensure_future(bot._mode_switch_core(sess, "auto"))
        await asyncio.sleep(0.002)
        second = await bot._mode_switch_core(sess, "ask")
        return await first, second

    first, second = run_async(scenario())
    assert (first.reason, second.reason) == ("sent", "sent")
    # default -> acceptEdits -> plan -> auto, then auto -> default.
    assert term.writes == [BACKTAB] * 4
    assert sess.perm_mode == "ask"
    assert sess.mode_switch is not None and sess.mode_switch.target == "ask"
    assert sess.mode_switch.presses == 1


@pytest.mark.parametrize("gone", ["ended", "relaunching"])
def test_the_core_stops_the_presses_between_presses_when_the_session_goes(
        mk_bot, term, monkeypatch, run_async, gone):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.perm_mode = "ask"
    bot.registry._sessions["claude-jim"] = sess

    async def gap(_secs):
        if gone == "ended":
            sess.status = Status.GONE
        else:
            sess.relaunch_in_flight = True
    monkeypatch.setattr(inject, "_key_gap_sleep", gap)
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert term.writes == [BACKTAB]
    assert out.reason != "sent"
    # One press went in: shown one step on, no switch left waiting.
    assert sess.mode_switch is None
    assert sess.reported_mode == "acceptEdits"
    assert sess.perm_mode == "ask"


def test_a_restart_switch_runs_outside_the_switch_lock(mk_bot, run_async):
    """Into or out of Full access is a restart, which can take seconds: it
    never holds up an in-place switch's lock (nor a retry waiting on it)."""
    from aipager.bot import mode_switch as ms
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    held = []

    async def restart(s, target, **kw):
        held.append(ms.switch_lock(s.name).locked())
        return RestartOutcome(ok=True, reason="restarted", label="jim")
    bot._perms_switch_core = restart
    out = run_async(bot._mode_switch_core(sess, "full"))
    assert out.reason == "restarted"
    assert held == [False]


def test_a_dialog_result_keeps_a_newer_switch(core, run_async, monkeypatch):
    """A relaunch (or anything else that set newer state) while the
    presses waited: the stopped switch leaves that state alone."""
    bot, sess, _seen = core
    newer = ModeSwitch(target="ask", presses=1, sent_at=time.monotonic())

    async def press(name, count, **_kw):
        sess.mode_switch = newer
        sess.perm_mode = "auto"
        return inject.DIALOG_WHILE_TYPING
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", press)
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert out.reason == "dialog_open"
    assert sess.mode_switch is newer and sess.perm_mode == "auto"


def test_the_switch_is_marked_as_pressing_while_its_keys_go_in(core, run_async):
    bot, sess, seen = core
    out = run_async(bot._mode_switch_core(sess, "auto"))
    assert out.reason == "sent"
    assert seen["pressing"] is True
    assert sess.mode_switch is not None and sess.mode_switch.pressing is False


def test_pressing_ends_when_the_presses_raise(core, run_async, monkeypatch):
    bot, sess, _seen = core

    async def press(name, count, **_kw):
        raise RuntimeError("boom")
    monkeypatch.setattr("aipager.dtach.inject.press_mode_cycle", press)
    with pytest.raises(RuntimeError):
        run_async(bot._mode_switch_core(sess, "auto"))
    assert sess.mode_switch is not None and sess.mode_switch.pressing is False


# ── a hook while the keys are pressed ────────────────────────────────────


def test_a_hook_while_the_keys_are_pressed_is_never_their_result():
    """The presses may wait for the gate longer than the settle window: a
    hook then was fired before Claude Code read them."""
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=1.0, pressing=True)
    assert sess.note_reported_mode("default", 1000.0) == "settling"
    assert sess.reported_mode == ""
    sess.mode_switch.pressing = False
    assert sess.note_reported_mode("default", 1000.0) == "recorded"


# ── the confirm's retry (aipager/bot/mode_switch.py) ─────────────────────


class _Bot:
    _app = None


@pytest.fixture
def retry(monkeypatch):
    registry = SessionRegistry()
    sess = registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.status = Status.BUSY
    pending = ModeSwitch(target="auto", presses=3, sent_at=time.monotonic() - 5,
                         retried=True)
    sess.mode_switch = pending
    state = {"result": True, "pressing": None}
    settled = []

    async def press(name, count, **_kw):
        state["pressing"] = pending.pressing
        return state["result"]

    async def settle(bot, s, p, outcome):
        settled.append(outcome)
    monkeypatch.setattr(mode_switch.inject, "press_mode_cycle", press)
    monkeypatch.setattr(mode_switch, "settle", settle)
    return sess, pending, state, settled


def test_a_retry_that_meets_a_dialog_waits_for_the_next_hook(retry, run_async):
    sess, pending, state, settled = retry
    state["result"] = inject.DIALOG_BEFORE_TYPING
    sent_at = pending.sent_at
    run_async(mode_switch._retry(_Bot(), sess, pending, 2, "acceptEdits"))
    # Not spent, not settled: the next hook decides, from the mode reported.
    assert sess.mode_switch is pending and not pending.retried
    assert sess.reported_mode == "acceptEdits"
    assert pending.sent_at == sent_at
    assert settled == []


def test_a_retry_stopped_between_presses_leaves_the_mode_to_the_next_hook(retry, run_async):
    sess, pending, state, settled = retry
    state["result"] = inject.DIALOG_WHILE_TYPING
    run_async(mode_switch._retry(_Bot(), sess, pending, 2, "acceptEdits"))
    assert sess.mode_switch is pending and not pending.retried
    # Some presses went in: the mode reported before them no longer holds.
    assert sess.reported_mode == ""
    assert time.monotonic() - pending.sent_at < 1.0
    assert settled == []


def test_a_retry_is_marked_as_pressing_while_its_keys_go_in(retry, run_async):
    sess, pending, state, _settled = retry
    run_async(mode_switch._retry(_Bot(), sess, pending, 2, "acceptEdits"))
    assert state["pressing"] is True
    assert pending.pressing is False


def test_a_retry_gets_no_press_once_a_relaunch_began_during_the_gate_wait(
        term, monkeypatch, run_async):
    registry = SessionRegistry()
    sess = registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.status = Status.BUSY
    pending = ModeSwitch(target="auto", presses=3, sent_at=time.monotonic() - 5,
                         retried=True)
    sess.mode_switch = pending
    settled = []

    async def settle(bot, s, p, outcome):
        settled.append(outcome)
    monkeypatch.setattr(mode_switch, "settle", settle)
    answers = [1, 0]
    monkeypatch.setattr(typing_gate, "fresh_passes",
                        lambda base, session, window: answers.pop(0) if answers else 0)

    async def gate_sleep(_secs):
        sess.relaunch_in_flight = True
    monkeypatch.setattr(inject, "_gate_sleep", gate_sleep)
    run_async(mode_switch._retry(_Bot(), sess, pending, 2, "acceptEdits"))
    assert term.writes == []
    # The relaunch resets the switch itself: nothing said as unconfirmed;
    # should it not, the retry is not spent (the next hook decides).
    assert settled == []
    assert sess.mode_switch is pending and not pending.retried


def test_a_retry_waits_for_a_switch_in_progress(term, monkeypatch, run_async):
    """The retry takes the same switch lock: its presses never go in
    between another switch's."""
    registry = SessionRegistry()
    sess = registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.status = Status.BUSY
    pending = ModeSwitch(target="auto", presses=3, sent_at=time.monotonic() - 5,
                         retried=True)
    sess.mode_switch = pending

    async def scenario():
        lock = mode_switch.switch_lock(sess.name)
        await lock.acquire()
        task = asyncio.ensure_future(
            mode_switch._retry(_Bot(), sess, pending, 2, "acceptEdits"))
        await asyncio.sleep(0.02)
        during = list(term.writes)
        lock.release()
        await task
        return during

    assert run_async(scenario()) == []
    assert term.writes == [BACKTAB] * 2


def test_a_failed_retry_is_still_unconfirmed(retry, run_async):
    sess, pending, state, settled = retry
    state["result"] = False
    run_async(mode_switch._retry(_Bot(), sess, pending, 2, "acceptEdits"))
    assert settled == ["unconfirmed"]
    assert sess.mode_switch is None


# ── the /mode card's confirm (callbacks._do_perms_switch_via_fn) ─────────


def test_the_mode_cards_switch_says_a_prompt_is_open(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    bot._mode_switch_core = AsyncMock(return_value=RestartOutcome(
        ok=False, reason="dialog_open", label="jim", mode="auto", in_place=True))
    bot._mode_card = MagicMock(return_value=("CARD", "KB"))
    edits = []

    async def edit(text, **kw):
        edits.append(text)
    run_async(bot._do_perms_switch_via_fn(sess, "auto", edit, turn=sess.turn_key,
                                          chat_id=None, announce=False))
    note = bot._mode_card.call_args[0][2]
    assert note == i18n.get("en").html(MODE_DIALOG_OPEN, session="jim")
