"""Success criteria 5-8 and 17: Ask <-> Auto switches in place with Shift+Tab
presses (ESC [ Z) counted from the session's current mode, separate writes
with a 0.15 s gap between them, no kill and no launch; allowed while a turn
runs, refused while a dialog is open with no byte written; the /mode card
shows the target and says it is waiting for Claude Code."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager import perm_modes
from aipager.dtach import inject
from aipager.dtach.hook_receiver import HookReceiver
from aipager.state import SessionRegistry, Status

from _tm3 import (BACKTAB, DM_CHAT, OWNER, dm_bot, hook, mode_only_notify,
                  reply_buttons, reply_texts, settle_tasks, tap, typed)

NAME, LABEL = "claude-ben", "ben"
PENDING_LINE = "Waiting for Claude Code to confirm the switch."


def _bot(mk_bot, *, mode="ask", status=Status.IDLE):
    reg = SessionRegistry()
    bot = dm_bot(mk_bot, reg)
    sess = reg.get_or_create(NAME)
    sess.label = LABEL
    sess.status = status
    sess.perm_mode = mode
    sess.claude_session_id = "11111111-2222-3333-4444-555555555555"
    sess.cwd = "/home/aly/proj"
    reg.last_active_session = NAME
    return bot, sess


def _mode(bot, run_async, text):
    u = typed(text, chat_id=DM_CHAT, user_id=OWNER)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    return u


async def _mode_then_settle(bot, text):
    u = typed(text, chat_id=DM_CHAT, user_id=OWNER)
    await bot._handle_mode_cmd(u, MagicMock())
    await settle_tasks()
    return u


# ---- the exact presses --------------------------------------------------

def test_ask_to_auto_presses_backtab_three_times(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask")
    run_async(_mode_then_settle(bot, "/mode auto"))
    assert pty.stdin_writes == [BACKTAB] * 3


def test_auto_to_ask_presses_backtab_once(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="auto")
    run_async(_mode_then_settle(bot, "/mode ask"))
    assert pty.stdin_writes == [BACKTAB]


def test_each_press_is_its_own_dtach_p_write(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask")
    run_async(_mode_then_settle(bot, "/mode auto"))
    assert all("-p" in args for args, stdin in pty.runs if stdin)


@pytest.mark.parametrize("start,target,gaps", [("ask", "auto", 2), ("auto", "ask", 0)],
                         ids=["ask-to-auto", "auto-to-ask"])
def test_presses_are_gapped_by_0_15_s(mk_bot, run_async, pty, start, target, gaps):
    bot, _ = _bot(mk_bot, mode=start)
    run_async(_mode_then_settle(bot, f"/mode {target}"))
    assert pty.gaps == [0.15] * gaps


def test_gaps_sit_between_presses_never_after_the_last(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask")
    run_async(_mode_then_settle(bot, "/mode auto"))
    assert pty.order == ["write", "gap", "write", "gap", "write"]


@pytest.mark.parametrize("start,target", [("ask", "auto"), ("auto", "ask")],
                         ids=["ask-to-auto", "auto-to-ask"])
def test_in_place_switch_kills_nothing(mk_bot, run_async, pty, start, target):
    bot, _ = _bot(mk_bot, mode=start)
    run_async(_mode_then_settle(bot, f"/mode {target}"))
    assert pty.killed == []


@pytest.mark.parametrize("start,target", [("ask", "auto"), ("auto", "ask")],
                         ids=["ask-to-auto", "auto-to-ask"])
def test_in_place_switch_launches_nothing(mk_bot, run_async, pty, start, target):
    bot, _ = _bot(mk_bot, mode=start)
    run_async(_mode_then_settle(bot, f"/mode {target}"))
    assert pty.launched == []


def test_ask_to_auto_needs_no_confirm_card(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask")
    u = run_async(_mode_then_settle(bot, "/mode auto"))
    assert not any("perms_confirm" in (cb or "") for _t, cb in reply_buttons(u))


def test_switching_to_the_current_mode_presses_nothing(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="auto")
    run_async(_mode_then_settle(bot, "/mode auto"))
    assert pty.runs == []


# ---- the press helper and the key, as exported -----------------------------

def test_backtab_key_is_esc_bracket_z():
    assert inject.KEYS["BackTab"] == "\x1b[Z"


def test_send_keys_backtab_writes_esc_bracket_z(run_async, pty):
    run_async(inject.send_keys(NAME, "BackTab"))
    assert pty.stdin_writes == [BACKTAB]


@pytest.mark.parametrize("n", [0, 1, 2, 3], ids=["zero", "one", "two", "three"])
def test_press_mode_cycle_writes_n_presses(run_async, pty, n):
    run_async(inject.press_mode_cycle(NAME, n))
    assert pty.stdin_writes == [BACKTAB] * n


@pytest.mark.parametrize("n", [0, 1, 3], ids=["zero", "one", "three"])
def test_press_mode_cycle_gaps_n_minus_one(run_async, pty, n):
    run_async(inject.press_mode_cycle(NAME, n))
    assert pty.gaps == [perm_modes.MODE_PRESS_GAP] * max(n - 1, 0)


def test_press_mode_cycle_zero_reports_success(run_async, pty):
    assert run_async(inject.press_mode_cycle(NAME, 0)) is True


def test_press_mode_cycle_stops_at_the_first_failed_write(run_async, pty):
    pty.write_ok = False
    run_async(inject.press_mode_cycle(NAME, 3))
    assert len(pty.runs) == 1


def test_press_mode_cycle_reports_a_failed_write(run_async, pty):
    pty.write_ok = False
    assert run_async(inject.press_mode_cycle(NAME, 3)) is False


def test_press_gap_constant():
    assert perm_modes.MODE_PRESS_GAP == 0.15


# ---- counting from the reported mode ---------------------------------------

PRESS_TABLE = [
    ("default", "auto", 3), ("auto", "ask", 1), ("plan", "auto", 1),
    ("acceptEdits", "auto", 2), ("plan", "ask", 2), ("acceptEdits", "ask", 3),
    ("default", "ask", 0), ("auto", "auto", 0),
]


@pytest.mark.parametrize("raw,target,n", PRESS_TABLE,
                         ids=[f"{r}-to-{t}" for r, t, _ in PRESS_TABLE])
def test_presses_table(raw, target, n):
    assert perm_modes.presses(raw, target) == n


@pytest.mark.parametrize("raw,target,n", [
    ("plan", "ask", 1), ("acceptEdits", "ask", 2), ("default", "auto", None),
], ids=["plan-to-ask-3cycle", "acceptEdits-to-ask-3cycle", "default-to-auto-3cycle"])
def test_presses_without_auto_in_the_cycle(raw, target, n):
    assert perm_modes.presses(raw, target, auto_in_cycle=False) == n


@pytest.mark.parametrize("raw", ["bypassPermissions", "dontAsk", "nonsense"],
                         ids=["bypass", "dontAsk", "unknown"])
def test_presses_unreachable_start_is_none(raw):
    assert perm_modes.presses(raw, "auto") is None


REPORTED = [("plan", "auto", 1), ("acceptEdits", "auto", 2),
            ("plan", "ask", 2), ("acceptEdits", "ask", 3)]


@pytest.mark.parametrize("raw,target,n", REPORTED,
                         ids=[f"{r}-to-{t}" for r, t, _ in REPORTED])
def test_switch_counts_from_the_mode_a_hook_reported(mk_bot, run_async, pty, raw, target, n):
    """A person cycled the terminal into plan / acceptEdits; the next
    /mode counts its presses from there."""
    bot, sess = _bot(mk_bot, mode="ask")
    recv = HookReceiver(bot.registry, mode_only_notify(bot))

    async def _go():
        await recv._on_datagram(hook(NAME, mode=raw))
        sess.status = Status.IDLE
        await _mode_then_settle(bot, f"/mode {target}")
    run_async(_go())
    assert pty.stdin_writes == [BACKTAB] * n


# ---- BUSY is fine, INTERACTIVE is refused ----------------------------------

@pytest.mark.parametrize("start,target,n", [("ask", "auto", 3), ("auto", "ask", 1)],
                         ids=["ask-to-auto", "auto-to-ask"])
def test_switch_while_busy_presses(mk_bot, run_async, pty, start, target, n):
    bot, _ = _bot(mk_bot, mode=start, status=Status.BUSY)
    run_async(_mode_then_settle(bot, f"/mode {target}"))
    assert pty.stdin_writes == [BACKTAB] * n


@pytest.mark.parametrize("start,target", [("ask", "auto"), ("auto", "ask")],
                         ids=["ask-to-auto", "auto-to-ask"])
def test_switch_while_busy_offers_no_stop_and_switch(mk_bot, run_async, pty, start, target):
    bot, _ = _bot(mk_bot, mode=start, status=Status.BUSY)
    u = run_async(_mode_then_settle(bot, f"/mode {target}"))
    assert not any("perms_stop_switch" in (cb or "") for _t, cb in reply_buttons(u))


@pytest.mark.parametrize("start,target", [("ask", "auto"), ("auto", "ask")],
                         ids=["ask-to-auto", "auto-to-ask"])
def test_switch_while_busy_does_not_stop_the_turn(mk_bot, run_async, pty, start, target):
    bot, _ = _bot(mk_bot, mode=start, status=Status.BUSY)
    run_async(_mode_then_settle(bot, f"/mode {target}"))
    assert pty.killed == []


@pytest.mark.parametrize("start,target", [("ask", "auto"), ("auto", "ask")],
                         ids=["ask-to-auto", "auto-to-ask"])
def test_typed_switch_while_a_dialog_is_open_writes_nothing(mk_bot, run_async, pty,
                                                            start, target):
    bot, _ = _bot(mk_bot, mode=start, status=Status.INTERACTIVE)
    run_async(_mode_then_settle(bot, f"/mode {target}"))
    assert pty.runs == []


@pytest.mark.parametrize("start,target", [("ask", "auto"), ("auto", "ask")],
                         ids=["ask-to-auto", "auto-to-ask"])
def test_typed_switch_while_a_dialog_is_open_keeps_the_mode(mk_bot, run_async, pty,
                                                           start, target):
    bot, sess = _bot(mk_bot, mode=start, status=Status.INTERACTIVE)
    run_async(_mode_then_settle(bot, f"/mode {target}"))
    assert sess.perm_mode == start


def test_typed_switch_while_a_dialog_is_open_says_answer_it_first(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask", status=Status.INTERACTIVE)
    u = run_async(_mode_then_settle(bot, "/mode auto"))
    text = " ".join(reply_texts(u)).lower()
    assert "answer" in text or "dialog" in text or "question" in text, text


def _card_buttons(bot, run_async):
    u = _mode(bot, run_async, "/mode")
    return {t: cb for t, cb in reply_buttons(u)}


def test_mode_card_offers_the_other_two_modes_from_ask(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask")
    btns = _card_buttons(bot, run_async)
    verbs = sorted(cb.rsplit(":", 1)[-1].rstrip("0123456789") for cb in btns.values()
                   if cb and cb.rsplit(":", 1)[-1].startswith("mode"))
    assert verbs == ["modeauto", "modefull"]


def test_mode_card_offers_the_other_two_modes_from_full(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="full")
    btns = _card_buttons(bot, run_async)
    verbs = sorted(cb.rsplit(":", 1)[-1].rstrip("0123456789") for cb in btns.values()
                   if cb and cb.rsplit(":", 1)[-1].startswith("mode"))
    assert verbs == ["modeask", "modeauto"]


def _tap_mode(bot, run_async, verb_prefix):
    btns = _card_buttons(bot, run_async)
    data = next(cb for cb in btns.values() if cb and cb.rsplit(":", 1)[-1].startswith(verb_prefix))
    u, q = tap(data, chat_id=DM_CHAT, user_id=OWNER)

    async def _go():
        await bot._handle_callback(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    return q


def test_tapped_switch_while_a_dialog_is_open_writes_nothing(mk_bot, run_async, pty):
    bot, sess = _bot(mk_bot, mode="ask")
    btns = _card_buttons(bot, run_async)
    data = next(cb for cb in btns.values() if cb and "modeauto" in cb)
    sess.status = Status.INTERACTIVE
    u, q = tap(data, chat_id=DM_CHAT, user_id=OWNER)
    run_async(bot._handle_callback(u, MagicMock()))
    assert pty.runs == []


def test_tapped_switch_while_a_dialog_is_open_answers_with_a_toast(mk_bot, run_async, pty):
    bot, sess = _bot(mk_bot, mode="ask")
    btns = _card_buttons(bot, run_async)
    data = next(cb for cb in btns.values() if cb and "modeauto" in cb)
    sess.status = Status.INTERACTIVE
    u, q = tap(data, chat_id=DM_CHAT, user_id=OWNER)
    run_async(bot._handle_callback(u, MagicMock()))
    assert any(c.args and c.args[0] for c in q.answer.await_args_list)


def test_tapped_ask_to_auto_presses_three(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask")
    _tap_mode(bot, run_async, "modeauto")
    assert pty.stdin_writes == [BACKTAB] * 3


def test_tapped_switch_ignores_a_stale_turn_key(mk_bot, run_async, pty):
    """The turn key on a tapped button is ignored for an in-place switch
    (nothing is killed), design §8."""
    bot, _ = _bot(mk_bot, mode="ask")
    u, q = tap(f"{NAME}:modeauto99999", chat_id=DM_CHAT, user_id=OWNER)

    async def _go():
        await bot._handle_callback(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    assert pty.stdin_writes == [BACKTAB] * 3


# ---- what the person sees until Claude Code confirms -----------------------

def test_reply_says_the_switch_was_sent_and_waits(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask")
    u = run_async(_mode_then_settle(bot, "/mode auto"))
    assert PENDING_LINE in "\n".join(reply_texts(u))


def test_card_shows_the_target_mode_until_a_hook_arrives(mk_bot, run_async, pty):
    bot, _ = _bot(mk_bot, mode="ask")
    run_async(_mode_then_settle(bot, "/mode auto"))
    u = _mode(bot, run_async, "/mode")
    assert "🤖 Auto mode" in "\n".join(reply_texts(u))


def test_shown_mode_is_the_target_while_pending(mk_bot, run_async, pty):
    bot, sess = _bot(mk_bot, mode="ask")
    run_async(_mode_then_settle(bot, "/mode auto"))
    assert sess.shown_mode() == ("auto", "")


def test_a_failed_press_restores_the_previous_mode(mk_bot, run_async, pty):
    bot, sess = _bot(mk_bot, mode="ask")
    pty.write_ok = False
    run_async(_mode_then_settle(bot, "/mode auto"))
    assert sess.perm_mode == "ask"
