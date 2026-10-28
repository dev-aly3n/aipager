"""Claude Code refused its risky-removal prompt by itself (roadmap 8.128;
design.md criteria 12, 13, 14 and 20): no hook says so, so the session
monitor reads the call's result in the transcript once the refusal window
is open, on its scan and once at the refusal time. An error result closes
the prompt as refused: its message says so and loses its buttons, the
card's row is a refusal, the audit records one, and nothing says Allowed.
Silence closes nothing."""

from __future__ import annotations

import asyncio

import pytest

from aipager import i18n, preferences, rm_countdown, session_monitor
from aipager.bot import card_rows, rm_refusal
from aipager.state import Status
from tests.two_prompts_rig import CARD, GROUP

from tests.rm_clock_rig import REQ, RM, TUID, rm_prompt, scan

EN = i18n.get("en")
REFUSED_ROW = f"🔑 Bash: {RM} → Refused by Claude Code"
#: Inside the refusal window (it opens 15 s before the shown time).
IN_WINDOW = 120.0 - rm_countdown.WINDOW_LEAD_SECONDS + 1.0
BEFORE_WINDOW = 120.0 - rm_countdown.WINDOW_LEAD_SECONDS - 1.0


def edits(r):
    return r.bot._app.bot.edit_message_text.await_args_list


def no_allowed_anywhere(r, audits) -> bool:
    texts = ([t for _m, t, _k in r.card_edits] + [m["text"] or "" for m in r.sent]
             + [c.kwargs.get("text") or "" for c in edits(r)]
             + [str(s) for s, _d in r.sess.tool_history]
             + [a.get("action", "") for a in audits])
    return all("Allowed" not in t for t in texts)


# -- criterion 12: the refused close -------------------------------------------------

def test_the_separate_message_says_refused_and_loses_its_buttons(cardless, monitor_cl, tx,
                                                                 clock):
    rm_prompt(cardless, tx)
    [msg] = cardless.prompt_messages()
    tx.refused()
    clock.at(IN_WINDOW)
    scan(cardless, monitor_cl)
    [call] = edits(cardless)
    assert call.kwargs["message_id"] == msg["msg_id"]
    assert call.kwargs["text"] == msg["text"] + "\n\n" + EN.html(rm_refusal.RM_REFUSED)
    assert call.kwargs["reply_markup"] is None


def test_the_prompt_closes_and_the_session_works_again(rig, monitor, tx, clock):
    rm_prompt(rig, tx)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert rig.sess.open_prompts() == []
    assert rig.sess.status == Status.BUSY
    _mid, _text, markup = rig.card_edits[-1]
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert all(not str(b).endswith((":allow", ":deny")) for b in buttons)


def test_the_history_row_is_a_refusal(rig, monitor, tx, clock):
    rm_prompt(rig, tx)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert rig.sess.tool_history[-1] == (REFUSED_ROW, "failed")


def test_the_history_row_is_drawn_in_the_chats_language(rig, monitor, tx, clock):
    preferences.set_ui_language(GROUP, "fa")
    rm_prompt(rig, tx)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    row, _done = rig.sess.tool_history[-1]
    fa = i18n.get("fa")
    drawn = card_rows.render_md(fa, row)
    assert fa.md(card_rows.REFUSED_BY_CLAUDE) in drawn
    assert "Refused by Claude Code" not in drawn


def test_the_audit_records_a_refusal_by_claude_code(rig, monitor, tx, clock, audits):
    rm_prompt(rig, tx)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    [rec] = audits
    assert (rec["action"], rec["denied"], rec["via"], rec["user_id"], rec["tool"]) == (
        "Refused by Claude Code", True, "claude_code_timeout", None, "Bash")


def test_nothing_says_allowed(cardless, monitor_cl, tx, clock, audits):
    rm_prompt(cardless, tx)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(cardless, monitor_cl)
    assert cardless.sess.open_prompts() == []
    assert no_allowed_anywhere(cardless, audits)


def test_no_answer_line_is_posted(rig, monitor, tx, clock):
    rm_prompt(rig, tx)
    sent = len(rig.sent)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert len(rig.sent) == sent


def test_the_other_open_prompt_stays(rig, monitor, tx, clock):
    rig.question()
    rm_prompt(rig, tx)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert [p["tool_info"]["name"] for p in rig.sess.open_prompts()] == ["AskUserQuestion"]
    assert rig.sess.status == Status.INTERACTIVE


def test_the_inline_card_is_marked_refused(rig, monitor, tx, clock):
    rm_prompt(rig, tx)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert rig.sess.prompt_refused(CARD)


# -- criterion 13: nothing closes without the evidence -------------------------------

def test_before_the_window_nothing_happens(rig, monitor, tx, clock, audits):
    rm_prompt(rig, tx)
    tx.refused()
    clock.at(BEFORE_WINDOW)
    scan(rig, monitor)
    assert len(rig.sess.open_prompts()) == 1
    assert audits == []


def test_an_absent_result_closes_nothing_until_it_appears(rig, monitor, tx, clock, audits):
    rm_prompt(rig, tx)
    tx.call()
    clock.at(IN_WINDOW + 60)
    scan(rig, monitor)
    scan(rig, monitor)
    assert len(rig.sess.open_prompts()) == 1 and audits == []
    tx.result()
    scan(rig, monitor)
    assert rig.sess.open_prompts() == []
    assert len(audits) == 1


def test_a_result_for_another_command_closes_nothing(rig, monitor, tx, clock, audits):
    """The id came from a parallel Bash call: its own failure is not this
    prompt's refusal."""
    rm_prompt(rig, tx)
    tx.call(command="make test")
    tx.result()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert len(rig.sess.open_prompts()) == 1 and audits == []


def test_an_unreadable_transcript_closes_nothing(rig, monitor, tmp_path, clock, audits):
    folder = tmp_path / "a-folder"
    folder.mkdir()
    rm_prompt(rig)
    rig.sess.transcript_path = str(folder)
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert len(rig.sess.open_prompts()) == 1 and audits == []


def test_an_ok_result_closes_it_silently(cardless, monitor_cl, tx, clock, audits):
    """Answered in the terminal and run: closed, with no refused text, no
    row and no audit."""
    rm_prompt(cardless, tx)
    tx.call()
    tx.result(is_error=False)
    clock.at(IN_WINDOW)
    rows = list(cardless.sess.tool_history)
    scan(cardless, monitor_cl)
    assert cardless.sess.open_prompts() == []
    assert edits(cardless) == []
    assert cardless.sess.tool_history == rows
    assert audits == []
    assert not cardless.sess.prompt_refused(cardless.prompt_messages()[0]["msg_id"])


def test_an_agents_prompt_is_never_closed_by_its_result(rig, monitor, tx, clock, audits):
    from tests.two_prompts_rig import BG
    rig.launch(BG)
    rig.hook("PermissionRequest", tool_name="Bash", tool_input={"command": RM},
             agent_id=BG, agent_type="ship-reviewer", permission_mode="bypassPermissions",
             permission_suggestions=[], tool_use_id=TUID, transcript_path=str(tx))
    tx.refused()
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert len(rig.sess.open_prompts()) == 1 and audits == []


# -- criterion 14: the refusal-time look ---------------------------------------------

def test_the_timer_looks_at_the_refusal_time_with_no_scan(rig, monitor, tx, clock,
                                                          monkeypatch, audits):
    waits = []

    async def due_sleep(seconds):
        waits.append(seconds)
        clock.at(120.0 + rm_countdown.CHECK_GRACE_SECONDS)
        tx.refused()

    monkeypatch.setattr(session_monitor, "_rm_due_sleep", due_sleep)
    rm_prompt(rig, tx)
    clock.at(2.0)

    async def one_scan_then_the_timer():
        import time
        await monitor._scan_sessions(time.monotonic(), set())
        tasks = list(monitor._rm_checks.values())
        await asyncio.gather(*tasks)

    rig.run_async(one_scan_then_the_timer())
    assert waits == [pytest.approx(120.0 + rm_countdown.CHECK_GRACE_SECONDS - 2.0)]
    assert rig.sess.open_prompts() == []
    assert len(audits) == 1


def test_two_scans_schedule_one_look(rig, monitor, tx, clock, monkeypatch):
    waits = []

    async def due_sleep(seconds):
        waits.append(seconds)
        await asyncio.Event().wait()

    monkeypatch.setattr(session_monitor, "_rm_due_sleep", due_sleep)
    rm_prompt(rig, tx)
    clock.at(2.0)

    async def two_scans():
        import time
        await monitor._scan_sessions(time.monotonic(), set())
        await asyncio.sleep(0)
        clock.at(4.0)
        await monitor._scan_sessions(time.monotonic(), set())
        await asyncio.sleep(0)
        for task in list(monitor._rm_checks.values()):
            task.cancel()
        await asyncio.sleep(0)

    rig.run_async(two_scans())
    assert len(waits) == 1


def test_no_timer_once_the_refusal_time_has_passed(rig, monitor, tx, clock, monkeypatch):
    waits = []

    async def due_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(session_monitor, "_rm_due_sleep", due_sleep)
    rm_prompt(rig, tx)
    clock.at(200.0)
    scan(rig, monitor)
    assert waits == []


# -- criterion 20: an answered prompt is never closed as refused ---------------------

def test_a_prompt_answered_from_the_chat_is_never_refused_later(rig, monitor, tx, clock,
                                                                audits):
    rm_prompt(rig, tx)
    rig.tap(CARD, "allow")
    assert [a["action"] for a in audits] == ["Allowed"]
    tx.refused()  # the command itself failed
    clock.at(IN_WINDOW)
    scan(rig, monitor)
    assert [a["action"] for a in audits] == ["Allowed"]
    assert all(not str(s).endswith("Refused by Claude Code")
               for s, _d in rig.sess.tool_history)


def test_the_same_refusal_twice_is_recorded_once(rig, tx, clock, audits):
    rm_prompt(rig, tx)
    perm = rig.sess.open_prompts()[0]
    clock.at(IN_WINDOW)
    ctx = {"perms": [perm], "outcome": "refused", "close": True}
    rig.run_async(rig.bot.notify(rig.sess, "rm_refused", dict(ctx)))
    rig.run_async(rig.bot.notify(rig.sess, "rm_refused", dict(ctx)))
    assert len(audits) == 1
    assert [s for s, _d in rig.sess.tool_history].count(REFUSED_ROW) == 1


def test_a_closed_prompt_is_not_refused(rig, tx, clock, audits):
    rm_prompt(rig, tx)
    perm = rig.sess.open_prompts()[0]
    rig.tap(CARD, "deny")
    audits.clear()
    rig.run_async(rig.bot.notify(rig.sess, "rm_refused", {
        "perms": [perm], "outcome": "refused", "close": True}))
    assert audits == []


def test_a_hook_answer_before_the_window_is_sent(rig, tx, clock):
    rm_prompt(rig, tx)
    clock.at(BEFORE_WINDOW)
    rig.tap(CARD, "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [(REQ, "allow")]
