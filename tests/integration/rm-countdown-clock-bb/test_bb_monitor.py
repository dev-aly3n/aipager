"""The refused close found by the session monitor and its deadline timer
(design.md success criteria 12, 13, 14 and 20; decision 3's table).

Partitions: the transcript result (error / ok / absent then late flush /
mismatch by command or name / unreadable), the window (just before
``refuse_at - 15``, exactly at it, past the deadline), the prompt (a clock
prompt, a non-clock prompt, an agent's), and what the chat already did
(answered through the hook). Observable: the separate message's edit, the
open prompts, the session status, ``tool_history``, the audit log."""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences, session_monitor
from aipager.state import Status
from tests.rm_clock_bb import (
    REFUSE_AT,
    REFUSED_EN,
    RM,
    T0,
    VERB_EN,
    WINDOW_AT,
    catalog,
)
from tests.two_prompts_rig import BG, CARD, GROUP

IN_WINDOW = REFUSE_AT + 1.0


def refused(env, at=IN_WINDOW):
    env.rm()
    env.refused_transcript()
    env.scan(at)


# -- criterion 12: the refused close ------------------------------------------------------------

def test_separate_message_edited_to_its_text_plus_the_refused_line(env):
    env.rm()
    original = env.rig.prompt_messages()[0]["text"]
    env.refused_transcript()
    env.scan(IN_WINDOW)
    assert [k["text"] for k in env.text_edits()] == [original + "\n\n" + REFUSED_EN]


def test_separate_message_edit_targets_the_prompt_message(env):
    refused(env)
    assert [k["message_id"] for k in env.refused_text_edits()] == env.separate_ids()


def test_separate_message_edit_drops_the_keyboard(env):
    refused(env)
    assert [k.get("reply_markup", "absent") for k in env.refused_text_edits()] == [None]


def test_prompt_is_closed(env):
    refused(env)
    assert env.rig.sess.open_prompts() == []


def test_session_goes_busy_when_it_was_the_only_prompt(env):
    refused(env)
    assert env.rig.sess.status == Status.BUSY


def test_session_stays_waiting_when_another_prompt_is_open(cenv):
    cenv.rig.launch(BG)
    cenv.rig.agent_bash(BG, "make", request_id="req-bg")
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(IN_WINDOW)
    assert cenv.rig.sess.status == Status.INTERACTIVE


def test_the_other_prompt_stays_open(cenv):
    cenv.rig.launch(BG)
    cenv.rig.agent_bash(BG, "make", request_id="req-bg")
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(IN_WINDOW)
    assert [p["tool_info"].get("agent_id") for p in cenv.rig.sess.open_prompts()] == [BG]


def test_history_row_says_refused_by_claude_code(env):
    refused(env)
    assert env.refused_rows() == [(f"🔑 Bash: {RM} → {VERB_EN}", "failed")]


def test_history_row_is_drawn_with_a_cross(cenv):
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(IN_WINDOW)
    rows = [line for line in cenv.last_card_text().splitlines() if VERB_EN in line]
    assert rows and all(r.startswith("❌") for r in rows)


def test_history_row_is_never_drawn_with_a_tick(cenv):
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(IN_WINDOW)
    assert not any("✅" in line and VERB_EN in line
                   for _mid, text, _m in cenv.rig.card_edits for line in text.splitlines())


def finished_card(cenv) -> str:
    """The finished card (Stop) drawn after the monitor's refused close."""
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(IN_WINDOW)
    cenv.hook("Stop")
    return cenv.rig.frames[-1] if cenv.rig.frames else ""


def refused_lines(text: str, verb: str) -> list[str]:
    return [line for line in text.splitlines() if verb in line]


def test_fa_finished_card_shows_the_translated_verb(cenv):
    """The live busy card shows every stored verb as stored (Allowed and
    Denied too); the finished card renders rows per language."""
    preferences.set_ui_language(GROUP, "fa")
    assert catalog("fa")["ans.verb.refused_by_claude"] in finished_card(cenv)


def test_fa_finished_card_shows_no_raw_english_verb(cenv):
    preferences.set_ui_language(GROUP, "fa")
    assert VERB_EN not in finished_card(cenv)


def test_refused_row_is_on_the_finished_card(cenv):
    assert refused_lines(finished_card(cenv), VERB_EN) != []


def test_refused_row_keeps_its_cross_on_the_finished_card(cenv):
    """design.md criterion 12: "never ✅"; spec requirement 4: the busy
    card's history row shows it refused (not ✅)."""
    lines = refused_lines(finished_card(cenv), VERB_EN)
    assert lines and not any("✅" in line for line in lines)


def test_fa_refused_row_keeps_its_cross_on_the_finished_card(cenv):
    preferences.set_ui_language(GROUP, "fa")
    lines = refused_lines(finished_card(cenv), catalog("fa")["ans.verb.refused_by_claude"])
    assert lines and not any("✅" in line for line in lines)


def test_audit_records_the_refusal(env):
    refused(env)
    assert [(a["action"], a["denied"], a["via"]) for a in env.refused_audit()] == [
        ("Refused by Claude Code", True, "claude_code_timeout")]


def test_audit_record_names_no_person(env):
    refused(env)
    assert [a.get("user_id") for a in env.refused_audit()] == [None]


def test_nothing_says_allowed(env):
    refused(env)
    blob = repr(env.audit()) + repr(env.rig.sess.tool_history) + repr(env.text_edits())
    assert "Allowed" not in blob


def test_no_attributed_answer_line_is_posted(env):
    """No person answered, so no "answered by" line goes to the chat."""
    env.rm()
    before = len(env.rig.sent)
    env.refused_transcript()
    env.scan(IN_WINDOW)
    assert env.rig.sent[before:] == []


def test_no_keys_typed_and_no_hook_decision(env):
    refused(env)
    assert (env.rig.keys, env.rig.decisions) == ([], [])


def test_inline_prompt_loses_its_buttons(cenv):
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(IN_WINDOW)
    markup = [m for mid, _t, m in cenv.rig.card_edits if mid == CARD][-1]
    labels = [b.callback_data for row in getattr(markup, "inline_keyboard", ()) for b in row]
    assert not any(c.endswith((":allow", ":deny", ":allow_always")) for c in labels)


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_auto_mode_closes_the_same(env, mode):
    env.rm(mode=mode)
    env.refused_transcript()
    env.scan(IN_WINDOW)
    assert len(env.refused_audit()) == 1


# -- criterion 13: the window boundary ---------------------------------------------------------

def test_just_before_the_window_nothing_happens(env):
    env.rm()
    env.refused_transcript()
    env.scan(WINDOW_AT - 0.5)
    assert len(env.rig.sess.open_prompts()) == 1


def test_just_before_the_window_no_record(env):
    env.rm()
    env.refused_transcript()
    env.scan(WINDOW_AT - 0.5)
    assert (env.refused_audit(), env.refused_rows(), env.refused_text_edits()) == ([], [], [])


def test_at_the_window_edge_it_closes(env):
    """entrypoints.md: True from ``refuse_at - 15 s``."""
    refused(env, at=WINDOW_AT)
    assert len(env.refused_audit()) == 1


def test_a_tick_before_then_in_the_window_closes_without_a_file_change(env):
    """Error guessing: a read gated on "the file changed" must not swallow
    the error seen (or skipped) before the window opened."""
    env.rm()
    env.refused_transcript()
    env.scan(T0 + 2.0)
    env.scan(WINDOW_AT - 0.5)
    env.scan(IN_WINDOW)
    assert len(env.refused_audit()) == 1


# -- criterion 13: the transcript partitions ---------------------------------------------------

def test_absent_result_does_nothing(env):
    env.rm()
    env.call()
    env.scan(IN_WINDOW)
    assert (len(env.rig.sess.open_prompts()), env.refused_audit()) == (1, [])


def test_absent_result_keeps_the_session_waiting(env):
    env.rm()
    env.call()
    env.scan(IN_WINDOW)
    assert env.rig.sess.status == Status.INTERACTIVE


def test_no_transcript_file_at_all_does_nothing(env):
    env.rm()
    env.scan(IN_WINDOW + 600.0)
    assert (len(env.rig.sess.open_prompts()), env.refused_audit()) == (1, [])


def test_late_flushed_result_closes_at_a_later_tick(env):
    env.rm()
    env.call()
    env.scan(IN_WINDOW)
    env.result()
    env.scan(IN_WINDOW + 2.0)
    assert len(env.refused_audit()) == 1


def test_late_flushed_result_closes_the_prompt(env):
    env.rm()
    env.call()
    env.scan(IN_WINDOW)
    env.result()
    env.scan(IN_WINDOW + 2.0)
    assert env.rig.sess.open_prompts() == []


def test_mismatch_by_command_does_nothing(env):
    env.rm()
    env.refused_transcript(command="rm -rf dist/")
    env.scan(IN_WINDOW)
    assert (len(env.rig.sess.open_prompts()), env.refused_audit()) == (1, [])


def test_mismatch_by_tool_name_does_nothing(env):
    env.rm()
    env.call(name="Read")
    env.result()
    env.scan(IN_WINDOW)
    assert (len(env.rig.sess.open_prompts()), env.refused_audit()) == (1, [])


def test_an_error_for_another_call_id_does_nothing(env):
    env.rm()
    env.refused_transcript(tuid="toolu_other")
    env.scan(IN_WINDOW)
    assert (len(env.rig.sess.open_prompts()), env.refused_audit()) == (1, [])


def test_unreadable_transcript_does_nothing(env, tmp_path):
    env.rm()
    env.rig.sess.transcript_path = str(tmp_path)  # a directory
    env.scan(IN_WINDOW)
    assert (len(env.rig.sess.open_prompts()), env.refused_audit()) == (1, [])


def test_ok_result_closes_the_prompt(env):
    env.rm()
    env.call()
    env.result(is_error=False)
    env.scan(IN_WINDOW)
    assert env.rig.sess.open_prompts() == []


def test_ok_result_closes_silently(env):
    env.rm()
    env.call()
    env.result(is_error=False)
    env.scan(IN_WINDOW)
    assert (env.refused_text_edits(), env.refused_rows(), env.refused_audit()) == ([], [], [])


# -- prompts that carry no clock ---------------------------------------------------------------

@pytest.mark.parametrize("mode", ["default", "acceptEdits", None])
def test_a_non_clock_prompt_is_never_closed_as_refused(env, mode):
    env.rm(mode=mode)
    env.refused_transcript()
    env.scan(IN_WINDOW + 600.0)
    assert env.refused_audit() == []


def test_an_agents_rm_is_never_closed_as_refused(cenv):
    cenv.rig.launch(BG)
    cenv.hook("PermissionRequest", tool_name="Bash",
              tool_input={"command": RM, "description": RM}, agent_id=BG,
              agent_type="ship-reviewer", tool_use_id="toolu_p1",
              permission_mode="bypassPermissions", permission_suggestions=[],
              aipager_reply_addr="/nonexistent/reply-bg.sock", aipager_request_id="req-bg")
    cenv.refused_transcript()
    cenv.scan(IN_WINDOW + 600.0)
    assert cenv.refused_audit() == []


# -- criterion 20: answered from the chat first ---------------------------------------------------

def test_a_prompt_allowed_by_hook_is_never_closed_as_refused(env):
    env.rm()
    env.rig.tap(env.separate_ids()[0], "allow")
    env.refused_transcript()
    env.scan(IN_WINDOW)
    assert env.refused_audit() == []


def test_a_prompt_denied_by_hook_is_never_closed_as_refused(env):
    env.rm()
    env.rig.tap(env.separate_ids()[0], "deny")
    env.refused_transcript()
    env.scan(IN_WINDOW)
    assert env.refused_rows() == []


def test_a_prompt_answered_by_keys_is_never_closed_as_refused(env):
    env.rm(hook=False)
    env.rig.tap(env.separate_ids()[0], "deny")
    env.refused_transcript()
    env.scan(IN_WINDOW)
    assert env.refused_audit() == []


def test_many_ticks_record_one_refusal(env):
    refused(env)
    for k in range(4):
        env.scan(IN_WINDOW + 2.0 * (k + 1))
    assert (len(env.refused_audit()), len(env.refused_rows()),
            len(env.refused_text_edits())) == (1, 1, 1)


def test_a_touched_transcript_after_the_close_records_nothing_more(env):
    refused(env)
    env.result()  # the same line flushed twice
    env.scan(IN_WINDOW + 2.0)
    assert len(env.refused_audit()) == 1


# -- criterion 14: the deadline timer -------------------------------------------------------------

class Timer:
    """The ``_rm_due_sleep`` seam: records each wait and moves the wall
    clock by it (a real deadline passing), or blocks forever."""

    def __init__(self, wall, *, block=False):
        self.wall, self.block, self.waits = wall, block, []

    async def __call__(self, seconds):
        self.waits.append(seconds)
        if self.block:
            await asyncio.Event().wait()
        self.wall.t += seconds


def run_and_drain(env, *coros_fns):
    async def go():
        for fn in coros_fns:
            await fn()
        for _ in range(50):
            await asyncio.sleep(0)
    env.run_async(go())


def test_timer_waits_until_refuse_at_plus_three_seconds(env, monkeypatch):
    timer = Timer(env.wall)
    monkeypatch.setattr(session_monitor, "_rm_due_sleep", timer)
    env.rm()
    env.wall.t = T0 + 1.0
    run_and_drain(env, env.monitor._scan)
    assert timer.waits == [pytest.approx(REFUSE_AT + 3.0 - (T0 + 1.0), abs=0.5)]


def test_timer_runs_the_check_with_no_further_tick(env, monkeypatch):
    timer = Timer(env.wall)
    monkeypatch.setattr(session_monitor, "_rm_due_sleep", timer)
    env.rm()
    env.refused_transcript()
    env.wall.t = T0 + 1.0
    run_and_drain(env, env.monitor._scan)
    assert len(env.refused_audit()) == 1


def test_timer_finding_no_result_closes_nothing(env, monkeypatch):
    timer = Timer(env.wall)
    monkeypatch.setattr(session_monitor, "_rm_due_sleep", timer)
    env.rm()
    env.call()
    env.wall.t = T0 + 1.0
    run_and_drain(env, env.monitor._scan)
    assert len(env.rig.sess.open_prompts()) == 1


def test_two_ticks_schedule_one_timer(env, monkeypatch):
    timer = Timer(env.wall, block=True)
    monkeypatch.setattr(session_monitor, "_rm_due_sleep", timer)
    env.rm()
    env.wall.t = T0 + 1.0
    run_and_drain(env, env.monitor._scan, env.monitor._scan, env.monitor._scan)
    assert len(timer.waits) == 1


def test_no_timer_for_a_non_clock_prompt(env, monkeypatch):
    timer = Timer(env.wall, block=True)
    monkeypatch.setattr(session_monitor, "_rm_due_sleep", timer)
    env.rm(mode="default")
    env.wall.t = T0 + 1.0
    run_and_drain(env, env.monitor._scan)
    assert timer.waits == []


def test_timer_after_a_chat_answer_records_nothing(env, monkeypatch):
    """Error guessing: the timer fires after the prompt was answered."""
    monkeypatch.setattr(session_monitor, "_rm_due_sleep", Timer(env.wall))
    env.rm()
    env.wall.t = T0 + 1.0
    env.rig.tap(env.separate_ids()[0], "deny")
    env.refused_transcript()
    run_and_drain(env, env.monitor._scan)
    assert env.refused_audit() == []


def test_timer_scheduled_then_answered_records_nothing(env, monkeypatch):
    """Error guessing: the timer was armed while the prompt was open, the
    chat answered before it fired, and the transcript then shows an error
    result (a failed command after an Allow is ``is_error`` too)."""
    timer = Timer(env.wall, block=True)
    monkeypatch.setattr(session_monitor, "_rm_due_sleep", timer)
    env.rm()
    env.wall.t = T0 + 1.0
    run_and_drain(env, env.monitor._scan)
    env.rig.tap(env.separate_ids()[0], "allow")
    env.refused_transcript()
    env.scan(IN_WINDOW)
    assert env.refused_audit() == []
