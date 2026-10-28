"""A risky-removal prompt answered in the terminal is never closed as
refused by Claude Code (roadmap 8.128, review iteration 1, issues 001 and
002).

- Answered Yes in the terminal, its rm then fails: PostToolUseFailure
  fires (Claude Code's own refusal fires no hook at all), and the
  transcript holds the call's error result. Its clock goes there, so the
  monitor's scan, the refusal-time timer, the next tool call and the
  turn's end all leave it alone.
- Answered "No" in the terminal: the same error result plus Claude Code's
  interrupt marker. That is the person's answer: neither the scan nor the
  timer, which runs on its own, says Claude Code refused it."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from aipager import i18n, rm_countdown, session_monitor, state
from aipager.bot import rm_refusal
from tests.rm_clock_rig import RM, TUID, rm_prompt, scan
from tests.two_prompts_rig import S

EN = i18n.get("en")
IN_WINDOW = 120.0 - rm_countdown.WINDOW_LEAD_SECONDS + 1.0
MARKER = "[Request interrupted by user for tool use]"


FAILURE = {"tool_name": "Bash", "tool_input": {"command": RM, "description": RM},
           "tool_use_id": TUID,
           "error": "rm: cannot remove '/home/alice/app': Permission denied"}


def failed_in_terminal(r, tx) -> None:
    """Answered Yes at the terminal; the rm ran and failed."""
    r.hook("PostToolUseFailure", **FAILURE)
    tx.call()
    tx.result(is_error=True)


async def failed_in_terminal_async(r, tx) -> None:
    """:func:`failed_in_terminal` from inside the rig's running loop (the
    rig's ``hook`` runs a loop of its own)."""
    r.recv._recent_fingerprints.clear()
    payload = dict({"session": S, "hook_event_name": "PostToolUseFailure",
                    "transcript_path": "", "_seq": next(r.n)}, **FAILURE)
    await r.recv._on_datagram(json.dumps(payload).encode())
    tx.call()
    tx.result(is_error=True)


def interrupted_in_terminal(tx) -> None:
    """Answered "No" at the terminal: the error result and the marker."""
    tx.call()
    tx.result(is_error=True)
    with tx.path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": MARKER}]}}) + "\n")


def nothing_refused(r, audits) -> bool:
    rows = [s for s, _d in r.sess.tool_history if s.endswith("Refused by Claude Code")]
    refused_text = EN.html(rm_refusal.RM_REFUSED)
    edits = [c for c in r.bot._app.bot.edit_message_text.await_args_list
             if refused_text in (c.kwargs.get("text") or "")]
    return (audits, rows, edits) == ([], [], [])


def run_timer(r, monitor, clock, monkeypatch, between) -> None:
    """A scan before the window schedules the refusal-time look; *between*
    happens while it waits (awaited when it returns a coroutine); then it
    looks, inside the window."""
    release = asyncio.Event()

    async def due_sleep(_seconds):
        await release.wait()

    monkeypatch.setattr(session_monitor, "_rm_due_sleep", due_sleep)
    clock.at(2.0)

    async def go():
        await monitor._scan_sessions(time.monotonic(), set())
        tasks = list(monitor._rm_checks.values())
        assert len(tasks) == 1  # the timer is really there
        step = between()
        if asyncio.iscoroutine(step):
            await step
        clock.at(120.0 + rm_countdown.CHECK_GRACE_SECONDS)
        release.set()
        await asyncio.gather(*tasks)

    r.run_async(go())


# -- issue 001: answered Yes in the terminal, the rm failed ---------------------------

def test_the_failed_call_closes_its_prompt(rig, tx):
    """Its call ran: the prompt closes as after a PostToolUse (8.151)."""
    rm_prompt(rig, tx)
    failed_in_terminal(rig, tx)
    assert rig.sess.open_prompts() == []


def test_the_interrupted_calls_clock_goes(rig, tx):
    """Escape while it ran: the record stays for the turn's end, its clock
    goes."""
    rm_prompt(rig, tx)
    rig.hook("PostToolUseFailure", **dict(FAILURE, is_interrupt=True))
    [perm] = rig.sess.open_prompts()
    assert "rm_clock" not in perm["tool_info"]


def test_a_failed_call_is_not_refused_by_the_scan(rig, monitor, tx, clock, audits):
    rm_prompt(rig, tx)
    failed_in_terminal(rig, tx)
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert nothing_refused(rig, audits)


def test_a_failed_call_is_not_refused_by_the_timer(rig, monitor, tx, clock, audits,
                                                   monkeypatch):
    rm_prompt(rig, tx)
    run_timer(rig, monitor, clock, monkeypatch,
              lambda: failed_in_terminal_async(rig, tx))
    assert nothing_refused(rig, audits)


def test_a_failed_call_is_not_refused_by_the_next_tool_call(rig, tx, clock, audits):
    rm_prompt(rig, tx)
    failed_in_terminal(rig, tx)
    clock.at(IN_WINDOW)
    rig.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"},
             tool_use_id="toolu_next")
    assert rig.sess.open_prompts() == []
    assert nothing_refused(rig, audits)


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_a_failed_call_is_not_refused_at_the_turns_end(rig, tx, clock, audits, event):
    rm_prompt(rig, tx)
    failed_in_terminal(rig, tx)
    clock.at(IN_WINDOW)
    rig.hook(event)
    assert nothing_refused(rig, audits)


def test_a_failed_call_on_its_own_message_is_not_refused(cardless, monitor_cl, tx,
                                                         clock, audits):
    rm_prompt(cardless, tx)
    failed_in_terminal(cardless, tx)
    clock.at(IN_WINDOW)
    scan(cardless, monitor_cl)
    cardless.hook("Stop")
    assert nothing_refused(cardless, audits)


def test_a_late_refusal_report_for_a_failed_call_records_nothing(rig, tx, clock, audits):
    """The bot's own check: a report that names a prompt whose call came
    back meanwhile records nothing."""
    rm_prompt(rig, tx)
    [perm] = rig.sess.open_prompts()
    failed_in_terminal(rig, tx)
    clock.at(IN_WINDOW)
    rig.run_async(rig.bot.notify(rig.sess, "rm_refused", {
        "perms": [perm], "outcome": "refused", "close": True}))
    assert nothing_refused(rig, audits)
    assert "rm_refused" not in perm


def test_the_dropped_clock_is_saved(rig, tx):
    """A restart reads the interrupted call's prompt without its clock."""
    rm_prompt(rig, tx)
    rig.bot.registry.save_if_dirty()  # saved with its clock, nothing left dirty
    rig.hook("PostToolUseFailure", **dict(FAILURE, is_interrupt=True))
    rig.bot.registry.save_if_dirty()
    saved = json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][S]
    assert "rm_clock" not in saved["open_prompt"]["perm"]


def test_a_failed_call_beside_another_prompt_closes_as_before(rig, tx, clock, audits):
    """Beside another open prompt the failed call's record goes at once
    (8.120); the other stays and nothing says refused."""
    rig.question()
    rm_prompt(rig, tx)
    failed_in_terminal(rig, tx)
    clock.at(IN_WINDOW)
    rig.hook("Stop")
    assert nothing_refused(rig, audits)


# -- issue 002: "No" in the terminal (the interrupt marker) ---------------------------

def test_a_terminal_no_is_not_refused_by_the_scan(rig, monitor, tx, clock, audits):
    rm_prompt(rig, tx)
    interrupted_in_terminal(tx)
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert nothing_refused(rig, audits)


def test_a_terminal_no_is_not_refused_by_the_timer(rig, monitor, tx, clock, audits,
                                                   monkeypatch):
    rm_prompt(rig, tx)
    run_timer(rig, monitor, clock, monkeypatch, lambda: interrupted_in_terminal(tx))
    assert nothing_refused(rig, audits)


def test_the_timer_still_refuses_with_no_marker(rig, monitor, tx, clock, audits,
                                                monkeypatch):
    """Control: the same timer run with the error result alone closes it
    as refused, so the two tests above prove the marker and the dropped
    clock, not a timer that never looks."""
    rm_prompt(rig, tx)
    run_timer(rig, monitor, clock, monkeypatch, tx.refused)
    assert [a["action"] for a in audits] == ["Refused by Claude Code"]
