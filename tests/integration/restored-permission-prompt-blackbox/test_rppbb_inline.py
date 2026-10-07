"""Black-box rows for roadmap 8.102: a restart while a card's inline
permission prompt waits (design.md success criteria 1, 2, 4, 5, 11, 12, 15).

Every row drives the real daemon on the virtual loop (see conftest.py): a
Telegram turn, the PreToolUse and PermissionRequest datagrams through the
hook receiver, the real state file through ``save()`` and a fresh
``load()`` in a new registry and bot, ``recover_sessions()``, and taps
through ``_handle_callback`` on the buttons the chat still shows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import datetime, timezone

import pytest

from aipager import audit as audit_mod
from aipager import preferences as prefs
from aipager.session_monitor import (
    IDLE_RECOVERY_GRACE,
    INTERACTIVE_TIMEOUT_SECONDS,
    SessionMonitor,
)
from aipager.state import Status

CHAT = 256113222
LABEL = "rppbb"
MARKER = "[Request interrupted by user for tool use]"


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _dead(tmp_path) -> str:
    return str(tmp_path / "gone.sock")


def _audit() -> list[dict]:
    path = audit_mod.AUDIT_LOG_PATH
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


async def _restored_inline(r, tmp_path, *, down: float = 30.0) -> int:
    card = await r.inline_prompt(reply_addr=_dead(tmp_path))
    await r.restart(down=down)
    return card


# ── SC1: restored INTERACTIVE, quiet, answerable ─────────────────────────

def test_restored_inline_prompt_is_interactive(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _restored_inline(r, tmp_path))
    assert r.sess.status is Status.INTERACTIVE


def test_restored_inline_prompt_runs_no_animation(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        await _restored_inline(r, tmp_path)
        await asyncio.sleep(10)
        return r.sess.animation_running()

    assert _run(vloop, scenario()) is False


def test_restored_inline_card_is_not_edited(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        before = r.chat.edits_of(card)
        await r.restart()
        await asyncio.sleep(10)
        return before, r.chat.edits_of(card)

    before, after = _run(vloop, scenario())
    assert after == before


def test_restored_inline_card_keeps_its_answer_buttons(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _restored_inline(r, tmp_path)
        await asyncio.sleep(10)
        return card

    card = _run(vloop, scenario())
    verbs = {d.rsplit(":", 1)[-1] for d in r.chat.markups[card]}
    assert {"allow", "deny"} <= verbs


def test_restored_inline_card_stays_live(replay, vloop, tmp_path):
    """Kept as is: not closed as "Daemon restarted", not deleted."""
    r = replay

    async def scenario():
        card = await _restored_inline(r, tmp_path)
        await asyncio.sleep(10)
        return card

    card = _run(vloop, scenario())
    assert r.chat.live_cards() == [card]


def test_restored_session_keeps_its_card(replay, vloop, tmp_path):
    r = replay
    card = _run(vloop, _restored_inline(r, tmp_path))
    assert r.sess.busy_msg_id == card


def test_pinned_bar_says_needs_you(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _restored_inline(r, tmp_path))
    text, _kb = r.bot._render_pinned(CHAT)
    assert "needs you" in text


def test_restore_logs_the_restored_line(replay, vloop, tmp_path, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        card = _run(vloop, _restored_inline(r, tmp_path))
    assert (f"[{LABEL}] permission prompt restored after restart - waiting "
            f"for an answer (inline card {card})") in caplog.messages


def test_recovery_summary_counts_the_restore(replay, vloop, tmp_path, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _restored_inline(r, tmp_path))
    assert any(m.startswith("recovered ") and "1 restored" in m
               for m in caplog.messages), caplog.messages


async def _allow_after_restore(r, tmp_path):
    card = await _restored_inline(r, tmp_path)
    keys_before = list(r.pty.keys)
    toast = await r.tap(card, "allow")
    await asyncio.sleep(3)
    return card, keys_before, toast


def test_allow_on_restored_card_types_enter_once(replay, vloop, tmp_path):
    r = replay
    _card, before, _toast = _run(vloop, _allow_after_restore(r, tmp_path))
    assert r.pty.keys[len(before):] == ["Enter"]


def test_allow_on_restored_card_is_audited_as_keystroke_fallback(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_after_restore(r, tmp_path))
    rec = _audit()[-1]
    assert (rec["action"], rec["via"], rec["denied"]) == (
        "Allowed", "keystroke_fallback", False)


def test_allow_on_restored_card_is_attributed_to_the_tapper(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_after_restore(r, tmp_path))
    rec = _audit()[-1]
    assert (rec["user_id"], rec["username"]) == (12345, "owner")


def test_allow_on_restored_card_toasts_allowed(replay, vloop, tmp_path):
    r = replay
    _card, _before, toast = _run(vloop, _allow_after_restore(r, tmp_path))
    assert toast and "Allowed" in toast


def test_allow_on_restored_card_makes_the_session_busy(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_after_restore(r, tmp_path))
    assert r.sess.status is Status.BUSY


def test_allow_on_restored_card_animates_the_card(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_after_restore(r, tmp_path))
    assert r.sess.animation_running() is True


def test_allow_on_restored_card_edits_the_same_card(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _restored_inline(r, tmp_path)
        before = r.chat.edits_of(card)
        await r.tap(card, "allow")
        await asyncio.sleep(10)
        return card, before

    card, before = _run(vloop, scenario())
    assert r.chat.edits_of(card) > before


async def _allow_then_finish(r, tmp_path):
    card = await _restored_inline(r, tmp_path)
    cards_before = set(r.chat.cards)
    await r.tap(card, "allow")
    await asyncio.sleep(3)
    r.post_tool()
    await asyncio.sleep(2)
    r.stop("build removed")
    await asyncio.sleep(20)
    await asyncio.gather(*r.tasks, return_exceptions=True)
    return card, cards_before


def test_stop_after_restored_answer_settles_the_card(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_then_finish(r, tmp_path))
    assert r.chat.live_cards() == []


def test_stop_after_restored_answer_sends_no_new_card(replay, vloop, tmp_path):
    r = replay
    _card, before = _run(vloop, _allow_then_finish(r, tmp_path))
    assert set(r.chat.cards) == before


def test_stop_after_restored_answer_ends_idle(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_then_finish(r, tmp_path))
    assert r.sess.status is Status.IDLE


def test_stop_after_restored_answer_delivers_the_answer(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_then_finish(r, tmp_path))
    assert any("build removed" in t for t in r.chat.sent_texts()), \
        r.chat.sent_texts()


# ── "Allow always" when offered ──────────────────────────────────────────

_SUGGESTION = [{"type": "addRules",
                "rules": [{"toolName": "Bash", "ruleContent": "rm -rf build"}],
                "behavior": "allow", "destination": "localSettings"}]


def test_allow_always_on_restored_card_types_down_enter(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await r.inline_prompt(reply_addr=_dead(tmp_path),
                                     permission_suggestions=_SUGGESTION)
        await r.restart()
        before = list(r.pty.keys)
        await r.tap(card, "allow_always")
        await asyncio.sleep(3)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == ["Down", "Enter"]


# ── SC2: Deny ────────────────────────────────────────────────────────────

async def _deny_after_restore(r, tmp_path, *, marker: bool = True):
    card = await _restored_inline(r, tmp_path)
    before = list(r.pty.keys)
    await r.tap(card, "deny")
    await asyncio.sleep(0.5)
    if marker:
        # Claude Code at the dialog's last row: it ends the turn like an
        # interrupt, no PostToolUse, no Stop.
        r.append(
            {"type": "user", "timestamp": _iso(r.wall()), "message": {
                "role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "toolu_01",
                    "is_error": True,
                    "content": "The user doesn't want to proceed."}]}},
            {"type": "user", "timestamp": _iso(r.wall()), "message": {
                "role": "user", "content": [{"type": "text", "text": MARKER}]}})
    await asyncio.sleep(30)
    return card, before


def test_deny_on_restored_card_types_five_downs_then_enter(
        replay, vloop, tmp_path):
    r = replay
    _card, before = _run(vloop, _deny_after_restore(r, tmp_path))
    assert r.pty.keys[len(before):] == ["Down"] * 5 + ["Enter"]


def test_deny_on_restored_card_is_audited_as_denied_keystroke(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _deny_after_restore(r, tmp_path))
    rec = _audit()[-1]
    assert (rec["denied"], rec["via"]) == (True, "keystroke_fallback")


def test_deny_on_restored_card_ends_the_turn_on_the_marker(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _deny_after_restore(r, tmp_path))
    assert r.sess.status is Status.IDLE


def test_deny_on_restored_card_settles_the_card(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _deny_after_restore(r, tmp_path))
    assert r.chat.live_cards() == []


def test_deny_on_restored_card_says_denied_on_the_card(replay, vloop, tmp_path):
    r = replay
    card, _before = _run(vloop, _deny_after_restore(r, tmp_path))
    assert "Denied" in r.chat.cards[card]["text"]


# ── SC4: restart within the hook's deadline, the hook still listening ────

@pytest.fixture
def listener(tmp_path_factory):
    d = tmp_path_factory.mktemp("h")
    path = str(d / "r.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    sock.setblocking(False)
    yield sock, path
    sock.close()


def _recv(sock):
    try:
        raw, _ = sock.recvfrom(65536)
    except BlockingIOError:
        return None
    return json.loads(raw.decode())


async def _quick_restart_allow(r, path, *, timeout_first: bool = False):
    card = await r.inline_prompt(reply_addr=path, settle=0.5)
    waited = 0.0
    while not any(d.endswith(":allow") for d in r.chat.markups.get(card, [])):
        assert waited < 18, "the prompt was never painted on the card"
        await asyncio.sleep(0.5)
        waited += 0.5
    await r.restart(down=1.0)
    if timeout_first:
        r.hook(hook_event_name="permission_reply_timeout",
               aipager_request_id="req-1")
        await asyncio.sleep(0.5)
    before = list(r.pty.keys)
    await r.tap(card, "allow")
    await asyncio.sleep(1)
    return before


def test_quick_restart_allow_types_no_keys(replay, vloop, listener):
    r = replay
    _sock, path = listener
    before = _run(vloop, _quick_restart_allow(r, path))
    assert r.pty.keys[len(before):] == []


def test_quick_restart_allow_is_audited_as_hook_decision(
        replay, vloop, listener):
    r = replay
    _sock, path = listener
    _run(vloop, _quick_restart_allow(r, path))
    assert _audit()[-1]["via"] == "hook_decision"


def test_quick_restart_allow_reaches_the_parked_hook(replay, vloop, listener):
    r = replay
    sock, path = listener
    _run(vloop, _quick_restart_allow(r, path))
    wire = _recv(sock)
    assert wire is not None and "allow" in json.dumps(wire)


def test_reply_timeout_after_restore_falls_back_to_keys(replay, vloop, listener):
    """entrypoints.md: ``permission_reply_timeout`` also clears the channel
    of a restored prompt, so the answer is typed."""
    r = replay
    _sock, path = listener
    before = _run(vloop, _quick_restart_allow(r, path, timeout_first=True))
    assert r.pty.keys[len(before):] == ["Enter"]


def test_reply_timeout_after_restore_sends_nothing_to_the_hook(
        replay, vloop, listener):
    r = replay
    sock, path = listener
    _run(vloop, _quick_restart_allow(r, path, timeout_first=True))
    assert _recv(sock) is None


# ── SC5 and the positive match: transcript tails that do not contradict ──

def _previous_turn(r, at: float) -> None:
    r.append(
        {"type": "user", "timestamp": _iso(at - 60), "message": {
            "role": "user", "content": "an earlier prompt"}},
        {"type": "assistant", "timestamp": _iso(at - 50), "message": {
            "id": "m0", "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "an earlier answer"}]}},
        {"type": "system", "subtype": "turn_duration",
         "timestamp": _iso(at - 49), "durationMs": 11000},
        {"type": "queue-operation", "operation": "enqueue",
         "timestamp": _iso(at - 1), "content": "clean up"},
        {"type": "queue-operation", "operation": "remove",
         "timestamp": _iso(at - 1)})


def test_previous_turn_tail_with_queue_operations_restores(
        replay, vloop, tmp_path):
    r = replay
    _previous_turn(r, vloop.wall())
    _run(vloop, _restored_inline(r, tmp_path))
    assert r.sess.status is Status.INTERACTIVE


def test_previous_turn_tail_card_is_not_closed(replay, vloop, tmp_path, caplog):
    r = replay
    _previous_turn(r, vloop.wall())
    with caplog.at_level(logging.INFO):
        card = _run(vloop, _restored_inline(r, tmp_path))
    assert r.chat.live_cards() == [card]


def test_matching_unanswered_tool_use_in_the_tail_restores(
        replay, vloop, tmp_path):
    """The spec's literal positive match: the tail is the pending tool_use
    with no result."""
    r = replay

    async def scenario():
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        r.append({"type": "assistant", "timestamp": _iso(r.wall()), "message": {
            "id": "m1", "role": "assistant", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "toolu_01", "name": "Bash",
                         "input": {"command": "rm -rf build"}}]}})
        await r.restart()
        return card

    _run(vloop, scenario())
    assert r.sess.status is Status.INTERACTIVE


def test_missing_transcript_file_restores(replay, vloop, tmp_path):
    """A first turn not flushed yet: no file is no contradiction."""
    import os

    r = replay
    os.unlink(r.transcript)
    _run(vloop, _restored_inline(r, tmp_path))
    assert r.sess.status is Status.INTERACTIVE


# ── SC11: the INTERACTIVE watchdog counts from the restore ───────────────

async def _old_prompt_restored(r, tmp_path):
    """A turn that ran for 400 s (tool rounds keep it alive) before its
    permission dialog: the card's saved anchor is 400 s old at the
    restart."""
    await r.card_turn()
    for i in range(40):
        r.pre_tool(tool_use_id=f"toolu_w{i}", tool_input={"command": f"s{i}"})
        await asyncio.sleep(5)
        r.post_tool(tool_use_id=f"toolu_w{i}", tool_input={"command": f"s{i}"})
        await asyncio.sleep(5)
    r.pre_tool(tool_use_id="toolu_01")
    await asyncio.sleep(0.5)
    r.permission(reply_addr=_dead(tmp_path))
    await asyncio.sleep(15)
    await r.restart(down=5)
    return SessionMonitor(r.bot.registry, r.bot.notify)


def test_watchdog_first_tick_keeps_restored_prompt(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        monitor = await _old_prompt_restored(r, tmp_path)
        await monitor.tick()
        return r.sess.status

    assert _run(vloop, scenario()) is Status.INTERACTIVE


def test_watchdog_keeps_restored_prompt_just_inside_the_timeout(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        monitor = await _old_prompt_restored(r, tmp_path)
        await asyncio.sleep(INTERACTIVE_TIMEOUT_SECONDS - 10)
        await monitor.tick()
        return r.sess.status

    assert _run(vloop, scenario()) is Status.INTERACTIVE


def test_watchdog_demotes_restored_prompt_past_the_timeout(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        monitor = await _old_prompt_restored(r, tmp_path)
        await asyncio.sleep(INTERACTIVE_TIMEOUT_SECONDS + 10)
        await monitor.tick()
        return r.sess.status

    assert _run(vloop, scenario()) is not Status.INTERACTIVE


# ── SC12: an answered restored prompt is not idle-recovered ──────────────

def test_answered_restored_prompt_stays_busy_on_a_quiet_finished_tail(
        replay, vloop, tmp_path):
    import os

    r = replay
    _previous_turn(r, vloop.wall())

    async def scenario():
        card = await _restored_inline(r, tmp_path)
        monitor = SessionMonitor(r.bot.registry, r.bot.notify)
        await r.tap(card, "allow")
        os.utime(r.transcript, (r.wall() - 120, r.wall() - 120))
        for _ in range(int(IDLE_RECOVERY_GRACE // 2) + 10):
            await asyncio.sleep(2)
            await monitor.tick()
        return r.sess.status

    assert _run(vloop, scenario()) is Status.BUSY


# ── SC15: a second restart ───────────────────────────────────────────────

def test_restored_prompt_survives_a_second_restart(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        await _restored_inline(r, tmp_path)
        await asyncio.sleep(5)
        await r.restart()
        return r.sess.status

    assert _run(vloop, scenario()) is Status.INTERACTIVE


def test_allow_after_a_second_restart_types_enter(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _restored_inline(r, tmp_path)
        await asyncio.sleep(5)
        await r.restart()
        before = list(r.pty.keys)
        await r.tap(card, "allow")
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == ["Enter"]


def test_second_tap_after_restored_answer_types_nothing(replay, vloop, tmp_path):
    """A tap after the restored prompt was answered: "already answered",
    no keys."""
    r = replay

    async def scenario():
        card = await _restored_inline(r, tmp_path)
        deny = r.button(card, "deny")   # the button a stale client still shows
        await r.tap(card, "allow")
        await asyncio.sleep(1)
        before = list(r.pty.keys)
        await r.tap(card, data=deny)
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == []
