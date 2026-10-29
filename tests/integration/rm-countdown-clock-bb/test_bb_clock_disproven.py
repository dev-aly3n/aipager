"""A false clock is disproven (roadmap 8.152 c).

An ask rule matching part of a risky rm (``Bash(sleep:*)`` on a
``$(sleep ...)`` inside it) makes Claude Code show its plain ask with no
countdown, and a server may set a countdown longer than 2 minutes; aipager
still shows its clock and, from 15 s before the shown time, refuses every
tap. Once the shown time plus 20 s has passed with no result for the call
in the transcript and the prompt is still open, the clock is disproven:
its line goes, taps answer again (also after a daemon restart), and the
prompt closes later as any other one; a refusal that does come later
still closes it as refused."""

from __future__ import annotations

import json

import pytest

from aipager import rm_countdown, state
from tests.rm_clock_bb import CLOCK_HEAD, REFUSE_AT, TOAST_REFUSAL_DUE, restart
from tests.two_prompts_rig import CARD, GROUP, S, answers

DUE = REFUSE_AT + rm_countdown.DISPROVE_AFTER_SECONDS
TUID = "toolu_p1"


def open_call(env):
    """Claude Code's transcript holds the call and nothing after it."""
    env.call()


def prompt_edits(env, msg_id) -> list[dict]:
    return [k for k in env.text_edits() if k.get("message_id") == msg_id]


# -- the rule's timing -------------------------------------------------------------------

def test_just_before_the_margin_nothing_changes(env):
    env.rm()
    open_call(env)
    [sent] = env.rig.prompt_messages()
    env.scan(DUE - 0.5)
    assert prompt_edits(env, sent["msg_id"]) == []
    q = env.rig.tap(sent["msg_id"], "allow")
    assert (answers(q), env.rig.decisions, env.rig.keys) == ([TOAST_REFUSAL_DUE], [], [])


def test_at_the_margin_the_clock_line_goes_and_the_buttons_stay(env):
    env.rm()
    open_call(env)
    [sent] = env.rig.prompt_messages()
    env.scan(DUE)
    [edit] = prompt_edits(env, sent["msg_id"])
    assert CLOCK_HEAD not in edit["text"]
    assert edit["text"] == sent["text"].rsplit("\n", 1)[0]
    assert edit["reply_markup"] is sent["reply_markup"]


def test_once_disproven_a_tap_answers_again(env):
    env.rm()
    open_call(env)
    [sent] = env.rig.prompt_messages()
    env.scan(DUE)
    q = env.rig.tap(sent["msg_id"], "allow")
    assert TOAST_REFUSAL_DUE not in answers(q)
    assert [d["behavior"] for _r, d in env.rig.decisions] == ["allow"]


def test_the_block_lifts_for_good(env):
    """Later scans and times never bring the block back."""
    env.rm()
    open_call(env)
    [sent] = env.rig.prompt_messages()
    env.scan(DUE)
    env.scan(DUE + 300.0)
    q = env.rig.tap(sent["msg_id"], "deny")
    assert TOAST_REFUSAL_DUE not in answers(q)
    assert [d["behavior"] for _r, d in env.rig.decisions] == ["deny"]


def test_the_line_goes_once(env):
    env.rm()
    open_call(env)
    [sent] = env.rig.prompt_messages()
    env.scan(DUE)
    env.call(command="echo more", tuid="toolu_other")   # the transcript changes
    env.scan(DUE + 5.0)
    assert len(prompt_edits(env, sent["msg_id"])) == 1


def test_the_inline_card_drops_the_line_and_keeps_its_buttons(cenv):
    cenv.rm()
    open_call(cenv)
    assert CLOCK_HEAD in cenv.last_card_text()
    cenv.scan(DUE)
    _mid, text, markup = [e for e in cenv.rig.card_edits if e[0] == CARD][-1]
    assert CLOCK_HEAD not in text
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(str(b).endswith(":allow") for b in buttons)


def test_the_bars_copy_after_the_disproof_has_no_line(env):
    env.rm()
    open_call(env)
    env.scan(DUE)
    outcome = env.run_async(env.rig.bot._resend_pending_prompt(GROUP, env.rig.sess))
    assert outcome == "sent"
    assert CLOCK_HEAD not in env.rig.sent[-1]["text"]


# -- what disproves it, and what does not -------------------------------------------------

def test_a_result_for_the_call_is_not_a_disproof_but_a_close(env):
    env.rm()
    env.refused_transcript()
    env.scan(DUE)
    assert [a["via"] for a in env.refused_audit()] == ["claude_code_timeout"]


@pytest.mark.parametrize("what", ["missing", "folder"])
def test_no_readable_transcript_disproves_nothing(env, tmp_path, what):
    env.rm()
    [sent] = env.rig.prompt_messages()
    if what == "folder":
        folder = tmp_path / "a-folder"
        folder.mkdir()
        env.rig.sess.transcript_path = str(folder)
    else:
        env.rig.sess.transcript_path = str(tmp_path / "never-written.jsonl")
    env.scan(DUE + 60.0)
    assert prompt_edits(env, sent["msg_id"]) == []
    q = env.rig.tap(sent["msg_id"], "allow")
    assert answers(q) == [TOAST_REFUSAL_DUE]


def test_a_result_borrowed_from_another_call_disproves_nothing(env):
    """A result for the id whose tool_use is another command (a mismatch)
    says nothing about this call."""
    env.rm()
    env.call(command="make test")
    env.result()
    [sent] = env.rig.prompt_messages()
    env.scan(DUE + 60.0)
    assert prompt_edits(env, sent["msg_id"]) == []


def test_a_closed_prompt_is_never_disproven(env):
    env.rm()
    open_call(env)
    [sent] = env.rig.prompt_messages()
    env.wall.t = REFUSE_AT - 60.0
    env.rig.tap(sent["msg_id"], "allow")
    edits = len(env.text_edits())
    env.scan(DUE)
    assert len(env.text_edits()) == edits


def test_a_prompt_with_no_clock_is_left_alone(env):
    env.rm(mode="default")
    open_call(env)
    env.scan(DUE)
    assert env.text_edits() == []


# -- after the disproof ------------------------------------------------------------------

def test_a_refusal_that_comes_later_still_closes_it_as_refused(env):
    """A server-set countdown longer than 2 minutes: the refusal comes
    after the disproof and is still read as Claude Code's own."""
    env.rm()
    open_call(env)
    env.scan(DUE)
    env.result()
    env.scan(DUE + 200.0)
    assert env.rig.sess.open_prompts() == []
    assert [a["via"] for a in env.refused_audit()] == ["claude_code_timeout"]


def test_the_next_tool_call_closes_it_as_answered_not_refused(cenv):
    """Claude's next parent tool call: the existing closer, with no
    refused row or audit."""
    cenv.rm()
    open_call(cenv)
    cenv.scan(DUE)
    cenv.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/home/alice/a"},
              tool_use_id="toolu_next")
    assert cenv.rig.sess.open_prompts() == []
    assert cenv.refused_audit() == [] and cenv.refused_rows() == []


def test_the_stop_closes_it_as_answered_not_refused(cenv):
    cenv.rm()
    open_call(cenv)
    cenv.scan(DUE)
    cenv.hook("Stop", stop_hook_active=False)
    assert cenv.refused_audit() == [] and cenv.refused_rows() == []


# -- across a daemon restart ---------------------------------------------------------------

def test_the_disproof_is_saved(env):
    env.rm()
    open_call(env)
    env.scan(DUE)
    env.rig.bot.registry.save()
    rec = json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][S]["open_prompt"]
    assert rec["perm"]["rm_clock"]["disproven"] is True
    assert CLOCK_HEAD not in rec["text"]


def test_after_a_restart_the_block_stays_lifted(cenv, mk_bot, monkeypatch, run_async):
    cenv.rm()
    open_call(cenv)
    cenv.scan(DUE)
    after = restart(cenv, mk_bot, monkeypatch, run_async)
    after.wall.t = DUE + 30.0
    assert CLOCK_HEAD not in after.rig.bot._build_busy_text("api", "Waiting", after.rig.sess)
    q = after.rig.tap(CARD, "allow")
    assert TOAST_REFUSAL_DUE not in answers(q)
    assert after.rig.keys or after.rig.decisions


def test_control_without_the_disproof_a_restart_keeps_the_block(cenv, mk_bot, monkeypatch,
                                                                 run_async):
    cenv.rm()
    open_call(cenv)
    after = restart(cenv, mk_bot, monkeypatch, run_async)
    after.wall.t = DUE - 1.0
    q = after.rig.tap(CARD, "allow")
    assert answers(q) == [TOAST_REFUSAL_DUE]


def test_an_unchanged_transcript_is_read_once(env, monkeypatch):
    """Once due, the disproof look reads the transcript again only after
    it changed (a result borrowed from another call stays unproven)."""
    from aipager import session_monitor
    reads = []
    real = session_monitor.tool_call_result

    def counting(*a, **kw):
        reads.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(session_monitor, "tool_call_result", counting)
    env.rm()
    env.call(command="make test")
    env.result()
    env.scan(DUE)
    first = len(reads)
    env.scan(DUE + 2.0)
    env.scan(DUE + 4.0)
    assert first >= 1 and len(reads) == first


def test_the_disproof_marks_the_state_to_be_saved(cenv):
    cenv.rm()
    open_call(cenv)
    cenv.scan(DUE - 5.0)
    cenv.rig.bot.registry._dirty = False
    cenv.scan(DUE)
    assert cenv.rig.bot.registry._dirty is True


@pytest.mark.parametrize("closer", ["PreToolUse", "Stop"])
def test_a_late_refusal_a_closer_meets_first_is_still_refused(cenv, closer):
    """A server-set countdown longer than 2 minutes: Claude Code refuses
    after the disproof, and Claude's next hook arrives before the scan
    reads the result. The closer still records Claude Code's refusal."""
    cenv.rm()
    open_call(cenv)
    cenv.scan(DUE)
    cenv.result()
    cenv.wall.t = DUE + 200.0
    if closer == "PreToolUse":
        cenv.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/home/alice/a"},
                  tool_use_id="toolu_next")
    else:
        cenv.hook("Stop", stop_hook_active=False)
    assert [a["via"] for a in cenv.refused_audit()] == ["claude_code_timeout"]
    assert cenv.refused_rows()
